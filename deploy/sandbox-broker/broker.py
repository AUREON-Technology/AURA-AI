"""Sandbox broker: the only Docker API the gateway can reach (audit F19).

The gateway used to talk to ``docker-socket-proxy`` directly. That proxy filters by *endpoint*,
not by request body: with ``CONTAINERS`` and ``POST`` on, ``POST /containers/create`` is allowed
whatever it carries, so a compromised gateway could create a ``Privileged`` container with ``/``
bind-mounted and own the host. Every hardening in ``aura_core/adapters/docker_sandbox.py`` is a
setting the *client* chooses to send; nothing on the server side enforced it.

This process sits between the two. The gateway's ``DOCKER_HOST`` points here, only this process
can reach the socket proxy (network isolation in both compose files), and it forwards only the
calls ``DockerSandboxRunner`` actually makes:

* ``GET /_ping``, ``GET /version`` -- docker-py's handshake.
* ``GET /containers/json`` -- only when filtered by a sandbox label.
* ``POST /containers/create`` -- only for a body that :func:`validate_create` accepts: the
  configured image and uid, all capabilities dropped, ``no-new-privileges``, a read-only rootfs,
  limits set, the sandbox network (or none), and binds only from one level under the workspace
  root, with no symlink on the way. Every key must be one the adapter sends, spelled exactly as
  docker-py spells it, because the daemon's JSON decoder matches keys case-insensitively and a
  key this broker did not recognise could still mean something to it.
* ``inspect``/``start``/``stop``/``kill``/``wait``/``logs``/``DELETE`` of a container -- only one
  that carries a sandbox label, so ``postgres``, ``ollama`` or ``nginx`` can be neither stopped,
  removed nor read. ``start`` re-checks the bind sources too, because bind mounts are resolved
  when a container starts, not when it is created.

Everything else is refused with 403. Standard library only: the image has nothing to update but
its base, and the whole policy fits on a screen.

Built, not proven: the policy and the forwarding are unit-tested against docker-py's own request
encoding, but no Docker daemon has run behind it yet.
"""

from __future__ import annotations

import http.client
import http.server
import json
import os
import posixpath
import re
import socketserver
import sys
import urllib.parse
from dataclasses import dataclass
from typing import Any, Callable, Mapping

WORKSPACE_LABEL = "aura.workspace"
ONESHOT_LABEL = "aura.sandbox.oneshot"
SANDBOX_LABELS = (WORKSPACE_LABEL, ONESHOT_LABEL)

#: Must match ``_SANDBOX_UID`` in docker_sandbox.py and deploy/sandbox/Dockerfile.
SANDBOX_USER = "10001:10001"
CONTAINER_WORKDIR = "/workspace"

MAX_REQUEST_BODY = 1 << 20
MAX_RESPONSE_BODY = 64 << 20
#: The oldest API version accepted in a versioned path. Older versions read host settings from
#: places this broker does not look (``HostConfig`` on ``start``, top-level host fields).
MIN_API_VERSION = (1, 24)

_VERSION = r"(?:/v(?P<major>\d+)\.(?P<minor>\d+))?"
_ID = r"(?P<id>[A-Za-z0-9][A-Za-z0-9_.-]{0,127})"

_ROUTES: tuple[tuple[str, re.Pattern[str], str], ...] = tuple(
    (method, re.compile(f"^{_VERSION}{pattern}$"), kind)
    for method, pattern, kind in (
        ("GET", r"/_ping", "ping"),
        ("HEAD", r"/_ping", "ping"),
        ("GET", r"/version", "version"),
        ("GET", r"/containers/json", "list"),
        ("POST", r"/containers/create", "create"),
        ("GET", rf"/containers/{_ID}/json", "inspect"),
        ("POST", rf"/containers/{_ID}/start", "start"),
        ("POST", rf"/containers/{_ID}/stop", "stop"),
        ("POST", rf"/containers/{_ID}/kill", "kill"),
        ("POST", rf"/containers/{_ID}/wait", "wait"),
        ("GET", rf"/containers/{_ID}/logs", "logs"),
        ("DELETE", rf"/containers/{_ID}", "remove"),
    )
)

#: Top-level keys of a create body, exactly as docker-py spells them. Anything else is refused,
#: even when null: the daemon's decoder folds case (``Bindſ`` reaches ``Binds``), so a key this
#: list does not name exactly is one whose meaning to the daemon was never checked here.
_CREATE_KEYS = frozenset({
    "Hostname", "Domainname", "User", "AttachStdin", "AttachStdout", "AttachStderr",
    "ExposedPorts", "Tty", "OpenStdin", "StdinOnce", "Env", "Cmd", "Healthcheck", "Image",
    "Volumes", "WorkingDir", "Entrypoint", "NetworkDisabled", "MacAddress", "Labels",
    "StopSignal", "StopTimeout", "Runtime", "HostConfig", "NetworkingConfig",
})
#: Top-level keys that must be empty: each one is either a host setting in the deprecated
#: top-level position or something the adapter never sends.
_CREATE_MUST_BE_EMPTY = frozenset({"ExposedPorts", "MacAddress", "Runtime", "Hostname", "Domainname"})

#: HostConfig keys the adapter sends (docker_sandbox.py ``_container_kwargs``), plus the two it
#: must never set to anything. Every other HostConfig key -- Privileged mounts, devices, host
#: namespaces, port publishing, cgroup parents, unmasked /proc paths -- is refused outright.
_HOST_CONFIG_KEYS = frozenset({
    "Memory", "MemorySwap", "NanoCpus", "ReadonlyRootfs", "NetworkMode", "RestartPolicy",
    "CapDrop", "CapAdd", "Privileged", "Dns", "SecurityOpt", "Binds", "Ulimits", "Tmpfs",
    "PidsLimit",
})
_NO_NEW_PRIVILEGES = frozenset({"no-new-privileges", "no-new-privileges:true", "no-new-privileges=true"})
_BIND_MODES = frozenset({"rw", "ro"})
_WORKSPACE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class PolicyError(Exception):
    """A request the broker refuses. ``status`` is the HTTP status sent back."""

    def __init__(self, message: str, status: int = 403) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Policy:
    image: str
    workspace_root: str
    network: str
    user: str = SANDBOX_USER

    def __post_init__(self) -> None:
        if not self.image:
            raise ValueError("the sandbox image must be named")
        root = self.workspace_root
        if not root.startswith("/") or posixpath.normpath(root) != root or root == "/":
            raise ValueError(f"workspace root must be a normalised absolute path, not {root!r}")
        if not self.network:
            raise ValueError("the sandbox network must be named")

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "Policy":
        return cls(
            image=env.get("AURA_SANDBOX_IMAGE", ""),
            workspace_root=env.get("AURA_SANDBOX_HOST_ROOT", ""),
            network=env.get("AURA_SANDBOX_NETWORK", ""),
        )


@dataclass(frozen=True)
class Route:
    kind: str
    container_id: str | None


# -- request classification ------------------------------------------------------------------


def classify(method: str, path: str) -> Route:
    """Map a request line to the one operation it is, or refuse it."""
    for route_method, pattern, kind in _ROUTES:
        if method != route_method:
            continue
        match = pattern.match(path)
        if match is None:
            continue
        if match.group("major") is not None:
            version = (int(match.group("major")), int(match.group("minor")))
            if version < MIN_API_VERSION:
                raise PolicyError(f"API version {version[0]}.{version[1]} is too old")
        return Route(kind=kind, container_id=match.groupdict().get("id"))
    raise PolicyError(f"{method} {path} is not a sandbox operation")


def _truthy_param(value: str) -> bool:
    return value.strip().lower() not in ("", "0", "false", "no")


def validate_query(route: Route, query: str) -> None:
    """Per-operation constraints on the query string."""
    params = urllib.parse.parse_qs(query, keep_blank_values=True)
    if route.kind == "list":
        raw = params.get("filters", [])
        if len(raw) != 1:
            raise PolicyError("listing containers needs exactly one filters parameter")
        try:
            filters = json.loads(raw[0])
        except ValueError as exc:
            raise PolicyError("filters is not JSON", status=400) from exc
        labels = filters.get("label") if isinstance(filters, dict) else None
        if isinstance(labels, dict):  # the older {"label": {"k=v": true}} form
            labels = [k for k, v in labels.items() if v]
        if not isinstance(labels, list) or not any(
            isinstance(entry, str) and entry.split("=", 1)[0] in SANDBOX_LABELS for entry in labels
        ):
            raise PolicyError("containers may only be listed by a sandbox label")
    elif route.kind == "logs":
        if any(_truthy_param(v) for v in params.get("follow", [])):
            raise PolicyError("following logs is not supported")
    elif route.kind == "remove":
        if any(_truthy_param(v) for v in params.get("link", [])):
            raise PolicyError("removing links is not a sandbox operation")


# -- body validation -------------------------------------------------------------------------


def parse_json_strict(raw: bytes) -> Any:
    """Parse JSON, refusing any object with keys equal under case folding.

    Python keeps the last of two duplicate keys; the daemon's decoder applies *both*, merging a
    nested object into the one before it. A body that validated here could otherwise carry a
    second ``HostConfig`` the daemon reads and this broker never saw.
    """

    def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        seen: set[str] = set()
        for key, _ in pairs:
            folded = key.casefold()
            if folded in seen:
                raise PolicyError(f"duplicate key {key!r}", status=400)
            seen.add(folded)
        return dict(pairs)

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
    except (UnicodeDecodeError, ValueError) as exc:
        raise PolicyError(f"request body is not JSON: {exc}", status=400) from exc


def _require_known_keys(obj: Mapping[str, Any], allowed: frozenset[str], where: str) -> None:
    unknown = sorted(set(obj) - allowed)
    if unknown:
        raise PolicyError(f"{where} sets {', '.join(unknown)}, which the sandbox never uses")


def _check_bind(bind: Any, policy: Policy, resolve: Callable[[str], str]) -> None:
    if not isinstance(bind, str):
        raise PolicyError("a bind must be a string")
    parts = bind.split(":")
    if len(parts) not in (2, 3):
        raise PolicyError(f"bind {bind!r} is not source:target[:mode]")
    source, target = parts[0], parts[1]
    mode = parts[2] if len(parts) == 3 else "rw"
    if mode not in _BIND_MODES:
        raise PolicyError(f"bind mode {mode!r} is not allowed")
    if target != CONTAINER_WORKDIR:
        raise PolicyError(f"binds may only target {CONTAINER_WORKDIR}, not {target!r}")
    check_bind_source(source, policy, resolve)


def check_bind_source(source: str, policy: Policy, resolve: Callable[[str], str]) -> None:
    """A bind source must be one workspace directory directly under the workspace root.

    The string check stops ``/`` and ``..``; ``resolve`` (``os.path.realpath`` over the root,
    which the broker mounts read-only at the same path) stops a symlink the gateway planted in
    the root -- the daemon follows symlinks in a bind source.
    """
    if not source.startswith("/") or posixpath.normpath(source) != source:
        raise PolicyError(f"bind source {source!r} is not a normalised absolute path")
    if posixpath.dirname(source) != policy.workspace_root:
        raise PolicyError(f"bind source {source!r} is not a workspace under {policy.workspace_root}")
    if not _WORKSPACE_NAME.match(posixpath.basename(source)):
        raise PolicyError(f"bind source {source!r} is not a workspace name")
    try:
        resolved = resolve(source)
    except OSError as exc:
        raise PolicyError(f"bind source {source!r} cannot be resolved: {exc}") from exc
    if resolved != source:
        raise PolicyError(f"bind source {source!r} resolves elsewhere ({resolved!r})")


def _check_network(body: Mapping[str, Any], host: Mapping[str, Any], policy: Policy) -> None:
    mode = host.get("NetworkMode")
    disabled = body.get("NetworkDisabled") is True
    if mode == policy.network:
        allowed_endpoints = {policy.network}
    elif mode == "none" or (disabled and mode in (None, "", "default")):
        allowed_endpoints = set()
    else:
        raise PolicyError(f"network mode {mode!r} is not the sandbox network")
    networking = body.get("NetworkingConfig")
    if networking in (None, {}):
        return
    if not isinstance(networking, dict):
        raise PolicyError("NetworkingConfig must be an object")
    # docker-py 7 sends `{"<network>": null}` here when `network=` is given without endpoint
    # settings. The daemon has no such field and ignores it; NetworkMode (checked above) is what
    # attaches the container. Accepted only in exactly that shape, for exactly that network.
    if allowed_endpoints and networking == {policy.network: None}:
        return
    _require_known_keys(networking, frozenset({"EndpointsConfig"}), "NetworkingConfig")
    endpoints = networking.get("EndpointsConfig") or {}
    if not isinstance(endpoints, dict):
        raise PolicyError("EndpointsConfig must be an object")
    for name, settings in endpoints.items():
        if name not in allowed_endpoints:
            raise PolicyError(f"endpoint {name!r} is not the sandbox network")
        # Aliases or a fixed address would let a workspace answer as `sandbox-proxy`.
        if settings not in (None, {}):
            raise PolicyError("endpoint settings are not allowed")


def validate_create(
    body: Any, policy: Policy, resolve: Callable[[str], str] | None = None
) -> None:
    """Accept exactly the container ``DockerSandboxRunner._container_kwargs`` describes."""
    if not isinstance(body, dict):
        raise PolicyError("create body must be an object", status=400)
    _require_known_keys(body, _CREATE_KEYS, "the create body")
    for key in _CREATE_MUST_BE_EMPTY:
        if body.get(key):
            raise PolicyError(f"{key} is not allowed")
    if body.get("Image") != policy.image:
        raise PolicyError(f"image {body.get('Image')!r} is not the sandbox image")
    if body.get("User") != policy.user:
        raise PolicyError(f"user {body.get('User')!r} is not the sandbox uid {policy.user}")

    labels = body.get("Labels")
    if not isinstance(labels, dict) or not all(isinstance(v, str) for v in labels.values()):
        raise PolicyError("a sandbox container must carry labels")
    present = [label for label in SANDBOX_LABELS if labels.get(label)]
    if len(present) != 1:
        raise PolicyError("a sandbox container carries exactly one sandbox label")

    host = body.get("HostConfig")
    if not isinstance(host, dict):
        raise PolicyError("HostConfig is required")
    _require_known_keys(host, _HOST_CONFIG_KEYS, "HostConfig")
    if host.get("Privileged"):
        raise PolicyError("privileged containers are not allowed")
    if host.get("CapAdd"):
        raise PolicyError("adding capabilities is not allowed")
    cap_drop = host.get("CapDrop")
    if not isinstance(cap_drop, list) or "ALL" not in {str(c).upper() for c in cap_drop}:
        raise PolicyError("CapDrop must contain ALL")
    security = host.get("SecurityOpt")
    if not isinstance(security, list) or not security or not set(security) <= _NO_NEW_PRIVILEGES:
        raise PolicyError("SecurityOpt must be exactly no-new-privileges")
    if host.get("ReadonlyRootfs") is not True:
        raise PolicyError("the root filesystem must be read-only")
    for limit in ("Memory", "PidsLimit"):
        value = host.get(limit)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise PolicyError(f"{limit} must be a positive limit")
    restart = host.get("RestartPolicy") or {}
    if not isinstance(restart, dict) or restart.get("Name") not in (None, "", "no"):
        raise PolicyError("containers must not restart on their own")
    binds = host.get("Binds") or []
    if not isinstance(binds, list):
        raise PolicyError("Binds must be a list")
    for bind in binds:
        _check_bind(bind, policy, resolve or resolve_existing_dir)
    volumes = body.get("Volumes") or {}
    if not isinstance(volumes, dict) or not set(volumes) <= {CONTAINER_WORKDIR}:
        raise PolicyError(f"only {CONTAINER_WORKDIR} may be declared as a volume")
    _check_network(body, host, policy)


def resolve_existing_dir(path: str) -> str:
    """``realpath`` that also insists the directory exists: a missing bind source is created by
    the daemon, as root, wherever the path points."""
    real = os.path.realpath(path, strict=True)
    if not os.path.isdir(real):
        raise OSError(f"{real} is not a directory")
    return real


def is_sandbox_container(inspect: Any) -> bool:
    labels = ((inspect or {}).get("Config") or {}).get("Labels") or {}
    return isinstance(labels, dict) and any(labels.get(label) for label in SANDBOX_LABELS)


def validate_start(
    inspect: Any, policy: Policy, resolve: Callable[[str], str] | None = None
) -> None:
    """Re-check what the daemon is about to mount. Binds are resolved at start, not at create,
    so a workspace directory swapped for a symlink in between is caught here."""
    if not is_sandbox_container(inspect):
        raise PolicyError("not a sandbox container")
    config = inspect.get("Config") or {}
    host = inspect.get("HostConfig") or {}
    if config.get("Image") != policy.image:
        raise PolicyError("not started from the sandbox image")
    if host.get("Privileged") or host.get("CapAdd"):
        raise PolicyError("container is privileged")
    for bind in host.get("Binds") or []:
        _check_bind(bind, policy, resolve or resolve_existing_dir)


# -- forwarding ------------------------------------------------------------------------------

_HOP_BY_HOP = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer",
    "transfer-encoding", "upgrade", "content-length", "host",
})


class Upstream:
    """One request to the socket proxy at a time per call; the response is read in full."""

    def __init__(self, host: str, port: int, timeout_s: float = 3600.0) -> None:
        self.host, self.port, self.timeout_s = host, port, timeout_s

    def request(
        self, method: str, path: str, body: bytes | None, headers: Mapping[str, str]
    ) -> tuple[int, list[tuple[str, str]], bytes]:
        conn = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout_s)
        try:
            conn.request(method, path, body=body, headers=dict(headers))
            response = conn.getresponse()
            data = response.read(MAX_RESPONSE_BODY + 1)
            if len(data) > MAX_RESPONSE_BODY:
                raise PolicyError("upstream response too large", status=502)
            return response.status, response.getheaders(), data
        finally:
            conn.close()

    def inspect(self, container_id: str) -> tuple[int, Any]:
        status, _, data = self.request("GET", f"/containers/{container_id}/json", None, {})
        if status != 200:
            return status, None
        try:
            return status, json.loads(data)
        except ValueError:
            return 502, None


def make_handler(policy: Policy, upstream: Upstream) -> type[http.server.BaseHTTPRequestHandler]:
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "aura-sandbox-broker"

        def log_message(self, fmt: str, *args: Any) -> None:  # one line per request on stderr
            sys.stderr.write(f"sandbox-broker: {fmt % args}\n")

        def _reply(self, status: int, headers: list[tuple[str, str]], body: bytes) -> None:
            self.send_response(status)
            for name, value in headers:
                if name.lower() not in _HOP_BY_HOP:
                    self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _refuse(self, error: PolicyError) -> None:
            sys.stderr.write(f"sandbox-broker: refused {self.command} {self.path}: {error}\n")
            payload = json.dumps({"message": f"sandbox-broker: {error}"}).encode()
            self._reply(error.status, [("Content-Type", "application/json")], payload)

        def _read_body(self) -> bytes:
            if self.headers.get("Transfer-Encoding"):
                raise PolicyError("chunked request bodies are not supported", status=411)
            length = int(self.headers.get("Content-Length") or 0)
            if length < 0 or length > MAX_REQUEST_BODY:
                raise PolicyError("request body too large", status=413)
            return self.rfile.read(length) if length else b""

        def _handle(self) -> None:
            try:
                if self.headers.get("Upgrade"):
                    raise PolicyError("connection upgrades are not supported")
                parsed = urllib.parse.urlsplit(self.path)
                route = classify(self.command, parsed.path)
                validate_query(route, parsed.query)
                body = self._read_body()
                forward_body: bytes | None = None
                if route.kind == "create":
                    document = parse_json_strict(body)
                    validate_create(document, policy)
                    # Forward what was validated, not the raw bytes, so the daemon decodes the
                    # same document this broker checked.
                    forward_body = json.dumps(document).encode()
                elif body:
                    raise PolicyError(f"{route.kind} takes no request body")
                if route.container_id is not None:
                    status, inspected = upstream.inspect(route.container_id)
                    if inspected is None:
                        raise PolicyError("no such container", status=404 if status == 404 else 502)
                    if not is_sandbox_container(inspected):
                        raise PolicyError("not a sandbox container")
                    if route.kind == "start":
                        validate_start(inspected, policy)
                headers = {"Content-Type": "application/json"} if forward_body is not None else {}
                status, response_headers, data = upstream.request(
                    self.command, self.path, forward_body, headers
                )
            except PolicyError as error:
                self._refuse(error)
                return
            except (OSError, http.client.HTTPException) as exc:
                self._refuse(PolicyError(f"upstream unavailable: {exc}", status=502))
                return
            self._reply(status, response_headers, data)

        do_GET = do_POST = do_DELETE = do_HEAD = _handle

        def _deny_method(self) -> None:
            self._refuse(PolicyError(f"method {self.command} is not allowed", status=405))

        do_PUT = do_PATCH = do_OPTIONS = _deny_method

    return Handler


class BrokerServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def serve(policy: Policy, upstream: Upstream, host: str, port: int) -> BrokerServer:
    return BrokerServer((host, port), make_handler(policy, upstream))


def _split_hostport(value: str, default_port: int) -> tuple[str, int]:
    host, _, port = value.rpartition(":")
    return (host, int(port)) if host else (value, default_port)


def main() -> int:
    try:
        policy = Policy.from_env()
    except ValueError as exc:
        sys.stderr.write(f"sandbox-broker: refusing to start: {exc}\n")
        return 2
    if not os.path.isdir(policy.workspace_root):
        # Without the root mounted, symlink checks would resolve against nothing and every
        # start would be refused; say so at boot rather than on the first command.
        sys.stderr.write(f"sandbox-broker: workspace root {policy.workspace_root} is not mounted\n")
        return 2
    up_host, up_port = _split_hostport(os.environ.get("BROKER_UPSTREAM", "docker-socket-proxy:2375"), 2375)
    listen_host, listen_port = _split_hostport(os.environ.get("BROKER_LISTEN", "0.0.0.0:2375"), 2375)
    server = serve(policy, Upstream(up_host, up_port), listen_host, listen_port)
    sys.stderr.write(
        f"sandbox-broker: listening on {listen_host}:{listen_port}, upstream {up_host}:{up_port}, "
        f"image {policy.image}, root {policy.workspace_root}, network {policy.network}\n"
    )
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
