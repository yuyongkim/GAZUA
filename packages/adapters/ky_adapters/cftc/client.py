"""CFTC COT (Commitments of Traders) adapter.

The CFTC publishes weekly position data via Socrata at publicreporting.cftc.gov.
Public, no key required.

Datasets:
  - 6dca-aqww  Disaggregated futures-only (current canonical)
  - kh3c-gbw2  Legacy futures-only

Useful columns:
  market_and_exchange_names, report_date_as_yyyy_mm_dd,
  managed_money_long_all, managed_money_short_all,
  prod_merc_positions_long_all, prod_merc_positions_short_all
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from ky_adapters.base import AdapterError, BaseAdapter

CFTC_BASE = "https://publicreporting.cftc.gov/resource"
DEFAULT_DATASET = "6dca-aqww"


@dataclass
class COTRow:
    market: str
    report_date: str
    managed_money_long: Optional[int]
    managed_money_short: Optional[int]
    commercial_long: Optional[int]
    commercial_short: Optional[int]
    raw: dict[str, Any] = field(default_factory=dict)

    def as_row(self, source_id: str = "cftc") -> dict[str, Any]:
        return {
            "source_id": source_id,
            "market": self.market,
            "report_date": self.report_date,
            "managed_money_long": self.managed_money_long,
            "managed_money_short": self.managed_money_short,
            "commercial_long": self.commercial_long,
            "commercial_short": self.commercial_short,
        }


class CFTCAdapter(BaseAdapter):
    source_id = "cftc"
    priority = 4

    def __init__(self, base_url: str = CFTC_BASE, dataset: str = DEFAULT_DATASET) -> None:
        super().__init__()
        self.base_url = base_url
        self.dataset = dataset

    @classmethod
    def from_settings(cls) -> "CFTCAdapter":
        return cls()

    def healthcheck(self) -> dict[str, Any]:
        t0 = time.perf_counter()
        try:
            rows = self.search_market("CRUDE OIL", limit=5)
            ok = len(rows) > 0
        except Exception as exc:  # noqa: BLE001
            return self._timed_fail(self.source_id, str(exc))
        latency_ms = (time.perf_counter() - t0) * 1000
        return self._timed_ok(latency_ms, self.source_id, {"sample_rows": len(rows)})

    def search_market(self, market_keyword: str, limit: int = 50) -> list[COTRow]:
        """Filter rows whose market_and_exchange_names contains the keyword
        (case-insensitive). Returns most-recent-first."""
        params = {
            "$where": f"upper(market_and_exchange_names) like '%{market_keyword.upper()}%'",
            "$order": "report_date_as_yyyy_mm_dd DESC",
            "$limit": limit,
        }
        return self._fetch(params)

    def latest_for_markets(self, markets: list[str]) -> list[COTRow]:
        """Returns the latest row for each provided market name (exact match)."""
        out: list[COTRow] = []
        for m in markets:
            rows = self._fetch(
                {
                    "market_and_exchange_names": m,
                    "$order": "report_date_as_yyyy_mm_dd DESC",
                    "$limit": 1,
                }
            )
            out.extend(rows)
        return out

    def _fetch(self, params: dict[str, Any]) -> list[COTRow]:
        url = f"{self.base_url}/{self.dataset}.json"
        resp = self._request("GET", url, params=params)
        if resp.status_code != 200:
            raise AdapterError(f"CFTC → HTTP {resp.status_code}: {resp.text[:300]}")
        payload = resp.json()
        if not isinstance(payload, list):
            return []
        rows: list[COTRow] = []
        for r in payload:
            rows.append(
                COTRow(
                    market=r.get("market_and_exchange_names") or "",
                    report_date=r.get("report_date_as_yyyy_mm_dd") or "",
                    managed_money_long=_int(r.get("m_money_positions_long_all")),
                    managed_money_short=_int(r.get("m_money_positions_short_all")),
                    commercial_long=_int(r.get("prod_merc_positions_long_all")),
                    commercial_short=_int(r.get("prod_merc_positions_short_all")),
                    raw=r,
                )
            )
        return rows


def _int(v: Any) -> Optional[int]:
    if v is None or v == "":
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None
