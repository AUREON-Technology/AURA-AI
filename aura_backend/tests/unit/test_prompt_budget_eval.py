"""The prompt-budget measurement (WP2, D-05): evidence only reaches the model if the budgets let it."""

from __future__ import annotations

import pytest

from evaluation.prompt_budget_eval import score_budget

#: What `prompt_budget_eval._configure` writes. Registered with monkeypatch first so the process
#: environment is restored afterwards and a later test's `load_settings` is not affected.
_ENV_KEYS = (
    "AURA_EMBEDDING_PROVIDER", "AURA_EMBEDDING_MODEL", "AURA_EMBEDDING_DIMENSION",
    "AURA_KNOWLEDGE_RERANK_PROVIDER", "AURA_KNOWLEDGE_SCORE_THRESHOLD",
    "AURA_LLM_MAX_PROMPT_TOKENS", "AURA_KNOWLEDGE_TOP_K", "AURA_KNOWLEDGE_PROMPT_CHUNK_CHARS",
)


@pytest.fixture(autouse=True)
def _restore_environment(monkeypatch) -> None:
    for key in _ENV_KEYS:
        monkeypatch.setenv(key, "placeholder")
        monkeypatch.delenv(key)


class TestPromptBudget:
    async def test_a_generous_budget_shows_the_model_more_evidence_than_the_default(self) -> None:
        default = await score_budget(cap=400, top_k=2, chars=280)
        generous = await score_budget(cap=3000, top_k=2, chars=1000)
        assert default["questions"] == generous["questions"] > 0
        # The same retrieval in both: the difference is only what survives into the prompt.
        assert default["retrieved_hit"] == generous["retrieved_hit"]
        assert generous["evidence_in_prompt"] > default["evidence_in_prompt"]
