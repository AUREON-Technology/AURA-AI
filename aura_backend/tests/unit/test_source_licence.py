"""Source licences gate the global corpus (D-001, KM-04, WP3).

The global corpus accepts CC0, CC BY and permissive licences only. A global source whose licence
is unknown or outside that list is *inert*: it can be registered (so the console keeps working
and an admin can fix it) but the refresh never fetches it. A source owned by a person or a
project is that owner's own content and is not subject to the global policy.
"""

from __future__ import annotations

import pytest

from aura_core.domain import DocumentId, UserId
from aura_core.domain.errors import NotFoundError
from aura_core.domain.knowledge import (
    DEFAULT_ALLOWED_LICENSES,
    KnowledgeSource,
    LicenseStatus,
    SourceType,
)
from aura_core.learning.knowledge_refresh import KnowledgeRefreshPipeline
from aura_core.learning.sources import FetchedDocument, InMemorySourceRegistry, StaticSourceFetcher


def _source(**changes) -> KnowledgeSource:
    base = dict(id="docs", type=SourceType.INTERNAL, uri="internal://docs")
    base.update(changes)
    return KnowledgeSource(**base)


class TestLicencePolicy:
    def test_a_new_source_has_an_unknown_licence(self) -> None:
        source = _source()
        assert source.license_status is LicenseStatus.UNKNOWN
        assert source.license_spdx is None

    def test_the_default_list_is_cc0_cc_by_and_permissive_only(self) -> None:
        assert {"CC0-1.0", "CC-BY-4.0", "MIT", "Apache-2.0", "BSD-3-Clause"} <= set(DEFAULT_ALLOWED_LICENSES)
        for excluded in ("CC-BY-SA-4.0", "CC-BY-NC-SA-4.0", "CC-BY-NC-4.0", "GPL-3.0-only"):
            assert excluded not in DEFAULT_ALLOWED_LICENSES

    def test_an_approved_allowed_licence_permits_a_global_source(self) -> None:
        source = _source(license_spdx="CC-BY-4.0", license_status=LicenseStatus.APPROVED)
        assert source.permits_global_corpus(DEFAULT_ALLOWED_LICENSES)

    def test_the_licence_id_is_compared_case_insensitively(self) -> None:
        source = _source(license_spdx="cc-by-4.0", license_status=LicenseStatus.APPROVED)
        assert source.permits_global_corpus(DEFAULT_ALLOWED_LICENSES)

    @pytest.mark.parametrize("spdx", ["CC-BY-SA-4.0", "CC-BY-NC-SA-4.0", "GPL-3.0-only", None])
    def test_a_licence_outside_the_list_does_not_permit_a_global_source(self, spdx) -> None:
        source = _source(license_spdx=spdx, license_status=LicenseStatus.APPROVED)
        assert not source.permits_global_corpus(DEFAULT_ALLOWED_LICENSES)

    @pytest.mark.parametrize("status", [LicenseStatus.UNKNOWN, LicenseStatus.RESTRICTED])
    def test_a_licence_that_is_not_approved_does_not_permit_a_global_source(self, status) -> None:
        source = _source(license_spdx="MIT", license_status=status)
        assert not source.permits_global_corpus(DEFAULT_ALLOWED_LICENSES)

    def test_a_source_owned_by_someone_is_outside_the_global_policy(self) -> None:
        source = _source(owner_id=UserId("alice"))
        assert source.permits_global_corpus(DEFAULT_ALLOWED_LICENSES)

    def test_the_allow_list_is_a_parameter(self) -> None:
        source = _source(license_spdx="CC-BY-4.0", license_status=LicenseStatus.APPROVED)
        assert not source.permits_global_corpus(("MIT",))


class TestRegistryProvenance:
    async def test_provenance_can_be_set_on_a_registered_source(self) -> None:
        registry = InMemorySourceRegistry([_source()])
        await registry.update_provenance(
            "docs", name="Project docs", publisher="Example Org", domains=("software",),
            language="en", license_spdx="MIT", license_status=LicenseStatus.APPROVED,
            attribution="(c) Example Org, MIT",
        )
        stored = await registry.get("docs")
        assert stored.name == "Project docs"
        assert stored.publisher == "Example Org"
        assert stored.domains == ("software",)
        assert stored.language == "en"
        assert stored.license_spdx == "MIT"
        assert stored.license_status is LicenseStatus.APPROVED
        assert stored.attribution == "(c) Example Org, MIT"

    async def test_only_the_named_fields_change(self) -> None:
        registry = InMemorySourceRegistry([_source(publisher="Example Org", license_spdx="MIT")])
        await registry.update_provenance("docs", attribution="credit")
        stored = await registry.get("docs")
        assert stored.attribution == "credit"
        assert stored.publisher == "Example Org"
        assert stored.license_spdx == "MIT"

    async def test_an_unknown_source_is_not_found(self) -> None:
        with pytest.raises(NotFoundError):
            await InMemorySourceRegistry().update_provenance("missing", publisher="x")


class _CountingFetcher(StaticSourceFetcher):
    def __init__(self, documents) -> None:
        super().__init__(documents)
        self.fetched: list[str] = []

    async def fetch(self, source):
        self.fetched.append(source.id)
        return await super().fetch(source)


_BODY = "The handbook says annual leave is twenty one days per year for permanent staff."


class TestRefreshHonoursTheLicence:
    async def _run(self, knowledge, index_manager, source: KnowledgeSource):
        fetcher = _CountingFetcher({
            source.id: [FetchedDocument(title="Leave", content=_BODY, document_id=DocumentId(f"{source.id}:leave"))],
        })
        pipeline = KnowledgeRefreshPipeline(
            registry=InMemorySourceRegistry([source]), fetcher=fetcher, knowledge=knowledge,
            index_manager=index_manager, max_documents=100,
        )
        await index_manager.ensure_ready()
        return await pipeline.run(), fetcher

    async def test_a_global_source_with_an_unknown_licence_is_not_fetched(
        self, knowledge, index_manager
    ) -> None:
        report, fetcher = await self._run(knowledge, index_manager, _source(id="unlicensed"))
        assert fetcher.fetched == []
        outcome = {s.source_id: s for s in report.sources}["unlicensed"]
        assert outcome.error == "license_not_approved"
        assert report.documents_ingested == 0

    async def test_a_global_source_outside_the_allow_list_is_not_fetched(
        self, knowledge, index_manager
    ) -> None:
        source = _source(id="sharealike", license_spdx="CC-BY-SA-4.0", license_status=LicenseStatus.APPROVED)
        report, fetcher = await self._run(knowledge, index_manager, source)
        assert fetcher.fetched == []
        assert {s.source_id: s for s in report.sources}["sharealike"].error == "license_not_approved"

    async def test_an_approved_global_source_is_ingested_with_its_provenance(
        self, knowledge, index_manager, documents
    ) -> None:
        source = _source(
            id="approved", name="Handbook", publisher="Example Org", domains=("hr",), language="en",
            license_spdx="CC-BY-4.0", license_status=LicenseStatus.APPROVED,
            attribution="Example Org, CC BY 4.0",
        )
        report, fetcher = await self._run(knowledge, index_manager, source)
        assert fetcher.fetched == ["approved"]
        assert report.documents_ingested == 1
        document = await documents.get(DocumentId("approved:leave"))
        assert document.metadata["source_id"] == "approved"
        assert document.metadata["publisher"] == "Example Org"
        assert document.metadata["license_spdx"] == "CC-BY-4.0"
        assert document.metadata["attribution"] == "Example Org, CC BY 4.0"
        assert list(document.metadata["domains"]) == ["hr"]
        assert document.language == "en"

    async def test_a_source_owned_by_a_user_needs_no_licence(self, knowledge, index_manager, user) -> None:
        report, fetcher = await self._run(knowledge, index_manager, _source(id="mine", owner_id=user))
        assert fetcher.fetched == ["mine"]
        assert report.documents_ingested == 1


pytest.importorskip("fastapi")


def _client(container, *, role: str):
    from fastapi.testclient import TestClient

    from aura_core.api import Principal, build_app

    async def authenticator(request):
        return Principal(
            user_id=UserId("op-1"), email="op@example.com", role=role,
            capabilities=("admin.act", "admin.super") if role == "SuperAdmin" else ("admin.act",),
        )

    return TestClient(build_app(container, authenticator=authenticator))


class TestAdminSourcesApi:
    def test_a_licensed_source_is_registered_as_approved_and_ingestible(self, container) -> None:
        with _client(container, role="SuperAdmin") as client:
            response = client.post("/api/v3/admin/sources", json={
                "id": "docs", "type": "web", "uri": "https://example.com/docs",
                "name": "Example docs", "publisher": "Example Org", "domains": ["software"],
                "language": "en", "license_spdx": "CC-BY-4.0", "attribution": "Example Org, CC BY 4.0",
            })
        assert response.status_code == 200, response.text
        source = response.json()["source"]
        assert source["license_spdx"] == "CC-BY-4.0"
        assert source["license_status"] == "approved"
        assert source["ingestible"] is True
        assert source["publisher"] == "Example Org"
        assert source["domains"] == ["software"]

    def test_a_source_without_a_licence_is_registered_but_inert(self, container) -> None:
        """The console's register form sends no licence yet, and an admin must be able to fix one."""
        with _client(container, role="SuperAdmin") as client:
            response = client.post("/api/v3/admin/sources", json={
                "id": "docs", "type": "web", "uri": "https://example.com/docs",
            })
        assert response.status_code == 200, response.text
        source = response.json()["source"]
        assert source["license_status"] == "unknown"
        assert source["ingestible"] is False

    @pytest.mark.parametrize("spdx", ["CC-BY-SA-4.0", "CC-BY-NC-SA-4.0", "GPL-3.0-only"])
    def test_a_licence_outside_the_list_is_refused(self, container, spdx) -> None:
        with _client(container, role="SuperAdmin") as client:
            response = client.post("/api/v3/admin/sources", json={
                "id": "docs", "type": "web", "uri": "https://example.com/docs", "license_spdx": spdx,
            })
            listed = client.get("/api/v3/admin/sources").json()["sources"]
        assert response.status_code == 422, response.text
        assert listed == []

    def test_provenance_can_be_added_to_an_existing_source(self, container) -> None:
        with _client(container, role="SuperAdmin") as client:
            client.post("/api/v3/admin/sources", json={
                "id": "docs", "type": "web", "uri": "https://example.com/docs",
            })
            response = client.put("/api/v3/admin/sources/docs/provenance", json={
                "publisher": "Example Org", "license_spdx": "MIT", "attribution": "MIT, Example Org",
            })
        assert response.status_code == 200, response.text
        source = response.json()["source"]
        assert source["license_status"] == "approved"
        assert source["ingestible"] is True
        assert source["publisher"] == "Example Org"

    def test_a_licence_outside_the_list_cannot_be_added_to_an_existing_source(self, container) -> None:
        with _client(container, role="SuperAdmin") as client:
            client.post("/api/v3/admin/sources", json={
                "id": "docs", "type": "web", "uri": "https://example.com/docs",
            })
            response = client.put("/api/v3/admin/sources/docs/provenance", json={
                "license_spdx": "CC-BY-NC-4.0",
            })
            stored = client.get("/api/v3/admin/sources").json()["sources"][0]
        assert response.status_code == 422, response.text
        assert stored["license_status"] == "unknown"

    def test_provenance_on_an_unknown_source_is_not_found(self, container) -> None:
        with _client(container, role="SuperAdmin") as client:
            response = client.put("/api/v3/admin/sources/missing/provenance", json={"publisher": "x"})
        assert response.status_code == 404, response.text

    def test_an_admin_who_is_not_a_super_admin_cannot_change_provenance(self, container) -> None:
        with _client(container, role="SuperAdmin") as client:
            client.post("/api/v3/admin/sources", json={
                "id": "docs", "type": "web", "uri": "https://example.com/docs",
            })
        with _client(container, role="Admin") as client:
            response = client.put("/api/v3/admin/sources/docs/provenance", json={"license_spdx": "MIT"})
        assert response.status_code == 403, response.text
