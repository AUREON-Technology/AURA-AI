"""Knowledge subsystem: ingestion, versioned indexes, hybrid retrieval, graph (ADR-005, ADR-007)."""

from .chunking import ChunkingConfig, chunk_text, chunk_with_headings, split_sentences
from .engine import IngestionResult, KnowledgeEngine
from .extraction import RuleEntityExtractor, LLMEntityExtractor
from .index_manager import BuildReport, GateResult, IndexManager, PromotionGate
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
    "IndexManager",
    "PromotionGate",
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
