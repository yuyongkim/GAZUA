"""KIS 국내주식 시세 API — activated 2026-04-22.

Five endpoints that cover the real-data needs of the Execute tab:

  ========================  ==============  ==========================================
  endpoint                  tr_id           path
  ========================  ==============  ==========================================
  stock_price (현재가)       FHKST01010100  /uapi/domestic-stock/v1/quotations/inquire-price
  stock_orderbook (호가)     FHKST01010200  /uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn
  investor_trend            FHKST01010900  /uapi/domestic-stock/v1/quotations/inquire-investor
  stock_fluctuation          FHPST01700000  /uapi/domestic-stock/v1/ranking/fluctuation
  program_trade              FHPPG04650101  /uapi/domestic-stock/v1/quotations/program-trade-by-stock
  ========================  ==============  ==========================================
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from ky_adapters.base import AdapterError, BaseAdapter, HealthStatus
from ky_adapters.kis.client import KISAdapter
from ky_adapters.kis.finance import _LIMITER

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class KISMarketEndpoint:
    name: str
    url: str
    tr_id: str
    description: str


KIS_MARKET_ENDPOINTS: dict[str, KISMarketEndpoint] = {
    "stock_price": KISMarketEndpoint(
        name="stock_price",
        url="/uapi/domestic-stock/v1/quotations/inquire-price",
        tr_id="FHKST01010100",
        description="주식현재가 시세 (v1_국내주식-008)",
    ),
    "stock_orderbook": KISMarketEndpoint(
        name="stock_orderbook",
        url="/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn",
        tr_id="FHKST01010200",
        description="주식현재가 호가/예상체결 (v1_국내주식-011)",
    ),
    "investor_trend": KISMarketEndpoint(
        name="investor_trend",
        url="/uapi/domestic-stock/v1/quotations/inquire-investor",
        tr_id="FHKST01010900",
        description="주식현재가 투자자 (v1_국내주식-012)",
    ),
    "stock_fluctuation": KISMarketEndpoint(
        name="stock_fluctuation",
        url="/uapi/domestic-stock/v1/ranking/fluctuation",
        tr_id="FHPST01700000",
        description="등락률 순위 (v1_국내주식-088)",
    ),
    "program_trade": KISMarketEndpoint(
        name="program_trade",
        url="/uapi/domestic-stock/v1/quotations/program-trade-by-stock",
        tr_id="FHPPG04650101",
        description="종목별 프로그램매매추이(체결) (v1_국내주식-044)",
    ),
    "index_daily_price": KISMarketEndpoint(
        name="index_daily_price",
        url="/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice",
        tr_id="FHKUP03500100",
        description="국내업종 일자별 지수 (v1_국내주식-068)",
    ),
}


def _normalise_price(raw: dict[str, Any]) -> dict[str, Any]:
    """Map FHKST01010100 output to snake_case frontend fields."""
    mapping = {
        "stck_prpr": "price",
        "prdy_vrss": "change",
        "prdy_vrss_sign": "change_sign",
        "prdy_ctrt": "change_pct",
        "acml_vol": "volume",
        "acml_tr_pbmn": "trade_value",
        "stck_oprc": "open",
        "stck_hgpr": "high",
        "stck_lwpr": "low",
        "stck_mxpr": "upper_limit",
        "stck_llam": "lower_limit",
        "w52_hgpr": "w52_high",
        "w52_lwpr": "w52_low",
        "hts_avls": "market_cap",
        "per": "per",
        "pbr": "pbr",
        "eps": "eps",
        "bps": "bps",
    }
    out: dict[str, Any] = {"_raw": dict(raw)}
    for k, v in raw.items():
        out[mapping.get(k, k)] = v
    return out


def _normalise_orderbook(raw1: dict[str, Any], raw2: dict[str, Any]) -> dict[str, Any]:
    """Flatten the 10-level bid/ask ladder from output1 + output2."""
    levels: list[dict[str, Any]] = []
    for i in range(1, 11):
        ask_px = raw1.get(f"askp{i}")
        bid_px = raw1.get(f"bidp{i}")
        if ask_px is None and bid_px is None:
            continue
        levels.append({
            "level": i,
            "ask_price": ask_px,
            "ask_qty": raw1.get(f"askp_rsqn{i}"),
            "bid_price": bid_px,
            "bid_qty": raw1.get(f"bidp_rsqn{i}"),
        })
    return {
        "levels": levels,
        "total_ask_qty": raw1.get("total_askp_rsqn"),
        "total_bid_qty": raw1.get("total_bidp_rsqn"),
        "expected_price": raw2.get("antc_cnpr"),
        "expected_qty": raw2.get("antc_cntg_vrss"),
        "_raw1": raw1,
        "_raw2": raw2,
    }


# --------------------------------------------------------------------------- #
# Adapter                                                                     #
# --------------------------------------------------------------------------- #


class KISMarketAdapter(BaseAdapter):
    """Live KIS market/price adapter."""

    source_id = "kis_market"
    priority = 1

    def __init__(
        self,
        *,
        app_key: str | None = None,
        app_secret: str | None = None,
        base_url: str | None = None,
        env: str | None = None,
        client: Optional[KISAdapter] = None,
    ) -> None:
        super().__init__()
        self.app_key = app_key
        self.app_secret = app_secret
        self.base_url = (base_url or "https://openapi.koreainvestment.com:9443").rstrip("/")
        self.env = env or "real"
        self._kis = client or KISAdapter(
            app_key=app_key, app_secret=app_secret, base_url=base_url, env=env
        )

    @classmethod
    def from_settings(cls) -> "KISMarketAdapter":
        kis = KISAdapter.from_settings()
        return cls(
            app_key=kis.app_key,
            app_secret=kis.app_secret,
            base_url=kis.base_url,
            env=kis.env,
            client=kis,
        )

    # --------- Contract ---------

    def healthcheck(self) -> dict[str, Any]:
        base = self._kis.healthcheck()
        base["source_id"] = self.source_id
        base.setdefault("endpoints", list(KIS_MARKET_ENDPOINTS.keys()))
        return base

    # --------- Public API ---------

    def get_quote(self, symbol: str, *, market: str = "J") -> dict[str, Any]:
        """Current price + change + high/low/OHLC for a single symbol."""
        ep = KIS_MARKET_ENDPOINTS["stock_price"]
        params = {
            "FID_COND_MRKT_DIV_CODE": market,
            "FID_INPUT_ISCD": str(symbol).zfill(6),
        }
        _LIMITER.wait()
        data = self._kis.call("GET", ep.url, tr_id=ep.tr_id, params=params)
        output = data.get("output") or {}
        if not isinstance(output, dict):
            raise AdapterError("stock_price unexpected output shape")
        return _normalise_price(output)

    def get_orderbook(self, symbol: str, *, market: str = "J") -> dict[str, Any]:
        """10-level bid/ask ladder."""
        ep = KIS_MARKET_ENDPOINTS["stock_orderbook"]
        params = {
            "FID_COND_MRKT_DIV_CODE": market,
            "FID_INPUT_ISCD": str(symbol).zfill(6),
        }
        _LIMITER.wait()
        data = self._kis.call("GET", ep.url, tr_id=ep.tr_id, params=params)
        output1 = data.get("output1") or {}
        output2 = data.get("output2") or {}
        return _normalise_orderbook(output1, output2)

    def get_investor_trend(self, symbol: str, *, market: str = "J") -> list[dict[str, Any]]:
        """Recent daily investor (개인/외국인/기관) net trade."""
        ep = KIS_MARKET_ENDPOINTS["investor_trend"]
        params = {
            "FID_COND_MRKT_DIV_CODE": market,
            "FID_INPUT_ISCD": str(symbol).zfill(6),
        }
        _LIMITER.wait()
        data = self._kis.call("GET", ep.url, tr_id=ep.tr_id, params=params)
        output = data.get("output") or []
        if isinstance(output, dict):
            output = [output]
        return list(output)

    def get_fluctuation_ranking(
        self,
        *,
        market: str = "J",
        rank_sort: str = "0",  # "0": 상승률, "1": 하락률
        top_n: int = 30,
    ) -> list[dict[str, Any]]:
        """등락률 Top-N — used on the krstock overview panel."""
        ep = KIS_MARKET_ENDPOINTS["stock_fluctuation"]
        params = {
            "fid_cond_mrkt_div_code": market,
            "fid_cond_scr_div_code": "20170",
            "fid_input_iscd": "0000",
            "fid_rank_sort_cls_code": rank_sort,
            "fid_input_cnt_1": str(top_n),
            "fid_prc_cls_code": "0",
            "fid_input_price_1": "",
            "fid_input_price_2": "",
            "fid_vol_cnt": "",
            "fid_trgt_cls_code": "0",
            "fid_trgt_exls_cls_code": "0",
            "fid_div_cls_code": "0",
            "fid_rsfl_rate1": "",
            "fid_rsfl_rate2": "",
        }
        _LIMITER.wait()
        data = self._kis.call("GET", ep.url, tr_id=ep.tr_id, params=params)
        output = data.get("output") or []
        if isinstance(output, dict):
            output = [output]
        return list(output)

    def get_program_trade(self, symbol: str, *, market: str = "J") -> list[dict[str, Any]]:
        """Program trading (프로그램매매) flow for a symbol."""
        ep = KIS_MARKET_ENDPOINTS["program_trade"]
        params = {
            "FID_COND_MRKT_DIV_CODE": market,
            "FID_INPUT_ISCD": str(symbol).zfill(6),
        }
        _LIMITER.wait()
        data = self._kis.call("GET", ep.url, tr_id=ep.tr_id, params=params)
        output = data.get("output") or []
        if isinstance(output, dict):
            output = [output]
        return list(output)

    def get_index_daily(
        self,
        sector_code: str,
        date_from: str,
        date_to: str,
        *,
        period: str = "D",
    ) -> list[dict[str, Any]]:
        """Daily OHLCV history for a sector / market index — single call.

        KIS endpoint returns at most 50 rows per call counting back from
        ``date_to``; for multi-year history use ``get_index_daily_history``
        which paginates by walking the window backwards.

        sector_code examples (FID_INPUT_ISCD):
          - 0001  KOSPI 종합
          - 1001  KOSDAQ 종합
          - 2001  KOSPI 200
          - 코스피 업종 indices: 4-digit codes per KRX (e.g. 0010 종합, 0011 대형주,
            0012 중형주, 0013 소형주, 0021 음식료품, 0022 섬유의복, ...)
        date_from/date_to: YYYYMMDD strings.
        period: 'D' daily, 'W' weekly, 'M' monthly, 'Y' annual.
        """
        ep = KIS_MARKET_ENDPOINTS["index_daily_price"]
        params = {
            "FID_COND_MRKT_DIV_CODE": "U",  # 'U' for index/sector
            "FID_INPUT_ISCD": str(sector_code).zfill(4),
            "FID_INPUT_DATE_1": date_from,
            "FID_INPUT_DATE_2": date_to,
            "FID_PERIOD_DIV_CODE": period,
        }
        _LIMITER.wait()
        data = self._kis.call("GET", ep.url, tr_id=ep.tr_id, params=params)
        # output1 = current/summary, output2 = list of daily rows
        output2 = data.get("output2") or []
        if isinstance(output2, dict):
            output2 = [output2]
        return list(output2)

    def get_index_daily_history(
        self,
        sector_code: str,
        date_from: str,
        date_to: str,
        *,
        period: str = "D",
        max_pages: int = 400,
    ) -> list[dict[str, Any]]:
        """Long-range daily index OHLCV by paginating ``get_index_daily``.

        KIS returns max 50 rows per call counting back from ``date_to``.
        We walk backwards: each iteration the new ``date_to`` is set to
        (earliest_date_in_response - 1 day) and we stop when the response
        is empty or earliest_date_in_response <= date_from. ``max_pages``
        is a safety cap (400 × 50 = 20,000 rows ≈ 80년 거래일치).
        """
        from datetime import datetime, timedelta

        seen_dates: set[str] = set()
        out: list[dict[str, Any]] = []
        cur_to = date_to
        for _ in range(max_pages):
            page = self.get_index_daily(
                sector_code=sector_code,
                date_from=date_from,
                date_to=cur_to,
                period=period,
            )
            if not page:
                break
            new_rows = []
            for row in page:
                d = str(row.get("stck_bsop_date") or row.get("bsop_date") or "")
                if not d or d in seen_dates:
                    continue
                seen_dates.add(d)
                new_rows.append((d, row))
            if not new_rows:
                break
            out.extend(r for _, r in new_rows)
            earliest = min(d for d, _ in new_rows)
            if earliest <= date_from:
                break
            try:
                prev = datetime.strptime(earliest, "%Y%m%d") - timedelta(days=1)
                cur_to = prev.strftime("%Y%m%d")
            except ValueError:
                break
        # Sort newest-first to match single-call ordering for downstream parity.
        out.sort(key=lambda r: str(r.get("stck_bsop_date") or ""), reverse=True)
        return out


__all__ = ["KISMarketAdapter", "KISMarketEndpoint", "KIS_MARKET_ENDPOINTS"]
