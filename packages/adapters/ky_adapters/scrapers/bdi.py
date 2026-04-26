"""BDI (Baltic Dry Index) scraper.

Pulls the daily BDI series from a publicly accessible mirror. There is no
official free API; we scrape one of the maritime data sites that publishes
the index. Stability is "low" — flag any wide changes for follow-up.

Primary source: tradingeconomics.com/commodity/baltic
Fallback:       investing.com/indices/baltic-dry historical
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

from ky_adapters.base import AdapterError, BaseAdapter

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


@dataclass
class BDIRow:
    date: str
    value: Optional[float]
    raw: dict[str, Any] = field(default_factory=dict)

    def as_row(self, source_id: str = "bdi") -> dict[str, Any]:
        return {"source_id": source_id, "date": self.date, "value": self.value}


class BDIScraperAdapter(BaseAdapter):
    source_id = "bdi"
    priority = 8

    def __init__(self) -> None:
        super().__init__()

    @classmethod
    def from_settings(cls) -> "BDIScraperAdapter":
        return cls()

    def healthcheck(self) -> dict[str, Any]:
        t0 = time.perf_counter()
        try:
            rows = self.get_latest()
            ok = len(rows) > 0
        except Exception as exc:  # noqa: BLE001
            return self._timed_fail(self.source_id, str(exc))
        latency_ms = (time.perf_counter() - t0) * 1000
        return self._timed_ok(latency_ms, self.source_id, {"sample_rows": len(rows)})

    def get_latest(self) -> list[BDIRow]:
        """Return the latest spot value (single row). Most maritime sites show
        only the most recent print on the public landing page."""
        url = "https://tradingeconomics.com/commodity/baltic"
        resp = self._request("GET", url, headers={"User-Agent": UA})
        if resp.status_code != 200:
            raise AdapterError(f"BDI scraper → HTTP {resp.status_code}")
        html = resp.text
        # tradingeconomics renders the latest value in the page header. The
        # exact layout uses a span like:
        #   <span ... id="last">1,234</span>
        # We tolerate alternates by matching on a commodity row.
        m = re.search(r'<span[^>]*id=["\']last["\'][^>]*>([\d,\.]+)</span>', html)
        if not m:
            m = re.search(r'Baltic Dry[^<]*</a>\s*</td>\s*<td[^>]*>([\d,\.]+)</td>', html, re.S)
        if not m:
            raise AdapterError("BDI scraper: could not locate value in HTML")
        try:
            value = float(m.group(1).replace(",", ""))
        except ValueError:
            raise AdapterError(f"BDI scraper: bad numeric '{m.group(1)}'")
        return [BDIRow(date=date.today().isoformat(), value=value, raw={"source_url": url})]
