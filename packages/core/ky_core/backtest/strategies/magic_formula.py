"""Magic Formula (Joel Greenblatt) — Earnings Yield x Return on Capital.

Annual rebalance. Ranks universe by two percentile ranks:

    EY  = operating_income / enterprise_value_proxy
    ROC = operating_income / total_assets

ky.db doesn't carry shares-outstanding, so the EV proxy is
``close * total_equity`` (consistent across the cross-section since
Magic-Formula uses relative ranks). ROC simplifies to operating_income
over total_assets because current-liabilities aren't tracked.

The top 30 names (sum of ranks) become candidates; the engine enforces
the 10 max-positions / 3-per-sector caps so the executable portfolio is
the top ~10 with sector spread.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ky_core.backtest.engine import Candidate, PanelView
from ky_core.backtest.strategies._pit import latest_only


@dataclass
class MagicFormulaStrategy:
    name: str = "magic_formula"
    rebalance: str = "annual"
    top_n: int = 30

    def pick(self, view: PanelView, as_of: str) -> list[Candidate]:
        fundamentals = latest_only(as_of)
        symbols = [s for s in view.available_symbols(min_history_days=60) if s in fundamentals]
        rows: list[dict[str, Any]] = []
        for sym in symbols:
            f = fundamentals[sym]
            closes = view.closes_up_to(sym, n=5)
            if not closes:
                continue
            close = closes[-1]
            op = f.get("operating_income")
            eq = f.get("total_equity")
            ta = f.get("total_assets")
            if op is None or eq is None or eq <= 0 or ta is None or ta <= 0:
                continue
            # EV proxy: close * book_value(equity), scaled to keep numbers comparable
            ev = max(1.0, close * eq / 1_000_000.0)
            ey = op / ev
            roc = op / ta
            if ey <= 0 or roc <= 0:   # Greenblatt filters loss-makers
                continue
            rows.append({"symbol": sym, "ey": ey, "roc": roc})

        if not rows:
            return []

        _attach_rank(rows, "ey", "rank_ey")
        _attach_rank(rows, "roc", "rank_roc")
        for r in rows:
            r["combined"] = r["rank_ey"] + r["rank_roc"]
        rows.sort(key=lambda r: r["combined"])
        top = rows[: self.top_n]
        return [
            Candidate(
                symbol=r["symbol"],
                score=1.0 - (r["combined"] / (2 * len(rows))),
                reason=f"EY#{int(r['rank_ey'])} · ROC#{int(r['rank_roc'])}",
                target_mult=3.0,
            )
            for r in top
        ]


def build() -> MagicFormulaStrategy:
    return MagicFormulaStrategy()


def _attach_rank(rows: list[dict[str, Any]], key: str, rank_key: str) -> None:
    ordered = sorted(rows, key=lambda r: r[key], reverse=True)
    for i, r in enumerate(ordered):
        r[rank_key] = i + 1
