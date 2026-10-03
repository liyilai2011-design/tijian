from conftest import new_exam, finish_items


def test_registration_to_report_and_revision(system):
    frontdesk = system["login"]("frontdesk")
    admin = system["login"]("admin")
    reviewer = system["login"]("reviewer")
    exam, _ = new_exam(frontdesk)
    assert exam["status"] == "registered"
    premature = reviewer.post(f"/api/exams/{exam['id']}/finalize", json={"conclusion": "未完成时禁止总检", "version": exam["version"]})
    assert premature.status_code == 409
    complete = finish_items(admin, admin.get(f"/api/exams/{exam['id']}").json())
    assert complete["status"] == "ready_for_review"
    response = reviewer.post(f"/api/exams/{exam['id']}/finalize", json={"conclusion": "测试总检结论", "version": complete["version"]})
    assert response.status_code == 200, response.text
    finalized = response.json()
    report = reviewer.get(f"/api/exams/{exam['id']}/report").json()
    assert report["revision"] == 1
    assert report["conclusion"] == "测试总检结论"
    assert len(report["items"]) == len(complete["items"])
    item = finalized["items"][0]
    assert admin.put(f"/api/exam-items/{item['id']}", json={"value": "不可直接覆盖", "is_abnormal": False, "note": None, "version": item["version"]}).status_code == 409
    reopened = reviewer.post(f"/api/exams/{exam['id']}/reopen", json={"reason": "测试修订流程", "version": finalized["version"]})
    assert reopened.status_code == 200, reopened.text
    assert reviewer.get(f"/api/exams/{exam['id']}/report").status_code == 409
    item = reopened.json()["items"][0]
    assert admin.put(f"/api/exam-items/{item['id']}", json={"value": "已修订的测试结果", "is_abnormal": False, "note": "修订说明", "version": item["version"]}).status_code == 200
    updated = reviewer.get(f"/api/exams/{exam['id']}").json()
    assert reviewer.post(f"/api/exams/{exam['id']}/finalize", json={"conclusion": "修订后结论", "version": updated["version"]}).status_code == 200
    report2 = reviewer.get(f"/api/exams/{exam['id']}/report").json()
    assert report2["revision"] == 2
    assert report2["conclusion"] == "修订后结论"
    from sqlalchemy import select
    from app.models import ReportSnapshot
    with system["app"].state.Session() as session:
        snapshots = session.scalars(select(ReportSnapshot).where(ReportSnapshot.exam_id == exam["id"]).order_by(ReportSnapshot.revision)).all()
        assert len(snapshots) == 2
        assert snapshots[0].payload == report
        assert snapshots[1].payload == report2


def test_department_authorization_is_enforced_by_server(system):
    admin = system["login"]("admin")
    doctor = system["login"]("doctor_internal")
    lab = system["login"]("doctor_lab")
    exam, _ = new_exam(admin)
    own = doctor.get(f"/api/exams/{exam['id']}")
    assert own.status_code == 200
    own_items = own.json()["items"]
    assert own_items
    identity = doctor.get("/api/auth/me").json()["user"]
    assert all(item["department_id"] == identity["department_id"] for item in own_items)
    foreign_item = next(item for item in exam["items"] if item["department_id"] != identity["department_id"])
    payload = {"value": "越权操作", "is_abnormal": False, "note": None, "version": foreign_item["version"]}
    assert doctor.put(f"/api/exam-items/{foreign_item['id']}", json=payload).status_code == 403
    assert doctor.get(f"/api/exam-items/{foreign_item['id']}/history").status_code == 403
    assert doctor.get(f"/api/exams/{exam['id']}/report").status_code == 403
    assert doctor.post("/api/patients", json={"name": "越权登记", "sex": "other", "request_id": "unauthorized-patient"}).status_code == 403
    assert lab.get(f"/api/exams/{exam['id']}").status_code == 200
    finish_items(admin, exam)
    frontdesk = system["login"]("frontdesk")
    visible = frontdesk.get(f"/api/exams/{exam['id']}").json()
    assert all(item["value"] is None and item["note"] is None for item in visible["items"])


def test_duplicate_creation_is_safe_after_lost_response(system):
    admin = system["login"]("admin")
    exam, payload = new_exam(admin)
    repeated = admin.post("/api/exams", json=payload)
    assert repeated.status_code in (200, 201), repeated.text
    assert repeated.json()["id"] == exam["id"]
    assert admin.get("/api/exams").json()["total"] == 1
    second, other_payload = new_exam(admin, name="第二位测试人员", request_id="second-request-0002")
    other_payload["request_id"] = payload["request_id"]
    assert admin.post("/api/exams", json=other_payload).status_code == 409
    assert admin.get("/api/exams").json()["total"] == 2


def test_result_conflict_keeps_first_save_and_history(system):
    admin = system["login"]("admin")
    exam, _ = new_exam(admin)
    item = exam["items"][0]
    url = f"/api/exam-items/{item['id']}"
    payload = {"value": "第一次结果", "is_abnormal": True, "note": "第一次说明", "version": item["version"]}
    first = admin.put(url, json=payload)
    assert first.status_code == 200
    payload["value"] = "旧页面覆盖"
    assert admin.put(url, json=payload).status_code == 409
    current = admin.get(f"/api/exams/{exam['id']}").json()["items"][0]
    assert current["value"] == "第一次结果"
    payload.update(version=current["version"], value="第二次结果")
    assert admin.put(url, json=payload).status_code == 200
    history = admin.get(url + "/history").json()
    assert {row["value"] for row in history} == {"第一次结果", "第二次结果"}


def test_authentication_csrf_origin_and_logout(system):
    anonymous = system["anonymous"]
    assert anonymous.get("/api/exams").status_code == 401
    assert anonymous.post("/api/auth/login", json={"username": "admin", "password": "wrong-password"}).status_code == 401
    assert anonymous.post("/api/auth/login", headers={"Origin": "https://untrusted.example"}, json={"username": "admin", "password": "Isolated-test-only-2026!"}).status_code == 403
    admin = system["login"]("admin")
    token = admin.headers.pop("X-CSRF-Token")
    patient = {"name": "CSRF测试", "sex": "other", "request_id": "csrf-patient-registration"}
    assert admin.post("/api/patients", json=patient).status_code == 403
    admin.headers["X-CSRF-Token"] = token
    assert admin.post("/api/patients", headers={"Origin": "https://untrusted.example"}, json=patient).status_code == 403
    assert admin.post("/api/auth/logout").status_code == 200
    assert admin.get("/api/auth/me").status_code == 401


def test_pagination_validation_and_soft_delete(system):
    admin = system["login"]("admin")
    for index, name in enumerate(("分页测试甲", "分页测试乙", "分页测试丙")):
        assert admin.post("/api/patients", json={"name": name, "sex": "other", "request_id": f"pagination-patient-{index}"}).status_code in (200, 201)
    first = admin.get("/api/patients", params={"q": "分页测试", "page_size": 2, "page": 1}).json()
    second = admin.get("/api/patients", params={"q": "分页测试", "page_size": 2, "page": 2}).json()
    assert first["total"] == second["total"] == 3
    assert len(first["items"]) == 2 and len(second["items"]) == 1
    assert {row["id"] for row in first["items"]}.isdisjoint({row["id"] for row in second["items"]})
    assert admin.get("/api/patients", params={"page_size": 101}).status_code == 422
    item = second["items"][0]
    assert admin.post(f"/api/patients/{item['id']}/archive", json={"reason": "重复测试登记"}).status_code == 200
    assert admin.get("/api/patients", params={"q": "分页测试"}).json()["total"] == 2
    invalid = admin.post("/api/patients", json={"name": "", "sex": "female", "birth_date": "2099-01-01", "request_id": "invalid-registration"})
    assert invalid.status_code == 422


def test_commit_failure_rolls_back_result_history_and_status(system):
    from sqlalchemy import event, func, select
    from sqlalchemy.exc import IntegrityError
    from app.models import AuditLog, ResultRevision

    admin = system["login"]("admin")
    exam, _ = new_exam(admin)
    item = exam["items"][0]

    def reject_audit(mapper, connection, target):
        if target.action == "result.update":
            raise IntegrityError("simulated audit persistence failure", {}, Exception("test failure"))

    event.listen(AuditLog, "before_insert", reject_audit)
    try:
        response = admin.put(f"/api/exam-items/{item['id']}", json={"value": "不能部分保存", "is_abnormal": True, "note": "失败测试", "version": item["version"]})
        assert response.status_code == 503
    finally:
        event.remove(AuditLog, "before_insert", reject_audit)
    saved = admin.get(f"/api/exams/{exam['id']}").json()
    assert saved["status"] == "registered" and saved["version"] == exam["version"]
    assert all(entry["value"] is None for entry in saved["items"])
    with system["app"].state.Session() as session:
        assert session.scalar(select(func.count()).select_from(ResultRevision)) == 0
        assert session.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "result.update")) == 0


def test_simultaneous_result_saves_never_silently_overwrite(system):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    admin = system["login"]("admin")
    exam, _ = new_exam(admin)
    item = exam["items"][0]
    barrier = Barrier(2)

    def save(value):
        barrier.wait(timeout=10)
        return admin.put(f"/api/exam-items/{item['id']}", json={"value": value, "is_abnormal": False, "note": None, "version": item["version"]})

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(save, "并发测试甲")
        second = pool.submit(save, "并发测试乙")
        responses = [first.result(timeout=30), second.result(timeout=30)]
    assert sum(response.status_code == 200 for response in responses) == 1
    assert all(response.status_code in (200, 409, 503) for response in responses)
    history = admin.get(f"/api/exam-items/{item['id']}/history").json()
    assert len(history) == 1
    current = admin.get(f"/api/exams/{exam['id']}").json()["items"][0]
    assert current["value"] == history[0]["value"]


def test_registration_retry_returns_original_patient(system):
    admin = system["login"]("admin")
    body = {"name": "登记重试测试", "sex": "female", "birth_date": None, "phone": None, "request_id": "same-registration-request"}
    first = admin.post("/api/patients", json=body)
    repeated = admin.post("/api/patients", json=body)
    assert first.status_code in (200, 201) and repeated.status_code in (200, 201)
    assert first.json()["id"] == repeated.json()["id"]
    assert admin.get("/api/patients").json()["total"] == 1
    changed = {**body, "name": "不同内容"}
    assert admin.post("/api/patients", json=changed).status_code == 409
    assert system["login"]("frontdesk").post("/api/patients", json=body).status_code == 409
    assert admin.get("/api/patients").json()["total"] == 1
    from sqlalchemy import func, select
    from app.models import AuditLog
    with system["app"].state.Session() as session:
        assert session.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "patient.create")) == 1
    assert admin.post(f"/api/patients/{first.json()['id']}/archive", json={"reason": "测试归档后重试"}).status_code == 200
    assert admin.post("/api/patients", json=body).status_code == 409


def test_simultaneous_registration_retries_create_only_one_patient(system):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sqlalchemy import func, select
    from app.models import AuditLog

    admin = system["login"]("admin")
    body = {"name": "并发登记测试", "sex": "other", "request_id": "simultaneous-registration-request"}
    barrier = Barrier(2)

    def register():
        barrier.wait(timeout=10)
        return admin.post("/api/patients", json=body)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(register)
        second = pool.submit(register)
        responses = [first.result(timeout=30), second.result(timeout=30)]
    assert all(response.status_code == 200 for response in responses)
    assert responses[0].json()["id"] == responses[1].json()["id"]
    assert admin.get("/api/patients").json()["total"] == 1
    with system["app"].state.Session() as session:
        assert session.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "patient.create")) == 1
