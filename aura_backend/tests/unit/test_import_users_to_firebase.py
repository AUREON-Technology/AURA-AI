"""SaaS P1-B: existing users move into Firebase Auth under the subject their data is stored under.

The import is what lets Google, Apple and email/password sign-ins all land on one account: an
existing person's Firebase uid *is* their current ``users.auth_subject``. These tests cover the
pure planning and record-building; the CLI's ``--apply`` calls ``firebase_admin.auth.import_users``
with exactly these records.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "import_users_to_firebase.py"


@pytest.fixture(scope="module")
def script():
    spec = importlib.util.spec_from_file_location("import_users_to_firebase", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve their module through sys.modules
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(spec.name, None)


def _row(subject: str, email: str, name: str = "") -> dict:
    return {"auth_subject": subject, "email": email, "display_name": name}


def test_existing_firebase_users_skipped(script) -> None:
    plan = script.plan_import(
        [_row("111111111111111111111", "a@example.com"), _row("222222222222222222222", "b@example.com")],
        existing=lambda uid: uid == "111111111111111111111",
    )
    assert [u["auth_subject"] for u in plan.to_create] == ["222222222222222222222"]
    assert plan.skipped == [("111111111111111111111", "exists")]


def test_rerun_is_a_no_op(script) -> None:
    rows = [_row("111111111111111111111", "a@example.com"), _row("222222222222222222222", "b@example.com")]
    plan = script.plan_import(rows, existing=lambda uid: True)
    assert plan.to_create == [] and {reason for _, reason in plan.skipped} == {"exists"}
    assert script.firebase_records(plan) == []


def test_placeholder_and_duplicate_emails(script) -> None:
    plan = script.plan_import(
        [
            _row("111111111111111111111", "ghost-1@unknown.local"),
            _row("222222222222222222222", "same@example.com"),
            _row("333333333333333333333", "Same@Example.com"),
        ],
        existing=lambda uid: False,
    )
    created = {u["auth_subject"]: u for u in plan.to_create}
    assert set(created) == {"111111111111111111111", "222222222222222222222", "333333333333333333333"}
    assert created["111111111111111111111"]["email"] is None
    assert created["222222222222222222222"]["email"] == "same@example.com"
    assert created["333333333333333333333"]["email"] is None  # never merged into the first
    assert ("111111111111111111111", "placeholder-email") in plan.skipped
    assert ("333333333333333333333", "duplicate-email") in plan.skipped


def test_records_keep_subject_as_uid_with_google_provider(script) -> None:
    plan = script.plan_import([_row("111111111111111111111", "A@Example.com", "Ann")], existing=lambda uid: False)
    (record,) = script.firebase_records(plan)
    assert record.uid == "111111111111111111111"
    assert record.email == "a@example.com" and record.email_verified is True
    assert record.display_name == "Ann"
    (provider,) = record.provider_data
    assert provider.uid == "111111111111111111111" and provider.provider_id == "google.com"


def test_reserved_and_malformed_subjects_filtered(script) -> None:
    plan = script.plan_import(
        [
            _row("service:flame", "svc@example.com"),
            _row("workspace:" + "a" * 32, "ws@example.com"),
            _row("mock_user_123", "mock@aura.ai"),
            _row("bad subject", "x@example.com"),
            _row("", "y@example.com"),
            _row("111111111111111111111", "ok@example.com"),
        ],
        existing=lambda uid: False,
    )
    assert [u["auth_subject"] for u in plan.to_create] == ["111111111111111111111"]
    reasons = dict(plan.skipped)
    assert reasons["service:flame"] == "not-a-person" and reasons["bad subject"] == "not-a-person"
    assert reasons["mock_user_123"] == "not-a-person"


def test_batches_of_at_most_1000(script) -> None:
    rows = [_row(f"{i:021d}", f"u{i}@example.com") for i in range(2500)]
    plan = script.plan_import(rows, existing=lambda uid: False)
    batches = list(script.batches(script.firebase_records(plan)))
    assert [len(b) for b in batches] == [1000, 1000, 500]
