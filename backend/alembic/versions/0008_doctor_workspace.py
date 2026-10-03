"""Department conclusions and committed clinical save receipts.

Revision ID: 0008
Revises: 0007
"""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("exam_departments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("exam_id", sa.Integer(), sa.ForeignKey("exams.id"), nullable=False),
        sa.Column("department_id", sa.Integer(), sa.ForeignKey("departments.id"), nullable=False),
        sa.Column("department_name", sa.String(100), nullable=False),
        sa.Column("conclusion", sa.Text(), nullable=True), sa.Column("note", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.UniqueConstraint("exam_id", "department_id"))
    op.create_index("ix_exam_departments_exam_id", "exam_departments", ["exam_id"])
    op.create_index("ix_exam_departments_department_id", "exam_departments", ["department_id"])
    op.execute("INSERT INTO exam_departments (exam_id, department_id, department_name, version) SELECT exam_id, department_id, MIN(department_name), 1 FROM exam_items GROUP BY exam_id, department_id")
    op.create_table("department_revisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("exam_department_id", sa.Integer(), sa.ForeignKey("exam_departments.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("conclusion", sa.Text(), nullable=False), sa.Column("note", sa.Text(), nullable=True),
        sa.Column("changed_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("changed_by_name", sa.String(100), nullable=False),
        sa.Column("changed_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("exam_department_id", "version"))
    op.create_index("ix_department_revisions_exam_department_id", "department_revisions", ["exam_department_id"])
    op.create_table("clinical_requests",
        sa.Column("request_id", sa.String(100), primary_key=True),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("action", sa.String(30), nullable=False), sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("response_snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False))


def downgrade():
    op.drop_table("clinical_requests")
    op.drop_table("department_revisions")
    op.drop_table("exam_departments")
