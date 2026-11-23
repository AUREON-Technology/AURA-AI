"""The usage dashboard API and agent-run search/timeline (ADR-041, roadmap rows 72 and 71).

Driven through the real ASGI app and the real kernel on the in-memory substrate: a turn is sent
to ``/chat``, the kernel writes its usage record, and ``/usage/*`` reads it back. Nothing here
mocks the code under test.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from aura_core.api import Principal, build_app  # noqa: E402
from aura_core.config import load_settings  # noqa: E402
from aura_core.container import Container  # noqa: E402
from aura_core.domain.agent_runs import (  # noqa: E402
    AgentPhase, AgentRun, AgentRunStatus, AgentStep, AgentStepKind,
)
from aura_core.domain.errors import AuthenticationError  # noqa: E402
from aura_core.domain.identifiers import AgentRunId, AgentStepId, UserId  # noqa: E402
from aura_core.domain.telemetry import TurnOutcome, TurnTelemetry  # noqa: E402
from aura_core.domain.identifiers import CorrelationId, TurnId  # noqa: E402
from aura_core.history.usage import summarize_usage  # noqa: E402
from aura_core.llm.providers.echo import ScriptedTurn  # noqa: E402

ALICE, BOB = "alice", "bob"
AUTH_A = {"Authorization": f"Bearer {ALICE}"}
AUTH_B = {"Authorization": f"Bearer {BOB}"}
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


def _app(container):
    async def authenticator(request):
        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer ") or header[7:] not in (ALICE, BOB):
            raise AuthenticationError("unknown principal")
        return Principal(user_id=UserId(header[7:]), email=f"{header[7:]}@example.com")

    return build_app(container, authenticator=authenticator)


def _priced(container) -> None:
    """Give the echo provider a price, so a turn has a real cost to total (echo.py invites this)."""
    echo = container.router.get("echo")
    echo._capabilities = replace(
        echo._capabilities, cost_per_1k_input_usd=2.0, cost_per_1k_output_usd=4.0
    )


def _telemetry(*, agent="orchestrator", at: datetime, ptok=100, ctok=50, cost=0.01,
               outcome=TurnOutcome.SUCCESS, ms=2000.0) -> TurnTelemetry:
    return TurnTelemetry(
        correlation_id=CorrelationId.new(), turn_id=TurnId.new(), agent=agent, outcome=outcome,
        prompt_tokens=ptok, completion_tokens=ctok, cost_usd=cost, total_ms=ms, started_at=at,
    )


class TestSummaryMath:
    """The pure summary: windows, groups and exact totals."""

    def _records(self):
        return [
            {"ts": (NOW - timedelta(hours=1)).isoformat(), "agent": "orchestrator", "outcome": "success",
             "prompt_tokens": 100, "completion_tokens": 50, "cost_usd": 0.01, "total_ms": 2000, "task_id": None},
            {"ts": (NOW - timedelta(hours=30)).isoformat(), "agent": "engineer", "outcome": "success",
             "prompt_tokens": 300, "completion_tokens": 100, "cost_usd": 0.04, "total_ms": 4000, "task_id": "t1"},
            {"ts": (NOW - timedelta(days=3)).isoformat(), "agent": "engineer", "outcome": "error",
             "prompt_tokens": 10, "completion_tokens": 0, "cost_usd": 0.001, "total_ms": 500, "task_id": "t1"},
            {"ts": (NOW - timedelta(days=20)).isoformat(), "agent": "orchestrator", "outcome": "success",
             "prompt_tokens": 1000, "completion_tokens": 1000, "cost_usd": 1.0, "total_ms": 9000, "task_id": None},
        ]

    def test_the_period_is_a_trailing_window(self) -> None:
        day = summarize_usage(self._records(), period="day", group_by="agent", now=NOW)
        week = summarize_usage(self._records(), period="week", group_by="agent", now=NOW)
        month = summarize_usage(self._records(), period="month", group_by="agent", now=NOW)
        assert (day["totals"]["turns"], week["totals"]["turns"], month["totals"]["turns"]) == (1, 3, 4)

    def test_totals_are_exact(self) -> None:
        week = summarize_usage(self._records(), period="week", group_by="agent", now=NOW)["totals"]
        assert week["tokens_in"] == 410 and week["tokens_out"] == 150 and week["total_tokens"] == 560
        assert week["cost_usd"] == pytest.approx(0.051)
        assert week["elapsed_s"] == pytest.approx(6.5)
        assert week["failed_turns"] == 1

    def test_groups_by_agent_task_and_day(self) -> None:
        by_agent = summarize_usage(self._records(), period="week", group_by="agent", now=NOW)
        assert {g["key"]: g["turns"] for g in by_agent["groups"]} == {"orchestrator": 1, "engineer": 2}
        by_task = summarize_usage(self._records(), period="week", group_by="task", now=NOW)
        assert {g["key"]: g["turns"] for g in by_task["groups"]} == {"(none)": 1, "t1": 2}
        by_day = summarize_usage(self._records(), period="week", group_by="day", now=NOW)
        assert [g["key"] for g in by_day["groups"]] == sorted(g["key"] for g in by_day["groups"])
        assert sum(g["turns"] for g in by_day["groups"]) == 3

    def test_rejects_unknown_period_or_group(self) -> None:
        from aura_core.domain.errors import ValidationError

        with pytest.raises(ValidationError):
            summarize_usage([], period="year", group_by="day", now=NOW)
        with pytest.raises(ValidationError):
            summarize_usage([], period="day", group_by="user", now=NOW)


class TestUsageApi:
    def test_a_chat_turn_shows_up_in_turns_and_the_summary(self, container) -> None:
        _priced(container)
        with TestClient(_app(container)) as client:
            reply = client.post("/api/v3/chat", json={"message": "hello there", "plan": False,
                                                      "use_tools": False}, headers=AUTH_A)
            assert reply.status_code == 200, reply.text
            usage = reply.json()["usage"]
            assert usage["prompt_tokens"] > 0 and usage["cost_usd"] > 0

            turns = client.get("/api/v3/usage/turns?limit=5", headers=AUTH_A).json()
            assert turns["enabled"] is True and len(turns["turns"]) == 1
            [turn] = turns["turns"]
            assert turn["turn_id"] == reply.json()["turn_id"]
            assert turn["usage"]["prompt_tokens"] == usage["prompt_tokens"]
            assert turn["usage"]["cost_usd"] == pytest.approx(usage["cost_usd"])
            assert turn["outcome"] == "success" and turn["task_id"] is None

            summary = client.get("/api/v3/usage/summary?period=day&group_by=agent", headers=AUTH_A).json()
            assert summary["totals"]["turns"] == 1
            assert summary["totals"]["tokens_in"] == usage["prompt_tokens"]
            assert summary["totals"]["tokens_out"] == usage["completion_tokens"]
            assert summary["totals"]["cost_usd"] == pytest.approx(usage["cost_usd"])
            assert summary["groups"][0]["key"] == "orchestrator"
            assert summary["truncated"] is False

    def test_the_stream_records_a_turn_too(self, container) -> None:
        with TestClient(_app(container)) as client:
            with client.stream("POST", "/api/v3/chat/stream", headers=AUTH_A,
                               json={"message": "stream me", "plan": False, "use_tools": False}) as r:
                body = "".join(r.iter_text())
            assert "event: done" in body
            turns = client.get("/api/v3/usage/turns", headers=AUTH_A).json()["turns"]
        assert len(turns) == 1 and turns[0]["usage"]["prompt_tokens"] > 0

    def test_a_person_only_ever_sees_their_own_usage(self, container) -> None:
        with TestClient(_app(container)) as client:
            client.post("/api/v3/chat", json={"message": "alice question", "plan": False,
                                              "use_tools": False}, headers=AUTH_A)
            assert client.get("/api/v3/usage/turns", headers=AUTH_B).json()["turns"] == []
            bob = client.get("/api/v3/usage/summary?period=month", headers=AUTH_B).json()
            assert bob["totals"]["turns"] == 0
            assert len(client.get("/api/v3/usage/turns", headers=AUTH_A).json()["turns"]) == 1

    def test_requires_authentication_and_validates_parameters(self, container) -> None:
        with TestClient(_app(container)) as client:
            assert client.get("/api/v3/usage/summary").status_code == 401
            assert client.get("/api/v3/usage/summary?period=year", headers=AUTH_A).status_code == 422
            assert client.get("/api/v3/usage/summary?group_by=user", headers=AUTH_A).status_code == 422
            assert client.get("/api/v3/usage/turns?limit=0", headers=AUTH_A).status_code == 422

    def test_a_failed_turn_that_spent_nothing_is_not_kept(self, container) -> None:
        ledger = container.usage_ledger
        asyncio.run(ledger.record("alice", _telemetry(at=NOW, ptok=0, ctok=0, cost=0.0,
                                                      outcome=TurnOutcome.RATE_LIMITED)))
        asyncio.run(ledger.record("alice", _telemetry(at=NOW, ptok=5, ctok=0, cost=0.0,
                                                      outcome=TurnOutcome.ERROR)))
        kept = asyncio.run(ledger.turns("alice"))
        assert len(kept) == 1 and kept[0]["outcome"] == "error"

    def test_the_log_is_bounded(self) -> None:
        container = Container(load_settings(refresh=True, validate=False))
        small = type(container.usage_ledger)(container.cache_backend, max_per_user=3)

        async def go():
            for i in range(7):
                await small.record("alice", _telemetry(at=NOW + timedelta(minutes=i), ptok=i + 1))
            return await small.turns("alice", limit=50)

        kept = asyncio.run(go())
        assert [t["prompt_tokens"] for t in kept] == [7, 6, 5]

    def test_truncation_is_reported_when_the_window_is_full(self) -> None:
        container = Container(load_settings(refresh=True, validate=False))
        small = type(container.usage_ledger)(container.cache_backend, max_per_user=2, clock=None)

        async def go():
            now = datetime.now(timezone.utc)
            for i in range(3):
                await small.record("u", _telemetry(at=now - timedelta(minutes=i)))
            return await small.summary("u", period="week", group_by="day")

        body = asyncio.run(go())
        assert body["truncated"] is True and body["totals"]["turns"] == 2


class TestOffSwitch:
    def test_off_records_nothing_and_says_so(self, monkeypatch) -> None:
        monkeypatch.setenv("AURA_USAGE_LEDGER_ENABLED", "false")
        container = Container(load_settings(refresh=True, validate=False))
        with TestClient(_app(container)) as client:
            client.post("/api/v3/chat", json={"message": "hi", "plan": False, "use_tools": False},
                        headers=AUTH_A)
            body = client.get("/api/v3/usage/summary", headers=AUTH_A).json()
        assert body["enabled"] is False and body["totals"] is None
        monkeypatch.delenv("AURA_USAGE_LEDGER_ENABLED")
        load_settings(refresh=True, validate=False)


def _run(user: str, *, agent="engineer", status=AgentRunStatus.COMPLETED, at: datetime,
         tools=("git_status",), tool_calls=2, plan_steps=3, elapsed=4.0) -> AgentRun:
    return AgentRun(
        id=AgentRunId.new(), user_id=UserId(user), agent_id=agent, turn_id="t", correlation_id="c",
        status=status, created_at=at, updated_at=at, tool_calls=tool_calls, tool_calls_ok=tool_calls,
        plan_steps_total=plan_steps, elapsed_s=elapsed, iterations=1,
    )


def _seed(container, run: AgentRun, tools=()) -> None:
    async def go():
        await container.agent_runs.save_run(run)
        for i, tool in enumerate(tools, start=1):
            await container.agent_runs.append_step(AgentStep(
                id=AgentStepId.new(), run_id=run.id, user_id=run.user_id, seq=i, iteration=1,
                phase=AgentPhase.EXECUTE, kind=AgentStepKind.TOOL_CALL, created_at=run.created_at,
                status="succeeded", tool=tool,
            ))
    asyncio.run(go())


class TestRunSearch:
    def test_filters_and_tool_name_search_without_goal_text(self, container) -> None:
        t = datetime.now(timezone.utc)
        a = _run("alice", at=t - timedelta(hours=1))
        b = _run("alice", agent="orchestrator", status=AgentRunStatus.FAILED, at=t - timedelta(hours=2))
        _seed(container, a, tools=("git_status", "run_command"))
        _seed(container, b, tools=("search_knowledge",))
        _seed(container, _run("bob", at=t), tools=("git_status",))
        with TestClient(_app(container)) as client:
            hit = client.get("/api/v3/agents/runs/search?q=run_command", headers=AUTH_A).json()
            assert [r["id"] for r in hit["runs"]] == [a.id.value]
            assert hit["runs"][0]["tools"] == ["git_status", "run_command"]
            assert hit["runs"][0]["matched_on"] == ["tools"]

            by_status = client.get("/api/v3/agents/runs/search?status=failed", headers=AUTH_A).json()
            assert [r["id"] for r in by_status["runs"]] == [b.id.value]
            by_agent = client.get("/api/v3/agents/runs/search?agent=orchestrator", headers=AUTH_A).json()
            assert [r["id"] for r in by_agent["runs"]] == [b.id.value]
            both = client.get("/api/v3/agents/runs/search?q=git_status failed", headers=AUTH_A).json()
            assert both["total_matches"] == 0

            future = (t + timedelta(days=1)).date().isoformat()
            assert client.get(f"/api/v3/agents/runs/search?since={future}", headers=AUTH_A
                              ).json()["total_matches"] == 0
            today = t.date().isoformat()
            assert client.get(f"/api/v3/agents/runs/search?since={today}&until={today}", headers=AUTH_A
                              ).json()["total_matches"] == 2

    def test_search_says_goal_text_is_not_searchable_when_previews_are_off(self, container) -> None:
        with TestClient(_app(container)) as client:
            body = client.get("/api/v3/agents/runs/search?q=anything", headers=AUTH_A).json()
        scope = body["search_scope"]
        assert scope["goal_text_searchable"] is False
        assert "goal_preview" not in scope["fields"]
        assert "AURA_AGENT_RUNS_STORE_PREVIEW" in scope["note"]

    def test_never_returns_another_users_runs(self, container) -> None:
        _seed(container, _run("alice", at=datetime.now(timezone.utc)), tools=("git_status",))
        with TestClient(_app(container)) as client:
            assert client.get("/api/v3/agents/runs/search?q=git_status", headers=AUTH_B
                              ).json()["total_matches"] == 0
            assert client.get("/api/v3/agents/runs/timeline", headers=AUTH_B).json()["days"] == []

    def test_the_search_route_is_not_shadowed_by_the_run_id_route(self, container) -> None:
        with TestClient(_app(container)) as client:
            assert client.get("/api/v3/agents/runs/search", headers=AUTH_A).status_code == 200
            assert client.get("/api/v3/agents/runs/timeline", headers=AUTH_A).status_code == 200
            assert client.get("/api/v3/agents/runs/not-a-run", headers=AUTH_A).status_code == 404

    def test_bad_dates_are_a_422(self, container) -> None:
        with TestClient(_app(container)) as client:
            assert client.get("/api/v3/agents/runs/search?since=yesterday", headers=AUTH_A).status_code == 422
            assert client.get("/api/v3/agents/runs/timeline?until=soon", headers=AUTH_A).status_code == 422


class TestGoalPreviewOptIn:
    @staticmethod
    def _container(monkeypatch, on: bool):
        monkeypatch.setenv("AURA_AGENT_RUNS_STORE_PREVIEW", "true" if on else "false")
        container = Container(load_settings(refresh=True, validate=False))
        monkeypatch.delenv("AURA_AGENT_RUNS_STORE_PREVIEW")
        return container

    @staticmethod
    def _planned_turn(client, message: str) -> str:
        import json

        container = client.app.state.container
        container.router.get("echo").queue(
            ScriptedTurn(text=json.dumps({"steps": [{
                "id": "s1", "kind": "tool", "tool": "search_knowledge",
                "arguments": {"query": "x"}, "description": "look it up"}]})),
            ScriptedTurn(text="All done."),
        )
        reply = client.post("/api/v3/chat", json={"message": message, "plan": True}, headers=AUTH_A)
        assert reply.status_code == 200, reply.text
        return reply.json()["agent_run_id"]

    def test_on_a_real_turn_keeps_a_redacted_200_char_preview_and_search_finds_it(
        self, monkeypatch
    ) -> None:
        container = self._container(monkeypatch, True)
        app = _app(container)
        app.state.container = container
        secret = "sk-" + "a1b2c3d4e5" * 4
        long_goal = f"Please refactor the invoice exporter. Token {secret} must never be kept. " + "x" * 400
        with TestClient(app) as client:
            run_id = self._planned_turn(client, long_goal)
            assert run_id
            hit = client.get("/api/v3/agents/runs/search?q=invoice exporter", headers=AUTH_A).json()
            assert [r["id"] for r in hit["runs"]] == [run_id]
            preview = hit["runs"][0]["goal_preview"]
            assert preview.startswith("Please refactor the invoice exporter")
            assert len(preview) <= 200
            assert secret not in preview
            assert hit["search_scope"]["goal_text_searchable"] is True
            assert hit["runs"][0]["matched_on"] == ["goal_preview"]
            # Other users cannot find it, and the run row itself still holds only a digest.
            assert client.get("/api/v3/agents/runs/search?q=invoice", headers=AUTH_B).json()["total_matches"] == 0
            run = client.get(f"/api/v3/agents/runs/{run_id}", headers=AUTH_A).json()
            assert "invoice" not in str(run) and run["goal_digest"]
            tl = client.get("/api/v3/agents/runs/timeline", headers=AUTH_A).json()
            assert tl["days"][0]["items"][0]["goal_preview"] == preview

    def test_off_by_default_stores_no_goal_text_at_all(self, monkeypatch) -> None:
        container = self._container(monkeypatch, False)
        app = _app(container)
        app.state.container = container
        with TestClient(app) as client:
            run_id = self._planned_turn(client, "Please refactor the invoice exporter")
            miss = client.get("/api/v3/agents/runs/search?q=invoice", headers=AUTH_A).json()
            assert miss["total_matches"] == 0
            assert miss["search_scope"]["goal_text_searchable"] is False
            # ...but the run is there and findable by what it did.
            assert client.get("/api/v3/agents/runs/search?status=completed", headers=AUTH_A
                              ).json()["runs"][0]["id"] == run_id
        cache = container.cache_backend
        assert not [k for k in getattr(cache, "_data", {}) if k.startswith("aura:runprev:")]


class TestTimeline:
    def test_groups_by_day_with_step_counts_and_durations(self, container) -> None:
        t = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
        _seed(container, _run("alice", at=t, plan_steps=3, tool_calls=2, elapsed=4.0))
        _seed(container, _run("alice", at=t - timedelta(minutes=5), status=AgentRunStatus.FAILED,
                              plan_steps=2, tool_calls=1, elapsed=1.5))
        _seed(container, _run("alice", at=t - timedelta(days=2), plan_steps=5, tool_calls=4, elapsed=2.0))
        with TestClient(_app(container)) as client:
            tl = client.get("/api/v3/agents/runs/timeline", headers=AUTH_A).json()
        days = {d["date"]: d for d in tl["days"]}
        today = t.date().isoformat()
        assert [d["date"] for d in tl["days"]] == sorted(days, reverse=True)
        assert days[today]["runs"] == 2
        assert days[today]["by_status"] == {"completed": 1, "failed": 1}
        assert days[today]["plan_steps"] == 5 and days[today]["tool_calls"] == 3
        assert days[today]["elapsed_s"] == pytest.approx(5.5)
        assert days[(t - timedelta(days=2)).date().isoformat()]["plan_steps"] == 5
        assert tl["totals"]["runs"] == 3 and tl["totals"]["tool_calls"] == 7
        assert tl["search_scope"]["goal_text_searchable"] is False
