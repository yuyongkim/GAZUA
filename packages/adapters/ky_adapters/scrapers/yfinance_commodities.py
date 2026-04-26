"""yfinance-based global commodity prices.

Covers cells that the WICS-34 map references as "LME …" or "WTI …" or
"리튬 가격". yfinance returns cleanly-shaped daily OHLCV without an API key.

Verified symbols (2026-04-26):
  HG=F   COMEX copper futures        (cents/lb, proxy for LME copper)
  ALI=F  CME aluminum futures        (USD/ton)
  SI=F   COMEX silver futures        (USD/oz)
  GC=F   COMEX gold futures          (USD/oz)
  CL=F   WTI crude oil               (USD/bbl)

Known gaps: ^BDI / BDIY (delisted), LCO=F (Brent — delisted on Yahoo),
^SCFI (no data). For these the collector records FAIL — we acknowledge
upfront they don't have a free public source.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from ky_adapters.base import AdapterError, BaseAdapter


@dataclass
class CommodityRow:
    symbol: str
    date: str
    open: Optional[float]
    high: Optional[float]
    low: Optional[float]
    close: Optional[float]
    volume: Optional[float]
    raw: dict[str, Any] = field(default_factory=dict)

    def as_row(self, source_id: str = "yf_commodities") -> dict[str, Any]:
        return {
            "source_id": source_id,
            "symbol": self.symbol,
            "date": self.date,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }


def _import_yf():
    try:
        import yfinance as yf  # noqa: WPS433
    except ImportError as exc:
        raise AdapterError(
            "yfinance not installed. `pip install yfinance` to enable."
        ) from exc
    return yf


class YFinanceCommoditiesAdapter(BaseAdapter):
    source_id = "yf_commodities"
    priority = 7

    @classmethod
    def from_settings(cls) -> "YFinanceCommoditiesAdapter":
        return cls()

    def healthcheck(self) -> dict[str, Any]:
        t0 = time.perf_counter()
        try:
            rows = self.get_history("HG=F", period="5d")
            ok = len(rows) > 0
        except Exception as exc:  # noqa: BLE001
            return self._timed_fail(self.source_id, str(exc))
        latency_ms = (time.perf_counter() - t0) * 1000
        return self._timed_ok(latency_ms, self.source_id, {"sample_rows": len(rows)})

    def get_history(self, symbol: str, period: str = "1y") -> list[CommodityRow]:
        """``period`` accepts yfinance values: '5d', '1mo', '3mo', '1y', '2y'."""
        yf = _import_yf()
        try:
            t = yf.Ticker(symbol)
            h = t.history(period=period)
        except Exception as exc:  # noqa: BLE001
            raise AdapterError(f"yfinance {symbol} fetch failed: {exc}") from exc
        if h is None or h.empty:
            return []
        out: list[CommodityRow] = []
        for ts, row in h.iterrows():
            out.append(
                CommodityRow(
                    symbol=symbol,
                    date=ts.strftime("%Y-%m-%d"),
                    open=float(row["Open"]) if row.get("Open") is not None else None,
                    high=float(row["High"]) if row.get("High") is not None else None,
                    low=float(row["Low"]) if row.get("Low") is not None else None,
                    close=float(row["Close"]) if row.get("Close") is not None else None,
                    volume=float(row["Volume"]) if row.get("Volume") is not None else None,
                )
            )
        return out
