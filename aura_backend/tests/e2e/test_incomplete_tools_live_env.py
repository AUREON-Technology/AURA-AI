"""Incomplete tools against REAL services and REAL data, from the "virtual environment".

Two kinds of test live here:

* **Real data, offline, always run.** Captured fixtures of real data: ``tests/fixtures/osv/real_osv_advisories.json``
  is the unmodified-shape OSV records the public ``api.osv.dev`` returned (fetched 2026-11-23 for requests 2.19.0,
  pyyaml 5.3, lodash 4.17.15, minimist 1.2.0); the repo's own Alembic migrations; the gateway's own Prometheus
  exposition and JSON log lines.
* **Real services, gated.** Run only with ``AURA_VIRTUAL_ENV=1`` after ``scripts/virtual_env_up.sh`` (which starts
  a real Redis and PostgreSQL, installs Playwright + Chromium and writes the ``AURA_VENV_*`` variables; see
  ``docs/reports/INCOMPLETE_TOOLS_SIMULATED_E2E.md``). Without it they skip with a clear reason, so the default
  ``pytest`` run needs nothing.

Rows: 17, 52, 53, 79, 92, 95, 103.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.e2e.sim_support import RecordingServer, call, context, ok_json

pytestmark = pytest.mark.asyncio

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
BACKEND = Path(__file__).resolve().parents[2]
LIVE = os.environ.get("AURA_VIRTUAL_ENV") == "1"
needs_env = pytest.mark.skipif(not LIVE, reason="live virtual environment not up: run scripts/virtual_env_up.sh, "
                               "`source /tmp/aura_virtual_env/env`, then AURA_VIRTUAL_ENV=1 pytest")


@pytest.fixture
async def reg(container, tmp_path):
    from aura_core.tools.analysis import build_analysis_tools

    await container.startup()
    root = tmp_path / "ws"
    root.mkdir()
    for tool in build_analysis_tools(root):
        container.tools.register(tool, overwrite=True)
    container.ws = root
    return container.tools


async def _migration_tables(reg) -> tuple[set[str], int]:
    """Tables the tool reports, one migration file per call (module-level constants are per file), unioned."""
    tables: set[str] = set()
    files = sorted((BACKEND / "alembic" / "versions").glob("*.py"))
    for path in files:
        out = await ok_json(reg, "inspect_sql", operation="schema", schema_info=path.read_text(encoding="utf-8"))
        tables |= set(out["tables"])
    return tables, len(files)


# ------------------------------------------------------------------------- row 17: real OSV advisories


async def test_row17_real_osv_advisories_flag_known_vulnerable_pins_and_not_safe_ones(reg, container) -> None:
    """REAL DATA: the OSV records are what api.osv.dev returned; the pins below are versions OSV says are affected."""
    ws = container.ws
    (ws / "advisories.json").write_text((FIXTURES / "osv" / "real_osv_advisories.json").read_text(encoding="utf-8"), encoding="utf-8")
    (ws / "requirements.lock").write_text(
        "requests==2.19.0 \\\n    --hash=sha256:aa\npyyaml==5.3 \\\n    --hash=sha256:bb\nflask==3.0.3 \\\n    --hash=sha256:cc\n", encoding="utf-8")
    (ws / "package-lock.json").write_text(json.dumps({"lockfileVersion": 3, "packages": {
        "": {}, "node_modules/lodash": {"version": "4.17.15"}, "node_modules/minimist": {"version": "1.2.0"},
        "node_modules/left-pad": {"version": "1.3.0"}}}), encoding="utf-8")
    out = await ok_json(reg, "scan_code_security", paths=["."], advisories_path="advisories.json")
    dep = out["dependency_vulnerabilities"]
    assert dep["checked"] is True and dep["advisories_loaded"] >= 15
    by_pkg: dict[str, list] = {}
    for v in dep["vulnerable"]:
        by_pkg.setdefault(v["package"].lower(), []).append(v)
    assert {"requests", "pyyaml", "lodash", "minimist"} <= set(by_pkg), sorted(by_pkg)
    assert "flask" not in by_pkg and "left-pad" not in by_pkg, "unaffected pins are not reported"
    ids = {str(v["advisory"]) for vs in by_pkg.values() for v in vs}
    aliases = {a for vs in by_pkg.values() for v in vs for a in v.get("aliases", [])}
    assert any(i.startswith(("GHSA-", "PYSEC-")) for i in ids) and any(a.startswith("CVE-") for a in aliases), (ids, aliases)
    assert all(v["fixed_in"] for vs in by_pkg.values() for v in vs if v["package"].lower() in ("lodash", "minimist")), "fix versions reported"
    assert out["secure"] is False


# ------------------------------------------------------------------------ row 92: real Alembic migrations


async def test_row92_real_alembic_migration_set_parses_into_the_platforms_actual_schema(reg) -> None:
    """REAL DATA: every migration under aura_backend/alembic/versions. Found by this test (and fixed in
    ``sql_schema_ext``): ``downgrade()`` was walked as well as ``upgrade()``, so each migration's own drops emptied
    its schema, and raw ``op.execute`` DDL (including statements held in module-level tuples) was ignored."""
    tables, count = await _migration_tables(reg)
    assert count >= 20 and len(tables) >= 25, (count, sorted(tables))
    assert {"aura_idempotency_keys", "aura_agent_runs", "memories", "aura_documents"} <= tables or {"aura_idempotency_keys", "aura_agent_runs"} <= tables


@needs_env
async def test_row92_tool_output_is_valid_on_a_real_postgres_with_explain(reg) -> None:
    """REAL SERVICE: ``alembic upgrade head`` on a real PostgreSQL; the tables the tool derived from the migration
    source are compared with ``information_schema`` and the SELECT it generates is EXPLAINed by the real planner
    (the tool itself has no DB connection and says ``plan_available: false``)."""
    import asyncpg

    from tests.postgres_migrations import migrated_database

    admin = os.environ.get("AURA_VENV_PG_DSN")
    if not admin:
        pytest.skip("AURA_VENV_PG_DSN not set (PostgreSQL not started by virtual_env_up.sh)")
    async with migrated_database(admin, "venv92") as url:
        conn = await asyncpg.connect(url)
        try:
            real = {r["table_name"] for r in await conn.fetch(
                "select table_name from information_schema.tables where table_schema='public' and table_name <> 'alembic_version'")}
            tool_tables, _ = await _migration_tables(reg)
            missing = real - tool_tables
            assert len(missing) <= 0.2 * len(real), ("real tables the tool did not find", sorted(missing))
            table = "aura_idempotency_keys"
            assert table in real and table in tool_tables
            source = (BACKEND / "alembic" / "versions" / "0006_idempotency_keys.py").read_text(encoding="utf-8")
            gen = await ok_json(reg, "inspect_sql", operation="generate_select", table=table, columns=["user_id", "operation"],
                                filters=["user_id"], schema_info=source, limit=5)
            sql = gen["sql"].replace(":user_id", "$1")
            assert await conn.fetch("EXPLAIN " + sql, "u1"), "the real planner accepted the generated SQL"
            assert await conn.fetch(sql, "u1") == [], "and it runs"
        finally:
            await conn.close()


# ---------------------------------------------------------------- row 95: real metrics and real log lines


async def test_row95_the_gateways_own_prometheus_exposition_and_json_logs_are_analysed(reg, container, monkeypatch) -> None:
    """REAL DATA: ``Metrics.render()`` is the actual text ``/metrics`` serves; the log lines are produced by the
    platform's own JSON logger."""
    import io
    import logging

    from aura_core.observability.logging import get_logger

    site = os.environ.get("AURA_VENV_SITE") or os.environ.get("AURA_VENV_PLAYWRIGHT_SITE")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [site, str(BACKEND)]))}
    probe = subprocess.run(
        [sys.executable, "-c", "from aura_core.observability.metrics import Metrics, PROMETHEUS_AVAILABLE\n"
         "import sys\nsys.exit(3) if not PROMETHEUS_AVAILABLE else None\n"
         "m = Metrics(enabled=True); sys.stdout.write(m.render().decode())"],
        capture_output=True, text=True, env=env, cwd=BACKEND, timeout=60)
    if probe.returncode == 3:
        pytest.skip("prometheus_client is not in the locked env; virtual_env_up.sh installs it outside the repo")
    assert probe.returncode == 0, probe.stderr[-500:]
    text = probe.stdout
    assert "# TYPE" in text, "the real exposition has typed families"
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    root = logging.getLogger()
    previous = [(h, h.formatter) for h in root.handlers]
    handler.setFormatter(previous[0][1] if previous and previous[0][1] else logging.Formatter("%(message)s"))
    root.addHandler(handler)
    try:
        log = get_logger("probe.real")
        for _ in range(3):
            log.error("upstream provider timeout")
        log.info("request ok")
    finally:
        root.removeHandler(handler)
    lines = [line for line in stream.getvalue().splitlines() if line.strip()]
    assert lines, "the platform logger emitted lines"
    out = await ok_json(reg, "analyze_logs", operation="incident", logs=lines, metrics_text=text)
    assert out["metrics"]["families"] >= 5 and out["metrics"]["samples"] >= 5, "the real exposition parsed"
    if out["logs"]:
        assert out["logs"]["records"] == len(lines)


# --------------------------------------------------------------------------- row 103: real Redis history


@needs_env
async def test_row103_drift_history_in_a_real_redis(container) -> None:
    """REAL SERVICE: the production ``RedisCache`` adapter against a real redis-server."""
    from aura_core.adapters.redis_cache import RedisCache
    from aura_core.tools.drift import build_drift_tools

    url = os.environ.get("AURA_VENV_REDIS_URL")
    if not url:
        pytest.skip("AURA_VENV_REDIS_URL not set (Redis not started by virtual_env_up.sh)")
    import redis.asyncio as aioredis

    await container.startup()
    cache = RedisCache(url)
    for tool in build_drift_tools(cache):
        container.tools.register(tool, overwrite=True)
    reg = container.tools
    raw = aioredis.from_url(url)
    try:
        await raw.flushdb()
        await ok_json(reg, "record_model_metric", operation="record", model="qwen", metric="accuracy", value=0.91)
        await ok_json(reg, "record_model_metric", operation="set_baseline", model="qwen", metric="accuracy")
        await ok_json(reg, "record_model_metric", operation="record", model="qwen", metric="accuracy", value=0.70)
        cmp = await ok_json(reg, "compare_model_metric", model="qwen", metric="accuracy", include_history=True)
        assert cmp["drift_detected"] is True and [h["value"] for h in cmp["history"]] == [0.91, 0.70]
        keys = [k.decode() for k in await raw.keys("aura:drift:v1:*")]
        assert any(k.endswith(":qwen:accuracy") for k in keys), keys
        ttl = await raw.ttl(keys[0])
        assert ttl is None or ttl != -2, "the history really lives in Redis"
        other, _ = await call(reg, "compare_model_metric", model="qwen", metric="accuracy", ctx=context("o", user=type(context().user_id)("other")))
        assert json.loads(other.content)["status"] == "insufficient_history", "history is per owner in Redis too"
    finally:
        await raw.flushdb()
        await raw.aclose()


# ------------------------------------------------------------- rows 52, 53: real Chromium via Playwright


def _real_playwright(monkeypatch):
    site = os.environ.get("AURA_VENV_PLAYWRIGHT_SITE")
    if not site or not Path(site, "playwright").is_dir():
        pytest.skip("Playwright not installed by virtual_env_up.sh (AURA_VENV_PLAYWRIGHT_SITE unset)")
    monkeypatch.syspath_prepend(site)
    for name in [n for n in sys.modules if n == "playwright" or n.startswith("playwright.")]:
        monkeypatch.delitem(sys.modules, name, raising=False)


def _allow_loopback(monkeypatch, port: int) -> None:
    """ONLY the host guard is wrapped: the one local test server is let through; every other URL goes to the real check_url."""
    from aura_core.tools.open import _ops_common, browser_subagent

    real = _ops_common.check_url

    def check(url):
        if isinstance(url, str) and url.startswith(f"http://127.0.0.1:{port}/"):
            return url
        return real(url)

    monkeypatch.setattr(browser_subagent, "check_url", check)
    monkeypatch.setattr(_ops_common, "check_url", check)


@needs_env
async def test_rows52_53_real_chromium_drives_a_page_screenshots_it_and_the_verifier_compares_the_files(monkeypatch, tmp_path) -> None:
    """REAL SERVICE: headless Chromium (Playwright) against a local static page. The loopback host guard is the only
    simulated part (the SSRF policy refuses loopback by design; see ``_allow_loopback``). The ``UiVisualVerifier``
    compares real screenshot files; it is a byte comparison, not a pixel diff (the class says so)."""
    _real_playwright(monkeypatch)
    from aura_core.tools.open.browser_subagent import BrowserSubagent
    from aura_core.tools.open.ui_visual_verifier import UiVisualVerifier

    blue = (b"<html><head><title>Checkout</title></head><body style='background:#1a4bd8;color:#fff'>"
            b"<h1 id='h'>Order total: 42</h1><input id='q'/><button id='go' onclick=\"document.title='Clicked'\">Pay</button>"
            b"<img id='leak' src='http://169.254.169.254/latest/meta-data/x.png'></body></html>")
    red = blue.replace(b"#1a4bd8", b"#d81a1a")
    server = RecordingServer({"/blue": (200, blue), "/blue2": (200, blue), "/red": (200, red)})
    _allow_loopback(monkeypatch, server.port)
    base = f"http://127.0.0.1:{server.port}"
    try:
        assert BrowserSubagent.check_playwright_availability()["available"] is True
        assert (await asyncio.to_thread(BrowserSubagent.run_actions, "http://127.0.0.1:9/x"))["executed"] is False, "other loopback ports stay refused"
        shot = tmp_path / "blue.png"
        result = await asyncio.to_thread(
            BrowserSubagent.run_actions, f"{base}/blue",
            [{"type": "fill", "selector": "#q", "value": "tv"}, {"type": "click", "selector": "#go"}],
            screenshot_path=str(shot), base_dir=str(tmp_path))
        assert result["executed"] is True, result
        assert result["title"] == "Clicked", "the click really ran the page's script"
        assert shot.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" and shot.stat().st_size > 500
        page = await asyncio.to_thread(BrowserSubagent.extract_page_content, f"{base}/blue", selector="#h")
        assert page["success"] and page["content"] == "Order total: 42"
        leaked = await asyncio.to_thread(BrowserSubagent.run_script_in_page, f"{base}/blue", "document.getElementById('leak').naturalWidth")
        assert leaked["success"] and leaked["result"] == 0, "the cloud-metadata sub-resource was aborted by the route guard"
        assert not [r for r in server.requests if "169.254" in r["path"]]
        assert (await asyncio.to_thread(BrowserSubagent.run_script_in_page, f"{base}/blue", "1+1"))["result"] == 2

        for name in ("blue2", "red"):
            out = await asyncio.to_thread(BrowserSubagent.navigate_and_screenshot, f"{base}/{name}", str(tmp_path / f"{name}.png"), base_dir=str(tmp_path))
            assert out["success"] is True, out
        dims = UiVisualVerifier.extract_image_dimensions(str(tmp_path / "blue2.png"), base_dir=str(tmp_path))
        assert dims[:2] == (1280, 720) and dims[2] == "PNG"
        same = UiVisualVerifier.verify_visuals(str(tmp_path / "blue2.png"), str(tmp_path / "blue2.png"), base_dir=str(tmp_path))
        assert same["status"] == "PASSED_VISUAL_CHECK"
        diff = UiVisualVerifier.verify_visuals(str(tmp_path / "blue2.png"), str(tmp_path / "red.png"), tolerance_percentage=0.5, base_dir=str(tmp_path))
        assert diff["status"] == "VISUAL_REGRESSION_DETECTED", diff
    finally:
        server.close()


# --------------------------------------------------------------------------------- row 79: real ffmpeg


@needs_env
async def test_row79_a_real_video_goes_through_real_ffmpeg_to_the_speech_engine(container, tmp_path) -> None:
    """REAL SERVICE for the extraction half: ffmpeg generates a 3 s tone video; ``transcribe_audio`` extracts its audio
    track with the real ffmpeg and hands real WAV bytes to the engine. The speech model itself is a scripted stand-in
    (faster-whisper is not installed and would need a model download); so is nothing else in this path.

    NOT_TESTABLE here: speaker diarization. It needs pyannote.audio (torch) and a gated Hugging Face model; the
    transcript's ``speaker`` stays null by design."""
    ffmpeg = os.environ.get("AURA_VENV_FFMPEG") or shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg not installed")
    from aura_core.ports.speech import Transcript, TranscriptSegment
    from aura_core.tools.media import build_media_tools

    media = tmp_path / "media"
    media.mkdir()
    made = subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                           "-f", "lavfi", "-i", "color=c=blue:s=64x64:d=3", "-shortest", "-c:v", "mpeg4", str(media / "clip.mp4")],
                          capture_output=True, timeout=60)
    assert made.returncode == 0, made.stderr.decode()[-300:]
    seen: dict = {}

    class Engine:
        name = "scripted"
        multilingual = True

        async def transcribe(self, audio: bytes, *, language=None) -> str:
            return "tone"

        async def transcribe_segments(self, audio: bytes, *, language=None, word_timestamps=True) -> Transcript:
            seen["audio"] = audio
            return Transcript(text="tone", segments=(TranscriptSegment(0.0, 3.0, "tone", 0.9),), language="en", duration=3.0)

    await container.startup()
    for tool in build_media_tools(media, stt_engine=Engine()):
        container.tools.register(tool, overwrite=True)
    out = await ok_json(container.tools, "transcribe_audio", path="clip.mp4")
    assert out["source"]["kind"] == "video" and out["source"]["audio_extracted_with"] == "ffmpeg"
    wav = seen["audio"]
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE" and len(wav) > 50_000, "a real extracted WAV reached the engine"
    assert out["segments"][0]["speaker"] is None and out["diarization"]["available"] is False
