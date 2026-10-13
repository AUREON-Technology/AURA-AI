"""Project knowledge (AP10): a deployed blueprint's documents, indexed under the project's own owner.

A blueprint's ``knowledge.sources`` name DOCUMENT elements of the twin -- files a connector
discovered. :meth:`ProjectKnowledge.sync` fetches each one through its connector, extracts its
text, and ingests it through the ordinary :class:`~aura_core.knowledge.engine.KnowledgeEngine`
path: the same chunking, injection guards and quarantine every upload gets, at
``TrustTier.UNTRUSTED`` because the text was written by whoever administers the connected system.

**Ownership is the isolation mechanism.** Every chunk is owned by :func:`project_owner` --
``project:<project id>``, namespaced the way a service key's ``service:<name>`` principal is, so it
can never equal a person's subject (the gateway refuses subjects in these namespaces). Since CB-11
an owner filter is an exact match and only the ``global`` sentinel is shared, which gives three
properties without any new filter code:

* a person's retrieval (the kernel's knowledge stage, ``search_knowledge``) never sees a project
  document, because it filters on that person's id;
* project search runs with ``include_global=False``, so a project agent answers from its project's
  documents, not from whatever the install's global corpus holds;
* one project's documents never reach another project's search.

Sync is idempotent and converges: an unchanged file is skipped on its content hash, a changed one
becomes a new version of the same document, and a document of this project that no source names
any more is deleted. One unreadable file is a recorded failure, never a failed deploy.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from ..domain.ausm import ElementKind, ProjectTwin
from ..domain.blueprint import Blueprint
from ..domain.errors import AuraError, ValidationError
from ..domain.identifiers import DocumentId, UserId
from ..domain.knowledge import SourceType, TrustTier
from ..observability.logging import get_logger
from ..ports.connectors import DocumentConnector

__all__ = [
    "PROJECT_OWNER_PREFIX",
    "project_owner",
    "project_document_id",
    "KnowledgeSyncReport",
    "ProjectKnowledge",
]

logger = get_logger("adaptation.knowledge")

#: Owner namespace of project knowledge. A person's subject never starts with it.
PROJECT_OWNER_PREFIX = "project:"
_PAGE = 100


def project_owner(project_id: str) -> UserId:
    """The owner every chunk of ``project_id``'s knowledge is written and searched under."""
    if not project_id or ":" in project_id:
        raise ValidationError("a project id is a non-empty slug", details={"project_id": project_id})
    return UserId(f"{PROJECT_OWNER_PREFIX}{project_id}")


def project_document_id(project_id: str, element: str) -> DocumentId:
    """A stable document id per (project, source element): re-syncs version it, never duplicate it."""
    digest = hashlib.sha256(f"{project_id}\x00{element}".encode("utf-8")).hexdigest()[:32]
    return DocumentId(f"pk-{digest}")


@dataclass(frozen=True, slots=True)
class KnowledgeSyncReport:
    """What one sync did, per source element. ``failed`` pairs an element with an error kind."""

    version: int
    ingested: tuple[str, ...] = ()
    unchanged: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    failed: tuple[tuple[str, str], ...] = ()
    #: Sources whose bytes no longer match the hash the twin recorded at discovery.
    changed_since_discovery: tuple[str, ...] = ()

    def summary(self) -> dict[str, Any]:
        return {
            "version": self.version, "ingested": len(self.ingested), "unchanged": len(self.unchanged),
            "removed": len(self.removed), "failed": [{"element": e, "kind": k} for e, k in self.failed],
            "changed_since_discovery": list(self.changed_since_discovery),
        }


class ProjectKnowledge:
    """Ingests a blueprint's knowledge sources and searches them, for one project.

    ``knowledge`` is the install's knowledge engine (``ingest``, ``retrieve``, ``delete_document``,
    and a document store with ``list_active``); ``extract`` turns a fetched document's bytes into
    blocks (``knowledge.documents.extract_document``). Both are injected by the container, which is
    what keeps this module out of the knowledge package (ADR-018).
    """

    def __init__(self, *, project_id: str, knowledge: Any, documents: Any,
                 extract: Callable[..., Any], open_connector: Callable[[str], Any]) -> None:
        self._project_id = project_id
        self._owner = project_owner(project_id)
        self._knowledge = knowledge
        self._documents = documents
        self._extract = extract
        self._open = open_connector
        self._lock = asyncio.Lock()

    @property
    def owner(self) -> UserId:
        return self._owner

    async def search(self, query: str, *, limit: int = 5) -> Any:
        """This project's passages for ``query`` -- never a person's documents, never global ones."""
        return await self._knowledge.retrieve(query, owner_id=self._owner, limit=limit, include_global=False)

    async def sync(self, blueprint: Blueprint, twin: ProjectTwin | None = None) -> KnowledgeSyncReport:
        """Make the project's indexed documents exactly the blueprint's knowledge sources."""
        if blueprint.project.id != self._project_id:
            raise ValidationError("that blueprint belongs to another project",
                                  details={"project_id": blueprint.project.id})
        recorded = _recorded_hashes(twin)
        async with self._lock:
            ingested: list[str] = []
            unchanged: list[str] = []
            failed: list[tuple[str, str]] = []
            drifted: list[str] = []
            wanted = {s.element for s in blueprint.knowledge.sources}
            for source in blueprint.knowledge.sources:
                try:
                    outcome, sha = await self._ingest(source.connector, source.element)
                except AuraError as exc:
                    logger.warning("project document not ingested",
                                   extra={"element": source.element, "kind": exc.kind})
                    failed.append((source.element, exc.kind))
                    continue
                (unchanged if outcome == "unchanged_content" else ingested).append(source.element)
                if source.element in recorded and recorded[source.element] != sha:
                    drifted.append(source.element)
            removed = await self._remove_unwanted(wanted)
        return KnowledgeSyncReport(blueprint.project.version, tuple(ingested), tuple(unchanged), tuple(removed),
                                   tuple(failed), tuple(drifted))

    async def _ingest(self, connector_id: str, element: str) -> tuple[str | None, str]:
        locator = element.removeprefix(f"{connector_id}:")
        connector = await self._open(connector_id)
        try:
            if not isinstance(connector, DocumentConnector):
                raise ValidationError("this connector cannot fetch documents", details={"connector": connector_id})
            document = await connector.fetch_document(locator)
        finally:
            await connector.aclose()
        extraction = self._extract(document.data, format=document.format, title=document.name)
        segments = tuple(extraction.blocks)
        if not segments:
            raise ValidationError("the document has no readable text", details={"element": element})
        result = await self._knowledge.ingest(
            title=document.name,
            document_id=project_document_id(self._project_id, element),
            owner_id=self._owner,
            source_type=SourceType.API,
            source_uri=f"connector://{connector_id}/{locator}",
            trust_tier=TrustTier.UNTRUSTED,
            metadata={"project_id": self._project_id, "connector": connector_id, "element": element,
                      "format": document.format},
            segments=segments,
        )
        return result.skipped_reason, document.sha256

    async def _remove_unwanted(self, wanted: set[str]) -> list[str]:
        stale = []
        offset = 0
        while True:
            page = await self._documents.list_active(owner_id=self._owner, limit=_PAGE, offset=offset)
            # The PostgreSQL store lists an owner's documents *plus* global ones; only the
            # project's own are ever candidates for removal.
            stale.extend(d for d in page
                         if d.owner_id == self._owner and d.metadata.get("element") not in wanted)
            if len(page) < _PAGE:
                break
            offset += _PAGE
        removed = []
        for document in stale:
            try:
                if await self._knowledge.delete_document(document.id, owner_id=self._owner):
                    removed.append(str(document.metadata.get("element") or document.id.value))
            except AuraError as exc:
                logger.warning("stale project document not removed",
                               extra={"document": document.id.value, "kind": exc.kind})
        return removed


def _recorded_hashes(twin: ProjectTwin | None) -> Mapping[str, str]:
    if twin is None:
        return {}
    return {e.id: e.properties["sha256"] for e in twin.of_kind(ElementKind.DOCUMENT)
            if isinstance(e.properties.get("sha256"), str)}
