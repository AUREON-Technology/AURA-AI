"""employees.salary

Revision ID: a7f3c2
"""
import sqlalchemy as sa
from alembic import op

revision = "a7f3c2"
down_revision = "0001"


def upgrade():
    op.add_column("employees", sa.Column("salary", sa.Numeric(10, 2)))


def downgrade():
    op.drop_column("employees", "salary")
