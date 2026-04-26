"""Ichimoku Cloud scanner — Tenkan / Kijun / Senkou A,B / Chikou.

Classical parameters (Goichi Hosoda, 1969):
    Tenkan-sen     = (highest high(9)  + lowest low(9))  / 2
    Kijun-sen      = (highest high(26) + lowest low(26)) / 2
    Senkou Span A  = (Tenkan + Kijun) / 2  shifted +26 bars forward
    Senkou Span B  = (highest high(52) + lowest low(52)) / 2  shifted +26
    Chikou Span    = close shifted -26 bars backward

For each symbol we classify:
    * vs_cloud:  'ABOVE' | 'BELOW' | 'INSIDE'
    * tk_cross:  'BULL' (Tenkan crossed above Kijun in last 5 bars) | 'BEAR' | '—'
    * chikou:    'ABOVE' | 'BELOW' (vs close 26 bars ago)
    * 3-cross align: all three bullish (perfect Bullish Ichimoku) or bearish
    * cloud thickness (% of price)
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from ky_core.scanning.loader import Panel


@dataclass
class IchimokuHit:
    symbol: str
    name: str
    market: str
    sector: str
    close: float
    tenkan: float
    kijun: float
    senkou_a: float           # projected value at today (shifted back to present)
    senkou_b: float
    cloud_top: float
    cloud_bot: float
    cloud_thickness_pct: float   # (top-bot)/close * 100
    vs_cloud: str             # 'ABOVE' | 'BELOW' | 'INSIDE'
    tk_cross: str             # 'BULL' | 'BEAR' | '—'
    chikou: str               # 'ABOVE' | 'BELOW'
    three_cross_bull: bool
    three_cross_bear: bool
    tone: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Primitives                                                                  #
# --------------------------------------------------------------------------- #


def _hh(highs: list[float], period: int, end: int) -> float:
    return max(highs[max(0, end - period + 1) : end + 1])


def _ll(lows: list[float], period: int, end: int) -> float:
    return min(lows[max(0, end - period + 1) : end + 1])


def ichimoku_values(
    highs: list[float],
    lows: list[float],
    closes: list[float],
) -> dict[str, float] | None:
    """Compute last-bar Ichimoku. Requires >= 52 bars."""
    n = len(closes)
    if n < 52:
        return None
    idx = n - 1
    tenkan = (_hh(highs, 9, idx) + _ll(lows, 9, idx)) / 2
    kijun = (_hh(highs, 26, idx) + _ll(lows, 26, idx)) / 2
    # Senkou A/B plotted 26 bars AHEAD; at today's bar, the cloud that shows
    # up at index ``idx`` was computed 26 bars earlier.
    if idx - 26 < 25:
        senkou_a_now = (tenkan + kijun) / 2
        senkou_b_now = (_hh(highs, 52, idx) + _ll(lows, 52, idx)) / 2
    else:
        past = idx - 26
        t_past = (_hh(highs, 9, past) + _ll(lows, 9, past)) / 2
        k_past = (_hh(highs, 26, past) + _ll(lows, 26, past)) / 2
        senkou_a_now = (t_past + k_past) / 2
        if past >= 51:
            senkou_b_now = (_hh(highs, 52, past) + _ll(lows, 52, past)) / 2
        else:
            senkou_b_now = (_hh(highs, 52, idx) + _ll(lows, 52, idx)) / 2
    return {
        "tenkan": tenkan,
        "kijun": kijun,
        "senkou_a": senkou_a_now,
        "senkou_b": senkou_b_now,
    }


def _tk_cross_recent(
    highs: list[float], lows: list[float], bars: int = 5
) -> str:
    """Look back ``bars`` bars for a Tenkan/Kijun cross."""
    n = len(highs)
    if n < 27 + bars:
        return "—"
    # build small tail series
    ten_tail = []
    kij_tail = []
    for i in range(n - bars - 1, n):
        if i < 25:
            return "—"
        ten_tail.append((_hh(highs, 9, i) + _ll(lows, 9, i)) / 2)
        kij_tail.append((_hh(highs, 26, i) + _ll(lows, 26, i)) / 2)
    # detect cross in the tail (first pair vs last pair; any intermediate change)
    prev_rel = ten_tail[0] - kij_tail[0]
    for i in range(1, len(ten_tail)):
        cur_rel = ten_tail[i] - kij_tail[i]
        if prev_rel <= 0 and cur_rel > 0:
            return "BULL"
        if prev_rel >= 0 and cur_rel < 0:
            return "BEAR"
        prev_rel = cur_rel
    return "—"


def scan_ichimoku(
    *,
    panel: Panel,
    min_close: float = 1000.0,
    require_three_cross: bool = False,
    limit: int = 300,
) -> list[IchimokuHit]:
    hits: list[IchimokuHit] = []
    for sym, rows in panel.series.items():
        if len(rows) < 60:
            continue
        closes = [r["close"] for r in rows]
        highs = [r["high"] or r["close"] for r in rows]
        lows = [r["low"] or r["close"] for r in rows]
        if closes[-1] is None or closes[-1] < min_close:
            continue

        ich = ichimoku_values(highs, lows, closes)
        if ich is None:
            continue
        close = closes[-1]
        tenkan = ich["tenkan"]
        kijun = ich["kijun"]
        sa = ich["senkou_a"]
        sb = ich["senkou_b"]
        top = max(sa, sb)
        bot = min(sa, sb)

        if close > top:
            vs_cloud = "ABOVE"
        elif close < bot:
            vs_cloud = "BELOW"
        else:
            vs_cloud = "INSIDE"

        tk = _tk_cross_recent(highs, lows, bars=5)
        # Chikou: today's close vs close 26 bars ago
        if len(closes) > 26:
            chikou = "ABOVE" if close > closes[-27] else "BELOW"
        else:
            chikou = "—"

        # 3-cross alignment: Tenkan > Kijun (trend) + Price > Cloud (position) +
        # Chikou > Price (confirmation). Bearish is the mirror.
        three_bull = tenkan > kijun and vs_cloud == "ABOVE" and chikou == "ABOVE"
        three_bear = tenkan < kijun and vs_cloud == "BELOW" and chikou == "BELOW"
        if require_three_cross and not (three_bull or three_bear):
            continue

        thickness = ((top - bot) / close * 100) if close > 0 else 0.0
        meta = panel.universe.get(sym, {})
        if three_bull:
            tone = "pos"
        elif three_bear:
            tone = "neg"
        elif vs_cloud == "ABOVE":
            tone = "pos"
        elif vs_cloud == "BELOW":
            tone = "neg"
        else:
            tone = "neutral"

        hits.append(
            IchimokuHit(
                symbol=sym,
                name=meta.get("name") or sym,
                market=meta.get("market") or "UNKNOWN",
                sector=meta.get("sector") or "기타",
                close=float(close),
                tenkan=round(tenkan, 2),
                kijun=round(kijun, 2),
                senkou_a=round(sa, 2),
                senkou_b=round(sb, 2),
                cloud_top=round(top, 2),
                cloud_bot=round(bot, 2),
                cloud_thickness_pct=round(thickness, 2),
                vs_cloud=vs_cloud,
                tk_cross=tk,
                chikou=chikou,
                three_cross_bull=three_bull,
                three_cross_bear=three_bear,
                tone=tone,
            )
        )

    # Order: 3-cross bulls first, then above-cloud, then neutrals, then bears.
    def sk(h: IchimokuHit) -> tuple:
        bucket = 0 if h.three_cross_bull else (1 if h.vs_cloud == "ABOVE" else (2 if h.vs_cloud == "INSIDE" else 3))
        return (bucket, -h.cloud_thickness_pct)

    hits.sort(key=sk)
    return hits[:limit]


def summary_counts(hits: list[IchimokuHit]) -> dict[str, int]:
    out = {"above": 0, "inside": 0, "below": 0, "three_bull": 0, "three_bear": 0, "tk_bull": 0, "tk_bear": 0}
    for h in hits:
        if h.vs_cloud == "ABOVE":
            out["above"] += 1
        elif h.vs_cloud == "BELOW":
            out["below"] += 1
        else:
            out["inside"] += 1
        if h.three_cross_bull:
            out["three_bull"] += 1
        if h.three_cross_bear:
            out["three_bear"] += 1
        if h.tk_cross == "BULL":
            out["tk_bull"] += 1
        elif h.tk_cross == "BEAR":
            out["tk_bear"] += 1
    return out
