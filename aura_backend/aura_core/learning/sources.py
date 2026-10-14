"""Approved knowledge sources and how documents are fetched from them.

The refresh pipeline reads **only** from this registry. There is deliberately no path by which a
scheduled job fetches an arbitrary URL: a source list that anyone could append to would turn the
nightly job into a server-side request forgery primitive running with the deployment's network
position and no user watching.

Consequently:

* every source is registered explicitly, by an administrator, and the registration is audited;
* HTTP fetching enforces an https-only, private-range-refusing, redirect-refusing policy -- the
  same rules as the ``fetch_url`` tool, for the same reason;
* every source declares its own ceilings (document count, bytes per document), so one
  misbehaving source cannot consume the whole run's budget.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import re
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Awaitable, Callable, Mapping, Protocol, Sequence, runtime_checkable
from urllib.parse import urlparse

from ..domain.errors import AuraError, AuthorizationError, NotFoundError, ValidationError
from ..domain.identifiers import DocumentId
from ..domain.knowledge import KnowledgeSource, SourceType, TrustTier
from ..domain.network import is_internal_address
from ..observability.logging import get_logger
from ..ports.connectors import DocumentConnector

__all__ = [
    "FetchedDocument",
    "SourceRegistry",
    "InMemorySourceRegistry",
    "SourceFetcher",
    "HttpSourceFetcher",
    "StaticSourceFetcher",
    "CompositeFetcher",
    "ConnectorSourceFetcher",
    "CONNECTOR_URI_PREFIX",
]

logger = get_logger("learning.sources")


@dataclass(frozen=True, slots=True)
class FetchedDocument:
    title: str
    content: str
    document_id: DocumentId | None = None
    uri: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class SourceRegistry(Protocol):
    async def list_enabled(self) -> Sequence[KnowledgeSource]:
        ...

    async def get(self, source_id: str) -> KnowledgeSource | None:
        ...

    async def register(self, source: KnowledgeSource) -> None:
        ...

    async def remove(self, source_id: str) -> bool:
        ...

    async def set_enabled(self, source_id: str, enabled: bool) -> None:
        ...

    async def record_success(self, source_id: str, *, cursor: str | None, at: datetime) -> None:
        ...


class InMemorySourceRegistry:
    """Reference registry. Production persists to Postgres via the same interface."""

    def __init__(self, sources: Sequence[KnowledgeSource] = ()) -> None:
        self._sources: dict[str, KnowledgeSource] = {s.id: s for s in sources}

    async def list_enabled(self) -> list[KnowledgeSource]:
        return [s for s in self._sources.values() if s.enabled]

    async def list_all(self) -> list[KnowledgeSource]:
        return list(self._sources.values())

    async def get(self, source_id: str) -> KnowledgeSource | None:
        return self._sources.get(source_id)

    async def register(self, source: KnowledgeSource) -> None:
        if source.id in self._sources:
            raise ValidationError(
                f"source {source.id!r} is already registered",
                details={"registered": sorted(self._sources)},
            )
        self._sources[source.id] = source
        logger.info("knowledge source registered", extra={"source": source.id, "type": str(source.type)})

    async def remove(self, source_id: str) -> bool:
        return self._sources.pop(source_id, None) is not None

    async def set_enabled(self, source_id: str, enabled: bool) -> None:
        source = self._sources.get(source_id)
        if source is None:
            raise NotFoundError(f"unknown source {source_id!r}")
        self._sources[source_id] = replace(source, enabled=enabled)

    async def record_success(self, source_id: str, *, cursor: str | None, at: datetime) -> None:
        source = self._sources.get(source_id)
        if source is not None:
            self._sources[source_id] = source.with_progress(cursor=cursor, at=at)


@runtime_checkable
class SourceFetcher(Protocol):
    async def fetch(self, source: KnowledgeSource) -> Sequence[FetchedDocument]:
        ...


class StaticSourceFetcher:
    """Serves pre-supplied documents. The test substrate for the refresh pipeline."""

    def __init__(self, documents: Mapping[str, Sequence[FetchedDocument]] | None = None) -> None:
        self._documents: dict[str, list[FetchedDocument]] = {
            k: list(v) for k, v in (documents or {}).items()
        }

    def set(self, source_id: str, documents: Sequence[FetchedDocument]) -> None:
        self._documents[source_id] = list(documents)

    async def fetch(self, source: KnowledgeSource) -> list[FetchedDocument]:
        return list(self._documents.get(source.id, []))


class HttpSourceFetcher:
    """Fetches over HTTPS under a strict network policy.

    The policy is identical to the ``fetch_url`` tool's, and for the same reason: this code runs
    on a schedule, unattended, with the deployment's network position. An SSRF here would be
    harder to notice than one triggered by a user request.
    """

    _TAG = re.compile(r"<[^>]+>")
    _SCRIPT = re.compile(r"<(script|style).*?</\1>", re.S | re.I)
    _TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)

    def __init__(
        self,
        *,
        timeout_s: float = 30.0,
        max_bytes: int = 4 * 1024 * 1024,
        user_agent: str = "AURA-KnowledgeRefresh/3.0",
    ) -> None:
        self._timeout = timeout_s
        self._max_bytes = max_bytes
        self._user_agent = user_agent

    @staticmethod
    def _assert_fetchable(url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "https":
            raise AuthorizationError("knowledge sources must use https", details={"url": url[:120]})
        host = (parsed.hostname or "").lower()
        if not host or host in {"localhost", "metadata.google.internal", "metadata"}:
            raise AuthorizationError("host is not permitted", details={"host": host})
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return
        if is_internal_address(address):
            raise AuthorizationError(
                "private and link-local addresses are not permitted", details={"host": host}
            )

    @staticmethod
    async def _assert_resolves_publicly(url: str) -> None:
        """The DNS half of the policy -- what ``fetch_url`` already did and this did not. An
        approved source is only ever checked by name above; if its DNS later points inside the
        network (a lapsed domain, a rebinding record) the nightly job would fetch it unattended.
        Same TOCTOU caveat as the tool: this narrows the window, IP pinning would close it."""
        import socket

        host = (urlparse(url).hostname or "").lower()
        try:
            infos = await asyncio.to_thread(socket.getaddrinfo, host, None)
        except socket.gaierror as exc:
            raise ValidationError(
                f"could not resolve source host {host!r}", details={"host": host}
            ) from exc
        for _family, _kind, _proto, _canon, sockaddr in infos:
            address = ipaddress.ip_address(str(sockaddr[0]).split("%", 1)[0])
            if is_internal_address(address):
                raise AuthorizationError(
                    "source host resolves to a private address; refusing to fetch",
                    details={"host": host},
                )

    async def fetch(self, source: KnowledgeSource) -> list[FetchedDocument]:
        self._assert_fetchable(source.uri)
        await self._assert_resolves_publicly(source.uri)
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover
            raise ValidationError("httpx is required for HTTP knowledge sources") from exc

        async with httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=False,  # a redirect is a trivial policy bypass
            headers={"User-Agent": self._user_agent},
        ) as client:
            response = await client.get(source.uri)
            if response.is_redirect:
                raise ValidationError(
                    "source redirected; redirects are not followed", details={"source": source.id}
                )
            response.raise_for_status()
            raw = response.text[: self._max_bytes]

        title_match = self._TITLE.search(raw)
        title = (title_match.group(1).strip() if title_match else source.id)[:200]
        body = self._SCRIPT.sub(" ", raw)
        body = self._TAG.sub(" ", body)
        body = re.sub(r"\s+", " ", body).strip()

        return [
            FetchedDocument(
                title=title,
                content=body,
                document_id=DocumentId(f"{source.id}:root"),
                uri=source.uri,
                metadata={"content_type": response.headers.get("content-type", "")},
            )
        ]


class CompositeFetcher:
    """Dispatches to a fetcher per source type.

    Keeps the refresh pipeline free of ``if source.type == ...`` branches, so adding a source
    type (a Git repository, an S3 prefix, a Confluence space) is a registration rather than an
    edit to the pipeline.
    """

    def __init__(self, fetchers: Mapping[SourceType, SourceFetcher], *, default: SourceFetcher | None = None) -> None:
        self._fetchers = dict(fetchers)
        self._default = default

    def register(self, source_type: SourceType, fetcher: SourceFetcher) -> None:
        self._fetchers[source_type] = fetcher

    async def fetch(self, source: KnowledgeSource) -> Sequence[FetchedDocument]:
        fetcher = self._fetchers.get(source.type, self._default)
        if fetcher is None:
            raise ValidationError(
                f"no fetcher is registered for source type {source.type}",
                details={"registered": [str(t) for t in self._fetchers]},
            )
        documents = await fetcher.fetch(source)
        # Enforce the per-source document ceiling here rather than trusting each fetcher to
        # respect it -- one place to get right.
        return list(documents)[: source.max_documents]


#: A connector-backed source's ``uri``: ``connector:<connector id>``.
CONNECTOR_URI_PREFIX = "connector:"
_CONNECTOR_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class ConnectorSourceFetcher:
    """Documents read through an active Track AP connector (AP10), for the nightly refresh.

    A ``connector`` source names a registered connector (``connector:<id>``) whose kind can list
    and fetch documents -- a Files directory or a Git checkout's documentation. Every run re-lists
    them, so a document added or edited in the connected system reaches knowledge without a new
    blueprint; the knowledge engine's content hash keeps unchanged ones from being re-embedded.

    Two rules hold here, not in the caller, because a source registry row is data an
    administrator typed and this runs unattended:

    * **untrusted content** -- the text was written by whoever administers the connected system
      (ADR-022), so a source above ``TrustTier.COMMUNITY`` is refused rather than ingested with
      lighter guards;
    * **project ownership** -- the source's owner must be a project's knowledge owner
      (``project:<id>``). A connector's documents are that project's knowledge; ingesting them
      globally or under a person would reach users the project's isolation was built to exclude.

    The connector is opened through the registry's ``open_active``, so a connector that is not
    active (pending approval, disabled, deleted) fails the source -- it is never read around.
    """

    def __init__(self, *, open_connector: Callable[[str], Awaitable[Any]], extract: Callable[..., Any],
                 owner_prefix: str = "project:") -> None:
        self._open = open_connector
        self._extract = extract
        self._owner_prefix = owner_prefix

    async def fetch(self, source: KnowledgeSource) -> list[FetchedDocument]:
        connector_id = source.uri.removeprefix(CONNECTOR_URI_PREFIX)
        if not source.uri.startswith(CONNECTOR_URI_PREFIX) or not _CONNECTOR_ID.match(connector_id):
            raise ValidationError("a connector source's uri is connector:<connector id>",
                                  details={"source": source.id})
        if source.trust_tier > TrustTier.COMMUNITY:
            raise AuthorizationError("connector content is untrusted; register it at trust tier 1 or lower",
                                     details={"source": source.id, "trust_tier": int(source.trust_tier)})
        if source.owner_id is None or not source.owner_id.value.startswith(self._owner_prefix):
            raise AuthorizationError("a connector source must be owned by a project",
                                     details={"source": source.id})
        connector = await self._open(connector_id)
        try:
            if not isinstance(connector, DocumentConnector):
                raise ValidationError("this connector cannot list documents", details={"connector": connector_id})
            locators = list(await connector.list_documents())[: source.max_documents]
            documents: list[FetchedDocument] = []
            for locator in locators:
                try:
                    fetched = await connector.fetch_document(locator)
                    if len(fetched.data) > source.max_bytes_per_document:
                        logger.info("connector document over the source's ceiling",
                                    extra={"source": source.id, "bytes": len(fetched.data)})
                        continue
                    extraction = self._extract(fetched.data, format=fetched.format, title=fetched.name)
                    text = "\n\n".join(b.text for b in extraction.blocks if b.text).strip()
                except AuraError as exc:
                    logger.warning("connector document skipped",
                                   extra={"source": source.id, "error": exc.kind})
                    continue
                if not text:
                    continue
                key = hashlib.sha256(f"{connector_id}\x00{locator}".encode("utf-8")).hexdigest()[:32]
                documents.append(FetchedDocument(
                    title=fetched.name[:200], content=text, document_id=DocumentId(f"cs-{key}"),
                    uri=f"connector://{connector_id}/{locator}"[:2000],
                    metadata={"connector": connector_id, "locator": locator[:512], "format": fetched.format},
                ))
            return documents
        finally:
            await connector.aclose()


async def gather_sources(
    registry: SourceRegistry, fetcher: SourceFetcher, *, concurrency: int = 3
) -> dict[str, Sequence[FetchedDocument]]:
    """Fetch every enabled source concurrently. Used by the admin preview endpoint."""
    sources = await registry.list_enabled()
    semaphore = asyncio.Semaphore(concurrency)

    async def one(source: KnowledgeSource) -> tuple[str, Sequence[FetchedDocument]]:
        async with semaphore:
            try:
                return source.id, await fetcher.fetch(source)
            except Exception as exc:
                logger.warning(
                    "source preview failed",
                    extra={"source": source.id, "error": type(exc).__name__},
                )
                return source.id, []

    return dict(await asyncio.gather(*(one(s) for s in sources)))
