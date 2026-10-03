from alembic import context

from app.config import get_settings
from app.database import Base, create_database
from app import models  # noqa: F401 — registers the models

config = context.config
url = config.attributes.get("database_url") or get_settings().resolved_database_url
target_metadata = Base.metadata

if context.is_offline_mode():
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()
else:
    engine, _ = create_database(url)
    try:
        with engine.connect() as connection:
            if engine.dialect.name == "sqlite":
                # Parent-table nullable changes require a batch copy. Disable FK
                # checks before BEGIN, execute all DDL atomically, then verify
                # every reference before committing. Restore FK enforcement even
                # when migration or integrity verification fails.
                connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
                connection.commit()
                try:
                    with connection.begin():
                        connection.exec_driver_sql("BEGIN IMMEDIATE")
                        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True, render_as_batch=True, transactional_ddl=True)
                        with context.begin_transaction():
                            context.run_migrations()
                        if connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall():
                            raise RuntimeError("Migration foreign-key verification failed")
                finally:
                    connection.exec_driver_sql("PRAGMA foreign_keys=ON")
                    connection.commit()
            else:
                context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
                with context.begin_transaction():
                    context.run_migrations()
    finally:
        engine.dispose()
