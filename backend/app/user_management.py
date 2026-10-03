"""Administrator account maintenance, serialized within the existing database."""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError

from .models import Department, LoginSession, User
from .schemas import UserCreate, UserUpdate
from .security import get_db, hash_password, lock_and_recheck, roles
from .services import audit

router = APIRouter(prefix="/api/users")


def user_summary(user):
    return {"id": user.id, "username": user.username, "display_name": user.display_name, "role": user.role, "department_id": user.department_id, "is_active": user.is_active}


def editable_snapshot(user):
    return {field: getattr(user, field) for field in ("display_name", "role", "department_id", "is_active")}


def lock_and_recheck_admin(request, db):
    # User APIs never delete rows. The smallest existing id is a stable shared
    # anchor, so two administrators cannot revoke each other in parallel.
    return lock_and_recheck(request, db, "admin")


def validate_department(db, role, department_id, existing=None):
    if role == "doctor":
        department = db.get(Department, department_id) if department_id is not None else None
        if department is None:
            raise HTTPException(422, "医生必须分配有效科室")
        if not department.is_active and not (existing is not None and existing.role == "doctor" and existing.department_id == department_id):
            raise HTTPException(422, "不能分配已停用的科室")
    elif department_id is not None:
        raise HTTPException(422, "只有科室医生可以分配科室")


@router.get("")
def list_users(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), q: str = Query("", max_length=100), actor=Depends(roles("admin")), db=Depends(get_db)):
    statement = select(User)
    if q.strip():
        statement = statement.where(or_(User.username.contains(q.strip(), autoescape=True), User.display_name.contains(q.strip(), autoescape=True)))
    total = db.scalar(select(func.count()).select_from(statement.subquery()))
    users = db.scalars(statement.order_by(User.id).offset((page - 1) * page_size).limit(page_size))
    return {"items": [user_summary(user) for user in users], "total": total, "page": page, "page_size": page_size}


@router.post("", status_code=201)
def create_user(body: UserCreate, request: Request, actor=Depends(roles("admin")), db=Depends(get_db)):
    # Calculate the hash before the shared write lock, then re-check the actor.
    password_hash = hash_password(body.password)
    actor = lock_and_recheck_admin(request, db)
    validate_department(db, body.role, body.department_id)
    if db.scalar(select(User.id).where(User.username == body.username)) is not None:
        raise HTTPException(409, "账号名已存在")
    user = User(username=body.username, display_name=body.display_name, password_hash=password_hash, role=body.role, department_id=body.department_id, is_active=True)
    try:
        db.add(user)
        db.flush()
        audit(db, actor, "user.create", "user", user.id, role=user.role, department_id=user.department_id)
        db.commit()
    except IntegrityError:
        db.rollback()
        # Translate only a real duplicate; an audit/other database failure is 503.
        if db.scalar(select(User.id).where(User.username == body.username)) is not None:
            raise HTTPException(409, "账号名已存在")
        raise
    return user_summary(user)


@router.get("/{user_id}")
def get_user(user_id: int, actor=Depends(roles("admin")), db=Depends(get_db)):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "账号不存在")
    return user_summary(user)


@router.put("/{user_id}")
def update_user(user_id: int, body: UserUpdate, request: Request, actor=Depends(roles("admin")), db=Depends(get_db)):
    actor = lock_and_recheck_admin(request, db)
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "账号不存在")
    before = editable_snapshot(user)
    if before != body.expected.model_dump():
        raise HTTPException(409, "账号已被其他人修改，请刷新后重试")
    validate_department(db, body.role, body.department_id, existing=user)
    if user.id == actor.id and (body.role != "admin" or not body.is_active):
        raise HTTPException(409, "不能停用本人账号或降低本人的管理员权限")
    after = body.model_dump(exclude={"expected"})
    permission_changed = any(before[field] != after[field] for field in ("role", "department_id", "is_active"))
    if user.role == "admin" and user.is_active and (body.role != "admin" or not body.is_active):
        active_admins = db.scalar(select(func.count()).select_from(User).where(User.role == "admin", User.is_active.is_(True)))
        if active_admins <= 1:
            raise HTTPException(409, "必须保留至少一个启用的管理员账号")
    for field, value in after.items():
        setattr(user, field, value)
    if permission_changed:
        db.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
    audit(db, actor, "user.update", "user", user.id, before=before, after=after, sessions_revoked=permission_changed)
    db.commit()
    return user_summary(user)
