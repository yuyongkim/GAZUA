"""Livermore — Line of Least Resistance (pivot breakout).

Rule set:
  - Today's close is a NEW 60-day high (pivot level)
  - Higher highs in last 3 successive 20-day windows
  - Close > SMA50 > SMA200 (uptrend confirmed)
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ky_core.scanning.loader import Panel
from ky_core.scanning.wizards._common import (
    avg_vol,
    closes,
    highs,
    meta,
    pct_change,
    sma,
    volumes,
)

if TYPE_CHECKING:
    from ky_core.scanning.wizards import WizardHit

DISPLAY_NAME = "Livermore"
CONDITION = "Pivot · 60d new high · trend confirmed"


def screen(panel: Panel) -> "list[WizardHit]":
    from ky_core.scanning.wizards import WizardHit

    out: list[WizardHit] = []
    for sym in panel.series:
        cs = closes(panel, sym)
        if len(cs) < 70:
            continue
        hs = highs(panel, sym)
        piv = max(hs[-61:-1])
        if cs[-1] < piv:
            continue
        # three successive higher highs across 20d blocks
        h1 = max(hs[-60:-40]) if len(hs) >= 60 else 0
        h2 = max(hs[-40:-20]) if len(hs) >= 40 else 0
        h3 = max(hs[-20:])
        if not (h3 > h2 > h1):
            continue
        sma50 = sma(cs, 50)
        sma200 = sma(cs, 200) if len(cs) >= 200 else sma50
        if not (cs[-1] > sma50 > sma200):
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
            reason=f"60d 피벗 돌파 · HH trend · SMA50>SMA200",
        ))
    out.sort(key=lambda h: h.pct_1d, reverse=True)
    return out
