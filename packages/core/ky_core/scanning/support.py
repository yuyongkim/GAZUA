"""Support/Resistance auto-detection.

Ported (and simplified) from
``QuantPlatform/analysis/technical/professional_technical_analysis.py::
find_support_resistance_levels``. Two sources of levels are combined:

    1. Long-range pivot highs/lows: over the last 252 bars, any bar
       whose high is within 2% of its ±window max is a resistance
       candidate; whose low is within 2% of its ±window min is a support
       candidate. Duplicates within 5% are merged.
    2. Classical floor pivot point (from yesterday's OHLC): P = (H+L+C)/3,
       R1/S1 = 2P - L / 2P - H. Added as "pivot" levels.

For each symbol we return the TOP 5 supports + TOP 5 resistances sorted
by proximity to today's close, and classify today's close state ('AT_S'
| 'AT_R' | 'MID').
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from ky_core.scanning.loader import Panel


@dataclass
class SRLevel:
    price: float
    kind: str          # 'S' | 'R' | 'PIVOT'
    strength: int      # # of bars within 5% band
    distance_pct: float  # (price - close) / close * 100 — signed


@dataclass
class SRHit:
    symbol: str
    name: str
    market: str
    sector: str
    close: float
    state: str                 # 'AT_S' | 'AT_R' | 'MID'
    nearest_support: float | None
    nearest_resistance: float | None
    dist_support_pct: float | None
    dist_resistance_pct: float | None
    levels: list[dict[str, Any]]   # top supports + resistances (<=10)
    tone: str

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


def find_levels(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    *,
    window: int = 20,
    lookback: int = 252,
    dedupe_pct: float = 0.05,
) -> tuple[list[SRLevel], list[SRLevel]]:
    """Long-range pivot-based S/R. Returns (supports, resistances)."""
    n = len(closes)
    if n < 2 * window + 5:
        return ([], [])
    start = max(window, n - lookback)
    end = n - window  # need window of bars forward
    close_now = closes[-1]

    supports: list[SRLevel] = []
    resistances: list[SRLevel] = []
    for i in range(start, end):
        hi_win = highs[i - window : i + window]
        lo_win = lows[i - window : i + window]
        if not hi_win or not lo_win:
            continue
        cur_hi = highs[i]
        cur_lo = lows[i]
        # Resistance candidate
        if cur_hi >= max(hi_win) * 0.98 and _unique(cur_hi, resistances, dedupe_pct):
            strength = sum(1 for v in highs[i - window : i + window] if v >= cur_hi * 0.95)
            resistances.append(
                SRLevel(
                    price=round(cur_hi, 2),
                    kind="R",
                    strength=strength,
                    distance_pct=round((cur_hi - close_now) / close_now * 100, 2) if close_now > 0 else 0.0,
                )
            )
        if cur_lo <= min(lo_win) * 1.02 and _unique(cur_lo, supports, dedupe_pct):
            strength = sum(1 for v in lows[i - window : i + window] if v <= cur_lo * 1.05)
            supports.append(
                SRLevel(
                    price=round(cur_lo, 2),
                    kind="S",
                    strength=strength,
                    distance_pct=round((cur_lo - close_now) / close_now * 100, 2) if close_now > 0 else 0.0,
                )
            )
    return (supports, resistances)


def _unique(price: float, existing: list[SRLevel], tol: float) -> bool:
    for lv in existing:
        if lv.price > 0 and abs(price - lv.price) / lv.price < tol:
            return False
    return True


def pivot_levels(o: float, h: float, l: float, c: float, close_now: float) -> list[SRLevel]:
    """Classical floor pivots from yesterday's OHLC."""
    p = (h + l + c) / 3.0
    r1 = 2 * p - l
    s1 = 2 * p - h
    out = []
    for price, kind in ((s1, "S"), (p, "PIVOT"), (r1, "R")):
        out.append(
            SRLevel(
                price=round(price, 2),
                kind=kind,
                strength=1,
                distance_pct=round((price - close_now) / close_now * 100, 2) if close_now > 0 else 0.0,
            )
        )
    return out


def _state(close: float, nearest_s: float | None, nearest_r: float | None, band: float = 0.015) -> str:
    """Determine if price is at support, at resistance, or in the middle."""
    if nearest_s is not None and close > 0 and abs(close - nearest_s) / close <= band:
        return "AT_S"
    if nearest_r is not None and close > 0 and abs(close - nearest_r) / close <= band:
        return "AT_R"
    return "MID"


def scan_support_resistance(
    *,
    panel: Panel,
    limit: int = 300,
    min_close: float = 1000.0,
    max_levels: int = 10,
) -> list[SRHit]:
    hits: list[SRHit] = []
    for sym, rows in panel.series.items():
        if len(rows) < 80:
            continue
        closes = [r["close"] for r in rows]
        highs = [r["high"] or r["close"] for r in rows]
        lows = [r["low"] or r["close"] for r in rows]
        opens = [r["open"] or r["close"] for r in rows]
        if closes[-1] is None or closes[-1] < min_close:
            continue
        close = closes[-1]

        supports, resistances = find_levels(highs, lows, closes)

        # Add floor pivot from yesterday's OHLC
        if len(rows) >= 2:
            pivs = pivot_levels(opens[-2], highs[-2], lows[-2], closes[-2], close_now=close)
            for lv in pivs:
                if lv.kind == "S" and _unique(lv.price, supports, 0.03):
                    supports.append(lv)
                elif lv.kind == "R" and _unique(lv.price, resistances, 0.03):
                    resistances.append(lv)

        # Rank supports below close (or at most marginally above), resistances above close.
        supports_below = [s for s in supports if s.price <= close * 1.005]
        supports_below.sort(key=lambda s: close - s.price)  # closest below first
        resistances_above = [r for r in resistances if r.price >= close * 0.995]
        resistances_above.sort(key=lambda r: r.price - close)

        nearest_s = supports_below[0].price if supports_below else None
        nearest_r = resistances_above[0].price if resistances_above else None
        dist_s = ((nearest_s - close) / close * 100) if nearest_s else None
        dist_r = ((nearest_r - close) / close * 100) if nearest_r else None
        state = _state(close, nearest_s, nearest_r)

        merged = (supports_below[:5] + resistances_above[:5])[:max_levels]
        levels_dict = [asdict(lv) for lv in merged]

        meta = panel.universe.get(sym, {})
        if state == "AT_S":
            tone = "pos"
        elif state == "AT_R":
            tone = "neg"
        else:
            tone = "neutral"
        hits.append(
            SRHit(
                symbol=sym,
                name=meta.get("name") or sym,
                market=meta.get("market") or "UNKNOWN",
                sector=meta.get("sector") or "기타",
                close=float(close),
                state=state,
                nearest_support=nearest_s,
                nearest_resistance=nearest_r,
                dist_support_pct=round(dist_s, 2) if dist_s is not None else None,
                dist_resistance_pct=round(dist_r, 2) if dist_r is not None else None,
                levels=levels_dict,
                tone=tone,
            )
        )

    # Sort: AT_S first (buy candidates), then AT_R, then MID by |dist|.
    def sk(h: SRHit) -> tuple:
        order = {"AT_S": 0, "AT_R": 1, "MID": 2}
        near = 1e9
        if h.dist_support_pct is not None:
            near = abs(h.dist_support_pct)
        if h.dist_resistance_pct is not None:
            near = min(near, abs(h.dist_resistance_pct))
        return (order.get(h.state, 3), near)

    hits.sort(key=sk)
    return hits[:limit]


def summary_counts(hits: list[SRHit]) -> dict[str, int]:
    out = {"at_support": 0, "at_resistance": 0, "mid": 0}
    for h in hits:
        if h.state == "AT_S":
            out["at_support"] += 1
        elif h.state == "AT_R":
            out["at_resistance"] += 1
        else:
            out["mid"] += 1
    return out
