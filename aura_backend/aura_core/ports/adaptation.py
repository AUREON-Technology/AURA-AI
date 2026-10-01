"""Ports for the Project Twin (AP4, ADR-020/026). Stdlib only, async throughout."""

from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

from ..domain.ausm import ProjectTwin
from ..domain.twin import LabelProposal, LabelRequest, TwinVersion

__all__ = ["TwinStore", "LabelingModel"]


@runtime_checkable
class TwinStore(Protocol):
    """Append-only storage of Project Twin snapshots.

    :meth:`append` refuses a version that already exists (``ConflictError``) -- versions are
    never rewritten; a bad one is superseded by the next.
    """

    async def append(self, version: TwinVersion, twin: ProjectTwin) -> None: ...

    async def latest(self, project_id: str) -> TwinVersion | None: ...

    async def get(self, project_id: str, version: int) -> tuple[TwinVersion, ProjectTwin] | None: ...

    async def versions(self, project_id: str, *, limit: int = 50) -> Sequence[TwinVersion]:
        """Newest first."""
        ...


@runtime_checkable
class LabelingModel(Protocol):
    """Proposes display names and descriptions for twin elements (ADR-026).

    Optional and batch-only. It may answer for fewer elements than asked, or raise; the labeling
    pass treats either as "keep the humanised identifier" for what is missing. ``name`` becomes
    part of the provenance extractor (``llm:<name>``), so it should identify the model.
    """

    name: str

    async def propose(self, requests: Sequence[LabelRequest]) -> Sequence[LabelProposal]: ...
