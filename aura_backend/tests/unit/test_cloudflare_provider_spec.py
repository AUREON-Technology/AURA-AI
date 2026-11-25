"""A Cloudflare Workers AI provider as the control center writes it (console plan, stage 6, B2).

Pure domain: the spec is validated on construction, ``provider_variables`` is the exact variable
set a provider needs, and the provider-list helpers edit ``AURA_LLM_PROVIDERS``. The point of the
loader tests at the bottom is that the builder's output is not merely well formed: it is accepted
or refused by the *real* ``ProviderConfig`` the gateway boots with, so the two cannot drift.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import pytest

from aura_core.config.external_inference import (
    WORKERS_AI_API_ROOT,
    is_hosted_open_weight_host,
)
from aura_core.config.settings import ProviderConfig
from aura_core.domain.cloudflare_provider import (
    PROVIDER_OPTIONAL_TAILS,
    PROVIDER_REQUIRED_TAILS,
    CloudflareProviderSpec,
    key_variable,
    provider_prefix,
    provider_variables,
    providers_with,
    providers_without,
)
from aura_core.domain.control import PLATFORM_TOKEN_HINTS, Platform, is_secret_name, is_secret_variable
from aura_core.domain.errors import ConfigurationError, NotFoundError, ValidationError

ACCOUNT = "0123456789abcdef0123456789abcdef"
MODEL = "@cf/openai/gpt-oss-20b"


def spec(**overrides) -> CloudflareProviderSpec:
    base = dict(name="cf-gpt-oss-20b", model=MODEL, context_window=32768)
    base.update(overrides)
    return CloudflareProviderSpec(**base)


class TestSpecValidation:
    def test_a_minimal_spec_gets_the_documented_defaults(self) -> None:
        s = spec()
        assert (s.quality_tier, s.max_output_tokens, s.enabled) == (2, 2048, True)
        assert (s.rpm, s.rpd, s.tpm, s.tpd) == (0, 0, 0, 0)
        assert (s.tokens_per_second, s.json_mode, s.vision) == (0.0, False, False)

    @pytest.mark.parametrize("name", ["cf-a", "a", "workersai", "cf-gpt-oss-20b", "a" * 32, "cf2"])
    def test_good_names(self, name: str) -> None:
        assert spec(name=name).name == name

    @pytest.mark.parametrize(
        "name",
        ["", "CF-A", "cf_a", "1cf", "-cf", "cf-", "cf a", "a" * 33, "cf.a", "local", "echo", None, 7],
    )
    def test_bad_names_are_refused(self, name) -> None:
        with pytest.raises(ValidationError):
            spec(name=name)

    @pytest.mark.parametrize("model", [MODEL, "@cf/qwen/qwen3-30b-a3b-fp8", "@cf/meta/llama-3.3-70b-instruct-fp8-fast"])
    def test_good_models(self, model: str) -> None:
        assert spec(model=model).model == model

    @pytest.mark.parametrize(
        "model",
        ["", "gpt-oss-20b", "openai/gpt-oss-20b", "@cf/", "@cf", "@hf/x/y", "@cf/a b", "@cf/a\nb", "@cf/" + "a" * 200, None],
    )
    def test_bad_models_are_refused(self, model) -> None:
        with pytest.raises(ValidationError):
            spec(model=model)

    @pytest.mark.parametrize(
        "field, value",
        [
            ("quality_tier", 0), ("quality_tier", 4), ("quality_tier", True), ("quality_tier", "2"),
            ("context_window", 511), ("context_window", 4_000_001), ("context_window", 1.5),
            ("max_output_tokens", 15), ("max_output_tokens", 40000),
            ("rpm", -1), ("rpd", -1), ("tpm", -1), ("tpd", -1), ("rpm", True),
            ("tokens_per_second", -1), ("tokens_per_second", 5001), ("tokens_per_second", float("nan")),
            ("tokens_per_second", float("inf")), ("tokens_per_second", True),
            ("enabled", "yes"), ("json_mode", 1), ("vision", "false"),
        ],
    )
    def test_out_of_range_or_mistyped_values_are_refused_not_coerced(self, field: str, value) -> None:
        with pytest.raises(ValidationError):
            spec(**{field: value})

    def test_output_cannot_exceed_the_context_window(self) -> None:
        with pytest.raises(ValidationError):
            spec(context_window=1024, max_output_tokens=2048)
        assert spec(context_window=2048, max_output_tokens=2048).max_output_tokens == 2048

    def test_a_spec_is_immutable(self) -> None:
        with pytest.raises(Exception):
            spec().name = "other"  # type: ignore[misc]


class TestNamesAndPrefix:
    def test_prefix_matches_what_the_settings_loader_reads(self) -> None:
        # ProviderConfig.from_env: f"PROVIDER_{name.upper().replace('-', '_')}", read as AURA_<that>.
        assert provider_prefix("cf-gpt-oss-20b") == "AURA_PROVIDER_CF_GPT_OSS_20B"
        assert provider_prefix("workersai") == "AURA_PROVIDER_WORKERSAI"

    def test_the_key_variable_is_secret_and_follows_the_railway_convention(self) -> None:
        name = key_variable("cf-a")
        assert name == "AURA_PROVIDER_CF_A_API_KEY"
        assert is_secret_name(name)


class TestProviderVariables:
    def test_the_exact_variable_set_for_a_default_spec(self) -> None:
        env = provider_variables(spec(), api_root=WORKERS_AI_API_ROOT, account_id=ACCOUNT)
        p = "AURA_PROVIDER_CF_GPT_OSS_20B"
        assert env == {
            f"{p}_KIND": "openai_compatible",
            f"{p}_BASE_URL": f"{WORKERS_AI_API_ROOT}/accounts/{ACCOUNT}/ai/v1",
            f"{p}_MODEL": MODEL,
            f"{p}_API_KEY_ENV": f"{p}_API_KEY",
            f"{p}_QUALITY_TIER": "2",
            f"{p}_CONTEXT_WINDOW": "32768",
            f"{p}_MAX_OUTPUT_TOKENS": "2048",
            f"{p}_ENABLED": "true",
            f"{p}_HEALTH_PROBE": "passive",
            f"{p}_SUPPORTS_JSON_MODE": "false",
            f"{p}_MODALITIES": "text",
        }

    def test_the_key_value_is_never_part_of_the_set(self) -> None:
        env = provider_variables(spec(), api_root=WORKERS_AI_API_ROOT, account_id=ACCOUNT)
        assert key_variable("cf-gpt-oss-20b") not in env
        assert all(not is_secret_variable(name, value) for name, value in env.items())

    def test_health_is_always_passive_because_the_models_path_answers_405(self) -> None:
        for s in (spec(), spec(enabled=False), spec(rpm=5)):
            env = provider_variables(s, api_root=WORKERS_AI_API_ROOT, account_id=ACCOUNT)
            assert env[f"{provider_prefix(s.name)}_HEALTH_PROBE"] == "passive"

    def test_quotas_are_written_only_when_declared(self) -> None:
        s = spec(rpm=300, tpd=200_000, tokens_per_second=150.5, json_mode=True, vision=True, enabled=False)
        env = provider_variables(s, api_root=WORKERS_AI_API_ROOT, account_id=ACCOUNT)
        p = provider_prefix(s.name)
        assert env[f"{p}_RPM"] == "300" and env[f"{p}_TPD"] == "200000"
        assert f"{p}_RPD" not in env and f"{p}_TPM" not in env
        assert env[f"{p}_TOKENS_PER_SECOND"] == "150.5"
        assert env[f"{p}_SUPPORTS_JSON_MODE"] == "true"
        assert env[f"{p}_MODALITIES"] == "text,image"
        assert env[f"{p}_ENABLED"] == "false"

    def test_every_name_written_is_a_known_tail(self) -> None:
        s = spec(rpm=1, rpd=1, tpm=1, tpd=1, tokens_per_second=1)
        env = provider_variables(s, api_root=WORKERS_AI_API_ROOT, account_id=ACCOUNT)
        p = provider_prefix(s.name)
        tails = {name[len(p):] for name in env}
        assert tails == {f"_{t}" for t in PROVIDER_REQUIRED_TAILS + PROVIDER_OPTIONAL_TAILS}

    def test_a_custom_key_variable_is_named_not_stored(self) -> None:
        env = provider_variables(spec(), api_root=WORKERS_AI_API_ROOT, account_id=ACCOUNT, key_env="CF_AI_KEY")
        assert env["AURA_PROVIDER_CF_GPT_OSS_20B_API_KEY_ENV"] == "CF_AI_KEY"

    @pytest.mark.parametrize("key_env", ["", "lower", "1BAD", "HAS SPACE", "A" * 200, "A-B"])
    def test_a_bad_key_variable_name_is_refused(self, key_env: str) -> None:
        with pytest.raises(ValidationError):
            provider_variables(spec(), api_root=WORKERS_AI_API_ROOT, account_id=ACCOUNT, key_env=key_env)

    @pytest.mark.parametrize("account", ["", "acct-123", "G" * 32, ACCOUNT[:-1], ACCOUNT + "0", ACCOUNT.upper()])
    def test_a_malformed_account_id_is_refused(self, account: str) -> None:
        with pytest.raises(ValidationError):
            provider_variables(spec(), api_root=WORKERS_AI_API_ROOT, account_id=account)

    @pytest.mark.parametrize(
        "root", ["", "http://x.test/client/v4", "ftp://x.test/client/v4", "https://x.test/client/v4?x=1", "https://x.test/a b", "x.test"]
    )
    def test_the_api_root_must_be_a_plain_https_url(self, root: str) -> None:
        with pytest.raises(ValidationError):
            provider_variables(spec(), api_root=root, account_id=ACCOUNT)

    def test_a_trailing_slash_on_the_root_does_not_double_up(self) -> None:
        env = provider_variables(spec(), api_root=WORKERS_AI_API_ROOT + "/", account_id=ACCOUNT)
        assert env["AURA_PROVIDER_CF_GPT_OSS_20B_BASE_URL"] == f"{WORKERS_AI_API_ROOT}/accounts/{ACCOUNT}/ai/v1"


class TestProviderList:
    def test_a_new_provider_goes_before_local(self) -> None:
        assert providers_with("hosted1,local", "cf-a") == "hosted1,cf-a,local"
        assert providers_with("hosted1, hosted2 ,local", "cf-a") == "hosted1,hosted2,cf-a,local"

    def test_without_local_it_is_appended(self) -> None:
        assert providers_with("hosted1", "cf-a") == "hosted1,cf-a"
        assert providers_with("", "cf-a") == "cf-a"
        assert providers_with("  ", "cf-a") == "cf-a"

    def test_a_duplicate_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            providers_with("hosted1,cf-a,local", "cf-a")

    def test_removing_keeps_the_order_and_the_rest(self) -> None:
        assert providers_without("hosted1,cf-a,local", "cf-a") == "hosted1,local"
        assert providers_without("cf-a,hosted1", "cf-a") == "hosted1"

    def test_removing_an_unknown_provider_is_not_found(self) -> None:
        with pytest.raises(NotFoundError):
            providers_without("hosted1,local", "cf-a")

    def test_the_last_provider_cannot_be_removed(self) -> None:
        with pytest.raises(ValidationError):
            providers_without("cf-a", "cf-a")


class TestTheHostConstantAndTheHint:
    def test_the_api_root_is_https_on_a_host_the_inference_policy_admits(self) -> None:
        parts = urlsplit(WORKERS_AI_API_ROOT)
        assert parts.scheme == "https" and parts.path == "/client/v4" and not parts.query
        assert is_hosted_open_weight_host(parts.hostname or "") is not None
        assert not WORKERS_AI_API_ROOT.endswith("/")

    def test_the_token_hint_names_a_user_token_and_the_permissions_the_tab_needs(self) -> None:
        hint = PLATFORM_TOKEN_HINTS[Platform.CLOUDFLARE]
        for needle in ("cfut_", "API Tokens: Edit", "Workers AI: Read", "Account Analytics: Read"):
            assert needle in hint
        assert "account" in hint.lower()  # says an account-owned token is not accepted


# --- the real loader -------------------------------------------------------------------------


def _load(monkeypatch: pytest.MonkeyPatch, s: CloudflareProviderSpec, *, hosted_flag: bool = True) -> ProviderConfig:
    env = provider_variables(s, api_root=WORKERS_AI_API_ROOT, account_id=ACCOUNT)
    for key in list(env):
        monkeypatch.setenv(key, env[key])
    monkeypatch.setenv("AURA_ALLOW_HOSTED_INFERENCE", "true" if hosted_flag else "false")
    return ProviderConfig.from_env(s.name)


class TestAcceptedByTheRealLoader:
    def test_a_default_spec_loads_as_a_hosted_third_party_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = _load(monkeypatch, spec())
        assert cfg.kind == "openai_compatible" and cfg.model == MODEL
        assert cfg.base_url == f"{WORKERS_AI_API_ROOT}/accounts/{ACCOUNT}/ai/v1"
        assert cfg.api_key_env == "AURA_PROVIDER_CF_GPT_OSS_20B_API_KEY"
        assert cfg.health_probe == "passive"
        assert cfg.third_party is True and cfg.is_local is False

    def test_every_form_field_arrives_intact(self, monkeypatch: pytest.MonkeyPatch) -> None:
        s = spec(quality_tier=3, context_window=65536, max_output_tokens=4096, rpm=300, rpd=5000,
                 tpm=40000, tpd=900000, tokens_per_second=120.0, json_mode=True, vision=True, enabled=False)
        cfg = _load(monkeypatch, s)
        assert (cfg.quality_tier, cfg.context_window, cfg.max_output_tokens) == (3, 65536, 4096)
        assert (cfg.rpm, cfg.rpd, cfg.tpm, cfg.tpd) == (300, 5000, 40000, 900000)
        assert cfg.tokens_per_second == 120.0 and cfg.supports_json_mode is True
        assert cfg.modalities == ("text", "image") and cfg.enabled is False

    def test_the_default_data_ceiling_stays_internal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = _load(monkeypatch, spec())
        assert cfg.data_classification_ceiling.value == "internal"


class TestRefusedByTheRealLoader:
    def test_without_the_consent_flag_boot_would_fail(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with pytest.raises(ConfigurationError, match="AURA_ALLOW_HOSTED_INFERENCE"):
            _load(monkeypatch, spec(), hosted_flag=False)

    def test_a_model_that_is_not_open_weight_is_refused_even_with_the_flag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Well-formed for the spec (the spec checks shape, not policy); the loader is the policy.
        s = spec(name="cf-clairic", model="@cf/cloudflare/clairic")
        with pytest.raises(ConfigurationError, match="open-weight"):
            _load(monkeypatch, s)
