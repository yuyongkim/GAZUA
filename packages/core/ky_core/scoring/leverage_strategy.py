"""레버리지 ETF 전략 — KODEX 200 레버리지/인버스를 macro z 임계치로 회전.

규칙:
  - macro avg z >= +1.0  → KODEX 레버리지 (1.5x KOSPI 200)
  - macro avg z <= -1.0  → KODEX 인버스 (-1x)
  - 그 외 → KODEX 200 (1x, 안전)

ATR 기반 변동성 조정 옵션. 레버리지 ETF는 daily decay 큼 → 변동성 높을 때
rebalance 빈도 ↑ 또는 노출 ↓.
"""
from __future__ import annotations

import csv
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
DATA_ROOT = Path.home() / ".gazua" / "data" / "sectors"

# Trade-able tickers
KODEX_LEVERAGE = "122630"      # KODEX 레버리지 1.5x
KODEX_INVERSE  = "114800"      # KODEX 인버스 -1x
KODEX_200      = "069500"      # KODEX 200 1x

LEVERAGE_DECAY = 0.0008        # daily volatility decay assumption (~20%/y in choppy)


@dataclass
class LeverageBacktestResult:
    strategy: str
    final_nav: float
    cagr: float
    sharpe: float
    max_drawdown: float
    n_periods: int
    long_pct: float    # % of time in 레버리지
    inverse_pct: float
    flat_pct: float
    monthly_returns: list[dict[str, Any]] = field(default_factory=list)


def _read_close(folder: str, slug: str) -> list[tuple[str, float]]:
    p = DATA_ROOT / folder / f"{slug}.csv"
    if not p.exists():
        return []
    out = []
    with p.open(encoding="utf-8", errors="ignore") as f:
        for row in csv.DictReader(f):
            d = (row.get("date") or "").strip()
            v = row.get("close")
            if not d or v is None or v == "":
                continue
            try:
                out.append((d[:10], float(v)))
            except ValueError:
                pass
    out.sort()
    return out


def _to_monthly(prices: list[tuple[str, float]]) -> dict[str, float]:
    last_per_month: dict[str, float] = {}
    for d, v in prices:
        m = d[:7]
        last_per_month[m] = v  # last write wins
    months = sorted(last_per_month.keys())
    out = {}
    for i in range(1, len(months)):
        prev = last_per_month[months[i - 1]]
        cur = last_per_month[months[i]]
        if prev > 0:
            out[months[i]] = (cur - prev) / prev
    return out


def run_leverage_backtest(
    *,
    z_threshold: float = 1.0,
    period_start: str = "2015-01",
    macro_method: str = "detrended",
) -> LeverageBacktestResult:
    """매월 strength 평균 z 부호로 레버리지/인버스/안전 회전.

    Single snapshot strength (현재 기준) — 향후 walk-forward로 확장.
    """
    from ky_core.scoring.macro_sector_strength import compute_macro_strength

    strengths = compute_macro_strength(method=macro_method)
    avg_z = sum(s.score for s in strengths) / max(1, len(strengths))

    # KODEX 200 자체 가격 (1x baseline) — yf_commodities에 KODEX 200 ETF 069500 없을 수 있음.
    # 대용: KODEX 200 비슷한 노출인 retail__KODEX_유통 등은 안 맞음.
    # 1차: KIS index KOSPI 종합 사용 (1x proxy)
    base_prices = _read_close("kis_index", "market__KOSPI_종합") or []
    if not base_prices:
        # Fallback: yf_commodities WTI (just to demonstrate framework)
        base_prices = _read_close("yf_commodities", "oil__WTI_원유_(CL=F)")
    base_monthly = _to_monthly(base_prices)
    months = sorted(m for m in base_monthly if m >= period_start)

    if not months:
        return LeverageBacktestResult(
            strategy=f"leverage_z{z_threshold}", final_nav=1.0, cagr=0.0,
            sharpe=0.0, max_drawdown=0.0, n_periods=0,
            long_pct=0, inverse_pct=0, flat_pct=0,
        )

    # Determine exposure per month based on AVG z (현재 snapshot 기준 — 향후 history 기반)
    # 간단화: 모든 기간에 대해 동일한 avg_z 사용 (single-snapshot)
    if avg_z >= z_threshold:
        exposure = 1.5  # 레버리지
        regime = "leverage"
    elif avg_z <= -z_threshold:
        exposure = -1.0  # 인버스
        regime = "inverse"
    else:
        exposure = 1.0   # 안전
        regime = "flat"

    nav = 1.0
    nav_curve = [1.0]
    monthly_returns = []
    for m in months:
        base_r = base_monthly[m]
        port_r = exposure * base_r - LEVERAGE_DECAY * 21 * (abs(exposure) - 1) if abs(exposure) > 1 else exposure * base_r
        nav *= (1 + port_r)
        nav_curve.append(nav)
        monthly_returns.append({"month": m, "return": round(port_r, 4),
                              "nav": round(nav, 4), "regime": regime})

    n = len(monthly_returns)
    if n < 2:
        cagr = 0.0; sharpe = 0.0; mdd = 0.0
    else:
        years = n / 12
        cagr = nav ** (1 / max(years, 0.1)) - 1
        rs = [m["return"] for m in monthly_returns]
        mean_r = sum(rs) / len(rs)
        var_r = sum((r - mean_r) ** 2 for r in rs) / max(1, len(rs) - 1)
        std_r = var_r ** 0.5
        sharpe = (mean_r * 12) / (std_r * (12 ** 0.5)) if std_r > 0 else 0.0
        peak = nav_curve[0]; mdd = 0
        for v in nav_curve:
            peak = max(peak, v)
            mdd = min(mdd, (v - peak) / peak if peak > 0 else 0)

    return LeverageBacktestResult(
        strategy=f"leverage_z{z_threshold}_{regime}",
        final_nav=round(nav, 4),
        cagr=round(cagr, 4), sharpe=round(sharpe, 3),
        max_drawdown=round(mdd, 4),
        n_periods=n,
        long_pct=100.0 if regime == "leverage" else 0,
        inverse_pct=100.0 if regime == "inverse" else 0,
        flat_pct=100.0 if regime == "flat" else 0,
        monthly_returns=monthly_returns[-24:],
    )
