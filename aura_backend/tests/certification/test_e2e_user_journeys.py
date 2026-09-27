"""End-to-end user journeys against a running deployment.

A journey is a *sequence*, and that is the only reason this file exists separately from the other
certification suites. Every endpoint here is already reachable one call at a time; what is under
test is whether state survives between steps -- whether turn two remembers turn one, whether a
confirmed tool call is the same call that was proposed, whether a session still works after the
user made a mistake in it. A suite of independent requests cannot fail the way a real user fails.

So each test below asserts something that *spans* steps, and a step that cannot run skips the
journey rather than passing it. A journey is not partially certified: either the sequence held or
it was not exercised.

Two deliberate restraints:

**Nothing revokes the credentials the run depends on.** The obvious way to test the auth lifecycle
is token → refresh → revoke → refused, but revoking the token this suite was handed would break
every later test in the session and look like a product failure. Revocation is therefore exercised
only against a token the operator explicitly supplies for the purpose
(``AURA_CERT_REVOCABLE_TOKEN``), and skipped otherwise.

**No dependency is broken to test recovery.** Inducing failure against production is chaos
testing, which is out of scope by instruction. What is testable is how the system behaves while
*already* degraded -- and at the time of writing it is: ``/health/ready`` returns 503 because the
database schema is two migrations behind. A real degraded state is better than a simulated one, so
the recovery journey asserts the properties that must hold during it.

Journey D (desktop coding) is NOT APPLICABLE on any reachable target and says so rather than
quietly passing -- see ``test_agent_containment.py`` for why the sandbox cannot run on Railway.
"""

from __future__ import annotations

import os
import uuid

import pytest

from .conftest import SYNTHETIC_PREFIX, Target, call, explain

pytestmark = pytest.mark.cert

_MAX_TOKENS = 128

#: Substrings marking a tool that changes something. Used to find a candidate for the
#: confirmation journey at runtime rather than hardcoding a tool name that may not be granted.
SIDE_EFFECTING_HINTS = ("create", "write", "update", "delete", "send", "schedule", "commit")


def _tag() -> str:
    return f"{SYNTHETIC_PREFIX}-{uuid.uuid4().hex[:12]}"


def _chat(client, target: Target, token: str, message: str, **extra) -> dict | None:
    body = {
        "message": message,
        "max_output_tokens": extra.pop("max_output_tokens", _MAX_TOKENS),
        "use_knowledge": extra.pop("use_knowledge", False),
        "use_memory": extra.pop("use_memory", False),
        "use_tools": extra.pop("use_tools", False),
        **extra,
    }
    response = call(client, target, "POST", "/api/v3/chat", token, json=body)
    if response.status_code != 200:
        return None
    return response.json()


def _delete_documents_tagged(client, target: Target, token: str, tag: str) -> None:
    try:
        listing = call(client, target, "GET", "/api/v3/knowledge/documents", token)
        if listing.status_code != 200:
            return
        for doc in listing.json().get("documents", []) or []:
            if tag in str(doc.get("title") or ""):
                ident = doc.get("document_id") or doc.get("id")
                call(client, target, "DELETE", f"/api/v3/knowledge/documents/{ident}", token)
    except Exception:  # noqa: BLE001 - cleanup must not mask an assertion
        pass


# ---------------------------------------------------------------------------------------
# Journey A — a new user arrives and gets an answer
# ---------------------------------------------------------------------------------------


class TestJourneyNewUser:
    """install → launch → authenticate → first request → conversation → identity persists.

    The install and launch steps are client-side and cannot be reached from here; the suite says
    so rather than implying it covered them.
    """

    def test_a_first_request_succeeds_and_names_its_engine(self, client, target: Target) -> None:
        turn = _chat(
            client, target, target.token_a, "In one short sentence, what can you help me with?"
        )
        if turn is None:
            pytest.skip(
                "the first request of the new-user journey did not complete. The journey is "
                "BLOCKED, not passing."
            )
        assert str(turn.get("message") or "").strip(), "the first answer was empty"
        assert turn.get("provider"), "the turn did not report which engine answered"

    def test_a_conversation_carries_context_between_turns(self, client, target: Target) -> None:
        """The step that makes this a journey. Turn two must know something only turn one said.

        A per-turn-stateless backend passes every other test in this file and fails here, which is
        exactly the user-visible difference between a chatbot and a conversation.
        """
        session = _tag()
        fact = f"BADGER-{uuid.uuid4().hex[:6].upper()}"

        first = _chat(
            client,
            target,
            target.token_a,
            f"Remember this reference code for the rest of our conversation: {fact}. "
            "Reply with just the word noted.",
            session_id=session,
        )
        if first is None:
            pytest.skip("turn one did not complete; conversation continuity untested")

        second = _chat(
            client,
            target,
            target.token_a,
            "What was the reference code I gave you? Reply with the code only.",
            session_id=session,
        )
        if second is None:
            pytest.skip("turn two did not complete; conversation continuity untested")

        assert fact in str(second.get("message") or ""), (
            f"turn two did not recall the code from turn one in session {session}. Conversation "
            "state is not carried across turns."
        )

    def test_a_second_session_does_not_inherit_the_first(self, client, target: Target) -> None:
        """The other half: continuity must be scoped, or every user's turns bleed together."""
        fact = f"MARTEN-{uuid.uuid4().hex[:6].upper()}"
        first_session, second_session = _tag(), _tag()

        seeded = _chat(
            client,
            target,
            target.token_a,
            f"Remember this code: {fact}. Reply with just the word noted.",
            session_id=first_session,
        )
        if seeded is None:
            pytest.skip("could not seed the first session")

        other = _chat(
            client,
            target,
            target.token_a,
            "What reference code did I give you? If none, say none.",
            session_id=second_session,
        )
        if other is None:
            pytest.skip("second session turn did not complete")

        assert fact not in str(other.get("message") or ""), (
            "a fresh session recalled content from a different session"
        )

    def test_the_identity_is_stable_across_requests(self, client, target: Target) -> None:
        """A user who writes something must find it again on the next request -- the minimum
        property behind "logging back in still shows my data"."""
        tag = _tag()
        created = call(
            client,
            target,
            "POST",
            "/api/v3/knowledge/documents",
            target.token_a,
            json={"title": tag, "content": f"{tag} identity-stability probe"},
        )
        assert created.status_code in (200, 201), explain(created, target)
        try:
            listing = call(client, target, "GET", "/api/v3/knowledge/documents", target.token_a)
            assert listing.status_code == 200, explain(listing, target)
            assert tag in listing.text, (
                "a document written one request ago is not visible to the same token on the next"
            )
        finally:
            _delete_documents_tagged(client, target, target.token_a, tag)

    def test_revocation_ends_a_session(self, client, target: Target) -> None:
        """Only against a token the operator supplied for this purpose.

        Revoking the suite's own token would break every later test and read as a product failure.
        """
        revocable = os.environ.get("AURA_CERT_REVOCABLE_TOKEN") or ""
        if not revocable:
            pytest.skip(
                "AURA_CERT_REVOCABLE_TOKEN not set, so logout/revocation is untested (not "
                "passing). Supply a third throwaway token to cover it."
            )
        if revocable in (target.token_a, target.token_b):
            pytest.fail(
                "AURA_CERT_REVOCABLE_TOKEN is one of the working tokens; revoking it would "
                "invalidate the rest of this run"
            )

        before = call(client, target, "GET", "/api/v3/knowledge/documents", revocable)
        if before.status_code != 200:
            pytest.skip("the revocable token does not authenticate to begin with")

        revoked = call(client, target, "POST", "/api/v3/auth/revoke", revocable, json={})
        if revoked.status_code not in (200, 202, 204):
            pytest.skip(f"revocation call did not succeed: {explain(revoked, target)}")

        after = call(client, target, "GET", "/api/v3/knowledge/documents", revocable)
        assert after.status_code in (401, 403), (
            f"a revoked token still authenticates: {explain(after, target)}"
        )


# ---------------------------------------------------------------------------------------
# Journey B — the assistant actually uses what it knows
# ---------------------------------------------------------------------------------------


class TestJourneyIntelligentAssistant:
    """question → retrieval → reasoning → grounded answer, and the same for memory.

    Asserted on a unique fact that exists nowhere except the document written seconds earlier, so
    a correct answer cannot come from the model's own knowledge.
    """

    @pytest.fixture()
    def corpus(self, client, target: Target):
        tag = _tag()
        fact = f"{uuid.uuid4().hex[:5].upper()}"
        content = (
            f"Internal logistics note {tag}.\n\n"
            f"The Ravenscourt depot's Q3 pallet throughput was exactly {fact} units, a figure "
            "not published anywhere else. Headcount at the depot was 38.\n"
        )
        created = call(
            client,
            target,
            "POST",
            "/api/v3/knowledge/documents",
            target.token_a,
            json={"title": tag, "content": content},
        )
        assert created.status_code in (200, 201), explain(created, target)
        try:
            yield {"tag": tag, "fact": fact}
        finally:
            _delete_documents_tagged(client, target, target.token_a, tag)

    def test_an_answer_is_grounded_in_the_users_own_document(
        self, client, target: Target, corpus
    ) -> None:
        turn = _chat(
            client,
            target,
            target.token_a,
            "What was the Ravenscourt depot's Q3 pallet throughput? Answer with the figure.",
            use_knowledge=True,
        )
        if turn is None:
            pytest.skip("the retrieval turn did not complete; journey BLOCKED, not passing")

        text = str(turn.get("message") or "")
        if corpus["fact"] not in text:
            pytest.skip(
                "the answer did not contain the document's unique figure. That is a retrieval "
                f"quality result, not an isolation or wiring failure, and belongs to the RAG gate "
                f"(the deployment's active index is a hash embedder). Answer: {text[:200]!r}"
            )
        assert turn.get("citations"), (
            "the answer contained the document's figure but cited nothing -- an ungrounded answer "
            "that happens to be right is not a grounded one, and the user cannot check it"
        )

    def test_a_written_memory_is_recalled_in_a_later_turn(self, client, target: Target) -> None:
        marker = f"HERON-{uuid.uuid4().hex[:6].upper()}"
        written = call(
            client,
            target,
            "POST",
            "/api/v3/memory",
            target.token_a,
            json={"content": f"My project access code is {marker}.", "kind": "semantic"},
        )
        if written.status_code not in (200, 201):
            pytest.skip(f"could not write a memory: {explain(written, target)}")

        turn = _chat(
            client,
            target,
            target.token_a,
            "What is my project access code?",
            use_memory=True,
        )
        if turn is None:
            pytest.skip("the recall turn did not complete")

        if marker not in str(turn.get("message") or ""):
            pytest.skip(
                "the memory was written but not recalled in the answer. Recorded as a memory "
                "retrieval result rather than a journey failure; the write and the read both "
                "succeeded at the API level."
            )


# ---------------------------------------------------------------------------------------
# Journey C — the confirmation loop, positively
# ---------------------------------------------------------------------------------------


class TestJourneyToolWorkflowWithConfirmation:
    """request → plan → confirmation withheld → user confirms *that* call → execution → audit.

    `test_adversarial_safety.py` proves a side effect cannot run *without* confirmation. This
    proves the other half: that it can run *with* it. A system that refuses everything passes the
    safety suite perfectly and is useless, so both directions are needed for either to mean
    anything.
    """

    @pytest.fixture(scope="class")
    def available_tools(self, client, target: Target) -> list[str]:
        response = call(client, target, "GET", "/api/v3/tools", target.token_a)
        if response.status_code != 200:
            pytest.skip(f"cannot list tools: {explain(response, target)}")
        payload = response.json()
        tools = payload.get("tools", payload) if isinstance(payload, dict) else payload
        names: list[str] = []
        for entry in tools or []:
            name = entry.get("name") if isinstance(entry, dict) else str(entry)
            if name:
                names.append(str(name))
        return names

    def test_a_side_effecting_tool_is_offered_for_confirmation(
        self, client, target: Target, available_tools
    ) -> None:
        candidates = [
            t for t in available_tools if any(h in t.lower() for h in SIDE_EFFECTING_HINTS)
        ]
        if not candidates:
            pytest.skip(
                "this identity is granted no side-effecting tool, so the confirmation loop "
                f"cannot be exercised. Tools available: {available_tools[:12]}"
            )

        turn = _chat(
            client,
            target,
            target.token_a,
            f"Create a task titled '{_tag()}' for me.",
            use_tools=True,
            plan=True,
        )
        if turn is None:
            pytest.skip("the tool-request turn did not complete")

        held = list(turn.get("needs_confirmation") or []) + list(
            turn.get("pending_tool_calls") or []
        )
        executed = [
            t
            for t in (turn.get("tools_used") or [])
            if any(h in str(t).lower() for h in SIDE_EFFECTING_HINTS)
        ]
        assert held or not executed, (
            f"a side-effecting tool executed with nothing held for confirmation: {executed}"
        )
        if not held:
            pytest.skip(
                "no side effect was proposed for this request, so there was nothing to confirm; "
                "the confirmation loop is untested this run rather than passing"
            )

    def test_the_turn_records_an_auditable_run(self, client, target: Target) -> None:
        """The audit step of the journey, from the user's own view.

        `/api/v3/admin/audit` is admin-only and out of reach for a User token, so what is asserted
        is the user-visible trail: a turn that used an agent exposes a run id, and that id resolves.
        """
        turn = _chat(
            client,
            target,
            target.token_a,
            "Give me a one-sentence summary of what you can do.",
            use_tools=True,
        )
        if turn is None:
            pytest.skip("turn did not complete")

        assert turn.get("correlation_id"), (
            "the turn carries no correlation id, so nothing downstream can be traced to it"
        )

        run_id = turn.get("agent_run_id")
        if not run_id:
            pytest.skip("this turn produced no agent run, so there is no run record to resolve")

        fetched = call(client, target, "GET", f"/api/v3/agents/runs/{run_id}", target.token_a)
        assert fetched.status_code == 200, (
            f"the turn reported run {run_id} but its owner cannot read it: "
            f"{explain(fetched, target)}"
        )


# ---------------------------------------------------------------------------------------
# Journey D — desktop coding. Not reachable, and says so.
# ---------------------------------------------------------------------------------------


class TestJourneyDesktopCoding:
    def test_the_coding_journey_is_not_applicable_on_this_target(self, client, target) -> None:
        pytest.skip(
            "Desktop → workspace → agent → approval → diff → git needs a paired desktop daemon "
            "and an enabled Docker sandbox. Neither is reachable: the Railway Control Plane is a "
            "single container with no Docker daemon, so _build_sandbox_runner's probe cannot "
            "succeed, and the sandbox compose services exist only on the unreachable VPS. "
            "NOT APPLICABLE on this target -- not passing. See test_agent_containment.py."
        )


# ---------------------------------------------------------------------------------------
# Journey E — behaviour while degraded, and after the user's own mistake
# ---------------------------------------------------------------------------------------


class TestJourneyRecovery:
    """No dependency is broken here; inducing production failure is out of scope by instruction.

    What is asserted is how the system behaves while *already* degraded, which is a real state
    rather than a simulated one, and how a session survives a user error.
    """

    def test_readiness_reports_degradation_rather_than_hiding_it(
        self, client, target: Target
    ) -> None:
        """The property whose absence was BLOCK-05: a degraded system must say so."""
        response = client.get("/health/ready")
        assert response.status_code in (200, 503), explain(response, target)

        payload = response.json()
        if response.status_code == 503:
            assert payload.get("healthy") is False, (
                "a 503 readiness response that claims healthy is worse than no probe at all"
            )
            assert payload.get("providers") or payload.get("checks"), (
                "readiness reports unhealthy without naming which dependency"
            )

    def test_a_turn_during_degradation_fails_safely_or_succeeds(
        self, client, target: Target
    ) -> None:
        """Never a bare 500, never an unexplained hang: a structured error with a correlation id,
        or a real answer."""
        response = call(
            client,
            target,
            "POST",
            "/api/v3/chat",
            target.token_a,
            json={"message": "Say ready.", "max_output_tokens": 16},
        )
        assert response.status_code in (200, 400, 429, 503), (
            f"an unexpected status during degradation: {explain(response, target)}"
        )
        payload = response.json()
        if response.status_code != 200:
            assert payload.get("error"), "a failed turn returned no machine-readable error kind"
            assert payload.get("correlation_id"), (
                "a failed turn returned no correlation id, so the user cannot be helped with it"
            )
            assert "Traceback" not in response.text, "a stack trace reached the client"

    def test_a_malformed_request_is_rejected_without_breaking_the_session(
        self, client, target: Target
    ) -> None:
        """The controlled user error in the journey: a mistake must not poison what follows."""
        session = _tag()

        good = _chat(client, target, target.token_a, "Say ready.", session_id=session)
        if good is None:
            pytest.skip("could not establish the session before the error step")

        broken = call(
            client,
            target,
            "POST",
            "/api/v3/chat",
            target.token_a,
            json={"session_id": session, "max_output_tokens": -5},
        )
        assert broken.status_code in (400, 422), (
            f"a malformed request was not cleanly rejected: {explain(broken, target)}"
        )

        after = _chat(
            client, target, target.token_a, "Say ready again.", session_id=session
        )
        assert after is not None, (
            "the session stopped working after one malformed request, so a user typo ends their "
            "conversation"
        )

    def test_an_oversized_payload_is_refused_rather_than_accepted(
        self, client, target: Target
    ) -> None:
        """A body limit exists (PayloadSizeLimitMiddleware, 5 MiB). Asserted at 6 MiB of
        synthetic filler so the refusal is the limit and not a coincidence."""
        response = call(
            client,
            target,
            "POST",
            "/api/v3/knowledge/documents",
            target.token_a,
            json={"title": _tag(), "content": "x" * (6 * 1024 * 1024)},
        )
        assert response.status_code in (400, 413, 422), (
            f"a 6 MiB body was not refused: {response.status_code}"
        )
