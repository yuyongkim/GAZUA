"""Volume Profile (VPVR) — 60-day range distribution.

For each symbol, we bin the last 60 trading days into 40 price buckets
and accumulate volume per bucket. From the distribution we derive:

    * POC (Point of Control): the single price bucket with the greatest
      traded volume.
    * Value Area (VAH / VAL): the smallest price range that contains 70%
      of total volume around the POC.

We then classify where the current close sits relative to that structure
('NEAR_POC' | 'ABOVE_VAH' | 'BELOW_VAL' | 'INSIDE_VA' | 'OUTSIDE_VA').

Cost: O(60*40) per symbol; ~4500 symbols fit under 500ms.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from ky_core.scanning.loader import Panel


@dataclass
class VProfileHit:
    symbol: str
    name: str
    market: str
    sector: str
    close: float
    poc: float
    vah: float
    val: float
    price_to_poc_pct: float     # (close - poc) / poc * 100
    position: str               # 'NEAR_POC' | 'ABOVE_VAH' | 'BELOW_VAL' | 'INSIDE_VA' | 'OUTSIDE_VA'
    signal: str                 # 'SUPPORT' | 'RESISTANCE' | 'BREAKOUT' | 'BREAKDOWN' | 'NEUTRAL'
    tone: str
    value_area_pct: float       # (vah-val)/poc*100

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compute_vprofile(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    vols: list[float],
    *,
    lookback: int = 60,
    bins: int = 40,
    value_area: float = 0.70,
) -> dict[str, float] | None:
    """Bucket volumes by price over the last ``lookback`` bars."""
    n = len(closes)
    if n < lookback or bins < 5:
        return None
    tail = slice(n - lookback, n)
    hs = highs[tail]
    ls = lows[tail]
    cs = closes[tail]
    vs = vols[tail]

    hi = max(hs)
    lo = min(ls)
    if hi <= lo:
        return None
    bin_w = (hi - lo) / bins
    bucket_vol = [0.0] * bins
    for i in range(lookback):
        if vs[i] <= 0 or bin_w <= 0:
            continue
        # Distribute the day's volume across all buckets its H/L spans.
        lo_b = max(0, int((ls[i] - lo) / bin_w))
        hi_b = min(bins - 1, int((hs[i] - lo) / bin_w))
        span = max(1, hi_b - lo_b + 1)
        per = vs[i] / span
        for b in range(lo_b, hi_b + 1):
            bucket_vol[b] += per

    total = sum(bucket_vol)
    if total <= 0:
        return None

    # POC
    poc_i = max(range(bins), key=lambda i: bucket_vol[i])
    poc = lo + (poc_i + 0.5) * bin_w

    # Value area expansion around POC until we cover 70% of volume.
    target = total * value_area
    acc = bucket_vol[poc_i]
    low_i = high_i = poc_i
    while acc < target and (low_i > 0 or high_i < bins - 1):
        l_next = bucket_vol[low_i - 1] if low_i > 0 else -1
        h_next = bucket_vol[high_i + 1] if high_i < bins - 1 else -1
        if h_next >= l_next:
            if high_i < bins - 1:
                high_i += 1
                acc += bucket_vol[high_i]
            else:
                low_i -= 1
                acc += bucket_vol[low_i]
        else:
            if low_i > 0:
                low_i -= 1
                acc += bucket_vol[low_i]
            else:
                high_i += 1
                acc += bucket_vol[high_i]

    vah = lo + (high_i + 1.0) * bin_w
    val = lo + low_i * bin_w
    return {"poc": poc, "vah": vah, "val": val}


def _classify(close: float, poc: float, vah: float, val: float) -> tuple[str, str, str]:
    """→ (position, signal, tone)."""
    poc_tol = max(poc * 0.01, 1e-6)  # within 1% of POC → NEAR_POC
    if abs(close - poc) <= poc_tol:
        return ("NEAR_POC", "RESISTANCE" if close >= poc else "SUPPORT", "amber")
    if close > vah:
        # potential breakout above value area
        return ("ABOVE_VAH", "BREAKOUT", "pos")
    if close < val:
        return ("BELOW_VAL", "BREAKDOWN", "neg")
    # inside value area
    if close > poc:
        return ("INSIDE_VA", "RESISTANCE", "amber")
    return ("INSIDE_VA", "SUPPORT", "amber")


def scan_vprofile(
    *,
    panel: Panel,
    lookback: int = 60,
    bins: int = 40,
    min_close: float = 1000.0,
    limit: int = 300,
) -> list[VProfileHit]:
    hits: list[VProfileHit] = []
    for sym, rows in panel.series.items():
        if len(rows) < lookback:
            continue
        closes = [r["close"] for r in rows]
        highs = [r["high"] or r["close"] for r in rows]
        lows = [r["low"] or r["close"] for r in rows]
        vols = [float(r.get("volume") or 0) for r in rows]
        if closes[-1] is None or closes[-1] < min_close:
            continue

        vp = compute_vprofile(
            highs, lows, closes, vols, lookback=lookback, bins=bins
        )
        if vp is None:
            continue
        close = closes[-1]
        poc = vp["poc"]
        vah = vp["vah"]
        val = vp["val"]

        position, signal, tone = _classify(close, poc, vah, val)
        meta = panel.universe.get(sym, {})

        hits.append(
            VProfileHit(
                symbol=sym,
                name=meta.get("name") or sym,
                market=meta.get("market") or "UNKNOWN",
                sector=meta.get("sector") or "기타",
                close=float(close),
                poc=round(poc, 2),
                vah=round(vah, 2),
                val=round(val, 2),
                price_to_poc_pct=round((close - poc) / poc * 100 if poc > 0 else 0, 2),
                position=position,
                signal=signal,
                tone=tone,
                value_area_pct=round((vah - val) / poc * 100 if poc > 0 else 0, 2),
            )
        )

    # Sort: breakouts first, then near-POC, then the rest.
    def sk(h: VProfileHit) -> tuple:
        order = {"ABOVE_VAH": 0, "NEAR_POC": 1, "INSIDE_VA": 2, "BELOW_VAL": 3}
        return (order.get(h.position, 4), -abs(h.price_to_poc_pct))

    hits.sort(key=sk)
    return hits[:limit]


def summary_counts(hits: list[VProfileHit]) -> dict[str, int]:
    out = {"above_vah": 0, "near_poc": 0, "inside_va": 0, "below_val": 0, "breakout": 0, "breakdown": 0}
    for h in hits:
        if h.position == "ABOVE_VAH":
            out["above_vah"] += 1
        elif h.position == "NEAR_POC":
            out["near_poc"] += 1
        elif h.position == "INSIDE_VA":
            out["inside_va"] += 1
        elif h.position == "BELOW_VAL":
            out["below_val"] += 1
        if h.signal == "BREAKOUT":
            out["breakout"] += 1
        elif h.signal == "BREAKDOWN":
            out["breakdown"] += 1
    return out
