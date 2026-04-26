"""SQLAlchemy ORM schema for the ky-platform store.

Design notes:

- ``owner_id`` is present on every user-facing table so we can scope per-tenant
  reads/writes. Default: ``"self"``.
- ``Observation`` is the generic time-series row (macro series). ``series_id``
  is source-specific; (``source_id``, ``series_id``, ``date``, ``owner_id``) is
  the natural key.
- ``Filing`` mirrors OpenDART rows but generalises to any filing source.
- ``Universe`` is a stub for ticker metadata (populated once KIS is live).
- ``OHLCV`` carries daily price/volume for any symbol (KR + US + ...).
- ``FinancialPIT`` carries point-in-time quarterly/annual fundamentals.
- ``meta`` columns are JSON blobs for source-specific fields we don't want to
  promote to first-class columns.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


# --------------------------------------------------------------------------- #
# Observations — macro time-series                                            #
# --------------------------------------------------------------------------- #


class Observation(Base):
    __tablename__ = "observations"
    __table_args__ = (
        UniqueConstraint(
            "source_id", "series_id", "date", "owner_id",
            name="uq_observations_src_series_date_owner",
        ),
        Index("ix_observations_source_series", "source_id", "series_id"),
        Index("ix_observations_date", "date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(String(32), nullable=False)
    series_id: Mapped[str] = mapped_column(String(128), nullable=False)
    date: Mapped[str] = mapped_column(String(10), nullable=False)  # ISO YYYY-MM-DD
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    owner_id: Mapped[str] = mapped_column(String(32), nullable=False, default="self")


# --------------------------------------------------------------------------- #
# Filings — DART disclosures                                                  #
# --------------------------------------------------------------------------- #


class Filing(Base):
    __tablename__ = "filings"
    __table_args__ = (
        UniqueConstraint(
            "source_id", "receipt_no", "owner_id",
            name="uq_filings_src_receipt_owner",
        ),
        Index("ix_filings_corp_code", "corp_code"),
        Index("ix_filings_filed_at", "filed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(String(32), nullable=False)
    corp_code: Mapped[str] = mapped_column(String(32), nullable=False)
    receipt_no: Mapped[str] = mapped_column(String(32), nullable=False)
    filed_at: Mapped[str] = mapped_column(String(10), nullable=False)
    type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    owner_id: Mapped[str] = mapped_column(String(32), nullable=False, default="self")


# --------------------------------------------------------------------------- #
# Universe — ticker master                                                    #
# --------------------------------------------------------------------------- #


class Universe(Base):
    __tablename__ = "universe"
    __table_args__ = (
        UniqueConstraint("ticker", "market", "owner_id", name="uq_universe_ticker_market_owner"),
        Index("ix_universe_ticker", "ticker"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(32), nullable=False)
    market: Mapped[str] = mapped_column(String(16), nullable=False)  # KOSPI/KOSDAQ/NASDAQ/...
    name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sector: Mapped[str | None] = mapped_column(String(64), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_etf: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    owner_id: Mapped[str] = mapped_column(String(32), nullable=False, default="self")


# --------------------------------------------------------------------------- #
# OHLCV — daily price/volume bars                                             #
# --------------------------------------------------------------------------- #


class OHLCV(Base):
    __tablename__ = "ohlcv"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "symbol", "date",
            name="uq_ohlcv_owner_symbol_date",
        ),
        Index("ix_ohlcv_date_symbol", "date", "symbol"),
        Index("ix_ohlcv_symbol_date", "symbol", "date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)       # "005930"
    market: Mapped[str] = mapped_column(String(16), nullable=False, default="UNKNOWN")
    date: Mapped[str] = mapped_column(String(10), nullable=False)          # ISO YYYY-MM-DD
    open: Mapped[float | None] = mapped_column(Float, nullable=True)
    high: Mapped[float | None] = mapped_column(Float, nullable=True)
    low: Mapped[float | None] = mapped_column(Float, nullable=True)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    adj_close: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_id: Mapped[str] = mapped_column(String(32), nullable=False)      # "company_credit" | "quant_platform"
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    owner_id: Mapped[str] = mapped_column(String(32), nullable=False, default="self")


# --------------------------------------------------------------------------- #
# FinancialsPIT — point-in-time quarterly/annual fundamentals                 #
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# FnguideSnapshot — per-symbol fundamentals snapshot (Naver + FnGuide)        #
# --------------------------------------------------------------------------- #


class FnguideSnapshot(Base):
    __tablename__ = "fnguide_snapshots"
    __table_args__ = (
        UniqueConstraint("symbol", "owner_id", name="uq_fnguide_symbol_owner"),
        Index("ix_fnguide_symbol", "symbol"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)   # JSON
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    degraded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    owner_id: Mapped[str] = mapped_column(String(32), nullable=False, default="self")


# --------------------------------------------------------------------------- #
# FinancialStatementDB — per-account quarterly/annual line items               #
# (migrated from Company_Credit Naver full-collection)                         #
# --------------------------------------------------------------------------- #


class FinancialStatementDB(Base):
    __tablename__ = "financial_statements_db"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "symbol", "period", "period_type", "account_name", "source_id",
            name="uq_fin_stmt_db_owner_sym_period_acct_src",
        ),
        Index("ix_fin_stmt_db_symbol_period", "symbol", "period"),
        Index("ix_fin_stmt_db_symbol_period_type", "symbol", "period_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    period: Mapped[str] = mapped_column(String(16), nullable=False)       # "2024", "2024Q3", ...
    period_type: Mapped[str] = mapped_column(String(16), nullable=False)  # "annual" | "quarterly"
    account_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    account_name: Mapped[str] = mapped_column(String(255), nullable=False)
    account_level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    yoy: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_estimate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_id: Mapped[str] = mapped_column(String(32), nullable=False, default="naver_comp")
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    owner_id: Mapped[str] = mapped_column(String(32), nullable=False, default="self")


# --------------------------------------------------------------------------- #
# FinancialSegment — DART 사업부문별 매출/이익 breakdown                       #
# --------------------------------------------------------------------------- #


class FinancialSegment(Base):
    __tablename__ = "financial_segments"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "symbol", "period_end", "segment_name", "source_id",
            name="uq_fin_seg_owner_sym_period_name_src",
        ),
        Index("ix_fin_seg_symbol_period", "symbol", "period_end"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    corp_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    period_end: Mapped[str] = mapped_column(String(10), nullable=False)  # ISO
    period_type: Mapped[str] = mapped_column(String(8), nullable=False, default="FY")
    segment_name: Mapped[str] = mapped_column(String(255), nullable=False)
    revenue: Mapped[float | None] = mapped_column(Float, nullable=True)
    operating_income: Mapped[float | None] = mapped_column(Float, nullable=True)
    revenue_share: Mapped[float | None] = mapped_column(Float, nullable=True)  # 0..1
    source_id: Mapped[str] = mapped_column(String(32), nullable=False, default="dart")
    raw: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON blob
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    owner_id: Mapped[str] = mapped_column(String(32), nullable=False, default="self")


# --------------------------------------------------------------------------- #
# DividendHistory — DART-derived per-share DPS history                        #
# --------------------------------------------------------------------------- #


class DividendHistory(Base):
    __tablename__ = "dividend_history"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "symbol", "period_end", "share_type", "source_id",
            name="uq_div_hist_owner_sym_period_type_src",
        ),
        Index("ix_div_hist_symbol_period", "symbol", "period_end"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    corp_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    period_end: Mapped[str] = mapped_column(String(10), nullable=False)  # ISO year-end
    share_type: Mapped[str] = mapped_column(String(16), nullable=False, default="common")
    dps: Mapped[float | None] = mapped_column(Float, nullable=True)            # 주당 배당금 (KRW)
    payout_total: Mapped[float | None] = mapped_column(Float, nullable=True)   # 총 배당금
    payout_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)   # 배당성향 (%)
    dividend_yield: Mapped[float | None] = mapped_column(Float, nullable=True) # 시가배당률 (%)
    source_id: Mapped[str] = mapped_column(String(32), nullable=False, default="dart")
    raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    owner_id: Mapped[str] = mapped_column(String(32), nullable=False, default="self")


# --------------------------------------------------------------------------- #
# Tenant — multi-tenant control plane (API keys, plan, rate limit)            #
# --------------------------------------------------------------------------- #


class Tenant(Base):
    __tablename__ = "tenants"
    __table_args__ = (
        UniqueConstraint("api_key_hash", name="uq_tenants_api_key_hash"),
        Index("ix_tenants_enabled", "enabled"),
    )

    # ``tenant_id`` doubles as ``owner_id`` for the data tables. Stored as the
    # primary key so the relational guarantees follow naturally.
    tenant_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    api_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)   # SHA-256 hex
    plan: Mapped[str] = mapped_column(String(16), nullable=False, default="self")
    rate_limit_per_min: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )


# --------------------------------------------------------------------------- #
# ApiUsageLog — per-request latency + status + tenant for billing dashboards  #
# --------------------------------------------------------------------------- #


class ApiUsageLog(Base):
    __tablename__ = "api_usage_log"
    __table_args__ = (
        Index("ix_api_usage_tenant_ts", "tenant_id", "ts"),
        Index("ix_api_usage_ts", "ts"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(32), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(255), nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False, default=200)
    ts: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )


# --------------------------------------------------------------------------- #
# AuditLog — sensitive action trail (orders, config changes, admin ops)       #
# --------------------------------------------------------------------------- #


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_tenant_ts", "tenant_id", "ts"),
        Index("ix_audit_action", "action"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)   # JSON string
    ts: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )


class FinancialPIT(Base):
    __tablename__ = "financials_pit"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "symbol", "period_end", "period_type", "source_id",
            name="uq_fin_pit_owner_sym_period_type_src",
        ),
        Index("ix_fin_pit_symbol_period", "symbol", "period_end"),
        Index("ix_fin_pit_report_date", "report_date"),
        # _load_eps_signals filters WHERE period_type IN ('FY','Q4') AND period_end <= ?.
        # A leading period_type index lets SQLite seek instead of scanning the
        # whole table (~100k rows).
        Index("ix_fin_pit_period_type_end", "period_type", "period_end"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    corp_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    report_date: Mapped[str | None] = mapped_column(String(10), nullable=True)     # 공시일 (PIT)
    period_end: Mapped[str] = mapped_column(String(10), nullable=False)            # 회계기간 종료
    period_type: Mapped[str] = mapped_column(String(8), nullable=False)            # "Q1" | "Q2" | "Q3" | "Q4" | "FY"
    revenue: Mapped[float | None] = mapped_column(Float, nullable=True)
    operating_income: Mapped[float | None] = mapped_column(Float, nullable=True)
    net_income: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_assets: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_liabilities: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_equity: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw: Mapped[str | None] = mapped_column(Text, nullable=True)   # JSON string for extras
    source_id: Mapped[str] = mapped_column(String(32), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    owner_id: Mapped[str] = mapped_column(String(32), nullable=False, default="self")
