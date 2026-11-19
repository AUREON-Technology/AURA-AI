#!/usr/bin/env python3
"""Retrieval quality per embedder on English, Arabic, and Arabic-asks-English suites.

The evidence behind switching the embedder (docs/plans/arabic-language_plan.md, stage 4, owner
decision 10): the switch is made only if English stays within 1 nDCG point of the current model
on the phase-a3 corpus (the R2.4 rule) and Arabic improves. Each embedder gets a fresh in-memory
container, the same ``RetrievalEvaluator`` (k=5) the regression gate uses, and three suites:

* ``docs_corpus``     -- 23 English questions over the architecture ADRs (phase-a3);
* ``docs_corpus_ar``  -- the same 23 asked in Arabic over the same English ADRs (an Arabic
                         speaker searching English documents, the common case);
* ``arabic_corpus``   -- Arabic questions over an Arabic document set.

Usage (from aura_backend/; downloads the models on first use)::

    python evaluation/multilingual_retrieval_eval.py --out report.json \\
        --embedder hash --embedder BAAI/bge-small-en-v1.5 --embedder intfloat/multilingual-e5-small \\
        [--reranker heuristic --reranker cross-encoder/mmarco-mMiniLMv2-L12-H384-v1] \\
        [--threshold 0.35 --threshold 0.0]

Prints one JSON object: metrics per embedder per suite.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_SUITES = Path(__file__).resolve().parent / "suites"


def _corpora() -> dict[str, list[tuple[str, str, str]]]:
    from evaluation.suites.arabic_corpus_sources import load_arabic_corpus_sources
    from evaluation.suites.docs_corpus_sources import load_docs_corpus_sources

    return {"adr": list(load_docs_corpus_sources()), "arabic": list(load_arabic_corpus_sources())}


#: suite name -> (case file, corpus it is asked over)
SUITES = {
    "docs_corpus": ("docs_corpus.jsonl", "adr"),
    "docs_corpus_ar": ("docs_corpus_ar.jsonl", "adr"),
    "arabic_corpus": ("arabic_corpus.jsonl", "arabic"),
}


def _configure(embedder: str, reranker: str, threshold: float | None) -> None:
    for key in ("AURA_EMBEDDING_QUERY_PREFIX", "AURA_EMBEDDING_DOCUMENT_PREFIX"):
        os.environ.pop(key, None)
    # The relevance gate is the *reranker's* score (KnowledgeEngine._rerank): "heuristic" is
    # lexical overlap, so an Arabic question about an English document cannot pass it whatever
    # the embedder found; a multilingual cross-encoder scores the pair itself (raw logits, so it
    # needs its own threshold).
    if reranker == "heuristic":
        os.environ["AURA_KNOWLEDGE_RERANK_PROVIDER"] = "heuristic"
    elif reranker.startswith("either:"):
        # The combined gate (EitherGateReranker): heuristic OR the named cross-encoder.
        os.environ["AURA_KNOWLEDGE_RERANK_PROVIDER"] = "either"
        os.environ["AURA_KNOWLEDGE_RERANK_MODEL"] = reranker.removeprefix("either:")
    else:
        os.environ["AURA_KNOWLEDGE_RERANK_PROVIDER"] = "cross-encoder"
        os.environ["AURA_KNOWLEDGE_RERANK_MODEL"] = reranker
    if threshold is None:
        os.environ.pop("AURA_KNOWLEDGE_SCORE_THRESHOLD", None)
    else:
        os.environ["AURA_KNOWLEDGE_SCORE_THRESHOLD"] = str(threshold)
    if embedder == "hash":
        os.environ["AURA_EMBEDDING_PROVIDER"] = "local-embeddings"
        os.environ["AURA_EMBEDDING_MODEL"] = "hash-embed-v1"
    else:
        os.environ["AURA_EMBEDDING_PROVIDER"] = "sentence-transformers"
        os.environ["AURA_EMBEDDING_MODEL"] = embedder
    os.environ["AURA_EMBEDDING_DIMENSION"] = "384"


async def _score(
    embedder: str, reranker: str, threshold: float | None,
    corpus: list[tuple[str, str, str]], case_file: str, k: int,
) -> dict:
    from aura_core.config.settings import load_settings
    from aura_core.container import Container
    from aura_core.domain import DocumentId
    from aura_core.evaluation.harness import EvalSuite, RetrievalEvaluator

    _configure(embedder, reranker, threshold)
    container = Container(load_settings(refresh=True, validate=False))
    await container.startup()
    try:
        for document_id, title, content in corpus:
            await container.knowledge.ingest(
                content=content, title=title, document_id=DocumentId(document_id), owner_id=None,
            )
        lines = (_SUITES / case_file).read_text(encoding="utf-8").splitlines()
        suite = EvalSuite.from_jsonl(case_file.removesuffix(".jsonl"), lines)
        result = await RetrievalEvaluator(container.knowledge, k=k).run(suite)
        return {name: round(value, 4) for name, value in result.metrics().items()}
    finally:
        await container.shutdown()


async def run(
    embedders: list[str], rerankers: list[str], thresholds: list[float | None], k: int,
) -> dict:
    corpora = _corpora()
    report: dict[str, dict] = {}
    for embedder in embedders:
        for reranker in rerankers:
            for threshold in thresholds:
                label = f"{embedder} | {reranker} | threshold={threshold if threshold is not None else 'default'}"
                report[label] = {}
                for suite, (case_file, corpus) in SUITES.items():
                    report[label][suite] = await _score(
                        embedder, reranker, threshold, corpora[corpus], case_file, k
                    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--embedder", action="append", required=True,
                        help="'hash' or a sentence-transformers model id; repeatable")
    parser.add_argument("--reranker", action="append", default=None,
                        help="'heuristic' (default), a cross-encoder model id, or "
                             "'either:<model id>' for the combined gate; repeatable")
    parser.add_argument("--threshold", action="append", type=float, default=None,
                        help="relevance threshold on the reranker's score; repeatable "
                             "(default: the deployment's AURA_KNOWLEDGE_SCORE_THRESHOLD)")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--out", type=Path, default=None,
                        help="write the JSON here (stdout also carries the container's logs)")
    args = parser.parse_args(argv)
    report = asyncio.run(run(
        args.embedder, args.reranker or ["heuristic"], args.threshold or [None], args.k,
    ))
    text = json.dumps(report, indent=2)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
