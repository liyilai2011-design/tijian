from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


def create_database(database_url: str):
    url = make_url(database_url)
    options = {"pool_pre_ping": True, "echo": False, "hide_parameters": True}
    if url.drivername.startswith("sqlite"):
        options["connect_args"] = {"check_same_thread": False, "timeout": 15}
        if not url.database or url.database == ":memory:":
            options["poolclass"] = StaticPool
        else:
            Path(url.database).resolve().parent.mkdir(parents=True, exist_ok=True)
    else:
        options.update(pool_size=10, max_overflow=10, pool_timeout=15)
    engine = create_engine(database_url, **options)
    if url.drivername.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def sqlite_config(connection, record):
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=15000")
            cursor.close()
    return engine, sessionmaker(bind=engine, expire_on_commit=False)
