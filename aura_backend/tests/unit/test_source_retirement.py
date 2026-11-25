"""WP-2 (knowledge readiness KB-003, owner decision D3): withdrawing a source withdraws its content.

Deleting a global source used to remove only its registry row, so everything it had already ingested
stayed retrievable by every user, and a source could never be marked restricted. Now:

* a source's documents can be found (by the ``source_id`` the refresh stamps) and retired through
  the normal delete path;
* deleting a global source that has live documents needs an explicit choice, ``retire`` or ``keep``;
* a Super Admin takedown disables the source, marks it restricted and retires its documents;
* a restricted source stays restricted when its licence text is edited.
"""

from __future__ import annotations

import pytest

from aura_core.domain import DocumentId, UserId
from aura_core.knowledge.source_retirement import documents_of_source, retire_source_documents

pytest.importorskip("fastapi")


async def _put(knowledge, source_id: str, name: str, text: str, *, owner=None, stamped=True):
    return await knowledge.ingest(
        content=text, title=name, document_id=DocumentId(f"{source_id}:{name}"),
        owner_id=owner, metadata={"source_id": source_id} if stamped else {},
    )


async def _findable(knowledge, query: str, user: UserId, source_id: str) -> bool:
    """Whether retrieval returns a chunk of one of ``source_id``'s documents. Checked by source,
    not by "anything came back": with the score floor at 0 an unrelated document is returned too."""
    result = await knowledge.retrieve(query, owner_id=user, include_global=True, score_threshold=0.0)
    return any(hit.chunk.document_id.value.startswith(f"{source_id}:") for hit in result.chunks)


class TestFindingASourcesDocuments:
    async def test_documents_are_matched_by_the_stamped_source_id(self, knowledge, documents) -> None:
        await _put(knowledge, "alpha", "one", "# One\nAlpha documents describe zebras.")
        await _put(knowledge, "beta", "two", "# Two\nBeta documents describe giraffes.")
        found = await documents_of_source(documents, "alpha", owner_id=None)
        assert [d.id.value for d in found] == ["alpha:one"]

    async def test_a_users_own_documents_are_never_matched(self, knowledge, documents, user) -> None:
        await _put(knowledge, "alpha", "mine", "# Mine\nMy private zebra notes.", owner=user)
        assert await documents_of_source(documents, "alpha", owner_id=None) == []

    async def test_an_unstamped_document_is_matched_only_by_its_root_id(
        self, knowledge, documents
    ) -> None:
        """Before the refresh stamped ``source_id``, a page was stored as ``<source>:root``."""
        await _put(knowledge, "alpha", "root", "# Root\nOld alpha page about zebras.", stamped=False)
        await _put(knowledge, "alpha", "other", "# Other\nAn unstamped, non-root alpha page.", stamped=False)
        found = await documents_of_source(documents, "alpha", owner_id=None)
        assert [d.id.value for d in found] == ["alpha:root"]

    async def test_a_source_whose_id_prefixes_another_does_not_claim_its_documents(
        self, knowledge, documents
    ) -> None:
        """Source ids may contain ':', so 'a:' must not match the documents of a source 'a:b'."""
        await _put(knowledge, "a:b", "root", "# Root\nDocuments of the longer-named source.")
        await _put(knowledge, "a:b", "extra", "# Extra\nMore of the longer-named source.", stamped=False)
        assert await documents_of_source(documents, "a", owner_id=None) == []

    async def test_every_page_of_a_long_listing_is_read(self, knowledge, documents) -> None:
        for i in range(105):
            await _put(knowledge, "big", f"d{i:03}", f"# D{i}\nDocument number {i} about topic {i}.")
        assert len(await documents_of_source(documents, "big", owner_id=None)) == 105


class TestRetiring:
    async def test_retired_documents_leave_retrieval_and_others_stay(
        self, knowledge, documents, user
    ) -> None:
        await _put(knowledge, "alpha", "one", "# One\nAlpha documents describe zebras in savannah.")
        await _put(knowledge, "beta", "two", "# Two\nBeta documents describe quokkas in forests.")
        assert await _findable(knowledge, "zebras savannah", user, "alpha")

        report = await retire_source_documents(knowledge, documents, "alpha", owner_id=None)

        assert (report.matched, report.retired, report.complete) == (1, 1, True)
        assert report.chunks_removed >= 1
        assert not await _findable(knowledge, "zebras savannah", user, "alpha")
        assert await _findable(knowledge, "quokkas forests", user, "beta")

    async def test_a_second_run_finds_nothing_left(self, knowledge, documents) -> None:
        await _put(knowledge, "alpha", "one", "# One\nAlpha documents describe zebras.")
        await retire_source_documents(knowledge, documents, "alpha", owner_id=None)
        again = await retire_source_documents(knowledge, documents, "alpha", owner_id=None)
        assert (again.matched, again.retired) == (0, 0)

    async def test_a_failing_document_is_reported_and_the_rest_are_retired(
        self, knowledge, documents
    ) -> None:
        await _put(knowledge, "alpha", "bad", "# Bad\nThis one will not delete cleanly.")
        await _put(knowledge, "alpha", "good", "# Good\nThis one deletes without trouble.")

        class _Flaky:
            async def delete_document(self, document_id, *, owner_id):
                if document_id.value.endswith(":bad"):
                    raise RuntimeError("vector store down")
                return await knowledge.delete_document(document_id, owner_id=owner_id)

        report = await retire_source_documents(_Flaky(), documents, "alpha", owner_id=None)

        assert report.failed == ("alpha:bad",)
        assert (report.matched, report.retired, report.complete) == (2, 1, False)
        # What failed is still there, so a second run can finish the job.
        assert [d.id.value for d in await documents_of_source(documents, "alpha", owner_id=None)] == [
            "alpha:bad"
        ]


def _client(container, *, role: str):
    from fastapi.testclient import TestClient

    from aura_core.api import Principal, build_app

    async def authenticator(request):
        return Principal(
            user_id=UserId("op-1"), email="op@example.com", role=role,
            capabilities=("admin.act", "admin.super") if role == "SuperAdmin" else ("admin.act",),
        )

    return TestClient(build_app(container, authenticator=authenticator))


def _register(client, source_id: str = "docs", **extra):
    return client.post("/api/v3/admin/sources", json={
        "id": source_id, "type": "web", "uri": "https://example.com/docs",
        "license_spdx": "CC-BY-4.0", **extra,
    })


async def _seed(container, source_id: str = "docs", count: int = 2) -> None:
    await container.startup()
    for i in range(count):
        await _put(container.knowledge, source_id, f"p{i}", f"# P{i}\nPage {i} explains zebra migration routes.")


async def _findable_in(container, query: str, source_id: str = "docs") -> bool:
    return await _findable(container.knowledge, query, UserId("reader"), source_id)


class TestDeletingASource:
    async def test_the_count_is_available_before_deciding(self, container) -> None:
        await _seed(container, count=3)
        with _client(container, role="SuperAdmin") as client:
            _register(client)
            body = client.get("/api/v3/admin/sources/docs/documents").json()
        assert body["count"] == 3
        assert {d["id"] for d in body["documents"]} == {"docs:p0", "docs:p1", "docs:p2"}

    async def test_a_source_with_documents_needs_a_choice(self, container) -> None:
        await _seed(container)
        with _client(container, role="SuperAdmin") as client:
            _register(client)
            response = client.delete("/api/v3/admin/sources/docs")
            still_there = [s["id"] for s in client.get("/api/v3/admin/sources").json()["sources"]]
        assert response.status_code == 409, response.text
        assert response.json()["details"]["live_documents"] == 2
        assert still_there == ["docs"]
        assert await _findable_in(container, "zebra migration")

    async def test_retire_removes_the_documents_then_the_source(self, container) -> None:
        await _seed(container)
        with _client(container, role="SuperAdmin") as client:
            _register(client)
            response = client.delete("/api/v3/admin/sources/docs?documents=retire")
            listed = client.get("/api/v3/admin/sources").json()["sources"]
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["deleted"] is True
        assert body["retirement"]["retired"] == 2
        assert listed == []
        assert not await _findable_in(container, "zebra migration")

    async def test_keep_removes_only_the_registry_row(self, container) -> None:
        await _seed(container)
        with _client(container, role="SuperAdmin") as client:
            _register(client)
            response = client.delete("/api/v3/admin/sources/docs?documents=keep")
        assert response.status_code == 200, response.text
        assert response.json()["deleted"] is True
        assert await _findable_in(container, "zebra migration")

    async def test_a_source_with_no_documents_needs_no_choice(self, container) -> None:
        await container.startup()
        with _client(container, role="SuperAdmin") as client:
            _register(client)
            response = client.delete("/api/v3/admin/sources/docs")
        assert response.status_code == 200, response.text
        assert response.json()["deleted"] is True

    async def test_an_unknown_choice_is_refused(self, container) -> None:
        await container.startup()
        with _client(container, role="SuperAdmin") as client:
            _register(client)
            response = client.delete("/api/v3/admin/sources/docs?documents=maybe")
        assert response.status_code == 422, response.text

    async def test_only_a_super_admin_may_delete(self, container) -> None:
        await _seed(container)
        with _client(container, role="SuperAdmin") as client:
            _register(client)
        with _client(container, role="Admin") as client:
            response = client.delete("/api/v3/admin/sources/docs?documents=retire")
        assert response.status_code == 403, response.text
        assert await _findable_in(container, "zebra migration")


class TestTakedown:
    async def test_a_takedown_restricts_disables_and_retires(self, container) -> None:
        await _seed(container)
        with _client(container, role="SuperAdmin") as client:
            _register(client)
            response = client.post(
                "/api/v3/admin/sources/docs/takedown", json={"reason": "rights holder complaint"}
            )
            listed = client.get("/api/v3/admin/sources").json()["sources"]
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["retirement"]["retired"] == 2 and body["retirement"]["complete"] is True
        source = body["source"]
        assert source["license_status"] == "restricted"
        assert source["enabled"] is False
        assert source["ingestible"] is False
        # The decision stays visible: the source is still registered.
        assert [s["id"] for s in listed] == ["docs"]
        assert not await _findable_in(container, "zebra migration")

    async def test_a_takedown_can_be_run_again(self, container) -> None:
        await _seed(container)
        with _client(container, role="SuperAdmin") as client:
            _register(client)
            client.post("/api/v3/admin/sources/docs/takedown", json={"reason": "first pass"})
            again = client.post("/api/v3/admin/sources/docs/takedown", json={"reason": "second pass"})
        assert again.status_code == 200, again.text
        assert again.json()["retirement"]["matched"] == 0

    async def test_a_reason_is_required(self, container) -> None:
        await _seed(container)
        with _client(container, role="SuperAdmin") as client:
            _register(client)
            response = client.post("/api/v3/admin/sources/docs/takedown", json={"reason": "x"})
        assert response.status_code == 422, response.text
        assert await _findable_in(container, "zebra migration")

    async def test_an_unknown_source_is_not_found(self, container) -> None:
        await container.startup()
        with _client(container, role="SuperAdmin") as client:
            response = client.post("/api/v3/admin/sources/missing/takedown", json={"reason": "reason"})
        assert response.status_code == 404, response.text

    async def test_only_a_super_admin_may_take_a_source_down(self, container) -> None:
        await _seed(container)
        with _client(container, role="SuperAdmin") as client:
            _register(client)
        with _client(container, role="Admin") as client:
            response = client.post("/api/v3/admin/sources/docs/takedown", json={"reason": "reason"})
        assert response.status_code == 403, response.text
        assert await _findable_in(container, "zebra migration")

    async def test_a_takedown_is_audited(self, container) -> None:
        await _seed(container)
        with _client(container, role="SuperAdmin") as client:
            _register(client)
            client.post("/api/v3/admin/sources/docs/takedown", json={"reason": "rights holder complaint"})
            events = client.get("/api/v3/admin/audit?action=admin.source.takedown").json()["events"]
        assert len(events) == 1
        assert events[0]["resource"] == "docs"


class TestRestrictedStatus:
    async def test_a_source_can_be_marked_restricted_without_retiring_anything(self, container) -> None:
        await _seed(container)
        with _client(container, role="SuperAdmin") as client:
            _register(client)
            response = client.put("/api/v3/admin/sources/docs/provenance", json={"restricted": True})
        assert response.status_code == 200, response.text
        assert response.json()["source"]["license_status"] == "restricted"
        assert response.json()["source"]["ingestible"] is False
        assert await _findable_in(container, "zebra migration")

    async def test_editing_the_licence_does_not_lift_a_restriction(self, container) -> None:
        await container.startup()
        with _client(container, role="SuperAdmin") as client:
            _register(client)
            client.put("/api/v3/admin/sources/docs/provenance", json={"restricted": True})
            response = client.put("/api/v3/admin/sources/docs/provenance", json={"license_spdx": "MIT"})
        assert response.status_code == 200, response.text
        source = response.json()["source"]
        assert source["license_status"] == "restricted"
        assert source["license_spdx"] == "MIT"

    async def test_a_restriction_is_lifted_only_explicitly(self, container) -> None:
        await container.startup()
        with _client(container, role="SuperAdmin") as client:
            _register(client)
            client.put("/api/v3/admin/sources/docs/provenance", json={"restricted": True})
            response = client.put(
                "/api/v3/admin/sources/docs/provenance", json={"restricted": False, "license_spdx": "MIT"}
            )
        assert response.json()["source"]["license_status"] == "approved"
        assert response.json()["source"]["ingestible"] is True
