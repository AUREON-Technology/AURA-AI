"""The ten retrieval strategies.

Each strategy's docstring makes claims; these check them. The cases worth having are the ones where
a strategy must *not* do the obvious thing:

* Multi-Query and HyDE must degrade to their inner strategy when there is no generation budget,
  rather than failing or silently skipping retrieval.
* Corrective must not loop, and must return the evidence it found even when grading it insufficient
  -- a partial answer with a stated gap beats a refusal.
* Self-RAG must distinguish "did not look" from "looked and found nothing".
* Memory must keep memories out of ``chunks``, or a user's own words get cited as a document.
* The adaptive router must survive one strategy failing.
* Multimodal must be honest that no extraction pipeline exists behind it.

Fakes throughout: no index, no model, no network. That is the same property the rest of this suite
has, and it is what lets these run in seconds.
"""

from __future__ import annotations

import pytest

from aura_core.domain.identifiers import ChunkId, DocumentId, MemoryId, UserId
from aura_core.domain.knowledge import Chunk, RetrievalResult, RetrievedChunk
from aura_core.domain.memory import MemoryKind, MemoryRecord, Provenance
from aura_core.rag import (
    AdaptiveRouter,
    AgenticRAG,
    CorrectiveRAG,
    EvidenceGrade,
    GraphRAG,
    HyDERAG,
    MemoryRAG,
    MultiQueryRAG,
    MultimodalRAG,
    QueryClass,
    QueryRewriter,
    RetrievalRequest,
    SelfRAG,
    SimpleRAG,
    STRATEGY_NAMES,
    build_strategies,
    classify_query,
    fuse_results,
    grade_evidence,
)

OWNER = UserId("u1")


# -- fakes --------------------------------------------------------------------------------


def _chunk(chunk_id: str, content: str, score: float = 0.8) -> RetrievedChunk:
    return RetrievedChunk(
        chunk=Chunk(
            id=ChunkId(chunk_id),
            document_id=DocumentId(chunk_id.split(":")[0]),
            document_version=1,
            index=0,
            content=content,
        ),
        score=score,
    )


def _result(*chunks: RetrievedChunk, considered: int | None = None, query: str = "q") -> RetrievalResult:
    return RetrievalResult(
        chunks=tuple(chunks),
        query=query,
        candidates_considered=considered if considered is not None else len(chunks),
    )


class FakeKnowledge:
    """Returns a canned result, recording every query and filter it was asked for.

    Honours a ``modality`` filter, because MultimodalRAG's whole behaviour depends on the first
    (filtered) pass coming back empty on a corpus with no extracted media. A fake that ignored the
    filter would make that strategy untestable -- and would have quietly reported it as working.
    ``media`` holds the results a filtered query is allowed to see.
    """

    def __init__(self, result: RetrievalResult | None = None) -> None:
        self.queries: list[str] = []
        self.filters: list[dict] = []
        self._result = result
        self.per_query: dict[str, RetrievalResult] = {}
        self.media: dict[str, RetrievalResult] = {}

    async def retrieve(self, query, *, owner_id=None, limit=None, include_global=True,
                       score_threshold=None, filters=None):
        self.queries.append(query)
        applied = dict(filters or {})
        self.filters.append(applied)
        if "modality" in applied:
            return self.media.get(query, RetrievalResult(query=query, candidates_considered=0))
        if query in self.per_query:
            return self.per_query[query]
        if self._result is not None:
            return RetrievalResult(
                chunks=self._result.chunks,
                query=query,
                candidates_considered=self._result.candidates_considered,
                below_threshold=self._result.below_threshold,
            )
        return RetrievalResult(query=query, candidates_considered=0)


class FakeRouter:
    """One canned completion, or an exception."""

    def __init__(self, text: str = "", *, fail: bool = False) -> None:
        self.text = text
        self.fail = fail
        self.calls = 0

    async def complete(self, request, *, budget=None):
        self.calls += 1
        if self.fail:
            raise RuntimeError("no provider available")

        class R:
            pass

        r = R()
        r.text = self.text
        r.provider = "fake"
        r.model = "fake"
        r.usage = None
        return r


class FakeMemory:
    def __init__(self, contents=(), *, fail: bool = False) -> None:
        self._contents = contents
        self.fail = fail
        self.queries: list[str] = []

    async def recall(self, query):
        self.queries.append(query.text)
        if self.fail:
            raise RuntimeError("memory store down")

        class Hit:
            def __init__(self, content):
                self.record = MemoryRecord(
                    id=MemoryId(f"m-{abs(hash(content)) % 999}"),
                    kind=MemoryKind.SEMANTIC,
                    owner_id=OWNER,
                    content=content,
                    provenance=Provenance(source="conversation"),
                )
                self.similarity = 0.8
                self.score = 0.8

            @property
            def content(self):
                return self.record.content

        return [Hit(c) for c in self._contents]


class FakeEntity:
    def __init__(self, key: str) -> None:
        self.key = key


class FakeEdge:
    def __init__(self, subject: str, predicate: str, obj: str) -> None:
        self.subject = subject
        self.predicate = predicate
        self.object = obj


class FakeGraph:
    def __init__(self, entities=(), edges=None, *, fail: bool = False) -> None:
        self._entities = [FakeEntity(e) for e in entities]
        self._edges = edges or {}
        self.fail = fail

    async def find_entities(self, query, *, owner_id=None, limit=10):
        if self.fail:
            raise RuntimeError("graph store down")
        return self._entities[:limit]

    async def neighbours(self, key, *, owner_id=None):
        return [FakeEdge(*e) for e in self._edges.get(key, [])]


class FailingStrategy:
    name = "failing"

    async def retrieve(self, request):
        raise RuntimeError("strategy exploded")


# -- simple -------------------------------------------------------------------------------


class TestSimpleRAG:
    async def test_it_delegates_to_the_knowledge_engine(self) -> None:
        knowledge = FakeKnowledge(_result(_chunk("d1:v1:0", "refund policy text")))
        outcome = await SimpleRAG(knowledge).retrieve(
            RetrievalRequest(query="refund policy", owner_id=OWNER)
        )
        assert knowledge.queries == ["refund policy"]
        assert len(outcome.chunks) == 1
        assert outcome.trace.name == "simple"

    async def test_an_empty_corpus_gives_an_empty_outcome(self) -> None:
        outcome = await SimpleRAG(FakeKnowledge()).retrieve(RetrievalRequest(query="anything"))
        assert not outcome
        assert outcome.trace.returned == 0

    async def test_the_trace_records_duration(self) -> None:
        """Cost has to be visible or a slow turn is unattributable."""
        outcome = await SimpleRAG(FakeKnowledge()).retrieve(RetrievalRequest(query="q"))
        assert outcome.trace.duration_ms >= 0.0


# -- memory -------------------------------------------------------------------------------


class TestMemoryRAG:
    async def test_memories_are_kept_out_of_chunks(self) -> None:
        """A memory is the user's own statement. Presenting it as a retrieved chunk would cite the
        corpus for something the user said, which reads as the system inventing a document."""
        knowledge = FakeKnowledge(_result(_chunk("d1:v1:0", "corpus text")))
        strategy = MemoryRAG(FakeMemory(["the user studies CS"]), SimpleRAG(knowledge))
        outcome = await strategy.retrieve(RetrievalRequest(query="what do I study", owner_id=OWNER))

        assert len(outcome.memories) == 1
        assert len(outcome.chunks) == 1
        contents = [c.chunk.content for c in outcome.chunks]
        assert "the user studies CS" not in contents

    async def test_it_skips_recall_without_an_owner_and_says_so(self) -> None:
        """Memory is owner-scoped by construction. Silently returning nothing would let a caller
        conclude the user has none."""
        memory = FakeMemory(["x"])
        outcome = await MemoryRAG(memory, SimpleRAG(FakeKnowledge())).retrieve(
            RetrievalRequest(query="q", owner_id=None)
        )
        assert memory.queries == []
        assert any("no owner" in n for n in outcome.trace.notes)

    async def test_a_memory_outage_still_returns_corpus_results(self) -> None:
        knowledge = FakeKnowledge(_result(_chunk("d1:v1:0", "corpus text")))
        outcome = await MemoryRAG(FakeMemory(fail=True), SimpleRAG(knowledge)).retrieve(
            RetrievalRequest(query="q", owner_id=OWNER)
        )
        assert len(outcome.chunks) == 1
        assert any("memory unavailable" in n for n in outcome.trace.notes)

    async def test_also_search_false_consults_memory_only(self) -> None:
        knowledge = FakeKnowledge(_result(_chunk("d1:v1:0", "corpus")))
        strategy = MemoryRAG(FakeMemory(["fact"]), SimpleRAG(knowledge), also_search=False)
        outcome = await strategy.retrieve(RetrievalRequest(query="q", owner_id=OWNER))
        assert knowledge.queries == []
        assert len(outcome.memories) == 1


# -- multi-query --------------------------------------------------------------------------


class TestMultiQueryRAG:
    async def test_it_retrieves_for_every_variant(self) -> None:
        knowledge = FakeKnowledge(_result(_chunk("d1:v1:0", "refunds are processed in 14 days")))
        router = FakeRouter("how do I get money back\nrefund timeline\nreturns policy")
        strategy = MultiQueryRAG(SimpleRAG(knowledge), router, variants=3)
        await strategy.retrieve(RetrievalRequest(query="refund policy"))
        # Original plus three variants.
        assert len(knowledge.queries) == 4
        assert "refund policy" in knowledge.queries

    async def test_it_costs_exactly_one_generation(self) -> None:
        """Variants come from a single reply; one generation per turn, not one per variant."""
        router = FakeRouter("a\nb\nc")
        strategy = MultiQueryRAG(SimpleRAG(FakeKnowledge()), router)
        outcome = await strategy.retrieve(RetrievalRequest(query="q"))
        assert router.calls == 1
        assert outcome.trace.generations == 1

    async def test_no_generation_budget_degrades_to_the_inner_strategy(self) -> None:
        knowledge = FakeKnowledge(_result(_chunk("d1:v1:0", "text")))
        router = FakeRouter("unused")
        strategy = MultiQueryRAG(SimpleRAG(knowledge), router)
        outcome = await strategy.retrieve(RetrievalRequest(query="q", max_generations=0))
        assert router.calls == 0
        assert knowledge.queries == ["q"]
        assert any("no generation budget" in n for n in outcome.trace.notes)
        assert len(outcome.chunks) == 1  # retrieval still happened

    async def test_a_failed_expansion_still_retrieves(self) -> None:
        """Losing the enhancement is acceptable; losing retrieval is not."""
        knowledge = FakeKnowledge(_result(_chunk("d1:v1:0", "text")))
        strategy = MultiQueryRAG(SimpleRAG(knowledge), FakeRouter(fail=True))
        outcome = await strategy.retrieve(RetrievalRequest(query="q"))
        assert knowledge.queries == ["q"]
        assert len(outcome.chunks) == 1
        assert any("expansion failed" in n for n in outcome.trace.notes)

    async def test_a_variant_identical_to_the_query_is_not_retrieved_twice(self) -> None:
        knowledge = FakeKnowledge()
        router = FakeRouter("refund policy\nREFUND POLICY\nsomething else")
        strategy = MultiQueryRAG(SimpleRAG(knowledge), router)
        await strategy.retrieve(RetrievalRequest(query="refund policy"))
        assert len(knowledge.queries) == 2  # original + "something else"

    async def test_numbered_and_fenced_output_is_parsed(self) -> None:
        """Local models number inconsistently and sometimes wrap output in a fence. Failing on
        presentation would lose the expansion for a formatting reason."""
        knowledge = FakeKnowledge()
        router = FakeRouter("```\nHere are three versions:\n1. first way\n2) second way\n- third way\n```")
        strategy = MultiQueryRAG(SimpleRAG(knowledge), router, variants=3)
        await strategy.retrieve(RetrievalRequest(query="original"))
        assert "first way" in knowledge.queries
        assert "second way" in knowledge.queries
        assert "third way" in knowledge.queries
        assert not any("Here are three versions" in q for q in knowledge.queries)


# -- hyde ---------------------------------------------------------------------------------


class TestHyDERAG:
    async def test_it_retrieves_with_the_hypothetical_answer(self) -> None:
        knowledge = FakeKnowledge()
        router = FakeRouter("Refunds are issued within 14 days of the return being received.")
        await HyDERAG(SimpleRAG(knowledge), router).retrieve(
            RetrievalRequest(query="how long do refunds take")
        )
        assert any("14 days" in q for q in knowledge.queries)

    async def test_the_original_query_is_retained(self) -> None:
        """If the hypothetical drifts off-topic the original still retrieves."""
        knowledge = FakeKnowledge()
        await HyDERAG(SimpleRAG(knowledge), FakeRouter("invented text")).retrieve(
            RetrievalRequest(query="original question")
        )
        assert "original question" in knowledge.queries

    async def test_the_hypothetical_text_is_never_put_in_the_trace(self) -> None:
        """It is model output derived from a user query and has no place in logs."""
        secret = "Refunds are issued within 14 days."
        outcome = await HyDERAG(SimpleRAG(FakeKnowledge()), FakeRouter(secret)).retrieve(
            RetrievalRequest(query="q")
        )
        rendered = " ".join(outcome.trace.notes)
        assert secret not in rendered
        assert "ch" in rendered  # a length, not the text

    async def test_no_budget_falls_back_to_the_plain_query(self) -> None:
        knowledge = FakeKnowledge()
        router = FakeRouter("unused")
        await HyDERAG(SimpleRAG(knowledge), router).retrieve(
            RetrievalRequest(query="q", max_generations=0)
        )
        assert router.calls == 0
        assert knowledge.queries == ["q"]


# -- grading ------------------------------------------------------------------------------


class TestEvidenceGrading:
    def test_no_chunks_after_searching_is_insufficient(self) -> None:
        grade, reason = grade_evidence("refund policy", _result(considered=12))
        assert grade == EvidenceGrade.INSUFFICIENT
        assert "relevance floor" in reason

    def test_no_candidates_is_reported_differently(self) -> None:
        """A corpus that could not be searched is not the same as one with nothing relevant."""
        grade, reason = grade_evidence("refund policy", _result(considered=0))
        assert grade == EvidenceGrade.INSUFFICIENT
        assert "no candidates" in reason

    def test_good_coverage_and_a_strong_top_score_is_sufficient(self) -> None:
        result = _result(_chunk("d1:v1:0", "our refund policy allows returns within 30 days", 0.9))
        grade, _ = grade_evidence("what is the refund policy", result)
        assert grade == EvidenceGrade.SUFFICIENT

    def test_low_coverage_and_a_weak_score_is_insufficient(self) -> None:
        result = _result(_chunk("d1:v1:0", "the office is open on weekdays", 0.2))
        grade, _ = grade_evidence("what is the refund policy for damaged goods", result)
        assert grade == EvidenceGrade.INSUFFICIENT

    def test_coverage_is_computed_across_chunks_not_per_chunk(self) -> None:
        """A two-topic question is legitimately answered by two passages covering half each."""
        result = _result(
            _chunk("d1:v1:0", "the refund policy permits returns", 0.9),
            _chunk("d2:v1:0", "shipping is handled by a courier", 0.9),
        )
        grade, _ = grade_evidence("refund and shipping", result)
        assert grade == EvidenceGrade.SUFFICIENT

    def test_a_stop_word_only_query_is_graded_on_score_alone(self) -> None:
        result = _result(_chunk("d1:v1:0", "anything", 0.9))
        grade, reason = grade_evidence("what is it", result)
        assert "no content terms" in reason
        assert grade == EvidenceGrade.SUFFICIENT


# -- corrective ---------------------------------------------------------------------------


class TestCorrectiveRAG:
    async def test_sufficient_evidence_skips_the_retry(self) -> None:
        good = _result(_chunk("d1:v1:0", "the refund policy permits returns within 30 days", 0.9))
        knowledge = FakeKnowledge(good)
        router = FakeRouter("variant")
        strategy = CorrectiveRAG(
            SimpleRAG(knowledge), MultiQueryRAG(SimpleRAG(knowledge), router)
        )
        outcome = await strategy.retrieve(RetrievalRequest(query="what is the refund policy"))
        assert router.calls == 0
        assert any("sufficient" in n for n in outcome.trace.notes)

    async def test_thin_evidence_triggers_exactly_one_retry(self) -> None:
        """It must not loop: a second reformulation of an uncovered query finds the same nothing."""
        knowledge = FakeKnowledge(_result(_chunk("d1:v1:0", "unrelated text", 0.1), considered=5))
        router = FakeRouter("alternative phrasing")
        strategy = CorrectiveRAG(
            SimpleRAG(knowledge), MultiQueryRAG(SimpleRAG(knowledge), router)
        )
        outcome = await strategy.retrieve(RetrievalRequest(query="refund policy for damaged goods"))
        assert router.calls == 1
        assert any("retrying" in n for n in outcome.trace.notes)

    async def test_evidence_is_returned_even_when_graded_insufficient(self) -> None:
        """A partial answer with a stated gap is more useful than a refusal, and the note is what
        lets the generator say so."""
        thin = _result(_chunk("d1:v1:0", "unrelated", 0.1), considered=3)
        knowledge = FakeKnowledge(thin)
        strategy = CorrectiveRAG(SimpleRAG(knowledge), MultiQueryRAG(SimpleRAG(knowledge), FakeRouter("x")))
        outcome = await strategy.retrieve(RetrievalRequest(query="refund policy damaged goods"))
        assert len(outcome.chunks) == 1
        assert any("insufficient evidence after retry" in n for n in outcome.trace.notes)

    async def test_without_a_fallback_it_reports_that(self) -> None:
        knowledge = FakeKnowledge(_result(considered=4))
        outcome = await CorrectiveRAG(SimpleRAG(knowledge)).retrieve(
            RetrievalRequest(query="refund policy")
        )
        assert any("no fallback configured" in n for n in outcome.trace.notes)

    async def test_the_better_graded_attempt_wins(self) -> None:
        knowledge = FakeKnowledge()
        knowledge.per_query["refund policy for damaged goods"] = _result(
            _chunk("d1:v1:0", "unrelated", 0.1), considered=3
        )
        knowledge.per_query["better phrasing"] = _result(
            _chunk("d2:v1:0", "the refund policy covers damaged goods fully", 0.95), considered=3
        )
        strategy = CorrectiveRAG(
            SimpleRAG(knowledge), MultiQueryRAG(SimpleRAG(knowledge), FakeRouter("better phrasing"))
        )
        outcome = await strategy.retrieve(RetrievalRequest(query="refund policy for damaged goods"))
        assert any("kept retry" in n for n in outcome.trace.notes)


# -- self-rag -----------------------------------------------------------------------------


class TestSelfRAG:
    @pytest.mark.parametrize(
        "query",
        ["rewrite this paragraph", "translate this to French", "2 + 2 * 5", "proofread this"],
    )
    async def test_a_self_contained_request_skips_retrieval(self, query: str) -> None:
        knowledge = FakeKnowledge()
        outcome = await SelfRAG(SimpleRAG(knowledge)).retrieve(RetrievalRequest(query=query))
        assert knowledge.queries == []
        assert any("skipped" in n for n in outcome.trace.notes)

    async def test_skipping_is_distinguishable_from_finding_nothing(self) -> None:
        """The caller must be able to tell these apart -- one is an answer, the other is a fault."""
        skipped = await SelfRAG(SimpleRAG(FakeKnowledge())).retrieve(
            RetrievalRequest(query="rewrite this")
        )
        searched = await SelfRAG(SimpleRAG(FakeKnowledge(_result(considered=7)))).retrieve(
            RetrievalRequest(query="what is the refund policy")
        )
        assert skipped.is_empty_but_searched is False
        assert searched.is_empty_but_searched is True

    async def test_a_knowledge_question_retrieves(self) -> None:
        knowledge = FakeKnowledge(_result(_chunk("d1:v1:0", "refund policy details", 0.9)))
        outcome = await SelfRAG(SimpleRAG(knowledge)).retrieve(
            RetrievalRequest(query="what is the refund policy")
        )
        assert knowledge.queries == ["what is the refund policy"]
        assert len(outcome.chunks) == 1

    async def test_a_verb_marker_mid_sentence_still_counts_as_self_contained(self) -> None:
        strategy = SelfRAG(SimpleRAG(FakeKnowledge()))
        needed, why = strategy.needs_retrieval("please rewrite the following text")
        assert needed is False
        assert "self-contained" in why

    async def test_weak_grounding_is_recorded_not_hidden(self) -> None:
        thin = _result(_chunk("d1:v1:0", "totally unrelated content", 0.15), considered=4)
        outcome = await SelfRAG(SimpleRAG(FakeKnowledge(thin))).retrieve(
            RetrievalRequest(query="refund policy for damaged goods")
        )
        assert any("weakly grounded" in n for n in outcome.trace.notes)

    async def test_it_emits_no_numeric_confidence(self) -> None:
        """The brief forbids showing an uncalibrated confidence figure, and nothing here is
        calibrated against outcomes."""
        outcome = await SelfRAG(SimpleRAG(FakeKnowledge())).retrieve(RetrievalRequest(query="q"))
        assert not hasattr(outcome, "confidence")


# -- graph --------------------------------------------------------------------------------


class TestGraphRAG:
    async def test_it_traverses_to_the_hop_limit(self) -> None:
        graph = FakeGraph(
            entities=["auth"],
            edges={
                "auth": [("auth", "depends_on", "db")],
                "db": [("db", "depends_on", "disk")],
                "disk": [("disk", "depends_on", "hardware")],
            },
        )
        outcome = await GraphRAG(graph, SimpleRAG(FakeKnowledge()), max_hops=2).retrieve(
            RetrievalRequest(query="what does auth depend on", owner_id=OWNER)
        )
        flattened = " ".join(" ".join(p) for p in outcome.graph_paths)
        assert "db" in flattened
        assert "hardware" not in flattened  # third hop excluded

    async def test_a_cycle_terminates(self) -> None:
        """Graphs extracted from prose routinely contain cycles; without visited-tracking this
        would not stop."""
        graph = FakeGraph(
            entities=["a"],
            edges={"a": [("a", "rel", "b")], "b": [("b", "rel", "a")]},
        )
        outcome = await GraphRAG(graph, None, max_hops=5).retrieve(
            RetrievalRequest(query="a and b", owner_id=OWNER)
        )
        assert outcome.graph_paths  # produced something and returned

    async def test_a_graph_outage_still_returns_corpus_results(self) -> None:
        knowledge = FakeKnowledge(_result(_chunk("d1:v1:0", "text")))
        outcome = await GraphRAG(FakeGraph(fail=True), SimpleRAG(knowledge)).retrieve(
            RetrievalRequest(query="q", owner_id=OWNER)
        )
        assert len(outcome.chunks) == 1
        assert any("graph unavailable" in n for n in outcome.trace.notes)

    async def test_no_matching_entities_is_reported(self) -> None:
        outcome = await GraphRAG(FakeGraph(), None).retrieve(RetrievalRequest(query="q"))
        assert any("no matching entities" in n for n in outcome.trace.notes)

    async def test_the_path_limit_is_enforced(self) -> None:
        edges = {"root": [("root", "rel", f"n{i}") for i in range(50)]}
        graph = FakeGraph(entities=["root"], edges=edges)
        outcome = await GraphRAG(graph, None, max_paths=10).retrieve(RetrievalRequest(query="q"))
        assert len(outcome.graph_paths) <= 10
        assert any("path limit" in n for n in outcome.trace.notes)


# -- agentic ------------------------------------------------------------------------------


class TestAgenticRAG:
    async def test_it_retrieves_each_sub_question(self) -> None:
        knowledge = FakeKnowledge()
        router = FakeRouter("what is the refund policy\nwhat is the shipping policy")
        await AgenticRAG(SimpleRAG(knowledge), router).retrieve(
            RetrievalRequest(query="compare refund and shipping policies")
        )
        assert "what is the refund policy" in knowledge.queries
        assert "what is the shipping policy" in knowledge.queries

    async def test_it_is_bounded_by_max_steps(self) -> None:
        knowledge = FakeKnowledge()
        router = FakeRouter("\n".join(f"question {i}" for i in range(10)))
        await AgenticRAG(SimpleRAG(knowledge), router, max_steps=3).retrieve(
            RetrievalRequest(query="compare everything")
        )
        assert len(knowledge.queries) <= 3

    async def test_a_planning_failure_retrieves_the_original(self) -> None:
        knowledge = FakeKnowledge()
        outcome = await AgenticRAG(SimpleRAG(knowledge), FakeRouter(fail=True)).retrieve(
            RetrievalRequest(query="compare things")
        )
        assert knowledge.queries == ["compare things"]
        assert any("planning failed" in n for n in outcome.trace.notes)

    async def test_no_budget_skips_planning(self) -> None:
        knowledge = FakeKnowledge()
        router = FakeRouter("unused")
        await AgenticRAG(SimpleRAG(knowledge), router).retrieve(
            RetrievalRequest(query="compare", max_generations=0)
        )
        assert router.calls == 0
        assert knowledge.queries == ["compare"]


# -- multimodal ---------------------------------------------------------------------------


class TestMultimodalRAG:
    async def test_it_filters_to_non_text_modalities(self) -> None:
        knowledge = FakeKnowledge()
        await MultimodalRAG(SimpleRAG(knowledge)).retrieve(
            RetrievalRequest(query="what does the diagram show")
        )
        assert "modality" in knowledge.filters[0]
        assert "image" in knowledge.filters[0]["modality"]

    async def test_it_is_honest_when_no_extraction_pipeline_has_run(self) -> None:
        """There is no OCR or vision in ingestion yet. Claiming a multimodal answer drawn from
        prose would be worse than saying nothing was found."""
        knowledge = FakeKnowledge()
        outcome = await MultimodalRAG(SimpleRAG(knowledge), fall_back_to_text=False).retrieve(
            RetrievalRequest(query="what does the chart say")
        )
        assert any("no non-text content indexed" in n for n in outcome.trace.notes)

    async def test_the_text_fallback_records_that_it_was_not_multimodal(self) -> None:
        knowledge = FakeKnowledge()
        knowledge.per_query["what does the chart say"] = _result(_chunk("d1:v1:0", "prose"))
        outcome = await MultimodalRAG(SimpleRAG(knowledge)).retrieve(
            RetrievalRequest(query="what does the chart say")
        )
        assert any("fell back to text" in n for n in outcome.trace.notes)

    async def test_extracted_media_is_returned_without_falling_back(self) -> None:
        """The success path: once ingestion writes a modality, this finds it and stops there."""
        knowledge = FakeKnowledge()
        knowledge.media["what does the chart say"] = _result(
            _chunk("d1:v1:0", "Chart: revenue rose 12% in Q3")
        )
        outcome = await MultimodalRAG(SimpleRAG(knowledge)).retrieve(
            RetrievalRequest(query="what does the chart say")
        )
        assert len(outcome.chunks) == 1
        assert "revenue rose" in outcome.chunks[0].chunk.content
        assert not any("fell back" in n for n in outcome.trace.notes)
        # Exactly one retrieval: no wasted second pass when the filtered one succeeded.
        assert len(knowledge.queries) == 1

    async def test_a_users_filters_are_preserved(self) -> None:
        knowledge = FakeKnowledge()
        await MultimodalRAG(SimpleRAG(knowledge)).retrieve(
            RetrievalRequest(query="q", filters={"document_id": "d1"})
        )
        assert knowledge.filters[0]["document_id"] == "d1"


# -- classification -----------------------------------------------------------------------


class TestQueryClassification:
    @pytest.mark.parametrize(
        "query,expected",
        [
            ("what is our refund policy", QueryClass.FACTUAL),
            ("what did I tell you about this project last week", QueryClass.PERSONAL),
            ("which services depend on the auth module", QueryClass.RELATIONAL),
            ("compare these three documents", QueryClass.COMPARATIVE),
            ("what does the diagram on page 4 show", QueryClass.MEDIA),
            ("rewrite this paragraph", QueryClass.SELF_CONTAINED),
            ("is that actually supported by the docs", QueryClass.VERIFICATION),
        ],
    )
    def test_the_brief_s_examples_classify_as_intended(self, query: str, expected: str) -> None:
        assert expected in classify_query(query)

    def test_factual_is_always_present_as_a_floor(self) -> None:
        """The router must always have somewhere to send a query."""
        assert QueryClass.FACTUAL in classify_query("zzzz unmatched gibberish")

    def test_an_empty_query_still_classifies(self) -> None:
        assert classify_query("").primary == QueryClass.FACTUAL

    def test_a_query_can_carry_several_classes(self) -> None:
        classification = classify_query("what did I say about which services depend on auth")
        assert QueryClass.PERSONAL in classification
        assert QueryClass.RELATIONAL in classification

    def test_signals_explain_the_decision(self) -> None:
        """Routing has to be explicable or misroutes are unfixable."""
        assert classify_query("compare these documents").signals


# -- adaptive router ----------------------------------------------------------------------


class TestAdaptiveRouter:
    def _router(self, **overrides):
        knowledge = FakeKnowledge(_result(_chunk("d1:v1:0", "text")))
        base = build_strategies(knowledge=knowledge, router=FakeRouter("x"))
        base.update(overrides)
        return AdaptiveRouter(base, default="simple"), knowledge

    def test_it_refuses_a_default_that_is_not_configured(self) -> None:
        with pytest.raises(ValueError):
            AdaptiveRouter({"simple": object()}, default="nonexistent")

    def test_plan_exposes_the_decision_without_running_it(self) -> None:
        router, knowledge = self._router()
        classification, chosen = router.plan("which services depend on auth")
        assert QueryClass.RELATIONAL in classification
        assert "graph" in chosen
        assert knowledge.queries == []

    def test_a_factual_query_routes_to_simple(self) -> None:
        router, _ = self._router()
        _, chosen = router.plan("what is our refund policy")
        assert chosen == ["simple"]

    def test_parallel_strategies_are_capped(self) -> None:
        knowledge = FakeKnowledge()
        strategies = build_strategies(knowledge=knowledge, router=FakeRouter("x"))
        router = AdaptiveRouter(strategies, default="simple", max_parallel=2)
        _, chosen = router.plan("what did I say about which services depend on auth and compare them")
        assert len(chosen) <= 2

    async def test_one_failing_strategy_does_not_lose_the_others(self) -> None:
        """A graph store being down must not stop a relational question getting a text answer."""
        knowledge = FakeKnowledge(_result(_chunk("d1:v1:0", "text")))
        strategies = build_strategies(knowledge=knowledge, router=FakeRouter("x"))
        strategies["graph"] = FailingStrategy()
        router = AdaptiveRouter(strategies, default="simple", max_parallel=2)
        outcome = await router.retrieve(
            RetrievalRequest(query="which services depend on auth", owner_id=OWNER)
        )
        assert any("failed" in n for n in outcome.trace.notes)

    async def test_every_strategy_failing_falls_back_to_the_default(self) -> None:
        knowledge = FakeKnowledge(_result(_chunk("d1:v1:0", "text")))
        strategies = build_strategies(knowledge=knowledge, router=FakeRouter("x"))
        strategies["graph"] = FailingStrategy()
        strategies["memory"] = FailingStrategy()
        router = AdaptiveRouter(strategies, default="simple", max_parallel=2)
        outcome = await router.retrieve(
            RetrievalRequest(query="what did I say about what depends on auth", owner_id=OWNER)
        )
        assert len(outcome.chunks) == 1
        assert any("using default" in n for n in outcome.trace.notes)

    async def test_the_trace_records_which_strategies_ran(self) -> None:
        router, _ = self._router()
        outcome = await router.retrieve(RetrievalRequest(query="what is the refund policy"))
        assert any("ran=" in n for n in outcome.trace.notes)


# -- fusion -------------------------------------------------------------------------------


class TestFusion:
    def test_a_single_result_passes_through_unchanged(self) -> None:
        result = _result(_chunk("d1:v1:0", "a"))
        assert fuse_results([result]) is result

    def test_no_results_gives_an_empty_fused_result(self) -> None:
        assert fuse_results([]).chunks == ()

    def test_a_chunk_ranked_well_by_two_retrievals_outranks_one_ranked_well_once(self) -> None:
        """The property RRF exists for: agreement across retrievals beats a single high score."""
        a, b, c = _chunk("d1:v1:0", "a"), _chunk("d2:v1:0", "b"), _chunk("d3:v1:0", "c")
        first = _result(b, a)
        second = _result(c, a)
        fused = fuse_results([first, second])
        assert fused.chunks[0].chunk.id.value == "d1:v1:0"

    def test_the_original_score_is_preserved_not_replaced_by_the_rrf_score(self) -> None:
        """An RRF score is not a similarity; overwriting `score` would make it incomparable with
        the relevance threshold the engine applied."""
        chunk = _chunk("d1:v1:0", "a", score=0.77)
        fused = fuse_results([_result(chunk), _result(chunk)])
        assert fused.chunks[0].score == pytest.approx(0.77)

    def test_candidates_considered_accumulates(self) -> None:
        fused = fuse_results([_result(considered=5), _result(considered=3)])
        assert fused.candidates_considered == 8

    def test_the_limit_is_applied(self) -> None:
        chunks = [_chunk(f"d{i}:v1:0", f"c{i}") for i in range(10)]
        fused = fuse_results([_result(*chunks), _result(*chunks)], limit=3)
        assert len(fused.chunks) == 3


# -- rewriter -----------------------------------------------------------------------------


class TestQueryRewriter:
    async def test_a_follow_up_is_rewritten(self) -> None:
        router = FakeRouter("What is the refund policy for damaged goods?")
        rewriter = QueryRewriter(router)
        query, rewritten = await rewriter.rewrite(
            RetrievalRequest(
                query="and for damaged ones?",
                history=(("user", "what is the refund policy"), ("assistant", "30 days")),
            )
        )
        assert rewritten is True
        assert "refund" in query

    async def test_a_standalone_question_is_left_alone(self) -> None:
        """Rewriting it wastes a generation and can only make it worse."""
        router = FakeRouter("should not be called")
        original = "what is the escalation procedure for outages"
        query, rewritten = await QueryRewriter(router).rewrite(
            RetrievalRequest(query=original, history=(("user", "hello"),))
        )
        assert router.calls == 0
        assert query == original
        assert rewritten is False

    async def test_no_history_means_no_rewrite(self) -> None:
        router = FakeRouter("x")
        query, rewritten = await QueryRewriter(router).rewrite(
            RetrievalRequest(query="and the second one?")
        )
        assert rewritten is False
        assert router.calls == 0

    async def test_a_suspiciously_long_rewrite_is_rejected(self) -> None:
        """Guards against a model answering the question instead of rewriting it."""
        router = FakeRouter("x" * 500)
        original = "and it?"
        query, rewritten = await QueryRewriter(router).rewrite(
            RetrievalRequest(query=original, history=(("user", "hi"),))
        )
        assert query == original
        assert rewritten is False

    async def test_a_generation_failure_returns_the_original(self) -> None:
        query, rewritten = await QueryRewriter(FakeRouter(fail=True)).rewrite(
            RetrievalRequest(query="and it?", history=(("user", "hi"),))
        )
        assert query == "and it?"
        assert rewritten is False


# -- wiring -------------------------------------------------------------------------------


class TestBuildStrategies:
    def test_all_ten_strategies_are_built(self) -> None:
        built = build_strategies(knowledge=FakeKnowledge(), router=FakeRouter())
        assert set(built) == set(STRATEGY_NAMES)

    def test_missing_memory_and_graph_degrade_rather_than_raise(self) -> None:
        """A deployment without a graph store must still route relational questions somewhere."""
        built = build_strategies(knowledge=FakeKnowledge(), router=FakeRouter())
        assert built["graph"] is built["simple"]
        assert built["memory"] is built["simple"]

    def test_memory_and_graph_are_used_when_provided(self) -> None:
        built = build_strategies(
            knowledge=FakeKnowledge(),
            router=FakeRouter(),
            memory=FakeMemory(),
            graph=FakeGraph(),
        )
        assert built["graph"].name == "graph"
        assert built["memory"].name == "memory"

    def test_self_rag_wraps_corrective_not_simple(self) -> None:
        """Grading a result that was never allowed to retry would report weak grounding it could
        have fixed."""
        built = build_strategies(knowledge=FakeKnowledge(), router=FakeRouter())
        assert built["self_rag"]._inner is built["corrective"]

    def test_corrective_falls_back_to_multi_query(self) -> None:
        built = build_strategies(knowledge=FakeKnowledge(), router=FakeRouter())
        assert built["corrective"]._fallback is built["multi_query"]
