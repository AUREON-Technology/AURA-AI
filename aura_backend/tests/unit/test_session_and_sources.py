"""Session memory and the approved-source registry.

Both modules exist to make an unattended process safe. Session state is cached data a user does
not control the key of, so ownership is re-checked on every load. The source registry is the
*only* thing the 02:00 job may fetch from, which is what keeps a scheduled job with the
deployment's network position from being a server-side request forgery primitive.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from aura_core.domain.errors import AuthorizationError, NotFoundError, ValidationError
from aura_core.domain.identifiers import DocumentId, SessionId, UserId
from aura_core.domain.knowledge import KnowledgeSource, SourceType, TrustTier
from aura_core.domain.messages import Message, Role
from aura_core.learning.sources import (
    CompositeFetcher,
    FetchedDocument,
    HttpSourceFetcher,
    InMemorySourceRegistry,
    StaticSourceFetcher,
    gather_sources,
)
from aura_core.llm import InMemoryCache
from aura_core.memory.session import (
    EpisodicMemory,
    SessionState,
    WorkingMemory,
    build_session_summariser,
    merge_context,
)
from aura_core.prompts import build_default_registry


class TestWorkingMemory:
    def test_notes_are_bounded_keeping_the_most_recent(self) -> None:
        """An agent loop appending a note per step would otherwise exhaust the context budget
        with its own bookkeeping."""
        working = WorkingMemory(max_notes=3)
        for i in range(10):
            working.note(f"note {i}")

        assert working.as_context() == ["note 7", "note 8", "note 9"]

    def test_blank_notes_are_ignored(self) -> None:
        working = WorkingMemory()
        working.note("   ")
        working.note("")
        assert not working.notes

    def test_notes_are_stripped(self) -> None:
        working = WorkingMemory()
        working.note("  a note  ")
        assert working.notes == ["a note"]

    def test_tool_results_are_bounded_independently(self) -> None:
        working = WorkingMemory(max_tool_results=2)
        for i in range(5):
            working.record_tool(f"tool{i}", f"result {i}")
        assert [name for name, _ in working.tool_results] == ["tool3", "tool4"]

    def test_facts_round_trip_with_a_default(self) -> None:
        working = WorkingMemory()
        working.set("city", "Cairo")
        assert working.get("city") == "Cairo"
        assert working.get("absent", "fallback") == "fallback"

    def test_clear_empties_everything(self) -> None:
        working = WorkingMemory()
        working.note("n")
        working.set("k", "v")
        working.record_tool("t", "r")
        working.clear()
        assert not working

    def test_truthiness_reflects_whether_anything_was_recorded(self) -> None:
        assert not WorkingMemory()
        populated = WorkingMemory()
        populated.set("k", "v")
        assert populated


class TestSessionStateSerialisation:
    def _state(self, **overrides) -> SessionState:
        base = dict(
            session_id=SessionId("s1"),
            owner_id=UserId("u1"),
            messages=(Message.user("hello"), Message.assistant("hi")),
            summary="earlier: greetings",
            turn_count=3,
            updated_at=1234.5,
        )
        base.update(overrides)
        return SessionState(**base)

    def test_round_trip(self) -> None:
        restored = SessionState.from_json(self._state().to_json())
        assert restored.session_id == SessionId("s1")
        assert restored.owner_id == UserId("u1")
        assert restored.summary == "earlier: greetings"
        assert restored.turn_count == 3
        assert [m.content for m in restored.messages] == ["hello", "hi"]

    def test_system_messages_are_not_persisted(self) -> None:
        """Persisting them would pin a session to a stale prompt version forever."""
        state = self._state(messages=(
            Message.system("You are AURA v1."), Message.user("hello"),
        ))
        restored = SessionState.from_json(state.to_json())
        assert all(m.role is not Role.SYSTEM for m in restored.messages)

    def test_corrupt_state_is_a_cold_start_not_an_exception(self) -> None:
        """A bad cache entry must not make a user's session permanently unusable."""
        assert SessionState.from_json(b"{{{ not json") is None
        assert SessionState.from_json(json.dumps({"missing": "fields"})) is None

    def test_bytes_and_str_are_both_accepted(self) -> None:
        raw = self._state().to_json()
        assert SessionState.from_json(raw.encode("utf-8")) is not None


class TestEpisodicMemory:
    @pytest.fixture
    def episodic(self) -> EpisodicMemory:
        return EpisodicMemory(cache=InMemoryCache(), window=2, summarise_after=4)

    async def test_an_unknown_session_starts_empty(self, episodic) -> None:
        state = await episodic.load(SessionId("new"), UserId("u1"))
        assert state.messages == ()
        assert state.turn_count == 0

    async def test_an_exchange_is_recorded_and_reloaded(self, episodic) -> None:
        await episodic.append(
            SessionId("s1"), UserId("u1"),
            user_message="what is my leave?", assistant_message="21 days.",
        )
        conversation, summary = await episodic.history(SessionId("s1"), UserId("u1"))

        assert [m.content for m in conversation] == ["what is my leave?", "21 days."]
        assert summary == ""

    async def test_turn_count_accumulates(self, episodic) -> None:
        for i in range(3):
            state = await episodic.append(
                SessionId("s1"), UserId("u1"), user_message=f"q{i}", assistant_message=f"a{i}"
            )
        assert state.turn_count == 3

    async def test_overflow_is_summarised_and_the_window_trimmed(self, episodic) -> None:
        for i in range(5):
            state = await episodic.append(
                SessionId("s1"), UserId("u1"), user_message=f"q{i}", assistant_message=f"a{i}"
            )

        assert len(state.messages) == 2
        assert state.summary  # the trimmed turns are not simply lost
        assert "q0" in state.summary

    async def test_a_session_id_is_not_a_capability(self, episodic) -> None:
        """Serving another user's conversation because a guessed id landed in the cache would be
        a direct data leak."""
        await episodic.append(
            SessionId("s1"), UserId("alice"),
            user_message="my salary is 95000", assistant_message="noted",
        )
        state = await episodic.load(SessionId("s1"), UserId("mallory"))

        assert state.messages == ()
        assert state.owner_id == UserId("mallory")

    async def test_a_foreign_write_does_not_replace_the_owners_state(self, episodic) -> None:
        """Reading was owner-checked; writing was not -- mallory's append on alice's session id
        overwrote alice's history under the shared key."""
        await episodic.append(
            SessionId("s1"), UserId("alice"), user_message="my salary is 95000",
            assistant_message="noted",
        )
        await episodic.append(
            SessionId("s1"), UserId("mallory"), user_message="hi", assistant_message="hello",
        )
        state = await episodic.load(SessionId("s1"), UserId("alice"))
        assert [m.content for m in state.messages] == ["my salary is 95000", "noted"]

    async def test_legacy_unnamespaced_state_is_still_read_by_its_owner(self, episodic) -> None:
        from aura_core.memory.session import SessionState

        legacy = SessionState(session_id=SessionId("s1"), owner_id=UserId("alice"), turn_count=4)
        await episodic._cache.set(episodic._key(SessionId("s1")), legacy.to_json().encode())

        assert (await episodic.load(SessionId("s1"), UserId("alice"))).turn_count == 4
        assert (await episodic.load(SessionId("s1"), UserId("mallory"))).turn_count == 0

    async def test_clearing_is_owner_scoped(self, episodic) -> None:
        await episodic.append(
            SessionId("s1"), UserId("alice"), user_message="q", assistant_message="a"
        )
        await episodic.clear(SessionId("s1"), UserId("mallory"))
        assert (await episodic.load(SessionId("s1"), UserId("alice"))).messages != ()

    async def test_clearing_removes_the_state(self, episodic) -> None:
        await episodic.append(
            SessionId("s1"), UserId("u1"), user_message="q", assistant_message="a"
        )
        await episodic.clear(SessionId("s1"), UserId("u1"))
        assert (await episodic.load(SessionId("s1"), UserId("u1"))).messages == ()

    async def test_a_configured_summariser_is_used(self) -> None:
        calls: list[str] = []

        async def summariser(transcript: str, previous: str) -> str:
            calls.append(transcript)
            return "MODEL SUMMARY"

        episodic = EpisodicMemory(
            cache=InMemoryCache(), window=2, summarise_after=4, summariser=summariser
        )
        for i in range(5):
            state = await episodic.append(
                SessionId("s1"), UserId("u1"), user_message=f"q{i}", assistant_message=f"a{i}"
            )

        assert state.summary == "MODEL SUMMARY"
        assert "q0" in calls[0]

    async def test_an_append_during_summarisation_is_not_lost(self) -> None:
        """F-16: append loaded the state, awaited the model for the summary, then wrote the
        state it had loaded -- so any exchange appended while the model was thinking (a second
        device, a retried request) was overwritten and gone."""
        import asyncio

        started, release = asyncio.Event(), asyncio.Event()

        async def slow(transcript: str, previous: str) -> str:
            started.set()
            await release.wait()
            return "SUMMARY"

        episodic = EpisodicMemory(
            cache=InMemoryCache(), window=2, summarise_after=4, summariser=slow
        )
        sid, uid = SessionId("s1"), UserId("u1")
        await episodic.append(sid, uid, user_message="q0", assistant_message="a0")
        await episodic.append(sid, uid, user_message="q1", assistant_message="a1")

        summarising = asyncio.create_task(
            episodic.append(sid, uid, user_message="q2", assistant_message="a2")
        )
        await asyncio.wait_for(started.wait(), 1)
        # The model is slow; a concurrent turn must not wait for it, nor be erased by it.
        await asyncio.wait_for(
            episodic.append(sid, uid, user_message="q3", assistant_message="a3"), 1
        )
        release.set()
        await summarising

        state = await episodic.load(sid, uid)
        contents = [m.content for m in state.messages]
        assert contents[-2:] == ["q3", "a3"], "a concurrent append was lost"
        assert "q2" in contents
        assert state.summary == "SUMMARY"
        assert "q0" not in contents  # the summarised turns were folded, not kept twice
        assert state.turn_count == 4

    async def test_a_summary_is_not_applied_over_a_cleared_session(self) -> None:
        """Clearing while the model summarises must not bring the old turns back."""
        import asyncio

        started, release = asyncio.Event(), asyncio.Event()

        async def slow(transcript: str, previous: str) -> str:
            started.set()
            await release.wait()
            return "SUMMARY OF THE OLD CONVERSATION"

        episodic = EpisodicMemory(
            cache=InMemoryCache(), window=2, summarise_after=4, summariser=slow
        )
        sid, uid = SessionId("s1"), UserId("u1")
        for i in range(2):
            await episodic.append(sid, uid, user_message=f"q{i}", assistant_message=f"a{i}")
        summarising = asyncio.create_task(
            episodic.append(sid, uid, user_message="q2", assistant_message="a2")
        )
        await asyncio.wait_for(started.wait(), 1)
        await episodic.clear(sid, uid)
        release.set()
        await summarising

        state = await episodic.load(sid, uid)
        assert state.messages == ()
        assert state.summary == ""

    @pytest.mark.parametrize("first", ["a", "b"])
    async def test_two_workers_summarising_at_once_apply_exactly_one(self, first) -> None:
        """Two workers (two instances over one shared cache, as two processes over Redis) can
        both see the overflow and both summarise. Whichever applies first wins; the other's
        summary was computed against state that no longer exists, so it is discarded rather
        than folding the same turns in twice or dropping the winner's trim."""
        import asyncio

        cache = InMemoryCache()
        gates = {"a": asyncio.Event(), "b": asyncio.Event()}
        started = {"a": asyncio.Event(), "b": asyncio.Event()}

        def summariser(name: str):
            async def summarise(transcript: str, previous: str) -> str:
                started[name].set()
                await gates[name].wait()
                return f"SUMMARY-{name.upper()}"
            return summarise

        workers = {
            name: EpisodicMemory(cache=cache, window=2, summarise_after=4,
                                 summariser=summariser(name))
            for name in ("a", "b")
        }
        sid, uid = SessionId("s1"), UserId("u1")
        for i in range(2):
            await workers["a"].append(sid, uid, user_message=f"q{i}", assistant_message=f"a{i}")

        task_a = asyncio.create_task(
            workers["a"].append(sid, uid, user_message="q2", assistant_message="a2"))
        await asyncio.wait_for(started["a"].wait(), 1)
        task_b = asyncio.create_task(
            workers["b"].append(sid, uid, user_message="q3", assistant_message="a3"))
        await asyncio.wait_for(started["b"].wait(), 1)

        second = "b" if first == "a" else "a"
        gates[first].set()
        await (task_a if first == "a" else task_b)
        gates[second].set()
        await asyncio.gather(task_a, task_b)

        state = await workers["a"].load(sid, uid)
        contents = [m.content for m in state.messages]
        assert state.summary == f"SUMMARY-{first.upper()}"
        assert contents[-2:] == ["q3", "a3"]
        assert "q0" not in contents and "a1" not in contents
        assert state.turn_count == 4
        assert state.folded + len(state.messages) == 8  # every message: folded once or kept

    async def test_a_lease_held_by_another_worker_is_waited_for(self) -> None:
        import asyncio

        cache = InMemoryCache()
        episodic = EpisodicMemory(cache=cache, window=2, summarise_after=4)
        sid, uid = SessionId("s1"), UserId("u1")
        lease = f"{EpisodicMemory._LEASE_PREFIX}{episodic._key(sid, uid)}"
        assert await cache.incr(lease, ttl_s=10) == 1  # another worker is mid-write

        pending = asyncio.create_task(
            episodic.append(sid, uid, user_message="q", assistant_message="a"))
        await asyncio.sleep(0.05)
        assert not pending.done()

        await cache.delete(lease)
        await asyncio.wait_for(pending, 1)
        assert (await episodic.load(sid, uid)).turn_count == 1
        assert await cache.get(lease) is None  # released after the write

    async def test_a_stuck_lease_delays_but_never_drops_an_exchange(self) -> None:
        cache = InMemoryCache()
        episodic = EpisodicMemory(cache=cache, window=2, summarise_after=4, lease_wait_s=0.05)
        sid, uid = SessionId("s1"), UserId("u1")
        await cache.incr(f"{EpisodicMemory._LEASE_PREFIX}{episodic._key(sid, uid)}", ttl_s=60)

        await episodic.append(sid, uid, user_message="q", assistant_message="a")
        assert (await episodic.load(sid, uid)).turn_count == 1

    async def test_a_cache_that_cannot_arbitrate_still_records_the_exchange(self) -> None:
        """Redis down: incr raises (it must, for the rate limiter); the append degrades to the
        in-process lock rather than failing the turn's persistence."""

        class NoIncr(InMemoryCache):
            async def incr(self, key, *, ttl_s=None):  # noqa: ANN001
                raise ConnectionError("redis is down")

        episodic = EpisodicMemory(cache=NoIncr(), window=2, summarise_after=4)
        sid, uid = SessionId("s1"), UserId("u1")
        await episodic.append(sid, uid, user_message="q", assistant_message="a")
        assert (await episodic.load(sid, uid)).turn_count == 1

    async def test_concurrent_appends_are_all_recorded(self) -> None:
        import asyncio

        episodic = EpisodicMemory(cache=InMemoryCache(), window=50, summarise_after=100)
        sid, uid = SessionId("s1"), UserId("u1")
        await asyncio.gather(*(
            episodic.append(sid, uid, user_message=f"q{i}", assistant_message=f"a{i}")
            for i in range(8)
        ))
        state = await episodic.load(sid, uid)
        assert state.turn_count == 8
        assert {m.content for m in state.messages} == (
            {f"q{i}" for i in range(8)} | {f"a{i}" for i in range(8)}
        )

    async def test_a_failing_summariser_falls_back_to_a_transcript(self) -> None:
        """Losing the conversation entirely is worse than keeping a crude record of it."""

        async def broken(transcript: str, previous: str) -> str:
            raise RuntimeError("model is down")

        episodic = EpisodicMemory(
            cache=InMemoryCache(), window=2, summarise_after=4, summariser=broken
        )
        for i in range(5):
            state = await episodic.append(
                SessionId("s1"), UserId("u1"), user_message=f"q{i}", assistant_message=f"a{i}"
            )
        assert "q0" in state.summary

    async def test_the_fallback_summary_is_bounded(self) -> None:
        episodic = EpisodicMemory(cache=InMemoryCache(), window=2, summarise_after=2)
        for i in range(40):
            state = await episodic.append(
                SessionId("s1"), UserId("u1"),
                user_message=f"question {i} " + "x" * 400, assistant_message="ok",
            )
        assert len(state.summary) <= 4000

    async def test_corrupt_cached_state_yields_a_fresh_session(self, episodic) -> None:
        await episodic._cache.set(episodic._key(SessionId("s1")), b"garbage")
        assert (await episodic.load(SessionId("s1"), UserId("u1"))).messages == ()


class TestSessionSummariser:
    async def test_it_generates_deterministically(self) -> None:
        """Sampling adds nothing to a compression task, and non-determinism would make a
        session's summary depend on when it happened."""
        captured: dict = {}

        class Router:
            async def complete(self, request):  # noqa: ANN001
                captured["temperature"] = request.sampling.temperature

                class Response:
                    text = "  A summary.  "

                return Response()

        summarise = build_session_summariser(Router(), build_default_registry())
        assert await summarise("user: hi\nassistant: hello", "") == "A summary."
        assert captured["temperature"] == 0.0


class TestMergeContext:
    def test_it_reports_shape_not_content(self) -> None:
        """Session context reaches logs and telemetry; message text must not."""
        state = SessionState(
            session_id=SessionId("s1"), owner_id=UserId("u1"),
            messages=(Message.user("my salary is 95000"),), summary="something",
        )
        payload = merge_context(state, {"route": "chat"})

        assert payload == {
            "turn_count": 0, "has_summary": True, "window_messages": 1, "route": "chat",
        }
        assert "95000" not in json.dumps(payload)


# --------------------------------------------------------------------------------------
# Source registry
# --------------------------------------------------------------------------------------


def _source(source_id: str = "handbook", **overrides) -> KnowledgeSource:
    base = dict(id=source_id, type=SourceType.INTERNAL, uri="https://example.com/handbook")
    base.update(overrides)
    return KnowledgeSource(**base)


class TestSourceRegistry:
    async def test_registration_and_lookup(self) -> None:
        registry = InMemorySourceRegistry()
        await registry.register(_source())

        assert (await registry.get("handbook")).uri == "https://example.com/handbook"
        assert len(await registry.list_all()) == 1

    async def test_duplicate_registration_is_refused(self) -> None:
        """Silently replacing would let a re-registration change a source's trust tier."""
        registry = InMemorySourceRegistry([_source()])
        with pytest.raises(ValidationError) as excinfo:
            await registry.register(_source())
        assert "already registered" in str(excinfo.value)

    async def test_only_enabled_sources_are_listed_for_the_job(self) -> None:
        registry = InMemorySourceRegistry([
            _source("a"), _source("b", enabled=False),
        ])
        assert [s.id for s in await registry.list_enabled()] == ["a"]

    async def test_disabling_is_reversible(self) -> None:
        registry = InMemorySourceRegistry([_source()])
        await registry.set_enabled("handbook", False)
        assert await registry.list_enabled() == []
        await registry.set_enabled("handbook", True)
        assert len(await registry.list_enabled()) == 1

    async def test_disabling_an_unknown_source_raises(self) -> None:
        with pytest.raises(NotFoundError):
            await InMemorySourceRegistry().set_enabled("nope", False)

    async def test_removal_reports_whether_anything_was_removed(self) -> None:
        registry = InMemorySourceRegistry([_source()])
        assert await registry.remove("handbook") is True
        assert await registry.remove("handbook") is False

    async def test_progress_is_recorded_for_incremental_fetches(self) -> None:
        registry = InMemorySourceRegistry([_source()])
        at = datetime(2026, 3, 1, 2, 0, tzinfo=timezone.utc)
        await registry.record_success("handbook", cursor="page-3", at=at)

        source = await registry.get("handbook")
        assert source.last_cursor == "page-3"
        assert source.last_success_at == at

    async def test_recording_progress_for_an_unknown_source_is_a_no_op(self) -> None:
        registry = InMemorySourceRegistry()
        await registry.record_success("nope", cursor=None, at=datetime.now(timezone.utc))

    async def test_an_unknown_source_is_none(self) -> None:
        assert await InMemorySourceRegistry().get("nope") is None


class TestSourceCeilings:
    async def test_positive_ceilings_are_required(self) -> None:
        with pytest.raises(ValueError):
            _source(max_documents=0)
        with pytest.raises(ValueError):
            _source(max_bytes_per_document=0)

    async def test_an_empty_id_is_refused(self) -> None:
        with pytest.raises(ValueError):
            _source("")


class TestHttpFetcherGuards:
    """The 02:00 job runs unattended with the deployment's network position."""

    def test_plain_http_is_refused(self) -> None:
        with pytest.raises(AuthorizationError):
            HttpSourceFetcher._assert_fetchable("http://example.com/doc")

    @pytest.mark.parametrize(
        "url",
        [
            "https://169.254.169.254/latest/meta-data/",
            "https://127.0.0.1/admin",
            "https://10.0.0.5/internal",
            "https://192.168.1.1/",
            "https://localhost/",
            "https://metadata.google.internal/",
        ],
    )
    def test_addresses_inside_the_deployment_are_refused(self, url: str) -> None:
        with pytest.raises(AuthorizationError):
            HttpSourceFetcher._assert_fetchable(url)

    def test_a_public_https_host_is_permitted(self) -> None:
        HttpSourceFetcher._assert_fetchable("https://docs.example.com/handbook")

    def test_a_public_ip_literal_is_permitted(self) -> None:
        HttpSourceFetcher._assert_fetchable("https://93.184.216.34/doc")

    def test_a_url_with_no_host_is_refused(self) -> None:
        with pytest.raises(AuthorizationError):
            HttpSourceFetcher._assert_fetchable("https:///path")


class TestFetcherDispatch:
    async def test_a_static_fetcher_returns_its_configured_documents(self) -> None:
        document = FetchedDocument(
            title="Handbook", content="Annual leave is 21 days.",
            document_id=DocumentId("handbook"), uri="static://handbook",
        )
        fetcher = StaticSourceFetcher({"handbook": [document]})
        assert (await fetcher.fetch(_source()))[0].title == "Handbook"

    async def test_an_unknown_static_source_yields_nothing(self) -> None:
        assert await StaticSourceFetcher({}).fetch(_source()) == []

    async def test_dispatch_selects_by_source_type(self) -> None:
        """Keeps the refresh pipeline free of `if source.type == ...` branches."""
        doc = FetchedDocument(
            title="T", content="C", document_id=DocumentId("d"), uri="u",
        )
        composite = CompositeFetcher({SourceType.INTERNAL: StaticSourceFetcher({"handbook": [doc]})})
        assert len(await composite.fetch(_source())) == 1

    async def test_an_unregistered_type_names_what_is_registered(self) -> None:
        composite = CompositeFetcher({})
        with pytest.raises(ValidationError) as excinfo:
            await composite.fetch(_source(type=SourceType.WEB))
        assert "registered" in excinfo.value.details

    async def test_a_default_fetcher_handles_unregistered_types(self) -> None:
        doc = FetchedDocument(title="T", content="C", document_id=DocumentId("d"), uri="u")
        composite = CompositeFetcher(
            {}, default=StaticSourceFetcher({"handbook": [doc]})
        )
        assert len(await composite.fetch(_source(type=SourceType.WEB))) == 1

    async def test_the_per_source_ceiling_is_enforced_centrally(self) -> None:
        """Enforced here rather than trusting each fetcher -- one place to get right."""
        docs = [
            FetchedDocument(title=f"T{i}", content="C", document_id=DocumentId(f"d{i}"), uri="u")
            for i in range(10)
        ]
        composite = CompositeFetcher({SourceType.INTERNAL: StaticSourceFetcher({"handbook": docs})})
        assert len(await composite.fetch(_source(max_documents=3))) == 3

    async def test_registering_a_fetcher_after_construction(self) -> None:
        doc = FetchedDocument(title="T", content="C", document_id=DocumentId("d"), uri="u")
        composite = CompositeFetcher({})
        composite.register(SourceType.INTERNAL, StaticSourceFetcher({"handbook": [doc]}))
        assert len(await composite.fetch(_source())) == 1


class TestGatherSources:
    async def test_every_enabled_source_is_fetched(self) -> None:
        docs = {
            "a": [FetchedDocument(title="A", content="C", document_id=DocumentId("a"), uri="u")],
            "b": [FetchedDocument(title="B", content="C", document_id=DocumentId("b"), uri="u")],
        }
        registry = InMemorySourceRegistry([_source("a"), _source("b"), _source("c", enabled=False)])
        result = await gather_sources(registry, StaticSourceFetcher(docs))

        assert set(result) == {"a", "b"}
        assert len(result["a"]) == 1

    async def test_one_failing_source_does_not_abort_the_run(self) -> None:
        """A single misbehaving source must not consume or fail the whole nightly run."""

        class PartlyBroken:
            async def fetch(self, source: KnowledgeSource):
                if source.id == "broken":
                    raise RuntimeError("upstream is down")
                return [FetchedDocument(title="ok", content="c",
                                        document_id=DocumentId(source.id), uri="u")]

        registry = InMemorySourceRegistry([_source("good"), _source("broken")])
        result = await gather_sources(registry, PartlyBroken())

        assert result["broken"] == []
        assert len(result["good"]) == 1

    async def test_an_empty_registry_gathers_nothing(self) -> None:
        assert await gather_sources(InMemorySourceRegistry(), StaticSourceFetcher({})) == {}
