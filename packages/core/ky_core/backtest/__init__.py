"""ky_core.backtest — real-data backtest engine.

Loads a wide OHLCV panel once from ``ky.db`` (10M+ rows), iterates
day-by-day with strict point-in-time isolation (signal on close,
fill on next open), applies Korean-market trading costs, and emits
a run artefact (equity curve, trades, metrics, sector attribution).

Strategies plug in by implementing the :class:`Strategy` protocol;
current lineup:

    * ``sepa``              — Minervini SEPA (TT + VCP + leader score)
    * ``magic_formula``     — Greenblatt EY + ROC annual rebalance
    * ``quality_momentum``  — ROE x 6-month momentum (monthly)
    * ``value_qmj``         — Quality Minus Junk value screen (quarterly)

Run artefacts land in ``~/.gazua/data/backtest/run_<id>.json``.
"""
from __future__ import annotations

from ky_core.backtest.engine import BacktestEngine, BacktestConfig, BacktestRun
from ky_core.backtest.cost import CostModel, DEFAULT_COST
from ky_core.backtest.portfolio import Portfolio, Position
from ky_core.backtest.metrics import compute_metrics, Metrics

__all__ = [
    "BacktestEngine",
    "BacktestConfig",
    "BacktestRun",
    "CostModel",
    "DEFAULT_COST",
    "Portfolio",
    "Position",
    "compute_metrics",
    "Metrics",
]
