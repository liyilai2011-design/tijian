"""Use a fresh database and explicitly chosen test credentials for every test."""
from contextlib import ExitStack
from pathlib import Path
import os
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
TEST_PASSWORD = "Isolated-test-only-2026!"


@pytest.fixture
def system(tmp_path, monkeypatch):
    database_url = "sqlite:///" + (tmp_path / "test.db").as_posix()
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://testserver")
    environment = os.environ.copy()
    environment["HEALTHCHECK_TEST_PASSWORD"] = TEST_PASSWORD
    result = subprocess.run(
        [sys.executable, "-m", "app.cli", "init", "--demo", "--password-env", "HEALTHCHECK_TEST_PASSWORD"],
        cwd=ROOT / "backend", env=environment, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    from app.main import create_app
    from fastapi.testclient import TestClient

    app = create_app(database_url=database_url)
    with ExitStack() as stack:
        clients = {}

        def login(username):
            if username not in clients:
                client = stack.enter_context(TestClient(app))
                response = client.post("/api/auth/login", json={"username": username, "password": TEST_PASSWORD})
                assert response.status_code == 200, response.text
                client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
                clients[username] = client
            return clients[username]

        yield {"app": app, "login": login, "anonymous": stack.enter_context(TestClient(app)), "database": tmp_path / "test.db"}


def new_exam(client, name="测试受检者", request_id="test-request-0001"):
    response = client.post("/api/patients", json={"name": name, "sex": "female", "birth_date": "1990-05-10", "phone": None, "request_id": "patient-" + request_id})
    assert response.status_code in (200, 201), response.text
    patient = response.json()
    packages = client.get("/api/packages").json()
    assert packages
    payload = {"patient_id": patient["id"], "package_id": packages[0]["id"], "request_id": request_id}
    response = client.post("/api/exams", json=payload)
    assert response.status_code in (200, 201), response.text
    return response.json(), payload


def finish_items(client, exam):
    for index, item in enumerate(exam["items"]):
        response = client.put(f"/api/exam-items/{item['id']}", json={"value": f"测试结果{index}", "is_abnormal": index == 0, "note": "仅用于软件测试", "version": item["version"]})
        assert response.status_code == 200, response.text
    return client.get(f"/api/exams/{exam['id']}").json()
