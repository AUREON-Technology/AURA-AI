"""``GET /api/v3/me/usage``, ``/me/usage/history`` and ``/plans``.

Messages and storage come from the plan's meter; tokens and cost are summed from the content-free
turn telemetry under the person's keyed owner reference. The tests assert the numbers a person would
read, derived from fixed inputs (turns recorded on known days), never from the code under test, and
that nobody sees anybody else's.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from aura_core.adapters.runtime import InMemoryTelemetrySink
from aura_core.api.usage import _month_periods
from aura_core.domain.identifiers import CorrelationId, TurnId, UserId
from aura_core.domain.plans import period_for
from aura_core.domain.telemetry import TurnTelemetry
from aura_core.security.owner_ref import owner_ref_maker

pytest.importorskip("fastapi")

SECRET = "usage-test-signing-secret-0123456789abcdef"
NOW = datetime.now(timezone.utc)
TODAY = datetime(NOW.year, NOW.month, NOW.day, 12, tzinfo=timezone.utc)


def _make_ref():
    return owner_ref_maker(lambda: SECRET)


def _turn(user: str, at: datetime, prompt: int = 100, completion: int = 20, cost: float = 0.001, estimated: bool = False) -> TurnTelemetry:
    return TurnTelemetry(
        correlation_id=CorrelationId.new(), turn_id=TurnId.new(), started_at=at, owner_ref=_make_ref()(user),
        prompt_tokens=prompt, completion_tokens=completion, cost_usd=cost, usage_estimated=estimated,
    )


@pytest.fixture
def app_container(container):
    container.owner_ref = _make_ref()
    container.telemetry_sink = InMemoryTelemetrySink()
    return container


def _app(container, role: str = "User"):
    from aura_core.api import Principal, build_app
    from aura_core.domain.errors import AuthenticationError

    async def authenticate(request):
        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer "):
            raise AuthenticationError("missing")
        return Principal(user_id=UserId(header[7:]), email="", role=role)

    return build_app(container, authenticator=authenticate)


def _as(user: str) -> dict:
    return {"Authorization": f"Bearer {user}"}


async def _record(container, *turns: TurnTelemetry) -> None:
    for turn in turns:
        await container.telemetry_sink.record_turn(turn)


# ---------------------------------------------------------------------------------------------
# pure helper
# ---------------------------------------------------------------------------------------------


class TestMonthPeriods:
    def test_it_counts_back_across_a_year_boundary(self) -> None:
        assert _month_periods(datetime(2026, 2, 10, tzinfo=timezone.utc), 4) == ["2026-02", "2026-01", "2025-12", "2025-11"]

    def test_a_single_month_is_the_current_one(self) -> None:
        assert _month_periods(datetime(2026, 12, 31, tzinfo=timezone.utc), 1) == ["2026-12"]

    def test_two_years_back(self) -> None:
        periods = _month_periods(datetime(2026, 1, 1, tzinfo=timezone.utc), 24)
        assert periods[0] == "2026-01" and periods[1] == "2025-12" and periods[-1] == "2024-02" and len(set(periods)) == 24


# ---------------------------------------------------------------------------------------------
# the sink's per-day query
# ---------------------------------------------------------------------------------------------


class TestUsageByDay:
    async def test_it_groups_by_utc_day_newest_first_and_sums(self) -> None:
        sink = InMemoryTelemetrySink()
        ref = _make_ref()("alice")
        day1, day2 = datetime(2026, 11, 20, tzinfo=timezone.utc), datetime(2026, 11, 21, tzinfo=timezone.utc)
        for t in (
            _turn("alice", day1 + timedelta(hours=1), 100, 10, 0.002),
            _turn("alice", day1 + timedelta(hours=23, minutes=59), 50, 5, 0.001, estimated=True),
            _turn("alice", day2 + timedelta(minutes=1), 7, 3, 0.0005),
            _turn("bob", day1 + timedelta(hours=2), 9999, 9999, 9.0),
        ):
            await sink.record_turn(t)

        rows = await sink.usage_by_day(ref, since=day1, until=day2 + timedelta(days=1))

        assert [r["day"] for r in rows] == ["2026-11-21", "2026-11-20"]
        assert rows[1] == {"day": "2026-11-20", "turns": 2, "prompt_tokens": 150, "completion_tokens": 15,
                           "cost_usd": pytest.approx(0.003), "estimated_turns": 1}
        assert (rows[0]["turns"], rows[0]["prompt_tokens"], rows[0]["estimated_turns"]) == (1, 7, 0)

    async def test_the_window_is_half_open_and_an_empty_reference_has_nothing(self) -> None:
        sink = InMemoryTelemetrySink()
        edge = datetime(2026, 11, 20, tzinfo=timezone.utc)
        await sink.record_turn(_turn("alice", edge))
        ref = _make_ref()("alice")
        assert len(await sink.usage_by_day(ref, since=edge, until=edge + timedelta(seconds=1))) == 1, "since is inclusive"
        assert await sink.usage_by_day(ref, since=edge - timedelta(days=1), until=edge) == [], "until is exclusive"
        assert await sink.usage_by_day("", since=edge, until=edge + timedelta(days=1)) == []
        assert await sink.usage_by_day(_make_ref()("bob"), since=edge, until=edge + timedelta(days=1)) == []


# ---------------------------------------------------------------------------------------------
# GET /me/usage
# ---------------------------------------------------------------------------------------------


class TestMyUsage:
    async def test_a_new_person_sees_the_free_plan_and_zeros(self, app_container) -> None:
        from fastapi.testclient import TestClient

        with TestClient(_app(app_container)) as client:
            body = client.get("/api/v3/me/usage", headers=_as("alice")).json()

        assert body["metered"] is True and body["period"] == period_for(NOW)
        assert body["plan"]["id"] == "free" and body["workspace"]["kind"] == "personal"
        assert body["messages"]["used"] == 0 and body["messages"]["allowed"] > 0
        assert body["storage"]["used_bytes"] == 0 and body["storage"]["allowed_bytes"] > 0
        assert body["tokens"] == {"available": True, "since": body["tokens"]["since"], "turns": 0, "prompt_tokens": 0,
                                  "completion_tokens": 0, "total_tokens": 0, "cost_usd": 0, "estimated_turns": 0}
        assert body["subscription"] is None and body["billing_enabled"] is False

    async def test_messages_storage_and_this_months_tokens_are_the_callers_own(self, app_container) -> None:
        from fastapi.testclient import TestClient

        month_start = datetime(NOW.year, NOW.month, 1, 6, tzinfo=timezone.utc)
        last_month = month_start - timedelta(days=3)
        await _record(
            app_container,
            _turn("alice", month_start, 100, 20, 0.001), _turn("alice", month_start + timedelta(hours=1), 50, 10, 0.002, estimated=True),
            _turn("alice", last_month, 7777, 7777, 7.0),          # a past month: not this month's
            _turn("bob", month_start, 9999, 9999, 9.0),           # someone else's
        )
        with TestClient(_app(app_container)) as client:
            workspace = (await app_container.tenancy.ensure_personal("alice")).id
            await app_container.plans.meter.try_consume(workspace, "messages", period_for(NOW), 3, 1000)
            client.post("/api/v3/knowledge/documents", json={"title": "n", "content": "x" * 120}, headers=_as("alice"))
            mine = client.get("/api/v3/me/usage", headers=_as("alice")).json()
            bobs = client.get("/api/v3/me/usage", headers=_as("bob")).json()

        assert mine["messages"]["used"] == 3 and mine["storage"]["used_bytes"] == 120
        tokens = mine["tokens"]
        assert (tokens["turns"], tokens["prompt_tokens"], tokens["completion_tokens"], tokens["total_tokens"]) == (2, 150, 30, 180)
        assert tokens["cost_usd"] == pytest.approx(0.003) and tokens["estimated_turns"] == 1
        assert bobs["messages"]["used"] == 0 and bobs["tokens"]["prompt_tokens"] == 9999

    async def test_operators_are_not_metered_but_their_tokens_are_still_totalled(self, app_container) -> None:
        from fastapi.testclient import TestClient

        await _record(app_container, _turn("ops", datetime(NOW.year, NOW.month, 1, 8, tzinfo=timezone.utc), 40, 4))
        with TestClient(_app(app_container, role="Admin")) as client:
            body = client.get("/api/v3/me/usage", headers=_as("ops")).json()
        assert body["metered"] is False
        assert (body["plan"], body["messages"], body["storage"], body["workspace"]) == (None, None, None, None)
        assert body["tokens"]["total_tokens"] == 44

    async def test_without_a_signing_secret_tokens_are_unavailable_not_zero(self, app_container) -> None:
        from fastapi.testclient import TestClient

        app_container.owner_ref = owner_ref_maker(lambda: "")
        with TestClient(_app(app_container)) as client:
            body = client.get("/api/v3/me/usage", headers=_as("alice")).json()
        assert body["tokens"] == {"available": False}

    async def test_an_active_subscription_is_summarised_when_billing_is_on(self, app_container, monkeypatch) -> None:
        from fastapi.testclient import TestClient

        from aura_core.domain.billing import Subscription, SubscriptionStatus

        monkeypatch.setattr(app_container.billing, "settings", replace(app_container.billing.settings, enabled=True))
        workspace = (await app_container.tenancy.ensure_personal("alice")).id
        ends = NOW + timedelta(days=12)
        await app_container.billing.subscriptions.put(Subscription(
            workspace_id=workspace, plan_id="pro", seats=1, status=SubscriptionStatus.ACTIVE, provider="fawry",
            period_start=NOW - timedelta(days=18), period_end=ends, updated_at=NOW, card_token="tok-secret",
        ))
        with TestClient(_app(app_container)) as client:
            body = client.get("/api/v3/me/usage", headers=_as("alice")).json()
        assert body["billing_enabled"] is True
        assert body["subscription"] == {
            "plan": "pro", "seats": 1, "status": "active", "period_end": ends.isoformat(), "grace_until": None,
        }
        assert "tok-secret" not in str(body), "a card token reached the response"

    async def test_one_persons_subscription_is_not_anothers(self, app_container, monkeypatch) -> None:
        from fastapi.testclient import TestClient

        from aura_core.domain.billing import Subscription, SubscriptionStatus

        monkeypatch.setattr(app_container.billing, "settings", replace(app_container.billing.settings, enabled=True))
        alice_ws = (await app_container.tenancy.ensure_personal("alice")).id
        await app_container.billing.subscriptions.put(Subscription(
            workspace_id=alice_ws, plan_id="pro", seats=1, status=SubscriptionStatus.ACTIVE, provider="fawry",
            period_start=NOW, period_end=NOW + timedelta(days=30), updated_at=NOW,
        ))
        with TestClient(_app(app_container)) as client:
            bob = client.get("/api/v3/me/usage", headers=_as("bob")).json()
        assert bob["subscription"] is None and bob["plan"]["id"] == "free"

    async def test_it_needs_a_signed_in_caller(self, app_container) -> None:
        from fastapi.testclient import TestClient

        with TestClient(_app(app_container)) as client:
            for path in ("/api/v3/me/usage", "/api/v3/me/usage/history", "/api/v3/plans"):
                assert client.get(path).status_code == 401, path


# ---------------------------------------------------------------------------------------------
# GET /me/usage/history
# ---------------------------------------------------------------------------------------------


class TestHistory:
    async def test_messages_per_month_newest_first_with_empty_months_as_zero(self, app_container) -> None:
        from fastapi.testclient import TestClient

        workspace = (await app_container.tenancy.ensure_personal("alice")).id
        periods = _month_periods(NOW, 3)
        await app_container.plans.meter.try_consume(workspace, "messages", periods[0], 5, 1000)
        await app_container.plans.meter.try_consume(workspace, "messages", periods[2], 11, 1000)
        with TestClient(_app(app_container)) as client:
            body = client.get("/api/v3/me/usage/history", params={"months": 3}, headers=_as("alice")).json()
            other = client.get("/api/v3/me/usage/history", params={"months": 3}, headers=_as("bob")).json()

        assert body["messages"] == [
            {"period": periods[0], "messages_used": 5}, {"period": periods[1], "messages_used": 0},
            {"period": periods[2], "messages_used": 11},
        ]
        assert [m["messages_used"] for m in other["messages"]] == [0, 0, 0]

    async def test_tokens_per_day_within_the_window_for_the_caller_only(self, app_container) -> None:
        from fastapi.testclient import TestClient

        await _record(
            app_container,
            _turn("alice", TODAY, 10, 1), _turn("alice", TODAY - timedelta(days=2), 20, 2),
            _turn("alice", TODAY - timedelta(days=40), 30, 3),   # outside a 30-day window
            _turn("bob", TODAY, 9999, 9999),
        )
        with TestClient(_app(app_container)) as client:
            narrow = client.get("/api/v3/me/usage/history", params={"days": 30}, headers=_as("alice")).json()["tokens"]
            wide = client.get("/api/v3/me/usage/history", params={"days": 60}, headers=_as("alice")).json()["tokens"]

        assert [d["day"] for d in narrow["days"]] == [TODAY.date().isoformat(), (TODAY - timedelta(days=2)).date().isoformat()]
        assert narrow["prompt_tokens"] == 30 and narrow["total_tokens"] == 33 and narrow["turns"] == 2
        assert len(wide["days"]) == 3 and wide["prompt_tokens"] == 60, "a wider window must reach the older day"

    async def test_the_days_window_includes_exactly_that_many_days_ending_today(self, app_container) -> None:
        from fastapi.testclient import TestClient

        await _record(
            app_container,
            _turn("alice", TODAY - timedelta(days=6), 1, 0),   # the 7th day back, counting today as the first
            _turn("alice", TODAY - timedelta(days=7), 100, 0), # one day too old for a 7-day window
        )
        with TestClient(_app(app_container)) as client:
            week = client.get("/api/v3/me/usage/history", params={"days": 7}, headers=_as("alice")).json()["tokens"]
        assert [d["prompt_tokens"] for d in week["days"]] == [1] and week["total_tokens"] == 1

    async def test_it_says_how_long_tokens_are_kept_and_when_they_are_unavailable(self, app_container) -> None:
        from fastapi.testclient import TestClient

        with TestClient(_app(app_container)) as client:
            ok = client.get("/api/v3/me/usage/history", headers=_as("alice")).json()["tokens"]
            app_container.owner_ref = owner_ref_maker(lambda: "")
            off = client.get("/api/v3/me/usage/history", headers=_as("alice")).json()["tokens"]
        assert ok["retention_days"] == app_container.settings.observability.telemetry_retention_days and ok["available"] is True
        assert off["available"] is False and off["days"] == [] and "total_tokens" not in off

    async def test_operators_have_no_message_history_but_have_tokens(self, app_container) -> None:
        from fastapi.testclient import TestClient

        await _record(app_container, _turn("ops", TODAY, 5, 5))
        with TestClient(_app(app_container, role="SuperAdmin")) as client:
            body = client.get("/api/v3/me/usage/history", headers=_as("ops")).json()
        assert body["metered"] is False and body["messages"] == [] and body["tokens"]["total_tokens"] == 10

    @pytest.mark.parametrize("params", [{"months": 0}, {"months": 25}, {"days": 0}, {"days": 367}])
    async def test_out_of_range_windows_are_422(self, app_container, params) -> None:
        from fastapi.testclient import TestClient

        with TestClient(_app(app_container)) as client:
            assert client.get("/api/v3/me/usage/history", params=params, headers=_as("alice")).status_code == 422


# ---------------------------------------------------------------------------------------------
# GET /plans
# ---------------------------------------------------------------------------------------------


class TestPlans:
    async def test_every_plan_and_what_it_allows(self, app_container) -> None:
        from fastapi.testclient import TestClient

        with TestClient(_app(app_container)) as client:
            body = client.get("/api/v3/plans", headers=_as("alice")).json()
        by_id = {p["id"]: p for p in body["plans"]}
        assert {"free", "pro", "team"} <= set(by_id)
        for plan in by_id.values():
            assert set(plan) == {"id", "messages_per_month", "messages_pooled_per_seat", "engineer", "storage_bytes",
                                 "min_seats", "max_seats", "workspace_kinds"}
        assert by_id["free"]["engineer"] is False and by_id["pro"]["messages_per_month"] > by_id["free"]["messages_per_month"]
        assert by_id["team"]["workspace_kinds"] == ["team"] and by_id["team"]["messages_pooled_per_seat"] is True
        assert "personal" in by_id["free"]["workspace_kinds"]
