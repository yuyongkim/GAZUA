"""KIS 국내주식 재무 API — activated 2026-04-22.

Mirrors ``_tmp_kis_repo/examples_llm/domestic_stock/finance_*/`` — eight
endpoints that together cover balance sheet, income statement, and six ratio
families (growth / profit / stability / financial / other-major / ratio
leaderboard).

The shape is deliberately compatible with ``FnguideSnapshot`` so the output
can be merged 1:1 with FnGuide rows (``revenue`` / ``operating_income`` /
``net_income`` / ``eps`` / ``roe`` / ``debt_ratio`` ...).

KIS finance endpoint table (domestic stock, v1):

  ========================================  ========  ==========================================
  endpoint                                  tr_id     path
  ========================================  ========  ==========================================
  balance-sheet (재무상태표)                 FHKST66430100  /uapi/domestic-stock/v1/finance/balance-sheet
  income-statement (손익계산서)              FHKST66430200  /uapi/domestic-stock/v1/finance/income-statement
  financial-ratio (재무비율)                 FHKST66430300  /uapi/domestic-stock/v1/finance/financial-ratio
  profit-ratio (수익성비율)                  FHKST66430400  /uapi/domestic-stock/v1/finance/profit-ratio
  other-major-ratios (기타주요비율)          FHKST66430500  /uapi/domestic-stock/v1/finance/other-major-ratios
  stability-ratio (안정성비율)               FHKST66430600  /uapi/domestic-stock/v1/finance/stability-ratio
  growth-ratio (성장성비율)                  FHKST66430800  /uapi/domestic-stock/v1/finance/growth-ratio
  ranking/finance-ratio (재무비율 순위)      FHPST01750000  /uapi/domestic-stock/v1/ranking/finance-ratio
  ========================================  ========  ==========================================

Common parameters for all ``/finance/*`` endpoints:

  FID_DIV_CLS_CODE           "0" = 연간, "1" = 분기
  fid_cond_mrkt_div_code     "J" (주식)
  fid_input_iscd             6-digit ticker (e.g. "005930")
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Literal, Optional

from ky_adapters.base import AdapterError, AuthError, BaseAdapter, HealthStatus
from ky_adapters.kis.client import KISAdapter

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Endpoint catalogue                                                          #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class KISFinanceEndpoint:
    """Shape of a single KIS finance endpoint."""

    name: str
    url: str
    tr_id: str
    description: str
    supports_period: bool = True  # all finance/* endpoints take FID_DIV_CLS_CODE


KIS_ENDPOINTS: dict[str, KISFinanceEndpoint] = {
    "balance_sheet": KISFinanceEndpoint(
        name="balance_sheet",
        url="/uapi/domestic-stock/v1/finance/balance-sheet",
        tr_id="FHKST66430100",
        description="국내주식 대차대조표 (v1_국내주식-078)",
    ),
    "income_statement": KISFinanceEndpoint(
        name="income_statement",
        url="/uapi/domestic-stock/v1/finance/income-statement",
        tr_id="FHKST66430200",
        description="국내주식 손익계산서 (v1_국내주식-079)",
    ),
    "financial_ratio": KISFinanceEndpoint(
        name="financial_ratio",
        url="/uapi/domestic-stock/v1/finance/financial-ratio",
        tr_id="FHKST66430300",
        description="국내주식 재무비율 (v1_국내주식-080)",
    ),
    "profit_ratio": KISFinanceEndpoint(
        name="profit_ratio",
        url="/uapi/domestic-stock/v1/finance/profit-ratio",
        tr_id="FHKST66430400",
        description="국내주식 수익성비율 (v1_국내주식-081)",
    ),
    "other_major_ratios": KISFinanceEndpoint(
        name="other_major_ratios",
        url="/uapi/domestic-stock/v1/finance/other-major-ratios",
        tr_id="FHKST66430500",
        description="국내주식 기타주요비율 (v1_국내주식-082)",
    ),
    "stability_ratio": KISFinanceEndpoint(
        name="stability_ratio",
        url="/uapi/domestic-stock/v1/finance/stability-ratio",
        tr_id="FHKST66430600",
        description="국내주식 안정성비율 (v1_국내주식-083)",
    ),
    "growth_ratio": KISFinanceEndpoint(
        name="growth_ratio",
        url="/uapi/domestic-stock/v1/finance/growth-ratio",
        tr_id="FHKST66430800",
        description="국내주식 성장성비율 (v1_국내주식-084)",
    ),
    "ranking_finance_ratio": KISFinanceEndpoint(
        name="ranking_finance_ratio",
        url="/uapi/domestic-stock/v1/ranking/finance-ratio",
        tr_id="FHPST01750000",
        description="국내주식 재무비율 순위",
        supports_period=False,
    ),
}


PeriodLiteral = Literal["annual", "quarterly"]


def _period_code(period: PeriodLiteral) -> str:
    if period == "annual":
        return "0"
    if period == "quarterly":
        return "1"
    raise ValueError(f"period must be 'annual' or 'quarterly', got {period!r}")


# --------------------------------------------------------------------------- #
# Output schema mapping                                                       #
# --------------------------------------------------------------------------- #

KIS_OUTPUT_MAP: dict[str, dict[str, str]] = {
    "income_statement": {
        "stac_yymm": "period",
        "sale_account": "revenue",
        "sale_cost": "cost_of_sales",
        "sale_totl_prfi": "gross_profit",
        "bsop_prti": "operating_income",
        "bsop_non_ernn": "non_op_income",
        "bsop_non_expn": "non_op_expense",
        "thtr_ntin": "net_income",
    },
    "balance_sheet": {
        "stac_yymm": "period",
        "cras": "current_assets",
        "fxas": "non_current_assets",
        "total_aset": "total_assets",
        "flow_lblt": "current_liabilities",
        "fix_lblt": "non_current_liabilities",
        "total_lblt": "total_liabilities",
        "cpfn": "capital",
        "cfp_surp": "capital_surplus",
        "prfi_surp": "retained_earnings",
        "total_cptl": "total_equity",
    },
    "financial_ratio": {
        "stac_yymm": "period",
        "grs": "sales_growth",
        "bsop_prfi_inrt": "op_income_growth",
        "ntin_inrt": "net_income_growth",
        "roe_val": "roe",
        "eps": "eps",
        "sps": "sps",
        "bps": "bps",
        "rsrv_rate": "retention_ratio",
        "lblt_rate": "debt_ratio",
    },
    "profit_ratio": {
        "stac_yymm": "period",
        "cptl_ntin_rate": "roa",
        "self_cptl_ntin_inrt": "roe",
        "sale_ntin_rate": "net_margin",
        "sale_totl_rate": "gross_margin",
    },
    "stability_ratio": {
        "stac_yymm": "period",
        "lblt_rate": "debt_ratio",
        "bram_depn": "borrowings_ratio",
        "crnt_rate": "current_ratio",
        "quck_rate": "quick_ratio",
    },
    "growth_ratio": {
        "stac_yymm": "period",
        "grs": "sales_growth",
        "bsop_prfi_inrt": "op_income_growth",
        "equt_inrt": "equity_growth",
        "ntin_inrt": "net_income_growth",
    },
    "other_major_ratios": {
        "stac_yymm": "period",
        "payout_rate": "payout_ratio",
        "eva": "eva",
        "ebitda": "ebitda",
        "ev_ebitda": "ev_ebitda",
    },
}


def _normalise_output(endpoint: str, raw: dict[str, Any]) -> dict[str, Any]:
    mapping = KIS_OUTPUT_MAP.get(endpoint, {})
    out: dict[str, Any] = {}
    for kis_field, value in raw.items():
        out[mapping.get(kis_field, kis_field)] = value
    # Keep raw fields too so callers can audit
    out["_raw"] = dict(raw)
    return out


# --------------------------------------------------------------------------- #
# Rate limiter                                                                #
# --------------------------------------------------------------------------- #
# KIS real-env limit is 20 req/s per appkey; we stay conservative at ~2 req/s
# when doing the 9-endpoint smoke walk so we don't trip the shared limiter.


class _RateLimiter:
    def __init__(self, min_interval: float = 0.5) -> None:
        self._lock = threading.Lock()
        self._last = 0.0
        self._min_interval = min_interval

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delta = now - self._last
            if delta < self._min_interval:
                time.sleep(self._min_interval - delta)
            self._last = time.monotonic()


_LIMITER = _RateLimiter(min_interval=0.5)


# --------------------------------------------------------------------------- #
# Adapter                                                                     #
# --------------------------------------------------------------------------- #


class KISFinanceAdapter(BaseAdapter):
    """Live KIS finance adapter — 9 endpoints over the shared OAuth client."""

    source_id = "kis_finance"
    priority = 2  # FnGuide is P1 for fundamentals; KIS is cross-check.

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
    def from_settings(cls) -> "KISFinanceAdapter":
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
        if not self._kis.app_key or not self._kis.app_secret:
            return HealthStatus(
                ok=False,
                source_id=self.source_id,
                last_error="KIS_APP_KEY/KIS_APP_SECRET not configured",
                extra={"endpoints": list(KIS_ENDPOINTS.keys())},
            ).to_dict()
        # Delegate to base KIS healthcheck (token issuance)
        base = self._kis.healthcheck()
        base["source_id"] = self.source_id
        base.setdefault("endpoints", list(KIS_ENDPOINTS.keys()))
        return base

    # --------- Request plumbing ---------

    def _call_endpoint(
        self,
        endpoint: str,
        symbol: str,
        *,
        period: PeriodLiteral = "annual",
        market: str = "J",
    ) -> list[dict[str, Any]]:
        ep = KIS_ENDPOINTS.get(endpoint)
        if ep is None:
            raise AdapterError(f"unknown KIS finance endpoint: {endpoint!r}")

        params: dict[str, Any] = {
            "fid_cond_mrkt_div_code": market,
        }
        if symbol:
            params["fid_input_iscd"] = str(symbol).zfill(6)
        if ep.supports_period:
            params["FID_DIV_CLS_CODE"] = _period_code(period)

        _LIMITER.wait()
        data = self._kis.call("GET", ep.url, tr_id=ep.tr_id, params=params)

        output = data.get("output")
        if output is None:
            return []
        # KIS returns object for single rows, array for multi-period
        if isinstance(output, dict):
            return [output]
        if isinstance(output, list):
            return list(output)
        raise AdapterError(f"unexpected KIS output shape for {endpoint}: {type(output).__name__}")

    # --------- Public API surface ---------

    def get_balance_sheet(
        self, symbol: str, *, period: PeriodLiteral = "annual"
    ) -> list[dict[str, Any]]:
        rows = self._call_endpoint("balance_sheet", symbol, period=period)
        return [_normalise_output("balance_sheet", r) for r in rows]

    def get_income_statement(
        self, symbol: str, *, period: PeriodLiteral = "annual"
    ) -> list[dict[str, Any]]:
        rows = self._call_endpoint("income_statement", symbol, period=period)
        return [_normalise_output("income_statement", r) for r in rows]

    def get_financial_ratio(
        self, symbol: str, *, period: PeriodLiteral = "annual"
    ) -> list[dict[str, Any]]:
        rows = self._call_endpoint("financial_ratio", symbol, period=period)
        return [_normalise_output("financial_ratio", r) for r in rows]

    def get_profit_ratio(
        self, symbol: str, *, period: PeriodLiteral = "annual"
    ) -> list[dict[str, Any]]:
        rows = self._call_endpoint("profit_ratio", symbol, period=period)
        return [_normalise_output("profit_ratio", r) for r in rows]

    def get_stability_ratio(
        self, symbol: str, *, period: PeriodLiteral = "annual"
    ) -> list[dict[str, Any]]:
        rows = self._call_endpoint("stability_ratio", symbol, period=period)
        return [_normalise_output("stability_ratio", r) for r in rows]

    def get_growth_ratio(
        self, symbol: str, *, period: PeriodLiteral = "annual"
    ) -> list[dict[str, Any]]:
        rows = self._call_endpoint("growth_ratio", symbol, period=period)
        return [_normalise_output("growth_ratio", r) for r in rows]

    def get_other_major_ratios(
        self, symbol: str, *, period: PeriodLiteral = "annual"
    ) -> list[dict[str, Any]]:
        rows = self._call_endpoint("other_major_ratios", symbol, period=period)
        return [_normalise_output("other_major_ratios", r) for r in rows]

    def get_ranking_finance_ratio(self) -> list[dict[str, Any]]:
        # Ranking endpoint has a different param shape — but same plumbing.
        rows = self._call_endpoint("ranking_finance_ratio", "", period="annual")
        return rows  # no normalisation yet

    # --------- Cross-check helper ---------

    def crosscheck_fnguide(
        self, symbol: str, *, period: PeriodLiteral = "annual"
    ) -> dict[str, Any]:
        rows_is = self.get_income_statement(symbol, period=period)
        rows_bs = self.get_balance_sheet(symbol, period=period)
        rows_fr = self.get_financial_ratio(symbol, period=period)
        merged: dict[str, Any] = {}
        for bucket in (rows_is, rows_bs, rows_fr):
            if bucket:
                merged.update(bucket[0])
        return merged


__all__ = [
    "KISFinanceAdapter",
    "KISFinanceEndpoint",
    "KIS_ENDPOINTS",
    "KIS_OUTPUT_MAP",
]
