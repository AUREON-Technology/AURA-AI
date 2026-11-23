"""``GET /api/v3/sessions/{id}/export`` and ``GET /api/v3/me/export``.

What these routes promise, asserted against inputs fixed in the test (never against the code under
test): a conversation export is **partial and says so** (the server never holds the encrypted chat,
only its own working transcript -- ADR-032), the bundle carries the caller's personal data and
nothing of anyone else's, one unreadable store takes down only its own section, and a list is
marked ``truncated`` only when a row was really left out.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from aura_core.api import export as export_module
from aura_core.api.export import BUNDLE_SECTIONS, EXPORT_VERSION, EXPORTS_PER_MINUTE, collect, conversation_markdown
from aura_core.domain.approvals import ToolApproval
from aura_core.domain.identifiers import SessionId, UserId
from aura_core.memory.session import EpisodicMemory

pytest.importorskip("fastapi")

ALICE, BOB = "alice", "bob"
SESSION = SessionId("alice-chat-1")


def _app(container, *, service: bool = False):
    from aura_core.api import Principal, build_app
    from aura_core.domain.errors import AuthenticationError

    class _Key:
        key_id, name, rate_per_minute, principal_id = "svc-1", "flame", 60, "svc-owner"

        @staticmethod
        def allows(method, path):
            return True

    async def authenticate(request):
        if service:
            return Principal.for_service(_Key())
        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer "):
            raise AuthenticationError("missing")
        return Principal(user_id=UserId(header[7:]), email="", role="User")

    return build_app(container, authenticator=authenticate)


def _as(user: str) -> dict:
    return {"Authorization": f"Bearer {user}"}


def _client(container, **kw):
    from fastapi.testclient import TestClient

    return TestClient(_app(container, **kw))


async def _say(container, session: SessionId, owner: str, *exchanges: tuple[str, str]) -> None:
    for question, answer in exchanges:
        await container.episodic.append(session, UserId(owner), user_message=question, assistant_message=answer)


# ---------------------------------------------------------------------------------------------
# one conversation
# ---------------------------------------------------------------------------------------------


class TestSessionExport:
    def test_the_owner_gets_the_transcript_and_is_told_it_is_partial(self, container) -> None:
        asyncio.run(_say(container, SESSION, ALICE, ("What is the capital of Egypt?", "Cairo."), ("And of Peru?", "Lima.")))
        with _client(container) as client:
            response = client.get(f"/api/v3/sessions/{SESSION.value}/export", headers=_as(ALICE))

        body = response.json()
        assert response.status_code == 200
        assert body["version"] == EXPORT_VERSION and body["session_id"] == SESSION.value
        assert body["complete"] is False and "encrypted" in body["note"]
        assert body["messages"] == [
            {"role": "user", "content": "What is the capital of Egypt?"},
            {"role": "assistant", "content": "Cairo."},
            {"role": "user", "content": "And of Peru?"},
            {"role": "assistant", "content": "Lima."},
        ]
        assert body["turn_count"] == 2 and body["summary"] == "" and body["earlier_messages_summarised"] == 0

    def test_a_summarised_conversation_exports_the_summary_and_how_much_it_stands_for(self, container) -> None:
        async def summarise(transcript: str, previous: str) -> str:
            return "SUMMARY-OF-OLDER-TURNS"

        container.episodic = EpisodicMemory(
            container.cache_backend, window=2, summarise_after=4, summariser=summarise,
        )
        asyncio.run(_say(container, SESSION, ALICE, *[(f"question {i}", f"answer {i}") for i in range(3)]))
        with _client(container) as client:
            body = client.get(f"/api/v3/sessions/{SESSION.value}/export", headers=_as(ALICE)).json()

        assert body["summary"] == "SUMMARY-OF-OLDER-TURNS"
        assert body["earlier_messages_summarised"] == 4
        assert [m["content"] for m in body["messages"]] == ["question 2", "answer 2"]
        assert body["turn_count"] == 3

    def test_markdown_is_an_attachment_with_the_summary_then_each_turn(self, container) -> None:
        async def summarise(transcript: str, previous: str) -> str:
            return "EARLIER-SUMMARY"

        container.episodic = EpisodicMemory(
            container.cache_backend, window=2, summarise_after=4, summariser=summarise,
        )
        asyncio.run(_say(container, SESSION, ALICE, *[(f"question {i}", f"answer {i}") for i in range(3)]))
        with _client(container) as client:
            response = client.get(f"/api/v3/sessions/{SESSION.value}/export?format=markdown", headers=_as(ALICE))

        text = response.text
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/markdown")
        assert response.headers["content-disposition"] == 'attachment; filename="aura-chat-alice-chat-1.md"'
        assert text.startswith(f"# Conversation {SESSION.value}\n")
        assert text.index("EARLIER-SUMMARY") < text.index("## You") < text.index("question 2") < text.index("## AURA") < text.index("answer 2")
        assert "Partial" in text

    def test_nothing_held_is_a_404_and_so_is_someone_elses_session(self, container) -> None:
        asyncio.run(_say(container, SESSION, ALICE, ("My flight is on the 14th.", "Noted.")))
        with _client(container) as client:
            missing = client.get("/api/v3/sessions/never-existed/export", headers=_as(ALICE))
            foreign = client.get(f"/api/v3/sessions/{SESSION.value}/export", headers=_as(BOB))
            own = client.get(f"/api/v3/sessions/{SESSION.value}/export", headers=_as(ALICE))

        assert missing.status_code == foreign.status_code == 404
        assert (foreign.json()["error"], foreign.json()["message"]) == (missing.json()["error"], missing.json()["message"])
        assert foreign.json()["details"] == {"session_id": SESSION.value}, "only the id the caller named"
        assert "14th" not in foreign.text
        assert own.status_code == 200, "Bob's attempt must not have disturbed Alice's transcript"

    def test_an_id_that_is_not_an_identifier_is_refused(self, container) -> None:
        with _client(container) as client:
            response = client.get("/api/v3/sessions/not%20valid!/export", headers=_as(ALICE))
        assert response.status_code == 422
        assert response.json()["error"] == "validation_error"

    def test_an_unknown_format_is_refused(self, container) -> None:
        asyncio.run(_say(container, SESSION, ALICE, ("hi", "hello")))
        with _client(container) as client:
            response = client.get(f"/api/v3/sessions/{SESSION.value}/export?format=pdf", headers=_as(ALICE))
        assert response.status_code == 422

    def test_a_service_key_has_no_conversation_to_export(self, container) -> None:
        asyncio.run(_say(container, SESSION, "svc-owner", ("hi", "hello")))
        with _client(container, service=True) as client:
            response = client.get(f"/api/v3/sessions/{SESSION.value}/export")
        assert response.status_code == 403

    def test_a_caller_must_be_signed_in(self, container) -> None:
        with _client(container) as client:
            assert client.get(f"/api/v3/sessions/{SESSION.value}/export").status_code == 401


class TestMarkdownRendering:
    def test_roles_become_speakers_and_an_empty_summary_has_no_section(self) -> None:
        text = conversation_markdown(
            "s1", "", [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi there"}], "NOTE",
        )
        assert text == "# Conversation s1\n\n> NOTE\n\n## You\n\nhello\n\n## AURA\n\nhi there\n"

    def test_a_file_name_cannot_carry_a_path_a_quote_or_a_newline(self) -> None:
        assert export_module._safe_name('../../etc/pass"wd\r\n') == ".._.._etc_pass_wd__"
        assert export_module._safe_name("") == "conversation"
        assert len(export_module._safe_name("a" * 200)) == 64


# ---------------------------------------------------------------------------------------------
# the bundle
# ---------------------------------------------------------------------------------------------


async def _seed(container, user: str, tag: str) -> None:
    """One of everything the bundle carries, for ``user``, each marked with ``tag``."""
    from aura_core.domain.custom_agents import CustomAgent, new_custom_agent_id
    from aura_core.domain.model_settings import ModelSettings
    from aura_core.domain.notifications import NotificationSettings

    owner = UserId(user)
    now = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)
    await container.custom_agents.create(
        CustomAgent(id=new_custom_agent_id(), owner_id=owner, name=f"agent-{tag}", base_agent_id="orchestrator",
                    created_at=now, updated_at=now, instruction=f"instruction-{tag}"),
        limit=10,
    )
    await container.model_settings.put(owner, ModelSettings(routing_policy="quality_first", temperature=0.3), at=now)
    await container.notification_settings.put(owner, NotificationSettings(disabled_kinds=frozenset({"renewal_due"})), at=now)
    await container.inbox.notify(owner, "payment_received", {"plan_id": f"plan-{tag}"})
    await container.approval_ledger.record_held([
        ToolApproval(id=f"appr-{tag}", owner_id=owner, tool=f"tool-{tag}", call_id=f"call-{tag}", digest="d" * 8,
                     created_at=container.clock.now(), expires_at=container.clock.now().replace(year=2099)),
    ])
    await container.memory.remember(owner_id=owner, content=f"memory-{tag}", subject=f"subject-{tag}",
                                    source="test")
    await container.knowledge.ingest(content=f"content-{tag}", title=f"doc-{tag}", owner_id=owner)


class TestBundle:
    def test_a_new_person_gets_every_section_empty_and_a_download_header(self, container) -> None:
        with _client(container) as client:
            response = client.get("/api/v3/me/export", headers=_as(ALICE))

        body = response.json()
        assert response.status_code == 200
        assert response.headers["content-disposition"].startswith('attachment; filename="aura-data-')
        assert response.headers["content-disposition"].endswith('.json"')
        assert body["version"] == EXPORT_VERSION and body["user_id"] == ALICE and body["scope"] == "personal"
        assert body["exported_at"].endswith("+00:00")
        assert len(body["not_included"]) == 3
        for name in BUNDLE_SECTIONS:
            assert body[name]["available"] is True, name
        assert body["custom_agents"]["items"] == [] and body["memories"]["items"] == []
        assert body["knowledge_documents"]["items"] == [] and body["approvals"]["items"] == []
        assert body["notifications"]["items"] == [] and body["notification_settings"]["disabled_kinds"] == []
        assert body["model_settings"]["settings"] is None

    def test_it_carries_what_the_server_holds_and_only_the_callers(self, container) -> None:
        asyncio.run(_seed(container, ALICE, "A"))
        asyncio.run(_seed(container, BOB, "B"))
        with _client(container) as client:
            alice = client.get("/api/v3/me/export", headers=_as(ALICE))
            bob = client.get("/api/v3/me/export", headers=_as(BOB))

        a = alice.json()
        assert [x["name"] for x in a["custom_agents"]["items"]] == ["agent-A"]
        assert a["custom_agents"]["items"][0]["instruction"] == "instruction-A"
        assert a["model_settings"]["settings"]["routing_policy"] == "quality_first"
        assert a["model_settings"]["settings"]["temperature"] == 0.3
        assert a["notification_settings"]["disabled_kinds"] == ["renewal_due"]
        assert [(n["kind"], n["params"]) for n in a["notifications"]["items"]] == [("payment_received", {"plan_id": "plan-A"})]
        assert [(x["approval_id"], x["tool"]) for x in a["approvals"]["items"]] == [("appr-A", "tool-A")]
        assert [(m["content"], m["subject"]) for m in a["memories"]["items"]] == [("memory-A", "subject-A")]
        assert [d["title"] for d in a["knowledge_documents"]["items"]] == ["doc-A"]

        # Nothing of Bob's in Alice's download, and the other way round.
        assert "-B" not in alice.text and "plan-B" not in alice.text
        assert "-A" not in bob.text and "plan-A" not in bob.text
        assert [x["name"] for x in bob.json()["custom_agents"]["items"]] == ["agent-B"]

    def test_the_bundle_holds_no_document_text_and_no_approval_arguments(self, container) -> None:
        asyncio.run(_seed(container, ALICE, "A"))
        with _client(container) as client:
            text = client.get("/api/v3/me/export", headers=_as(ALICE)).text
        assert "content-A" not in text, "document text is served by its own route, not copied into the bundle"
        assert "call-A" in text and "digest" not in text and "arguments" not in text

    def test_one_store_that_will_not_answer_takes_down_only_its_own_section(self, container) -> None:
        asyncio.run(_seed(container, ALICE, "A"))

        class Broken:
            async def list(self, *a, **kw):
                raise RuntimeError("database is down")

        container.notification_store = Broken()
        with _client(container) as client:
            response = client.get("/api/v3/me/export", headers=_as(ALICE))

        body = response.json()
        assert response.status_code == 200
        assert body["notifications"] == {"available": False}
        assert [x["name"] for x in body["custom_agents"]["items"]] == ["agent-A"]
        assert [m["content"] for m in body["memories"]["items"]] == ["memory-A"]

    def test_a_deployment_without_a_store_marks_that_section_unavailable(self, container) -> None:
        container.approval_ledger = None
        with _client(container) as client:
            body = client.get("/api/v3/me/export", headers=_as(ALICE)).json()
        assert body["approvals"] == {"available": False}
        assert body["memories"]["available"] is True

    def test_a_service_key_has_nothing_to_export(self, container) -> None:
        with _client(container, service=True) as client:
            assert client.get("/api/v3/me/export").status_code == 403

    def test_a_caller_must_be_signed_in(self, container) -> None:
        with _client(container) as client:
            assert client.get("/api/v3/me/export").status_code == 401

    def test_exports_are_rate_limited_per_person_across_both_routes(self, container) -> None:
        asyncio.run(_say(container, SESSION, ALICE, ("hi", "hello")))
        with _client(container) as client:
            statuses = []
            for i in range(EXPORTS_PER_MINUTE + 1):
                route = "/api/v3/me/export" if i % 2 == 0 else f"/api/v3/sessions/{SESSION.value}/export"
                statuses.append(client.get(route, headers=_as(ALICE)).status_code)
            other = client.get("/api/v3/me/export", headers=_as(BOB)).status_code
        assert statuses == [200] * EXPORTS_PER_MINUTE + [429]
        assert other == 200, "one person's exports must not spend another's budget"


class TestCaps:
    def test_a_list_is_truncated_only_when_a_row_was_left_out(self, container, monkeypatch) -> None:
        async def remember(*facts: str) -> None:
            for fact in facts:
                await container.memory.remember(owner_id=UserId(ALICE), content=fact, subject=fact[:6], source="t")

        monkeypatch.setattr(export_module, "MAX_MEMORIES", 3)
        monkeypatch.setattr(export_module, "_PAGE", 2)
        asyncio.run(remember("Lives in Alexandria", "Prefers tea over coffee", "Works at a bank"))
        with _client(container) as client:
            exactly = client.get("/api/v3/me/export", headers=_as(ALICE)).json()["memories"]
            asyncio.run(remember("Has two cats"))
            more = client.get("/api/v3/me/export", headers=_as(ALICE)).json()["memories"]

        assert len(exactly["items"]) == 3 and exactly["truncated"] is False, "exactly the cap is not a cut"
        assert len(more["items"]) == 3 and more["truncated"] is True

    def test_documents_and_approvals_are_capped_the_same_way(self, container, monkeypatch) -> None:
        monkeypatch.setattr(export_module, "MAX_DOCUMENTS", 2)
        monkeypatch.setattr(export_module, "MAX_APPROVALS", 2)
        monkeypatch.setattr(export_module, "_PAGE", 1)

        async def seed(n: int) -> None:
            for i in range(n):
                await container.knowledge.ingest(content=f"body {i} of the document", title=f"doc {i}", owner_id=UserId(ALICE))
                await container.approval_ledger.record_held([
                    ToolApproval(id=f"a{i}", owner_id=UserId(ALICE), tool="t", call_id=f"c{i}", digest="d" * 8,
                                 created_at=container.clock.now(), expires_at=container.clock.now().replace(year=2099)),
                ])

        asyncio.run(seed(3))
        with _client(container) as client:
            body = client.get("/api/v3/me/export", headers=_as(ALICE)).json()
        assert len(body["knowledge_documents"]["items"]) == 2 and body["knowledge_documents"]["truncated"] is True
        assert len(body["approvals"]["items"]) == 2 and body["approvals"]["truncated"] is True


    def test_exactly_the_cap_of_approvals_is_not_a_cut(self, container, monkeypatch) -> None:
        monkeypatch.setattr(export_module, "MAX_APPROVALS", 2)

        async def seed() -> None:
            for i in range(2):
                await container.approval_ledger.record_held([
                    ToolApproval(id=f"a{i}", owner_id=UserId(ALICE), tool="t", call_id=f"c{i}", digest="d" * 8,
                                 created_at=container.clock.now(), expires_at=container.clock.now().replace(year=2099)),
                ])

        asyncio.run(seed())
        with _client(container) as client:
            approvals = client.get("/api/v3/me/export", headers=_as(ALICE)).json()["approvals"]
        assert len(approvals["items"]) == 2 and approvals["truncated"] is False


class TestCollect:
    async def _fetch_from(self, rows):
        calls = []

        async def fetch(*, limit, offset):
            calls.append((limit, offset))
            return rows[offset:offset + limit]

        return fetch, calls

    async def test_it_pages_through_everything_below_the_cap(self, monkeypatch) -> None:
        monkeypatch.setattr(export_module, "_PAGE", 2)
        fetch, calls = await self._fetch_from(list(range(5)))
        rows, truncated = await collect(fetch, 10)
        assert rows == [0, 1, 2, 3, 4] and truncated is False
        assert calls == [(2, 0), (2, 2), (2, 4)]

    async def test_it_cuts_at_the_cap_and_says_so(self, monkeypatch) -> None:
        monkeypatch.setattr(export_module, "_PAGE", 2)
        fetch, _ = await self._fetch_from(list(range(9)))
        rows, truncated = await collect(fetch, 4)
        assert rows == [0, 1, 2, 3] and truncated is True

    async def test_a_source_with_exactly_the_cap_is_not_truncated(self, monkeypatch) -> None:
        monkeypatch.setattr(export_module, "_PAGE", 2)
        fetch, _ = await self._fetch_from(list(range(4)))
        rows, truncated = await collect(fetch, 4)
        assert rows == [0, 1, 2, 3] and truncated is False

    async def test_an_empty_source_is_one_call(self) -> None:
        fetch, calls = await self._fetch_from([])
        assert await collect(fetch, 5) == ([], False)
        assert len(calls) == 1
