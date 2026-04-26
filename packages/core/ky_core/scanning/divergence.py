"""Divergence scanner — RSI / MACD / OBV vs price.

Divergence occurs when a price pivot (higher-high or lower-low) is NOT
confirmed by the oscillator — a classic early-reversal tell.

Four classes emitted:
    * Bullish Regular  — price lower-low, indicator higher-low (reversal up)
    * Bearish Regular  — price higher-high, indicator lower-high (reversal down)
    * Bullish Hidden   — price higher-low, indicator lower-low (trend continuation up)
    * Bearish Hidden   — price lower-high, indicator higher-high (trend continuation down)

All three indicators are run per symbol; the strongest divergence on any
of them surfaces per row. "Strength" is measured as (% move between the
two pivots) * (% indicator move in the opposite direction).
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from ky_core.scanning.loader import Panel


@dataclass
class DivergenceHit:
    symbol: str
    name: str
    market: str
    sector: str
    indicator: str            # 'RSI' | 'MACD' | 'OBV'
    kind: str                 # 'bullish' | 'bearish' | 'hidden_bullish' | 'hidden_bearish'
    tone: str                 # 'pos' | 'neg'
    close: float
    rsi: float
    d5_pct: float             # 5-day price %-change
    strength: float           # composite strength score (0..1)
    strength_label: str       # 'STRONG' | 'MID' | 'WEAK'

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Indicator primitives                                                        #
# --------------------------------------------------------------------------- #


def rsi_series(closes: list[float], period: int = 14) -> list[float]:
    """Classical Wilder RSI(14) — returns list aligned to closes (NaNs → 50)."""
    n = len(closes)
    out = [50.0] * n
    if n <= period:
        return out
    gains = [0.0] * n
    losses = [0.0] * n
    for i in range(1, n):
        ch = closes[i] - closes[i - 1]
        gains[i] = ch if ch > 0 else 0.0
        losses[i] = -ch if ch < 0 else 0.0
    # seed
    avg_g = sum(gains[1 : period + 1]) / period
    avg_l = sum(losses[1 : period + 1]) / period
    for i in range(period, n):
        if i > period:
            avg_g = (avg_g * (period - 1) + gains[i]) / period
            avg_l = (avg_l * (period - 1) + losses[i]) / period
        if avg_l == 0:
            out[i] = 100.0
        else:
            rs = avg_g / avg_l
            out[i] = 100.0 - 100.0 / (1.0 + rs)
    return out


def ema(values: list[float], period: int) -> list[float]:
    n = len(values)
    out = [0.0] * n
    if n == 0:
        return out
    k = 2.0 / (period + 1)
    out[0] = values[0]
    for i in range(1, n):
        out[i] = values[i] * k + out[i - 1] * (1 - k)
    return out


def macd_hist(closes: list[float], fast: int = 12, slow: int = 26, sig: int = 9) -> list[float]:
    if len(closes) < slow + sig:
        return [0.0] * len(closes)
    efast = ema(closes, fast)
    eslow = ema(closes, slow)
    macd_line = [f - s for f, s in zip(efast, eslow)]
    sig_line = ema(macd_line, sig)
    return [m - s for m, s in zip(macd_line, sig_line)]


def obv_series(closes: list[float], vols: list[float]) -> list[float]:
    out = [0.0] * len(closes)
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            out[i] = out[i - 1] + vols[i]
        elif closes[i] < closes[i - 1]:
            out[i] = out[i - 1] - vols[i]
        else:
            out[i] = out[i - 1]
    return out


# --------------------------------------------------------------------------- #
# Pivot detection + divergence logic                                          #
# --------------------------------------------------------------------------- #


def _pivot_lows(xs: list[float], k: int = 3) -> list[int]:
    """Indices where xs[i] is the min of xs[i-k..i+k]."""
    n = len(xs)
    out = []
    for i in range(k, n - k):
        window = xs[i - k : i + k + 1]
        if xs[i] == min(window):
            out.append(i)
    return out


def _pivot_highs(xs: list[float], k: int = 3) -> list[int]:
    n = len(xs)
    out = []
    for i in range(k, n - k):
        window = xs[i - k : i + k + 1]
        if xs[i] == max(window):
            out.append(i)
    return out


def _last_two(indices: list[int], cutoff_age: int, latest: int) -> tuple[int, int] | None:
    """Return the two most recent pivots whose latest is within ``cutoff_age``
    bars of ``latest``; else None."""
    valid = [i for i in indices if i >= latest - cutoff_age]
    if len(valid) < 2:
        return None
    return (valid[-2], valid[-1])


def _detect_divergence(
    closes: list[float],
    indicator: list[float],
) -> tuple[str, float] | None:
    """Return (kind, strength) or None if no recent divergence."""
    n = len(closes)
    if n < 30:
        return None
    latest = n - 1
    cutoff = 40  # both pivots must be within 40 bars

    # Regular bullish: two lower price lows, two higher indicator lows.
    plows = _pivot_lows(closes, k=3)
    ilow_p = _last_two(plows, cutoff, latest)
    if ilow_p:
        a, b = ilow_p
        if closes[b] < closes[a] and indicator[b] > indicator[a]:
            p_mv = (closes[a] - closes[b]) / max(1e-9, closes[a])
            i_mv = (indicator[b] - indicator[a]) / max(1e-9, abs(indicator[a]) + 1e-6)
            return ("bullish", min(1.0, (p_mv + i_mv) / 2.0 * 5))

    # Regular bearish: two higher price highs, two lower indicator highs.
    phighs = _pivot_highs(closes, k=3)
    ihi_p = _last_two(phighs, cutoff, latest)
    if ihi_p:
        a, b = ihi_p
        if closes[b] > closes[a] and indicator[b] < indicator[a]:
            p_mv = (closes[b] - closes[a]) / max(1e-9, closes[a])
            i_mv = (indicator[a] - indicator[b]) / max(1e-9, abs(indicator[a]) + 1e-6)
            return ("bearish", min(1.0, (p_mv + i_mv) / 2.0 * 5))

    # Hidden bullish: higher price low, lower indicator low (trend-continuation).
    if ilow_p:
        a, b = ilow_p
        if closes[b] > closes[a] and indicator[b] < indicator[a]:
            p_mv = (closes[b] - closes[a]) / max(1e-9, closes[a])
            i_mv = (indicator[a] - indicator[b]) / max(1e-9, abs(indicator[a]) + 1e-6)
            return ("hidden_bullish", min(1.0, (p_mv + i_mv) / 2.0 * 5))

    # Hidden bearish: lower price high, higher indicator high.
    if ihi_p:
        a, b = ihi_p
        if closes[b] < closes[a] and indicator[b] > indicator[a]:
            p_mv = (closes[a] - closes[b]) / max(1e-9, closes[a])
            i_mv = (indicator[b] - indicator[a]) / max(1e-9, abs(indicator[a]) + 1e-6)
            return ("hidden_bearish", min(1.0, (p_mv + i_mv) / 2.0 * 5))

    return None


# --------------------------------------------------------------------------- #
# Public scanner                                                              #
# --------------------------------------------------------------------------- #


def scan_divergences(
    *,
    panel: Panel,
    limit: int = 200,
    min_close: float = 1000.0,
) -> list[DivergenceHit]:
    hits: list[DivergenceHit] = []
    for sym, rows in panel.series.items():
        if len(rows) < 50:
            continue
        closes = [r["close"] for r in rows]
        vols = [float(r.get("volume") or 0) for r in rows]
        if closes[-1] is None or closes[-1] < min_close:
            continue

        rsi = rsi_series(closes, 14)
        macd = macd_hist(closes)
        obv = obv_series(closes, vols)

        best: tuple[str, str, float] | None = None  # (indicator, kind, strength)
        for name, series in (("RSI", rsi), ("MACD", macd), ("OBV", obv)):
            result = _detect_divergence(closes, series)
            if result is None:
                continue
            kind, strength = result
            if best is None or strength > best[2]:
                best = (name, kind, strength)
        if best is None:
            continue

        indicator, kind, strength = best
        tone = "pos" if "bullish" in kind else "neg"
        meta = panel.universe.get(sym, {})

        d5 = 0.0
        if len(closes) >= 6 and closes[-6] > 0:
            d5 = (closes[-1] / closes[-6] - 1.0) * 100

        hits.append(
            DivergenceHit(
                symbol=sym,
                name=meta.get("name") or sym,
                market=meta.get("market") or "UNKNOWN",
                sector=meta.get("sector") or "기타",
                indicator=indicator,
                kind=kind,
                tone=tone,
                close=float(closes[-1]),
                rsi=round(rsi[-1], 1),
                d5_pct=round(d5, 2),
                strength=round(strength, 3),
                strength_label=_strength_label(strength),
            )
        )

    hits.sort(key=lambda x: (x.tone != "pos", -x.strength))
    return hits[:limit]


def _strength_label(v: float) -> str:
    if v >= 0.6:
        return "STRONG"
    if v >= 0.3:
        return "MID"
    return "WEAK"


def summary_counts(hits: list[DivergenceHit]) -> dict[str, int]:
    out = {"bullish": 0, "bearish": 0, "hidden_bullish": 0, "hidden_bearish": 0}
    for h in hits:
        out[h.kind] = out.get(h.kind, 0) + 1
    return out
