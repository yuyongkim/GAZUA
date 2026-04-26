"""Market breadth metrics over the entire KOSPI+KOSDAQ universe.

- advancers / decliners / unchanged
- % of symbols above SMA20 / SMA50 / SMA200
- new 52-week highs & lows
- up volume / down volume ratio
- McClellan oscillator (EMA19 - EMA39 of daily A/D diff, last 60 days)
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date as _date
from typing import Any

from ky_core.scanning.loader import Panel, load_panel


@dataclass
class BreadthSnapshot:
    as_of: str
    universe: int
    advancers: int
    decliners: int
    unchanged: int
    pct_above_sma20: float
    pct_above_sma50: float
    pct_above_sma200: float
    new_highs_52w: int
    new_lows_52w: int
    up_volume: int
    down_volume: int
    up_vol_pct: float
    mcclellan: float          # latest EMA19 - EMA39 of (adv - dec)
    ad_line_series: list[float]  # cumulative A/D for sparkline


def compute_breadth(
    as_of: _date | str | None = None,
    *,
    panel: Panel | None = None,
) -> BreadthSnapshot:
    panel = panel or load_panel(as_of)

    adv = dec = unc = 0
    above20 = above50 = above200 = 0
    counted = 0
    new_hi = new_lo = 0
    up_vol = dn_vol = 0

    # Pre-build per-day adv/dec counters for McClellan
    date_counts: dict[str, dict[str, int]] = {}

    for sym, rows in panel.series.items():
        if len(rows) < 30:
            continue
        closes = [r["close"] for r in rows]
        highs = [r["high"] or r["close"] for r in rows]
        lows = [r["low"] or r["close"] for r in rows]
        vols = [r.get("volume") or 0 for r in rows]

        counted += 1
        prev = closes[-2] if len(closes) >= 2 else closes[-1]
        last = closes[-1]
        if last > prev:
            adv += 1
            up_vol += vols[-1]
        elif last < prev:
            dec += 1
            dn_vol += vols[-1]
        else:
            unc += 1

        if last > _sma(closes, 20):
            above20 += 1
        if last > _sma(closes, 50):
            above50 += 1
        if len(closes) >= 200 and last > _sma(closes, 200):
            above200 += 1

        tail = highs[-252:] if len(highs) >= 252 else highs
        tail_lo = lows[-252:] if len(lows) >= 252 else lows
        if last >= max(tail) * 0.9995:
            new_hi += 1
        if last <= min(tail_lo) * 1.0005:
            new_lo += 1

        # Fill per-day A/D up to lookback 60 for McClellan
        window = rows[-61:] if len(rows) >= 61 else rows
        prev_c = None
        for r in window:
            d = r["date"]
            bucket = date_counts.setdefault(d, {"adv": 0, "dec": 0})
            if prev_c is not None:
                if r["close"] > prev_c:
                    bucket["adv"] += 1
                elif r["close"] < prev_c:
                    bucket["dec"] += 1
            prev_c = r["close"]

    total_vol = up_vol + dn_vol
    up_pct = (up_vol / total_vol * 100) if total_vol else 0.0

    dates_sorted = sorted(date_counts.keys())[-60:]
    diffs = [date_counts[d]["adv"] - date_counts[d]["dec"] for d in dates_sorted]
    mc_series = _mcclellan_series(diffs)
    mcclellan = mc_series[-1] if mc_series else 0.0
    ad_line = _cumulative(diffs)

    return BreadthSnapshot(
        as_of=panel.as_of,
        universe=counted,
        advancers=adv,
        decliners=dec,
        unchanged=unc,
        pct_above_sma20=_pct(above20, counted),
        pct_above_sma50=_pct(above50, counted),
        pct_above_sma200=_pct(above200, counted),
        new_highs_52w=new_hi,
        new_lows_52w=new_lo,
        up_volume=up_vol,
        down_volume=dn_vol,
        up_vol_pct=up_pct,
        mcclellan=mcclellan,
        ad_line_series=ad_line,
    )


def to_dict(b: BreadthSnapshot) -> dict[str, Any]:
    return asdict(b)


# --------------------------------------------------------------------------- #
# Internals                                                                   #
# --------------------------------------------------------------------------- #


def _sma(xs: list[float], n: int) -> float:
    if not xs:
        return 0.0
    window = xs[-n:] if len(xs) >= n else xs
    return sum(window) / len(window)


def _pct(part: int, total: int) -> float:
    return (part / total * 100) if total else 0.0


def _cumulative(xs: list[float]) -> list[float]:
    out: list[float] = []
    s = 0.0
    for v in xs:
        s += v
        out.append(s)
    return out


def _ema(xs: list[float], n: int) -> list[float]:
    if not xs:
        return []
    k = 2 / (n + 1)
    out = [float(xs[0])]
    for v in xs[1:]:
        out.append(out[-1] + k * (v - out[-1]))
    return out


def _mcclellan_series(diffs: list[float]) -> list[float]:
    if not diffs:
        return []
    e19 = _ema(diffs, 19)
    e39 = _ema(diffs, 39)
    return [e19[i] - e39[i] for i in range(len(diffs))]
