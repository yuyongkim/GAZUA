"""Value-QMJ (Quality Minus Junk value screen).

A simplified Asness-style screen:

    Quality   = ROE + ROA - leverage
    Value     = book-to-price proxy
    Growth    = YoY net-income growth (when available)
    Junk      = high leverage penalty

Score = Quality_z + Value_z + Growth_z - 0.5 * Leverage_z.
Quarterly rebalance. Picks top 10.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from typing import Any

from ky_core.backtest.engine import Candidate, PanelView
from ky_core.backtest.strategies._pit import load_latest_fundamentals


@dataclass
class ValueQmjStrategy:
    name: str = "value_qmj"
    rebalance: str = "quarterly"
    top_n: int = 10

    def pick(self, view: PanelView, as_of: str) -> list[Candidate]:
        per_sym = load_latest_fundamentals(as_of, max_records_per_symbol=2)
        rows: list[dict[str, Any]] = []
        for sym in view.available_symbols(min_history_days=60):
            closes = view.closes_up_to(sym, n=5)
            if not closes:
                continue
            close = closes[-1]
            recs = per_sym.get(sym) or []
            if not recs:
                continue
            latest = recs[0]
            prev = recs[1] if len(recs) > 1 else None
            eq = latest.get("total_equity")
            ta = latest.get("total_assets")
            ni = latest.get("net_income")
            if eq is None or eq <= 0 or ta is None or ta <= 0 or ni is None:
                continue
            roe = ni / eq
            roa = ni / ta
            leverage = max(0.0, (ta - eq) / ta)
            book_to_price = eq / max(close, 1e-6) / 1_000_000.0
            prior_ni = prev.get("net_income") if prev else None
            growth = 0.0
            if prior_ni and prior_ni != 0:
                growth = (ni - prior_ni) / abs(prior_ni)
            rows.append({
                "symbol": sym,
                "quality": roe + roa - leverage,
                "value": book_to_price,
                "growth": growth,
                "leverage": leverage,
                "roe": roe,
            })
        if not rows:
            return []

        q_z = _z(rows, "quality")
        v_z = _z(rows, "value")
        g_z = _z(rows, "growth")
        lev_z = _z(rows, "leverage")
        scored: list[tuple[float, Candidate]] = []
        for i, r in enumerate(rows):
            score = q_z[i] + v_z[i] + g_z[i] - 0.5 * lev_z[i]
            reason = f"ROE {r['roe']:.1%} · lev {r['leverage']:.2f}"
            scored.append((score, Candidate(
                symbol=r["symbol"], score=score, reason=reason, target_mult=3.0,
            )))
        scored.sort(key=lambda t: t[0], reverse=True)
        return [c for _s, c in scored[: self.top_n]]


def build() -> ValueQmjStrategy:
    return ValueQmjStrategy()


def _z(rows: list[dict[str, Any]], key: str) -> list[float]:
    values = [r[key] for r in rows]
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / max(1, len(values) - 1)
    sd = sqrt(var) or 1.0
    return [(v - mean) / sd for v in values]
