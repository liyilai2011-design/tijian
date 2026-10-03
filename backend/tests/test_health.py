"""Base server checks do not require account or business-table initialization."""
from pathlib import Path
import sys

from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def make_app(tmp_path, monkeypatch):
    database_file = tmp_path / "uninitialized.db"
    database_url = "sqlite:///" + database_file.as_posix()
    # Avoid importing the default application with a real deployment environment.
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "reports"))
    from app.config import Settings
    from app.main import create_app

    settings = Settings(_env_file=None, database_url=database_url, data_dir=tmp_path, cookie_secure=False, allowed_origins="http://testserver")
    return create_app(settings=settings), database_file


def test_health_is_live_without_connecting_to_uninitialized_database(tmp_path, monkeypatch):
    app, database_file = make_app(tmp_path, monkeypatch)

    def forbidden_connection(*args):
        raise AssertionError("Liveness must not connect to a database")

    event.listen(app.state.engine, "connect", forbidden_connection)
    try:
        with TestClient(app) as client:
            response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        assert not database_file.exists()
    finally:
        event.remove(app.state.engine, "connect", forbidden_connection)
        app.state.engine.dispose()


def test_ready_returns_clear_unavailable_response_before_initialization(tmp_path, monkeypatch):
    app, _ = make_app(tmp_path, monkeypatch)
    try:
        with TestClient(app) as client:
            response = client.get("/api/ready")
            assert response.status_code == 503
            assert response.json() == {"detail": "数据库尚未就绪，请检查数据库服务和初始化状态"}
            assert client.get("/api/health").json() == {"status": "ok"}
    finally:
        app.state.engine.dispose()


def test_ready_connection_failure_keeps_health_available(tmp_path, monkeypatch):
    app, database_file = make_app(tmp_path, monkeypatch)

    def failed_connection(*args):
        raise OperationalError("connection unavailable", {}, Exception("probe test"))

    event.listen(app.state.engine, "do_connect", failed_connection)
    try:
        with TestClient(app) as client:
            response = client.get("/api/ready")
            assert response.status_code == 503
            assert response.json() == {"detail": "数据库尚未就绪，请检查数据库服务和初始化状态"}
            assert client.get("/api/health").status_code == 200
        assert not database_file.exists()
    finally:
        event.remove(app.state.engine, "do_connect", failed_connection)
        app.state.engine.dispose()
