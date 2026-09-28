"""אתחול SQLite וניהול סשנים."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

import logging

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from .config import PATHS
from .models import Base

log = logging.getLogger("polixor.db")

_engine: Optional[Engine] = None
_SessionLocal: Optional[sessionmaker[Session]] = None


def _configure_sqlite(dbapi_conn, _record) -> None:
    """WAL + foreign keys: קריאה במקביל לכתיבה, חשוב לעיבוד ברקע."""
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA busy_timeout=10000")
    cur.close()


# עמודות שנוספו אחרי גרסאות קודמות. create_all יוצר טבלאות חדשות אבל
# אינו מוסיף עמודות לטבלה קיימת, ולכן מוסיפים אותן כאן במפורש כדי
# שמסד נתונים ישן ימשיך לעבוד אחרי שדרוג.
_ADDED_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "jobs": [
        ("live_state", "VARCHAR(16) DEFAULT 'idle'"),
        ("live_started_at", "DATETIME"),
        ("live_captured_seconds", "FLOAT DEFAULT 0"),
        ("live_reconnects", "INTEGER DEFAULT 0"),
        ("live_stop_requested", "BOOLEAN DEFAULT 0"),
        ("live_segments", "JSON DEFAULT '[]'"),
        ("live_error", "TEXT DEFAULT ''"),
        # פרויקטים (שדרוג 2)
        ("mode", "VARCHAR(16)"),
        ("phase", "VARCHAR(16) DEFAULT ''"),
        ("run_scope", "VARCHAR(16) DEFAULT 'all'"),
        ("ui_language", "VARCHAR(8) DEFAULT 'he'"),
        ("content_language", "VARCHAR(8) DEFAULT 'auto'"),
        ("project_config", "JSON DEFAULT '{}'"),
        ("analysis", "JSON"),
        ("error_data", "JSON DEFAULT '{}'"),
        ("updated_at", "DATETIME"),
    ],
}


def _migrate(engine: Engine) -> None:
    insp = inspect(engine)
    existing_tables = set(insp.get_table_names())
    with engine.begin() as conn:
        for table, columns in _ADDED_COLUMNS.items():
            if table not in existing_tables:
                continue
            have = {c["name"] for c in insp.get_columns(table)}
            for name, ddl in columns:
                if name in have:
                    continue
                try:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
                    log.info("migrated: %s.%s added", table, name)
                except OperationalError as exc:   # פועל כבר – לא קריטי
                    log.warning("migration skipped %s.%s: %s", table, name, exc)


def init_db(db_path: Optional[Path] = None) -> Engine:
    global _engine, _SessionLocal
    path = Path(db_path) if db_path else PATHS.db_path
    path.parent.mkdir(parents=True, exist_ok=True)

    _engine = create_engine(
        f"sqlite:///{path}",
        future=True,
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    event.listen(_engine, "connect", _configure_sqlite)
    Base.metadata.create_all(_engine)
    _migrate(_engine)
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        init_db()
    assert _engine is not None
    return _engine


@contextmanager
def session_scope() -> Iterator[Session]:
    """סשן עם commit/rollback אוטומטי."""
    if _SessionLocal is None:
        init_db()
    assert _SessionLocal is not None
    s = _SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_session() -> Session:
    """סשן לשימוש ב-Depends של FastAPI (נסגר על-ידי הקורא)."""
    if _SessionLocal is None:
        init_db()
    assert _SessionLocal is not None
    return _SessionLocal()


def db_dependency() -> Iterator[Session]:
    s = get_session()
    try:
        yield s
    finally:
        s.close()
