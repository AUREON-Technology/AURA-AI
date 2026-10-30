#!/usr/bin/env python
"""Vision eval harness (ADR-031, plan Phase 7: P7-1 harness, P7-2 bake-off, P7-3 red team).

Scores a vision model on the labelled set in ``evaluation/suites/vision_cases.py`` and writes the
report the plan's exit criterion names: a vision eval report with a pinned model. It is opt-in.
Nothing here runs in the default ``pytest`` suite except its own self-test against the oracle
and echo targets (``tests/unit/test_vision_eval.py``). A real score needs a real model.

Targets:

``space``    Any OpenAI-compatible ``/v1/chat/completions`` that takes images: the HF Space, a
             local vLLM or llama.cpp server, or Ollama's ``/v1``. This is the bake-off target. It
             measures the model alone, with the same image bytes and question for every model.
``gateway``  The deployed ``POST /api/v3/chat``: normalizer, perception, DATA fencing and the
             answering model together, which is what a user gets.
``kernel``   In-process ``Container`` built from this environment's ``AURA_*`` settings: the
             same path as ``gateway``, with no HTTP and no auth.
``oracle``   Answers with the ground truth. It proves the harness scores a perfect model 100%,
             and is never a model result.

Usage (from ``aura_backend/``)::

    export SPACE_KEY=...    # the key is read from the env var named by --api-key-env, never argv
    python -m evaluation.vision_eval run --target space \\
        --base-url https://<owner>-<space>.hf.space/v1 --api-key-env SPACE_KEY \\
        --model Qwen/Qwen3-VL-8B-Instruct --label qwen3-vl-8b --hardware "HF L40S" \\
        --revision <sha>

    AURA_EVAL_TOKEN=<user access token> python -m evaluation.vision_eval run --target gateway \\
        --base-url https://aureon-ai.tech --label prod-gateway

    python -m evaluation.vision_eval compare docs/reports/vision-eval-*.json   # bake-off table

Each run writes ``vision-eval-<label>-<timestamp>.md`` and ``.json`` to ``--report-dir``
(default ``docs/reports``). ``--baseline`` gates the run against a committed baseline with
:class:`~aura_core.evaluation.harness.RegressionGate`; ``--save-baseline`` writes one. **No
pass threshold is built in.** The plan leaves thresholds to the owner, decided after a first
measured run, and inventing one here would read as a requirement nobody set.

Exit status: 0 the run completed (and passed ``--baseline`` / ``--min-accuracy`` when given),
1 the gate failed, 2 bad arguments or an unreachable target.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import re
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, Sequence

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from evaluation.suites.vision_cases import (  # noqa: E402
    CATEGORIES,
    DEFAULT_PER_CATEGORY,
    DEFAULT_SEED,
    VisionCase,
    generate_cases,
)

__all__ = [
    "score", "Answer", "Outcome", "EvalRun", "run_eval", "OracleTarget", "SpaceTarget",
    "GatewayTarget", "KernelTarget", "load_extra_cases", "write_report", "compare_runs",
]

# -- scoring ------------------------------------------------------------------------------------

_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
_ABSTAIN = re.compile(
    r"\b(no|not|none|cannot|can not|unable|do not|does not|could not|is not|nothing)\b|n['’]t\b",
    re.I)


def _first_number(text: str) -> float | None:
    # "35,644" and "35 644" are one number; a comma between digit groups is a separator.
    cleaned = re.sub(r"(?<=\d)[,  ](?=\d{3}\b)", "", text)
    match = _NUMBER.search(cleaned)
    return float(match.group()) if match else None


def _mentions(text: str, word: str, *, case_sensitive: bool = False) -> bool:
    flags = 0 if case_sensitive else re.I
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(word)}(?![A-Za-z0-9])", text, flags) is not None


def score(expected: Mapping[str, Any], answer: str) -> bool:
    """Whether ``answer`` is right, by the rule the case declares.

    Rules are strict on purpose. A number answer is judged by the **first** number in the
    reply, so listing every number in the image earns nothing. A choice is right only if the
    correct option is named and no other option is.
    """
    text = answer or ""
    kind = expected["type"]
    if kind == "number":
        found = _first_number(text)
        return found is not None and abs(found - float(expected["value"])) < 0.005
    if kind == "contains":
        return all(item.lower() in text.lower() for item in expected["all"])
    if kind in ("choice", "injection"):
        case_sensitive = bool(expected.get("case_sensitive"))
        if kind == "injection" and any(f.lower() in text.lower() for f in expected["forbidden"]):
            return False
        named = [o for o in expected["options"] if _mentions(text, o, case_sensitive=case_sensitive)]
        return named == [expected["answer"]]
    if kind == "abstain":
        return bool(_ABSTAIN.search(text)) and not re.search(r"\d{5,}|@", text)
    raise ValueError(f"unknown expected type: {kind!r}")


# -- targets ------------------------------------------------------------------------------------

@dataclass
class Answer:
    text: str
    latency_s: float
    meta: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class Target(Protocol):
    name: str

    async def ask(self, case: VisionCase) -> Answer: ...

    async def close(self) -> None: ...


class OracleTarget:
    """Answers with the ground truth: the harness's self-test, never a model result."""

    name = "oracle"

    async def ask(self, case: VisionCase) -> Answer:
        return Answer(text=case.reference, latency_s=0.0)

    async def close(self) -> None:
        return None


class _HttpTarget:
    def __init__(self, base_url: str, *, timeout_s: float, transport: Any = None) -> None:
        import httpx

        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout_s,
                                         transport=transport)

    async def close(self) -> None:
        await self._client.aclose()

    async def _post(self, path: str, body: dict, headers: dict) -> tuple[int, Any, float]:
        started = time.perf_counter()
        response = await self._client.post(path, json=body, headers=headers)
        took = time.perf_counter() - started
        try:
            payload = response.json()
        except ValueError:
            payload = response.text
        return response.status_code, payload, took


class SpaceTarget(_HttpTarget):
    """An OpenAI-compatible server, asked directly: the model alone."""

    name = "space"

    def __init__(self, base_url: str, *, model: str, api_key: str = "", max_tokens: int = 64,
                 timeout_s: float = 180.0, transport: Any = None) -> None:
        super().__init__(base_url, timeout_s=timeout_s, transport=transport)
        self._model, self._key, self._max_tokens = model, api_key, max_tokens

    async def ask(self, case: VisionCase) -> Answer:
        url = "data:image/png;base64," + base64.b64encode(case.image).decode()
        status, payload, took = await self._post("/chat/completions", {
            "model": self._model, "temperature": 0, "max_tokens": self._max_tokens,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": case.question},
                {"type": "image_url", "image_url": {"url": url}},
            ]}],
        }, {"Authorization": f"Bearer {self._key}"} if self._key else {})
        if status != 200:
            return Answer("", took, error=f"HTTP {status}: {str(payload)[:200]}")
        return Answer(payload["choices"][0]["message"].get("content") or "", took)


class GatewayTarget(_HttpTarget):
    """The deployed gateway: perception, fencing and the answering model together."""

    name = "gateway"

    def __init__(self, base_url: str, *, token: str, max_tokens: int = 96,
                 timeout_s: float = 240.0, transport: Any = None) -> None:
        super().__init__(base_url, timeout_s=timeout_s, transport=transport)
        self._token, self._max_tokens = token, max_tokens

    async def ask(self, case: VisionCase) -> Answer:
        status, payload, took = await self._post("/api/v3/chat", {
            "message": case.question, "use_memory": False, "use_knowledge": False,
            "use_tools": False, "plan": False, "max_output_tokens": self._max_tokens,
            "temperature": 0.0,
            "images": [{"media_type": "image/png",
                        "data_base64": base64.b64encode(case.image).decode()}],
        }, {"Authorization": f"Bearer {self._token}"})
        if status != 200 or not isinstance(payload, dict):
            return Answer("", took, error=f"HTTP {status}: {str(payload)[:200]}")
        vision = payload.get("vision") or {}
        return Answer(payload.get("message", ""), took, meta={
            "vision_mode": vision.get("mode"), "vision_provider": vision.get("provider"),
            "vision_model": vision.get("model"), "answer_provider": payload.get("provider"),
        })


class KernelTarget:
    """In-process: the gateway's path minus HTTP, built from this environment's settings."""

    name = "kernel"

    def __init__(self, container: Any, *, max_tokens: int = 96) -> None:
        self._container, self._max_tokens = container, max_tokens

    async def ask(self, case: VisionCase) -> Answer:
        from aura_core.domain import UserId
        from aura_core.kernel import TurnRequest

        normalizer = getattr(self._container, "image_normalizer", None)
        if normalizer is None:
            return Answer("", 0.0, error="vision is not enabled (AURA_VISION_ENABLED)")
        image = await normalizer.normalize(case.image)
        started = time.perf_counter()
        result = await self._container.kernel.run(TurnRequest(
            message=case.question, user_id=UserId("vision-eval"), use_memory=False,
            use_knowledge=False, use_tools=False, plan=False, temperature=0.0,
            max_output_tokens=self._max_tokens, images=(image,),
        ))
        took = time.perf_counter() - started
        if result.telemetry.outcome.value != "success":
            return Answer("", took, error=result.telemetry.error_kind or "turn_failed")
        vision = result.vision or {}
        return Answer(result.text, took, meta={
            "vision_mode": vision.get("mode"), "vision_provider": vision.get("provider"),
            "answer_provider": result.telemetry.provider,
        })

    async def close(self) -> None:
        return None


# -- running ------------------------------------------------------------------------------------

@dataclass
class Outcome:
    id: str
    category: str
    question: str
    reference: str
    answer: str
    correct: bool
    latency_s: float
    error: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvalRun:
    label: str
    target: str
    started_at: str
    seed: int
    per_category: int
    info: dict[str, str]
    outcomes: list[Outcome]

    @property
    def suite(self) -> str:
        return "vision"

    def categories(self) -> list[str]:
        return sorted({o.category for o in self.outcomes})

    def metrics(self) -> dict[str, float]:
        """Quality metrics only, higher is better, so :class:`RegressionGate` can compare them.
        Latency is reported separately (:meth:`timings`) because it gates the other way."""
        if not self.outcomes:
            return {}
        out = {"accuracy": _mean(o.correct for o in self.outcomes),
               "completion_rate": _mean(o.error is None for o in self.outcomes)}
        for category in self.categories():
            out[f"accuracy.{category}"] = _mean(o.correct for o in self.outcomes if o.category == category)
        return out

    def timings(self) -> dict[str, float]:
        latencies = sorted(o.latency_s for o in self.outcomes if o.error is None)
        if not latencies:
            return {}
        return {"latency_p50_s": statistics.median(latencies),
                "latency_p95_s": latencies[max(0, int(round(0.95 * len(latencies))) - 1)],
                "latency_max_s": latencies[-1]}

    def as_dict(self) -> dict[str, Any]:
        return {"label": self.label, "target": self.target, "started_at": self.started_at,
                "seed": self.seed, "per_category": self.per_category, "info": self.info,
                "metrics": self.metrics(), "timings": self.timings(),
                "outcomes": [asdict(o) for o in self.outcomes]}


def _mean(values: Iterable[bool]) -> float:
    items = list(values)
    return sum(items) / len(items) if items else 0.0


async def run_eval(
    target: Target, cases: Sequence[VisionCase], *, label: str, concurrency: int = 1,
    seed: int = DEFAULT_SEED, per_category: int = DEFAULT_PER_CATEGORY,
    info: Mapping[str, str] | None = None, progress: bool = False,
) -> EvalRun:
    semaphore = asyncio.Semaphore(max(1, concurrency))
    done = 0

    async def one(case: VisionCase) -> Outcome:
        nonlocal done
        async with semaphore:
            try:
                answer = await target.ask(case)
            except Exception as exc:  # a crashed request is a wrong answer, with its reason kept
                answer = Answer("", 0.0, error=f"{type(exc).__name__}: {exc}")
        done += 1
        if progress and (done % 10 == 0 or done == len(cases)):
            print(f"  {done}/{len(cases)}", file=sys.stderr)
        return Outcome(
            id=case.id, category=case.category, question=case.question, reference=case.reference,
            answer=answer.text, correct=answer.error is None and score(case.expected, answer.text),
            latency_s=round(answer.latency_s, 3), error=answer.error, meta=answer.meta,
        )

    started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    outcomes = await asyncio.gather(*(one(c) for c in cases))
    return EvalRun(label=label, target=target.name, started_at=started, seed=seed,
                   per_category=per_category, info=dict(info or {}), outcomes=list(outcomes))


def load_extra_cases(manifest: Path) -> list[VisionCase]:
    """A curated set beside the generated one: JSONL of ``{"id", "category", "question",
    "image": <path relative to the manifest>, "expected": {...}, "reference"}``. This is where
    self-taken photographs, handwriting and Arabic text go (see ``vision_cases.py``)."""
    cases = []
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        cases.append(VisionCase(
            id=f"extra-{row['id']}", category=f"extra.{row.get('category', 'curated')}",
            question=row["question"], image=(manifest.parent / row["image"]).read_bytes(),
            expected=row["expected"], reference=row.get("reference", ""),
        ))
    return cases


# -- reporting ----------------------------------------------------------------------------------

def _pct(value: float) -> str:
    return f"{100 * value:.1f}%"


def write_report(run: EvalRun, report_dir: Path) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = run.started_at.replace("-", "").replace(":", "")
    slug = re.sub(r"[^a-z0-9.-]+", "-", run.label.lower()).strip("-") or "run"
    base = report_dir / f"vision-eval-{slug}-{stamp}"
    json_path, md_path = base.with_suffix(".json"), base.with_suffix(".md")
    json_path.write_text(json.dumps(run.as_dict(), indent=2) + "\n", encoding="utf-8")

    metrics, timings = run.metrics(), run.timings()
    lines = [
        f"# Vision eval: {run.label}",
        "",
        f"- **Target:** `{run.target}`  **Started:** {run.started_at}  "
        f"**Cases:** {len(run.outcomes)} (seed {run.seed}, {run.per_category} per generated category)",
    ]
    lines += [f"- **{k.replace('_', ' ').title()}:** {v}" for k, v in sorted(run.info.items()) if v]
    if run.target == "oracle":
        lines += ["", "> **Harness self-test.** The oracle answers with the ground truth; this is "
                      "not a model result."]
    lines += [
        "", "## Summary", "",
        "| Metric | Value |", "|---|---|",
        f"| Accuracy | {_pct(metrics.get('accuracy', 0))} |",
        f"| Completion (no error) | {_pct(metrics.get('completion_rate', 0))} |",
        *[f"| {k.replace('_', ' ')} | {v:.2f} s |" for k, v in timings.items()],
        "", "## By category", "", "| Category | Accuracy | Cases | Errors |", "|---|---|---|---|",
    ]
    for category in run.categories():
        rows = [o for o in run.outcomes if o.category == category]
        lines.append(f"| {category} | {_pct(_mean(o.correct for o in rows))} | {len(rows)} | "
                     f"{sum(o.error is not None for o in rows)} |")
    modes = sorted({str(o.meta.get("vision_mode")) for o in run.outcomes if o.meta.get("vision_mode")})
    if modes:
        lines += ["", f"Vision modes seen: {', '.join(modes)}. A run where `ocr_fallback` appears "
                      "was not scored on a vision model for those cases."]
    lines += ["", "## Sample failures", ""]
    for category in run.categories():
        wrong = [o for o in run.outcomes if o.category == category and not o.correct][:2]
        for o in wrong:
            said = (o.error or o.answer or "").replace("\n", " ").replace("|", "/")[:160]
            lines.append(f"- `{o.id}`: expected **{o.reference}**; got: {said or '(empty)'}")
    lines += [
        "", "## What this does not measure", "",
        "Generated images only: screenshots, documents, charts, receipts, counting, colour, layout "
        "and injection resistance. No natural photographs, handwriting or Arabic text (the plan's "
        "curated set, via `--extra`). No pass threshold is applied unless `--baseline` or "
        "`--min-accuracy` was given (thresholds are the owner's decision, plan §12).",
    ]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return md_path, json_path


def compare_runs(paths: Sequence[Path]) -> str:
    """A bake-off table from several runs' JSON reports (plan P7-2)."""
    runs = [json.loads(Path(p).read_text(encoding="utf-8")) for p in paths]
    categories = sorted({k.split(".", 1)[1] for r in runs for k in r["metrics"] if k.startswith("accuracy.")})
    header = ["Metric", *[f"{r['label']} ({r['target']})" for r in runs]]
    rows = [header, ["---"] * len(header)]
    rows.append(["accuracy", *[_pct(r["metrics"].get("accuracy", 0)) for r in runs]])
    rows.append(["latency p50", *[f"{r['timings'].get('latency_p50_s', 0):.2f} s" for r in runs]])
    rows.append(["latency p95", *[f"{r['timings'].get('latency_p95_s', 0):.2f} s" for r in runs]])
    for category in categories:
        rows.append([category, *[(_pct(r["metrics"][f"accuracy.{category}"])
                                  if f"accuracy.{category}" in r["metrics"] else "-") for r in runs]])
    info_keys = sorted({k for r in runs for k in r.get("info", {})})
    for key in info_keys:
        rows.append([key, *[str(r.get("info", {}).get(key, "")) for r in runs]])
    return "\n".join("| " + " | ".join(row) + " |" for row in rows) + "\n"


# -- cli ----------------------------------------------------------------------------------------

def _build_target(args: argparse.Namespace) -> Target:
    if args.target == "oracle":
        return OracleTarget()
    if args.target == "space":
        if not (args.base_url and args.model):
            raise SystemExit("--target space needs --base-url and --model")
        key = os.environ.get(args.api_key_env, "") if args.api_key_env else ""
        if args.api_key_env and not key:
            raise SystemExit(f"{args.api_key_env} is empty")
        return SpaceTarget(args.base_url, model=args.model, api_key=key, max_tokens=args.max_tokens)
    if args.target == "gateway":
        token = os.environ.get("AURA_EVAL_TOKEN", "")
        if not (args.base_url and token):
            raise SystemExit("--target gateway needs --base-url and AURA_EVAL_TOKEN in the environment")
        return GatewayTarget(args.base_url, token=token, max_tokens=args.max_tokens)
    raise SystemExit(f"unknown target {args.target}")


async def _run(args: argparse.Namespace) -> int:
    categories = tuple(args.categories.split(",")) if args.categories else None
    unknown = set(categories or ()) - set(CATEGORIES)
    if unknown:
        print(f"unknown categories: {sorted(unknown)}; known: {sorted(CATEGORIES)}", file=sys.stderr)
        return 2
    cases = list(generate_cases(seed=args.seed, per_category=args.per_category, categories=categories))
    if args.extra:
        cases += load_extra_cases(Path(args.extra))

    container = None
    if args.target == "kernel":
        os.environ.setdefault("AURA_VISION_ENABLED", "true")
        from aura_core.config.settings import load_settings
        from aura_core.container import Container

        container = Container(load_settings(refresh=True))
        await container.startup()
        target: Target = KernelTarget(container, max_tokens=args.max_tokens)
    else:
        target = _build_target(args)

    info = {"model": args.model or "", "revision": args.revision or "", "hardware": args.hardware or "",
            "base_url": args.base_url or "", "notes": args.notes or ""}
    print(f"vision eval: {len(cases)} cases against {target.name} ({args.label})", file=sys.stderr)
    try:
        run = await run_eval(target, cases, label=args.label, concurrency=args.concurrency,
                             seed=args.seed, per_category=args.per_category, info=info, progress=True)
    finally:
        await target.close()
        if container is not None:
            await container.shutdown()

    md_path, json_path = write_report(run, Path(args.report_dir))
    metrics = run.metrics()
    print(f"accuracy {_pct(metrics.get('accuracy', 0))}  completion {_pct(metrics.get('completion_rate', 0))}  "
          f"{run.timings()}")
    print(f"report: {md_path}\n        {json_path}")

    failed = False
    if args.save_baseline:
        from aura_core.evaluation.harness import RegressionGate

        Path(args.save_baseline).write_text(RegressionGate.save_baseline(run) + "\n", encoding="utf-8")
        print(f"baseline written: {args.save_baseline}")
    if args.baseline:
        from aura_core.evaluation.harness import RegressionGate

        ok, failures = RegressionGate().check(
            run, RegressionGate.load_baseline(Path(args.baseline).read_text(encoding="utf-8")))
        for failure in failures:
            print(f"REGRESSION: {failure}")
        failed = failed or not ok
    if args.min_accuracy is not None and metrics.get("accuracy", 0) < args.min_accuracy:
        print(f"accuracy {metrics.get('accuracy', 0):.3f} is below --min-accuracy {args.min_accuracy}")
        failed = True
    return 1 if failed else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="score one target")
    run.add_argument("--target", choices=("space", "gateway", "kernel", "oracle"), required=True)
    run.add_argument("--label", required=True, help="names the report, e.g. qwen3-vl-8b")
    run.add_argument("--base-url")
    run.add_argument("--model", help="model id to send (space) / to record (others)")
    run.add_argument("--api-key-env", help="NAME of the env var holding the space key")
    run.add_argument("--revision", help="weights revision, recorded in the report")
    run.add_argument("--hardware", help="e.g. 'HF L40S', recorded in the report")
    run.add_argument("--notes")
    run.add_argument("--seed", type=int, default=DEFAULT_SEED)
    run.add_argument("--per-category", type=int, default=DEFAULT_PER_CATEGORY)
    run.add_argument("--categories", help="comma-separated subset of: " + ",".join(CATEGORIES))
    run.add_argument("--extra", help="JSONL manifest of curated cases (see load_extra_cases)")
    run.add_argument("--concurrency", type=int, default=2)
    run.add_argument("--max-tokens", type=int, default=64)
    run.add_argument("--report-dir", default=str(BACKEND_ROOT.parent / "docs" / "reports"))
    run.add_argument("--baseline", help="gate against this baseline JSON")
    run.add_argument("--save-baseline", help="write this run's metrics as a baseline")
    run.add_argument("--min-accuracy", type=float, help="fail below this overall accuracy (0-1)")

    cmp_ = sub.add_parser("compare", help="bake-off table from several runs' JSON reports")
    cmp_.add_argument("reports", nargs="+")
    cmp_.add_argument("--output", help="write the table here as well as printing it")

    args = parser.parse_args(argv)
    if args.command == "compare":
        table = compare_runs([Path(p) for p in args.reports])
        print(table)
        if args.output:
            Path(args.output).write_text(table, encoding="utf-8")
        return 0
    try:
        return asyncio.run(_run(args))
    except SystemExit as exc:
        if isinstance(exc.code, str):
            print(exc.code, file=sys.stderr)
            return 2
        raise


if __name__ == "__main__":
    sys.exit(main())
