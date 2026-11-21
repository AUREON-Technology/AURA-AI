"""Per-provider tokenizers (KAN-103).

What is pinned here, stated plainly because the ticket asked for "within ±5% of a reference":

* **No exact reference tokenizer for Qwen2, o200k (gpt-oss) or Claude is available offline** in
  the environment these tests run in (``tiktoken`` is not in ``requirements.lock``; no
  ``tokenizer.json`` for those families ships with the repo), and the default suite may not
  download one. So the ±5% check is against each family profile's **documented calibration
  snapshot** (:data:`CALIBRATION`): it pins the estimator, so any change to it is deliberate,
  and it is not evidence of accuracy against the real vocabularies.
* What *is* evidence: the estimate never falls below the old ``len/4`` on Arabic and code (the
  measured undercount), and the exact backend (a Hugging Face ``tokenizer.json`` read by the
  locked ``tokenizers`` package) returns the true count and says ``exact``.
* ``AURA_TOKENIZER_REFERENCE_<FAMILY>=<path to tokenizer.json>`` opts into a real comparison:
  the family's heuristic must not undercount the real tokenizer by more than 5% on any fixture.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from aura_core.context.engine import ContextEngine
from aura_core.context.tokenizer import (
    HeuristicTokenizer,
    HFTokenizer,
    parse_tokenizer_spec,
    tokenizer_for,
)
from aura_core.domain.llm import ProviderCapabilities
from aura_core.domain.messages import Conversation, Message
from aura_core.domain.tokens import PROFILES, infer_family
from aura_core.llm.registry import capabilities_from_config
from aura_core.config.settings import ProviderConfig

ENGLISH = (
    "The quarterly report shows that revenue grew by twelve percent, driven mostly by the new "
    "subscription tier. Support tickets fell after the onboarding redesign, although response "
    "times on weekends are still longer than the team would like. Next quarter the priority is "
    "reliability: fewer incidents, faster recovery, and clearer status updates for customers."
)

#: ~2,000 characters of Modern Standard Arabic (the size of the KAN-28 measurement).
ARABIC = (
    "تهدف هذه الوثيقة إلى شرح طريقة عمل النظام بشكل واضح ومختصر، بحيث يستطيع أي موظف جديد أن "
    "يفهم الخطوات الأساسية دون الحاجة إلى تدريب طويل. يبدأ المستخدم بتسجيل الدخول إلى حسابه، ثم "
    "يختار المشروع الذي يريد العمل عليه من القائمة الرئيسية. بعد ذلك تظهر لوحة التحكم التي تعرض "
    "المهام المفتوحة والتقارير الأخيرة والتنبيهات المهمة. يمكن للمستخدم أن يضيف مهمة جديدة "
    "بالضغط على زر الإضافة وكتابة عنوان المهمة ووصفها وتاريخ التسليم المتوقع. "
) * 5

#: ~1,200 characters of Python, indentation and identifiers included.
CODE = '''def summarise_orders(orders: list[dict], *, currency: str = "USD") -> dict[str, float]:
    """Total, average and largest order value for one currency."""
    totals: dict[str, float] = {"total": 0.0, "average": 0.0, "largest": 0.0}
    matching = [o for o in orders if o.get("currency") == currency]
    if not matching:
        return totals
    for order in matching:
        value = float(order["amount"]) * (1 - order.get("discount_rate", 0.0))
        totals["total"] += value
        totals["largest"] = max(totals["largest"], value)
    totals["average"] = totals["total"] / len(matching)
    return {key: round(val, 2) for key, val in totals.items()}


class OrderCache:
    def __init__(self, max_entries: int = 1024) -> None:
        self._entries: dict[str, dict] = {}
        self._max_entries = max_entries

    def put(self, order_id: str, order: dict) -> None:
        if len(self._entries) >= self._max_entries:
            oldest = next(iter(self._entries))
            del self._entries[oldest]
        self._entries[order_id] = order

    def get(self, order_id: str) -> dict | None:
        return self._entries.get(order_id)
'''

FIXTURES = {"english": ENGLISH, "arabic": ARABIC, "code": CODE}

#: The calibration snapshot: each family profile's count of each fixture when the profiles were
#: written (2026-11-21). A change outside ±5% is a deliberate recalibration and updates this.
CALIBRATION: dict[str, dict[str, int]] = {
    "generic": {"english": 90, "arabic": 951, "code": 423},
    "qwen2": {"english": 90, "arabic": 951, "code": 425},
    "o200k": {"english": 90, "arabic": 951, "code": 422},
    "cl100k": {"english": 90, "arabic": 951, "code": 422},
    "claude": {"english": 99, "arabic": 1041, "code": 463},
}


def _len4(text: str) -> int:
    return (len(text) + 3) // 4


class TestFixtureSizes:
    def test_fixtures_match_the_measurement_sizes(self) -> None:
        assert 1900 <= len(ARABIC) <= 2200
        assert 1100 <= len(CODE) <= 1400


class TestCalibration:
    @pytest.mark.parametrize("family", sorted(CALIBRATION))
    @pytest.mark.parametrize("fixture", sorted(FIXTURES))
    def test_within_five_percent_of_the_calibration_snapshot(self, family: str, fixture: str) -> None:
        reference = CALIBRATION[family][fixture]
        count = tokenizer_for(family).count(FIXTURES[fixture])
        assert abs(count - reference) <= 0.05 * reference, (family, fixture, count, reference)

    @pytest.mark.parametrize("family", sorted(PROFILES))
    @pytest.mark.parametrize("fixture", ["arabic", "code"])
    def test_arabic_and_code_are_not_undercounted_like_len_over_4(
        self, family: str, fixture: str
    ) -> None:
        """The measured defect: len/4 counted 2,020 chars of Arabic as 505 and 1,220 chars of
        code as 305. Every family's estimate must stay well above that (by a quarter at least;
        BPE vocabularies spend ~2-3 characters per token on both, len/4 assumed 4)."""
        text = FIXTURES[fixture]
        assert tokenizer_for(family).count(text) >= 1.25 * _len4(text)

    def test_english_count_is_held_at_the_len_over_four_floor(self) -> None:
        """The rules alone give 9 (unchanged from before profiles); the ceil(len/4) floor
        (test_token_estimate_floor.py) lifts this 38-character sentence to 10."""
        assert HeuristicTokenizer().count("This is a fairly long English sentence") == 10

    def test_other_scripts_are_no_longer_free(self) -> None:
        """Cyrillic and Hebrew letters used to match no rule and count as zero tokens."""
        assert HeuristicTokenizer().count("Привет, как дела у тебя сегодня") >= 10
        assert HeuristicTokenizer().count("שלום לכולם ותודה רבה") >= 8

    def test_qwen_counts_each_digit(self) -> None:
        assert tokenizer_for("qwen2").count("1234567890") > tokenizer_for("o200k").count("1234567890")

    def test_claude_profile_has_the_larger_margin(self) -> None:
        assert tokenizer_for("claude").count(ENGLISH) > tokenizer_for("generic").count(ENGLISH)


class TestSelection:
    @pytest.mark.parametrize(
        ("model_id", "family"),
        [
            ("qwen2.5:3b-instruct-q4_K_M", "qwen2"),
            ("Qwen/Qwen2.5-Coder-7B-Instruct", "qwen2"),
            ("openai/gpt-oss-120b", "o200k"),
            ("claude-sonnet", "claude"),
            ("llama-3.1-8b", "generic"),
            (None, "generic"),
        ],
    )
    def test_family_is_inferred_from_the_model_id(self, model_id: str | None, family: str) -> None:
        assert infer_family(model_id) == family
        assert parse_tokenizer_spec("", model_id) == (family, None)

    def test_an_explicit_spec_wins_over_the_model_id(self) -> None:
        assert parse_tokenizer_spec("o200k", "qwen2.5") == ("o200k", None)
        assert parse_tokenizer_spec("qwen2@/models/tokenizer.json", None) == (
            "qwen2", "/models/tokenizer.json"
        )
        assert parse_tokenizer_spec("no-such-family", "qwen2.5") == ("generic", None)

    def test_no_family_claims_exact_counts_without_a_real_vocabulary(self) -> None:
        """tiktoken is not installed, and cl100k was never the right vocabulary anyway."""
        for family in PROFILES:
            tokenizer = tokenizer_for(family)
            assert not tokenizer.exact, family

    def test_provider_config_declares_the_tokenizer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AURA_ALLOW_CLAUDE_INFERENCE", "true")
        caps = capabilities_from_config(
            ProviderConfig(name="space", kind="openai_compatible", model="m", tokenizer="qwen2",
                           base_url="http://localhost:8000/v1")
        )
        assert caps.tokenizer == "qwen2"
        claude = capabilities_from_config(ProviderConfig(name="c", kind="claude", model="claude-opus-5-5", api_key_env="K")
        )
        assert claude.tokenizer == "claude"
        default = ProviderCapabilities(model_id="qwen2.5:3b", context_window=8192, max_output_tokens=512)
        assert tokenizer_for(default.tokenizer, model_id=default.model_id).name == "heuristic:qwen2"


def _write_word_level_tokenizer(path: Path) -> None:
    """A real (tiny) tokenizer.json, built offline with the locked ``tokenizers`` package."""
    tokenizers = pytest.importorskip("tokenizers")
    from tokenizers import models, pre_tokenizers

    vocab = {"[UNK]": 0, "hello": 1, "world": 2, ",": 3, "!": 4}
    tok = tokenizers.Tokenizer(models.WordLevel(vocab=vocab, unk_token="[UNK]"))
    tok.pre_tokenizer = pre_tokenizers.Sequence(
        [pre_tokenizers.Whitespace(), pre_tokenizers.Punctuation()]
    )
    tok.save(str(path))


class TestExactBackend:
    def test_a_tokenizer_json_gives_exact_counts(self, tmp_path: Path) -> None:
        path = tmp_path / "tokenizer.json"
        _write_word_level_tokenizer(path)
        tokenizer = tokenizer_for(f"qwen2@{path}")
        assert isinstance(tokenizer, HFTokenizer)
        assert tokenizer.exact and tokenizer.name == "hf:qwen2"
        assert tokenizer.count("hello, world!") == 4
        assert tokenizer.count("") == 0

    def test_a_missing_file_falls_back_honestly(self, tmp_path: Path) -> None:
        tokenizer = tokenizer_for(f"o200k@{tmp_path / 'absent.json'}")
        assert not tokenizer.exact
        assert tokenizer.name == "heuristic:o200k"
        assert tokenizer.count(ENGLISH) == tokenizer_for("o200k").count(ENGLISH)

    def test_the_context_engine_reports_the_tokenizer_it_counts_with(self, tmp_path: Path) -> None:
        path = tmp_path / "tokenizer.json"
        _write_word_level_tokenizer(path)
        engine = ContextEngine(tokenizer=tokenizer_for(f"qwen2@{path}"))
        assert engine.counts_are_exact and engine.tokenizer_name == "hf:qwen2"
        assert not ContextEngine(tokenizer=tokenizer_for("qwen2")).counts_are_exact


class TestMessageArithmetic:
    def test_count_messages_matches_conversation_approx_tokens(self) -> None:
        conversation = Conversation.of([Message.system("Be brief."), Message.user(ARABIC)])
        for family in PROFILES:
            tokenizer = tokenizer_for(family)
            assert tokenizer.count_messages(conversation.messages) == conversation.approx_tokens(
                tokenizer.count
            )
        assert Conversation().approx_tokens() == 0 == HeuristicTokenizer().count_messages([])


@pytest.mark.parametrize("family", sorted(PROFILES))
def test_against_a_real_reference_tokenizer_when_one_is_provided(family: str) -> None:
    """Opt-in: point ``AURA_TOKENIZER_REFERENCE_<FAMILY>`` at the family's tokenizer.json."""
    path = os.environ.get(f"AURA_TOKENIZER_REFERENCE_{family.upper()}")
    if not path:
        pytest.skip(f"no reference tokenizer for {family} (set AURA_TOKENIZER_REFERENCE_{family.upper()})")
    reference = HFTokenizer(path, fallback=HeuristicTokenizer(), family=family)
    assert reference.exact, f"could not load {path}"
    estimate = tokenizer_for(family)
    for name, text in FIXTURES.items():
        exact = reference.count(text)
        assert estimate.count(text) >= 0.95 * exact, (family, name, estimate.count(text), exact)
