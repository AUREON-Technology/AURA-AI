"""The sandbox broker refuses every Docker API request the engineer sandbox does not need (F19).

``docker-socket-proxy`` filters by endpoint, not by body: with ``CONTAINERS`` and ``POST`` on, a
compromised gateway could create a privileged container with ``/`` bind-mounted. The broker in
``deploy/sandbox-broker/broker.py`` sits between the two and enforces, server-side, the
confinement ``DockerSandboxRunner`` only *asks* for. These tests hold it to two things at once:

* everything the adapter really sends -- as docker-py really encodes it, over HTTP -- passes;
* each way out of that confinement is refused, and never reaches the daemon.

No Docker daemon is involved: a fake one on loopback stands in for the socket proxy.
"""

from __future__ import annotations

import copy
import http.server
import importlib.util
import json
import os
import re
import socketserver
import sys
import threading
import urllib.parse
from pathlib import Path

import pytest

from aura_core.adapters.docker_sandbox import DockerSandboxRunner

_REPO = Path(__file__).resolve().parents[3]
_BROKER = _REPO / "deploy" / "sandbox-broker" / "broker.py"
_IMAGE = "aura-sandbox:latest"
_NETWORK = "aura_sandbox_egress"


def _load_broker():
    spec = importlib.util.spec_from_file_location("sandbox_broker", _BROKER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["sandbox_broker"] = module  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(module)
    return module


broker = _load_broker()


@pytest.fixture
def root(tmp_path: Path) -> Path:
    real = tmp_path.resolve() / "sandboxes"
    real.mkdir()
    return real


@pytest.fixture
def policy(root: Path):
    return broker.Policy(image=_IMAGE, workspace_root=str(root), network=_NETWORK)


def _body(root: Path, *, label: str = "aura.sandbox.oneshot", network: str = _NETWORK) -> dict:
    """What docker-py 7 sends for ``DockerSandboxRunner._container_kwargs`` (captured verbatim)."""
    workspace = root / "abc123"
    workspace.mkdir(exist_ok=True)
    body = {
        "Hostname": None, "Domainname": None, "ExposedPorts": None, "User": "10001:10001",
        "Tty": False, "OpenStdin": False, "StdinOnce": False, "AttachStdin": False,
        "AttachStdout": True, "AttachStderr": True,
        "Env": ["HTTP_PROXY=http://sandbox-proxy:8888", "NO_PROXY=localhost,127.0.0.1"],
        "Cmd": ["echo", "hi"], "Image": _IMAGE, "Volumes": {"/workspace": {}},
        "NetworkDisabled": False, "Entrypoint": None, "WorkingDir": "/workspace",
        "HostConfig": {
            "Memory": 536870912, "MemorySwap": 536870912, "ReadonlyRootfs": True,
            "NetworkMode": network, "RestartPolicy": {"Name": "no"}, "CapDrop": ["ALL"],
            "Dns": ["127.0.0.11"], "SecurityOpt": ["no-new-privileges"],
            "Binds": [f"{workspace}:/workspace:rw"],
            "Ulimits": [{"Name": "nofile", "Soft": 4096, "Hard": 4096}],
            "Tmpfs": {"/tmp": "rw,nosuid,nodev,noexec,size=256m,mode=1777",
                      "/home/aura": "rw,nosuid,nodev,size=64m,uid=10001,gid=10001,mode=0700"},
            "PidsLimit": 512, "NanoCpus": 1000000000,
        },
        "NetworkingConfig": {network: None},  # docker-py 7.2's own (odd) encoding
        "MacAddress": None, "Labels": {label: "abc123"}, "StopSignal": None,
        "Healthcheck": None, "StopTimeout": None, "Runtime": None,
    }
    return body


class TestTheSandboxContainerPasses:
    @pytest.mark.parametrize("label", ["aura.sandbox.oneshot", "aura.workspace"])
    def test_the_adapter_s_own_body_is_accepted(self, root, policy, label) -> None:
        broker.validate_create(_body(root, label=label), policy)

    def test_a_container_with_networking_disabled_is_accepted(self, root, policy) -> None:
        body = _body(root)
        body["NetworkDisabled"] = True
        body["HostConfig"]["NetworkMode"] = "default"
        body.pop("NetworkingConfig")
        broker.validate_create(body, policy)


def _mutate(path: str, value):
    """Set a dotted path in the body; ``value is _DELETE`` removes it."""

    def apply(body: dict) -> dict:
        target = body
        *parents, leaf = path.split(".")
        for key in parents:
            target = target[key]
        if value is _DELETE:
            target.pop(leaf, None)
        else:
            target[leaf] = value(body) if callable(value) else value
        return body

    return apply


_DELETE = object()

_ESCAPES = {
    "privileged": _mutate("HostConfig.Privileged", True),
    "cap_add": _mutate("HostConfig.CapAdd", ["SYS_ADMIN"]),
    "cap_drop_not_all": _mutate("HostConfig.CapDrop", ["NET_RAW"]),
    "cap_drop_missing": _mutate("HostConfig.CapDrop", _DELETE),
    "root_bind": _mutate("HostConfig.Binds", ["/:/workspace:rw"]),
    "root_bind_other_target": _mutate("HostConfig.Binds", ["/:/host"]),
    "docker_socket_bind": _mutate("HostConfig.Binds", ["/var/run/docker.sock:/workspace"]),
    "bind_traversal": _mutate(
        "HostConfig.Binds", lambda b: [b["HostConfig"]["Binds"][0].split(":")[0] + "/../..:/workspace"]
    ),
    "bind_to_the_root_itself": _mutate(
        "HostConfig.Binds", lambda b: [str(Path(b["HostConfig"]["Binds"][0].split(":")[0]).parent) + ":/workspace"]
    ),
    "bind_other_target": _mutate(
        "HostConfig.Binds", lambda b: [b["HostConfig"]["Binds"][0].split(":")[0] + ":/etc"]
    ),
    "bind_propagation_mode": _mutate(
        "HostConfig.Binds", lambda b: [b["HostConfig"]["Binds"][0].rsplit(":", 1)[0] + ":rshared"]
    ),
    "mounts": _mutate("HostConfig.Mounts", [{"Type": "bind", "Source": "/", "Target": "/host"}]),
    "network_host": _mutate("HostConfig.NetworkMode", "host"),
    "network_bridge": _mutate("HostConfig.NetworkMode", "bridge"),
    "network_other_container": _mutate("HostConfig.NetworkMode", "container:postgres"),
    "endpoint_alias": _mutate(
        "NetworkingConfig.EndpointsConfig", {_NETWORK: {"Aliases": ["sandbox-proxy"]}}
    ),
    "extra_endpoint": _mutate("NetworkingConfig.EndpointsConfig", {_NETWORK: {}, "aura_data": {}}),
    "pid_host": _mutate("HostConfig.PidMode", "host"),
    "ipc_host": _mutate("HostConfig.IpcMode", "host"),
    "userns_host": _mutate("HostConfig.UsernsMode", "host"),
    "devices": _mutate("HostConfig.Devices", [{"PathOnHost": "/dev/sda", "PathInContainer": "/dev/sda"}]),
    "device_requests": _mutate("HostConfig.DeviceRequests", [{"Count": -1}]),
    "unmasked_proc": _mutate("HostConfig.MaskedPaths", []),
    "seccomp_unconfined": _mutate("HostConfig.SecurityOpt", ["no-new-privileges", "seccomp=unconfined"]),
    "no_security_opt": _mutate("HostConfig.SecurityOpt", []),
    "writable_rootfs": _mutate("HostConfig.ReadonlyRootfs", False),
    "no_memory_limit": _mutate("HostConfig.Memory", 0),
    "no_pids_limit": _mutate("HostConfig.PidsLimit", _DELETE),
    "restart_always": _mutate("HostConfig.RestartPolicy", {"Name": "always"}),
    "port_publish": _mutate("HostConfig.PortBindings", {"22/tcp": [{"HostPort": "2222"}]}),
    "cgroup_parent": _mutate("HostConfig.CgroupParent", "/"),
    "sysctls": _mutate("HostConfig.Sysctls", {"net.ipv4.ip_forward": "1"}),
    "runtime": _mutate("Runtime", "runsc-debug"),
    "root_user": _mutate("User", "0:0"),
    "no_user": _mutate("User", _DELETE),
    "other_image": _mutate("Image", "alpine:latest"),
    "no_label": _mutate("Labels", {}),
    "both_labels": _mutate("Labels", {"aura.workspace": "a", "aura.sandbox.oneshot": "a"}),
    "anonymous_volume_elsewhere": _mutate("Volumes", {"/workspace": {}, "/etc": {}}),
    # The daemon's JSON decoder matches keys case-insensitively, including Unicode folds
    # (U+017F folds to "s"); a key this broker does not know exactly is a key it did not check.
    "lowercase_privileged": _mutate("HostConfig.privileged", True),
    "long_s_binds": _mutate("HostConfig.Bindſ", ["/:/host"]),
    "unknown_null_key": _mutate("HostConfig.Bindſ", None),
    "top_level_host_field": _mutate("Privileged", True),
    "no_host_config": _mutate("HostConfig", _DELETE),
}


class TestEveryWayOutIsRefused:
    @pytest.mark.parametrize("escape", sorted(_ESCAPES))
    def test_refused(self, root, policy, escape) -> None:
        body = _ESCAPES[escape](_body(root))
        with pytest.raises(broker.PolicyError):
            broker.validate_create(body, policy)

    def test_a_symlinked_workspace_is_refused(self, root, policy) -> None:
        """The gateway holds the workspace root read-write; a symlink it plants there would be
        followed by the daemon to wherever it points."""
        (root / "evil").symlink_to("/")
        body = _body(root)
        body["HostConfig"]["Binds"] = [f"{root}/evil:/workspace:rw"]
        with pytest.raises(broker.PolicyError, match="resolves elsewhere"):
            broker.validate_create(body, policy)

    def test_a_missing_workspace_is_refused(self, root, policy) -> None:
        """The daemon creates a missing bind source as root -- not something to let it do."""
        body = _body(root)
        body["HostConfig"]["Binds"] = [f"{root}/nothere:/workspace:rw"]
        with pytest.raises(broker.PolicyError, match="cannot be resolved"):
            broker.validate_create(body, policy)

    def test_duplicate_keys_are_refused_before_validation(self) -> None:
        """Python keeps the last duplicate; the daemon merges both. Never let them disagree."""
        raw = b'{"HostConfig": {"Privileged": true}, "HostConfig": {"CapDrop": ["ALL"]}}'
        with pytest.raises(broker.PolicyError, match="duplicate"):
            broker.parse_json_strict(raw)
        with pytest.raises(broker.PolicyError, match="duplicate"):
            broker.parse_json_strict(b'{"Image": "a", "image": "b"}')

    def test_start_rechecks_the_mount(self, root, policy) -> None:
        """Binds resolve at start. A workspace swapped for a symlink after create is caught."""
        body = _body(root)
        broker.validate_create(body, policy)
        inspected = {"Config": {"Image": _IMAGE, "Labels": body["Labels"]}, "HostConfig": body["HostConfig"]}
        broker.validate_start(inspected, policy)
        workspace = root / "abc123"
        workspace.rmdir()
        workspace.symlink_to("/")
        with pytest.raises(broker.PolicyError):
            broker.validate_start(inspected, policy)


class TestOnlySandboxOperationsAreRouted:
    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("POST", "/v1.43/containers/abc/exec"),
            ("POST", "/v1.43/exec/abc/start"),
            ("POST", "/v1.43/images/create"),
            ("POST", "/v1.43/build"),
            ("POST", "/v1.43/containers/abc/attach"),
            ("POST", "/v1.43/containers/abc/update"),
            ("PUT", "/v1.43/containers/abc/archive"),
            ("GET", "/v1.43/containers/abc/archive"),
            ("POST", "/v1.43/networks/create"),
            ("POST", "/v1.43/volumes/create"),
            ("GET", "/v1.43/info"),
            ("POST", "/v1.18/containers/create"),  # pre-1.24 reads host settings from elsewhere
            ("POST", "/v1.43/containers/../create"),
        ],
    )
    def test_refused(self, method, path) -> None:
        with pytest.raises(broker.PolicyError):
            broker.classify(method, path)

    @pytest.mark.parametrize(
        ("method", "path", "kind"),
        [
            ("GET", "/_ping", "ping"),
            ("GET", "/version", "version"),
            ("POST", "/v1.43/containers/create", "create"),
            ("POST", "/v1.43/containers/abc/start", "start"),
            ("POST", "/v1.43/containers/abc/wait", "wait"),
            ("GET", "/v1.43/containers/abc/logs", "logs"),
            ("DELETE", "/v1.43/containers/abc", "remove"),
        ],
    )
    def test_routed(self, method, path, kind) -> None:
        assert broker.classify(method, path).kind == kind

    def test_listing_needs_a_sandbox_label_filter(self) -> None:
        route = broker.Route(kind="list", container_id=None)
        with pytest.raises(broker.PolicyError):
            broker.validate_query(route, "all=1")
        with pytest.raises(broker.PolicyError):
            broker.validate_query(route, "all=1&filters=" + urllib.parse.quote('{"label": ["com.docker.compose.project=aura"]}'))
        broker.validate_query(route, "all=1&filters=" + urllib.parse.quote('{"label": ["aura.workspace=k"]}'))

    def test_logs_cannot_be_followed(self) -> None:
        route = broker.Route(kind="logs", container_id="abc")
        with pytest.raises(broker.PolicyError):
            broker.validate_query(route, "stdout=1&follow=1")
        broker.validate_query(route, "stdout=1&follow=0")


# -- end to end: docker-py -> broker -> fake daemon ----------------------------------------


class _FakeDaemon:
    """The few endpoints the adapter uses, with an unlabelled `postgres` container present."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []
        self.containers: dict[str, dict] = {
            "postgres": {"Id": "postgres", "Config": {"Image": "postgres:16", "Labels": {}}, "HostConfig": {}},
        }
        self._next = 0

    def handler(self):
        daemon = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _send(self, status, payload=b"", ctype="application/json"):
                data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _handle(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                path = urllib.parse.urlsplit(self.path).path
                path = re.sub(r"^/v\d+\.\d+", "", path)
                daemon.requests.append((self.command, path))
                if path == "/_ping":
                    return self._send(200, b"OK", "text/plain")
                if path == "/version":
                    return self._send(200, {"ApiVersion": "1.43", "Version": "24.0.0"})
                if path == "/containers/create":
                    doc = json.loads(body)
                    daemon._next += 1
                    cid = f"c{daemon._next}"
                    daemon.containers[cid] = {
                        "Id": cid, "Config": {"Image": doc["Image"], "Labels": doc.get("Labels") or {}, "Tty": False},
                        "HostConfig": doc["HostConfig"], "State": {"Status": "created"},
                    }
                    return self._send(201, {"Id": cid, "Warnings": []})
                if path == "/containers/json":
                    return self._send(200, [
                        {"Id": c["Id"]} for c in daemon.containers.values()
                        if any(k in c["Config"]["Labels"] for k in broker.SANDBOX_LABELS)
                    ])
                match = re.match(r"^/containers/([^/]+)(?:/(\w+))?$", path)
                if not match or match.group(1) not in daemon.containers:
                    return self._send(404, {"message": "no such container"})
                cid, action = match.group(1), match.group(2)
                if action == "json":
                    return self._send(200, daemon.containers[cid])
                if action in ("start", "stop", "kill"):
                    return self._send(204, b"")
                if action == "wait":
                    return self._send(200, {"StatusCode": 0})
                if action == "logs":
                    # Multiplexed stream frame: stdout, 3 bytes.
                    return self._send(200, b"\x01\x00\x00\x00\x00\x00\x00\x03hi\n", "application/vnd.docker.raw-stream")
                if action is None and self.command == "DELETE":
                    daemon.containers.pop(cid)
                    return self._send(204, b"")
                return self._send(404, {"message": "unhandled"})

            do_GET = do_POST = do_DELETE = do_HEAD = _handle

        return Handler


class _Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


@pytest.fixture
def stack(root, policy):
    fake = _FakeDaemon()
    upstream_server = _Server(("127.0.0.1", 0), fake.handler())
    threads = [threading.Thread(target=upstream_server.serve_forever, daemon=True)]
    up_port = upstream_server.server_address[1]
    broker_server = broker.serve(policy, broker.Upstream("127.0.0.1", up_port, timeout_s=10), "127.0.0.1", 0)
    threads.append(threading.Thread(target=broker_server.serve_forever, daemon=True))
    for t in threads:
        t.start()
    docker = pytest.importorskip("docker")
    client = docker.DockerClient(base_url=f"tcp://127.0.0.1:{broker_server.server_address[1]}", version="1.43", timeout=10)
    try:
        yield fake, client
    finally:
        client.close()
        broker_server.shutdown()
        upstream_server.shutdown()
        broker_server.server_close()
        upstream_server.server_close()


class TestThroughTheBroker:
    async def test_the_adapter_runs_a_command_through_the_broker(self, stack, root) -> None:
        fake, client = stack
        runner = DockerSandboxRunner(image=_IMAGE, network=_NETWORK, host_root=str(root))
        runner._client = client
        handle = await runner.create_task()

        result = await runner.run(handle, ["echo", "hi"], timeout_s=10)

        assert result.exit_code == 0
        assert result.stdout == "hi\n"
        assert ("POST", "/containers/create") in fake.requests
        assert ("DELETE", f"/containers/c1") in fake.requests

    async def test_the_adapter_opens_and_finds_a_durable_workspace(self, stack, root) -> None:
        fake, client = stack
        runner = DockerSandboxRunner(image=_IMAGE, network=_NETWORK, host_root=str(root))
        runner._client = client

        await runner.open_workspace("ws1")

        assert await runner.workspace_keys() == ["ws1"]

    def test_a_privileged_create_never_reaches_the_daemon(self, stack) -> None:
        fake, client = stack
        import docker

        with pytest.raises(docker.errors.APIError) as info:
            client.containers.create(
                _IMAGE, ["sh"], privileged=True, user="10001:10001",
                volumes={"/": {"bind": "/workspace", "mode": "rw"}},
                labels={"aura.sandbox.oneshot": "x"},
            )
        assert info.value.status_code == 403
        assert ("POST", "/containers/create") not in fake.requests

    def test_a_non_sandbox_container_cannot_be_touched(self, stack) -> None:
        fake, client = stack
        import docker

        for call in (
            lambda: client.api.stop("postgres"),
            lambda: client.api.remove_container("postgres", force=True),
            lambda: client.api.logs("postgres"),
            lambda: client.api.inspect_container("postgres"),
        ):
            with pytest.raises(docker.errors.APIError) as info:
                call()
            assert info.value.status_code == 403
        assert "postgres" in fake.containers
        assert all(path != "/containers/postgres" for _, path in fake.requests)

    def test_unfiltered_listing_is_refused(self, stack) -> None:
        _, client = stack
        import docker

        with pytest.raises(docker.errors.APIError):
            client.containers.list(all=True)


def test_policy_refuses_to_start_without_its_settings() -> None:
    with pytest.raises(ValueError):
        broker.Policy.from_env({})
    with pytest.raises(ValueError):
        broker.Policy(image=_IMAGE, workspace_root="/opt/x/../y", network=_NETWORK)
    assert os.path.basename(str(_BROKER)) == "broker.py"
