"""OpenDART adapter.

Docs: https://opendart.fss.go.kr/guide/main.do
Primary endpoints:
  - /api/list.json                  — disclosure list
  - /api/fnlttSinglAcntAll.json     — full financial statements (XBRL-backed)
  - /api/company.json               — company meta
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Optional

from ky_adapters.base import AdapterError, AuthError, BaseAdapter

DART_BASE = "https://opendart.fss.or.kr/api"

# DART pattern: reprt_code
REPORT_CODES = {
    "annual": "11011",  # 사업보고서
    "q3": "11014",
    "semi": "11012",
    "q1": "11013",
}


@dataclass
class Filing:
    corp_code: str
    corp_name: str
    receipt_no: str
    report_name: str
    filed_at: str  # ISO YYYY-MM-DD
    filer_name: str | None = None

    def as_row(self, source_id: str = "dart") -> dict[str, Any]:
        return {
            "source_id": source_id,
            "corp_code": self.corp_code,
            "receipt_no": self.receipt_no,
            "filed_at": self.filed_at,
            "type": self.report_name,
            "summary": self.corp_name,
            "meta": {"filer_name": self.filer_name},
        }


class DARTAdapter(BaseAdapter):
    source_id = "dart"
    priority = 1

    def __init__(self, api_key: Optional[str] = None, base_url: str = DART_BASE) -> None:
        super().__init__()
        self.api_key = api_key
        self.base_url = base_url

    @classmethod
    def from_settings(cls) -> "DARTAdapter":
        return cls(api_key=cls._env("DART_API_KEY"))

    # --------- Contract ---------

    def healthcheck(self) -> dict[str, Any]:
        if not self.api_key:
            return self._timed_fail(self.source_id, "DART_API_KEY not configured")
        t0 = time.perf_counter()
        try:
            # Pull a tiny disclosure page as the probe.
            end = date.today()
            start = end - timedelta(days=7)
            resp = self._request(
                "GET",
                f"{self.base_url}/list.json",
                params={
                    "crtfc_key": self.api_key,
                    "bgn_de": _yyyymmdd(start),
                    "end_de": _yyyymmdd(end),
                    "page_no": 1,
                    "page_count": 1,
                },
            )
            resp.raise_for_status()
            body = resp.json()
            status = body.get("status")
            ok = status in ("000", "013")  # 013 = no rows but valid request
        except Exception as exc:  # noqa: BLE001
            return self._timed_fail(self.source_id, str(exc))
        latency_ms = (time.perf_counter() - t0) * 1000
        return self._timed_ok(latency_ms, self.source_id, {"status": status, "api_ok": ok})

    # --------- API surface ---------

    def list_disclosures(
        self,
        corp_code: str | None = None,
        start: date | str | None = None,
        end: date | str | None = None,
        page_no: int = 1,
        page_count: int = 20,
    ) -> list[Filing]:
        if not self.api_key:
            raise AuthError("DART_API_KEY not configured")
        params: dict[str, Any] = {
            "crtfc_key": self.api_key,
            "page_no": page_no,
            "page_count": min(page_count, 100),
        }
        if corp_code:
            params["corp_code"] = corp_code
        if start:
            params["bgn_de"] = _yyyymmdd(start)
        if end:
            params["end_de"] = _yyyymmdd(end)

        resp = self._request("GET", f"{self.base_url}/list.json", params=params)
        if resp.status_code != 200:
            raise AdapterError(f"DART list → HTTP {resp.status_code}: {resp.text[:200]}")
        body = resp.json()
        status = body.get("status")
        if status == "013":
            return []
        if status != "000":
            raise AdapterError(f"DART status={status}: {body.get('message')}")
        rows = body.get("list") or []
        out: list[Filing] = []
        for row in rows:
            out.append(
                Filing(
                    corp_code=row.get("corp_code", ""),
                    corp_name=row.get("corp_name", ""),
                    receipt_no=row.get("rcept_no", ""),
                    report_name=row.get("report_nm", ""),
                    filed_at=_iso_from_yyyymmdd(row.get("rcept_dt", "")),
                    filer_name=row.get("flr_nm"),
                )
            )
        return out

    def get_financial_statements(
        self,
        corp_code: str,
        year: int,
        report_code: str = REPORT_CODES["annual"],
        fs_div: str = "CFS",
    ) -> list[dict[str, Any]]:
        """Fetch full financial statements for (corp_code, year, report_code).

        ``fs_div``:
            - ``CFS`` — 연결재무제표
            - ``OFS`` — 별도재무제표
        """
        if not self.api_key:
            raise AuthError("DART_API_KEY not configured")
        params = {
            "crtfc_key": self.api_key,
            "corp_code": corp_code,
            "bsns_year": str(year),
            "reprt_code": report_code,
            "fs_div": fs_div,
        }
        resp = self._request("GET", f"{self.base_url}/fnlttSinglAcntAll.json", params=params)
        if resp.status_code != 200:
            raise AdapterError(f"DART fs → HTTP {resp.status_code}: {resp.text[:200]}")
        body = resp.json()
        status = body.get("status")
        if status == "013":
            return []
        if status != "000":
            raise AdapterError(f"DART fs status={status}: {body.get('message')}")
        return body.get("list", [])


# --------------------------------------------------------------------------- #
# Date helpers                                                                #
# --------------------------------------------------------------------------- #


def _yyyymmdd(d: date | str) -> str:
    if isinstance(d, date):
        return d.strftime("%Y%m%d")
    return str(d).replace("-", "")


def _iso_from_yyyymmdd(raw: str) -> str:
    raw = (raw or "").strip()
    if len(raw) == 8 and raw.isdigit():
        return f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"
    return raw
