"""AP10: the nightly refresh reads documents through Track AP connectors (``CompositeFetcher``).

``Container`` now builds a :class:`CompositeFetcher` instead of sending every knowledge source to
the HTTP fetcher: the web types keep the HTTPS policy, and with Track AP on, a ``connector`` source
(``connector:<id>``) is read through an *active* connector that can list documents -- a Files
directory or a Git checkout's documentation. Two rules are enforced where the source is read and
again where it is registered: connector text is untrusted (trust tier 1 or lower), and it belongs
to the project (owner ``project:<id>``), never to everyone.

The end-to-end test drives the real container over HTTP: register and approve a Files connector,
register a ``connector`` knowledge source, run the refresh pipeline, and find the documents
ingested under the project's owner.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from aura_core.adapters.connectors.files import FilesConnectorFactory
from aura_core.adapters.connectors.git import GitConnectorFactory
from aura_core.domain.connectors import ConnectorDescriptor, ConnectorKind, IdentityMode
from aura_core.domain.errors import AuthorizationError, ConflictError, NotFoundError, ValidationError
from aura_core.domain.identifiers import UserId
from aura_core.domain.knowledge import KnowledgeSource, SourceType, TrustTier
from aura_core.knowledge.documents import extract_document
from aura_core.learning import CompositeFetcher, ConnectorSourceFetcher, HttpSourceFetcher

BACKEND = Path(__file__).resolve().parents[2]
PILOTS = BACKEND / "tests" / "fixtures" / "pilots"
PROJECT = UserId("project:p0")


def _descriptor(kind: ConnectorKind, endpoint: str, connector_id: str) -> ConnectorDescriptor:
    return ConnectorDescriptor(id=connector_id, kind=kind, identity_mode=IdentityMode.SERVICE_ACCOUNT,
                               display_name=connector_id, endpoint=endpoint)


@pytest.fixture
def files_root(tmp_path: Path) -> Path:
    docs = tmp_path / "hr-docs"
    docs.mkdir()
    for document in (PILOTS / "hr" / "docs").iterdir():
        shutil.copyfile(document, docs / document.name)
    return tmp_path


def _opener(root: Path):
    factory = FilesConnectorFactory(root=str(root))

    async def open_connector(connector_id: str):
        if connector_id != "hr-docs":
            raise ConflictError(f"connector {connector_id!r} is pending, not active")
        return await factory.open(_descriptor(ConnectorKind.FILES, "hr-docs", "hr-docs"), None)

    return open_connector


def _source(*, uri: str = "connector:hr-docs", owner: UserId | None = PROJECT,
            trust: TrustTier = TrustTier.UNTRUSTED, **extra) -> KnowledgeSource:
    return KnowledgeSource(id="hr-docs-nightly", type=SourceType.CONNECTOR, uri=uri, owner_id=owner,
                           trust_tier=trust, **extra)


def _fetcher(root: Path) -> ConnectorSourceFetcher:
    return ConnectorSourceFetcher(open_connector=_opener(root), extract=extract_document)


# ------------------------------------------------------------------------------- the fetcher


class TestConnectorSourceFetcher:
    async def test_documents_are_listed_fetched_and_extracted(self, files_root) -> None:
        documents = await _fetcher(files_root).fetch(_source())
        by_title = {d.title: d for d in documents}
        assert set(by_title) == {p.name for p in (PILOTS / "hr" / "docs").iterdir()}
        assert "25 days" in by_title["leave-policy.md"].content
        html = by_title["remote-work.html"].content
        assert "home office allowance" in html and "<p>" not in html and "ignore previous" not in html
        assert "gifts worth more than 50" in by_title["code-of-conduct.pdf"].content.lower()
        assert by_title["leave-policy.md"].uri == "connector://hr-docs/document:leave-policy.md"
        assert by_title["leave-policy.md"].metadata == {"connector": "hr-docs", "locator": "document:leave-policy.md",
                                                        "format": "markdown"}

    async def test_document_ids_are_stable_across_runs(self, files_root) -> None:
        first = {d.title: d.document_id for d in await _fetcher(files_root).fetch(_source())}
        second = {d.title: d.document_id for d in await _fetcher(files_root).fetch(_source())}
        assert first == second and len(set(first.values())) == len(first)

    async def test_the_sources_ceilings_hold(self, files_root) -> None:
        assert len(await _fetcher(files_root).fetch(_source(max_documents=2))) == 2
        small = await _fetcher(files_root).fetch(_source(max_bytes_per_document=600))
        assert small and all(len((files_root / "hr-docs" / d.title).read_bytes()) <= 600 for d in small)

    @pytest.mark.parametrize("trust", [TrustTier.VERIFIED, TrustTier.INTERNAL])
    async def test_connector_text_is_never_trusted(self, files_root, trust) -> None:
        with pytest.raises(AuthorizationError):
            await _fetcher(files_root).fetch(_source(trust=trust))

    @pytest.mark.parametrize("owner", [None, UserId("alice"), UserId("service:billing")])
    async def test_only_a_project_may_own_connector_documents(self, files_root, owner) -> None:
        with pytest.raises(AuthorizationError):
            await _fetcher(files_root).fetch(_source(owner=owner))

    @pytest.mark.parametrize("uri", ["https://example.com", "connector:", "connector:../etc", "connector:HR DOCS"])
    async def test_the_uri_must_name_a_connector(self, files_root, uri) -> None:
        with pytest.raises(ValidationError):
            await _fetcher(files_root).fetch(_source(uri=uri))

    async def test_an_inactive_connector_fails_the_source(self, files_root) -> None:
        with pytest.raises(ConflictError):
            await _fetcher(files_root).fetch(_source(uri="connector:pending-one"))

    async def test_a_connector_that_cannot_list_documents_is_refused(self) -> None:
        class TablesOnly:
            async def health(self):
                return None

            async def aclose(self):
                return None

        async def open_connector(_):
            return TablesOnly()

        fetcher = ConnectorSourceFetcher(open_connector=open_connector, extract=extract_document)
        with pytest.raises(ValidationError):
            await fetcher.fetch(_source())

    async def test_an_unreadable_document_is_skipped_not_fatal(self, files_root) -> None:
        (files_root / "hr-docs" / "broken.md").write_bytes(b"\xff\xfe not utf-8")
        titles = {d.title for d in await _fetcher(files_root).fetch(_source())}
        assert "broken.md" not in titles and "leave-policy.md" in titles


# ------------------------------------------------------------------------------- Git documentation


class TestGitDocumentation:
    async def _connector(self, tmp_path: Path):
        shutil.copytree(PILOTS / "hr" / "repo", tmp_path / "app")
        docs = tmp_path / "app" / "docs" / "guides"
        docs.mkdir(parents=True)
        (docs / "onboarding.md").write_text("# Onboarding\nNew starters get a laptop on day one.\n")
        (tmp_path / "app" / "docs" / "diagram.png").write_bytes(b"\x89PNG")
        return await GitConnectorFactory(root=str(tmp_path)).open(_descriptor(ConnectorKind.GIT, "app", "hr-repo"), None)

    async def test_readme_and_docs_are_the_documentation(self, tmp_path) -> None:
        connector = await self._connector(tmp_path)
        assert await connector.list_documents() == ["document:README.md", "document:docs/guides/onboarding.md"]
        fetched = await connector.fetch_document("document:docs/guides/onboarding.md")
        assert fetched.format == "markdown" and b"laptop" in fetched.data

    @pytest.mark.parametrize("locator", ["document:app/models.py", "document:../README.md",
                                         "document:docs/../README.md", "document:/etc/passwd",
                                         "document:migrations/versions/0001_initial.py", "table:public.x",
                                         "document:docs/.hidden.md"])
    async def test_anything_else_is_refused(self, tmp_path, locator) -> None:
        connector = await self._connector(tmp_path)
        with pytest.raises(ValidationError):
            await connector.fetch_document(locator)

    @pytest.mark.skipif(not hasattr(os, "symlink"), reason="needs symlinks")
    async def test_a_symlink_anywhere_on_the_path_is_not_followed(self, tmp_path, tmp_path_factory) -> None:
        connector = await self._connector(tmp_path)
        elsewhere = tmp_path_factory.mktemp("elsewhere")
        (elsewhere / "secret.md").write_text("the root password is hunter2")
        (tmp_path / "app" / "docs" / "linked").symlink_to(elsewhere, target_is_directory=True)
        assert "document:docs/linked/secret.md" not in await connector.list_documents()
        with pytest.raises(NotFoundError):
            await connector.fetch_document("document:docs/linked/secret.md")

    async def test_discovery_reports_the_documentation(self, tmp_path) -> None:
        from aura_core.domain.discovery import ArtifactKind, DiscoveryBudget

        report = await (await self._connector(tmp_path)).discover(DiscoveryBudget())
        documents = {a.locator: a for a in report.artifacts if a.kind is ArtifactKind.DOCUMENT}
        assert set(documents) == {"document:README.md", "document:docs/guides/onboarding.md"}
        assert documents["document:README.md"].extractor == "code_documents"


# ------------------------------------------------------------------------------- the container


class TestContainerWiring:
    def test_web_types_keep_the_https_policy_and_connector_is_off_without_track_ap(self, container) -> None:
        fetcher = container.source_fetcher
        assert isinstance(fetcher, CompositeFetcher)
        assert isinstance(fetcher._fetchers[SourceType.WEB], HttpSourceFetcher)
        assert SourceType.CONNECTOR not in fetcher._fetchers

    async def test_without_track_ap_a_connector_source_is_never_read(self, container) -> None:
        # No connector fetcher is registered without Track AP, and no default fetcher catches the
        # type (it used to fall through to the HTTPS fetcher, which refused the non-https uri).
        with pytest.raises(ValidationError):
            await container.source_fetcher.fetch(_source())


def _adaptation_container(monkeypatch, files_root: Path):
    from cryptography.fernet import Fernet

    from aura_core.config.settings import load_settings
    from aura_core.container import Container

    monkeypatch.setenv("AURA_ADAPTATION_ENABLED", "true")
    monkeypatch.setenv("AURA_SECRETS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("AURA_ADAPTATION_FILES_ROOT", str(files_root))
    return Container(load_settings(refresh=True, validate=False))


async def test_the_nightly_refresh_reads_an_approved_connector_end_to_end(monkeypatch, files_root) -> None:
    import httpx

    from aura_core.api import Principal, build_app

    container = _adaptation_container(monkeypatch, files_root)
    assert isinstance(container.source_fetcher._fetchers[SourceType.CONNECTOR], ConnectorSourceFetcher)

    async def authenticator(request):
        return Principal(user_id=UserId("op-1"), email="op@example.com", role="SuperAdmin", capabilities=())

    transport = httpx.ASGITransport(app=build_app(container, authenticator=authenticator))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        refused = await client.post("/api/v3/admin/sources", json={
            "id": "too-trusted", "type": "connector", "uri": "connector:hr-docs", "trust_tier": 2})
        assert refused.status_code == 422, refused.text

        created = await client.post("/api/v3/admin/connectors", json={
            "id": "hr-docs", "kind": "files", "identity_mode": "service_account", "display_name": "HR documents",
            "endpoint": "hr-docs"})
        assert created.status_code == 201, created.text
        approval_id = created.json()["approval"]["id"]
        source = await client.post("/api/v3/admin/sources", json={
            "id": "hr-docs-nightly", "type": "connector", "uri": "connector:hr-docs", "trust_tier": 0})
        assert source.status_code == 200, source.text
        assert source.json()["source"]["owner"] == "project:p0"

        before = await container.refresh_pipeline.run()  # connector not active yet: the source fails
        assert {s.source_id: s.error for s in before.sources}["hr-docs-nightly"] == "ConflictError"

        await client.post(f"/api/v3/admin/approvals/{approval_id}/decide", json={"approved": True})
        activated = await client.post("/api/v3/admin/connectors/hr-docs/activate", json={"approval_id": approval_id})
        assert activated.status_code == 200, activated.text

    await container.index_manager.ensure_ready()
    report = await container.refresh_pipeline.run()
    outcome = {s.source_id: s for s in report.sources}["hr-docs-nightly"]
    assert outcome.error is None and outcome.ingested == len(list((PILOTS / "hr" / "docs").iterdir()))

    stored = await container.documents.list_active(owner_id=PROJECT, limit=100)
    project_docs = [d for d in stored if d.owner_id == PROJECT]
    assert {d.title for d in project_docs} == {p.name for p in (PILOTS / "hr" / "docs").iterdir()}
    assert all(d.trust_tier is TrustTier.UNTRUSTED for d in project_docs)
    for person in (UserId("alice"), None):
        assert not any(d.owner_id == PROJECT for d in await container.documents.list_active(owner_id=person, limit=100))


async def test_a_connector_source_needs_track_ap(container) -> None:
    import httpx

    from aura_core.api import Principal, build_app

    async def authenticator(request):
        return Principal(user_id=UserId("op-1"), email="op@example.com", role="SuperAdmin", capabilities=())

    transport = httpx.ASGITransport(app=build_app(container, authenticator=authenticator))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/v3/admin/sources", json={
            "id": "x", "type": "connector", "uri": "connector:hr-docs", "trust_tier": 0})
    assert response.status_code == 503

