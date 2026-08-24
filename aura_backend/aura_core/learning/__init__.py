"""Continuous learning (ADR-008).

Loop A -- the nightly knowledge refresh -- is fully automatic and reversible.
Loop B -- model adaptation -- prepares datasets and recommends jobs automatically, but promoting
a model requires a recorded human approval.
"""

from .approval import (
    ApprovalKind,
    ApprovalRequest,
    ApprovalService,
    ApprovalStatus,
    InMemoryApprovalStore,
)
from .curation import (
    CuratedExample,
    DatasetCurator,
    DatasetStatistics,
    FeedbackRecord,
    FineTuneRecommendation,
    dataset_digest,
)
from .feedback import FeedbackStore, InMemoryFeedbackStore
from .knowledge_refresh import KnowledgeRefreshPipeline, RefreshReport, SourceOutcome
from .jobs import run_knowledge_refresh, run_memory_consolidate, run_outbox_drain
from .sources import (
    CompositeFetcher,
    FetchedDocument,
    HttpSourceFetcher,
    InMemorySourceRegistry,
    SourceFetcher,
    SourceRegistry,
    StaticSourceFetcher,
)

__all__ = [
    "KnowledgeRefreshPipeline",
    "RefreshReport",
    "SourceOutcome",
    "SourceRegistry",
    "InMemorySourceRegistry",
    "SourceFetcher",
    "HttpSourceFetcher",
    "StaticSourceFetcher",
    "CompositeFetcher",
    "FetchedDocument",
    "run_knowledge_refresh",
    "run_memory_consolidate",
    "run_outbox_drain",
    "DatasetCurator",
    "FeedbackRecord",
    "FeedbackStore",
    "InMemoryFeedbackStore",
    "CuratedExample",
    "DatasetStatistics",
    "FineTuneRecommendation",
    "dataset_digest",
    "ApprovalService",
    "ApprovalRequest",
    "ApprovalKind",
    "ApprovalStatus",
    "InMemoryApprovalStore",
]
