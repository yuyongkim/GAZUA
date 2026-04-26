"""Quality x Momentum — ROE top 50 ∩ 6M momentum top 50.

Monthly rebalance. Picks up to 10 symbols that appear in both the ROE
top-50 and 6-month return top-50 lists. Ranked by combined z-score.

Fundamentals are loaded point-in-time via
:func:`ky_core.backtest.strategies._pit.latest_only` (see that module
for how we treat the bulk-import DART sentinel report_date).
"""
from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from typing import Any

from ky_core.backtest.engine import Candidate, PanelView
from ky_core.backtest.strategies._pit import latest_only


@dataclass
class QualityMomentumStrategy:
    name: str = "quality_momentum"
    rebalance: str = "monthly"
    top_quality: int = 50
    top_momentum: int = 50
    top_n: int = 10

    def pick(self, view: PanelView, as_of: str) -> list[Candidate]:
        fundamentals = latest_only(as_of)

        # -------- momentum ----------------------------------------------
        moms: list[tuple[str, float]] = []
        for sym in view.available_symbols(min_history_days=130):
            closes = view.closes_up_to(sym, n=130)
            if len(closes) < 126 or closes[-126] <= 0:
                continue
            moms.append((sym, closes[-1] / closes[-126] - 1.0))
        moms.sort(key=lambda t: t[1], reverse=True)
        mom_top = dict(moms[: self.top_momentum])
        mom_set = set(mom_top)

        # -------- quality (ROE) -----------------------------------------
        rois: list[tuple[str, float]] = []
        for sym, f in fundamentals.items():
            eq = f.get("total_equity")
            ni = f.get("net_income")
            if not eq or eq <= 0 or ni is None:
                continue
            rois.append((sym, ni / eq))
        rois.sort(key=lambda t: t[1], reverse=True)
        roi_top = dict(rois[: self.top_quality])
        roi_set = set(roi_top)

        inter = mom_set & roi_set
        if not inter:
            # Relax fallback: momentum winners with positive ROE.
            inter = {s for s in mom_set if (fundamentals.get(s) or {}).get("net_income", -1) > 0}

        mom_z = _z_map(moms)
        roi_z = _z_map(rois)
        scored: list[tuple[float, Candidate]] = []
        for sym in inter:
            z = mom_z.get(sym, 0.0) + roi_z.get(sym, 0.0)
            rec = fundamentals.get(sym) or {}
            ni = rec.get("net_income") or 0.0
            eq = rec.get("total_equity") or 0.0
            roe = (ni / eq) if eq > 0 else 0.0
            mom_val = mom_top.get(sym, 0.0)
            reason = f"ROE {roe:.1%} · 6m {mom_val:+.1%}"
            scored.append((z, Candidate(
                symbol=sym, score=z, reason=reason, target_mult=2.5,
            )))
        scored.sort(key=lambda t: t[0], reverse=True)
        return [c for _z, c in scored[: self.top_n]]


def build() -> QualityMomentumStrategy:
    return QualityMomentumStrategy()


def _z_map(pairs: list[tuple[str, float]]) -> dict[str, float]:
    if not pairs:
        return {}
    values = [v for _s, v in pairs]
    n = len(values)
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / max(1, n - 1)
    sd = sqrt(var) or 1.0
    return {s: (v - mean) / sd for s, v in pairs}
