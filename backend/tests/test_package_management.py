"""Package templates, paginated selection and preserved visit snapshots."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import hashlib
from io import StringIO
import json
from pathlib import Path
from threading import Barrier, Event
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import MetaData, event, func, select, text
from sqlalchemy.exc import IntegrityError

from test_patients import create, patient_system, patients_password_hash
from test_exam_registration import body_for, register
from test_complete_registration import body as registration_body, save as register_whole
from test_foundation_management import catalog, department, edit_catalog, edit_department
from app import main, package_management as management, registration
from app.database import create_database
from app.models import AuditLog, CatalogItem, Exam, ExamItem, Package, PackageItem, PackageRequest, Patient, RegistrationRequest, ReportSnapshot, ResultRevision, User
from app.schemas import RegistrationCreate


def create_package(system, **changes):
    body = {"name": "阶段八软件验收套餐", "description": "软件验收说明，不含真实资料", "item_ids": [1], "is_active": True, "request_id": uuid4().hex}
    body.update(changes)
    response = system.admin.post("/api/admin/packages", json=body)
    assert response.status_code == 200, response.text
    return response.json(), body


def edit_package(system, record, **changes):
    body = {field: record[field] for field in ("name", "description", "item_ids", "is_active", "version")}
    body.update(changes)
    return system.admin.put(f"/api/admin/packages/{record['id']}", json=body)


def counts(system):
    with system.app.state.Session() as db:
        return tuple(db.scalar(select(func.count()).select_from(model)) for model in (Package, PackageItem, PackageRequest, Exam, ExamItem))


def test_multi_item_create_sorted_replay_edit_noop_and_version_conflict(patient_system):
    other, _ = catalog(patient_system)
    record, body = create_package(patient_system, name="  阶段八软件验收套餐  ", description="  测试描述  ", item_ids=[other["id"], 1])
    assert record["name"] == "阶段八软件验收套餐" and record["description"] == "测试描述"
    assert record["item_ids"] == [1, other["id"]] and record["item_count"] == 2 and record["version"] == 1
    assert record["is_available"] and record["unavailable_reason"] == "" and len(record["items"]) == 2
    assert patient_system.admin.post("/api/admin/packages", json=dict(body, item_ids=[1, other["id"]])).json() == record
    updated = edit_package(patient_system, record, name="已编辑套餐", description="已改说明", item_ids=[other["id"]])
    assert updated.status_code == 200 and updated.json()["version"] == 2
    assert updated.json()["item_count"] == 1 and updated.json()["items"][0]["id"] == other["id"]
    assert edit_package(patient_system, updated.json()).json() == updated.json()
    assert edit_package(patient_system, record, name="过期修改").status_code == 409
    assert patient_system.admin.post("/api/admin/packages", json=body).json() == updated.json()
    assert counts(patient_system)[:3] == (2, 2, 1)


def test_name_duplicate_and_create_request_content_or_actor_conflicts(patient_system):
    record, body = create_package(patient_system)
    assert patient_system.admin.post("/api/admin/packages", json=dict(body, request_id=uuid4().hex)).status_code == 409
    assert patient_system.admin.post("/api/admin/packages", json=dict(body, name="另一内容")).status_code == 409
    assert patient_system.client_for("admin").post("/api/admin/packages", json=body).status_code == 409
    initial = patient_system.admin.get(f"/api/admin/packages/{patient_system.package_id}").json()
    assert edit_package(patient_system, initial, name=record["name"]).status_code == 409
    assert counts(patient_system)[:3] == (2, 2, 1)


def test_restart_persists_package_fields_associations_and_create_replay(patient_system):
    record, body = create_package(patient_system)
    restarted = main.create_app(settings=patient_system.settings)
    try:
        with TestClient(restarted) as client:
            headers = dict(patient_system.admin.headers)
            assert client.get(f"/api/admin/packages/{record['id']}", headers=headers).json() == record
            assert client.post("/api/admin/packages", json=body, headers=headers).json() == record
    finally:
        restarted.state.engine.dispose()


@pytest.mark.parametrize("changes", [{"name": " "}, {"name": "x" * 101}, {"description": "x" * 2001}, {"description": None}, {"item_ids": []}, {"item_ids": [1, 1]}, {"item_ids": [0]}, {"item_ids": [True]}, {"item_ids": ["1"]}, {"item_ids": list(range(1, 202))}, {"item_ids": [999]}, {"is_active": "true"}, {"unknown": 1}])
def test_invalid_create_input_has_no_package_request_or_association_write(patient_system, changes):
    body = {"name": "不可创建", "item_ids": [1], "request_id": uuid4().hex}
    body.update(changes)
    assert patient_system.admin.post("/api/admin/packages", json=body).status_code == 422
    assert counts(patient_system)[:3] == (1, 1, 0)


@pytest.mark.parametrize("role", ["registrar", "doctor", "reviewer"])
def test_administrator_permissions_for_package_reads_and_writes(patient_system, role):
    client = patient_system.client_for(role)
    assert client.get("/api/admin/packages").status_code == 403
    assert client.get("/api/admin/packages/1").status_code == 403
    assert client.post("/api/admin/packages", json={"name": "禁止", "item_ids": [1], "request_id": uuid4().hex}).status_code == 403
    assert client.put("/api/admin/packages/1", json={"name": "禁止", "item_ids": [1], "version": 1}).status_code == 403
    assert client.get("/api/registration/packages").status_code == (200 if role == "registrar" else 403)
    assert client.get("/api/registration/packages/1").status_code == (200 if role == "registrar" else 403)


def test_missing_auth_csrf_and_post_anchor_role_revocation_refuse_writes(patient_system, monkeypatch):
    body = {"name": "禁止", "item_ids": [1], "request_id": uuid4().hex}
    with TestClient(patient_system.app) as client:
        assert client.post("/api/admin/packages", json=body).status_code == 401
        headers = dict(patient_system.admin.headers)
        headers.pop("x-csrf-token")
        assert client.post("/api/admin/packages", json=body, headers=headers).status_code == 403
    original = management.lock_and_recheck
    def revoked(request, db, *roles):
        with patient_system.app.state.Session.begin() as other:
            actor = other.scalar(select(User).where(User.role == "admin"))
            actor.role = "registrar"
        return original(request, db, *roles)
    monkeypatch.setattr(management, "lock_and_recheck", revoked)
    assert patient_system.admin.post("/api/admin/packages", json=body).status_code == 403
    assert counts(patient_system)[:3] == (1, 1, 0)


def test_create_same_request_concurrently_saves_one_package_relation_set_and_audit(patient_system, monkeypatch):
    meet = Barrier(2)
    original = management.lock_and_recheck
    def lock(request, db, *roles):
        meet.wait(timeout=10)
        return original(request, db, *roles)
    monkeypatch.setattr(management, "lock_and_recheck", lock)
    body = {"name": "并发软件验收套餐", "item_ids": [1], "request_id": uuid4().hex}
    headers = dict(patient_system.admin.headers)
    def save(_):
        with TestClient(patient_system.app) as client:
            return client.post("/api/admin/packages", json=body, headers=headers)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(save, range(2)))
    assert [r.status_code for r in responses] == [200, 200] and responses[0].json() == responses[1].json()
    assert counts(patient_system)[:3] == (2, 2, 1)
    with patient_system.app.state.Session() as db:
        assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "package.create")) == 1


def test_admin_paging_summary_and_registration_filter_before_count_limit_and_search(patient_system):
    extra, _ = catalog(patient_system, name="失效成员")
    for index in range(21):
        record, _ = create_package(patient_system, name=f"软件套餐{index:02}", description="可搜索说明", item_ids=[extra["id"]] if index == 20 else [1])
        if index == 19:
            assert edit_package(patient_system, record, is_active=False).status_code == 200
    assert edit_catalog(patient_system, extra, is_active=False).status_code == 200
    first = patient_system.admin.get("/api/admin/packages").json()
    assert first["total"] == 22 and len(first["items"]) == 20
    assert all("items" not in record and "item_ids" not in record for record in first["items"])
    assert len(patient_system.admin.get("/api/admin/packages?page=2").json()["items"]) == 2
    assert patient_system.admin.get("/api/admin/packages?is_active=false").json()["total"] == 1
    picker = patient_system.client_for("registrar").get("/api/registration/packages").json()
    assert picker["total"] == 20 and len(picker["items"]) == 20 and all(row["is_available"] for row in picker["items"])
    assert patient_system.admin.get("/api/registration/packages?page=2").json()["items"] == []
    for path in ("/api/admin/packages", "/api/registration/packages"):
        assert patient_system.admin.get(path + "?page_size=101").status_code == 422
        assert patient_system.admin.get(path + "?page=0").status_code == 422
        assert patient_system.admin.get(path + "?q=%25").json()["total"] == 0
    assert patient_system.admin.get("/api/admin/packages?q=可搜索说明").json()["total"] == 21
    assert patient_system.admin.get("/api/registration/packages?q=可搜索说明").json()["total"] == 19
    assert patient_system.admin.get(f"/api/registration/packages/{record['id']}").status_code == 422


def test_old_empty_or_invalid_package_metadata_disable_remove_and_repair(patient_system):
    with patient_system.app.state.Session.begin() as db:
        old = Package(name="旧空套餐", description="旧元资料", is_active=True, items=[])
        db.add(old)
        db.flush()
        old_id = old.id
    record = patient_system.admin.get(f"/api/admin/packages/{old_id}").json()
    assert not record["is_available"] and record["item_count"] == 0
    renamed = edit_package(patient_system, record, description="旧空元资料维护")
    assert renamed.status_code == 200
    disabled = edit_package(patient_system, renamed.json(), is_active=False)
    assert disabled.status_code == 200
    assert edit_package(patient_system, disabled.json(), is_active=True).status_code == 422
    repaired = edit_package(patient_system, disabled.json(), item_ids=[1], is_active=True)
    assert repaired.status_code == 200 and repaired.json()["is_available"]
    initial = patient_system.admin.get(f"/api/admin/packages/{patient_system.package_id}").json()
    item = patient_system.admin.get("/api/admin/catalog-items/1").json()
    assert edit_catalog(patient_system, item, is_active=False).status_code == 200
    initial = patient_system.admin.get(f"/api/admin/packages/{patient_system.package_id}").json()
    renamed_invalid = edit_package(patient_system, initial, description="保留旧失效成员维护")
    assert renamed_invalid.status_code == 200 and not renamed_invalid.json()["is_available"]
    inactive = edit_package(patient_system, renamed_invalid.json(), is_active=False)
    assert inactive.status_code == 200
    assert edit_package(patient_system, inactive.json(), is_active=True).status_code == 422
    other, _ = catalog(patient_system, name="有效修复成员")
    assert edit_package(patient_system, inactive.json(), item_ids=[other["id"]], is_active=True).status_code == 200
    assert patient_system.admin.post("/api/admin/packages", json={"name": "不准新增失效关联", "item_ids": [1], "is_active": False, "request_id": uuid4().hex}).status_code == 422


@pytest.mark.parametrize("action", ["package.create", "package.update"])
def test_audit_failure_rolls_back_package_association_and_version_or_request(patient_system, action):
    other, _ = catalog(patient_system)
    record, body = create_package(patient_system)
    before = counts(patient_system)
    def fail(mapper, connection, target):
        if target.action == action:
            raise IntegrityError("private_sql", {}, Exception("private_error"))
    event.listen(AuditLog, "before_insert", fail)
    try:
        if action == "package.create":
            response = patient_system.admin.post("/api/admin/packages", json=dict(body, name="未保存新套餐", item_ids=[other["id"]], request_id=uuid4().hex))
        else:
            response = edit_package(patient_system, record, item_ids=[other["id"]], description="未保存修改")
    finally:
        event.remove(AuditLog, "before_insert", fail)
    assert response.status_code == 503 and counts(patient_system) == before
    assert patient_system.admin.get(f"/api/admin/packages/{record['id']}").json() == record


def test_commit_failure_never_claims_success_or_preserves_partial_package_links(patient_system):
    record, body = create_package(patient_system)
    other, _ = catalog(patient_system)
    before = counts(patient_system)
    def fail(db):
        if not db.in_nested_transaction():
            raise IntegrityError("private_commit", {}, Exception("private_error"))
    event.listen(patient_system.app.state.Session.class_, "before_commit", fail)
    try:
        assert edit_package(patient_system, record, item_ids=[other["id"]]).status_code == 503
        assert patient_system.admin.post("/api/admin/packages", json=dict(body, name="不成功新建", request_id=uuid4().hex)).status_code == 503
    finally:
        event.remove(patient_system.app.state.Session.class_, "before_commit", fail)
    assert counts(patient_system) == before and patient_system.admin.get(f"/api/admin/packages/{record['id']}").json() == record


def test_package_change_and_disable_never_rewrites_registered_items_results_or_report(patient_system):
    other, _ = catalog(patient_system, name="第二阶段八软件项目", result_type="boolean", abnormal_allowed=False)
    package, _ = create_package(patient_system, item_ids=[1, other["id"]])
    patient = create(patient_system, identity_number=None)
    request = body_for(patient_system, patient["id"], package_id=package["id"], package_version=package["version"])
    first = register(patient_system, request)
    assert first["package_version"] == 1 and len(first["items"]) == 2
    assert len({item["id"] for item in first["items"]}) == 2
    for item in first["items"]:
        assert patient_system.admin.put(f"/api/exam-items/{item['id']}", json={"value": "否" if item["result_type"] == "boolean" else "软件验收值", "version": item["version"]}).status_code == 200
    ready = patient_system.admin.get(f"/api/exams/{first['id']}").json()
    assert patient_system.admin.post(f"/api/exams/{first['id']}/finalize", json={"version": ready["version"], "conclusion": "阶段八软件验收结论"}).status_code == 200
    completed = patient_system.admin.get(f"/api/exams/{first['id']}").json()
    report = patient_system.admin.get(f"/api/exams/{first['id']}/report").json()
    with patient_system.app.state.Session() as db:
        old_rows = tuple(tuple(db.scalars(select(model).where(model.exam_id == first["id"]))) if model is ReportSnapshot else tuple(db.scalars(select(model).where(model.item_id.in_([row["id"] for row in first["items"]])))) for model in (ReportSnapshot, ResultRevision))
        old_report = dict(old_rows[0][0].payload)
        old_revisions = [(row.id, row.item_id, row.value, row.version) for row in old_rows[1]]
    changed = edit_package(patient_system, package, name="新版套餐名", description="新版说明", item_ids=[other["id"]])
    assert changed.status_code == 200
    assert patient_system.admin.get(f"/api/exams/{first['id']}").json() == completed
    assert patient_system.admin.get(f"/api/exams/{first['id']}/report").json() == report
    assert register(patient_system, request) == completed
    assert patient_system.admin.post("/api/exams", json=dict(request, request_id=uuid4().hex)).status_code == 409
    second = register(patient_system, dict(request, request_id=uuid4().hex, package_version=2))
    assert second["package_name"] == "新版套餐名" and second["package_version"] == 2 and len(second["items"]) == 1
    disabled = edit_package(patient_system, changed.json(), is_active=False)
    assert disabled.status_code == 200
    assert register(patient_system, request) == completed
    assert patient_system.admin.get(f"/api/exams/{second['id']}").json() == second
    with patient_system.app.state.Session() as db:
        assert db.scalar(select(ReportSnapshot).where(ReportSnapshot.exam_id == first["id"])).payload == old_report
        revisions = db.scalars(select(ResultRevision).where(ResultRevision.item_id.in_([row["id"] for row in first["items"]])))
        assert [(row.id, row.item_id, row.value, row.version) for row in revisions] == old_revisions


def test_whole_registration_package_version_replay_and_legacy_none_fingerprint_compatibility(patient_system):
    package, _ = create_package(patient_system)
    old_body = registration_body(package_id=package["id"])
    first = register_whole(patient_system, old_body)
    model = RegistrationCreate.model_validate(old_body)
    old_encoded = json.dumps(model.model_dump(mode="json", exclude={"request_id", "package_version"}), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    old_hash = hashlib.sha256(old_encoded.encode("utf-8")).hexdigest()
    with patient_system.app.state.Session.begin() as db:
        request = db.get(RegistrationRequest, old_body["request_id"])
        assert request.fingerprint == old_hash
        request.fingerprint = old_hash
    assert register_whole(patient_system, old_body) == first
    explicit_body = dict(old_body, request_id=uuid4().hex, package_version=package["version"])
    second = register_whole(patient_system, explicit_body)
    modified = edit_package(patient_system, package, description="管理员已改套餐")
    assert modified.status_code == 200
    assert register_whole(patient_system, explicit_body) == second
    assert patient_system.admin.post("/api/registrations", json=dict(explicit_body, request_id=uuid4().hex)).status_code == 409
    assert patient_system.admin.post("/api/registrations", json=dict(explicit_body, package_version=2)).status_code == 409
    assert edit_package(patient_system, modified.json(), is_active=False).status_code == 200
    assert register_whole(patient_system, old_body) == first and register_whole(patient_system, explicit_body) == second


@pytest.mark.parametrize("path", ["/api/exams", "/api/registrations"])
def test_version_without_package_id_is_invalid_and_legacy_without_version_works(patient_system, path):
    payload = body_for(patient_system, package_id=None, package_version=1) if path == "/api/exams" else registration_body(package_id=None, package_version=1)
    assert patient_system.admin.post(path, json=payload).status_code == 422
    payload.pop("package_version")
    response = patient_system.admin.post(path, json=payload)
    assert response.status_code == 200
    exam = response.json() if path == "/api/exams" else response.json()["exam"]
    assert exam["package_id"] is None and exam["package_version"] is None


@pytest.mark.parametrize("change", ["modify", "disable"])
@pytest.mark.parametrize("entrypoint", ["/api/exams", "/api/registrations"])
@pytest.mark.parametrize("first_operation", ["update", "create"])
def test_real_package_update_and_registration_concurrency_uses_one_order(patient_system, monkeypatch, change, entrypoint, first_operation):
    package, _ = create_package(patient_system)
    locked, waiting, release = Event(), Event(), Event()
    original_update = management.lock_and_recheck
    creator_module, creator_attr = (main, "lock_and_recheck") if entrypoint == "/api/exams" else (registration, "lock_actor")
    original_create = getattr(creator_module, creator_attr)
    def update_guard(request, db, *roles):
        if first_operation == "create":
            waiting.set()
        actor = original_update(request, db, *roles)
        if first_operation == "update":
            locked.set()
            assert release.wait(timeout=10)
        return actor
    def create_guard(request, db, *roles):
        if first_operation == "update":
            waiting.set()
        actor = original_create(request, db, *roles)
        if first_operation == "create":
            locked.set()
            assert release.wait(timeout=10)
        return actor
    monkeypatch.setattr(management, "lock_and_recheck", update_guard)
    monkeypatch.setattr(creator_module, creator_attr, create_guard)
    update_body = {field: package[field] for field in ("name", "description", "item_ids", "is_active", "version")}
    update_body["is_active" if change == "disable" else "description"] = False if change == "disable" else "已改套餐内容"
    payload = body_for(patient_system, package_id=package["id"], package_version=package["version"]) if entrypoint == "/api/exams" else registration_body(package_id=package["id"], package_version=package["version"])
    headers = dict(patient_system.admin.headers)
    def perform(operation):
        with TestClient(patient_system.app) as client:
            return (client.put(f"/api/admin/packages/{package['id']}", json=update_body, headers=headers) if operation == "update"
                    else client.post(entrypoint, json=payload, headers=headers))
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(perform, first_operation)
        assert locked.wait(timeout=10)
        second_op = "create" if first_operation == "update" else "update"
        second = pool.submit(perform, second_op)
        assert waiting.wait(timeout=10)
        release.set()
        results = {first_operation: first.result(timeout=15), second_op: second.result(timeout=15)}
    assert results["update"].status_code == 200
    expected_create = 200 if first_operation == "create" else 409
    assert results["create"].status_code == expected_create
    assert counts(patient_system)[3:] == ((1, 1) if first_operation == "create" else (0, 0))
    if first_operation == "create":
        result = results["create"].json()
        exam = result if entrypoint == "/api/exams" else result["exam"]
        assert exam["package_version"] == 1
        assert patient_system.admin.get(f"/api/exams/{exam['id']}").json() == exam


def test_populated_0006_migration_preserves_every_old_row_and_adds_nullable_snapshot(tmp_path):
    url = "sqlite:///" + (tmp_path / "populated_0006.db").as_posix()
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.attributes["database_url"] = url
    command.upgrade(config, "0006")
    engine, _ = create_database(url)
    old = MetaData()
    old.reflect(engine)
    now = datetime(2026, 10, 2, 16, 30)
    with engine.begin() as conn:
        conn.execute(old.tables["users"].insert(), {"id": 1, "username": "phase8_migrate", "display_name": "迁移软件验收", "password_hash": "invalid-marker", "role": "admin", "department_id": None, "is_active": True})
        conn.execute(old.tables["departments"].insert(), {"id": 1, "name": "旧科室", "is_active": True, "version": 1})
        conn.execute(old.tables["catalog_items"].insert(), {"id": 1, "name": "旧项目", "department_id": 1, "unit": "旧单位", "reference_range": "旧范围", "category": "旧分类", "result_type": "text", "abnormal_allowed": True, "is_active": True, "version": 1})
        conn.execute(old.tables["packages"].insert(), {"id": 1, "name": "旧套餐", "description": "旧描述", "is_active": True})
        conn.execute(old.tables["package_items"].insert(), {"package_id": 1, "item_id": 1})
        conn.execute(old.tables["patients"].insert(), {"id": 1, "name": "旧人员", "sex": "other", "created_at": now, "updated_at": now, "version": 1})
        conn.execute(old.tables["exams"].insert(), {"id": 1, "exam_number": "OLD-PHASE8-IMMUTABLE", "request_id": "old-phase8-request", "requested_by": 1, "patient_id": 1, "package_id": 1, "patient_snapshot": {"id": 1, "name": "旧快照"}, "package_name": "旧套餐", "status": "completed", "version": 3, "conclusion": "旧结论", "completed_at": now, "latest_report_revision": 1, "created_at": now, "updated_at": now, "exam_type": "个人体检"})
        conn.execute(old.tables["exam_items"].insert(), {"id": 1, "exam_id": 1, "item_name": "旧项目", "department_id": 1, "department_name": "旧科室", "unit": "旧单位", "reference_range": "旧范围", "value": "旧值", "is_abnormal": True, "status": "completed", "version": 2, "category": "旧分类", "result_type": "text", "abnormal_allowed": True})
        conn.execute(old.tables["result_revisions"].insert(), {"id": 1, "item_id": 1, "version": 2, "value": "旧值", "is_abnormal": True, "changed_by": 1, "changed_by_name": "旧医生", "changed_at": now})
        conn.execute(old.tables["report_snapshots"].insert(), {"id": 1, "exam_id": 1, "revision": 1, "payload": {"conclusion": "旧结论", "patient": {"name": "旧快照"}}, "created_at": now})
    with engine.connect() as conn:
        before = {name: conn.execute(table.select()).all() for name, table in old.tables.items()}
    command.upgrade(config, "head")
    command.check(config)
    with engine.connect() as conn:
        for name, table in old.tables.items():
            if name != "alembic_version":
                assert conn.execute(table.select()).all() == before[name]
        assert conn.execute(text("SELECT version FROM packages")).scalar_one() == 1
        assert conn.execute(text("SELECT package_version FROM exams")).scalar_one() is None
        assert conn.execute(text("PRAGMA foreign_key_check")).all() == []
        assert conn.execute(text("PRAGMA integrity_check")).scalar_one() == "ok"
    engine.dispose()


@pytest.mark.parametrize("url", ["sqlite:///offline.db", "postgresql+psycopg://offline:unused@localhost/offline"])
def test_0007_offline_sql_is_additive_and_portable(url):
    output = StringIO()
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"), output_buffer=output)
    config.attributes["database_url"] = url
    command.upgrade(config, "0006:0007", sql=True)
    sql = output.getvalue()
    assert "ADD COLUMN package_version INTEGER" in sql and "CREATE TABLE package_requests" in sql
    assert "DROP TABLE" not in sql and "_alembic_tmp" not in sql and "UPDATE report_snapshots" not in sql


def test_real_create_request_unique_collision_recovers_current_matching_record(patient_system, monkeypatch):
    original_record, body = create_package(patient_system)
    changed = edit_package(patient_system, original_record, name="原请求套餐已经更名")
    assert changed.status_code == 200
    original = management.replay
    calls = []
    def stale(db, body, actor):
        calls.append(True)
        return None if len(calls) == 1 else original(db, body, actor)
    monkeypatch.setattr(management, "replay", stale)
    response = patient_system.admin.post("/api/admin/packages", json=body)
    assert response.status_code == 200 and response.json() == changed.json()
    assert len(calls) == 2 and counts(patient_system)[:3] == (2, 2, 1)


def test_concurrent_same_version_updates_do_not_overwrite_package_links(patient_system, monkeypatch):
    second_item, _ = catalog(patient_system)
    package, _ = create_package(patient_system)
    meet = Barrier(2)
    original = management.lock_and_recheck
    def lock(request, db, *roles):
        meet.wait(timeout=10)
        return original(request, db, *roles)
    monkeypatch.setattr(management, "lock_and_recheck", lock)
    original_body = {field: package[field] for field in ("name", "description", "item_ids", "is_active", "version")}
    payloads = [dict(original_body, description="并发甲", item_ids=[1, second_item["id"]]), dict(original_body, description="并发乙", item_ids=[second_item["id"]])]
    headers = dict(patient_system.admin.headers)
    def save(body):
        with TestClient(patient_system.app) as client:
            return client.put(f"/api/admin/packages/{package['id']}", json=body, headers=headers)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(save, payloads))
    assert sorted(response.status_code for response in responses) == [200, 409]
    current = patient_system.admin.get(f"/api/admin/packages/{package['id']}").json()
    winner = next(response.json() for response in responses if response.status_code == 200)
    assert current == winner and current["version"] == 2
    assert current["item_ids"] == ([1, second_item["id"]] if current["description"] == "并发甲" else [second_item["id"]])


def test_empty_removal_requires_disabling_and_missing_package_is_safe(patient_system):
    package, _ = create_package(patient_system)
    assert edit_package(patient_system, package, item_ids=[]).status_code == 422
    disabled = edit_package(patient_system, package, item_ids=[], is_active=False)
    assert disabled.status_code == 200 and disabled.json()["item_count"] == 0
    assert not disabled.json()["is_available"]
    for path in ("/api/admin/packages/999999", "/api/registration/packages/999999"):
        assert patient_system.admin.get(path).status_code == 404
    assert patient_system.admin.put("/api/admin/packages/999999", json={"name": "不存在", "item_ids": [1], "version": 1}).status_code == 404


def test_package_relation_primary_key_rejects_direct_duplicates_in_database(patient_system):
    with pytest.raises(IntegrityError):
        with patient_system.app.state.Session.begin() as db:
            db.add(PackageItem(package_id=patient_system.package_id, item_id=1))
    assert counts(patient_system)[:2] == (1, 1)


def test_two_department_package_instantiates_once_and_doctors_see_only_their_items(patient_system):
    second_dep, _ = department(patient_system, name="阶段八第二软件科室")
    second_item, _ = catalog(patient_system, department_id=second_dep["id"], name="阶段八第二科室软件项目", result_type="boolean", abnormal_allowed=False)
    package, _ = create_package(patient_system, item_ids=[second_item["id"], 1])
    payload = body_for(patient_system, package_id=package["id"], package_version=package["version"])
    exam = register(patient_system, payload)
    assert len(exam["items"]) == 2 and {item["department_id"] for item in exam["items"]} == {1, second_dep["id"]}
    assert register(patient_system, payload) == exam and counts(patient_system)[3:] == (1, 2)
    doctor_one = patient_system.client_for("doctor")
    doctor_two = patient_system.client_for("doctor")
    second_user_id = doctor_two.get("/api/auth/me").json()["user"]["id"]
    with patient_system.app.state.Session.begin() as db:
        db.get(User, second_user_id).department_id = second_dep["id"]
    own_one = doctor_one.get(f"/api/exams/{exam['id']}").json()["items"]
    own_two = doctor_two.get(f"/api/exams/{exam['id']}").json()["items"]
    assert len(own_one) == len(own_two) == 1 and own_one[0]["id"] != own_two[0]["id"]
    assert doctor_one.put(f"/api/exam-items/{own_two[0]['id']}", json={"value": "是", "version": 1}).status_code == 403
    assert doctor_two.put(f"/api/exam-items/{own_two[0]['id']}", json={"value": "否", "version": 1}).status_code == 200


def test_source_changes_increment_each_related_package_once_including_disabled_and_noop_skips(patient_system):
    second, _ = catalog(patient_system, name="同科室第二软件项目")
    other_dep, _ = department(patient_system, name="不受影响的软件科室")
    unrelated_item, _ = catalog(patient_system, name="不受影响的软件项目", department_id=other_dep["id"])
    multiple, _ = create_package(patient_system, name="同科室多项套餐", item_ids=[1, second["id"]])
    disabled, _ = create_package(patient_system, name="关联已停用套餐", is_active=False)
    unrelated, _ = create_package(patient_system, name="不受影响套餐", item_ids=[unrelated_item["id"]])
    first_item = patient_system.admin.get("/api/admin/catalog-items/1").json()
    changed = edit_catalog(patient_system, first_item, name="软件项目改名")
    assert changed.status_code == 200
    affected_ids = [patient_system.package_id, multiple["id"], disabled["id"]]
    assert [patient_system.admin.get(f"/api/admin/packages/{value}").json()["version"] for value in affected_ids] == [2, 2, 2]
    assert patient_system.admin.get(f"/api/admin/packages/{unrelated['id']}").json()["version"] == 1
    assert edit_catalog(patient_system, changed.json()).status_code == 200
    assert [patient_system.admin.get(f"/api/admin/packages/{value}").json()["version"] for value in affected_ids] == [2, 2, 2]
    original_dep = patient_system.admin.get("/api/admin/departments/1").json()
    renamed = edit_department(patient_system, original_dep, name="同科室改名")
    assert renamed.status_code == 200
    assert [patient_system.admin.get(f"/api/admin/packages/{value}").json()["version"] for value in affected_ids] == [3, 3, 3]
    assert patient_system.admin.get(f"/api/admin/packages/{unrelated['id']}").json()["version"] == 1
    assert edit_department(patient_system, renamed.json()).status_code == 200
    assert [patient_system.admin.get(f"/api/admin/packages/{value}").json()["version"] for value in affected_ids] == [3, 3, 3]


@pytest.mark.parametrize("kind", ["department", "catalog_item"])
@pytest.mark.parametrize("failure", ["audit", "commit"])
def test_source_write_and_related_package_version_increment_roll_back_together(patient_system, kind, failure):
    path = "/api/admin/departments/1" if kind == "department" else "/api/admin/catalog-items/1"
    before_source = patient_system.admin.get(path).json()
    before_package = patient_system.admin.get(f"/api/admin/packages/{patient_system.package_id}").json()
    def fail_audit(mapper, connection, target):
        if target.action == kind + ".update":
            raise IntegrityError("private_source_sql", {}, Exception("private_error"))
    def fail_commit(db):
        if not db.in_nested_transaction():
            raise IntegrityError("private_commit", {}, Exception("private_error"))
    if failure == "audit":
        event.listen(AuditLog, "before_insert", fail_audit)
    else:
        event.listen(patient_system.app.state.Session.class_, "before_commit", fail_commit)
    try:
        response = edit_department(patient_system, before_source, name="不应提交的科室") if kind == "department" else edit_catalog(patient_system, before_source, name="不应提交的项目")
    finally:
        if failure == "audit":
            event.remove(AuditLog, "before_insert", fail_audit)
        else:
            event.remove(patient_system.app.state.Session.class_, "before_commit", fail_commit)
    assert response.status_code == 503
    assert patient_system.admin.get(path).json() == before_source
    assert patient_system.admin.get(f"/api/admin/packages/{patient_system.package_id}").json() == before_package


@pytest.mark.parametrize("kind", ["department", "catalog_item"])
@pytest.mark.parametrize("entrypoint", ["/api/exams", "/api/registrations"])
def test_source_disable_then_restore_permanently_refuses_old_explicit_version(patient_system, kind, entrypoint):
    package = patient_system.admin.get(f"/api/admin/packages/{patient_system.package_id}").json()
    path = "/api/admin/departments/1" if kind == "department" else "/api/admin/catalog-items/1"
    source = patient_system.admin.get(path).json()
    payload = body_for(patient_system, package_version=package["version"]) if entrypoint == "/api/exams" else registration_body(package_id=package["id"], package_version=package["version"])
    disable = edit_department(patient_system, source, is_active=False) if kind == "department" else edit_catalog(patient_system, source, is_active=False)
    assert disable.status_code == 200
    failed = patient_system.admin.post(entrypoint, json=payload)
    assert failed.status_code == 409 and failed.json()["detail"] == "套餐已被修改，请重新读取套餐后核对"
    restored = edit_department(patient_system, disable.json(), is_active=True) if kind == "department" else edit_catalog(patient_system, disable.json(), is_active=True)
    assert restored.status_code == 200
    assert patient_system.admin.post(entrypoint, json=payload).status_code == 409
    current_package = patient_system.admin.get(f"/api/admin/packages/{package['id']}").json()
    assert current_package["version"] == package["version"] + 2
    assert counts(patient_system)[3:] == (0, 0)
    assert patient_system.admin.post(entrypoint, json=dict(payload, request_id=uuid4().hex, package_version=current_package["version"])).status_code == 200


@pytest.mark.parametrize("entrypoint", ["/api/exams", "/api/registrations"])
def test_committed_requests_replay_before_source_changes_and_legacy_none_version_stays_compatible(patient_system, entrypoint):
    package = patient_system.admin.get(f"/api/admin/packages/{patient_system.package_id}").json()
    payload = body_for(patient_system, package_version=package["version"]) if entrypoint == "/api/exams" else registration_body(package_id=package["id"], package_version=package["version"])
    legacy_payload = dict(payload, request_id=uuid4().hex)
    legacy_payload.pop("package_version")
    first = patient_system.admin.post(entrypoint, json=payload)
    legacy = patient_system.admin.post(entrypoint, json=legacy_payload)
    assert first.status_code == legacy.status_code == 200
    item = patient_system.admin.get("/api/admin/catalog-items/1").json()
    changed = edit_catalog(patient_system, item, is_active=False, unit="新软件单位")
    assert changed.status_code == 200
    assert patient_system.admin.post(entrypoint, json=payload).json() == first.json()
    assert patient_system.admin.post(entrypoint, json=legacy_payload).json() == legacy.json()
    fresh_legacy = dict(legacy_payload, request_id=uuid4().hex)
    assert patient_system.admin.post(entrypoint, json=fresh_legacy).status_code == 422
    assert edit_catalog(patient_system, changed.json(), is_active=True).status_code == 200
    assert patient_system.admin.post(entrypoint, json=fresh_legacy).status_code == 200
