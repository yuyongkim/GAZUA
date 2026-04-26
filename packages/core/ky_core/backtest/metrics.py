"""Performance metrics for a backtest run.

All inputs are plain Python lists — no pandas. Returns are computed
from the equity curve (dict {date, equity}).
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from math import sqrt
from typing import Any


@dataclass
class Metrics:
    start: str
    end: str
    n_days: int
    final_equity: float
    total_return: float        # decimal (0.10 == 10%)
    cagr: float
    max_drawdown: float        # decimal, negative (-0.19 == -19%)
    sharpe: float
    sortino: float
    calmar: float
    volatility: float          # annualised stdev of daily returns
    win_rate: float            # fraction of profitable closed trades
    profit_factor: float       # gross wins / gross losses
    n_trades: int
    avg_holding_days: float
    best_trade: float          # pct
    worst_trade: float         # pct

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compute_metrics(
    equity_curve: list[dict[str, Any]],
    trades: list[dict[str, Any]],
    *,
    risk_free_rate: float = 0.03,
    periods_per_year: int = 252,
) -> Metrics:
    if not equity_curve:
        return _empty_metrics()
    start_eq = equity_curve[0]["equity"]
    end_eq = equity_curve[-1]["equity"]
    start_date = equity_curve[0]["date"]
    end_date = equity_curve[-1]["date"]
    n_days = len(equity_curve)

    total_return = (end_eq / start_eq) - 1.0 if start_eq > 0 else 0.0
    years = max(n_days / periods_per_year, 1.0 / periods_per_year)
    cagr = (end_eq / start_eq) ** (1.0 / years) - 1.0 if start_eq > 0 else 0.0

    daily_rets = _daily_returns(equity_curve)
    vol = _stdev(daily_rets) * sqrt(periods_per_year) if daily_rets else 0.0

    excess = [r - (risk_free_rate / periods_per_year) for r in daily_rets]
    sharpe = (_mean(excess) * periods_per_year) / vol if vol > 1e-9 else 0.0

    downside = [r for r in excess if r < 0]
    dstd = _stdev(downside) * sqrt(periods_per_year) if downside else 0.0
    sortino = (_mean(excess) * periods_per_year) / dstd if dstd > 1e-9 else 0.0

    mdd = _max_drawdown(equity_curve)
    calmar = cagr / abs(mdd) if mdd < 0 else 0.0

    wins = [t for t in trades if t.get("pnl", 0) > 0]
    losses = [t for t in trades if t.get("pnl", 0) < 0]
    win_rate = len(wins) / len(trades) if trades else 0.0
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = -sum(t["pnl"] for t in losses)
    profit_factor = gross_win / gross_loss if gross_loss > 1e-9 else (float("inf") if gross_win > 0 else 0.0)
    avg_hold = sum(t.get("holding_days", 0) for t in trades) / len(trades) if trades else 0.0
    best = max((t.get("pnl_pct", 0) for t in trades), default=0.0)
    worst = min((t.get("pnl_pct", 0) for t in trades), default=0.0)

    return Metrics(
        start=start_date,
        end=end_date,
        n_days=n_days,
        final_equity=end_eq,
        total_return=total_return,
        cagr=cagr,
        max_drawdown=mdd,
        sharpe=sharpe,
        sortino=sortino,
        calmar=calmar,
        volatility=vol,
        win_rate=win_rate,
        profit_factor=profit_factor if profit_factor != float("inf") else 999.0,
        n_trades=len(trades),
        avg_holding_days=avg_hold,
        best_trade=best,
        worst_trade=worst,
    )


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _empty_metrics() -> Metrics:
    return Metrics(
        start="", end="", n_days=0, final_equity=0.0, total_return=0.0,
        cagr=0.0, max_drawdown=0.0, sharpe=0.0, sortino=0.0, calmar=0.0,
        volatility=0.0, win_rate=0.0, profit_factor=0.0, n_trades=0,
        avg_holding_days=0.0, best_trade=0.0, worst_trade=0.0,
    )


def _daily_returns(curve: list[dict[str, Any]]) -> list[float]:
    out: list[float] = []
    prev = None
    for row in curve:
        eq = row["equity"]
        if prev is not None and prev > 0:
            out.append(eq / prev - 1.0)
        prev = eq
    return out


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _stdev(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    var = sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
    return sqrt(var)


def _max_drawdown(curve: list[dict[str, Any]]) -> float:
    peak = curve[0]["equity"] if curve else 0.0
    worst = 0.0
    for row in curve:
        eq = row["equity"]
        if eq > peak:
            peak = eq
        if peak > 0:
            dd = eq / peak - 1.0
            if dd < worst:
                worst = dd
    return worst
