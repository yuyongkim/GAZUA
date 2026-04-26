"""6 Market Wizards screens.

Each module exposes:
    NAME           : canonical preset key ('minervini', 'oneil', ...)
    DISPLAY_NAME   : human label
    CONDITION      : one-line rule string shown in UI
    screen(panel)  : -> list[WizardHit]

The registry below lets the API route resolve a preset name to a module
without hardcoding the import site.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Callable

from ky_core.scanning.loader import Panel
from ky_core.scanning.wizards import (
    minervini as _minervini,
    oneil as _oneil,
    darvas as _darvas,
    livermore as _livermore,
    zanger as _zanger,
    weinstein as _weinstein,
)


@dataclass
class WizardHit:
    symbol: str
    name: str
    market: str
    sector: str
    close: float
    pct_1d: float
    vol_x: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


WizardScreener = Callable[[Panel], list[WizardHit]]


REGISTRY: dict[str, dict[str, Any]] = {
    "minervini": {
        "name": _minervini.DISPLAY_NAME,
        "condition": _minervini.CONDITION,
        "screen": _minervini.screen,
    },
    "oneil": {
        "name": _oneil.DISPLAY_NAME,
        "condition": _oneil.CONDITION,
        "screen": _oneil.screen,
    },
    "darvas": {
        "name": _darvas.DISPLAY_NAME,
        "condition": _darvas.CONDITION,
        "screen": _darvas.screen,
    },
    "livermore": {
        "name": _livermore.DISPLAY_NAME,
        "condition": _livermore.CONDITION,
        "screen": _livermore.screen,
    },
    "zanger": {
        "name": _zanger.DISPLAY_NAME,
        "condition": _zanger.CONDITION,
        "screen": _zanger.screen,
    },
    "weinstein": {
        "name": _weinstein.DISPLAY_NAME,
        "condition": _weinstein.CONDITION,
        "screen": _weinstein.screen,
    },
}


def list_presets() -> list[str]:
    return list(REGISTRY.keys())


__all__ = ["WizardHit", "WizardScreener", "REGISTRY", "list_presets"]
