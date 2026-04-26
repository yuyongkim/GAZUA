"""CFTC Commitments of Traders (COT) adapter — institutional positioning."""
from __future__ import annotations

from ky_adapters.cftc.client import CFTCAdapter, COTRow

__all__ = ["CFTCAdapter", "COTRow"]
