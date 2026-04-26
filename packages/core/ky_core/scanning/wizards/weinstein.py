"""Weinstein — Stage Analysis (Stage 2 advance).

Rule set:
  - Close > SMA30 (Weinstein's 30-week MA ≈ SMA30 on weekly; we use 150d SMA)
  - SMA30 (150d) is rising over last 22 days
  - Price crossed above SMA30 in the last 22 days OR held above it for > 22 days
  - Volume on breakout day >= 1.3 × 50d avg (relaxed)
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ky_core.scanning.loader import Panel
from ky_core.scanning.wizards._common import (
    avg_vol,
    closes,
    meta,
    pct_change,
    sma,
    volumes,
)

if TYPE_CHECKING:
    from ky_core.scanning.wizards import WizardHit

DISPLAY_NAME = "Weinstein"
CONDITION = "Stage 2 · SMA30 돌파 & 상승"


def screen(panel: Panel) -> "list[WizardHit]":
    from ky_core.scanning.wizards import WizardHit

    out: list[WizardHit] = []
    for sym in panel.series:
        cs = closes(panel, sym)
        if len(cs) < 180:
            continue
        s150_now = sma(cs, 150)
        s150_prev = sma(cs[:-22], 150)
        if s150_now <= s150_prev:
            continue
        if cs[-1] <= s150_now:
            continue
        # Either crossed in last 22 days OR held 22d
        window = cs[-22:]
        prev_win = cs[-44:-22] if len(cs) >= 44 else []
        prev_above = all(c > s150_prev for c in prev_win) if prev_win else False
        crossed = any(c <= s150_now for c in window[:-1]) and window[-1] > s150_now
        if not (crossed or prev_above):
            continue
        vs = volumes(panel, sym)
        a50 = avg_vol(vs, 50)
        if a50 <= 0 or vs[-1] < a50 * 1.3:
            # Still allow if price is simply above SMA30 (Stage 2 continuation)
            if not prev_above:
                continue

        m = meta(panel, sym)
        vx = (vs[-1] / a50) if a50 else 0.0
        out.append(WizardHit(
            symbol=sym,
            name=m["name"],
            market=m["market"],
            sector=m["sector"],
            close=cs[-1],
            pct_1d=pct_change(cs, 1),
            vol_x=vx,
            reason=f"Stage 2 · SMA30 rising · {'cross' if crossed else 'hold'}",
        ))
    out.sort(key=lambda h: h.pct_1d, reverse=True)
    return out
