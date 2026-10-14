"""SQLAlchemy models for the HR service."""

import enum
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class LeaveType(enum.Enum):
    ANNUAL = "annual"
    SICK = "sick"
    UNPAID = "unpaid"
    PARENTAL = "parental"


class Department(Base):
    """Organisational units."""

    __tablename__ = "departments"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text, unique=True)
    cost_center: Mapped[Optional[str]] = mapped_column(Text)


class Employee(Base):
    """People employed by the company."""

    __tablename__ = "employees"

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_code: Mapped[str] = mapped_column(Text, unique=True)
    full_name: Mapped[str] = mapped_column(Text)
    email: Mapped[str] = mapped_column(Text, unique=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"))
    manager_id: Mapped[Optional[int]] = mapped_column(ForeignKey("employees.id"))
    hired_on: Mapped[date]
    status: Mapped[str] = mapped_column(Text)
    salary: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))

    __table_args__ = (
        CheckConstraint("status IN ('active', 'on_leave', 'terminated')", name="employees_status_check"),
    )


class LeaveRequest(Base):
    __tablename__ = "leave_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"))
    leave_type: Mapped[LeaveType] = mapped_column(Enum(LeaveType, name="leave_type"))
    starts_on: Mapped[date]
    ends_on: Mapped[date]
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    approved_by: Mapped[Optional[int]] = mapped_column(ForeignKey("employees.id"))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default="now()")

    __table_args__ = (
        CheckConstraint("status IN ('pending', 'approved', 'rejected', 'cancelled')",
                        name="leave_requests_status_check"),
    )


class Attendance(Base):
    __tablename__ = "attendance"

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"))
    work_date: Mapped[date]
    check_in: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    check_out: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    __table_args__ = (UniqueConstraint("employee_id", "work_date", name="attendance_one_per_day"),)


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    tag: Mapped[str] = mapped_column(String(32), unique=True)
    kind: Mapped[str] = mapped_column(Text)
    assigned_to: Mapped[Optional[int]] = mapped_column(ForeignKey("employees.id"))
