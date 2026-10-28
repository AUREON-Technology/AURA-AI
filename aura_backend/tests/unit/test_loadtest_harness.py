"""The Phase 3 load harness's own logic (``scripts/loadtest``), in the default suite.

Everything a capacity report prints -- percentiles, error classes, threshold verdicts, the
saturation knee, the Markdown itself -- is computed by pure functions here, so it is pinned
against hand-computed values rather than trusted. No network, no model: the HTTP layer is driven
through ``httpx.MockTransport``. The in-process end-to-end run lives in
``tests/integration/test_loadtest_smoke.py``.
"""

from __future__ import annotations

import json
import random
import wave
import io

import httpx
import pytest

from scripts.loadtest import prom
from scripts.loadtest.auth import AuthError, TokenSource
from scripts.loadtest.client import VU, Gateway, error_detail
from scripts.loadtest.cli import build_parser, config_from_args
from scripts.loadtest.config import RunConfig, Thresholds, parse_matrix, parse_mix, parse_range
from scripts.loadtest.redact import redact_url
from scripts.loadtest.report import render_markdown
from scripts.loadtest.scenarios import Preflight, long_context_message, scenario_availability
from scripts.loadtest.sse import SseParser
from scripts.loadtest.stats import (
    ERR_4XX,
    ERR_5XX,
    ERR_429,
    ERR_503,
    ERR_BLOCKED,
    ERR_CONNECTION,
    ERR_TIMEOUT,
    ERROR_KIND_STATUS,
    FAIL,
    NO_DATA,
    NOT_EVALUATED,
    PASS,
    Iteration,
    Sample,
    classify_status,
    classify_stream_error,
    detect_knee,
    distribution,
    evaluate_thresholds,
    percentile,
    queue_verdict,
    summarize,
)
from scripts.loadtest.wav import synth_wav


def _sample(**kw) -> Sample:
    base = dict(scenario="chat", op="chat_stream", kind="llm", vu=0, started_s=1.0, e2e_ms=100.0,
                status=200)
    base.update(kw)
    return Sample(**base)


def _iter(**kw) -> Iteration:
    base = dict(scenario="chat", vu=0, started_s=1.0, e2e_ms=100.0, ok=True, requests=1)
    base.update(kw)
    return Iteration(**base)


class TestPercentile:
    def test_linear_interpolation_matches_hand_computed_values(self) -> None:
        values = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
        assert percentile(values, 50) == pytest.approx(55.0)
        assert percentile(values, 95) == pytest.approx(95.5)
        assert percentile(values, 99) == pytest.approx(99.1)
        assert percentile(values, 0) == 10
        assert percentile(values, 100) == 100

    def test_order_does_not_matter_and_single_value_is_itself(self) -> None:
        assert percentile([3, 1, 2], 50) == 2
        assert percentile([7.5], 99) == 7.5

    def test_empty_is_none_not_zero(self) -> None:
        # A p95 of "no samples" rendered as 0 ms is how a broken scenario reads as a fast one.
        assert percentile([], 95) is None
        assert distribution([])["p95"] is None

    def test_rejects_out_of_range_quantile(self) -> None:
        with pytest.raises(ValueError):
            percentile([1, 2], 101)


class TestClassification:
    @pytest.mark.parametrize(
        ("status", "expected"),
        [(200, None), (204, None), (401, ERR_4XX), (422, ERR_4XX), (429, ERR_429),
         (500, ERR_5XX), (502, ERR_5XX), (503, ERR_503), (504, ERR_5XX), (None, ERR_CONNECTION)],
    )
    def test_status_classes(self, status, expected) -> None:
        assert classify_status(status) == expected

    def test_stream_error_frames(self) -> None:
        assert classify_stream_error("rate_limited") == ERR_429
        assert classify_stream_error("all_providers_failed") == ERR_503
        assert classify_stream_error("timeout") == ERR_5XX
        assert classify_stream_error("internal_error") == ERR_5XX
        assert classify_stream_error("safety_violation") == ERR_BLOCKED
        assert classify_stream_error("something_new") == ERR_5XX

    def test_kind_table_matches_the_domain_errors(self) -> None:
        """The harness keeps a copy of the kind -> status table so it never imports aura_core;
        this pins the copy to the original."""
        from aura_core.domain import errors

        declared = {
            cls.kind: cls.http_status
            for cls in vars(errors).values()
            if isinstance(cls, type) and issubclass(cls, errors.AuraError) and cls is not errors.AuraError
        }
        for kind, status in declared.items():
            assert ERROR_KIND_STATUS.get(kind) == status, kind

    def test_error_detail_prefers_the_gateways_error_slug(self) -> None:
        assert error_detail(b'{"detail": {"error": "rate_limited"}}', 429) == "rate_limited"
        assert error_detail(b'{"error": "dependency_unavailable"}', 503) == "dependency_unavailable"
        assert "edge proxy" in error_detail(b"<html>503 Service Temporarily Unavailable</html>", 503)
        assert error_detail(b"", 500) == "HTTP 500"


class TestSummarize:
    def test_rates_windows_and_errors(self) -> None:
        samples = [
            _sample(e2e_ms=100, ttft_ms=10),
            _sample(e2e_ms=200, ttft_ms=20),
            _sample(e2e_ms=300, status=503, error=ERR_503, detail="all_providers_failed"),
            _sample(e2e_ms=400, status=429, error=ERR_429),
            _sample(e2e_ms=999, steady=False),  # ramp-up: excluded
        ]
        iters = [_iter(e2e_ms=100), _iter(e2e_ms=200), _iter(e2e_ms=300, ok=False),
                 _iter(e2e_ms=400, ok=False), _iter(e2e_ms=999, steady=False)]
        s = summarize(samples, iters, window_s=2.0)
        assert s.requests == 4
        assert s.rps == pytest.approx(2.0)
        assert s.rpm == pytest.approx(120.0)
        assert s.goodput_per_s == pytest.approx(1.0)
        assert s.errors[ERR_503] == 1 and s.errors[ERR_429] == 1
        assert s.error_rate == pytest.approx(0.5)
        # 429 is the limiter, not a server failure.
        assert s.server_failure_rate == pytest.approx(0.25)
        assert s.ttft_ms["n"] == 2 and s.ttft_ms["p50"] == pytest.approx(15.0)
        assert s.e2e_ms["max"] == 400
        assert s.top_error_details[0][1] == 1


class TestThresholds:
    def _eval(self, samples, iters, queue=None, **kw):
        return {t.name: t for t in evaluate_thresholds(samples, iters, Thresholds(**kw), queue_series=queue)}

    def test_pass_and_fail_per_threshold(self) -> None:
        samples = [_sample(ttft_ms=1000.0 + i, e2e_ms=5000.0) for i in range(20)]
        samples += [_sample(scenario="api", op="search", kind="api", e2e_ms=100.0 + i) for i in range(20)]
        iters = [_iter(e2e_ms=5000.0) for _ in range(20)]
        r = self._eval(samples, iters, queue=[1, 1, 1, 1, 1, 1])
        assert r["Non-LLM API p95"].verdict == PASS
        assert r["TTFT p95"].verdict == PASS
        assert r["Ordinary response p95"].verdict == PASS
        assert r["5xx rate"].verdict == PASS
        assert r["Bounded queue"].verdict == PASS

        slow = [_sample(ttft_ms=4000.0, e2e_ms=20_000.0) for _ in range(10)]
        r = self._eval(slow, [_iter(e2e_ms=20_000.0) for _ in range(10)])
        assert r["TTFT p95"].verdict == FAIL
        assert r["Ordinary response p95"].verdict == FAIL
        assert r["Non-LLM API p95"].verdict == NO_DATA
        assert r["Bounded queue"].verdict == NOT_EVALUATED

    def test_ttft_and_response_gate_only_scenario_a(self) -> None:
        # A slow agent turn must not fail the interactive-chat budget.
        samples = [_sample(scenario="agent", ttft_ms=60_000.0, e2e_ms=90_000.0)]
        r = self._eval(samples, [_iter(scenario="agent", e2e_ms=90_000.0)])
        assert r["TTFT p95"].verdict == NO_DATA
        assert r["Ordinary response p95"].verdict == NO_DATA

    def test_server_error_rate_counts_timeouts_but_not_429(self) -> None:
        ok = [_sample() for _ in range(98)]
        r = self._eval(ok + [_sample(status=429, error=ERR_429)] * 2, [])
        assert r["5xx rate"].verdict == PASS
        r = self._eval(ok + [_sample(status=None, error=ERR_TIMEOUT)] * 2, [])
        assert r["5xx rate"].verdict == FAIL

    def test_queue_growth_and_ceiling(self) -> None:
        t = Thresholds()
        assert queue_verdict([0, 1, 2, 3, 5, 8, 12, 16, 20], t)[0] == FAIL
        assert queue_verdict([4, 6, 6, 6, 6, 6], t)[0] == PASS
        assert queue_verdict([1, 2], t)[0] == NOT_EVALUATED
        assert queue_verdict([1, 1, 50], Thresholds(max_queue_depth=10))[0] == FAIL


class TestKnee:
    def test_finds_the_last_step_that_still_scaled(self) -> None:
        points = [(1, 1.0, 1000.0), (2, 1.9, 1050.0), (5, 4.5, 1100.0), (10, 4.6, 2200.0), (20, 4.6, 4400.0)]
        knee = detect_knee(points)
        assert knee is not None
        assert knee.knee_concurrency == 5
        assert knee.saturated_at == 10

    def test_no_knee_when_throughput_keeps_scaling(self) -> None:
        assert detect_knee([(1, 1.0, 100.0), (2, 2.0, 110.0), (4, 3.9, 130.0)]) is None

    def test_flat_throughput_with_flat_latency_is_not_saturation(self) -> None:
        # A step that neither helped nor hurt (e.g. think-time bound) is not a knee.
        assert detect_knee([(1, 1.0, 100.0), (2, 1.0, 101.0)]) is None

    def test_step_where_nothing_succeeded_counts_as_saturated(self) -> None:
        knee = detect_knee([(1, 1.0, 100.0), (5, 0.0, None)])
        assert knee is not None and knee.knee_concurrency == 1


class TestSseParser:
    def test_frames_split_at_arbitrary_boundaries(self) -> None:
        stream = (
            'event: token\ndata: {"type": "token", "text": "Hel"}\n\n'
            'event: token\ndata: {"type": "token", "text": "lo"}\n\n'
            ': keep-alive comment\n\n'
            'event: done\r\ndata: {"type": "done", "usage": {"completion_tokens": 2}}\r\n\r\n'
        )
        for size in (1, 3, 7, len(stream)):
            parser = SseParser()
            frames = []
            for i in range(0, len(stream), size):
                frames.extend(parser.feed(stream[i:i + size]))
            frames.extend(parser.flush())
            assert [f["type"] for f in frames] == ["token", "token", "done"]
            assert frames[2]["usage"]["completion_tokens"] == 2

    def test_non_json_data_is_kept_not_dropped(self) -> None:
        frames = list(SseParser().feed("event: token\ndata: not json\n\n"))
        assert frames == [{"type": "token", "raw": "not json"}]

    def test_trailing_frame_without_blank_line(self) -> None:
        parser = SseParser()
        assert list(parser.feed('data: {"type": "done"}')) == []
        assert [f["type"] for f in parser.flush()] == ["done"]


class TestPrometheus:
    TEXT = """# HELP aura_llm_queue_depth Requests waiting.
# TYPE aura_llm_queue_depth gauge
# TYPE aura_llm_inflight gauge
aura_llm_inflight{provider="local"} 3.0
aura_llm_inflight{provider="gpu"} 1.0
# TYPE aura_llm_ttft_seconds histogram
aura_llm_ttft_seconds_sum{model="m",provider="local"} 4.0
aura_llm_ttft_seconds_count{model="m",provider="local"} 8.0
aura_llm_requests_total{model="m",outcome="success",provider="local"} 10.0
aura_llm_requests_total{model="m",outcome="error",provider="local"} 2.0
weird line that is not a metric
"""

    def test_parse_sums_and_declared_families(self) -> None:
        snap = prom.parse(self.TEXT)
        assert prom.total(snap, "aura_llm_inflight") == 4.0
        assert prom.total(snap, "aura_llm_queue_depth") is None
        assert "aura_llm_queue_depth" in prom.declared(self.TEXT)

    def test_step_summary_deltas_and_signal_status(self) -> None:
        before = prom.parse(self.TEXT)
        after_text = self.TEXT.replace("_sum{model=\"m\",provider=\"local\"} 4.0", "_sum{model=\"m\",provider=\"local\"} 6.0")
        after_text = after_text.replace("_count{model=\"m\",provider=\"local\"} 8.0", "_count{model=\"m\",provider=\"local\"} 12.0")
        after_text = after_text.replace('outcome="success",provider="local"} 10.0', 'outcome="success",provider="local"} 15.0')
        m = prom.StepMetrics(reachable=True, before=prom.compact(before),
                             after=prom.compact(prom.parse(after_text)),
                             declared=prom.declared(self.TEXT),
                             series=[(0.0, None, None, 4.0), (1.0, None, None, 6.0)])
        summary = m.summary()
        assert summary["server_ttft_mean_ms"] == pytest.approx(500.0)  # 2.0 s over 4 calls
        assert summary["llm_requests_by_outcome"] == {"success": 5.0}
        assert summary["llm_inflight_max"] == 6.0
        exported = summary["exported"]
        assert exported["LLM queue wait (aura_llm_queue_depth)"] == prom.DECLARED_ONLY
        assert exported["LLM in-flight generations (aura_llm_inflight)"] == prom.EXPORTED
        assert exported["Celery queue depth"] == prom.ABSENT
        assert m.queue_series() is None
        assert "declared" in m.queue_note()

    def test_unreachable_is_not_evaluated_not_zero(self) -> None:
        m = prom.StepMetrics(reachable=False, error="HTTP 404")
        assert m.queue_series() is None
        assert m.summary() == {"reachable": False, "error": "HTTP 404"}


class TestRedaction:
    def test_userinfo_and_credential_params_are_removed(self) -> None:
        url = "https://ops:hunter2@staging.example:8443/base?api_key=abc&token=xyz&q=1"
        out = redact_url(url)
        assert "hunter2" not in out and "ops" not in out
        assert "abc" not in out and "xyz" not in out
        assert out.startswith("https://REDACTED@staging.example:8443/base?")
        assert "q=1" in out

    def test_plain_url_unchanged(self) -> None:
        assert redact_url("http://gateway:8000") == "http://gateway:8000"


class TestAuth:
    def test_minted_token_is_accepted_by_the_real_v2_verifier(self, monkeypatch) -> None:
        """Mint mode must produce exactly what services/jwt_auth verifies -- checked against the
        real verifier and role resolution, not a re-implementation of them."""
        import asyncio

        from fastapi.security import HTTPAuthorizationCredentials

        import services.jwt_auth as jwt_auth
        from services.rbac import get_user_role

        secret = "unit-test-secret-not-real-0123456789"
        monkeypatch.setattr(jwt_auth, "_secret", lambda: secret)
        # Bound to this gateway's iss/aud, so the check does not lean on the legacy grace period.
        monkeypatch.setattr(jwt_auth, "_legacy_tokens_until", lambda: jwt_auth.datetime.min.replace(
            tzinfo=jwt_auth.timezone.utc))
        source = TokenSource.minted(
            secret=secret, role="Developer", issuer=jwt_auth._issuer(), audience=jwt_auth._audience(),
        )
        claims = asyncio.run(jwt_auth.verify_access_token(
            None, HTTPAuthorizationCredentials(scheme="Bearer", credentials=source.token(4)),
        ))
        assert claims["uid"] == "loadtest-vu-005"
        assert get_user_role(claims) == "Developer"

    def test_env_minted_tokens_default_to_the_production_binding(self, monkeypatch) -> None:
        """minted_from_env targets production by default; its iss/aud must be what a production
        gateway derives, or every minted request 401s once the legacy grace period ends."""
        import services.jwt_auth as jwt_auth
        from scripts.loadtest.auth import DEFAULT_TARGET_AUDIENCE, DEFAULT_TARGET_ISSUER

        monkeypatch.setenv("AURA_ENVIRONMENT", "production")
        monkeypatch.setattr(jwt_auth.settings, "jwt_issuer", "")
        monkeypatch.setattr(jwt_auth.settings, "jwt_audience", "")
        monkeypatch.delenv("AURA_JWT_ISSUER", raising=False)
        monkeypatch.delenv("AURA_JWT_AUDIENCE", raising=False)
        assert (DEFAULT_TARGET_ISSUER, DEFAULT_TARGET_AUDIENCE) == (jwt_auth._issuer(), jwt_auth._audience())

        monkeypatch.setenv("LT_TEST_SECRET", "x" * 32)
        monkeypatch.delenv("AURA_LOADTEST_JWT_ISSUER", raising=False)
        monkeypatch.setenv("AURA_LOADTEST_JWT_AUDIENCE", "aura-api-staging")
        source = TokenSource.minted_from_env("LT_TEST_SECRET")
        assert (source.issuer, source.audience) == (DEFAULT_TARGET_ISSUER, "aura-api-staging")

    def test_distinct_identity_per_vu_and_token_reuse(self) -> None:
        source = TokenSource.minted(secret="s" * 32)
        assert source.user_id(0) != source.user_id(1)
        assert source.token(0) == source.token(0)
        assert source.headers(2)["Authorization"].startswith("Bearer ")

    def test_admin_roles_are_refused(self) -> None:
        for role in ("Admin", "SuperAdmin"):
            with pytest.raises(AuthError):
                TokenSource.minted(secret="s" * 32, role=role)

    def test_secret_comes_from_the_named_env_var_only(self, monkeypatch) -> None:
        monkeypatch.delenv("LT_TEST_SECRET", raising=False)
        with pytest.raises(AuthError, match="LT_TEST_SECRET"):
            TokenSource.minted_from_env("LT_TEST_SECRET")
        monkeypatch.setenv("LT_TEST_SECRET", "x" * 32)
        assert TokenSource.minted_from_env("LT_TEST_SECRET").mode == "mint"
        assert "x" * 32 not in TokenSource.minted_from_env("LT_TEST_SECRET").describe()

    def test_tokens_file_round_robin(self, tmp_path) -> None:
        path = tmp_path / "tokens.txt"
        path.write_text("# comment\naaa\nbbb\n")
        source = TokenSource.from_file(str(path))
        assert source.distinct_users == 2
        assert [source.token(i) for i in range(3)] == ["aaa", "bbb", "aaa"]


class TestConfig:
    def test_parsers(self) -> None:
        assert parse_matrix("10, 1,5,5") == (1, 5, 10)
        assert parse_mix("chat=50,agent=15") == {"chat": 50.0, "agent": 15.0}
        assert parse_range("2") == (2.0, 2.0)
        assert parse_range("1-3") == (1.0, 3.0)
        with pytest.raises(ValueError):
            parse_matrix("0,1")

    def test_validation(self) -> None:
        with pytest.raises(ValueError, match="unknown scenario"):
            RunConfig(base_url="http://x", scenarios=("bogus",)).validate()
        with pytest.raises(ValueError):
            RunConfig(base_url="http://x", think_min_s=3, think_max_s=1).validate()
        with pytest.raises(ValueError):
            RunConfig(base_url="http://x", auth_mode="tokens").validate()

    def test_cli_defaults_are_the_phase_3_matrix(self) -> None:
        args = build_parser().parse_args(["run", "--base-url", "http://gw:8000"])
        cfg = config_from_args(args)
        assert cfg.matrix == (1, 2, 5, 10, 20, 30, 40, 50, 75, 100)
        assert cfg.thresholds.ttft_p95_ms == 2500.0

    def test_public_dict_carries_no_secret(self, monkeypatch) -> None:
        cfg = RunConfig(base_url="https://u:pw@h/x", auth_mode="mint", jwt_secret_env="MY_ENV")
        data = json.dumps(cfg.public_dict())
        assert "pw" not in data and "MY_ENV" in data


class TestScenarioAvailability:
    def test_engineer_skips_with_the_precise_reason(self) -> None:
        cfg = RunConfig(base_url="http://x")
        pre = Preflight(agents=[{"id": "engineer", "usable": False}], workspaces_enabled=False)
        assert scenario_availability("engineer", cfg, pre) == (False, "no --engineer-repo given")
        cfg.engineer_repo = "acme/app"
        ok, reason = scenario_availability("engineer", cfg, pre)
        assert not ok and "AURA_ENGINEER_AGENT_ROLES" in reason
        pre.agents = [{"id": "engineer", "usable": True}]
        ok, reason = scenario_availability("engineer", cfg, pre)
        assert not ok and "sandbox" in reason
        pre.workspaces_enabled = True
        assert scenario_availability("engineer", cfg, pre)[0]

    def test_voice_needs_both_engines(self) -> None:
        cfg = RunConfig(base_url="http://x")
        pre = Preflight(health={"voice": {"stt": "faster-whisper-small", "tts": None}})
        ok, reason = scenario_availability("voice", cfg, pre)
        assert not ok and "TTS" in reason
        pre.health = {"voice": {"stt": "w", "tts": "piper"}}
        assert scenario_availability("voice", cfg, pre)[0]

    def test_long_context_message_is_sized_and_capped(self) -> None:
        assert 11_000 < len(long_context_message(12_000)) < 13_500
        assert len(long_context_message(100_000)) < 32_000  # ChatRequest.message cap


class TestGatewayClient:
    """The HTTP layer against scripted responses: retries, TTFT, mid-stream errors."""

    def _gateway(self, handler, **cfg_kw) -> tuple[Gateway, VU]:
        cfg = RunConfig(base_url="http://gw", **cfg_kw)
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://gw")
        vu = VU(index=0, rng=random.Random(0), session_id="s", t0=0.0)
        return Gateway(client, cfg, TokenSource.none()), vu

    async def test_stream_ttft_tokens_and_usage(self) -> None:
        body = (
            'event: citations\ndata: {"type": "citations", "citations": []}\n\n'
            'event: token\ndata: {"type": "token", "text": "Hi"}\n\n'
            'event: done\ndata: {"type": "done", "usage": {"completion_tokens": 7}}\n\n'
        )

        def handler(request):
            assert json.loads(request.content)["message"] == "q"
            return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

        gw, vu = self._gateway(handler)
        sample, result = await gw.chat_stream(vu, scenario="chat", op="chat_stream", payload={"message": "q"})
        assert sample.ok and sample.ttft_ms is not None
        assert sample.completion_tokens == 7
        assert result.text == "Hi" and result.done is not None

    async def test_mid_stream_error_frame_is_classified(self) -> None:
        body = 'event: error\ndata: {"type": "error", "error": "all_providers_failed"}\n\n'
        gw, vu = self._gateway(lambda r: httpx.Response(200, text=body))
        sample, _ = await gw.chat_stream(vu, scenario="chat", op="chat_stream", payload={"message": "q"})
        assert sample.error == ERR_503 and sample.status == 200

    async def test_stream_without_done_is_a_dropped_connection(self) -> None:
        body = 'event: token\ndata: {"type": "token", "text": "par"}\n\n'
        gw, vu = self._gateway(lambda r: httpx.Response(200, text=body))
        sample, _ = await gw.chat_stream(vu, scenario="chat", op="chat_stream", payload={"message": "q"})
        assert sample.error == ERR_CONNECTION

    async def test_429_is_retried_honouring_retry_after_then_succeeds(self) -> None:
        calls = []

        def handler(request):
            calls.append(1)
            if len(calls) == 1:
                return httpx.Response(429, json={"detail": {"error": "rate_limited"}},
                                      headers={"retry-after": "0"})
            return httpx.Response(200, json={"agents": []})

        gw, vu = self._gateway(handler, max_retries=2)
        sample, result = await gw.request(vu, scenario="api", op="agents", kind="api",
                                          method="GET", path="/api/v3/agents")
        assert sample.ok and sample.retries == 1 and result.body == {"agents": []}

    async def test_no_retry_by_default_and_4xx_is_never_retried(self) -> None:
        calls = []

        def handler(request):
            calls.append(1)
            return httpx.Response(401, json={"detail": "Invalid access token."})

        gw, vu = self._gateway(handler, max_retries=3)
        sample, _ = await gw.request(vu, scenario="api", op="agents", kind="api",
                                     method="GET", path="/api/v3/agents")
        assert sample.error == ERR_4XX and len(calls) == 1

    async def test_transport_failure_becomes_a_sample_not_an_exception(self) -> None:
        def handler(request):
            raise httpx.ConnectError("refused")

        gw, vu = self._gateway(handler)
        sample, _ = await gw.request(vu, scenario="api", op="agents", kind="api",
                                     method="GET", path="/api/v3/agents")
        assert sample.error == ERR_CONNECTION and sample.status is None


def test_synthesized_wav_is_a_valid_16khz_mono_fixture() -> None:
    data = synth_wav()
    with wave.open(io.BytesIO(data)) as handle:
        assert handle.getframerate() == 16_000
        assert handle.getnchannels() == 1
        assert handle.getnframes() == 32_000
    assert synth_wav() == data  # deterministic


def test_report_renders_conditions_verdicts_and_the_capacity_rule() -> None:
    step = {
        "concurrency": 5,
        "summary": summarize([_sample(ttft_ms=900.0)], [_iter()], window_s=10.0).as_dict(),
        "by_op": {},
        "thresholds": [t.as_dict() for t in evaluate_thresholds(
            [_sample(ttft_ms=3000.0)], [_iter()], Thresholds(), queue_series=None)],
        "metrics": {"reachable": False},
        "counters": {},
    }
    results = {
        "label": "gpu-l4",
        "conditions": {"date_utc": "2026-09-24 12:00:00 UTC", "target_sha": "abc1234",
                       "gateway_url": "https://staging.example", "model": "vllm/qwen",
                       "target_hardware": "1x L4", "extra": {"OLLAMA_NUM_PARALLEL": "4"}},
        "config": {"matrix": [5], "duration_s": 60, "ramp_s": 10},
        "thresholds": Thresholds().as_dict(),
        "runs": [{"run": "chat", "title": "A - normal chat", "steps": [step], "knee": None,
                  "highest_passing_concurrency": None},
                 {"run": "voice", "title": "E - voice", "skipped": "target has no STT engine configured"}],
        "warnings": [],
    }
    md = render_markdown(results)
    for needle in ("# Load test - gpu-l4", "abc1234", "1x L4", "vllm/qwen", "OLLAMA_NUM_PARALLEL",
                   "## Launch thresholds", "**FAIL**", "## Saturation", "no knee found",
                   "skipped -- target has no STT engine configured",
                   "Capacity is accepted only from a run of this harness against the target hardware"):
        assert needle in md, needle
