"""Knowledge erasure against a real Postgres: ``PostgresDocumentStore.purge_owner``.

Opt-in (``-m load``); skips unless ``AURA_TEST_DATABASE_URL`` names a superuser DSN (see
``test_adaptation_postgres.py``).

A tombstone keeps chunk text in ``aura_chunks``, so deleting documents one by one never erased
them. ``KnowledgeEngine.erase_owner`` deletes each active document (vectors, lexical entries,
graph facts) and then every row and chunk the owner has, earlier tombstones and old versions
included -- and must never touch another owner's rows or global content.
"""

from __future__ import annotations

import os
import uuid
from urllib.parse import urlsplit, urlunsplit

import pytest

from aura_core.adapters.memory_stores import InMemoryVectorStore
from aura_core.adapters.postgres_store import PostgresDocumentStore
from aura_core.domain.identifiers import UserId
from aura_core.knowledge.engine import KnowledgeEngine
from aura_core.knowledge.index_manager import IndexManager
from aura_core.llm.embeddings import HashEmbeddingProvider

pytestmark = pytest.mark.load

_URL = os.environ.get("AURA_TEST_DATABASE_URL", "")
ALICE, BOB = UserId("alice-sub"), UserId("bob-sub")


@pytest.fixture
async def database_url():
    if not _URL:
        pytest.skip("AURA_TEST_DATABASE_URL is not set")
    import asyncpg

    name = f"erase_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(_URL)
    await admin.execute(f'CREATE DATABASE "{name}"')
    try:
        yield urlunsplit(urlsplit(_URL)._replace(path=f"/{name}"))
    finally:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


async def _counts(database_url: str, owner: UserId | None) -> tuple[int, int]:
    import asyncpg

    conn = await asyncpg.connect(database_url)
    try:
        if owner is None:
            docs = await conn.fetchval("SELECT count(*) FROM aura_documents WHERE owner_id IS NULL")
            chunks = await conn.fetchval("SELECT count(*) FROM aura_chunks WHERE owner_id IS NULL")
        else:
            docs = await conn.fetchval(
                "SELECT count(*) FROM aura_documents WHERE owner_id = $1", owner.value
            )
            chunks = await conn.fetchval(
                "SELECT count(*) FROM aura_chunks WHERE owner_id = $1", owner.value
            )
        return docs, chunks
    finally:
        await conn.close()


async def test_erase_owner_removes_every_row_and_chunk_and_nothing_else(database_url) -> None:
    documents = PostgresDocumentStore(database_url)
    vectors = InMemoryVectorStore()
    embedder = HashEmbeddingProvider(dimension=128)
    engine = KnowledgeEngine(
        documents=documents, vectors=vectors, embedder=embedder,
        index_manager=IndexManager(vectors=vectors, documents=documents, embedder=embedder, alias="erase"),
        score_threshold=0.0,
    )
    await engine.ingest(title="global", content="# Global\n\nCompany holidays.", owner_id=None)
    for owner in (ALICE, BOB):
        notes = await engine.ingest(title="notes", content="# Notes\n\nPrivate notes v1.", owner_id=owner)
        await engine.ingest(
            title="notes", content="# Notes\n\nPrivate notes v2.", owner_id=owner,
            document_id=notes.document.id,
        )
        draft = await engine.ingest(title="draft", content="# Draft\n\nDeleted draft.", owner_id=owner)
        await engine.delete_document(draft.document.id, owner_id=owner)
    assert (await _counts(database_url, ALICE))[1] > 0

    removed = await engine.erase_owner(ALICE)

    assert removed == 3, "two versions of notes plus the tombstoned draft"
    assert await _counts(database_url, ALICE) == (0, 0)
    bob_docs, bob_chunks = await _counts(database_url, BOB)
    assert bob_docs == 3 and bob_chunks > 0
    global_docs, _ = await _counts(database_url, None)
    assert global_docs == 1, "global content is not the owner's to erase"
