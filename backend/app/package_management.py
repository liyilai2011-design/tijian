"""Versioned packages; editing templates never changes a registered visit."""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from .foundation_management import body_fingerprint, catalog_json, exact_unique
from .models import CatalogItem, Department, Package, PackageItem, PackageRequest
from .schemas import PackageCreate, PackageUpdate
from .security import get_db, lock_and_recheck, roles
from .services import audit

router = APIRouter(prefix="/api")


def item_count_expression():
    return select(func.count()).select_from(PackageItem).where(PackageItem.package_id == Package.id).correlate(Package).scalar_subquery()


def bad_items_expression():
    return exists(select(PackageItem.item_id).join(CatalogItem, CatalogItem.id == PackageItem.item_id).join(Department, Department.id == CatalogItem.department_id)
                  .where(PackageItem.package_id == Package.id, or_(CatalogItem.is_active.is_(False), Department.is_active.is_(False))))


def available_packages(statement):
    # Filtering happens before pagination/counting, not after a browser download.
    return statement.where(Package.is_active.is_(True), item_count_expression() > 0, ~bad_items_expression())


def summary(record, item_count=None, bad_items=None):
    if item_count is None:
        item_count = len(record.items)
    if bad_items is None:
        bad_items = any(not item.is_active or not item.department.is_active for item in record.items)
    reason = ("套餐已停用" if not record.is_active else "套餐没有检查项目" if not item_count
              else "包含已停用的检查项目或科室" if bad_items else "")
    return {"id": record.id, "name": record.name, "description": record.description, "is_active": record.is_active,
            "version": record.version, "item_count": item_count, "is_available": not reason, "unavailable_reason": reason}


def detail(record):
    return {**summary(record), "item_ids": [item.id for item in record.items], "items": [catalog_json(item) for item in record.items]}


def load_package(db, package_id):
    record = db.scalar(select(Package).where(Package.id == package_id).options(selectinload(Package.items).selectinload(CatalogItem.department)).execution_options(populate_existing=True))
    if record is None:
        raise HTTPException(404, "套餐不存在")
    return record


def validate_items(db, body, previous=None):
    items = list(db.scalars(select(CatalogItem).where(CatalogItem.id.in_(body.item_ids)).options(selectinload(CatalogItem.department)).order_by(CatalogItem.id).execution_options(populate_existing=True)))
    if len(items) != len(body.item_ids):
        raise HTTPException(422, "部分检查项目不存在，请重新读取后核对")
    old_ids = set() if previous is None else {item.id for item in previous.items}
    activating = previous is not None and not previous.is_active and body.is_active
    if not items and body.is_active and (previous is None or previous.items or activating):
        raise HTTPException(422, "启用的套餐必须包含至少一个有效检查项目")
    for item in items:
        if not item.is_active or not item.department.is_active:
            if item.id not in old_ids or activating:
                raise HTTPException(422, "不能新增关联或启用包含停用项目或科室的套餐")
    return items


def replay(db, body, actor):
    request = db.get(PackageRequest, body.request_id)
    if request is None:
        return None
    if request.actor_id != actor.id or request.fingerprint != body_fingerprint(body, "package"):
        raise HTTPException(409, "该套餐请求已用于其他内容，请核对后重新操作")
    return load_package(db, request.package_id)


def page_result(db, statement, page, page_size):
    total = db.scalar(select(func.count()).select_from(statement.subquery()))
    rows = db.execute(statement.add_columns(item_count_expression().label("item_count"), bad_items_expression().label("bad_items"))
                      .order_by(Package.id).offset((page - 1) * page_size).limit(page_size))
    return {"items": [summary(record, item_count, bad_items) for record, item_count, bad_items in rows], "total": total, "page": page, "page_size": page_size}


def search_query(q):
    statement = select(Package)
    if q.strip():
        query = q.strip()
        statement = statement.where(or_(Package.name.contains(query, autoescape=True), Package.description.contains(query, autoescape=True)))
    return statement


@router.get("/admin/packages")
def list_admin_packages(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), q: str = Query("", max_length=100), is_active: bool | None = None, actor=Depends(roles("admin")), db=Depends(get_db)):
    statement = search_query(q)
    if is_active is not None:
        statement = statement.where(Package.is_active == is_active)
    return page_result(db, statement, page, page_size)


@router.get("/admin/packages/{package_id}")
def get_admin_package(package_id: int, actor=Depends(roles("admin")), db=Depends(get_db)):
    return detail(load_package(db, package_id))


@router.post("/admin/packages")
def create_package(body: PackageCreate, request: Request, actor=Depends(roles("admin")), db=Depends(get_db)):
    actor = lock_and_recheck(request, db, "admin")
    record = replay(db, body, actor)
    if record is not None:
        db.commit()
        return detail(record)
    if db.scalar(select(Package.id).where(Package.name == body.name)) is not None:
        raise HTTPException(409, "套餐名称已存在")
    items = validate_items(db, body)
    record = Package(name=body.name, description=body.description, is_active=body.is_active, version=1, items=items)
    try:
        db.add(record)
        db.flush()
        db.add(PackageRequest(request_id=body.request_id, actor_id=actor.id, fingerprint=body_fingerprint(body, "package"), package_id=record.id))
        audit(db, actor, "package.create", "package", record.id, version=record.version, item_ids=body.item_ids)
        db.commit()
    except IntegrityError as error:
        db.rollback()
        if exact_unique(error, "package_requests", "request_id", "package_requests_pkey"):
            actor = lock_and_recheck(request, db, "admin")
            record = replay(db, body, actor)
            if record is not None:
                db.commit()
                return detail(record)
        if exact_unique(error, "packages", "name", "packages_name_key"):
            raise HTTPException(409, "套餐名称已存在") from None
        raise
    return detail(record)


@router.put("/admin/packages/{package_id}")
def update_package(package_id: int, body: PackageUpdate, request: Request, actor=Depends(roles("admin")), db=Depends(get_db)):
    actor = lock_and_recheck(request, db, "admin")
    record = load_package(db, package_id)
    if record.version != body.version:
        raise HTTPException(409, "套餐已被其他人修改，请重新读取后核对")
    if db.scalar(select(Package.id).where(Package.name == body.name, Package.id != package_id)) is not None:
        raise HTTPException(409, "套餐名称已存在")
    items = validate_items(db, body, previous=record)
    before_ids = [item.id for item in record.items]
    changed = [field for field in ("name", "description", "is_active") if getattr(record, field) != getattr(body, field)]
    if before_ids != body.item_ids:
        changed.append("item_ids")
    if changed:
        record.name, record.description, record.is_active = body.name, body.description, body.is_active
        record.items = items
        record.version += 1
        audit(db, actor, "package.update", "package", record.id, changed_fields=changed, version=record.version, before_item_ids=before_ids, item_ids=body.item_ids)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        if exact_unique(error, "packages", "name", "packages_name_key"):
            raise HTTPException(409, "套餐名称已存在") from None
        raise
    return detail(record)


@router.get("/registration/packages")
def list_registration_packages(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), q: str = Query("", max_length=100), actor=Depends(roles("admin", "registrar")), db=Depends(get_db)):
    return page_result(db, available_packages(search_query(q)), page, page_size)


@router.get("/registration/packages/{package_id}")
def get_registration_package(package_id: int, actor=Depends(roles("admin", "registrar")), db=Depends(get_db)):
    record = load_package(db, package_id)
    if not summary(record)["is_available"]:
        raise HTTPException(422, "套餐已停用或包含不可用项目，请重新选择套餐")
    return detail(record)
