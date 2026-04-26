"""ky_core.storage — SQLite-backed persistence for collected data.

Exposes:

- :func:`get_engine`, :func:`get_session_factory` — SQLAlchemy plumbing
- ORM models: :class:`Observation`, :class:`Filing`, :class:`Universe`
- :class:`Repository` — CRUD wrapper with upsert helpers

The default DB path is ``~/.gazua/data/ky.db``. Overridable via
``KY_DB_PATH`` env var.
"""
from __future__ import annotations

from ky_core.storage.db import DEFAULT_DB_PATH, get_engine, get_session_factory, init_db
from ky_core.storage.repository import Repository
from ky_core.storage.schema import (
    Base,
    DividendHistory,
    Filing,
    FinancialPIT,
    FinancialSegment,
    FinancialStatementDB,
    FnguideSnapshot,
    Observation,
    OHLCV,
    Universe,
)

__all__ = [
    "Base",
    "DEFAULT_DB_PATH",
    "DividendHistory",
    "Filing",
    "FinancialPIT",
    "FinancialSegment",
    "FinancialStatementDB",
    "FnguideSnapshot",
    "Observation",
    "OHLCV",
    "Repository",
    "Universe",
    "get_engine",
    "get_session_factory",
    "init_db",
]
