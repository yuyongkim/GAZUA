"""Shared helpers for wizard modules (tiny so each wizard stays terse)."""
from __future__ import annotations

from typing import Any

from ky_core.scanning.loader import Panel


def rows(panel: Panel, symbol: str) -> list[dict[str, Any]]:
    return panel.series.get(symbol) or []


def closes(panel: Panel, symbol: str) -> list[float]:
    return [r["close"] for r in panel.series.get(symbol, [])]


def highs(panel: Panel, symbol: str) -> list[float]:
    return [r["high"] or r["close"] for r in panel.series.get(symbol, [])]


def lows(panel: Panel, symbol: str) -> list[float]:
    return [r["low"] or r["close"] for r in panel.series.get(symbol, [])]


def volumes(panel: Panel, symbol: str) -> list[int]:
    return [int(r.get("volume") or 0) for r in panel.series.get(symbol, [])]


def sma(xs: list[float], n: int) -> float:
    if not xs:
        return 0.0
    w = xs[-n:] if len(xs) >= n else xs
    return sum(w) / len(w)


def pct_change(xs: list[float], back: int) -> float:
    if len(xs) <= back or xs[-back - 1] <= 0:
        return 0.0
    return (xs[-1] / xs[-back - 1] - 1.0) * 100


def avg_vol(v: list[int], n: int) -> float:
    if not v:
        return 0.0
    w = v[-n:] if len(v) >= n else v
    return sum(w) / len(w) if w else 0.0


def meta(panel: Panel, symbol: str) -> dict[str, Any]:
    return panel.universe.get(symbol) or {
        "symbol": symbol,
        "name": symbol,
        "market": "UNKNOWN",
        "sector": "기타",
    }
