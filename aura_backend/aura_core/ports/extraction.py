"""Structured-extraction port (ADR-038).

Kept apart from :mod:`aura_core.ports.llm` on purpose. ``clairic`` is not a chat model and is not
reached through :class:`~aura_core.llm.router.ProviderRouter`: nothing here streams, fails over or
competes with a provider. An extractor is one fixed endpoint behind one tool.

Contract for adapters:

* **Async only**, like every I/O port.
* **Errors are domain errors.** A failed call raises
  :class:`~aura_core.domain.errors.ExtractionError`; a call refused before anything is sent raises
  :class:`~aura_core.domain.errors.ExtractionRefusedError`. Never a vendor exception, never an
  empty result that reads like "nothing was found".
* **Confidence is applied here, not by the caller.** Every adapter passes each raw answer through
  :func:`aura_core.domain.extraction.classify_answer`, so the withholding rule is one function.
* **The text and the answers are never logged**, and neither is the credential.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain.extraction import ExtractionRequest, ExtractionResult
from .llm import HealthStatus

__all__ = ["StructuredExtractor"]


@runtime_checkable
class StructuredExtractor(Protocol):
    @property
    def name(self) -> str:
        """Stable, low-cardinality identifier; a metric and audit label."""
        ...

    @property
    def model_id(self) -> str:
        """The model the answers came from. Recorded in the audit event."""
        ...

    async def extract(self, request: ExtractionRequest, *, min_confidence: float) -> ExtractionResult:
        """Fill every field in ``request`` from its text.

        Returns one entry per declared field, in the request's order, each already classified
        against ``min_confidence``. Raises :class:`ExtractionError` on any upstream failure.
        """
        ...

    async def health(self) -> HealthStatus:
        """Cheap probe that must not spend quota. An adapter with no free endpoint reports its
        configured state rather than calling out."""
        ...

    async def close(self) -> None:
        """Release connections. Idempotent."""
        ...
