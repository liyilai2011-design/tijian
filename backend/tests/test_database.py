"""Isolated database acceptance checks; no demo accounts or deployment data."""
from pathlib import Path
import sys
from types import SimpleNamespace

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, inspect, select, text
from sqlalchemy.exc import IntegrityError, OperationalError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import Settings
from app.database import create_database
from app.models import User
from app.security import get_db


def migration_config(database_url):
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.attributes["database_url"] = database_url
    return config


@pytest.fixture
def database(tmp_path):
    url = "sqlite:///" + (tmp_path / "isolated_stage_two.db").as_posix()
    config = migration_config(url)
    command.upgrade(config, "head")
    engine, Session = create_database(url)
    try:
        yield SimpleNamespace(url=url, engine=engine, Session=Session, config=config, directory=tmp_path)
    finally:
        engine.dispose()


def make_user(username, **changes):
    values = dict(username=username, display_name="数据库验收用测试用户", password_hash="unusable-test-hash-marker", role="registrar", department_id=None)
    values.update(changes)
    return User(**values)


def test_migrated_user_create_read_update_survives_new_engine(database):
    assert "users" in inspect(database.engine).get_table_names()
    with database.Session.begin() as session:
        user = make_user("stage_two_persistent")
        session.add(user)
    saved_id = user.id
    with database.Session.begin() as session:
        saved = session.scalar(select(User).where(User.username == "stage_two_persistent"))
        assert saved.id == saved_id
        saved.display_name = "已更新的数据库测试用户"
    assert database.engine.pool.checkedout() == 0
    database.engine.dispose()
    # A separate engine/session factory simulates a new process starting on the file.
    restarted_engine, RestartedSession = create_database(database.url)
    try:
        with RestartedSession() as session:
            saved = session.get(User, saved_id)
            assert saved.username == "stage_two_persistent"
            assert saved.display_name == "已更新的数据库测试用户"
        assert restarted_engine.pool.checkedout() == 0
    finally:
        restarted_engine.dispose()


def test_unique_username_failure_can_rollback_and_continue(database):
    with database.Session.begin() as session:
        session.add(make_user("stage_two_unique"))
    with database.Session() as session:
        session.add(make_user("stage_two_unique", display_name="不允许覆盖原用户"))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        original = session.scalar(select(User).where(User.username == "stage_two_unique"))
        assert original.display_name == "数据库验收用测试用户"
        session.add(make_user("stage_two_after_rollback"))
        session.commit()
    with database.Session() as session:
        assert len(session.scalars(select(User)).all()) == 2
    assert database.engine.pool.checkedout() == 0


def test_user_department_foreign_key_rejects_missing_department(database):
    with database.Session() as session:
        assert session.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        session.add(make_user("stage_two_bad_department", role="doctor", department_id=99999))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        assert session.scalar(select(User).where(User.username == "stage_two_bad_department")) is None
    assert database.engine.pool.checkedout() == 0


def test_request_session_closes_and_rolls_back_after_success_or_failure(database):
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(Session=database.Session)))
    normal_dependency = get_db(request)
    normal_session = next(normal_dependency)
    assert normal_session.execute(text("SELECT 1")).scalar_one() == 1
    assert database.engine.pool.checkedout() == 1
    normal_dependency.close()
    assert database.engine.pool.checkedout() == 0

    failed_dependency = get_db(request)
    failed_session = next(failed_dependency)
    failed_session.add(make_user("stage_two_uncommitted"))
    failed_session.flush()
    with pytest.raises(RuntimeError, match="request aborted"):
        failed_dependency.throw(RuntimeError("request aborted"))
    assert database.engine.pool.checkedout() == 0
    with database.Session() as session:
        assert session.scalar(select(User).where(User.username == "stage_two_uncommitted")) is None


def test_database_api_error_hides_driver_details_and_releases_session(database, monkeypatch, caplog):
    monkeypatch.setenv("DATABASE_URL", database.url)
    monkeypatch.setenv("DATA_DIR", str(database.directory))
    monkeypatch.setenv("REPORTS_DIR", str(database.directory / "reports"))
    from app.main import create_app

    settings = Settings(_env_file=None, database_url=database.url, data_dir=database.directory, allowed_origins="http://testserver", cookie_secure=False)
    app = create_app(settings=settings)
    secret_marker = "driver-private-test-marker"
    sql_marker = "SELECT private_sql_test_marker FROM private_table"

    def unavailable(*args):
        raise OperationalError(sql_marker, {"password": secret_marker}, Exception(secret_marker))

    event.listen(app.state.engine, "before_cursor_execute", unavailable)
    try:
        with TestClient(app) as client:
            # The existing login route reaches the database before any password hashing.
            response = client.post("/api/auth/login", json={"username": "unavailable_test", "password": "temporary-test-input"})
            assert response.status_code == 503
            assert response.json() == {"detail": "数据尚未保存，请检查网络连接后重新尝试。服务器数据库暂不可用"}
        assert secret_marker not in response.text + caplog.text
        assert sql_marker not in response.text + caplog.text
        assert app.state.engine.pool.checkedout() == 0
    finally:
        event.remove(app.state.engine, "before_cursor_execute", unavailable)
        app.state.engine.dispose()


def test_failed_migration_releases_engine(database, monkeypatch):
    import app.database as database_module
    disposal_events = []
    event.listen(database.engine, "engine_disposed", lambda engine: disposal_events.append(True))
    monkeypatch.setattr(database_module, "create_database", lambda url: (database.engine, database.Session))

    def migration_failure(*args, **kwargs):
        raise RuntimeError("isolated migration failure")

    monkeypatch.setattr(MigrationContext, "run_migrations", migration_failure)
    with pytest.raises(RuntimeError, match="isolated migration failure"):
        command.upgrade(database.config, "head")
    assert disposal_events == [True]
    assert database.engine.pool.checkedout() == 0


def test_failed_cli_transaction_releases_engine(database, monkeypatch, capsys):
    import app.cli as cli
    disposal_events = []
    event.listen(database.engine, "engine_disposed", lambda engine: disposal_events.append(True))
    monkeypatch.setattr(cli, "get_settings", lambda: Settings(_env_file=None, database_url=database.url, data_dir=database.directory))
    monkeypatch.setattr(cli, "migrate_database", lambda url: None)
    monkeypatch.setattr(cli, "read_password", lambda name: "unused-isolated-test-input")
    monkeypatch.setattr(cli, "create_database", lambda url: (database.engine, database.Session))
    # Existing validation deliberately fails inside the database transaction.
    result = cli.main(["create-user", "--username", "stage_two_cleanup", "--display-name", "", "--role", "registrar", "--password-env", "UNUSED_TEST_ENV"])
    assert result == 1
    output = capsys.readouterr()
    assert "账号已创建" not in output.out
    assert "显示名称" in output.err
    assert disposal_events == [True]
    assert database.engine.pool.checkedout() == 0
