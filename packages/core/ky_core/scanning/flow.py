"""Flow scanner — 수급 대시보드 (외국인 · 기관 · 개인).

Source of truth: ``fnguide_snapshots.payload.investor_trend`` — the 10 most
recent trading days of foreign/institution/individual net-buy and foreign
holding ratio per symbol (the same block that powers the stock-detail modal).

Approach (no pandas):
    - Read every snapshot row in one SQL pass.
    - For each symbol, pre-compute N-day cumulative nets (1/5/20).
    - Rank Top-15 foreign and Top-10 institution by 5-day cumulative net.
    - Aggregate per-sector foreign flow for a small heatmap.

Fallback: when a snapshot is missing ``investor_trend`` (or has <1 row) we
simply skip the symbol — which mirrors how the stock detail modal behaves
today and keeps the dashboard defensive against partial DB coverage.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from datetime import date as _date
from typing import Any

from sqlalchemy import text

from ky_core.scanning.loader import Panel, load_panel
from ky_core.storage.db import get_engine, init_db

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Data shapes                                                                 #
# --------------------------------------------------------------------------- #


@dataclass
class FlowRow:
    rank: int
    symbol: str
    name: str
    sector: str
    market: str
    d1: float        # 당일 외/기 net (원 단위)
    d5: float        # 5일 누적
    d20: float       # 20일 누적 (10일 payload cap 시 실제 최대치)
    streak: int      # 최근 연속 순매수/매도 일수 (부호가 동일한 최장 스트릭)
    price_pct: float # 최근 5일 종가 변동률 (%)
    close: float


@dataclass
class SectorFlow:
    name: str
    members: int
    d1: float
    d5: float
    d20: float


@dataclass
class FlowBundle:
    as_of: str
    universe_size: int      # number of snapshots read
    covered: int            # snapshots that actually had investor_trend data
    foreign_top: list[FlowRow]
    institution_top: list[FlowRow]
    individual_top: list[FlowRow]
    sector_foreign: list[SectorFlow]


# --------------------------------------------------------------------------- #
# Public API                                                                  #
# --------------------------------------------------------------------------- #


def scan_flow(
    as_of: _date | str | None = None,
    *,
    days: int = 5,
    panel: Panel | None = None,
    owner_id: str = "self",
    top_foreign: int = 15,
    top_institution: int = 10,
) -> FlowBundle:
    """Scan all fnguide snapshots and surface the top flow rows."""
    panel = panel or load_panel(as_of, owner_id=owner_id)
    universe = panel.universe  # symbol -> meta

    init_db()
    engine = get_engine()

    rows_fo: list[FlowRow] = []
    rows_in: list[FlowRow] = []
    rows_ind: list[FlowRow] = []
    sector_agg: dict[str, dict[str, float]] = {}
    covered = 0

    with engine.connect() as conn:
        rs = conn.execute(
            text(
                """
                SELECT symbol, payload
                FROM fnguide_snapshots
                WHERE owner_id = :owner_id
                """
            ),
            {"owner_id": owner_id},
        )
        for sym, payload_str in rs:
            meta = universe.get(sym)
            if not meta:
                continue  # non-universe symbol
            try:
                payload = json.loads(payload_str)
            except (ValueError, TypeError):
                continue
            trend = payload.get("investor_trend") or []
            if not trend:
                continue
            covered += 1

            # ``investor_trend`` is already sorted most-recent-first.
            flow = _extract_flow_rows(sym, meta, trend, days=days)
            if flow is None:
                continue

            rows_fo.append(
                _to_flow_row(flow, leg="foreign")
            )
            rows_in.append(
                _to_flow_row(flow, leg="institution")
            )
            rows_ind.append(
                _to_flow_row(flow, leg="individual")
            )

            # Sector aggregation (foreign leg)
            sec = meta.get("sector") or "기타"
            agg = sector_agg.setdefault(sec, {"d1": 0.0, "d5": 0.0, "d20": 0.0, "n": 0})
            agg["d1"] += flow["foreign"]["d1"]
            agg["d5"] += flow["foreign"]["d5"]
            agg["d20"] += flow["foreign"]["d20"]
            agg["n"] += 1

    # Sort + rank
    rows_fo.sort(key=lambda r: r.d5, reverse=True)
    rows_in.sort(key=lambda r: r.d5, reverse=True)
    rows_ind.sort(key=lambda r: r.d5, reverse=True)

    foreign_top = _rank(rows_fo[:top_foreign])
    institution_top = _rank(rows_in[:top_institution])
    individual_top = _rank(rows_ind[:top_institution])

    scale = 1e8  # 원 → 억원 for sector aggregation too
    sector_rows = [
        SectorFlow(
            name=sec,
            members=int(v["n"]),
            d1=round(v["d1"] / scale, 2),
            d5=round(v["d5"] / scale, 2),
            d20=round(v["d20"] / scale, 2),
        )
        for sec, v in sector_agg.items()
        if v["n"] >= 1
    ]
    sector_rows.sort(key=lambda s: abs(s.d5), reverse=True)

    return FlowBundle(
        as_of=panel.as_of,
        universe_size=len(universe),
        covered=covered,
        foreign_top=foreign_top,
        institution_top=institution_top,
        individual_top=individual_top,
        sector_foreign=sector_rows[:20],
    )


def to_dict(b: FlowBundle) -> dict[str, Any]:
    return {
        "as_of": b.as_of,
        "universe_size": b.universe_size,
        "covered": b.covered,
        "foreign_top": [asdict(r) for r in b.foreign_top],
        "institution_top": [asdict(r) for r in b.institution_top],
        "individual_top": [asdict(r) for r in b.individual_top],
        "sector_foreign": [asdict(s) for s in b.sector_foreign],
    }


# --------------------------------------------------------------------------- #
# Internals                                                                   #
# --------------------------------------------------------------------------- #


def _rank(rows: list[FlowRow]) -> list[FlowRow]:
    for i, r in enumerate(rows, start=1):
        r.rank = i
    return rows


def _safe_float(v: Any) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _extract_flow_rows(
    symbol: str,
    meta: dict[str, Any],
    trend: list[dict[str, Any]],
    *,
    days: int,
) -> dict[str, Any] | None:
    """Reduce raw investor_trend list to per-leg rollups."""
    if not trend:
        return None

    # investor_trend is already ordered newest -> oldest.
    today = trend[0]
    close_today = _safe_float(today.get("close"))
    close_d5 = _safe_float(trend[min(len(trend) - 1, 4)].get("close")) or close_today
    price_pct = 0.0
    if close_d5 > 0:
        price_pct = (close_today / close_d5 - 1.0) * 100.0

    def _cum_value(field: str, window: int) -> float:
        """Convert per-day (shares × close) to net traded value, 원 단위."""
        total = 0.0
        for r in trend[:window]:
            shares = _safe_float(r.get(field))
            px = _safe_float(r.get("close")) or close_today
            total += shares * px
        return total

    foreign = {
        "d1": _cum_value("foreign_net", 1),
        "d5": _cum_value("foreign_net", max(1, days)),
        "d20": _cum_value("foreign_net", 20),
    }
    institution = {
        "d1": _cum_value("institution_net", 1),
        "d5": _cum_value("institution_net", max(1, days)),
        "d20": _cum_value("institution_net", 20),
    }
    individual = {
        "d1": _cum_value("individual_net", 1),
        "d5": _cum_value("individual_net", max(1, days)),
        "d20": _cum_value("individual_net", 20),
    }

    return {
        "symbol": symbol,
        "name": meta.get("name") or symbol,
        "sector": meta.get("sector") or "기타",
        "market": meta.get("market") or "",
        "close": close_today,
        "price_pct": price_pct,
        "foreign": foreign,
        "institution": institution,
        "individual": individual,
        "foreign_streak": _streak([_safe_float(r.get("foreign_net")) for r in trend]),
        "institution_streak": _streak(
            [_safe_float(r.get("institution_net")) for r in trend]
        ),
        "individual_streak": _streak(
            [_safe_float(r.get("individual_net")) for r in trend]
        ),
    }


def _to_flow_row(flow: dict[str, Any], *, leg: str) -> FlowRow:
    block = flow[leg]
    streak = flow[f"{leg}_streak"]
    # Convert net 원 → 억원 for display.
    scale = 1e8
    return FlowRow(
        rank=0,
        symbol=flow["symbol"],
        name=flow["name"],
        sector=flow["sector"],
        market=flow["market"],
        d1=round(block["d1"] / scale, 2),
        d5=round(block["d5"] / scale, 2),
        d20=round(block["d20"] / scale, 2),
        streak=streak,
        price_pct=round(flow["price_pct"], 2),
        close=round(flow["close"], 2),
    )


def _streak(values: list[float]) -> int:
    """Longest run (from today backwards) with the same net-buy sign.

    Returns a signed integer: positive length for a positive-net streak,
    negative length for a negative-net streak.
    """
    if not values:
        return 0
    first_sign = 1 if values[0] > 0 else (-1 if values[0] < 0 else 0)
    if first_sign == 0:
        return 0
    count = 0
    for v in values:
        sign = 1 if v > 0 else (-1 if v < 0 else 0)
        if sign != first_sign:
            break
        count += 1
    return first_sign * count
