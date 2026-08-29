"""Digest-addressed curated dataset storage (ADR-008 Loop B).

An approval binds to a content digest; the dataset must still be there when a
human later launches training. In-memory for tests; filesystem + metadata for
production (volume-mounted path).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ..domain.errors import NotFoundError
from .curation import CuratedExample, DatasetCurator, DatasetStatistics

__all__ = [
    "StoredDataset",
    "DatasetStore",
    "InMemoryDatasetStore",
    "FileDatasetStore",
]


@dataclass(frozen=True, slots=True)
class StoredDataset:
    digest: str
    examples: tuple[CuratedExample, ...]
    statistics: DatasetStatistics
    format: str = "chatml"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    base_model: str = ""
    jsonl: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "digest": self.digest,
            "example_count": len(self.examples),
            "statistics": self.statistics.as_dict(),
            "format": self.format,
            "created_at": self.created_at.isoformat(),
            "base_model": self.base_model,
        }


@runtime_checkable
class DatasetStore(Protocol):
    async def put(self, dataset: StoredDataset) -> None:
        ...

    async def get(self, digest: str) -> StoredDataset | None:
        ...

    async def export(self, digest: str, *, fmt: str | None = None) -> str:
        ...


class InMemoryDatasetStore:
    def __init__(self) -> None:
        self._datasets: dict[str, StoredDataset] = {}

    async def put(self, dataset: StoredDataset) -> None:
        self._datasets[dataset.digest] = dataset

    async def get(self, digest: str) -> StoredDataset | None:
        return self._datasets.get(digest)

    async def export(self, digest: str, *, fmt: str | None = None) -> str:
        dataset = self._datasets.get(digest)
        if dataset is None:
            raise NotFoundError(f"unknown dataset digest {digest!r}")
        if fmt is None or fmt == dataset.format:
            return dataset.jsonl
        return DatasetCurator.export(list(dataset.examples), fmt=fmt)


class FileDatasetStore:
    """Persist JSONL under ``root/{digest}.jsonl`` plus a sidecar metadata file."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def _jsonl_path(self, digest: str) -> Path:
        return self._root / f"{digest}.jsonl"

    def _meta_path(self, digest: str) -> Path:
        return self._root / f"{digest}.meta.json"

    async def put(self, dataset: StoredDataset) -> None:
        self._jsonl_path(dataset.digest).write_text(dataset.jsonl, encoding="utf-8")
        meta = {
            "digest": dataset.digest,
            "format": dataset.format,
            "created_at": dataset.created_at.isoformat(),
            "base_model": dataset.base_model,
            "statistics": dataset.statistics.as_dict(),
            "examples": [
                {
                    "prompt": e.prompt,
                    "completion": e.completion,
                    "source_id": e.source_id,
                    "weight": e.weight,
                    "agent": e.agent,
                    "was_corrected": e.was_corrected,
                }
                for e in dataset.examples
            ],
        }
        self._meta_path(dataset.digest).write_text(
            json.dumps(meta, ensure_ascii=False), encoding="utf-8"
        )

    async def get(self, digest: str) -> StoredDataset | None:
        meta_path = self._meta_path(digest)
        jsonl_path = self._jsonl_path(digest)
        if not meta_path.exists() or not jsonl_path.exists():
            return None
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        examples = tuple(
            CuratedExample(
                prompt=row["prompt"],
                completion=row["completion"],
                source_id=row["source_id"],
                weight=float(row.get("weight", 1.0)),
                agent=row.get("agent", ""),
                was_corrected=bool(row.get("was_corrected", False)),
            )
            for row in meta.get("examples", [])
        )
        stats_raw = meta.get("statistics") or {}
        rejected = stats_raw.get("rejected") or {}
        statistics = DatasetStatistics(
            total_candidates=int(stats_raw.get("total_candidates", 0)),
            accepted=int(stats_raw.get("accepted", 0)),
            rejected_low_rating=int(rejected.get("low_rating", 0)),
            rejected_duplicate=int(rejected.get("duplicate", 0)),
            rejected_pii=int(rejected.get("pii_or_secret", 0)),
            rejected_degenerate=int(rejected.get("degenerate", 0)),
            rejected_short=int(rejected.get("too_short", 0)),
            distinct_users=int(stats_raw.get("distinct_users", 0)),
            corrected_fraction=float(stats_raw.get("corrected_fraction", 0.0)),
            mean_prompt_chars=float(stats_raw.get("mean_prompt_chars", 0.0)),
            mean_completion_chars=float(stats_raw.get("mean_completion_chars", 0.0)),
            agent_distribution=dict(stats_raw.get("agent_distribution") or {}),
            model_distribution=dict(stats_raw.get("model_distribution") or {}),
        )
        created = meta.get("created_at")
        return StoredDataset(
            digest=digest,
            examples=examples,
            statistics=statistics,
            format=meta.get("format", "chatml"),
            created_at=(
                datetime.fromisoformat(created)
                if isinstance(created, str)
                else datetime.now(timezone.utc)
            ),
            base_model=meta.get("base_model", ""),
            jsonl=jsonl_path.read_text(encoding="utf-8"),
        )

    async def export(self, digest: str, *, fmt: str | None = None) -> str:
        dataset = await self.get(digest)
        if dataset is None:
            raise NotFoundError(f"unknown dataset digest {digest!r}")
        if fmt is None or fmt == dataset.format:
            return dataset.jsonl
        return DatasetCurator.export(list(dataset.examples), fmt=fmt)
