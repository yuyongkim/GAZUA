"""Internal helpers for the backtest engine.

Kept separate so engine.py stays under the 400-line ceiling in
``CONTRIBUTING.md``. These functions are pure + side-effect-free and
are re-exported by ``ky_core.backtest.engine``.
"""
from __future__ import annotations

import uuid
from datetime import date as _date, datetime
from typing import Any


__all__ = [
    "new_run_id",
    "pick_rebalance_dates",
    "equal_weight_index",
    "sector_attribution",
]


def new_run_id() -> str:
    return datetime.utcnow().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]


def pick_rebalance_dates(days: list[str], cadence: str) -> set[str]:
    """Select rebalance days from the list of trading days.

    'weekly'    — last trading day of each ISO week (Fri-equivalent)
    'monthly'   — last trading day of each calendar month
    'quarterly' — last trading day of each calendar quarter
    'annual'    — last trading day of each calendar year
    """
    if not days:
        return set()
    out: set[str]
    if cadence == "weekly":
        buckets: dict[tuple[int, int], str] = {}
        for d in days:
            dt = _date.fromisoformat(d)
            buckets[(dt.year, dt.isocalendar().week)] = d
        out = set(buckets.values())
    elif cadence == "monthly":
        buckets_m: dict[str, str] = {}
        for d in days:
            buckets_m[d[:7]] = d
        out = set(buckets_m.values())
    elif cadence == "quarterly":
        buckets_q: dict[tuple[int, int], str] = {}
        for d in days:
            y, m = int(d[:4]), int(d[5:7])
            buckets_q[(y, (m - 1) // 3 + 1)] = d
        out = set(buckets_q.values())
    elif cadence == "annual":
        buckets_a: dict[str, str] = {}
        for d in days:
            buckets_a[d[:4]] = d
        out = set(buckets_a.values())
    else:
        raise ValueError(f"unknown rebalance cadence: {cadence}")
    out.add(days[0])
    return out


def equal_weight_index(
    symbols: list[str],
    series: dict[str, list[dict[str, Any]]],
    days: list[str],
) -> list[dict[str, Any]]:
    """Equal-weight benchmark index built in a single pass.

    For every symbol:
        * base = first close on or after ``days[0]`` (100 index).
        * Then a per-day cursor carries forward the last known close.
    Final index = mean of member cumulative returns x 100.
    """
    if not days:
        return []
    cached: dict[str, tuple[list[str], list[float]]] = {}
    base: dict[str, float] = {}
    for sym in symbols:
        rows = series.get(sym) or []
        if not rows:
            continue
        ds = [r["date"] for r in rows]
        cs = [float(r["close"]) if r["close"] else 0.0 for r in rows]
        cached[sym] = (ds, cs)
        for i, d in enumerate(ds):
            if d >= days[0] and cs[i] > 0:
                base[sym] = cs[i]
                break
    cursors: dict[str, int] = {s: 0 for s in cached}
    out: list[dict[str, Any]] = []
    for d in days:
        vals: list[float] = []
        for sym, (ds, cs) in cached.items():
            base_close = base.get(sym)
            if base_close is None:
                continue
            i = cursors[sym]
            while i + 1 < len(ds) and ds[i + 1] <= d:
                i += 1
            cursors[sym] = i
            if ds[i] > d or cs[i] <= 0:
                continue
            vals.append(cs[i] / base_close)
        idx = (sum(vals) / len(vals) * 100.0) if vals else 100.0
        out.append({"date": d, "value": idx})
    return out


def sector_attribution(trades: list[Any]) -> dict[str, dict[str, Any]]:
    """Aggregate closed trades by sector; trades must expose
    ``.sector`` and ``.pnl`` attributes (Trade dataclass shape)."""
    out: dict[str, dict[str, Any]] = {}
    for t in trades:
        key = getattr(t, "sector", None) or "기타"
        slot = out.setdefault(key, {
            "n_trades": 0, "gross_pnl": 0.0, "gross_win": 0.0,
            "gross_loss": 0.0, "wins": 0, "losses": 0,
        })
        slot["n_trades"] += 1
        slot["gross_pnl"] += t.pnl
        if t.pnl > 0:
            slot["gross_win"] += t.pnl
            slot["wins"] += 1
        elif t.pnl < 0:
            slot["gross_loss"] += t.pnl
            slot["losses"] += 1
    for slot in out.values():
        slot["win_rate"] = slot["wins"] / slot["n_trades"] if slot["n_trades"] else 0.0
    return out
