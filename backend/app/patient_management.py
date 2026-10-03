"""Patient profiles; clinical snapshots remain separate and immutable."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from .models import Exam, Patient, utcnow
from .schemas import PatientArchive, PatientCreate, PatientUpdate
from .security import get_db, roles
from .services import PATIENT_FIELDS, audit, conflicting_identity, create_patient, exam_json, existing_registration, lock_active_patient, patient_record_json

router = APIRouter(prefix="/api/patients")


def active_patient(db, patient_id):
    patient = db.scalar(select(Patient).where(Patient.id == patient_id, Patient.deleted_at.is_(None)))
    if patient is None:
        raise HTTPException(404, "未找到有效受检者")
    return patient


@router.get("")
def patients(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), q: str = Query("", max_length=100), user=Depends(roles("admin", "registrar")), db=Depends(get_db)):
    statement = select(Patient).where(Patient.deleted_at.is_(None))
    query = q.strip()
    if query:
        statement = statement.where(or_(Patient.name.contains(query, autoescape=True), Patient.phone.contains(query, autoescape=True), Patient.identity_number.contains(query.upper(), autoescape=True)))
    total = db.scalar(select(func.count()).select_from(statement.subquery()))
    rows = db.scalars(statement.order_by(Patient.id.desc()).offset((page - 1) * page_size).limit(page_size))
    return {"items": [patient_record_json(row) for row in rows], "total": total, "page": page, "page_size": page_size}


@router.post("")
def add_patient(body: PatientCreate, user=Depends(roles("admin", "registrar")), db=Depends(get_db)):
    try:
        patient = create_patient(db, body, user)
        db.commit()
    except IntegrityError:
        db.rollback()
        # Recover a lost response/concurrent request only for the same actor/data.
        patient = existing_registration(db, body, user)
        if patient is None:
            if conflicting_identity(db, body.identity_number):
                raise HTTPException(409, "已有有效档案使用此身份证号，请先搜索现有受检者")
            raise
    return patient_record_json(patient)


@router.get("/{patient_id}")
def patient_detail(patient_id: int, user=Depends(roles("admin", "registrar")), db=Depends(get_db)):
    return patient_record_json(active_patient(db, patient_id))


@router.put("/{patient_id}")
def edit_patient(patient_id: int, body: PatientUpdate, user=Depends(roles("admin", "registrar")), db=Depends(get_db)):
    try:
        patient = lock_active_patient(db, patient_id)
        if patient.version != body.version:
            raise HTTPException(409, "档案已被其他人修改，请读取最新资料后核对")
        # Old clients do not know ethnicity/address: omission must preserve them.
        omitted_new = {field for field in ("ethnicity", "address") if field not in body.model_fields_set}
        changed = [field for field in PATIENT_FIELDS if field not in omitted_new and getattr(patient, field) != getattr(body, field)]
        if not changed:
            db.commit()
            return patient_record_json(patient)
        if conflicting_identity(db, body.identity_number, exclude_id=patient.id):
            raise HTTPException(409, "已有有效档案使用此身份证号，请先搜索现有受检者")
        values = body.model_dump(exclude={"version"} | omitted_new)
        values.update(version=body.version + 1, updated_at=utcnow())
        claim = db.execute(update(Patient).where(Patient.id == patient_id, Patient.deleted_at.is_(None), Patient.version == body.version).values(**values).execution_options(synchronize_session=False))
        if claim.rowcount != 1:
            raise HTTPException(409, "档案已被其他人修改，请读取最新资料后核对")
        db.refresh(patient)
        audit(db, user, "patient.update", "patient", patient.id, changed_fields=changed, version=patient.version)
        db.commit()
    except IntegrityError:
        db.rollback()
        if conflicting_identity(db, body.identity_number, exclude_id=patient_id):
            raise HTTPException(409, "已有有效档案使用此身份证号，请先搜索现有受检者")
        raise
    return patient_record_json(patient)


@router.get("/{patient_id}/exams")
def patient_exams(patient_id: int, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), user=Depends(roles("admin", "registrar")), db=Depends(get_db)):
    active_patient(db, patient_id)
    statement = select(Exam).where(Exam.patient_id == patient_id, Exam.deleted_at.is_(None))
    total = db.scalar(select(func.count()).select_from(statement.subquery()))
    rows = db.scalars(statement.options(selectinload(Exam.items)).order_by(Exam.id.desc()).offset((page - 1) * page_size).limit(page_size))
    return {"items": [exam_json(row, user, detail=False) for row in rows], "total": total, "page": page, "page_size": page_size}


@router.post("/{patient_id}/archive")
def archive_patient(patient_id: int, body: PatientArchive, user=Depends(roles("admin")), db=Depends(get_db)):
    patient = lock_active_patient(db, patient_id)
    if body.version is not None and patient.version != body.version:
        raise HTTPException(409, "档案已被其他人修改，请读取最新资料后核对")
    if db.scalar(select(Exam.id).where(Exam.patient_id == patient_id, Exam.deleted_at.is_(None)).limit(1)):
        raise HTTPException(409, "受检者已有有效体检，不能归档")
    now = utcnow()
    patient.deleted_at = now
    patient.updated_at = now
    patient.deleted_by = user.id
    patient.version += 1
    # Preserve the reason in the protected audit table, never in runtime logs.
    audit(db, user, "patient.archive", "patient", patient.id, reason=body.reason, version=patient.version)
    db.commit()
    return {"message": "受检者已归档，数据仍保留"}
