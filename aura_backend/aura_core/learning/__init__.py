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
from .knowledge_refresh import KnowledgeRefreshPipeline, RefreshReport, SourceOutcome
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
    "DatasetCurator",
    "FeedbackRecord",
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
