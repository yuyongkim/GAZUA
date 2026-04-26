"""Google Trends adapter (via pytrends Python lib).

Optional dependency: ``pip install pytrends``. Adapter falls back gracefully
if the library isn't installed.
"""
from __future__ import annotations

from ky_adapters.pytrends.client import PyTrendsAdapter, TrendsObservation

__all__ = ["PyTrendsAdapter", "TrendsObservation"]
