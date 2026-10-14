"""ADR-030 Phase 6 live checks: hosted open-weight inference, against the real providers.

Run inside the gateway container by ``verify-hosted-inference.sh``. It goes through ``Container``
and the router the gateway itself built -- the same providers, the same Redis, the same audit
sink -- so what it proves is the production wiring, not a lookalike.

Nothing here can be inferred from a green test suite. The default ``pytest`` run never makes a
network call; the provider contract tests replay *documented* response shapes, not live ones;
and quota behaviour depends on limits only the real provider enforces. Each check below closes
one of those gaps, and the report it writes is the evidence ADR-030 asks for before Phases 1-5
may be called proven.

It spends real quota: a handful of small completions, plus -- unless
``AURA_VERIFY_SKIP_429=1`` -- a short burst of large prompts sized to cross the provider's
per-minute token limit, which is the only way to observe a real 429 and its reset headers.

Exit code is the number of failed checks, capped at 125.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import traceback
from datetime import datetime, timezone

_GREEN, _RED, _YELLOW, _DIM, _RESET = "\033[1;32m", "\033[1;31m", "\033[1;33m", "\033[2m", "\033[0m"

#: A distinctive phrase sent to the hosted provider. The audit check asserts it never appears in
#: an audit record: the log is for "what left, when, at what classification" -- never content.
_PROBE = "aura-phase6-probe: reply with the single word ready"
_VERIFIER = os.environ.get("AURA_VERIFY_USER", "phase-6-verifier")

_results: list[tuple[str, str, str]] = []
_observations: list[str] = []


def _record(number: int, name: str, status: str, detail: str) -> None:
    colour = {"PASS": _GREEN, "FAIL": _RED, "SKIP": _YELLOW, "MANUAL": _YELLOW}[status]
    print(f"  {colour}{status:<6}{_RESET} {number:>2}. {name}")
    if detail:
        for line in detail.splitlines():
            print(f"         {_DIM}{line}{_RESET}")
    _results.append((str(number), name, status))


class _Skipped(Exception):
    """A check that cannot run here. Reported as SKIP, never silently as a pass."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def check(number: int, name: str):
    def decorator(fn):
        async def invoke(*args, **kwargs):
            try:
                detail = await fn(*args, **kwargs)
                _record(number, name, "PASS", detail or "")
                return True
            except _Skipped as exc:
                _record(number, name, "SKIP", str(exc))
                return True
            except Exception as exc:
                detail = f"{type(exc).__name__}: {exc}"
                if os.environ.get("AURA_VERIFY_TRACEBACK"):
                    detail += "\n" + traceback.format_exc()
                _record(number, name, "FAIL", detail)
                return False
        invoke.__name__ = fn.__name__
        return invoke
    return decorator


def _request(text: str = _PROBE, *, max_output_tokens: int = 16, timeout_s: float = 60.0):
    from aura_core.domain import CompletionRequest, Conversation, Message
    from aura_core.domain.llm import SamplingParams

    return CompletionRequest(
        conversation=Conversation.of([Message.user(text)]),
        sampling=SamplingParams(temperature=0.0, max_output_tokens=max_output_tokens),
        timeout_s=timeout_s,
    )


async def _third_party_events(audit, since: datetime) -> list:
    events = await audit.query(action="llm.third_party_generation", since=since, limit=500)
    return [e for e in events if e.action == "llm.third_party_generation"]


# -- the checks ---------------------------------------------------------------------------------


@check(1, "configuration: hosted slots are gated, open-weight, never local, with a fallback")
async def check_configuration(ctx) -> str:
    from aura_core.domain.llm import DataClassification

    hosted, local = ctx["hosted"], ctx["fallback"]
    _require(hosted, "no third-party provider is configured. List a hosted slot in "
                     "AURA_LLM_PROVIDERS (and set AURA_ALLOW_HOSTED_INFERENCE=true).")
    _require(local, "no non-hosted provider is configured. Sensitive turns would have nowhere to "
                    "go and would fail; keep `local` in AURA_LLM_PROVIDERS.")
    lines = []
    for p in hosted:
        caps = p.capabilities
        _require(not caps.is_local, f"{p.name} is hosted but declares is_local=true")
        _require(caps.max_data_classification is not DataClassification.SENSITIVE
                 or os.environ.get("AURA_VERIFY_ALLOW_SENSITIVE_CEILING"),
                 f"{p.name} accepts SENSITIVE content. If that is deliberate, rerun with "
                 "AURA_VERIFY_ALLOW_SENSITIVE_CEILING=1; check 5 then proves less.")
        limits = ctx["router"].quota.limits(p.name)
        lines.append(
            f"{p.name}: model={caps.model_id} ceiling={caps.max_data_classification.value} "
            f"tier={caps.quality_tier} tps={caps.tokens_per_second:g} "
            f"limits rpm={limits.rpm} rpd={limits.rpd} tpm={limits.tpm} tpd={limits.tpd}"
        )
    lines.append("fallback: " + ", ".join(p.name for p in local))
    return "\n".join(lines)


@check(2, "health probe reaches every hosted provider without spending a completion")
async def check_health(ctx) -> str:
    report = await ctx["router"].health()
    lines, bad = [], []
    for p in ctx["hosted"]:
        status = report.get(p.name, {})
        lines.append(f"{p.name}: healthy={status.get('healthy')} "
                     f"detail={status.get('detail')} latency_ms={status.get('latency_ms')}")
        if not status.get("healthy"):
            bad.append(p.name)
    _require(not bad, "unhealthy: " + ", ".join(bad) + ". If the provider serves no /models at "
                      "this base URL, set AURA_PROVIDER_<NAME>_HEALTH_PATH.\n" + "\n".join(lines))
    return "\n".join(lines)


@check(3, "an INTERNAL completion is served by a hosted provider, with real usage")
async def check_internal_complete(ctx) -> str:
    from aura_core.domain.llm import DataClassification
    from aura_core.llm.sensitivity import data_scope

    router = ctx["router"]
    started = time.perf_counter()
    with data_scope(DataClassification.INTERNAL, "phase6_probe"):
        response = await router.complete(_request(max_output_tokens=32))
    elapsed = (time.perf_counter() - started) * 1000
    hosted_names = {p.name for p in ctx["hosted"]}
    _require(response.provider in hosted_names,
             f"served by {response.provider!r}, not a hosted provider. Check "
             "AURA_LLM_ROUTING_POLICY (quality_first) and the slot's QUALITY_TIER.")
    _require(not response.raw_metadata.get("usage_estimated"),
             "the provider reported no usage; quotas would settle on an estimate")
    tps = response.usage.completion_tokens / (elapsed / 1000) if elapsed else 0
    _observations.append(
        f"complete via {response.provider}: {elapsed:.0f} ms, prompt={response.usage.prompt_tokens} "
        f"completion={response.usage.completion_tokens} tokens"
    )
    return (f"{response.provider} answered in {elapsed:.0f} ms "
            f"({response.usage.prompt_tokens}+{response.usage.completion_tokens} tokens, "
            f"~{tps:.0f} tok/s incl. latency): {response.text.strip()[:60]!r}")


@check(4, "an INTERNAL stream is served by a hosted provider, with usage on the last chunk")
async def check_internal_stream(ctx) -> str:
    from aura_core.domain.llm import DataClassification
    from aura_core.llm.sensitivity import data_scope

    router = ctx["router"]
    started = time.perf_counter()
    first_at = None
    chunks = []
    with data_scope(DataClassification.INTERNAL, "phase6_probe"):
        async for chunk in router.stream(_request(max_output_tokens=32)):
            if first_at is None and chunk.delta:
                first_at = time.perf_counter()
            chunks.append(chunk)
    providers = {c.provider for c in chunks if c.provider}
    hosted_names = {p.name for p in ctx["hosted"]}
    _require(providers and providers <= hosted_names, f"served by {providers}, not hosted")
    usage = next((c.usage for c in reversed(chunks) if c.usage is not None), None)
    _require(usage is not None and usage.completion_tokens > 0, "no usage on the final chunk")
    ttft = (first_at - started) * 1000 if first_at else None
    _observations.append(f"stream via {providers.pop()}: TTFT {ttft:.0f} ms" if ttft else "stream: no text")
    return f"TTFT {ttft:.0f} ms, usage {usage.prompt_tokens}+{usage.completion_tokens}" if ttft else "ok"


@check(5, "SENSITIVE content never reaches a hosted provider -- it goes local, or fails")
async def check_sensitive_stays_local(ctx) -> str:
    from aura_core.domain.errors import AllProvidersFailedError
    from aura_core.domain.llm import DataClassification
    from aura_core.llm import ProviderRouter
    from aura_core.llm.sensitivity import data_scope

    router, audit, since = ctx["router"], ctx["audit"], datetime.now(timezone.utc)
    hosted_names = {p.name for p in ctx["hosted"]}

    with data_scope(DataClassification.SENSITIVE, "phase6_probe"):
        decision = router.plan(_request())
        for name in hosted_names:
            _require(decision.excluded.get(name) == "data_classification",
                     f"{name} was not excluded for SENSITIVE content: {decision.excluded}")
        response = await router.complete(_request(max_output_tokens=8, timeout_s=180.0))
    _require(response.provider not in hosted_names, f"SENSITIVE served by {response.provider}")

    # With only hosted providers available, the turn must fail -- not send anyway.
    hosted_only = ProviderRouter(ctx["hosted"])
    with data_scope(DataClassification.SENSITIVE, "phase6_probe"):
        try:
            await hosted_only.complete(_request())
        except AllProvidersFailedError as exc:
            reasons = {a.get("reason") for a in exc.attempts}
            _require(reasons == {"data_classification"}, f"failed for the wrong reason: {reasons}")
        else:
            raise AssertionError("a hosted-only router served SENSITIVE content")

    leaked = await _third_party_events(audit, since)
    _require(not leaked, f"{len(leaked)} third-party generation(s) were audited during this check")
    return f"plan excluded {sorted(hosted_names)}; served by {response.provider}; hosted-only router refused"


@check(6, "kernel turns: a plain turn goes hosted, a turn with caller evidence stays local")
async def check_kernel_turns(ctx) -> str:
    from aura_core.domain.caller_context import CallerContext
    from aura_core.domain.identifiers import UserId
    from aura_core.kernel import TurnRequest

    kernel, audit = ctx["container"].kernel, ctx["audit"]
    user = UserId(_VERIFIER)
    plain = dict(user_id=user, use_memory=False, use_knowledge=False, use_tools=False,
                 max_output_tokens=32)

    since = datetime.now(timezone.utc)
    result = await kernel.run(TurnRequest(message=_PROBE, **plain))
    _require(result.text.strip(), f"plain turn produced no answer ({result.telemetry.error_kind})")
    hosted_events = [e for e in await _third_party_events(audit, since) if e.actor == user.value]
    _require(hosted_events, "the plain INTERNAL turn produced no third-party audit record -- it "
                            "did not reach a hosted provider")

    since = datetime.now(timezone.utc)
    evidence = (CallerContext.of(id="phase6", text="probe evidence, not secret", source="verifier"),)
    result = await kernel.run(TurnRequest(message=_PROBE, caller_context=evidence, **plain))
    leaked = [e for e in await _third_party_events(audit, since) if e.actor == user.value]
    _require(not leaked, "a turn carrying caller evidence (SENSITIVE) reached a hosted provider")
    return (f"plain turn: {len(hosted_events)} hosted generation(s) audited; "
            f"evidence turn: none (answered by fallback: {bool(result.text.strip())})")


@check(7, "the audit log records each hosted generation, never its content")
async def check_audit(ctx) -> str:
    events = await _third_party_events(ctx["audit"], ctx["started"])
    _require(events, "no llm.third_party_generation records since this run started")
    for event in events:
        detail = event.detail or {}
        _require(_PROBE not in repr(detail), "an audit record contains the prompt text")
        _require(detail.get("data_classification") in {"public", "internal"},
                 f"an audited hosted generation was classified {detail.get('data_classification')!r}")
        _require(detail.get("prompt_tokens") is not None, "an audit record has no token counts")
    return f"{len(events)} record(s); classifications {sorted({e.detail.get('data_classification') for e in events})}"


@check(8, "daily quota counters are persisted to the shared cache")
async def check_quota_persisted(ctx) -> str:
    router, cache = ctx["router"], ctx["container"].cache_backend
    declared = [p for p in ctx["hosted"] if router.quota.limits(p.name).declared]
    if not declared:
        raise _Skipped("no hosted slot declares RPM/RPD/TPM/TPD; nothing is counted to persist")
    await router.quota.flush()
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = []
    for p in declared:
        raw = await cache.get(f"llm:quota:v1:{p.name}:day:{day}")
        _require(raw is not None, f"no stored counter for {p.name} -- a restart would forget today")
        lines.append(f"{p.name}: {raw.decode()}")
    return "\n".join(lines)


@check(9, "a declared budget skips the provider before anything is sent")
async def check_declared_budget(ctx) -> str:
    from aura_core.domain.llm import DataClassification, RoutingPolicy
    from aura_core.llm import ProviderRouter, QuotaLimits, QuotaTracker
    from aura_core.llm.sensitivity import data_scope

    target = ctx["hosted"][0]
    since = datetime.now(timezone.utc)
    quota = QuotaTracker({target.name: QuotaLimits(rpm=1)})
    router = ProviderRouter([target, *ctx["fallback"]], quota=quota, audit=ctx["audit"],
                            default_policy=RoutingPolicy.QUALITY_FIRST)
    with data_scope(DataClassification.INTERNAL, "phase6_probe"):
        first = await router.complete(_request(max_output_tokens=8))
        decision = router.plan(_request())
    _require(first.provider == target.name, f"first request went to {first.provider}")
    _require(decision.excluded.get(target.name) == "quota_rpm",
             f"second request was not skipped by budget: {decision.excluded}")
    sent = [e for e in await _third_party_events(ctx["audit"], since) if e.resource == target.name]
    _require(len(sent) == 1, f"expected exactly one hosted generation, audited {len(sent)}")
    return f"{target.name}: 1 sent, then excluded as quota_rpm without a request"


@check(10, "a real 429 benches the provider until its reset, without tripping the breaker")
async def check_real_429(ctx) -> str:
    if os.environ.get("AURA_VERIFY_SKIP_429"):
        raise _Skipped("AURA_VERIFY_SKIP_429 is set")
    from aura_core.domain.llm import DataClassification, RoutingPolicy
    from aura_core.llm import ProviderRouter, QuotaTracker
    from aura_core.llm.sensitivity import data_scope

    target = ctx["hosted"][0]
    # No declared limits on this router: the point is to reach the provider's *own* limit.
    router = ProviderRouter([target, *ctx["fallback"]], quota=QuotaTracker(),
                            default_policy=RoutingPolicy.QUALITY_FIRST)
    # Roughly 2,000 prompt tokens per request: a few of these cross an 8,000 TPM limit.
    filler = ("The quick brown fox jumps over the lazy dog. " * 220).strip()
    budget = int(os.environ.get("AURA_VERIFY_429_MAX_REQUESTS", "12"))
    served: list[str] = []
    with data_scope(DataClassification.INTERNAL, "phase6_429_drive"):
        for _ in range(budget):
            response = await router.complete(
                _request(f"{filler}\n\nReply with ok.", max_output_tokens=4, timeout_s=180.0)
            )
            served.append(response.provider)
            if router.quota.cooldown_remaining(target.name) > 0:
                break
    remaining = router.quota.cooldown_remaining(target.name)
    if remaining <= 0:
        raise _Skipped(f"no 429 within {budget} requests ({served.count(target.name)} served by "
                       f"{target.name}); its limit is higher than this burst. Raise "
                       "AURA_VERIFY_429_MAX_REQUESTS to drive further.")
    breaker = router._stats[target.name].breaker
    _require(breaker.consecutive_failures == 0, "the 429 counted as a breaker failure")
    _require(served[-1] != target.name, f"after the 429 the request was served by {served[-1]}")
    snapshot = router.quota.snapshot(target.name)
    _observations.append(f"429 after {served.count(target.name)} hosted requests; cooldown "
                         f"{remaining:.1f}s ({snapshot['cooldown_reason']})")
    return (f"{target.name} answered {served.count(target.name)} request(s), then 429; benched "
            f"for {remaining:.1f}s from its own reset header; the next request went to {served[-1]}")


def _manual() -> None:
    _record(11, "MANUAL: chat from the app feels faster, and Grafana shows llm_quota_events_total",
            "MANUAL",
            "Not scriptable from here: send a few ordinary chat messages from the client, confirm\n"
            "the answers come from the hosted model (llm_requests_total{provider=\"hosted1\"}) and\n"
            "that asking about an uploaded document still works (served by `local`).")


# -- driver -------------------------------------------------------------------------------------


async def main() -> int:
    from aura_core.config.settings import load_settings
    from aura_core.container import Container
    from infrastructure.secret_hydration import hydrate_secrets_from_files

    print(f"\n{_DIM}ADR-030 Phase 6 live checks -- hosted open-weight inference{_RESET}\n")
    hydrate_secrets_from_files()
    container = Container(load_settings(validate=False))
    if hasattr(container, "startup"):
        await container.startup()
    router = container.router
    providers = [router.get(name) for name in router.provider_names]
    ctx = {
        "container": container,
        "router": router,
        "audit": container.audit,
        "hosted": [p for p in providers if p.capabilities.third_party],
        "fallback": [p for p in providers if not p.capabilities.third_party],
        "started": datetime.now(timezone.utc),
    }

    failures = 0
    failures += not await check_configuration(ctx)
    if not ctx["hosted"] or not ctx["fallback"]:
        print(f"\n{_RED}Configuration is incomplete; the remaining checks cannot run.{_RESET}\n")
        return 125
    for step in (check_health, check_internal_complete, check_internal_stream,
                 check_sensitive_stays_local, check_kernel_turns, check_audit,
                 check_quota_persisted, check_declared_budget, check_real_429):
        failures += not await step(ctx)
    _manual()

    try:
        await router.close()
    except Exception:
        pass

    scripted = [r for r in _results if r[2] != "MANUAL"]
    passed = sum(1 for r in scripted if r[2] == "PASS")
    skipped = sum(1 for r in scripted if r[2] == "SKIP")
    print("\n  Observations:")
    for line in _observations:
        print(f"    - {line}")
    print(f"\n  {passed}/{len(scripted)} scripted checks passed"
          + (f", {skipped} skipped" if skipped else "")
          + (f", {_RED}{failures} FAILED{_RESET}" if failures else "")
          + "\n  1 check (11) is manual and is not counted either way.\n")
    if failures:
        print(f"{_RED}ADR-030 is not proven. Fix the failures above before claiming it.{_RESET}\n")
    elif skipped:
        print(f"{_YELLOW}Every check that ran passed, but some were skipped -- proven only for the "
              f"checks marked PASS.{_RESET}\n")
    else:
        print(f"{_GREEN}All scripted checks passed. Complete check 11 by hand, then record this "
              f"report as Phase 6's evidence.{_RESET}\n")
    return min(failures, 125)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
