"""Sector indicator registry — concrete (sector, indicator, source) specs."""
from __future__ import annotations

from ky_core.sectors.registry import (
    IndicatorSpec,
    all_specs,
    specs_by_source,
    total_count,
)

__all__ = ["IndicatorSpec", "all_specs", "specs_by_source", "total_count"]
