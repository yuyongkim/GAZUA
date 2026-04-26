"""Repository — thin CRUD wrapper around the SQLAlchemy schema.

Why a wrapper: application code and collect scripts should not construct ORM
sessions or execute raw SQL. They pass dicts shaped like the adapter rows and
the repository takes care of upserting, dedup, and tenant scoping.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterable, Iterator, Sequence

from sqlalchemy import and_, desc, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from ky_core.storage.db import get_session_factory, init_db
from ky_core.storage.schema import (
    ApiUsageLog,
    AuditLog,
    DividendHistory,
    Filing,
    FinancialPIT,
    FinancialSegment,
    FinancialStatementDB,
    FnguideSnapshot,
    Observation,
    OHLCV,
    Tenant,
    Universe,
)


class Repository:
    """Entry point for all persistence operations."""

    def __init__(self, owner_id: str = "self", *, auto_init: bool = True) -> None:
        self.owner_id = owner_id
        if auto_init:
            init_db()

    # --------- Session management ---------

    @contextmanager
    def session(self) -> Iterator[Session]:
        factory = get_session_factory()
        sess = factory()
        try:
            yield sess
            sess.commit()
        except Exception:
            sess.rollback()
            raise
        finally:
            sess.close()

    # --------- Observations ---------

    def upsert_observations(self, rows: Iterable[dict[str, Any]]) -> int:
        """Upsert observation rows. Returns the number of rows written."""
        rows = list(rows)
        if not rows:
            return 0
        now = datetime.utcnow()
        payload = []
        for row in rows:
            payload.append(
                {
                    "source_id": row["source_id"],
                    "series_id": row["series_id"],
                    "date": row["date"],
                    "value": row.get("value"),
                    "unit": row.get("unit"),
                    "meta": row.get("meta"),
                    "fetched_at": now,
                    "owner_id": row.get("owner_id", self.owner_id),
                }
            )
        stmt = sqlite_insert(Observation.__table__).values(payload)
        stmt = stmt.on_conflict_do_update(
            index_elements=["source_id", "series_id", "date", "owner_id"],
            set_={
                "value": stmt.excluded.value,
                "unit": stmt.excluded.unit,
                "meta": stmt.excluded.meta,
                "fetched_at": stmt.excluded.fetched_at,
            },
        )
        with self.session() as sess:
            sess.execute(stmt)
        return len(payload)

    def get_observations(
        self,
        source_id: str,
        series_id: str,
        start: str | None = None,
        end: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        with self.session() as sess:
            stmt = select(Observation).where(
                and_(
                    Observation.source_id == source_id,
                    Observation.series_id == series_id,
                    Observation.owner_id == self.owner_id,
                )
            )
            if start:
                stmt = stmt.where(Observation.date >= start)
            if end:
                stmt = stmt.where(Observation.date <= end)
            stmt = stmt.order_by(Observation.date.asc())
            if limit:
                stmt = stmt.limit(limit)
            rows = sess.execute(stmt).scalars().all()
            return [_obs_to_dict(r) for r in rows]

    def latest_observation(self, source_id: str, series_id: str) -> dict[str, Any] | None:
        with self.session() as sess:
            stmt = (
                select(Observation)
                .where(
                    Observation.source_id == source_id,
                    Observation.series_id == series_id,
                    Observation.owner_id == self.owner_id,
                )
                .order_by(desc(Observation.date))
                .limit(1)
            )
            row = sess.execute(stmt).scalars().first()
            return _obs_to_dict(row) if row else None

    # --------- Filings ---------

    def upsert_filings(self, rows: Iterable[dict[str, Any]]) -> int:
        rows = list(rows)
        if not rows:
            return 0
        now = datetime.utcnow()
        payload = []
        for row in rows:
            payload.append(
                {
                    "source_id": row["source_id"],
                    "corp_code": row.get("corp_code", ""),
                    "receipt_no": row["receipt_no"],
                    "filed_at": row.get("filed_at", ""),
                    "type": row.get("type"),
                    "summary": row.get("summary"),
                    "meta": row.get("meta"),
                    "fetched_at": now,
                    "owner_id": row.get("owner_id", self.owner_id),
                }
            )
        stmt = sqlite_insert(Filing.__table__).values(payload)
        stmt = stmt.on_conflict_do_update(
            index_elements=["source_id", "receipt_no", "owner_id"],
            set_={
                "type": stmt.excluded.type,
                "summary": stmt.excluded.summary,
                "meta": stmt.excluded.meta,
                "filed_at": stmt.excluded.filed_at,
                "corp_code": stmt.excluded.corp_code,
                "fetched_at": stmt.excluded.fetched_at,
            },
        )
        with self.session() as sess:
            sess.execute(stmt)
        return len(payload)

    def recent_filings(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.session() as sess:
            stmt = (
                select(Filing)
                .where(Filing.owner_id == self.owner_id)
                .order_by(desc(Filing.filed_at))
                .limit(limit)
            )
            rows = sess.execute(stmt).scalars().all()
            return [_filing_to_dict(r) for r in rows]

    # --------- Universe ---------

    def upsert_universe(self, rows: Sequence[dict[str, Any]]) -> int:
        if not rows:
            return 0
        now = datetime.utcnow()
        payload = [
            {
                "ticker": r["ticker"],
                "market": r["market"],
                "name": r.get("name"),
                "sector": r.get("sector"),
                "industry": r.get("industry"),
                "is_etf": bool(r.get("is_etf", False)),
                "meta": r.get("meta"),
                "updated_at": now,
                "owner_id": r.get("owner_id", self.owner_id),
            }
            for r in rows
        ]
        stmt = sqlite_insert(Universe.__table__).values(payload)
        stmt = stmt.on_conflict_do_update(
            index_elements=["ticker", "market", "owner_id"],
            set_={
                "name": stmt.excluded.name,
                "sector": stmt.excluded.sector,
                "industry": stmt.excluded.industry,
                "is_etf": stmt.excluded.is_etf,
                "meta": stmt.excluded.meta,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        with self.session() as sess:
            sess.execute(stmt)
        return len(payload)

    def count_universe(self) -> int:
        with self.session() as sess:
            return sess.query(Universe).filter(Universe.owner_id == self.owner_id).count()

    def get_universe(self, ticker: str) -> dict[str, Any] | None:
        with self.session() as sess:
            stmt = (
                select(Universe)
                .where(Universe.ticker == ticker, Universe.owner_id == self.owner_id)
                .limit(1)
            )
            row = sess.execute(stmt).scalars().first()
            if not row:
                return None
            return {
                "ticker": row.ticker,
                "market": row.market,
                "name": row.name,
                "sector": row.sector,
                "industry": row.industry,
                "is_etf": bool(row.is_etf),
                "meta": row.meta,
            }

    # --------- OHLCV ---------

    def upsert_ohlcv(self, rows: Iterable[dict[str, Any]]) -> int:
        """Batch upsert OHLCV rows.

        Each row must contain: symbol, date (ISO), close, source_id.
        Optional: open, high, low, volume, adj_close, market, owner_id.
        """
        rows = list(rows)
        if not rows:
            return 0
        now = datetime.utcnow()
        payload = []
        for row in rows:
            payload.append(
                {
                    "symbol": row["symbol"],
                    "market": row.get("market") or "UNKNOWN",
                    "date": row["date"],
                    "open": row.get("open"),
                    "high": row.get("high"),
                    "low": row.get("low"),
                    "close": row["close"],
                    "volume": row.get("volume"),
                    "adj_close": row.get("adj_close"),
                    "source_id": row["source_id"],
                    "fetched_at": now,
                    "owner_id": row.get("owner_id", self.owner_id),
                }
            )
        stmt = sqlite_insert(OHLCV.__table__).values(payload)
        stmt = stmt.on_conflict_do_update(
            index_elements=["owner_id", "symbol", "date"],
            set_={
                "market": stmt.excluded.market,
                "open": stmt.excluded.open,
                "high": stmt.excluded.high,
                "low": stmt.excluded.low,
                "close": stmt.excluded.close,
                "volume": stmt.excluded.volume,
                "adj_close": stmt.excluded.adj_close,
                "source_id": stmt.excluded.source_id,
                "fetched_at": stmt.excluded.fetched_at,
            },
        )
        with self.session() as sess:
            sess.execute(stmt)
        return len(payload)

    def get_ohlcv(
        self,
        symbol: str,
        start: str | None = None,
        end: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        with self.session() as sess:
            stmt = select(OHLCV).where(
                OHLCV.symbol == symbol,
                OHLCV.owner_id == self.owner_id,
            )
            if start:
                stmt = stmt.where(OHLCV.date >= start)
            if end:
                stmt = stmt.where(OHLCV.date <= end)
            stmt = stmt.order_by(OHLCV.date.asc())
            if limit:
                stmt = stmt.limit(limit)
            return [_ohlcv_to_dict(r) for r in sess.execute(stmt).scalars().all()]

    def get_ohlcv_latest(self, symbol: str) -> dict[str, Any] | None:
        with self.session() as sess:
            stmt = (
                select(OHLCV)
                .where(OHLCV.symbol == symbol, OHLCV.owner_id == self.owner_id)
                .order_by(desc(OHLCV.date))
                .limit(1)
            )
            row = sess.execute(stmt).scalars().first()
            return _ohlcv_to_dict(row) if row else None

    def count_ohlcv(self) -> int:
        with self.session() as sess:
            return sess.query(OHLCV).filter(OHLCV.owner_id == self.owner_id).count()

    def count_ohlcv_symbols(self) -> int:
        from sqlalchemy import distinct, func
        with self.session() as sess:
            return sess.query(func.count(distinct(OHLCV.symbol))).filter(
                OHLCV.owner_id == self.owner_id
            ).scalar() or 0

    # --------- Financials PIT ---------

    def upsert_financials(self, rows: Iterable[dict[str, Any]]) -> int:
        rows = list(rows)
        if not rows:
            return 0
        now = datetime.utcnow()
        payload = []
        for row in rows:
            payload.append(
                {
                    "corp_code": row.get("corp_code"),
                    "symbol": row["symbol"],
                    "report_date": row.get("report_date"),
                    "period_end": row["period_end"],
                    "period_type": row["period_type"],
                    "revenue": row.get("revenue"),
                    "operating_income": row.get("operating_income"),
                    "net_income": row.get("net_income"),
                    "total_assets": row.get("total_assets"),
                    "total_liabilities": row.get("total_liabilities"),
                    "total_equity": row.get("total_equity"),
                    "raw": row.get("raw"),
                    "source_id": row["source_id"],
                    "fetched_at": now,
                    "owner_id": row.get("owner_id", self.owner_id),
                }
            )
        stmt = sqlite_insert(FinancialPIT.__table__).values(payload)
        stmt = stmt.on_conflict_do_update(
            index_elements=["owner_id", "symbol", "period_end", "period_type", "source_id"],
            set_={
                "corp_code": stmt.excluded.corp_code,
                "report_date": stmt.excluded.report_date,
                "revenue": stmt.excluded.revenue,
                "operating_income": stmt.excluded.operating_income,
                "net_income": stmt.excluded.net_income,
                "total_assets": stmt.excluded.total_assets,
                "total_liabilities": stmt.excluded.total_liabilities,
                "total_equity": stmt.excluded.total_equity,
                "raw": stmt.excluded.raw,
                "fetched_at": stmt.excluded.fetched_at,
            },
        )
        with self.session() as sess:
            sess.execute(stmt)
        return len(payload)

    def get_financials(
        self,
        symbol: str,
        period_end: str | None = None,
        as_of: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return financials. If ``as_of`` provided, only those with
        report_date <= as_of (proper PIT query)."""
        with self.session() as sess:
            stmt = select(FinancialPIT).where(
                FinancialPIT.symbol == symbol,
                FinancialPIT.owner_id == self.owner_id,
            )
            if period_end:
                stmt = stmt.where(FinancialPIT.period_end == period_end)
            if as_of:
                stmt = stmt.where(FinancialPIT.report_date != None)  # noqa: E711
                stmt = stmt.where(FinancialPIT.report_date <= as_of)
            stmt = stmt.order_by(FinancialPIT.period_end.desc())
            return [_fin_to_dict(r) for r in sess.execute(stmt).scalars().all()]

    def count_financials(self) -> int:
        with self.session() as sess:
            return sess.query(FinancialPIT).filter(
                FinancialPIT.owner_id == self.owner_id
            ).count()

    # --------- FnGuide snapshots ---------

    def upsert_fnguide_snapshot(
        self,
        symbol: str,
        payload_json: str,
        *,
        source: str | None = None,
        degraded: bool = False,
        fetched_at: datetime | None = None,
    ) -> int:
        """Upsert a single fnguide snapshot. Returns 1.

        When ``fetched_at`` is provided we honour it — bulk migrations pass the
        original source capture timestamp so downstream freshness checks track
        data age rather than import age.
        """
        now = fetched_at if fetched_at is not None else datetime.utcnow()
        row = {
            "symbol": symbol,
            "payload": payload_json,
            "source": source,
            "degraded": bool(degraded),
            "fetched_at": now,
            "owner_id": self.owner_id,
        }
        stmt = sqlite_insert(FnguideSnapshot.__table__).values([row])
        stmt = stmt.on_conflict_do_update(
            index_elements=["symbol", "owner_id"],
            set_={
                "payload": stmt.excluded.payload,
                "source": stmt.excluded.source,
                "degraded": stmt.excluded.degraded,
                "fetched_at": stmt.excluded.fetched_at,
            },
        )
        with self.session() as sess:
            sess.execute(stmt)
        return 1

    def get_fnguide_snapshot(self, symbol: str) -> dict[str, Any] | None:
        with self.session() as sess:
            stmt = (
                select(FnguideSnapshot)
                .where(
                    FnguideSnapshot.symbol == symbol,
                    FnguideSnapshot.owner_id == self.owner_id,
                )
                .limit(1)
            )
            row = sess.execute(stmt).scalars().first()
            if not row:
                return None
            return {
                "symbol": row.symbol,
                "payload": row.payload,
                "source": row.source,
                "degraded": bool(row.degraded),
                "fetched_at": row.fetched_at.isoformat() if row.fetched_at else None,
                "owner_id": row.owner_id,
            }

    def get_fnguide_cached(
        self,
        symbol: str,
        *,
        max_age_hours: float = 24.0,
    ) -> dict[str, Any] | None:
        """Return a cached fnguide snapshot if it exists AND is younger than
        ``max_age_hours``.

        Differs from ``get_fnguide_snapshot`` by gating on age — callers that
        want the DB-first freshness semantics (FnguideAdapter) should use this.
        Returns ``None`` when the row is missing OR stale."""
        row = self.get_fnguide_snapshot(symbol)
        if not row:
            return None
        fetched_iso = row.get("fetched_at")
        if not fetched_iso:
            return None
        try:
            fetched_dt = datetime.fromisoformat(fetched_iso)
        except ValueError:
            return None
        age_hours = (datetime.utcnow() - fetched_dt).total_seconds() / 3600.0
        if age_hours > max_age_hours:
            return None
        row["age_hours"] = age_hours
        return row

    def get_fnguide_age_hours(self, symbol: str) -> float | None:
        """Hours since this symbol's snapshot was last persisted. ``None`` when
        there is no row. Useful for deciding fresh vs stale vs missing without
        double-reading the payload."""
        row = self.get_fnguide_snapshot(symbol)
        if not row or not row.get("fetched_at"):
            return None
        try:
            fetched_dt = datetime.fromisoformat(row["fetched_at"])
        except ValueError:
            return None
        return (datetime.utcnow() - fetched_dt).total_seconds() / 3600.0

    # --------- Financial Statements (migrated from Naver full-collection) ---

    def upsert_financial_statements(self, rows: Iterable[dict[str, Any]]) -> int:
        """Bulk-upsert per-account statement rows. Returns count persisted.

        Each row must contain: symbol, period, period_type, account_name.
        Optional: account_code, account_level, value, yoy, is_estimate,
        source_id, owner_id.

        Internally chunked to ~500 rows per INSERT to stay under SQLite's
        ``SQLITE_MAX_VARIABLE_NUMBER`` (default 999 on older builds; 32k on
        newer). 500 rows × 12 cols = 6k variables — safe on every SQLite
        build.
        """
        rows = list(rows)
        if not rows:
            return 0
        now = datetime.utcnow()
        payload = []
        for row in rows:
            payload.append(
                {
                    "symbol": row["symbol"],
                    "period": row["period"],
                    "period_type": row["period_type"],
                    "account_code": row.get("account_code"),
                    "account_name": row["account_name"],
                    "account_level": row.get("account_level"),
                    "value": row.get("value"),
                    "yoy": row.get("yoy"),
                    "is_estimate": bool(row.get("is_estimate", False)),
                    "source_id": row.get("source_id", "naver_comp"),
                    "fetched_at": now,
                    "owner_id": row.get("owner_id", self.owner_id),
                }
            )
        chunk_size = 500
        total = 0
        with self.session() as sess:
            for i in range(0, len(payload), chunk_size):
                chunk = payload[i : i + chunk_size]
                stmt = sqlite_insert(FinancialStatementDB.__table__).values(chunk)
                stmt = stmt.on_conflict_do_update(
                    index_elements=[
                        "owner_id", "symbol", "period", "period_type",
                        "account_name", "source_id",
                    ],
                    set_={
                        "account_code": stmt.excluded.account_code,
                        "account_level": stmt.excluded.account_level,
                        "value": stmt.excluded.value,
                        "yoy": stmt.excluded.yoy,
                        "is_estimate": stmt.excluded.is_estimate,
                        "fetched_at": stmt.excluded.fetched_at,
                    },
                )
                sess.execute(stmt)
                total += len(chunk)
        return total

    def get_statements(
        self,
        symbol: str,
        *,
        period_type: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return financial statement rows for a symbol, newest period first."""
        with self.session() as sess:
            stmt = select(FinancialStatementDB).where(
                FinancialStatementDB.symbol == symbol,
                FinancialStatementDB.owner_id == self.owner_id,
            )
            if period_type:
                stmt = stmt.where(FinancialStatementDB.period_type == period_type)
            stmt = stmt.order_by(
                FinancialStatementDB.period.desc(),
                FinancialStatementDB.account_level.asc().nullsfirst(),
            )
            if limit:
                stmt = stmt.limit(limit)
            rows = sess.execute(stmt).scalars().all()
            return [
                {
                    "symbol": r.symbol,
                    "period": r.period,
                    "period_type": r.period_type,
                    "account_code": r.account_code,
                    "account_name": r.account_name,
                    "account_level": r.account_level,
                    "value": r.value,
                    "yoy": r.yoy,
                    "is_estimate": bool(r.is_estimate),
                    "source_id": r.source_id,
                }
                for r in rows
            ]

    def count_statements(self) -> int:
        with self.session() as sess:
            return sess.query(FinancialStatementDB).filter(
                FinancialStatementDB.owner_id == self.owner_id
            ).count()

    # --------- Financial Segments (DART 사업부문별) ----------

    def upsert_segments(self, rows: Iterable[dict[str, Any]]) -> int:
        """Bulk-upsert segment rows. Returns count persisted.

        Each row must contain: ``symbol``, ``period_end``, ``segment_name``.
        Optional: ``corp_code``, ``period_type``, ``revenue``,
        ``operating_income``, ``revenue_share``, ``source_id``, ``raw``.
        """
        rows = list(rows)
        if not rows:
            return 0
        now = datetime.utcnow()
        payload = []
        for row in rows:
            payload.append(
                {
                    "symbol": row["symbol"],
                    "corp_code": row.get("corp_code"),
                    "period_end": row["period_end"],
                    "period_type": row.get("period_type", "FY"),
                    "segment_name": row["segment_name"],
                    "revenue": row.get("revenue"),
                    "operating_income": row.get("operating_income"),
                    "revenue_share": row.get("revenue_share"),
                    "source_id": row.get("source_id", "dart"),
                    "raw": row.get("raw"),
                    "fetched_at": now,
                    "owner_id": row.get("owner_id", self.owner_id),
                }
            )
        chunk_size = 400
        total = 0
        with self.session() as sess:
            for i in range(0, len(payload), chunk_size):
                chunk = payload[i : i + chunk_size]
                stmt = sqlite_insert(FinancialSegment.__table__).values(chunk)
                stmt = stmt.on_conflict_do_update(
                    index_elements=[
                        "owner_id", "symbol", "period_end", "segment_name", "source_id",
                    ],
                    set_={
                        "corp_code": stmt.excluded.corp_code,
                        "period_type": stmt.excluded.period_type,
                        "revenue": stmt.excluded.revenue,
                        "operating_income": stmt.excluded.operating_income,
                        "revenue_share": stmt.excluded.revenue_share,
                        "raw": stmt.excluded.raw,
                        "fetched_at": stmt.excluded.fetched_at,
                    },
                )
                sess.execute(stmt)
                total += len(chunk)
        return total

    def get_segments(
        self,
        symbol: str,
        *,
        period_end: str | None = None,
    ) -> list[dict[str, Any]]:
        with self.session() as sess:
            stmt = select(FinancialSegment).where(
                FinancialSegment.symbol == symbol,
                FinancialSegment.owner_id == self.owner_id,
            )
            if period_end:
                stmt = stmt.where(FinancialSegment.period_end == period_end)
            stmt = stmt.order_by(
                FinancialSegment.period_end.desc(),
                desc(FinancialSegment.revenue_share),
            )
            rows = sess.execute(stmt).scalars().all()
            return [
                {
                    "symbol": r.symbol,
                    "corp_code": r.corp_code,
                    "period_end": r.period_end,
                    "period_type": r.period_type,
                    "segment_name": r.segment_name,
                    "revenue": r.revenue,
                    "operating_income": r.operating_income,
                    "revenue_share": r.revenue_share,
                    "source_id": r.source_id,
                }
                for r in rows
            ]

    def count_segments(self) -> int:
        with self.session() as sess:
            return sess.query(FinancialSegment).filter(
                FinancialSegment.owner_id == self.owner_id
            ).count()

    def count_segment_symbols(self) -> int:
        from sqlalchemy import distinct, func
        with self.session() as sess:
            return sess.query(func.count(distinct(FinancialSegment.symbol))).filter(
                FinancialSegment.owner_id == self.owner_id
            ).scalar() or 0

    # --------- Dividend History (DART-derived DPS) ----------

    def upsert_dividend_history(self, rows: Iterable[dict[str, Any]]) -> int:
        """Bulk-upsert per-year DPS rows. Returns count persisted."""
        rows = list(rows)
        if not rows:
            return 0
        now = datetime.utcnow()
        payload = []
        for row in rows:
            payload.append(
                {
                    "symbol": row["symbol"],
                    "corp_code": row.get("corp_code"),
                    "period_end": row["period_end"],
                    "share_type": row.get("share_type", "common"),
                    "dps": row.get("dps"),
                    "payout_total": row.get("payout_total"),
                    "payout_ratio": row.get("payout_ratio"),
                    "dividend_yield": row.get("dividend_yield"),
                    "source_id": row.get("source_id", "dart"),
                    "raw": row.get("raw"),
                    "fetched_at": now,
                    "owner_id": row.get("owner_id", self.owner_id),
                }
            )
        chunk_size = 400
        total = 0
        with self.session() as sess:
            for i in range(0, len(payload), chunk_size):
                chunk = payload[i : i + chunk_size]
                stmt = sqlite_insert(DividendHistory.__table__).values(chunk)
                stmt = stmt.on_conflict_do_update(
                    index_elements=[
                        "owner_id", "symbol", "period_end", "share_type", "source_id",
                    ],
                    set_={
                        "corp_code": stmt.excluded.corp_code,
                        "dps": stmt.excluded.dps,
                        "payout_total": stmt.excluded.payout_total,
                        "payout_ratio": stmt.excluded.payout_ratio,
                        "dividend_yield": stmt.excluded.dividend_yield,
                        "raw": stmt.excluded.raw,
                        "fetched_at": stmt.excluded.fetched_at,
                    },
                )
                sess.execute(stmt)
                total += len(chunk)
        return total

    def get_dividend_history(
        self,
        symbol: str,
        *,
        share_type: str = "common",
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        with self.session() as sess:
            stmt = select(DividendHistory).where(
                DividendHistory.symbol == symbol,
                DividendHistory.owner_id == self.owner_id,
                DividendHistory.share_type == share_type,
            ).order_by(DividendHistory.period_end.asc())
            if limit:
                stmt = stmt.limit(limit)
            rows = sess.execute(stmt).scalars().all()
            return [
                {
                    "symbol": r.symbol,
                    "period_end": r.period_end,
                    "share_type": r.share_type,
                    "dps": r.dps,
                    "payout_total": r.payout_total,
                    "payout_ratio": r.payout_ratio,
                    "dividend_yield": r.dividend_yield,
                    "source_id": r.source_id,
                }
                for r in rows
            ]

    def get_all_dividend_history(self) -> list[dict[str, Any]]:
        """Fetch every DPS row for the tenant — used by the dividend screener
        to compute long-horizon streaks in one SQL."""
        with self.session() as sess:
            stmt = select(DividendHistory).where(
                DividendHistory.owner_id == self.owner_id,
                DividendHistory.share_type == "common",
            ).order_by(
                DividendHistory.symbol.asc(), DividendHistory.period_end.asc()
            )
            rows = sess.execute(stmt).scalars().all()
            return [
                {
                    "symbol": r.symbol,
                    "period_end": r.period_end,
                    "dps": r.dps,
                    "payout_total": r.payout_total,
                    "dividend_yield": r.dividend_yield,
                }
                for r in rows
            ]

    def count_dividend_history(self) -> int:
        with self.session() as sess:
            return sess.query(DividendHistory).filter(
                DividendHistory.owner_id == self.owner_id
            ).count()

    def count_dividend_symbols(self) -> int:
        from sqlalchemy import distinct, func
        with self.session() as sess:
            return sess.query(func.count(distinct(DividendHistory.symbol))).filter(
                DividendHistory.owner_id == self.owner_id
            ).scalar() or 0

    # --------- Tenants (control plane — not scoped by owner_id) ------------

    def list_tenants(self, *, enabled_only: bool = False) -> list[dict[str, Any]]:
        with self.session() as sess:
            stmt = select(Tenant).order_by(Tenant.created_at.asc())
            if enabled_only:
                stmt = stmt.where(Tenant.enabled == True)  # noqa: E712
            rows = sess.execute(stmt).scalars().all()
            return [_tenant_to_dict(r) for r in rows]

    def get_tenant(self, tenant_id: str) -> dict[str, Any] | None:
        with self.session() as sess:
            row = sess.get(Tenant, tenant_id)
            return _tenant_to_dict(row) if row else None

    def get_tenant_by_key_hash(self, api_key_hash: str) -> dict[str, Any] | None:
        with self.session() as sess:
            stmt = select(Tenant).where(Tenant.api_key_hash == api_key_hash).limit(1)
            row = sess.execute(stmt).scalars().first()
            return _tenant_to_dict(row) if row else None

    def upsert_tenant(
        self,
        *,
        tenant_id: str,
        display_name: str,
        api_key_hash: str,
        plan: str = "trial",
        rate_limit_per_min: int = 60,
        enabled: bool = True,
    ) -> dict[str, Any]:
        with self.session() as sess:
            existing = sess.get(Tenant, tenant_id)
            if existing is None:
                row = Tenant(
                    tenant_id=tenant_id,
                    display_name=display_name,
                    api_key_hash=api_key_hash,
                    plan=plan,
                    rate_limit_per_min=rate_limit_per_min,
                    enabled=enabled,
                    created_at=datetime.utcnow(),
                )
                sess.add(row)
            else:
                existing.display_name = display_name
                existing.api_key_hash = api_key_hash
                existing.plan = plan
                existing.rate_limit_per_min = rate_limit_per_min
                existing.enabled = enabled
                row = existing
            sess.flush()
            return _tenant_to_dict(row)

    def rotate_tenant_api_key(
        self, tenant_id: str, new_api_key_hash: str
    ) -> dict[str, Any] | None:
        with self.session() as sess:
            row = sess.get(Tenant, tenant_id)
            if row is None:
                return None
            row.api_key_hash = new_api_key_hash
            sess.flush()
            return _tenant_to_dict(row)

    def set_tenant_enabled(self, tenant_id: str, enabled: bool) -> dict[str, Any] | None:
        with self.session() as sess:
            row = sess.get(Tenant, tenant_id)
            if row is None:
                return None
            row.enabled = bool(enabled)
            sess.flush()
            return _tenant_to_dict(row)

    def ensure_self_tenant(self) -> dict[str, Any]:
        """Idempotent seed: guarantees the ``self`` row exists so legacy code paths
        (``owner_id='self'``) keep working once the Tenant table ships."""
        existing = self.get_tenant("self")
        if existing:
            return existing
        return self.upsert_tenant(
            tenant_id="self",
            display_name="self",
            api_key_hash="",              # sentinel — cannot match any API key
            plan="self",
            rate_limit_per_min=9999,
            enabled=True,
        )

    # --------- API usage log + audit log ----------

    def log_api_usage(
        self,
        *,
        tenant_id: str,
        endpoint: str,
        latency_ms: int,
        status_code: int,
        ts: datetime | None = None,
    ) -> int:
        with self.session() as sess:
            row = ApiUsageLog(
                tenant_id=tenant_id,
                endpoint=endpoint,
                latency_ms=int(latency_ms),
                status_code=int(status_code),
                ts=ts or datetime.utcnow(),
            )
            sess.add(row)
            sess.flush()
            return row.id

    def count_api_usage_since(self, tenant_id: str, since: datetime) -> int:
        with self.session() as sess:
            return (
                sess.query(ApiUsageLog)
                .filter(ApiUsageLog.tenant_id == tenant_id, ApiUsageLog.ts >= since)
                .count()
            )

    def list_api_usage(
        self,
        *,
        tenant_id: str | None = None,
        since: datetime | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        with self.session() as sess:
            stmt = select(ApiUsageLog).order_by(desc(ApiUsageLog.ts))
            if tenant_id:
                stmt = stmt.where(ApiUsageLog.tenant_id == tenant_id)
            if since:
                stmt = stmt.where(ApiUsageLog.ts >= since)
            stmt = stmt.limit(limit)
            rows = sess.execute(stmt).scalars().all()
            return [
                {
                    "id": r.id,
                    "tenant_id": r.tenant_id,
                    "endpoint": r.endpoint,
                    "latency_ms": r.latency_ms,
                    "status_code": r.status_code,
                    "ts": r.ts.isoformat() if r.ts else None,
                }
                for r in rows
            ]

    def usage_summary_by_tenant(
        self, *, since: datetime | None = None
    ) -> list[dict[str, Any]]:
        from sqlalchemy import func
        with self.session() as sess:
            q = sess.query(
                ApiUsageLog.tenant_id,
                func.count(ApiUsageLog.id).label("calls"),
                func.coalesce(func.avg(ApiUsageLog.latency_ms), 0).label("avg_latency_ms"),
            )
            if since:
                q = q.filter(ApiUsageLog.ts >= since)
            q = q.group_by(ApiUsageLog.tenant_id)
            out = []
            for tid, calls, avg_lat in q.all():
                out.append(
                    {
                        "tenant_id": tid,
                        "calls": int(calls or 0),
                        "avg_latency_ms": float(avg_lat or 0.0),
                    }
                )
            return out

    def log_audit(
        self,
        *,
        tenant_id: str,
        action: str,
        detail: str | None = None,
        ts: datetime | None = None,
    ) -> int:
        with self.session() as sess:
            row = AuditLog(
                tenant_id=tenant_id,
                action=action,
                detail=detail,
                ts=ts or datetime.utcnow(),
            )
            sess.add(row)
            sess.flush()
            return row.id

    def list_audit(
        self,
        *,
        tenant_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        with self.session() as sess:
            stmt = select(AuditLog).order_by(desc(AuditLog.ts))
            if tenant_id:
                stmt = stmt.where(AuditLog.tenant_id == tenant_id)
            stmt = stmt.limit(limit)
            rows = sess.execute(stmt).scalars().all()
            return [
                {
                    "id": r.id,
                    "tenant_id": r.tenant_id,
                    "action": r.action,
                    "detail": r.detail,
                    "ts": r.ts.isoformat() if r.ts else None,
                }
                for r in rows
            ]


# --------------------------------------------------------------------------- #
# Row adapters                                                                #
# --------------------------------------------------------------------------- #


def _tenant_to_dict(row: Tenant | None) -> dict[str, Any]:
    if row is None:
        return {}
    return {
        "tenant_id": row.tenant_id,
        "display_name": row.display_name,
        "api_key_hash": row.api_key_hash,
        "plan": row.plan,
        "rate_limit_per_min": row.rate_limit_per_min,
        "enabled": bool(row.enabled),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _obs_to_dict(row: Observation) -> dict[str, Any]:
    return {
        "source_id": row.source_id,
        "series_id": row.series_id,
        "date": row.date,
        "value": row.value,
        "unit": row.unit,
        "meta": row.meta,
        "fetched_at": row.fetched_at.isoformat() if row.fetched_at else None,
        "owner_id": row.owner_id,
    }


def _filing_to_dict(row: Filing) -> dict[str, Any]:
    return {
        "source_id": row.source_id,
        "corp_code": row.corp_code,
        "receipt_no": row.receipt_no,
        "filed_at": row.filed_at,
        "type": row.type,
        "summary": row.summary,
        "meta": row.meta,
        "fetched_at": row.fetched_at.isoformat() if row.fetched_at else None,
        "owner_id": row.owner_id,
    }


def _ohlcv_to_dict(row: OHLCV) -> dict[str, Any]:
    return {
        "symbol": row.symbol,
        "market": row.market,
        "date": row.date,
        "open": row.open,
        "high": row.high,
        "low": row.low,
        "close": row.close,
        "volume": row.volume,
        "adj_close": row.adj_close,
        "source_id": row.source_id,
        "owner_id": row.owner_id,
    }


def _fin_to_dict(row: FinancialPIT) -> dict[str, Any]:
    return {
        "corp_code": row.corp_code,
        "symbol": row.symbol,
        "report_date": row.report_date,
        "period_end": row.period_end,
        "period_type": row.period_type,
        "revenue": row.revenue,
        "operating_income": row.operating_income,
        "net_income": row.net_income,
        "total_assets": row.total_assets,
        "total_liabilities": row.total_liabilities,
        "total_equity": row.total_equity,
        "raw": row.raw,
        "source_id": row.source_id,
        "owner_id": row.owner_id,
    }
