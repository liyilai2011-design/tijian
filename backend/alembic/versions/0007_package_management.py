"""Versioned package templates and registration-time version snapshots.

Revision ID: 0007
Revises: 0006
"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("packages", sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")))
    op.add_column("exams", sa.Column("package_version", sa.Integer(), nullable=True))
    op.create_table("package_requests",
        sa.Column("request_id", sa.String(100), primary_key=True),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("package_id", sa.Integer(), sa.ForeignKey("packages.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False))


def downgrade():
    op.drop_table("package_requests")
    op.drop_column("exams", "package_version")
    op.drop_column("packages", "version")
