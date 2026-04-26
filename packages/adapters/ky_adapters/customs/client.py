"""관세청 무역통계 adapter — data.go.kr (institution code 1220000).

All endpoints under this institution share one Korean public-data key
(`DATA_GO_KR_API_KEY`). Responses are XML — we parse defensively. data.go.kr
APIs require **per-API activation**: activation can lag application by minutes
to hours even with auto-approval. Healthcheck reports per-endpoint status.

Verified endpoints (live as of 2026-04-25):

  - get_hs_country_monthly  → /1220000/nitemtrade/getNitemtradeList ✓

Pending endpoints (path/activation TBD — adapter will surface 403/500 cleanly
so the operator can retry once the data.go.kr backend has caught up):

  - get_hs_monthly              → 품목별 수출입실적
  - get_10day_export_by_item    → 수출 주요품목 10일 잠정치
  - get_10day_import_by_item    → 수입 주요품목 10일 잠정치
  - get_10day_export_by_country → 수출 주요국가 10일 잠정치
  - get_10day_import_by_country → 수입 주요국가 10일 잠정치
"""
from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date
from typing import Any, Optional

from ky_adapters.base import AdapterError, AuthError, BaseAdapter

CUSTOMS_BASE = "https://apis.data.go.kr/1220000"

# Per-API endpoint registry. The first tuple element is the URL path; the
# second is whether the API is verified-working (True) or candidate (False).
ENDPOINTS: dict[str, tuple[str, bool]] = {
    # nitemtrade 한 endpoint가 (HS×국가), (HS만), (국가만) 세 케이스 모두 처리.
    # data.go.kr이 데이터 페이지를 여러 제목으로 listing하지만 실제는 동일 endpoint.
    "hs_country_monthly":   ("/nitemtrade/getNitemtradeList",                                  True),  # hsSgn + cntyCd 모두 지정
    "hs_monthly":           ("/nitemtrade/getNitemtradeList",                                  True),  # hsSgn만 (cntyCd 없음 → 품목별 전체 국가 분해)
    "country_monthly":      ("/nitemtrade/getNitemtradeList",                                  True),  # cntyCd만 (hsSgn 없음 → 국가 단위 모든 HS)
    "10day_export_country": ("/cntyMmUtPrviExpAcrs/getCntyMmUtPrviExpAcrs",                    True),   # verified 2026-04-26
    "10day_import_country": ("/cntyMmUtPrviImpAcrs/getCntyMmUtPrviImpAcrs",                    True),   # verified 2026-04-26
    "10day_export_item":    ("/prlstMmUtPrviExpAcrs/getPrlstMmUtPrviExpAcrs",                  True),   # verified 2026-04-26 (prlst = 주요품목)
    "10day_import_item":    ("/prlstMmUtPrviImpAcrs/getPrlstMmUtPrviImpAcrs",                  True),   # verified 2026-04-26
}


@dataclass
class CustomsObservation:
    """One row of trade statistics — flat schema across all 6 endpoints."""
    period: str
    hs_code: Optional[str]
    country_code: Optional[str]
    country_name: Optional[str]
    export_usd: Optional[float]
    import_usd: Optional[float]
    export_kg: Optional[float]
    import_kg: Optional[float]
    export_count: Optional[int]
    import_count: Optional[int]
    trade_balance_usd: Optional[float]
    raw: dict[str, Any]

    def as_row(self, source_id: str = "customs") -> dict[str, Any]:
        return {
            "source_id": source_id,
            "period": self.period,
            "hs_code": self.hs_code,
            "country_code": self.country_code,
            "country_name": self.country_name,
            "export_usd": self.export_usd,
            "import_usd": self.import_usd,
            "export_kg": self.export_kg,
            "import_kg": self.import_kg,
            "export_count": self.export_count,
            "import_count": self.import_count,
            "trade_balance_usd": self.trade_balance_usd,
        }


class CustomsAdapter(BaseAdapter):
    source_id = "customs"
    priority = 2

    def __init__(self, api_key: Optional[str] = None, base_url: str = CUSTOMS_BASE) -> None:
        super().__init__()
        self.api_key = api_key
        self.base_url = base_url

    @classmethod
    def from_settings(cls) -> "CustomsAdapter":
        # Prefer the unambiguous key; fall back to the legacy EXIM_API_KEY label
        # which historically held the same data.go.kr key in some .env files.
        return cls(api_key=cls._env("DATA_GO_KR_API_KEY") or cls._env("EXIM_API_KEY"))

    # ----- Contract ---------------------------------------------------

    def healthcheck(self) -> dict[str, Any]:
        """Calls the verified endpoint with a known-good HS code (8542 = semis)
        for the most recent 3-month window."""
        if not self.api_key:
            return self._timed_fail(self.source_id, "DATA_GO_KR_API_KEY not configured")
        t0 = time.perf_counter()
        try:
            today = date.today()
            end_yymm = today.strftime("%Y%m")
            start_year = today.year if today.month > 3 else today.year - 1
            start_month = today.month - 3 if today.month > 3 else 12 + (today.month - 3)
            start_yymm = f"{start_year:04d}{start_month:02d}"
            rows = self.get_hs_country_monthly(
                hs_code="8542",
                year_month_start=start_yymm,
                year_month_end=end_yymm,
                num_rows=3,
            )
            ok = isinstance(rows, list)
        except Exception as exc:  # noqa: BLE001
            return self._timed_fail(self.source_id, str(exc))
        latency_ms = (time.perf_counter() - t0) * 1000
        return self._timed_ok(
            latency_ms,
            self.source_id,
            {
                "verified_endpoint": "hs_country_monthly",
                "sample_rows": len(rows or []),
                "pending_endpoints": [
                    k for k, (_, verified) in ENDPOINTS.items() if not verified
                ],
            },
        )

    def healthcheck_all(self) -> dict[str, Any]:
        """Hit every endpoint with minimal params and report per-endpoint
        status. Useful after the operator activates new APIs on data.go.kr."""
        if not self.api_key:
            return self._timed_fail(self.source_id, "DATA_GO_KR_API_KEY not configured")
        results: dict[str, Any] = {}
        # Default params per endpoint family
        today = date.today()
        ym_end = today.strftime("%Y%m")
        ym_start = (today.replace(day=1)).strftime("%Y%m")
        # 10-day endpoints accept YYYYMM (one row per country per month, with
        # itemUsdAmt00/01/02 for 초순/중순/하순). No decade slug needed.
        for key, (_, _verified) in ENDPOINTS.items():
            try:
                if key == "hs_country_monthly":
                    rows = self.get_hs_country_monthly("8542", ym_start, ym_end, num_rows=2)
                elif key == "hs_monthly":
                    rows = self.get_hs_monthly("8542", ym_start, ym_end, num_rows=2)
                elif key == "10day_export_item":
                    rows = self.get_10day_export_by_item(ym_end, ym_end, num_rows=2)
                elif key == "10day_import_item":
                    rows = self.get_10day_import_by_item(ym_end, ym_end, num_rows=2)
                elif key == "10day_export_country":
                    rows = self.get_10day_export_by_country(ym_end, ym_end, num_rows=2)
                elif key == "10day_import_country":
                    rows = self.get_10day_import_by_country(ym_end, ym_end, num_rows=2)
                else:
                    rows = []
                results[key] = {"ok": True, "rows": len(rows or [])}
            except Exception as exc:  # noqa: BLE001
                results[key] = {"ok": False, "error": str(exc)[:200]}
        return {"source_id": self.source_id, "endpoints": results}

    # ----- Public methods --------------------------------------------

    def get_hs_country_monthly(
        self,
        hs_code: str,
        year_month_start: Optional[str] = None,
        year_month_end: Optional[str] = None,
        country_code: Optional[str] = None,
        page_no: int = 1,
        num_rows: int = 100,
    ) -> list[CustomsObservation]:
        """품목별 국가별 수출입실적 — verified endpoint."""
        params = self._period_params(year_month_start, year_month_end, decade=False)
        params["hsSgn"] = hs_code
        if country_code:
            params["cntyCd"] = country_code
        params["pageNo"] = page_no
        params["numOfRows"] = num_rows
        return self._call("hs_country_monthly", params)

    def get_hs_monthly(
        self,
        hs_code: str,
        year_month_start: Optional[str] = None,
        year_month_end: Optional[str] = None,
        page_no: int = 1,
        num_rows: int = 200,
    ) -> list[CustomsObservation]:
        """품목별 수출입실적 — HS만 지정, cntyCd 비우면 nitemtrade가 해당
        HS의 모든 거래 국가 분해를 반환한다 (총합 1행 + 국가 N행)."""
        params = self._period_params(year_month_start, year_month_end, decade=False)
        params["hsSgn"] = hs_code
        params["pageNo"] = page_no
        params["numOfRows"] = num_rows
        return self._call("hs_monthly", params)

    def get_country_monthly(
        self,
        country_code: str,
        year_month_start: Optional[str] = None,
        year_month_end: Optional[str] = None,
        page_no: int = 1,
        num_rows: int = 200,
    ) -> list[CustomsObservation]:
        """국가별 수출입실적 — cntyCd만 지정, 해당 국가의 모든 HS 분해."""
        params = self._period_params(year_month_start, year_month_end, decade=False)
        params["cntyCd"] = country_code
        params["pageNo"] = page_no
        params["numOfRows"] = num_rows
        return self._call("country_monthly", params)

    def get_10day_export_by_item(
        self,
        year_month_start: Optional[str] = None,
        year_month_end: Optional[str] = None,
        page_no: int = 1,
        num_rows: int = 100,
    ) -> list[CustomsObservation]:
        """수출 주요품목별 10일 잠정치 (path TBD)."""
        params = self._period_params(year_month_start, year_month_end, decade=False)
        params["pageNo"] = page_no
        params["numOfRows"] = num_rows
        return self._call("10day_export_item", params)

    def get_10day_import_by_item(
        self,
        year_month_start: Optional[str] = None,
        year_month_end: Optional[str] = None,
        page_no: int = 1,
        num_rows: int = 100,
    ) -> list[CustomsObservation]:
        params = self._period_params(year_month_start, year_month_end, decade=False)
        params["pageNo"] = page_no
        params["numOfRows"] = num_rows
        return self._call("10day_import_item", params)

    def get_10day_export_by_country(
        self,
        year_month_start: Optional[str] = None,
        year_month_end: Optional[str] = None,
        page_no: int = 1,
        num_rows: int = 100,
    ) -> list[CustomsObservation]:
        """수출 주요국가별 10일 잠정치 (verified 2026-04-26).
        Period is YYYYMM. Response splits each row into itemUsdAmt00/01/02
        which represent 초순/중순/하순 cumulative USD."""
        params = self._period_params(year_month_start, year_month_end, decade=False)
        params["pageNo"] = page_no
        params["numOfRows"] = num_rows
        return self._call("10day_export_country", params)

    def get_10day_import_by_country(
        self,
        year_month_start: Optional[str] = None,
        year_month_end: Optional[str] = None,
        page_no: int = 1,
        num_rows: int = 100,
    ) -> list[CustomsObservation]:
        params = self._period_params(year_month_start, year_month_end, decade=False)
        params["pageNo"] = page_no
        params["numOfRows"] = num_rows
        return self._call("10day_import_country", params)

    # ----- Multi-year wrappers (chunked 12-month windows) -------------

    def get_hs_country_history(
        self,
        hs_code: str,
        year_month_start: str,
        year_month_end: str,
        country_code: Optional[str] = None,
        num_rows: int = 1000,
    ) -> list[CustomsObservation]:
        """Long-range wrapper around ``get_hs_country_monthly``. data.go.kr
        TRASS enforces a max 12-month window per call, so for multi-year
        history (사장님 30년 백필) we loop in 12-month chunks and concatenate.

        Args use YYYYMM (inclusive). Returns one combined list — duplicates
        are deduped on (period, country_code, hs_code).
        """
        out: list[CustomsObservation] = []
        seen: set[tuple[str, Optional[str], Optional[str]]] = set()
        for chunk_start, chunk_end in _yyyymm_year_chunks(year_month_start, year_month_end):
            chunk = self.get_hs_country_monthly(
                hs_code=hs_code,
                year_month_start=chunk_start,
                year_month_end=chunk_end,
                country_code=country_code,
                num_rows=num_rows,
            )
            for obs in chunk:
                key = (obs.period, obs.country_code, obs.hs_code)
                if key in seen:
                    continue
                seen.add(key)
                out.append(obs)
        return out

    def get_country_history(
        self,
        country_code: str,
        year_month_start: str,
        year_month_end: str,
        num_rows: int = 1000,
    ) -> list[CustomsObservation]:
        """Long-range wrapper around ``get_country_monthly``."""
        out: list[CustomsObservation] = []
        seen: set[tuple[str, Optional[str], Optional[str]]] = set()
        for chunk_start, chunk_end in _yyyymm_year_chunks(year_month_start, year_month_end):
            chunk = self.get_country_monthly(
                country_code=country_code,
                year_month_start=chunk_start,
                year_month_end=chunk_end,
                num_rows=num_rows,
            )
            for obs in chunk:
                key = (obs.period, obs.country_code, obs.hs_code)
                if key in seen:
                    continue
                seen.add(key)
                out.append(obs)
        return out

    # ----- Internals --------------------------------------------------

    def _period_params(
        self,
        start: Optional[str],
        end: Optional[str],
        *,
        decade: bool,
    ) -> dict[str, Any]:
        out: dict[str, Any] = {"serviceKey": self.api_key}
        if decade:
            if start:
                out["strtYymmDd"] = start
            if end:
                out["endYymmDd"] = end
        else:
            if start:
                out["strtYymm"] = start
            if end:
                out["endYymm"] = end
        return out

    def _call(self, endpoint_key: str, params: dict[str, Any]) -> list[CustomsObservation]:
        if not self.api_key:
            raise AuthError("DATA_GO_KR_API_KEY not configured")
        path, _verified = ENDPOINTS[endpoint_key]
        resp = self._request("GET", f"{self.base_url}{path}", params=params)
        if resp.status_code == 403:
            raise AuthError(
                f"customs {endpoint_key} → 403 Forbidden. "
                "API likely not yet activated for this key on data.go.kr — "
                "wait for approval email or retry in a few minutes."
            )
        if resp.status_code != 200:
            raise AdapterError(
                f"customs {endpoint_key} → HTTP {resp.status_code}: {resp.text[:300]}"
            )
        return [self._row_to_obs(r) for r in self._parse_xml(resp.text)]

    @staticmethod
    def _parse_xml(body: str) -> list[dict[str, Any]]:
        try:
            root = ET.fromstring(body)
        except ET.ParseError as exc:
            raise AdapterError(f"customs response not valid XML: {exc}") from exc
        rc_el = root.find(".//resultCode")
        if rc_el is not None and rc_el.text and rc_el.text not in ("00", "0"):
            msg_el = root.find(".//resultMsg")
            raise AdapterError(
                f"customs API error {rc_el.text}: "
                f"{(msg_el.text if msg_el is not None else '')}"
            )
        items: list[dict[str, Any]] = []
        for item in root.iter("item"):
            rec = {child.tag: (child.text or "").strip() for child in item}
            items.append(rec)
        return items

    @staticmethod
    def _row_to_obs(rec: dict[str, Any]) -> CustomsObservation:
        # Field names confirmed live for hs_country_monthly:
        # year, hsCd, statKor (country), expDlr, impDlr, expWgt, impWgt, balPayments
        def _f(*keys: str) -> Optional[str]:
            for k in keys:
                v = rec.get(k)
                if v not in (None, "", " ", "-"):
                    return str(v).strip()
            return None

        def _num(*keys: str) -> Optional[float]:
            v = _f(*keys)
            if v is None:
                return None
            try:
                return float(v.replace(",", ""))
            except ValueError:
                return None

        # 10-day provisional country/item endpoints use priodMon + priodDt + itemUsdAmt00..10.
        # We collapse into the canonical schema by treating period = "<priodMon>:<priodDt>"
        # and value = itemUsdAmt00 (총합). Per-country detail stays in raw.
        period_mon = _f("priodMon")
        period_dt = _f("priodDt")
        if period_mon and period_dt:
            period = f"{period_mon}:{period_dt}"
            usd_total = _num("itemUsdAmt00")
            return CustomsObservation(
                period=period,
                hs_code=None,
                country_code=None,
                country_name=None,
                export_usd=usd_total,
                import_usd=None,
                export_kg=None,
                import_kg=None,
                export_count=None,
                import_count=None,
                trade_balance_usd=None,
                raw=rec,
            )

        period = _f("year", "yymm", "yymmDd", "ttlMm", "yyyymmdd") or ""
        country_name = _f("statCdCntnKor1", "statKor", "cntyNm", "ctyNm")
        return CustomsObservation(
            period=period,
            hs_code=_f("hsCd", "hsSgn", "hsSgnCd"),
            country_code=_f("statCd", "cntyCd", "ctyCd"),
            country_name=country_name,
            export_usd=_num("expDlr", "expUsd", "expAmt"),
            import_usd=_num("impDlr", "impUsd", "impAmt"),
            export_kg=_num("expWgt", "expKg"),
            import_kg=_num("impWgt", "impKg"),
            export_count=int(_num("expCnt") or 0) if _f("expCnt") else None,
            import_count=int(_num("impCnt") or 0) if _f("impCnt") else None,
            trade_balance_usd=_num("balPayments"),
            raw=rec,
        )


def _yyyymm_year_chunks(start: str, end: str) -> list[tuple[str, str]]:
    """Split a YYYYMM range into <=12-month chunks honouring TRASS's window cap.

    Inclusive on both ends. Returns ``[(chunk_start, chunk_end), ...]``.
    Examples:
        >>> _yyyymm_year_chunks("202301", "202612")
        [('202301', '202312'), ('202401', '202412'), ('202501', '202512'), ('202601', '202612')]
    """
    if len(start) != 6 or len(end) != 6:
        raise ValueError(f"YYYYMM expected, got start={start!r} end={end!r}")
    sy, sm = int(start[:4]), int(start[4:])
    ey, em = int(end[:4]), int(end[4:])
    if (sy, sm) > (ey, em):
        return []
    chunks: list[tuple[str, str]] = []
    cur_y, cur_m = sy, sm
    while (cur_y, cur_m) <= (ey, em):
        win_end_y = cur_y
        win_end_m = 12
        if (win_end_y, win_end_m) > (ey, em):
            win_end_y, win_end_m = ey, em
        chunks.append((f"{cur_y:04d}{cur_m:02d}", f"{win_end_y:04d}{win_end_m:02d}"))
        if win_end_m == 12:
            cur_y, cur_m = win_end_y + 1, 1
        else:
            cur_y, cur_m = win_end_y, win_end_m + 1
    return chunks
