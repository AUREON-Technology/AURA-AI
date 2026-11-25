"""Simulated end-to-end proof: the open-tool classes that are not agent tools, and the platform-level rows.

Rows covered: 53 (bytes level), 54, 55, 58, 65, 66, 67 (relay half), 68, 78, 86, 97, 98, 99, 100, 104,
136-140 of the incomplete-tools sheet. Rows 52 and 53 against a real browser are in
``test_incomplete_tools_live_env.py`` (gated); 52's logic is also driven here over a scripted Playwright.

These classes stay unregistered on purpose (they run code or act outside the workspace), so their
public API is called directly -- in temp directories, with real subprocesses where the class starts
one. What is SIMULATED:

* Playwright (not in the lock file): a scripted ``playwright.sync_api`` module injected into
  ``sys.modules`` whose pages fetch from a loopback server; the real route guard runs on every request;
* public DNS/TCP for the SSRF-guarded webhook: ``simulate_public_hosts`` (see sim_support) -- the real
  ``check_url`` runs unchanged;
* a paired desktop and a second gateway worker: a scripted daemon behind the real
  ``HostAgentSandboxRunner`` and two ``HostAgentRegistry`` objects sharing an ``InMemoryRelayBus``;
* the planner for kernel turns (``ScriptedPlanner``).
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import tarfile
import types
import zipfile
from pathlib import Path
from typing import Any

import pytest

from aura_core.domain.identifiers import UserId
from aura_core.kernel import TurnRequest
from tests.e2e.sim_support import ALICE, ENGINEER, RecordingServer, call, context, simulate_public_hosts
from tests.unit.test_autonomy_profile import ScriptedPlanner

pytestmark = pytest.mark.asyncio


# ------------------------------------------------------------------------ row 52: browser_subagent


class _FakePage:
    def __init__(self, base_url: str, requests: list, guard: Any) -> None:
        self.url = base_url
        self._requests = requests
        self._guard = guard
        self._html = ""
        self.clicked: list[str] = []
        self.filled: dict[str, str] = {}

    def set_default_timeout(self, ms: int) -> None:
        self.timeout = ms

    def _fetch(self, url: str) -> bytes:
        import urllib.request

        route = types.SimpleNamespace(request=types.SimpleNamespace(url=url))
        verdict: dict[str, str] = {}
        route.abort = lambda: verdict.setdefault("v", "aborted")
        route.continue_ = lambda: verdict.setdefault("v", "continued")
        self._guard(route)
        self._requests.append((url, verdict["v"]))
        if verdict["v"] == "aborted":
            return b""
        with urllib.request.urlopen(url, timeout=5) as response:  # the loopback server
            return response.read()

    def goto(self, url: str, **kw: Any) -> None:
        self.url = url
        self._html = self._fetch(url).decode()
        self._fetch("http://169.254.169.254/latest/meta-data/")  # a page sub-resource aimed at cloud metadata

    def click(self, selector: str) -> None:
        self.clicked.append(selector)

    def fill(self, selector: str, value: str) -> None:
        self.filled[selector] = value

    def wait_for_selector(self, selector: str) -> None:
        assert selector.lstrip("#.") in self._html or True

    def screenshot(self, path: str, **kw: Any) -> None:
        Path(path).write_bytes(b"\x89PNG\r\n\x1a\nFAKE")

    def title(self) -> str:
        return self._html.split("<title>")[1].split("</title>")[0] if "<title>" in self._html else ""

    def inner_text(self, selector: str) -> str:
        return self._html

    def query_selector(self, selector: str) -> Any:
        return None

    def evaluate(self, script: str) -> Any:
        return {"ran": script}


def _install_fake_playwright(monkeypatch, requests: list) -> None:
    class Context:
        def route(self, pattern: str, handler: Any) -> None:
            self.handler = handler

        def new_page(self) -> _FakePage:
            return _FakePage("", requests, self.handler)

    class Browser:
        def new_context(self, **kw: Any) -> Context:
            return Context()

        def close(self) -> None:
            pass

    class Playwright:
        chromium = types.SimpleNamespace(launch=lambda headless=True: Browser())

    class Manager:
        def __enter__(self) -> Playwright:
            return Playwright()

        def __exit__(self, *a: Any) -> None:
            return None

    package = types.ModuleType("playwright")
    package.__path__ = []  # type: ignore[attr-defined]
    api = types.ModuleType("playwright.sync_api")
    api.sync_playwright = lambda: Manager()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "playwright", package)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", api)


async def test_row52_browser_subagent_guards_urls_and_every_request_and_drives_the_page(monkeypatch, tmp_path) -> None:
    from aura_core.tools.open import browser_subagent as bs
    from aura_core.tools.open.browser_subagent import BrowserSubagent

    page = (b"<html><head><title>Cart</title></head><body>2 items</body></html>")
    server = RecordingServer({"/cart": (200, page)})
    simulate_public_hosts(monkeypatch, {"shop.sim.example": server.port})
    try:
        assert BrowserSubagent.check_playwright_availability()["available"] is False, "Playwright is not installed here"
        for refused in ("http://127.0.0.1:8000/", "http://169.254.169.254/latest/meta-data/", "file:///etc/passwd",
                        "http://localhost/", "http://db/"):
            out = BrowserSubagent.run_actions(refused)
            assert out["executed"] is False and "URL refused" in out["error"], refused
        no_pw = BrowserSubagent.run_actions("http://shop.sim.example/cart")
        assert no_pw["executed"] is False and "not installed" in no_pw["error"], "never a fake success"

        probe = BrowserSubagent.check_url_accessibility("http://shop.sim.example/cart")
        assert probe["accessible"] is True and probe["status_code"] == 200

        requests: list = []
        _install_fake_playwright(monkeypatch, requests)
        shot = tmp_path / "cart.png"
        result = BrowserSubagent.run_actions(
            "http://shop.sim.example/cart",
            [{"type": "fill", "selector": "#q", "value": "tv"}, {"type": "click", "selector": "#go"}],
            screenshot_path=str(shot), base_dir=str(tmp_path))
        assert result["executed"] is True and result["title"] == "Cart" and result["skipped_actions"] == 0
        assert [s["action"] for s in result["steps"]] == ["goto", "fill", "click", "screenshot"]
        assert shot.read_bytes().startswith(b"\x89PNG")
        assert ("http://169.254.169.254/latest/meta-data/", "aborted") in requests, "the route guard aborts internal sub-requests"
        assert ("http://shop.sim.example/cart", "continued") in requests

        skipped = BrowserSubagent.run_actions("http://shop.sim.example/cart", [{"type": "teleport", "selector": "#x"}])
        assert skipped["executed"] is False and skipped["skipped_actions"] == 1, "an unknown action is reported, not skipped silently"
        bad_shot = BrowserSubagent.run_actions("http://shop.sim.example/cart", [], screenshot_path=str(tmp_path / "x.txt"),
                                               base_dir=str(tmp_path))
        assert bad_shot["executed"] is False
        text = BrowserSubagent.extract_page_content("http://shop.sim.example/cart")
        assert text["success"] is True and "2 items" in text["content"] and text["title"] == "Cart"
        script = BrowserSubagent.run_script_in_page("http://shop.sim.example/cart", "document.title")
        assert script["success"] is True
    finally:
        server.close()


# ------------------------------------------------------------------ row 53: ui_visual_verifier (bytes)


def _png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    import struct
    import zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


async def test_row53_ui_visual_verifier_compares_files_dimensions_and_fingerprints(tmp_path) -> None:
    from aura_core.tools.open.ui_visual_verifier import UiVisualVerifier

    files = {"a": _png(40, 20, (10, 120, 240)), "same": _png(40, 20, (10, 120, 240)),
             "other": _png(40, 20, (240, 20, 10)), "wide": _png(80, 20, (10, 120, 240))}
    path = {}
    for name, data in files.items():
        path[name] = str(tmp_path / f"{name}.png")
        (tmp_path / f"{name}.png").write_bytes(data)
    base = str(tmp_path)
    assert UiVisualVerifier.extract_image_dimensions(path["a"], base_dir=base) == (40, 20, "PNG")
    same = UiVisualVerifier.verify_visuals(path["a"], path["same"], base_dir=base)
    assert same["exact_match"] is True and same["status"] == "PASSED_VISUAL_CHECK" and same["diff_percentage"] == 0.0
    differ = UiVisualVerifier.verify_visuals(path["a"], path["other"], tolerance_percentage=1.0, base_dir=base)
    assert differ["status"] == "VISUAL_REGRESSION_DETECTED" and differ["diff_percentage"] > 1.0
    assert "bytes are compared, not decoded pixels" in " ".join(differ["warnings"]), "it states it is not a pixel diff"
    cmp = UiVisualVerifier.compare_image_dimensions(path["a"], path["wide"], base_dir=base)
    assert cmp["match"] is False and cmp["reference"]["width"] == 80 and cmp["ratio_difference"] == 50.0
    assert UiVisualVerifier.compute_perceptual_hash(path["a"], base_dir=base) == UiVisualVerifier.compute_perceptual_hash(path["same"], base_dir=base)
    assert len(UiVisualVerifier.compute_perceptual_hash(path["a"], base_dir=base)) == 16
    outside = UiVisualVerifier.verify_visuals("../../etc/passwd", path["a"], base_dir=base)
    assert outside["status"] == "INPUT_ERROR" and "outside" in outside["error"]


# --------------------------------------------------------------------------- row 54: dev_server_manager


async def test_row54_dev_server_manager_runs_a_real_server_probes_it_and_stops_the_whole_group(tmp_path) -> None:
    from aura_core.tools.open.dev_server_manager import DevServerManager

    (tmp_path / "index.html").write_text("<h1>dev server up</h1>", encoding="utf-8")
    manager = DevServerManager()
    for refused in ("python -c 'print(1)'", "bash -c 'echo hi'", "npx something", "rm -rf /", "python -m pip install x"):
        out = manager.start_server(refused, cwd=str(tmp_path), base_dir=str(tmp_path))
        assert out["started"] is False and "rejected" in out["error"].lower(), refused
    port = DevServerManager.find_free_port(18500)
    started = manager.start_server(f"python3 -m http.server {port} --bind 127.0.0.1", cwd=str(tmp_path),
                                   base_dir=str(tmp_path), wait_seconds=0.5, port=port)
    try:
        assert started["started"] is True, started
        pid = started["pid"]
        assert DevServerManager.wait_for_port_ready("127.0.0.1", port, timeout_seconds=10) is True
        health = DevServerManager.check_http_health(f"http://127.0.0.1:{port}/index.html")
        assert health["healthy"] is True and health["status_code"] == 200
        assert DevServerManager.check_http_health("http://127.0.0.1:1/")["healthy"] is False, "an unallocated port is refused"
        assert "refused" in DevServerManager.check_http_health(f"http://127.0.0.1:{port}/api/v3/x")["error"].lower()
        assert manager.check_process_status(pid)["running"] is True
        escape = manager.start_server("python3 -m http.server 1", cwd="/", base_dir=str(tmp_path))
        assert escape["started"] is False, "cwd is confined to base_dir"
    finally:
        stopped = manager.stop_all()
    assert manager.check_process_status(pid)["running"] is False, stopped
    assert DevServerManager.wait_for_port_ready("127.0.0.1", port, timeout_seconds=1) is False


# ----------------------------------------------------------------------- row 55: background_task_manager


async def test_row55_background_task_manager_start_poll_input_wait_terminate(tmp_path) -> None:
    from aura_core.tools.open.background_task_manager import BackgroundTaskManager

    manager = BackgroundTaskManager()
    py = "python3"
    script = tmp_path / "echoer.py"
    script.write_text("import sys\nprint('ready', flush=True)\nfor line in sys.stdin:\n    print('got:' + line.strip(), flush=True)\n    if line.strip() == 'quit':\n        break\n", encoding="utf-8")
    allowed = ["python3"]
    try:
        started = manager.start_task("t1", [py, str(script)], cwd=str(tmp_path), base_dir=str(tmp_path), allowed_executables=allowed)
        assert started["started"] is True, started
        dup = manager.start_task("t1", [py, str(script)], cwd=str(tmp_path), base_dir=str(tmp_path), allowed_executables=allowed)
        assert dup["started"] is False, "a running task id cannot be reused"
        for _ in range(50):
            if "ready" in manager.read_output("t1").get("stdout", ""):
                break
            await asyncio.sleep(0.1)
        assert manager.send_task_input("t1", "hello")["delivered"] is True
        manager.send_task_input("t1", "quit")
        done = manager.wait_for_task("t1", timeout_seconds=10)
        assert done.get("exit_code") == 0, done
        assert "got:hello" in manager.read_output("t1")["stdout"]

        sleeper = manager.start_task("t2", [py, "-c", "import time; time.sleep(60)"], cwd=str(tmp_path), base_dir=str(tmp_path), allowed_executables=allowed)
        assert sleeper["started"] is True
        assert manager.get_status("t2")["running"] is True
        assert manager.terminate_task("t2")["terminated"] is True
        assert manager.get_status("t2")["running"] is False
        assert manager.start_task("bad", ["/bin/sh", "-c", "echo x"], allowed_executables=allowed)["started"] is False
        leaked = manager.start_task("env", [py, "-c", "pass"], env={"AURA_SECRET_KEY": "x"}, allowed_executables=allowed)
        assert leaked["started"] is False, "a credential-looking env name is refused"
        outside = manager.start_task("cwd", [py, "-c", "pass"], cwd="/", base_dir=str(tmp_path), allowed_executables=allowed)
        assert outside["started"] is False
    finally:
        manager.terminate_all()


# ----------------------------------------------------------------- row 66: push_notification_dispatcher


async def test_row66_push_dispatcher_builds_formats_and_posts_through_the_ssrf_guard(monkeypatch) -> None:
    from aura_core.tools.open.push_notification_dispatcher import PushNotificationDispatcher as P

    server = RecordingServer({"/hook": (200, {"ok": True}), "/redir": (302, b"")})
    simulate_public_hosts(monkeypatch, {"hooks.sim.example": server.port})
    try:
        payload = P.build_payload("Build done", "All 12 tests passed", event_type="task_completed", priority="high")
        assert payload["title"] == "Build done" and payload["priority"] == "high"
        slack = P.format_slack_block_kit("Build done", "All 12 tests passed", priority="high", action_url="https://example.com/run/1")
        assert "Build done" in json.dumps(slack) and "All 12 tests passed" in json.dumps(slack)
        assert "Build done" in json.dumps(P.format_discord_embed("Build done", "msg", priority="urgent"))
        headers = P.format_ntfy_headers("Build done", priority="high", tags=["white_check_mark"])
        assert any(k.lower() == "priority" for k in headers)

        sent = P.dispatch("Build done", "All 12 tests passed", webhook_url="http://hooks.sim.example/hook")
        assert sent["dispatched"] is True and sent["status"] == "delivered" and sent["status_code"] == 200, sent
        (request,) = server.requests
        assert request["method"] == "POST" and request["path"] == "/hook"
        assert json.loads(request["body"])["title"] == "Build done"
        for refused in ("http://127.0.0.1/hook", "http://169.254.169.254/", "http://localhost:9/"):
            out = P.dispatch("t", "m", webhook_url=refused)
            assert out.get("dispatched") is False and "refused" in json.dumps(out).lower(), (refused, out)
        assert P.dispatch("t", "m").get("dispatched") is False, "without a URL it says not_sent"
        redirected = P.dispatch("t", "m", webhook_url="http://hooks.sim.example/redir")
        assert redirected.get("dispatched") is not True, ("redirects are never followed", redirected)
    finally:
        server.close()


# ---------------------------------------------------------------------- row 68: remote_session_controller


async def test_row68_remote_session_controller_ledgers_actions_and_checkpoints_and_admits_it_signals_nothing() -> None:
    from aura_core.tools.open.remote_session_controller import RemoteSessionController

    c = RemoteSessionController()
    assert c.get_or_create_session("s1")["state"] == "running"
    paused = c.apply_action("s1", "pause", reason="lunch")
    assert (paused["previous_state"], paused["current_state"]) == ("running", "paused")
    assert "does not signal, pause or stop any running session" in paused["note"], "it says it is a ledger only"
    assert c.apply_action("s1", "resume")["current_state"] == "running"
    redirected = c.apply_action("s1", "redirect", redirect_target="fix the failing test")
    assert redirected["target"] == "fix the failing test"
    bad = c.apply_action("s1", "explode")
    assert bad["updated"] is False and "Invalid action" in bad["error"]
    saved = c.save_session_checkpoint("s1", {"step": 3, "files": ["a.py"]})
    restored = c.restore_session_checkpoint("s1", saved["checkpoint_id"])
    assert restored["restored"] is True and restored["snapshot_data"] == {"step": 3, "files": ["a.py"]}
    c.save_session_checkpoint("s1", {"step": 4})
    assert c.restore_session_checkpoint("s1")["snapshot_data"] == {"step": 4}, "the latest by default"
    assert [s["session_id"] for s in c.list_active_sessions()] == ["s1"]
    assert c.terminate_session("s1", "done")["current_state"] == "terminated"
    assert c.list_active_sessions(state_filter="running") == []


# --------------------------------------------------------------------------------- row 78: archive_extractor


async def test_row78_archive_extractor_lists_audits_extracts_reads_and_refuses_hostile_archives(tmp_path) -> None:
    from aura_core.tools.open.archive_extractor import ArchiveExtractor as A

    def at(name: str) -> str:
        return str(tmp_path / name)

    base = str(tmp_path)
    with zipfile.ZipFile(at("good.zip"), "w") as z:
        z.writestr("src/main.py", "print('hello')\n")
        z.writestr("docs/readme.txt", "read me\n")
    listing = A.list_archive_contents(at("good.zip"), base_dir=base)
    assert listing["valid"] and {m["name"] for m in listing["members"]} == {"src/main.py", "docs/readme.txt"}
    assert A.audit_archive_safety(at("good.zip"), base_dir=base)["safe"] is True
    extracted = A.extract_archive(at("good.zip"), at("out"), base_dir=base)
    assert extracted["extracted"] is True and extracted["extracted_count"] == 2
    assert (tmp_path / "out" / "src" / "main.py").read_text() == "print('hello')\n"
    assert A.read_archive_member(at("good.zip"), "docs/readme.txt", base_dir=base)["text"] == "read me\n"
    assert A.extract_single_file(at("good.zip"), "src/main.py", at("one/main.py"), base_dir=base)["extracted"] is True
    assert (tmp_path / "one" / "main.py").exists()

    with zipfile.ZipFile(at("evil.zip"), "w") as z:
        z.writestr("../../outside.txt", "pwned")
        z.writestr("ok.txt", "fine")
    audit = A.audit_archive_safety(at("evil.zip"), base_dir=base)
    assert audit["safe"] is False and audit["path_traversal_attempts"] == ["../../outside.txt"]
    # pytest's tmp_path parent is shared by every test in the session, and other tests leave their
    # own `outside.txt` there, so "does not exist" is not a safe assertion: compare it with how it
    # was before the extraction and check it never holds this archive's payload.
    shared = tmp_path.parent / "outside.txt"
    before = shared.read_bytes() if shared.exists() else None
    refused = A.extract_archive(at("evil.zip"), at("out2"), base_dir=base)
    assert refused["extracted"] is False and "Path traversal blocked" in refused["error"]
    assert (shared.read_bytes() if shared.exists() else None) == before and before != b"pwned"
    assert not (tmp_path / "outside.txt").exists()

    with tarfile.open(at("link.tar"), "w") as t:
        info = tarfile.TarInfo("evil_link")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        t.addfile(info)
    assert A.audit_archive_safety(at("link.tar"), base_dir=base)["link_members"] == ["evil_link"], "links are flagged"

    with zipfile.ZipFile(at("bomb.zip"), "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("zeros.bin", b"\0" * 30_000_000)
    assert A.audit_archive_safety(at("bomb.zip"), base_dir=base)["compression_ratio"] > 100, "30 MB of zeros stored in a few KB"
    capped = A.extract_archive(at("bomb.zip"), at("out3"), max_size_mb=5, base_dir=base)
    assert capped["extracted"] is False, "a decompression bomb is stopped by the size cap"

    created = A.create_archive(at("out"), at("made.tar.gz"), format_type="tar.gz", base_dir=base)
    assert created["created"] is True and created["files_count"] == 2
    with tarfile.open(tmp_path / "made.tar.gz") as t:
        assert any(n.endswith("main.py") for n in t.getnames())
    assert A.list_archive_contents("../../etc/passwd", base_dir=base)["valid"] is False


# --------------------------------------------------------------------- rows 97, 98, 100: ledger classes


async def test_row97_data_lineage_tracker_chains_cycles_and_mermaid() -> None:
    from aura_core.tools.open.data_lineage_provenance_tracker import DataLineageProvenanceTracker as T

    t = T()
    t.record_transformation("upload.pdf", "chunks", "chunk")
    t.record_transformation("chunks", "embeddings", "embed")
    t.record_transformation("embeddings", "answer", "retrieve")
    assert t.get_upstream_sources("answer") == ["embeddings"] and t.get_downstream_targets("upload.pdf") == ["chunks"]
    assert t.trace_path("upload.pdf") == ["upload.pdf", "chunks", "embeddings", "answer"]
    assert t.get_provenance_chain("answer") == ["embeddings", "chunks", "upload.pdf"], "nearest ancestor first, back to the root"
    assert t.detect_circular_lineage() == []
    t.record_transformation("answer", "upload.pdf", "feedback")
    assert t.detect_circular_lineage(), "a cycle is reported"
    mermaid = t.export_to_mermaid()
    assert mermaid.startswith("flowchart") and "chunks" in mermaid


async def test_row98_agent_tool_test_harness_runs_cases_schemas_and_fuzz() -> None:
    from aura_core.tools.open.agent_tool_test_harness import AgentToolTestHarness as H

    def add(a: int, b: int) -> int:
        return a + b

    ok = H.run_tool_case("add", add, {"a": 1, "b": 2}, required_keys=["a", "b"], expected_return_type=int)
    assert ok["passed"] is True and ok["output_preview"] == 3
    missing = H.run_tool_case("add", add, {"a": 1}, required_keys=["a", "b"], expected_return_type=int)
    assert missing["passed"] is False and missing["key_check"]["missing_keys"] == ["b"]
    wrong = H.run_tool_case("add", lambda a, b: "x", {"a": 1, "b": 2}, expected_return_type=int)
    assert wrong["passed"] is False
    suite = H.run_suite([
        {"tool_name": "add", "callable": add, "payload": {"a": 1, "b": 2}, "expected_return_type": int},
        {"tool_name": "boom", "callable": lambda: 1 / 0, "payload": {}},
    ])
    assert (suite["total_cases"], suite["passed_count"], suite["failed_count"]) == (2, 1, 1), suite
    assert "boom" in H.generate_tool_test_report(suite)
    good_schema = {"name": "f", "description": "Looks a thing up by its identifier and returns it.", "parameters": {"type": "object", "properties": {"x": {"type": "string"}}}}
    assert H.validate_tool_schema(good_schema).get("valid") is True
    assert H.validate_tool_schema({"description": "no name"}).get("valid") is False
    fragile = H.run_fuzz_tests(lambda text: text.upper(), {"text": "a"}, iterations=5)
    assert fragile["resilient"] is False and fragile["crashes_count"] >= 1, "a tool that crashes on odd input is reported"
    sturdy = H.run_fuzz_tests(lambda text: str(text).upper(), {"text": "a"}, iterations=5)
    assert sturdy["resilient"] is True


async def test_row100_gpu_scheduler_ledger_estimates_and_queue() -> None:
    from aura_core.tools.open.gpu_resource_scheduler import GpuResourceScheduler as G

    g = G(total_vram_gb=12.0)
    need = G.estimate_model_vram_requirement(7, precision_bits=16, context_length=4096)
    need4 = G.estimate_model_vram_requirement(7, precision_bits=4, context_length=4096)
    assert need["weights_vram_gb"] == 13.04 and need4["weights_vram_gb"] < 4.0
    assert g.request_allocation("job-a", 8.0)["allocated"] is True
    assert g.request_allocation("job-b", 8.0)["allocated"] is False, "over capacity is refused"
    assert g.available_vram_gb() == 4.0
    queued = g.schedule_workload_queue([{"job_id": "job-c", "requested_vram_gb": 3.0}, {"job_id": "job-d", "requested_vram_gb": 3.0}])
    assert queued["allocated_jobs"] == ["job-c"] and "job-d" not in queued["allocated_jobs"]
    assert "not read from the GPU" in queued["current_status"]["note"], "it admits it is a ledger"
    assert g.release_allocation("job-a")
    offload = G.suggest_offload_strategy(14.0, 8.0)
    assert offload["fits_natively"] is False and offload["deficit_gb"] == 6.0


# -------------------------------------------------------------------- row 99: model registry and promotion


async def test_row99_model_registry_stages_approval_gated_promotion_rollback_and_ab_assignment() -> None:
    from aura_core.mlops.model_registry import InMemoryModelStore, ModelRegistry, ModelStage, ModelVersion

    from aura_core.learning import ApprovalKind, ApprovalService, InMemoryApprovalStore

    approvals = ApprovalService(store=InMemoryApprovalStore())
    registry = ModelRegistry(store=InMemoryModelStore(), approvals=approvals)

    async def approved(key: str) -> str:
        request = await approvals.request(kind=ApprovalKind.MODEL_PROMOTION, summary=f"promote {key}", artefact_digest=key)
        await approvals.decide(request.id, approved=True, by="reviewer@example.com")
        return request.id

    v1 = await registry.register(ModelVersion(model_id="qwen", version="1", provider_kind="openai_compatible"))
    await registry.register(ModelVersion(model_id="qwen", version="2", provider_kind="openai_compatible"))
    assert v1.stage is ModelStage.CANDIDATE
    await registry.record_metrics("qwen", "2", {"accuracy": 0.93, "p95_ms": 840.0})
    assert (await registry.get("qwen", "2")).metrics["accuracy"] == 0.93
    with pytest.raises(Exception):
        await registry.transition("qwen", "2", to=ModelStage.PRODUCTION)  # not even staged, no approval id: refused
    for stage in (ModelStage.SHADOW, ModelStage.CANARY):
        await registry.transition("qwen", "1", to=stage, canary_percent=5 if stage is ModelStage.CANARY else 0, actor="admin")
    await registry.transition("qwen", "1", to=ModelStage.PRODUCTION, approval_id=await approved("qwen@1"), actor="admin")
    await registry.transition("qwen", "2", to=ModelStage.SHADOW, actor="admin")
    await registry.transition("qwen", "2", to=ModelStage.CANARY, canary_percent=20, actor="admin")
    assert (await registry.production("qwen")).version == "1"
    seen = set()
    for i in range(200):
        assigned = await registry.assign("qwen", subject=f"user-{i}")
        seen.add(assigned.version)
    assert seen == {"1", "2"}, "a canary takes a share of subjects"
    await registry.transition("qwen", "2", to=ModelStage.PRODUCTION, approval_id=await approved("qwen@2"), actor="admin")
    await registry.rollback("qwen", to_version="1", actor="admin")
    assert (await registry.production("qwen")).version == "1"
    assert await registry.inventory()


# -------------------------------------------------------------------- row 104: knowledge graph (class + durable)


async def test_row104_knowledge_graph_class_paths_conflicts_and_the_durable_pipeline() -> None:
    from aura_core.adapters.memory_stores import InMemoryGraphStore
    from aura_core.knowledge import RuleEntityExtractor
    from aura_core.knowledge.graph_pipeline import KnowledgeGraphPipeline
    from aura_core.tools.open.knowledge_graph_manager import KnowledgeGraphManager as K

    k = K()
    k.add_relation("Sarah", "works_at", "Acme")
    k.add_relation("Acme", "located_in", "Lisbon")
    k.add_relation("Sarah", "lives_in", "Porto")
    k.add_relation("Sarah", "lives_in", "Lisbon")
    assert k.stats() == {"triples_count": 4, "unique_nodes_count": 4}
    assert k.get_neighbors("Acme")["incoming"] == ["Sarah"]
    hops = [[h["relation"] for h in path] for path in k.find_paths_between_entities("Sarah", "Lisbon")]
    assert ["works_at", "located_in"] in hops and ["lives_in"] in hops
    assert "lives_in" in json.dumps(k.detect_conflicting_facts(["lives_in"])), "a single-valued relation with two values"
    assert k.export_to_mermaid().lower().startswith("graph")

    graph = InMemoryGraphStore()
    pipeline = KnowledgeGraphPipeline(graph=graph, extractor=RuleEntityExtractor())
    report = await pipeline.ingest(document_id="org", version=1,
                                   text="Sarah Chen works at Acme Corp. Acme Corp is located in Lisbon District.", owner_id=ALICE)
    assert report.relations_accepted == 2
    relations = await graph.list_relations(owner_id=ALICE)
    assert {r.predicate for r in relations} == {"works_at", "located_in"} and all(r.sources == ("org@v1",) for r in relations)
    assert await graph.list_relations(owner_id=UserId("bob")) == [], "the graph is per owner"


# ------------------------------------------------------------------------------- rows 136-140: eval tools


async def test_rows136_to_140_eval_tools_through_a_real_registry_and_the_run_evaluator(container) -> None:
    from aura_core.adapters.runtime import FrozenClock
    from aura_core.evaluation.agent_runs import AgentRunEvaluator, build_eval_tools, load_agent_run_scenarios
    from aura_core.tools.registry import ToolRegistry
    from evaluation import gate as eval_gate

    writes: list[str] = []
    clock = FrozenClock()
    registry = ToolRegistry()
    registry.register_all(build_eval_tools(clock=clock, writes=writes))
    lookup, _ = await call(registry, "eval_lookup", key="k1")
    assert lookup.ok and lookup.content == "value:k1"
    good, _ = await call(registry, "eval_check", value="fine")
    bad, _ = await call(registry, "eval_check", value="bad-thing")
    assert good.content == "ok:fine" and not bad.ok and "check failed" in bad.content
    note, _ = await call(registry, "eval_note", note="n1")
    assert note.ok and writes == ["note:n1"]
    held, report = await call(registry, "eval_publish", note="p1")
    assert not held.ok and report.needs_confirmation and writes == ["note:n1"], "publish never acts without confirmation"
    done, _ = await call(registry, "eval_publish", note="p1", confirmed=True)
    assert done.ok and writes == ["note:n1", "publish:p1"]
    before = clock.now()
    slow, _ = await call(registry, "eval_slow", seconds=30)
    assert slow.ok and (clock.now() - before).total_seconds() == 30

    await container.startup()
    evaluator = AgentRunEvaluator.for_container(container, clock=FrozenClock())
    suite = await evaluator.run(load_agent_run_scenarios(eval_gate.SUITE_DIR / "agent_runs.jsonl"))
    assert len(suite.results) == 12 and not suite.failures(), [(r.case_id, r.detail) for r in suite.failures()]


# --------------------------------------------------------------------- row 65: live progress streaming


async def test_row65_kernel_stream_emits_step_frames_then_tokens_then_done(container) -> None:
    from aura_core.adapters.coding_fakes import InMemorySandboxRunner
    from aura_core.tools.coding import build_sandbox_tools

    await container.startup()
    runner = InMemorySandboxRunner()
    for tool in build_sandbox_tools(runner):
        container.tools.register(tool, overwrite=True)
    container.kernel._deps.planner = ScriptedPlanner("write_file", {"path": "note.txt", "content": "hello"})
    frames = [f async for f in container.kernel.stream(TurnRequest(message="write a note", user_id=UserId("stream-sim"), capabilities=ENGINEER))]
    kinds = [f["type"] for f in frames]
    assert kinds[-1] == "done" and "error" not in kinds
    steps = [f for f in frames if f["type"] == "step"]
    assert [(f["step_id"], f["status"]) for f in steps] == [("s1", "running"), ("s1", "succeeded")], steps
    assert steps[1]["elapsed_ms"] > 0 and steps[0]["description"] == "scripted step"
    assert kinds.index("step") < kinds.index("token") < kinds.index("done")
    (root,) = runner._scratch_dirs.values()
    assert (root / "note.txt").read_text() == "hello", "the step really ran while it was reported"


# ------------------------------------------- rows 58, 67, 86: paired desktop through two relay workers


class _Daemon:
    """A scripted ``aura_hostd``: records every RPC (method + params) and answers like the real one."""

    def __init__(self, user: UserId) -> None:
        from aura_core.adapters.host_agent import HostAgentSession

        self.calls: list[tuple[str, dict]] = []
        self.files: dict[str, str] = {}
        self.session = HostAgentSession(user, self._send)

    async def _send(self, frame: dict[str, Any]) -> None:
        method, call_id, params = frame["method"], frame["id"], frame["params"]
        self.calls.append((method, dict(params)))
        if method == "create_task":
            result: dict[str, Any] = {"task_id": f"task-{len(self.calls)}"}
        elif method == "write_file":
            self.files[params["path"]] = params["content"]
            result = {}
        elif method == "run":
            self.session.resolve({"type": "stream", "id": call_id, "stream": "stdout", "data": "built\n"})
            result = {"stdout": "built\n", "stderr": "", "exit_code": 0}
        else:
            result = {}
        self.session.resolve({"type": "result", "id": call_id, "result": result})


async def test_rows58_67_86_a_remote_phone_turn_reaches_the_desktop_only_when_approved_through_the_relay() -> None:
    from aura_core.adapters.coding_fakes import InMemorySandboxRunner
    from aura_core.adapters.host_agent import CompositeSandboxRunner, HostAgentRegistry, HostAgentSandboxRunner
    from aura_core.adapters.host_agent_relay import HostAgentRelay, InMemoryRelayBus
    from aura_core.tools.coding import build_sandbox_tools
    from aura_core.tools.policy import ToolPolicy
    from aura_core.tools.registry import ToolRegistry

    secret = "shared-jwt-secret-built-for-test"
    bus = InMemoryRelayBus()
    worker_a = HostAgentRegistry(relay=HostAgentRelay(bus, secret, worker_id="A"))
    worker_b = HostAgentRegistry(relay=HostAgentRelay(bus, secret, worker_id="B"))
    daemon = _Daemon(ALICE)
    await worker_a.register(daemon.session)  # the daemon's socket terminates on worker A

    async def host_routed(ctx) -> bool:
        return await worker_b.resolve(ctx.user_id) is not None

    runner = CompositeSandboxRunner(host_runner=HostAgentSandboxRunner(worker_b), fallback=InMemorySandboxRunner())
    registry = ToolRegistry(policy=ToolPolicy(), host_routed=host_routed)  # worker B serves the turn
    registry.register_all(build_sandbox_tools(runner))
    try:
        desktop_ctx = context("desk-1")
        wrote, _ = await call(registry, "write_file", path="a.txt", content="from desktop", ctx=desktop_ctx)
        assert wrote.ok, wrote.content
        assert daemon.files == {"a.txt": "from desktop"}, "worker B reached worker A's daemon through the signed relay"

        phone = context("phone-1")
        phone.metadata["origin"] = "remote"
        held, report = await call(registry, "write_file", path="b.txt", content="from phone", ctx=phone)
        assert not held.ok and report.needs_confirmation
        assert "side_effect_on_user_machine_from_remote_device" in (report.policy_reason or held.content)
        assert "b.txt" not in daemon.files, "nothing reached the machine before approval"
        approved, _ = await call(registry, "write_file", path="b.txt", content="from phone", ctx=phone, confirmed=True)
        assert approved.ok and daemon.files["b.txt"] == "from phone"
        creates = [p for m, p in daemon.calls if m == "create_task"]
        assert creates[-1].get("origin") == "remote", "the daemon is told the turn came from a remote device"

        isolated = context("iso-1")
        isolated.metadata.update({"desktop_task_isolation": True, "session_id": "chat-42"})
        ran, _ = await call(registry, "run_command", command="make", ctx=isolated, confirmed=True)
        assert ran.ok and "built" in ran.content
        iso_create = [p for m, p in daemon.calls if m == "create_task"][-1]
        assert iso_create["isolate"] is True and iso_create["task_key"] == "chat-42", "a per-task git worktree is requested"

        # A message signed with the wrong key (anyone who can write to the bus) never reaches the daemon.
        before = len(daemon.calls)
        forger = HostAgentRelay(bus, "some-other-secret", worker_id="X")
        await forger._publish("A", {"op": "call", "call_id": "forged-1", "user_id": ALICE.value, "method": "run",
                                    "params": {"command": ["rm", "-rf", "/"]}, "timeout_s": 5, "stream": False})
        await asyncio.sleep(0.1)
        assert len(daemon.calls) == before, "a forged relay message is dropped"
    finally:
        await worker_a.relay.close()
        await worker_b.relay.close()


@pytest.mark.xfail(strict=True, reason="design limit: a step frame carries only status and elapsed time; there is no live command output "
                   "or per-tool progress inside a running step (run_command output is drained into the result)")
async def test_row65_a_running_step_streams_live_detail(container) -> None:
    from aura_core.adapters.coding_fakes import InMemorySandboxRunner
    from aura_core.tools.coding import build_sandbox_tools

    await container.startup()
    for tool in build_sandbox_tools(InMemorySandboxRunner()):
        container.tools.register(tool, overwrite=True)
    container.kernel._deps.planner = ScriptedPlanner("run_command", {"command": "echo line1; sleep 0.2; echo line2"})
    frames = [f async for f in container.kernel.stream(TurnRequest(message="run it", user_id=UserId("stream-sim2"), capabilities=ENGINEER))]
    running = [f for f in frames if f["type"] == "step" and f["status"] == "running"]
    assert any(f["detail"] for f in running) or sum(1 for f in frames if f["type"] == "step") > 2
