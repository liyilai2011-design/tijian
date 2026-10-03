import base64
from copy import deepcopy
import logging
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import delete, exists, func, or_, select, text, update
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from sqlalchemy.orm import selectinload

from .config import PROJECT_ROOT, Settings, get_settings
from .database import Base, create_database
from .models import AuditLog, CatalogItem, Department, Exam, ExamItem, LoginSession, Package, Patient, ReportSnapshot, ResultRevision, User, utcnow
from .schemas import ExamCreate, Finalize, ItemUpdate, Login, Reopen
from .security import LoginThrottle, SUPPORTED_ROLES, current_user, get_db, lock_and_recheck, new_session, roles, token_hash, user_json, verify_password
from .services import audit, basic_exam_patient, create_exam, exam_json, exam_unique_collision, existing_exam_request, finalize_exam, get_exam, iso, item_json, reopen_exam, update_result
from .user_management import router as user_router
from .patient_management import router as patient_router
from .registration import router as registration_router
from .foundation_management import router as foundation_router
from .package_management import router as package_router

logger = logging.getLogger("healthcheck")
# A valid-cost dummy verification prevents revealing whether a username exists by timing.
DUMMY_HASH = "scrypt$131072$8$1$" + base64.b64encode(b"\x00" * 16).decode() + "$" + base64.b64encode(b"\x00" * 32).decode()


def create_app(settings: Settings | None = None, database_url: str | None = None) -> FastAPI:
    settings = settings or get_settings()
    if database_url:
        settings = settings.model_copy(update={"database_url": database_url})
    application = FastAPI(title="体检中心", docs_url=None, redoc_url=None, openapi_url=None)
    application.state.settings = settings
    application.state.engine, application.state.Session = create_database(settings.resolved_database_url)
    application.state.Base = Base
    application.state.login_throttle = LoginThrottle()
    application.add_middleware(CORSMiddleware, allow_origins=sorted(settings.origins), allow_credentials=True, allow_methods=["GET", "POST", "PUT"], allow_headers=["Content-Type", "X-CSRF-Token"])

    @application.middleware("http")
    async def request_safety(request: Request, call_next):
        if request.url.path.startswith("/api") and request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            referer = request.headers.get("referer")
            if not origin and referer:
                parsed = urlsplit(referer)
                origin = f"{parsed.scheme}://{parsed.netloc}"
            if origin and origin.rstrip("/") not in settings.origins:
                return JSONResponse({"detail": "请求来源不被允许"}, status_code=403)
            if request.headers.get("sec-fetch-site") == "cross-site" and not origin:
                return JSONResponse({"detail": "请求来源不被允许"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @application.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse({"detail": "输入内容不符合要求，请检查必填字段、长度、日期和分页参数"}, status_code=422)

    @application.exception_handler(SQLAlchemyError)
    async def database_error(request, exc):
        # Never log exception text: driver errors may contain SQL and patient parameters.
        logger.error("Database operation failed (%s)", type(exc).__name__)
        if request.url.path == "/api/ready":
            return JSONResponse({"detail": "数据库尚未就绪，请检查数据库服务和初始化状态"}, status_code=503)
        return JSONResponse({"detail": "数据尚未保存，请检查网络连接后重新尝试。服务器数据库暂不可用"}, status_code=503)

    @application.get("/api/health")
    def health():
        # Liveness works before database initialization and during database outages.
        return {"status": "ok"}

    @application.get("/api/ready")
    def ready(db=Depends(get_db)):
        db.execute(text("SELECT 1"))
        # Checking a real table detects a server whose migrations have not run.
        db.execute(select(User.id).limit(1))
        return {"status": "ok"}

    @application.post("/api/auth/login")
    def login(body: Login, request: Request, response: Response, db=Depends(get_db)):
        request.app.state.login_throttle.check(request.client.host if request.client else "unknown", body.username)
        user = db.scalar(select(User).where(User.username == body.username))
        verified_hash = user.password_hash if user else DUMMY_HASH
        valid = verify_password(body.password, verified_hash)
        accepted = False
        if user and valid and user.is_active and user.role in SUPPORTED_ROLES:
            # A successful hash check may overlap a password reset. This conditional
            # no-change write locks the user until session/audit commit on SQLite and
            # PostgreSQL, and rejects a hash or active flag changed during verification.
            claim = db.execute(update(User).where(User.id == user.id, User.password_hash == verified_hash, User.is_active.is_(True), User.role.in_(SUPPORTED_ROLES)).values(password_hash=User.password_hash).execution_options(synchronize_session=False))
            accepted = claim.rowcount == 1
        if not accepted:
            audit(db, user, "auth.login_failed", "user", user.id if user else "unknown")
            db.commit()
            raise HTTPException(401, "用户名或密码不正确")
        db.refresh(user)
        old_token = request.cookies.get(settings.session_cookie)
        if old_token and len(old_token) <= 128:
            db.execute(delete(LoginSession).where(LoginSession.token_hash == token_hash(old_token)))
        db.execute(delete(LoginSession).where(LoginSession.expires_at < utcnow()))
        token, record = new_session(user, settings.session_hours)
        db.add(record)
        audit(db, user, "auth.login", "user", user.id)
        db.commit()
        response.set_cookie(settings.session_cookie, token, max_age=settings.session_hours * 3600, httponly=True, secure=settings.cookie_secure, samesite="lax", path="/api")
        return {"user": user_json(user), "csrf_token": record.csrf_token}

    @application.get("/api/auth/me")
    def me(request: Request, user=Depends(current_user)):
        return {"user": user_json(user), "csrf_token": request.state.login_session.csrf_token}

    @application.post("/api/auth/logout")
    def logout(request: Request, response: Response, user=Depends(current_user), db=Depends(get_db)):
        db.delete(request.state.login_session)
        audit(db, user, "auth.logout", "user", user.id)
        db.commit()
        response.delete_cookie(settings.session_cookie, path="/api", httponly=True, secure=settings.cookie_secure, samesite="lax")
        return {"message": "已退出登录"}

    @application.get("/api/departments")
    def departments(user=Depends(current_user), db=Depends(get_db)):
        return [{"id": entry.id, "name": entry.name, "is_active": entry.is_active} for entry in db.scalars(select(Department).order_by(Department.id))]

    @application.get("/api/packages")
    def packages(user=Depends(current_user), db=Depends(get_db)):
        statement = select(Package).where(Package.is_active.is_(True)).options(selectinload(Package.items).selectinload(CatalogItem.department)).order_by(Package.id)
        return [{"id": entry.id, "name": entry.name, "description": entry.description, "items": [{"id": item.id, "name": item.name, "department_id": item.department_id, "department_name": item.department.name, "category": item.category, "unit": item.unit, "reference_range": item.reference_range, "result_type": item.result_type, "abnormal_allowed": item.abnormal_allowed} for item in entry.items]} for entry in db.scalars(statement) if entry.items and all(item.is_active and item.department.is_active for item in entry.items)]

    @application.post("/api/exams")
    def add_exam(body: ExamCreate, request: Request, user=Depends(roles("admin", "registrar")), db=Depends(get_db)):
        user = lock_and_recheck(request, db, "admin", "registrar")
        try:
            exam, created = create_exam(db, body, user, settings)
            db.commit()
        except IntegrityError as error:
            db.rollback()
            # Recover only a committed identical request, never a generic write failure.
            if not exam_unique_collision(error, "request_id"):
                raise
            exam = existing_exam_request(db, body, user)
            if exam is None:
                raise
        return exam_json(exam, user)

    @application.get("/api/exams")
    def exams(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), q: str = Query("", max_length=100), status: str = Query("", pattern="^(registered|in_progress|ready_for_review|completed)?$"), department_id: int | None = Query(None, ge=1), user=Depends(current_user), db=Depends(get_db)):
        statement = select(Exam).join(Patient).where(Exam.deleted_at.is_(None), Patient.deleted_at.is_(None))
        if q.strip():
            statement = statement.where(or_(Patient.name.contains(q.strip(), autoescape=True), Exam.exam_number.contains(q.strip(), autoescape=True)))
        if status:
            statement = statement.where(Exam.status == status)
        if user.role == "doctor":
            if not user.department_id:
                raise HTTPException(403, "医生账号尚未分配科室")
            condition = [ExamItem.exam_id == Exam.id, ExamItem.department_id == user.department_id]
            if not status:
                condition.append(ExamItem.status == "pending")
            statement = statement.where(exists().where(*condition))
        elif department_id:
            statement = statement.where(exists().where(ExamItem.exam_id == Exam.id, ExamItem.department_id == department_id))
        total = db.scalar(select(func.count()).select_from(statement.subquery()))
        rows = db.scalars(statement.options(selectinload(Exam.items)).order_by(Exam.id.desc()).offset((page - 1) * page_size).limit(page_size))
        return {"items": [exam_json(entry, user, detail=False) for entry in rows], "total": total, "page": page, "page_size": page_size}

    @application.get("/api/exams/{exam_id}")
    def exam_detail(exam_id: int, user=Depends(current_user), db=Depends(get_db)):
        return exam_json(get_exam(db, exam_id, user), user)

    @application.put("/api/exam-items/{item_id}")
    def result_update(item_id: int, body: ItemUpdate, user=Depends(roles("admin", "doctor")), db=Depends(get_db)):
        item = update_result(db, item_id, body, user)
        db.commit()
        return item_json(item)

    @application.post("/api/exams/{exam_id}/finalize")
    def finalize(exam_id: int, body: Finalize, user=Depends(roles("admin", "reviewer")), db=Depends(get_db)):
        exam = finalize_exam(db, exam_id, body, user)
        db.commit()
        return exam_json(exam, user)

    @application.post("/api/exams/{exam_id}/reopen")
    def reopen(exam_id: int, body: Reopen, user=Depends(roles("admin", "reviewer")), db=Depends(get_db)):
        exam = reopen_exam(db, exam_id, body, user)
        db.commit()
        return exam_json(exam, user)

    @application.get("/api/exams/{exam_id}/report")
    def report(exam_id: int, user=Depends(roles("admin", "reviewer")), db=Depends(get_db)):
        exam = get_exam(db, exam_id, user)
        if exam.status != "completed":
            raise HTTPException(409, "体检尚未总检或已重新打开，暂无有效的当前报告")
        snapshot = db.scalar(select(ReportSnapshot).where(ReportSnapshot.exam_id == exam.id, ReportSnapshot.revision == exam.latest_report_revision))
        if snapshot is None:
            raise HTTPException(404, "报告不存在")
        audit(db, user, "report.view", "exam", exam.id, revision=snapshot.revision)
        db.commit()
        # Filter only the response copy; legacy immutable stored reports stay intact.
        payload = deepcopy(snapshot.payload)
        payload["patient"] = basic_exam_patient(payload.get("patient") or {})
        return payload

    @application.get("/api/exam-items/{item_id}/history")
    def history(item_id: int, user=Depends(roles("admin", "reviewer", "doctor")), db=Depends(get_db)):
        item = db.get(ExamItem, item_id)
        if item is None:
            raise HTTPException(404, "检查项目不存在")
        get_exam(db, item.exam_id, user)
        if user.role == "doctor" and item.department_id != user.department_id:
            raise HTTPException(403, "只能查看本科室结果历史")
        rows = db.scalars(select(ResultRevision).where(ResultRevision.item_id == item_id).order_by(ResultRevision.version.desc()))
        audit(db, user, "result.history_view", "exam_item", item_id)
        result = [{"id": row.id, "version": row.version, "value": row.value, "is_abnormal": row.is_abnormal, "note": row.note, "changed_by": row.changed_by_name, "changed_at": iso(row.changed_at)} for row in rows]
        db.commit()
        return result

    application.include_router(user_router)
    application.include_router(patient_router)
    application.include_router(registration_router)
    application.include_router(foundation_router)
    application.include_router(package_router)

    @application.api_route("/api/{unknown:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
    def missing_api(unknown: str):
        raise HTTPException(404, "接口不存在")

    dist = PROJECT_ROOT / "frontend" / "dist"
    if (dist / "assets").is_dir():
        application.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @application.get("/{path:path}")
    def frontend(path: str):
        if not (dist / "index.html").is_file():
            raise HTTPException(404, "前端尚未构建，请先运行前端构建命令")
        candidate = (dist / path).resolve()
        try:
            candidate.relative_to(dist.resolve())
        except ValueError:
            raise HTTPException(404, "页面不存在")
        if path.startswith("api"):
            raise HTTPException(404, "接口不存在")
        if candidate.is_file():
            return FileResponse(candidate)
        if "." in Path(path).name:
            raise HTTPException(404, "文件不存在")
        return FileResponse(dist / "index.html", headers={"Cache-Control": "no-cache"})

    return application


app = create_app()
