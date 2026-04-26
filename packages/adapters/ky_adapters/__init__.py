"""ky_adapters — external data source adapters.

Contract: every adapter inherits from :class:`ky_adapters.base.BaseAdapter` and
implements ``healthcheck`` / ``close``. Data-source policy (2026-04-25):

Korean equities + disclosures:
- KIS:     single backbone for KR market data / orders
- DART:    disclosures + PIT financials
- KOSIS:   통계청 industry/employment series
- ECOS:    한은 rates / FX / sentiment
- EXIM:    한국수출입은행 환율 (legacy label — value historically pointed at the
           data.go.kr 일반 인증키, see ``DATA_GO_KR_API_KEY`` in shared.env)
- naver_fnguide: financial statements crawl (KIS doesn't expose these)

Korean trade — added 2026-04-25:
- customs: 관세청 무역통계 6 APIs via data.go.kr (institution code 1220000)

Macro / global:
- FRED:        US Federal Reserve macro
- EIA:         US energy
- OECD:        SDMX REST — CLI / PMI / sentiment indicators
- worldbank:   long-run GDP / industry share / demographics
- un_comtrade: HS-code global trade flows
- cftc:        Commitments of Traders — institutional positioning

Sentiment:
- pytrends: Google Trends (optional dep — install with
            ``pip install ky-adapters[trends]``)

Do NOT create Kiwoom or Yahoo adapters — policy is KIS-only for KR quotes.
yfinance was considered for global tickers (SPY / SOX / etc.) but deferred
until a clear non-overlap with KIS is established.
"""
from __future__ import annotations

from ky_adapters.base import (
    AdapterError,
    AuthError,
    BaseAdapter,
    HealthStatus,
    RateLimitError,
)

__version__ = "0.2.0"

__all__ = [
    "AdapterError",
    "AuthError",
    "BaseAdapter",
    "HealthStatus",
    "RateLimitError",
    "__version__",
]
