"""initial: departments and employees

Revision ID: 0001
"""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None


def upgrade():
    op.create_table(
        "departments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("cost_center", sa.Text()),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "employees",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("employee_code", sa.Text(), nullable=False, unique=True),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("department_id", sa.Integer(), sa.ForeignKey("departments.id"), nullable=False),
        sa.Column("manager_id", sa.Integer(), sa.ForeignKey("employees.id")),
        sa.Column("hired_on", sa.Date(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.UniqueConstraint("email"),
        sa.CheckConstraint("status IN ('active', 'on_leave', 'terminated')", name="employees_status_check"),
    )


def downgrade():
    op.drop_table("employees")
    op.drop_table("departments")
