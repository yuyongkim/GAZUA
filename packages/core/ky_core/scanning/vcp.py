"""Simplified VCP (Volatility Contraction Pattern) detector + base-pattern family.

Full VCP detection requires wave-by-wave pivot tracking; for the
EOD Chartist dashboard we use a robust approximation:

  1. Compute rolling 20-day true-range volatility (pct).
  2. Count how many successive 20-day windows over the last 80 days
     show a decreasing volatility (contraction step).
  3. Verify the last 20-day avg volume is < 60% of the 60-day avg
     (volume dry-up).
  4. Report "stage" 1..4 based on contraction count & proximity to 52w
     high.

This buys us a deterministic ``VCPStatus`` — coarse, but close enough
for the scanning count columns on the dashboard.

In addition to VCP, this module classifies three sibling base patterns
via :func:`detect_base_pattern`:

    * Cup with Handle — rounded 20+ bar cup (drawdown 12-35%) followed
      by a 5-15 bar handle that retraces <= 50% of the cup depth with
      declining volume.
    * Flat Base — 20+ bar corridor where high-low range <= 15%, after
      a prior 25%+ advance.
    * Ascending Triangle — flat highs (within 3%) + rising lows
      (successive pivots up).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ky_core.scanning.loader import Panel


@dataclass(frozen=True)
class VCPStatus:
    symbol: str
    stage: int                 # 0 none, 1..3 progressive, 4 breakout-ready
    contractions: int
    last_vol_ratio: float      # latest 20d vol / prev 20d vol
    volume_dry_up: bool
    pct_of_52w_high: float     # close / high52w
    score: float               # 0..1 composite
    label: str                 # 'VCP' | 'Base' | 'B.out' | '—'


# Panel-scoped memo cache. scan_leaders + minervini both call detect_vcp on
# every symbol; memoising collapses those two passes into one compute.
_vcp_cache: dict[tuple[str, str], "VCPStatus"] = {}
_vcp_cache_key_as_of: str | None = None


def detect_vcp(
    symbol: str,
    *,
    panel: Panel,
) -> VCPStatus:
    global _vcp_cache_key_as_of
    if _vcp_cache_key_as_of != panel.as_of:
        _vcp_cache.clear()
        _vcp_cache_key_as_of = panel.as_of
    cache_key = (panel.as_of, symbol)
    cached = _vcp_cache.get(cache_key)
    if cached is not None:
        return cached

    rows = panel.series.get(symbol) or []
    if len(rows) < 80:
        out = _empty(symbol)
        _vcp_cache[cache_key] = out
        return out

    closes = [r["close"] for r in rows]
    highs = [r["high"] or r["close"] for r in rows]
    lows = [r["low"] or r["close"] for r in rows]
    volumes = [r.get("volume") or 0 for r in rows]

    # Volatility proxy: 20-day mean of (high-low)/close
    def vol20(end: int) -> float:
        s = 0.0
        n = 0
        for i in range(max(0, end - 20), end):
            c = closes[i]
            if c <= 0:
                continue
            s += (highs[i] - lows[i]) / c
            n += 1
        return s / n if n else 0.0

    # Look over last 80 days in 4 successive 20-day buckets
    tail = len(closes)
    windows = [vol20(tail - 60), vol20(tail - 40), vol20(tail - 20), vol20(tail)]
    contractions = 0
    for i in range(1, len(windows)):
        if windows[i] < windows[i - 1] * 0.95:
            contractions += 1

    last_ratio = windows[-1] / windows[-2] if windows[-2] > 0 else 1.0

    # Volume dry-up: last 20 avg vs 60d avg
    avg20 = _mean(volumes[-20:])
    avg60 = _mean(volumes[-60:])
    volume_dry_up = avg60 > 0 and avg20 < avg60 * 0.75

    # Position vs 52w high
    hi52 = max(highs[-252:]) if highs[-252:] else max(highs)
    pct_hi = closes[-1] / hi52 if hi52 > 0 else 0.0

    # Stage logic (coarse)
    stage = 0
    if contractions >= 1 and pct_hi >= 0.80:
        stage = 1
    if contractions >= 2 and pct_hi >= 0.90:
        stage = 2
    if contractions >= 2 and pct_hi >= 0.95 and volume_dry_up:
        stage = 3
    if contractions >= 1 and pct_hi >= 1.0 * 0.999:
        stage = 4

    score = min(1.0, 0.25 * contractions + 0.25 * pct_hi + (0.25 if volume_dry_up else 0.0))
    label = _label_for(stage, pct_hi)
    out = VCPStatus(
        symbol=symbol,
        stage=stage,
        contractions=contractions,
        last_vol_ratio=last_ratio,
        volume_dry_up=volume_dry_up,
        pct_of_52w_high=pct_hi,
        score=score,
        label=label,
    )
    _vcp_cache[cache_key] = out
    return out


def _label_for(stage: int, pct_hi: float) -> str:
    if stage >= 4:
        return "B.out"
    if stage >= 2:
        return "VCP"
    if pct_hi >= 0.85:
        return "Base"
    return "—"


def _empty(symbol: str) -> VCPStatus:
    return VCPStatus(symbol, 0, 0, 1.0, False, 0.0, 0.0, "—")


def _mean(xs: list[Any]) -> float:
    if not xs:
        return 0.0
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else 0.0


# --------------------------------------------------------------------------- #
# Sibling base patterns — Cup&Handle, Flat Base, Ascending Triangle           #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class BasePattern:
    symbol: str
    name: str
    market: str
    sector: str
    close: float
    pattern: str         # 'Cup&Handle' | 'Flat Base' | 'Asc Triangle' | 'VCP' | 'None'
    stage: int           # 0..4 (for VCP-like) or 1..3 for cup/flat/triangle maturity
    score: float         # 0..1
    pct_of_52w_high: float
    depth_pct: float     # max drawdown inside the base (positive %)
    duration_days: int   # base length in trading days
    volume_dry_up: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "market": self.market,
            "sector": self.sector,
            "close": self.close,
            "pattern": self.pattern,
            "stage": self.stage,
            "score": self.score,
            "pct_of_52w_high": self.pct_of_52w_high,
            "depth_pct": self.depth_pct,
            "duration_days": self.duration_days,
            "volume_dry_up": self.volume_dry_up,
        }


def detect_base_pattern(
    symbol: str, *, panel: Panel
) -> BasePattern | None:
    rows = panel.series.get(symbol) or []
    if len(rows) < 80:
        return None
    closes = [r["close"] for r in rows]
    highs = [r["high"] or r["close"] for r in rows]
    lows = [r["low"] or r["close"] for r in rows]
    vols = [r.get("volume") or 0 for r in rows]
    meta = panel.universe.get(symbol, {})

    close = closes[-1]
    hi52 = max(highs[-252:]) if len(highs) >= 252 else max(highs)
    pct_hi = close / hi52 if hi52 > 0 else 0.0

    # --- Flat Base ---------------------------------------------------------
    base_len = 20
    base_hi = max(highs[-base_len:])
    base_lo = min(lows[-base_len:])
    flat_range = (base_hi - base_lo) / base_hi if base_hi > 0 else 1.0
    prior_start = max(0, len(closes) - base_len - 40)
    prior = closes[prior_start : len(closes) - base_len]
    prior_advance = (base_hi - min(prior)) / min(prior) if prior and min(prior) > 0 else 0.0
    if flat_range <= 0.15 and prior_advance >= 0.20 and pct_hi >= 0.85:
        depth = (base_hi - base_lo) / base_hi * 100 if base_hi > 0 else 0.0
        vdu = _mean(vols[-20:]) < _mean(vols[-60:]) * 0.75
        return BasePattern(
            symbol=symbol,
            name=meta.get("name") or symbol,
            market=meta.get("market") or "UNKNOWN",
            sector=meta.get("sector") or "기타",
            close=close,
            pattern="Flat Base",
            stage=2 if pct_hi >= 0.95 else 1,
            score=min(1.0, 0.5 + pct_hi * 0.4),
            pct_of_52w_high=round(pct_hi, 3),
            depth_pct=round(depth, 2),
            duration_days=base_len,
            volume_dry_up=vdu,
        )

    # --- Cup with Handle ---------------------------------------------------
    if len(closes) >= 60:
        cup_window = closes[-60:-8]
        hi_left = max(cup_window[:10]) if len(cup_window) >= 10 else max(cup_window)
        cup_low = min(cup_window)
        cup_depth = (hi_left - cup_low) / hi_left if hi_left > 0 else 0.0
        handle = closes[-15:]
        h_hi = max(handle)
        h_lo = min(handle)
        handle_retrace = (h_hi - h_lo) / (hi_left - cup_low) if (hi_left - cup_low) > 0 else 1.0
        v20 = _mean(vols[-20:])
        v60 = _mean(vols[-60:])
        cond_depth = 0.10 <= cup_depth <= 0.40
        cond_recover = closes[-1] >= hi_left * 0.88
        cond_handle_small = handle_retrace <= 0.50
        cond_vdu = v60 > 0 and v20 < v60 * 0.85
        if cond_depth and cond_recover and cond_handle_small:
            return BasePattern(
                symbol=symbol,
                name=meta.get("name") or symbol,
                market=meta.get("market") or "UNKNOWN",
                sector=meta.get("sector") or "기타",
                close=close,
                pattern="Cup&Handle",
                stage=3 if pct_hi >= 0.97 else 2,
                score=min(1.0, 0.5 + pct_hi * 0.5),
                pct_of_52w_high=round(pct_hi, 3),
                depth_pct=round(cup_depth * 100, 2),
                duration_days=len(cup_window) + len(handle),
                volume_dry_up=cond_vdu,
            )

    # --- Ascending Triangle -----------------------------------------------
    if len(closes) >= 40:
        seg_hi = highs[-40:]
        seg_lo = lows[-40:]
        top = max(seg_hi)
        top_tol = top * 0.97
        touches = sum(1 for h in seg_hi if h >= top_tol)
        chunk_mins = [min(seg_lo[i * 10 : (i + 1) * 10]) for i in range(4)]
        rising = all(chunk_mins[i] <= chunk_mins[i + 1] for i in range(3))
        if touches >= 3 and rising and pct_hi >= 0.85:
            depth = (top - chunk_mins[0]) / top * 100 if top > 0 else 0.0
            vdu = _mean(vols[-20:]) < _mean(vols[-60:]) * 0.85
            return BasePattern(
                symbol=symbol,
                name=meta.get("name") or symbol,
                market=meta.get("market") or "UNKNOWN",
                sector=meta.get("sector") or "기타",
                close=close,
                pattern="Asc Triangle",
                stage=2 if pct_hi >= 0.95 else 1,
                score=min(1.0, 0.45 + pct_hi * 0.45),
                pct_of_52w_high=round(pct_hi, 3),
                depth_pct=round(depth, 2),
                duration_days=40,
                volume_dry_up=vdu,
            )

    # --- VCP fallback ------------------------------------------------------
    vcp = detect_vcp(symbol, panel=panel)
    if vcp.stage >= 1:
        return BasePattern(
            symbol=symbol,
            name=meta.get("name") or symbol,
            market=meta.get("market") or "UNKNOWN",
            sector=meta.get("sector") or "기타",
            close=close,
            pattern="VCP",
            stage=vcp.stage,
            score=round(vcp.score, 3),
            pct_of_52w_high=round(vcp.pct_of_52w_high, 3),
            depth_pct=round((1 - vcp.pct_of_52w_high) * 100, 2),
            duration_days=80,
            volume_dry_up=vcp.volume_dry_up,
        )
    return None


def scan_base_patterns(
    *,
    panel: Panel,
    limit: int = 300,
    min_close: float = 1000.0,
) -> list[BasePattern]:
    out: list[BasePattern] = []
    for sym, rows in panel.series.items():
        if not rows or (rows[-1]["close"] or 0) < min_close:
            continue
        bp = detect_base_pattern(sym, panel=panel)
        if bp is None:
            continue
        out.append(bp)
    out.sort(key=lambda b: (-b.stage, -b.pct_of_52w_high, -b.score))
    return out[:limit]


def summary_counts(rows: list[BasePattern]) -> dict[str, int]:
    out = {"VCP": 0, "Cup&Handle": 0, "Flat Base": 0, "Asc Triangle": 0, "stage3+": 0}
    for r in rows:
        out[r.pattern] = out.get(r.pattern, 0) + 1
        if r.stage >= 3:
            out["stage3+"] += 1
    return out
