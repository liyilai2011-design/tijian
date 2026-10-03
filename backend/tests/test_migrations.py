"""Upgrade a populated original SQLite schema without rebuilding patient rows."""
from pathlib import Path
from io import StringIO
import sys

from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, inspect, select, text
from sqlalchemy.exc import IntegrityError
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.database import create_database
from app.models import utcnow


def test_patient_registration_migration_preserves_existing_exam_and_report(tmp_path):
    url = "sqlite:///" + (tmp_path / "old_schema.db").as_posix()
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.attributes["database_url"] = url
    command.upgrade(config, "0eadd00bbe3b")
    engine, _ = create_database(url)
    old = MetaData()
    old.reflect(engine)
    now = utcnow()
    with engine.begin() as connection:
        connection.execute(old.tables["users"].insert(), {"id": 1, "username": "migration_check", "display_name": "迁移测试", "password_hash": "invalid-test-marker", "role": "admin", "department_id": None, "is_active": True})
        connection.execute(old.tables["packages"].insert(), {"id": 1, "name": "旧套餐", "description": "迁移验证", "is_active": True})
        connection.execute(old.tables["patients"].insert(), {"id": 1, "name": "旧档案测试", "sex": "other", "birth_date": None, "phone": None, "created_at": now, "deleted_at": None, "deleted_by": None})
        connection.execute(old.tables["exams"].insert(), {"id": 1, "exam_number": "OLD-MIGRATION-001", "request_id": "old-exam-request", "requested_by": 1, "patient_id": 1, "package_id": 1, "patient_snapshot": {"id": 1, "name": "旧档案测试"}, "package_name": "旧套餐", "status": "completed", "version": 1, "conclusion": "旧报告测试", "completed_at": now, "latest_report_revision": 1, "created_at": now, "updated_at": now, "deleted_at": None})
        connection.execute(old.tables["report_snapshots"].insert(), {"id": 1, "exam_id": 1, "revision": 1, "payload": {"conclusion": "旧报告测试"}, "created_at": now})
    command.upgrade(config, "head")
    command.check(config)
    with engine.connect() as connection:
        patient = connection.execute(text("SELECT name, registration_request_id, registered_by FROM patients WHERE id=1")).one()
        assert patient == ("旧档案测试", None, None)
        profile = connection.execute(text("SELECT identity_number, employer, note, created_at, updated_at, version FROM patients WHERE id=1")).one()
        assert profile[:3] == (None, None, None)
        assert profile[3] == profile[4] and profile[5] == 1
        assert connection.execute(text("SELECT patient_id FROM exams WHERE id=1")).scalar_one() == 1
        assert connection.execute(select(old.tables["report_snapshots"].c.payload)).scalar_one() == {"conclusion": "旧报告测试"}
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
        assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
    foreign_keys = inspect(engine).get_foreign_keys("patients")
    assert any(key["constrained_columns"] == ["registered_by"] and key["referred_table"] == "users" for key in foreign_keys)
    indexes = inspect(engine).get_indexes("patients")
    identity_index = next(index for index in indexes if index["name"] == "uq_patients_active_identity_number")
    assert identity_index["unique"] == 1
    assert "deleted_at IS NULL" in str(identity_index["dialect_options"]["sqlite_where"])
    with engine.begin() as connection:
        connection.execute(text("UPDATE patients SET registration_request_id='migration-request-001', registered_by=1 WHERE id=1"))
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(text("UPDATE patients SET registered_by=999 WHERE id=1"))
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO patients(name, sex, created_at, registration_request_id, registered_by) VALUES ('重复请求测试','other',:now,'migration-request-001',1)"), {"now": now.isoformat()})
    engine.dispose()


@pytest.mark.parametrize("url", ["sqlite:///offline_only.db", "postgresql+psycopg://offline:unused@localhost/offline"])
def test_profile_migration_offline_sql_has_portable_active_only_unique_index(url):
    output = StringIO()
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"), output_buffer=output)
    config.attributes["database_url"] = url
    command.upgrade(config, "0002:0003", sql=True)
    generated = output.getvalue()
    assert "ADD COLUMN identity_number VARCHAR(32)" in generated
    assert "ADD COLUMN version INTEGER DEFAULT 1 NOT NULL" in generated
    assert "UPDATE patients SET updated_at = created_at WHERE updated_at IS NULL" in generated
    assert "CREATE UNIQUE INDEX uq_patients_active_identity_number ON patients (identity_number) WHERE deleted_at IS NULL AND identity_number IS NOT NULL" in generated
    assert "DROP TABLE" not in generated and "_alembic_tmp" not in generated
