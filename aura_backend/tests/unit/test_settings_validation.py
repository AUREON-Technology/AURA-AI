"""Configuration parsing and the production safety gate.

Every check in ``AuraSettings.validate`` corresponds to a real finding in the Security Report.
The point of testing them is that a running process should never be able to reach a state where
one is violated -- SEC-05 was a single debug flag that turned every endpoint into an
unauthenticated one operating as a fixed mock user, and nothing refused to start.
"""

from __future__ import annotations

import json

import pytest

from aura_core.config.settings import (
    AuraSettings,
    Environment,
    ProviderConfig,
    _bool,
    _float,
    _int,
    _list,
    load_settings,
)
from aura_core.domain.errors import ConfigurationError


@pytest.fixture
def env(monkeypatch):
    """A clean AURA_-prefixed environment, so a stray variable cannot make a test pass."""
    import os

    for key in [k for k in os.environ if k.startswith("AURA_")]:
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def _problems(settings: AuraSettings) -> list[str]:
    with pytest.raises(ConfigurationError) as excinfo:
        settings.validate()
    return excinfo.value.details["problems"]


class TestPrimitiveParsing:
    @pytest.mark.parametrize("raw,expected", [
        ("true", True), ("TRUE", True), ("yes", True), ("1", True), ("on", True),
        ("false", False), ("no", False), ("0", False), ("off", False),
    ])
    def test_booleans(self, env, raw: str, expected: bool) -> None:
        env.setenv("AURA_FLAG", raw)
        assert _bool("FLAG", not expected) is expected

    def test_an_unset_boolean_takes_its_default(self, env) -> None:
        assert _bool("ABSENT", True) is True

    def test_an_ambiguous_boolean_refuses_to_start(self, env) -> None:
        """Guessing would silently disable a security control on a typo."""
        env.setenv("AURA_FLAG", "maybe")
        with pytest.raises(ConfigurationError) as excinfo:
            _bool("FLAG", False)
        assert excinfo.value.details["value"] == "maybe"

    def test_integers_with_bounds(self, env) -> None:
        env.setenv("AURA_N", "42")
        assert _int("N", 1) == 42
        assert _int("ABSENT", 7) == 7

    def test_a_non_numeric_integer_refuses_to_start(self, env) -> None:
        env.setenv("AURA_N", "lots")
        with pytest.raises(ConfigurationError):
            _int("N", 1)

    def test_integer_bounds_are_enforced(self, env) -> None:
        env.setenv("AURA_N", "0")
        with pytest.raises(ConfigurationError):
            _int("N", 1, minimum=1)
        env.setenv("AURA_N", "1000")
        with pytest.raises(ConfigurationError):
            _int("N", 1, maximum=10)

    def test_floats_with_bounds(self, env) -> None:
        env.setenv("AURA_F", "0.75")
        assert _float("F", 0.0) == pytest.approx(0.75)
        with pytest.raises(ConfigurationError):
            _float("F", 0.0, maximum=0.5)
        with pytest.raises(ConfigurationError):
            _float("F", 0.0, minimum=0.9)

    def test_a_non_numeric_float_refuses_to_start(self, env) -> None:
        env.setenv("AURA_F", "some")
        with pytest.raises(ConfigurationError):
            _float("F", 0.0)

    def test_lists_are_split_and_trimmed(self, env) -> None:
        env.setenv("AURA_ITEMS", " a , b ,, c ")
        assert _list("ITEMS") == ("a", "b", "c")
        assert _list("ABSENT", ("d",)) == ("d",)


class TestProviderConfig:
    def test_an_unknown_kind_is_refused_with_the_supported_set(self) -> None:
        with pytest.raises(ConfigurationError) as excinfo:
            ProviderConfig(name="p", kind="telepathy", model="m")
        assert "openai_compatible" in excinfo.value.details["supported"]

    def test_a_model_is_required_except_for_the_echo_provider(self) -> None:
        with pytest.raises(ConfigurationError):
            ProviderConfig(name="p", kind="gemini", model="")
        ProviderConfig(name="echo", kind="echo", model="")

    def test_http_kinds_require_a_base_url(self) -> None:
        with pytest.raises(ConfigurationError):
            ProviderConfig(name="p", kind="openai_compatible", model="m", base_url="")
        with pytest.raises(ConfigurationError):
            ProviderConfig(name="p", kind="ollama", model="m", base_url="")

    def test_an_api_key_is_read_at_use_and_never_stored(self, env) -> None:
        """So it cannot be dumped by as_dict(), a config echo, or a Sentry breadcrumb."""
        env.setenv("MY_PROVIDER_KEY", "sk-secret-value")
        provider = ProviderConfig(name="p", kind="gemini", model="m",
                                  api_key_env="MY_PROVIDER_KEY")

        assert provider.api_key == "sk-secret-value"
        assert "sk-secret-value" not in json.dumps(
            {f: getattr(provider, f) for f in ("name", "kind", "model", "api_key_env")}
        )

    def test_no_key_variable_means_an_empty_key(self) -> None:
        assert ProviderConfig(name="p", kind="echo", model="").api_key == ""

    def test_construction_from_the_environment(self, env) -> None:
        env.setenv("AURA_PROVIDER_LOCAL_KIND", "openai_compatible")
        env.setenv("AURA_PROVIDER_LOCAL_BASE_URL", "http://ollama:11434/v1")
        env.setenv("AURA_PROVIDER_LOCAL_MODEL", "qwen2.5:7b")
        env.setenv("AURA_PROVIDER_LOCAL_IS_LOCAL", "true")
        env.setenv("AURA_PROVIDER_LOCAL_QUALITY_TIER", "1")

        provider = ProviderConfig.from_env("local")
        assert provider.is_local is True
        assert provider.quality_tier == 1
        assert provider.model == "qwen2.5:7b"

    def test_a_hyphenated_name_maps_to_underscored_variables(self, env) -> None:
        env.setenv("AURA_PROVIDER_MY_NODE_MODEL", "m")
        env.setenv("AURA_PROVIDER_MY_NODE_BASE_URL", "http://host/v1")
        assert ProviderConfig.from_env("my-node").model == "m"


class TestDefaultProfile:
    def test_the_default_is_local_and_starts_without_any_configuration(self, env) -> None:
        settings = AuraSettings.from_env()
        assert settings.environment is Environment.LOCAL
        settings.validate()

    def test_an_unconfigured_deployment_gets_the_echo_provider(self, env) -> None:
        """So the platform is runnable end to end with no vendor account."""
        settings = AuraSettings.from_env()
        assert [p.kind for p in settings.llm.providers] == ["echo"]

    def test_an_unknown_routing_policy_names_the_supported_set(self, env) -> None:
        env.setenv("AURA_LLM_ROUTING_POLICY", "vibes")
        with pytest.raises(ConfigurationError) as excinfo:
            AuraSettings.from_env()
        assert "balanced" in excinfo.value.details["supported"]


class TestProductionGate:
    """SEC-05: a production process must refuse to start in an insecure configuration."""

    def _production(self, env, **overrides) -> AuraSettings:
        env.setenv("AURA_ENVIRONMENT", "production")
        for key, value in overrides.items():
            env.setenv(key, value)
        return AuraSettings.from_env()

    def test_debug_mode_is_refused(self, env) -> None:
        assert any("debug must be false" in p
                   for p in _problems(self._production(env, AURA_DEBUG="true")))

    def test_disabled_authentication_is_refused(self, env) -> None:
        assert any("require_auth" in p
                   for p in _problems(self._production(env, AURA_REQUIRE_AUTH="false")))

    def test_mock_authentication_is_refused(self, env) -> None:
        assert any("allow_mock_auth" in p
                   for p in _problems(self._production(env, AURA_ALLOW_MOCK_AUTH="true")))

    def test_disabled_safety_is_refused(self, env) -> None:
        assert any("safety.enabled" in p
                   for p in _problems(self._production(env, AURA_SAFETY_ENABLED="false")))

    def test_disabled_rate_limiting_is_refused(self, env) -> None:
        assert any("rate_limit_enabled" in p
                   for p in _problems(self._production(env, AURA_RATE_LIMIT_ENABLED="false")))

    def test_disabled_audit_logging_is_refused(self, env) -> None:
        assert any("audit_enabled" in p
                   for p in _problems(self._production(env, AURA_AUDIT_ENABLED="false")))

    @pytest.mark.parametrize(
        "variable", ["AURA_VECTOR_BACKEND", "AURA_DOCUMENT_BACKEND", "AURA_MEMORY_BACKEND"]
    )
    def test_in_memory_stores_are_refused_as_a_system_of_record(
        self, env, variable: str
    ) -> None:
        problems = _problems(self._production(env, **{variable: "memory"}))
        assert any("data would be lost on every restart" in p for p in problems)

    def test_the_echo_provider_is_refused(self, env) -> None:
        """It answers with the user's own message; in production that is not a fallback."""
        env.setenv("AURA_LLM_PROVIDERS", "test")
        env.setenv("AURA_PROVIDER_TEST_KIND", "echo")
        problems = _problems(self._production(env))
        assert any("'echo' test provider" in p for p in problems)

    def test_content_tracing_is_refused_by_default(self, env) -> None:
        problems = _problems(
            self._production(env, AURA_TRACE_CONTENT_SAMPLING="0.1")
        )
        assert any("persists prompt and output text" in p for p in problems)

    def test_every_problem_is_reported_at_once(self, env) -> None:
        """Reporting one at a time turns a misconfiguration into several restart cycles."""
        problems = _problems(self._production(
            env, AURA_DEBUG="true", AURA_REQUIRE_AUTH="false", AURA_SAFETY_ENABLED="false",
        ))
        assert len(problems) >= 3

    def test_a_correctly_configured_production_deployment_starts(self, env) -> None:
        env.setenv("AURA_ENVIRONMENT", "production")
        env.setenv("AURA_LLM_PROVIDERS", "local")
        env.setenv("AURA_PROVIDER_LOCAL_KIND", "openai_compatible")
        env.setenv("AURA_PROVIDER_LOCAL_BASE_URL", "http://ollama:11434/v1")
        env.setenv("AURA_PROVIDER_LOCAL_MODEL", "qwen2.5:7b")
        env.setenv("AURA_VECTOR_BACKEND", "qdrant")
        env.setenv("AURA_QDRANT_URL", "http://qdrant:6333")
        env.setenv("AURA_DOCUMENT_BACKEND", "postgres")
        env.setenv("AURA_MEMORY_BACKEND", "postgres")
        env.setenv("AURA_DATABASE_URL", "postgresql+asyncpg://aura@postgres/aura")
        env.setenv("AURA_CACHE_BACKEND", "redis")
        env.setenv("AURA_REDIS_URL", "redis://redis:6379/0")

        AuraSettings.from_env().validate()


class TestAlwaysOnChecks:
    def test_automatic_fine_tune_launch_is_never_permitted(self, env) -> None:
        """ADR-008. The setting exists so its absence is explicit and testable rather than
        merely undocumented."""
        env.setenv("AURA_LEARNING_FINETUNE_AUTO_LAUNCH", "true")
        problems = _problems(AuraSettings.from_env())
        assert any("recorded human approval" in p for p in problems)

    def test_at_least_one_provider_must_be_enabled(self, env) -> None:
        env.setenv("AURA_LLM_PROVIDERS", "a")
        env.setenv("AURA_PROVIDER_A_KIND", "echo")
        env.setenv("AURA_PROVIDER_A_ENABLED", "false")
        problems = _problems(AuraSettings.from_env())
        assert any("at least one LLM provider" in p for p in problems)

    def test_duplicate_provider_names_are_refused(self, env) -> None:
        """Two providers with one name makes routing and circuit state ambiguous."""
        env.setenv("AURA_LLM_PROVIDERS", "dup,dup")
        env.setenv("AURA_PROVIDER_DUP_KIND", "echo")
        problems = _problems(AuraSettings.from_env())
        assert any("must be unique" in p for p in problems)

    def test_a_cloud_provider_must_name_its_key_variable(self, env) -> None:
        env.setenv("AURA_LLM_PROVIDERS", "cloud")
        env.setenv("AURA_PROVIDER_CLOUD_KIND", "gemini")
        env.setenv("AURA_PROVIDER_CLOUD_MODEL", "gemini-2.5-flash")
        problems = _problems(AuraSettings.from_env())
        assert any("requires an api_key_env" in p for p in problems)

    def test_a_named_but_unset_key_variable_is_reported(self, env) -> None:
        """Otherwise the failure surfaces as a 401 on the first user request."""
        env.setenv("AURA_LLM_PROVIDERS", "cloud")
        env.setenv("AURA_PROVIDER_CLOUD_KIND", "gemini")
        env.setenv("AURA_PROVIDER_CLOUD_MODEL", "gemini-2.5-flash")
        env.setenv("AURA_PROVIDER_CLOUD_API_KEY_ENV", "GEMINI_KEY_THAT_IS_NOT_SET")
        problems = _problems(AuraSettings.from_env())
        assert any("which is unset" in p for p in problems)

    def test_qdrant_requires_its_url(self, env) -> None:
        env.setenv("AURA_VECTOR_BACKEND", "qdrant")
        assert any("AURA_QDRANT_URL" in p for p in _problems(AuraSettings.from_env()))

    def test_redis_requires_its_url(self, env) -> None:
        env.setenv("AURA_CACHE_BACKEND", "redis")
        assert any("AURA_REDIS_URL" in p for p in _problems(AuraSettings.from_env()))

    def test_postgres_backends_require_a_database_url(self, env) -> None:
        env.setenv("AURA_DOCUMENT_BACKEND", "postgres")
        assert any("AURA_DATABASE_URL" in p for p in _problems(AuraSettings.from_env()))

    def test_tracing_requires_a_collector_endpoint(self, env) -> None:
        env.setenv("AURA_TRACING_ENABLED", "true")
        assert any("AURA_OTLP_ENDPOINT" in p for p in _problems(AuraSettings.from_env()))


class TestCoherenceChecks:
    def test_a_chunk_target_above_its_maximum_is_refused(self, env) -> None:
        env.setenv("AURA_KNOWLEDGE_CHUNK_TOKENS", "2000")
        env.setenv("AURA_KNOWLEDGE_CHUNK_MAX_TOKENS", "1000")
        with pytest.raises(ConfigurationError):
            AuraSettings.from_env()

    def test_top_k_above_candidate_k_is_refused(self, env) -> None:
        """Reranking would have nothing to select from."""
        env.setenv("AURA_KNOWLEDGE_TOP_K", "50")
        env.setenv("AURA_KNOWLEDGE_CANDIDATE_K", "10")
        with pytest.raises(ConfigurationError):
            AuraSettings.from_env()


class TestIntrospection:
    def test_as_dict_never_contains_a_key(self, env) -> None:
        env.setenv("GEMINI_KEY", "sk-super-secret")
        env.setenv("AURA_LLM_PROVIDERS", "cloud")
        env.setenv("AURA_PROVIDER_CLOUD_KIND", "gemini")
        env.setenv("AURA_PROVIDER_CLOUD_MODEL", "gemini-2.5-flash")
        env.setenv("AURA_PROVIDER_CLOUD_API_KEY_ENV", "GEMINI_KEY")

        payload = AuraSettings.from_env().as_dict()
        assert "sk-super-secret" not in json.dumps(payload)
        assert payload["llm"]["providers"][0]["api_key_configured"] is True

    def test_a_missing_key_is_reported_as_not_configured(self, env) -> None:
        env.setenv("AURA_LLM_PROVIDERS", "cloud")
        env.setenv("AURA_PROVIDER_CLOUD_KIND", "gemini")
        env.setenv("AURA_PROVIDER_CLOUD_MODEL", "m")
        env.setenv("AURA_PROVIDER_CLOUD_API_KEY_ENV", "ABSENT_KEY")
        payload = AuraSettings.from_env().as_dict()
        assert payload["llm"]["providers"][0]["api_key_configured"] is False

    def test_as_dict_is_json_serialisable(self, env) -> None:
        json.dumps(AuraSettings.from_env().as_dict())

    def test_redaction_can_be_disabled_for_diffing(self, env) -> None:
        payload = AuraSettings.from_env().as_dict(redact=False)
        assert "api_key_configured" not in payload["llm"]["providers"][0]


class TestCaching:
    def test_settings_are_cached_because_config_must_not_change_mid_flight(self, env) -> None:
        first = load_settings(refresh=True, validate=False)
        env.setenv("AURA_DEBUG", "true")
        assert load_settings(validate=False) is first

    def test_refresh_rereads_the_environment(self, env) -> None:
        load_settings(refresh=True, validate=False)
        env.setenv("AURA_LOG_LEVEL", "ERROR")
        assert load_settings(refresh=True, validate=False).observability.log_level == "ERROR"

    def test_validation_can_be_skipped_for_tests(self, env) -> None:
        env.setenv("AURA_ENVIRONMENT", "production")
        env.setenv("AURA_DEBUG", "true")
        load_settings(refresh=True, validate=False)
        with pytest.raises(ConfigurationError):
            load_settings(refresh=True, validate=True)
