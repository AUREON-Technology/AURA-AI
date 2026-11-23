"""Every ADR-039 setting is documented and wired the same way in both stacks.

CLAUDE.md: the two compose files "are meant to stay at parity". Each new variable must appear in
the prod gateway, the dev gateway, ``deploy/.env.example`` and the backend README, default to its
off/empty value in compose, and be read by the settings loader under that exact name.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from aura_core.config.settings import load_settings

REPO = Path(__file__).resolve().parents[3]

#: variable -> its compose default (the "nothing changes" value)
SETTINGS = {
    "AURA_TOOL_POLICY_FILE": "",
    "AURA_AUTONOMY_ALLOW_AUTONOMOUS": "false",
    "AURA_AUTONOMY_DEFAULT": "standard",
    "AURA_SAFETY_REDACT_CONTEXT_SECRETS": "false",
    "AURA_SAFETY_REDACT_INPUT_SECRETS": "false",
    "AURA_SAFETY_REDACT_HISTORY_SECRETS": "false",
    "AURA_SANDBOX_ALLOWED_EGRESS_HOSTS": "",
}

PROD = yaml.safe_load((REPO / "deploy" / "docker-compose.prod.yml").read_text())
DEV = yaml.safe_load((REPO / "deploy" / "docker-compose.dev.yml").read_text())
ENV_EXAMPLE = (REPO / "deploy" / ".env.example").read_text()
README = (REPO / "aura_backend" / "README.md").read_text()


@pytest.mark.parametrize("name,default", SETTINGS.items())
def test_each_setting_is_wired_documented_and_defaults_to_nothing_changing(name: str, default: str) -> None:
    for label, env in (("prod", PROD["services"]["gateway"]["environment"]),
                       ("dev", DEV["services"]["dev-gateway"]["environment"])):
        assert env.get(name) == "${" + name + ":-" + default + "}", f"{name} missing or different in {label}"
    assert name in ENV_EXAMPLE, f"{name} is not in deploy/.env.example"
    assert name in README, f"{name} is not in aura_backend/README.md"


def test_the_defaults_leave_every_new_behaviour_off(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in SETTINGS:
        monkeypatch.delenv(name, raising=False)
    settings = load_settings(refresh=True, validate=False)
    assert settings.tools.policy_file == ""
    assert settings.tools.allow_autonomous_profile is False
    assert settings.tools.autonomy_default == "standard"
    assert (settings.safety.redact_secrets_in_context, settings.safety.redact_secrets_in_input,
            settings.safety.redact_secrets_in_history) == (False, False, False)
    assert settings.sandbox.allowed_egress_hosts == ()


def test_the_settings_loader_reads_each_variable_under_its_documented_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AURA_TOOL_POLICY_FILE", "/etc/aura/policy.json")
    monkeypatch.setenv("AURA_AUTONOMY_ALLOW_AUTONOMOUS", "true")
    monkeypatch.setenv("AURA_AUTONOMY_DEFAULT", "strict")
    monkeypatch.setenv("AURA_SAFETY_REDACT_CONTEXT_SECRETS", "true")
    monkeypatch.setenv("AURA_SAFETY_REDACT_INPUT_SECRETS", "true")
    monkeypatch.setenv("AURA_SAFETY_REDACT_HISTORY_SECRETS", "true")
    monkeypatch.setenv("AURA_SANDBOX_ALLOWED_EGRESS_HOSTS", "a.example.com")
    settings = load_settings(refresh=True, validate=False)
    assert settings.tools.policy_file == "/etc/aura/policy.json"
    assert settings.tools.allow_autonomous_profile is True
    assert settings.tools.autonomy_default == "strict"
    assert settings.safety.redact_secrets_in_context and settings.safety.redact_secrets_in_input
    assert settings.safety.redact_secrets_in_history
    assert settings.sandbox.allowed_egress_hosts == ("a.example.com",)


def test_autonomous_cannot_be_a_deployment_default(monkeypatch: pytest.MonkeyPatch) -> None:
    from aura_core.domain.errors import AuraError

    monkeypatch.setenv("AURA_AUTONOMY_DEFAULT", "autonomous")
    settings = load_settings(refresh=True, validate=False)
    with pytest.raises(AuraError) as caught:
        settings.validate()
    assert "AURA_AUTONOMY_DEFAULT" in str(caught.value.details)
