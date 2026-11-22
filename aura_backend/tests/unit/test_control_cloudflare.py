"""The Cloudflare tab's engine: providers, models and the account (console plan, stage 6, B3).

Everything runs against in-memory Railway, Cloudflare tokens and Cloudflare catalog (real state, no
network). The validator is the interesting part: ``_real_loader_rule`` boots every Cloudflare
provider in each *candidate* gateway environment through the real ``ProviderConfig`` loader, so a
change the engine proposes passes only if the gateway would really start with it.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

from aura_core.adapters.cloudflare_account import InMemoryCloudflareAccount
from aura_core.config.external_inference import HOSTED_OPEN_WEIGHT_HOSTS, WORKERS_AI_API_ROOT
from aura_core.config.settings import ProviderConfig
from aura_core.control.cloudflare import CloudflareControl
from aura_core.domain.cloudflare_provider import (
    CloudflareProviderSpec,
    key_variable,
    provider_prefix,
    provider_variables,
)
from aura_core.domain.control import Platform, PlatformCredential
from aura_core.domain.errors import ConfigurationError, NotFoundError, ValidationError
from aura_core.ports.control_plane import CloudflareModel

ROOT = "root@aura.ai"
ACCOUNT = "0123456789abcdef0123456789abcdef"
MODEL = "@cf/openai/gpt-oss-20b"
QWEN = "@cf/qwen/qwen3-30b-a3b-fp8"
OLD_CF = "old-cf-key-zq19-never-shown"
GROQ = "groq-key-zq19-never-shown"
PASTED = "pasted-cf-key-zq19-never-shown"
GROQ_HOST = next(host for host in HOSTED_OPEN_WEIGHT_HOSTS if "groq" in host)


@pytest.fixture(autouse=True)
def vault_key(monkeypatch):
    monkeypatch.setenv("AURA_SECRETS_ENCRYPTION_KEY", Fernet.generate_key().decode())


def spec(name: str = "cf-new", model: str = MODEL, **overrides) -> CloudflareProviderSpec:
    return CloudflareProviderSpec(name=name, model=model, context_window=32768, **overrides)


def gateway_env() -> dict[str, str]:
    env = {
        "AURA_ENVIRONMENT": "production",
        "AURA_ALLOW_HOSTED_INFERENCE": "true",
        "AURA_LLM_PROVIDERS": "hosted1,cf-old,local",
        # A hosted host may not be named in source (ADR-030): take it from the registry.
        "AURA_PROVIDER_HOSTED1_BASE_URL": f"https://{GROQ_HOST}/openai/v1",
        "AURA_PROVIDER_HOSTED1_MODEL": "openai/gpt-oss-120b",
        "AURA_PROVIDER_HOSTED1_API_KEY_ENV": "AURA_PROVIDER_HOSTED1_API_KEY",
        "AURA_PROVIDER_HOSTED1_API_KEY": GROQ,
        "AURA_PROVIDER_LOCAL_KIND": "ollama",
        "AURA_PROVIDER_LOCAL_BASE_URL": "http://ollama:11434",
        "AURA_PROVIDER_LOCAL_MODEL": "qwen2.5:3b",
    }
    env.update(provider_variables(spec("cf-old", rpm=300), api_root=WORKERS_AI_API_ROOT, account_id=ACCOUNT))
    env[key_variable("cf-old")] = OLD_CF
    return env


def _real_loader_rule(environ) -> str | None:
    """Boot each Cloudflare provider in the candidate environment through the real loader."""
    names = [n.strip() for n in environ.get("AURA_LLM_PROVIDERS", "").split(",") if n.strip()]
    with patch.dict(os.environ, dict(environ), clear=True):
        for name in names:
            if not environ.get(f"{provider_prefix(name)}_BASE_URL", "").startswith(WORKERS_AI_API_ROOT):
                continue
            try:
                ProviderConfig.from_env(name)
            except ConfigurationError as exc:
                return str(exc)
    return None


async def _connect(container, platform: Platform, target: str = "") -> None:
    await container.control_store.put_credential(PlatformCredential(
        platform=platform, secret_ref=f"ref-{platform.value}", configured_by=ROOT,
        configured_at=datetime.now(timezone.utc), target=target,
    ))


def _container(*, env: dict[str, str] | None = None):
    from aura_core.config import load_settings
    from aura_core.container import Container

    container = Container(load_settings(refresh=True, validate=False))
    container.railway_api.add_service("svc-gw", "gateway", env if env is not None else gateway_env())
    container.railway.self_service_id = "svc-gw"
    tokens = container.cloudflare_tokens
    container.old_cf_id = tokens.add("aura-cf-old", OLD_CF)
    container.settings_validator.refuse_when(_real_loader_rule)
    container.cf_account = InMemoryCloudflareAccount()
    container.cf = CloudflareControl(
        control=container.control, store=container.control_store, railway=container.railway,
        tokens=tokens, account=container.cf_account, api_root=WORKERS_AI_API_ROOT,
        provider_health=lambda: [{"provider": "cf-old", "state": "closed", "healthy": True}],
    )
    return container


async def _ready(container) -> None:
    await _connect(container, Platform.RAILWAY)
    await _connect(container, Platform.CLOUDFLARE, ACCOUNT)


def _env(container) -> dict[str, str]:
    return container.railway_api.raw["svc-gw"]


def _reason(caught) -> str:
    return caught.value.details["reason"]


def _never_leaked(container, *payloads) -> None:
    audit = json.dumps([getattr(e, "__dict__", str(e)) for e in container.audit._events], default=str)
    for secret in (OLD_CF, GROQ, PASTED):
        assert secret not in audit
        for payload in payloads:
            assert secret not in json.dumps(payload, default=str)


class TestOverview:
    async def test_nothing_is_known_until_railway_and_cloudflare_are_connected(self) -> None:
        container = _container()
        overview = await container.cf.overview()
        assert overview["gateway_ready"] is False and overview["connected"] is False
        assert overview["providers"] == [] and overview["account_id"] == ""

    async def test_it_lists_only_cloudflare_providers_with_their_state(self) -> None:
        container = _container()
        await _ready(container)
        overview = await container.cf.overview()
        assert overview["connected"] and overview["gateway_ready"] and overview["account_id"] == ACCOUNT
        assert overview["hosted_inference_allowed"] is True
        rows = {r["name"]: r for r in overview["providers"]}
        assert set(rows) == {"cf-old"}  # not the Groq slot, not the local fallback
        row = rows["cf-old"]
        assert row["model"] == MODEL and row["account_id"] == ACCOUNT and row["rpm"] == 300
        assert row["key_set"] is True and row["key_env"] == key_variable("cf-old")
        assert row["health_probe"] == "passive" and row["enabled"] is True
        assert row["healthy"] is True and row["state"] == "closed"
        _never_leaked(container, overview)

    async def test_the_consent_flag_reads_false_when_unset(self) -> None:
        container = _container()
        await _ready(container)
        _env(container).pop("AURA_ALLOW_HOSTED_INFERENCE")
        assert (await container.cf.overview())["hosted_inference_allowed"] is False


class TestModels:
    def _catalog(self, container) -> None:
        account = container.cf_account
        account.add_model(CloudflareModel(model=MODEL, task="Text Generation", description="open", context_window=128000))
        account.add_model(CloudflareModel(model="@cf/cloudflare/clairic", task="Text Generation"))
        account.add_model(CloudflareModel(model="@cf/baai/bge-m3", task="Text Embeddings"))
        account.add_model(CloudflareModel(model="@cf/moonshotai/kimi-k2.6", task="Text Generation", paid_only=True))

    async def test_each_model_says_whether_aura_can_use_it_and_why_not(self) -> None:
        container = _container()
        await _ready(container)
        self._catalog(container)
        body = await container.cf.models(task="")
        rows = {r["model"]: r for r in body["models"]}
        assert rows[MODEL]["can_add"] is True and rows[MODEL]["refusal"] == ""
        assert rows["@cf/cloudflare/clairic"]["can_add"] is False
        assert rows["@cf/cloudflare/clairic"]["refusal"] == "not_open_weight"
        assert rows["@cf/baai/bge-m3"]["can_add"] is False and rows["@cf/baai/bge-m3"]["refusal"] == "not_chat"
        assert rows["@cf/moonshotai/kimi-k2.6"]["paid_only"] is True
        assert rows["@cf/moonshotai/kimi-k2.6"]["can_add"] is True  # allowed, but needs a paid plan
        assert rows[MODEL]["context_window"] == 128000

    async def test_search_and_paging_reach_the_catalog_for_the_connected_account(self) -> None:
        container = _container()
        await _ready(container)
        self._catalog(container)
        body = await container.cf.models(task="Text Generation", search="gpt", page=1, per_page=50)
        assert [r["model"] for r in body["models"]] == [MODEL]
        assert (body["page"], body["per_page"]) == (1, 50)
        assert container.cf_account.calls[-1]["account_id"] == ACCOUNT

    async def test_it_needs_cloudflare_to_be_connected_with_an_account(self) -> None:
        container = _container()
        with pytest.raises(ValidationError) as caught:
            await container.cf.models()
        assert _reason(caught) == "cloudflare_not_connected"
        await _connect(container, Platform.CLOUDFLARE, "")
        with pytest.raises(ValidationError) as caught:
            await container.cf.models()
        assert _reason(caught) == "no_account"


class TestAdd:
    async def test_add_creates_its_key_and_writes_one_watched_change(self) -> None:
        container = _container()
        await _ready(container)
        change = await container.cf.add_provider(spec(), key="create", actor=ROOT, confirm="cf-new")
        assert change["action"] == "add_cloudflare_provider" and change["status"] == "watching"
        env = _env(container)
        p = "AURA_PROVIDER_CF_NEW"
        assert env["AURA_LLM_PROVIDERS"] == "hosted1,cf-old,cf-new,local"
        assert env[f"{p}_MODEL"] == MODEL and env[f"{p}_HEALTH_PROBE"] == "passive"
        key = env[f"{p}_API_KEY"]
        assert key and key != OLD_CF
        created = container.cloudflare_tokens.created
        assert len(created) == 1 and created[0][1] == ACCOUNT
        assert await container.cloudflare_tokens.identify(key) == created[0][0]
        assert change["detail"]["provider"] == "cf-new" and change["detail"]["key"] == "created"
        assert change["detail"]["cloudflare_token_id"] == created[0][0]
        assert container.settings_validator.calls[-1]["AURA_LLM_PROVIDERS"].endswith("cf-new,local")
        _never_leaked(container, change)
        assert key not in json.dumps(change)

    async def test_a_pasted_key_is_used_and_no_token_is_created(self) -> None:
        container = _container()
        await _ready(container)
        change = await container.cf.add_provider(spec(), key="paste", key_value=PASTED, actor=ROOT, confirm="cf-new")
        assert _env(container)[key_variable("cf-new")] == PASTED
        assert container.cloudflare_tokens.created == [] and change["detail"]["key"] == "pasted"
        assert PASTED not in json.dumps(change)
        _never_leaked(container, change)

    @pytest.mark.parametrize("value", [None, "", "   "])
    async def test_paste_without_a_value_is_refused(self, value) -> None:
        container = _container()
        await _ready(container)
        with pytest.raises(ValidationError) as caught:
            await container.cf.add_provider(spec(), key="paste", key_value=value, actor=ROOT, confirm="cf-new")
        assert _reason(caught) == "value_required"

    async def test_an_unknown_key_mode_is_refused(self) -> None:
        container = _container()
        await _ready(container)
        with pytest.raises(ValidationError) as caught:
            await container.cf.add_provider(spec(), key="keep", actor=ROOT, confirm="cf-new")
        assert _reason(caught) == "bad_key_mode"

    async def test_without_consent_nothing_is_written_and_no_token_is_made(self) -> None:
        container = _container()
        await _ready(container)
        _env(container).pop("AURA_ALLOW_HOSTED_INFERENCE")
        before = dict(_env(container))
        with pytest.raises(ValidationError) as caught:
            await container.cf.add_provider(spec(), key="create", actor=ROOT, confirm="cf-new")
        assert _reason(caught) == "hosted_inference_not_consented"
        assert _env(container) == before and container.cloudflare_tokens.created == []

    async def test_with_consent_the_flag_is_set_in_the_same_change(self) -> None:
        container = _container()
        await _ready(container)
        _env(container).pop("AURA_ALLOW_HOSTED_INFERENCE")
        change = await container.cf.add_provider(
            spec(), key="create", enable_consent=True, actor=ROOT, confirm="cf-new",
        )
        assert _env(container)["AURA_ALLOW_HOSTED_INFERENCE"] == "true"
        assert change["detail"]["consent_enabled"] is True

    async def test_consent_already_given_is_not_rewritten(self) -> None:
        container = _container()
        await _ready(container)
        change = await container.cf.add_provider(
            spec(), key="create", enable_consent=True, actor=ROOT, confirm="cf-new",
        )
        assert change["detail"]["consent_enabled"] is False

    async def test_a_model_that_is_not_open_weight_is_refused_before_anything_happens(self) -> None:
        container = _container()
        await _ready(container)
        before = dict(_env(container))
        with pytest.raises(ValidationError) as caught:
            await container.cf.add_provider(
                spec(model="@cf/cloudflare/clairic"), key="create", actor=ROOT, confirm="cf-new",
            )
        assert _reason(caught) == "not_open_weight"
        assert _env(container) == before and container.cloudflare_tokens.created == []

    async def test_a_name_already_in_use_is_refused(self) -> None:
        container = _container()
        await _ready(container)
        with pytest.raises(ValidationError) as caught:
            await container.cf.add_provider(spec("cf-old"), key="create", actor=ROOT, confirm="cf-old")
        assert _reason(caught) == "provider_exists"

    async def test_a_name_that_shares_a_variable_prefix_is_refused(self) -> None:
        container = _container()
        await _ready(container)
        _env(container)["AURA_LLM_PROVIDERS"] = "hosted1,cf_x,cf-old,local"  # "cf_x" and "cf-x" share a prefix
        with pytest.raises(ValidationError) as caught:
            await container.cf.add_provider(spec("cf-x"), key="create", actor=ROOT, confirm="cf-x")
        assert _reason(caught) == "provider_name_collision"

    async def test_the_typed_confirmation_is_the_provider_name(self) -> None:
        container = _container()
        await _ready(container)
        for wrong in (None, "", "gateway", "CF-NEW"):
            with pytest.raises(ValidationError) as caught:
                await container.cf.add_provider(spec(), key="create", actor=ROOT, confirm=wrong)
            assert _reason(caught) == "confirmation_required"
        assert "AURA_PROVIDER_CF_NEW_MODEL" not in _env(container)

    async def test_it_needs_railway(self) -> None:
        container = _container()
        await _connect(container, Platform.CLOUDFLARE, ACCOUNT)
        with pytest.raises(ValidationError) as caught:
            await container.cf.add_provider(spec(), key="create", actor=ROOT, confirm="cf-new")
        assert _reason(caught) == "railway_not_connected"

    async def test_it_needs_cloudflare_connected_with_an_account(self) -> None:
        container = _container()
        await _connect(container, Platform.RAILWAY)
        with pytest.raises(ValidationError) as caught:
            await container.cf.add_provider(spec(), key="create", actor=ROOT, confirm="cf-new")
        assert _reason(caught) == "cloudflare_not_connected"
        await _connect(container, Platform.CLOUDFLARE, "")
        with pytest.raises(ValidationError) as caught:
            await container.cf.add_provider(spec(), key="paste", key_value=PASTED, actor=ROOT, confirm="cf-new")
        assert _reason(caught) == "no_account"

    async def test_if_the_gateway_would_not_start_the_key_it_made_is_revoked(self) -> None:
        container = _container()
        await _ready(container)
        container.settings_validator.refuse_when(
            lambda environ: "the gateway would not start" if "AURA_PROVIDER_CF_NEW_MODEL" in environ else None
        )
        before = dict(_env(container))
        with pytest.raises(ValidationError) as caught:
            await container.cf.add_provider(spec(), key="create", actor=ROOT, confirm="cf-new")
        assert _reason(caught) == "settings_invalid"
        assert _env(container) == before
        created = container.cloudflare_tokens.created
        assert len(created) == 1 and container.cloudflare_tokens.revoked == [created[0][0]]

    async def test_a_dry_run_checks_everything_and_changes_nothing(self) -> None:
        container = _container()
        await _ready(container)
        before = dict(_env(container))
        result = await container.cf.add_provider(spec(), key="create", actor=ROOT, confirm="cf-new", dry_run=True)
        assert result["ok"] is True and result["creates_key"] is True and result["enables_consent"] is False
        names = {v["name"] for v in result["variables"]}
        assert "AURA_LLM_PROVIDERS" in names and "AURA_PROVIDER_CF_NEW_MODEL" in names
        assert _env(container) == before and container.cloudflare_tokens.created == []
        assert container.settings_validator.calls  # it was validated


class TestReplace:
    async def test_the_model_and_quotas_change_and_an_undeclared_quota_is_deleted(self) -> None:
        container = _container()
        await _ready(container)
        change = await container.cf.replace_provider(
            "cf-old", spec("cf-old", model=QWEN), actor=ROOT, confirm="cf-old",
        )
        env = _env(container)
        assert change["action"] == "replace_cloudflare_provider" and change["status"] == "watching"
        assert env["AURA_PROVIDER_CF_OLD_MODEL"] == QWEN
        assert "AURA_PROVIDER_CF_OLD_RPM" not in env  # rpm 300 -> undeclared
        assert env[key_variable("cf-old")] == OLD_CF  # the key is kept
        assert env["AURA_LLM_PROVIDERS"] == "hosted1,cf-old,local"
        assert container.cloudflare_tokens.created == []
        _never_leaked(container, change)

    async def test_the_passive_probe_is_applied_to_a_provider_still_on_the_default(self) -> None:
        container = _container()
        await _ready(container)
        _env(container)["AURA_PROVIDER_CF_OLD_HEALTH_PROBE"] = "active"
        await container.cf.replace_provider("cf-old", spec("cf-old", rpm=300), actor=ROOT, confirm="cf-old")
        assert _env(container)["AURA_PROVIDER_CF_OLD_HEALTH_PROBE"] == "passive"

    async def test_a_new_key_goes_into_the_variable_the_provider_already_reads(self) -> None:
        container = _container()
        await _ready(container)
        env = _env(container)
        env["AURA_PROVIDER_CF_OLD_API_KEY_ENV"] = "AURA_CF_KEY"
        env["AURA_CF_KEY"] = OLD_CF
        env.pop(key_variable("cf-old"))
        change = await container.cf.replace_provider(
            "cf-old", spec("cf-old", rpm=300), key="create", actor=ROOT, confirm="cf-old",
        )
        assert env["AURA_PROVIDER_CF_OLD_API_KEY_ENV"] == "AURA_CF_KEY"
        assert env["AURA_CF_KEY"] != OLD_CF and key_variable("cf-old") not in env
        assert container.cloudflare_tokens.revoked == []  # the old token stays until revoked
        assert change["detail"]["key"] == "created"
        _never_leaked(container, change)

    async def test_a_pasted_key_replaces_the_old_one(self) -> None:
        container = _container()
        await _ready(container)
        await container.cf.replace_provider(
            "cf-old", spec("cf-old", rpm=300), key="paste", key_value=PASTED, actor=ROOT, confirm="cf-old",
        )
        assert _env(container)[key_variable("cf-old")] == PASTED

    async def test_nothing_to_change_is_reported(self) -> None:
        container = _container()
        await _ready(container)
        with pytest.raises(ValidationError) as caught:
            await container.cf.replace_provider("cf-old", spec("cf-old", rpm=300), actor=ROOT, confirm="cf-old")
        assert _reason(caught) == "no_changes"

    async def test_refusals(self) -> None:
        container = _container()
        await _ready(container)
        with pytest.raises(ValidationError) as caught:  # not a Cloudflare provider
            await container.cf.replace_provider("hosted1", spec("hosted1"), actor=ROOT, confirm="hosted1")
        assert _reason(caught) == "not_a_cloudflare_provider"
        with pytest.raises(NotFoundError):
            await container.cf.replace_provider("cf-missing", spec("cf-missing"), actor=ROOT, confirm="cf-missing")
        with pytest.raises(ValidationError) as caught:  # the name is fixed once added
            await container.cf.replace_provider("cf-old", spec("cf-other"), actor=ROOT, confirm="cf-old")
        assert _reason(caught) == "name_mismatch"
        with pytest.raises(ValidationError) as caught:
            await container.cf.replace_provider(
                "cf-old", spec("cf-old", model="@cf/cloudflare/clairic"), actor=ROOT, confirm="cf-old",
            )
        assert _reason(caught) == "not_open_weight"
        with pytest.raises(ValidationError) as caught:
            await container.cf.replace_provider("cf-old", spec("cf-old", model=QWEN), actor=ROOT, confirm="nope")
        assert _reason(caught) == "confirmation_required"
        assert _env(container)["AURA_PROVIDER_CF_OLD_MODEL"] == MODEL

    async def test_a_dry_run_changes_nothing(self) -> None:
        container = _container()
        await _ready(container)
        before = dict(_env(container))
        result = await container.cf.replace_provider(
            "cf-old", spec("cf-old", model=QWEN), actor=ROOT, confirm="cf-old", dry_run=True,
        )
        assert result["ok"] is True and _env(container) == before


class TestRemove:
    async def test_it_removes_the_name_and_every_variable_of_the_provider(self) -> None:
        container = _container()
        await _ready(container)
        _env(container)["AURA_PROVIDER_CF_OLD_POOLS"] = "reasoning"  # set from the AURA tab, not by us
        change = await container.cf.remove_provider("cf-old", actor=ROOT, confirm="cf-old")
        env = _env(container)
        assert change["action"] == "remove_cloudflare_provider" and change["status"] == "watching"
        assert env["AURA_LLM_PROVIDERS"] == "hosted1,local"
        assert not [name for name in env if name.startswith("AURA_PROVIDER_CF_OLD_")]
        assert env["AURA_PROVIDER_HOSTED1_API_KEY"] == GROQ  # other providers are untouched
        # The token is revoked after the change succeeds (B4); here it is only named.
        assert change["detail"]["revoke_token_id"] == container.old_cf_id
        assert container.cloudflare_tokens.revoked == []
        _never_leaked(container, change)

    async def test_a_provider_whose_name_is_a_prefix_of_another_does_not_take_it_with_it(self) -> None:
        container = _container()
        await _ready(container)
        env = _env(container)
        env["AURA_LLM_PROVIDERS"] = "hosted1,cf,cf-old,local"
        env.update(provider_variables(spec("cf"), api_root=WORKERS_AI_API_ROOT, account_id=ACCOUNT))
        env[key_variable("cf")] = PASTED
        await container.cf.remove_provider("cf", actor=ROOT, confirm="cf")
        assert not [n for n in env if n.startswith("AURA_PROVIDER_CF_") and not n.startswith("AURA_PROVIDER_CF_OLD_")]
        assert env["AURA_PROVIDER_CF_OLD_MODEL"] == MODEL and env[key_variable("cf-old")] == OLD_CF

    async def test_a_key_shared_with_another_provider_is_neither_deleted_nor_revoked(self) -> None:
        container = _container()
        await _ready(container)
        env = _env(container)
        env["AURA_LLM_PROVIDERS"] = "hosted1,cf-old,cf-two,local"
        env.update(provider_variables(spec("cf-two"), api_root=WORKERS_AI_API_ROOT, account_id=ACCOUNT, key_env="AURA_CF_KEY"))
        env["AURA_PROVIDER_CF_OLD_API_KEY_ENV"] = "AURA_CF_KEY"
        env["AURA_CF_KEY"] = OLD_CF
        env.pop(key_variable("cf-old"))
        change = await container.cf.remove_provider("cf-old", actor=ROOT, confirm="cf-old")
        assert env["AURA_CF_KEY"] == OLD_CF  # cf-two still reads it
        assert change["detail"]["revoke_token_id"] is None

    async def test_the_only_provider_cannot_be_removed(self) -> None:
        container = _container()
        await _ready(container)
        _env(container)["AURA_LLM_PROVIDERS"] = "cf-old"
        with pytest.raises(ValidationError) as caught:
            await container.cf.remove_provider("cf-old", actor=ROOT, confirm="cf-old")
        assert _reason(caught) == "last_provider"

    async def test_refusals(self) -> None:
        container = _container()
        await _ready(container)
        with pytest.raises(ValidationError) as caught:
            await container.cf.remove_provider("hosted1", actor=ROOT, confirm="hosted1")
        assert _reason(caught) == "not_a_cloudflare_provider"
        with pytest.raises(NotFoundError):
            await container.cf.remove_provider("cf-missing", actor=ROOT, confirm="cf-missing")
        with pytest.raises(ValidationError) as caught:
            await container.cf.remove_provider("cf-old", actor=ROOT, confirm="gateway")
        assert _reason(caught) == "confirmation_required"
        assert "cf-old" in _env(container)["AURA_LLM_PROVIDERS"]

    async def test_a_dry_run_changes_nothing_and_names_the_token_it_would_revoke(self) -> None:
        container = _container()
        await _ready(container)
        before = dict(_env(container))
        result = await container.cf.remove_provider("cf-old", actor=ROOT, confirm="cf-old", dry_run=True)
        assert result["ok"] is True and result["revoke_token_id"] == container.old_cf_id
        assert _env(container) == before and container.cloudflare_tokens.revoked == []

    async def test_a_key_that_is_not_a_known_token_is_not_revoked(self) -> None:
        container = _container()
        await _ready(container)
        _env(container)[key_variable("cf-old")] = "a-key-cloudflare-does-not-know"
        change = await container.cf.remove_provider("cf-old", actor=ROOT, confirm="cf-old")
        assert change["detail"]["revoke_token_id"] is None
