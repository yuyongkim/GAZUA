"""Stooq.com HTML quote scraper — fallback when yfinance lacks the symbol.

Stooq rate-limits aggressively (≈ 2-3 calls per 10 seconds before returning
empty bodies). Adapter sleeps 5s between calls in the get_many() helper.

Verified-extracting symbols (2026-04-26):
  hg.f  COMEX copper futures
  al.c  Aluminum cash
  (most other symbols return empty — Stooq blocks)
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from ky_adapters.base import AdapterError, BaseAdapter

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36"


@dataclass
class StooqRow:
    symbol: str
    date: str
    value: Optional[float]
    raw: dict[str, Any] = field(default_factory=dict)

    def as_row(self, source_id: str = "stooq") -> dict[str, Any]:
        return {"source_id": source_id, "symbol": self.symbol,
                "date": self.date, "value": self.value}


class StooqQuoteAdapter(BaseAdapter):
    source_id = "stooq"
    priority = 8

    @classmethod
    def from_settings(cls) -> "StooqQuoteAdapter":
        return cls()

    def healthcheck(self) -> dict[str, Any]:
        t0 = time.perf_counter()
        try:
            rows = self.get_quote("hg.f")
            ok = len(rows) > 0
        except Exception as exc:  # noqa: BLE001
            return self._timed_fail(self.source_id, str(exc))
        latency_ms = (time.perf_counter() - t0) * 1000
        return self._timed_ok(latency_ms, self.source_id, {"sample_rows": len(rows)})

    def get_quote(self, symbol: str) -> list[StooqRow]:
        url = f"https://stooq.com/q/?s={symbol}"
        resp = self._request("GET", url, headers={"User-Agent": UA})
        if resp.status_code != 200 or len(resp.text) < 1000:
            raise AdapterError(
                f"stooq {symbol} blocked or empty (status={resp.status_code}, "
                f"len={len(resp.text)})"
            )
        sym_re = re.escape(symbol)
        m_value = re.search(rf'aq_{sym_re}_c[^>]*>([\d.,]+)', resp.text)
        m_date = re.search(rf'aq_{sym_re}_d2[^>]*>([\d\-]+)', resp.text)
        if not m_value:
            raise AdapterError(f"stooq {symbol}: value pattern not found")
        try:
            value = float(m_value.group(1).replace(",", ""))
        except ValueError as exc:
            raise AdapterError(f"stooq {symbol}: bad numeric '{m_value.group(1)}'") from exc
        date = m_date.group(1) if m_date else ""
        return [StooqRow(symbol=symbol, date=date, value=value)]

    def get_many(self, symbols: list[str], delay_s: float = 5.0) -> list[StooqRow]:
        """Fetch many symbols with delay to avoid rate-limit."""
        out: list[StooqRow] = []
        for i, s in enumerate(symbols):
            try:
                out.extend(self.get_quote(s))
            except AdapterError:
                pass
            if i < len(symbols) - 1:
                time.sleep(delay_s)
        return out
