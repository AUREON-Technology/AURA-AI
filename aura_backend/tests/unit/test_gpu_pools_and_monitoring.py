"""The opt-in GPU pool override, the admission compose defaults, and the P2.5 dashboards/alerts.

String/YAML-level fitness checks, like ``tests/architecture/test_dockerfile_and_observability.py``:
they prove the deploy files say what the code expects, not that vLLM runs -- that needs a GPU
host (see the override's own header: built, not proven).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from aura_core.config.settings import load_settings
from aura_core.domain.llm import ModelPool, WorkloadClass
from aura_core.llm import ProviderRouter, admission_limits_from_settings, build_providers
from aura_core.llm.workload import workload_for_turn
from aura_core.domain import CompletionRequest, Conversation, Message

yaml = pytest.importorskip("yaml")

REPO = Path(__file__).resolve().parents[3]
DEPLOY = REPO / "deploy"
GPU = DEPLOY / "docker-compose.gpu.yml"


class _ComposeLoader(yaml.SafeLoader):
    """SafeLoader that accepts compose's ``!override``/``!reset`` merge tags."""


def _passthrough(loader, node):
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node, deep=True)
    return loader.construct_scalar(node)


_ComposeLoader.add_constructor("!override", _passthrough)
_ComposeLoader.add_constructor("!reset", _passthrough)

_VAR = re.compile(r"\$\{([A-Z0-9_]+)(?::([-?])([^}]*))?\}")


def _interpolate(value: str, env: dict[str, str]) -> str:
    """Enough of compose's interpolation for these files: ``${X}``, ``${X:-d}``, ``${X:?msg}``."""

    def repl(m: re.Match) -> str:
        name, op, arg = m.group(1), m.group(2), m.group(3)
        if name in env:
            return env[name]
        if op == "?":
            raise KeyError(f"{name} is required: {arg}")
        return arg if op == "-" else ""

    return _VAR.sub(repl, str(value))


def _gpu() -> dict:
    return yaml.load(GPU.read_text(encoding="utf-8"), Loader=_ComposeLoader)


REVISIONS = {
    "AURA_VLLM_MODEL_REVISION": "1111111111111111111111111111111111111111",
    "AURA_VLLM_CODER_REVISION": "2222222222222222222222222222222222222222",
    "AURA_VLLM_EMBED_REVISION": "3333333333333333333333333333333333333333",
}


class TestGpuOverride:
    def test_every_vllm_service_pins_image_and_revision(self) -> None:
        services = _gpu()["services"]
        vllm = {n: s for n, s in services.items() if n.startswith("vllm")}
        assert set(vllm) == {"vllm", "vllm-coder", "vllm-embed"}
        for name, svc in vllm.items():
            image = svc["image"]
            assert ":latest" not in image and "@sha256:" in image, name
            command = svc["command"]
            # Unset revision must make compose refuse to render the file.
            assert re.search(r"--revision \$\{AURA_VLLM_[A-Z_]+:\?", command), name
            with pytest.raises(KeyError):
                _interpolate(command, {})
            assert svc["profiles"] == ["gpu"], name
            assert "ports" not in svc, f"{name} must not publish a port (SEC-12)"
            assert set(svc["networks"]) <= {"data", "egress"}, name
            assert svc["deploy"]["resources"]["reservations"]["devices"][0]["driver"] == "nvidia"
        for name in ("vllm", "vllm-coder"):
            assert "--enable-prefix-caching" in vllm[name]["command"]
            assert "--max-num-seqs" in vllm[name]["command"]

    def test_the_base_file_never_uses_latest_for_vllm(self) -> None:
        prod = (DEPLOY / "docker-compose.prod.yml").read_text(encoding="utf-8")
        assert "vllm/vllm-openai:latest" not in prod
        # The required-revision guard must stay out of the base file: compose interpolates the
        # whole file regardless of profiles, so it would break every CPU-only `up -d`.
        assert "AURA_VLLM_MODEL_REVISION:?" not in prod

    def test_pool_declarations_route_as_intended(self, monkeypatch) -> None:
        """Feed the override's provider env into real settings and a real router."""
        env = _gpu()["x-gpu-pool-env"]
        base = {"AURA_PROVIDER_LOCAL_KIND": "openai_compatible",
                "AURA_PROVIDER_LOCAL_BASE_URL": "http://ollama:11434/v1",
                "AURA_PROVIDER_LOCAL_MODEL": "qwen2.5:3b-instruct-q4_K_M",
                "AURA_PROVIDER_LOCAL_IS_LOCAL": "true",
                "AURA_PROVIDER_LOCAL_QUALITY_TIER": "1",
                "AURA_PROVIDER_LOCAL_MAX_CONCURRENCY": "1",
                "AURA_LLM_ROUTING_POLICY": "local_first"}
        for key, value in {**base, **env}.items():
            monkeypatch.setenv(key, _interpolate(value, REVISIONS))
        settings = load_settings(refresh=True, validate=False)

        names = [p.name for p in settings.llm.providers]
        assert names == ["general", "coder", "local", "embed"]
        by_name = {p.name: p for p in settings.llm.providers}
        assert by_name["general"].model_revision == REVISIONS["AURA_VLLM_MODEL_REVISION"]
        assert by_name["embed"].enabled is False  # declared for the embedder, not the router

        router = ProviderRouter(build_providers(settings.llm),
                                default_policy=settings.llm.default_policy,
                                group_limits=admission_limits_from_settings(settings.llm))
        assert "embed" not in router.provider_names

        def plan(workload: WorkloadClass, agent: str = "orchestrator"):
            hint = workload_for_turn(route="chat", agent=agent, user_key="u", workload=workload)
            return router.plan(CompletionRequest(
                conversation=Conversation.of([Message.user("hi")]), workload=hint))

        engineer = plan(WorkloadClass.ENGINEER, agent="engineer")
        assert engineer.candidates[0] == "coder"
        assert engineer.candidates[1:] == ("general", "local")   # fallbacks, general first
        chat = plan(WorkloadClass.INTERACTIVE)
        assert chat.candidates == ("general", "local")
        assert chat.excluded["coder"] == "pool_mismatch"

        limits = admission_limits_from_settings(settings.llm)
        assert limits["general"].max_queue == 16 and limits["general"].queue_timeout_s == 20
        assert limits["local"].max_concurrent == 1

    def test_embedding_pool_is_reachable_by_the_embedder_by_name(self, monkeypatch) -> None:
        env = _gpu()["x-gpu-pool-env"]
        for key, value in env.items():
            monkeypatch.setenv(key, _interpolate(value, REVISIONS))
        monkeypatch.setenv("AURA_PROVIDER_LOCAL_KIND", "echo")
        settings = load_settings(refresh=True, validate=False)
        embed = next(p for p in settings.llm.providers if p.name == "embed")
        assert embed.pools == (ModelPool.EMBEDDING.value,)
        assert embed.base_url == "http://vllm-embed:8000/v1"


class TestAdmissionComposeDefaults:
    @pytest.mark.parametrize("compose", ["docker-compose.prod.yml", "docker-compose.dev.yml"])
    def test_prod_and_dev_bound_the_ollama_queue(self, compose: str) -> None:
        """Parity: both stacks share the host's Ollama, so both must queue in AURA."""
        data = yaml.load((DEPLOY / compose).read_text(encoding="utf-8"), Loader=_ComposeLoader)
        gateway = data["services"]["gateway" if "prod" in compose else "dev-gateway"]["environment"]
        worker = data["services"]["worker-realtime"]["environment"]
        for env in (gateway, worker):
            assert _interpolate(env["AURA_PROVIDER_LOCAL_MAX_CONCURRENCY"], {}) == "1"
            assert int(_interpolate(env["AURA_LLM_ADMISSION_MAX_QUEUE"], {})) >= 0
            assert float(_interpolate(env["AURA_LLM_ADMISSION_QUEUE_TIMEOUT_S"], {})) > 0
        assert int(_interpolate(gateway["AURA_LLM_USER_MAX_CONCURRENT_GENERATIONS"], {})) >= 1


def _catalogue_names() -> set[str]:
    """Metric names declared in the ADR-009 catalogue, read from its source so the check holds
    without prometheus_client installed."""
    source = (REPO / "aura_backend/aura_core/observability/metrics.py").read_text(encoding="utf-8")
    return {
        f"aura_{m.group(1)}"
        for m in re.finditer(r'self\._(?:counter|gauge|histogram)\(\s*"([a-z0-9_]+)"', source)
    }


def _referenced_metrics(expr: str) -> set[str]:
    found = set()
    for token in re.findall(r"\baura_[a-z0-9_]+", expr):
        found.add(re.sub(r"_(bucket|sum|count)$", "", token))
    return found


class TestMonitoring:
    def test_dashboard_json_is_valid_and_panels_use_catalogue_metrics(self) -> None:
        dashboard = json.loads(
            (DEPLOY / "monitoring/grafana/dashboards/overview.json").read_text(encoding="utf-8")
        )
        ids = [p["id"] for p in dashboard["panels"]]
        assert len(ids) == len(set(ids)), "panel ids must be unique"
        catalogue = _catalogue_names()
        exprs = [t["expr"] for p in dashboard["panels"] for t in p.get("targets", [])]
        new = [e for e in exprs if "generation_" in e or "admission" in e or "group_" in e]
        assert len(new) >= 8
        for expr in new:
            missing = _referenced_metrics(expr) - catalogue
            assert not missing, (expr, missing)
        for series in ("queue_wait", "ttft", "decode", "total", "tokens_per_second"):
            assert any(f"aura_llm_generation_{series}" in e for e in exprs), series

    def test_saturation_alerts_exist_and_reference_catalogue_metrics(self) -> None:
        rules = yaml.safe_load(
            (DEPLOY / "monitoring/alerts/aura.yml").read_text(encoding="utf-8")
        )
        by_name = {r["alert"]: r for g in rules["groups"] for r in g["rules"]}
        catalogue = _catalogue_names()
        for name in ("AuraGenerationQueueSaturated", "AuraGenerationAdmissionRefusals"):
            rule = by_name[name]
            assert rule["for"] and rule["labels"]["severity"] in {"warning", "critical", "info"}
            missing = _referenced_metrics(rule["expr"]) - catalogue
            assert not missing, (name, missing)
        names = [r["alert"] for g in rules["groups"] for r in g["rules"]]
        assert len(names) == len(set(names)), "alert names must be unique"


BILLING_ALERTS = {
    "AuraBillingCycleStale": "critical",
    "AuraBillingCycleErrors": "warning",
    "AuraBillingNotificationsRejected": "warning",
    "AuraBillingNotifyErrors": "critical",
    "AuraStoreApiUnavailable": "warning",
    "AuraEmailFailing": "warning",
}


def test_billing_alerts_exist_reference_catalogue_metrics_and_respect_the_switch() -> None:
    """SaaS P1-H, readiness item 5: payment webhook failures, a stopped cycle and failing email
    alert; with billing off, none can fire."""
    rules = yaml.safe_load((DEPLOY / "monitoring/alerts/aura.yml").read_text(encoding="utf-8"))
    by_name = {r["alert"]: r for g in rules["groups"] for r in g["rules"]}
    catalogue = _catalogue_names()
    for name, severity in BILLING_ALERTS.items():
        rule = by_name[name]
        assert rule["labels"]["severity"] == severity, name
        assert "for" in rule and rule["annotations"]["summary"] and rule["annotations"]["description"], name
        assert re.search(r"and on\(\) max\(aura_billing_enabled\) == 1\s*$", rule["expr"]), (
            f"{name} must stay silent with billing off")
        missing = _referenced_metrics(rule["expr"]) - catalogue
        assert not missing, (name, missing)
    # Billing on but the cycle never ran: the timestamp is 0 and that must fire, so no "> 0 and"
    # guard of the kind AuraBackupStale has (a backup never configured is its own alert there).
    assert not re.search(r">\s*0\s+and", by_name["AuraBillingCycleStale"]["expr"])
    assert "time() - max(aura_billing_cycle_last_run_timestamp_seconds) > 10800" in by_name["AuraBillingCycleStale"]["expr"]
    names = [r["alert"] for g in rules["groups"] for r in g["rules"]]
    assert len(names) == len(set(names)), "alert names must be unique"
