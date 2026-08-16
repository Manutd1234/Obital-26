from __future__ import annotations

import os
import logging
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

logger = logging.getLogger(__name__)

_BACKEND_DIR = Path(__file__).resolve().parents[2]

RAW_LOCAL_DATABASE_URL = os.getenv("LOCAL_DATABASE_URL", "").strip()
RAW_DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
RAW_DATABASE_MIGRATION_URL = os.getenv("DATABASE_MIGRATION_URL", "").strip()
FORCE_CLOUD_DB = os.getenv("OBITAL_FORCE_CLOUD_DB", "").strip().lower() in {"1", "true", "yes"}
AUTO_CREATE_SCHEMA = os.getenv("DB_AUTO_CREATE_SCHEMA", "true").strip().lower() in {"1", "true", "yes"}
VERBOSE_DB_HEALTH_ERRORS = os.getenv("DB_HEALTH_VERBOSE_ERRORS", "").strip().lower() in {"1", "true", "yes"}


def _normalize_database_url(url: str) -> str:
    # Ensure SQLAlchemy uses psycopg3 for Postgres URLs copied from cloud dashboards.
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://") :]
    return url


# LOCAL_DATABASE_URL takes precedence for local mode (SQLite).
# DATABASE_URL is the cloud DB (Postgres) and also the sync target when in SQLite mode.
LOCAL_DATABASE_URL = "" if FORCE_CLOUD_DB else _normalize_database_url(RAW_LOCAL_DATABASE_URL)
DATABASE_URL = _normalize_database_url(RAW_DATABASE_URL)
DATABASE_MIGRATION_URL = _normalize_database_url(RAW_DATABASE_MIGRATION_URL or RAW_DATABASE_URL)

# The "effective" database URL is local SQLite when configured; DATABASE_URL remains
# available as the cloud sync target in that mode.
EFFECTIVE_DATABASE_URL = LOCAL_DATABASE_URL or DATABASE_URL


class Base(DeclarativeBase):
    pass


def _is_sqlite_url(url: str) -> bool:
    return url.startswith("sqlite")


def _create_engine(url: str):
    if not url:
        return None

    if _is_sqlite_url(url):
        eng = create_engine(
            url,
            future=True,
            connect_args={"check_same_thread": False},
        )

        @event.listens_for(eng, "connect")
        def _set_sqlite_pragma(dbapi_conn, _connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

        return eng

    # Supabase transaction pooler mode (:6543).
    # A small local pool reuses TCP connections across transactions within
    # the same serverless instance; PgBouncer handles actual multiplexing.
    # Bootstrap sync can open 2-3 overlapping sessions, so keep headroom.
    # Server-side prepared statements must remain disabled.
    if "pooler.supabase.com:6543" in url:
        return create_engine(
            url,
            future=True,
            pool_size=3,
            max_overflow=2,
            pool_pre_ping=True,
            connect_args={"prepare_threshold": None},
        )

    return create_engine(url, future=True, pool_pre_ping=True)


engine = _create_engine(EFFECTIVE_DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True) if engine else None


# --- Cloud DB engine (for sync between local SQLite and remote Postgres) ---
# When in SQLite mode, DATABASE_URL is the cloud/sync target.
# When EFFECTIVE_DATABASE_URL == DATABASE_URL (Postgres-only mode), reuse the
# same engine to avoid two separate pools competing for the same DB.
if DATABASE_URL and DATABASE_URL != EFFECTIVE_DATABASE_URL:
    _cloud_engine = _create_engine(DATABASE_URL)
else:
    _cloud_engine = engine
CloudSessionLocal = sessionmaker(bind=_cloud_engine, autoflush=False, autocommit=False, future=True) if _cloud_engine else None


def db_enabled() -> bool:
    return engine is not None and SessionLocal is not None


def cloud_db_enabled() -> bool:
    """True when a separate cloud DB is available for sync (SQLite mode + DATABASE_URL set)."""
    return _cloud_engine is not None and CloudSessionLocal is not None


def is_sqlite_mode() -> bool:
    return db_enabled() and bool(EFFECTIVE_DATABASE_URL) and _is_sqlite_url(EFFECTIVE_DATABASE_URL)


def is_stateless_db_mode() -> bool:
    if not db_enabled():
        return False
    if not EFFECTIVE_DATABASE_URL:
        return False
    # SQLite mode is opt-in: only Postgres triggers the "stateless" DB path.
    # Use is_db_mode() for any DB (including SQLite).
    if _is_sqlite_url(EFFECTIVE_DATABASE_URL):
        return False
    return True


def is_db_mode() -> bool:
    """True when any database is configured (SQLite or Postgres)."""
    return db_enabled() and bool(EFFECTIVE_DATABASE_URL)


def init_db() -> None:
    if not db_enabled():
        return
    if not AUTO_CREATE_SCHEMA:
        return
    if not is_sqlite_mode():
        return
    from backend.app.services import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _ensure_runtime_schema(engine)


def _ensure_runtime_schema(target_engine) -> None:
    """Add missing columns to existing tables so that model changes are reflected
    in SQLite databases that were created by an older build. Only runs in SQLite
    mode; Alembic handles Postgres schema evolution."""
    if not is_sqlite_mode():
        return
    inspector = inspect(target_engine)
    table_names = set(inspector.get_table_names())

    with target_engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in table_names:
                continue
            existing_columns = {col["name"] for col in inspector.get_columns(table.name)}
            for col in table.columns:
                if col.name not in existing_columns:
                    col_type = _render_sqlite_column_type(col)
                    nullable = "" if col.nullable else " NOT NULL"
                    default_clause = ""
                    if col.default and col.default.arg is not None:
                        default_clause = f" DEFAULT {_render_sqlite_default(col.default.arg)}"
                    conn.execute(
                        text(f"ALTER TABLE {table.name} ADD COLUMN {col.name} {col_type}{nullable}{default_clause}")
                    )
        _repair_runtime_data(conn, table_names)
        _ensure_runtime_indexes(conn)


def _repair_runtime_data(conn, table_names: set[str]) -> None:
    """Repair old SQLite rows that used empty strings in datetime columns."""
    repair_targets: dict[str, dict[str, str]] = {
        "chat_sessions": {
            "created_at": "CURRENT_TIMESTAMP",
            "updated_at": "COALESCE(NULLIF(created_at, ''), CURRENT_TIMESTAMP)",
        },
        "chat_messages": {
            "created_at": "CURRENT_TIMESTAMP",
            "updated_at": "COALESCE(NULLIF(created_at, ''), CURRENT_TIMESTAMP)",
        },
        "reward_sessions": {
            "created_at": "CURRENT_TIMESTAMP",
            "updated_at": "COALESCE(NULLIF(created_at, ''), CURRENT_TIMESTAMP)",
        },
        "reward_events": {
            "timestamp": "CURRENT_TIMESTAMP",
            "updated_at": "COALESCE(NULLIF(timestamp, ''), CURRENT_TIMESTAMP)",
        },
        "bin_items": {
            "deleted_at": "CURRENT_TIMESTAMP",
            "expires_at": "CURRENT_TIMESTAMP",
        },
        "account_unlock_sessions": {
            "expires_at": "CURRENT_TIMESTAMP",
            "created_at": "CURRENT_TIMESTAMP",
        },
    }
    for table, columns in repair_targets.items():
        if table not in table_names:
            continue
        for column, replacement in columns.items():
            conn.execute(text(f"UPDATE {table} SET {column} = {replacement} WHERE {column} = ''"))


def _ensure_runtime_indexes(conn) -> None:
    """Create runtime indexes for local SQLite databases made by older builds."""
    index_statements = [
        "CREATE INDEX IF NOT EXISTS ix_account_unlock_sessions_expires_at ON account_unlock_sessions (expires_at)",
        "CREATE INDEX IF NOT EXISTS ix_account_unlock_sessions_account_expires_created ON account_unlock_sessions (account_id, expires_at, created_at)",
        "CREATE INDEX IF NOT EXISTS ix_account_unlock_sessions_account_created_at ON account_unlock_sessions (account_id, created_at)",
        "CREATE INDEX IF NOT EXISTS ix_account_artifacts_deleted_at ON account_artifacts (deleted_at)",
        "CREATE INDEX IF NOT EXISTS ix_account_artifacts_expires_at ON account_artifacts (expires_at)",
        "CREATE INDEX IF NOT EXISTS ix_account_artifacts_account_deleted ON account_artifacts (account_id, deleted_at)",
        "CREATE INDEX IF NOT EXISTS ix_account_artifacts_account_expires ON account_artifacts (account_id, expires_at)",
        "CREATE INDEX IF NOT EXISTS ix_account_artifacts_hydration_queue ON account_artifacts (account_id, hydration_status, size_bytes, updated_at)",
        "CREATE INDEX IF NOT EXISTS ix_account_artifacts_hydration_queue_ordered ON account_artifacts (account_id, hydration_status, size_bytes, updated_at DESC)",
        "CREATE INDEX IF NOT EXISTS ix_bin_items_expires_at ON bin_items (expires_at)",
        "CREATE INDEX IF NOT EXISTS ix_bin_items_account_deleted_at ON bin_items (account_id, deleted_at)",
        "CREATE INDEX IF NOT EXISTS ix_bin_items_account_expires_at ON bin_items (account_id, expires_at)",
        "CREATE INDEX IF NOT EXISTS ix_chat_sessions_account_created_at ON chat_sessions (account_id, created_at)",
        "CREATE INDEX IF NOT EXISTS ix_chat_sessions_account_updated_at ON chat_sessions (account_id, updated_at)",
        "CREATE INDEX IF NOT EXISTS ix_reward_sessions_account_created_at ON reward_sessions (account_id, created_at)",
        "CREATE INDEX IF NOT EXISTS ix_rate_limit_buckets_updated_at ON rate_limit_buckets (updated_at)",
    ]
    for statement in index_statements:
        conn.execute(text(statement))


def _render_sqlite_column_type(col) -> str:
    t = str(col.type).upper()
    if "VARCHAR" in t or "STRING" in t:
        return "TEXT"
    if "DATETIME" in t or "TIMESTAMP" in t:
        return "TIMESTAMP"
    if "INTEGER" in t or "BIGINT" in t:
        return "INTEGER"
    if "BLOB" in t or "BINARY" in t:
        return "BLOB"
    if "BOOLEAN" in t:
        return "INTEGER"
    return "TEXT"


def _render_sqlite_default(value) -> str:
    if isinstance(value, str):
        return f"'{value}'"
    if callable(value):
        return "''"
    return str(value)


def db_healthcheck() -> dict[str, object]:
    if not db_enabled():
        return {"enabled": False, "ok": False, "detail": "DATABASE_URL or LOCAL_DATABASE_URL not set"}
    assert engine is not None
    try:
        with engine.connect() as conn:
            value = conn.exec_driver_sql("select 1").scalar_one()
        return {"enabled": True, "ok": value == 1, "detail": "connected"}
    except Exception as exc:
        if VERBOSE_DB_HEALTH_ERRORS:
            return {"enabled": True, "ok": False, "detail": f"{type(exc).__name__}: {exc}"}
        return {"enabled": True, "ok": False, "detail": "connection_failed"}


def dispose_db_engines() -> None:
    if engine is not None:
        engine.dispose()
    if _cloud_engine is not None:
        _cloud_engine.dispose()
