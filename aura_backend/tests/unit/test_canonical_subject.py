"""Review B#4: a Firebase sign-in lands on the account the person's data already lives under.

Before Plan B everyone signed in with Google directly, so their subject is their Google ``sub``.
The import script covers everyone in ``users`` when it runs; this covers everyone after that --
old app versions still create Google-sub accounts, and an operator's console sign-in is still a
direct Google token. A Firebase token whose Google identity matches an existing subject resolves to
that subject, but only when the Firebase uid has no account of its own (never moving someone's
data out from under a uid that already has it).
"""

from __future__ import annotations

import pytest

from services.security import canonical_subject

GOOGLE_SUB = "109876543210987654321"
FIREBASE_UID = "aBcDeFgHiJkLmNoPqRsTuVwXyZ12"


def _decoded(uid=FIREBASE_UID, google=(GOOGLE_SUB,)):
    identities = {"google.com": list(google)} if google else {}
    return {"uid": uid, "firebase": {"identities": identities, "sign_in_provider": "google.com"}}


@pytest.mark.parametrize(
    "existing, expected",
    [
        ({GOOGLE_SUB}, GOOGLE_SUB),            # old Google account, no Firebase-uid account yet
        ({GOOGLE_SUB, FIREBASE_UID}, FIREBASE_UID),  # both exist: never move the uid's own data
        (set(), FIREBASE_UID),                 # brand-new person
        ({FIREBASE_UID}, FIREBASE_UID),
    ],
)
async def test_resolution(existing, expected) -> None:
    async def exists(subject: str) -> bool:
        return subject in existing

    assert await canonical_subject(_decoded(), exists=exists) == expected


async def test_imported_user_is_already_canonical() -> None:
    async def exists(subject: str) -> bool:
        return True

    assert await canonical_subject(_decoded(uid=GOOGLE_SUB), exists=exists) == GOOGLE_SUB


async def test_no_google_identity_keeps_the_uid() -> None:
    async def exists(subject: str) -> bool:
        return True

    assert await canonical_subject(_decoded(google=()), exists=exists) == FIREBASE_UID


async def test_lookup_failure_keeps_the_uid() -> None:
    async def exists(subject: str) -> bool:
        raise RuntimeError("database down")

    assert await canonical_subject(_decoded(), exists=exists) == FIREBASE_UID
