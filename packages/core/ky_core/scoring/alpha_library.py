"""Alpha library — 사용자 정의 전략 DSL.

JSON DSL:
{
  "name": "내 전략 1",
  "weights": {
    "macro_sector_z": 0.40,
    "price_rs": 0.30,
    "tt_passes": 0.15,
    "vcp_stage": 0.10,
    "eps_yoy": 0.05
  },
  "filters": {
    "min_macro_z": 1.0,
    "min_dollar_volume_pct": 0.5,
    "exclude_sectors": []
  }
}

→ 매트릭스 + 종목 metric 들을 weighted sum 으로 결합 → ranking.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class StrategyDSL:
    name: str
    weights: dict[str, float]   # metric_name → weight (0..1)
    filters: dict[str, Any] = field(default_factory=dict)
    description: str = ""


@dataclass
class StrategyResult:
    strategy_name: str
    rank: int
    ticker: str
    name: str
    sector: str
    composite_score: float
    contributions: dict[str, float]  # metric → weight × value


SUPPORTED_METRICS = {
    "macro_sector_z",     # 섹터 macro z (정규화 0..1)
    "price_rs",           # 가격 RS percentile
    "tt_passes",          # TT 통과 0..8 → 0..1
    "vcp_stage",          # VCP 단계 0..3 → 0..1
    "eps_yoy",            # EPS YoY 0..1
    "dollar_volume",      # 거래대금 percentile
    "foreign_net",        # 외인 순매수
    "roe", "per_inv", "pbr_inv",
}


def validate_strategy(s: StrategyDSL) -> list[str]:
    errs = []
    if not s.name:
        errs.append("name required")
    if not s.weights:
        errs.append("weights required")
    for k in s.weights:
        if k not in SUPPORTED_METRICS:
            errs.append(f"unknown metric: {k}")
    total = sum(s.weights.values())
    if abs(total - 1.0) > 0.01:
        errs.append(f"weights sum = {total:.3f} (should be 1.0)")
    return errs


def run_strategy(dsl: StrategyDSL, top_n: int = 30) -> list[StrategyResult]:
    """DSL → stock_matrix metrics에 weighted sum 적용 → ranking."""
    errs = validate_strategy(dsl)
    if errs:
        raise ValueError("invalid strategy: " + "; ".join(errs))

    from ky_core.scoring.stock_matrix import compute_stock_matrix
    rows = compute_stock_matrix(top_n=2000)

    filt = dsl.filters or {}
    min_macro = filt.get("min_macro_z", 0.0)
    excl = set(filt.get("exclude_sectors") or [])
    min_dv = filt.get("min_dollar_volume_pct", 0.0)

    out: list[StrategyResult] = []
    for r in rows:
        m = r.metrics
        # filters
        if m.get("sector_macro", 0.5) < min_macro:
            continue
        if r.wics_sector_num in excl:
            continue
        if m.get("dollar_volume", 0.5) < min_dv:
            continue
        # composite
        contrib = {}
        score = 0.0
        for metric, weight in dsl.weights.items():
            if metric == "macro_sector_z":
                v = m.get("sector_macro", 0.5)
            else:
                v = m.get(metric, 0.5)
            contrib[metric] = round(weight * v, 3)
            score += weight * v
        out.append(StrategyResult(
            strategy_name=dsl.name, rank=0,
            ticker=r.ticker, name=r.name,
            sector=r.sector,
            composite_score=round(score, 4),
            contributions=contrib,
        ))

    out.sort(key=lambda r: r.composite_score, reverse=True)
    for i, r in enumerate(out[:top_n], 1):
        r.rank = i
    return out[:top_n]


# --- Pre-built example strategies -------------------------------------------

PRESETS = [
    StrategyDSL(
        name="고배당+가치 (저PER저PBR)",
        weights={
            "per_inv": 0.30, "pbr_inv": 0.30,
            "roe": 0.20, "macro_sector_z": 0.20,
        },
        filters={"min_dollar_volume_pct": 0.3},
        description="가치주 스타일 — 낮은 PER/PBR + ROE + 섹터 매크로",
    ),
    StrategyDSL(
        name="모멘텀 강세주",
        weights={
            "price_rs": 0.40, "tt_passes": 0.30,
            "macro_sector_z": 0.20, "vcp_stage": 0.10,
        },
        filters={"min_macro_z": 0.5},
        description="가격 RS + Trend Template + VCP + 섹터 매크로",
    ),
    StrategyDSL(
        name="펀더 + 매크로 결합",
        weights={
            "eps_yoy": 0.30, "roe": 0.20,
            "macro_sector_z": 0.30, "price_rs": 0.20,
        },
        filters={},
        description="실적 + 매크로 + 가격 종합",
    ),
]
