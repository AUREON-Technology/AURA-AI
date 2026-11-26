"""Knowledge-base entities: documents, versions, chunks, retrieval results, graph elements.

Two ideas drive this module:

1. **Documents are versioned, chunks are derived** (ADR-007). A document has an immutable
   ``(id, version)`` identity; re-ingesting changed content creates version n+1 and supersedes n.
   Chunk ids embed the version, so retrieval never silently mixes stale and fresh content and
   "delete my data" is verifiable.

2. **Index identity includes the embedding model.** ``IndexIdentity`` makes it impossible to
   write 768-dimensional vectors into a 384-dimensional index by accident -- the failure the
   legacy code "solves" by deleting the whole collection (SEC-03).
"""

from __future__ import annotations

import enum
import hashlib
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from .identifiers import ChunkId, DocumentId, UserId

__all__ = [
    "DocumentStats",
    "SourceType",
    "TrustTier",
    "LicenseStatus",
    "DEFAULT_ALLOWED_LICENSES",
    "DocumentStatus",
    "Document",
    "Chunk",
    "IndexIdentity",
    "RetrievedChunk",
    "RetrievalResult",
    "Entity",
    "Relation",
    "GraphDelta",
    "KnowledgeSource",
]

_SLUG = re.compile(r"[^a-z0-9]+")


def _slug(value: str) -> str:
    return _SLUG.sub("-", value.lower()).strip("-") or "unknown"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SourceType(str, enum.Enum):
    UPLOAD = "upload"
    DRIVE = "drive"
    WEB = "web"
    RSS = "rss"
    API = "api"
    REPOSITORY = "repository"
    CONVERSATION = "conversation"
    INTERNAL = "internal"
    #: Documents read through an active Track AP connector (AP10); ``uri`` is ``connector:<id>``.
    CONNECTOR = "connector"

    def __str__(self) -> str:
        return self.value


class TrustTier(int, enum.Enum):
    """How much a source is trusted, which drives safety strictness during ingestion.

    Untrusted sources get the full guard pipeline and are quarantined aggressively; internal
    sources (operator-curated) get lighter treatment. Trust is never "skip the guards" --
    ADR-006 applies to every channel -- it only changes thresholds.
    """

    UNTRUSTED = 0
    COMMUNITY = 1
    VERIFIED = 2
    INTERNAL = 3


class LicenseStatus(str, enum.Enum):
    """Whether anyone has checked a source's licence against the corpus policy."""

    APPROVED = "approved"
    RESTRICTED = "restricted"
    UNKNOWN = "unknown"

    def __str__(self) -> str:
        return self.value


#: Licences the global corpus accepts (owner decision D-001, 2026-11-22): CC0, CC BY and
#: permissive licences. No share-alike and no non-commercial. Overridable per deployment with
#: ``AURA_KNOWLEDGE_ALLOWED_LICENSES``; compared case-insensitively against SPDX ids.
DEFAULT_ALLOWED_LICENSES: tuple[str, ...] = (
    "CC0-1.0",
    "CC-BY-4.0",
    "CC-BY-3.0",
    "MIT",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "Apache-2.0",
    "PSF-2.0",
    "PostgreSQL",
    "ISC",
)


class DocumentStatus(str, enum.Enum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    QUARANTINED = "quarantined"
    TOMBSTONED = "tombstoned"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class DocumentStats:
    """What one owner's active documents add up to (``GET /api/v3/knowledge``)."""

    documents: int = 0
    #: UTF-8 bytes of the active versions' text -- the same measure the plan's storage quota counts.
    content_bytes: int = 0
    pinned: int = 0


@dataclass(frozen=True, slots=True)
class Document:
    """A versioned unit of knowledge.

    ``content_hash`` is the idempotency key for ingestion: re-ingesting identical content is a
    no-op rather than a new version, which keeps nightly refreshes (ADR-008) from churning the
    index when upstream sources are unchanged.
    """

    id: DocumentId
    version: int
    title: str
    content: str
    source_type: SourceType = SourceType.UPLOAD
    source_uri: str | None = None
    owner_id: UserId | None = None
    trust_tier: TrustTier = TrustTier.COMMUNITY
    status: DocumentStatus = DocumentStatus.ACTIVE
    language: str | None = None
    #: When true, this document is always packed into the CAG prefix (ADR-010). Pinning is
    #: a flag, not a new version -- re-ingesting the same content keeps the pin.
    cag_pinned: bool = False
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.version < 1:
            raise ValueError("document version starts at 1")

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()

    @property
    def is_global(self) -> bool:
        """Global documents are visible to every user; owned documents are partitioned.

        This is enforced at the store layer by a mandatory filter, not by convention.
        """
        return self.owner_id is None

    def next_version(self, content: str, **changes: Any) -> "Document":
        return replace(
            self,
            version=self.version + 1,
            content=content,
            updated_at=_utcnow(),
            status=DocumentStatus.ACTIVE,
            **changes,
        )

    def superseded(self) -> "Document":
        return replace(self, status=DocumentStatus.SUPERSEDED, updated_at=_utcnow())


@dataclass(frozen=True, slots=True)
class Chunk:
    """A retrievable fragment of a document version.

    Carries enough provenance (``document_id``, ``document_version``, ``index``, ``heading_path``)
    to render a citation without a second lookup, which is what makes groundedness measurable
    (ADR-009).
    """

    id: ChunkId
    document_id: DocumentId
    document_version: int
    index: int
    content: str
    heading_path: tuple[str, ...] = ()
    token_estimate: int = 0
    owner_id: UserId | None = None
    trust_tier: TrustTier = TrustTier.COMMUNITY
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def build_id(cls, document_id: DocumentId, version: int, index: int) -> ChunkId:
        return ChunkId(f"{document_id.value}:v{version}:{index}")

    def citation(self) -> str:
        """A human-readable reference, precise enough to go and check.

        Prefers the page number when the extraction recorded one, because "page 4" is a place a
        reader can turn to and "chunk 7" is not. Falls back to the heading path, then to the chunk
        index -- in that order, because a heading is still a location a person can find.
        """
        page = self.metadata.get("page")
        if page is not None:
            location = f"page {page}"
            if self.heading_path:
                location += " > " + " > ".join(self.heading_path)
        elif self.heading_path:
            location = " > ".join(self.heading_path)
        else:
            location = f"chunk {self.index}"
        return f"{self.document_id.value}@v{self.document_version} ({location})"


@dataclass(frozen=True, slots=True)
class IndexIdentity:
    """The identity of a physical vector index (ADR-007).

    Two indexes are the same only if their logical name, schema version, embedding model, and
    dimension all match. Anything else is a *different* index and must be built, verified, and
    switched to via an alias -- never overwritten in place.
    """

    logical_name: str
    schema_version: int
    embedding_model_id: str
    dimension: int
    distance: str = "cosine"

    def __post_init__(self) -> None:
        if self.dimension <= 0:
            raise ValueError("dimension must be positive")
        if self.schema_version < 1:
            raise ValueError("schema_version starts at 1")
        if self.distance not in {"cosine", "dot", "euclid"}:
            raise ValueError(f"unsupported distance metric: {self.distance}")

    @property
    def physical_name(self) -> str:
        return (
            f"{_slug(self.logical_name)}__v{self.schema_version}"
            f"__{_slug(self.embedding_model_id)}__{self.dimension}"
        )

    def matches_embedding(self, model_id: str, dimension: int) -> bool:
        # Compare slugged forms, not raw ones: an identity read back through
        # ``_parse_physical_name`` never has its original casing or punctuation (the round trip
        # is deliberately lossy -- see that function's docstring), so ``self.embedding_model_id``
        # here may already be a slug (e.g. "baai-bge-small-en-v1-5") while ``model_id`` is the
        # embedder's raw id ("BAAI/bge-small-en-v1.5"). A raw comparison would then be false for
        # every real HuggingFace-style model id, permanently failing ``resolve_for_read`` and
        # silently returning empty retrieval on every query -- a real-model deployment never
        # actually retrieves anything, though HashEmbeddingProvider's plain "local-embeddings"
        # id happens to already be its own slug, which is why no existing test caught this.
        # ``_slug`` is idempotent, so this is exact for an already-slugged value and correct for
        # a raw one either side of the comparison.
        return _slug(self.embedding_model_id) == _slug(model_id) and self.dimension == dimension

    def for_model(self, model_id: str, dimension: int) -> "IndexIdentity":
        return replace(self, embedding_model_id=model_id, dimension=dimension)

    def __str__(self) -> str:
        return self.physical_name


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    """A chunk with its retrieval scores.

    Scores from each retriever are kept separately rather than collapsed, so fusion is
    reproducible and so ``aura_retrieval_top_score`` can be reported per retriever when
    diagnosing why recall dropped.
    """

    chunk: Chunk
    score: float
    dense_score: float | None = None
    lexical_score: float | None = None
    rerank_score: float | None = None
    dense_rank: int | None = None
    lexical_rank: int | None = None
    quarantined: bool = False
    quarantine_reason: str | None = None

    @property
    def content(self) -> str:
        return self.chunk.content

    def citation(self) -> str:
        return self.chunk.citation()


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    """The outcome of one retrieval, including what was *rejected* and why.

    ``below_threshold`` being non-empty while ``chunks`` is empty is the honest "I searched and
    found nothing relevant" signal. The legacy pipeline cannot express this: it always returns
    top-k and labels it "Relevant Context", so the model grounds confident answers in noise
    (see the AI Capability Report).
    """

    chunks: tuple[RetrievedChunk, ...] = ()
    query: str = ""
    candidates_considered: int = 0
    below_threshold: tuple[RetrievedChunk, ...] = ()
    quarantined: tuple[RetrievedChunk, ...] = ()
    strategy: str = "hybrid"
    duration_ms: float = 0.0

    def __bool__(self) -> bool:
        return bool(self.chunks)

    def __len__(self) -> int:
        return len(self.chunks)

    def __iter__(self):
        return iter(self.chunks)

    @property
    def top_score(self) -> float:
        return self.chunks[0].score if self.chunks else 0.0

    @property
    def is_empty_but_searched(self) -> bool:
        return not self.chunks and self.candidates_considered > 0

    @property
    def is_unavailable(self) -> bool:
        """The corpus could not be searched at all (no published index, or one built by another
        embedding model), which is not the same as searching it and finding nothing: nothing was
        looked at, so ``candidates_considered`` is 0 and ``is_empty_but_searched`` is false."""
        return self.strategy == "unavailable"

    def citations(self) -> tuple[str, ...]:
        return tuple(c.citation() for c in self.chunks)


# --------------------------------------------------------------------------------------
# Knowledge graph
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Entity:
    """A node in the knowledge graph.

    ``key`` is the canonical, normalised identity used for merging mentions across documents;
    ``name`` is the surface form most recently seen.
    """

    key: str
    name: str
    type: str
    owner_id: UserId | None = None
    aliases: tuple[str, ...] = ()
    attributes: Mapping[str, Any] = field(default_factory=dict)
    mention_count: int = 1
    first_seen: datetime = field(default_factory=_utcnow)
    last_seen: datetime = field(default_factory=_utcnow)
    sources: tuple[str, ...] = ()
    #: Calibrated belief that this is a real entity of this type (see ``domain/graph.py``). A
    #: store recomputes it from the entity's live provenance, so it falls when a source that
    #: asserted it is superseded or deleted.
    confidence: float = 0.5
    #: Validity interval. ``valid_to`` is set when every source asserting the entity has been
    #: superseded -- the entity is then *expired*, kept for history, and invisible to retrieval.
    valid_from: datetime | None = None
    valid_to: datetime | None = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be within [0, 1]")

    @property
    def is_current(self) -> bool:
        return self.valid_to is None

    @staticmethod
    def canonical_key(name: str, type_: str) -> str:
        return f"{_slug(type_)}:{_slug(name)}"

    def merged_with(self, other: "Entity") -> "Entity":
        attributes = dict(self.attributes)
        attributes.update(other.attributes)
        # dict.fromkeys preserves insertion order while de-duplicating; the canonical name is
        # never repeated as one of its own aliases.
        candidates = dict.fromkeys((*self.aliases, *other.aliases, other.name))
        aliases = tuple(alias for alias in candidates if alias != self.name)
        return replace(
            self,
            aliases=aliases,
            attributes=attributes,
            mention_count=self.mention_count + other.mention_count,
            last_seen=max(self.last_seen, other.last_seen),
            sources=tuple(dict.fromkeys((*self.sources, *other.sources))),
            confidence=max(self.confidence, other.confidence),
        )


@dataclass(frozen=True, slots=True)
class Relation:
    """A typed, directed, evidence-bearing edge.

    ``confidence`` and ``sources`` are mandatory in spirit: an edge with no evidence is not a
    fact, it is a guess, and the graph is only useful if the difference is recorded.
    """

    subject_key: str
    predicate: str
    object_key: str
    owner_id: UserId | None = None
    confidence: float = 0.5
    sources: tuple[str, ...] = ()
    attributes: Mapping[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=_utcnow)
    #: Validity interval, as on :class:`Entity`: an edge whose every asserting source has been
    #: superseded is expired (``valid_to`` set) rather than silently kept as a current fact.
    valid_from: datetime | None = None
    valid_to: datetime | None = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be within [0, 1]")

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.subject_key, self.predicate, self.object_key)

    @property
    def is_current(self) -> bool:
        return self.valid_to is None


@dataclass(frozen=True, slots=True)
class GraphDelta:
    """The graph change produced by ingesting one document version.

    Deltas are applied transactionally and are individually reversible, so a bad extraction in a
    nightly run can be rolled back without rebuilding the graph.
    """

    entities: tuple[Entity, ...] = ()
    relations: tuple[Relation, ...] = ()
    source: str = ""

    def __bool__(self) -> bool:
        return bool(self.entities or self.relations)

    def merge(self, other: "GraphDelta") -> "GraphDelta":
        return GraphDelta(
            entities=self.entities + other.entities,
            relations=self.relations + other.relations,
            source=self.source or other.source,
        )


@dataclass(frozen=True, slots=True)
class KnowledgeSource:
    """A registered, *approved* source for the nightly refresh (ADR-008 step 1).

    The refresh pipeline reads only from this registry. There is no path by which the scheduled
    job fetches an arbitrary URL, which keeps a compromised source list from turning the job
    into an SSRF primitive.
    """

    id: str
    type: SourceType
    uri: str
    enabled: bool = True
    trust_tier: TrustTier = TrustTier.VERIFIED
    owner_id: UserId | None = None
    schedule_cron: str = "0 2 * * *"
    max_documents: int = 1000
    max_bytes_per_document: int = 5 * 1024 * 1024
    credentials_ref: str | None = None
    last_success_at: datetime | None = None
    last_cursor: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    #: Provenance (WP3, KM-04). All optional, so a source registered before this existed keeps
    #: working; the licence fields decide whether a *global* source may be refreshed at all.
    name: str = ""
    publisher: str = ""
    domains: tuple[str, ...] = ()
    language: str | None = None
    license_spdx: str | None = None
    license_status: LicenseStatus = LicenseStatus.UNKNOWN
    #: The credit line a citation must carry when the licence requires attribution (CC BY).
    attribution: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("KnowledgeSource.id must be non-empty")
        if self.max_documents <= 0 or self.max_bytes_per_document <= 0:
            raise ValueError("source ceilings must be positive")

    def with_progress(self, *, cursor: str | None, at: datetime) -> "KnowledgeSource":
        return replace(self, last_cursor=cursor, last_success_at=at)

    def permits_global_corpus(self, allowed_licenses: Sequence[str]) -> bool:
        """Whether this source may feed the corpus every user reads from (D-001).

        A source owned by a person or a project is that owner's own content, so the global
        policy does not apply to it. A global source needs an *approved* status **and** a licence
        id on the allow-list: unknown means "nobody has checked", which is not permission.
        """
        if self.owner_id is not None:
            return True
        if self.license_status is not LicenseStatus.APPROVED or not self.license_spdx:
            return False
        return self.license_spdx.strip().lower() in {a.strip().lower() for a in allowed_licenses}


def estimate_tokens(text: str) -> int:
    """Shared cheap token estimate for chunk sizing.

    Kept here (rather than importing a tokenizer) so the domain layer stays stdlib-only; the
    context engine substitutes exact counts when a tokenizer is available.
    """
    return max(1, (len(text) + 3) // 4)


def build_chunks(
    document: Document,
    pieces: Sequence[tuple[str, tuple[str, ...]]],
    *,
    chunk_metadata: Sequence[Mapping[str, Any]] | None = None,
) -> tuple[Chunk, ...]:
    """Materialise chunks for a document version from ``(content, heading_path)`` pairs.

    ``chunk_metadata`` is index-aligned with ``pieces`` and merged into each chunk's metadata. It
    exists so that per-page provenance from a structured extraction (``modality``, ``page``) reaches
    the vector payload, where ``MultimodalRAG`` can filter on it. Kept as a parallel sequence rather
    than widening the ``pieces`` tuple, because every existing caller passes the output of
    ``chunk_with_headings`` and a shape change would break all of them for one optional feature.

    A shorter ``chunk_metadata`` than ``pieces`` is not an error -- the remaining chunks simply get
    the base metadata -- so a caller that filters pieces after building the metadata list degrades
    to less provenance rather than to a crash.
    """
    extra = tuple(chunk_metadata or ())
    return tuple(
        Chunk(
            id=Chunk.build_id(document.id, document.version, i),
            document_id=document.id,
            document_version=document.version,
            index=i,
            content=content,
            heading_path=heading_path,
            token_estimate=estimate_tokens(content),
            owner_id=document.owner_id,
            trust_tier=document.trust_tier,
            metadata={
                "source_type": str(document.source_type),
                "title": document.title,
                **(dict(extra[i]) if i < len(extra) else {}),
            },
        )
        for i, (content, heading_path) in enumerate(pieces)
    )
