"""Add exam registration date and note without rebuilding clinical parent rows.

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("exams", sa.Column("exam_date", sa.Date(), nullable=True))
    op.add_column("exams", sa.Column("note", sa.Text(), nullable=True))
    if op.get_context().dialect.name == "sqlite":
        op.execute("UPDATE exams SET exam_date = date(created_at, '+8 hours') WHERE exam_date IS NULL")
    else:
        op.execute("UPDATE exams SET exam_date = CAST(created_at + INTERVAL '8 hours' AS DATE) WHERE exam_date IS NULL")


def downgrade():
    op.drop_column("exams", "note")
    op.drop_column("exams", "exam_date")
