"""Point-in-time fundamentals loader shared by the fundamental-driven
backtest strategies (Magic Formula, Quality+Momentum, Value-QMJ).

Why this exists
---------------

``financials_pit.report_date`` is populated for most recent filings
but for older filings it is either NULL or carries a bulk-import
sentinel (``2025-06-25``) because we ingested the historical DART
corpus in one batch. Using that sentinel as the PIT gate would hide
every pre-2025 fundamental from every pre-2025 backtest — defeating
the purpose.

:func:`effective_date` implements the following rule:

    if report_date is None: use period_end + 90 days
    if report_date is present AND (report_date - period_end) > 180 days:
        treat as bulk-import sentinel → use period_end + 90 days
    else: use report_date as-is

This keeps real filings honest (a 2024 Q1 report filed 2024-05-15 is
usable from 2024-05-15 on) while still allowing the decade of historical
fundamentals to participate, with a conservative 90-day embargo.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import text

from ky_core.storage.db import get_engine


def effective_date(report_date: str | None, period_end: str) -> str:
    """Return the date from which a financial record may be used."""
    try:
        pe = date.fromisoformat(period_end)
    except (ValueError, TypeError):
        return period_end
    fallback = (pe + timedelta(days=90)).isoformat()
    if not report_date:
        return fallback
    try:
        rd = date.fromisoformat(report_date)
    except ValueError:
        return fallback
    if (rd - pe).days > 180:
        # bulk-import sentinel timestamp — fall back to the 90-day lag
        return fallback
    return report_date


def load_latest_fundamentals(
    as_of: str,
    *,
    period_types: tuple[str, ...] = ("FY", "Q4"),
    max_records_per_symbol: int = 2,
) -> dict[str, list[dict[str, Any]]]:
    """Return up to ``max_records_per_symbol`` latest periods per symbol
    that were effectively available by ``as_of``.

    Record order: newest period first.
    """
    engine = get_engine()
    placeholders = ",".join(f":p{i}" for i in range(len(period_types)))
    params: dict[str, Any] = {"as_of": as_of}
    for i, pt in enumerate(period_types):
        params[f"p{i}"] = pt
    per_sym: dict[str, list[dict[str, Any]]] = {}
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                f"""
                SELECT symbol, period_end, period_type, report_date,
                       revenue, operating_income, net_income,
                       total_assets, total_equity
                FROM financials_pit
                WHERE owner_id = 'self'
                  AND period_type IN ({placeholders})
                  AND period_end <= :as_of
                ORDER BY symbol, period_end DESC
                """
            ),
            params,
        ).fetchall()
    for r in rows:
        eff = effective_date(r.report_date, r.period_end)
        if eff > as_of:
            continue
        arr = per_sym.setdefault(r.symbol, [])
        if len(arr) >= max_records_per_symbol:
            continue
        arr.append({
            "period_end": r.period_end,
            "period_type": r.period_type,
            "report_date": r.report_date,
            "effective_date": eff,
            "revenue": r.revenue,
            "operating_income": r.operating_income,
            "net_income": r.net_income,
            "total_assets": r.total_assets,
            "total_equity": r.total_equity,
        })
    return per_sym


def latest_only(
    as_of: str,
    *,
    period_types: tuple[str, ...] = ("FY", "Q4"),
) -> dict[str, dict[str, Any]]:
    per_sym = load_latest_fundamentals(as_of, period_types=period_types, max_records_per_symbol=1)
    return {sym: recs[0] for sym, recs in per_sym.items() if recs}
