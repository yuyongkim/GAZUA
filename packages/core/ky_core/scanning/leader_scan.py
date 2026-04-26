"""LeaderScore = 0.45·TT + 0.20·RS + 0.20·VCP + 0.10·EPS + 0.05·SectorStrength.

Outputs a ranked list of "leader" stocks, with enough structured fields
to drive the ``Leader`` table on the dashboard.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date as _date
from typing import Any, Iterable

from ky_core.scanning.loader import Panel, load_panel
from ky_core.scanning.sepa import TrendTemplate, evaluate
from ky_core.scanning.sector_strength import SectorStrength, sector_strength
from ky_core.scanning.vcp import VCPStatus, detect_vcp
from ky_core.storage.db import get_engine
from sqlalchemy import text


@dataclass
class Leader:
    symbol: str
    name: str
    market: str
    sector: str
    close: float
    leader_score: float      # 0..1 composite
    tt_passes: int           # 0..8
    trend_template: str      # 'X/8' label
    rs: float                # 6m return
    rs_percentile: float     # 0..100
    d1: float                # pct
    d5: float                # pct
    m1: float                # pct
    vol_x: float             # today vol / 50d avg
    vcp_stage: int
    pattern: str             # VCP / Base / B.out / —
    eps_signal: float        # 0..1 EPS YoY score
    sector_strength: float   # 0..1 composite from SectorStrength
    reason: str              # short explanation

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def scan_leaders(
    as_of: _date | str | None = None,
    *,
    universe_filter: Iterable[str] | None = None,
    min_leader_score: float = 0.0,
    top_n: int = 200,
    panel: Panel | None = None,
) -> list[Leader]:
    """Score + rank symbols by LeaderScore.

    Returns up to ``top_n`` leaders whose score >= ``min_leader_score``.
    """
    panel = panel or load_panel(as_of)
    sectors = sector_strength(panel=panel)
    sector_by_name = {s.name: s.score for s in sectors}

    eps_by_symbol = _load_eps_signals(panel.as_of)

    symbols = list(panel.series.keys())
    if universe_filter is not None:
        filter_set = set(universe_filter)
        symbols = [s for s in symbols if s in filter_set]

    results: list[Leader] = []
    for sym in symbols:
        tt = evaluate(sym, panel=panel)
        if tt is None:
            continue
        vcp = detect_vcp(sym, panel=panel)
        meta = panel.universe.get(sym, {})

        rows = panel.series[sym]
        closes = [r["close"] for r in rows]
        vols = [r.get("volume") or 0 for r in rows]
        d1 = _pct_return(closes, 1) * 100
        d5 = _pct_return(closes, 5) * 100
        m1 = _pct_return(closes, 21) * 100

        avg50 = sum(vols[-50:]) / max(1, min(50, len(vols)))
        vol_x = (vols[-1] / avg50) if avg50 > 0 else 0.0

        sec_score = sector_by_name.get(meta.get("sector", ""), 0.5)
        eps_signal = eps_by_symbol.get(sym, 0.5)

        ls = (
            0.45 * (tt.passes / 8.0)
            + 0.20 * min(1.0, max(0.0, tt.rs_value + 0.5))  # shift to 0..1
            + 0.20 * vcp.score
            + 0.10 * eps_signal
            + 0.05 * sec_score
        )
        if ls < min_leader_score:
            continue

        results.append(
            Leader(
                symbol=sym,
                name=meta.get("name") or sym,
                market=meta.get("market") or "UNKNOWN",
                sector=meta.get("sector") or "기타",
                close=closes[-1],
                leader_score=ls,
                tt_passes=tt.passes,
                trend_template=f"{tt.passes}/8",
                rs=tt.rs_value,
                rs_percentile=_rs_percentile_from_tt(tt),
                d1=d1,
                d5=d5,
                m1=m1,
                vol_x=vol_x,
                vcp_stage=vcp.stage,
                pattern=vcp.label,
                eps_signal=eps_signal,
                sector_strength=sec_score,
                reason=_reason(tt, vcp, sec_score, eps_signal),
            )
        )
    results.sort(key=lambda l: l.leader_score, reverse=True)
    return results[:top_n]


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _pct_return(closes: list[float], back: int) -> float:
    if len(closes) <= back or closes[-back - 1] <= 0:
        return 0.0
    return closes[-1] / closes[-back - 1] - 1.0


def _rs_percentile_from_tt(tt: TrendTemplate) -> float:
    # Derived from the same distribution used inside sepa.evaluate;
    # we recompute cheaply by re-using the raw RS value bucket.
    if tt.rs_value <= -0.5:
        return 0.0
    if tt.rs_value >= 1.0:
        return 99.0
    return round(50.0 + tt.rs_value * 50.0, 1)


def _reason(tt: TrendTemplate, vcp: VCPStatus, sec: float, eps: float) -> str:
    bits: list[str] = [f"TT {tt.passes}/8"]
    if vcp.stage >= 3:
        bits.append("VCP 3단계")
    elif vcp.stage >= 2:
        bits.append("VCP 수축")
    if tt.price_close_to_52w_high:
        bits.append("52W 근처")
    if sec >= 0.65:
        bits.append("강한 섹터")
    if eps >= 0.7:
        bits.append("EPS+")
    return " · ".join(bits)


# Memoise the EPS-signal table keyed by as_of. The SQL is a full-table scan
# over financials_pit; the /today build calls it from scan_leaders AND again
# from the O'Neil wizard — 2 queries → 1.
_eps_cache: dict[str, dict[str, float]] = {}


def _load_eps_signals(as_of: str) -> dict[str, float]:
    """EPS YoY proxy from financials_pit.net_income.

    Returns a sigmoid-normalized 0..1 score per symbol, where 1.0 means
    "latest annual net income > 2 × prior year". Missing → 0.5.
    """
    cached = _eps_cache.get(as_of)
    if cached is not None:
        return cached

    engine = get_engine()
    out: dict[str, float] = {}
    stmt = text(
        """
        SELECT symbol, period_end, period_type, net_income
        FROM financials_pit
        WHERE period_type IN ('FY', 'Q4')
          AND period_end <= :as_of
        ORDER BY symbol ASC, period_end DESC
        """
    )
    with engine.connect() as conn:
        by_sym: dict[str, list[tuple[str, float]]] = {}
        for r in conn.execute(stmt, {"as_of": as_of}):
            if r.net_income is None:
                continue
            by_sym.setdefault(r.symbol, []).append((r.period_end, float(r.net_income)))
    for sym, rows in by_sym.items():
        rows = sorted(rows, key=lambda x: x[0], reverse=True)
        if len(rows) < 2 or rows[1][1] == 0:
            continue
        growth = (rows[0][1] - rows[1][1]) / abs(rows[1][1])
        # map growth to 0..1: -100% → 0, 0% → 0.5, +100% → 0.75, +200% → ~0.9
        score = max(0.0, min(1.0, 0.5 + growth * 0.25))
        out[sym] = score
    _eps_cache[as_of] = out
    return out


def clear_eps_cache() -> None:
    _eps_cache.clear()
