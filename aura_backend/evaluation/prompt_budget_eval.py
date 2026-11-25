#!/usr/bin/env python3
"""How much of the evidence a question needs actually reaches the model's prompt (WP2, D-05).

Retrieval is scored on document ids (recall@k), so it cannot see the three budgets that sit
between a retrieved chunk and the model: ``AURA_KNOWLEDGE_TOP_K`` (how many chunks),
``AURA_KNOWLEDGE_PROMPT_CHUNK_CHARS`` (how much of each) and ``AURA_LLM_MAX_PROMPT_TOKENS`` (how
much of everything, history and memory included). This runs the real kernel turn over a grid of
those three, with a provider that records the fenced knowledge it was shown, and scores:

* ``evidence_in_prompt`` -- the share of answerable questions whose ``expected_substrings`` appear
  in the knowledge text of the rendered prompt;
* ``retrieved_hit`` -- the share whose expected document was retrieved at all (the ceiling the
  first number can reach);
* ``prompt_tokens`` -- mean and p95 of the turn's measured prompt size.

Same deterministic substrate as the regression gate: the hash embedder, the heuristic reranker, the
repository's own ADRs. It measures the budgets, not the embedder; a semantic embedder moves
``retrieved_hit`` and leaves the gap between the two numbers where it is.

Usage (from aura_backend/)::

    python evaluation/prompt_budget_eval.py --out report.json
    python evaluation/prompt_budget_eval.py --cap 400 --cap 3000 --top-k 2 --top-k 5 --chars 280 --chars 1000
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import os
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_SUITES = Path(__file__).resolve().parent / "suites"

#: Today's defaults first, so the first row of a report is the baseline.
DEFAULT_CAPS = (400, 1000, 3000)
DEFAULT_TOP_K = (2, 4, 6)
DEFAULT_CHARS = (280, 600, 1200)


def _configure(cap: int, top_k: int, chars: int) -> None:
    os.environ["AURA_EMBEDDING_PROVIDER"] = "local-embeddings"
    os.environ["AURA_EMBEDDING_MODEL"] = "hash-embed-v1"
    os.environ["AURA_EMBEDDING_DIMENSION"] = "384"
    os.environ["AURA_KNOWLEDGE_RERANK_PROVIDER"] = "heuristic"
    os.environ.pop("AURA_KNOWLEDGE_SCORE_THRESHOLD", None)
    os.environ["AURA_LLM_MAX_PROMPT_TOKENS"] = str(cap)
    os.environ["AURA_KNOWLEDGE_TOP_K"] = str(top_k)
    os.environ["AURA_KNOWLEDGE_PROMPT_CHUNK_CHARS"] = str(chars)


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))]


async def score_budget(cap: int, top_k: int, chars: int) -> dict[str, float]:
    """Run the docs-corpus suite once under one budget and return the three measures."""
    from aura_core.config.settings import load_settings
    from aura_core.container import Container
    from aura_core.domain import DocumentId, UserId
    from aura_core.evaluation.harness import EvalSuite
    from aura_core.kernel.kernel import TurnRequest
    from aura_core.llm.providers.echo import ContextGroundedProvider

    from evaluation.suites.docs_corpus_sources import load_docs_corpus_sources

    class _Recording(ContextGroundedProvider):
        """Keeps the fenced knowledge each completion request carried."""

        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.fenced: list[str] = []

        def _derive(self, request) -> str:
            self.fenced.extend(
                m.content for m in request.conversation.system_messages if "<<<AURA-DATA:" in m.content
            )
            return super()._derive(request)

    _configure(cap, top_k, chars)
    container = Container(load_settings(refresh=True, validate=False))
    await container.startup()
    try:
        for document_id, title, content in load_docs_corpus_sources():
            await container.knowledge.ingest(
                content=content, title=title, document_id=DocumentId(document_id), owner_id=None,
            )
        recorder = _Recording(name="echo", model="echo-1")
        container.router._providers["echo"] = recorder

        lines = (_SUITES / "docs_corpus.jsonl").read_text(encoding="utf-8").splitlines()
        suite = EvalSuite.from_jsonl("docs_corpus", lines)
        answerable = [c for c in suite.cases if c.relevant_documents and c.expected_substrings]

        in_prompt = hit = 0
        prompt_tokens: list[float] = []
        for case in answerable:
            recorder.fenced.clear()
            retrieval = await container.knowledge.retrieve(case.query, owner_id=None)
            retrieved_docs = {c.chunk.document_id.value for c in retrieval.chunks}
            hit += bool(retrieved_docs & set(case.relevant_documents))
            result = await container.kernel.run(
                TurnRequest(message=case.query, user_id=UserId("eval-user"), route="eval")
            )
            prompt_tokens.append(float(result.telemetry.prompt_tokens))
            shown = "\n".join(recorder.fenced).lower()
            in_prompt += any(s.lower() in shown for s in case.expected_substrings)

        total = len(answerable) or 1
        return {
            "questions": float(len(answerable)),
            "retrieved_hit": round(hit / total, 4),
            "evidence_in_prompt": round(in_prompt / total, 4),
            "prompt_tokens_mean": round(statistics.fmean(prompt_tokens), 1) if prompt_tokens else 0.0,
            "prompt_tokens_p95": round(_percentile(prompt_tokens, 0.95), 1),
        }
    finally:
        await container.shutdown()


async def run(caps: list[int], top_ks: list[int], chars: list[int]) -> dict[str, dict[str, float]]:
    report: dict[str, dict[str, float]] = {}
    for cap, top_k, width in itertools.product(caps, top_ks, chars):
        report[f"cap={cap} top_k={top_k} chars={width}"] = await score_budget(cap, top_k, width)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--cap", action="append", type=int, default=None,
                        help="AURA_LLM_MAX_PROMPT_TOKENS values; repeatable")
    parser.add_argument("--top-k", action="append", type=int, default=None,
                        help="AURA_KNOWLEDGE_TOP_K values; repeatable")
    parser.add_argument("--chars", action="append", type=int, default=None,
                        help="AURA_KNOWLEDGE_PROMPT_CHUNK_CHARS values; repeatable")
    parser.add_argument("--out", type=Path, default=None, help="write the JSON here")
    args = parser.parse_args(argv)
    report = asyncio.run(run(
        args.cap or list(DEFAULT_CAPS), args.top_k or list(DEFAULT_TOP_K), args.chars or list(DEFAULT_CHARS),
    ))
    text = json.dumps(report, indent=2)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
