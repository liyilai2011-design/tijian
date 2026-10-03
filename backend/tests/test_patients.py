"""Patient profiles, real SQLite transactions and preserved clinical snapshots."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import date, timedelta
from pathlib import Path
import sys
from threading import Barrier, Event, current_thread
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
from app.models import AuditLog, CatalogItem, Department, Exam, Package, Patient, ReportSnapshot, User, local_today, utcnow
from app.security import hash_password, new_session
from app.services import EXAM_PATIENT_FIELDS, PATIENT_FIELDS, patient_record_json

PASSWORD = "Stage-five-isolated-test-password-2026!"
IDENTITY = "11010119900101123X"
RECORD_FIELDS = {"id", "name", "sex", "birth_date", "age", "phone", "identity_number", "employer", "ethnicity", "address", "note", "created_at", "updated_at", "version"}


@pytest.fixture(scope="session")
def patients_password_hash():
    return hash_password(PASSWORD)


@pytest.fixture
def patient_system(tmp_path, monkeypatch, patients_password_hash):
    url = "sqlite:///" + (tmp_path / "isolated_patients.db").as_posix()
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.attributes["database_url"] = url
    command.upgrade(config, "head")
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "reports"))
    from app.main import create_app

    settings = Settings(_env_file=None, database_url=url, data_dir=tmp_path, cookie_secure=False, allowed_origins="http://testserver")
    app = create_app(settings=settings)
    with app.state.Session.begin() as db:
        db.add(Department(id=1, name="软件测试科室"))
        db.flush()
        package = Package(name="档案验收套餐", description="软件验收测试，不含临床参考范围")
        package.items = [CatalogItem(name="软件测试项目", department_id=1, reference_range="仅软件测试")]
        db.add(package)
        db.flush()
        package_id = package.id
    try:
        with ExitStack() as stack:
            def client_for(role="admin", username=None):
                # Auth itself is covered separately; use genuine DB sessions here.
                with app.state.Session.begin() as db:
                    user = User(username=username or "patient_" + uuid4().hex[:10], display_name="档案验收" + role, password_hash=patients_password_hash, role=role, department_id=1 if role == "doctor" else None, is_active=True)
                    db.add(user)
                    db.flush()
                    token, record = new_session(user, 8)
                    db.add(record)
                    csrf = record.csrf_token
                client = stack.enter_context(TestClient(app))
                client.headers.update({"Cookie": settings.session_cookie + "=" + token, "X-CSRF-Token": csrf})
                return client
            admin = client_for()
            yield SimpleNamespace(app=app, admin=admin, client_for=client_for, settings=settings, package_id=package_id, url=url)
    finally:
        app.state.engine.dispose()


def registration(**changes):
    body = {"name": "档案测试姓名", "sex": "other", "birth_date": "1990-01-01", "phone": "13800001234", "identity_number": IDENTITY, "employer": "测试单位", "note": "仅软件验收备注", "request_id": uuid4().hex}
    body.update(changes)
    return body


def create(system, **changes):
    response = system.admin.post("/api/patients", json=registration(**changes))
    assert response.status_code == 200, response.text
    return response.json()


def edit_body(record, **changes):
    body = {field: record[field] for field in PATIENT_FIELDS}
    body["version"] = record["version"]
    body.update(changes)
    return body


def exam_for(system, patient_id):
    response = system.admin.post("/api/exams", json={"patient_id": patient_id, "package_id": system.package_id, "request_id": uuid4().hex})
    assert response.status_code == 200, response.text
    return response.json()


def test_create_detail_edit_normalization_version_and_noop(patient_system):
    patient = create(patient_system, identity_number=" " + IDENTITY.lower() + " ", phone=" 13800001234 ", employer="   ", note="  备注  ")
    assert set(patient) == RECORD_FIELDS
    assert patient["identity_number"] == IDENTITY and patient["employer"] is None and patient["note"] == "备注"
    assert patient["version"] == 1 and patient["created_at"] == patient["updated_at"]
    assert patient["created_at"].endswith("Z")
    assert patient_system.admin.get(f"/api/patients/{patient['id']}").json() == patient
    changed = patient_system.admin.put(f"/api/patients/{patient['id']}", json=edit_body(patient, name="编辑后姓名", note="", birth_date=""))
    assert changed.status_code == 200, changed.text
    current = changed.json()
    assert current["version"] == 2 and current["created_at"] == patient["created_at"]
    assert current["birth_date"] is None and current["age"] is None and current["note"] is None
    noop = patient_system.admin.put(f"/api/patients/{patient['id']}", json=edit_body(current))
    assert noop.status_code == 200 and noop.json() == current
    stale = patient_system.admin.put(f"/api/patients/{patient['id']}", json=edit_body(patient, name="过期写入"))
    assert stale.status_code == 409
    assert patient_system.admin.get(f"/api/patients/{patient['id']}").json() == current


def test_expanded_registration_retry_never_duplicates_or_overwrites(patient_system):
    body = registration()
    first = patient_system.admin.post("/api/patients", json=body)
    assert first.status_code == 200
    assert patient_system.admin.post("/api/patients", json=body).json() == first.json()
    for field in ("identity_number", "employer", "note"):
        changed = dict(body)
        changed[field] = "110101199001011234" if field == "identity_number" else "请求内容已改变"
        assert patient_system.admin.post("/api/patients", json=changed).status_code == 409
    other = patient_system.client_for("registrar")
    assert other.post("/api/patients", json=body).status_code == 409
    record = first.json()
    updated = patient_system.admin.put(f"/api/patients/{record['id']}", json=edit_body(record, note="修改后的备注"))
    assert updated.status_code == 200
    assert patient_system.admin.post("/api/patients", json=body).status_code == 409
    with patient_system.app.state.Session() as db:
        assert db.scalar(select(func.count()).select_from(Patient)) == 1
        assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "patient.create")) == 1


def test_search_and_pagination_are_server_bounded(patient_system):
    first = create(patient_system)
    second = create(patient_system, name="第二档案", phone="13900005678", identity_number="123456789012345")
    create(patient_system, name="搜索%_字面量", identity_number=None, phone=None)
    for query, expected in (("档案测试", first["id"]), ("00001234", first["id"]), (IDENTITY.lower(), first["id"]), ("5678", second["id"]), ("%_", 3)):
        result = patient_system.admin.get("/api/patients", params={"q": query}).json()
        assert result["total"] == 1 and result["items"][0]["id"] == expected
    one = patient_system.admin.get("/api/patients", params={"page_size": 2}).json()
    two = patient_system.admin.get("/api/patients", params={"page_size": 2, "page": 2}).json()
    assert one["total"] == two["total"] == 3 and len(one["items"]) == 2 and len(two["items"]) == 1
    assert {row["id"] for row in one["items"]}.isdisjoint(row["id"] for row in two["items"])
    assert patient_system.admin.get("/api/patients", params={"page_size": 101}).status_code == 422
    assert patient_system.admin.get("/api/patients", params={"page": 0}).status_code == 422


def test_date_age_boundaries_and_optional_fields(patient_system):
    today = local_today()
    record = create(patient_system, birth_date=today.isoformat(), phone="", identity_number="", employer="", note="")
    assert record["age"] == 0
    assert all(record[field] is None for field in ("phone", "identity_number", "employer", "note"))
    with patient_system.app.state.Session() as db:
        patient = db.get(Patient, record["id"])
        patient.birth_date = date(2000, 2, 29)
        assert patient_record_json(patient, date(2025, 2, 28))["age"] == 24
        assert patient_record_json(patient, date(2025, 3, 1))["age"] == 25
        patient.birth_date = None
        assert patient_record_json(patient, today)["age"] is None
        patient.updated_at = None
        assert patient_record_json(patient, today)["updated_at"] == record["created_at"]
    assert create(patient_system, birth_date="1900-01-01", identity_number=None)["age"] == today.year - 1900


@pytest.mark.parametrize("changes", [
    {"name": "   "}, {"sex": "unknown"}, {"birth_date": "1899-12-31"},
    {"birth_date": (local_today() + timedelta(days=1)).isoformat()}, {"birth_date": "2025-02-29"},
    {"identity_number": "invalid-sensitive-id"}, {"phone": "x" * 33},
    {"employer": "x" * 201}, {"note": "private-secret" * 200}, {"age": 12},
])
def test_invalid_input_is_generic_and_not_persisted(patient_system, changes):
    body = registration(**changes)
    response = patient_system.admin.post("/api/patients", json=body)
    assert response.status_code == 422
    assert "private-secret" not in response.text and "invalid-sensitive-id" not in response.text
    with patient_system.app.state.Session() as db:
        assert db.scalar(select(func.count()).select_from(Patient)) == 0


@pytest.mark.parametrize("role", ["doctor", "reviewer"])
def test_patient_profile_denied_before_lookup_for_clinical_roles(patient_system, role):
    record = create(patient_system)
    client = patient_system.client_for(role)
    for patient_id in (record["id"], 999999):
        assert client.get(f"/api/patients/{patient_id}").status_code == 403
        assert client.get(f"/api/patients/{patient_id}/exams").status_code == 403
        assert client.put(f"/api/patients/{patient_id}", json=edit_body(record)).status_code == 403
        assert client.post(f"/api/patients/{patient_id}/archive", json={"reason": "权限测试", "version": 1}).status_code == 403
    assert client.get("/api/patients").status_code == 403
    assert client.post("/api/patients", json=registration()).status_code == 403


def test_registrar_can_manage_profiles_but_cannot_archive(patient_system):
    registrar = patient_system.client_for("registrar")
    response = registrar.post("/api/patients", json=registration())
    assert response.status_code == 200
    record = response.json()
    assert registrar.get(f"/api/patients/{record['id']}").status_code == 200
    assert registrar.get("/api/patients").status_code == 200
    assert registrar.put(f"/api/patients/{record['id']}", json=edit_body(record, employer="前台修改单位")).status_code == 200
    assert registrar.get(f"/api/patients/{record['id']}/exams").status_code == 200
    assert registrar.post(f"/api/patients/{record['id']}/archive", json={"reason": "前台归档"}).status_code == 403
    with TestClient(patient_system.app) as anonymous:
        assert anonymous.get("/api/patients").status_code == 401


def test_identity_unique_constraint_and_archived_reuse(patient_system):
    original = registration()
    response = patient_system.admin.post("/api/patients", json=original)
    record = response.json()
    assert patient_system.admin.post("/api/patients", json=registration()).status_code == 409
    other = create(patient_system, identity_number=None)
    duplicate_edit = patient_system.admin.put(f"/api/patients/{other['id']}", json=edit_body(other, identity_number=IDENTITY))
    assert duplicate_edit.status_code == 409
    with pytest.raises(IntegrityError):
        with patient_system.app.state.Session.begin() as db:
            db.add(Patient(name="绕过预检的重复身份证", sex="other", identity_number=IDENTITY))
            db.flush()
    archive = patient_system.admin.post(f"/api/patients/{record['id']}/archive", json={"reason": "归档验收", "version": 1})
    assert archive.status_code == 200
    replacement = create(patient_system)
    assert replacement["id"] != record["id"]
    assert patient_system.admin.post("/api/patients", json=original).status_code == 409
    assert patient_system.admin.get(f"/api/patients/{record['id']}").status_code == 404
    assert patient_system.admin.get(f"/api/patients/{record['id']}/exams").status_code == 404
    assert patient_system.admin.put(f"/api/patients/{record['id']}", json=edit_body(record)).status_code == 404
    with patient_system.app.state.Session() as db:
        archived = db.get(Patient, record["id"])
        assert archived.deleted_at is not None and archived.version == 2
        assert archived.updated_at >= archived.created_at
    assert patient_system.admin.get("/api/patients", params={"q": IDENTITY}).json()["total"] == 1


def test_archive_stale_version_and_existing_exam_are_rejected(patient_system):
    record = create(patient_system)
    edited = patient_system.admin.put(f"/api/patients/{record['id']}", json=edit_body(record, note="已编辑")).json()
    assert patient_system.admin.post(f"/api/patients/{record['id']}/archive", json={"reason": "过期归档", "version": record["version"]}).status_code == 409
    exam_for(patient_system, record["id"])
    assert patient_system.admin.post(f"/api/patients/{record['id']}/archive", json={"reason": "已有体检", "version": edited["version"]}).status_code == 409
    assert patient_system.admin.get(f"/api/patients/{record['id']}").status_code == 200


def test_history_is_paginated_by_patient_id_and_has_no_results(patient_system):
    record = create(patient_system)
    exams = [exam_for(patient_system, record["id"]) for _ in range(3)]
    assert patient_system.admin.put(f"/api/patients/{record['id']}", json=edit_body(record, name="新的档案姓名")).status_code == 200
    first = patient_system.admin.get(f"/api/patients/{record['id']}/exams", params={"page_size": 2}).json()
    second = patient_system.admin.get(f"/api/patients/{record['id']}/exams", params={"page_size": 2, "page": 2}).json()
    assert first["total"] == second["total"] == 3 and len(first["items"]) == 2 and len(second["items"]) == 1
    assert {row["id"] for row in first["items"] + second["items"]} == {exam["id"] for exam in exams}
    for row in first["items"] + second["items"]:
        assert not ({"items", "conclusion", "value"} & row.keys())
        assert set(row["patient"]) == set(EXAM_PATIENT_FIELDS)
        assert row["patient"]["name"] == record["name"]
    assert patient_system.admin.get(f"/api/patients/{record['id']}/exams", params={"page_size": 101}).status_code == 422
    with patient_system.app.state.Session.begin() as db:
        db.get(Exam, exams[0]["id"]).deleted_at = utcnow()
    assert patient_system.admin.get(f"/api/patients/{record['id']}/exams").json()["total"] == 2


def test_create_edit_audit_only_fields_and_archive_preserves_protected_reason(patient_system):
    record = create(patient_system)
    assert patient_system.admin.put(f"/api/patients/{record['id']}", json=edit_body(record, note="新秘密备注")).status_code == 200
    assert patient_system.admin.post(f"/api/patients/{record['id']}/archive", json={"reason": IDENTITY + "13800001234新秘密备注", "version": 2}).status_code == 200
    with patient_system.app.state.Session() as db:
        logs = list(db.scalars(select(AuditLog).where(AuditLog.entity_type == "patient").order_by(AuditLog.id)))
        assert [log.action for log in logs] == ["patient.create", "patient.update", "patient.archive"]
        assert logs[1].details == {"changed_fields": ["note"], "version": 2}
        assert logs[2].details == {"reason": IDENTITY + "13800001234新秘密备注", "version": 3}
        assert all(set(log.details) <= {"changed_fields", "version"} for log in logs[:2])
        serialized = str([log.details for log in logs[:2]])
        assert IDENTITY not in serialized and "13800001234" not in serialized and "秘密备注" not in serialized


@pytest.mark.parametrize("operation", ["create", "update", "archive"])
def test_audit_failure_rolls_back_whole_patient_operation(patient_system, operation, caplog):
    record = create(patient_system) if operation != "create" else None
    def reject(mapper, connection, target):
        if target.action == "patient." + operation:
            raise IntegrityError("INSERT private SQL", {"secret": IDENTITY}, Exception("private-note"))
    event.listen(AuditLog, "before_insert", reject)
    try:
        if operation == "create":
            response = patient_system.admin.post("/api/patients", json=registration())
        elif operation == "update":
            response = patient_system.admin.put(f"/api/patients/{record['id']}", json=edit_body(record, name="不应保存"))
        else:
            response = patient_system.admin.post(f"/api/patients/{record['id']}/archive", json={"reason": "不应归档", "version": 1})
    finally:
        event.remove(AuditLog, "before_insert", reject)
    assert response.status_code == 503
    assert IDENTITY not in response.text + caplog.text and "private-note" not in response.text + caplog.text
    with patient_system.app.state.Session() as db:
        if record is None:
            assert db.scalar(select(func.count()).select_from(Patient)) == 0
        else:
            assert patient_record_json(db.get(Patient, record["id"])) == record
            assert db.get(Patient, record["id"]).deleted_at is None


def test_commit_failure_never_returns_saved_and_rolls_back(patient_system):
    record = create(patient_system)
    def fail_commit(db):
        raise IntegrityError("COMMIT failure", {}, Exception("simulated disconnected database"))
    event.listen(patient_system.app.state.Session.class_, "before_commit", fail_commit)
    try:
        response = patient_system.admin.put(f"/api/patients/{record['id']}", json=edit_body(record, employer="未提交单位"))
    finally:
        event.remove(patient_system.app.state.Session.class_, "before_commit", fail_commit)
    assert response.status_code == 503
    assert patient_system.admin.get(f"/api/patients/{record['id']}").json() == record


def test_two_real_threads_cannot_overwrite_same_patient_version(patient_system, monkeypatch):
    from app import patient_management
    record = create(patient_system)
    barrier = Barrier(2)
    original = patient_management.lock_active_patient
    def meet_before_lock(db, patient_id):
        barrier.wait(timeout=10)
        return original(db, patient_id)
    monkeypatch.setattr(patient_management, "lock_active_patient", meet_before_lock)
    headers = dict(patient_system.admin.headers)
    def save(name):
        with TestClient(patient_system.app) as client:
            return client.put(f"/api/patients/{record['id']}", json=edit_body(record, name=name), headers=headers)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(save, ["并发甲", "并发乙"]))
    assert sorted(response.status_code for response in results) == [200, 409]
    current = patient_system.admin.get(f"/api/patients/{record['id']}").json()
    assert current["version"] == 2 and current["name"] in {"并发甲", "并发乙"}
    with patient_system.app.state.Session() as db:
        assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "patient.update")) == 1


@pytest.mark.parametrize("first", ["archive", "exam"])
def test_archive_and_exam_creation_share_real_write_lock(patient_system, monkeypatch, first):
    from app import main, patient_management, services
    record = create(patient_system)
    locked, second_started, release = Event(), Event(), Event()
    original = services.lock_active_patient
    def pause_with_lock(db, patient_id):
        operation = current_thread().name
        if operation == "second":
            second_started.set()
        result = original(db, patient_id)
        if operation == "first":
            locked.set()
            assert release.wait(10)
        return result
    monkeypatch.setattr(patient_management, "lock_active_patient", pause_with_lock)
    monkeypatch.setattr(services, "lock_active_patient", pause_with_lock)
    # FastAPI sync handlers run in AnyIO threads; annotate using request payload's
    # endpoint wrapper through separate contexts before reaching the lock.
    sequence = {"first": first, "second": "exam" if first == "archive" else "archive"}
    original_archive = patient_management.lock_active_patient
    original_exam = services.lock_active_patient
    def archive_lock(db, patient_id):
        old = current_thread().name
        current_thread().name = "first" if first == "archive" else "second"
        try:
            return original_archive(db, patient_id)
        finally:
            current_thread().name = old
    def exam_lock(db, patient_id):
        old = current_thread().name
        current_thread().name = "first" if first == "exam" else "second"
        try:
            return original_exam(db, patient_id)
        finally:
            current_thread().name = old
    monkeypatch.setattr(patient_management, "lock_active_patient", archive_lock)
    monkeypatch.setattr(services, "lock_active_patient", exam_lock)
    # Registration now acquires the shared foundation anchor before the patient
    # lock. If archive owns SQLite's writer first, the waiting exam reaches that
    # anchor and cannot yet reach lock_active_patient; observe its actual entry.
    original_anchor = main.lock_and_recheck
    def exam_anchor(request, db, *roles):
        if first == "archive":
            second_started.set()
        return original_anchor(request, db, *roles)
    monkeypatch.setattr(main, "lock_and_recheck", exam_anchor)
    headers = dict(patient_system.admin.headers)
    def request(operation):
        with TestClient(patient_system.app) as client:
            if operation == "archive":
                return client.post(f"/api/patients/{record['id']}/archive", json={"reason": "并发归档", "version": 1}, headers=headers)
            return client.post("/api/exams", json={"patient_id": record["id"], "package_id": patient_system.package_id, "request_id": uuid4().hex}, headers=headers)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(request, sequence["first"])
        assert locked.wait(10)
        second_future = executor.submit(request, sequence["second"])
        assert second_started.wait(10)
        release.set()
        responses = [first_future.result(15), second_future.result(15)]
    assert [response.status_code for response in responses] == ([200, 404] if first == "archive" else [200, 409])
    with patient_system.app.state.Session() as db:
        patient = db.get(Patient, record["id"])
        active_exams = db.scalar(select(func.count()).select_from(Exam).where(Exam.patient_id == record["id"], Exam.deleted_at.is_(None)))
        assert not (patient.deleted_at is not None and active_exams)
        assert active_exams == (0 if first == "archive" else 1)


def test_profile_survives_new_database_connection_and_app_restart(patient_system):
    record = create(patient_system)
    updated = patient_system.admin.put(f"/api/patients/{record['id']}", json=edit_body(record, employer="持久化测试单位", note="持久化备注")).json()
    engine, Session = create_database(patient_system.url)
    try:
        with Session() as db:
            assert patient_record_json(db.get(Patient, record["id"])) == updated
    finally:
        engine.dispose()
    from app.main import create_app
    restarted = create_app(settings=patient_system.settings)
    try:
        with TestClient(restarted) as client:
            assert client.get(f"/api/patients/{record['id']}", headers=dict(patient_system.admin.headers)).json() == updated
    finally:
        restarted.state.engine.dispose()


def test_profile_edits_do_not_change_exam_or_report_and_legacy_response_is_filtered(patient_system):
    record = create(patient_system)
    exam = exam_for(patient_system, record["id"])
    with patient_system.app.state.Session() as db:
        original_snapshot = dict(db.get(Exam, exam["id"]).patient_snapshot)
        assert not ({"identity_number", "employer", "note", "age", "updated_at", "version"} & original_snapshot.keys())
    item = exam["items"][0]
    saved = patient_system.admin.put(f"/api/exam-items/{item['id']}", json={"value": "软件测试值", "version": item["version"]})
    assert saved.status_code == 200
    current_exam = patient_system.admin.get(f"/api/exams/{exam['id']}").json()
    assert patient_system.admin.post(f"/api/exams/{exam['id']}/finalize", json={"version": current_exam["version"], "conclusion": "软件测试结论"}).status_code == 200
    with patient_system.app.state.Session() as db:
        original_report = dict(db.scalar(select(ReportSnapshot).where(ReportSnapshot.exam_id == exam["id"])).payload)
    assert patient_system.admin.put(f"/api/patients/{record['id']}", json=edit_body(record, name="档案新姓名", phone="13900000000", note="档案新备注")).status_code == 200
    report = patient_system.admin.get(f"/api/exams/{exam['id']}/report").json()
    assert report == original_report and report["patient"]["name"] == record["name"]
    with patient_system.app.state.Session.begin() as db:
        stored_exam = db.get(Exam, exam["id"])
        assert stored_exam.patient_snapshot == original_snapshot
        stored_report = db.scalar(select(ReportSnapshot).where(ReportSnapshot.exam_id == exam["id"]))
        assert stored_report.payload == original_report
        polluted = dict(stored_exam.patient_snapshot, identity_number=IDENTITY, employer="敏感单位", note="敏感备注")
        stored_exam.patient_snapshot = polluted
        legacy_payload = dict(stored_report.payload, patient=polluted)
        stored_report.payload = legacy_payload
    doctor = patient_system.client_for("doctor")
    assert set(doctor.get(f"/api/exams/{exam['id']}").json()["patient"]) == set(EXAM_PATIENT_FIELDS)
    assert set(patient_system.admin.get(f"/api/exams/{exam['id']}/report").json()["patient"]) == set(EXAM_PATIENT_FIELDS)
    with patient_system.app.state.Session() as db:
        assert db.scalar(select(ReportSnapshot).where(ReportSnapshot.exam_id == exam["id"])).payload == legacy_payload
    latest = patient_system.admin.get(f"/api/exams/{exam['id']}").json()
    assert patient_system.admin.post(f"/api/exams/{exam['id']}/reopen", json={"version": latest["version"], "reason": "软件验收"}).status_code == 200
    reopened = patient_system.admin.get(f"/api/exams/{exam['id']}").json()
    assert patient_system.admin.post(f"/api/exams/{exam['id']}/finalize", json={"version": reopened["version"], "conclusion": "软件测试新结论"}).status_code == 200
    assert set(patient_system.admin.get(f"/api/exams/{exam['id']}/report").json()["patient"]) == set(EXAM_PATIENT_FIELDS)
    with patient_system.app.state.Session() as db:
        reports = list(db.scalars(select(ReportSnapshot).where(ReportSnapshot.exam_id == exam["id"]).order_by(ReportSnapshot.revision)))
        assert len(reports) == 2 and reports[0].payload == legacy_payload
        assert set(reports[1].payload["patient"]) == set(EXAM_PATIENT_FIELDS)
