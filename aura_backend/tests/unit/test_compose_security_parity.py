"""Security-relevant compose settings the two VPS stacks must agree on (audit FSA-010, FSA-012,
FSA-100; CLAUDE.md's parity rule).

The prod (`aura`) and dev (`aura-dev`) stacks share one host, so a hardening applied to only one
leaves the other as the real attack surface. Three things had drifted or were never set:

* **Forwarded headers were trusted from any container** (`--forwarded-allow-ips='*'`): any
  service on the gateway's network could spoof `X-Forwarded-For`, and with it the per-IP limiter
  and every audit `source_ip`. Only nginx terminates TLS, so only nginx's address may be trusted;
  that address has to be static for the flag to name it.
* **The production CORS default listed localhost origins with credentials.** A browser page on a
  developer's machine could call the production API with the user's cookies.
* **Dev and prod disagreed on the sandbox network name, the mock-attestation default and the
  sandbox resource limits.** The dev value for the network did not even name a network Docker
  would create.
"""

from __future__ import annotations

import ipaddress
import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOY = _ROOT / "deploy"
_COMPOSE = {"prod": _DEPLOY / "docker-compose.prod.yml", "dev": _DEPLOY / "docker-compose.dev.yml"}
_GATEWAY = {"prod": "gateway", "dev": "dev-gateway"}
_PROJECT = {"prod": "aura", "dev": "aura-dev"}
_DEFAULT = re.compile(r"^\$\{[A-Z0-9_]+:-(.*)\}$")


def _load(name: str) -> dict:
    return yaml.safe_load(_COMPOSE[name].read_text(encoding="utf-8"))


def _default(value: str) -> str:
    """The compose default of `${VAR:-default}`, or the literal when there is no variable."""
    match = _DEFAULT.match(str(value))
    return match.group(1) if match else str(value)


def _command(service: dict) -> str:
    command = service["command"]
    return command if isinstance(command, str) else " ".join(command)


def _forwarded_allow_ips(service: dict) -> str:
    match = re.search(r"--forwarded-allow-ips[= ]'?([^' ]+)'?", _command(service))
    assert match, "the gateway command must carry --forwarded-allow-ips"
    return match.group(1)


class TestOnlyNginxMaySetForwardedHeaders:
    """FSA-012. nginx is the only container with a published port (SEC-12), so it is the only
    peer whose X-Forwarded-For means anything. uvicorn 0.52 takes exact addresses or networks
    for the flag, never names, so nginx gets a static address on each network a gateway shares
    with it."""

    def _nginx_addresses(self) -> dict[str, str]:
        prod = _load("prod")
        nginx = prod["services"]["nginx"]
        networks = nginx.get("networks")
        assert isinstance(networks, dict), "nginx needs per-network settings to carry a static address"
        addresses = {
            net: cfg["ipv4_address"]
            for net, cfg in networks.items()
            if isinstance(cfg, dict) and cfg.get("ipv4_address")
        }
        assert addresses, "nginx has no static address on any network"
        return addresses

    @pytest.mark.parametrize("name", sorted(_COMPOSE))
    def test_the_gateway_trusts_exactly_nginx_s_static_address(self, name: str) -> None:
        gateway = _load(name)["services"][_GATEWAY[name]]
        trusted = _forwarded_allow_ips(gateway)
        assert trusted != "*", f"{name}: any container can spoof X-Forwarded-For"
        nginx = self._nginx_addresses()
        gateway_networks = gateway["networks"]
        shared = [net for net in gateway_networks if net in nginx]
        assert shared, f"{name}: the gateway shares no network on which nginx has a static address"
        assert trusted in {nginx[net] for net in shared}, (
            f"{name}: trusts {trusted!r}, but nginx reaches this gateway from {[nginx[n] for n in shared]}"
        )

    def test_each_static_address_lies_in_its_network_s_declared_subnet(self) -> None:
        """A static address outside the network's pool is refused by Docker at `compose up`."""
        prod = _load("prod")
        for net, address in self._nginx_addresses().items():
            definition = prod["networks"][net]
            subnets = [cfg["subnet"] for cfg in definition.get("ipam", {}).get("config", [])]
            assert subnets, f"network {net} carries a static address but declares no subnet"
            assert any(ipaddress.ip_address(address) in ipaddress.ip_network(s) for s in subnets), (
                f"{address} is outside {net}'s subnets {subnets}"
            )


class TestProductionCorsNamesProductionOriginsOnly:
    """FSA-010. Localhost origins with `allow_credentials` belong to a developer's stack, never to
    the production default: the compose default and the code default both have to drop them when
    the environment is production-like."""

    def test_the_prod_compose_default_has_no_localhost_origin(self) -> None:
        env = _load("prod")["services"]["gateway"]["environment"]
        default = _default(env["ALLOWED_ORIGINS"])
        assert "localhost" not in default and "127.0.0.1" not in default, default
        assert "AURA_DOMAIN" in default, "the production origin must still be allowed"

    def test_the_code_default_drops_localhost_in_a_production_like_environment(self, monkeypatch) -> None:
        from config import Settings

        monkeypatch.setenv("AURA_ENVIRONMENT", "production")
        monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)
        origins = Settings().allowed_origins
        assert "localhost" not in origins and "127.0.0.1" not in origins, origins

    def test_the_code_default_keeps_local_flutter_web_in_a_local_environment(self, monkeypatch) -> None:
        from config import Settings

        monkeypatch.setenv("AURA_ENVIRONMENT", "local")
        monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)
        assert "http://localhost:8080" in Settings().allowed_origins


class TestTheTwoStacksAgreeOnSandboxAndAttestation:
    """FSA-100."""

    LIMIT_KEYS = (
        "AURA_SANDBOX_MEMORY_LIMIT",
        "AURA_SANDBOX_CPU_LIMIT",
        "AURA_SANDBOX_WORKSPACE_DISK_QUOTA_MB",
        "AURA_SANDBOX_MIN_FREE_MEMORY_MB",
        "AURA_SANDBOX_HOT_IDLE_S",
    )

    def _gateway_env(self, name: str) -> dict:
        return _load(name)["services"][_GATEWAY[name]]["environment"]

    @pytest.mark.parametrize("key", LIMIT_KEYS)
    def test_sandbox_limits_are_set_identically(self, key: str) -> None:
        prod, dev = self._gateway_env("prod"), self._gateway_env("dev")
        assert key in prod, f"prod lacks {key}"
        assert key in dev, f"dev lacks {key}"
        assert _default(prod[key]) == _default(dev[key]), f"{key} differs: {prod[key]} vs {dev[key]}"

    @pytest.mark.parametrize("name", sorted(_COMPOSE))
    def test_mock_attestation_defaults_off(self, name: str) -> None:
        env = self._gateway_env(name)
        assert _default(env["ALLOW_MOCK_ATTESTATION"]) == "false", (
            f"{name}: a debug build's fake attestation is accepted by default; set it in deploy/.env instead"
        )

    @pytest.mark.parametrize("name", sorted(_COMPOSE))
    def test_the_sandbox_network_default_names_the_network_docker_creates(self, name: str) -> None:
        """Compose prefixes a network's key with the project name unless the network sets `name:`;
        the adapter attaches sandboxes by that real name, so a bare key never matches."""
        data = _load(name)
        env = self._gateway_env(name)
        definition = data["networks"]["sandbox_egress"]
        real_name = definition.get("name") or f"{data.get('name') or _PROJECT[name]}_sandbox_egress"
        assert _default(env["AURA_SANDBOX_NETWORK"]) == real_name, (
            f"{name}: AURA_SANDBOX_NETWORK defaults to {env['AURA_SANDBOX_NETWORK']!r}, "
            f"but Docker names the network {real_name!r}"
        )
