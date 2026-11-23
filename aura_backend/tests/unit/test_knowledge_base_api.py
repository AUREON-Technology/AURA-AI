"""The corpus-level knowledge routes: ``GET /api/v3/knowledge``, ``GET /knowledge/documents/{id}``,
``DELETE /knowledge/documents?confirm=true`` and uploading Markdown, text and HTML files.

There is one corpus per person (or per team), not several named knowledge bases; these routes read
and clear it. Everything runs through the real ASGI app and the real knowledge engine on the
in-memory substrate; what is asserted is what a person can see afterwards (does search still find
it, does another person's document survive), not which helper ran.
"""

from __future__ import annotations

import pytest

from aura_core.domain.identifiers import UserId

pytest.importorskip("fastapi")

ALICE_FACT = "The ZEBRAFISH-4410 protocol requires a quarterly audit of the cold storage vault."
BOB_FACT = "The OSPREY-9921 handbook says visitors sign in at the north gate."


def _app(container):
    from aura_core.api import Principal, build_app
    from aura_core.domain.errors import AuthenticationError

    async def authenticate(request):
        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer "):
            raise AuthenticationError("missing")
        return Principal(user_id=UserId(header[7:]), email="", role="User")

    return build_app(container, authenticator=authenticate)


def _as(user: str) -> dict:
    return {"Authorization": f"Bearer {user}"}


def _ingest(client, user: str, title: str, content: str, **extra):
    return client.post("/api/v3/knowledge/documents", json={"title": title, "content": content, **extra}, headers=_as(user))


def _found(client, user: str, query: str, marker: str) -> bool:
    hits = client.post("/api/v3/knowledge/search", json={"query": query}, headers=_as(user)).json()["results"]
    return any(marker in hit["content"] for hit in hits)


# ---------------------------------------------------------------------------------------------
# the store's own totals
# ---------------------------------------------------------------------------------------------


class TestOwnerStats:
    async def test_counts_bytes_and_pins_of_one_owner_only(self, container) -> None:
        await container.startup()
        alice, bob = UserId("alice"), UserId("bob")
        text = "héllo wörld — ünïcode " * 10  # multi-byte on purpose: bytes are not characters
        for owner, title, content in ((alice, "a1", ASCII := "a" * 100), (alice, "a2", text), (bob, "b1", "b" * 300)):
            await container.knowledge.ingest(content=content, title=title, owner_id=owner)
        (second,) = [d for d in await container.documents.list_active(owner_id=alice) if d.title == "a2"]
        await container.documents.set_cag_pin(second.id, owner_id=alice, pinned=True)

        stats = await container.documents.owner_stats(owner_id=alice)

        assert stats.documents == 2 and stats.pinned == 1
        assert stats.content_bytes == 100 + len(text.strip().encode("utf-8")) > 100 + len(text.strip())
        assert (await container.documents.owner_stats(owner_id=bob)).documents == 1
        assert (await container.documents.owner_stats(owner_id=UserId("nobody"))).documents == 0

    async def test_a_deleted_document_is_no_longer_counted(self, container) -> None:
        await container.startup()
        alice = UserId("alice")
        kept = await container.knowledge.ingest(content="k" * 50, title="kept", owner_id=alice)
        gone = await container.knowledge.ingest(content="g" * 70, title="gone", owner_id=alice)
        await container.knowledge.delete_document(gone.document.id, owner_id=alice)

        stats = await container.documents.owner_stats(owner_id=alice)
        assert (stats.documents, stats.content_bytes) == (1, 50) and kept.document is not None


# ---------------------------------------------------------------------------------------------
# GET /knowledge
# ---------------------------------------------------------------------------------------------


class TestSummary:
    def test_it_totals_the_callers_corpus_and_nobody_elses(self, container) -> None:
        from fastapi.testclient import TestClient

        with TestClient(_app(container)) as client:
            assert _ingest(client, "alice", "fact", ALICE_FACT).status_code == 200
            assert _ingest(client, "alice", "other", "second document " * 8).status_code == 200
            assert _ingest(client, "bob", "fact", BOB_FACT).status_code == 200
            mine = client.get("/api/v3/knowledge", headers=_as("alice")).json()
            theirs = client.get("/api/v3/knowledge", headers=_as("bob")).json()
            empty = client.get("/api/v3/knowledge", headers=_as("carol")).json()

        assert mine["documents"] == 2 and theirs["documents"] == 1 and empty["documents"] == 0
        assert mine["content_bytes"] == len(ALICE_FACT) + len(("second document " * 8).strip())
        assert mine["cag_pinned"] == 0
        assert theirs["content_bytes"] == len(BOB_FACT)

    def test_it_tells_a_client_what_an_upload_may_be_and_how_much_plan_storage_is_used(self, container) -> None:
        from fastapi.testclient import TestClient

        with TestClient(_app(container)) as client:
            before = client.get("/api/v3/knowledge", headers=_as("alice")).json()
            _ingest(client, "alice", "fact", ALICE_FACT)
            after = client.get("/api/v3/knowledge", headers=_as("alice")).json()
        assert before["upload_types"] == ["pdf", "md", "txt", "html"]
        assert before["max_document_bytes"] == container.settings.knowledge.max_document_bytes
        assert before["storage"]["used_bytes"] == 0 and before["storage"]["allowed_bytes"] > 0
        assert after["storage"]["used_bytes"] == len(ALICE_FACT), "the plan's meter and the summary disagree"


# ---------------------------------------------------------------------------------------------
# GET /knowledge/documents/{id}
# ---------------------------------------------------------------------------------------------


class TestReadOne:
    def test_it_describes_the_document_and_returns_the_text_only_when_asked(self, container) -> None:
        from fastapi.testclient import TestClient

        long_text = "\n\n".join(f"Section {i}. " + ("detail " * 120) for i in range(6))
        with TestClient(_app(container)) as client:
            made = _ingest(client, "alice", "Manual", long_text, document_id="manual-1", metadata={"team": "ops"}).json()
            plain = client.get("/api/v3/knowledge/documents/manual-1", headers=_as("alice"))
            full = client.get("/api/v3/knowledge/documents/manual-1", params={"content": "true"}, headers=_as("alice"))

        body = plain.json()
        assert plain.status_code == 200
        assert (body["document_id"], body["title"], body["version"], body["status"]) == ("manual-1", "Manual", 1, "active")
        assert body["size_bytes"] == len(long_text.strip().encode("utf-8"))
        assert body["chunks"] == made["chunks"] and body["chunks"] > 1
        assert body["metadata"] == {"team": "ops"} and body["cag_pinned"] is False
        assert "content" not in body
        assert full.json()["content"] == long_text.strip()

    def test_the_size_is_bytes_not_characters(self, container) -> None:
        from fastapi.testclient import TestClient

        text = "Zürich café — naïve résumé " * 4
        with TestClient(_app(container)) as client:
            _ingest(client, "alice", "accents", text, document_id="accents")
            size = client.get("/api/v3/knowledge/documents/accents", headers=_as("alice")).json()["size_bytes"]
        assert size == len(text.strip().encode("utf-8")) > len(text.strip())

    def test_someone_elses_a_global_and_an_unknown_document_all_answer_the_same_404(self, container) -> None:
        import asyncio

        from fastapi.testclient import TestClient

        asyncio.run(container.startup())
        asyncio.run(container.knowledge.ingest(content="global corpus text " * 5, title="global", document_id=None, owner_id=None))
        (global_doc,) = asyncio.run(container.documents.list_active(owner_id=None))
        with TestClient(_app(container)) as client:
            _ingest(client, "alice", "mine", ALICE_FACT, document_id="alice-doc")
            answers = [
                client.get("/api/v3/knowledge/documents/alice-doc", headers=_as("bob")),
                client.get(f"/api/v3/knowledge/documents/{global_doc.id.value}", headers=_as("bob")),
                client.get("/api/v3/knowledge/documents/never-existed", headers=_as("bob")),
            ]
            mine = client.get("/api/v3/knowledge/documents/alice-doc", headers=_as("alice"))

        assert [r.status_code for r in answers] == [404, 404, 404]
        assert len({r.json()["error"] for r in answers}) == 1
        assert "ZEBRAFISH" not in "".join(r.text for r in answers)
        assert mine.status_code == 200

    def test_a_deleted_document_is_a_404(self, container) -> None:
        from fastapi.testclient import TestClient

        with TestClient(_app(container)) as client:
            _ingest(client, "alice", "mine", ALICE_FACT, document_id="alice-doc")
            client.delete("/api/v3/knowledge/documents/alice-doc", headers=_as("alice"))
            assert client.get("/api/v3/knowledge/documents/alice-doc", headers=_as("alice")).status_code == 404


# ---------------------------------------------------------------------------------------------
# DELETE /knowledge/documents
# ---------------------------------------------------------------------------------------------


class TestDeleteAll:
    def test_it_refuses_without_confirm_and_deletes_nothing(self, container) -> None:
        from fastapi.testclient import TestClient

        with TestClient(_app(container)) as client:
            _ingest(client, "alice", "fact", ALICE_FACT)
            for params in ({}, {"confirm": "false"}):
                refused = client.delete("/api/v3/knowledge/documents", params=params, headers=_as("alice"))
                assert refused.status_code == 422 and refused.json()["details"]["field"] == "confirm"
            assert client.get("/api/v3/knowledge", headers=_as("alice")).json()["documents"] == 1
            assert _found(client, "alice", "ZEBRAFISH-4410 quarterly audit", "ZEBRAFISH")

    def test_it_clears_the_callers_corpus_and_search_stops_finding_it_but_nobody_elses(self, container) -> None:
        import asyncio

        from fastapi.testclient import TestClient

        asyncio.run(container.startup())
        asyncio.run(container.knowledge.ingest(content="global corpus text SEAGULL-0001 " * 5, title="global", owner_id=None))
        with TestClient(_app(container)) as client:
            for i in range(3):
                _ingest(client, "alice", f"a{i}", ALICE_FACT + f" variant {i}")
            _ingest(client, "bob", "b", BOB_FACT)
            assert _found(client, "alice", "ZEBRAFISH-4410 quarterly audit", "ZEBRAFISH")

            done = client.delete("/api/v3/knowledge/documents", params={"confirm": "true"}, headers=_as("alice"))

            assert done.status_code == 200
            body = done.json()
            assert (body["deleted"], body["failed"], body["remaining"]) == (3, [], 0) and body["chunks_removed"] >= 3
            assert not _found(client, "alice", "ZEBRAFISH-4410 quarterly audit", "ZEBRAFISH"), "a deleted document is still retrievable"
            assert client.get("/api/v3/knowledge", headers=_as("alice")).json()["documents"] == 0
            assert client.get("/api/v3/knowledge", headers=_as("bob")).json()["documents"] == 1, "Alice's delete-all reached Bob"
            assert _found(client, "bob", "OSPREY-9921 north gate visitors", "OSPREY")
            assert _found(client, "bob", "SEAGULL-0001 global corpus", "SEAGULL"), "the global corpus was touched"

    def test_it_stops_at_its_cap_and_says_how_many_remain(self, container, monkeypatch) -> None:
        from fastapi.testclient import TestClient

        monkeypatch.setattr("aura_core.api.v3.BULK_DELETE_LIMIT", 3)
        with TestClient(_app(container)) as client:
            for i in range(5):
                _ingest(client, "alice", f"doc{i}", f"distinct body number {i} " * 6)
            first = client.delete("/api/v3/knowledge/documents", params={"confirm": "true"}, headers=_as("alice")).json()
            second = client.delete("/api/v3/knowledge/documents", params={"confirm": "true"}, headers=_as("alice")).json()
        assert (first["deleted"], first["remaining"]) == (3, 2)
        assert (second["deleted"], second["remaining"]) == (2, 0)

    def test_one_document_that_cannot_be_removed_is_reported_and_the_rest_still_go(self, container, monkeypatch) -> None:
        from fastapi.testclient import TestClient

        real = container.knowledge.delete_document

        async def flaky(document_id, *, owner_id):
            if document_id.value == "stubborn":
                raise RuntimeError("index unreachable")
            return await real(document_id, owner_id=owner_id)

        monkeypatch.setattr(container.knowledge, "delete_document", flaky)
        with TestClient(_app(container)) as client:
            for name in ("one", "stubborn", "three"):
                _ingest(client, "alice", name, f"body of {name} " * 6, document_id=name)
            body = client.delete("/api/v3/knowledge/documents", params={"confirm": "true"}, headers=_as("alice")).json()
            left = client.get("/api/v3/knowledge/documents", headers=_as("alice")).json()["documents"]
        assert body["deleted"] == 2 and body["failed"] == ["stubborn"] and body["remaining"] == 1
        assert [d["document_id"] for d in left] == ["stubborn"]


# ---------------------------------------------------------------------------------------------
# uploading Markdown, text and HTML
# ---------------------------------------------------------------------------------------------


def _upload(client, name: str, data: bytes, title: str = "An upload", user: str = "alice"):
    return client.post(
        "/api/v3/knowledge/documents/upload", files={"file": (name, data, "application/octet-stream")},
        data={"title": title}, headers=_as(user),
    )


class TestUploadTextFormats:
    def test_markdown_text_and_html_files_are_ingested_and_found(self, container) -> None:
        from fastapi.testclient import TestClient

        cases = {
            "notes.md": b"# Vault\n\nThe NARWHAL-3301 rotation happens every March.\n",
            "plain.txt": b"The KINGFISHER-7782 invoice is due on the first of each month.\n",
            "page.html": b"<html><body><h1>Gate</h1><p>The PUFFIN-5503 delivery arrives at dock four.</p>"
                         b"<script>alert('x')</script></body></html>",
        }
        with TestClient(_app(container)) as client:
            for name, data in cases.items():
                response = _upload(client, name, data, title=name)
                assert response.status_code == 200, (name, response.text)
                assert response.json()["created"] is True and response.json()["chunks"] >= 1
            assert _found(client, "alice", "NARWHAL-3301 rotation March", "NARWHAL")
            assert _found(client, "alice", "KINGFISHER-7782 invoice", "KINGFISHER")
            assert _found(client, "alice", "PUFFIN-5503 delivery dock", "PUFFIN")
            html_hits = client.post("/api/v3/knowledge/search", json={"query": "PUFFIN-5503 delivery dock"}, headers=_as("alice")).json()["results"]
            assert not any("alert(" in hit["content"] or "<p>" in hit["content"] for hit in html_hits), "markup or script was indexed"

    def test_a_text_file_that_is_not_utf8_is_refused_with_the_reason(self, container) -> None:
        from fastapi.testclient import TestClient

        with TestClient(_app(container)) as client:
            response = _upload(client, "latin.txt", "caf\xe9 au lait".encode("latin-1") + b"\xff\xfe")
            assert response.status_code == 422 and "UTF-8" in response.json()["message"]
            assert client.get("/api/v3/knowledge", headers=_as("alice")).json()["documents"] == 0

    @pytest.mark.parametrize("name", ["report.docx", "sheet.xlsx", "archive.tar.gz", "image.png", ".hidden"])
    def test_any_other_type_is_a_422_that_lists_what_is_supported(self, container, name: str) -> None:
        from fastapi.testclient import TestClient

        with TestClient(_app(container)) as client:
            response = _upload(client, name, b"data")
        assert response.status_code == 422
        assert response.json()["details"]["supported"] == ["pdf", "md", "txt", "html"]

    def test_a_pdf_and_a_name_with_no_extension_still_take_the_pdf_path(self, container) -> None:
        from fastapi.testclient import TestClient

        from tests.unit.test_document_extraction import _pdf

        pdf = _pdf([{"text": ["The CORMORANT-1188 clause covers late refunds."]}])
        with TestClient(_app(container)) as client:
            named = _upload(client, "terms.PDF", pdf, title="terms")
            bare = _upload(client, "terms", pdf, title="terms again")
        assert named.status_code == bare.status_code == 200, (named.text, bare.text)
        assert named.json()["pages"] == 1
