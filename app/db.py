"""SQLAlchemy engine and session setup."""
from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, scoped_session, sessionmaker


class Base(DeclarativeBase):
    pass


Session = scoped_session(sessionmaker(expire_on_commit=False))
_engine = None


def init_engine(url: str):
    global _engine
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    _engine = create_engine(url, connect_args=connect_args)

    if url.startswith("sqlite"):
        @event.listens_for(_engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

    Session.remove()
    Session.configure(bind=_engine)
    from . import models  # noqa: F401  (register tables)

    Base.metadata.create_all(_engine)
    return _engine
