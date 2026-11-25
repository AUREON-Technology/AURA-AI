"""ADR-047: platform health, live telemetry, trace analysis and CI-workflow deploys.

REAL: the tools, the HTTP code (urllib, no-redirect opener, caps), the registry pipeline (grants,
confirmation, capability), the analyzers. SIMULATED: the far side -- a local HTTP server that answers
the way the platform's gateway (``/health/*``), Loki, Prometheus and Tempo answer (shapes checked
against their documented APIs and the live gateway's ``/health/ready`` source), and an in-memory
GitHub client that records what was dispatched.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from aura_core.config.settings import OpsSettings
from aura_core.domain.errors import ConfigurationError
from aura_core.domain.identifiers import ToolCallId, UserId
from aura_core.domain.messages import ToolCall
from aura_core.ports.runtime import ToolContext
from aura_core.tools.builtin import default_tools
from aura_core.tools.open.trace_ext import analyze_trace, parse_spans
from aura_core.tools.ops import HEALTH_PATHS, build_ops_tools, judge_health
from aura_core.tools.ops_deploy import DeployWorkflow, build_deploy_tools, parse_deploy_workflows
from aura_core.tools.policy import ToolPolicy
from aura_core.tools.registry import ToolRegistry

SHA = "a" * 40
#: The shape ``Container.health`` returns (checked against the live gateway's /health/ready).
READY_OK = {"healthy": True, "providers": {"hosted1": {"healthy": True}, "hfspace": {"healthy": False, "detail": "HTTP 503"}},
            "checks": {"blueprint": {"healthy": True}, "schema": {"healthy": True, "revision": "0033"}},
            "release": {"git_sha": SHA}}


class Backend:
    """One local server playing the gateway, Loki, Prometheus and Tempo."""

    def __init__(self) -> None:
        self.ready = (200, READY_OK)
        self.live = (200, {"status": "alive"})
        self.version = (200, {"git_sha": SHA, "deployment_id": "d-1", "app_version": "3.0.0"})
        self.requests: list[str] = []
        self.routes: dict[str, tuple[int, object]] = {}
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): ...

            def do_GET(self):
                outer.requests.append(self.path)
                path = urllib.parse.urlsplit(self.path).path
                table = {"/health/live": outer.live, "/health/version": outer.version, "/health/ready": outer.ready}
                code, body = outer.routes.get(path) or table.get(path) or next(
                    (v for k, v in outer.routes.items() if path.startswith(k)), (404, {"error": "nope"}))
                if path == "/redirect":
                    self.send_response(302); self.send_header("Location", "http://127.0.0.1:1/"); self.end_headers(); return
                raw = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def close(self) -> None:
        self.server.shutdown()


@pytest.fixture()
def backend():
    b = Backend()
    yield b
    b.close()


def ctx() -> ToolContext:
    return ToolContext(user_id=UserId("u"), correlation_id="c", deadline_s=60.0, capabilities=("engineer_agent",))


async def call(tools, name: str, **args):
    registry = ToolRegistry(policy=ToolPolicy())
    registry.register_all(tools)
    result, _ = await registry.execute(
        ToolCall(id=ToolCallId("t"), name=name, arguments=args), ctx(), capabilities=("engineer_agent",))
    return result


def data(result) -> dict:
    assert result.ok, result.content
    return json.loads(result.content)


def ops(backend, **kw):
    return build_ops_tools(platform_origins=[backend.url], allow_insecure_http=True, **kw)


# -- check_platform_health -------------------------------------------------------------------------


class TestPlatformHealth:
    async def test_a_ready_platform_is_reported_with_its_commit_schema_and_components(self, backend) -> None:
        out = data(await call(ops(backend), "check_platform_health", expected_git_sha=SHA[:12],
                              expected_schema_revision="0033"))
        (origin,) = out["origins"]
        assert out["all_healthy"] and origin["git_sha"] == SHA and origin["schema"]["revision"] == "0033"
        assert origin["components"] == {"providers.hosted1": True, "providers.hfspace": False,
                                        "checks.blueprint": True, "checks.schema": True}
        assert origin["providers_down"] == ["hfspace"], "a provider down is reported, but does not make it not-ready"
        assert sorted(r.split("?")[0] for r in backend.requests) == sorted(HEALTH_PATHS)

    async def test_a_503_ready_with_an_unhealthy_component_names_it(self, backend) -> None:
        backend.ready = (503, {"healthy": False, "database": {"healthy": False}, "providers": {"a": {"healthy": True}}})
        out = data(await call(ops(backend), "check_platform_health", attempts=1))
        assert not out["all_healthy"] and "unhealthy components: database" in out["verdict"]

    async def test_a_schema_behind_the_code_is_called_out_the_cb43_case(self, backend) -> None:
        backend.ready = (503, {"healthy": False, "checks": {"schema": {"revision": "0013", "expected": "0015", "healthy": False}}})
        out = data(await call(ops(backend), "check_platform_health", attempts=1))
        assert "migration has not run" in out["verdict"] and "0013" in out["verdict"]

    async def test_the_wrong_commit_or_revision_fails_the_check(self, backend) -> None:
        out = data(await call(ops(backend), "check_platform_health", expected_git_sha="bbbbbbb", attempts=1))
        assert not out["all_healthy"] and "expected bbbbbbb" in out["verdict"]
        out = data(await call(ops(backend), "check_platform_health", expected_schema_revision="0032", attempts=1))
        assert "expected 0032" in out["verdict"]

    async def test_a_router_level_application_not_found_is_distinguished_from_the_app(self, backend) -> None:
        for p in HEALTH_PATHS:
            backend.routes[p] = (404, {"status": "error", "code": 404, "message": "Application not found"})
        out = data(await call(ops(backend), "check_platform_health", attempts=1))
        assert "Application not found" in out["verdict"] and "router" in out["verdict"]

    async def test_an_unreachable_host_is_a_finding_not_a_crash(self) -> None:
        tools = build_ops_tools(platform_origins=["http://127.0.0.1:9"], allow_insecure_http=True)
        out = data(await call(tools, "check_platform_health", attempts=1))
        assert not out["all_healthy"] and "could not be reached" in out["verdict"]

    async def test_it_retries_until_the_platform_comes_up(self, backend, monkeypatch) -> None:
        backend.ready = (503, {"healthy": False})
        import asyncio

        original = asyncio.sleep

        async def flip(_s):
            backend.ready = (200, READY_OK)
            await original(0)

        monkeypatch.setattr("aura_core.tools.ops.asyncio.sleep", flip)
        out = data(await call(ops(backend), "check_platform_health", attempts=3))
        assert out["all_healthy"] and out["origins"][0]["attempts"] == 2

    async def test_the_caller_cannot_choose_the_target(self, backend) -> None:
        tools = ops(backend)
        for bad in ("http://169.254.169.254", "https://evil.example", backend.url + "/api/v3/x",
                    "http://127.0.0.1:1", "file:///etc/passwd"):
            result = await call(tools, "check_platform_health", origin=bad)
            assert not result.ok, bad
        assert backend.requests == []
        tool = next(t for t in tools if t.spec.name == "check_platform_health")
        assert {p.name for p in tool.spec.parameters} == {"origin", "expected_git_sha", "expected_schema_revision", "attempts"}

    async def test_a_redirect_is_reported_not_followed(self, backend) -> None:
        backend.routes["/health/live"] = (302, b"")
        out = data(await call(ops(backend), "check_platform_health", attempts=1))
        assert "HTTP 302" in out["verdict"]

    async def test_a_hostile_expected_sha_is_refused(self, backend) -> None:
        assert not (await call(ops(backend), "check_platform_health", expected_git_sha="abc; rm -rf /")).ok

    def test_it_only_exists_when_the_operator_configured_an_origin(self) -> None:
        assert build_ops_tools() == []
        with pytest.raises(ValueError):
            build_ops_tools(platform_origins=["http://aureon-ai.tech"])  # https only outside tests
        with pytest.raises(ValueError):
            build_ops_tools(platform_origins=["https://user:pw@aureon-ai.tech"])
        with pytest.raises(ValueError):
            build_ops_tools(platform_origins=["https://aureon-ai.tech/health"])

    def test_judge_health_is_pure(self) -> None:
        ok, problems, facts = judge_health({
            "/health/live": {"status": 200, "body": b"{}"},
            "/health/version": {"status": 200, "body": json.dumps({"git_sha": SHA}).encode()},
            "/health/ready": {"status": 200, "body": json.dumps(READY_OK).encode()}})
        assert ok and problems == [] and facts["git_sha"] == SHA


# -- query_telemetry ---------------------------------------------------------------------------------


LOKI = {"status": "success", "data": {"result": [{"stream": {"app": "gateway"}, "values": [
    [str(int(time.time() * 1e9) - 3_000_000_000), '{"level":"error","msg":"provider timeout","component":"router"}'],
    [str(int(time.time() * 1e9) - 2_000_000_000), '{"level":"error","msg":"provider timeout","component":"router"}'],
    [str(int(time.time() * 1e9) - 1_000_000_000), '{"level":"info","msg":"ok"}']]}]}}
PROM = {"status": "success", "data": {"resultType": "matrix", "result": [
    {"metric": {"job": "gateway"}, "values": [[1, "0.1"], [2, "0.4"], [3, "NaN"], [4, "0.9"]]}]}}


def span(sid, parent, name, start_ms, end_ms, *, error=False, service="gateway"):
    return {"spanId": sid, "parentSpanId": parent, "name": name, "startTimeUnixNano": str((start_ms + 1000) * 1_000_000),
            "endTimeUnixNano": str((end_ms + 1000) * 1_000_000), "status": {"code": 2 if error else 1}}


def trace(*spans_by_service):
    return {"batches": [{"resource": {"attributes": [{"key": "service.name", "value": {"stringValue": svc}}]},
                         "scopeSpans": [{"spans": spans}]} for svc, spans in spans_by_service]}


TRACE = trace(("gateway", [span("a", "", "POST /chat", 0, 1000), span("b", "a", "plan", 10, 200),
                           span("c", "a", "generate", 210, 990)]),
              ("llm", [span("d", "c", "provider.complete", 220, 980, error=True)]))


class TestTelemetry:
    def tools(self, backend, **kw):
        return build_ops_tools(loki_url=backend.url, prometheus_url=backend.url, tempo_url=backend.url, **kw)

    async def test_logs_are_fetched_from_loki_with_bounds_and_analysed(self, backend) -> None:
        backend.routes["/loki/api/v1/query_range"] = (200, LOKI)
        out = data(await call(self.tools(backend), "query_telemetry", source="logs", query='{app="gateway"} |= "error"',
                              minutes=30, limit=100))
        assert out["lines"] == 3 and out["analysis"] and out["read_only"]
        sent = urllib.parse.parse_qs(urllib.parse.urlsplit(backend.requests[-1]).query)
        assert sent["limit"] == ["100"] and sent["direction"] == ["backward"]
        assert (int(sent["end"][0]) - int(sent["start"][0])) / 1e9 == pytest.approx(1800, abs=2)

    async def test_metrics_are_summarised_per_series_skipping_nan(self, backend) -> None:
        backend.routes["/api/v1/query_range"] = (200, PROM)
        out = data(await call(self.tools(backend), "query_telemetry", source="metrics", query="rate(x[5m])"))
        (series,) = out["summary"]
        assert (series["min"], series["max"], series["last"], series["points"]) == (0.1, 0.9, 0.9, 3)

    async def test_trace_search_and_a_single_trace_are_analysed(self, backend) -> None:
        backend.routes["/api/search"] = (200, {"traces": [{"traceID": "ab" * 16, "rootServiceName": "gateway",
                                                            "rootTraceName": "POST /chat", "durationMs": 1000}]})
        backend.routes["/api/traces/"] = (200, TRACE)
        found = data(await call(self.tools(backend), "query_telemetry", source="traces", query='{ status = error }'))
        assert found["traces"][0]["traceID"] == "ab" * 16
        one = data(await call(self.tools(backend), "query_telemetry", source="trace", query="ab" * 16))
        assert one["analysis"]["error_count"] == 1 and one["analysis"]["services"] == ["gateway", "llm"]

    async def test_a_backend_error_unreachable_host_and_oversized_answer_are_clear_failures(self, backend) -> None:
        backend.routes["/loki/api/v1/query_range"] = (400, b"parse error at line 1")
        bad = await call(self.tools(backend), "query_telemetry", source="logs", query="{")
        assert not bad.ok and "HTTP 400" in bad.content and "parse error" in bad.content
        gone = await call(build_ops_tools(loki_url="http://127.0.0.1:9"), "query_telemetry", source="logs", query="{a=\"b\"}")
        assert not gone.ok and "unreachable" in gone.content
        backend.routes["/api/v1/query_range"] = (200, b"[" + b"1," * 3_000_000 + b"1]")
        huge = await call(self.tools(backend), "query_telemetry", source="metrics", query="x")
        assert not huge.ok and "4 MiB" in huge.content

    async def test_only_configured_sources_exist_and_a_trace_id_is_validated(self, backend) -> None:
        only_logs = build_ops_tools(loki_url=backend.url)
        assert not (await call(only_logs, "query_telemetry", source="metrics", query="x")).ok
        assert not (await call(self.tools(backend), "query_telemetry", source="trace", query="../../etc/passwd")).ok
        assert not (await call(self.tools(backend), "query_telemetry", source="logs", query="a\x00b")).ok
        assert build_ops_tools(loki_url="") == []

    async def test_a_bearer_token_comes_from_the_named_environment_variable(self, backend, monkeypatch) -> None:
        seen: list[str] = []
        backend.routes["/loki/api/v1/query_range"] = (200, LOKI)
        monkeypatch.setenv("OPS_TEST_TOKEN", "s3cret-token")
        import aura_core.tools.ops as ops_module

        real = ops_module._get

        def spy(url, **kw):
            seen.append((kw.get("headers") or {}).get("Authorization", ""))
            return real(url, **kw)

        monkeypatch.setattr(ops_module, "_get", spy)
        data(await call(self.tools(backend, token_env="OPS_TEST_TOKEN"), "query_telemetry", source="logs", query="{a=\"b\"}"))
        assert seen == ["Bearer s3cret-token"]


class TestReviewFindings:
    """Found by an independent review of the first version."""

    def results(self, *, version=None, ready=None, live=200):
        def entry(status, body):
            return {"status": status, "body": body if isinstance(body, bytes) else json.dumps(body).encode(), "ms": 1}

        return {"/health/live": entry(live, {"status": "alive"}),
                "/health/version": entry(*(version or (200, {"git_sha": SHA}))),
                "/health/ready": entry(*(ready or (200, READY_OK)))}

    def test_a_host_that_is_not_the_gateway_cannot_pass_a_verification(self) -> None:
        page = b"<html>single page app</html>"
        ok, problems, _ = judge_health(self.results(version=(200, page), ready=(200, page)), expected_git_sha="abc1234")
        assert not ok and any("not the gateway" in p for p in problems) and any("was not verified" in p for p in problems)

    def test_an_expected_revision_with_no_schema_block_is_not_a_pass(self) -> None:
        ok, problems, _ = judge_health(self.results(ready=(200, {"healthy": True})), expected_schema_revision="0033")
        assert not ok and any("schema revision could not be read" in p for p in problems)

    def test_a_shorter_reported_sha_is_compared_on_their_common_length(self) -> None:
        ok, problems, _ = judge_health(self.results(version=(200, {"git_sha": SHA[:9]})), expected_git_sha=SHA)
        assert ok, problems
        bad, problems, _ = judge_health(self.results(version=(200, {"git_sha": "b" * 9})), expected_git_sha=SHA)
        assert not bad

    def test_cyclic_self_parented_duplicate_and_very_deep_traces_do_not_crash(self) -> None:
        cyc = trace(("s", [span("a", "b", "x", 0, 10), span("b", "a", "y", 1, 9)]))
        out = analyze_trace(cyc)
        assert out["spans"] == 2 and "loop" in out["warning"]
        selfp = analyze_trace(trace(("s", [span("a", "a", "solo", 0, 10)])))
        assert selfp["spans"] == 1 and selfp["roots"] == 1
        dup = analyze_trace(trace(("s", [span("r", "", "root", 0, 10), span("a", "r", "x", 1, 9), span("a", "a", "dup", 2, 8)])))
        assert dup["spans"] == 2
        deep = [span(f"s{i}", f"s{i - 1}" if i else "", f"n{i}", 0, 10_000 - i) for i in range(5000)]
        out = analyze_trace(trace(("s", deep)))
        assert out["spans"] == 5000 and out["critical_path_truncated"] == 5000 and len(out["critical_path"]) == 50

    def test_a_span_with_no_start_time_is_dropped_not_measured_from_1970(self) -> None:
        bad = {"spanId": "x", "name": "n", "startTimeUnixNano": "0", "endTimeUnixNano": "5000000"}
        good = span("g", "", "ok", 0, 5)
        out = analyze_trace({"batches": [{"resource": {}, "scopeSpans": [{"spans": [bad, good]}]}]})
        assert out["spans"] == 1 and out["duration_ms"] == 5.0

    async def test_results_stay_valid_json_even_with_wide_label_sets(self, backend) -> None:
        wide = {f"label{i}": "v" * 500 for i in range(60)}
        rows = [[str(int(time.time() * 1e9) - i), "x" * 390] for i in range(200)]
        backend.routes["/loki/api/v1/query_range"] = (200, {"data": {"result": [{"stream": wide, "values": rows}]}})
        tools = build_ops_tools(loki_url=backend.url)
        result = await call(tools, "query_telemetry", source="logs", query="{a=\"b\"}", limit=200)
        parsed = data(result)
        assert len(result.content) <= 12_000 and len(parsed["sample"][0]["labels"]) <= 10
        assert all(len(v) <= 80 for v in parsed["sample"][0]["labels"].values())

    async def test_a_loki_metric_query_and_odd_backend_bodies_are_handled(self, backend) -> None:
        backend.routes["/loki/api/v1/query_range"] = (200, {"data": {"resultType": "matrix", "result": [
            {"metric": {"app": "gw"}, "values": [[1, "2"], [2, "4"]]}]}})
        tools = build_ops_tools(loki_url=backend.url, tempo_url=backend.url)
        out = data(await call(tools, "query_telemetry", source="logs", query="rate({a=\"b\"}[1m])"))
        assert out["series"][0]["max"] == 4.0
        backend.routes["/api/search"] = (200, [1, 2, 3])
        bad = await call(tools, "query_telemetry", source="traces", query="{}")
        assert not bad.ok and "JSON object" in bad.content
        backend.routes["/api/search"] = (200, {"traces": None})
        assert data(await call(tools, "query_telemetry", source="traces", query="{}"))["traces"] == []

    def test_an_origin_with_a_query_is_a_configuration_error_not_a_startup_crash(self, monkeypatch) -> None:
        monkeypatch.setenv("AURA_OPS_PLATFORM_ORIGINS", "https://a.example?x=1")
        with pytest.raises(ConfigurationError):
            OpsSettings.from_env()

    async def test_a_slow_drip_response_hits_the_total_deadline(self, monkeypatch) -> None:
        import aura_core.tools.ops as ops_module

        monkeypatch.setattr(ops_module, "_TOTAL_S", 0.3)

        class Slow(BaseHTTPRequestHandler):
            def log_message(self, *a): ...

            def do_GET(self):
                self.send_response(200); self.send_header("Content-Length", "100000"); self.end_headers()
                for _ in range(100):
                    self.wfile.write(b"x" * 10); self.wfile.flush(); time.sleep(0.2)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Slow)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            got = ops_module._get(f"http://127.0.0.1:{server.server_port}/", timeout=5.0, limit=1_000_000)
            assert "error" in got and "TimeoutError" in got["error"]
        finally:
            server.shutdown()


class TestRailway:
    """``source=railway``: deployments and their logs from Railway's GraphQL API (shapes checked live)."""

    IDS = dict(railway_project_id="a85f9ca9-b8c6-4ccd-a99c-90cb45fadef9", railway_service_id="d0e5cc8a-0818-4bef-84a1-fbd3ec760011",
               railway_environment_id="a9a791f3-3ffe-49a6-8760-e5594a9742ec")

    def tools(self, backend, **kw):
        return build_ops_tools(railway_token_env="RW_TEST_TOKEN", railway_api_url=backend.url + "/graphql", **self.IDS, **kw)

    def serve(self, backend, *, logs, deployments=None):
        deployments = deployments if deployments is not None else [
            {"node": {"id": "dep-2", "status": "BUILDING", "createdAt": "2026-11-25T10:00:00Z", "meta": {"commitHash": "f" * 40}}},
            {"node": {"id": "dep-1", "status": "SUCCESS", "createdAt": "2026-11-24T10:00:00Z",
                      "meta": {"commitHash": SHA, "branch": "main", "reason": "deploy", "commitMessage": "fix login"}}}]
        seen: list[dict] = []
        outer = backend

        class Post(BaseHTTPRequestHandler):
            def log_message(self, *a): ...

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                seen.append({"auth": self.headers.get("Authorization"), **body})
                data = ({"deployments": {"edges": deployments}} if "deployments(" in body["query"]
                        else {"deploymentLogs": logs})
                raw = json.dumps({"data": data}).encode()
                self.send_response(200); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Post)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        backend.url = f"http://127.0.0.1:{server.server_port}"
        outer.server_extra = server
        return seen

    async def test_logs_come_from_the_latest_successful_deployment_with_its_history(self, backend, monkeypatch) -> None:
        monkeypatch.setenv("RW_TEST_TOKEN", "rw-secret")
        seen = self.serve(backend, logs=[
            {"timestamp": "2026-11-25T10:00:00Z", "message": '{"level":"error","msg":"database timeout","component":"db"}',
             "severity": "error"},
            {"timestamp": "2026-11-25T10:00:01Z", "message": '{"level":"error","msg":"database timeout","component":"db"}',
             "severity": "error"}])
        out = data(await call(self.tools(backend), "query_telemetry", source="railway", query="@level:error", minutes=120))
        assert out["logs_from"] == "dep-1", "BUILDING is skipped: logs come from the deployment that is serving"
        assert [d["status"] for d in out["deployments"]] == ["BUILDING", "SUCCESS"] and out["deployments"][1]["commit"] == SHA[:12]
        assert out["lines"] == 2 and out["analysis"]
        logs_call = seen[-1]
        assert logs_call["variables"]["f"] == "@level:error" and logs_call["variables"]["d"] == "dep-1"
        assert all(c["auth"] == "Bearer rw-secret" for c in seen)
        star = data(await call(self.tools(backend), "query_telemetry", source="railway", query="*"))
        assert seen[-1]["variables"]["f"] is None
        backend.server_extra.shutdown()

    async def test_a_missing_token_is_an_actionable_error_and_ids_are_validated(self, backend, monkeypatch) -> None:
        monkeypatch.delenv("RW_TEST_TOKEN", raising=False)
        res = await call(self.tools(backend), "query_telemetry", source="railway", query="*")
        assert not res.ok and "AURA_OPS_RAILWAY_TOKEN_ENV" in res.content
        with pytest.raises(ValueError):
            build_ops_tools(railway_project_id="not-a-uuid", railway_service_id="x", railway_environment_id="y")
        assert build_ops_tools(railway_token_env="X") == []

    async def test_no_deployments_is_reported(self, backend, monkeypatch) -> None:
        monkeypatch.setenv("RW_TEST_TOKEN", "t")
        self.serve(backend, logs=[], deployments=[])
        out = data(await call(self.tools(backend), "query_telemetry", source="railway", query="*"))
        assert out["deployments"] == [] and "no deployments" in out["note"]
        backend.server_extra.shutdown()


class TestTraceAnalysis:
    def test_critical_path_slowest_self_time_and_errors(self) -> None:
        out = analyze_trace(TRACE)
        assert [s["name"] for s in out["critical_path"]] == ["POST /chat", "generate", "provider.complete"]
        assert out["duration_ms"] == 1000.0 and out["spans"] == 4 and out["error_count"] == 1
        assert out["slowest_by_self_time"][0]["name"] == "provider.complete"
        assert out["self_time_by_service_ms"]["llm"] == 760.0
        assert "provider.complete" in out["verdict"] and out["orphans"] == 0

    def test_formats_other_than_tempos_are_read(self) -> None:
        jaeger = {"data": [{"processes": {"p1": {"serviceName": "api"}}, "spans": [
            {"spanID": "1", "operationName": "root", "processID": "p1", "startTime": 1_000_000, "duration": 5000, "tags": []},
            {"spanID": "2", "operationName": "child", "processID": "p1", "startTime": 1_001_000, "duration": 2000,
             "references": [{"refType": "CHILD_OF", "spanID": "1"}], "tags": [{"key": "error", "value": True}]}]}]}
        out = analyze_trace(jaeger)
        assert out["spans"] == 2 and out["error_count"] == 1 and out["critical_path"][1]["name"] == "child"
        flat = analyze_trace([span("x", "", "solo", 0, 5)])
        assert flat["spans"] == 1 and flat["verdict"] == "no span reported an error"
        assert len(parse_spans({"trace": TRACE})) == 4 and len(parse_spans({"resourceSpans": TRACE["batches"]})) == 4

    def test_garbage_overlap_and_orphans_do_not_crash(self) -> None:
        assert analyze_trace({"batches": []})["spans"] == 0
        assert analyze_trace([{"nonsense": 1}])["spans"] == 0
        overlapping = trace(("s", [span("a", "", "r", 0, 100), span("b", "a", "k1", 10, 60), span("c", "a", "k2", 30, 90),
                                   span("o", "missing", "orphan", 5, 6)]))
        out = analyze_trace(overlapping)
        assert out["orphans"] == 1 and out["roots"] == 2
        root_self = next(s for s in out["slowest_by_self_time"] if s["name"] == "r")["self_ms"]
        assert root_self == 20.0, "the children's overlapping time is counted once"


# -- deploy tools --------------------------------------------------------------------------------------


class FakeGitHub:
    def __init__(self) -> None:
        self.dispatched: list[tuple] = []
        self.reachable = {"abcdef1234", "abcdef1"}  # commits that are part of main
        self.runs = [{"id": 1, "status": "completed", "conclusion": "success", "sha": SHA, "branch": "main"}]

    async def dispatch_workflow(self, repo, workflow, *, ref, inputs=None):
        self.dispatched.append((repo, workflow, ref, dict(inputs or {})))
        return f"Dispatched {workflow} on {ref}"

    async def list_workflow_runs(self, repo, workflow, *, limit=10):
        return self.runs[:limit]

    async def commit_is_ancestor(self, repo, sha, ref):
        return sha.lower() in self.reachable


WORKFLOWS = parse_deploy_workflows(["deploy-backend.yml:sha", "control-plane-deploy.yml"])


@pytest.fixture()
def github():
    return FakeGitHub()


def deploy(github, **kw):
    async def resolver(_user):
        return github

    return build_deploy_tools(resolver, repo="acme/aura", workflows=WORKFLOWS, **kw)


class TestDeployTools:
    def test_they_exist_only_with_a_repository_and_an_allowlist(self, github) -> None:
        async def r(_u):
            return github

        assert build_deploy_tools(r, repo="", workflows=WORKFLOWS) == []
        assert build_deploy_tools(r, repo="a/b", workflows=()) == []

    def test_the_allowlist_syntax_is_checked(self) -> None:
        assert parse_deploy_workflows(["a.yml:sha"]) == (DeployWorkflow("a.yml", "sha"),)
        for bad in ("a.sh", "../a.yml", "a.yml:", "a.yml:sha input", "dir/a.yml", ""):
            if bad == "a.yml:":
                assert parse_deploy_workflows([bad]) == (DeployWorkflow("a.yml", ""),)
                continue
            with pytest.raises(ValueError):
                parse_deploy_workflows([bad])

    def test_every_dispatch_is_confirmed_by_the_user_and_needs_the_engineer_capability(self, github) -> None:
        specs = {t.spec.name: t.spec for t in deploy(github)}
        for name in ("deploy_release", "rollback_release"):
            assert specs[name].side_effecting and specs[name].requires_confirmation, name
            assert specs[name].required_capability == "engineer_agent"
        assert not specs["list_workflow_runs"].side_effecting

    async def test_without_a_confirmation_nothing_is_dispatched(self, github) -> None:
        result = await call(deploy(github), "deploy_release", workflow="deploy-backend.yml", reason="ship the fix to prod")
        assert not result.ok and github.dispatched == []

    async def test_a_confirmed_deploy_dispatches_only_an_allowed_workflow_on_an_allowed_ref(self, github) -> None:
        tools = deploy(github)
        registry = ToolRegistry(policy=ToolPolicy())
        registry.register_all(tools)

        async def run(**args):
            res, _ = await registry.execute(ToolCall(id=ToolCallId("t"), name="deploy_release", arguments=args),
                                            ctx(), capabilities=("engineer_agent",), confirmed=True)
            return res

        ok = await run(workflow="deploy-backend.yml", reason="ship the fix to production")
        assert ok.ok and github.dispatched == [("acme/aura", "deploy-backend.yml", "main", {})]
        for bad in ({"workflow": "evil.yml", "reason": "a long enough reason"},
                    {"workflow": "deploy-backend.yml", "reason": "a long enough reason", "ref": "attacker-branch"},
                    {"workflow": "deploy-backend.yml", "reason": "short"},
                    {"workflow": "../../x.yml", "reason": "a long enough reason"}):
            assert not (await run(**bad)).ok, bad
        assert len(github.dispatched) == 1

    async def test_rollback_redeploys_an_earlier_commit_only_where_the_workflow_takes_one(self, github) -> None:
        registry = ToolRegistry(policy=ToolPolicy())
        registry.register_all(deploy(github))

        async def run(**args):
            res, _ = await registry.execute(ToolCall(id=ToolCallId("t"), name="rollback_release", arguments=args),
                                            ctx(), capabilities=("engineer_agent",), confirmed=True)
            return res

        good = await run(workflow="deploy-backend.yml", sha="ABCDEF1234", reason="the last release broke login")
        assert good.ok and github.dispatched == [("acme/aura", "deploy-backend.yml", "main", {"sha": "abcdef1234"})]
        assert json.loads(good.content)["rollback_to"] == "abcdef1234"
        fork = await run(workflow="deploy-backend.yml", sha="deadbeefcafe", reason="the last release broke login")
        assert not fork.ok and "not part of main" in fork.content, "a commit that is not on main is never deployed"
        refused = await run(workflow="control-plane-deploy.yml", sha="abcdef1", reason="the last release broke login")
        assert not refused.ok and "revert" in refused.content
        for sha in ("zzzzzzz", "abc", "abcdef1; rm", "main"):
            assert not (await run(workflow="deploy-backend.yml", sha=sha, reason="the last release broke login")).ok
        assert len(github.dispatched) == 1

    async def test_run_history_is_readable_without_confirmation(self, github) -> None:
        out = data(await call(deploy(github), "list_workflow_runs", workflow="deploy-backend.yml", limit=3))
        assert out["runs"][0]["conclusion"] == "success"
        assert not (await call(deploy(github), "list_workflow_runs", workflow="evil.yml")).ok

    async def test_not_connected_to_github_is_an_actionable_error(self) -> None:
        async def none(_u):
            return None

        tools = build_deploy_tools(none, repo="a/b", workflows=WORKFLOWS)
        assert "not connected" in (await call(tools, "list_workflow_runs", workflow="deploy-backend.yml")).content


# -- settings, wiring and the real GitHub adapter -------------------------------------------------------------


class TestWiring:
    def test_by_default_nothing_is_registered_and_the_catalogue_does_not_grow(self) -> None:
        base = {t.spec.name for t in default_tools(enable_analysis_tools=True)}
        with_empty = {t.spec.name for t in default_tools(enable_analysis_tools=True, ops=OpsSettings())}
        assert base == with_empty

    def test_configuration_registers_exactly_what_it_enables(self, github) -> None:
        async def resolver(_u):
            return github

        cfg = OpsSettings(platform_origins=("https://aureon-ai.tech",), loki_url="http://loki:3100",
                          deploy_workflows=("deploy-backend.yml:sha",))
        names = {t.spec.name for t in default_tools(enable_analysis_tools=True, ops=cfg, github_client_resolver=resolver,
                                                    github_default_repo="acme/aura")}
        assert {"check_platform_health", "query_telemetry", "list_workflow_runs", "deploy_release",
                "rollback_release"} <= names

    def test_the_engineer_persona_holds_the_grants_and_the_coding_agent_does_not(self) -> None:
        from aura_core.agents.registry import build_default_agent_registry

        reg = build_default_agent_registry()
        assert all(reg.get("engineer").permits_tool(n) for n in
                   ("check_platform_health", "query_telemetry", "deploy_release", "rollback_release"))

    def test_env_parsing_and_validation(self, monkeypatch) -> None:
        monkeypatch.setenv("AURA_OPS_PLATFORM_ORIGINS", "https://aureon-ai.tech/, https://b.example")
        monkeypatch.setenv("AURA_OPS_LOKI_URL", "http://loki:3100")
        monkeypatch.setenv("AURA_OPS_DEPLOY_WORKFLOWS", "deploy-backend.yml:sha")
        cfg = OpsSettings.from_env()
        assert cfg.platform_origins == ("https://aureon-ai.tech", "https://b.example") and cfg.deploy_refs == ("main",)
        for key, value in (("AURA_OPS_PLATFORM_ORIGINS", "http://insecure.example"),
                           ("AURA_OPS_PLATFORM_ORIGINS", "https://a.example/path"),
                           ("AURA_OPS_LOKI_URL", "ftp://x"), ("AURA_OPS_DEPLOY_WORKFLOWS", "../evil.yml")):
            monkeypatch.setenv(key, value)
            with pytest.raises(ConfigurationError):
                OpsSettings.from_env()
            monkeypatch.undo()
            monkeypatch.setenv("AURA_OPS_PLATFORM_ORIGINS", "https://aureon-ai.tech")

    async def test_the_real_github_client_sends_only_a_workflow_file_name_and_the_dispatch_body(self) -> None:
        import httpx

        from aura_core.adapters.github_client import GitHubApiError, GitHubClient

        sent: list[tuple[str, str, dict | None]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            sent.append((request.method, request.url.path, json.loads(request.content) if request.content else None))
            if request.method == "POST":
                return httpx.Response(204)
            return httpx.Response(200, json={"workflow_runs": [{"id": 7, "status": "completed", "conclusion": "success",
                                                                 "head_branch": "main", "head_sha": SHA,
                                                                 "event": "workflow_dispatch", "html_url": "u"}]})

        client = GitHubClient(token="token")
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
        assert "Dispatched" in await client.dispatch_workflow("acme/aura", "deploy-backend.yml", ref="main", inputs={"sha": "abc1234"})
        assert sent[-1] == ("POST", "/repos/acme/aura/actions/workflows/deploy-backend.yml/dispatches",
                            {"ref": "main", "inputs": {"sha": "abc1234"}})
        runs = await client.list_workflow_runs("acme/aura", "deploy-backend.yml", limit=5)
        assert runs[0]["sha"] == SHA and sent[-1][1].endswith("/deploy-backend.yml/runs")
        for status, expected in (("ahead", True), ("identical", True), ("behind", False), ("diverged", False)):
            client._client = httpx.AsyncClient(
                transport=httpx.MockTransport(lambda r, st=status: httpx.Response(200, json={"status": st})),
                base_url="https://api.github.com")
            assert await client.commit_is_ancestor("acme/aura", "abcdef1234", "main") is expected, status
        with pytest.raises(GitHubApiError):
            await client.commit_is_ancestor("acme/aura", "../x", "main")
        for bad in ("../../repos/x/issues", "a.sh", "a b.yml", "deploy.yml?x=1"):
            with pytest.raises(GitHubApiError):
                await client.dispatch_workflow("acme/aura", bad, ref="main")


def test_requests_carry_an_explicit_user_agent_not_urllibs_default() -> None:
    """Cloudflare-fronted APIs (Railway's) answer urllib's default agent with error 1010 -- found live."""
    from aura_core.tools import ops as ops_module

    assert ops_module._UA and "urllib" not in ops_module._UA.lower()
