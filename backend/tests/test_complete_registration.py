"""Real transactions for the complete front-desk person/visit operation."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from io import StringIO
from pathlib import Path
from threading import Barrier
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import MetaData, event, func, inspect, select, text
from sqlalchemy.exc import IntegrityError

from test_patients import IDENTITY, create, edit_body, patient_system, patients_password_hash
from app import registration, services
from app.config import Settings
from app.database import create_database
from app.models import AuditLog, Exam, ExamItem, ExamNumberCounter, Patient, RegistrationRequest, ReportSnapshot, User


def body(**changes):
    value = {"patient": {"name": "完整登记（虚构）", "sex": "other", "birth_date": "1990-01-01", "identity_number": IDENTITY, "ethnicity": "软件验收民族", "address": "软件验收虚构地址"}, "request_id": uuid4().hex, "package_id": None, "exam_date": None, "exam_type": "个人体检", "note": "仅软件测试"}
    value.update(changes)
    return value


def save(system, value):
    response = system.admin.post("/api/registrations", json=value)
    assert response.status_code == 200, response.text
    return response.json()


def counts(system):
    with system.app.state.Session() as db:
        return tuple(db.scalar(select(func.count()).select_from(model)) for model in (Patient, Exam, ExamItem, RegistrationRequest, ExamNumberCounter))


def test_one_transaction_new_person_visit_optional_package_and_server_today(patient_system, monkeypatch):
    monkeypatch.setattr(services, "utcnow", lambda: datetime(2026, 10, 1, 16, 30))
    result = save(patient_system, body())
    patient, exam = result["patient"], result["exam"]
    assert not result["reused_patient"] and exam["patient"]["id"] == patient["id"]
    assert patient["ethnicity"] == "软件验收民族" and patient["address"] == "软件验收虚构地址"
    assert exam["exam_number"] == "20261002-0001" and exam["exam_date"] == "2026-10-02"
    assert exam["exam_type"] == "个人体检" and exam["package_id"] is None
    assert exam["package_name"] == "未选择套餐" and exam["items"] == [] and exam["status"] == "registered"
    assert counts(patient_system) == (1, 1, 0, 1, 1)
    assert patient_system.admin.post(f"/api/exams/{exam['id']}/finalize", json={"version": exam["version"], "conclusion": "不能总检空项目"}).status_code == 409
    with patient_system.app.state.Session() as db:
        stored = db.get(RegistrationRequest, next(iter(db.scalars(select(RegistrationRequest.request_id)))))
        assert len(stored.fingerprint) == 64 and IDENTITY not in stored.fingerprint
        assert all(IDENTITY not in str(log.details) and patient["address"] not in str(log.details) for log in db.scalars(select(AuditLog)))


def test_identity_lookup_exact_post_only_normalized_and_reuses_saved_person(patient_system):
    first = save(patient_system, body())
    lookup = patient_system.admin.post("/api/patients/lookup-identity", json={"identity_number": " " + IDENTITY.lower() + " "})
    assert lookup.status_code == 200 and lookup.json()["patient"] == first["patient"]
    assert patient_system.admin.post("/api/patients/lookup-identity", json={"identity_number": "110101199001011234"}).json() == {"patient": None}
    assert patient_system.admin.post("/api/patients/lookup-identity", json={"identity_number": "199001"}).status_code == 422
    assert patient_system.admin.get("/api/patients/lookup-identity").status_code != 200
    later = body(package_id=patient_system.package_id, exam_type="入职体检", exam_date="2026-10-03")
    later["patient"].update(address="新读卡地址不应自动覆盖", ethnicity="新民族不应覆盖", phone="00000000000")
    second = save(patient_system, later)
    assert second["reused_patient"] and second["patient"] == first["patient"]
    assert second["exam"]["id"] != first["exam"]["id"] and second["exam"]["exam_number"] != first["exam"]["exam_number"]
    assert second["exam"]["exam_type"] == "入职体检" and len(second["exam"]["items"]) == 1
    assert patient_system.admin.get(f"/api/exams/{first['exam']['id']}").json() == first["exam"]
    assert counts(patient_system) == (1, 2, 1, 2, 1)


@pytest.mark.parametrize("field,value", [("name", "另一姓名"), ("sex", "female"), ("birth_date", "1991-01-01")])
def test_same_identity_conflicting_demographics_never_overwrites(patient_system, field, value):
    first = save(patient_system, body())
    changed = body()
    changed["patient"][field] = value
    assert patient_system.admin.post("/api/registrations", json=changed).status_code == 409
    assert patient_system.admin.get(f"/api/patients/{first['patient']['id']}").json() == first["patient"]
    assert counts(patient_system) == (1, 1, 0, 1, 1)


def test_selected_person_pair_version_and_identity_are_required(patient_system):
    first = save(patient_system, body())
    selected = body(patient_id=first["patient"]["id"], patient_version=1)
    assert save(patient_system, selected)["reused_patient"]
    assert patient_system.admin.post("/api/registrations", json=body(patient_id=first["patient"]["id"])).status_code == 422
    assert patient_system.admin.post("/api/registrations", json=body(patient_version=1)).status_code == 422
    assert patient_system.admin.post("/api/registrations", json=body(patient_id=first["patient"]["id"], patient_version=2)).status_code == 409
    wrong = body(patient_id=first["patient"]["id"], patient_version=1)
    wrong["patient"]["identity_number"] = None
    assert patient_system.admin.post("/api/registrations", json=wrong).status_code == 409
    assert counts(patient_system) == (1, 2, 0, 2, 1)


def test_manual_without_identity_can_explicitly_reuse_selected_patient(patient_system):
    manual = body()
    manual["patient"]["identity_number"] = None
    first = save(patient_system, manual)
    second = save(patient_system, dict(manual, request_id=uuid4().hex, patient_id=first["patient"]["id"], patient_version=first["patient"]["version"]))
    assert second["reused_patient"] and second["patient"]["id"] == first["patient"]["id"]
    third = save(patient_system, dict(manual, request_id=uuid4().hex))
    assert not third["reused_patient"] and third["patient"]["id"] != first["patient"]["id"]
    assert counts(patient_system) == (2, 3, 0, 3, 1)


def test_archived_identity_requires_administrator_resolution(patient_system):
    old = create(patient_system)
    assert patient_system.admin.post(f"/api/patients/{old['id']}/archive", json={"version": old["version"], "reason": "仅测试归档"}).status_code == 200
    assert patient_system.admin.post("/api/patients/lookup-identity", json={"identity_number": IDENTITY}).status_code == 409
    assert patient_system.admin.post("/api/registrations", json=body()).status_code == 409
    assert counts(patient_system) == (1, 0, 0, 0, 0)


def test_whole_registration_replay_across_midnight_restart_and_conflicting_actor(patient_system, monkeypatch):
    original = body()
    monkeypatch.setattr(services, "utcnow", lambda: datetime(2026, 10, 1, 15, 59))
    first = save(patient_system, original)
    monkeypatch.setattr(services, "utcnow", lambda: datetime(2026, 10, 1, 16, 1))
    assert save(patient_system, original) == first
    assert patient_system.client_for("registrar").post("/api/registrations", json=original).status_code == 409
    assert patient_system.admin.post("/api/registrations", json=dict(original, exam_type="其他")).status_code == 409
    changed = dict(original, patient=dict(original["patient"], address="不同地址"))
    assert patient_system.admin.post("/api/registrations", json=changed).status_code == 409
    from app.main import create_app
    restarted = create_app(settings=patient_system.settings)
    try:
        with TestClient(restarted) as client:
            response = client.post("/api/registrations", json=original, headers=dict(patient_system.admin.headers))
            assert response.status_code == 200 and response.json() == first
    finally:
        restarted.state.engine.dispose()
    assert counts(patient_system) == (1, 1, 0, 1, 1)


@pytest.mark.parametrize("same_request", [True, False])
def test_real_two_thread_registration_never_duplicates_same_identity(patient_system, monkeypatch, same_request):
    meet = Barrier(2)
    original = registration.lock_actor
    def lock(request, db):
        meet.wait(timeout=10)
        return original(request, db)
    monkeypatch.setattr(registration, "lock_actor", lock)
    first = body()
    inputs = [first, first if same_request else dict(first, request_id=uuid4().hex)]
    headers = dict(patient_system.admin.headers)
    def save_one(value):
        with TestClient(patient_system.app) as client:
            return client.post("/api/registrations", json=value, headers=headers)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(save_one, inputs))
    assert [r.status_code for r in responses] == [200, 200]
    assert len({r.json()["patient"]["id"] for r in responses}) == 1
    assert len({r.json()["exam"]["id"] for r in responses}) == (1 if same_request else 2)
    assert counts(patient_system) == (1, 1 if same_request else 2, 0, 1 if same_request else 2, 1)


def test_parallel_legacy_exam_and_new_registration_share_safe_counter(patient_system):
    first = save(patient_system, body())
    value = body()
    legacy = {"patient_id": first["patient"]["id"], "package_id": patient_system.package_id, "request_id": uuid4().hex}
    headers = dict(patient_system.admin.headers)
    def save_one(path_payload):
        path, payload = path_payload
        with TestClient(patient_system.app) as client:
            return client.post(path, json=payload, headers=headers)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(save_one, [("/api/exams", legacy), ("/api/registrations", value)]))
    assert [r.status_code for r in responses] == [200, 200]
    numbers = [first["exam"]["exam_number"], responses[0].json()["exam_number"], responses[1].json()["exam"]["exam_number"]]
    assert len(set(numbers)) == 3
    assert {n[-4:] for n in numbers} == {"0001", "0002", "0003"}
    assert counts(patient_system) == (1, 3, 1, 2, 1)


@pytest.mark.parametrize("role", ["doctor", "reviewer"])
def test_non_registration_roles_cannot_lookup_create_or_options(patient_system, role):
    client = patient_system.client_for(role)
    assert client.post("/api/patients/lookup-identity", json={"identity_number": IDENTITY}).status_code == 403
    assert client.post("/api/registrations", json=body()).status_code == 403
    assert client.get("/api/registration/options").status_code == 403
    assert counts(patient_system) == (0, 0, 0, 0, 0)


def test_missing_auth_csrf_and_post_lock_revocation_cannot_create(patient_system, monkeypatch):
    with TestClient(patient_system.app) as client:
        assert client.post("/api/registrations", json=body()).status_code == 401
        headers = dict(patient_system.admin.headers)
        headers.pop("x-csrf-token")
        assert client.post("/api/registrations", json=body(), headers=headers).status_code == 403
    original = registration.lock_actor
    def revoked(request, db):
        with patient_system.app.state.Session.begin() as separate:
            actor = separate.scalar(select(User).where(User.role == "admin"))
            actor.is_active = False
        return original(request, db)
    monkeypatch.setattr(registration, "lock_actor", revoked)
    assert patient_system.admin.post("/api/registrations", json=body()).status_code == 401
    assert counts(patient_system) == (0, 0, 0, 0, 0)


@pytest.mark.parametrize("changes", [{"package_id": 999999}, {"package_id": 0}, {"exam_type": "非法类型"}, {"exam_date": "1899-12-31"}, {"exam_date": "2026-02-29"}, {"status": "completed"}])
def test_bad_input_package_does_not_leave_half_registered_person(patient_system, changes):
    assert patient_system.admin.post("/api/registrations", json=body(**changes)).status_code == 422
    assert counts(patient_system) == (0, 0, 0, 0, 0)


@pytest.mark.parametrize("field,length", [("ethnicity", 51), ("address", 501)])
def test_extended_patient_lengths_are_server_validated(patient_system, field, length):
    value = body()
    value["patient"][field] = "x" * length
    assert patient_system.admin.post("/api/registrations", json=value).status_code == 422
    assert counts(patient_system) == (0, 0, 0, 0, 0)


def test_old_patient_editor_preserves_omitted_extended_fields(patient_system):
    record = save(patient_system, body())["patient"]
    update = edit_body(record, phone="00000000000")
    update.pop("ethnicity")
    update.pop("address")
    response = patient_system.admin.put(f"/api/patients/{record['id']}", json=update)
    assert response.status_code == 200 and response.json()["address"] == record["address"] and response.json()["ethnicity"] == record["ethnicity"]
    explicit = edit_body(response.json(), address="", ethnicity="")
    current = patient_system.admin.put(f"/api/patients/{record['id']}", json=explicit)
    assert current.status_code == 200 and current.json()["address"] is None and current.json()["ethnicity"] is None


@pytest.mark.parametrize("fail_action", ["patient.create", "exam.create", "registration.create"])
def test_audit_write_failure_rolls_back_person_exam_counter_and_request(patient_system, fail_action, caplog):
    private = body()
    def fail(mapper, connection, target):
        if target.action == fail_action:
            raise IntegrityError("private_insert", {"identity": IDENTITY}, Exception("private_details"))
    event.listen(AuditLog, "before_insert", fail)
    try:
        response = patient_system.admin.post("/api/registrations", json=private)
    finally:
        event.remove(AuditLog, "before_insert", fail)
    assert response.status_code == 503 and counts(patient_system) == (0, 0, 0, 0, 0)
    assert IDENTITY not in response.text + caplog.text and "private_details" not in response.text + caplog.text
    retried = save(patient_system, private)
    assert retried["exam"]["exam_number"].endswith("-0001")


def test_outer_commit_failure_never_returns_success_or_partial_save(patient_system):
    def fail(db):
        if not db.in_nested_transaction():
            raise IntegrityError("private_commit", {}, Exception("private_error"))
    event.listen(patient_system.app.state.Session.class_, "before_commit", fail)
    try:
        response = patient_system.admin.post("/api/registrations", json=body())
    finally:
        event.remove(patient_system.app.state.Session.class_, "before_commit", fail)
    assert response.status_code == 503 and counts(patient_system) == (0, 0, 0, 0, 0)


def test_number_configuration_and_counter_capacity_are_explicit(patient_system, monkeypatch):
    monkeypatch.setattr(services, "utcnow", lambda: datetime(2026, 10, 1, 16, 30))
    settings = patient_system.settings.model_copy(update={"exam_number_prefix": "HC", "exam_number_date_format": "%Y-%m-%d", "exam_number_separator": "_", "exam_number_width": 3})
    patient_system.app.state.settings = settings
    assert save(patient_system, body())["exam"]["exam_number"] == "HC2026-10-02_001"
    with patient_system.app.state.Session.begin() as db:
        db.get(ExamNumberCounter, date(2026, 10, 2)).value = 999
    failed = patient_system.admin.post("/api/registrations", json=body(request_id=uuid4().hex))
    assert failed.status_code == 503 and counts(patient_system) == (1, 1, 0, 1, 1)
    with patient_system.app.state.Session() as db:
        assert db.get(ExamNumberCounter, date(2026, 10, 2)).value == 999
    patient_system.app.state.settings = settings.model_copy(update={"exam_number_mode": "random"})
    assert save(patient_system, body())["exam"]["exam_number"].startswith("HCTJ20261002")


@pytest.mark.parametrize("changes", [{"exam_number_mode": "bad"}, {"exam_number_width": 2}, {"exam_number_width": 9}, {"exam_number_prefix": "<script>"}, {"exam_number_separator": " "}, {"exam_number_date_format": "%m"}])
def test_invalid_number_configuration_refused_at_startup(changes):
    with pytest.raises(ValueError):
        Settings(_env_file=None, **changes)


def test_migration_preserves_every_old_field_and_restores_all_fk_enforcement(tmp_path):
    url = "sqlite:///" + (tmp_path / "populated_0004.db").as_posix()
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.attributes["database_url"] = url
    command.upgrade(config, "0004")
    engine, _ = create_database(url)
    old = MetaData()
    old.reflect(engine)
    now = datetime(2026, 10, 1, 16, 30)
    with engine.begin() as conn:
        conn.execute(old.tables["users"].insert(), {"id": 1, "username": "migration", "display_name": "迁移验收", "password_hash": "invalid-test-marker", "role": "admin", "department_id": None, "is_active": True})
        conn.execute(old.tables["departments"].insert(), {"id": 1, "name": "旧科室"})
        conn.execute(old.tables["packages"].insert(), {"id": 1, "name": "旧套餐", "description": "旧描述", "is_active": True})
        conn.execute(old.tables["patients"].insert(), {"id": 1, "name": "旧人员", "sex": "other", "created_at": now, "updated_at": now, "version": 1, "identity_number": IDENTITY})
        conn.execute(old.tables["exams"].insert(), {"id": 1, "exam_number": "OLD-IMMUTABLE-EXAM", "request_id": "old-immutable-exam", "requested_by": 1, "patient_id": 1, "package_id": 1, "patient_snapshot": {"id": 1, "name": "旧快照"}, "package_name": "旧套餐", "status": "completed", "version": 3, "conclusion": "旧结论", "completed_at": now, "latest_report_revision": 1, "created_at": now, "updated_at": now, "exam_date": date(2026, 10, 2), "note": "旧登记备注"})
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
        assert conn.execute(text("SELECT ethnicity,address FROM patients")).one() == (None, None)
        assert conn.execute(text("SELECT exam_type FROM exams")).scalar_one() == "个人体检"
        assert conn.execute(text("PRAGMA foreign_key_check")).all() == []
        assert conn.execute(text("PRAGMA integrity_check")).scalar_one() == "ok"
        assert conn.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(text("UPDATE exam_items SET exam_id=999 WHERE id=1"))
    engine.dispose()


@pytest.mark.parametrize("url", ["sqlite:///offline.db", "postgresql+psycopg://offline:unused@localhost/offline"])
def test_complete_registration_offline_migration_compiles_for_both_databases(url):
    output = StringIO()
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"), output_buffer=output)
    config.attributes["database_url"] = url
    command.upgrade(config, "0004:0005", sql=True)
    sql = output.getvalue()
    assert "CREATE TABLE registration_requests" in sql and "CREATE TABLE exam_number_counters" in sql
    assert "ADD COLUMN ethnicity VARCHAR(50)" in sql and "ADD COLUMN address VARCHAR(500)" in sql
    assert "DROP TABLE exam_items" not in sql and "DROP TABLE report_snapshots" not in sql
    if url.startswith("postgresql"):
        assert "ALTER TABLE exams ALTER COLUMN package_id DROP NOT NULL" in sql and "_alembic_tmp" not in sql


def test_changed_person_before_selection_confirmation_has_version_conflict(patient_system):
    first = save(patient_system, body())
    record = first["patient"]
    assert patient_system.admin.put(f"/api/patients/{record['id']}", json=edit_body(record, address="人员已编辑地址")).status_code == 200
    selected = body(patient_id=record["id"], patient_version=record["version"])
    assert patient_system.admin.post("/api/registrations", json=selected).status_code == 409
    assert patient_system.admin.get(f"/api/exams/{first['exam']['id']}").json() == first["exam"]


def test_real_identity_unique_collision_resolves_existing_person_once(patient_system, monkeypatch):
    previous = create(patient_system, name="完整登记（虚构）", sex="other", ethnicity="旧民族", address="旧地址")
    original = registration.identity_patient
    calls = []
    def stale(db, identity):
        calls.append(True)
        return None if len(calls) == 1 else original(db, identity)
    monkeypatch.setattr(registration, "identity_patient", stale)
    result = save(patient_system, body())
    assert len(calls) == 2 and result["reused_patient"] and result["patient"] == previous
    assert counts(patient_system) == (1, 1, 0, 1, 1)
    assert result["exam"]["exam_number"].endswith("-0001")


def test_no_extended_identity_details_leak_into_clinical_report_or_audit(patient_system):
    private_note = "仅登记员核对备注_不可向医生显示_隐私验收"
    result = save(patient_system, body(package_id=patient_system.package_id, note=private_note))
    patient, exam = result["patient"], result["exam"]
    doctor = patient_system.client_for("doctor")
    detail = doctor.get(f"/api/exams/{exam['id']}")
    assert detail.status_code == 200
    for private in (IDENTITY, patient["address"], patient["ethnicity"], private_note):
        assert private not in detail.text
    item = exam["items"][0]
    assert patient_system.admin.put(f"/api/exam-items/{item['id']}", json={"value": "软件验收值", "version": item["version"]}).status_code == 200
    ready = patient_system.admin.get(f"/api/exams/{exam['id']}").json()
    assert patient_system.admin.post(f"/api/exams/{exam['id']}/finalize", json={"version": ready["version"], "conclusion": "软件验收结论"}).status_code == 200
    report = patient_system.admin.get(f"/api/exams/{exam['id']}/report")
    assert report.status_code == 200 and report.json()["exam_type"] == "个人体检"
    for private in (IDENTITY, patient["address"], patient["ethnicity"], private_note):
        assert private not in report.text


def test_failed_fk_verification_rolls_back_all_migration_ddl_and_rows(tmp_path):
    url = "sqlite:///" + (tmp_path / "invalid_old_fk.db").as_posix()
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.attributes["database_url"] = url
    command.upgrade(config, "0004")
    engine, _ = create_database(url)
    old = MetaData()
    old.reflect(engine)
    now = datetime(2026, 10, 1, 16, 30)
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        conn.commit()
        with conn.begin():
            conn.execute(old.tables["exams"].insert(), {"id": 1, "exam_number": "OLD-INVALID-FK", "request_id": "old-invalid-fk", "requested_by": 999, "patient_id": 999, "package_id": 999, "patient_snapshot": {"id": 999, "name": "旧损坏测试"}, "package_name": "旧套餐", "status": "registered", "version": 1, "latest_report_revision": 0, "created_at": now, "updated_at": now, "exam_date": date(2026, 10, 2)})
        before = {name: conn.execute(table.select()).all() for name, table in old.tables.items()}
    with pytest.raises(RuntimeError, match="foreign-key verification"):
        command.upgrade(config, "head")
    inspection = inspect(engine)
    assert "exam_number_counters" not in inspection.get_table_names() and "registration_requests" not in inspection.get_table_names()
    assert "exam_type" not in {column["name"] for column in inspection.get_columns("exams")}
    assert "address" not in {column["name"] for column in inspection.get_columns("patients")}
    assert next(column for column in inspection.get_columns("exams") if column["name"] == "package_id")["nullable"] is False
    with engine.connect() as conn:
        for name, table in old.tables.items():
            assert conn.execute(table.select()).all() == before[name]
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0004"
        assert conn.execute(text("PRAGMA integrity_check")).scalar_one() == "ok"
    # A fresh connection uses the regular enforced runtime settings after failure.
    engine.dispose()
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
    engine.dispose()
