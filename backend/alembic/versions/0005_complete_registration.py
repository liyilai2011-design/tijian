"""Complete person/visit registration; preserve all previous clinical rows.

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def exams_copy_table(package_nullable):
    # Explicit historical schema also supports SQLite offline SQL generation.
    metadata = sa.MetaData()
    table = sa.Table("exams", metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("exam_number", sa.String(40), nullable=False, unique=True),
        sa.Column("request_id", sa.String(100), nullable=False, unique=True),
        sa.Column("requested_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("patient_id", sa.Integer(), sa.ForeignKey("patients.id"), nullable=False),
        sa.Column("package_id", sa.Integer(), sa.ForeignKey("packages.id"), nullable=package_nullable),
        sa.Column("patient_snapshot", sa.JSON(), nullable=False),
        sa.Column("package_name", sa.String(100), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("conclusion", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("latest_report_revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("exam_date", sa.Date(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("exam_type", sa.String(50), nullable=False, server_default="个人体检"),
    )
    for name, columns in (("ix_exams_created_at", ["created_at"]), ("ix_exams_patient_id", ["patient_id"]), ("ix_exams_status", ["status"]), ("ix_exams_status_created", ["status", "created_at"])):
        sa.Index(name, *(table.c[column] for column in columns))
    return table


def upgrade():
    op.add_column("patients", sa.Column("ethnicity", sa.String(50), nullable=True))
    op.add_column("patients", sa.Column("address", sa.String(500), nullable=True))
    op.add_column("exams", sa.Column("exam_type", sa.String(50), nullable=False, server_default="个人体检"))
    if op.get_context().dialect.name == "sqlite":
        with op.batch_alter_table("exams", copy_from=exams_copy_table(False)) as batch:
            batch.alter_column("package_id", existing_type=sa.Integer(), nullable=True)
    else:
        op.alter_column("exams", "package_id", existing_type=sa.Integer(), nullable=True)
    op.create_table("exam_number_counters", sa.Column("registration_date", sa.Date(), primary_key=True), sa.Column("value", sa.Integer(), nullable=False))
    op.create_table("registration_requests",
        sa.Column("request_id", sa.String(100), primary_key=True),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("patient_id", sa.Integer(), sa.ForeignKey("patients.id"), nullable=False),
        sa.Column("exam_id", sa.Integer(), sa.ForeignKey("exams.id"), nullable=False, unique=True),
        sa.Column("reused_patient", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False))


def downgrade():
    # Restoring the former NOT NULL constraint must never discard visits.
    if not op.get_context().as_sql and op.get_bind().execute(sa.text("SELECT id FROM exams WHERE package_id IS NULL LIMIT 1")).first():
        raise RuntimeError("Cannot downgrade while visits without packages exist")
    op.drop_table("registration_requests")
    op.drop_table("exam_number_counters")
    if op.get_context().dialect.name == "sqlite":
        with op.batch_alter_table("exams", copy_from=exams_copy_table(True)) as batch:
            batch.alter_column("package_id", existing_type=sa.Integer(), nullable=False)
            batch.drop_column("exam_type")
    else:
        op.alter_column("exams", "package_id", existing_type=sa.Integer(), nullable=False)
        op.drop_column("exams", "exam_type")
    op.drop_column("patients", "address")
    op.drop_column("patients", "ethnicity")
