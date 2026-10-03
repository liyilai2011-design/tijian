"""Foundation maintenance using real SQLite concurrency and clinical snapshots."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from io import StringIO
from pathlib import Path
from threading import Barrier, Event
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import MetaData, event, func, inspect, select, text
from sqlalchemy.exc import IntegrityError

from test_patients import create, patient_system, patients_password_hash
from test_exam_registration import body_for, register
from app import foundation_management as foundation, main, registration, user_management
from app.database import create_database
from app.models import AuditLog, CatalogItem, Department, Exam, ExamItem, FoundationRequest, Package, ReportSnapshot


def department(system, name="阶段七软件验收科室", **changes):
    body = {"name": name, "request_id": uuid4().hex}
    body.update(changes)
    response = system.admin.post("/api/admin/departments", json=body)
    assert response.status_code == 200, response.text
    return response.json(), body


def catalog(system, **changes):
    body = {"name": "阶段七软件验收项目", "department_id": 1, "category": "软件测试分类", "unit": "测试单位", "reference_range": "软件测试说明，不是临床范围", "result_type": "text", "abnormal_allowed": True, "is_active": True, "request_id": uuid4().hex}
    body.update(changes)
    response = system.admin.post("/api/admin/catalog-items", json=body)
    assert response.status_code == 200, response.text
    return response.json(), body


def edit_department(system, value, **changes):
    body = {field: value[field] for field in ("name", "is_active", "version")}
    body.update(changes)
    response = system.admin.put(f"/api/admin/departments/{value['id']}", json=body)
    return response


def edit_catalog(system, value, **changes):
    body = {field: value[field] for field in foundation.CATALOG_FIELDS + ("version",)}
    body.update(changes)
    return system.admin.put(f"/api/admin/catalog-items/{value['id']}", json=body)


def counts(system):
    with system.app.state.Session() as db:
        return tuple(db.scalar(select(func.count()).select_from(model)) for model in (Department, CatalogItem, FoundationRequest, Exam, ExamItem))


def test_department_create_replay_update_noop_and_stale_version(patient_system):
    value, body = department(patient_system, name="  阶段七软件验收科室  ")
    assert value["name"] == "阶段七软件验收科室" and value["version"] == 1 and value["is_active"]
    assert patient_system.admin.post("/api/admin/departments", json=body).json() == value
    assert patient_system.admin.get(f"/api/admin/departments/{value['id']}").json() == value
    updated = edit_department(patient_system, value, name="已修改软件科室", is_active=False)
    assert updated.status_code == 200 and updated.json()["version"] == 2
    assert edit_department(patient_system, updated.json()).json() == updated.json()
    assert edit_department(patient_system, value, name="过期修改").status_code == 409
    assert patient_system.admin.post("/api/admin/departments", json=body).json() == updated.json()
    assert counts(patient_system)[:3] == (2, 1, 1)
    assert patient_system.admin.post("/api/admin/departments", json={"name": "已修改软件科室", "request_id": uuid4().hex}).status_code == 409
    original = patient_system.admin.get("/api/admin/departments/1").json()
    assert edit_department(patient_system, original, name="已修改软件科室").status_code == 409
    assert edit_department(patient_system, original, name="新名").status_code == 200


def test_catalog_create_normalize_replay_edit_category_fields_and_move_department(patient_system):
    other, _ = department(patient_system)
    item, body = catalog(patient_system, name="  软件新项目  ", category="  分组  ", unit="  mg  ")
    assert item["name"] == "软件新项目" and item["category"] == "分组" and item["unit"] == "mg"
    assert item["version"] == 1 and item["department_name"] == "软件测试科室" and item["department_is_active"]
    assert patient_system.admin.post("/api/admin/catalog-items", json=body).json() == item
    updated = edit_catalog(patient_system, item, department_id=other["id"], result_type="number", abnormal_allowed=False, reference_range="新测试范围", is_active=False)
    assert updated.status_code == 200, updated.text
    current = updated.json()
    assert current["version"] == 2 and current["department_name"] == other["name"] and current["result_type"] == "number"
    assert edit_catalog(patient_system, current).json() == current
    assert edit_catalog(patient_system, item, name="旧版覆盖").status_code == 409
    assert patient_system.admin.get(f"/api/admin/catalog-items/{item['id']}").json() == current
    assert patient_system.admin.post("/api/admin/catalog-items", json=body).json() == current
    assert counts(patient_system)[:3] == (2, 2, 2)


@pytest.mark.parametrize("kind", ["departments", "catalog-items"])
def test_request_id_conflict_actor_and_payload_are_rejected(patient_system, kind):
    value, body = department(patient_system) if kind == "departments" else catalog(patient_system)
    assert patient_system.admin.post("/api/admin/" + kind, json=dict(body, name="另一内容")).status_code == 409
    other = patient_system.client_for("admin")
    assert other.post("/api/admin/" + kind, json=body).status_code == 409
    cross = {"name": "跨类型", "request_id": body["request_id"]}
    if kind == "departments":
        cross["department_id"] = 1
    assert patient_system.admin.post("/api/admin/" + ("catalog-items" if kind == "departments" else "departments"), json=cross).status_code == 409
    assert counts(patient_system)[2] == 1


@pytest.mark.parametrize("kind", ["departments", "catalog-items"])
def test_concurrent_same_creation_request_saves_one_row_and_audit(patient_system, monkeypatch, kind):
    meet = Barrier(2)
    original = foundation.lock_and_recheck
    def lock(request, db, *roles):
        meet.wait(timeout=10)
        return original(request, db, *roles)
    monkeypatch.setattr(foundation, "lock_and_recheck", lock)
    body = {"name": "并发基础资料（虚构）", "request_id": uuid4().hex}
    if kind == "catalog-items":
        body["department_id"] = 1
    headers = dict(patient_system.admin.headers)
    def save(_):
        with TestClient(patient_system.app) as client:
            return client.post("/api/admin/" + kind, json=body, headers=headers)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(save, range(2)))
    assert [r.status_code for r in responses] == [200, 200]
    assert responses[0].json() == responses[1].json()
    assert counts(patient_system)[:3] == ((2, 1, 1) if kind == "departments" else (1, 2, 1))
    with patient_system.app.state.Session() as db:
        action = "department.create" if kind == "departments" else "catalog_item.create"
        assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == action)) == 1


@pytest.mark.parametrize("role", ["registrar", "doctor", "reviewer"])
def test_server_admin_permissions_cover_all_foundation_routes(patient_system, role):
    client = patient_system.client_for(role)
    for path, create_body, edit_body in (("departments", {"name": "禁止", "request_id": uuid4().hex}, {"name": "禁止", "is_active": False, "version": 1}), ("catalog-items", {"name": "禁止", "department_id": 1, "request_id": uuid4().hex}, {"name": "禁止", "department_id": 1, "version": 1})):
        assert client.get("/api/admin/" + path).status_code == 403
        assert client.get("/api/admin/" + path + "/1").status_code == 403
        assert client.post("/api/admin/" + path, json=create_body).status_code == 403
        assert client.put("/api/admin/" + path + "/1", json=edit_body).status_code == 403
    assert counts(patient_system)[:3] == (1, 1, 0)


def test_auth_csrf_and_role_revocation_after_lock_are_enforced(patient_system, monkeypatch):
    value = {"name": "不可创建", "request_id": uuid4().hex}
    with TestClient(patient_system.app) as client:
        assert client.post("/api/admin/departments", json=value).status_code == 401
        headers = dict(patient_system.admin.headers)
        headers.pop("x-csrf-token")
        assert client.post("/api/admin/departments", json=value, headers=headers).status_code == 403
    original = foundation.lock_and_recheck
    def revoked(request, db, *roles):
        from app.models import User
        with patient_system.app.state.Session.begin() as separate:
            actor = separate.scalar(select(User).where(User.role == "admin"))
            actor.role = "registrar"
        return original(request, db, *roles)
    monkeypatch.setattr(foundation, "lock_and_recheck", revoked)
    assert patient_system.admin.post("/api/admin/departments", json=value).status_code == 403
    assert counts(patient_system)[:3] == (1, 1, 0)


@pytest.mark.parametrize("changes", [{"name": " "}, {"name": "n" * 101}, {"category": "n" * 101}, {"unit": "n" * 51}, {"reference_range": "n" * 501}, {"department_id": 0}, {"department_id": 999}, {"result_type": "date"}, {"abnormal_allowed": "true"}, {"is_active": 1}, {"unknown": "x"}])
def test_catalog_invalid_input_never_writes(patient_system, changes):
    body = {"name": "新项目", "department_id": 1, "request_id": uuid4().hex}
    body.update(changes)
    assert patient_system.admin.post("/api/admin/catalog-items", json=body).status_code == 422
    assert counts(patient_system)[:3] == (1, 1, 0)


def test_server_pagination_search_literal_wildcard_filters_and_limits(patient_system):
    for index in range(21):
        dep, _ = department(patient_system, name=f"批次科室{index:02}")
        item, _ = catalog(patient_system, name=f"批次项目{index:02}", department_id=dep["id"], category="批次分类")
        if index == 20:
            assert edit_department(patient_system, dep, is_active=False).status_code == 200
            assert edit_catalog(patient_system, item, is_active=False).status_code == 200
    for path in ("departments", "catalog-items"):
        first = patient_system.admin.get("/api/admin/" + path).json()
        assert first["total"] == 22 and len(first["items"]) == 20
        assert len(patient_system.admin.get("/api/admin/" + path + "?page=2").json()["items"]) == 2
        assert patient_system.admin.get("/api/admin/" + path + "?page_size=101").status_code == 422
        assert patient_system.admin.get("/api/admin/" + path + "?page=0").status_code == 422
        assert patient_system.admin.get("/api/admin/" + path + "?is_active=false").json()["total"] == 1
        assert patient_system.admin.get("/api/admin/" + path + "?q=%25").json()["total"] == 0
    assert patient_system.admin.get("/api/admin/catalog-items?q=批次分类").json()["total"] == 21
    assert patient_system.admin.get("/api/admin/catalog-items?q=批次科室03").json()["total"] == 1
    assert patient_system.admin.get("/api/admin/catalog-items?department_id=1").json()["total"] == 1


@pytest.mark.parametrize("action", ["department.create", "catalog_item.create"])
def test_audit_failure_rolls_back_target_request_and_does_not_false_recover(patient_system, action, caplog):
    def fail(mapper, connection, target):
        if target.action == action:
            raise IntegrityError("private_sql", {"private": "private_value"}, Exception("private_error"))
    event.listen(AuditLog, "before_insert", fail)
    path = "departments" if action == "department.create" else "catalog-items"
    body = {"name": "事务测试", "request_id": uuid4().hex}
    if path == "catalog-items":
        body["department_id"] = 1
    try:
        response = patient_system.admin.post("/api/admin/" + path, json=body)
    finally:
        event.remove(AuditLog, "before_insert", fail)
    assert response.status_code == 503 and counts(patient_system)[:3] == (1, 1, 0)
    assert "private_value" not in response.text + caplog.text and "private_error" not in response.text + caplog.text
    assert patient_system.admin.post("/api/admin/" + path, json=body).status_code == 200


@pytest.mark.parametrize("kind", ["departments", "catalog-items"])
def test_commit_failure_rolls_back_create_and_update(patient_system, kind):
    value, _ = department(patient_system) if kind == "departments" else catalog(patient_system)
    before = counts(patient_system)
    def fail(db):
        if not db.in_nested_transaction():
            raise IntegrityError("private_commit", {}, Exception("private_error"))
    event.listen(patient_system.app.state.Session.class_, "before_commit", fail)
    try:
        update = edit_department(patient_system, value, name="未提交修改") if kind == "departments" else edit_catalog(patient_system, value, name="未提交修改")
        assert update.status_code == 503
        body = {"name": "未提交新增", "request_id": uuid4().hex}
        if kind == "catalog-items":
            body["department_id"] = 1
        assert patient_system.admin.post("/api/admin/" + kind, json=body).status_code == 503
    finally:
        event.remove(patient_system.app.state.Session.class_, "before_commit", fail)
    assert patient_system.admin.get(f"/api/admin/{kind}/{value['id']}").json() == value
    assert counts(patient_system) == before


def test_catalog_changes_are_snapshotted_and_old_report_remains_identical(patient_system):
    patient = create(patient_system, identity_number=None)
    first = register(patient_system, body_for(patient_system, patient["id"]))
    old_item = first["items"][0]
    assert old_item["category"] == "" and old_item["result_type"] == "text" and old_item["abnormal_allowed"]
    catalog_item = patient_system.admin.get("/api/admin/catalog-items/1").json()
    updated = edit_catalog(patient_system, catalog_item, name="已改基础项目", category="数值分组", unit="新单位", reference_range="新软件范围", result_type="number", abnormal_allowed=False)
    assert updated.status_code == 200
    dep = patient_system.admin.get("/api/admin/departments/1").json()
    assert edit_department(patient_system, dep, name="已改科室名称").status_code == 200
    assert patient_system.admin.get(f"/api/exams/{first['id']}").json() == first
    second = register(patient_system, body_for(patient_system, patient["id"]))
    new_item = second["items"][0]
    assert new_item["item_name"] == "已改基础项目" and new_item["department_name"] == "已改科室名称"
    assert new_item["result_type"] == "number" and not new_item["abnormal_allowed"] and new_item["category"] == "数值分组"
    assert patient_system.admin.put(f"/api/exam-items/{old_item['id']}", json={"value": "旧自由文本仍合法", "is_abnormal": True, "version": old_item["version"]}).status_code == 200
    ready = patient_system.admin.get(f"/api/exams/{first['id']}").json()
    assert patient_system.admin.post(f"/api/exams/{first['id']}/finalize", json={"version": ready["version"], "conclusion": "旧体检软件验收结论"}).status_code == 200
    report = patient_system.admin.get(f"/api/exams/{first['id']}/report").json()
    with patient_system.app.state.Session() as db:
        stored = dict(db.scalar(select(ReportSnapshot).where(ReportSnapshot.exam_id == first["id"])).payload)
    assert edit_catalog(patient_system, updated.json(), is_active=False, reference_range="再次更改范围").status_code == 200
    assert patient_system.admin.get(f"/api/exams/{first['id']}/report").json() == report
    with patient_system.app.state.Session() as db:
        assert db.scalar(select(ReportSnapshot).where(ReportSnapshot.exam_id == first["id"])).payload == stored


@pytest.mark.parametrize("result_type,value,abnormal,expected", [("number", "-1.25e2", False, 200), ("number", ".5", False, 200), ("number", "NaN", False, 422), ("number", "Infinity", False, 422), ("number", "1e999", False, 422), ("number", "非数值", False, 422), ("number", "123", True, 422), ("boolean", "是", False, 200), ("boolean", "否", False, 200), ("boolean", "true", False, 422), ("text", "自由文本", False, 200), ("text", "自由文本", True, 422)])
def test_result_input_uses_finite_numeric_boolean_and_abnormal_snapshot(patient_system, result_type, value, abnormal, expected):
    item = patient_system.admin.get("/api/admin/catalog-items/1").json()
    assert edit_catalog(patient_system, item, result_type=result_type, abnormal_allowed=False).status_code == 200
    exam = register(patient_system, body_for(patient_system))
    target = exam["items"][0]
    response = patient_system.admin.put(f"/api/exam-items/{target['id']}", json={"value": value, "is_abnormal": abnormal, "version": target["version"]})
    assert response.status_code == expected, response.text
    current = patient_system.admin.get(f"/api/exams/{exam['id']}").json()
    assert current["status"] == ("ready_for_review" if expected == 200 else "registered")
    assert current["items"][0]["version"] == (2 if expected == 200 else 1)
    assert current["items"][0]["is_abnormal"] is False


@pytest.mark.parametrize("kind", ["departments", "catalog-items"])
def test_disable_hides_whole_package_rejects_new_exam_but_preserves_old_work(patient_system, kind):
    first = register(patient_system, body_for(patient_system))
    value = patient_system.admin.get("/api/admin/" + kind + "/1").json()
    response = edit_department(patient_system, value, is_active=False) if kind == "departments" else edit_catalog(patient_system, value, is_active=False)
    assert response.status_code == 200
    assert patient_system.admin.get("/api/packages").json() == []
    assert patient_system.admin.post("/api/exams", json=body_for(patient_system, first["patient"]["id"])).status_code == 422
    assert patient_system.admin.get(f"/api/exams/{first['id']}").json() == first
    doctor = patient_system.client_for("doctor")
    item = first["items"][0]
    assert doctor.put(f"/api/exam-items/{item['id']}", json={"value": "仍可录入旧检查", "version": item["version"]}).status_code == 200
    with patient_system.app.state.Session() as db:
        assert len(db.get(Package, patient_system.package_id).items) == 1
    if kind == "departments":
        public = patient_system.admin.get("/api/departments").json()
        assert public[0]["name"] == value["name"] and public[0]["is_active"] is False


def test_disabled_department_assignment_rejected_but_existing_doctor_maintenance_allowed(patient_system, monkeypatch, patients_password_hash):
    doctor = patient_system.client_for("doctor")
    user_id = doctor.get("/api/auth/me").json()["user"]["id"]
    before = patient_system.admin.get(f"/api/users/{user_id}").json()
    dep = patient_system.admin.get("/api/admin/departments/1").json()
    assert edit_department(patient_system, dep, is_active=False).status_code == 200
    monkeypatch.setattr(user_management, "hash_password", lambda value: patients_password_hash)
    assert patient_system.admin.post("/api/users", json={"username": "cannot_assign", "display_name": "新医生", "password": "Isolated-password-2026!", "role": "doctor", "department_id": 1}).status_code == 422
    expected = {field: before[field] for field in ("display_name", "role", "department_id", "is_active")}
    body = dict(expected, display_name="原科室医生改名", expected=expected)
    changed = patient_system.admin.put(f"/api/users/{user_id}", json=body)
    assert changed.status_code == 200 and changed.json()["department_id"] == 1
    assert doctor.get("/api/auth/me").status_code == 200
    assert patient_system.admin.post("/api/admin/catalog-items", json={"name": "不能新增关联", "department_id": 1, "request_id": uuid4().hex}).status_code == 422
    item = patient_system.admin.get("/api/admin/catalog-items/1").json()
    assert edit_catalog(patient_system, item, name="维护已有项目").status_code == 200
    disabled = edit_catalog(patient_system, patient_system.admin.get("/api/admin/catalog-items/1").json(), is_active=False)
    assert disabled.status_code == 200
    assert edit_catalog(patient_system, disabled.json(), is_active=True).status_code == 422


@pytest.mark.parametrize("kind", ["departments", "catalog-items"])
@pytest.mark.parametrize("entrypoint", ["/api/exams", "/api/registrations"])
@pytest.mark.parametrize("first_operation", ["disable", "create"])
def test_real_concurrent_disable_and_registration_have_one_serial_order(patient_system, monkeypatch, kind, entrypoint, first_operation):
    locked, waiting, release = Event(), Event(), Event()
    foundation_original = foundation.lock_and_recheck
    if entrypoint == "/api/exams":
        creator_module, creator_attr = main, "lock_and_recheck"
    else:
        creator_module, creator_attr = registration, "lock_actor"
    creator_original = getattr(creator_module, creator_attr)
    def foundation_guard(request, db, *roles):
        if first_operation == "create":
            waiting.set()
        actor = foundation_original(request, db, *roles)
        if first_operation == "disable":
            locked.set()
            assert release.wait(timeout=10)
        return actor
    def creator_guard(request, db, *roles):
        if first_operation == "disable":
            waiting.set()
        actor = creator_original(request, db, *roles)
        if first_operation == "create":
            locked.set()
            assert release.wait(timeout=10)
        return actor
    monkeypatch.setattr(foundation, "lock_and_recheck", foundation_guard)
    monkeypatch.setattr(creator_module, creator_attr, creator_guard)
    value = patient_system.admin.get("/api/admin/" + kind + "/1").json()
    change = {field: value[field] for field in (("name", "is_active", "version") if kind == "departments" else foundation.CATALOG_FIELDS + ("version",))}
    change["is_active"] = False
    if entrypoint == "/api/exams":
        payload = body_for(patient_system)
    else:
        payload = {"patient": {"name": "并发登记虚构人员", "sex": "other"}, "package_id": patient_system.package_id, "request_id": uuid4().hex}
    payload["package_version"] = patient_system.admin.get(f"/api/admin/packages/{patient_system.package_id}").json()["version"]
    headers = dict(patient_system.admin.headers)
    def operation(kind_of_op):
        with TestClient(patient_system.app) as client:
            if kind_of_op == "disable":
                return client.put("/api/admin/" + kind + "/1", json=change, headers=headers)
            return client.post(entrypoint, json=payload, headers=headers)
    second_operation = "create" if first_operation == "disable" else "disable"
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(operation, first_operation)
        assert locked.wait(timeout=10)
        second = pool.submit(operation, second_operation)
        assert waiting.wait(timeout=10)
        release.set()
        responses = {first_operation: first.result(timeout=15), second_operation: second.result(timeout=15)}
    assert responses["disable"].status_code == 200
    assert responses["create"].status_code == (409 if first_operation == "disable" else 200)
    assert counts(patient_system)[3:] == ((0, 0) if first_operation == "disable" else (1, 1))
    assert patient_system.admin.get("/api/packages").json() == []
    if first_operation == "create":
        result = responses["create"].json()
        exam = result if entrypoint == "/api/exams" else result["exam"]
        assert patient_system.admin.get(f"/api/exams/{exam['id']}").json() == exam


def test_populated_0005_upgrade_adds_defaults_without_changing_any_old_column(tmp_path):
    url = "sqlite:///" + (tmp_path / "populated_0005.db").as_posix()
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.attributes["database_url"] = url
    command.upgrade(config, "0005")
    engine, _ = create_database(url)
    old = MetaData()
    old.reflect(engine)
    now = datetime(2026, 10, 1, 16, 30)
    with engine.begin() as conn:
        conn.execute(old.tables["users"].insert(), {"id": 1, "username": "phase7_migrate", "display_name": "迁移软件验收", "password_hash": "invalid-marker", "role": "admin", "department_id": None, "is_active": True})
        conn.execute(old.tables["departments"].insert(), {"id": 1, "name": "旧科室"})
        conn.execute(old.tables["catalog_items"].insert(), {"id": 1, "name": "旧项目", "department_id": 1, "unit": "旧单位", "reference_range": "旧范围"})
        conn.execute(old.tables["packages"].insert(), {"id": 1, "name": "旧套餐", "description": "旧描述", "is_active": True})
        conn.execute(old.tables["package_items"].insert(), {"package_id": 1, "item_id": 1})
        conn.execute(old.tables["patients"].insert(), {"id": 1, "name": "旧人员", "sex": "other", "created_at": now, "updated_at": now, "version": 1, "ethnicity": "旧民族", "address": "旧地址"})
        conn.execute(old.tables["exams"].insert(), {"id": 1, "exam_number": "OLD-PHASE7-IMMUTABLE", "request_id": "old-phase7-request", "requested_by": 1, "patient_id": 1, "package_id": 1, "patient_snapshot": {"id": 1, "name": "旧快照"}, "package_name": "旧套餐", "status": "completed", "version": 3, "conclusion": "旧结论", "completed_at": now, "latest_report_revision": 1, "created_at": now, "updated_at": now, "exam_type": "个人体检", "note": "旧备注"})
        conn.execute(old.tables["exam_items"].insert(), {"id": 1, "exam_id": 1, "item_name": "旧项目", "department_id": 1, "department_name": "旧科室", "unit": "旧单位", "reference_range": "旧范围", "value": "旧值", "is_abnormal": True, "note": "旧备注", "status": "completed", "version": 2})
        conn.execute(old.tables["result_revisions"].insert(), {"id": 1, "item_id": 1, "version": 2, "value": "旧值", "is_abnormal": True, "note": "旧备注", "changed_by": 1, "changed_by_name": "旧医生", "changed_at": now})
        conn.execute(old.tables["report_snapshots"].insert(), {"id": 1, "exam_id": 1, "revision": 1, "payload": {"conclusion": "旧结论", "patient": {"name": "旧快照"}}, "created_at": now})
    with engine.connect() as conn:
        before = {name: conn.execute(table.select()).all() for name, table in old.tables.items()}
    command.upgrade(config, "head")
    command.check(config)
    with engine.connect() as conn:
        for name, table in old.tables.items():
            if name != "alembic_version":
                assert conn.execute(table.select()).all() == before[name]
        assert conn.execute(text("SELECT is_active,version FROM departments")).one() == (1, 1)
        assert conn.execute(text("SELECT category,result_type,abnormal_allowed,is_active,version FROM catalog_items")).one() == ("", "text", 1, 1, 1)
        assert conn.execute(text("SELECT category,result_type,abnormal_allowed FROM exam_items")).one() == ("", "text", 1)
        assert conn.execute(text("PRAGMA foreign_key_check")).all() == []
        assert conn.execute(text("PRAGMA integrity_check")).scalar_one() == "ok"
    engine.dispose()


@pytest.mark.parametrize("url", ["sqlite:///offline.db", "postgresql+psycopg://offline:unused@localhost/offline"])
def test_offline_0006_sql_compiles_without_rebuilding_history(url):
    output = StringIO()
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"), output_buffer=output)
    config.attributes["database_url"] = url
    command.upgrade(config, "0005:0006", sql=True)
    sql = output.getvalue()
    assert "ADD COLUMN result_type VARCHAR(20)" in sql and "CREATE TABLE foundation_requests" in sql
    assert "DROP TABLE" not in sql and "_alembic_tmp" not in sql and "UPDATE report_snapshots" not in sql


def test_real_catalog_request_unique_collision_replays_only_matching_committed_row(patient_system, monkeypatch):
    value, body = catalog(patient_system)
    original = foundation.replay
    calls = []
    def stale(db, body, actor, kind):
        calls.append(True)
        return None if len(calls) == 1 else original(db, body, actor, kind)
    monkeypatch.setattr(foundation, "replay", stale)
    replayed = patient_system.admin.post("/api/admin/catalog-items", json=body)
    assert replayed.status_code == 200 and replayed.json() == value
    assert len(calls) == 2 and counts(patient_system)[:3] == (1, 2, 1)


def test_original_exam_endpoint_rechecks_session_after_waiting_for_anchor(patient_system, monkeypatch):
    payload = body_for(patient_system)
    original = main.lock_and_recheck
    def revoked(request, db, *roles):
        from app.models import User
        with patient_system.app.state.Session.begin() as separate:
            actor = separate.scalar(select(User).where(User.role == "admin"))
            actor.is_active = False
        return original(request, db, *roles)
    monkeypatch.setattr(main, "lock_and_recheck", revoked)
    assert patient_system.admin.post("/api/exams", json=payload).status_code == 401
    assert counts(patient_system)[3:] == (0, 0)


def test_foundation_records_and_configuration_survive_application_restart(patient_system):
    dep, dep_body = department(patient_system)
    item, item_body = catalog(patient_system, department_id=dep["id"], result_type="boolean", abnormal_allowed=False)
    from app.main import create_app
    restarted = create_app(settings=patient_system.settings)
    try:
        with TestClient(restarted) as client:
            headers = dict(patient_system.admin.headers)
            assert client.get(f"/api/admin/departments/{dep['id']}", headers=headers).json() == dep
            assert client.get(f"/api/admin/catalog-items/{item['id']}", headers=headers).json() == item
            assert client.post("/api/admin/departments", json=dep_body, headers=headers).json() == dep
            assert client.post("/api/admin/catalog-items", json=item_body, headers=headers).json() == item
    finally:
        restarted.state.engine.dispose()
    assert counts(patient_system)[:3] == (2, 2, 2)
