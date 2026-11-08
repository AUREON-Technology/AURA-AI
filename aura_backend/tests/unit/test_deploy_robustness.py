"""Deploy and runtime robustness (audit F-25).

Four gaps, each pinned here because none of them is visible to the rest of the suite -- the
default run never starts a container, never deploys, and never kills a worker:

* **The edge waited for readiness.** nginx had ``depends_on: gateway: service_healthy`` and the
  gateway's healthcheck hit ``/health/ready``, which is false whenever no LLM provider is healthy.
  So an Ollama outage plus any nginx recreate (every deploy does one) left nothing on 80/443.
* **The deploy gate could not fail.** ``curl -f https://$domain/health`` passes on nginx's 301
  (there is no ``/health`` route), and readiness was printed but "not fatal".
* **Every deploy SIGKILLed in-flight streams** after Docker's 10 s default.
* **Celery had no time limits, early acks, prefetch 4, and no healthchecks.**

Checks are against both compose files: the two stacks share a host (CLAUDE.md's parity rule).
"""

from __future__ import annotations

import http.server
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

_BACKEND = Path(__file__).resolve().parents[2]
_ROOT = _BACKEND.parent
_DEPLOY = _ROOT / "deploy"
_COMPOSE = {
    "prod": _DEPLOY / "docker-compose.prod.yml",
    "dev": _DEPLOY / "docker-compose.dev.yml",
}
_GATEWAY = {"prod": "gateway", "dev": "dev-gateway"}
_VPS_UPDATE = _DEPLOY / "scripts" / "vps-update.sh"
_CI_DEPLOY = _DEPLOY / "scripts" / "ci-deploy.sh"

#: The floors this change argues for (see the gateway comment in docker-compose.prod.yml).
GATEWAY_GRACE_FLOOR_S = 150
WORKER_GRACE_FLOOR_S = 120
#: Time lifespan shutdown (container.shutdown()) gets after uvicorn's drain, before SIGKILL.
LIFESPAN_MARGIN_S = 15


def _load(name: str) -> dict:
    path = _COMPOSE[name]
    assert path.is_file(), f"{path} is missing; these checks would pass vacuously"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module", params=sorted(_COMPOSE))
def compose(request) -> tuple[str, dict]:
    return request.param, _load(request.param)


def _seconds(value: str | int) -> int:
    if isinstance(value, int):
        return value
    match = re.fullmatch(r"(?:(\d+)m)?(?:(\d+)s)?", str(value).strip())
    assert match and any(match.groups()), f"unparseable duration {value!r}"
    minutes, seconds = (int(g) if g else 0 for g in match.groups())
    return minutes * 60 + seconds


def _graceful_shutdown_default(command: str) -> int:
    match = re.search(r"--timeout-graceful-shutdown\s+\$\{AURA_GRACEFUL_SHUTDOWN_S:-(\d+)\}", command)
    assert match, f"no --timeout-graceful-shutdown with a default in {command!r}"
    return int(match.group(1))


def _celery_services(services: dict, role: str) -> dict:
    return {
        name: svc
        for name, svc in services.items()
        if isinstance(svc.get("command"), str)
        and re.match(rf"celery -A \S+ {role}\b", svc["command"])
    }


# ---------------------------------------------------------------------------- the edge


class TestTheEdgeDoesNotWaitForReadiness:
    def test_nothing_waits_for_a_healthy_gateway(self, compose) -> None:
        """nginx directly, and console too: nginx depends on console, so a console that waited
        for a healthy gateway held the edge down transitively."""
        name, data = compose
        gateway = _GATEWAY[name]
        offenders = [
            svc_name
            for svc_name, svc in data["services"].items()
            if isinstance(svc.get("depends_on"), dict)
            and (svc["depends_on"].get(gateway) or {}).get("condition") == "service_healthy"
        ]
        assert not offenders, f"{name}: {offenders} wait for {gateway} to be healthy"

    def test_nginx_depends_on_the_gateway_only_being_started(self) -> None:
        nginx = _load("prod")["services"]["nginx"]
        assert nginx["depends_on"]["gateway"]["condition"] == "service_started"

    def test_the_gateway_healthcheck_is_liveness(self, compose) -> None:
        name, data = compose
        test = " ".join(data["services"][_GATEWAY[name]]["healthcheck"]["test"])
        assert "/health/live" in test
        assert "/health/ready" not in test


# ---------------------------------------------------------------------------- graceful drain


class TestGracefulDrain:
    def test_the_gateway_drains_before_it_is_killed(self, compose) -> None:
        name, data = compose
        svc = data["services"][_GATEWAY[name]]
        grace = _seconds(svc["stop_grace_period"])
        drain = _graceful_shutdown_default(svc["command"])

        assert grace >= GATEWAY_GRACE_FLOOR_S
        assert drain >= 120, "shorter than an ordinary turn on the CPU model"
        assert grace >= drain + LIFESPAN_MARGIN_S, "SIGKILL would land mid-drain"

    def test_the_gateway_command_is_execd_not_wrapped_in_a_shell(self, compose) -> None:
        """A list-free command string is exec'd by the entrypoint (`exec "$@"`), so uvicorn is
        PID 1 and receives SIGTERM. A `sh -c` wrapper without `exec` would swallow it."""
        name, data = compose
        command = data["services"][_GATEWAY[name]]["command"]
        assert command.split()[0] == "uvicorn"
        assert "exec \"$@\"" in (_BACKEND / "docker-entrypoint.sh").read_text(encoding="utf-8")

    def test_every_worker_gets_a_warm_shutdown_window(self, compose) -> None:
        name, data = compose
        workers = _celery_services(data["services"], "worker")
        assert {"worker-realtime", "worker-ingestion", "worker-training"} <= set(workers)
        for svc_name, svc in workers.items():
            assert "stop_grace_period" in svc, f"{name}/{svc_name} is SIGKILLed after 10 s"
            assert _seconds(svc["stop_grace_period"]) >= WORKER_GRACE_FLOOR_S, svc_name

    def test_prod_and_dev_drain_alike(self) -> None:
        prod, dev = _load("prod")["services"], _load("dev")["services"]
        assert prod["gateway"]["stop_grace_period"] == dev["dev-gateway"]["stop_grace_period"]
        assert _graceful_shutdown_default(prod["gateway"]["command"]) == _graceful_shutdown_default(
            dev["dev-gateway"]["command"]
        )
        for svc_name in ("worker-realtime", "worker-ingestion", "worker-training", "beat"):
            assert prod[svc_name]["stop_grace_period"] == dev[svc_name]["stop_grace_period"]
            assert prod[svc_name]["healthcheck"] == dev[svc_name]["healthcheck"], svc_name

    def test_railway_drains_the_same_way(self) -> None:
        config = json.loads((_BACKEND / "railway.json").read_text(encoding="utf-8"))
        cmd = next(
            ln for ln in (_BACKEND / "Dockerfile").read_text(encoding="utf-8").splitlines()
            if ln.startswith("CMD")
        )
        drain = _graceful_shutdown_default(cmd)

        assert "exec uvicorn" in cmd, "sh -c without exec leaves the shell as PID 1"
        assert config["deploy"]["drainingSeconds"] >= drain + LIFESPAN_MARGIN_S
        # Railway's gate stays on liveness (test_release_identity.py says why); unchanged here.
        assert config["deploy"]["healthcheckPath"] == "/health/live"


# ---------------------------------------------------------------------------- healthchecks


class TestCeleryHealthchecks:
    def test_every_worker_pings_itself(self, compose) -> None:
        name, data = compose
        for svc_name, svc in _celery_services(data["services"], "worker").items():
            test = svc.get("healthcheck", {}).get("test", [])
            assert test[-2:] == ["/app/scripts/celery_healthcheck.py", "worker"], f"{name}/{svc_name}"

    def test_beat_is_checked_against_the_file_it_writes(self, compose) -> None:
        name, data = compose
        beat = data["services"]["beat"]
        assert beat["healthcheck"]["test"][-1] == "beat"
        schedule = re.search(r"--schedule=(\S+)", beat["command"]).group(1)

        import scripts.celery_healthcheck as hc

        # The shelve backend may add a suffix (.db, .dat/.dir), hence the glob.
        assert hc.BEAT_SCHEDULE_GLOB == schedule + "*"

    def test_the_script_ships_in_the_image(self) -> None:
        """`COPY . .` with no .dockerignore rule excluding scripts/."""
        assert (_BACKEND / "scripts" / "celery_healthcheck.py").is_file()
        ignore = (_BACKEND / ".dockerignore").read_text(encoding="utf-8").split()
        assert not any(rule.strip("/").startswith("scripts") for rule in ignore)


class _FakeControl:
    def __init__(self, replies=None, exc=None):
        self.replies, self.exc, self.calls = replies or [], exc, []

    def ping(self, destination, timeout):
        self.calls.append(destination)
        if self.exc:
            raise self.exc
        return self.replies


class _FakeConnection:
    def __init__(self, exc=None):
        self.exc = exc

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def ensure_connection(self, **kwargs):
        if self.exc:
            raise self.exc


class _FakeApp:
    def __init__(self, control=None, conn_exc=None):
        self.control = control or _FakeControl()
        self._conn_exc = conn_exc

    def connection_for_write(self):
        return _FakeConnection(self._conn_exc)


class TestCeleryHealthcheckScript:
    def test_a_worker_is_healthy_only_when_its_own_node_answers(self) -> None:
        import scripts.celery_healthcheck as hc

        mine = _FakeApp(_FakeControl([{"celery@abc": {"ok": "pong"}}]))
        assert hc.check_worker(mine, "abc") == 0
        assert mine.control.calls == [["celery@abc"]]

        someone_else = _FakeApp(_FakeControl([{"celery@other": {"ok": "pong"}}]))
        assert hc.check_worker(someone_else, "abc") == 1
        assert hc.check_worker(_FakeApp(_FakeControl([])), "abc") == 1
        assert hc.check_worker(_FakeApp(_FakeControl(exc=OSError("refused"))), "abc") == 1

    def test_beat_needs_the_broker_and_a_fresh_schedule(self, tmp_path) -> None:
        import scripts.celery_healthcheck as hc

        schedule = tmp_path / "celerybeat-schedule"
        glob_ = str(tmp_path / "celerybeat-schedule*")
        assert hc.check_beat(_FakeApp(), glob_, 900) == 1, "no file yet"

        schedule.write_bytes(b"x")
        now = time.time()
        assert hc.check_beat(_FakeApp(), glob_, 900, now=now) == 0
        assert hc.check_beat(_FakeApp(), glob_, 900, now=now + 901) == 1, "stale"
        assert hc.check_beat(_FakeApp(conn_exc=OSError("down")), glob_, 900, now=now) == 1


# ---------------------------------------------------------------------------- celery config


class TestCeleryConfig:
    @pytest.fixture(scope="class")
    def conf(self):
        from celery_app import celery_app

        return celery_app.conf

    def test_late_acks_and_one_message_per_process(self, conf) -> None:
        assert conf.task_acks_late is True
        assert conf.task_reject_on_worker_lost is True
        assert conf.worker_prefetch_multiplier == 1

    def test_every_task_has_a_soft_and_a_hard_limit(self, conf) -> None:
        import tasks  # noqa: F401 -- registers the tasks
        from celery_app import celery_app

        assert 0 < conf.task_soft_time_limit < conf.task_time_limit
        ours = [n for n in celery_app.tasks if n.startswith("tasks.")]
        assert ours, "no tasks registered"
        for name in ours:
            limits = conf.task_annotations.get(name, {})
            soft = limits.get("soft_time_limit", conf.task_soft_time_limit)
            hard = limits.get("time_limit", conf.task_time_limit)
            assert 0 < soft < hard, name

    def test_annotations_name_real_tasks(self, conf) -> None:
        import tasks  # noqa: F401
        from celery_app import celery_app

        assert set(conf.task_annotations) <= set(celery_app.tasks)

    def test_training_is_never_redelivered(self, conf) -> None:
        """A redelivered training task retrains for hours."""
        for name in ("tasks.finetune_job_task", "tasks.drain_finetune_queue_task"):
            assert conf.task_annotations[name]["acks_late"] is False

    def test_redis_never_redelivers_a_task_that_is_still_running(self, conf) -> None:
        """With late acks on Redis, a message unacked for longer than the visibility timeout is
        handed to another worker -- so it must outlast every acks_late task's hard limit."""
        import tasks  # noqa: F401
        from celery_app import celery_app

        visibility = conf.broker_transport_options["visibility_timeout"]
        for name in (n for n in celery_app.tasks if n.startswith("tasks.")):
            limits = conf.task_annotations.get(name, {})
            if limits.get("acks_late", conf.task_acks_late):
                assert limits.get("time_limit", conf.task_time_limit) < visibility, name

    def test_an_interrupted_run_does_not_leave_its_coroutine_on_the_loop(self) -> None:
        """A soft time limit raises from a signal handler, out of run_until_complete. The worker
        loop is reused, so a coroutine left scheduled would resume inside the *next* task."""
        if not hasattr(signal, "setitimer"):
            pytest.skip("needs SIGALRM")
        import asyncio

        import tasks

        class _Limit(Exception):
            pass

        state = {"cancelled": False, "resumed": False}

        async def slow():
            try:
                await asyncio.sleep(5)
                state["resumed"] = True
            except asyncio.CancelledError:
                state["cancelled"] = True
                raise

        def _raise(*_):
            raise _Limit()

        previous = signal.signal(signal.SIGALRM, _raise)
        try:
            signal.setitimer(signal.ITIMER_REAL, 0.05)
            with pytest.raises(_Limit):
                tasks._run_async(slow())
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)

        assert state["cancelled"] is True

        async def quick():
            await asyncio.sleep(0.01)
            return 7

        assert tasks._run_async(quick()) == 7
        assert state["resumed"] is False


# ---------------------------------------------------------------------------- deploy gate


def _ready_probe() -> str:
    text = _VPS_UPDATE.read_text(encoding="utf-8")
    match = re.search(r"^READY_PROBE='(.*?)'$", text, flags=re.S | re.M)
    assert match, "READY_PROBE not found in vps-update.sh"
    return match.group(1)


class _Handler(http.server.BaseHTTPRequestHandler):
    status = 200
    payload: dict = {}

    def do_GET(self):  # noqa: N802
        body = json.dumps(type(self).payload).encode()
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class TestDeployGate:
    def test_liveness_is_checked_on_a_routed_path_and_must_be_200(self) -> None:
        ci = _CI_DEPLOY.read_text(encoding="utf-8")
        update = _VPS_UPDATE.read_text(encoding="utf-8")

        assert 'AURA_HEALTH_URL="https://${domain}/health/live"' in ci
        assert '"https://${DOMAIN}/health/live"' in update
        assert '[ "$LIVE_CODE" = 200 ]' in update, "curl -f would accept nginx's 301"

    def test_readiness_is_fatal(self) -> None:
        update = _VPS_UPDATE.read_text(encoding="utf-8")
        assert "Not fatal" not in update
        gate = update[update.index("Waiting for readiness") :]
        assert "exit 1" in gate[: gate.index('info "readiness: OK')]

    def test_ci_deploy_has_no_byte_order_mark(self) -> None:
        """A BOM ahead of `#!` makes bash run the shebang line as a command."""
        assert _CI_DEPLOY.read_bytes().startswith(b"#!")

    @pytest.fixture()
    def serve(self):
        server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def run(status: int, payload: dict, expect_schema: bool = True):
            _Handler.status, _Handler.payload = status, payload
            probe = _ready_probe().replace(
                "http://127.0.0.1:8000", f"http://127.0.0.1:{server.server_port}"
            )
            env = {**os.environ, "AURA_EXPECT_SCHEMA": "1" if expect_schema else "0"}
            done = subprocess.run(
                [sys.executable, "-c", probe], env=env, capture_output=True, text=True, timeout=30
            )
            return done.returncode, done.stdout.splitlines()[0]

        yield run
        server.shutdown()

    def test_the_probe_passes_a_ready_gateway(self, serve) -> None:
        code, verdict = serve(
            200, {"healthy": True, "checks": {"schema": {"healthy": True, "revision": "0016"}}}
        )
        assert (code, verdict) == (0, "READY")

    @pytest.mark.parametrize(
        "status, payload, reason",
        [
            (503, {"healthy": False, "checks": {"schema": {"healthy": True, "revision": "0016"}}},
             "healthy is not true"),
            (503, {"healthy": False, "checks": {"schema": {
                "healthy": False, "revision": "0013", "expected": "0016",
                "detail": "database schema is behind the code in this image"}}},
             "schema: database schema is behind"),
            (200, {"healthy": True, "checks": {"schema": {
                "healthy": True, "expected": "0016", "detail": "unverified: connection refused"}}},
             "schema unverified"),
            (200, {"healthy": True}, "no schema block"),
        ],
    )
    def test_the_probe_refuses_anything_less(self, serve, status, payload, reason) -> None:
        code, verdict = serve(status, payload)
        assert code == 1
        assert verdict.startswith("NOT_READY") and reason in verdict, verdict

    def test_a_stack_without_postgres_needs_no_schema_block(self, serve) -> None:
        assert serve(200, {"healthy": True}, expect_schema=False) == (0, "READY")


# ---------------------------------------------------------------- forwarded headers (FSA-005)


class TestOneLimiterBehindATrustedHop:
    """Audit FSA-005: behind Railway's edge every request used to arrive with the proxy's
    address, so the legacy per-IP middleware put every user in one bucket and audit rows
    recorded the proxy. uvicorn must trust the edge's forwarded headers, and only the
    aura_core limiter (keyed on `extract_client_ip`) may be wired."""

    def test_the_image_command_trusts_forwarded_headers(self) -> None:
        cmd = next(
            ln for ln in (_BACKEND / "Dockerfile").read_text(encoding="utf-8").splitlines()
            if ln.startswith("CMD")
        )
        assert "--proxy-headers" in cmd
        assert "--forwarded-allow-ips" in cmd, "without it --proxy-headers trusts 127.0.0.1 only"

    def test_both_compose_gateways_trust_forwarded_headers(self, compose) -> None:
        name, data = compose
        command = data["services"][_GATEWAY[name]]["command"]
        text = command if isinstance(command, str) else " ".join(command)
        assert "--proxy-headers" in text and "--forwarded-allow-ips" in text

    def test_the_legacy_per_ip_middleware_is_not_wired(self) -> None:
        source = (_BACKEND / "aura_service.py").read_text(encoding="utf-8")
        assert "add_middleware(RateLimitMiddleware" not in source
        # The one limiter: SlidingWindowRateLimiter handed to the v3 router.
        assert "SlidingWindowRateLimiter.from_settings" in source
