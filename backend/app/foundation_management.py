"""Small administrator-owned department and examination catalog module."""
import hashlib
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from .models import CatalogItem, Department, FoundationRequest, Package, PackageItem
from .schemas import CatalogCreate, CatalogUpdate, DepartmentCreate, DepartmentUpdate
from .security import get_db, lock_and_recheck, roles
from .services import audit

router = APIRouter(prefix="/api/admin")
CATALOG_FIELDS = ("name", "department_id", "category", "unit", "reference_range", "result_type", "abnormal_allowed", "is_active")


def department_json(record):
    return {"id": record.id, "name": record.name, "is_active": record.is_active, "version": record.version}


def catalog_json(record):
    return {"id": record.id, "department_name": record.department.name, "department_is_active": record.department.is_active,
            **{field: getattr(record, field) for field in CATALOG_FIELDS}, "version": record.version}


def body_fingerprint(body, kind):
    encoded = json.dumps({"kind": kind, "body": body.model_dump(mode="json", exclude={"request_id"})}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def replay(db, body, actor, kind):
    record = db.get(FoundationRequest, body.request_id)
    if record is None:
        return None
    target = record.department_id if kind == "department" else record.catalog_item_id
    if target is None or record.actor_id != actor.id or record.fingerprint != body_fingerprint(body, kind):
        raise HTTPException(409, "该基础资料请求已用于其他内容，请核对后重新操作")
    model = Department if kind == "department" else CatalogItem
    existing = db.get(model, target)
    if existing is None:
        raise HTTPException(409, "该基础资料已不可使用，请联系管理员核对")
    return existing


def exact_unique(error, table, column, pg_constraint):
    original = error.orig
    if getattr(original, "sqlstate", None) == "23505":
        return getattr(getattr(original, "diag", None), "constraint_name", None) == pg_constraint
    return (getattr(original, "sqlite_errorcode", None) in {1555, 2067}
            and str(original) == "UNIQUE constraint failed: " + table + "." + column)


def valid_department(db, department_id, previous=None, active=True):
    department = db.get(Department, department_id)
    if department is None:
        raise HTTPException(422, "请选择有效科室")
    keeps_existing = previous is not None and previous.department_id == department_id
    if not department.is_active and (not keeps_existing or (not previous.is_active and active)):
        raise HTTPException(422, "不能新增关联或启用已停用科室的检查项目")
    return department


def bump_related_packages(db, kind, record_id):
    # A package's version covers the effective configuration shown at selection.
    # IN gives each affected package one increment even when several of its items
    # belong to the changed department. The shared anchor is already held.
    related = select(PackageItem.package_id)
    if kind == "department":
        related = related.join(CatalogItem, CatalogItem.id == PackageItem.item_id).where(CatalogItem.department_id == record_id)
    else:
        related = related.where(PackageItem.item_id == record_id)
    result = db.execute(update(Package).where(Package.id.in_(related)).values(version=Package.version + 1).execution_options(synchronize_session=False))
    return result.rowcount


def create_record(db, body, request, kind):
    actor = lock_and_recheck(request, db, "admin")
    existing = replay(db, body, actor, kind)
    if existing is not None:
        db.commit()
        return existing
    if kind == "department":
        if db.scalar(select(Department.id).where(Department.name == body.name)) is not None:
            raise HTTPException(409, "科室名称已存在")
        record = Department(name=body.name, is_active=True, version=1)
    else:
        department = valid_department(db, body.department_id)
        record = CatalogItem(**body.model_dump(exclude={"request_id"}), department=department, version=1)
    try:
        db.add(record)
        db.flush()
        target = {"department_id": record.id} if kind == "department" else {"catalog_item_id": record.id}
        db.add(FoundationRequest(request_id=body.request_id, actor_id=actor.id, fingerprint=body_fingerprint(body, kind), **target))
        audit(db, actor, kind + ".create", kind, record.id, version=record.version)
        db.commit()
    except IntegrityError as error:
        db.rollback()
        if exact_unique(error, "foundation_requests", "request_id", "foundation_requests_pkey"):
            actor = lock_and_recheck(request, db, "admin")
            record = replay(db, body, actor, kind)
            if record is not None:
                db.commit()
                return record
        if kind == "department" and exact_unique(error, "departments", "name", "departments_name_key"):
            raise HTTPException(409, "科室名称已存在") from None
        raise
    return record


@router.get("/departments")
def list_departments(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), q: str = Query("", max_length=100), is_active: bool | None = None, actor=Depends(roles("admin")), db=Depends(get_db)):
    statement = select(Department)
    if q.strip():
        statement = statement.where(Department.name.contains(q.strip(), autoescape=True))
    if is_active is not None:
        statement = statement.where(Department.is_active == is_active)
    total = db.scalar(select(func.count()).select_from(statement.subquery()))
    rows = db.scalars(statement.order_by(Department.id).offset((page - 1) * page_size).limit(page_size))
    return {"items": [department_json(row) for row in rows], "total": total, "page": page, "page_size": page_size}


@router.get("/departments/{department_id}")
def get_department(department_id: int, actor=Depends(roles("admin")), db=Depends(get_db)):
    record = db.get(Department, department_id)
    if record is None:
        raise HTTPException(404, "科室不存在")
    return department_json(record)


@router.post("/departments")
def add_department(body: DepartmentCreate, request: Request, actor=Depends(roles("admin")), db=Depends(get_db)):
    return department_json(create_record(db, body, request, "department"))


@router.put("/departments/{department_id}")
def edit_department(department_id: int, body: DepartmentUpdate, request: Request, actor=Depends(roles("admin")), db=Depends(get_db)):
    actor = lock_and_recheck(request, db, "admin")
    record = db.get(Department, department_id)
    if record is None:
        raise HTTPException(404, "科室不存在")
    if record.version != body.version:
        raise HTTPException(409, "科室已被其他人修改，请重新读取后核对")
    if db.scalar(select(Department.id).where(Department.name == body.name, Department.id != department_id)) is not None:
        raise HTTPException(409, "科室名称已存在")
    changed = [field for field in ("name", "is_active") if getattr(record, field) != getattr(body, field)]
    if changed:
        record.name, record.is_active = body.name, body.is_active
        record.version += 1
        affected = bump_related_packages(db, "department", record.id)
        audit(db, actor, "department.update", "department", record.id, changed_fields=changed, version=record.version, affected_packages=affected)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        if exact_unique(error, "departments", "name", "departments_name_key"):
            raise HTTPException(409, "科室名称已存在") from None
        raise
    return department_json(record)


@router.get("/catalog-items")
def list_catalog(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), q: str = Query("", max_length=100), department_id: int | None = Query(None, ge=1), is_active: bool | None = None, actor=Depends(roles("admin")), db=Depends(get_db)):
    statement = select(CatalogItem).join(Department)
    if q.strip():
        query = q.strip()
        statement = statement.where(or_(CatalogItem.name.contains(query, autoescape=True), CatalogItem.category.contains(query, autoescape=True), Department.name.contains(query, autoescape=True)))
    if department_id is not None:
        statement = statement.where(CatalogItem.department_id == department_id)
    if is_active is not None:
        statement = statement.where(CatalogItem.is_active == is_active)
    total = db.scalar(select(func.count()).select_from(statement.subquery()))
    rows = db.scalars(statement.options(selectinload(CatalogItem.department)).order_by(CatalogItem.id).offset((page - 1) * page_size).limit(page_size))
    return {"items": [catalog_json(row) for row in rows], "total": total, "page": page, "page_size": page_size}


@router.get("/catalog-items/{item_id}")
def get_catalog(item_id: int, actor=Depends(roles("admin")), db=Depends(get_db)):
    record = db.get(CatalogItem, item_id)
    if record is None:
        raise HTTPException(404, "检查项目不存在")
    return catalog_json(record)


@router.post("/catalog-items")
def add_catalog(body: CatalogCreate, request: Request, actor=Depends(roles("admin")), db=Depends(get_db)):
    return catalog_json(create_record(db, body, request, "catalog_item"))


@router.put("/catalog-items/{item_id}")
def edit_catalog(item_id: int, body: CatalogUpdate, request: Request, actor=Depends(roles("admin")), db=Depends(get_db)):
    actor = lock_and_recheck(request, db, "admin")
    record = db.get(CatalogItem, item_id)
    if record is None:
        raise HTTPException(404, "检查项目不存在")
    if record.version != body.version:
        raise HTTPException(409, "检查项目已被其他人修改，请重新读取后核对")
    department = valid_department(db, body.department_id, previous=record, active=body.is_active)
    changed = [field for field in CATALOG_FIELDS if getattr(record, field) != getattr(body, field)]
    if changed:
        for field in CATALOG_FIELDS:
            setattr(record, field, getattr(body, field))
        record.department = department
        record.version += 1
        affected = bump_related_packages(db, "catalog_item", record.id)
        audit(db, actor, "catalog_item.update", "catalog_item", record.id, changed_fields=changed, version=record.version, affected_packages=affected)
    db.commit()
    return catalog_json(record)
