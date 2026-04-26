"""Candlestick pattern scanner — 15+ classical patterns, no TA-Lib.

Detects canonical Japanese candlestick patterns on the last 1-5 daily bars
of every KOSPI+KOSDAQ symbol in the panel. Each pattern is pure-Python
and looks at open/high/low/close ratios rather than absolute prices so
they work across ticks of any size.

Covered patterns (15):

    Single-bar:    Hammer, Inverted Hammer, Hanging Man, Shooting Star,
                   Doji, Dragonfly Doji, Gravestone Doji, Marubozu
    Two-bar:       Bullish Engulfing, Bearish Engulfing, Piercing Line,
                   Dark Cloud Cover, Tweezer Bottom, Tweezer Top
    Three-bar:     Morning Star, Evening Star, Three White Soldiers,
                   Three Black Crows

Each hit is enriched with a **historical success rate** derived from the
same panel (how often the pattern was followed by +ve close over the
next 5 bars in the past 250 days) and an "average +5D" avg return.
That way the UI can show real bars-backed stats instead of mocks.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Callable

from ky_core.scanning.loader import Panel


# --------------------------------------------------------------------------- #
# Types                                                                       #
# --------------------------------------------------------------------------- #


@dataclass
class CandleHit:
    symbol: str
    name: str
    market: str
    sector: str
    pattern: str          # canonical English name
    pattern_ko: str       # 한국어 라벨
    type: str             # 'Bullish Reversal' | 'Bearish Reversal' | ...
    tone: str             # 'pos' | 'neg' | 'neutral'
    close: float
    win_rate: float       # 0..100 historical success % (+ve 5-day close)
    avg_fwd_5d: float     # historical mean forward 5-day return (%)
    vol_x: float          # today volume / 20-day avg
    sample_n: int         # historical occurrences counted
    confluence: str       # short descriptor ('RSI oversold' etc.)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Pattern detectors — each returns True on the *last* bar                     #
# All accept o,h,l,c,v tuples aligned asc; detectors use negative indexing.   #
# --------------------------------------------------------------------------- #


def _body(o: float, c: float) -> float:
    return abs(c - o)


def _range(h: float, l: float) -> float:
    return max(0.0, h - l)


def _upper_shadow(o: float, h: float, c: float) -> float:
    return h - max(o, c)


def _lower_shadow(o: float, l: float, c: float) -> float:
    return min(o, c) - l


def _is_bull(o: float, c: float) -> bool:
    return c > o


def _is_bear(o: float, c: float) -> bool:
    return c < o


def _pct_body(o: float, h: float, l: float, c: float) -> float:
    r = _range(h, l)
    return (_body(o, c) / r) if r > 0 else 0.0


# Single-bar ---------------------------------------------------------------- #


def _pat_hammer(o, h, l, c) -> bool:
    """Small body near top, long lower shadow >= 2x body."""
    body = _body(o[-1], c[-1])
    if body <= 0:
        return False
    lower = _lower_shadow(o[-1], l[-1], c[-1])
    upper = _upper_shadow(o[-1], h[-1], c[-1])
    rng = _range(h[-1], l[-1])
    if rng <= 0:
        return False
    return lower >= 2 * body and upper <= body * 0.5 and body / rng <= 0.35


def _pat_inverted_hammer(o, h, l, c) -> bool:
    body = _body(o[-1], c[-1])
    if body <= 0:
        return False
    upper = _upper_shadow(o[-1], h[-1], c[-1])
    lower = _lower_shadow(o[-1], l[-1], c[-1])
    rng = _range(h[-1], l[-1])
    if rng <= 0:
        return False
    return upper >= 2 * body and lower <= body * 0.5 and body / rng <= 0.35


def _pat_hanging_man(o, h, l, c) -> bool:
    # Same shape as hammer but in an uptrend (prior 5-day close rising).
    if not _pat_hammer(o, h, l, c):
        return False
    if len(c) < 6:
        return False
    return c[-2] > c[-6]


def _pat_shooting_star(o, h, l, c) -> bool:
    if not _pat_inverted_hammer(o, h, l, c):
        return False
    if len(c) < 6:
        return False
    return c[-2] > c[-6]  # in prior uptrend


def _pat_doji(o, h, l, c) -> bool:
    rng = _range(h[-1], l[-1])
    if rng <= 0:
        return False
    return _body(o[-1], c[-1]) / rng <= 0.10


def _pat_dragonfly_doji(o, h, l, c) -> bool:
    if not _pat_doji(o, h, l, c):
        return False
    upper = _upper_shadow(o[-1], h[-1], c[-1])
    lower = _lower_shadow(o[-1], l[-1], c[-1])
    rng = _range(h[-1], l[-1])
    return upper <= rng * 0.1 and lower >= rng * 0.6


def _pat_gravestone_doji(o, h, l, c) -> bool:
    if not _pat_doji(o, h, l, c):
        return False
    upper = _upper_shadow(o[-1], h[-1], c[-1])
    lower = _lower_shadow(o[-1], l[-1], c[-1])
    rng = _range(h[-1], l[-1])
    return lower <= rng * 0.1 and upper >= rng * 0.6


def _pat_marubozu(o, h, l, c) -> bool:
    rng = _range(h[-1], l[-1])
    if rng <= 0:
        return False
    return _body(o[-1], c[-1]) / rng >= 0.92


# Two-bar ------------------------------------------------------------------- #


def _pat_bull_engulfing(o, h, l, c) -> bool:
    if len(c) < 2:
        return False
    # Yesterday bearish, today bullish, today body engulfs yesterday's.
    prev_bear = _is_bear(o[-2], c[-2])
    today_bull = _is_bull(o[-1], c[-1])
    if not (prev_bear and today_bull):
        return False
    return c[-1] >= o[-2] and o[-1] <= c[-2]


def _pat_bear_engulfing(o, h, l, c) -> bool:
    if len(c) < 2:
        return False
    prev_bull = _is_bull(o[-2], c[-2])
    today_bear = _is_bear(o[-1], c[-1])
    if not (prev_bull and today_bear):
        return False
    return o[-1] >= c[-2] and c[-1] <= o[-2]


def _pat_piercing(o, h, l, c) -> bool:
    if len(c) < 2:
        return False
    prev_bear = _is_bear(o[-2], c[-2])
    today_bull = _is_bull(o[-1], c[-1])
    if not (prev_bear and today_bull):
        return False
    mid = (o[-2] + c[-2]) / 2
    return o[-1] < c[-2] and c[-1] > mid and c[-1] < o[-2]


def _pat_dark_cloud(o, h, l, c) -> bool:
    if len(c) < 2:
        return False
    prev_bull = _is_bull(o[-2], c[-2])
    today_bear = _is_bear(o[-1], c[-1])
    if not (prev_bull and today_bear):
        return False
    mid = (o[-2] + c[-2]) / 2
    return o[-1] > c[-2] and c[-1] < mid and c[-1] > o[-2]


def _pat_tweezer_bottom(o, h, l, c) -> bool:
    if len(c) < 2:
        return False
    # Two consecutive low matches within 0.2%
    if l[-2] <= 0:
        return False
    diff = abs(l[-1] - l[-2]) / l[-2]
    return diff < 0.003 and _is_bear(o[-2], c[-2]) and _is_bull(o[-1], c[-1])


def _pat_tweezer_top(o, h, l, c) -> bool:
    if len(c) < 2:
        return False
    if h[-2] <= 0:
        return False
    diff = abs(h[-1] - h[-2]) / h[-2]
    return diff < 0.003 and _is_bull(o[-2], c[-2]) and _is_bear(o[-1], c[-1])


# Three-bar ----------------------------------------------------------------- #


def _pat_morning_star(o, h, l, c) -> bool:
    if len(c) < 3:
        return False
    # Day-3: long bear, Day-2: small body (gap down), Day-1: long bull closing > midpoint of day-3.
    d3_bear = _is_bear(o[-3], c[-3]) and _pct_body(o[-3], h[-3], l[-3], c[-3]) >= 0.55
    d2_small = _pct_body(o[-2], h[-2], l[-2], c[-2]) <= 0.35
    d1_bull = _is_bull(o[-1], c[-1]) and _pct_body(o[-1], h[-1], l[-1], c[-1]) >= 0.45
    if not (d3_bear and d2_small and d1_bull):
        return False
    mid3 = (o[-3] + c[-3]) / 2
    return c[-1] > mid3


def _pat_evening_star(o, h, l, c) -> bool:
    if len(c) < 3:
        return False
    d3_bull = _is_bull(o[-3], c[-3]) and _pct_body(o[-3], h[-3], l[-3], c[-3]) >= 0.55
    d2_small = _pct_body(o[-2], h[-2], l[-2], c[-2]) <= 0.35
    d1_bear = _is_bear(o[-1], c[-1]) and _pct_body(o[-1], h[-1], l[-1], c[-1]) >= 0.45
    if not (d3_bull and d2_small and d1_bear):
        return False
    mid3 = (o[-3] + c[-3]) / 2
    return c[-1] < mid3


def _pat_three_white_soldiers(o, h, l, c) -> bool:
    if len(c) < 3:
        return False
    if not all(_is_bull(o[i], c[i]) for i in (-3, -2, -1)):
        return False
    if not (c[-1] > c[-2] > c[-3]):
        return False
    # each body substantial
    for i in (-3, -2, -1):
        if _pct_body(o[i], h[i], l[i], c[i]) < 0.45:
            return False
    # each open inside prior body (no wide gaps up)
    return o[-2] >= o[-3] and o[-2] <= c[-3] and o[-1] >= o[-2] and o[-1] <= c[-2]


def _pat_three_black_crows(o, h, l, c) -> bool:
    if len(c) < 3:
        return False
    if not all(_is_bear(o[i], c[i]) for i in (-3, -2, -1)):
        return False
    if not (c[-1] < c[-2] < c[-3]):
        return False
    for i in (-3, -2, -1):
        if _pct_body(o[i], h[i], l[i], c[i]) < 0.45:
            return False
    return o[-2] <= o[-3] and o[-2] >= c[-3] and o[-1] <= o[-2] and o[-1] >= c[-2]


# --------------------------------------------------------------------------- #
# Registry                                                                    #
# --------------------------------------------------------------------------- #


# (key, ko label, type, tone, detector)
PATTERNS: list[tuple[str, str, str, str, Callable]] = [
    ("Hammer",               "망치형",      "Bullish Reversal", "pos",     _pat_hammer),
    ("Inverted Hammer",      "역망치형",    "Bullish Reversal", "pos",     _pat_inverted_hammer),
    ("Hanging Man",          "교수형",      "Bearish Reversal", "neg",     _pat_hanging_man),
    ("Shooting Star",        "유성형",      "Bearish Reversal", "neg",     _pat_shooting_star),
    ("Doji",                 "도지",        "Indecision",       "neutral", _pat_doji),
    ("Dragonfly Doji",       "잠자리 도지", "Bullish Reversal", "pos",     _pat_dragonfly_doji),
    ("Gravestone Doji",      "비석 도지",   "Bearish Reversal", "neg",     _pat_gravestone_doji),
    ("Marubozu",             "장대양/음봉", "Continuation",     "neutral", _pat_marubozu),
    ("Bullish Engulfing",    "상승 장악형", "Bullish Reversal", "pos",     _pat_bull_engulfing),
    ("Bearish Engulfing",    "하락 장악형", "Bearish Reversal", "neg",     _pat_bear_engulfing),
    ("Piercing Line",        "관통형",      "Bullish Reversal", "pos",     _pat_piercing),
    ("Dark Cloud Cover",     "먹구름형",    "Bearish Reversal", "neg",     _pat_dark_cloud),
    ("Tweezer Bottom",       "집게 바닥",   "Bullish Reversal", "pos",     _pat_tweezer_bottom),
    ("Tweezer Top",          "집게 천장",   "Bearish Reversal", "neg",     _pat_tweezer_top),
    ("Morning Star",         "샛별형",      "Bullish Reversal", "pos",     _pat_morning_star),
    ("Evening Star",         "석별형",      "Bearish Reversal", "neg",     _pat_evening_star),
    ("Three White Soldiers", "적삼병",      "Bullish Continuation", "pos", _pat_three_white_soldiers),
    ("Three Black Crows",    "흑삼병",      "Bearish Continuation", "neg", _pat_three_black_crows),
]


# --------------------------------------------------------------------------- #
# Scanner                                                                     #
# --------------------------------------------------------------------------- #


def scan_candlesticks(
    *,
    panel: Panel,
    patterns: list[str] | None = None,
    limit: int = 300,
    min_close: float = 1000.0,
) -> list[CandleHit]:
    """Scan the whole panel for today's candlestick hits.

    Returns a flat list ordered by (bullish hits first, vol_x desc).
    ``patterns`` lets the caller restrict to a subset of pattern names.
    """
    want = set(patterns) if patterns else None
    hits: list[CandleHit] = []
    for sym, rows in panel.series.items():
        if len(rows) < 30:  # need 30 days for forward-5 historical stats
            continue
        o = [r["open"] or r["close"] for r in rows]
        h = [r["high"] or r["close"] for r in rows]
        l = [r["low"] or r["close"] for r in rows]
        c = [r["close"] for r in rows]
        v = [r.get("volume") or 0 for r in rows]
        if c[-1] is None or c[-1] < min_close:
            continue

        avg20_vol = sum(v[-20:]) / max(1, min(20, len(v)))
        vol_x = (v[-1] / avg20_vol) if avg20_vol > 0 else 0.0

        meta = panel.universe.get(sym, {})
        for key, ko, kind, tone, detector in PATTERNS:
            if want is not None and key not in want:
                continue
            try:
                matched = detector(o, h, l, c)
            except Exception:  # noqa: BLE001 — pattern math
                matched = False
            if not matched:
                continue
            wr, mean_fwd, n = _historical_stats(detector, o, h, l, c)
            hits.append(
                CandleHit(
                    symbol=sym,
                    name=meta.get("name") or sym,
                    market=meta.get("market") or "UNKNOWN",
                    sector=meta.get("sector") or "기타",
                    pattern=key,
                    pattern_ko=ko,
                    type=kind,
                    tone=tone,
                    close=float(c[-1]),
                    win_rate=wr,
                    avg_fwd_5d=mean_fwd,
                    vol_x=round(vol_x, 2),
                    sample_n=n,
                    confluence=_confluence(c, v, tone),
                )
            )

    # Bullish-first ordering, then by vol_x desc, then by win_rate desc.
    def sort_key(hit: CandleHit) -> tuple:
        tone_rank = {"pos": 0, "neutral": 1, "neg": 2}.get(hit.tone, 3)
        return (tone_rank, -hit.vol_x, -hit.win_rate)

    hits.sort(key=sort_key)
    return hits[:limit]


def summary_counts(hits: list[CandleHit]) -> dict[str, int]:
    """Convenience — aggregate counts by tone/type for the summary row."""
    out = {"bullish": 0, "bearish": 0, "neutral": 0}
    for h in hits:
        if h.tone == "pos":
            out["bullish"] += 1
        elif h.tone == "neg":
            out["bearish"] += 1
        else:
            out["neutral"] += 1
    return out


# --------------------------------------------------------------------------- #
# Historical stats                                                            #
# --------------------------------------------------------------------------- #


def _historical_stats(
    detector: Callable,
    o: list[float],
    h: list[float],
    l: list[float],
    c: list[float],
) -> tuple[float, float, int]:
    """Over the last 250 bars (excluding today), count how often the pattern
    fired AND the next 5-day return was positive. Returns (win_rate%,
    mean forward 5-day return %, sample size)."""
    tail = len(c)
    if tail < 30:
        return (0.0, 0.0, 0)
    # only scan windows where we also have +5 ahead for forward return
    start = max(5, tail - 250)
    end = tail - 6  # leave 5 days for forward return
    wins = 0
    total = 0
    fwd_sum = 0.0
    for i in range(start, end):
        oi, hi, li, ci = o[: i + 1], h[: i + 1], l[: i + 1], c[: i + 1]
        try:
            if not detector(oi, hi, li, ci):
                continue
        except Exception:  # noqa: BLE001
            continue
        close_now = c[i]
        close_fwd = c[i + 5]
        if close_now <= 0:
            continue
        fwd = (close_fwd / close_now - 1.0) * 100
        total += 1
        fwd_sum += fwd
        if fwd > 0:
            wins += 1
    if total == 0:
        return (0.0, 0.0, 0)
    return (round(100.0 * wins / total, 1), round(fwd_sum / total, 2), total)


def _confluence(c: list[float], v: list[float], tone: str) -> str:
    """Tiny qualitative note — RSI-oversold / 거래량 폭증 / 52주고가 근처."""
    bits: list[str] = []
    # Volume surge
    avg20 = sum(v[-20:]) / max(1, min(20, len(v)))
    if avg20 > 0 and v[-1] >= avg20 * 1.5:
        bits.append("거래량↑")
    # RSI proxy (14)
    if len(c) >= 15:
        gains = 0.0
        losses = 0.0
        for i in range(-14, 0):
            ch = c[i] - c[i - 1]
            if ch >= 0:
                gains += ch
            else:
                losses -= ch
        if gains + losses > 0:
            rs = gains / (losses + 1e-9)
            rsi = 100.0 - 100.0 / (1.0 + rs)
            if tone == "pos" and rsi <= 35:
                bits.append(f"RSI {rsi:.0f}")
            elif tone == "neg" and rsi >= 65:
                bits.append(f"RSI {rsi:.0f}")
    # 52w high proximity
    if len(c) >= 252:
        hi = max(c[-252:])
        if hi > 0 and c[-1] / hi >= 0.97:
            bits.append("52w 근처")
    return " · ".join(bits) if bits else "단일"
