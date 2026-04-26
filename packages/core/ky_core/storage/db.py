"""SQLAlchemy engine + session factory for the ky-platform SQLite store."""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

log = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path.home() / ".gazua" / "data" / "ky.db"


def _resolve_db_path() -> Path:
    override = os.environ.get("KY_DB_PATH")
    path = Path(override) if override else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _utf8_text_factory(raw):
    """Force UTF-8 decoding with replacement for any byte column.

    Defensive: SQLite's default ``text_factory`` already assumes UTF-8, but if
    a row is ever pushed with mixed cp949/cp1252 bytes (e.g. a Windows
    backfill script that wrote via raw ``sqlite3`` without explicit
    encoding), this keeps reads from crashing. Invalid bytes become U+FFFD
    replacements so the consumer sees a visible diagnostic rather than a
    :class:`UnicodeDecodeError`.
    """
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return raw


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    path = _resolve_db_path()
    url = f"sqlite:///{path.as_posix()}"
    # NullPool: every session opens its own SQLite connection and closes on
    # disposal. SQLite handles many concurrent *readers* cheaply and the default
    # QueuePool (size=5, overflow=10) gets exhausted quickly when the API runs
    # parallel page requests that each hold a session for several seconds —
    # the FastAPI thread-pool can fan out to ~40 workers. NullPool avoids the
    # QueuePool exhaustion without hurting correctness on file-backed SQLite.
    engine = create_engine(
        url,
        future=True,
        echo=False,
        poolclass=NullPool,
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    # Pin ``text_factory`` on every new DBAPI connection. Without this hook
    # each new sqlite3 connection inherits Python's default (already UTF-8),
    # but we want a single, explicit contract documented in one place — and a
    # ``errors='replace'`` fallback so one bad legacy row can't take the API
    # down with a 500.
    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn, _conn_record):  # pragma: no cover (trivial)
        try:
            dbapi_conn.text_factory = _utf8_text_factory
        except Exception:  # noqa: BLE001
            log.warning("text_factory set failed", exc_info=True)

    return engine


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False, future=True)


def init_db() -> None:
    """Create all tables if they don't exist."""
    from ky_core.storage.schema import Base  # local import to avoid cycles

    engine = get_engine()
    Base.metadata.create_all(engine)


def reset_engine_cache() -> None:
    """Test hook: clear memoised engine/session."""
    get_engine.cache_clear()
    get_session_factory.cache_clear()
