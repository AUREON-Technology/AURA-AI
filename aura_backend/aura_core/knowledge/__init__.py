"""Knowledge subsystem: ingestion, versioned indexes, hybrid retrieval, graph (ADR-005, ADR-007)."""

from .chunking import ChunkingConfig, chunk_text, chunk_with_headings, split_sentences
from .engine import IngestAssessment, IngestionResult, KnowledgeEngine
from .extraction import RuleEntityExtractor, LLMEntityExtractor
from .index_manager import BuildReport, GateResult, IndexManager, PromotionGate
from .refresh_gate import RefreshContentGate
from .retrieval import (
    BM25Index,
    HeuristicReranker,
    maximal_marginal_relevance,
    reciprocal_rank_fusion,
    tokenize,
)

__all__ = [
    "KnowledgeEngine",
    "IngestionResult",
    "IngestAssessment",
    "IndexManager",
    "PromotionGate",
    "RefreshContentGate",
    "GateResult",
    "BuildReport",
    "ChunkingConfig",
    "chunk_text",
    "chunk_with_headings",
    "split_sentences",
    "BM25Index",
    "HeuristicReranker",
    "reciprocal_rank_fusion",
    "maximal_marginal_relevance",
    "tokenize",
    "RuleEntityExtractor",
    "LLMEntityExtractor",
]
