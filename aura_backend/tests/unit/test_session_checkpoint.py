"""Structured session checkpoints (inventory row 26): decisions, completed, blockers, next steps."""

from __future__ import annotations

import json

import pytest

from aura_core.domain.identifiers import SessionId, UserId
from aura_core.memory.checkpoint import SessionCheckpoint, extract_checkpoint, parse_checkpoint_json
from aura_core.memory.session import EpisodicMemory

U, V, S = UserId("alice"), UserId("bob"), SessionId("s1")

CONVO = [
    ("user", "We need to migrate the billing tables."),
    ("assistant", "We decided to use Alembic for the migration. The schema change is done and merged."),
    ("user", "The deploy is failing with a timeout error. Is the proxy ok?"),
    ("assistant", "Blocked: the staging database is unreachable. Next, we need to rerun the load test."),
]


async def _memory(cache, checkpointer=None) -> EpisodicMemory:
    memory = EpisodicMemory(cache, checkpointer=checkpointer)
    for user, assistant in [(CONVO[0][1], CONVO[1][1]), (CONVO[2][1], CONVO[3][1])]:
        await memory.append(S, U, user_message=user, assistant_message=assistant)
    return memory


@pytest.fixture
def cache(container):
    return container.cache_backend


class TestExtractive:
    def test_sentences_land_in_the_right_list_and_questions_are_skipped(self) -> None:
        from aura_core.domain.messages import Message

        cp = extract_checkpoint([Message.user(t) if r == "user" else Message.assistant(t) for r, t in CONVO])
        assert any("Alembic" in d for d in cp.decisions)
        assert any("done and merged" in c for c in cp.completed)
        assert any("staging database is unreachable" in b for b in cp.blockers)
        assert any("rerun the load test" in n for n in cp.next_steps)
        assert not any("Is the proxy ok" in x for x in cp.decisions + cp.completed + cp.blockers + cp.next_steps)
        assert cp.source == "extractive"

    def test_arabic_is_understood(self) -> None:
        cp = extract_checkpoint([], "قررنا نستخدم Alembic للهجرة. تم دمج التعديل. في مشكلة في الـ staging. لازم نعيد اختبار الحمل.")
        assert cp.decisions and cp.completed and cp.blockers and cp.next_steps

    def test_lists_are_capped_and_deduplicated(self) -> None:
        cp = extract_checkpoint([], " ".join(f"We decided option number {i} is final." for i in range(40)) + " We decided option number 3 is final.")
        assert len(cp.decisions) <= 12 and len(set(cp.decisions)) == len(cp.decisions)

    def test_empty_input_is_an_empty_checkpoint(self) -> None:
        assert extract_checkpoint([], "").is_empty()


class TestModelReplyParsing:
    def test_plain_fenced_and_surrounded_json(self) -> None:
        body = {"decisions": ["use Alembic"], "completed": ["schema merged"], "blockers": [], "next_steps": ["rerun load test"]}
        for raw in (json.dumps(body), f"```json\n{json.dumps(body)}\n```", f"Here you go:\n{json.dumps(body)}\nHope it helps"):
            cp = parse_checkpoint_json(raw)
            assert cp is not None and cp.decisions == ("use Alembic",) and cp.source == "model"

    def test_fields_of_the_wrong_shape_are_tolerated(self) -> None:
        cp = parse_checkpoint_json(json.dumps({"decisions": "Use Alembic. Keep Postgres.", "completed": [None, 3, {"text": "done x"}], "blockers": {}, "next_steps": ["a" * 900]}))
        assert cp is not None and cp.decisions == ("Use Alembic.", "Keep Postgres.") and cp.completed == ("done x",)
        assert len(cp.next_steps[0]) == 300

    @pytest.mark.parametrize("raw", ["", "no json here", "[1,2]", '{"unrelated": 1}', "{broken", None, 7])
    def test_unusable_replies_return_none(self, raw) -> None:
        assert parse_checkpoint_json(raw) is None


class TestEpisodicCheckpoint:
    async def test_a_model_written_checkpoint_is_saved_and_recalled(self, cache) -> None:
        async def model(transcript: str, previous: str) -> str:
            assert "Alembic" in transcript
            return json.dumps({"decisions": ["use Alembic"], "completed": [], "blockers": ["staging db down"], "next_steps": ["rerun load test"]})

        memory = await _memory(cache, model)
        cp = await memory.checkpoint(S, U)
        assert cp.source == "model" and cp.blockers == ("staging db down",) and cp.covers_messages == 4
        again = await memory.latest_checkpoint(S, U)
        assert again == SessionCheckpoint.from_dict(cp.as_dict())
        assert "staging db down" in again.as_text()

    async def test_without_a_model_it_falls_back_to_extraction(self, cache) -> None:
        cp = await (await _memory(cache)).checkpoint(S, U)
        assert cp.source == "extractive" and cp.blockers and cp.next_steps

    async def test_a_failing_or_garbage_model_falls_back_instead_of_raising(self, cache) -> None:
        async def boom(t, p):
            raise RuntimeError("provider down")

        async def garbage(t, p):
            return "I cannot do that"

        memory = await _memory(cache, boom)
        assert (await memory.checkpoint(S, U)).source == "extractive"
        memory2 = EpisodicMemory(cache, checkpointer=garbage)
        await memory2.append(SessionId("s2"), U, user_message=CONVO[0][1], assistant_message=CONVO[1][1])
        assert (await memory2.checkpoint(SessionId("s2"), U)).source == "extractive"

    async def test_an_empty_session_has_an_empty_checkpoint_and_saves_nothing(self, cache) -> None:
        memory = EpisodicMemory(cache)
        cp = await memory.checkpoint(SessionId("empty"), U)
        assert cp.is_empty() and await memory.latest_checkpoint(SessionId("empty"), U) is None

    async def test_a_checkpoint_belongs_to_its_owner(self, cache) -> None:
        memory = await _memory(cache)
        await memory.checkpoint(S, U)
        assert await memory.latest_checkpoint(S, V) is None  # session id alone is not a capability
        assert (await memory.checkpoint(S, V)).is_empty()  # and bob cannot checkpoint alice's session

    async def test_clearing_the_session_clears_its_checkpoint(self, cache) -> None:
        memory = await _memory(cache)
        await memory.checkpoint(S, U)
        await memory.clear(S, U)
        assert await memory.latest_checkpoint(S, U) is None

    async def test_save_false_does_not_persist(self, cache) -> None:
        memory = await _memory(cache)
        await memory.checkpoint(S, U, save=False)
        assert await memory.latest_checkpoint(S, U) is None


async def test_the_container_wires_a_checkpointer(container) -> None:
    assert container.episodic._checkpointer is not None
