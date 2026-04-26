"""HTML / API scrapers for series with no stable institutional API.

Stability is explicitly low — sites can change layout without notice.
Each scraper raises AdapterError on parse failure so the collector
records it instead of silently writing bad rows.

Adapters in this module:
  - YFinanceCommoditiesAdapter — Yahoo Finance via yfinance lib
                                 (HG=F copper, ALI=F aluminum, gold, silver, oil)
  - OpenFDAAdapter             — api.fda.gov drug approvals
  - StooqQuoteAdapter          — fallback HTML scraper (rate-limited)
  - BDIScraperAdapter          — Baltic Dry Index (tradingeconomics-blocked stub)
"""
from __future__ import annotations

from ky_adapters.scrapers.bdi import BDIScraperAdapter, BDIRow
from ky_adapters.scrapers.yfinance_commodities import YFinanceCommoditiesAdapter, CommodityRow
from ky_adapters.scrapers.openfda import OpenFDAAdapter, FDAApprovalRow
from ky_adapters.scrapers.stooq_quote import StooqQuoteAdapter, StooqRow

__all__ = [
    "BDIScraperAdapter", "BDIRow",
    "YFinanceCommoditiesAdapter", "CommodityRow",
    "OpenFDAAdapter", "FDAApprovalRow",
    "StooqQuoteAdapter", "StooqRow",
]
