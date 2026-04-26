"""Darvas Box breakout.

Rule set (EOD):
  - Price has traded inside a 3-5% range over the last 20 days
  - Today's close pushes above the top of that box
  - Volume >= 1.5 × 50-day avg
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ky_core.scanning.loader import Panel
from ky_core.scanning.wizards._common import (
    avg_vol,
    closes,
    highs,
    lows,
    meta,
    pct_change,
    volumes,
)

if TYPE_CHECKING:
    from ky_core.scanning.wizards import WizardHit

DISPLAY_NAME = "Darvas"
CONDITION = "Box Breakout · Vol≥1.5×"


def screen(panel: Panel) -> "list[WizardHit]":
    from ky_core.scanning.wizards import WizardHit

    out: list[WizardHit] = []
    for sym, rows in panel.series.items():
        if len(rows) < 60:
            continue
        cs = closes(panel, sym)
        hs = highs(panel, sym)
        ls = lows(panel, sym)
        vs = volumes(panel, sym)
        # box = window [-21:-1]; today = -1
        if len(cs) < 22:
            continue
        box_hi = max(hs[-22:-1])
        box_lo = min(ls[-22:-1])
        if box_hi <= 0 or box_lo <= 0:
            continue
        range_pct = (box_hi - box_lo) / box_lo
        if not (0.02 <= range_pct <= 0.10):  # tight box: 2-10% range
            continue
        if cs[-1] < box_hi:
            continue  # today must break above box top
        a50 = avg_vol(vs, 50)
        if a50 <= 0 or vs[-1] < a50 * 1.5:
            continue

        m = meta(panel, sym)
        vx = vs[-1] / a50
        out.append(WizardHit(
            symbol=sym,
            name=m["name"],
            market=m["market"],
            sector=m["sector"],
            close=cs[-1],
            pct_1d=pct_change(cs, 1),
            vol_x=vx,
            reason=f"Box {range_pct:.1%} 돌파 · Vol {vx:.1f}×",
        ))
    out.sort(key=lambda h: h.vol_x, reverse=True)
    return out
