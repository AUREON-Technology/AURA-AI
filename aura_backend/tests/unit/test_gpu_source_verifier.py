"""The SaaS P1-G readiness verifier still runs, and fails where it must.

``deploy/scripts/verify_gpu_source.py`` only ever runs by hand against production. Nothing else
exercises it, so a mistake would first show in the one run meant to produce evidence. This
runs the whole driver against a loopback fake of the gateway, the Space and the Hugging Face API,
so it proves the driver's logic only. What the real GPU source does is what its report is for.
"""

from __future__ import annotations

import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

DRIVER = Path(__file__).resolve().parents[3] / "deploy" / "scripts" / "verify_gpu_source.py"
SPACE_KEY = "space-key"
USER = "user-token"
ADMIN = "admin-token"
HF = "hf-token"


class World:
    """What each fake answers; a test changes one field to break one thing."""

    def __init__(self) -> None:
        self.space_open = False
        self.probe = "active"
        self.hfspace_healthy = True
        self.hardware = "l4x1"
        self.gc_timeout: int | None = None
        self.metrics_status = 404
        self.training = False
        self.answer_provider = "hfspace"
        self.answer_wrong_code = False
        self.load_failures = 0
        self.code: str | None = None
        self.deleted: list[str] = []
        self.chats = 0
        self.lock = threading.Lock()


def _handler(world: World):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:  # quiet
            pass

        def _send(self, status: int, body: object) -> None:
            raw = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _auth(self) -> str:
            return self.headers.get("Authorization", "").removeprefix("Bearer ")

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(length) or b"{}")

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?")[0]
            if path == "/space/v1/models":
                if self._auth() != SPACE_KEY and not world.space_open:
                    return self._send(401, {"error": "unauthorized"})
                return self._send(200, {"data": [{"id": "Qwen/Qwen2.5-VL-7B-Instruct-AWQ"}]})
            if path == "/hf/api/spaces/me/gpu/runtime":
                if self._auth() != HF:
                    return self._send(401, {})
                return self._send(200, {"stage": "RUNNING", "hardware": {"current": world.hardware,
                                        "requested": world.hardware}, "gcTimeout": world.gc_timeout})
            if path == "/health/ready":
                return self._send(200, {"healthy": True, "checks": {"schema": {"healthy": True, "revision": "0029"}},
                                        "providers": {"hfspace": {"healthy": world.hfspace_healthy,
                                                                  "probe": world.probe},
                                                      "hosted1": {"healthy": True}}})
            if path in ("/metrics", "/docs", "/redoc", "/openapi.json"):
                return self._send(world.metrics_status, {"detail": "x"})
            if path == "/api/v3/admin/status":
                if self._auth() != ADMIN:
                    return self._send(403, {})
                return self._send(200, {"providers": [
                    {"provider": "hfspace", "may_train_on_prompts": False},
                    {"provider": "hosted1", "may_train_on_prompts": world.training},
                ]})
            self._send(404, {"detail": "Not Found"})

        def do_POST(self) -> None:  # noqa: N802
            body = self._body()
            if self.path == "/api/v3/knowledge/documents":
                text = body.get("content", "")
                world.code = next(w for w in text.split() if w.isdigit() and len(w) == 6)
                return self._send(200, {"document_id": "doc-1", "chunks": 1})
            if self.path == "/api/v3/chat":
                with world.lock:
                    world.chats += 1
                    failing = world.load_failures > 0 and not body.get("use_knowledge")
                    if failing:
                        world.load_failures -= 1
                if failing:
                    return self._send(503, {"error": "all_providers_failed"})
                if body.get("use_knowledge"):
                    code = "000000" if world.answer_wrong_code else world.code
                    return self._send(200, {"message": f"The code is {code}.", "provider": world.answer_provider})
                return self._send(200, {"message": "ready", "provider": "hfspace"})
            self._send(404, {})

        def do_DELETE(self) -> None:  # noqa: N802
            if self.path.startswith("/api/v3/knowledge/documents/"):
                world.deleted.append(self.path.rsplit("/", 1)[-1])
                return self._send(200, {"deleted": True})
            self._send(404, {})

    return Handler


@pytest.fixture
def world(monkeypatch, tmp_path):
    state = World()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    env = {
        "AURA_VERIFY_GATEWAY": base,
        "AURA_VERIFY_TOKEN": USER,
        # Spreads the load over accounts, which also skips the single-token 61 s wait.
        "AURA_VERIFY_EXTRA_TOKENS": "u2,u3",
        "AURA_VERIFY_ADMIN_TOKEN": ADMIN,
        "AURA_VERIFY_SPACE_URL": f"{base}/space",
        "AURA_VERIFY_SPACE_KEY": SPACE_KEY,
        "AURA_VERIFY_HF_TOKEN": HF,
        "AURA_VERIFY_SPACE_ID": "me/gpu",
        "AURA_VERIFY_HF_API": f"{base}/hf",
        "AURA_VERIFY_REPORT_DIR": str(tmp_path),
        "AURA_VERIFY_CONCURRENCY": "10",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    state.report_dir = tmp_path
    yield state
    server.shutdown()


def _run() -> tuple[int, str]:
    spec = importlib.util.spec_from_file_location("verify_gpu_source", DRIVER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    failed = module.main()
    reports = sorted(Path(module.REPORT_DIR_USED).glob("readiness-gpu-*.txt"))
    return failed, reports[-1].read_text()


def test_all_checks_pass_against_a_healthy_world(world) -> None:
    failed, report = _run()
    assert failed == 0, report
    assert report.count("PASS") >= 7 and "FAIL " not in report and "SKIP" not in report
    assert "PROVEN" in report and "NOT PROVEN" not in report
    for secret in (SPACE_KEY, USER, ADMIN, HF):
        assert secret not in report


def test_an_open_space_fails_check_1(world) -> None:
    world.space_open = True
    failed, report = _run()
    assert failed >= 1 and "FAIL   1." in report


def test_a_passive_probe_is_not_proof_of_health(world) -> None:
    world.probe = "passive"
    failed, report = _run()
    assert "FAIL   3." in report and "passive" in report


def test_a_sleeping_or_wrong_gpu_space_fails_check_2(world) -> None:
    world.gc_timeout = 3600
    failed, report = _run()
    assert "FAIL   2." in report
    world.gc_timeout = None
    world.hardware = "a10g-small"
    _, report = _run()
    assert "FAIL   2." in report


def test_public_operator_surfaces_and_training_models_fail(world) -> None:
    world.metrics_status = 200
    world.training = True
    _, report = _run()
    assert "FAIL   4." in report and "FAIL   5." in report


def test_a_document_answered_by_another_provider_fails(world) -> None:
    world.answer_provider = "hosted1"
    _, report = _run()
    assert "FAIL   6." in report and "hosted1" in report


def test_partial_failures_under_load_fail_capacity(world) -> None:
    world.load_failures = 2
    _, report = _run()
    assert "FAIL   7." in report and "2 of 10" in report


def test_the_document_is_deleted_even_when_the_check_fails(world) -> None:
    world.answer_wrong_code = True
    _, report = _run()
    assert "FAIL   6." in report
    assert world.deleted == ["doc-1"]


def test_missing_credentials_are_skip_not_pass(world, monkeypatch) -> None:
    monkeypatch.delenv("AURA_VERIFY_ADMIN_TOKEN")
    monkeypatch.delenv("AURA_VERIFY_HF_TOKEN")
    failed, report = _run()
    assert failed == 0
    assert "SKIP   2." in report and "SKIP   5." in report
    assert "NOT PROVEN" in report
