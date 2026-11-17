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

    python evaluation/multilingual_retrieval_eval.py \\
        --embedder hash --embedder BAAI/bge-small-en-v1.5 --embedder intfloat/multilingual-e5-small

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


def _configure(embedder: str) -> None:
    for key in ("AURA_EMBEDDING_QUERY_PREFIX", "AURA_EMBEDDING_DOCUMENT_PREFIX"):
        os.environ.pop(key, None)
    if embedder == "hash":
        os.environ["AURA_EMBEDDING_PROVIDER"] = "local-embeddings"
        os.environ["AURA_EMBEDDING_MODEL"] = "hash-embed-v1"
    else:
        os.environ["AURA_EMBEDDING_PROVIDER"] = "sentence-transformers"
        os.environ["AURA_EMBEDDING_MODEL"] = embedder
    os.environ["AURA_EMBEDDING_DIMENSION"] = "384"


async def _score(embedder: str, corpus: list[tuple[str, str, str]], case_file: str, k: int) -> dict:
    from aura_core.config.settings import load_settings
    from aura_core.container import Container
    from aura_core.domain import DocumentId
    from aura_core.evaluation.harness import EvalSuite, RetrievalEvaluator

    _configure(embedder)
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


async def run(embedders: list[str], k: int) -> dict:
    corpora = _corpora()
    report: dict[str, dict] = {}
    for embedder in embedders:
        report[embedder] = {}
        for suite, (case_file, corpus) in SUITES.items():
            report[embedder][suite] = await _score(embedder, corpora[corpus], case_file, k)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--embedder", action="append", required=True,
                        help="'hash' or a sentence-transformers model id; repeatable")
    parser.add_argument("--k", type=int, default=5)
    args = parser.parse_args(argv)
    print(json.dumps(asyncio.run(run(args.embedder, args.k)), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
