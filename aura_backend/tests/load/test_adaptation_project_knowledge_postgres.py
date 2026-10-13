"""AP10 against a real Postgres: project knowledge sync over ``PostgresDocumentStore``.

Opt-in (``-m load``); skips unless ``AURA_TEST_DATABASE_URL`` names a superuser DSN with password
auth (see ``test_adaptation_postgres.py``).

What the unit tests (in-memory document store) cannot show and this does: the PostgreSQL store's
``list_active(owner)`` returns the owner's documents *and* global ones, and the sync's document
metadata (which source element a document came from) round-trips through JSONB. Sync still
converges on the blueprint's sources and never touches a global document.
"""

from __future__ import annotations

import dataclasses
import os
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

from aura_core.adapters.memory_stores import InMemoryVectorStore
from aura_core.adapters.postgres_store import PostgresDocumentStore
from aura_core.domain.blueprint import KnowledgeSection
from aura_core.knowledge.engine import KnowledgeEngine
from aura_core.knowledge.index_manager import IndexManager
from aura_core.llm.embeddings import HashEmbeddingProvider
from tests.unit.test_adaptation_project_knowledge import CONNECTOR, HR_DOCS, _compiled, _opener, _project, _with_docs

pytestmark = pytest.mark.load

_URL = os.environ.get("AURA_TEST_DATABASE_URL", "")


@pytest.fixture
async def database_url():
    if not _URL:
        pytest.skip("AURA_TEST_DATABASE_URL is not set")
    import asyncpg

    name = f"ap10_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(_URL)
    await admin.execute(f'CREATE DATABASE "{name}"')
    try:
        yield urlunsplit(urlsplit(_URL)._replace(path=f"/{name}"))
    finally:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


async def test_sync_converges_and_leaves_global_documents_alone(database_url, tmp_path: Path) -> None:
    documents = PostgresDocumentStore(database_url)
    vectors = InMemoryVectorStore()
    embedder = HashEmbeddingProvider(dimension=128)
    engine = KnowledgeEngine(
        documents=documents, vectors=vectors, embedder=embedder,
        index_manager=IndexManager(vectors=vectors, documents=documents, embedder=embedder, alias="ap10"),
        score_threshold=0.0,
    )
    handbook = await engine.ingest(title="global-handbook", content="Annual leave questions go to HR.", owner_id=None)
    exports = tmp_path / "hr-exports"
    exports.mkdir()
    _with_docs(exports)
    twin, compiled = await _compiled(tmp_path)
    bp = compiled.blueprint
    project = _project(engine, documents, _opener(tmp_path))

    first = await project.sync(bp, twin)
    assert len(first.ingested) == len(HR_DOCS) and not first.failed
    again = await project.sync(bp, twin)
    assert again.ingested == () and len(again.unchanged) == len(HR_DOCS) and again.removed == ()

    listed = await documents.list_active(owner_id=project.owner, limit=100)
    assert handbook.document.id in {d.id for d in listed}  # the store does list global documents
    elements = {d.metadata.get("element") for d in listed if d.owner_id == project.owner}
    assert elements == {f"{CONNECTOR}:document:{name}" for name in HR_DOCS}

    onboarding = f"{CONNECTOR}:document:onboarding.txt"
    narrowed = dataclasses.replace(bp, knowledge=KnowledgeSection(
        tuple(s for s in bp.knowledge.sources if s.element != onboarding)))
    trimmed = await project.sync(narrowed)
    assert trimmed.removed == (onboarding,)
    assert (await documents.get(handbook.document.id)) is not None
    assert handbook.document.id in {d.id for d in await documents.list_active(owner_id=None)}
