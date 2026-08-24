"""The trigger engine, its default specs, and the durable-event decode registry (Phase E2).

``document_quarantined`` is exercised as a full pipeline (encode -> enqueue -> claim -> decode ->
handle -> surface) in ``tests/unit/test_kernel_goals.py``-style container tests below; this file
also pins the lower-level contracts: dispatch matching, per-trigger failure isolation, and the
decoder/durable-type lockstep that keeps a durable event type from silently becoming
undeliverable.
"""

from __future__ import annotations

from aura_core.domain.events import DocumentIngested, DomainEvent, TurnFailed
from aura_core.domain.identifiers import DocumentId, UserId
from aura_core.domain.outbox import OutboxEntry
from aura_core.learning import run_outbox_drain
from aura_core.triggers import DEFAULT_TRIGGER_SPECS, DURABLE_EVENT_TYPES, decode_durable_event
from aura_core.triggers.engine import TriggerContext, TriggerEngine, TriggerSpec


class _FakeInsightSink:
    def __init__(self) -> None:
        self.surfaced: list[dict] = []

    async def surface(self, owner_id, *, insight_id, kind, message, explanation) -> None:
        self.surfaced.append({
            "owner_id": owner_id, "insight_id": insight_id, "kind": kind,
            "message": message, "explanation": explanation,
        })


def _quarantined_event(**overrides) -> DocumentIngested:
    base = dict(
        document_id=DocumentId("doc1"), version=1, chunks=0, source_type="upload",
        owner_id=UserId("u1"), quarantined=True,
    )
    base.update(overrides)
    return DocumentIngested(**base)


class TestTriggerEngineDispatch:
    async def test_matching_event_and_predicate_fires_the_action(self) -> None:
        fired = []

        async def action(event, context):
            fired.append(event)

        engine = TriggerEngine((
            TriggerSpec(
                name="t1", event_types=(DocumentIngested,), predicate=lambda e: True, action=action,
            ),
        ))
        event = _quarantined_event()
        names = await engine.handle(event, context=TriggerContext(insight_sink=_FakeInsightSink()))
        assert names == ["t1"]
        assert fired == [event]

    async def test_non_matching_event_type_does_not_fire(self) -> None:
        fired = []

        async def action(event, context):
            fired.append(event)

        engine = TriggerEngine((
            TriggerSpec(name="t1", event_types=(DocumentIngested,), predicate=lambda e: True, action=action),
        ))
        names = await engine.handle(
            TurnFailed(error_kind="x", stage="y"), context=TriggerContext(insight_sink=_FakeInsightSink())
        )
        assert names == []
        assert fired == []

    async def test_false_predicate_does_not_fire(self) -> None:
        fired = []

        async def action(event, context):
            fired.append(event)

        engine = TriggerEngine((
            TriggerSpec(
                name="t1", event_types=(DocumentIngested,), predicate=lambda e: e.quarantined,
                action=action,
            ),
        ))
        names = await engine.handle(
            _quarantined_event(quarantined=False), context=TriggerContext(insight_sink=_FakeInsightSink())
        )
        assert names == []

    async def test_one_broken_trigger_does_not_stop_the_others(self) -> None:
        fired = []

        async def broken_action(event, context):
            raise RuntimeError("boom")

        async def good_action(event, context):
            fired.append(event)

        engine = TriggerEngine((
            TriggerSpec(name="broken", event_types=(DocumentIngested,), predicate=lambda e: True, action=broken_action),
            TriggerSpec(name="good", event_types=(DocumentIngested,), predicate=lambda e: True, action=good_action),
        ))
        event = _quarantined_event()
        names = await engine.handle(event, context=TriggerContext(insight_sink=_FakeInsightSink()))
        assert names == ["good"]
        assert fired == [event]

    async def test_a_broken_predicate_is_treated_as_not_matching(self) -> None:
        def broken_predicate(event: DomainEvent) -> bool:
            raise ValueError("boom")

        fired = []

        async def action(event, context):
            fired.append(event)

        engine = TriggerEngine((
            TriggerSpec(name="t1", event_types=(DocumentIngested,), predicate=broken_predicate, action=action),
        ))
        names = await engine.handle(
            _quarantined_event(), context=TriggerContext(insight_sink=_FakeInsightSink())
        )
        assert names == []
        assert fired == []


class TestDocumentQuarantinedTrigger:
    async def test_fires_and_surfaces_an_insight_for_a_quarantined_document(self) -> None:
        engine = TriggerEngine(DEFAULT_TRIGGER_SPECS)
        sink = _FakeInsightSink()
        event = _quarantined_event(chunks=0)
        names = await engine.handle(event, context=TriggerContext(insight_sink=sink))
        assert names == ["document_quarantined"]
        assert len(sink.surfaced) == 1
        assert sink.surfaced[0]["kind"] == "document_quarantined"
        assert sink.surfaced[0]["owner_id"] == UserId("u1")

    async def test_does_not_fire_for_a_clean_ingestion(self) -> None:
        engine = TriggerEngine(DEFAULT_TRIGGER_SPECS)
        sink = _FakeInsightSink()
        names = await engine.handle(
            _quarantined_event(quarantined=False), context=TriggerContext(insight_sink=sink)
        )
        assert names == []
        assert sink.surfaced == []

    async def test_no_owner_means_no_insight_but_no_crash(self) -> None:
        engine = TriggerEngine(DEFAULT_TRIGGER_SPECS)
        sink = _FakeInsightSink()
        names = await engine.handle(
            _quarantined_event(owner_id=None), context=TriggerContext(insight_sink=sink)
        )
        assert names == ["document_quarantined"]
        assert sink.surfaced == []

    async def test_the_insight_id_is_deterministic_for_the_same_document_and_version(self) -> None:
        engine = TriggerEngine(DEFAULT_TRIGGER_SPECS)
        sink = _FakeInsightSink()
        await engine.handle(_quarantined_event(), context=TriggerContext(insight_sink=sink))
        await engine.handle(_quarantined_event(), context=TriggerContext(insight_sink=sink))
        ids = {row["insight_id"] for row in sink.surfaced}
        assert len(ids) == 1  # a redelivered event must not mint a new insight id


class TestDurableDecodeRegistry:
    def test_every_durable_event_type_has_a_decoder(self) -> None:
        """A durable type with no decoder would enqueue successfully and then fail every drain
        attempt forever -- this is the fitness check that prevents that drift."""
        for event_type in DURABLE_EVENT_TYPES:
            event = _quarantined_event() if event_type is DocumentIngested else None
            assert event is not None, f"add a construction case for {event_type.__name__}"
            entry = OutboxEntry(
                id="x", event_name=event.name, payload=event.as_dict(),
                created_at=event.occurred_at, attempts=0,
            )
            decoded = decode_durable_event(entry)
            assert isinstance(decoded, event_type)

    def test_an_unregistered_event_name_raises(self) -> None:
        entry = OutboxEntry(id="x", event_name="NotARealEvent", payload={}, created_at=None, attempts=0)
        try:
            decode_durable_event(entry)
        except ValueError as exc:
            assert "NotARealEvent" in str(exc)
        else:
            raise AssertionError("expected ValueError")

    def test_document_ingested_round_trips_through_as_dict(self) -> None:
        original = _quarantined_event(chunks=3, version=2)
        entry = OutboxEntry(
            id="x", event_name=original.name, payload=original.as_dict(),
            created_at=original.occurred_at, attempts=0,
        )
        decoded = decode_durable_event(entry)
        assert isinstance(decoded, DocumentIngested)
        assert decoded.document_id == original.document_id
        assert decoded.owner_id == original.owner_id
        assert decoded.chunks == 3
        assert decoded.version == 2
        assert decoded.quarantined is True


class TestContainerWiring:
    """The whole pipeline through the object graph the same way production does it: publish
    through ``container.events`` (a real ``DurableEventBus``), drain through
    ``run_outbox_drain``, land in ``container.insight_sink`` (in-memory backend, same code path
    ``PostgresInsightSink`` exercised for real against Postgres for this phase)."""

    async def test_a_quarantined_document_ends_up_as_a_surfaced_insight(self, container) -> None:
        await container.startup()
        event = _quarantined_event()

        await container.events.publish(event)
        assert len(container.event_outbox) == 1

        result = await run_outbox_drain(
            container.event_outbox, decode_durable_event,
            lambda e: container.triggers.handle(e, context=container.trigger_context),
        )
        assert result["processed"] == 1 and result["failed"] == 0
        assert len(container.insight_sink.surfaced) == 1
        assert container.insight_sink.surfaced[0]["kind"] == "document_quarantined"

    async def test_a_clean_ingestion_still_drains_but_surfaces_nothing(self, container) -> None:
        await container.startup()
        await container.events.publish(_quarantined_event(quarantined=False))

        result = await run_outbox_drain(
            container.event_outbox, decode_durable_event,
            lambda e: container.triggers.handle(e, context=container.trigger_context),
        )
        assert result["processed"] == 1
        assert container.insight_sink.surfaced == []

    async def test_non_durable_events_still_reach_the_trigger_engine_live(self, container) -> None:
        """DocumentIngested is durable-only in this phase's scope, but the trigger engine is
        wired onto the *live* inner bus too -- this proves that path is not dead code, just
        currently unused by any shipped trigger."""
        await container.startup()
        fired = []

        async def action(event, context):
            fired.append(event)

        container.triggers._specs = container.triggers._specs + (
            TriggerSpec(
                name="live_test", event_types=(TurnFailed,), predicate=lambda e: True, action=action,
            ),
        )
        await container.events.publish(TurnFailed(error_kind="x", stage="y"))
        assert len(fired) == 1
