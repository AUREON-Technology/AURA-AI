"""Nightly knowledge refresh (ADR-008, Loop A).

Runs at 02:00 on the ``learning`` queue. **Nothing from a source reaches the live corpus unless
that source passes the content gates; the index is rebuilt and gated only when the embedding model
(or the index layout) has changed.**

```
per source (3 at a time):  fetch -> validate -> dedupe -> assess (writes nothing)
                           -> content gate: quarantine rate, shrink      <- a failing source is HELD
                           -> apply: ingest (clean, guard, embed, index, graph)
once, afterwards:          index current for the embedder?  yes -> done
                                                            no  -> rebuild a new index -> gates -> promote
```

A held source writes nothing and is retried next run; the sources that pass still apply. Applying
goes through :meth:`KnowledgeEngine.ingest`, the path a user upload takes, so the job cannot drift
from interactive behaviour. A same-embedder run embeds only what changed -- the previous design
rebuilt and re-embedded the whole corpus every night -- and the full rebuild, with its error-rate
and recall gates, happens exactly when it is needed: a different embedding model is a different
physical index, so its candidate really is separate from the one serving traffic.

Every run produces an immutable :class:`RefreshReport` and a numbered generation. Rollback of a
promotion is an alias repoint (``index_manager.rollback``), which is O(1) and needs no data movement.

What this pipeline deliberately does **not** do: touch the language model. Curating datasets and
recommending a fine-tune is Loop B (:mod:`aura_core.learning.curation`), and promoting one
requires a recorded human approval.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from ..domain.events import KnowledgeRefreshCompleted
from ..domain.identifiers import GenerationId
from ..domain.knowledge import DEFAULT_ALLOWED_LICENSES, KnowledgeSource, SourceType, TrustTier
from ..knowledge.index_manager import GateResult
from ..knowledge.refresh_gate import RefreshContentGate
from ..observability.logging import LogContext, get_logger
from .sources import SourceRegistry, SourceFetcher, FetchedDocument

__all__ = ["KnowledgeRefreshPipeline", "RefreshReport", "SourceOutcome"]

logger = get_logger("learning.refresh")

#: The outcome error of a global source refused before any fetch because nobody has approved its
#: licence for the global corpus. An administrator's pending decision, not a failure of the job.
LICENSE_NOT_APPROVED = "license_not_approved"


@dataclass(frozen=True, slots=True)
class SourceOutcome:
    source_id: str
    fetched: int = 0
    ingested: int = 0
    skipped_unchanged: int = 0
    skipped_duplicate: int = 0
    quarantined: int = 0
    failed: int = 0
    error: str | None = None
    #: Why the content gate held this source back; empty when it passed. A held source wrote
    #: nothing, and its ``last_success_at`` did not move, so the next run tries it again.
    held: tuple[str, ...] = ()
    #: Documents that passed the gate and would have been written. Only a dry run sets it: it
    #: writes nothing, so ``ingested`` stays zero and this is the answer to "what would change".
    would_apply: int = 0

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass(frozen=True, slots=True)
class RefreshReport:
    """The immutable record of one refresh run."""

    generation: GenerationId
    started_at: datetime
    duration_ms: float
    sources: tuple[SourceOutcome, ...] = ()
    #: Whether the index alias was switched this run. Normally false: a refresh with an unchanged
    #: embedder writes into the live index and has nothing to promote.
    promoted: bool = False
    #: Index-phase gate failures (a rebuild after an embedder change). Source-level failures are
    #: in :attr:`held_sources`.
    gate_failures: tuple[str, ...] = ()
    measurements: Mapping[str, float] = field(default_factory=dict)
    candidate_index: str | None = None
    previous_index: str | None = None
    #: ``not_needed`` (the live index already matches the embedder), ``promoted`` (rebuilt and
    #: promoted) or ``held`` (rebuilt, a gate failed, the previous index keeps serving).
    index_rebuild: str = "not_needed"

    @property
    def documents_ingested(self) -> int:
        return sum(s.ingested for s in self.sources)

    @property
    def documents_skipped(self) -> int:
        return sum(s.skipped_unchanged + s.skipped_duplicate for s in self.sources)

    @property
    def quarantined(self) -> int:
        return sum(s.quarantined for s in self.sources)

    @property
    def failures(self) -> int:
        return sum(s.failed for s in self.sources)

    @property
    def error_rate(self) -> float:
        attempted = sum(s.fetched for s in self.sources)
        return self.failures / attempted if attempted else 0.0

    @property
    def held_sources(self) -> dict[str, tuple[str, ...]]:
        """Source id -> the reasons the content gate held it back."""
        return {s.source_id: s.held for s in self.sources if s.held}

    @property
    def all_gate_failures(self) -> tuple[str, ...]:
        """Index-phase failures plus every held source's reasons, each prefixed ``source:<id>:``."""
        held = tuple(
            f"source:{source_id}: {reason}"
            for source_id, reasons in self.held_sources.items()
            for reason in reasons
        )
        return (*self.gate_failures, *held)

    @property
    def succeeded(self) -> bool:
        """No source failed to fetch and the index phase did not stall.

        A *held* source does not make a run unsuccessful: the gate did its job, and that is a
        ticket for an operator (``refresh_sources_held``), not a stale corpus. Nor does a source
        refused for want of an approved licence: it is still registered, awaiting an
        administrator's decision, and the rest of the corpus refreshed normally.
        """
        return (
            all(s.ok or s.error == LICENSE_NOT_APPROVED for s in self.sources)
            and self.index_rebuild != "held"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "generation": self.generation.value,
            "started_at": self.started_at.isoformat(),
            "duration_ms": round(self.duration_ms, 1),
            "promoted": self.promoted,
            "index_rebuild": self.index_rebuild,
            "gate_failures": list(self.gate_failures),
            "held_sources": {k: list(v) for k, v in self.held_sources.items()},
            "measurements": dict(self.measurements),
            "candidate_index": self.candidate_index,
            "previous_index": self.previous_index,
            "totals": {
                "ingested": self.documents_ingested,
                "skipped": self.documents_skipped,
                "quarantined": self.quarantined,
                "failed": self.failures,
                "held": len(self.held_sources),
                "error_rate": round(self.error_rate, 4),
            },
            "sources": [
                {
                    "id": s.source_id, "fetched": s.fetched, "ingested": s.ingested,
                    "skipped_unchanged": s.skipped_unchanged,
                    "skipped_duplicate": s.skipped_duplicate,
                    "quarantined": s.quarantined, "failed": s.failed, "error": s.error,
                    "held": list(s.held), "would_apply": s.would_apply,
                }
                for s in self.sources
            ],
        }


@dataclass(slots=True)
class _Staged:
    """One source after fetching and assessing, before any write."""

    fetched: int = 0
    failed: int = 0
    duplicate: int = 0
    error: str | None = None
    items: list[tuple[FetchedDocument, Any]] = field(default_factory=list)

    @property
    def unchanged(self) -> int:
        return sum(1 for _doc, assessment in self.items if assessment.unchanged)

    @property
    def quarantined(self) -> int:
        """A fully quarantined document counts once, a partly quarantined one by its chunks --
        the same arithmetic the report always used."""
        return sum(
            1 if assessment.fully_quarantined else assessment.quarantined_chunks
            for _doc, assessment in self.items
        )


class KnowledgeRefreshPipeline:
    def __init__(
        self,
        *,
        registry: SourceRegistry,
        fetcher: SourceFetcher,
        knowledge: Any,
        index_manager: Any,
        evaluator: Any = None,
        metrics: Any = None,
        event_bus: Any = None,
        max_documents: int = 5000,
        max_concurrent_sources: int = 3,
        promote_requires_gates: bool = True,
        index_retention_days: int = 7,
        content_gate: RefreshContentGate | None = None,
        allowed_licenses: Sequence[str] = DEFAULT_ALLOWED_LICENSES,
    ) -> None:
        self._allowed_licenses = tuple(allowed_licenses)
        self._registry = registry
        self._fetcher = fetcher
        self._knowledge = knowledge
        self._indexes = index_manager
        self._evaluator = evaluator
        self._metrics = metrics
        self._events = event_bus
        self._max_documents = max_documents
        self._max_concurrent = max(1, max_concurrent_sources)
        self._require_gates = promote_requires_gates
        self._retention_days = index_retention_days
        self._content_gate = content_gate or RefreshContentGate()
        self.last_report: RefreshReport | None = None

    async def run(self, *, dry_run: bool = False) -> RefreshReport:
        """Execute one refresh cycle. A dry run writes nothing: it stops each source after its
        content gate and reports what would have been applied."""
        generation = GenerationId.new()
        started_at = datetime.now(timezone.utc)
        started = time.perf_counter()

        with LogContext(correlation_id=generation.value, route="knowledge_refresh"):
            logger.info("knowledge refresh starting", extra={"generation": generation.value})

            sources = [s for s in await self._registry.list_enabled()]
            # With no source there is nothing to fetch, but the index phase still has a job when
            # the published index is not the one the running configuration builds (KM-01). This
            # used to return here regardless, so an embedder change on a deployment with no
            # registered source left retrieval unavailable and this job unable to repair it.
            if not sources and (dry_run or not await self._index_needs_rebuild()):
                logger.info("no enabled knowledge sources; nothing to refresh")
                report = RefreshReport(
                    generation=generation, started_at=started_at,
                    duration_ms=(time.perf_counter() - started) * 1000.0,
                )
                self.last_report = report
                return report

            # Per source: stage, gate, apply. Sources run concurrently but bounded -- an unbounded
            # fan-out over a hundred sources would saturate the embedding provider and the
            # network at once -- and each is judged on its own, so one bad source never holds back
            # the others.
            semaphore = asyncio.Semaphore(self._max_concurrent)
            budget = _DocumentBudget(self._max_documents)

            async def process(source: KnowledgeSource) -> SourceOutcome:
                async with semaphore:
                    return await self._refresh_source(source, budget, dry_run=dry_run)

            outcomes = await asyncio.gather(*(process(s) for s in sources))

            # The index phase. Applying a source embedded its documents into the live index, so
            # there is nothing to rebuild unless the live index is not the one the running
            # configuration would build (a new embedding model, or a new index layout).
            promoted = False
            gate_failures: tuple[str, ...] = ()
            measurements: Mapping[str, float] = {}
            index_rebuild = "not_needed"
            candidate = await self._indexes.active_identity()
            previous = candidate

            if not dry_run and await self._index_needs_rebuild():
                promoted, gate_failures, measurements, candidate, previous = await self._promote(
                    generation, outcomes
                )
                index_rebuild = "promoted" if promoted else "held"

            report = RefreshReport(
                generation=generation,
                started_at=started_at,
                duration_ms=(time.perf_counter() - started) * 1000.0,
                sources=tuple(outcomes),
                promoted=promoted,
                gate_failures=gate_failures,
                measurements=measurements,
                candidate_index=candidate.physical_name if candidate else None,
                previous_index=previous.physical_name if previous else None,
                index_rebuild=index_rebuild,
            )

            self._observe(report, dry_run=dry_run)
            await self._publish(
                KnowledgeRefreshCompleted(
                    generation=generation,
                    promoted=report.promoted,
                    sources_processed=len(outcomes),
                    documents_ingested=report.documents_ingested,
                    documents_skipped=report.documents_skipped,
                    quarantined=report.quarantined,
                    gate_failures=report.all_gate_failures,
                    duration_ms=report.duration_ms,
                )
            )
            logger.info("knowledge refresh complete", extra=report.as_dict()["totals"])
            self.last_report = report
            return report

    # -- per-source --------------------------------------------------------------------

    async def _refresh_source(
        self, source: KnowledgeSource, budget: "_DocumentBudget", *, dry_run: bool
    ) -> SourceOutcome:
        staged = await self._stage_source(source, budget)
        if staged.error is not None:
            return SourceOutcome(source_id=source.id, error=staged.error)

        verdict = self._gate_source(staged)
        if not verdict.passed:
            logger.warning(
                "source held back by the content gate; nothing from it was written",
                extra={"source": source.id, "reasons": list(verdict.failures)},
            )
            return SourceOutcome(
                source_id=source.id, fetched=staged.fetched,
                skipped_unchanged=staged.unchanged, skipped_duplicate=staged.duplicate,
                quarantined=staged.quarantined, failed=staged.failed, held=verdict.failures,
            )

        if dry_run:
            return SourceOutcome(
                source_id=source.id, fetched=staged.fetched,
                skipped_unchanged=staged.unchanged, skipped_duplicate=staged.duplicate,
                quarantined=staged.quarantined, failed=staged.failed,
                would_apply=len(staged.items) - staged.unchanged,
            )
        return await self._apply_source(source, staged)

    @staticmethod
    def _ingest_arguments(source: KnowledgeSource, document: FetchedDocument) -> dict[str, Any]:
        """The arguments for ingesting one fetched document, shared by the assessment and the
        write so the two cannot disagree about what they were asked."""
        return {
            "content": document.content,
            "title": document.title,
            "document_id": document.document_id,
            "owner_id": source.owner_id,
            "source_type": source.type,
            "source_uri": document.uri or source.uri,
            "trust_tier": source.trust_tier,
            "language": source.language,
            # Where the document came from and under what licence, on every ingested document.
            "metadata": {**_provenance(source), **dict(document.metadata)},
        }

    async def _stage_source(self, source: KnowledgeSource, budget: "_DocumentBudget") -> _Staged:
        """Fetch, validate, dedupe and assess. Writes nothing."""
        staged = _Staged()
        seen_hashes: set[str] = set()

        if not source.permits_global_corpus(self._allowed_licenses):
            # Before any network call: a global source nobody has licence-checked (D-001) must not
            # reach every user's retrieval. It stays registered, so an admin can add the licence.
            logger.warning(
                "source skipped: licence not approved for the global corpus",
                extra={"source": source.id, "license": source.license_spdx or "unknown",
                       "status": str(source.license_status)},
            )
            staged.error = LICENSE_NOT_APPROVED
            return staged

        try:
            documents = await self._fetcher.fetch(source)
        except Exception as exc:
            logger.warning(
                "source fetch failed",
                extra={"source": source.id, "error": type(exc).__name__},
            )
            staged.error = type(exc).__name__
            return staged

        for document in documents:
            if not budget.take():
                logger.warning("refresh document budget exhausted", extra={"source": source.id})
                break
            staged.fetched += 1

            # Step 2: validate against the source's declared ceilings.
            problem = _validate(document, source)
            if problem is not None:
                staged.failed += 1
                logger.debug("document rejected", extra={"source": source.id, "reason": problem})
                continue

            # Step 3: exact then near-duplicate detection within this run. Cross-run duplicates
            # are handled by the knowledge engine's content-hash idempotency.
            digest = hashlib.sha256(document.content.encode("utf-8")).hexdigest()
            if digest in seen_hashes:
                staged.duplicate += 1
                continue
            if _is_near_duplicate(document.content, budget):
                staged.duplicate += 1
                continue
            seen_hashes.add(digest)

            # Steps 4-5, read-only: what would clean, guard and chunking do with it? Same code as
            # the write below (`KnowledgeEngine._prepare`), so the answer is what ingest will do.
            try:
                assessment = await self._knowledge.assess(**self._ingest_arguments(source, document))
            except Exception as exc:
                staged.failed += 1
                logger.warning(
                    "document assessment failed",
                    extra={"source": source.id, "error": type(exc).__name__},
                )
                continue
            staged.items.append((document, assessment))
        return staged

    def _gate_source(self, staged: _Staged) -> GateResult:
        return self._content_gate.evaluate([assessment for _doc, assessment in staged.items])

    async def _apply_source(self, source: KnowledgeSource, staged: _Staged) -> SourceOutcome:
        """Write what passed the gate, through the same path a user upload takes."""
        ingested = 0
        unchanged = staged.unchanged
        quarantined = failed = 0

        for document, assessment in staged.items:
            if assessment.unchanged:
                continue
            try:
                result = await self._knowledge.ingest(**self._ingest_arguments(source, document))
            except Exception as exc:
                failed += 1
                logger.warning(
                    "document ingestion failed",
                    extra={"source": source.id, "error": type(exc).__name__},
                )
                continue

            if result.skipped_reason == "unchanged_content":
                unchanged += 1  # it changed back, or another writer got there first, since assess
            elif result.skipped_reason == "fully_quarantined":
                quarantined += 1
            else:
                ingested += 1
                quarantined += result.quarantined

        # A source whose every document failed to validate or ingest has not been refreshed, so its
        # timestamp must not say it was: `last_success_at` is what an operator reads to decide
        # whether a source is alive. Nothing to apply at all is not a failure.
        if not (staged.fetched > 0 and ingested + unchanged == 0):
            await self._registry.record_success(source.id, cursor=None, at=datetime.now(timezone.utc))
        return SourceOutcome(
            source_id=source.id,
            fetched=staged.fetched,
            ingested=ingested,
            skipped_unchanged=unchanged,
            skipped_duplicate=staged.duplicate,
            quarantined=quarantined,
            failed=staged.failed + failed,
        )

    # -- index phase -------------------------------------------------------------------

    async def _index_needs_rebuild(self) -> bool:
        """Whether the published index is not the one the running configuration builds.

        Compared as published versus required physical names (``IndexManager.status``), never as
        identity objects: an identity read back from the alias is lossy. Nothing published means
        nothing was ever ingested, so there is nothing to rebuild.
        """
        status = await self._indexes.status()
        return status["published"] is not None and not status["up_to_date"]

    async def _promote(
        self, generation: GenerationId, outcomes: Sequence[SourceOutcome]
    ) -> tuple[bool, tuple[str, ...], Mapping[str, float], Any, Any]:
        """Build a candidate index, evaluate it, and switch the alias if the gates pass.

        Reached only when the embedder (or index layout) changed, so the candidate is a different
        physical index from the one serving traffic and the live index really is untouched until
        the alias moves: a failed build leaves production exactly as it was. It is a full rebuild
        from the document store, which costs more and is the only way to move to a new embedding
        space.
        """
        previous = await self._indexes.active_identity()
        candidate = self._indexes.identity_for_current_embedder()

        build = await self._indexes.build(identity=candidate, generation=generation)

        baseline_recall = candidate_recall = None
        if self._evaluator is not None:
            try:
                baseline_recall, candidate_recall = await self._evaluator.compare(
                    baseline=previous, candidate=candidate
                )
            except Exception:
                # An unavailable evaluator means the recall gate cannot be assessed. Treating
                # that as a pass would defeat the gate; the count and error-rate gates still
                # apply, and the missing measurement is visible in the report.
                logger.warning("retrieval evaluation unavailable; recall gate not assessed")

        gate = await self._indexes.evaluate_promotion(
            candidate,
            report=build,
            candidate_recall=candidate_recall,
            baseline_recall=baseline_recall,
        )

        if self._require_gates and not gate.passed:
            logger.error(
                "refresh gates failed; keeping the previous index",
                extra={"failures": list(gate.failures)},
            )
            return False, gate.failures, gate.measurements, candidate, previous

        await self._indexes.promote(candidate, generation=generation, gate=gate, reason="refresh")

        if self._retention_days == 0 and previous is not None and previous != candidate:
            await self._indexes.prune(keep=[candidate])

        return True, gate.failures, gate.measurements, candidate, previous

    # -- observation -------------------------------------------------------------------

    def _observe(self, report: RefreshReport, *, dry_run: bool = False) -> None:
        if self._metrics is None:
            return
        self._metrics.refresh_duration.observe(report.duration_ms / 1000.0)
        for failure in report.gate_failures:
            self._metrics.refresh_gate_failures_total.labels(gate=failure.split()[0]).inc()
        # The label is the check's name ("quarantine", "shrink"), never the source id: a source id
        # is operator-chosen and unbounded, which is exactly what a metric label must not be.
        for reasons in report.held_sources.values():
            for reason in reasons:
                self._metrics.refresh_gate_failures_total.labels(gate=reason.split()[0]).inc()
        self._metrics.refresh_sources_held.set(len(report.held_sources))
        # A dry run changed nothing, so it says nothing about how fresh the corpus is.
        if report.succeeded and not dry_run:
            self._metrics.refresh_last_success_timestamp.set(time.time())
            self._metrics.knowledge_age_seconds.set(0)

    async def _publish(self, event: Any) -> None:
        if self._events is None:
            return
        try:
            await self._events.publish(event)
        except Exception:  # pragma: no cover - defensive
            logger.debug("refresh event publication failed", exc_info=True)


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------


class _DocumentBudget:
    """Global ceiling across all sources in one run, plus the SimHash corpus for dedup."""

    __slots__ = ("_remaining", "simhashes", "shingles")

    def __init__(self, limit: int) -> None:
        self._remaining = limit
        self.simhashes: list[int] = []
        self.shingles: list[set[str]] = []

    def take(self) -> bool:
        if self._remaining <= 0:
            return False
        self._remaining -= 1
        return True


def _provenance(source: KnowledgeSource) -> dict[str, Any]:
    """Where a document came from and under what licence, carried on every ingested document.

    Only what is set: an owner's private source has no licence and records none.
    """
    provenance: dict[str, Any] = {"source_id": source.id}
    for key, value in (
        ("source_name", source.name),
        ("publisher", source.publisher),
        ("license_spdx", source.license_spdx),
        ("attribution", source.attribution),
    ):
        if value:
            provenance[key] = value
    if source.domains:
        provenance["domains"] = list(source.domains)
    return provenance


def _validate(document: FetchedDocument, source: KnowledgeSource) -> str | None:
    """Reject documents that fail the source's declared constraints.

    The size ceiling matters more than it looks: a source that starts returning an HTML error
    page, or a redirect loop, produces plausible-looking documents that would otherwise be
    ingested and pollute retrieval.
    """
    if not document.content.strip():
        return "empty"
    size = len(document.content.encode("utf-8"))
    if size > source.max_bytes_per_document:
        return f"too_large:{size}"
    if not document.title.strip():
        return "missing_title"
    # A "document" that is almost entirely markup is a fetch failure, not content.
    markup_ratio = document.content.count("<") / max(1, len(document.content) / 100)
    if markup_ratio > 5:
        return "markup_heavy"
    return None


def _simhash(text: str, *, bits: int = 64) -> int:
    """64-bit SimHash over word shingles.

    Used for *long* documents, where an O(1) comparison is the only affordable option across a
    corpus of thousands. See :func:`_is_near_duplicate` for why short texts take a different
    path.
    """
    tokens = text.lower().split()
    if not tokens:
        return 0
    shingles = [" ".join(tokens[i : i + 3]) for i in range(max(1, len(tokens) - 2))]
    vector = [0] * bits
    for shingle in shingles:
        digest = int.from_bytes(
            hashlib.blake2b(shingle.encode("utf-8"), digest_size=8).digest(), "big"
        )
        for bit in range(bits):
            vector[bit] += 1 if (digest >> bit) & 1 else -1
    result = 0
    for bit in range(bits):
        if vector[bit] > 0:
            result |= 1 << bit
    return result


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def _shingles(text: str, size: int = 3) -> set[str]:
    tokens = text.lower().split()
    if len(tokens) < size:
        return {" ".join(tokens)}
    return {" ".join(tokens[i : i + size]) for i in range(len(tokens) - size + 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


#: Below this token count SimHash is unreliable: the signature is dominated by a handful of
#: shingles, so appending one sentence to a short document flips ~10 of 64 bits -- well past
#: any threshold that would still be meaningful on long text. Exact Jaccard is cheap at this
#: size, so short documents take the accurate path and long ones take the fast one.
_SIMHASH_MIN_TOKENS = 200
_SIMHASH_MAX_DISTANCE = 6
_JACCARD_THRESHOLD = 0.75


def _is_near_duplicate(text: str, seen: "_DocumentBudget") -> bool:
    """Length-aware near-duplicate check.

    Long documents are compared by SimHash distance (O(1) per comparison). Short ones are
    compared by exact shingle Jaccard, which is affordable at that size and does not suffer
    SimHash's small-document instability.
    """
    token_count = len(text.split())

    if token_count >= _SIMHASH_MIN_TOKENS:
        signature = _simhash(text)
        if any(_hamming(signature, other) <= _SIMHASH_MAX_DISTANCE for other in seen.simhashes):
            return True
        seen.simhashes.append(signature)
        return False

    shingles = _shingles(text)
    if any(_jaccard(shingles, other) >= _JACCARD_THRESHOLD for other in seen.shingles):
        return True
    seen.shingles.append(shingles)
    return False


def default_source(uri: str, *, source_id: str, type: SourceType = SourceType.WEB) -> KnowledgeSource:
    return KnowledgeSource(
        id=source_id, type=type, uri=uri, trust_tier=TrustTier.VERIFIED
    )
