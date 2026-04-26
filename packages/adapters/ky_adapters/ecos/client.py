"""Bank of Korea ECOS adapter.

Endpoint:
  http://ecos.bok.or.kr/api/StatisticSearch/{KEY}/json/kr/{START_ROW}/{END_ROW}/{STAT}/{FREQ}/{START}/{END}/{ITEM1}/{ITEM2}/{ITEM3}/{ITEM4}
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional

from ky_adapters.base import AdapterError, AuthError, BaseAdapter

ECOS_BASE = "http://ecos.bok.or.kr/api"


@dataclass
class ECOSObservation:
    stat_code: str
    item_code: str
    date: str  # raw from API (freq-dependent: 20240101 / 202401 / 2024)
    value: float | None
    unit: str | None = None

    def as_row(self, source_id: str = "ecos") -> dict[str, Any]:
        series_id = f"{self.stat_code}/{self.item_code}"
        return {
            "source_id": source_id,
            "series_id": series_id,
            "date": _normalise_date(self.date),
            "value": self.value,
            "unit": self.unit,
        }


class ECOSAdapter(BaseAdapter):
    source_id = "ecos"
    priority = 1

    def __init__(self, api_key: Optional[str] = None, base_url: str = ECOS_BASE) -> None:
        super().__init__()
        self.api_key = api_key
        self.base_url = base_url

    @classmethod
    def from_settings(cls) -> "ECOSAdapter":
        return cls(api_key=cls._env("ECOS_API_KEY"))

    # --------- Contract ---------

    def healthcheck(self) -> dict[str, Any]:
        if not self.api_key:
            return self._timed_fail(self.source_id, "ECOS_API_KEY not configured")
        t0 = time.perf_counter()
        try:
            # Service list call is light and reliable.
            url = f"{self.base_url}/StatisticTableList/{self.api_key}/json/kr/1/1"
            resp = self._request("GET", url)
            resp.raise_for_status()
            body = resp.json()
            # ECOS returns {"RESULT": {"CODE": "INFO-200", "MESSAGE": "해당하는 데이터가 없습니다."}}
            # on an empty list, which is still considered "reachable".
            ok = isinstance(body, dict) and (
                "StatisticTableList" in body or "RESULT" in body
            )
        except Exception as exc:  # noqa: BLE001
            return self._timed_fail(self.source_id, str(exc))
        latency_ms = (time.perf_counter() - t0) * 1000
        return self._timed_ok(latency_ms, self.source_id, {"api_ok": ok})

    # --------- API surface ---------

    def get_series(
        self,
        stat_code: str,
        item_code: str,
        start: str,
        end: str,
        freq: str = "D",
        page_size: int = 1000,
    ) -> list[ECOSObservation]:
        """Fetch observations for a single (stat_code, item_code) pair.

        Args:
            stat_code: e.g. "722Y001" (기준금리)
            item_code: e.g. "0101000"
            start / end: date string matching ``freq`` (D=YYYYMMDD, M=YYYYMM, Y=YYYY)
            freq: D/M/Q/Y

        ECOS caps each call at roughly ``list_total_count`` but requires an
        explicit ``start_row``/``end_row`` range. To cover 10-year daily
        series we page until the response is shorter than ``page_size``.
        """
        if not self.api_key:
            raise AuthError("ECOS_API_KEY not configured")
        out: list[ECOSObservation] = []
        start_row = 1
        while True:
            end_row = start_row + page_size - 1
            url = (
                f"{self.base_url}/StatisticSearch/{self.api_key}/json/kr/"
                f"{start_row}/{end_row}/{stat_code}/{freq}/{start}/{end}/{item_code}"
            )
            resp = self._request("GET", url)
            if resp.status_code != 200:
                raise AdapterError(
                    f"ECOS observations → HTTP {resp.status_code}: {resp.text[:200]}"
                )
            body = resp.json()
            if "StatisticSearch" not in body:
                result = body.get("RESULT") or {}
                code = result.get("CODE")
                msg = result.get("MESSAGE", "unknown ECOS error")
                if code in ("INFO-100", "INFO-200"):
                    return out
                raise AdapterError(f"ECOS error {code}: {msg}")
            rows = body["StatisticSearch"].get("row", [])
            total = body["StatisticSearch"].get("list_total_count")
            for row in rows:
                val_raw = row.get("DATA_VALUE")
                try:
                    val = float(val_raw) if val_raw not in (None, "") else None
                except ValueError:
                    val = None
                out.append(
                    ECOSObservation(
                        stat_code=stat_code,
                        item_code=item_code,
                        date=row.get("TIME", ""),
                        value=val,
                        unit=row.get("UNIT_NAME"),
                    )
                )
            # Stop when we've read the last page
            if len(rows) < page_size:
                return out
            if isinstance(total, int) and end_row >= total:
                return out
            start_row = end_row + 1


def _normalise_date(raw: str) -> str:
    """Normalise ECOS date strings to ISO ``YYYY-MM-DD`` where possible.

    Cycle-dependent inputs:

    - ``D`` — ``YYYYMMDD``
    - ``M`` — ``YYYYMM``
    - ``Q`` — ``YYYYQn`` (map to first day of the quarter)
    - ``A`` / ``Y`` — ``YYYY``
    """
    raw = (raw or "").strip()
    if len(raw) == 8 and raw.isdigit():
        return f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"
    if len(raw) == 6 and raw.isdigit():
        return f"{raw[0:4]}-{raw[4:6]}-01"
    # Quarterly: YYYYQn -> first day of that quarter
    if len(raw) == 6 and raw[4] in ("Q", "q") and raw[:4].isdigit() and raw[5].isdigit():
        q = int(raw[5])
        month = {1: "01", 2: "04", 3: "07", 4: "10"}.get(q, "01")
        return f"{raw[:4]}-{month}-01"
    if len(raw) == 4 and raw.isdigit():
        return f"{raw}-01-01"
    return raw
