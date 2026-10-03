"""Department/catalog maintenance and immutable item configuration snapshots.

Revision ID: 0006
Revises: 0005
"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("departments", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("departments", sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")))
    op.add_column("catalog_items", sa.Column("category", sa.String(100), nullable=False, server_default=""))
    op.add_column("catalog_items", sa.Column("result_type", sa.String(20), nullable=False, server_default="text"))
    op.add_column("catalog_items", sa.Column("abnormal_allowed", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("catalog_items", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("catalog_items", sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")))
    op.add_column("exam_items", sa.Column("category", sa.String(100), nullable=False, server_default=""))
    op.add_column("exam_items", sa.Column("result_type", sa.String(20), nullable=False, server_default="text"))
    op.add_column("exam_items", sa.Column("abnormal_allowed", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.create_table("foundation_requests",
        sa.Column("request_id", sa.String(100), primary_key=True),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("department_id", sa.Integer(), sa.ForeignKey("departments.id"), nullable=True),
        sa.Column("catalog_item_id", sa.Integer(), sa.ForeignKey("catalog_items.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("(department_id IS NOT NULL AND catalog_item_id IS NULL) OR (department_id IS NULL AND catalog_item_id IS NOT NULL)", name="ck_foundation_request_one_target"))


def downgrade():
    op.drop_table("foundation_requests")
    for field in ("abnormal_allowed", "result_type", "category"):
        op.drop_column("exam_items", field)
    for field in ("version", "is_active", "abnormal_allowed", "result_type", "category"):
        op.drop_column("catalog_items", field)
    op.drop_column("departments", "version")
    op.drop_column("departments", "is_active")
