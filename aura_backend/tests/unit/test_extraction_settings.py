"""``ExtractionSettings`` (ADR-038): one consent flag, no endpoint, no model, no secret in the dump."""

from __future__ import annotations

import dataclasses
import json

import pytest

from aura_core.config.external_inference import is_cloudflare_account_id
from aura_core.config.settings import AuraSettings, ExtractionSettings
from aura_core.domain.errors import ConfigurationError

ACCOUNT = "0123456789abcdef0123456789abcdef"
TOKEN = "cfat_not_a_real_token_value"

_NAMES = (
    "ALLOW_EXTRACTION_INFERENCE",
    "EXTRACT_CLOUDFLARE_ACCOUNT_ID",
    "EXTRACT_API_KEY_ENV",
    "EXTRACT_MIN_CONFIDENCE",
    "EXTRACT_MAX_CHARS",
    "EXTRACT_TIMEOUT_S",
    "EXTRACT_DAILY_CALL_CAP",
    "EXTRACT_ENABLED",
    "ALLOW_HOSTED_INFERENCE",
    "ALLOW_CLAUDE_INFERENCE",
    "TEST_EXTRACT_TOKEN",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _NAMES:
        monkeypatch.delenv(f"AURA_{name}", raising=False)


def _enabled(**kw) -> ExtractionSettings:
    base = dict(enabled=True, account_id=ACCOUNT, api_key_env="AURA_TEST_EXTRACT_TOKEN")
    base.update(kw)
    return ExtractionSettings(**base)


class TestFromEnv:
    def test_it_is_off_by_default_with_conservative_limits(self) -> None:
        s = ExtractionSettings.from_env()
        assert s.enabled is False
        assert (s.min_confidence, s.max_chars, s.timeout_s, s.daily_call_cap) == (0.5, 20_000, 30.0, 100)
        assert s.account_id == "" and s.api_key_env == "" and s.api_key == ""

    def test_the_consent_flag_turns_it_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AURA_ALLOW_EXTRACTION_INFERENCE", "true")
        assert ExtractionSettings.from_env().enabled is True

    def test_a_flag_that_is_not_a_boolean_is_refused_not_guessed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AURA_ALLOW_EXTRACTION_INFERENCE", "banana")
        with pytest.raises(ConfigurationError):
            ExtractionSettings.from_env()

    def test_other_consents_and_a_made_up_switch_do_not_enable_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for name in ("ALLOW_HOSTED_INFERENCE", "ALLOW_CLAUDE_INFERENCE", "EXTRACT_ENABLED"):
            monkeypatch.setenv(f"AURA_{name}", "true")
        assert ExtractionSettings.from_env().enabled is False

    def test_values_are_read_and_trimmed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AURA_EXTRACT_CLOUDFLARE_ACCOUNT_ID", f"  {ACCOUNT}  ")
        monkeypatch.setenv("AURA_EXTRACT_API_KEY_ENV", " AURA_TEST_EXTRACT_TOKEN ")
        monkeypatch.setenv("AURA_EXTRACT_MIN_CONFIDENCE", "0.8")
        monkeypatch.setenv("AURA_EXTRACT_MAX_CHARS", "5000")
        monkeypatch.setenv("AURA_EXTRACT_TIMEOUT_S", "12.5")
        monkeypatch.setenv("AURA_EXTRACT_DAILY_CALL_CAP", "25")
        s = ExtractionSettings.from_env()
        assert (s.account_id, s.api_key_env) == (ACCOUNT, "AURA_TEST_EXTRACT_TOKEN")
        assert (s.min_confidence, s.max_chars, s.timeout_s, s.daily_call_cap) == (0.8, 5000, 12.5, 25)

    @pytest.mark.parametrize(
        "name, value",
        [
            ("EXTRACT_MIN_CONFIDENCE", "1.5"), ("EXTRACT_MIN_CONFIDENCE", "-0.1"),
            ("EXTRACT_MIN_CONFIDENCE", "abc"),
            ("EXTRACT_MAX_CHARS", "50"), ("EXTRACT_MAX_CHARS", "100001"), ("EXTRACT_MAX_CHARS", "x"),
            ("EXTRACT_TIMEOUT_S", "0"), ("EXTRACT_TIMEOUT_S", "301"),
            ("EXTRACT_DAILY_CALL_CAP", "0"), ("EXTRACT_DAILY_CALL_CAP", "100001"),
        ],
    )
    def test_out_of_range_values_are_refused(self, monkeypatch: pytest.MonkeyPatch, name: str, value: str) -> None:
        monkeypatch.setenv(f"AURA_{name}", value)
        with pytest.raises(ConfigurationError):
            ExtractionSettings.from_env()

    def test_there_is_no_endpoint_or_model_setting(self) -> None:
        names = {f.name for f in dataclasses.fields(ExtractionSettings)}
        assert not {n for n in names if "url" in n or "host" in n or "model" in n or "endpoint" in n}, names


class TestSecret:
    def test_the_key_is_read_from_the_named_variable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AURA_TEST_EXTRACT_TOKEN", TOKEN)
        assert _enabled().api_key == TOKEN

    def test_no_named_variable_means_no_key_even_if_one_is_lying_around(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLOUDFLARE_API_TOKEN", TOKEN)
        monkeypatch.setenv("AURA_TEST_EXTRACT_TOKEN", TOKEN)
        assert _enabled(api_key_env="").api_key == ""

    def test_the_secret_is_a_property_and_not_a_field(self) -> None:
        assert "api_key" not in {f.name for f in dataclasses.fields(ExtractionSettings)}

    def test_as_dict_names_the_variable_but_never_carries_the_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AURA_TEST_EXTRACT_TOKEN", TOKEN)
        dump = AuraSettings(extraction=_enabled()).as_dict()
        text = json.dumps(dump)
        assert TOKEN not in text
        assert dump["extraction"]["api_key_env"] == "AURA_TEST_EXTRACT_TOKEN"
        assert dump["extraction"]["enabled"] is True


class TestProblems:
    def test_a_disabled_block_blocks_nothing(self) -> None:
        assert ExtractionSettings().problems() == []
        assert ExtractionSettings(account_id="nonsense").problems() == []

    def test_a_complete_block_has_no_problems(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AURA_TEST_EXTRACT_TOKEN", TOKEN)
        assert _enabled().problems() == []

    @pytest.mark.parametrize("account", ["", "abc", ACCOUNT.upper(), "../x" + ACCOUNT, ACCOUNT + "/other"])
    def test_a_bad_account_id_is_named(self, account: str, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AURA_TEST_EXTRACT_TOKEN", TOKEN)
        (problem,) = _enabled(account_id=account).problems()
        assert "AURA_EXTRACT_CLOUDFLARE_ACCOUNT_ID" in problem

    def test_a_missing_key_variable_name_is_named(self) -> None:
        (problem,) = _enabled(api_key_env="").problems()
        assert "AURA_EXTRACT_API_KEY_ENV" in problem

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_a_named_variable_that_is_unset_or_blank_is_named(
        self, value: str | None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        if value is not None:
            monkeypatch.setenv("AURA_TEST_EXTRACT_TOKEN", value)
        (problem,) = _enabled().problems()
        assert "AURA_TEST_EXTRACT_TOKEN" in problem and "unset or empty" in problem

    @pytest.mark.parametrize("bad", [float("nan"), -0.01, 1.01, float("inf")])
    def test_a_bad_threshold_is_caught_even_when_the_tool_is_off(self, bad: float) -> None:
        (problem,) = ExtractionSettings(min_confidence=bad).problems()
        assert "AURA_EXTRACT_MIN_CONFIDENCE" in problem

    def test_no_message_contains_the_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AURA_TEST_EXTRACT_TOKEN", TOKEN)
        assert TOKEN not in " ".join(_enabled(account_id="bad").problems())


class TestAccountIdHelper:
    @pytest.mark.parametrize("value", [ACCOUNT, "a" * 32, "0" * 32])
    def test_valid(self, value: str) -> None:
        assert is_cloudflare_account_id(value)

    @pytest.mark.parametrize("value", ["", "abc", "g" * 32, "A" * 32, ACCOUNT + "0", ACCOUNT[:-1],
                                       ACCOUNT + "\n", None, 123, b"x" * 32])
    def test_invalid(self, value: object) -> None:
        assert not is_cloudflare_account_id(value)


def _settings(extraction: ExtractionSettings) -> AuraSettings:
    """A valid local configuration (``from_env`` supplies the echo provider) with this block."""
    return dataclasses.replace(AuraSettings.from_env(), extraction=extraction)


class TestInAuraSettings:
    def test_from_env_includes_the_block_and_it_is_off(self) -> None:
        assert AuraSettings.from_env().extraction.enabled is False

    def test_the_baseline_used_below_is_itself_valid(self) -> None:
        _settings(ExtractionSettings()).validate()

    def test_validate_reports_extraction_problems_with_the_rest(self) -> None:
        settings = _settings(_enabled(account_id="bad", api_key_env=""))
        with pytest.raises(ConfigurationError) as caught:
            settings.validate()
        problems = caught.value.details["problems"]
        assert any("AURA_EXTRACT_CLOUDFLARE_ACCOUNT_ID" in p for p in problems)
        assert any("AURA_EXTRACT_API_KEY_ENV" in p for p in problems)

    def test_validate_passes_for_a_complete_block(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AURA_TEST_EXTRACT_TOKEN", TOKEN)
        _settings(_enabled()).validate()

    def test_validate_ignores_a_half_configured_block_that_is_off(self) -> None:
        _settings(ExtractionSettings(account_id="bad")).validate()

    def test_validate_catches_a_bad_threshold_even_when_off(self) -> None:
        with pytest.raises(ConfigurationError):
            _settings(ExtractionSettings(min_confidence=float("nan"))).validate()
