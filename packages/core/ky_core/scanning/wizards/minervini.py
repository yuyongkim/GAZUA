"""Minervini (SEPA + VCP + Pivot).

Rule set (simplified for EOD):
  - TT passes >= 6 of 8
  - RS percentile >= 70
  - VCP stage >= 2 (contraction detected)
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ky_core.scanning.loader import Panel
from ky_core.scanning.sepa import evaluate
from ky_core.scanning.vcp import detect_vcp
from ky_core.scanning.wizards._common import (
    avg_vol,
    closes,
    meta,
    pct_change,
    volumes,
)

if TYPE_CHECKING:
    from ky_core.scanning.wizards import WizardHit

DISPLAY_NAME = "Minervini"
CONDITION = "TT 6/8+ · RS≥70 · VCP"


def screen(panel: Panel) -> "list[WizardHit]":
    from ky_core.scanning.wizards import WizardHit

    out: list[WizardHit] = []
    for sym in panel.series:
        tt = evaluate(sym, panel=panel)
        if tt is None or tt.passes < 6:
            continue
        if not tt.rs_rating_ge_70:
            continue
        vcp = detect_vcp(sym, panel=panel)
        if vcp.stage < 2:
            continue

        m = meta(panel, sym)
        cs = closes(panel, sym)
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
            reason=f"TT {tt.passes}/8 · RS {tt.rs_value:+.0%} · VCP stage {vcp.stage}",
        ))
    out.sort(key=lambda h: (h.pct_1d, h.vol_x), reverse=True)
    return out
