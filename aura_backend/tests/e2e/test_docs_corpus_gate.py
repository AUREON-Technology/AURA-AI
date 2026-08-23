"""Phase A3: retrieval regression gate over a real, labelled corpus.

The corpus is the repository's own architecture ADRs (``docs/architecture/*.md``), ingested
whole, with graded queries in ``evaluation/suites/docs_corpus.jsonl`` asserting which document
should come back for each. This is retrieval-only: :class:`AnswerEvaluator`'s content assertions
need a model that answers from context, and the default ``EchoProvider`` echoes the user's last
message instead, so gating on answer content would be gating on noise. Answer-level gating is a
follow-up once a real (or scripted) provider is wired into CI.

The recorded baseline is genuinely imperfect (recall@5 around 0.65, not near-1.0): the default
embedder is ``HashEmbeddingProvider`` (deterministic n-gram hashing, explicitly non-semantic --
see its docstring), so a natural paraphrase of an ADR sentence does not always out-rank the other
nine documents on lexical overlap alone. That is the honest number, not a bug in the corpus --
inflating queries into near-verbatim copies to force a green run would defeat the point of
measuring. The gate exists to hold this number steady and catch regressions below it; raising it
for real is future work (a semantic embedder swaps in cleanly through ADR-007's index-alias
switch).

Metrics and gate logic are the harness's (``aura_core/evaluation/harness.py``); this file adds no
new metric, only the corpus and the wiring.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aura_core.domain import DocumentId
from aura_core.evaluation.harness import EvalSuite, RegressionGate, RetrievalEvaluator

from evaluation.suites.docs_corpus_sources import load_docs_corpus_sources

_SUITE_DIR = Path(__file__).resolve().parents[2] / "evaluation" / "suites"
_BASELINE_PATH = _SUITE_DIR / "docs_corpus_baseline.json"


@pytest.fixture
async def docs_corpus_index(container):
    """A container with every architecture ADR ingested as a global document."""
    await container.startup()
    for document_id, title, content in load_docs_corpus_sources():
        await container.knowledge.ingest(
            content=content, title=title, document_id=DocumentId(document_id), owner_id=None,
        )
    return container


@pytest.fixture
def docs_corpus_suite() -> EvalSuite:
    lines = (_SUITE_DIR / "docs_corpus.jsonl").read_text(encoding="utf-8").splitlines()
    return EvalSuite.from_jsonl("docs_corpus", lines)


class TestDocsCorpusRetrievalGate:
    async def test_corpus_loads_ten_documents(self) -> None:
        assert len(load_docs_corpus_sources()) == 10

    async def test_suite_has_grounded_and_out_of_corpus_cases(self, docs_corpus_suite) -> None:
        assert len(docs_corpus_suite) == 23
        grounded = [c for c in docs_corpus_suite.cases if c.relevant_documents]
        empty = [c for c in docs_corpus_suite.cases if not c.relevant_documents]
        assert len(grounded) == 20
        assert len(empty) == 3

    async def test_retrieval_meets_the_recorded_baseline(
        self, docs_corpus_index, docs_corpus_suite
    ) -> None:
        """The CI gate: fails the build if retrieval quality drops beyond tolerance.

        The baseline is a real number from a real run (``docs_corpus_baseline.json``), not an
        aspirational target -- see ``RegressionGate.save_baseline`` and the harness docstring.
        """
        if not _BASELINE_PATH.exists():
            pytest.fail(
                f"no baseline at {_BASELINE_PATH}; generate one with "
                "RegressionGate.save_baseline() from a genuine local run before gating CI on it"
            )

        evaluator = RetrievalEvaluator(docs_corpus_index.knowledge, k=5)
        result = await evaluator.run(docs_corpus_suite)

        baseline = RegressionGate.load_baseline(_BASELINE_PATH.read_text(encoding="utf-8"))
        gate = RegressionGate()
        ok, failures = gate.check(result, baseline)

        assert ok, "retrieval regression vs. baseline:\n" + "\n".join(failures)
