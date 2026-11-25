"""A correction supersedes the stale fact only when it is about the same fact (audit B4 follow-up).

``MemoryEngine.remember`` treats a near-duplicate worded differently as the newer word on the fact,
unless the new write is less trusted. That rule ignored what the memory is *about*: "I also like
coffee" could delete "I like tea". The subject says what a record is about, so:

* the same words reinforce (unchanged);
* a near-duplicate about the **same subject** is a correction: the old record is superseded, not
  deleted, and stays in the store for audit;
* a new write with **no subject** inherits the old record's subject;
* anything else is a different fact: both records stay active.

The embedder here returns one fixed vector for every text, so any two memories are guaranteed to be
near-duplicates and the tests exercise the real duplicate-detection path rather than patching it.
"""

from __future__ import annotations

import pytest

from aura_core.adapters import InMemoryMemoryStore, InMemoryVectorStore
from aura_core.domain.memory import MemoryKind, MemoryQuery
from aura_core.llm.embeddings import HashEmbeddingProvider
from aura_core.memory import MemoryEngine


class _ConstantEmbedder(HashEmbeddingProvider):
    """Every text embeds to the same unit vector: cosine 1.0 between any two memories."""

    def _vector(self, text: str) -> tuple[float, ...]:
        return (1.0,) + (0.0,) * (self._dimension - 1)


@pytest.fixture
def engine(clock) -> MemoryEngine:
    return MemoryEngine(
        store=InMemoryMemoryStore(), vectors=InMemoryVectorStore(),
        embedder=_ConstantEmbedder(dimension=32), index_alias="subject_supersede", clock=clock,
    )


PERSON = "api:v3:test-user"      # the person's own write: trust 3
MODEL = "tool:remember_fact:t1"  # the model restating the person: trust 2
EXTRACTOR = "extractor:v2"       # anything else: trust 1


async def _active(engine: MemoryEngine, user) -> list[str]:
    hits = await engine.recall(MemoryQuery(owner_id=user, text="anything", min_score=0.0, limit=20))
    return sorted(h.record.content for h in hits)


class TestCorrectionSupersedes:
    async def test_a_same_subject_correction_supersedes_and_keeps_the_old_record(
        self, engine, user
    ) -> None:
        old = await engine.remember(owner_id=user, content="My office is in Cairo", subject="office",
                                    kind=MemoryKind.SEMANTIC, source=PERSON)
        new = await engine.remember(owner_id=user, content="My office moved to Giza", subject="office",
                                    kind=MemoryKind.SEMANTIC, source=PERSON)

        assert new.id != old.id
        assert new.metadata["replaces"] == old.id.value
        stored_old = await engine._store.get(old.id)
        assert stored_old is not None, "the superseded record must stay for audit"
        assert stored_old.superseded_by == new.id
        assert await _active(engine, user) == ["My office moved to Giza"]

    async def test_a_write_with_no_subject_inherits_the_old_subject_and_supersedes(
        self, engine, user
    ) -> None:
        old = await engine.remember(owner_id=user, content="My office is in Cairo", subject="office",
                                    kind=MemoryKind.SEMANTIC, source=PERSON)
        new = await engine.remember(owner_id=user, content="My office moved to Giza",
                                    kind=MemoryKind.SEMANTIC, source=PERSON)

        assert new.subject == "office"
        assert (await engine._store.get(old.id)).superseded_by == new.id
        assert await _active(engine, user) == ["My office moved to Giza"]

    async def test_the_replacement_starts_from_its_own_confidence(self, engine, user) -> None:
        old = await engine.remember(owner_id=user, content="I live in Cairo", subject="home",
                                    kind=MemoryKind.SEMANTIC, source=PERSON, confidence=0.9)
        new = await engine.remember(owner_id=user, content="I live in Alexandria", subject="home",
                                    kind=MemoryKind.SEMANTIC, source=PERSON, confidence=0.6)
        assert new.confidence == pytest.approx(0.6)
        assert old.id != new.id


class TestDifferentFactsStayApart:
    async def test_different_subjects_keep_both_records_active(self, engine, user) -> None:
        tea = await engine.remember(owner_id=user, content="I like tea", subject="drink",
                                    kind=MemoryKind.SEMANTIC, source=PERSON)
        coffee = await engine.remember(owner_id=user, content="I also like coffee", subject="food",
                                       kind=MemoryKind.SEMANTIC, source=PERSON)

        assert coffee.id != tea.id
        assert "replaces" not in coffee.metadata
        assert (await engine._store.get(tea.id)).superseded_by is None
        assert await _active(engine, user) == ["I also like coffee", "I like tea"]

    async def test_an_old_record_with_no_subject_is_never_replaced(self, engine, user) -> None:
        old = await engine.remember(owner_id=user, content="I like tea",
                                    kind=MemoryKind.SEMANTIC, source=PERSON)
        new = await engine.remember(owner_id=user, content="I also like coffee", subject="drink",
                                    kind=MemoryKind.SEMANTIC, source=PERSON)

        assert new.id != old.id
        assert (await engine._store.get(old.id)).superseded_by is None
        assert await _active(engine, user) == ["I also like coffee", "I like tea"]

    async def test_trust_ranking_does_not_apply_when_nothing_is_overwritten(
        self, engine, user
    ) -> None:
        """A lower-trust write about a *different* fact is just a new fact, not a rewording."""
        stated = await engine.remember(owner_id=user, content="I like tea", subject="drink",
                                       kind=MemoryKind.SEMANTIC, source=PERSON)
        other = await engine.remember(owner_id=user, content="I work nights", subject="work",
                                      kind=MemoryKind.SEMANTIC, source=EXTRACTOR)
        assert other.id != stated.id
        assert await _active(engine, user) == ["I like tea", "I work nights"]


class TestTrustStillProtectsWhatThePersonSaid:
    @pytest.mark.parametrize("source", [EXTRACTOR, MODEL])
    @pytest.mark.parametrize("subject", ["home", None])
    async def test_a_lower_trust_rewording_of_the_same_fact_leaves_the_record_untouched(
        self, engine, user, source, subject
    ) -> None:
        stated = await engine.remember(owner_id=user, content="I live in Cairo", subject="home",
                                       kind=MemoryKind.SEMANTIC, source=PERSON)
        kept = await engine.remember(owner_id=user, content="I live in Giza", subject=subject,
                                     kind=MemoryKind.SEMANTIC, source=source)

        assert kept.id == stated.id
        assert kept.content == "I live in Cairo"
        assert kept.confidence == pytest.approx(stated.confidence)
        assert (await engine._store.get(stated.id)).superseded_by is None
        assert await _active(engine, user) == ["I live in Cairo"]

    async def test_the_person_can_still_correct_themself(self, engine, user) -> None:
        stated = await engine.remember(owner_id=user, content="I live in Cairo", subject="home",
                                       kind=MemoryKind.SEMANTIC, source=PERSON)
        corrected = await engine.remember(owner_id=user, content="I live in Alexandria",
                                          subject="home", kind=MemoryKind.SEMANTIC, source=PERSON)
        assert (await engine._store.get(stated.id)).superseded_by == corrected.id


class TestRestatingStillReinforces:
    async def test_the_same_words_reinforce_and_do_not_supersede(self, engine, user) -> None:
        first = await engine.remember(owner_id=user, content="I am vegetarian.", subject="diet",
                                      kind=MemoryKind.SEMANTIC, source=PERSON)
        again = await engine.remember(owner_id=user, content="i am vegetarian", subject="other",
                                      kind=MemoryKind.SEMANTIC, source=PERSON)

        assert again.id == first.id
        assert again.confidence > first.confidence
        assert (await engine._store.get(first.id)).superseded_by is None


class TestSupersessionIsSafe:
    async def test_the_old_record_is_superseded_only_after_the_new_one_is_stored(
        self, engine, user, monkeypatch
    ) -> None:
        """A failure in between must leave both records, never neither."""
        old = await engine.remember(owner_id=user, content="I live in Cairo", subject="home",
                                    kind=MemoryKind.SEMANTIC, source=PERSON)
        real_update = engine._store.update

        async def failing_update(record):
            if record.id == old.id and record.superseded_by is not None:
                raise RuntimeError("store went away")
            await real_update(record)

        monkeypatch.setattr(engine._store, "update", failing_update)
        with pytest.raises(RuntimeError):
            await engine.remember(owner_id=user, content="I live in Giza", subject="home",
                                  kind=MemoryKind.SEMANTIC, source=PERSON)

        monkeypatch.setattr(engine._store, "update", real_update)
        assert (await engine._store.get(old.id)) is not None
        assert len(await engine._store.list_for_owner(user, limit=10)) == 2

    async def test_erasure_still_removes_superseded_records(self, engine, user) -> None:
        await engine.remember(owner_id=user, content="I live in Cairo", subject="home",
                              kind=MemoryKind.SEMANTIC, source=PERSON)
        await engine.remember(owner_id=user, content="I live in Giza", subject="home",
                              kind=MemoryKind.SEMANTIC, source=PERSON)

        await engine.forget_owner(user)

        assert await engine._store.list_for_owner(user, limit=10, include_superseded=True) == []
