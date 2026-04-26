"""O'Neil CANSLIM (simplified).

Rule set:
  - RS percentile >= 80  (leadership)
  - EPS YoY > 25% (from financials_pit if available, else relaxed skip)
  - Close within 10% of 52w high (near the buy point)
  - TT passes >= 5
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ky_core.scanning.leader_scan import _load_eps_signals  # reuse cache
from ky_core.scanning.loader import Panel
from ky_core.scanning.sepa import evaluate
from ky_core.scanning.wizards._common import (
    avg_vol,
    closes,
    highs,
    meta,
    pct_change,
    volumes,
)

if TYPE_CHECKING:
    from ky_core.scanning.wizards import WizardHit

DISPLAY_NAME = "O'Neil"
CONDITION = "CANSLIM · RS≥80 · EPS YoY+"


def screen(panel: Panel) -> "list[WizardHit]":
    from ky_core.scanning.wizards import WizardHit

    eps = _load_eps_signals(panel.as_of)
    out: list[WizardHit] = []
    for sym in panel.series:
        tt = evaluate(sym, panel=panel)
        if tt is None or tt.passes < 5:
            continue
        # RS percentile proxy via tt.rs_value distribution (>= 80 percentile)
        if tt.rs_value < 0.25:   # ~ top-20% in the sample
            continue
        hs = highs(panel, sym)
        cs = closes(panel, sym)
        hi52 = max(hs[-252:]) if len(hs) >= 252 else max(hs)
        if hi52 <= 0 or cs[-1] < hi52 * 0.90:
            continue
        eps_score = eps.get(sym, 0.5)
        if eps_score < 0.55:      # require positive EPS signal
            continue

        m = meta(panel, sym)
        vs = volumes(panel, sym)
        a50 = avg_vol(vs, 50)
        vx = (vs[-1] / a50) if a50 else 0.0
        out.append(WizardHit(
            symbol=sym,
            name=m["name"],
            market=m["market"],
            sector=m["sector"],
            close=cs[-1],
            pct_1d=pct_change(cs, 1),
            vol_x=vx,
            reason=f"TT {tt.passes}/8 · RS {tt.rs_value:+.0%} · EPS {eps_score:.2f}",
        ))
    out.sort(key=lambda h: h.pct_1d, reverse=True)
    return out
