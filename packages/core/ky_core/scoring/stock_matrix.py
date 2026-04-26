"""Stock matrix — 4,516 KRX 종목 × 10 metric 매트릭스.

10 metric (정규화된 0..1):
  1. price_rs_6m       — 6개월 가격 RS percentile
  2. tt_passes         — Minervini Trend Template (0..8 → 0..1)
  3. vcp_stage         — VCP detection (0..3 → 0..1)
  4. dollar_volume     — 20일 평균 거래대금 percentile (시총 proxy)
  5. foreign_net       — 30일 외인 누적 매수 (z-score → 0..1)
  6. eps_yoy           — DART 분기 EPS YoY (0..1)
  7. roe               — DART 최근 ROE (0..1)
  8. per_inv           — PER 역수 (낮은 PER → 높은 점수)
  9. pbr_inv           — PBR 역수
 10. sector_macro      — leader_stock의 macro_score 그대로

종목별 composite stock_score = mean of 10 metrics.

기존 `scanning.leader_scan` 결과 + ky.db universe + macro_sector_strength 결합.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
DB_PATH = Path.home() / ".gazua" / "data" / "ky.db"


@dataclass
class StockMetrics:
    ticker: str
    name: str
    market: str
    sector: str
    wics_sector_num: str | None
    metrics: dict[str, float]
    composite_score: float
    rank: int = 0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _normalize_dict(d: dict[str, float]) -> dict[str, float]:
    """percentile rank 0..1 across all values."""
    if not d:
        return {}
    items = sorted(d.items(), key=lambda kv: kv[1])
    n = len(items)
    out = {}
    for i, (k, _v) in enumerate(items):
        out[k] = (i + 1) / n
    return out


def compute_stock_matrix(top_n: int = 100) -> list[StockMetrics]:
    """4,516 KRX 종목 × 10 metric → 정규화된 매트릭스.

    Args:
        top_n: 결과 size cap

    Returns:
        list[StockMetrics] sorted by composite_score desc
    """
    if not DB_PATH.exists():
        return []

    # 1. universe
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    universe = con.execute(
        "SELECT ticker, name, market, sector FROM universe "
        "WHERE is_etf = 0 AND sector IS NOT NULL AND sector <> ''"
    ).fetchall()
    con.close()

    # 2. 가격 leaders (RS / TT / VCP) from scanning.leader_scan
    price_data: dict[str, dict[str, float]] = {}
    try:
        from ky_core.scanning.leader_scan import scan_leaders
        leaders = scan_leaders(top_n=5000, min_leader_score=0.0)
        for L in leaders:
            price_data[L.symbol] = {
                "price_rs_6m": (L.rs_percentile or 50.0) / 100.0,
                "tt_passes": (L.tt_passes or 0) / 8.0,
                "vcp_stage": min((L.vcp_stage or 0) / 3.0, 1.0),
                "dollar_volume_proxy": min(L.vol_x or 0, 5.0) / 5.0,
                "eps_yoy": L.eps_signal or 0.5,
            }
    except Exception as e:
        logger.warning("leader_scan unavailable: %s", e)

    # 3. macro sector strength → ticker per sector
    macro_score_by_wics: dict[str, float] = {}
    try:
        from ky_core.scoring.macro_sector_strength import compute_macro_strength
        for s in compute_macro_strength(method="detrended"):
            # Normalize z to 0..1
            macro_score_by_wics[s.sector_num] = max(0.0, min(1.0, (s.score + 3) / 6))
    except Exception as e:
        logger.warning("macro_sector_strength unavailable: %s", e)

    # universe.sector → wics_num via leader_stock.UNIVERSE_TO_WICS
    from ky_core.scoring.leader_stock import UNIVERSE_TO_WICS

    # 4. 펀더 (DART) — financials_pit table
    fund_data: dict[str, dict[str, float]] = {}
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        # try schema introspect
        cols = con.execute("PRAGMA table_info(financials_pit)").fetchall()
        col_names = [c["name"] for c in cols]
        if "ticker" in col_names:
            for r in con.execute(
                "SELECT ticker, MAX(report_date) as latest, "
                "AVG(eps_yoy_pct) as eps_yoy, AVG(roe) as roe, "
                "AVG(per) as per, AVG(pbr) as pbr "
                "FROM financials_pit GROUP BY ticker"
            ).fetchall() if "eps_yoy_pct" in col_names else []:
                fund_data[r["ticker"]] = {
                    "eps_yoy": _to_unit(r["eps_yoy"], 0, 100),
                    "roe": _to_unit(r["roe"], 0, 30),
                    "per_inv": 1.0 - _to_unit(r["per"], 0, 50),
                    "pbr_inv": 1.0 - _to_unit(r["pbr"], 0, 5),
                }
    except Exception as e:
        logger.info("financials_pit not available: %s", e)
    finally:
        con.close()

    # 5. Build per-ticker
    rows: list[StockMetrics] = []
    for u in universe:
        ticker = u["ticker"]
        sector = (u["sector"] or "").strip()
        wics_num = UNIVERSE_TO_WICS.get(sector)

        pdata = price_data.get(ticker, {})
        fdata = fund_data.get(ticker, {})

        metrics = {
            "price_rs_6m":    pdata.get("price_rs_6m", 0.5),
            "tt_passes":      pdata.get("tt_passes", 0.0),
            "vcp_stage":      pdata.get("vcp_stage", 0.0),
            "dollar_volume":  pdata.get("dollar_volume_proxy", 0.5),
            "foreign_net":    0.5,    # placeholder (KIS investor_trend 별도)
            "eps_yoy":        fdata.get("eps_yoy", pdata.get("eps_yoy", 0.5)),
            "roe":            fdata.get("roe", 0.5),
            "per_inv":        fdata.get("per_inv", 0.5),
            "pbr_inv":        fdata.get("pbr_inv", 0.5),
            "sector_macro":   macro_score_by_wics.get(wics_num or "", 0.5),
        }

        composite = sum(metrics.values()) / len(metrics)

        # Reason snippet
        rs_pct = metrics["price_rs_6m"] * 100
        tt = int(metrics["tt_passes"] * 8)
        reason_parts = []
        if rs_pct >= 80:
            reason_parts.append(f"RS={rs_pct:.0f}")
        if tt >= 5:
            reason_parts.append(f"TT={tt}/8")
        if metrics["vcp_stage"] >= 1.0:
            reason_parts.append("VCP")
        if metrics["sector_macro"] >= 0.7:
            reason_parts.append("macro강")

        rows.append(StockMetrics(
            ticker=ticker, name=u["name"] or ticker, market=u["market"] or "",
            sector=sector, wics_sector_num=wics_num,
            metrics={k: round(v, 3) for k, v in metrics.items()},
            composite_score=round(composite, 3),
            reason=" · ".join(reason_parts) or "—",
        ))

    rows.sort(key=lambda r: r.composite_score, reverse=True)
    for i, r in enumerate(rows, 1):
        r.rank = i
    return rows[:top_n]


def _to_unit(v: float | None, lo: float, hi: float) -> float:
    """Linear scale [lo, hi] → [0, 1]; clipped."""
    if v is None:
        return 0.5
    if v <= lo:
        return 0.0
    if v >= hi:
        return 1.0
    return (v - lo) / (hi - lo)
