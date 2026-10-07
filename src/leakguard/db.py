"""Database engine and session setup.

SQLite today. Moving to Postgres means changing Settings.db_url only.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from leakguard.config import Settings, load_settings
from leakguard.models import Base

_engines: dict[str, Engine] = {}


def _enable_sqlite_foreign_keys(dbapi_connection, _record) -> None:  # noqa: ANN001
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def get_engine(settings: Settings | None = None) -> Engine:
    """Return a cached engine for the current mode, creating tables if needed."""
    settings = settings or load_settings()
    url = settings.db_url
    if url not in _engines:
        settings.db_path.parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url)
        if url.startswith("sqlite"):
            event.listen(engine, "connect", _enable_sqlite_foreign_keys)
        Base.metadata.create_all(engine)
        _engines[url] = engine
    return _engines[url]


def dispose_engine(settings: Settings) -> None:
    """Close the engine so the database file can be deleted (needed on Windows)."""
    engine = _engines.pop(settings.db_url, None)
    if engine is not None:
        engine.dispose()


def reset_database(settings: Settings) -> None:
    """Empty one mode's database. Drops and recreates every table rather than
    deleting the file, so it works while the dashboard has the file open."""
    engine = get_engine(settings)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    if engine.url.get_backend_name() == "sqlite":
        with engine.connect() as conn:
            conn.exec_driver_sql("VACUUM")


@contextmanager
def session_scope(settings: Settings | None = None) -> Iterator[Session]:
    """Session that commits on success and rolls back on error."""
    factory = sessionmaker(bind=get_engine(settings), expire_on_commit=False)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
