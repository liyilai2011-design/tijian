from datetime import datetime, timedelta
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased, object_session, selectinload
from sqlalchemy.orm.attributes import set_committed_value

from .models import AuditLog, CatalogItem, DepartmentRevision, Exam, ExamDepartment, ExamItem, ExamNumberCounter, Package, Patient, ReportSnapshot, ResultRevision, User, local_today, utcnow
from .config import Settings
from .security import lock_data_anchor


def iso(value):
    if value is None:
        return None
    return value.isoformat() + ("Z" if isinstance(value, datetime) and value.tzinfo is None else "")


def audit(db, user: User | None, action: str, entity_type: str, entity_id, **details):
    # Medical result text belongs in the restricted revision tables, not in runtime logs.
    db.add(AuditLog(user_id=user.id if user else None, action=action, entity_type=entity_type, entity_id=str(entity_id), details=details))


def patient_json(patient: Patient):
    return {"id": patient.id, "name": patient.name, "sex": patient.sex, "birth_date": iso(patient.birth_date), "phone": patient.phone, "created_at": iso(patient.created_at)}


PATIENT_FIELDS = ("name", "sex", "birth_date", "phone", "identity_number", "employer", "ethnicity", "address", "note")
EXAM_PATIENT_FIELDS = ("id", "name", "sex", "birth_date", "phone")


def basic_exam_patient(snapshot):
    return {field: snapshot.get(field) for field in EXAM_PATIENT_FIELDS}


def patient_record_json(patient, today=None):
    today = today or local_today()
    birth = patient.birth_date
    age = None if birth is None or birth > today else today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))
    return {"id": patient.id, "name": patient.name, "sex": patient.sex, "birth_date": iso(birth), "age": age, "phone": patient.phone, "identity_number": patient.identity_number, "employer": patient.employer, "ethnicity": patient.ethnicity, "address": patient.address, "note": patient.note, "created_at": iso(patient.created_at), "updated_at": iso(patient.updated_at or patient.created_at), "version": patient.version}


def lock_active_patient(db, patient_id):
    # All profile edits, archives and exam creation take this lock first. A real
    # no-change write protects SQLite too, where SELECT FOR UPDATE is ineffective.
    claim = db.execute(update(Patient).where(Patient.id == patient_id, Patient.deleted_at.is_(None)).values(id=Patient.id).execution_options(synchronize_session=False))
    if claim.rowcount != 1:
        raise HTTPException(404, "未找到有效受检者")
    patient = db.scalar(select(Patient).where(Patient.id == patient_id, Patient.deleted_at.is_(None)).execution_options(populate_existing=True))
    if patient is None:
        raise HTTPException(404, "未找到有效受检者")
    return patient


def conflicting_identity(db, identity_number, exclude_id=None):
    if identity_number is None:
        return False
    query = select(Patient.id).where(Patient.deleted_at.is_(None), Patient.identity_number == identity_number)
    if exclude_id is not None:
        query = query.where(Patient.id != exclude_id)
    return db.scalar(query.limit(1)) is not None


def existing_registration(db, body, user):
    existing = db.scalar(select(Patient).where(Patient.registration_request_id == body.request_id))
    if existing is None:
        return None
    same_fields = all(getattr(existing, field) == getattr(body, field) for field in PATIENT_FIELDS)
    if existing.deleted_at is not None or existing.registered_by != user.id or not same_fields:
        raise HTTPException(409, "该登记请求编号已被使用或记录已归档，请刷新后重试")
    return existing


def create_patient(db, body, user):
    existing = existing_registration(db, body, user)
    if existing is not None:
        return existing
    if conflicting_identity(db, body.identity_number):
        raise HTTPException(409, "已有有效档案使用此身份证号，请先搜索现有受检者")
    now = utcnow()
    patient = Patient(**body.model_dump(exclude={"request_id"}), registration_request_id=body.request_id, registered_by=user.id, created_at=now, updated_at=now)
    db.add(patient)
    db.flush()
    audit(db, user, "patient.create", "patient", patient.id, changed_fields=[field for field in PATIENT_FIELDS if getattr(patient, field) is not None], version=patient.version)
    return patient


def item_json(item: ExamItem, hide_results=False):
    if not hasattr(item, "_recording"):
        attach_recording(object_session(item), [item], ResultRevision, "item_id")
    return {"id": item.id, "item_name": item.item_name, "department_id": item.department_id, "department_name": item.department_name, "category": item.category, "unit": item.unit, "reference_range": item.reference_range, "result_type": item.result_type, "abnormal_allowed": item.abnormal_allowed, "value": None if hide_results else item.value, "is_abnormal": False if hide_results else item.is_abnormal, "note": None if hide_results else item.note, "status": item.status, "version": item.version, **(empty_recording() if hide_results else item._recording)}


def empty_recording():
    return dict.fromkeys(("submitted_by", "submitted_by_name", "submitted_at", "updated_by", "updated_by_name", "updated_at"))


def attach_recording(db, records, revision_model, target_field):
    from sqlalchemy import func
    by_id = {record.id: record for record in records}
    for record in records:
        record._recording = empty_recording()
    if db is None or not by_id:
        return
    target = getattr(revision_model, target_field)
    extremes = select(target.label("target"), func.min(revision_model.version).label("first_version"), func.max(revision_model.version).label("last_version")).where(target.in_(by_id)).group_by(target).subquery()
    first, last = aliased(revision_model), aliased(revision_model)
    rows = db.execute(select(first, last).join(extremes, (getattr(first, target_field) == extremes.c.target) & (first.version == extremes.c.first_version))
                      .join(last, (getattr(last, target_field) == extremes.c.target) & (last.version == extremes.c.last_version)))
    for oldest, newest in rows:
        by_id[getattr(oldest, target_field)]._recording = {"submitted_by": oldest.changed_by, "submitted_by_name": oldest.changed_by_name, "submitted_at": iso(oldest.changed_at), "updated_by": newest.changed_by, "updated_by_name": newest.changed_by_name, "updated_at": iso(newest.changed_at)}


def department_json(record):
    if not hasattr(record, "_recording"):
        attach_recording(object_session(record), [record], DepartmentRevision, "exam_department_id")
    return {"id": record.id, "exam_id": record.exam_id, "department_id": record.department_id, "department_name": record.department_name,
            "conclusion": record.conclusion, "note": record.note, "version": record.version, **record._recording}


def exam_departments(db, exam, user):
    if db is None or user.role == "registrar":
        return []
    statement = select(ExamDepartment).where(ExamDepartment.exam_id == exam.id)
    if user.role == "doctor":
        statement = statement.where(ExamDepartment.department_id == user.department_id)
    records = list(db.scalars(statement.order_by(ExamDepartment.department_id)))
    attach_recording(db, records, DepartmentRevision, "exam_department_id")
    return [department_json(record) for record in records]


def visible_items(exam, user):
    return [item for item in exam.items if user.role != "doctor" or item.department_id == user.department_id]


def exam_json(exam: Exam, user: User, detail=True):
    items = visible_items(exam, user)
    patient = basic_exam_patient(exam.patient_snapshot)
    result = {"id": exam.id, "exam_number": exam.exam_number, "patient": patient, "package_name": exam.package_name, "package_id": exam.package_id, "package_version": exam.package_version, "exam_type": exam.exam_type, "exam_date": iso(exam_date(exam)), "status": exam.status, "created_at": iso(exam.created_at), "completed_count": sum(item.status == "completed" for item in items), "total_count": len(items), "version": exam.version}
    if detail:
        attach_recording(object_session(exam), items, ResultRevision, "item_id")
        result.update(items=[item_json(item, user.role == "registrar") for item in items], conclusion=exam.conclusion if user.role in {"admin", "reviewer"} else None, completed_at=iso(exam.completed_at), latest_report_revision=exam.latest_report_revision)
        result["departments"] = exam_departments(object_session(exam), exam, user)
        if user.role in {"admin", "registrar"}:
            result["note"] = exam.note
    return result


def get_exam(db, exam_id, user, lock=False):
    statement = select(Exam).where(Exam.id == exam_id, Exam.deleted_at.is_(None)).options(selectinload(Exam.items))
    if lock:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    exam = db.scalar(statement)
    if exam is None:
        raise HTTPException(404, "未找到该体检")
    if user.role == "doctor" and not any(item.department_id == user.department_id for item in exam.items):
        raise HTTPException(403, "只能查看本科室的体检")
    return exam


def claim_exam(db, exam: Exam, expected: int):
    if exam.version != expected:
        raise HTTPException(409, "记录已被其他人修改，请刷新后重试")
    result = db.execute(update(Exam).where(Exam.id == exam.id, Exam.version == expected).values(version=expected + 1, updated_at=utcnow()).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        raise HTTPException(409, "记录已被其他人修改，请刷新后重试")
    set_committed_value(exam, "version", expected + 1)


def exam_date(exam):
    return exam.exam_date or (exam.created_at + timedelta(hours=8)).date()


def exam_unique_collision(error, field):
    """Recognize only this exact unique key; unrelated write failures must propagate."""
    original = error.orig
    if getattr(original, "sqlstate", None) == "23505":
        return getattr(getattr(original, "diag", None), "constraint_name", None) == "exams_" + field + "_key"
    return getattr(original, "sqlite_errorcode", None) == 2067 and str(original) == "UNIQUE constraint failed: exams." + field


def existing_exam_request(db, body, user):
    existing = db.scalar(select(Exam).where(Exam.request_id == body.request_id).options(selectinload(Exam.items)))
    if existing is not None:
        if (existing.deleted_at is not None or existing.patient_id != body.patient_id or existing.package_id != body.package_id
                or existing.requested_by != user.id or existing.note != body.note
                or existing.exam_type != body.exam_type
                or (body.package_version is not None and existing.package_version != body.package_version)
                or (body.exam_date is not None and exam_date(existing) != body.exam_date)):
            raise HTTPException(409, "该请求编号已用于其他登记，请核对后重新登记")
    return existing


def new_exam_number(registration_date):
    return "TJ" + registration_date.strftime("%Y%m%d") + uuid4().hex[:12].upper()


def generate_exam_number(db, registration_date, settings):
    if settings.exam_number_mode == "random":
        return settings.exam_number_prefix + new_exam_number(registration_date)
    # UPSERT takes a real per-day row lock on PostgreSQL and SQLite. Allocation
    # belongs to the outer transaction, so failed registrations leave no counter.
    if db.bind.dialect.name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        from sqlalchemy.dialects.postgresql import insert
    statement = insert(ExamNumberCounter).values(registration_date=registration_date, value=1)
    statement = statement.on_conflict_do_update(index_elements=[ExamNumberCounter.registration_date], set_={"value": ExamNumberCounter.value + 1}).returning(ExamNumberCounter.value)
    sequence = db.scalar(statement)
    if sequence >= 10 ** settings.exam_number_width:
        raise HTTPException(503, "数据尚未保存，今日体检编号已达到配置上限，请联系管理员")
    return (settings.exam_number_prefix + registration_date.strftime(settings.exam_number_date_format)
            + settings.exam_number_separator + str(sequence).zfill(settings.exam_number_width))


def create_exam(db, body, user, settings=None):
    # Always acquire the same foundation/account anchor before any patient or
    # counter lock. Endpoints additionally recheck the session after waiting.
    lock_data_anchor(db)
    existing = existing_exam_request(db, body, user)
    if existing is not None:
        return existing, False
    patient = lock_active_patient(db, body.patient_id)
    # Waiting for the patient lock may have let an identical request finish first.
    existing = existing_exam_request(db, body, user)
    if existing is not None:
        return existing, False
    package = None if body.package_id is None else db.scalar(select(Package).where(Package.id == body.package_id).options(selectinload(Package.items).selectinload(CatalogItem.department)).execution_options(populate_existing=True))
    if package is not None and body.package_version is not None and package.version != body.package_version:
        raise HTTPException(409, "套餐已被修改，请重新读取套餐后核对")
    if body.package_id is not None and (package is None or not package.is_active or not package.items):
        raise HTTPException(422, "套餐不存在、已停用或没有检查项目")
    if package is not None and any(not item.is_active or not item.department.is_active for item in package.items):
        raise HTTPException(422, "套餐包含已停用的检查项目或科室，请选择其他套餐")
    now = utcnow()
    registration_date = (now + timedelta(hours=8)).date()
    settings = settings or Settings(_env_file=None)
    for attempt in range(3):
        exam = Exam(exam_number=generate_exam_number(db, registration_date, settings), request_id=body.request_id, requested_by=user.id, patient_id=patient.id, package_id=package.id if package else None, package_version=package.version if package else None, patient_snapshot=patient_json(patient), package_name=package.name if package else "未选择套餐", exam_date=body.exam_date or registration_date, exam_type=body.exam_type, note=body.note, status="registered", created_at=now, updated_at=now)
        exam.items = [] if package is None else [ExamItem(item_name=item.name, department_id=item.department_id, department_name=item.department.name, category=item.category, unit=item.unit, reference_range=item.reference_range, result_type=item.result_type, abnormal_allowed=item.abnormal_allowed) for item in package.items]
        try:
            # Retry only a number collision, without persisting partial items or logs.
            with db.begin_nested():
                db.add(exam)
                db.flush()
            break
        except IntegrityError as error:
            if not exam_unique_collision(error, "exam_number"):
                raise
            if attempt == 2:
                raise HTTPException(503, "数据尚未保存，体检编号暂时无法生成，请重新尝试") from None
    audit(db, user, "exam.create", "exam", exam.id)
    departments = {item.department_id: item.department_name for item in exam.items}
    db.add_all([ExamDepartment(exam_id=exam.id, department_id=key, department_name=name) for key, name in departments.items()])
    return exam, True


def update_result(db, item_id, body, user):
    # Lock/claim parent first; this serializes finalize and result writes for this exam.
    exam_id = db.scalar(select(ExamItem.exam_id).where(ExamItem.id == item_id))
    if exam_id is None:
        raise HTTPException(404, "未找到检查项目")
    exam = get_exam(db, exam_id, user, lock=True)
    item = next(item for item in exam.items if item.id == item_id)
    if user.role != "admin" and (user.role != "doctor" or item.department_id != user.department_id):
        raise HTTPException(403, "只能录入本科室的检查结果")
    if exam.status == "completed":
        raise HTTPException(409, "体检已总检，请先重新打开体检")
    if item.version != body.version:
        raise HTTPException(409, "检查结果已被修改，请刷新后重试")
    if not item.abnormal_allowed and body.is_abnormal:
        raise HTTPException(422, "此检查项目不支持异常标记")
    if item.result_type == "number":
        from decimal import Decimal, InvalidOperation
        import re
        if not re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", body.value):
            raise HTTPException(422, "此检查项目必须填写有效数值")
        try:
            numeric = Decimal(body.value)
        except InvalidOperation:
            raise HTTPException(422, "此检查项目必须填写有效数值") from None
        import math
        if not numeric.is_finite() or not math.isfinite(float(body.value)):
            raise HTTPException(422, "此检查项目必须填写有限数值")
    elif item.result_type == "boolean" and body.value not in {"是", "否"}:
        raise HTTPException(422, "此检查项目只能填写“是”或“否”")
    if item.status == "completed" and (item.value, item.is_abnormal, item.note) == (body.value, body.is_abnormal, body.note):
        return item
    claim_exam(db, exam, exam.version)
    item.value = body.value
    item.is_abnormal = body.is_abnormal
    item.note = body.note
    item.status = "completed"
    item.version += 1
    db.add(ResultRevision(item_id=item.id, version=item.version, value=item.value, is_abnormal=item.is_abnormal, note=item.note, changed_by=user.id, changed_by_name=user.display_name))
    exam.status = "ready_for_review" if all(entry.status == "completed" for entry in exam.items) else "in_progress"
    audit(db, user, "result.update", "exam_item", item.id, version=item.version)
    return item


def finalize_exam(db, exam_id, body, user):
    exam = get_exam(db, exam_id, user, lock=True)
    if exam.status == "completed":
        raise HTTPException(409, "此体检已完成总检")
    if not exam.items or any(item.status != "completed" for item in exam.items):
        raise HTTPException(409, "仍有未完成的检查项目，暂不能总检")
    claim_exam(db, exam, body.version)
    exam.status = "completed"
    exam.conclusion = body.conclusion
    exam.completed_at = utcnow()
    exam.latest_report_revision += 1
    attach_recording(db, exam.items, ResultRevision, "item_id")
    payload = {"exam_id": exam.id, "exam_number": exam.exam_number, "exam_type": exam.exam_type, "exam_date": iso(exam_date(exam)), "revision": exam.latest_report_revision, "issued_at": iso(exam.completed_at), "patient": basic_exam_patient(exam.patient_snapshot), "package_name": exam.package_name, "items": [item_json(item) for item in exam.items], "departments": exam_departments(db, exam, user), "conclusion": exam.conclusion, "reviewer_name": user.display_name}
    db.add(ReportSnapshot(exam_id=exam.id, revision=exam.latest_report_revision, payload=payload))
    audit(db, user, "exam.finalize", "exam", exam.id, revision=exam.latest_report_revision)
    return exam


def reopen_exam(db, exam_id, body, user):
    exam = get_exam(db, exam_id, user, lock=True)
    if exam.status != "completed":
        raise HTTPException(409, "只有已总检的体检可以重新打开")
    claim_exam(db, exam, body.version)
    exam.status = "ready_for_review"
    exam.conclusion = None
    exam.completed_at = None
    audit(db, user, "exam.reopen", "exam", exam.id, reason=body.reason, previous_revision=exam.latest_report_revision)
    return exam
