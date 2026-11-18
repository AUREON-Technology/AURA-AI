"""Import existing users into Firebase Auth under the subject their data is stored under (SaaS P1-B).

Before Plan B every person signed in with Google directly, so their ``users.auth_subject`` -- the
key on every row and vector they own -- is their Google ``sub``. Apple and email/password sign-in
only exist through Firebase, which would give the same person a *new* uid and an empty account.

This script prevents that by creating each existing person in Firebase **with uid = their current
subject** and their Google identity attached. With Firebase's "link accounts that use the same
email" setting, any later Google, Apple or email/password sign-in for that address resolves to the
same uid, so no stored data is ever rewritten.

    python scripts/import_users_to_firebase.py                  # dry run: print the plan
    python scripts/import_users_to_firebase.py --apply          # create the Firebase users

Run it once, *before* shipping a client build that signs in through Firebase. It is safe to re-run:
a uid that already exists in Firebase is skipped, never modified. Rolling back means deleting the
created uids (``firebase_admin.auth.delete_users``); nothing in AURA's own database changes.

Database: ``--database-url`` or ``AURA_DATABASE_URL`` (a ``postgresql://`` DSN). Firebase:
``FIREBASE_CREDENTIALS_JSON`` (the service-account JSON) or ``GOOGLE_APPLICATION_CREDENTIALS``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Iterator

# `python scripts/import_users_to_firebase.py` puts scripts/ on sys.path, not the backend root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aura_core.domain.identifiers import is_reserved_principal  # noqa: E402

BATCH = 1000  # firebase_admin.auth.import_users accepts at most 1000 records per call
PLACEHOLDER_EMAIL_SUFFIX = "@unknown.local"  # aura_core/adapters/postgres_app_users.ensure_user_id
_PERSON_SUBJECT = re.compile(r"^[A-Za-z0-9]{6,128}$")


@dataclass
class ImportPlan:
    to_create: list[dict] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)


def plan_import(
    users: Iterable[dict],
    existing: Callable[[str], bool],
    email_taken: Callable[[str], bool] = lambda email: False,
) -> ImportPlan:
    """Decide what to create. ``existing(uid)`` says whether Firebase already has that uid.

    Skip reasons: ``not-a-person`` (a service, project or workspace principal, a mock or malformed
    subject), ``exists`` (already in Firebase; never touched). Two reasons still create the user
    but without an email: ``placeholder-email`` (the ``@unknown.local`` stand-in) and
    ``duplicate-email`` (a second subject with an address already planned -- reported, never merged,
    because merging would join two people's data). ``email-taken`` skips the person entirely:
    Firebase already has *another* account with that address (someone registered it, or signed in
    through the new client first), and ``import_users`` does not check email uniqueness, so
    importing would create a second account for one address. Resolve those by hand.
    """
    plan = ImportPlan()
    claimed: set[str] = set()
    for row in users:
        subject = str(row.get("auth_subject") or "")
        if not subject or is_reserved_principal(subject) or not _PERSON_SUBJECT.match(subject):
            plan.skipped.append((subject, "not-a-person"))
            continue
        if existing(subject):
            plan.skipped.append((subject, "exists"))
            continue
        email = (row.get("email") or "").strip().lower() or None
        if email and not email.endswith(PLACEHOLDER_EMAIL_SUFFIX) and email_taken(email):
            plan.skipped.append((subject, "email-taken"))
            continue
        if email and email.endswith(PLACEHOLDER_EMAIL_SUFFIX):
            plan.skipped.append((subject, "placeholder-email"))
            email = None
        elif email and email in claimed:
            plan.skipped.append((subject, "duplicate-email"))
            email = None
        if email:
            claimed.add(email)
        plan.to_create.append({
            "auth_subject": subject, "email": email, "display_name": (row.get("display_name") or "").strip() or None,
        })
    return plan


def firebase_records(plan: ImportPlan) -> list:
    """One ``ImportUserRecord`` per planned person: uid = subject, Google identity attached."""
    from firebase_admin import auth

    records = []
    for person in plan.to_create:
        subject, email = person["auth_subject"], person["email"]
        records.append(auth.ImportUserRecord(
            uid=subject,
            email=email,
            email_verified=True if email else None,
            display_name=person["display_name"],
            provider_data=[auth.UserProvider(uid=subject, provider_id="google.com", email=email)],
        ))
    return records


def batches(records: list) -> Iterator[list]:
    for start in range(0, len(records), BATCH):
        yield records[start:start + BATCH]


async def _load_users(database_url: str) -> list[dict]:
    import asyncpg

    conn = await asyncpg.connect(database_url)
    try:
        rows = await conn.fetch("SELECT auth_subject, email, display_name FROM users ORDER BY created_at, auth_subject")
    finally:
        await conn.close()
    return [dict(r) for r in rows]


def _firebase_app():
    import firebase_admin
    from firebase_admin import credentials

    raw = os.environ.get("FIREBASE_CREDENTIALS_JSON", "").strip()
    cred = credentials.Certificate(json.loads(raw)) if raw else credentials.ApplicationDefault()
    return firebase_admin.initialize_app(cred, name="aura-user-import")


def _email_taken_in(app) -> Callable[[str], bool]:
    from firebase_admin import auth

    def taken(email: str) -> bool:
        try:
            auth.get_user_by_email(email, app=app)
            return True
        except auth.UserNotFoundError:
            return False

    return taken


def _exists_in(app) -> Callable[[str], bool]:
    from firebase_admin import auth

    def exists(uid: str) -> bool:
        try:
            auth.get_user(uid, app=app)
            return True
        except auth.UserNotFoundError:
            return False

    return exists


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true", help="create the users (default: dry run)")
    parser.add_argument("--database-url", default=os.environ.get("AURA_DATABASE_URL", ""))
    parser.add_argument("--no-firebase-lookup", action="store_true",
                        help="dry run without Firebase credentials: assume no subject exists yet")
    args = parser.parse_args(argv)
    if not args.database_url:
        parser.error("--database-url or AURA_DATABASE_URL is required")
    if args.apply and args.no_firebase_lookup:
        parser.error("--apply needs Firebase; drop --no-firebase-lookup")
    url = args.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)

    users = asyncio.run(_load_users(url))
    app = None if args.no_firebase_lookup else _firebase_app()
    if app is None:
        plan = plan_import(users, existing=lambda uid: False)
    else:
        plan = plan_import(users, existing=_exists_in(app), email_taken=_email_taken_in(app))

    print(f"users in database: {len(users)}")
    print(f"to create in Firebase: {len(plan.to_create)}")
    for subject, reason in plan.skipped:
        print(f"  note {reason}: {subject}")
    if not args.apply:
        print("dry run: nothing was created. Re-run with --apply to import.")
        return 0

    from firebase_admin import auth

    failures = 0
    for number, batch in enumerate(batches(firebase_records(plan)), start=1):
        result = auth.import_users(batch, app=app)
        failures += result.failure_count
        print(f"batch {number}: {result.success_count} created, {result.failure_count} failed")
        for error in result.errors:
            print(f"  failed {batch[error.index].uid}: {error.reason}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
