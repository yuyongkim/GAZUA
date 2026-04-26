"""Backtest strategies.

Each strategy module exports a module-level factory `build()` that
returns a strategy instance matching the engine's `Strategy`
protocol (``name``, ``rebalance``, ``pick(view, as_of)``).
"""
from __future__ import annotations

from ky_core.backtest.strategies import sepa, magic_formula, quality_momentum, value_qmj


REGISTRY = {
    "sepa":             sepa.build,
    "magic_formula":    magic_formula.build,
    "quality_momentum": quality_momentum.build,
    "value_qmj":        value_qmj.build,
}


def build(name: str):
    try:
        return REGISTRY[name]()
    except KeyError as exc:
        raise ValueError(f"unknown strategy: {name}. Options: {list(REGISTRY)}") from exc


__all__ = ["REGISTRY", "build"]
