"""Stable exam registration using isolated real SQLite transactions."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from io import StringIO
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import MetaData, event, func, select, text
from sqlalchemy.exc import IntegrityError

# Reuse the phase-five isolated database/session fixture, including its cached hash.
from test_patients import patient_system, patients_password_hash, create, edit_body
from app.database import create_database
from app.models import AuditLog, CatalogItem, Exam, ExamItem, Package, ReportSnapshot
from app import services


def body_for(system, patient_id=None, **changes):
    if patient_id is None:
        patient_id = create(system, identity_number=None)["id"]
    body = {"patient_id": patient_id, "package_id": system.package_id, "request_id": uuid4().hex}
    body.update(changes)
    return body


def register(system, body):
    response = system.admin.post("/api/exams", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def counts(system):
    with system.app.state.Session() as db:
        return tuple(db.scalar(select(func.count()).select_from(model)) for model in (Exam, ExamItem)) + (db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "exam.create")),)


def test_registration_saves_server_china_date_unique_number_relation_and_normalized_note(patient_system, monkeypatch):
    monkeypatch.setattr(services, "utcnow", lambda: datetime(2026, 10, 1, 16, 30))
    body = body_for(patient_system, note="  仅软件验收登记备注  ")
    exam = register(patient_system, body)
    assert exam["exam_date"] == "2026-10-02"
    assert exam["exam_number"] == "20261002-0001"
    assert exam["note"] == "仅软件验收登记备注" and exam["status"] == "registered"
    assert exam["patient"]["id"] == body["patient_id"]
    with patient_system.app.state.Session() as db:
        stored = db.get(Exam, exam["id"])
        assert stored.exam_date == date(2026, 10, 2) and stored.patient_id == body["patient_id"]
        assert db.execute(text("PRAGMA foreign_key_check")).all() == []
        log = db.scalar(select(AuditLog).where(AuditLog.action == "exam.create"))
        assert log.details == {} and "仅软件验收登记备注" not in str(log.details)


def test_explicit_date_survives_restart_with_empty_note_normalized(patient_system):
    exam = register(patient_system, body_for(patient_system, exam_date="2030-02-28", note="  "))
    assert exam["exam_date"] == "2030-02-28" and exam["note"] is None
    from app.main import create_app
    restarted = create_app(settings=patient_system.settings)
    try:
        with TestClient(restarted) as client:
            response = client.get(f"/api/exams/{exam['id']}", headers=dict(patient_system.admin.headers))
            assert response.status_code == 200 and response.json() == exam
    finally:
        restarted.state.engine.dispose()


@pytest.mark.parametrize("changes", [{"exam_date": "not-a-date"}, {"exam_date": "2025-02-29"}, {"exam_date": "1899-12-31"}, {"note": "n" * 2001}, {"patient_id": 0}, {"package_id": 0}, {"status": "completed"}, {"status": "unexpected"}])
def test_invalid_registration_input_never_writes_exam(patient_system, changes):
    response = patient_system.admin.post("/api/exams", json=body_for(patient_system, **changes))
    assert response.status_code == 422
    assert counts(patient_system) == (0, 0, 0)


@pytest.mark.parametrize("role,expected", [("registrar", 200), ("doctor", 403), ("reviewer", 403)])
def test_creation_has_server_role_checks(patient_system, role, expected):
    client = patient_system.client_for(role)
    response = client.post("/api/exams", json=body_for(patient_system))
    assert response.status_code == expected
    assert counts(patient_system)[0] == (1 if expected == 200 else 0)


def test_anonymous_and_missing_csrf_cannot_create(patient_system):
    body = body_for(patient_system)
    with TestClient(patient_system.app) as client:
        assert client.post("/api/exams", json=body).status_code == 401
        headers = dict(patient_system.admin.headers)
        headers.pop("x-csrf-token")
        assert client.post("/api/exams", json=body, headers=headers).status_code == 403
    assert counts(patient_system) == (0, 0, 0)


def test_same_request_null_date_is_idempotent_after_china_midnight(patient_system, monkeypatch):
    monkeypatch.setattr(services, "utcnow", lambda: datetime(2026, 10, 1, 15, 59))
    body = body_for(patient_system, exam_date=None, note="  幂等登记备注  ")
    first = register(patient_system, body)
    assert first["exam_date"] == "2026-10-01"
    monkeypatch.setattr(services, "utcnow", lambda: datetime(2026, 10, 1, 16, 1))
    assert register(patient_system, body) == first
    assert register(patient_system, dict(body, exam_date="2026-10-01", note="幂等登记备注")) == first
    assert counts(patient_system) == (1, 1, 1)


@pytest.mark.parametrize("changed", ["date", "note", "actor", "patient", "package"])
def test_request_id_rejects_different_registration(patient_system, changed):
    body = body_for(patient_system, exam_date="2026-10-02", note="原始备注")
    first = register(patient_system, body)
    client = patient_system.admin
    altered = dict(body)
    if changed == "date":
        altered["exam_date"] = "2026-10-03"
    elif changed == "note":
        altered["note"] = "另一备注"
    elif changed == "actor":
        client = patient_system.client_for("registrar")
    elif changed == "patient":
        altered["patient_id"] = create(patient_system, identity_number=None)["id"]
    else:
        altered["package_id"] = 999999
    assert client.post("/api/exams", json=altered).status_code == 409
    assert patient_system.admin.get(f"/api/exams/{first['id']}").json() == first
    assert counts(patient_system) == (1, 1, 1)


def test_two_real_threads_same_request_create_exactly_one_exam(patient_system, monkeypatch):
    body = body_for(patient_system, exam_date="2026-10-02", note="并发测试")
    meet = Barrier(2)
    import app.main as main
    original = main.lock_and_recheck
    def lock(request, db, *roles):
        meet.wait(timeout=10)
        return original(request, db, *roles)
    monkeypatch.setattr(main, "lock_and_recheck", lock)
    headers = dict(patient_system.admin.headers)
    def save(_):
        with TestClient(patient_system.app) as client:
            return client.post("/api/exams", json=body, headers=headers)
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(save, range(2)))
    assert [response.status_code for response in responses] == [200, 200]
    assert responses[0].json() == responses[1].json()
    assert counts(patient_system) == (1, 1, 1)


def test_number_collision_retries_only_number_without_partial_items(patient_system, monkeypatch):
    first = register(patient_system, body_for(patient_system))
    sequence = iter([first["exam_number"], "TJ20261002ABCDEF123456"])
    monkeypatch.setattr(services, "generate_exam_number", lambda db, today, settings: next(sequence))
    second = register(patient_system, body_for(patient_system, first["patient"]["id"]))
    assert second["id"] != first["id"] and second["exam_number"] == "TJ20261002ABCDEF123456"
    assert counts(patient_system) == (2, 2, 2)


def test_repeated_number_collisions_stop_with_no_partial_save_and_allow_retry(patient_system, monkeypatch):
    first = register(patient_system, body_for(patient_system))
    generated = []
    def collide(db, today, settings):
        generated.append(today)
        return first["exam_number"]
    monkeypatch.setattr(services, "generate_exam_number", collide)
    body = body_for(patient_system, first["patient"]["id"], note="保持此请求")
    failed = patient_system.admin.post("/api/exams", json=body)
    assert failed.status_code == 503 and "尚未保存" in failed.text
    assert len(generated) == 3 and counts(patient_system) == (1, 1, 1)
    monkeypatch.setattr(services, "generate_exam_number", lambda db, today, settings: "TJ20261002ABCDEF123456")
    assert register(patient_system, body)["note"] == "保持此请求"
    assert counts(patient_system) == (2, 2, 2)


def test_real_unique_request_collision_can_only_reuse_matching_committed_request(patient_system, monkeypatch):
    body = body_for(patient_system, note="恢复相同请求")
    first = register(patient_system, body)
    original = services.existing_exam_request
    calls = []
    # Force stale initial reads but let the main recovery perform its real DB read.
    def stale(db, body, user):
        calls.append(True)
        return None
    monkeypatch.setattr(services, "existing_exam_request", stale)
    assert register(patient_system, body) == first
    assert len(calls) == 2 and counts(patient_system) == (1, 1, 1)
    monkeypatch.setattr(services, "existing_exam_request", original)


def test_audit_failure_rolls_back_exam_items_and_never_retries_creation(patient_system, monkeypatch, caplog):
    body = body_for(patient_system, note="不应泄露的登记备注")
    generated = []
    original = services.generate_exam_number
    def number(db, today, settings):
        generated.append(True)
        return original(db, today, settings)
    monkeypatch.setattr(services, "generate_exam_number", number)
    def fail(mapper, connection, target):
        if target.action == "exam.create":
            raise IntegrityError("INSERT private_exam_sql", {"note": body["note"]}, Exception("private_database_error"))
    event.listen(AuditLog, "before_insert", fail)
    try:
        response = patient_system.admin.post("/api/exams", json=body)
    finally:
        event.remove(AuditLog, "before_insert", fail)
    assert response.status_code == 503 and len(generated) == 1
    assert counts(patient_system) == (0, 0, 0)
    for marker in (body["note"], "private_exam_sql", "private_database_error"):
        assert marker not in response.text + caplog.text
    assert register(patient_system, body)["note"] == body["note"]
    assert counts(patient_system) == (1, 1, 1)


def test_commit_failure_never_returns_success_or_leaves_exam(patient_system):
    body = body_for(patient_system)
    def fail_commit(db):
        # begin_nested also commits a savepoint; fail only the outer commit.
        if not db.in_nested_transaction():
            raise IntegrityError("COMMIT private_failure", {}, Exception("private_error"))
    event.listen(patient_system.app.state.Session.class_, "before_commit", fail_commit)
    try:
        response = patient_system.admin.post("/api/exams", json=body)
    finally:
        event.remove(patient_system.app.state.Session.class_, "before_commit", fail_commit)
    assert response.status_code == 503 and counts(patient_system) == (0, 0, 0)


def test_summary_note_permissions_and_report_do_not_leak_registration_note(patient_system):
    exam = register(patient_system, body_for(patient_system, note="仅登记员可见的备注"))
    assert "note" not in patient_system.admin.get("/api/exams").json()["items"][0]
    assert "note" not in patient_system.admin.get(f"/api/patients/{exam['patient']['id']}/exams").json()["items"][0]
    for role in ("doctor", "reviewer"):
        client = patient_system.client_for(role)
        detail = client.get(f"/api/exams/{exam['id']}")
        assert detail.status_code == 200 and "note" not in detail.json()
        assert exam["note"] not in detail.text
    registrar = patient_system.client_for("registrar")
    assert registrar.get(f"/api/exams/{exam['id']}").json()["note"] == exam["note"]
    item = exam["items"][0]
    assert patient_system.admin.put(f"/api/exam-items/{item['id']}", json={"value": "软件测试值", "version": item["version"]}).status_code == 200
    ready = patient_system.admin.get(f"/api/exams/{exam['id']}").json()
    assert patient_system.admin.post(f"/api/exams/{exam['id']}/finalize", json={"conclusion": "软件测试结论", "version": ready["version"]}).status_code == 200
    report = patient_system.admin.get(f"/api/exams/{exam['id']}/report")
    assert report.status_code == 200 and exam["note"] not in report.text and "note" not in report.json()


def test_state_progression_persists_and_second_exam_cannot_overwrite_history(patient_system):
    with patient_system.app.state.Session.begin() as db:
        package = db.get(Package, patient_system.package_id)
        package.items.append(CatalogItem(name="第二软件测试项目", department_id=1))
    record = create(patient_system)
    first = register(patient_system, body_for(patient_system, record["id"], exam_date="2026-10-01", note="第一次登记"))
    assert first["status"] == "registered"
    for index, item in enumerate(first["items"]):
        assert patient_system.admin.put(f"/api/exam-items/{item['id']}", json={"value": f"软件测试值{index}", "version": item["version"]}).status_code == 200
        current = patient_system.admin.get(f"/api/exams/{first['id']}").json()
        assert current["status"] == ("in_progress" if index == 0 else "ready_for_review")
    assert patient_system.admin.post(f"/api/exams/{first['id']}/finalize", json={"conclusion": "第一次软件测试结论", "version": current["version"]}).status_code == 200
    completed = patient_system.admin.get(f"/api/exams/{first['id']}").json()
    first_report = patient_system.admin.get(f"/api/exams/{first['id']}/report").json()
    with patient_system.app.state.Session() as db:
        first_snapshot = dict(db.get(Exam, first["id"]).patient_snapshot)
        stored_report = dict(db.scalar(select(ReportSnapshot).where(ReportSnapshot.exam_id == first["id"])).payload)
    assert patient_system.admin.put(f"/api/patients/{record['id']}", json=edit_body(record, name="第二次档案新姓名")).status_code == 200
    second = register(patient_system, body_for(patient_system, record["id"], exam_date="2026-10-02", note="第二次登记"))
    assert second["id"] != first["id"] and second["exam_number"] != first["exam_number"]
    assert second["status"] == "registered" and second["patient"]["name"] == "第二次档案新姓名"
    assert patient_system.admin.get(f"/api/exams/{first['id']}").json() == completed
    assert patient_system.admin.get(f"/api/exams/{first['id']}/report").json() == first_report
    with patient_system.app.state.Session() as db:
        assert db.get(Exam, first["id"]).patient_snapshot == first_snapshot
        assert db.scalar(select(ReportSnapshot).where(ReportSnapshot.exam_id == first["id"])).payload == stored_report
    from app.main import create_app
    restarted = create_app(settings=patient_system.settings)
    try:
        with TestClient(restarted) as client:
            headers = dict(patient_system.admin.headers)
            assert client.get(f"/api/exams/{first['id']}", headers=headers).json() == completed
            assert client.get(f"/api/exams/{second['id']}", headers=headers).json() == second
    finally:
        restarted.state.engine.dispose()
    history = patient_system.admin.get(f"/api/patients/{record['id']}/exams?page_size=1").json()
    assert history["total"] == 2 and len(history["items"]) == 1 and history["items"][0]["id"] == second["id"]
    next_page = patient_system.admin.get(f"/api/patients/{record['id']}/exams?page_size=1&page=2").json()
    assert next_page["items"][0]["id"] == first["id"]


def test_many_registrations_are_unique_and_server_list_remains_paginated(patient_system):
    body = body_for(patient_system)
    records = [register(patient_system, dict(body, request_id=uuid4().hex)) for _ in range(21)]
    assert len({row["exam_number"] for row in records}) == 21
    first = patient_system.admin.get("/api/exams").json()
    assert first["total"] == 21 and len(first["items"]) == 20
    assert len(patient_system.admin.get("/api/exams?page=2").json()["items"]) == 1
    assert patient_system.admin.get("/api/exams?page_size=101").status_code == 422
    match = patient_system.admin.get("/api/exams", params={"q": records[0]["exam_number"]}).json()
    assert match["total"] == 1 and match["items"][0]["id"] == records[0]["id"]


def test_invalid_patient_or_package_and_empty_search_have_safe_errors(patient_system):
    body = body_for(patient_system)
    assert patient_system.admin.post("/api/exams", json=dict(body, patient_id=999999)).status_code == 404
    assert patient_system.admin.post("/api/exams", json=dict(body, package_id=999999)).status_code == 422
    with patient_system.app.state.Session.begin() as db:
        db.get(Package, body["package_id"]).is_active = False
    assert patient_system.admin.post("/api/exams", json=body).status_code == 422
    assert counts(patient_system) == (0, 0, 0)
    assert patient_system.admin.get("/api/exams?q=empty").json()["items"] == []


def test_unique_error_classifier_does_not_accept_unrelated_write_failures():
    def pg(constraint, sqlstate="23505"):
        return IntegrityError("private", {}, SimpleNamespace(sqlstate=sqlstate, diag=SimpleNamespace(constraint_name=constraint)))
    assert services.exam_unique_collision(pg("exams_request_id_key"), "request_id")
    assert services.exam_unique_collision(pg("exams_exam_number_key"), "exam_number")
    assert not services.exam_unique_collision(pg("audit_logs_pkey"), "request_id")
    assert not services.exam_unique_collision(pg("exams_request_id_key", "23503"), "request_id")
    assert not services.exam_unique_collision(IntegrityError("private", {}, Exception("UNIQUE constraint failed: exams.request_id")), "request_id")


def test_exam_registration_migration_preserves_populated_clinical_history(tmp_path):
    url = "sqlite:///" + (tmp_path / "old_exams.db").as_posix()
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.attributes["database_url"] = url
    command.upgrade(config, "0003")
    engine, _ = create_database(url)
    old = MetaData()
    old.reflect(engine)
    now = datetime(2026, 10, 1, 16, 30)
    with engine.begin() as connection:
        connection.execute(old.tables["users"].insert(), {"id": 1, "username": "migration_exam", "display_name": "迁移测试", "password_hash": "invalid-test-marker", "role": "admin", "department_id": None, "is_active": True})
        connection.execute(old.tables["departments"].insert(), {"id": 1, "name": "迁移科室"})
        connection.execute(old.tables["packages"].insert(), {"id": 1, "name": "旧套餐", "description": "迁移验证", "is_active": True})
        connection.execute(old.tables["patients"].insert(), {"id": 1, "name": "旧档案测试", "sex": "other", "birth_date": None, "phone": None, "created_at": now, "updated_at": now, "version": 1})
        connection.execute(old.tables["exams"].insert(), {"id": 1, "exam_number": "OLD-EXAM-MIGRATION", "request_id": "old-exam-request", "requested_by": 1, "patient_id": 1, "package_id": 1, "patient_snapshot": {"id": 1, "name": "旧档案快照"}, "package_name": "旧套餐", "status": "completed", "version": 4, "conclusion": "旧软件测试结论", "completed_at": now, "latest_report_revision": 1, "created_at": now, "updated_at": now})
        connection.execute(old.tables["exam_items"].insert(), {"id": 1, "exam_id": 1, "item_name": "旧项目", "department_id": 1, "department_name": "迁移科室", "unit": "", "reference_range": "", "value": "旧软件测试值", "is_abnormal": False, "note": "旧结果备注", "status": "completed", "version": 2})
        connection.execute(old.tables["result_revisions"].insert(), {"id": 1, "item_id": 1, "version": 2, "value": "旧软件测试值", "is_abnormal": False, "note": "旧结果备注", "changed_by": 1, "changed_by_name": "迁移测试", "changed_at": now})
        connection.execute(old.tables["report_snapshots"].insert(), {"id": 1, "exam_id": 1, "revision": 1, "payload": {"conclusion": "旧软件测试结论", "patient": {"name": "旧档案快照"}}, "created_at": now})
    with engine.connect() as connection:
        before = {name: connection.execute(table.select()).all() for name, table in old.tables.items()}
    command.upgrade(config, "head")
    command.check(config)
    with engine.connect() as connection:
        for name, table in old.tables.items():
            if name != "alembic_version":
                assert connection.execute(table.select()).all() == before[name]
        assert connection.execute(text("SELECT exam_date, note FROM exams")).one() == ("2026-10-02", None)
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
        assert connection.execute(text("PRAGMA integrity_check")).scalar_one() == "ok"
    engine.dispose()


@pytest.mark.parametrize("url,backfill", [("sqlite:///offline_only.db", "date(created_at, '+8 hours')"), ("postgresql+psycopg://offline:unused@localhost/offline", "CAST(created_at + INTERVAL '8 hours' AS DATE)")])
def test_exam_date_migration_offline_sql_is_additive_for_sqlite_and_postgresql(url, backfill):
    output = StringIO()
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"), output_buffer=output)
    config.attributes["database_url"] = url
    command.upgrade(config, "0003:0004", sql=True)
    sql = output.getvalue()
    assert "ALTER TABLE exams ADD COLUMN exam_date DATE" in sql
    assert "ALTER TABLE exams ADD COLUMN note TEXT" in sql
    assert "UPDATE exams SET exam_date = " + backfill in sql
    assert "DROP TABLE" not in sql and "_alembic_tmp" not in sql
