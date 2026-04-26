"""Minervini SEPA strategy — TT + VCP + simplified leader score.

Scans the full universe each Friday (weekly rebalance), ranks by a
composite:

    0.50 * (TT passes / 8)                # trend template strength
    0.25 * (1 + 6m_return) / 2            # RS proxy (clipped to [0, 1])
    0.15 * vcp_contraction_score          # proximity to pivot + vol dry-up
    0.10 * pct_of_52w_high

Returns up to 10 candidates whose composite >= 0.55. Stops are handled
by the engine (-7% by default), target = +2R (entry * 1.14).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ky_core.backtest.engine import Candidate, PanelView


@dataclass
class SepaStrategy:
    name: str = "sepa"
    rebalance: str = "weekly"
    min_score: float = 0.55
    top_n: int = 10

    # ---------- picker ------------------------------------------------

    def pick(self, view: PanelView, as_of: str) -> list[Candidate]:
        candidates: list[tuple[float, Candidate]] = []
        # cached per-symbol 6m return distribution for RS percentile
        rs_table: list[tuple[str, float]] = []
        for sym in view.available_symbols(min_history_days=200):
            closes = view.closes_up_to(sym, n=260)
            if len(closes) < 126:
                continue
            rs = _six_month_rs(closes)
            rs_table.append((sym, rs))

        # rank-lookup for RS percentile
        rs_table.sort(key=lambda t: t[1])
        rs_rank: dict[str, float] = {}
        n = len(rs_table)
        for i, (sym, _v) in enumerate(rs_table):
            rs_rank[sym] = (i + 1) / max(1, n)  # 0..1

        for sym in rs_rank.keys():
            rows = view.rows_up_to(sym, n=260)
            if len(rows) < 200:
                continue
            closes = [r["close"] for r in rows]
            highs = [r["high"] or r["close"] for r in rows]
            lows = [r["low"] or r["close"] for r in rows]
            tt = _evaluate_tt(closes, highs, lows, rs_rank[sym])
            passes = sum(tt.values())
            if passes < 5:
                continue
            if closes[-1] <= 0:
                continue
            vcp_score = _vcp_contraction_score(closes, highs, lows)
            six_m = _six_month_rs(closes)
            rs_norm = max(0.0, min(1.0, (six_m + 0.5)))
            hi52 = max(highs[-252:])
            pct_52 = closes[-1] / hi52 if hi52 > 0 else 0.0

            score = (
                0.50 * (passes / 8.0)
                + 0.25 * rs_norm
                + 0.15 * vcp_score
                + 0.10 * pct_52
            )
            if score < self.min_score:
                continue
            reason = _reason(passes, vcp_score, rs_norm, pct_52)
            candidates.append((score, Candidate(
                symbol=sym, score=score, reason=reason, target_mult=2.0,
            )))

        candidates.sort(key=lambda t: t[0], reverse=True)
        return [c for _s, c in candidates[: self.top_n]]


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def build() -> SepaStrategy:
    return SepaStrategy()


def _sma(xs: list[float], n: int) -> float:
    if not xs:
        return 0.0
    w = xs[-n:] if len(xs) >= n else xs
    return sum(w) / len(w)


def _six_month_rs(closes: list[float]) -> float:
    if len(closes) < 126 or closes[-126] <= 0:
        return 0.0
    return closes[-1] / closes[-126] - 1.0


def _evaluate_tt(
    closes: list[float],
    highs: list[float],
    lows: list[float],
    rs_pct: float,
) -> dict[str, bool]:
    close = closes[-1]
    sma50 = _sma(closes, 50)
    sma150 = _sma(closes, 150)
    sma200 = _sma(closes, 200)
    sma200_prev = _sma(closes[:-22], 200) if len(closes) >= 222 else sma200
    hi52 = max(highs[-252:]) if highs[-252:] else close
    lo52 = min(lows[-252:]) if lows[-252:] else close
    return {
        "close_gt_150_200": close > sma150 and close > sma200,
        "sma150_gt_200":    sma150 > sma200,
        "sma200_rising":    sma200 > sma200_prev,
        "sma50_gt_150_200": sma50 > sma150 > sma200,
        "close_gt_50":      close > sma50,
        "gt_52w_low_30":    close >= lo52 * 1.30 if lo52 > 0 else False,
        "near_52w_high":    close >= hi52 * 0.75 if hi52 > 0 else False,
        "rs_ge_70":         rs_pct >= 0.70,
    }


def _vcp_contraction_score(
    closes: list[float], highs: list[float], lows: list[float]
) -> float:
    """Approximate VCP score in [0, 1].

    Looks at 4 x 20-day windows; awards 0.25 per successive contraction
    (volatility drop > 5%). Adds a small bonus for proximity to 52w high.
    """
    def vol20(end: int) -> float:
        s, n = 0.0, 0
        for i in range(max(0, end - 20), end):
            c = closes[i]
            if c <= 0:
                continue
            s += (highs[i] - lows[i]) / c
            n += 1
        return s / n if n else 0.0

    tail = len(closes)
    if tail < 80:
        return 0.0
    windows = [vol20(tail - 60), vol20(tail - 40), vol20(tail - 20), vol20(tail)]
    contractions = sum(
        1 for i in range(1, len(windows)) if windows[i] < windows[i - 1] * 0.95
    )
    hi52 = max(highs[-252:]) if highs[-252:] else closes[-1]
    pct = closes[-1] / hi52 if hi52 > 0 else 0.0
    return min(1.0, 0.25 * contractions + 0.2 * pct)


def _reason(passes: int, vcp: float, rs: float, pct_52: float) -> str:
    bits: list[str] = [f"TT {passes}/8"]
    if vcp >= 0.6:
        bits.append("VCP")
    if rs >= 0.75:
        bits.append("RS↑")
    if pct_52 >= 0.90:
        bits.append("52W")
    return " · ".join(bits)
