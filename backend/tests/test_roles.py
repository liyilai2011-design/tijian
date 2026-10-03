"""Role/account acceptance with isolated data and one cached test password hash."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
import sys
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import IntegrityError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import Settings
from app.database import create_database
from app.models import AuditLog, CatalogItem, Department, LoginSession, Package, User
from app.security import hash_password

ADMIN = "isolated_roles_admin"
PASSWORD = "Stage-four-isolated-test-only-2026!"
SUMMARY_FIELDS = {"id", "username", "display_name", "role", "department_id", "is_active"}


@pytest.fixture(scope="session")
def roles_password_hash():
    return hash_password(PASSWORD)


@pytest.fixture
def role_system(tmp_path, monkeypatch, roles_password_hash):
    url = "sqlite:///" + (tmp_path / "isolated_roles.db").as_posix()
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.attributes["database_url"] = url
    command.upgrade(config, "head")
    engine, Session = create_database(url)
    try:
        with Session.begin() as db:
            db.add(User(username=ADMIN, display_name="权限验收管理员", password_hash=roles_password_hash, role="admin", is_active=True))
            db.add_all([Department(id=1, name="权限测试内科"), Department(id=2, name="权限测试检验科")])
    finally:
        engine.dispose()
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "reports"))
    from app.main import create_app

    app = create_app(settings=Settings(_env_file=None, database_url=url, data_dir=tmp_path, allowed_origins="http://testserver", cookie_secure=False))
    try:
        with ExitStack() as stack:
            def sign_in(username, password=PASSWORD):
                client = stack.enter_context(TestClient(app))
                response = client.post("/api/auth/login", json={"username": username, "password": password})
                assert response.status_code == 200, response.text
                client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
                return client

            def add_account(username, role, department_id=None):
                with app.state.Session.begin() as db:
                    user = User(username=username, display_name="权限验收" + username, password_hash=roles_password_hash, role=role, department_id=department_id, is_active=True)
                    db.add(user)
                    db.flush()
                    return user.id

            yield SimpleNamespace(app=app, admin=sign_in(ADMIN), login=sign_in, add=add_account)
    finally:
        app.state.engine.dispose()


def snapshot(user):
    return {field: user[field] for field in ("display_name", "role", "department_id", "is_active")}


def edit_body(user, **changes):
    body = snapshot(user)
    body.update(changes)
    body["expected"] = snapshot(user)
    return body


def account(system, user_id):
    rows = system.admin.get("/api/users").json()["items"]
    return next(user for user in rows if user["id"] == user_id)


def make_exam(system):
    with system.app.state.Session.begin() as db:
        package = Package(name="权限验证套餐", description="仅软件验收")
        package.items = [CatalogItem(name="内科权限测试项目", department_id=1, unit="", reference_range="软件测试"), CatalogItem(name="检验权限测试项目", department_id=2, unit="", reference_range="软件测试")]
        db.add(package)
        db.flush()
        package_id = package.id
    patient = system.admin.post("/api/patients", json={"name": "权限验收受检者", "sex": "other", "request_id": uuid4().hex})
    assert patient.status_code == 200
    exam = system.admin.post("/api/exams", json={"patient_id": patient.json()["id"], "package_id": package_id, "request_id": uuid4().hex})
    assert exam.status_code == 200
    return exam.json()


def test_admin_creates_four_roles_and_lists_without_credentials(role_system):
    chosen_password = "  Created-user-test-password!  "
    for role in ("admin", "registrar", "doctor", "reviewer"):
        response = role_system.admin.post("/api/users", json={"username": "created_" + role, "display_name": "创建测试" + role, "password": chosen_password, "role": role, "department_id": 1 if role == "doctor" else None})
        assert response.status_code == 201, response.text
        assert set(response.json()) == SUMMARY_FIELDS
        assert response.json()["role"] == role
        with role_system.app.state.Session() as db:
            saved = db.get(User, response.json()["id"])
            assert saved.password_hash.startswith("scrypt$") and chosen_password not in saved.password_hash
    first = role_system.admin.get("/api/users", params={"q": "created_", "page": 1, "page_size": 2}).json()
    second = role_system.admin.get("/api/users", params={"q": "created_", "page": 2, "page_size": 2}).json()
    assert first["total"] == second["total"] == 4
    assert len(first["items"]) == len(second["items"]) == 2
    assert {row["id"] for row in first["items"]}.isdisjoint(row["id"] for row in second["items"])
    assert all(set(row) == SUMMARY_FIELDS for row in first["items"] + second["items"])
    assert role_system.admin.get("/api/users", params={"page_size": 101}).status_code == 422
    created = role_system.login("created_doctor", chosen_password)
    rejected = created.post("/api/auth/login", json={"username": "created_doctor", "password": chosen_password.strip()})
    assert rejected.status_code == 401


@pytest.mark.parametrize("role", ["registrar", "doctor", "reviewer"])
def test_non_admin_cannot_read_create_or_edit_accounts(role_system, role):
    role_system.add("denied_" + role, role, 1 if role == "doctor" else None)
    client = role_system.login("denied_" + role)
    administrator = role_system.admin.get("/api/users").json()["items"][0]
    assert client.get("/api/users").status_code == 403
    assert client.get(f"/api/users/{administrator['id']}").status_code == 403
    assert client.get("/api/users/99999").status_code == 403
    assert client.post("/api/users", json={"username": "unauthorized_new", "display_name": "越权测试", "password": PASSWORD, "role": "registrar", "department_id": None}).status_code == 403
    assert client.put(f"/api/users/{administrator['id']}", json=edit_body(administrator, display_name="越权修改")).status_code == 403


def test_admin_can_read_latest_account_even_when_disabled_and_missing_is_404(role_system):
    user_id = role_system.add("latest_account_reviewer", "reviewer")
    response = role_system.admin.get(f"/api/users/{user_id}")
    assert response.status_code == 200
    assert set(response.json()) == SUMMARY_FIELDS
    disabled = role_system.admin.put(f"/api/users/{user_id}", json=edit_body(response.json(), is_active=False))
    assert disabled.status_code == 200
    latest = role_system.admin.get(f"/api/users/{user_id}")
    assert latest.status_code == 200 and latest.json() == disabled.json()
    assert latest.json()["is_active"] is False
    assert role_system.admin.get("/api/users/99999").status_code == 404
    with TestClient(role_system.app) as anonymous:
        assert anonymous.get(f"/api/users/{user_id}").status_code == 401


def test_role_business_scope_is_enforced_by_backend(role_system):
    role_system.add("scope_registrar", "registrar")
    role_system.add("scope_doctor", "doctor", 1)
    role_system.add("scope_reviewer", "reviewer")
    registrar = role_system.login("scope_registrar")
    doctor = role_system.login("scope_doctor")
    reviewer = role_system.login("scope_reviewer")
    exam = make_exam(role_system)
    own = doctor.get(f"/api/exams/{exam['id']}").json()
    assert len(own["items"]) == 1 and own["items"][0]["department_id"] == 1
    assert doctor.get("/api/patients").status_code == 403
    foreign = next(item for item in exam["items"] if item["department_id"] == 2)
    assert doctor.put(f"/api/exam-items/{foreign['id']}", json={"value": "跨科拒绝测试", "version": foreign["version"]}).status_code == 403
    assert doctor.get(f"/api/exam-items/{foreign['id']}/history").status_code == 403
    assert doctor.get(f"/api/exams/{exam['id']}/report").status_code == 403
    assert registrar.post(f"/api/exams/{exam['id']}/finalize", json={"conclusion": "越权总检", "version": exam["version"]}).status_code == 403
    assert reviewer.get("/api/patients").status_code == 403
    assert reviewer.put(f"/api/exam-items/{exam['items'][0]['id']}", json={"value": "总检不能录科室结果", "version": 1}).status_code == 403
    assert len(reviewer.get(f"/api/exams/{exam['id']}").json()["items"]) == 2
    for item in exam["items"]:
        assert role_system.admin.put(f"/api/exam-items/{item['id']}", json={"value": "权限验收结果", "note": "软件测试", "is_abnormal": True, "version": item["version"]}).status_code == 200
    hidden = registrar.get(f"/api/exams/{exam['id']}").json()
    assert all(item["value"] is None and item["note"] is None and not item["is_abnormal"] for item in hidden["items"])
    ready = reviewer.get(f"/api/exams/{exam['id']}").json()
    assert reviewer.post(f"/api/exams/{exam['id']}/finalize", json={"conclusion": "权限测试总检", "version": ready["version"]}).status_code == 200
    assert reviewer.get(f"/api/exams/{exam['id']}/report").status_code == 200


def test_duplicate_username_and_invalid_role_department_are_rejected(role_system):
    base = {"username": "valid_input_test", "display_name": "输入验证", "password": PASSWORD, "role": "registrar", "department_id": None}
    duplicate = role_system.admin.post("/api/users", json={**base, "username": ADMIN})
    assert duplicate.status_code == 409
    for changed in ({"role": "unknown"}, {"role": "doctor"}, {"role": "doctor", "department_id": 999}, {"department_id": 1}, {"username": "bad username"}, {"password": "short"}):
        response = role_system.admin.post("/api/users", json={**base, **changed})
        assert response.status_code == 422, response.text
        assert PASSWORD not in response.text and "short" not in response.text
    with role_system.app.state.Session() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 1


def test_self_profile_change_keeps_session_and_self_demotion_is_forbidden(role_system):
    administrator = role_system.admin.get("/api/users").json()["items"][0]
    update = role_system.admin.put(f"/api/users/{administrator['id']}", json=edit_body(administrator, display_name="管理员新显示名"))
    assert update.status_code == 200
    assert role_system.admin.get("/api/auth/me").json()["user"]["display_name"] == "管理员新显示名"
    current = update.json()
    assert role_system.admin.put(f"/api/users/{current['id']}", json=edit_body(current, role="registrar")).status_code == 409
    assert role_system.admin.put(f"/api/users/{current['id']}", json=edit_body(current, is_active=False)).status_code == 409
    assert role_system.admin.put(f"/api/users/{current['id']}", json={**edit_body(current), "username": "forbidden_username_change"}).status_code == 422
    assert role_system.admin.put(f"/api/users/{current['id']}", json=edit_body(administrator, display_name="旧快照覆盖")).status_code == 409


def test_edit_rejects_invalid_role_and_department_without_changing_user(role_system):
    user_id = role_system.add("invalid_update_doctor", "doctor", 1)
    original = account(role_system, user_id)
    for changed in ({"role": "unknown"}, {"department_id": None}, {"department_id": 999}, {"role": "registrar"}):
        response = role_system.admin.put(f"/api/users/{user_id}", json=edit_body(original, **changed))
        assert response.status_code == 422
        assert account(role_system, user_id) == original


def test_role_change_revokes_old_session_but_profile_change_does_not(role_system):
    user_id = role_system.add("changed_doctor", "doctor", 1)
    doctor = role_system.login("changed_doctor")
    second_doctor_session = role_system.login("changed_doctor")
    original = account(role_system, user_id)
    profile = role_system.admin.put(f"/api/users/{user_id}", json=edit_body(original, display_name="只改显示名"))
    assert profile.status_code == 200
    assert doctor.get("/api/auth/me").status_code == 200
    assert second_doctor_session.get("/api/auth/me").status_code == 200
    changed = role_system.admin.put(f"/api/users/{user_id}", json=edit_body(profile.json(), role="registrar", department_id=None))
    assert changed.status_code == 200
    assert doctor.get("/api/auth/me").status_code == 401
    assert second_doctor_session.get("/api/auth/me").status_code == 401
    new_session = role_system.login("changed_doctor")
    assert new_session.get("/api/patients").status_code == 200
    assert new_session.get("/api/users").status_code == 403


def test_department_change_revokes_session_and_restricts_new_department(role_system):
    user_id = role_system.add("moved_doctor", "doctor", 1)
    doctor = role_system.login("moved_doctor")
    exam = make_exam(role_system)
    original = account(role_system, user_id)
    response = role_system.admin.put(f"/api/users/{user_id}", json=edit_body(original, department_id=2))
    assert response.status_code == 200
    assert doctor.get("/api/auth/me").status_code == 401
    moved = role_system.login("moved_doctor")
    visible = moved.get(f"/api/exams/{exam['id']}").json()
    assert all(item["department_id"] == 2 for item in visible["items"])
    foreign = next(item for item in exam["items"] if item["department_id"] == 1)
    assert moved.put(f"/api/exam-items/{foreign['id']}", json={"value": "不能写原科室", "version": 1}).status_code == 403


def test_disabled_account_cannot_login_until_reenabled(role_system):
    user_id = role_system.add("disabled_reviewer", "reviewer")
    reviewer = role_system.login("disabled_reviewer")
    disabled = role_system.admin.put(f"/api/users/{user_id}", json=edit_body(account(role_system, user_id), is_active=False))
    assert disabled.status_code == 200
    assert reviewer.get("/api/auth/me").status_code == 401
    assert reviewer.post("/api/auth/login", json={"username": "disabled_reviewer", "password": PASSWORD}).status_code == 401
    assert role_system.admin.put(f"/api/users/{user_id}", json=edit_body(disabled.json(), is_active=True)).status_code == 200
    assert role_system.login("disabled_reviewer").get("/api/auth/me").status_code == 200


def test_unknown_database_role_fails_closed(role_system):
    with role_system.app.state.Session.begin() as db:
        db.scalar(select(User).where(User.username == ADMIN)).role = "unknown"
    assert role_system.admin.get("/api/auth/me").status_code == 403
    assert role_system.admin.get("/api/packages").status_code == 403
    assert role_system.admin.get("/api/users").status_code == 403
    assert role_system.admin.post("/api/auth/login", json={"username": ADMIN, "password": PASSWORD}).status_code == 401


@pytest.mark.parametrize("operation", ["create", "edit"])
def test_account_audit_failure_rolls_back_user_and_session_changes(role_system, operation, caplog):
    user_id = role_system.add("atomic_reviewer", "reviewer")
    target = role_system.login("atomic_reviewer")
    before = account(role_system, user_id)

    def fail_audit(mapper, connection, row):
        if row.action == ("user.create" if operation == "create" else "user.update"):
            raise IntegrityError("private-user-audit-query", {"secret": "user-private-test-marker"}, Exception("user-private-test-marker"))

    event.listen(AuditLog, "before_insert", fail_audit)
    try:
        if operation == "create":
            response = role_system.admin.post("/api/users", json={"username": "atomic_new_user", "display_name": "失败新增", "password": PASSWORD, "role": "registrar", "department_id": None})
        else:
            response = role_system.admin.put(f"/api/users/{user_id}", json=edit_body(before, role="registrar", is_active=False))
    finally:
        event.remove(AuditLog, "before_insert", fail_audit)
    assert response.status_code == 503
    assert "user-private-test-marker" not in response.text + caplog.text
    assert "private-user-audit-query" not in response.text + caplog.text
    assert account(role_system, user_id) == before
    assert target.get("/api/auth/me").status_code == 200
    with role_system.app.state.Session() as db:
        assert db.scalar(select(User).where(User.username == "atomic_new_user")) is None
        assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action.in_(["user.create", "user.update"]))) == 0


def test_two_admins_cannot_concurrently_remove_each_others_authority(role_system, monkeypatch):
    import app.user_management as management
    second_id = role_system.add("second_racing_admin", "admin")
    second = role_system.login("second_racing_admin")
    original_users = role_system.admin.get("/api/users").json()["items"]
    first_user = next(user for user in original_users if user["username"] == ADMIN)
    second_user = next(user for user in original_users if user["id"] == second_id)
    both_authenticated = Barrier(2)
    original_guard = management.lock_and_recheck_admin

    def synchronize_before_shared_lock(request, db):
        both_authenticated.wait(timeout=15)
        return original_guard(request, db)

    monkeypatch.setattr(management, "lock_and_recheck_admin", synchronize_before_shared_lock)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(role_system.admin.put, f"/api/users/{second_id}", json=edit_body(second_user, role="registrar"))
        other = pool.submit(second.put, f"/api/users/{first_user['id']}", json=edit_body(first_user, role="registrar"))
        responses = [first.result(timeout=30), other.result(timeout=30)]
    monkeypatch.setattr(management, "lock_and_recheck_admin", original_guard)
    assert sum(response.status_code == 200 for response in responses) == 1
    assert all(response.status_code in (200, 401, 403, 409, 503) for response in responses)
    with role_system.app.state.Session() as db:
        assert db.scalar(select(func.count()).select_from(User).where(User.role == "admin", User.is_active.is_(True))) == 1
        survivor = db.scalar(select(User).where(User.role == "admin"))
        surviving_summary = management.user_summary(survivor)
    lost_actor = second if responses[0].status_code == 200 else role_system.admin
    assert lost_actor.get("/api/users").status_code == 401
    assert lost_actor.put(f"/api/users/{surviving_summary['id']}", json=edit_body(surviving_summary, role="registrar")).status_code == 401
