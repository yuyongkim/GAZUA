"""FRED (St. Louis Fed) adapter.

Fetches observations from https://api.stlouisfed.org/fred/series/observations.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Optional

from ky_adapters.base import AdapterError, AuthError, BaseAdapter

FRED_BASE = "https://api.stlouisfed.org/fred"


@dataclass
class Observation:
    series_id: str
    date: str  # ISO YYYY-MM-DD
    value: float | None
    unit: str | None = None

    def as_row(self, source_id: str = "fred") -> dict[str, Any]:
        return {
            "source_id": source_id,
            "series_id": self.series_id,
            "date": self.date,
            "value": self.value,
            "unit": self.unit,
        }


class FREDAdapter(BaseAdapter):
    source_id = "fred"
    priority = 1

    def __init__(self, api_key: Optional[str] = None, base_url: str = FRED_BASE) -> None:
        super().__init__()
        self.api_key = api_key
        self.base_url = base_url

    @classmethod
    def from_settings(cls) -> "FREDAdapter":
        return cls(api_key=cls._env("FRED_API_KEY"))

    # --------- Contract ---------

    def healthcheck(self) -> dict[str, Any]:
        if not self.api_key:
            return self._timed_fail(self.source_id, "FRED_API_KEY not configured")
        t0 = time.perf_counter()
        try:
            # lightweight call: fetch series metadata for a well-known series
            resp = self._request(
                "GET",
                f"{self.base_url}/series",
                params={"series_id": "GDP", "api_key": self.api_key, "file_type": "json"},
            )
            resp.raise_for_status()
            body = resp.json()
            ok = "seriess" in body and len(body["seriess"]) > 0
        except Exception as exc:  # noqa: BLE001
            return self._timed_fail(self.source_id, str(exc))
        latency_ms = (time.perf_counter() - t0) * 1000
        return self._timed_ok(latency_ms, self.source_id, {"series_probed": "GDP", "api_ok": ok})

    # --------- API surface ---------

    def get_series(
        self,
        series_id: str,
        start: date | str | None = None,
        end: date | str | None = None,
        limit: int = 100000,
    ) -> list[Observation]:
        if not self.api_key:
            raise AuthError("FRED_API_KEY not configured")
        params: dict[str, Any] = {
            "series_id": series_id,
            "api_key": self.api_key,
            "file_type": "json",
            "limit": limit,
        }
        if start:
            params["observation_start"] = _iso(start)
        if end:
            params["observation_end"] = _iso(end)

        resp = self._request("GET", f"{self.base_url}/series/observations", params=params)
        if resp.status_code != 200:
            raise AdapterError(f"FRED observations → HTTP {resp.status_code}: {resp.text[:200]}")
        payload = resp.json()
        rows = payload.get("observations", [])
        unit = self._try_get_unit(series_id)
        out: list[Observation] = []
        for row in rows:
            raw_val = row.get("value")
            val: float | None
            if raw_val in (None, ".", ""):
                val = None
            else:
                try:
                    val = float(raw_val)
                except ValueError:
                    val = None
            out.append(Observation(series_id=series_id, date=row["date"], value=val, unit=unit))
        return out

    # --------- Internal ---------

    def _try_get_unit(self, series_id: str) -> str | None:
        try:
            resp = self._request(
                "GET",
                f"{self.base_url}/series",
                params={"series_id": series_id, "api_key": self.api_key, "file_type": "json"},
            )
            if resp.status_code == 200:
                body = resp.json()
                items = body.get("seriess") or []
                if items:
                    return items[0].get("units")
        except Exception:  # noqa: BLE001
            return None
        return None


def _iso(d: date | str) -> str:
    if isinstance(d, date):
        return d.isoformat()
    if isinstance(d, datetime):
        return d.date().isoformat()
    return str(d)
