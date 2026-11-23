"""The agent task gateway over HTTP (ADR-041, roadmap rows 84, 87 and 91).

Everything is the real stack on the in-memory substrate: the ASGI app, the same ``to_turn`` /
``admit_turn`` / rate-limit code ``/chat`` uses, the real queue and store, and the real kernel
running each task. The model is the scripted echo provider.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import MappingProxyType

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from aura_core.api import Principal, build_app  # noqa: E402
from aura_core.config import load_settings  # noqa: E402
from aura_core.container import Container  # noqa: E402
from aura_core.domain.errors import AuthenticationError  # noqa: E402
from aura_core.domain.identifiers import UserId  # noqa: E402
from aura_core.domain.plans import DEFAULT_CATALOGUE  # noqa: E402
from aura_core.domain.tools import ParameterSpec, ToolCategory, ToolSensitivity, ToolSpec  # noqa: E402
from aura_core.llm.providers.echo import ScriptedTurn  # noqa: E402
from aura_core.security.rate_limit import RateLimitPolicy, SlidingWindowRateLimiter  # noqa: E402
from aura_core.tools.base import FunctionTool  # noqa: E402

ALICE, BOB, ENG = "alice", "bob", "eng"
AUTH_A = {"Authorization": f"Bearer {ALICE}"}
AUTH_B = {"Authorization": f"Bearer {BOB}"}
AUTH_E = {"Authorization": f"Bearer {ENG}"}
TASKS = "/api/v3/agents/tasks"


def _app(container, *, rate_limiter=None):
    people = {ALICE: (), BOB: (), ENG: ("engineer_agent",)}

    async def authenticator(request):
        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer ") or header[7:] not in people:
            raise AuthenticationError("unknown principal")
        name = header[7:]
        return Principal(user_id=UserId(name), email=f"{name}@example.com", capabilities=people[name],
                         email_verified=True)

    return build_app(container, authenticator=authenticator, rate_limiter=rate_limiter)


def _container(monkeypatch, **env) -> Container:
    for key, value in env.items():
        monkeypatch.setenv(f"AURA_{key}", str(value))
    container = Container(load_settings(refresh=True, validate=False))
    for key in env:
        monkeypatch.delenv(f"AURA_{key}")
    return container


def _wait(client, task_id, headers, want, timeout=15.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = client.get(f"{TASKS}/{task_id}", headers=headers).json()["task"]
        if last["status"] in want:
            return last
        time.sleep(0.03)
    raise AssertionError(f"task never reached {want}: {last}")


def _submit(client, goal="say hello", headers=AUTH_A, **extra):
    return client.post(TASKS, json={"goal": goal, "plan": False, "use_tools": False, **extra},
                       headers=headers)


class TestSubmitPollResult:
    def test_a_task_runs_server_side_and_its_result_is_there_to_poll(self, container) -> None:
        container.router.get("echo").queue(ScriptedTurn(text="The quarterly report is ready."))
        with TestClient(_app(container)) as client:
            created = _submit(client, "write the quarterly report")
            assert created.status_code == 202, created.text
            body = created.json()
            task = body["task"]
            assert task["status"] in ("queued", "running") and task["goal"] == "write the quarterly report"
            assert task["persona"] == "orchestrator" and task["priority"] == "normal"
            assert body["idempotent_replay"] is False and "owner" not in task

            # No connection is held open: the client polls, as a phone would after reconnecting.
            done = _wait(client, task["id"], AUTH_A, {"succeeded", "failed"})
            assert done["status"] == "succeeded", done
            assert done["result"]["message"] == "The quarterly report is ready."
            assert done["result"]["summary"].startswith("The quarterly report")
            assert done["result"]["usage"]["prompt_tokens"] > 0
            assert done["turn_id"] and done["started_at"] and done["finished_at"]
            assert done["error"] is None and done["attempts"] == 1

    def test_a_planned_task_links_its_durable_agent_run(self, container) -> None:
        container.router.get("echo").queue(
            ScriptedTurn(text=json.dumps({"steps": [{
                "id": "s1", "kind": "tool", "tool": "search_knowledge",
                "arguments": {"query": "x"}, "description": "look it up"}]})),
            ScriptedTurn(text="Found it."),
        )
        with TestClient(_app(container)) as client:
            task = client.post(TASKS, json={"goal": "look something up", "plan": True},
                               headers=AUTH_A).json()["task"]
            done = _wait(client, task["id"], AUTH_A, {"succeeded", "failed"})
            assert done["status"] == "succeeded" and done["run_id"]
            run = client.get(f"/api/v3/agents/runs/{done['run_id']}", headers=AUTH_A)
            assert run.status_code == 200 and run.json()["status"] == "completed"

    def test_it_is_the_chat_path_so_spend_lands_in_the_usage_api_grouped_by_task(self, container) -> None:
        container.router.get("echo").queue(ScriptedTurn(text="done"))
        with TestClient(_app(container)) as client:
            task = _submit(client).json()["task"]
            _wait(client, task["id"], AUTH_A, {"succeeded"})
            turns = client.get("/api/v3/usage/turns", headers=AUTH_A).json()["turns"]
            assert [t["task_id"] for t in turns] == [task["id"]]
            by_task = client.get("/api/v3/usage/summary?period=day&group_by=task", headers=AUTH_A).json()
            assert [g["key"] for g in by_task["groups"]] == [task["id"]]
            assert by_task["groups"][0]["turns"] == 1

    def test_the_task_runs_with_the_owners_capabilities_never_more(self, container) -> None:
        """The grant is fixed from the principal at submission and stored with the task. An
        ordinary user asking for the Engineer persona is refused exactly as on /chat (their plan
        has no Engineer), and an Engineer-capable person's task carries that capability -- and
        nothing beyond it."""
        container.router.get("echo").queue(ScriptedTurn(text="hi"))
        with TestClient(_app(container)) as client:
            chat = client.post("/api/v3/chat", headers=AUTH_A, json={
                "message": "x", "agent": "engineer", "plan": False, "use_tools": False})
            refused = _submit(client, persona="engineer")
            assert refused.status_code == chat.status_code == 402
            assert refused.json()["error"] == chat.json()["error"] == "plan_limit"

            tid = _submit(client).json()["task"]["id"]
            _wait(client, tid, AUTH_A, {"succeeded"})
            spec = asyncio.run(container.agent_task_store.spec(ALICE, tid))
            assert spec["user_id"] == ALICE and spec["capabilities"] == []
            assert spec["delegated_tool_grant"] is None and spec["origin"] == "remote"

            # An Engineer-capable person: the grant is theirs and can only be narrowed (admit_turn
            # drops `engineer_agent` from a non-Engineer turn on a plan without it, as on /chat).
            container.router.get("echo").queue(ScriptedTurn(text="eng"))
            eng = _submit(client, headers=AUTH_E).json()["task"]["id"]
            _wait(client, eng, AUTH_E, {"succeeded"})
            eng_spec = asyncio.run(container.agent_task_store.spec(ENG, eng))
        assert eng_spec["user_id"] == ENG and set(eng_spec["capabilities"]) <= {"engineer_agent"}

    def test_listing_is_the_callers_own_newest_first_with_filters(self, container) -> None:
        with TestClient(_app(container)) as client:
            ids = []
            for goal in ("one", "two", "three"):
                container.router.get("echo").queue(ScriptedTurn(text=f"answer {goal}"))
                task = _submit(client, goal).json()["task"]
                _wait(client, task["id"], AUTH_A, {"succeeded"})
                ids.append(task["id"])
            other = _submit(client, "bobs", headers=AUTH_B).json()["task"]
            _wait(client, other["id"], AUTH_B, {"succeeded", "failed"})

            listed = client.get(TASKS, headers=AUTH_A).json()
            assert [t["id"] for t in listed["tasks"]] == list(reversed(ids))
            assert all("result" not in t and t["result_summary"] for t in listed["tasks"])
            assert client.get(f"{TASKS}?status=failed", headers=AUTH_A).json()["tasks"] == []
            assert len(client.get(f"{TASKS}?status=succeeded&limit=2", headers=AUTH_A).json()["tasks"]) == 2
            assert client.get(f"{TASKS}?persona=engineer", headers=AUTH_A).json()["tasks"] == []
            assert client.get(f"{TASKS}?status=bogus", headers=AUTH_A).status_code == 422
            assert [t["id"] for t in client.get(TASKS, headers=AUTH_B).json()["tasks"]] == [other["id"]]

    def test_a_turn_that_fails_is_a_failed_task_with_the_chat_error_kind(self, container) -> None:
        container.router.get("echo").queue(ScriptedTurn(text="sk-" + "a1b2c3d4e5" * 4))  # output guard
        with TestClient(_app(container)) as client:
            task = _submit(client).json()["task"]
            done = _wait(client, task["id"], AUTH_A, {"failed", "succeeded"})
        assert done["status"] == "failed" and done["error"]["kind"] == "safety_violation"
        assert done["error"]["retryable"] is False and done["result"] is None


class TestRefusals:
    def test_validation_is_422_and_unsupported_fields_are_not_silently_dropped(self, container) -> None:
        with TestClient(_app(container)) as client:
            assert client.post(TASKS, json={}, headers=AUTH_A).status_code == 422
            assert client.post(TASKS, json={"goal": ""}, headers=AUTH_A).status_code == 422
            assert client.post(TASKS, json={"goal": "x", "priority": "urgent"}, headers=AUTH_A).status_code == 422
            assert client.post(TASKS, json={"goal": "x", "images": []}, headers=AUTH_A).status_code == 422
            assert client.post(TASKS, json={"goal": "x", "response_schema": {}}, headers=AUTH_A).status_code == 422
            assert client.post(TASKS, json={"goal": "x", "session_id": "bad id!"}, headers=AUTH_A).status_code == 422
            assert client.post(TASKS, json={"goal": "x"}).status_code == 401

    def test_run_at_too_far_ahead_is_refused(self, container) -> None:
        far = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        with TestClient(_app(container)) as client:
            refused = _submit(client, run_at=far)
        assert refused.status_code == 422 and refused.json()["details"]["field"] == "run_at"

    def test_the_rate_limit_is_the_chat_budget(self, container) -> None:
        from aura_core.security.rate_limit import ROUTE_COSTS

        limiter = SlidingWindowRateLimiter(
            container.cache_backend,
            policies=(RateLimitPolicy(scope="user", limit=ROUTE_COSTS["chat"], window_s=3600),),
        )
        container.router.get("echo").queue(ScriptedTurn(text="one"))
        with TestClient(_app(container, rate_limiter=limiter)) as client:
            assert _submit(client, "first").status_code == 202
            second = _submit(client, "second")
        assert second.status_code == 429 and second.json()["error"] == "rate_limited"
        assert int(second.headers["retry-after"]) >= 0

    def test_the_plan_entitlement_applies_and_a_failed_task_gives_its_message_back(
        self, container
    ) -> None:
        free = replace(DEFAULT_CATALOGUE["free"], messages_per_month=1)
        container.plans.catalogue = MappingProxyType({**DEFAULT_CATALOGUE, "free": free})

        async def used() -> int:
            ws = (await container.tenancy.ensure_personal(ALICE)).id
            return (await container.plans.usage(ws))["messages_used"]

        container.router.get("echo").queue(ScriptedTurn(text="sk-" + "a1b2c3d4e5" * 4))
        with TestClient(_app(container)) as client:
            failing = _submit(client, "will be blocked by the output guard").json()["task"]
            assert _wait(client, failing["id"], AUTH_A, {"failed"})["status"] == "failed"
            deadline = time.time() + 5
            while asyncio.run(used()) != 0 and time.time() < deadline:
                time.sleep(0.05)
            assert asyncio.run(used()) == 0, "a task that produced no answer used up the month"

            container.router.get("echo").queue(ScriptedTurn(text="fine"))
            ok = _submit(client, "this one counts").json()["task"]
            _wait(client, ok["id"], AUTH_A, {"succeeded"})
            assert asyncio.run(used()) == 1
            over = _submit(client, "one too many")
        assert over.status_code == 402, over.text
        assert over.json()["error"] == "plan_limit"

    def test_engineer_consent_is_refused_at_submission_not_discovered_in_a_failed_task(
        self, container
    ) -> None:
        # Plan entitlement is checked first (as on /chat); give the plan the Engineer so consent
        # is the gate under test.
        free = replace(DEFAULT_CATALOGUE["free"], engineer=True)
        container.plans.catalogue = MappingProxyType({**DEFAULT_CATALOGUE, "free": free})
        with TestClient(_app(container)) as client:
            refused = _submit(client, "fix the build", headers=AUTH_E, persona="engineer")
            assert refused.status_code == 403
            assert refused.json()["error"] == "consent_required"
            assert client.get(TASKS, headers=AUTH_E).json()["tasks"] == []

    def test_disabled_deployment_answers_503(self, monkeypatch) -> None:
        container = _container(monkeypatch, AGENT_TASKS_ENABLED="false")
        with TestClient(_app(container)) as client:
            refused = _submit(client)
        assert refused.status_code == 503 and refused.json()["error"] == "dependency_unavailable"

    def test_a_full_queue_is_a_429_with_retry_after_and_releases_the_key(self, monkeypatch) -> None:
        container = _container(monkeypatch, AGENT_TASKS_MAX_QUEUED=1, AGENT_TASKS_MAX_CONCURRENT=1,
                               AGENT_TASKS_MAX_CONCURRENT_PER_USER=1)
        later = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        with TestClient(_app(container)) as client:
            assert _submit(client, "waits one", run_at=later).status_code == 202
            refused = _submit(client, "no room", run_at=later, client_request_id="key-1")
            assert refused.status_code == 429
            assert refused.json()["error"] == "task_queue_full"
            assert int(refused.headers["retry-after"]) >= 1
            assert refused.json()["details"]["scope"] == "global"
            # The refusal created nothing, so the same key is free to be used again.
            assert len(client.get(TASKS, headers=AUTH_A).json()["tasks"]) == 1

    def test_a_users_waiting_tasks_are_capped(self, monkeypatch) -> None:
        container = _container(monkeypatch, AGENT_TASKS_MAX_QUEUED_PER_USER=2)
        later = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        with TestClient(_app(container)) as client:
            assert _submit(client, "a", run_at=later).status_code == 202
            assert _submit(client, "b", run_at=later).status_code == 202
            third = _submit(client, "c", run_at=later)
            assert third.status_code == 429 and third.json()["details"]["scope"] == "user"
            assert _submit(client, "bobs", headers=AUTH_B, run_at=later).status_code == 202


class TestOwnership:
    def test_bob_cannot_read_cancel_or_approve_alices_task(self, container) -> None:
        later = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        with TestClient(_app(container)) as client:
            task = _submit(client, "alice private", run_at=later).json()["task"]
            tid = task["id"]
            assert client.get(f"{TASKS}/{tid}", headers=AUTH_B).status_code == 404
            assert client.post(f"{TASKS}/{tid}/cancel", headers=AUTH_B).status_code == 404
            assert client.post(f"{TASKS}/{tid}/approve", json={"approval_ids": ["x"]},
                               headers=AUTH_B).status_code == 404
            assert client.get(f"{TASKS}/does-not-exist", headers=AUTH_B).status_code == 404
            still = client.get(f"{TASKS}/{tid}", headers=AUTH_A).json()["task"]
            assert still["status"] == "queued"

    def test_the_same_key_from_two_people_makes_two_tasks(self, container) -> None:
        later = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        with TestClient(_app(container)) as client:
            a = _submit(client, "same", run_at=later, client_request_id="shared-key").json()["task"]
            b = _submit(client, "same", headers=AUTH_B, run_at=later,
                        client_request_id="shared-key").json()["task"]
        assert a["id"] != b["id"]


class TestCancel:
    def test_cancel_a_scheduled_task(self, container) -> None:
        later = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        with TestClient(_app(container)) as client:
            tid = _submit(client, "later", run_at=later).json()["task"]["id"]
            cancelled = client.post(f"{TASKS}/{tid}/cancel", headers=AUTH_A).json()
            assert cancelled["task"]["status"] == "cancelled" and cancelled["cancel"]["accepted"] is True
            again = client.post(f"{TASKS}/{tid}/cancel", headers=AUTH_A).json()
            assert again["cancel"]["accepted"] is False and again["task"]["status"] == "cancelled"

    def test_cancel_a_running_task_stops_its_turn(self, container) -> None:
        container.router.get("echo").queue(ScriptedTurn(text="never delivered", delay_ms=20_000))
        with TestClient(_app(container)) as client:
            tid = _submit(client, "slow one").json()["task"]["id"]
            _wait(client, tid, AUTH_A, {"running"})
            cancelled = client.post(f"{TASKS}/{tid}/cancel", headers=AUTH_A).json()
            assert cancelled["task"]["status"] == "cancelled"
            final = client.get(f"{TASKS}/{tid}", headers=AUTH_A).json()["task"]
            assert final["status"] == "cancelled" and final["result"] is None
            assert container.agent_tasks.stats()["running"] == 0

    def test_cancelling_a_finished_task_changes_nothing(self, container) -> None:
        container.router.get("echo").queue(ScriptedTurn(text="done"))
        with TestClient(_app(container)) as client:
            tid = _submit(client).json()["task"]["id"]
            _wait(client, tid, AUTH_A, {"succeeded"})
            result = client.post(f"{TASKS}/{tid}/cancel", headers=AUTH_A).json()
        assert result["task"]["status"] == "succeeded" and result["cancel"]["accepted"] is False


class TestScheduling:
    def test_run_at_delays_the_start(self, container) -> None:
        container.router.get("echo").queue(ScriptedTurn(text="on time"))
        start_after = datetime.now(timezone.utc) + timedelta(seconds=2.0)
        with TestClient(_app(container)) as client:
            tid = _submit(client, "scheduled", run_at=start_after.isoformat()).json()["task"]["id"]
            time.sleep(0.4)
            early = client.get(f"{TASKS}/{tid}", headers=AUTH_A).json()
            assert early["task"]["status"] == "queued" and early["queue"]["position"] == 1
            done = _wait(client, tid, AUTH_A, {"succeeded"})
        assert datetime.fromisoformat(done["started_at"]) >= start_after - timedelta(milliseconds=50)

    def test_a_naive_run_at_is_utc_and_the_past_means_now(self, container) -> None:
        container.router.get("echo").queue(ScriptedTurn(text="now"))
        past = (datetime.now(timezone.utc) - timedelta(hours=2)).replace(tzinfo=None).isoformat()
        with TestClient(_app(container)) as client:
            tid = _submit(client, "overdue", run_at=past).json()["task"]["id"]
            assert _wait(client, tid, AUTH_A, {"succeeded"})["status"] == "succeeded"

    def test_priority_decides_who_goes_first_in_a_real_queue(self, monkeypatch) -> None:
        container = _container(monkeypatch, AGENT_TASKS_MAX_CONCURRENT=1,
                               AGENT_TASKS_MAX_CONCURRENT_PER_USER=1)
        echo = container.router.get("echo")
        echo.queue(ScriptedTurn(text="blocker", delay_ms=2500))
        with TestClient(_app(container)) as client:
            blocker = _submit(client, "blocker").json()["task"]["id"]
            _wait(client, blocker, AUTH_A, {"running"})
            echo.queue(ScriptedTurn(text="L"), ScriptedTurn(text="N"), ScriptedTurn(text="H"))
            low = _submit(client, "low", priority="low", headers=AUTH_B).json()["task"]["id"]
            normal = _submit(client, "normal", headers=AUTH_B).json()["task"]["id"]
            # Same owner, so they run one at a time; the high one was queued last.
            high = _submit(client, "high", priority="high", headers=AUTH_B).json()["task"]["id"]
            rows = {tid: _wait(client, tid, AUTH_B, {"succeeded"}) for tid in (low, normal, high)}
        started = sorted(rows, key=lambda tid: rows[tid]["started_at"])
        assert started == [high, normal, low]


class TestIdempotency:
    def test_the_same_key_and_request_returns_the_same_task_and_runs_it_once(self, container) -> None:
        container.router.get("echo").queue(ScriptedTurn(text="once"))
        with TestClient(_app(container)) as client:
            first = _submit(client, "pay the invoice", client_request_id="req-1")
            assert first.status_code == 202
            tid = first.json()["task"]["id"]
            _wait(client, tid, AUTH_A, {"succeeded"})
            replay = _submit(client, "pay the invoice", client_request_id="req-1")
            assert replay.status_code == 200 and replay.headers["idempotent-replayed"] == "true"
            assert replay.json()["task"]["id"] == tid and replay.json()["idempotent_replay"] is True
            assert replay.json()["task"]["status"] == "succeeded"
            assert len(client.get(TASKS, headers=AUTH_A).json()["tasks"]) == 1
            assert len(client.get("/api/v3/usage/turns", headers=AUTH_A).json()["turns"]) == 1

    def test_a_different_request_under_a_reused_key_is_409(self, container) -> None:
        later = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        with TestClient(_app(container)) as client:
            assert _submit(client, "original", run_at=later, client_request_id="k").status_code == 202
            clash = _submit(client, "something else", run_at=later, client_request_id="k")
            assert clash.status_code == 409 and clash.json()["error"] == "idempotency_key_reused"
            changed_option = _submit(client, "original", run_at=later, client_request_id="k",
                                     priority="high")
            assert changed_option.status_code == 409
            assert len(client.get(TASKS, headers=AUTH_A).json()["tasks"]) == 1

    def test_the_header_and_the_body_field_are_the_same_key(self, container) -> None:
        later = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        with TestClient(_app(container)) as client:
            a = client.post(TASKS, json={"goal": "x", "plan": False, "use_tools": False, "run_at": later},
                            headers={**AUTH_A, "Idempotency-Key": "hdr-1"})
            b = _submit(client, "x", run_at=later, idempotency_key="hdr-1")
            c = _submit(client, "x", run_at=later, client_request_id="hdr-1")
            assert a.status_code == 202 and b.status_code == 200 and c.status_code == 200
            assert a.json()["task"]["id"] == b.json()["task"]["id"] == c.json()["task"]["id"]
            disagree = client.post(TASKS, json={"goal": "x", "client_request_id": "one"},
                                   headers={**AUTH_A, "Idempotency-Key": "two"})
            assert disagree.status_code == 422

    def test_a_malformed_key_is_422(self, container) -> None:
        with TestClient(_app(container)) as client:
            assert _submit(client, client_request_id="has space").status_code == 422
            assert _submit(client, client_request_id="x" * 300).status_code == 422

    def test_a_refused_request_does_not_burn_its_key(self, container) -> None:
        with TestClient(_app(container)) as client:
            refused = client.post(TASKS, json={"goal": "x", "session_id": "bad id!",
                                               "client_request_id": "retry-me"}, headers=AUTH_A)
            assert refused.status_code == 422
            container.router.get("echo").queue(ScriptedTurn(text="ok"))
            fixed = _submit(client, "x", client_request_id="retry-me")
            assert fixed.status_code == 202

    def test_concurrent_retries_with_one_key_make_one_task(self, container) -> None:
        later = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        app = _app(container)
        from concurrent.futures import ThreadPoolExecutor

        with TestClient(app) as client:
            def send(_):
                return _submit(client, "burst", run_at=later, client_request_id="burst-key")

            with ThreadPoolExecutor(max_workers=6) as pool:
                responses = list(pool.map(send, range(6)))
            ids = {r.json()["task"]["id"] for r in responses if r.status_code in (200, 202)}
            assert len(ids) == 1, [r.text for r in responses]
            assert sorted(r.status_code for r in responses).count(202) == 1
            assert len(client.get(TASKS, headers=AUTH_A).json()["tasks"]) == 1


# -- approval (a confirm-gated tool), with idempotency ------------------------------------------------


class _Ran:
    def __init__(self) -> None:
        self.targets: list[str] = []


def _gated_tool(ran: _Ran) -> FunctionTool:
    async def handler(target: str, context) -> str:
        ran.targets.append(target)
        return f"deleted {target}"

    spec = ToolSpec(
        name="delete_record", description="A side-effecting, confidential tool: always confirmed.",
        parameters=(ParameterSpec(name="target", type="string", description="target"),),
        category=ToolCategory.UTILITY, sensitivity=ToolSensitivity.CONFIDENTIAL, side_effecting=True,
    )
    return FunctionTool(spec, handler)


def _plan(target: str) -> ScriptedTurn:
    return ScriptedTurn(text=json.dumps({"steps": [{
        "id": "s1", "kind": "tool", "tool": "delete_record", "arguments": {"target": target},
        "description": f"delete {target}"}]}))


class TestApproval:
    @pytest.fixture
    def ran(self, container) -> _Ran:
        ran = _Ran()
        container.tools.register_all([_gated_tool(ran)])
        return ran

    def _held(self, client, container, target="A"):
        container.router.get("echo").queue(_plan(target), ScriptedTurn(text="I need your approval."))
        tid = client.post(TASKS, json={"goal": f"delete {target}", "plan": True},
                          headers=AUTH_A).json()["task"]["id"]
        task = _wait(client, tid, AUTH_A, {"awaiting_approval", "succeeded", "failed"})
        assert task["status"] == "awaiting_approval", task
        return task

    def test_a_held_call_parks_the_task_and_approval_resumes_it_once(self, container, ran) -> None:
        with TestClient(_app(container)) as client:
            task = self._held(client, container)
            assert ran.targets == []
            [pending] = task["result"]["pending_tool_calls"]
            assert pending["tool"] == "delete_record" and pending["approval_id"]
            assert task["finished_at"] is None

            container.router.get("echo").queue(ScriptedTurn(text="Deleted A."))
            resumed = client.post(f"{TASKS}/{task['id']}/approve", headers=AUTH_A,
                                  json={"approval_ids": [pending["approval_id"]]})
            assert resumed.status_code == 202
            assert resumed.json()["task"]["status"] == "queued" and resumed.json()["task"]["attempts"] == 2
            done = _wait(client, task["id"], AUTH_A, {"succeeded", "failed"})
            assert done["status"] == "succeeded", done
        assert ran.targets == ["A"]

    def test_approval_is_idempotent_with_a_client_request_id(self, container, ran) -> None:
        with TestClient(_app(container)) as client:
            task = self._held(client, container)
            approval = task["result"]["pending_tool_calls"][0]["approval_id"]
            container.router.get("echo").queue(ScriptedTurn(text="Deleted A."))
            body = {"approval_ids": [approval], "client_request_id": "approve-1"}
            first = client.post(f"{TASKS}/{task['id']}/approve", json=body, headers=AUTH_A)
            assert first.status_code == 202
            _wait(client, task["id"], AUTH_A, {"succeeded"})
            # An offline client replays its queued approval: same answer, nothing runs twice, and
            # not the 409 a spent one-shot approval would otherwise give.
            replay = client.post(f"{TASKS}/{task['id']}/approve", json=body, headers=AUTH_A)
            assert replay.status_code == 200 and replay.json()["idempotent_replay"] is True
            assert replay.json()["task"]["id"] == task["id"]
            without_key = client.post(f"{TASKS}/{task['id']}/approve", headers=AUTH_A,
                                      json={"approval_ids": [approval]})
            assert without_key.status_code == 409
            clash = client.post(f"{TASKS}/{task['id']}/approve", headers=AUTH_A,
                                json={"approval_ids": ["other"], "client_request_id": "approve-1"})
            assert clash.status_code == 409 and clash.json()["error"] == "idempotency_key_reused"
        assert ran.targets == ["A"]

    def test_an_unknown_approval_id_is_refused_and_the_task_stays_parked(self, container, ran) -> None:
        with TestClient(_app(container)) as client:
            task = self._held(client, container)
            bad = client.post(f"{TASKS}/{task['id']}/approve", headers=AUTH_A,
                              json={"approval_ids": ["not-an-approval"], "client_request_id": "k"})
            assert bad.status_code == 409 and bad.json()["error"] == "confirmation_invalid"
            assert client.get(f"{TASKS}/{task['id']}", headers=AUTH_A).json()["task"]["status"] == "awaiting_approval"
            # The failed attempt released the key; the right ids under it now work.
            container.router.get("echo").queue(ScriptedTurn(text="ok"))
            good = client.post(f"{TASKS}/{task['id']}/approve", headers=AUTH_A, json={
                "approval_ids": [task["result"]["pending_tool_calls"][0]["approval_id"]],
                "client_request_id": "k"})
            assert good.status_code == 202
            _wait(client, task["id"], AUTH_A, {"succeeded"})
        assert ran.targets == ["A"]

    def test_only_a_task_awaiting_approval_can_be_approved(self, container) -> None:
        container.router.get("echo").queue(ScriptedTurn(text="done"))
        with TestClient(_app(container)) as client:
            tid = _submit(client).json()["task"]["id"]
            _wait(client, tid, AUTH_A, {"succeeded"})
            refused = client.post(f"{TASKS}/{tid}/approve", json={"approval_ids": ["x"]}, headers=AUTH_A)
        assert refused.status_code == 409 and refused.json()["error"] == "task_not_awaiting_approval"

    def test_cancelling_a_parked_task_cancels_it(self, container, ran) -> None:
        with TestClient(_app(container)) as client:
            task = self._held(client, container)
            cancelled = client.post(f"{TASKS}/{task['id']}/cancel", headers=AUTH_A).json()
        assert cancelled["task"]["status"] == "cancelled" and ran.targets == []


class TestLifecycleAndRecovery:
    def test_a_silent_task_reads_failed_interrupted(self, container) -> None:
        """A process that died while holding a task leaves a record nobody heartbeats."""
        from aura_core.tasks import AgentTask

        long_ago = datetime.now(timezone.utc) - timedelta(minutes=30)
        orphan = AgentTask(
            id="orphan-1", owner=ALICE, goal="lost", persona="orchestrator", status="running",
            priority="normal", created_at=long_ago, updated_at=long_ago, started_at=long_ago,
            heartbeat_at=long_ago, attempts=1,
        )
        asyncio.run(container.agent_task_store.create(orphan, {"message": "lost"}))
        with TestClient(_app(container)) as client:
            body = client.get(f"{TASKS}/orphan-1", headers=AUTH_A).json()["task"]
            assert body["status"] == "failed" and body["error"]["kind"] == "interrupted"
            assert body["error"]["retryable"] is True
            listed = client.get(f"{TASKS}?status=failed", headers=AUTH_A).json()["tasks"]
            assert [t["id"] for t in listed] == ["orphan-1"]

    def test_shutdown_marks_running_and_queued_tasks_so_none_is_left_running(self, container) -> None:
        container.router.get("echo").queue(ScriptedTurn(text="slow", delay_ms=20_000))
        later = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        with TestClient(_app(container)) as client:
            running = _submit(client, "slow").json()["task"]["id"]
            _wait(client, running, AUTH_A, {"running"})
            waiting = _submit(client, "scheduled", run_at=later).json()["task"]["id"]
        # The with-block ran the app's shutdown, which stops the queue.
        for tid in (running, waiting):
            final = asyncio.run(container.agent_task_store.get(ALICE, tid))
            assert final.status == "failed" and final.error["kind"] == "server_shutdown", final

    def test_the_queue_starts_with_the_app_and_is_idle_until_a_task_is_posted(self, container) -> None:
        with TestClient(_app(container)) as client:
            assert container.agent_tasks.running
            assert container.agent_tasks.stats()["running"] == 0
            assert client.get(TASKS, headers=AUTH_A).json() == {"tasks": [], "count": 0}
        assert not container.agent_tasks.running
