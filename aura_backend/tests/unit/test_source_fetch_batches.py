"""Fetcher framework: batches, cursors, conditional fetch, and honest success (WP4, KM-03/07/08/13)."""

from __future__ import annotations

import json
import socket

import httpx
import pytest

from aura_core.domain import DocumentId, UserId
from aura_core.domain.errors import ValidationError
from aura_core.domain.knowledge import KnowledgeSource, SourceType
from aura_core.learning.knowledge_refresh import KnowledgeRefreshPipeline
from aura_core.learning.sources import (
    CompositeFetcher,
    FetchBatch,
    FetchedDocument,
    HttpSourceFetcher,
    InMemorySourceRegistry,
    StaticSourceFetcher,
)

_URL = "https://docs.example.com/handbook"
_PAGE = "<title>Handbook</title><p>Annual leave is twenty one days per year for permanent staff.</p>"


def _resolver(address: str = "93.184.216.34"):
    def resolve(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0))]

    return resolve


class _Recorder:
    """Stands in for the network under httpx and keeps the requests it was sent."""

    def __init__(self, handler) -> None:
        self.handler = handler
        self.requests: list[httpx.Request] = []

    def install(self, monkeypatch) -> None:
        original = httpx.AsyncClient
        transport = httpx.MockTransport(self)

        def factory(*args, **kwargs):
            kwargs["transport"] = transport
            return original(*args, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", factory)
        monkeypatch.setattr(socket, "getaddrinfo", _resolver())

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)


def _page(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200, text=_PAGE, headers={
            "content-type": "text/html", "etag": '"v1"', "last-modified": "Wed, 18 Nov 2026 10:00:00 GMT",
        },
    )


def _source(**changes) -> KnowledgeSource:
    base = dict(id="docs", type=SourceType.WEB, uri=_URL)
    base.update(changes)
    return KnowledgeSource(**base)


class TestConditionalFetch:
    async def test_the_first_fetch_sends_no_validators_and_returns_them_as_the_cursor(self, monkeypatch) -> None:
        recorder = _Recorder(_page)
        recorder.install(monkeypatch)
        batch = await HttpSourceFetcher().fetch_batch(_source())
        assert "if-none-match" not in recorder.requests[0].headers
        assert "if-modified-since" not in recorder.requests[0].headers
        assert len(batch.documents) == 1
        assert not batch.unchanged
        assert json.loads(batch.cursor) == {"etag": '"v1"', "last_modified": "Wed, 18 Nov 2026 10:00:00 GMT"}

    async def test_the_next_fetch_sends_the_validators_and_a_304_means_unchanged(self, monkeypatch) -> None:
        cursor = json.dumps({"etag": '"v1"', "last_modified": "Wed, 18 Nov 2026 10:00:00 GMT"})
        recorder = _Recorder(lambda request: httpx.Response(304))
        recorder.install(monkeypatch)
        batch = await HttpSourceFetcher().fetch_batch(_source(last_cursor=cursor))
        request = recorder.requests[0]
        assert request.headers["if-none-match"] == '"v1"'
        assert request.headers["if-modified-since"] == "Wed, 18 Nov 2026 10:00:00 GMT"
        assert batch.unchanged
        assert batch.documents == ()
        assert batch.cursor == cursor

    async def test_a_changed_page_replaces_the_cursor(self, monkeypatch) -> None:
        old = json.dumps({"etag": '"v0"'})
        recorder = _Recorder(_page)
        recorder.install(monkeypatch)
        batch = await HttpSourceFetcher().fetch_batch(_source(last_cursor=old))
        assert not batch.unchanged
        assert json.loads(batch.cursor)["etag"] == '"v1"'

    async def test_a_page_with_no_validators_has_no_cursor(self, monkeypatch) -> None:
        recorder = _Recorder(lambda request: httpx.Response(200, text=_PAGE, headers={"content-type": "text/html"}))
        recorder.install(monkeypatch)
        batch = await HttpSourceFetcher().fetch_batch(_source(last_cursor=json.dumps({"etag": '"v0"'})))
        assert batch.cursor is None
        assert len(batch.documents) == 1

    async def test_a_malformed_cursor_is_ignored(self, monkeypatch) -> None:
        recorder = _Recorder(_page)
        recorder.install(monkeypatch)
        batch = await HttpSourceFetcher().fetch_batch(_source(last_cursor="not json {"))
        assert "if-none-match" not in recorder.requests[0].headers
        assert len(batch.documents) == 1

    async def test_the_plain_fetch_used_for_previews_ignores_the_cursor(self, monkeypatch) -> None:
        recorder = _Recorder(_page)
        recorder.install(monkeypatch)
        documents = await HttpSourceFetcher().fetch(_source(last_cursor=json.dumps({"etag": '"v1"'})))
        assert "if-none-match" not in recorder.requests[0].headers
        assert len(documents) == 1


class TestCompositeFetcher:
    def test_it_says_which_types_it_can_fetch(self) -> None:
        composite = CompositeFetcher({SourceType.WEB: StaticSourceFetcher()})
        assert composite.supports(SourceType.WEB)
        assert not composite.supports(SourceType.RSS)
        assert not composite.supports(SourceType.REPOSITORY)

    def test_a_default_fetcher_supports_every_type(self) -> None:
        composite = CompositeFetcher({}, default=StaticSourceFetcher())
        assert composite.supports(SourceType.RSS)

    async def test_a_fetcher_without_batches_is_wrapped(self) -> None:
        document = FetchedDocument(title="T", content="Some content here.", document_id=DocumentId("a"))
        composite = CompositeFetcher({SourceType.WEB: StaticSourceFetcher({"docs": [document]})})
        batch = await composite.fetch_batch(_source())
        assert tuple(batch.documents) == (document,)
        assert batch.cursor is None and not batch.unchanged

    async def test_the_per_source_document_ceiling_applies_to_a_batch(self) -> None:
        documents = [FetchedDocument(title=f"T{i}", content=f"Content {i}.", document_id=DocumentId(f"d{i}")) for i in range(5)]
        composite = CompositeFetcher({SourceType.WEB: StaticSourceFetcher({"docs": documents})})
        batch = await composite.fetch_batch(_source(max_documents=2))
        assert len(batch.documents) == 2

    async def test_an_unsupported_type_is_refused(self) -> None:
        composite = CompositeFetcher({SourceType.WEB: StaticSourceFetcher()})
        with pytest.raises(ValidationError):
            await composite.fetch_batch(_source(type=SourceType.RSS))


class _ScriptedFetcher:
    """Returns the scripted batches in order, whatever the source."""

    def __init__(self, *batches: FetchBatch) -> None:
        self._batches = list(batches)
        self.seen_cursors: list[str | None] = []

    async def fetch_batch(self, source) -> FetchBatch:
        self.seen_cursors.append(source.last_cursor)
        return self._batches.pop(0)


def _doc(name: str = "leave", content: str = _PAGE) -> FetchedDocument:
    return FetchedDocument(title="Leave", content=content, document_id=DocumentId(f"docs:{name}"))


class TestRefreshUsesBatches:
    async def _pipeline(self, knowledge, index_manager, fetcher, user):
        registry = InMemorySourceRegistry([
            KnowledgeSource(id="docs", type=SourceType.INTERNAL, uri="internal://docs", owner_id=user),
        ])
        await index_manager.ensure_ready()
        pipeline = KnowledgeRefreshPipeline(
            registry=registry, fetcher=fetcher, knowledge=knowledge, index_manager=index_manager,
            max_documents=100,
        )
        return pipeline, registry

    async def test_the_cursor_is_stored_and_sent_back_on_the_next_run(self, knowledge, index_manager, user) -> None:
        fetcher = _ScriptedFetcher(
            FetchBatch(documents=(_doc(),), cursor='{"etag": "v1"}'),
            FetchBatch(cursor='{"etag": "v1"}', unchanged=True),
        )
        pipeline, registry = await self._pipeline(knowledge, index_manager, fetcher, user)
        first = await pipeline.run()
        assert first.documents_ingested == 1
        assert (await registry.get("docs")).last_cursor == '{"etag": "v1"}'

        second = await pipeline.run()
        assert fetcher.seen_cursors == [None, '{"etag": "v1"}']
        outcome = second.sources[0]
        assert outcome.not_modified is True
        assert outcome.ingested == 0 and outcome.error is None
        assert second.as_dict()["sources"][0]["not_modified"] is True

    async def test_an_unchanged_source_still_counts_as_a_success(self, knowledge, index_manager, user) -> None:
        fetcher = _ScriptedFetcher(FetchBatch(cursor='{"etag": "v1"}', unchanged=True))
        pipeline, registry = await self._pipeline(knowledge, index_manager, fetcher, user)
        await pipeline.run()
        assert (await registry.get("docs")).last_success_at is not None

    async def test_the_cursor_does_not_advance_when_a_document_failed(self, knowledge, index_manager, user) -> None:
        """If it did, the failed document would not be fetched again until upstream changed."""
        fetcher = _ScriptedFetcher(
            FetchBatch(documents=(_doc("good"), _doc("empty", content="   ")), cursor='{"etag": "v2"}'),
        )
        pipeline, registry = await self._pipeline(knowledge, index_manager, fetcher, user)
        report = await pipeline.run()
        assert report.sources[0].failed == 1
        assert (await registry.get("docs")).last_cursor is None

    async def test_a_source_whose_every_document_failed_is_not_recorded_as_a_success(
        self, knowledge, index_manager, user
    ) -> None:
        fetcher = _ScriptedFetcher(FetchBatch(documents=(_doc("empty", content="   "),), cursor='{"etag": "v2"}'))
        pipeline, registry = await self._pipeline(knowledge, index_manager, fetcher, user)
        await pipeline.run()
        stored = await registry.get("docs")
        assert stored.last_success_at is None
        assert stored.last_cursor is None

    async def test_a_fetcher_without_batches_still_works(self, knowledge, index_manager, user) -> None:
        pipeline, registry = await self._pipeline(
            knowledge, index_manager, StaticSourceFetcher({"docs": [_doc()]}), user
        )
        report = await pipeline.run()
        assert report.documents_ingested == 1
        assert (await registry.get("docs")).last_success_at is not None


pytest.importorskip("fastapi")


class TestRegistrationRefusesUnfetchableTypes:
    @staticmethod
    def _client(container):
        from fastapi.testclient import TestClient

        from aura_core.api import Principal, build_app

        async def authenticator(request):
            return Principal(
                user_id=UserId("op-1"), email="op@example.com", role="SuperAdmin",
                capabilities=("admin.act", "admin.super"),
            )

        return TestClient(build_app(container, authenticator=authenticator))

    @pytest.mark.parametrize("source_type", ["rss", "api", "repository", "drive", "upload"])
    def test_a_type_with_no_fetcher_is_refused(self, container, source_type) -> None:
        with self._client(container) as client:
            response = client.post("/api/v3/admin/sources", json={
                "id": "feed", "type": source_type, "uri": "https://example.com/feed",
            })
            listed = client.get("/api/v3/admin/sources").json()["sources"]
        assert response.status_code == 422, response.text
        assert response.json()["details"]["supported"], response.text
        assert listed == []

    def test_a_web_source_is_still_accepted(self, container) -> None:
        with self._client(container) as client:
            response = client.post("/api/v3/admin/sources", json={
                "id": "page", "type": "web", "uri": "https://example.com/page",
            })
        assert response.status_code == 200, response.text
