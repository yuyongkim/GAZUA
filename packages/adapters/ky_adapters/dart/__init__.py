"""DART (Korea Financial Supervisory Service OpenDART) adapter."""
from __future__ import annotations

from ky_adapters.dart.client import DARTAdapter, Filing
from ky_adapters.dart.dividends import DARTDividendExtractor, DividendYear
from ky_adapters.dart.segments import DARTSegmentExtractor, Segment

__all__ = [
    "DARTAdapter",
    "DARTDividendExtractor",
    "DARTSegmentExtractor",
    "DividendYear",
    "Filing",
    "Segment",
]
