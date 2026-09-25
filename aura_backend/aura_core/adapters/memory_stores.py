"""In-memory adapters for every storage port.

These are the reference implementations. They exist for three reasons, in order of importance:

1. **The test suite runs without infrastructure.** Every engine, the kernel, and the API can be
   exercised end to end on bare CPython. That is the difference between a test suite that runs
   in CI and one that requires a Docker Compose stack nobody starts locally.
2. **They define the contract.** A Qdrant or Postgres adapter is correct if it behaves like
   these; ``tests/unit/adapters/test_store_contract.py`` runs the same suite against both.
3. **Local development needs no services.** ``python -m aura_core.cli chat`` works on a fresh
   clone.

They are refused in staging and production by ``AuraSettings.validate()``: an in-memory vector
store loses every document on restart, which is not a system of record.

Concurrency: guarded by ``asyncio.Lock`` where a read-modify-write sequence could interleave.
Single-process asyncio does not need a lock for atomic dict operations, but ``upsert`` and
``apply`` are not atomic, and a concurrent turn can interleave at every ``await``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Mapping, Sequence

from ..domain.agent_runs import AgentRun, AgentStep
from ..domain.engineering import AgentWorkspace, EngineerRun
from ..domain.errors import ConflictError, NotFoundError, ValidationError
from ..domain.github_identity import GitHubIdentity
from ..domain.goals import GoalSnapshot
from ..domain.identifiers import DocumentId, MemoryId, PersonalOsRecordId, UserId, WorkspaceId
from ..domain.knowledge import (
    Chunk,
    Document,
    DocumentStatus,
    Entity,
    GraphDelta,
    IndexIdentity,
    Relation,
)
from ..domain.memory import MemoryKind, MemoryRecord
from ..domain.personal_os import PersonalOsRecord, RecordKind
from dataclasses import replace as _dc_replace
from ..llm.embeddings import cosine_similarity
from ..ports.storage import VectorPoint

__all__ = [
    "InMemoryVectorStore",
    "InMemoryDocumentStore",
    "InMemoryMemoryStore",
    "InMemoryPersonalOsStore",
    "InMemoryGraphStore",
    "InMemoryGoalsPort",
    "InMemoryInsightSink",
    "InMemoryGitHubIdentityPort",
    "InMemoryWorkspaceStore",
]


class InMemoryVectorStore:
    """Exhaustive-scan vector search.

    O(n) per query with no ANN index. That is correct rather than fast, which is the right
    trade for a reference implementation -- an approximate index here would make test
    assertions about ranking non-deterministic, and the production adapter is Qdrant.
    """

    def __init__(self) -> None:
        self._indexes: dict[str, dict[str, VectorPoint]] = {}
        self._identities: dict[str, IndexIdentity] = {}
        self._aliases: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def ensure_index(self, identity: IndexIdentity) -> None:
        name = identity.physical_name
        async with self._lock:
            existing = self._identities.get(name)
            if existing is None:
                self._indexes[name] = {}
                self._identities[name] = identity
                return
            if existing != identity:
                # Never silently reuse an index whose parameters differ -- the failure mode this
                # whole design exists to prevent (SEC-03).
                raise ConflictError(
                    f"index {name} exists with different parameters",
                    details={"existing": str(existing), "requested": str(identity)},
                )

    async def index_exists(self, identity: IndexIdentity) -> bool:
        return identity.physical_name in self._indexes

    async def upsert(self, identity: IndexIdentity, points: Sequence[VectorPoint]) -> int:
        await self.ensure_index(identity)
        index = self._indexes[identity.physical_name]
        async with self._lock:
            for point in points:
                if len(point.vector) != identity.dimension:
                    raise ValidationError(
                        f"vector dimension {len(point.vector)} does not match index "
                        f"dimension {identity.dimension}",
                        details={"index": identity.physical_name},
                    )
                index[point.id] = point
        return len(points)

    @staticmethod
    def _matches(payload: Mapping[str, Any], filters: Mapping[str, Any] | None) -> bool:
        if not filters:
            return True
        for key, wanted in filters.items():
            value = payload.get(key)
            if isinstance(wanted, (list, tuple, set)):
                if value not in wanted:
                    return False
            elif value != wanted:
                return False
        return True

    async def search(
        self,
        identity: IndexIdentity,
        vector: Sequence[float],
        *,
        limit: int,
        owner_id: UserId | None,
        include_global: bool = True,
        filters: Mapping[str, Any] | None = None,
        score_threshold: float | None = None,
    ) -> list[tuple[str, float, Mapping[str, Any]]]:
        index = self._indexes.get(identity.physical_name)
        if not index:
            return []

        # Tenant isolation is applied before scoring, not after: filtering a scored list would
        # mean a cross-tenant document could displace a legitimate one out of the top-k.
        owner_value = owner_id.value if owner_id else None
        results: list[tuple[str, float, Mapping[str, Any]]] = []
        for point in index.values():
            payload_owner = point.payload.get("owner_id")
            if owner_value is None:
                if payload_owner not in (None, "", "global"):
                    continue
            elif payload_owner != owner_value:
                if not (include_global and payload_owner in (None, "", "global")):
                    continue
            if not self._matches(point.payload, filters):
                continue
            score = cosine_similarity(vector, point.vector)
            if score_threshold is not None and score < score_threshold:
                continue
            results.append((point.id, score, point.payload))

        results.sort(key=lambda item: item[1], reverse=True)
        return results[:limit]

    async def delete(self, identity: IndexIdentity, *, point_ids: Sequence[str]) -> int:
        index = self._indexes.get(identity.physical_name)
        if not index:
            return 0
        async with self._lock:
            return sum(1 for pid in point_ids if index.pop(pid, None) is not None)

    async def delete_by_filter(
        self, identity: IndexIdentity, *, filters: Mapping[str, Any], owner_id: UserId | None
    ) -> int:
        index = self._indexes.get(identity.physical_name)
        if not index:
            return 0
        owner_value = owner_id.value if owner_id else None
        async with self._lock:
            doomed = [
                pid
                for pid, point in index.items()
                if self._matches(point.payload, filters)
                and (owner_value is None or point.payload.get("owner_id") == owner_value)
            ]
            for pid in doomed:
                index.pop(pid, None)
        return len(doomed)

    async def count(self, identity: IndexIdentity, *, owner_id: UserId | None = None) -> int:
        index = self._indexes.get(identity.physical_name)
        if not index:
            return 0
        if owner_id is None:
            return len(index)
        return sum(1 for p in index.values() if p.payload.get("owner_id") == owner_id.value)

    async def set_alias(self, alias: str, identity: IndexIdentity) -> None:
        await self.ensure_index(identity)
        self._aliases[alias] = identity.physical_name

    async def resolve_alias(self, alias: str) -> IndexIdentity | None:
        physical = self._aliases.get(alias)
        return self._identities.get(physical) if physical else None

    async def drop_index(self, identity: IndexIdentity) -> None:
        name = identity.physical_name
        if name in self._aliases.values():
            raise ConflictError(
                f"cannot drop {name}: an alias still points at it",
                details={"aliases": [a for a, p in self._aliases.items() if p == name]},
            )
        self._indexes.pop(name, None)
        self._identities.pop(name, None)

    def index_names(self) -> list[str]:
        return sorted(self._indexes)


class InMemoryDocumentStore:
    def __init__(self) -> None:
        self._documents: dict[tuple[str, int], Document] = {}
        self._chunks: dict[tuple[str, int], list[Chunk]] = {}
        self._lock = asyncio.Lock()

    async def put(self, document: Document, chunks: Sequence[Chunk]) -> None:
        key = (document.id.value, document.version)
        async with self._lock:
            self._documents[key] = document
            self._chunks[key] = list(chunks)

    async def get(self, document_id: DocumentId, *, version: int | None = None) -> Document | None:
        if version is not None:
            return self._documents.get((document_id.value, version))
        candidates = [
            d for (doc_id, _), d in self._documents.items()
            if doc_id == document_id.value and d.status is DocumentStatus.ACTIVE
        ]
        return max(candidates, key=lambda d: d.version) if candidates else None

    async def get_by_hash(self, content_hash: str, *, owner_id: UserId | None) -> Document | None:
        """The newest *active* version with this content, matching the Postgres adapter's
        ``status = 'active'`` filter. Returning a superseded or tombstoned version made a
        deleted document impossible to upload again, and a revert to earlier content a no-op --
        both skipped as "unchanged" against a version nobody could retrieve any more."""
        owner = owner_id.value if owner_id else None
        matches = [
            d for d in self._documents.values()
            if d.status is DocumentStatus.ACTIVE
            and (d.owner_id.value if d.owner_id else None) == owner
            and d.content_hash == content_hash
        ]
        return max(matches, key=lambda d: d.version) if matches else None

    async def chunks_for(self, document_id: DocumentId, version: int) -> list[Chunk]:
        return list(self._chunks.get((document_id.value, version), []))

    async def list_active(
        self, *, owner_id: UserId | None, limit: int = 100, offset: int = 0
    ) -> list[Document]:
        owner = owner_id.value if owner_id else None
        active = [
            d for d in self._documents.values()
            if d.status is DocumentStatus.ACTIVE
            and ((d.owner_id.value if d.owner_id else None) == owner)
        ]
        active.sort(key=lambda d: (d.id.value, -d.version))
        return active[offset : offset + limit]

    async def set_cag_pin(
        self, document_id: DocumentId, *, owner_id: UserId | None, pinned: bool
    ) -> Document:
        async with self._lock:
            document = await self.get(document_id)
            if document is None:
                raise NotFoundError(
                    f"document {document_id.value!r} not found",
                    details={"document_id": document_id.value},
                )
            doc_owner = document.owner_id.value if document.owner_id else None
            caller = owner_id.value if owner_id else None
            if doc_owner != caller:
                raise NotFoundError(
                    f"document {document_id.value!r} not found",
                    details={"document_id": document_id.value},
                )
            updated = _dc_replace(document, cag_pinned=pinned)
            self._documents[(document.id.value, document.version)] = updated
            return updated

    async def list_cag_pinned(self, *, owner_id: UserId | None) -> list[Document]:
        owner = owner_id.value if owner_id else None
        matches = [
            d for d in self._documents.values()
            if d.status is DocumentStatus.ACTIVE
            and d.cag_pinned
            and (
                (d.owner_id is None)
                or (owner is not None and d.owner_id is not None and d.owner_id.value == owner)
            )
        ]
        # Deduplicate to active version only (get already prefers active, but put may have
        # multiple versions; keep highest version per id).
        by_id: dict[str, Document] = {}
        for doc in matches:
            prev = by_id.get(doc.id.value)
            if prev is None or doc.version > prev.version:
                by_id[doc.id.value] = doc
        return sorted(
            by_id.values(),
            key=lambda d: (d.owner_id is not None, -int(d.trust_tier), d.id.value, -d.version),
        )

    async def iter_all_chunks(self, *, batch_size: int = 500) -> AsyncIterator[list[Chunk]]:
        """Stream chunks for index rebuilds.

        Streaming rather than materialising: a rebuild must not be bounded by available memory,
        and the Postgres adapter implementing this port will page over a cursor.
        """
        batch: list[Chunk] = []
        for (doc_id, version), document in list(self._documents.items()):
            if document.status is not DocumentStatus.ACTIVE:
                continue
            for chunk in self._chunks.get((doc_id, version), []):
                batch.append(chunk)
                if len(batch) >= batch_size:
                    yield batch
                    batch = []
        if batch:
            yield batch

    async def supersede(self, document_id: DocumentId, *, old_version: int, new_version: int) -> None:
        key = (document_id.value, old_version)
        document = self._documents.get(key)
        if document is not None:
            self._documents[key] = document.superseded()

    async def tombstone(self, document_id: DocumentId, *, owner_id: UserId | None) -> int:
        removed = 0
        owner = owner_id.value if owner_id else None
        async with self._lock:
            for key, document in list(self._documents.items()):
                if key[0] != document_id.value:
                    continue
                if (document.owner_id.value if document.owner_id else None) != owner:
                    continue
                self._documents[key] = _dc_replace(document, status=DocumentStatus.TOMBSTONED)
                removed += len(self._chunks.get(key, []))
        return removed


class InMemoryMemoryStore:
    def __init__(self) -> None:
        self._records: dict[str, MemoryRecord] = {}
        self._lock = asyncio.Lock()

    async def put(self, record: MemoryRecord) -> None:
        async with self._lock:
            self._records[record.id.value] = record

    async def get(self, memory_id: MemoryId) -> MemoryRecord | None:
        return self._records.get(memory_id.value)

    async def get_many(self, memory_ids: Sequence[MemoryId]) -> list[MemoryRecord]:
        return [self._records[m.value] for m in memory_ids if m.value in self._records]

    async def list_for_owner(
        self,
        owner_id: UserId,
        *,
        kinds: Sequence[MemoryKind] | None = None,
        subject: str | None = None,
        include_superseded: bool = False,
        limit: int = 200,
        offset: int = 0,
    ) -> list[MemoryRecord]:
        wanted = set(kinds) if kinds else None
        matches = [
            r for r in self._records.values()
            if r.owner_id == owner_id
            and (wanted is None or r.kind in wanted)
            and (subject is None or r.subject == subject)
            and (include_superseded or r.superseded_by is None)
        ]
        matches.sort(key=lambda r: r.created_at, reverse=True)
        return matches[offset : offset + limit]

    async def update(self, record: MemoryRecord) -> None:
        async with self._lock:
            self._records[record.id.value] = record

    async def delete(self, memory_id: MemoryId, *, owner_id: UserId) -> bool:
        record = self._records.get(memory_id.value)
        if record is None or record.owner_id != owner_id:
            # Ownership is checked on delete, not assumed. A memory id is not a capability.
            return False
        async with self._lock:
            self._records.pop(memory_id.value, None)
        return True

    async def delete_by_provenance(self, *, owner_id: UserId, source: str) -> int:
        async with self._lock:
            doomed = [
                mid for mid, r in self._records.items()
                if r.owner_id == owner_id and r.provenance.source == source
            ]
            for mid in doomed:
                self._records.pop(mid, None)
        return len(doomed)

    async def count(self, owner_id: UserId, *, kind: MemoryKind | None = None) -> int:
        return sum(
            1 for r in self._records.values()
            if r.owner_id == owner_id and (kind is None or r.kind == kind)
        )


class InMemoryPersonalOsStore:
    """Reference implementation of :class:`~aura_core.ports.storage.PersonalOsStore` (Phase C2)."""

    def __init__(self) -> None:
        self._records: dict[str, PersonalOsRecord] = {}
        self._lock = asyncio.Lock()

    async def put(self, record: PersonalOsRecord) -> None:
        async with self._lock:
            self._records[record.id.value] = record

    async def get(
        self, record_id: PersonalOsRecordId, *, owner_id: UserId
    ) -> PersonalOsRecord | None:
        record = self._records.get(record_id.value)
        if record is None or record.owner_id != owner_id:
            return None
        return record

    async def list_for_owner(
        self, owner_id: UserId, *, kind: RecordKind | None = None, limit: int = 100,
    ) -> list[PersonalOsRecord]:
        matches = [
            r for r in self._records.values()
            if r.owner_id == owner_id and (kind is None or r.kind == kind)
        ]
        matches.sort(key=lambda r: r.created_at, reverse=True)
        return matches[:limit]

    async def update(self, record: PersonalOsRecord) -> None:
        async with self._lock:
            self._records[record.id.value] = record

    async def delete(self, record_id: PersonalOsRecordId, *, owner_id: UserId) -> bool:
        record = self._records.get(record_id.value)
        if record is None or record.owner_id != owner_id:
            return False
        async with self._lock:
            self._records.pop(record_id.value, None)
        return True


class InMemoryEngineerRunStore:
    """Reference implementation of :class:`~aura_core.ports.coding.AgentRunStore` -- the
    engineer-run ledger plus the per-run agent state that generalises it (GAP-024). Engineer
    runs are read-only from a caller's perspective beyond :meth:`record`; agent runs are
    upserted by id and their steps appended, exactly as the Postgres adapter does."""

    def __init__(self) -> None:
        self._runs: list[EngineerRun] = []
        self._agent_runs: dict[str, AgentRun] = {}
        self._agent_steps: dict[str, list[AgentStep]] = {}
        self._lock = asyncio.Lock()

    # -- agent runs (any persona) ----------------------------------------------------------

    async def save_run(self, run: AgentRun) -> None:
        async with self._lock:
            existing = self._agent_runs.get(run.id.value)
            if existing is not None and existing.user_id != run.user_id:
                # The Postgres upsert keys on id alone; a second owner claiming an existing id is
                # a bug upstream, and silently re-owning the row would be a cross-user write.
                raise ValueError("agent run id already belongs to another user")
            self._agent_runs[run.id.value] = run

    async def append_step(self, step: AgentStep) -> None:
        async with self._lock:
            self._agent_steps.setdefault(step.run_id.value, []).append(step)

    async def get_run(self, run_id: Any, *, user_id: UserId) -> AgentRun | None:
        run = self._agent_runs.get(run_id.value)
        if run is None or run.user_id != user_id:
            return None
        return run

    async def list_runs(
        self,
        user_id: UserId,
        *,
        agent_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[AgentRun]:
        matches = [
            r for r in self._agent_runs.values()
            if r.user_id == user_id
            and (agent_id is None or r.agent_id == agent_id)
            and (status is None or r.status == status)
        ]
        matches.sort(key=lambda r: (r.created_at, r.id.value), reverse=True)
        return matches[:limit]

    async def list_steps(
        self, run_id: Any, *, user_id: UserId, limit: int = 500,
    ) -> list[AgentStep]:
        if await self.get_run(run_id, user_id=user_id) is None:
            return []
        steps = [s for s in self._agent_steps.get(run_id.value, []) if s.user_id == user_id]
        steps.sort(key=lambda s: s.seq)
        return steps[:limit]

    # -- engineer runs -----------------------------------------------------------------------

    async def record(self, run: EngineerRun) -> None:
        async with self._lock:
            self._runs.append(run)

    async def list_for_user(
        self, user_id: UserId, *, since: datetime | None = None, limit: int = 200,
    ) -> list[EngineerRun]:
        matches = [
            r for r in self._runs
            if r.user_id == user_id and (since is None or r.created_at >= since)
        ]
        matches.sort(key=lambda r: r.created_at, reverse=True)
        return matches[:limit]


class InMemoryWorkspaceStore:
    """Reference implementation of :class:`~aura_core.ports.coding.WorkspaceStore`.

    A real implementation, not a mock: the uniqueness rule on ``(user_id, repo)`` is enforced
    here exactly as the Postgres index enforces it, so a test that would provision two workspaces
    for one repository fails against this substrate too rather than only in production.
    """

    def __init__(self) -> None:
        self._by_id: dict[str, AgentWorkspace] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _key(user_id: UserId, repo: str) -> tuple[str, str]:
        return (user_id.value, repo)

    async def upsert(self, workspace: AgentWorkspace) -> None:
        async with self._lock:
            existing = next(
                (
                    w for w in self._by_id.values()
                    if self._key(w.user_id, w.repo) == self._key(workspace.user_id, workspace.repo)
                    and w.id != workspace.id
                ),
                None,
            )
            if existing is not None:
                # Mirrors ix_aura_agent_workspaces_user_repo. Two rows for one checkout would
                # mean two containers racing on the same directory.
                raise ValueError(
                    f"a workspace for {workspace.repo!r} already exists for this user "
                    f"({existing.id.value})"
                )
            self._by_id[workspace.id.value] = workspace

    async def get(self, user_id: UserId, repo: str) -> AgentWorkspace | None:
        async with self._lock:
            for workspace in self._by_id.values():
                if self._key(workspace.user_id, workspace.repo) == self._key(user_id, repo):
                    return workspace
        return None

    async def get_by_id(self, workspace_id: WorkspaceId) -> AgentWorkspace | None:
        async with self._lock:
            return self._by_id.get(workspace_id.value)

    async def list_for_user(self, user_id: UserId) -> list[AgentWorkspace]:
        async with self._lock:
            matches = [w for w in self._by_id.values() if w.user_id == user_id]
        matches.sort(key=lambda w: w.last_active_at, reverse=True)
        return matches

    async def list_all(self) -> list[AgentWorkspace]:
        async with self._lock:
            matches = list(self._by_id.values())
        matches.sort(key=lambda w: w.last_active_at, reverse=True)
        return matches

    async def list_idle_since(self, cutoff: datetime) -> list[AgentWorkspace]:
        async with self._lock:
            matches = [w for w in self._by_id.values() if w.last_active_at <= cutoff]
        matches.sort(key=lambda w: w.last_active_at)
        return matches

    async def touch(
        self, workspace_id: WorkspaceId, *, at: datetime, status: str | None = None
    ) -> None:
        async with self._lock:
            current = self._by_id.get(workspace_id.value)
            if current is None:
                return
            self._by_id[workspace_id.value] = _dc_replace(
                current,
                last_active_at=at,
                status=current.status if status is None else status,
            )

    async def delete(self, workspace_id: WorkspaceId) -> None:
        async with self._lock:
            self._by_id.pop(workspace_id.value, None)


_FAR_FUTURE = datetime(9999, 12, 31, tzinfo=timezone.utc)


class InMemoryGoalsPort:
    """Reference implementation of :class:`~aura_core.ports.storage.GoalsPort` (Phase D2).

    Test/local-dev only: the real source of truth is the legacy ``goals`` table synced from the
    Flutter client, which only :class:`~aura_core.adapters.postgres_goals.PostgresGoalsPort`
    can see. ``seed`` lets a test populate goals directly, since there is no write path here.
    """

    def __init__(self) -> None:
        self._by_owner: dict[str, list[GoalSnapshot]] = {}

    def seed(self, owner_id: UserId, snapshots: Sequence[GoalSnapshot]) -> None:
        self._by_owner.setdefault(owner_id.value, []).extend(snapshots)

    async def active_summaries(
        self, owner_id: UserId, *, limit: int = 5
    ) -> Sequence[GoalSnapshot]:
        goals = [g for g in self._by_owner.get(owner_id.value, []) if g.status == "active"]
        priority_rank = {"high": 0, "medium": 1, "low": 2}
        goals.sort(
            key=lambda g: (
                priority_rank.get(g.priority, 1),
                g.target_date or _FAR_FUTURE,
            )
        )
        return goals[:limit]


class InMemoryGitHubIdentityPort:
    """Reference implementation of :class:`~aura_core.ports.storage.GitHubIdentityPort`.

    Test/local-dev only: a real deployment's identities live in Postgres
    (:class:`~aura_core.adapters.postgres_github_identity.PostgresGitHubIdentityPort`), encrypted
    at rest. Here they sit in a plain dict -- fine for tests, since a real per-user OAuth token
    must never exist outside that encrypted table.
    """

    def __init__(self) -> None:
        self._by_user: dict[str, GitHubIdentity] = {}

    async def get(self, user_id: UserId) -> GitHubIdentity | None:
        return self._by_user.get(user_id.value)

    async def upsert(self, user_id: UserId, identity: GitHubIdentity) -> None:
        self._by_user[user_id.value] = identity

    async def delete(self, user_id: UserId) -> None:
        self._by_user.pop(user_id.value, None)


class InMemoryInsightSink:
    """Reference implementation of :class:`~aura_core.ports.storage.InsightSink` (Phase E2).

    ``surfaced`` is a plain list, most recent last -- tests read it directly rather than through
    a query method, since the only real consumer of this port's *output* is the Flutter Today
    screen reading the legacy ``insights`` table via :class:`PostgresInsightSink`.
    """

    def __init__(self) -> None:
        self.surfaced: list[dict] = []
        self._seen_ids: set[str] = set()

    async def surface(
        self, owner_id: UserId, *, insight_id: str, kind: str, message: str, explanation: str,
    ) -> None:
        if insight_id in self._seen_ids:
            return
        self._seen_ids.add(insight_id)
        self.surfaced.append({
            "owner_id": owner_id, "insight_id": insight_id, "kind": kind,
            "message": message, "explanation": explanation,
        })


class InMemoryGraphStore:
    def __init__(self) -> None:
        self._entities: dict[tuple[str | None, str], Entity] = {}
        self._relations: dict[tuple[str | None, str, str, str], Relation] = {}
        self._lock = asyncio.Lock()

    async def apply(self, delta: GraphDelta, *, owner_id: UserId | None) -> None:
        owner = owner_id.value if owner_id else None
        async with self._lock:
            for entity in delta.entities:
                key = (owner, entity.key)
                existing = self._entities.get(key)
                # Merge rather than overwrite: a second mention of the same entity should add
                # attributes and aliases, not replace what was already known.
                self._entities[key] = existing.merged_with(entity) if existing else entity
            for relation in delta.relations:
                key = (owner, relation.subject_key, relation.predicate, relation.object_key)
                existing = self._relations.get(key)
                if existing is None or relation.confidence > existing.confidence:
                    self._relations[key] = relation

    async def get_entity(self, key: str, *, owner_id: UserId | None) -> Entity | None:
        return self._entities.get((owner_id.value if owner_id else None, key))

    async def find_entities(
        self, *, owner_id: UserId | None, name: str | None = None, type: str | None = None,
        limit: int = 50,
    ) -> list[Entity]:
        owner = owner_id.value if owner_id else None
        needle = name.lower() if name else None
        matches = [
            e for (o, _), e in self._entities.items()
            if o == owner
            and (type is None or e.type == type)
            and (
                needle is None
                or needle in e.name.lower()
                or any(needle in alias.lower() for alias in e.aliases)
            )
        ]
        matches.sort(key=lambda e: e.mention_count, reverse=True)
        return matches[:limit]

    async def neighbours(
        self, key: str, *, owner_id: UserId | None, depth: int = 1, limit: int = 50
    ) -> tuple[list[Entity], list[Relation]]:
        """Breadth-first traversal with a hard depth cap.

        Depth is clamped to 3 regardless of what the caller asks for: an unbounded graph walk
        driven by model-chosen arguments is a denial-of-service primitive.
        """
        owner = owner_id.value if owner_id else None
        depth = max(1, min(depth, 3))

        frontier = {key}
        seen = {key}
        edges: list[Relation] = []
        for _ in range(depth):
            next_frontier: set[str] = set()
            for (o, subject, _predicate, obj), relation in self._relations.items():
                if o != owner:
                    continue
                if subject in frontier and obj not in seen:
                    edges.append(relation)
                    next_frontier.add(obj)
                elif obj in frontier and subject not in seen:
                    edges.append(relation)
                    next_frontier.add(subject)
                if len(edges) >= limit:
                    break
            seen |= next_frontier
            frontier = next_frontier
            if not frontier or len(edges) >= limit:
                break

        entities = [self._entities[(owner, k)] for k in seen if (owner, k) in self._entities]
        return entities, edges[:limit]

    async def revert(self, source: str, *, owner_id: UserId | None) -> int:
        owner = owner_id.value if owner_id else None
        async with self._lock:
            doomed_relations = [
                k for k, r in self._relations.items() if k[0] == owner and source in r.sources
            ]
            for k in doomed_relations:
                self._relations.pop(k, None)
            doomed_entities = [
                k for k, e in self._entities.items()
                if k[0] == owner and e.sources == (source,)
            ]
            for k in doomed_entities:
                self._entities.pop(k, None)
        return len(doomed_relations) + len(doomed_entities)

    def size(self) -> tuple[int, int]:
        return len(self._entities), len(self._relations)
