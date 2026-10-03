"""Prevent duplicate patient registration after a lost response.

Revision ID: 0002
Revises: 0eadd00bbe3b
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0eadd00bbe3b"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("patients", sa.Column("registration_request_id", sa.String(100), nullable=True))
    if op.get_context().dialect.name == "sqlite":
        # SQLite permits a nullable REFERENCES column in ADD COLUMN. This preserves
        # the original table and its existing exam foreign keys during an upgrade.
        op.execute("ALTER TABLE patients ADD COLUMN registered_by INTEGER REFERENCES users(id)")
    else:
        op.add_column("patients", sa.Column("registered_by", sa.Integer(), nullable=True))
        op.create_foreign_key("fk_patients_registered_by_users", "patients", "users", ["registered_by"], ["id"])
    op.create_index("ix_patients_registration_request_id", "patients", ["registration_request_id"], unique=True)


def downgrade():
    op.drop_index("ix_patients_registration_request_id", table_name="patients")
    if op.get_context().dialect.name != "sqlite":
        op.drop_constraint("fk_patients_registered_by_users", "patients", type_="foreignkey")
    # Downgrade is a manual maintenance operation; preserve existing table data.
    op.drop_column("patients", "registered_by")
    op.drop_column("patients", "registration_request_id")
