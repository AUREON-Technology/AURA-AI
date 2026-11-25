"""A placeholder email is replaced by the person's real one the next time they sign in.

``@unknown.local`` rows exist because some paths create the user row before any token with an
email has been seen (``aura_core/adapters/postgres_app_users.ensure_user_id``). The Access page
finds people by exact email, so such a person could never be given a role, even after signing in
with a verified address: ``get_or_create_user`` found the row by subject and never touched its
email. Found live on 2026-11-22: a Google account that had first reached the API through a path
with no email signed in on the web, and "Set to Developer" answered "no user with that email".
"""

from __future__ import annotations

import uuid

import pytest

from infrastructure.db.models import User
from infrastructure.db.user_repo import get_or_create_user

SUBJECT = "113000000000000000001"
PLACEHOLDER = f"{SUBJECT}@unknown.local"
REAL = "person@example.com"


class _Result:
    def __init__(self, rows: list[User]) -> None:
        self._rows = rows

    def scalar_one_or_none(self) -> User | None:
        return self._rows[0] if self._rows else None


class _Db:
    """The two lookups ``get_or_create_user`` makes, answered from a list of rows: by
    ``auth_subject``, and by email (case-insensitively, as the query lowers the column)."""

    def __init__(self, *users: User) -> None:
        self.users = list(users)

    async def execute(self, statement) -> _Result:
        compiled = statement.compile()
        sql, value = str(compiled), next(iter(compiled.params.values()))
        if "users.auth_subject =" in sql:
            rows = [u for u in self.users if u.auth_subject == value]
        elif "lower(users.email)" in sql:
            rows = [u for u in self.users if u.email.lower() == str(value).lower()]
        else:
            raise AssertionError(f"unexpected query: {sql}")
        return _Result(rows)

    def add(self, user: User) -> None:
        self.users.append(user)

    async def flush(self) -> None:
        pass


def _row(subject: str, email: str, role: str = "User") -> User:
    return User(id=uuid.uuid4(), auth_subject=subject, email=email, display_name="", role=role)


def _token(email: str | None = REAL, *, verified: bool = True, uid: str = SUBJECT) -> dict:
    info: dict = {"uid": uid, "name": "Person", "email_verified": verified}
    if email is not None:
        info["email"] = email
    return info


@pytest.mark.asyncio
async def test_placeholder_email_becomes_the_verified_one() -> None:
    row = _row(SUBJECT, PLACEHOLDER)
    user = await get_or_create_user(_Db(row), _token())
    assert user is row
    assert row.email == REAL


@pytest.mark.asyncio
async def test_an_unverified_email_is_not_trusted_to_replace_it() -> None:
    row = _row(SUBJECT, PLACEHOLDER)
    await get_or_create_user(_Db(row), _token(verified=False))
    assert row.email == PLACEHOLDER


@pytest.mark.asyncio
async def test_a_token_without_an_email_leaves_the_placeholder() -> None:
    row = _row(SUBJECT, PLACEHOLDER)
    await get_or_create_user(_Db(row), _token(email=None))
    assert row.email == PLACEHOLDER


@pytest.mark.asyncio
async def test_a_token_that_is_itself_a_placeholder_changes_nothing() -> None:
    row = _row(SUBJECT, PLACEHOLDER)
    await get_or_create_user(_Db(row), _token(email="someone-else@unknown.local"))
    assert row.email == PLACEHOLDER


@pytest.mark.asyncio
async def test_an_email_another_account_has_is_never_taken_over() -> None:
    row = _row(SUBJECT, PLACEHOLDER)
    other = _row("firebase-uid-xyz", "Person@Example.com")
    await get_or_create_user(_Db(row, other), _token())
    assert row.email == PLACEHOLDER
    assert other.email == "Person@Example.com"


@pytest.mark.asyncio
async def test_a_real_email_is_not_overwritten_by_a_different_one() -> None:
    row = _row(SUBJECT, "kept@example.com")
    await get_or_create_user(_Db(row), _token())
    assert row.email == "kept@example.com"


@pytest.mark.asyncio
async def test_the_role_is_never_written() -> None:
    row = _row(SUBJECT, PLACEHOLDER, role="Developer")
    await get_or_create_user(_Db(row), _token())
    assert row.role == "Developer"


@pytest.mark.asyncio
async def test_a_new_subject_still_gets_a_row_with_the_token_email() -> None:
    db = _Db()
    user = await get_or_create_user(db, _token(uid="fresh-uid"))
    assert db.users == [user]
    assert user.email == REAL and user.role == "User"


@pytest.mark.asyncio
async def test_a_new_subject_without_an_email_gets_the_placeholder() -> None:
    db = _Db()
    user = await get_or_create_user(db, _token(email=None, uid="fresh-uid"))
    assert user.email == "fresh-uid@unknown.local"
