from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )
    data_dir: Path = PROJECT_ROOT / "data"
    reports_dir: Path | None = None
    database_url: str = ""
    cookie_secure: bool = False
    allowed_origins: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000,http://127.0.0.1:8000"
    session_hours: int = 8
    session_cookie: str = "healthcheck_session"
    exam_number_mode: str = "date_sequence"
    exam_number_prefix: str = ""
    exam_number_date_format: str = "%Y%m%d"
    exam_number_width: int = 4
    exam_number_separator: str = "-"

    @field_validator("exam_number_mode")
    @classmethod
    def valid_number_mode(cls, value):
        if value not in {"date_sequence", "random"}:
            raise ValueError("EXAM_NUMBER_MODE must be date_sequence or random")
        return value

    @field_validator("exam_number_date_format")
    @classmethod
    def valid_number_date_format(cls, value):
        if value not in {"%Y%m%d", "%Y-%m-%d", "%y%m%d"}:
            raise ValueError("Unsupported EXAM_NUMBER_DATE_FORMAT")
        return value

    @field_validator("exam_number_width")
    @classmethod
    def valid_number_width(cls, value):
        if not 3 <= value <= 8:
            raise ValueError("EXAM_NUMBER_WIDTH must be 3 to 8")
        return value

    @field_validator("exam_number_prefix", "exam_number_separator")
    @classmethod
    def safe_number_text(cls, value):
        import re
        if len(value) > 10 or not re.fullmatch(r"[A-Za-z0-9_-]*", value):
            raise ValueError("Exam number prefix/separator accepts up to 10 ASCII letters, digits, - and _")
        return value

    @field_validator("data_dir", mode="before")
    @classmethod
    def default_data_dir(cls, value):
        return PROJECT_ROOT / "data" if value in {None, ""} else value

    @field_validator("reports_dir", mode="before")
    @classmethod
    def default_reports_dir(cls, value):
        return None if value in {None, ""} else value

    @field_validator("session_hours")
    @classmethod
    def valid_session_hours(cls, value):
        if not 1 <= value <= 24:
            raise ValueError("SESSION_HOURS must be between 1 and 24")
        return value

    @field_validator("data_dir", "reports_dir")
    @classmethod
    def rooted_path(cls, value):
        if value is not None and not value.is_absolute():
            return PROJECT_ROOT / value
        return value

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            url = self.database_url
            if url.startswith("postgres://"):
                return "postgresql+psycopg://" + url[len("postgres://"):]
            if url.startswith("postgresql://"):
                return "postgresql+psycopg://" + url[len("postgresql://"):]
            if url.startswith("sqlite:///") and not url.endswith(":memory:"):
                from sqlalchemy.engine import make_url
                parsed = make_url(url)
                if parsed.database and not Path(parsed.database).is_absolute():
                    return str(parsed.set(database=(PROJECT_ROOT / parsed.database).as_posix()))
            return url
        return "sqlite:///" + (self.data_dir.resolve() / "healthcheck.db").as_posix()

    @property
    def origins(self) -> set[str]:
        import json
        value = self.allowed_origins.strip()
        origins = json.loads(value) if value.startswith("[") else value.split(",")
        return {str(origin).strip().rstrip("/") for origin in origins if str(origin).strip()}


def get_settings() -> Settings:
    return Settings()
