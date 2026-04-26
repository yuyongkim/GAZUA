"""Panel loader — pull a wide OHLCV window once, cache, reuse.

Design:
    A Chartist /today request needs the last ~250 trading days of
    close+volume for every KOSPI+KOSDAQ symbol. Issuing N per-symbol
    ``repo.get_ohlcv`` calls costs ~3-5s on this dataset; a single
    ``SELECT symbol, date, open, high, low, close, volume FROM ohlcv
    WHERE date >= :start`` returns the whole panel in ~400ms.

    We cache the most recent panel keyed on (as_of, lookback, markets).
    Cache lives inside the process for the lifetime of the FastAPI worker.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import Any, Iterable

from sqlalchemy import text

from ky_core.storage.db import get_engine, init_db


DEFAULT_MARKETS: tuple[str, ...] = ("KOSPI", "KOSDAQ", "KONEX")
DEFAULT_LOOKBACK_DAYS = 365  # calendar days → ~250 trading days


@dataclass(frozen=True)
class Panel:
    """Wide OHLCV panel covering multiple symbols.

    ``series`` maps symbol -> list of daily dict rows ordered by date asc.
    ``as_of`` is the latest date present in the panel. ``universe`` maps
    symbol -> ``{ticker, market, name, sector}`` for labelling.
    """
    as_of: str
    start: str
    series: dict[str, list[dict[str, Any]]]
    universe: dict[str, dict[str, Any]]

    @property
    def symbols(self) -> list[str]:
        return list(self.series.keys())

    def closes(self, symbol: str) -> list[float]:
        return [r["close"] for r in self.series.get(symbol, []) if r["close"] is not None]

    def volumes(self, symbol: str) -> list[int]:
        return [int(r["volume"]) for r in self.series.get(symbol, []) if r.get("volume")]

    def dates(self, symbol: str) -> list[str]:
        return [r["date"] for r in self.series.get(symbol, [])]


# Module-level single-slot cache. Keyed by (as_of, lookback_days, markets_tuple, owner).
_cache: dict[tuple[str, int, tuple[str, ...], str], Panel] = {}


def load_panel(
    as_of: date | str | None = None,
    *,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    markets: Iterable[str] = DEFAULT_MARKETS,
    owner_id: str = "self",
    min_rows: int = 60,
) -> Panel:
    """Return a wide panel. Heavy SQL query is cached per key.

    ``as_of`` is the target close date; if None we use the latest
    FULL-coverage date (>= 500 distinct symbols) in the DB. The
    partial-coverage April 4–17 tail (5 sentinel symbols) is therefore
    skipped automatically.
    """
    init_db()
    markets_tuple = tuple(sorted(markets))
    resolved_as_of = _resolve_as_of(as_of)
    key = (resolved_as_of, lookback_days, markets_tuple, owner_id)
    if key in _cache:
        return _cache[key]

    start_dt = datetime.strptime(resolved_as_of, "%Y-%m-%d").date() - timedelta(days=lookback_days)
    start = start_dt.isoformat()

    universe = _load_universe(markets_tuple, owner_id)
    series = _load_ohlcv(start, resolved_as_of, markets_tuple, owner_id)

    # drop symbols with too few rows or not in universe (filters UNKNOWN/etfs etc.)
    cleaned = {
        sym: rows
        for sym, rows in series.items()
        if sym in universe and len(rows) >= min_rows
    }
    panel = Panel(
        as_of=resolved_as_of,
        start=start,
        series=cleaned,
        universe={s: universe[s] for s in cleaned},
    )
    _cache[key] = panel
    return panel


def clear_cache() -> None:
    _cache.clear()
    _resolve_as_of_cached.cache_clear()


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _resolve_as_of(as_of: date | str | None) -> str:
    if as_of is None:
        return _resolve_as_of_cached()
    if isinstance(as_of, date):
        return as_of.isoformat()
    return as_of


@lru_cache(maxsize=1)
def _resolve_as_of_cached() -> str:
    """Latest date with full-universe coverage (>= 500 symbols)."""
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(text(
            """
            SELECT date FROM (
                SELECT date, COUNT(*) c FROM ohlcv GROUP BY date
            ) WHERE c >= 500 ORDER BY date DESC LIMIT 1
            """
        )).first()
    if row is None:
        return date.today().isoformat()
    return row[0]


def _load_universe(markets: tuple[str, ...], owner_id: str) -> dict[str, dict[str, Any]]:
    engine = get_engine()
    placeholders = ",".join(f":m{i}" for i in range(len(markets)))
    params: dict[str, Any] = {f"m{i}": m for i, m in enumerate(markets)}
    params["owner_id"] = owner_id
    stmt = text(
        f"""
        SELECT ticker, market, name, sector, industry, is_etf
        FROM universe
        WHERE market IN ({placeholders})
          AND owner_id = :owner_id
          AND COALESCE(is_etf, 0) = 0
        """
    )
    out: dict[str, dict[str, Any]] = {}
    with engine.connect() as conn:
        for r in conn.execute(stmt, params):
            out[r.ticker] = {
                "symbol": r.ticker,
                "market": r.market,
                "name": r.name or r.ticker,
                "sector": r.sector or "기타",
                "industry": r.industry,
            }
    return out


def _load_ohlcv(
    start: str,
    end: str,
    markets: tuple[str, ...],
    owner_id: str,
) -> dict[str, list[dict[str, Any]]]:
    engine = get_engine()
    placeholders = ",".join(f":m{i}" for i in range(len(markets)))
    params: dict[str, Any] = {f"m{i}": m for i, m in enumerate(markets)}
    params.update({"start": start, "end": end, "owner_id": owner_id})
    stmt = text(
        f"""
        SELECT symbol, date, open, high, low, close, volume
        FROM ohlcv
        WHERE owner_id = :owner_id
          AND market IN ({placeholders})
          AND date BETWEEN :start AND :end
        ORDER BY symbol ASC, date ASC
        """
    )
    out: dict[str, list[dict[str, Any]]] = {}
    with engine.connect() as conn:
        for r in conn.execute(stmt, params):
            out.setdefault(r.symbol, []).append(
                {
                    "date": r.date,
                    "open": r.open,
                    "high": r.high,
                    "low": r.low,
                    "close": r.close,
                    "volume": r.volume,
                }
            )
    return out
