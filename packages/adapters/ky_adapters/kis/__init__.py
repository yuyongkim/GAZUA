"""KIS (Korea Investment & Securities) adapter — activated 2026-04-22.

Real OAuth2 token issuance and authenticated REST calls are enabled.
Credentials are loaded from ``~/.gazua/shared.env``.
"""
from __future__ import annotations

from ky_adapters.kis.client import KISAdapter
from ky_adapters.kis.finance import (
    KIS_ENDPOINTS,
    KIS_OUTPUT_MAP,
    KISFinanceAdapter,
    KISFinanceEndpoint,
)
from ky_adapters.kis.market import (
    KIS_MARKET_ENDPOINTS,
    KISMarketAdapter,
    KISMarketEndpoint,
)
from ky_adapters.kis.websocket import (
    TR_ORDERBOOK,
    TR_TICK,
    WS_URL_DEMO,
    WS_URL_REAL,
    KISFrame,
    KISWebSocketClient,
    issue_approval_key,
    normalise_orderbook_record,
    normalise_tick_record,
    parse_frame,
    stream_symbol,
)

__all__ = [
    "KISAdapter",
    "KISFinanceAdapter",
    "KISFinanceEndpoint",
    "KIS_ENDPOINTS",
    "KIS_OUTPUT_MAP",
    "KISMarketAdapter",
    "KISMarketEndpoint",
    "KIS_MARKET_ENDPOINTS",
    # WebSocket
    "KISWebSocketClient",
    "KISFrame",
    "TR_ORDERBOOK",
    "TR_TICK",
    "WS_URL_REAL",
    "WS_URL_DEMO",
    "issue_approval_key",
    "parse_frame",
    "normalise_orderbook_record",
    "normalise_tick_record",
    "stream_symbol",
]
