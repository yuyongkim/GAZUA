"""52-week breakout scanner.

Two scans live here:

* :func:`scan_breakouts` — today's close is at (or within 0.5 % of) the 252
  day closing/intraday high AND volume expansion gate cleared. Think
  "broken out today" — these are the stocks traders act on immediately.
* :func:`scan_near_52w` — close sits within ``proximity_pct`` (default 2 %)
  of the 252 day high. Breakout *candidates* — the stocks traders stalk.

The old hard "vol >= 1.5x" gate was so strict that with our current 2026-04-17
panel it returned a single row. We relaxed the default to 1.0x (i.e. "not
noticeably below average") and widened the "close ≥ high" check to 0.995.
Callers that want the original behaviour pass ``vol_x_min=1.5``.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date as _date
from typing import Any

from ky_core.scanning.loader import Panel, load_panel


@dataclass
class BreakoutRow:
    symbol: str
    name: str
    market: str
    sector: str
    close: float
    pct_up: float        # 1-day change pct
    vol_x: float         # today vol / 50d avg
    high52w: float
    dist_from_high_pct: float = 0.0  # (high - close) / high * 100  (>=0)


def scan_breakouts(
    as_of: _date | str | None = None,
    *,
    panel: Panel | None = None,
    vol_x_min: float = 1.0,
    breakout_tolerance: float = 0.995,  # close >= high * 0.995
    limit: int = 100,
) -> list[BreakoutRow]:
    """Stocks that broke, or essentially tied, their 252-day high today.

    ``breakout_tolerance`` is multiplied against the 252-day high — any close
    at or above that threshold is treated as a breakout. 0.995 -> within
    0.5 % (covers post-close adjustment wobble and intraday ticks).
    """
    panel = panel or load_panel(as_of)
    out: list[BreakoutRow] = []
    for sym, rows in panel.series.items():
        if len(rows) < 60:
            continue
        closes = [r["close"] for r in rows]
        highs = [r["high"] or r["close"] for r in rows]
        vols = [r.get("volume") or 0 for r in rows]

        hi52 = max(highs[-252:]) if len(highs) >= 252 else max(highs)
        close = closes[-1]
        if hi52 <= 0:
            continue
        if close < hi52 * breakout_tolerance:
            continue

        avg50 = sum(vols[-50:]) / max(1, min(50, len(vols)))
        vol_x = (vols[-1] / avg50) if avg50 > 0 else 0.0
        if vol_x < vol_x_min:
            continue

        prev = closes[-2] if len(closes) >= 2 else close
        pct_up = (close / prev - 1.0) * 100 if prev > 0 else 0.0
        dist = max(0.0, (hi52 - close) / hi52 * 100)

        meta = panel.universe.get(sym, {})
        if meta.get("market") in (None, "UNKNOWN"):
            continue
        out.append(
            BreakoutRow(
                symbol=sym,
                name=meta.get("name") or sym,
                market=meta.get("market") or "UNKNOWN",
                sector=meta.get("sector") or "기타",
                close=close,
                pct_up=pct_up,
                vol_x=vol_x,
                high52w=hi52,
                dist_from_high_pct=round(dist, 3),
            )
        )
    # Rank: strongest vol × pct_up first; ties broken by proximity to the high.
    out.sort(key=lambda b: (b.vol_x * max(0.5, b.pct_up / 5), -b.dist_from_high_pct), reverse=True)
    return out[:limit]


def scan_near_52w(
    as_of: _date | str | None = None,
    *,
    panel: Panel | None = None,
    proximity_pct: float = 2.0,
    vol_x_min: float = 0.7,
    limit: int = 150,
) -> list[BreakoutRow]:
    """Stocks trading within ``proximity_pct`` of their 252-day high.

    Softer filter than :func:`scan_breakouts` — volume gate is lowered to
    0.7 × so we do not drop setups that are coiling on quiet tape. Output
    rows are ordered by proximity (closest-to-high first) then vol_x.
    """
    panel = panel or load_panel(as_of)
    out: list[BreakoutRow] = []
    for sym, rows in panel.series.items():
        if len(rows) < 60:
            continue
        closes = [r["close"] for r in rows]
        highs = [r["high"] or r["close"] for r in rows]
        vols = [r.get("volume") or 0 for r in rows]

        hi52 = max(highs[-252:]) if len(highs) >= 252 else max(highs)
        close = closes[-1]
        if hi52 <= 0:
            continue
        dist_pct = (hi52 - close) / hi52 * 100
        if dist_pct < 0 or dist_pct > proximity_pct:
            continue

        avg50 = sum(vols[-50:]) / max(1, min(50, len(vols)))
        vol_x = (vols[-1] / avg50) if avg50 > 0 else 0.0
        if vol_x < vol_x_min:
            continue

        prev = closes[-2] if len(closes) >= 2 else close
        pct_up = (close / prev - 1.0) * 100 if prev > 0 else 0.0

        meta = panel.universe.get(sym, {})
        if meta.get("market") in (None, "UNKNOWN"):
            continue
        out.append(
            BreakoutRow(
                symbol=sym,
                name=meta.get("name") or sym,
                market=meta.get("market") or "UNKNOWN",
                sector=meta.get("sector") or "기타",
                close=close,
                pct_up=pct_up,
                vol_x=vol_x,
                high52w=hi52,
                dist_from_high_pct=round(dist_pct, 3),
            )
        )
    out.sort(key=lambda b: (b.dist_from_high_pct, -b.vol_x))
    return out[:limit]


def to_dict(b: BreakoutRow) -> dict[str, Any]:
    return asdict(b)
