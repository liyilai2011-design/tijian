from datetime import date, datetime, timedelta, timezone

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def local_today() -> date:
    return datetime.now(timezone(timedelta(hours=8))).date()


class Department(Base):
    __tablename__ = "departments"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(20))
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class LoginSession(Base):
    __tablename__ = "login_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    user: Mapped[User] = relationship()


class Package(Base):
    __tablename__ = "packages"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    items: Mapped[list["CatalogItem"]] = relationship(secondary="package_items", order_by="CatalogItem.id")


class CatalogItem(Base):
    __tablename__ = "catalog_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"))
    unit: Mapped[str] = mapped_column(String(50), default="")
    reference_range: Mapped[str] = mapped_column(String(500), default="")
    category: Mapped[str] = mapped_column(String(100), default="", server_default="")
    result_type: Mapped[str] = mapped_column(String(20), default="text", server_default="text")
    abnormal_allowed: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    department: Mapped[Department] = relationship()


class PackageItem(Base):
    __tablename__ = "package_items"
    package_id: Mapped[int] = mapped_column(ForeignKey("packages.id"), primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("catalog_items.id"), primary_key=True)


class Patient(Base):
    __tablename__ = "patients"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), index=True)
    sex: Mapped[str] = mapped_column(String(10))
    birth_date: Mapped[date | None] = mapped_column(Date)
    phone: Mapped[str | None] = mapped_column(String(32))
    identity_number: Mapped[str | None] = mapped_column(String(32))
    employer: Mapped[str | None] = mapped_column(String(200))
    ethnicity: Mapped[str | None] = mapped_column(String(50))
    address: Mapped[str | None] = mapped_column(String(500))
    note: Mapped[str | None] = mapped_column(Text)
    # Nullable for patients registered before request-id protection was introduced.
    registration_request_id: Mapped[str | None] = mapped_column(String(100), unique=True, index=True)
    registered_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    # Nullable at the DB boundary for old programs; the API falls back to created_at.
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)
    deleted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    __table_args__ = (
        Index("uq_patients_active_identity_number", "identity_number", unique=True,
              sqlite_where=text("deleted_at IS NULL AND identity_number IS NOT NULL"),
              postgresql_where=text("deleted_at IS NULL AND identity_number IS NOT NULL")),
    )


class Exam(Base):
    __tablename__ = "exams"
    id: Mapped[int] = mapped_column(primary_key=True)
    exam_number: Mapped[str] = mapped_column(String(40), unique=True)
    request_id: Mapped[str] = mapped_column(String(100), unique=True)
    requested_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    package_id: Mapped[int | None] = mapped_column(ForeignKey("packages.id"))
    package_version: Mapped[int | None] = mapped_column(Integer)
    patient_snapshot: Mapped[dict] = mapped_column(JSON)
    package_name: Mapped[str] = mapped_column(String(100))
    # Kept nullable at the DB boundary for old binaries; migrated rows are backfilled.
    exam_date: Mapped[date | None] = mapped_column(Date, default=local_today)
    note: Mapped[str | None] = mapped_column(Text)
    exam_type: Mapped[str] = mapped_column(String(50), default="个人体检", server_default="个人体检")
    status: Mapped[str] = mapped_column(String(30), default="registered", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    conclusion: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    latest_report_revision: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)
    patient: Mapped[Patient] = relationship()
    items: Mapped[list["ExamItem"]] = relationship(order_by="ExamItem.id", cascade="all, delete-orphan")
    __table_args__ = (Index("ix_exams_status_created", "status", "created_at"),)


class ExamItem(Base):
    __tablename__ = "exam_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    exam_id: Mapped[int] = mapped_column(ForeignKey("exams.id"), index=True)
    item_name: Mapped[str] = mapped_column(String(100))
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), index=True)
    department_name: Mapped[str] = mapped_column(String(100))
    unit: Mapped[str] = mapped_column(String(50), default="")
    reference_range: Mapped[str] = mapped_column(String(500), default="")
    category: Mapped[str] = mapped_column(String(100), default="", server_default="")
    result_type: Mapped[str] = mapped_column(String(20), default="text", server_default="text")
    abnormal_allowed: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    value: Mapped[str | None] = mapped_column(Text)
    is_abnormal: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    version: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (Index("ix_exam_items_department_status", "department_id", "status"),)


class ResultRevision(Base):
    __tablename__ = "result_revisions"
    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("exam_items.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    value: Mapped[str] = mapped_column(Text)
    is_abnormal: Mapped[bool] = mapped_column(Boolean)
    note: Mapped[str | None] = mapped_column(Text)
    changed_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    changed_by_name: Mapped[str] = mapped_column(String(100))
    changed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    __table_args__ = (UniqueConstraint("item_id", "version"),)


class ReportSnapshot(Base):
    __tablename__ = "report_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    exam_id: Mapped[int] = mapped_column(ForeignKey("exams.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    __table_args__ = (UniqueConstraint("exam_id", "revision"),)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(100), index=True)
    entity_type: Mapped[str] = mapped_column(String(50))
    entity_id: Mapped[str] = mapped_column(String(100))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class ExamNumberCounter(Base):
    __tablename__ = "exam_number_counters"
    registration_date: Mapped[date] = mapped_column(Date, primary_key=True)
    value: Mapped[int] = mapped_column(Integer, nullable=False)


class RegistrationRequest(Base):
    __tablename__ = "registration_requests"
    request_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    fingerprint: Mapped[str] = mapped_column(String(64))
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"))
    exam_id: Mapped[int] = mapped_column(ForeignKey("exams.id"), unique=True)
    reused_patient: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class FoundationRequest(Base):
    __tablename__ = "foundation_requests"
    request_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    fingerprint: Mapped[str] = mapped_column(String(64))
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"))
    catalog_item_id: Mapped[int | None] = mapped_column(ForeignKey("catalog_items.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    __table_args__ = (CheckConstraint("(department_id IS NOT NULL AND catalog_item_id IS NULL) OR (department_id IS NULL AND catalog_item_id IS NOT NULL)", name="ck_foundation_request_one_target"),)


class PackageRequest(Base):
    __tablename__ = "package_requests"
    request_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    fingerprint: Mapped[str] = mapped_column(String(64))
    package_id: Mapped[int] = mapped_column(ForeignKey("packages.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ExamDepartment(Base):
    __tablename__ = "exam_departments"
    id: Mapped[int] = mapped_column(primary_key=True)
    exam_id: Mapped[int] = mapped_column(ForeignKey("exams.id"), index=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), index=True)
    department_name: Mapped[str] = mapped_column(String(100))
    conclusion: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    __table_args__ = (UniqueConstraint("exam_id", "department_id"),)


class DepartmentRevision(Base):
    __tablename__ = "department_revisions"
    id: Mapped[int] = mapped_column(primary_key=True)
    exam_department_id: Mapped[int] = mapped_column(ForeignKey("exam_departments.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    conclusion: Mapped[str] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    changed_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    changed_by_name: Mapped[str] = mapped_column(String(100))
    changed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    __table_args__ = (UniqueConstraint("exam_department_id", "version"),)


class ClinicalRequest(Base):
    __tablename__ = "clinical_requests"
    request_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(30))
    target_id: Mapped[int] = mapped_column(Integer)
    fingerprint: Mapped[str] = mapped_column(String(64))
    response_snapshot: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
