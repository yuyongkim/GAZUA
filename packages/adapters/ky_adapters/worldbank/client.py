"""World Bank Open Data adapter.

No API key needed. JSON REST at api.worldbank.org/v2.
Common indicators we need:
  - NY.GDP.MKTP.CD       Nominal GDP (USD)
  - NY.GDP.MKTP.KD.ZG    GDP growth (annual %)
  - NV.IND.MANF.ZS       Manufacturing % of GDP
  - SP.POP.TOTL          Total population
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional

from ky_adapters.base import AdapterError, BaseAdapter

WB_BASE = "https://api.worldbank.org/v2"


@dataclass
class WBObservation:
    indicator: str
    country_iso: str
    year: int
    value: Optional[float]

    def as_row(self, source_id: str = "worldbank") -> dict[str, Any]:
        return {
            "source_id": source_id,
            "indicator": self.indicator,
            "country_iso": self.country_iso,
            "year": self.year,
            "value": self.value,
        }


class WorldBankAdapter(BaseAdapter):
    source_id = "worldbank"
    priority = 3

    def __init__(self, base_url: str = WB_BASE) -> None:
        super().__init__()
        self.base_url = base_url

    @classmethod
    def from_settings(cls) -> "WorldBankAdapter":
        return cls()

    def healthcheck(self) -> dict[str, Any]:
        t0 = time.perf_counter()
        try:
            obs = self.get_indicator("KOR", "NY.GDP.MKTP.CD", date_range="2020:2024")
            ok = len(obs) > 0
        except Exception as exc:  # noqa: BLE001
            return self._timed_fail(self.source_id, str(exc))
        latency_ms = (time.perf_counter() - t0) * 1000
        return self._timed_ok(latency_ms, self.source_id, {"sample_rows": len(obs)})

    def get_indicator(
        self,
        country_iso: str,
        indicator: str,
        date_range: Optional[str] = None,
        per_page: int = 200,
    ) -> list[WBObservation]:
        """country_iso: ISO3 like 'KOR'/'USA'/'CHN' or 'all'.
        date_range: 'YYYY:YYYY' (e.g. '2010:2024')."""
        url = f"{self.base_url}/country/{country_iso}/indicator/{indicator}"
        params: dict[str, Any] = {"format": "json", "per_page": per_page}
        if date_range:
            params["date"] = date_range
        resp = self._request("GET", url, params=params)
        if resp.status_code != 200:
            raise AdapterError(
                f"WorldBank {indicator} → HTTP {resp.status_code}: {resp.text[:300]}"
            )
        payload = resp.json()
        # WB returns [meta, data]
        if not isinstance(payload, list) or len(payload) < 2:
            return []
        rows = payload[1] or []
        out: list[WBObservation] = []
        for row in rows:
            year_s = row.get("date")
            try:
                year = int(year_s)
            except (TypeError, ValueError):
                continue
            value = row.get("value")
            try:
                value = float(value) if value is not None else None
            except (TypeError, ValueError):
                value = None
            out.append(
                WBObservation(
                    indicator=indicator,
                    country_iso=row.get("countryiso3code") or country_iso,
                    year=year,
                    value=value,
                )
            )
        # WB returns most-recent-first; reverse to chronological
        out.reverse()
        return out
