#!/usr/bin/env python
"""AURA Agent Evaluation Runner.

Orchestrates evaluation suites against the AI Kernel, collecting traces and metrics.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

# Add backend to path
BACKEND_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_ROOT))

from aura_core.evaluation.harness import (
    AgentEvaluator,
    AgentCase,
    AgentSuiteResult,
    RegressionGate,
    load_agent_cases,
)
from aura_core.container import Container
from aura_core.config.settings import AuraSettings


async def main() -> int:
    parser = argparse.ArgumentParser(description="Run AURA agent evaluation suite")
    parser.add_argument("--suite", default="agent_tasks", help="Suite name (file without .jsonl)")
    parser.add_argument("--baseline", help="Path to baseline JSON for regression gate")
    parser.add_argument("--output", help="Output path for results JSON")
    parser.add_argument("--max-turns", type=int, default=10, help="Max turns per task")
    parser.add_argument("--timeout", type=float, default=120.0, help="Timeout per turn (seconds)")
    args = parser.parse_args()

    settings = AuraSettings()
    container = Container(settings)
    await container.startup()

    try:
        suite_path = BACKEND_ROOT / "evaluation" / "suites" / f"{args.suite}.jsonl"
        if not suite_path.exists():
            print(f"Suite not found: {suite_path}")
            return 1

        cases = load_agent_cases(suite_path)
        # Override max_turns from CLI if provided
        cases = [AgentCase(**{**case.__dict__, "max_turns": args.max_turns}) for case in cases]
        print(f"Loaded {len(cases)} cases from {suite_path}")

        evaluator = AgentEvaluator(
            container.kernel,
            max_turns=args.max_turns,
            timeout_s=args.timeout,
        )
        started = time.perf_counter()
        result = await evaluator.run(cases)
        total_duration = (time.perf_counter() - started) * 1000.0

        print(f"\n=== {result.suite} ===")
        print(f"Cases: {len(result.results)}")
        print(f"Total duration: {total_duration:.0f}ms")
        print(f"Pass rate: {result.pass_rate:.2%}")
        print(f"Tool success rate: {result.avg_tool_success_rate:.2%}")
        print(f"Task completion rate: {result.task_completion_rate:.2%}")
        print(f"Avg turns: {result.metrics()['avg_turns']}")
        print(f"Avg duration per case: {result.metrics()['avg_duration_ms']:.0f}ms")

        if args.baseline and Path(args.baseline).exists():
            with Path(args.baseline).open("r") as f:
                baseline = json.load(f)
            gate = RegressionGate()
            ok, failures = gate.check(result, baseline.get("metrics", {}))
            print(f"\nRegression gate: {'PASS' if ok else 'FAIL'}")
            for f in failures:
                print(f"  - {f}")
            if not ok:
                return 1

        if args.output:
            output_path = Path(args.output)
            output_path.write_text(json.dumps(result.as_dict(), indent=2))
            print(f"\nResults written to {output_path}")

        return 0

    finally:
        await container.shutdown()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))