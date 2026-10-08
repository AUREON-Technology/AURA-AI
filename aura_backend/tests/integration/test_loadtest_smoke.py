"""The load harness driving the real v3 app, in-process, so it cannot silently rot.

Real ``build_app``, real kernel/retrieval/safety/planner on the in-memory container and echo
provider, over ``httpx.ASGITransport`` -- tiny concurrency (1 and 3 users, 1-2 iterations each).
It proves the harness still speaks the gateway's actual routes, payloads and SSE frames and that
every report section renders from a real run. It measures nothing about capacity, and asserts
nothing about latency.

The voice engines are replaced by deterministic doubles (the in-memory stack has no Piper, and
a real Whisper forward pass does not belong in the default suite); what is under test is the
harness's three-call voice flow, not speech recognition.
"""

from __future__ import annotations

import json

import pytest

from scripts.loadtest.cli import write_outputs
from scripts.loadtest.config import RunConfig
from scripts.loadtest.inprocess import inprocess_target
from scripts.loadtest.report import render_markdown
from scripts.loadtest.runner import Runner
from scripts.loadtest.stats import NOT_EVALUATED


class _FixedTranscriptSTT:
    name = "test-stt"

    async def transcribe(self, audio: bytes, language: str | None = None) -> str:
        assert audio[:4] == b"RIFF"
        return "How many days of annual leave do I get?"


class _TinyWavTTS:
    name = "test-tts"

    async def synthesize(self, text: str, voice: str | None = None) -> bytes:
        return b"RIFF" + b"\x00" * 40


def _config(**kw) -> RunConfig:
    base = dict(
        base_url="http://aura-inprocess", matrix=(1, 3), iterations_per_vu=2, ramp_s=0.0,
        think_min_s=0.0, think_max_s=0.0, cooldown_s=0.0, max_output_tokens=64,
        request_timeout_s=30.0, api_timeout_s=10.0, metrics_url="/metrics",
        metrics_interval_s=0.05, label="smoke",
    )
    base.update(kw)
    return RunConfig(**base)


async def _run(container, cfg: RunConfig) -> dict:
    async with inprocess_target(container) as target:
        container.stt_engine = _FixedTranscriptSTT()
        container.tts_engine = _TinyWavTTS()
        runner = Runner(cfg, tokens=target.tokens, transport=target.transport, in_process=True,
                        log=lambda _msg: None)
        return await runner.run()


async def test_every_scenario_runs_against_the_real_app(container, tmp_path) -> None:
    cfg = _config(scenarios=("chat", "long_context", "agent", "voice", "api", "engineer", "mixed"))
    results = await _run(container, cfg)

    runs = {r["run"]: r for r in results["runs"]}
    # Engineer skips cleanly, with its reason, rather than erroring or being silently absent.
    assert runs["engineer"]["skipped"] == "no --engineer-repo given"
    assert results["preflight"]["probe_provider"] == "echo"
    assert results["setup"]["identities"] == 3 and results["setup"]["failed"] == 0

    for name in ("chat", "long_context", "agent", "voice", "api", "mixed"):
        steps = runs[name]["steps"]
        assert [s["concurrency"] for s in steps] == [1, 3]
        for step in steps:
            summary = step["summary"]
            assert summary["iterations"] == step["concurrency"] * 2, name
            assert summary["error_rate"] == 0.0, (name, summary["top_error_details"])
            assert step["harness_error_count"] == 0, step["harness_errors"]
            # /metrics was scraped in-process, and the queue verdict is honest about the gauge.
            assert step["metrics"]["reachable"] is True
            queue = next(t for t in step["thresholds"] if t["name"] == "Bounded queue")
            assert queue["verdict"] == NOT_EVALUATED

    chat = runs["chat"]["steps"][0]
    assert chat["summary"]["ttft_ms"]["n"] == 2
    assert {t["name"]: t["verdict"] for t in chat["thresholds"]}["TTFT p95"] == "PASS"
    voice_ops = runs["voice"]["steps"][1]["by_op"]
    assert {"voice/transcribe", "voice/chat_stream", "voice/speak"} <= set(voice_ops)
    assert runs["api"]["steps"][1]["summary"]["requests"] == 6

    # The report renders from the JSON alone, and says what kind of run this was.
    json_path, md_path = write_outputs(results, str(tmp_path))
    reloaded = json.loads(open(json_path, encoding="utf-8").read())
    markdown = render_markdown(reloaded)
    assert markdown == open(md_path, encoding="utf-8").read()
    assert "NOT a capacity measurement" in markdown
    assert "## Launch thresholds" in markdown and "## Saturation" in markdown


async def test_rejected_auth_aborts_before_any_load(container) -> None:
    from scripts.loadtest.auth import TokenSource
    from scripts.loadtest.runner import HarnessAbort

    async with inprocess_target(container) as target:
        wrong = TokenSource.minted(secret="not-the-servers-secret-0123456789")
        runner = Runner(_config(scenarios=("chat",)), tokens=wrong, transport=target.transport,
                        in_process=True, log=lambda _msg: None)
        with pytest.raises(HarnessAbort, match="401"):
            await runner.run()
