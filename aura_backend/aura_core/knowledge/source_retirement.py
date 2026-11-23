"""Retire every document a source contributed to the corpus (owner decision D3, KB-003).

Removing a source used to delete only its registry row: every document it had already ingested
stayed retrievable by all users, with no way to take withdrawn or mis-licensed content back out.
This finds a source's live documents and removes each one through :meth:`KnowledgeEngine.delete_document`,
the same path a user's own delete takes, so the tombstone, the vector and lexical entries and the
derived graph facts all go.

Which documents a source owns
-----------------------------
The refresh stamps ``source_id`` into each document's metadata, and that is what identifies them.
A document ingested before the stamp existed has no ``source_id`` at all; for those, and only those,
the ``<source id>:root`` id the single-page fetcher always used is accepted. Matching on an id prefix
would be wrong: source ids may contain ``:``, so ``a:`` would also claim the documents of a source
called ``a:b``.

Depends on the ``DocumentStore`` port and an engine's ``delete_document`` only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..domain.identifiers import UserId
from ..domain.knowledge import Document
from ..observability.logging import get_logger

__all__ = ["RetirementReport", "documents_of_source", "retire_source_documents"]

logger = get_logger("knowledge.retirement")

_PAGE = 100
#: A ceiling on how many documents one listing walks, so a bad store cannot loop forever.
_MAX_DOCUMENTS = 100_000


@dataclass(frozen=True, slots=True)
class RetirementReport:
    source_id: str
    matched: int
    retired: int
    chunks_removed: int
    failed: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        return not self.failed

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "matched": self.matched,
            "retired": self.retired,
            "chunks_removed": self.chunks_removed,
            "failed": list(self.failed),
            "complete": self.complete,
        }


def _belongs_to(document: Document, source_id: str) -> bool:
    stamped = document.metadata.get("source_id")
    if stamped is not None:
        return str(stamped) == source_id
    return document.id.value == f"{source_id}:root"


async def documents_of_source(
    store: Any, source_id: str, *, owner_id: UserId | None
) -> list[Document]:
    """The active documents ``source_id`` contributed to ``owner_id``'s corpus (``None``: global).

    Every page is read before the caller deletes anything, because deleting while paging would
    shift the offsets and skip documents.
    """
    found: list[Document] = []
    offset = 0
    while offset < _MAX_DOCUMENTS:
        page = await store.list_active(owner_id=owner_id, limit=_PAGE, offset=offset)
        if not page:
            break
        found.extend(document for document in page if _belongs_to(document, source_id))
        if len(page) < _PAGE:
            break
        offset += _PAGE
    return found


async def retire_source_documents(
    engine: Any, store: Any, source_id: str, *, owner_id: UserId | None
) -> RetirementReport:
    """Delete each of the source's documents; a document that fails is reported, not fatal.

    Safe to run again: it lists what is still active, so a second run finishes what a first one
    could not.
    """
    documents = await documents_of_source(store, source_id, owner_id=owner_id)
    retired = chunks_removed = 0
    failed: list[str] = []
    for document in documents:
        try:
            chunks_removed += await engine.delete_document(document.id, owner_id=owner_id)
            retired += 1
        except Exception as exc:
            failed.append(document.id.value)
            logger.warning(
                "could not retire a source's document",
                extra={"source": source_id, "document": document.id.value,
                       "error": type(exc).__name__},
            )
    return RetirementReport(
        source_id=source_id, matched=len(documents), retired=retired,
        chunks_removed=chunks_removed, failed=tuple(failed),
    )
