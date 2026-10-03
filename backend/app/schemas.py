from datetime import date
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import local_today


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Login(Input):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)

    @field_validator("password", mode="before")
    @classmethod
    def exact_password(cls, value):
        # Password whitespace is significant; unlike text fields it is not trimmed.
        return value

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class PatientFields(Input):
    name: str = Field(min_length=1, max_length=100)
    sex: Literal["male", "female", "other"]
    birth_date: date | None = None
    phone: str | None = Field(default=None, max_length=32)
    identity_number: str | None = Field(default=None, max_length=32)
    employer: str | None = Field(default=None, max_length=200)
    ethnicity: str | None = Field(default=None, max_length=50)
    address: str | None = Field(default=None, max_length=500)
    note: str | None = Field(default=None, max_length=2000)

    @field_validator("birth_date", "phone", "identity_number", "employer", "ethnicity", "address", "note", mode="before")
    @classmethod
    def normalize_optional(cls, value):
        if isinstance(value, str):
            return value.strip() or None
        return value

    @field_validator("birth_date")
    @classmethod
    def valid_birth_date(cls, value):
        if value is not None and (value > local_today() or value.year < 1900):
            raise ValueError("出生日期应在1900年与今天之间")
        return value

    @field_validator("identity_number")
    @classmethod
    def normalize_identity_number(cls, value):
        if value is None:
            return None
        value = value.upper()
        if not re.fullmatch(r"(?:[0-9]{15}|[0-9]{17}[0-9X])", value):
            raise ValueError("身份证号应为15位数字或18位数字（末位可为X）")
        return value


class PatientCreate(PatientFields):
    request_id: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")


class PatientUpdate(PatientFields):
    version: int = Field(gt=0)


class Reason(Input):
    reason: str = Field(min_length=2, max_length=1000)


class PatientArchive(Reason):
    version: int | None = Field(default=None, gt=0)


class ExamCreate(Input):
    patient_id: int = Field(gt=0)
    package_id: int | None = Field(default=None, gt=0)
    package_version: int | None = Field(default=None, gt=0)
    request_id: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    # None is resolved only for the first creation, so retries after midnight reuse it.
    exam_date: date | None = None
    note: str | None = Field(default=None, max_length=2000)
    exam_type: Literal["个人体检", "单位体检", "入职体检", "其他"] = "个人体检"

    @field_validator("exam_date", "note", mode="before")
    @classmethod
    def normalize_optional(cls, value):
        return value.strip() or None if isinstance(value, str) else value

    @field_validator("exam_date")
    @classmethod
    def valid_exam_date(cls, value):
        if value is not None and value.year < 1900:
            raise ValueError("体检日期不能早于1900年")
        return value

    @model_validator(mode="after")
    def package_pair(self):
        if self.package_version is not None and self.package_id is None:
            raise ValueError("套餐版本必须与套餐编号一起提交")
        return self


class IdentityLookup(Input):
    identity_number: str = Field(min_length=15, max_length=18)

    @field_validator("identity_number")
    @classmethod
    def identity(cls, value):
        return PatientFields.normalize_identity_number(value)


class RegistrationCreate(Input):
    patient: PatientFields
    patient_id: int | None = Field(default=None, gt=0)
    patient_version: int | None = Field(default=None, gt=0)
    exam_date: date | None = None
    exam_type: Literal["个人体检", "单位体检", "入职体检", "其他"] = "个人体检"
    note: str | None = Field(default=None, max_length=2000)
    package_id: int | None = Field(default=None, gt=0)
    package_version: int | None = Field(default=None, gt=0)
    request_id: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")

    @field_validator("exam_date", "note", mode="before")
    @classmethod
    def optional(cls, value):
        return value.strip() or None if isinstance(value, str) else value

    @field_validator("exam_date")
    @classmethod
    def date_range(cls, value):
        return ExamCreate.valid_exam_date(value)

    @model_validator(mode="after")
    def selected_pair(self):
        if (self.patient_id is None) != (self.patient_version is None):
            raise ValueError("已选择人员必须同时提交档案编号和版本")
        if self.package_version is not None and self.package_id is None:
            raise ValueError("套餐版本必须与套餐编号一起提交")
        return self


class ItemUpdate(Input):
    value: str = Field(min_length=1, max_length=5000)
    is_abnormal: bool = False
    note: str | None = Field(default=None, max_length=5000)
    version: int = Field(gt=0)
    request_id: str | None = Field(default=None, min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")


class DepartmentResultUpdate(Input):
    conclusion: str = Field(min_length=1, max_length=5000)
    note: str | None = Field(default=None, max_length=2000)
    version: int = Field(gt=0)
    request_id: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")

    @field_validator("note", mode="before")
    @classmethod
    def optional_note(cls, value):
        return value.strip() or None if isinstance(value, str) else value


class Finalize(Input):
    conclusion: str = Field(min_length=1, max_length=10000)
    version: int = Field(gt=0)


class Reopen(Reason):
    version: int = Field(gt=0)


class UserCreate(BaseModel):
    # Password whitespace is significant; trim only the non-secret text fields.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    display_name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=12, max_length=1024)
    role: Literal["admin", "registrar", "doctor", "reviewer"]
    department_id: int | None = Field(default=None, gt=0)

    @field_validator("username", "display_name", mode="before")
    @classmethod
    def trim_public_text(cls, value):
        return value.strip() if isinstance(value, str) else value


class UserExpected(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    display_name: str = Field(min_length=1, max_length=100)
    # An administrator may repair a legacy invalid role using its exact snapshot.
    role: str = Field(min_length=1, max_length=20)
    department_id: int | None = Field(gt=0)
    is_active: bool = Field(strict=True)


class UserUpdate(Input):
    display_name: str = Field(min_length=1, max_length=100)
    role: Literal["admin", "registrar", "doctor", "reviewer"]
    department_id: int | None = Field(gt=0)
    is_active: bool = Field(strict=True)
    expected: UserExpected


class DepartmentCreate(Input):
    name: str = Field(min_length=1, max_length=100)
    request_id: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")


class DepartmentUpdate(Input):
    name: str = Field(min_length=1, max_length=100)
    is_active: bool = Field(strict=True)
    version: int = Field(gt=0)


class CatalogFields(Input):
    name: str = Field(min_length=1, max_length=100)
    department_id: int = Field(gt=0)
    category: str = Field(default="", max_length=100)
    unit: str = Field(default="", max_length=50)
    reference_range: str = Field(default="", max_length=500)
    result_type: Literal["text", "number", "boolean"] = "text"
    abnormal_allowed: bool = Field(default=True, strict=True)
    is_active: bool = Field(default=True, strict=True)


class CatalogCreate(CatalogFields):
    request_id: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")


class CatalogUpdate(CatalogFields):
    version: int = Field(gt=0)


class PackageFields(Input):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=2000)
    item_ids: list[Annotated[int, Field(gt=0, strict=True)]] = Field(min_length=1, max_length=200)
    is_active: bool = Field(default=True, strict=True)

    @field_validator("item_ids")
    @classmethod
    def sorted_unique_items(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("同一套餐不能重复关联检查项目")
        return sorted(value)


class PackageCreate(PackageFields):
    request_id: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")


class PackageUpdate(PackageFields):
    # Old empty templates remain editable/disableable; new creation still needs
    # at least one item and the service rejects activation of an empty template.
    item_ids: list[Annotated[int, Field(gt=0, strict=True)]] = Field(min_length=0, max_length=200)
    version: int = Field(gt=0)
