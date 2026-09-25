"""Command line: ``python -m scripts.loadtest run|render`` (from ``aura_backend/``)."""

from __future__ import annotations

import argparse
import asyncio
import datetime as _dt
import json
import os
import shlex
import sys
from typing import Any, Sequence

from .config import (
    DEFAULT_MATRIX,
    SCENARIOS,
    RunConfig,
    Thresholds,
    parse_conditions,
    parse_matrix,
    parse_mix,
    parse_range,
)
from .report import render_markdown


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.loadtest",
        description="Concurrent-user load harness for the AURA v3 gateway. "
        "See docs/guides/load-testing.md.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run the concurrency matrix against a gateway")
    target = run.add_argument_group("target")
    target.add_argument("--base-url", help="gateway base URL, e.g. https://staging.example or http://gateway:8000")
    target.add_argument("--in-process", action="store_true",
                        help="self-validation: the real app on the in-memory container + echo provider "
                        "(NOT a capacity measurement)")
    target.add_argument("--metrics-url", help="gateway Prometheus endpoint (reachable only inside its network)")
    target.add_argument("--metrics-interval", type=float, default=5.0, help="seconds between in-step scrapes")
    target.add_argument("--ca-bundle", help="CA bundle for a staging TLS certificate")

    load = run.add_argument_group("load shape")
    load.add_argument("--matrix", default=",".join(map(str, DEFAULT_MATRIX)),
                      help="virtual-user counts, e.g. 1,2,5,10,20,30,40,50,75,100")
    load.add_argument("--scenarios", default="mixed",
                      help=f"comma-separated runs from {','.join(SCENARIOS)} (each gets its own matrix)")
    load.add_argument("--mix", help="mixed-run weights, e.g. chat=50,long_context=10,agent=15,voice=5,api=20")
    load.add_argument("--ramp", type=float, default=10.0, help="ramp-up seconds per step")
    load.add_argument("--duration", type=float, default=60.0, help="steady-state seconds per step")
    load.add_argument("--iterations", type=int, help="count-bound mode: iterations per VU (overrides --duration)")
    load.add_argument("--think", default="1-3", help="think time seconds, 'N' or 'MIN-MAX'")
    load.add_argument("--cooldown", type=float, default=5.0, help="pause between steps")
    load.add_argument("--max-output-tokens", type=int, default=256)
    load.add_argument("--timeout", type=float, default=300.0, help="per-turn timeout (seconds)")
    load.add_argument("--api-timeout", type=float, default=30.0)
    load.add_argument("--retries", type=int, default=0, help="retries on 429/502/503/504/connection errors")
    load.add_argument("--retry-cap-s", type=float, default=10.0, help="longest wait before one retry")
    load.add_argument("--seed", type=int, default=1)

    auth = run.add_argument_group("identities")
    auth.add_argument("--auth", choices=("none", "tokens", "mint"), default="none")
    auth.add_argument("--tokens-file", help="one access token per line (auth=tokens)")
    auth.add_argument("--jwt-secret-env", default="AURA_LOADTEST_JWT_SECRET",
                      help="NAME of the env var holding the target's JWT secret (auth=mint)")
    auth.add_argument("--mint-role", default="User", help="User, Premium or Developer")
    auth.add_argument("--user-prefix", default="loadtest-vu")

    scen = run.add_argument_group("scenarios")
    scen.add_argument("--long-context-chars", type=int, default=12_000)
    scen.add_argument("--voice-wav", help="real 16 kHz mono WAV question (default: synthesized fixture)")
    scen.add_argument("--engineer-repo", help="repository the Engineer scenario opens (owner/name or URL)")
    scen.add_argument("--engineer-test-command", default="pytest -q")
    scen.add_argument("--engineer-max-confirm-rounds", type=int, default=3)
    scen.add_argument("--no-setup", action="store_true", help="skip per-identity document setup")
    scen.add_argument("--no-model-probe", action="store_true")

    rec = run.add_argument_group("run conditions (recorded in the report)")
    rec.add_argument("--label", default="loadtest")
    rec.add_argument("--target-sha", help="git SHA deployed on the target")
    rec.add_argument("--target-hardware", help="e.g. '1x L4 24GB, 8 vCPU, 32 GB'")
    rec.add_argument("--model", dest="model_label", help="model as deployed, e.g. 'vLLM Qwen2.5-7B-Instruct AWQ'")
    rec.add_argument("--condition", action="append", metavar="KEY=VALUE",
                     help="extra recorded condition, repeatable (e.g. OLLAMA_NUM_PARALLEL=4)")

    th = run.add_argument_group("thresholds")
    th.add_argument("--api-p95-ms", type=float, default=500.0)
    th.add_argument("--api-p99-ms", type=float, default=1000.0)
    th.add_argument("--ttft-p95-ms", type=float, default=2500.0)
    th.add_argument("--response-p95-ms", type=float, default=15000.0)
    th.add_argument("--max-5xx-rate", type=float, default=0.01)
    th.add_argument("--max-queue-depth", type=float)

    out = run.add_argument_group("output")
    out.add_argument("--out-dir", default="loadtest-results")
    out.add_argument("--title", help="report title")
    out.add_argument("--no-raw-samples", action="store_true", help="omit per-request rows from the JSON")

    render = sub.add_parser("render", help="re-render the Markdown report from a results JSON")
    render.add_argument("results")
    render.add_argument("--out", help="Markdown path (default: alongside the JSON)")
    render.add_argument("--title")
    return parser


def config_from_args(args: argparse.Namespace) -> RunConfig:
    think_min, think_max = parse_range(args.think)
    cfg = RunConfig(
        base_url=args.base_url or ("http://aura-inprocess" if args.in_process else ""),
        matrix=parse_matrix(args.matrix),
        scenarios=tuple(s.strip() for s in args.scenarios.split(",") if s.strip()),
        ramp_s=args.ramp,
        duration_s=args.duration,
        iterations_per_vu=args.iterations,
        think_min_s=think_min,
        think_max_s=think_max,
        cooldown_s=args.cooldown,
        max_output_tokens=args.max_output_tokens,
        request_timeout_s=args.timeout,
        api_timeout_s=args.api_timeout,
        max_retries=args.retries,
        retry_cap_s=args.retry_cap_s,
        seed=args.seed,
        auth_mode=args.auth,
        tokens_file=args.tokens_file,
        jwt_secret_env=args.jwt_secret_env,
        mint_role=args.mint_role,
        user_prefix=args.user_prefix,
        long_context_chars=args.long_context_chars,
        voice_wav=args.voice_wav,
        engineer_repo=args.engineer_repo,
        engineer_test_command=args.engineer_test_command,
        engineer_max_confirm_rounds=args.engineer_max_confirm_rounds,
        setup_documents=not args.no_setup,
        model_probe=not args.no_model_probe,
        metrics_url=args.metrics_url,
        metrics_interval_s=args.metrics_interval,
        label=args.label,
        target_sha=args.target_sha,
        target_hardware=args.target_hardware,
        model_label=args.model_label,
        conditions=parse_conditions(args.condition),
        ca_bundle=args.ca_bundle,
        include_raw_samples=not args.no_raw_samples,
        thresholds=Thresholds(
            api_p95_ms=args.api_p95_ms, api_p99_ms=args.api_p99_ms, ttft_p95_ms=args.ttft_p95_ms,
            response_p95_ms=args.response_p95_ms, server_error_rate=args.max_5xx_rate,
            max_queue_depth=args.max_queue_depth,
        ),
    )
    if args.mix:
        cfg.mix = parse_mix(args.mix)
    cfg.validate()
    return cfg


async def execute(cfg: RunConfig, *, in_process: bool = False, log=None) -> dict[str, Any]:
    from .runner import Runner

    if not in_process:
        return await Runner(cfg, log=log).run()
    from .inprocess import inprocess_target

    async with inprocess_target() as target:
        cfg.base_url = target.base_url
        if cfg.metrics_url is None:
            cfg.metrics_url = target.metrics_path
        cfg.auth_mode = "mint"
        runner = Runner(cfg, tokens=target.tokens, transport=target.transport, in_process=True, log=log)
        return await runner.run()


def write_outputs(results: dict[str, Any], out_dir: str, *, title: str | None = None) -> tuple[str, str]:
    os.makedirs(out_dir, exist_ok=True)
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = os.path.join(out_dir, f"{results.get('label', 'loadtest')}-{stamp}")
    json_path, md_path = f"{base}.json", f"{base}.md"
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(results, handle, indent=1, default=str)
    with open(md_path, "w", encoding="utf-8") as handle:
        handle.write(render_markdown(results, title=title))
    return json_path, md_path


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "render":
        with open(args.results, encoding="utf-8") as handle:
            results = json.load(handle)
        out = args.out or os.path.splitext(args.results)[0] + ".md"
        with open(out, "w", encoding="utf-8") as handle:
            handle.write(render_markdown(results, title=args.title))
        print(out)
        return 0
    if not args.base_url and not args.in_process:
        parser.error("--base-url is required (or --in-process for self-validation)")
    try:
        cfg = config_from_args(args)
    except ValueError as exc:
        parser.error(str(exc))
    from .runner import HarnessAbort

    try:
        results = asyncio.run(execute(cfg, in_process=args.in_process))
    except HarnessAbort as exc:
        print(f"aborted: {exc}", file=sys.stderr)
        return 2
    json_path, md_path = write_outputs(results, args.out_dir, title=args.title)
    print(f"results: {json_path}\nreport:  {md_path}")
    return 0


def args_from_env(value: str) -> list[str]:
    """``AURA_LOADTEST_ARGS`` (used by the ``-m load`` wrapper) -> argv."""
    return shlex.split(value)
