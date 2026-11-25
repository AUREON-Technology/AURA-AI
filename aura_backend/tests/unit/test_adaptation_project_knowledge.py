"""AP10: documents become project knowledge -- discovered, compiled, ingested on deploy, searched.

The HR pilot's recorded rows (as CSV) and its document set (Markdown, text, HTML, PDF) sit in one
Files connector directory. They go through discovery -> twin -> plan -> compile -> AP8 gate ->
approval -> deploy, after which the blueprint's agents hold a ``project_knowledge`` tool that
searches the project's documents and nothing else.

The isolation probe runs the whole knowledge engine over both vector adapters -- the in-memory
reference and ``QdrantVectorStore`` on qdrant-client's in-process mode, which evaluates the real
filter objects -- and checks the roadmap's property: a document owned by one identity (a person, a
project, the global corpus) is never retrieved for another.

The retrieval eval asks each pilot's question set (``tests/fixtures/pilots/<pilot>/retrieval_eval.json``)
against its documents with the test suite's hash embedder. That proves the plumbing and gives a
lexical floor; recall with the deployed embedding model is measured on the dev stack, not here.

No database, no network, no model.
"""

from __future__ import annotations

import csv
import dataclasses
import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest

from aura_core.adaptation.compiler import compile_blueprint
from aura_core.adaptation.deployment import BlueprintDeployment
from aura_core.adaptation.gate import BlueprintGate
from aura_core.adaptation.knowledge import ProjectKnowledge, project_owner
from aura_core.adaptation.planner import CapabilityRegistry, plan_capabilities
from aura_core.adaptation.runtime import ActiveBlueprint, BlueprintToolMaterializer
from aura_core.adaptation.twin import build_twin
from aura_core.adapters.connectors.files import FilesConnectorFactory
from aura_core.adapters.memory_adaptation import InMemoryBlueprintStore
from aura_core.adapters.memory_stores import InMemoryVectorStore
from aura_core.agents.registry import build_default_agent_registry
from aura_core.domain.ausm import ElementKind
from aura_core.domain.blueprint import (
    PROJECT_KNOWLEDGE_TOOL,
    BlueprintState,
    ConnectorRef,
    KnowledgeSection,
    generated_tool_name,
)
from aura_core.domain.compilation import BlueprintRecord
from aura_core.domain.connectors import ConnectorDescriptor, ConnectorKind, IdentityMode
from aura_core.domain.discovery import ArtifactKind, DiscoveryBudget
from aura_core.domain.errors import NotFoundError, ValidationError
from aura_core.domain.identifiers import ToolCallId, UserId
from aura_core.domain.messages import ToolCall
from aura_core.knowledge.documents import extract_document, extract_html
from aura_core.knowledge.engine import KnowledgeEngine
from aura_core.knowledge.index_manager import IndexManager
from aura_core.learning.approval import ApprovalService, InMemoryApprovalStore
from aura_core.ports.runtime import ToolContext
from aura_core.tools.policy import ToolPolicy
from aura_core.tools.registry import ToolRegistry

BACKEND = Path(__file__).resolve().parents[2]
PILOTS = BACKEND / "tests" / "fixtures" / "pilots"
ROWS = json.loads((PILOTS / "hr" / "golden" / "rows.json").read_text("utf-8"))
HR_DOCS = sorted(p.name for p in (PILOTS / "hr" / "docs").iterdir())
CONNECTOR = "hr-files"


def _descriptor(endpoint: str = "hr-exports", connector_id: str = CONNECTOR) -> ConnectorDescriptor:
    return ConnectorDescriptor(id=connector_id, kind=ConnectorKind.FILES, identity_mode=IdentityMode.SERVICE_ACCOUNT,
                               display_name="HR exports", endpoint=endpoint)


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _write_csv(path: Path, rows: list[dict]) -> None:
    columns = list(rows[0])
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        for row in rows:
            writer.writerow([_cell(row[c]) for c in columns])


def _with_docs(directory: Path, pilot: str = "hr") -> None:
    for document in (PILOTS / pilot / "docs").iterdir():
        shutil.copyfile(document, directory / document.name)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    exports = tmp_path / "hr-exports"
    exports.mkdir()
    for key, rows in ROWS.items():
        _write_csv(exports / f"{key.split('.', 1)[1]}.csv", rows)
    _with_docs(exports)
    return tmp_path


async def _discover(root: Path, endpoint: str = "hr-exports", connector_id: str = CONNECTOR):
    connector = await FilesConnectorFactory(root=str(root)).open(_descriptor(endpoint, connector_id), None)
    return await connector.discover(DiscoveryBudget())


async def _compiled(root: Path, *, project: str = "p0", endpoint: str = "hr-exports"):
    report = await _discover(root, endpoint)
    twin = build_twin(project, 1, [report]).twin
    plan = plan_capabilities(twin, CapabilityRegistry.from_directory())
    compiled = compile_blueprint(twin, plan, version=1, name="HR exports",
                                 connectors=[ConnectorRef(CONNECTOR, ConnectorKind.FILES, IdentityMode.SERVICE_ACCOUNT)])
    return twin, compiled


def _opener(root: Path, endpoint: str = "hr-exports"):
    factory = FilesConnectorFactory(root=str(root))

    async def open_connector(connector_id: str):
        if connector_id != CONNECTOR:
            raise NotFoundError("no such connector")
        return await factory.open(_descriptor(endpoint), None)

    return open_connector


def _project(knowledge: KnowledgeEngine, documents, open_connector, project_id: str = "p0") -> ProjectKnowledge:
    return ProjectKnowledge(project_id=project_id, knowledge=knowledge, documents=documents,
                            extract=extract_document, open_connector=open_connector)


def _context(*capabilities: str) -> ToolContext:
    return ToolContext(user_id=UserId("u1"), correlation_id="c1", deadline_s=10.0, capabilities=capabilities)


# ------------------------------------------------------------------------------- discovery


class TestDocumentDiscovery:
    async def test_documents_are_artifacts_that_never_carry_their_text(self, root) -> None:
        report = await _discover(root)
        documents = {a.locator: a for a in report.artifacts if a.kind is ArtifactKind.DOCUMENT}
        assert sorted(documents) == [f"document:{name}" for name in HR_DOCS]
        policy = (root / "hr-exports" / "leave-policy.md").read_bytes()
        attributes = documents["document:leave-policy.md"].attributes
        assert dict(attributes) == {"format": "markdown", "bytes": len(policy),
                                    "sha256": hashlib.sha256(policy).hexdigest()}
        assert {a.attributes["format"] for a in documents.values()} == {"markdown", "text", "html", "pdf"}
        assert "25 days" not in json.dumps([a.attributes for a in report.artifacts], default=str)

    async def test_office_formats_and_symlinks_are_reported_not_read(self, root, tmp_path_factory) -> None:
        exports = root / "hr-exports"
        (exports / "handbook.doc").write_bytes(b"\xd0\xcf\x11\xe0 not really a legacy doc")
        if hasattr(os, "symlink"):
            secret = tmp_path_factory.mktemp("secret") / "secret.md"
            secret.write_text("the root password is hunter2")
            (exports / "leak.md").symlink_to(secret)
        report = await _discover(root)
        issues = {(i.code, i.locator) for i in report.issues}
        assert ("unsupported_format", "document:handbook.doc") in issues
        assert not any(a.locator in ("document:handbook.doc", "document:leak.md") for a in report.artifacts)
        if hasattr(os, "symlink"):
            assert ("symlink_skipped", "document:leak.md") in issues

    @pytest.mark.parametrize("locator", ["document:../hr-exports/leave-policy.md", "document:/etc/passwd",
                                         "document:.hidden.md", "document:employees.csv",
                                         "table:files.employees", "leave-policy.md"])
    async def test_fetch_takes_only_a_plain_document_name(self, root, locator) -> None:
        connector = await FilesConnectorFactory(root=str(root)).open(_descriptor(), None)
        with pytest.raises(ValidationError):
            await connector.fetch_document(locator)

    async def test_a_document_that_disappeared_is_not_found(self, root) -> None:
        connector = await FilesConnectorFactory(root=str(root)).open(_descriptor(), None)
        fetched = await connector.fetch_document("document:leave-policy.md")
        assert fetched.format == "markdown" and b"25 days" in fetched.data
        (root / "hr-exports" / "leave-policy.md").unlink()
        with pytest.raises(NotFoundError):
            await connector.fetch_document("document:leave-policy.md")

    async def test_an_edited_document_changes_the_report_digest(self, root) -> None:
        before = await _discover(root)
        with (root / "hr-exports" / "onboarding.txt").open("a", encoding="utf-8") as handle:
            handle.write("\nNew starters also receive a welcome pack.\n")
        assert (await _discover(root)).digest != before.digest


# ------------------------------------------------------------------------------- compile


class TestCompile:
    async def test_documents_become_knowledge_sources_and_every_agent_may_search_them(self, root) -> None:
        twin, compiled = await _compiled(root)
        bp = compiled.blueprint
        documents = sorted(e.id for e in twin.of_kind(ElementKind.DOCUMENT))
        assert documents == [f"{CONNECTOR}:document:{name}" for name in HR_DOCS]
        assert all(e.properties["sha256"] for e in twin.of_kind(ElementKind.DOCUMENT))
        assert [s.element for s in bp.knowledge.sources] == documents
        assert bp.native_tools == (PROJECT_KNOWLEDGE_TOOL,)
        assert bp.agents and all(PROJECT_KNOWLEDGE_TOOL in a.tools for a in bp.agents)
        assert PROJECT_KNOWLEDGE_TOOL not in {t.name for t in bp.tools}

    async def test_without_documents_there_is_no_knowledge_tool(self, root) -> None:
        for name in HR_DOCS:
            (root / "hr-exports" / name).unlink()
        _, compiled = await _compiled(root)
        bp = compiled.blueprint
        assert bp.knowledge.sources == () and bp.native_tools == ()
        assert all(PROJECT_KNOWLEDGE_TOOL not in a.tools for a in bp.agents)

    async def test_the_knowledge_tool_cannot_be_declared_without_sources(self, root) -> None:
        _, compiled = await _compiled(root)
        bp = compiled.blueprint
        with pytest.raises(ValidationError):
            dataclasses.replace(bp, knowledge=KnowledgeSection())
        foreign = dataclasses.replace(bp.knowledge.sources[0], element="other:document:x.md")
        with pytest.raises(ValidationError):
            dataclasses.replace(bp, knowledge=KnowledgeSection((foreign,)))


# ------------------------------------------------------------------------------- deploy


class Twins:
    def __init__(self, twin) -> None:
        self.twin = twin

    async def get(self, version: int):
        return (None, self.twin) if version == self.twin.version else None


async def test_deploy_ingests_the_documents_and_grants_the_search_tool(root, knowledge, documents, audit, clock) -> None:
    """Discovery -> twin -> compile -> gate -> approval -> deploy, then the project agent searches."""
    twin, compiled = await _compiled(root)
    bp = compiled.blueprint
    open_connector = _opener(root)
    project = _project(knowledge, documents, open_connector)
    tools = ToolRegistry(policy=ToolPolicy(), audit=audit)
    agents = ActiveBlueprint(build_default_agent_registry())
    approvals = ApprovalService(store=InMemoryApprovalStore(), audit=audit)
    store = InMemoryBlueprintStore()
    gate = BlueprintGate(open_connector=open_connector, new_registry=lambda: ToolRegistry(policy=ToolPolicy()),
                         clock=clock)
    deployment = BlueprintDeployment(
        project_id="p0", store=store, approvals=approvals, agents=agents,
        materializer=BlueprintToolMaterializer(tools=tools, open_connector=open_connector, grace_s=0,
                                               knowledge=project),
        clock=clock, gate=gate, twins=Twins(twin), audit=audit, knowledge=project,
    )
    await store.append(BlueprintRecord(
        project_id="p0", version=1, state=BlueprintState.PROPOSED, digest=bp.digest,
        created_at="2026-10-13T00:00:00+00:00", created_by="sa@example.com",
        plan_digest="sha256:" + "1" * 64, registry_digest="sha256:" + "2" * 64,
        blueprint=bp, suites=compiled.suites, dropped=compiled.dropped,
    ))
    evaluated = await deployment.evaluate(1, by="sa@example.com")
    assert evaluated.state is BlueprintState.EVALUATED, evaluated.evaluation.blocking()
    request = await deployment.request_approval(1, by="sa@example.com")
    await approvals.decide(request.id, approved=True, by="reviewer@example.com")
    await deployment.approve(1, approval_id=request.id, by="sa@example.com")
    await deployment.deploy(1, by="sa@example.com")

    status = (await deployment.readiness())["knowledge"]
    assert status["ingested"] == len(HR_DOCS) and status["failed"] == [] and status["changed_since_discovery"] == []
    name = generated_tool_name(1, PROJECT_KNOWLEDGE_TOOL)
    assert name in tools
    granted = [spec for spec in agents.generated()]
    assert granted and all(name in spec.allowed_tools for spec in granted)

    call = ToolCall(id=ToolCallId("k1"), name=name, arguments={"query": "annual leave days per year"})
    result, _ = await tools.execute(call, _context("project_data"), capabilities=("project_data",))
    assert result.ok, result.content
    assert "25 days" in result.content and "leave-policy.md" in result.content
    refused, _ = await tools.execute(call, _context(), capabilities=())
    assert not refused.ok


# ------------------------------------------------------------------------------- sync


class TestSync:
    async def test_resync_converges_on_the_blueprints_sources(self, root, knowledge, documents) -> None:
        twin, compiled = await _compiled(root)
        bp = compiled.blueprint
        project = _project(knowledge, documents, _opener(root))
        first = await project.sync(bp, twin)
        assert len(first.ingested) == len(HR_DOCS) and not first.failed

        again = await project.sync(bp, twin)
        assert again.ingested == () and len(again.unchanged) == len(HR_DOCS)

        (root / "hr-exports" / "onboarding.txt").write_text("Probation now lasts three months.\n", "utf-8")
        edited = await project.sync(bp, twin)
        onboarding = f"{CONNECTOR}:document:onboarding.txt"
        assert edited.ingested == (onboarding,) and edited.changed_since_discovery == (onboarding,)
        hits = (await project.search("how long does probation last", limit=3)).chunks
        assert any("three months" in h.chunk.content for h in hits)
        assert not any("six months" in h.chunk.content for h in hits)

        kept = tuple(s for s in bp.knowledge.sources if s.element != onboarding)
        narrowed = dataclasses.replace(bp, knowledge=KnowledgeSection(kept))
        trimmed = await project.sync(narrowed)
        assert trimmed.removed == (onboarding,)
        assert not any(h.chunk.metadata.get("title") == "onboarding.txt"
                       for h in (await project.search("probation lasts three months", limit=10)).chunks)

    async def test_global_documents_are_never_removed_even_when_the_store_lists_them(self, root, knowledge,
                                                                                      documents) -> None:
        """The PostgreSQL store's ``list_active(owner)`` returns the owner's documents *and* global
        ones; the in-memory store returns the owner's only. Sync must behave the same over both."""
        listed = documents.list_active

        async def owner_plus_global(*, owner_id, limit=100, offset=0):
            own = await listed(owner_id=owner_id, limit=10_000)
            shared = await listed(owner_id=None, limit=10_000)
            return (list(own) + list(shared))[offset: offset + limit]

        documents.list_active = owner_plus_global
        handbook = await knowledge.ingest(title="global-handbook", content="Annual leave questions go to HR.",
                                          owner_id=None)
        _, compiled = await _compiled(root)
        report = await _project(knowledge, documents, _opener(root)).sync(compiled.blueprint)
        assert report.removed == () and not report.failed
        assert (await documents.get(handbook.document.id)) is not None
        assert any(d.id == handbook.document.id for d in await listed(owner_id=None))

    async def test_one_unreadable_document_does_not_stop_the_rest(self, root, knowledge, documents) -> None:
        twin, compiled = await _compiled(root)
        (root / "hr-exports" / "expenses-policy.md").unlink()
        (root / "hr-exports" / "onboarding.txt").write_bytes(b"\xff\xfe not utf-8 \xff")
        report = await _project(knowledge, documents, _opener(root)).sync(compiled.blueprint, twin)
        failed = dict(report.failed)
        assert failed[f"{CONNECTOR}:document:expenses-policy.md"] == "not_found"
        assert failed[f"{CONNECTOR}:document:onboarding.txt"] == "knowledge_error"
        assert len(report.ingested) == len(HR_DOCS) - 2

    async def test_a_connector_that_cannot_fetch_documents_is_a_recorded_failure(self, root, knowledge,
                                                                                  documents) -> None:
        _, compiled = await _compiled(root)

        class TablesOnly:
            async def health(self):
                return None

            async def aclose(self):
                return None

        async def open_connector(_):
            return TablesOnly()

        report = await _project(knowledge, documents, open_connector).sync(compiled.blueprint)
        assert report.ingested == () and {k for _e, k in report.failed} == {"validation_error"}

    async def test_another_projects_blueprint_is_refused(self, root, knowledge, documents) -> None:
        _, compiled = await _compiled(root, project="p1")
        with pytest.raises(ValidationError):
            await _project(knowledge, documents, _opener(root)).sync(compiled.blueprint)

    async def test_html_scripts_and_markup_are_not_indexed(self, root, knowledge, documents) -> None:
        _, compiled = await _compiled(root)
        project = _project(knowledge, documents, _opener(root))
        await project.sync(compiled.blueprint)
        hits = (await project.search("home office allowance desk chair", limit=5)).chunks
        text = " ".join(h.chunk.content for h in hits)
        assert "home office allowance" in text
        assert "ignore previous instructions" not in text and "<p>" not in text and "font-family" not in text


def test_html_extraction_keeps_visible_text_only() -> None:
    page = ("<html><head><title>T</title><style>p{color:red}</style></head><body><p>First</p><p>Second "
            "&amp; third</p><script>steal()</script><img alt='alt text'><br>Last</body></html>")
    assert extract_html(page).text() == "First\nSecond & third\nLast"


# ------------------------------------------------------------------------------- isolation


def _vectors(kind: str):
    if kind == "memory":
        return InMemoryVectorStore()
    from qdrant_client import AsyncQdrantClient

    from aura_core.adapters.qdrant_store import QdrantVectorStore

    store = QdrantVectorStore("http://qdrant.invalid:6333")
    store._client = AsyncQdrantClient(location=":memory:")
    return store


@pytest.fixture(params=["memory", "qdrant"])
def isolated_knowledge(request, documents, embedder, safety, metrics) -> KnowledgeEngine:
    vectors = _vectors(request.param)
    index = IndexManager(vectors=vectors, documents=documents, embedder=embedder, alias="isolation", metrics=metrics)
    return KnowledgeEngine(documents=documents, vectors=vectors, embedder=embedder, index_manager=index,
                           safety=safety, metrics=metrics, score_threshold=0.0)


async def test_isolation_probe_no_identity_retrieves_anothers_documents(root, isolated_knowledge, documents) -> None:
    """The AP10 security property, on both vector adapters: personal, project and global documents
    each reach exactly the identities they belong to."""
    engine = isolated_knowledge
    alice = UserId("alice")
    await engine.ingest(title="alice-notes", content="Annual leave: I have booked 12 days in August.",
                        owner_id=alice)
    await engine.ingest(title="global-faq", content="Annual leave questions go to the HR helpdesk.",
                        owner_id=None)
    _, compiled = await _compiled(root)
    p0 = _project(engine, documents, _opener(root))
    await p0.sync(compiled.blueprint)
    other = tmp = root / "other-exports"
    tmp.mkdir()
    (other / "secret-plan.md").write_text("Annual leave will be cut to 10 days next year. Confidential.", "utf-8")
    _, other_bp = await _compiled(root, project="p1", endpoint="other-exports")
    p1 = _project(engine, documents, _opener(root, "other-exports"), project_id="p1")
    assert not (await p1.sync(other_bp.blueprint)).failed

    query = "annual leave days"
    owners = lambda result: {(h.chunk.owner_id.value if h.chunk.owner_id else None) for h in result.chunks}  # noqa: E731

    p0_hits = await p0.search(query, limit=10)
    assert p0_hits.chunks and owners(p0_hits) == {project_owner("p0").value}
    assert owners(await p1.search(query, limit=10)) == {project_owner("p1").value}

    for person in (alice, UserId("bob"), None):
        seen = owners(await engine.retrieve(query, owner_id=person, limit=20))
        assert not any(o and o.startswith("project:") for o in seen), person
    assert owners(await engine.retrieve(query, owner_id=alice, limit=20)) == {"alice", None}
    assert owners(await engine.retrieve(query, owner_id=UserId("bob"), limit=20)) == {None}


# ------------------------------------------------------------------------------- retrieval eval


def recall_at_k(hits_by_question: list[tuple[str, list[str]]], k: int) -> float:
    found = sum(1 for expected, titles in hits_by_question if expected in titles[:k])
    return found / len(hits_by_question)


@pytest.mark.parametrize("pilot", ["hr", "ecommerce"])
async def test_pilot_retrieval_eval(pilot, tmp_path, knowledge, documents) -> None:
    spec = json.loads((PILOTS / pilot / "retrieval_eval.json").read_text("utf-8"))
    exports = tmp_path / "hr-exports"
    exports.mkdir()
    _with_docs(exports, pilot)
    _, compiled = await _compiled(tmp_path)
    project = _project(knowledge, documents, _opener(tmp_path))
    sync = await project.sync(compiled.blueprint)
    assert not sync.failed and len(sync.ingested) == len(list((PILOTS / pilot / "docs").iterdir()))

    k = spec["k"]
    results = []
    for case in spec["questions"]:
        hits = (await project.search(case["question"], limit=k)).chunks
        titles = list(dict.fromkeys(h.chunk.metadata.get("title") for h in hits))
        results.append((case["document"], titles))
    recall = recall_at_k(results, k)
    misses = [(q["question"], titles) for q, (_doc, titles) in zip(spec["questions"], results)
              if q["document"] not in titles]
    print(f"\n{pilot}: recall@{k} = {recall:.2f} over {len(results)} questions; misses: {misses}")
    assert recall >= spec["min_recall"], misses
