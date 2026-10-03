"""One patient, independent visits: a single transaction for front-desk registration."""
import hashlib
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from .models import Exam, Patient, RegistrationRequest, local_today, utcnow
from .schemas import ExamCreate, IdentityLookup, RegistrationCreate
from .security import get_db, lock_and_recheck, roles
from .services import PATIENT_FIELDS, audit, create_exam, exam_json, exam_unique_collision, lock_active_patient, patient_record_json

router = APIRouter(prefix="/api")
EXAM_TYPES = ["个人体检", "单位体检", "入职体检", "其他"]


def identity_patient(db, identity):
    if identity is None:
        return None
    patient = db.scalar(select(Patient).where(Patient.identity_number == identity, Patient.deleted_at.is_(None)))
    if patient is None and db.scalar(select(Patient.id).where(Patient.identity_number == identity, Patient.deleted_at.is_not(None)).limit(1)) is not None:
        raise HTTPException(409, "此身份证已有归档档案，请联系管理员核对后再登记")
    return patient


def fingerprint(body):
    # Hash canonical normalized input; never put identity/address in audit or logs.
    excluded = {"request_id"}
    if body.package_version is None:
        excluded.add("package_version")
    encoded = json.dumps(body.model_dump(mode="json", exclude=excluded), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def replay(db, body, actor):
    record = db.get(RegistrationRequest, body.request_id)
    if record is None:
        return None
    if record.actor_id != actor.id or record.fingerprint != fingerprint(body):
        raise HTTPException(409, "该登记请求已用于其他内容，请核对后重新登记")
    patient = db.get(Patient, record.patient_id)
    exam = db.scalar(select(Exam).where(Exam.id == record.exam_id).options(selectinload(Exam.items)))
    if patient is None or patient.deleted_at is not None or exam is None or exam.deleted_at is not None:
        raise HTTPException(409, "该登记已归档或不可使用，请联系管理员核对")
    return patient, exam, record.reused_patient


def lock_actor(request, db):
    # Same non-key anchor as account administration. Non-key writes avoid a
    # PostgreSQL FK KEY SHARE cycle with patient edits and examination creation.
    return lock_and_recheck(request, db, "admin", "registrar")


def register(db, body, actor, settings):
    existing = replay(db, body, actor)
    if existing is not None:
        return existing
    # The older /exams namespace also uses this key. Never adopt its unrelated
    # visit as a successful whole-registration response.
    if db.scalar(select(Exam.id).where(Exam.request_id == body.request_id)) is not None:
        raise HTTPException(409, "该登记请求编号已被使用，请核对后重新登记")
    found = identity_patient(db, body.patient.identity_number)
    if body.patient_id is not None:
        patient = lock_active_patient(db, body.patient_id)
        if patient.version != body.patient_version:
            raise HTTPException(409, "人员档案已更新，请重新读取后核对")
        if patient.identity_number != body.patient.identity_number or (found is not None and found.id != patient.id):
            raise HTTPException(409, "所选人员与身份证号不一致，请重新核对")
    elif found is not None:
        patient = lock_active_patient(db, found.id)
        # A profile change while waiting cannot be silently treated as a match.
        if patient.identity_number != body.patient.identity_number:
            raise HTTPException(409, "人员档案已更新，请重新读取后核对")
    else:
        patient = None
    reused = patient is not None
    if reused:
        if any(getattr(patient, field) != getattr(body.patient, field) for field in ("name", "sex", "birth_date")):
            raise HTTPException(409, "姓名、性别或出生日期与已有档案不一致，请核对已有人员档案")
        # Preserve all previously saved personal information. Profile editing is
        # explicit and versioned in its separate existing endpoint.
    else:
        now = utcnow()
        patient = Patient(**body.patient.model_dump(), registered_by=actor.id, created_at=now, updated_at=now)
        db.add(patient)
        db.flush()
        audit(db, actor, "patient.create", "patient", patient.id, changed_fields=[field for field in PATIENT_FIELDS if getattr(patient, field) is not None], version=patient.version)
    exam_body = ExamCreate(patient_id=patient.id, package_id=body.package_id, package_version=body.package_version, exam_date=body.exam_date, exam_type=body.exam_type, note=body.note, request_id=body.request_id)
    exam, _ = create_exam(db, exam_body, actor, settings)
    db.add(RegistrationRequest(request_id=body.request_id, actor_id=actor.id, fingerprint=fingerprint(body), patient_id=patient.id, exam_id=exam.id, reused_patient=reused))
    audit(db, actor, "registration.create", "exam", exam.id, reused_patient=reused)
    db.flush()
    return patient, exam, reused


def recoverable_collision(error):
    if exam_unique_collision(error, "request_id"):
        return True
    original = error.orig
    if getattr(original, "sqlstate", None) == "23505":
        constraint = getattr(getattr(original, "diag", None), "constraint_name", None)
        return constraint in {"uq_patients_active_identity_number", "registration_requests_pkey"}
    return (getattr(original, "sqlite_errorcode", None) in {1555, 2067}
            and str(original) in {"UNIQUE constraint failed: patients.identity_number", "UNIQUE constraint failed: registration_requests.request_id"})


@router.post("/patients/lookup-identity")
def lookup_identity(body: IdentityLookup, actor=Depends(roles("admin", "registrar")), db=Depends(get_db)):
    patient = identity_patient(db, body.identity_number)
    audit(db, actor, "patient.identity_lookup", "patient", patient.id if patient else "not-found")
    db.commit()
    return {"patient": patient_record_json(patient) if patient else None}


@router.get("/registration/options")
def options(actor=Depends(roles("admin", "registrar"))):
    return {"today": local_today().isoformat(), "exam_types": EXAM_TYPES}


@router.post("/registrations")
def create_registration(body: RegistrationCreate, request: Request, actor=Depends(roles("admin", "registrar")), db=Depends(get_db)):
    for attempt in range(2):
        actor = lock_actor(request, db)
        try:
            patient, exam, reused = register(db, body, actor, request.app.state.settings)
            db.commit()
            return {"patient": patient_record_json(patient), "exam": exam_json(exam, actor), "reused_patient": reused}
        except IntegrityError as error:
            db.rollback()
            # Only specific identity/request unique races may be resolved once.
            # Audit, commit and unrelated SQL failures never trigger a new write.
            if not recoverable_collision(error) or attempt == 1:
                raise
