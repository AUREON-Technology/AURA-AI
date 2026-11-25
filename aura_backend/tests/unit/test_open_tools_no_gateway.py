"""No tool under ``aura_core/tools/open`` can reach the AURA API gateway.

Three independent guarantees, each pinned here for the whole folder (the per-group suites test
their own entry points in depth):

1. **No code path to it** -- no module imports the gateway, container, adapters, config, auth or
   kernel, and the only platform modules the tools use are the document extractor and error types.
2. **Network only through the deny policy** -- only five named modules may import a network
   library, and each of them goes through ``_ops_common`` (``check_url`` / ``open_guarded`` /
   ``open_owned_local``).
3. **The policy refuses the gateway** -- loopback, private ranges, docker service names, Railway's
   private network, the platform's public hosts and the ``/api/v3/`` and ``/health`` paths, with no
   DNS lookup and no connection attempted.

What this cannot prove: a tool that runs a command the caller supplies
(``background_task_manager``, the project's own test or build scripts) can open its own sockets.
Closing that needs an egress-restricted container, not a check in Python.
"""

from __future__ import annotations

import ast
import socket
from pathlib import Path

import pytest

OPEN_DIR = Path(__file__).resolve().parents[2] / "aura_core" / "tools" / "open"
FILES = sorted(OPEN_DIR.glob("*.py"))

#: ``aura_core`` sub-packages a tool may import (lazily): the PDF extractor and the error types.
ALLOWED_PLATFORM_PACKAGES = {"knowledge", "domain"}
FORBIDDEN_ABSOLUTE = ("aura_core.api", "aura_core.container", "aura_core.adapters", "aura_core.config",
                      "aura_core.auth", "aura_core.kernel", "aura_service", "fastapi", "starlette")
NETWORK_MODULES = {"urllib.request", "http.client", "socket", "requests", "httpx", "aiohttp", "websocket",
                   "websockets", "playwright", "smtplib", "ftplib", "telnetlib", "ssl"}
#: The only modules allowed to import a network library, and what each must import from _ops_common.
NETWORK_ALLOWED = {
    "_ops_common.py": None,
    "browser_subagent.py": {"check_url", "open_guarded"},
    "deployment_release_manager.py": {"open_guarded"},
    "push_notification_dispatcher.py": {"open_guarded"},
    "dev_server_manager.py": {"open_owned_local"},
    # ADR-046: post-release health verification (tools/release.py); same sanctioned helper, redirects off.
    "release_ext.py": {"open_guarded"},
}


def _imports(path: Path) -> list[tuple[str, int, str]]:
    """``(module, relative_level, imported_name)`` for every import in the file, at any depth."""
    out: list[tuple[str, int, str]] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out.extend((a.name, 0, a.name) for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            out.extend((node.module or "", node.level, a.name) for a in node.names)
    return out


def test_the_folder_was_found() -> None:
    assert len(FILES) > 50, "expected the tools/open modules"


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_no_module_imports_the_gateway_or_its_internals(path: Path) -> None:
    for module, level, name in _imports(path):
        if level == 0:
            full = module
            assert not any(full == bad or full.startswith(bad + ".") for bad in FORBIDDEN_ABSOLUTE), (
                f"{path.name} imports {full}"
            )
        elif level >= 3:
            # ``from ...x import y`` inside tools/open resolves to ``aura_core.x``.
            package = (module or name).split(".")[0]
            assert package in ALLOWED_PLATFORM_PACKAGES, f"{path.name} imports aura_core.{package or name}"
        elif level == 2:
            # ``from ..x import y`` resolves to ``aura_core.tools.x``: sibling tool modules are fine
            # (the policy module), but never the registry/policy layer that carries credentials.
            assert (module or name).split(".")[0] not in {"registry"}, f"{path.name} imports tools.registry"


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_only_the_five_network_modules_may_do_network_io(path: Path) -> None:
    uses = {m for m, level, _ in _imports(path) if level == 0 and (m in NETWORK_MODULES or m.split(".")[0] in {"requests", "httpx", "aiohttp", "playwright", "websockets"})}
    if path.name in NETWORK_ALLOWED:
        required = NETWORK_ALLOWED[path.name]
        if required:
            imported = {name for m, level, name in _imports(path) if level == 1 and m == "_ops_common"}
            assert required <= imported, f"{path.name} must import {required - imported} from _ops_common"
        return
    assert not uses, f"{path.name} imports a network library: {sorted(uses)}"


@pytest.mark.parametrize("path", [p for p in FILES if p.name != "_ops_common.py"], ids=lambda p: p.name)
def test_no_tool_names_a_gateway_path_or_host(path: Path) -> None:
    """Only the policy module may mention the gateway's paths/hosts -- as its deny-list."""
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value.lower()
            if "/api/v3" in text or "aureon-ai" in text:
                # Documentation (docstrings) may explain the rule; code may not build such a URL.
                assert "\n" in node.value or len(node.value) > 120, f"{path.name} contains {node.value!r}"


GATEWAY_URLS = [
    "http://gateway:8000/api/v3/chat",
    "http://gateway/health/ready",
    "http://127.0.0.1:8000/health",
    "http://localhost:8000/api/v3/chat",
    "http://[::1]:8000/",
    "http://10.77.10.10/",
    "http://10.77.11.10/api/v3/x",
    "http://172.17.0.2:8000/",
    "http://192.168.1.10/",
    "http://169.254.169.254/latest/meta-data/",
    "http://aura-gateway.railway.internal:8080/",
    "https://aureon-ai.tech/api/v3/chat",
    "https://console.aureon-ai.tech/",
    "https://dev.aureon-ai.tech/health",
    "https://example.com/api/v3/chat",
    "https://example.com/%2e%2e/api/v3/chat",
    "https://example.com//api//v3/chat",
    "https://example.com/health/live",
    "http://127.1/",
    "http://0x7f.0.0.1/",
    "http://2130706433/",
    "file:///etc/passwd",
    "http://user:pw@example.com/",
]


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    calls = {"dns": 0, "connect": 0}

    def dns(*a, **k):
        calls["dns"] += 1
        raise socket.gaierror("blocked in test")

    def connect(*a, **k):
        calls["connect"] += 1
        raise OSError("blocked in test")

    monkeypatch.setattr(socket, "getaddrinfo", dns)
    monkeypatch.setattr(socket, "create_connection", connect)
    monkeypatch.setattr(socket.socket, "connect", connect)
    return calls


@pytest.mark.parametrize("url", GATEWAY_URLS)
def test_the_policy_refuses_every_route_to_the_gateway(url: str, no_network: dict[str, int]) -> None:
    from aura_core.tools.open._ops_common import UrlRefused, check_url

    with pytest.raises(UrlRefused):
        check_url(url)
    assert no_network == {"dns": no_network["dns"], "connect": 0}


@pytest.mark.parametrize("url", [u for u in GATEWAY_URLS if "example.com" not in u])
def test_refusal_needs_no_dns_lookup_for_internal_names_and_ip_literals(url: str, no_network: dict[str, int]) -> None:
    from aura_core.tools.open._ops_common import UrlRefused, check_url

    with pytest.raises(UrlRefused):
        check_url(url)
    assert no_network["dns"] == 0 and no_network["connect"] == 0


def test_platform_hosts_named_in_the_environment_are_refused(monkeypatch: pytest.MonkeyPatch, no_network: dict[str, int]) -> None:
    from aura_core.tools.open._ops_common import UrlRefused, check_url

    monkeypatch.setenv("AURA_PUBLIC_URL", "https://api.my-own-domain.example")
    monkeypatch.setenv("RAILWAY_PUBLIC_DOMAIN", "svc-prod.up.railway.app")
    for url in ("https://api.my-own-domain.example/", "https://sub.api.my-own-domain.example/x", "https://svc-prod.up.railway.app/"):
        with pytest.raises(UrlRefused):
            check_url(url)
    assert no_network["connect"] == 0


def test_a_child_process_does_not_inherit_gateway_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    from aura_core.tools.open._process import scrubbed_env

    for name in ("AURA_JWT_SECRET", "AURA_PROVIDER_HOSTED1_API_KEY", "DATABASE_URL", "AURA_SERVICE_KEY"):
        monkeypatch.setenv(name, "secret-value")
    env = scrubbed_env()
    assert not any("secret-value" == v for v in env.values())
    assert not any(k.startswith("AURA_") for k in env)
