"""The hosted open-weight slots are wired the same way everywhere they need to be (ADR-030 Phase 5).

Compose forwards only the variables a service names, so a setting the code reads but a compose
file does not pass is silently inert -- the failure ``test_hfspace_health_timeout_plumbing.py``
already guards for the HF Space provider. The hosted slots add three more ways to get it wrong:

* **A worker that lacks a slot fails to boot.** ``AURA_LLM_PROVIDERS`` is shared between the
  gateway and every worker, and a listed provider with no base URL is a ``ConfigurationError``.
  (Workers never *route* to a slot -- their generations are unclassified, hence SENSITIVE -- but
  they still construct it.)
* **A missing secret file stops a container from starting at all.** Every service that mounts a
  slot key needs ``bootstrap-secrets.sh`` to have created it, which ``vps-update.sh`` runs on
  every deploy.
* **Prod and dev drift.** The two stacks share a host (CLAUDE.md's parity rule), so the slot
  block is checked to be identical in both.

The last test builds a ``ProviderConfig`` from the compose defaults, which is what proves that
every default parses -- an empty string where the code wants an integer or a boolean would pass
every structural check here and fail only on the VPS.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

_DEPLOY = Path(__file__).resolve().parents[3] / "deploy"
_COMPOSE = {
    "prod": _DEPLOY / "docker-compose.prod.yml",
    "dev": _DEPLOY / "docker-compose.dev.yml",
}
_BOOTSTRAP = _DEPLOY / "scripts" / "bootstrap-secrets.sh"
_SLOTS = (1, 2, 3)

#: Every per-provider setting ADR-030 introduced, plus the ones a hosted slot cannot work without.
#: A suffix ProviderConfig.from_env reads that is missing here is a knob the VPS cannot turn.
_REQUIRED_SUFFIXES = (
    "KIND", "MODEL", "BASE_URL", "IS_LOCAL", "API_KEY_ENV", "API_KEY_FILE",
    "QUALITY_TIER", "CONTEXT_WINDOW", "MAX_OUTPUT_TOKENS", "TIMEOUT_S", "HEALTH_TIMEOUT_S",
    "MAX_CONCURRENCY", "RPM", "RPD", "TPM", "TPD", "HEALTH_PATH", "MAX_DATA_CLASSIFICATION",
    "TOKENS_PER_SECOND", "EXTRA_BODY", "SUPPORTS_JSON_MODE", "SUPPORTS_TOOLS",
)
_DEFAULT = re.compile(r"^\$\{[A-Z0-9_]+:-(.*)\}$")


def _load(path: Path) -> dict:
    assert path.is_file(), f"{path} is missing; these checks would pass vacuously"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module", params=sorted(_COMPOSE))
def compose(request) -> tuple[str, dict]:
    return request.param, _load(_COMPOSE[request.param])


def _provider_services(doc: dict) -> dict[str, dict]:
    """Every service that builds LLM providers, with its environment already merge-resolved."""
    out = {}
    for name, service in doc["services"].items():
        env = service.get("environment")
        if isinstance(env, dict) and "AURA_LLM_PROVIDERS" in env:
            out[name] = service
    return out


def _slot_env(env: dict) -> dict[str, str]:
    keys = ("AURA_ALLOW_HOSTED_INFERENCE", "AURA_LLM_ROUTING_POLICY", "AURA_LLM_RATE_LIMIT_COOLDOWN_S")
    return {
        k: str(v) for k, v in env.items()
        if k.startswith("AURA_PROVIDER_HOSTED") or k in keys
    }


def test_both_stacks_have_provider_services(compose) -> None:
    label, doc = compose
    services = _provider_services(doc)
    assert any("gateway" in name for name in services), f"{label}: no gateway builds providers?"
    assert len(services) >= 2, f"{label}: expected the gateway and at least one worker"


def test_every_provider_service_gets_every_slot(compose) -> None:
    label, doc = compose
    for name, service in _provider_services(doc).items():
        env = service["environment"]
        for n in _SLOTS:
            missing = [s for s in _REQUIRED_SUFFIXES if f"AURA_PROVIDER_HOSTED{n}_{s}" not in env]
            assert not missing, f"{label}/{name}: slot {n} lacks {missing}"
        assert "AURA_ALLOW_HOSTED_INFERENCE" in env, f"{label}/{name}: opt-in flag not forwarded"


def test_every_provider_service_mounts_every_slot_key(compose) -> None:
    label, doc = compose
    defined = set(doc.get("secrets") or {})
    for name, service in _provider_services(doc).items():
        mounted = set(service.get("secrets") or [])
        for n in _SLOTS:
            secret = f"hosted{n}_api_key"
            assert secret in defined, f"{label}: secret {secret} is not declared"
            assert secret in mounted, f"{label}/{name}: does not mount {secret}"
            env = service["environment"]
            assert env[f"AURA_PROVIDER_HOSTED{n}_API_KEY_FILE"] == f"/run/secrets/{secret}"
            assert env[f"AURA_PROVIDER_HOSTED{n}_API_KEY_ENV"] == f"AURA_PROVIDER_HOSTED{n}_API_KEY"


def test_a_slot_is_never_local(compose) -> None:
    """``is_local`` steers local_first and sensitive routing; ADR-030 §4 refuses it anyway."""
    label, doc = compose
    for name, service in _provider_services(doc).items():
        for n in _SLOTS:
            assert service["environment"][f"AURA_PROVIDER_HOSTED{n}_IS_LOCAL"] == "false", (
                f"{label}/{name}: slot {n}"
            )


def test_the_opt_in_defaults_to_an_explicit_false(compose) -> None:
    """An empty boolean raises in aura_core, and that error would hide the one explaining the opt-in."""
    label, doc = compose
    for name, service in _provider_services(doc).items():
        raw = service["environment"]["AURA_ALLOW_HOSTED_INFERENCE"]
        match = _DEFAULT.match(raw)
        assert match and match.group(1) == "false", f"{label}/{name}: {raw!r}"


def test_prod_and_dev_wire_the_slots_identically() -> None:
    prod = _provider_services(_load(_COMPOSE["prod"]))
    dev = _provider_services(_load(_COMPOSE["dev"]))
    reference = _slot_env(next(iter(prod.values()))["environment"])
    for label, services in (("prod", prod), ("dev", dev)):
        for name, service in services.items():
            assert _slot_env(service["environment"]) == reference, (
                f"{label}/{name} wires the hosted slots differently from prod's gateway"
            )


def test_bootstrap_creates_every_slot_secret() -> None:
    script = _BOOTSTRAP.read_text(encoding="utf-8")
    for n in _SLOTS:
        assert re.search(rf"^generate hosted{n}_api_key\b", script, re.M), (
            f"bootstrap-secrets.sh does not create hosted{n}_api_key; docker compose would refuse "
            "to start every service that mounts it"
        )


@pytest.mark.parametrize("n", _SLOTS)
def test_the_compose_defaults_build_a_valid_provider(n: int, monkeypatch) -> None:
    """Every default must parse. The structural checks above cannot see that."""
    from aura_core.config.external_inference import HOSTED_OPEN_WEIGHT_HOSTS
    from aura_core.config.settings import ProviderConfig
    from aura_core.domain.llm import DataClassification
    from aura_core.llm import capabilities_from_config

    env = _provider_services(_load(_COMPOSE["prod"]))["gateway"]["environment"]
    for key, raw in env.items():
        if not key.startswith(f"AURA_PROVIDER_HOSTED{n}_") and key != "AURA_ALLOW_HOSTED_INFERENCE":
            continue
        match = _DEFAULT.match(str(raw))
        monkeypatch.setenv(key, match.group(1) if match else str(raw))
    # What an operator adds in .env to switch the slot on. The host comes from the registry so
    # this file does not name a hosted endpoint itself (see test_no_external_inference.py).
    monkeypatch.setenv("AURA_ALLOW_HOSTED_INFERENCE", "true")
    monkeypatch.setenv(f"AURA_PROVIDER_HOSTED{n}_BASE_URL", f"https://{HOSTED_OPEN_WEIGHT_HOSTS[0]}/openai/v1")
    monkeypatch.setenv(f"AURA_PROVIDER_HOSTED{n}_MODEL", "openai/gpt-oss-120b")

    config = ProviderConfig.from_env(f"hosted{n}")
    caps = capabilities_from_config(config)
    assert config.is_local is False
    assert config.api_key_env == f"AURA_PROVIDER_HOSTED{n}_API_KEY"
    assert caps.third_party is True
    assert caps.max_data_classification is DataClassification.INTERNAL
    assert (config.rpm, config.rpd, config.tpm, config.tpd) == (0, 0, 0, 0)
    assert config.health_path == "/models"
    assert config.tokens_per_second == 0.0 and config.extra_body_fields == {}
