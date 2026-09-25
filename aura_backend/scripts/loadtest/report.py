"""Render a results dict (as written to the JSON file) into the Markdown report.

Same shape as ``docs/reports/phase-a4-load-test.md``: what was run and against what, the status
up front, the measured numbers, and what the numbers do and do not show. Rendered only from the
JSON, so ``python -m scripts.loadtest render results.json`` reproduces the report exactly and a
report can never contain a number its JSON does not.
"""

from __future__ import annotations

from typing import Any, Mapping

from .config import SCENARIO_TITLES
from .stats import ERROR_CLASSES, FAIL, PASS

_CAPACITY_RULE = (
    "Capacity is accepted only from a run of this harness against the target hardware, with the "
    "target model, recorded with its run conditions. A number from any other host, model or "
    "provider -- including every echo-provider or in-process run -- is a harness check, not a "
    "capacity figure."
)


def _ms(value: Any) -> str:
    if value is None:
        return "-"
    if value >= 10_000:
        return f"{value / 1000:,.1f} s"
    return f"{value:,.0f} ms"


def _num(value: Any, fmt: str = "{:,.2f}") -> str:
    return "-" if value is None else fmt.format(value)


def render_markdown(results: Mapping[str, Any], *, title: str | None = None) -> str:
    cond = results.get("conditions", {})
    cfg = results.get("config", {})
    runs = results.get("runs", [])
    warnings = results.get("warnings", [])
    lines: list[str] = []
    add = lines.append

    add(f"# {title or 'Load test - ' + str(results.get('label', 'loadtest'))}")
    add("")
    add(
        f"Run {cond.get('date_utc', '?')} against target `{cond.get('target_sha', '?')}` "
        f"(harness `{str(cond.get('harness_git_sha', '?'))[:12]}`, v{cond.get('harness_version', '?')}), "
        f"gateway `{cond.get('gateway_url', '?')}`, model `{cond.get('model', '?')}`, "
        f"hardware: {cond.get('target_hardware', '?')}. Produced by `aura_backend/scripts/loadtest` "
        "(runbook: [`docs/guides/load-testing.md`](../guides/load-testing.md))."
    )
    add("")

    # -- status ------------------------------------------------------------------------
    add("## Status")
    add("")
    if warnings:
        for warning in warnings:
            add(f"- **{warning}**" if warning.startswith("ECHO") else f"- {warning}")
    else:
        add("- No harness warnings.")
    add("")
    for run in runs:
        if run.get("skipped"):
            add(f"- **{run['title']}**: skipped -- {run['skipped']}")
            continue
        knee = run.get("knee")
        knee_text = (
            f"saturation knee at **{knee['knee_concurrency']}** users ({knee['reason']})"
            if knee else "no saturation knee inside the matrix"
        )
        highest = run.get("highest_passing_concurrency")
        pass_text = (
            f"every evaluated threshold passed up to **{highest}** users"
            if highest is not None else "no step passed every evaluated threshold"
        )
        add(f"- **{run['title']}**: {pass_text}; {knee_text}.")
    add("")
    add(f"> {_CAPACITY_RULE}")
    add("")

    # -- run conditions ----------------------------------------------------------------
    add("## Run conditions")
    add("")
    add("| | |")
    add("| --- | --- |")
    gen = cond.get("load_generator") or {}
    rows = [
        ("Date (UTC)", f"{cond.get('date_utc')} -> {cond.get('finished_utc')}"),
        ("Target git SHA", f"`{cond.get('target_sha')}`"),
        ("Harness git SHA", f"`{cond.get('harness_git_sha')}`"),
        ("Gateway URL (redacted)", f"`{cond.get('gateway_url')}`"),
        ("Metrics URL", f"`{cond.get('metrics_url')}`" if cond.get("metrics_url") else "not scraped"),
        ("Target hardware", cond.get("target_hardware")),
        ("Model", f"`{cond.get('model')}` (probe: {cond.get('probe_provider')}/{cond.get('probe_model')})"),
        ("Environment", cond.get("environment")),
        ("In-process (ASGI)", "yes" if cond.get("in_process") else "no"),
        ("Identities", cond.get("auth")),
        ("Load generator", f"{gen.get('platform')}, Python {gen.get('python')}, {gen.get('cpus')} CPUs"),
        ("Matrix", ", ".join(str(c) for c in cfg.get("matrix", []))),
        (
            "Step shape",
            (f"{cfg.get('iterations_per_vu')} iterations per VU" if cfg.get("iterations_per_vu")
             else f"ramp {cfg.get('ramp_s')} s, steady {cfg.get('duration_s')} s")
            + f", think {cfg.get('think_min_s')}-{cfg.get('think_max_s')} s, cooldown {cfg.get('cooldown_s')} s",
        ),
        ("Output tokens / timeouts", f"max_output_tokens {cfg.get('max_output_tokens')}; turn timeout "
         f"{cfg.get('request_timeout_s')} s, API timeout {cfg.get('api_timeout_s')} s; retries {cfg.get('max_retries')}"),
        ("Voice audio", cond.get("voice_audio") or "-"),
    ]
    for key, value in (cond.get("extra") or {}).items():
        rows.append((f"`{key}`", value))
    for key, value in rows:
        add(f"| {key} | {value} |")
    add("")
    pre = results.get("preflight", {})
    add(
        f"Preflight: `/health/ready` {pre.get('health_status')}, providers "
        f"{pre.get('providers') or '-'}, voice {pre.get('voice') or '-'}, workspaces "
        f"{pre.get('workspaces_enabled')}. Setup: {results.get('setup', {})}."
    )
    add("")

    # -- numbers -----------------------------------------------------------------------
    add("## The measured numbers")
    add("")
    add(
        "Per step: RPS/RPM count HTTP requests *started* in the steady window; goodput counts "
        "successful scenario iterations; TTFT is time to the first `token` frame; E2E is the whole "
        "scenario iteration (all of its requests, retries included); tok/s is `done.usage."
        "completion_tokens` over the post-first-token span, where the stream reports it."
    )
    add("")
    for run in runs:
        add(f"### {run['title']}")
        add("")
        if run.get("skipped"):
            add(f"Skipped: {run['skipped']}.")
            add("")
            continue
        if run.get("run") == "mixed":
            add(f"Traffic mix (weights, normalised over available scenarios): {run.get('mix')}.")
            if run.get("dropped_from_mix"):
                add(f"Dropped from the mix: {'; '.join(run['dropped_from_mix'])}.")
            add("")
        add("| Users | Req | RPS | Req/min | Goodput/s | TTFT p50 | TTFT p95 | TTFT p99 | E2E p50 | E2E p95 | E2E p99 | tok/s p50 | Err % | 5xx-class % | Retries |")
        add("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
        for step in run.get("steps", []):
            s = step["summary"]
            add(
                f"| {step['concurrency']} | {s['requests']} | {s['rps']:.2f} | {s['rpm']:.0f} | "
                f"{s['goodput_per_s']:.2f} | {_ms(s['ttft_ms'].get('p50'))} | {_ms(s['ttft_ms'].get('p95'))} | "
                f"{_ms(s['ttft_ms'].get('p99'))} | {_ms(s['e2e_ms'].get('p50'))} | {_ms(s['e2e_ms'].get('p95'))} | "
                f"{_ms(s['e2e_ms'].get('p99'))} | {_num(s['tokens_per_s'].get('p50'), '{:,.1f}')} | "
                f"{s['error_rate']:.1%} | {s['server_failure_rate']:.1%} | {s['retries']} |"
            )
        add("")
        add("Errors by class (steady-state requests):")
        add("")
        add("| Users | " + " | ".join(ERROR_CLASSES) + " | Top details |")
        add("| --- | " + " | ".join("---" for _ in ERROR_CLASSES) + " | --- |")
        for step in run.get("steps", []):
            s = step["summary"]
            details = "; ".join(f"{d} x{n}" for d, n in s.get("top_error_details", [])) or "-"
            add(f"| {step['concurrency']} | " + " | ".join(str(s["errors"].get(c, 0)) for c in ERROR_CLASSES)
                + f" | {details} |")
        add("")
        if run.get("run") == "mixed" or any(len(st.get("by_op", {})) > 1 for st in run.get("steps", [])):
            add("Per request type (p95 request latency / p95 TTFT / count):")
            add("")
            ops = sorted({op for st in run.get("steps", []) for op in st.get("by_op", {})})
            add("| Users | " + " | ".join(f"`{op}`" for op in ops) + " |")
            add("| --- | " + " | ".join("---" for _ in ops) + " |")
            for step in run.get("steps", []):
                cells = []
                for op in ops:
                    data = step.get("by_op", {}).get(op)
                    if not data:
                        cells.append("-")
                        continue
                    ttft = data["ttft_ms"].get("p95")
                    cells.append(
                        f"{_ms(data['request_ms'].get('p95'))}"
                        + (f" / {_ms(ttft)}" if ttft is not None else "")
                        + f" / n={data['requests']}"
                    )
                add(f"| {step['concurrency']} | " + " | ".join(cells) + " |")
            add("")
        counters = [(st["concurrency"], st.get("counters") or {}) for st in run.get("steps", [])]
        if any(c for _, c in counters):
            add("Scenario counters: " + "; ".join(
                f"{u} users {dict(c)}" for u, c in counters if c
            ) + ".")
            add("")

    # -- thresholds --------------------------------------------------------------------
    add("## Launch thresholds")
    add("")
    th = results.get("thresholds", {})
    add(
        f"Proposed launch thresholds: non-LLM API p95 < {th.get('api_p95_ms')} ms / p99 < "
        f"{th.get('api_p99_ms')} ms; TTFT p95 < {th.get('ttft_p95_ms')} ms and ordinary response "
        f"p95 < {th.get('response_p95_ms')} ms (scenario A only); 5xx-class < "
        f"{(th.get('server_error_rate') or 0):.1%} (5xx, 503, timeouts, dropped connections, "
        "mid-stream server errors -- not 429s); bounded LLM queue. `NO DATA` = no samples of that "
        "population in the step; `NOT EVALUATED` = the signal was not available (e.g. /metrics)."
    )
    add("")
    for run in runs:
        if run.get("skipped") or not run.get("steps"):
            continue
        names = [t["name"] for t in run["steps"][0]["thresholds"]]
        add(f"### {run['title']}")
        add("")
        add("| Users | " + " | ".join(names) + " |")
        add("| --- | " + " | ".join("---" for _ in names) + " |")
        for step in run["steps"]:
            cells = []
            for t in step["thresholds"]:
                verdict = t["verdict"]
                mark = f"**{verdict}**" if verdict in (PASS, FAIL) else verdict
                cells.append(f"{mark} ({t['observed']})")
            add(f"| {step['concurrency']} | " + " | ".join(cells) + " |")
        add("")

    # -- saturation --------------------------------------------------------------------
    add("## Saturation")
    add("")
    add(
        "Knee = the last step before throughput (goodput/s) stopped growing by at least 10% "
        "while E2E p95 rose by at least 10% -- beyond it, extra users only queue."
    )
    add("")
    for run in runs:
        if run.get("skipped") or not run.get("steps"):
            continue
        knee = run.get("knee")
        if knee:
            add(f"- **{run['title']}**: knee at **{knee['knee_concurrency']}** users, saturated at "
                f"{knee['saturated_at']} -- {knee['reason']}.")
        else:
            add(f"- **{run['title']}**: throughput kept scaling (or latency did not rise) across the "
                "whole matrix -- no knee found; extend the matrix to find it.")
    add("")

    # -- server metrics ----------------------------------------------------------------
    add("## Server-side metrics")
    add("")
    any_metrics = False
    for run in runs:
        for step in run.get("steps", []):
            m = step.get("metrics", {})
            if not m.get("reachable"):
                continue
            if not any_metrics:
                add("| Run | Users | Queue depth max / mean | Turns in flight max | LLM in-flight max | Server TTFT mean | Server turn mean | LLM calls by outcome | Rate-limit rejections | Fallbacks | Circuit opens |")
                add("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
                any_metrics = True
            add(
                f"| {run['run']} | {step['concurrency']} | {_num(m.get('queue_depth_max'), '{:.0f}')} / "
                f"{_num(m.get('queue_depth_mean'), '{:.1f}')} | {_num(m.get('turns_in_flight_max'), '{:.0f}')} | "
                f"{_num(m.get('llm_inflight_max'), '{:.0f}')} | "
                f"{_ms(m.get('server_ttft_mean_ms'))} | {_ms(m.get('server_turn_mean_ms'))} | "
                f"{m.get('llm_requests_by_outcome') or '-'} | {_num(m.get('rate_limit_rejections'), '{:.0f}')} | "
                f"{_num(m.get('fallbacks'), '{:.0f}')} | {_num(m.get('circuit_opens'), '{:.0f}')} |"
            )
    if any_metrics:
        add("")
        exported = next(
            (st["metrics"].get("exported") for run in runs for st in run.get("steps", [])
             if st.get("metrics", {}).get("exported")),
            None,
        )
        if exported:
            add("| Signal | Exported by the gateway |")
            add("| --- | --- |")
            for signal, status in exported.items():
                add(f"| {signal} | {status} |")
            add("")
            add("`declared, never set` means the family has a `# TYPE` line but no series: it "
                "looks available on a dashboard and measures nothing. `no` means the gateway does "
                "not export it at all. Neither is measured by this run. Note also that "
                "`aura_turns_in_flight` is maintained only by `AIKernel.run` (`POST /chat`), not "
                "by `AIKernel.stream` (`/chat/stream`, what every scenario here uses), so it reads "
                "~0 under streamed load; `aura_llm_inflight` is the in-flight signal to read.")
    else:
        add("Not captured: no `--metrics-url` was given or it was unreachable. Production nginx "
            "denies `/metrics` on the public edge; point `--metrics-url` at the gateway from inside "
            "its network (runbook, 'Server-side metrics').")
    add("")

    # -- meaning -----------------------------------------------------------------------
    add("## What this does and does not show")
    add("")
    add(
        "- It shows how *this* deployment -- the hardware, model, provider settings and limits in "
        "the run-conditions table -- behaved under the recorded matrix. Change any of those and "
        "the numbers no longer apply."
    )
    add(
        "- Closed-loop virtual users with think time: offered load is bounded by the user count, "
        "so a saturated system shows rising latency and flat throughput rather than an unbounded "
        "backlog. The knee and the threshold table are where to read capacity from."
    )
    add(
        "- 429s are the gateway's (or nginx's) rate limiting and are reported apart from server "
        "failures; a step dominated by them measured the limiter, not capacity."
    )
    add(f"- {_CAPACITY_RULE}")
    add("")
    return "\n".join(lines)


__all__ = ["render_markdown", "SCENARIO_TITLES"]
