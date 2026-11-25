"""``remember_fact`` stores only what the user said (tools/fact_grounding.py).

Its description has always told the model to store things the user *stated*, never things it
inferred. That was a request; this is the check. Durable memory is recalled into every later
prompt, so an invented "the user is a vegetarian" is not a passing mistake.

The three layers are tested where they live: the pure comparison, the tool that applies it, and the
kernel that decides what counts as "what the user said" (their own message -- never retrieved text,
and never a delegating agent's task).
"""

from __future__ import annotations

import pytest

from aura_core.domain.errors import ToolExecutionError
from aura_core.domain.identifiers import UserId
from aura_core.domain.memory import MemoryQuery
from aura_core.kernel import TurnRequest
from aura_core.ports.runtime import ToolContext
from aura_core.tools.builtin import build_memory_tools
from aura_core.tools.fact_grounding import check_fact_support


class TestTheComparison:
    @pytest.mark.parametrize(
        ("fact", "said"),
        [
            ("The user lives in Cairo", "I live in Cairo and work nights"),
            ("The user is allergic to peanuts", "btw I'm allergic to peanuts"),
            ("The user is 34 years old", "I'm 34"),
            ("The user's sister is called Mona", "my sister is called Mona"),
            ("المستخدم يسكن في القاهرة", "أنا أسكن في القاهرة"),
            ("The user is", "hello"),  # nothing but filler: no claim to check
        ],
    )
    def test_a_third_person_restatement_of_what_was_said_is_supported(self, fact, said) -> None:
        assert check_fact_support(fact, said).supported

    @pytest.mark.parametrize(
        ("fact", "said"),
        [
            ("The user is a vegetarian", "I had a salad today"),
            ("The user is 43 years old", "I'm 34"),
            ("The user lives in Alexandria", "I live in Cairo"),
            ("المستخدم يسكن في الاسكندرية", "أنا أسكن في القاهرة"),
            ("The user prefers dark mode and uses Linux", "I prefer dark mode"),
            ("The user is a doctor", ""),
        ],
    )
    def test_a_fact_the_user_did_not_state_is_not(self, fact, said) -> None:
        result = check_fact_support(fact, said)
        assert not result.supported
        assert result.missing, "the answer must name what was missing"

    def test_a_number_is_never_close_enough(self) -> None:
        result = check_fact_support("The user was born in 1985 in Cairo", "I was born in 1958 in Cairo")
        assert not result.supported and result.missing == ("1985",)

    def test_a_synonym_is_not_support(self) -> None:
        """"Does not eat meat" for "I'm a vegetarian" is a reasonable inference and still one."""
        assert not check_fact_support("The user does not eat meat", "I'm a vegetarian").supported

    def test_the_explanation_tells_the_model_what_to_do(self) -> None:
        text = check_fact_support("The user is a pilot", "hi").explain()
        assert "pilot" in text and "own words" in text and "ask" in text


@pytest.fixture
def user() -> UserId:
    return UserId("grounding-user")


def _ctx(user: UserId, said: str | None) -> ToolContext:
    metadata = {} if said is None else {"user_message": said}
    return ToolContext(user_id=user, correlation_id="corr-g", deadline_s=30.0, metadata=metadata)


class TestTheTool:
    async def test_a_supported_fact_is_stored(self, memory, user) -> None:
        _, remember = build_memory_tools(memory)
        out = await remember.execute({"fact": "The user lives in Cairo."}, _ctx(user, "I live in Cairo"))
        assert out.startswith("Recorded:")
        hits = await memory.recall(MemoryQuery(owner_id=user, text="the user lives in cairo"))
        assert hits and "Cairo" in hits[0].record.content

    async def test_an_unsupported_fact_is_refused_and_nothing_is_written(self, memory, user) -> None:
        _, remember = build_memory_tools(memory)
        with pytest.raises(ToolExecutionError, match="not stored"):
            await remember.execute({"fact": "The user is a vegetarian."}, _ctx(user, "what's for lunch?"))
        assert await memory.recall(MemoryQuery(owner_id=user, text="vegetarian")) == []

    async def test_a_context_with_no_user_message_is_not_checked(self, memory, user) -> None:
        """A context built outside a turn cannot be checked, and is not turned into a refusal."""
        _, remember = build_memory_tools(memory)
        out = await remember.execute({"fact": "The user lives in Cairo."}, _ctx(user, None))
        assert out.startswith("Recorded:")

    async def test_the_credential_refusal_still_comes_first(self, memory, user) -> None:
        _, remember = build_memory_tools(memory)
        key = "sk-" + "a1B2c3D4" * 4
        said = f"my key is {key}"
        with pytest.raises(ToolExecutionError, match="credential"):
            await remember.execute({"fact": f"The user's key is {key}"}, _ctx(user, said))


class TestTheKernelSuppliesWhatTheUserSaid:
    def _request(self, user: UserId, **overrides) -> TurnRequest:
        base = dict(message="I live in Cairo", user_id=user)
        base.update(overrides)
        return TurnRequest(**base)

    async def test_the_users_own_message_rides_the_tool_context(self, container, user) -> None:
        await container.startup()
        context = container.kernel._tool_context(self._request(user), False)
        assert context.metadata["user_message"] == "I live in Cairo"

    async def test_a_delegated_turn_carries_none(self, container, user) -> None:
        """Its message is another agent's task text; treating that as the user's words would let a
        sub-agent launder an invented fact into memory by quoting itself."""
        await container.startup()
        context = container.kernel._tool_context(self._request(user, delegation_depth=1), False)
        assert context.metadata["user_message"] is None

    async def test_a_very_long_message_is_bounded(self, container, user) -> None:
        await container.startup()
        context = container.kernel._tool_context(self._request(user, message="x " * 20_000), False)
        assert len(context.metadata["user_message"]) == 8000
