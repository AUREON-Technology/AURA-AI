"""Phase 3: the 1 -> 100 concurrent-user matrix against a real deployed gateway (opt-in).

A pytest front door for ``scripts/loadtest`` -- the same harness the CLI runs, configured with
the same flags, passed through one environment variable so there is exactly one way to describe a
run:

    cd aura_backend
    export AURA_LOADTEST_JWT_SECRET="$(cat ../deploy/secrets/jwt_secret)"   # mint mode only
    AURA_LOADTEST_ARGS="--base-url http://gateway:8000 --metrics-url http://gateway:8000/metrics \\
        --auth mint --scenarios mixed --target-sha $(git rev-parse HEAD) \\
        --target-hardware '1x L4 24GB, 8 vCPU' --model 'vLLM Qwen2.5-7B-Instruct' \\
        --out-dir ../docs/reports/loadtest" \\
    pytest tests/load/test_capacity_matrix.py -m load -s

Skips cleanly when ``AURA_LOADTEST_ARGS`` is unset. It asserts only that the harness completed
and the target answered (auth worked, not everything failed) -- never a capacity figure. The
thresholds' PASS/FAIL live in the report, where they sit next to the run conditions that make
them mean something; a hardcoded assertion here would be a number with no hardware attached.
See ``docs/guides/load-testing.md``.
"""

from __future__ import annotations

import asyncio
import os

import pytest

pytestmark = pytest.mark.load


def test_capacity_matrix_against_a_deployed_gateway(tmp_path) -> None:
    raw = os.environ.get("AURA_LOADTEST_ARGS", "").strip()
    if not raw:
        pytest.skip("AURA_LOADTEST_ARGS not set -- see docs/guides/load-testing.md")

    from scripts.loadtest.cli import args_from_env, build_parser, config_from_args, execute, write_outputs

    args = build_parser().parse_args(["run", *args_from_env(raw)])
    cfg = config_from_args(args)
    results = asyncio.run(execute(cfg, in_process=args.in_process))
    out_dir = args.out_dir if "--out-dir" in raw else str(tmp_path)
    json_path, md_path = write_outputs(results, out_dir, title=args.title)
    print(f"\nresults: {json_path}\nreport:  {md_path}")

    measured = [step for run in results["runs"] for step in run.get("steps", [])]
    assert measured, "every scenario was skipped -- nothing was measured"
    total = sum(step["summary"]["requests"] for step in measured)
    failed_auth = sum(step["summary"]["errors"].get("4xx", 0) for step in measured)
    assert total > 0
    assert failed_auth < total, "every request was rejected -- check the auth mode"
