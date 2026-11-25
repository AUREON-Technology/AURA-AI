"""Context pressure folds the session's older turns into its summary (ADR-039; row 28).

``EpisodicMemory.compact`` is the existing fold-older-turns-into-a-summary step (the one ``append``
runs when the window overflows), invoked on demand. The kernel calls it once when the agent loop
reports ``context_pressure``, so the *next* turn carries a summary instead of the verbatim turns.
"""

from __future__ import annotations

from aura_core.domain.identifiers import SessionId, UserId
from aura_core.llm import InMemoryCache
from aura_core.memory.session import EpisodicMemory
from tests.unit._loop_harness import (
    ENGINEER,
    collect_stream,
    engineer_turn,
    frames_of,
    make_container,
    ready_engineer,
    script_model,
    tool_step,
)


def _memory():
    calls: list[tuple[str, str]] = []

    async def summariser(transcript: str, previous: str) -> str:
        calls.append((transcript, previous))
        return f"summary of {transcript.count(chr(10)) + 1} messages"

    memory = EpisodicMemory(InMemoryCache(max_entries=64, default_ttl_s=60), window=12,
                            summarise_after=100, summariser=summariser)
    return memory, calls


async def _seed(memory, user, session, exchanges: int) -> None:
    for n in range(exchanges):
        await memory.append(session, user, user_message=f"q{n}", assistant_message=f"a{n}")


class TestCompact:
    async def test_older_turns_become_a_summary_and_the_recent_ones_stay_verbatim(self) -> None:
        memory, calls = _memory()
        user, session = UserId("u"), SessionId("s")
        await _seed(memory, user, session, 4)  # 8 messages, well under summarise_after

        assert await memory.compact(session, user, keep_last=2) is True
        state = await memory.load(session, user)
        assert [m.content for m in state.messages] == ["q3", "a3"]
        assert state.summary == "summary of 6 messages" and state.folded == 6
        assert "q0" in calls[0][0] and "a2" in calls[0][0] and "q3" not in calls[0][0]

    async def test_a_second_compaction_folds_into_the_running_summary(self) -> None:
        memory, calls = _memory()
        user, session = UserId("u"), SessionId("s")
        await _seed(memory, user, session, 3)
        await memory.compact(session, user, keep_last=2)
        await _seed(memory, user, session, 2)
        assert await memory.compact(session, user, keep_last=2) is True
        assert calls[1][1] == "summary of 4 messages", "the earlier summary is carried forward"

    async def test_nothing_to_fold_is_a_no_op(self) -> None:
        memory, calls = _memory()
        user, session = UserId("u"), SessionId("s")
        assert await memory.compact(session, user) is False
        await _seed(memory, user, session, 2)
        assert await memory.compact(session, user, keep_last=4) is False
        assert calls == []

    async def test_other_peoples_sessions_are_not_touched(self) -> None:
        memory, _ = _memory()
        session = SessionId("shared-id")
        await _seed(memory, UserId("owner"), session, 4)
        assert await memory.compact(session, UserId("intruder"), keep_last=1) is False
        assert len((await memory.load(session, UserId("owner"))).messages) == 8


class TestThroughARealTurn:
    async def test_the_kernel_folds_the_sessions_older_turns_once_pressure_fires(self, monkeypatch) -> None:
        container = make_container(monkeypatch, LLM_MAX_PROMPT_TOKENS="8000", CRITIC_ENABLED="false",
                                   ENGINEER_TOKEN_BUDGET="20000", CONTEXT_PRESSURE_THRESHOLD="0.1")
        echo = await ready_engineer(container)
        session = SessionId("long-session")
        for n in range(4):
            await container.episodic.append(session, ENGINEER, user_message=f"question {n}",
                                            assistant_message=f"answer {n}")
        script_model(echo, [[tool_step("s1", "run_command", "Say ok", {"command": "echo ok"})]],
                     final_text="Done.")

        frames = await collect_stream(container, engineer_turn("continue", session_id=session))

        assert len(frames_of(frames, "context_pressure")) == 1
        done = frames_of(frames, "done")[0]
        assert done["agent_loop"]["history_compacted"] is True
        state = await container.episodic.load(session, ENGINEER)
        assert state.folded == 4 and state.summary, "the four older messages are now a summary"
        assert [m.content for m in state.messages][-2:] == ["continue", "Done."]

    async def test_no_pressure_no_compaction(self, monkeypatch) -> None:
        container = make_container(monkeypatch, LLM_MAX_PROMPT_TOKENS="8000", CRITIC_ENABLED="false")
        echo = await ready_engineer(container)
        session = SessionId("calm-session")
        for n in range(4):
            await container.episodic.append(session, ENGINEER, user_message=f"q{n}", assistant_message=f"a{n}")
        script_model(echo, [[tool_step("s1", "run_command", "Say ok", {"command": "echo ok"})]])
        frames = await collect_stream(container, engineer_turn("continue", session_id=session))
        assert frames_of(frames, "done")[0]["agent_loop"]["history_compacted"] is False
        assert (await container.episodic.load(session, ENGINEER)).folded == 0

    async def test_the_non_streaming_path_compacts_too(self, monkeypatch) -> None:
        container = make_container(monkeypatch, LLM_MAX_PROMPT_TOKENS="8000", CRITIC_ENABLED="false",
                                   ENGINEER_TOKEN_BUDGET="20000", CONTEXT_PRESSURE_THRESHOLD="0.1")
        echo = await ready_engineer(container)
        session = SessionId("run-session")
        for n in range(4):
            await container.episodic.append(session, ENGINEER, user_message=f"q{n}", assistant_message=f"a{n}")
        script_model(echo, [[tool_step("s1", "run_command", "Say ok", {"command": "echo ok"})]])
        result = await container.kernel.run(engineer_turn("continue", session_id=session))
        assert result.agent_loop["history_compacted"] is True
        assert (await container.episodic.load(session, ENGINEER)).folded == 4
