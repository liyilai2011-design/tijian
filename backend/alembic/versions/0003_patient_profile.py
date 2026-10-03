"""Add patient profile data, edit versions and active identity uniqueness.

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("patients", sa.Column("identity_number", sa.String(32), nullable=True))
    op.add_column("patients", sa.Column("employer", sa.String(200), nullable=True))
    op.add_column("patients", sa.Column("note", sa.Text(), nullable=True))
    op.add_column("patients", sa.Column("updated_at", sa.DateTime(), nullable=True))
    op.add_column("patients", sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")))
    op.execute("UPDATE patients SET updated_at = created_at WHERE updated_at IS NULL")
    active = sa.text("deleted_at IS NULL AND identity_number IS NOT NULL")
    op.create_index("uq_patients_active_identity_number", "patients", ["identity_number"], unique=True, sqlite_where=active, postgresql_where=active)


def downgrade():
    op.drop_index("uq_patients_active_identity_number", table_name="patients")
    for column in ("version", "updated_at", "note", "employer", "identity_number"):
        op.drop_column("patients", column)
