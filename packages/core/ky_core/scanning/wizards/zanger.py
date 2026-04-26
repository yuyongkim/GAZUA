"""Zanger — Gap + Volume + Close at High-of-Day.

True Zanger needs intraday data; EOD approximation:
  - Today's open >= previous close × 1.03 (gap-up 3%+)
  - Today's volume >= 1.5 × 50-day avg
  - Today's close >= 0.95 × today's high (finished near HOD)
  - Close > SMA20 (short uptrend)
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ky_core.scanning.loader import Panel
from ky_core.scanning.wizards._common import (
    avg_vol,
    closes,
    meta,
    pct_change,
    rows,
    sma,
    volumes,
)

if TYPE_CHECKING:
    from ky_core.scanning.wizards import WizardHit

DISPLAY_NAME = "Zanger"
CONDITION = "Gap≥3% · Vol≥1.5× · Close≈HOD"


def screen(panel: Panel) -> "list[WizardHit]":
    from ky_core.scanning.wizards import WizardHit

    out: list[WizardHit] = []
    for sym in panel.series:
        data = rows(panel, sym)
        if len(data) < 25:
            continue
        last = data[-1]
        prev = data[-2]
        pc = prev["close"]
        if not (pc and last.get("open")):
            continue
        gap = (last["open"] / pc - 1.0) * 100
        if gap < 3.0:
            continue
        if not last.get("high") or last["high"] <= 0:
            continue
        hod_ratio = last["close"] / last["high"]
        if hod_ratio < 0.95:
            continue
        vs = volumes(panel, sym)
        a50 = avg_vol(vs, 50)
        if a50 <= 0 or vs[-1] < a50 * 1.5:
            continue
        cs = closes(panel, sym)
        if cs[-1] <= sma(cs, 20):
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
            reason=f"Gap {gap:+.1f}% · HOD {hod_ratio:.2f} · Vol {vx:.1f}×",
        ))
    out.sort(key=lambda h: h.pct_1d, reverse=True)
    return out
