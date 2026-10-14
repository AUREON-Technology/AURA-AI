from sqlalchemy import Integer
from sqlalchemy.orm import Mapped, mapped_column


class Scratch:
    """A test-only table: discovery must not report it."""

    __tablename__ = "scratch_should_not_appear"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
