"""Authentication acceptance using one isolated administrator per database."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
import sys
from threading import Barrier
from types import SimpleNamespace

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import IntegrityError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import Settings
from app.database import create_database
from app.models import AuditLog, LoginSession, User, utcnow
from app.security import hash_password, token_hash

USERNAME = "isolated_auth_admin"
PASSWORD = "Stage-three-isolated-test-only-2026!"


@pytest.fixture(scope="session")
def admin_password_hash():
    # Reuse a test hash to avoid hashing five demo accounts for each acceptance case.
    return hash_password(PASSWORD)


@pytest.fixture
def auth_system(tmp_path, monkeypatch, admin_password_hash):
    database_url = "sqlite:///" + (tmp_path / "isolated_auth.db").as_posix()
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.attributes["database_url"] = database_url
    command.upgrade(config, "head")
    engine, Session = create_database(database_url)
    try:
        with Session.begin() as session:
            session.add(User(username=USERNAME, display_name="认证验收测试管理员", password_hash=admin_password_hash, role="admin", is_active=True))
    finally:
        engine.dispose()
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "reports"))
    from app.main import create_app

    settings = Settings(_env_file=None, database_url=database_url, data_dir=tmp_path, cookie_secure=False, allowed_origins="http://testserver")
    app = create_app(settings=settings)
    try:
        with TestClient(app) as client:
            yield SimpleNamespace(app=app, client=client, settings=settings)
    finally:
        app.state.engine.dispose()


def login(system, password=PASSWORD, username=USERNAME):
    return system.client.post("/api/auth/login", json={"username": username, "password": password})


def test_correct_login_stores_password_and_session_as_hashes(auth_system):
    assert auth_system.client.get("/api/packages").status_code == 401
    response = login(auth_system)
    assert response.status_code == 200
    assert response.json()["user"]["username"] == USERNAME
    assert "password_hash" not in response.json()["user"]
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "path=/api" in cookie
    raw_token = auth_system.client.cookies.get(auth_system.settings.session_cookie)
    with auth_system.app.state.Session() as session:
        user = session.scalar(select(User).where(User.username == USERNAME))
        assert user.password_hash != PASSWORD and user.password_hash.startswith("scrypt$")
        record = session.scalar(select(LoginSession))
        assert record.token_hash == token_hash(raw_token)
        assert record.token_hash != raw_token
    assert auth_system.client.get("/api/packages").status_code == 200


def test_wrong_password_and_missing_user_have_same_generic_failure(auth_system):
    wrong = login(auth_system, password="Wrong-isolated-test-password")
    missing = login(auth_system, username="does_not_exist_test_user")
    assert wrong.status_code == missing.status_code == 401
    assert wrong.json() == missing.json() == {"detail": "用户名或密码不正确"}
    assert "set-cookie" not in wrong.headers and "set-cookie" not in missing.headers
    with auth_system.app.state.Session() as session:
        assert session.scalar(select(func.count()).select_from(LoginSession)) == 0


def test_session_refresh_and_server_restart_keep_committed_session(auth_system):
    response = login(auth_system)
    assert response.status_code == 200
    first_me = auth_system.client.get("/api/auth/me")
    second_me = auth_system.client.get("/api/auth/me")
    assert first_me.status_code == second_me.status_code == 200
    assert first_me.json() == second_me.json() == response.json()
    raw_token = auth_system.client.cookies.get(auth_system.settings.session_cookie)
    from app.main import create_app

    restarted = create_app(settings=auth_system.settings)
    try:
        with TestClient(restarted) as client:
            restored = client.get("/api/auth/me", headers={"Cookie": auth_system.settings.session_cookie + "=" + raw_token})
            assert restored.status_code == 200
            assert restored.json() == response.json()
    finally:
        restarted.state.engine.dispose()


def test_logout_csrf_origin_and_old_cookie_replay(auth_system):
    response = login(auth_system)
    raw_token = auth_system.client.cookies.get(auth_system.settings.session_cookie)
    csrf = response.json()["csrf_token"]
    assert auth_system.client.post("/api/auth/logout").status_code == 403
    assert auth_system.client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf, "Origin": "https://untrusted.example"}).status_code == 403
    logout = auth_system.client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf})
    assert logout.status_code == 200
    assert auth_system.client.get("/api/auth/me").status_code == 401
    assert auth_system.client.get("/api/auth/me", headers={"Cookie": auth_system.settings.session_cookie + "=" + raw_token}).status_code == 401
    with auth_system.app.state.Session() as session:
        assert session.scalar(select(func.count()).select_from(LoginSession)) == 0


@pytest.mark.parametrize("invalid_state", ["expired", "inactive"])
def test_expired_session_or_inactive_user_is_rejected(auth_system, invalid_state):
    assert login(auth_system).status_code == 200
    with auth_system.app.state.Session.begin() as session:
        if invalid_state == "expired":
            record = session.scalar(select(LoginSession))
            record.expires_at = utcnow() - timedelta(seconds=1)
        else:
            session.scalar(select(User)).is_active = False
    assert auth_system.client.get("/api/auth/me").status_code == 401
    if invalid_state == "inactive":
        rejected = login(auth_system)
        assert rejected.status_code == 401
        assert rejected.json() == {"detail": "用户名或密码不正确"}
        assert "set-cookie" not in rejected.headers


@pytest.mark.parametrize("body", [
    {"username": "", "password": "Invalid-input-secret-marker"},
    {"username": USERNAME, "password": "Invalid-input-secret-marker" * 50},
    {"username": USERNAME, "password": "Invalid-input-secret-marker", "unexpected": "private-field"},
])
def test_invalid_login_input_returns_422_without_echoing_secrets(auth_system, body):
    response = auth_system.client.post("/api/auth/login", json=body)
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], str)
    assert "Invalid-input-secret-marker" not in response.text and "private-field" not in response.text
    assert "set-cookie" not in response.headers


def test_cookie_secure_flag_is_configurable_for_https(auth_system):
    from app.main import create_app

    secure = create_app(settings=auth_system.settings.model_copy(update={"cookie_secure": True}))
    try:
        with TestClient(secure, base_url="https://testserver") as client:
            response = client.post("/api/auth/login", json={"username": USERNAME, "password": PASSWORD})
            assert response.status_code == 200
            assert "; secure" in response.headers["set-cookie"].lower()
            assert client.get("/api/auth/me").status_code == 200
    finally:
        secure.state.engine.dispose()


def test_login_transaction_failure_creates_neither_session_nor_cookie(auth_system, caplog):
    secret_marker = "transaction-private-test-marker"

    def reject_login_audit(mapper, connection, target):
        if target.action == "auth.login":
            raise IntegrityError("private-login-insert", {"secret": secret_marker}, Exception(secret_marker))

    event.listen(AuditLog, "before_insert", reject_login_audit)
    try:
        response = login(auth_system)
    finally:
        event.remove(AuditLog, "before_insert", reject_login_audit)
    assert response.status_code == 503
    assert "set-cookie" not in response.headers
    assert secret_marker not in response.text + caplog.text
    assert "private-login-insert" not in response.text + caplog.text
    with auth_system.app.state.Session() as session:
        assert session.scalar(select(func.count()).select_from(LoginSession)) == 0
        assert session.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "auth.login")) == 0
    assert auth_system.app.state.engine.pool.checkedout() == 0


def test_password_reset_finishes_before_old_verified_login_creates_session(auth_system, monkeypatch):
    import app.cli as cli
    import app.main as main
    verified = Barrier(2)
    reset_finished = Barrier(2)
    original_verify = main.verify_password
    new_password = "New-stage-three-isolated-test-password!"

    def pause_old_verification(password, stored):
        valid = original_verify(password, stored)
        if password == PASSWORD and valid:
            verified.wait(timeout=15)
            reset_finished.wait(timeout=15)
        return valid

    monkeypatch.setattr(main, "verify_password", pause_old_verification)
    monkeypatch.setattr(cli, "get_settings", lambda: auth_system.settings)
    monkeypatch.setenv("AUTH_RESET_TEST_PASSWORD", new_password)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending_login = pool.submit(login, auth_system)
        verified.wait(timeout=15)
        try:
            assert cli.main(["set-password", "--username", USERNAME, "--password-env", "AUTH_RESET_TEST_PASSWORD"]) == 0
        finally:
            reset_finished.wait(timeout=15)
        response = pending_login.result(timeout=15)
    assert response.status_code == 401
    assert response.json() == {"detail": "用户名或密码不正确"}
    assert "set-cookie" not in response.headers
    with auth_system.app.state.Session() as session:
        assert session.scalar(select(func.count()).select_from(LoginSession)) == 0
    assert login(auth_system, password=new_password).status_code == 200
