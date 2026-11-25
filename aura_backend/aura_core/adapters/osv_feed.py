"""The OSV advisory feed over HTTPS (:class:`~aura_core.ports.advisories.AdvisoryFeed`).

The only outbound call the dependency-vulnerability data needs, and it is pinned: ``base_url`` must be
``https`` on :data:`~aura_core.domain.advisories.FEED_HOST`, redirects are not followed, every
response is size-capped, and an advisory id is validated before it becomes part of a URL. Runs only in
the sync job (a Celery worker), never in the gateway's request path and never inside a tool.

``httpx`` is imported inside the methods that use it (the adapter rule: no vendor SDK at module scope).
"""

from __future__ import annotations

import asyncio
import json
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any, AsyncIterator, Mapping
from urllib.parse import urlsplit

from ..domain.advisories import FEED_BASE_URL, FEED_ECOSYSTEMS, FEED_HOST, parse_osv_time
from ..observability.logging import get_logger

__all__ = ["HttpOsvFeed", "FeedError"]

logger = get_logger("adapters.osv_feed")

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,120}$")
_MAX_INDEX_BYTES = 40 * 1024 * 1024
_MAX_DOCUMENT_BYTES = 5 * 1024 * 1024
_MAX_UNCOMPRESSED_BYTES = 3 * 1024 * 1024 * 1024


class FeedError(RuntimeError):
    """The feed could not be read (network, status, size, or an unusable archive)."""


class HttpOsvFeed:
    def __init__(
        self,
        *,
        base_url: str = FEED_BASE_URL,
        timeout_s: float = 60.0,
        archive_max_bytes: int = 400 * 1024 * 1024,
        allow_other_host: bool = False,
    ) -> None:
        parts = urlsplit(base_url)
        if not allow_other_host and (parts.scheme != "https" or parts.hostname != FEED_HOST):
            raise ValueError(f"the advisory feed must be https://{FEED_HOST}/..., not {parts.scheme}://{parts.hostname}")
        self._base = base_url.rstrip("/")
        self._timeout = timeout_s
        self._archive_max = archive_max_bytes

    def _client(self) -> Any:
        import httpx

        return httpx.AsyncClient(
            timeout=self._timeout, follow_redirects=False, headers={"User-Agent": "aura-advisory-sync"})

    def _dir(self, ecosystem: str) -> str:
        try:
            return FEED_ECOSYSTEMS[ecosystem]
        except KeyError:
            raise FeedError(f"unsupported ecosystem {ecosystem!r}") from None

    async def _get(self, client: Any, url: str, limit: int) -> Any:
        try:
            async with client.stream("GET", url) as response:
                if response.status_code == 404:
                    return None
                if response.status_code != 200:
                    raise FeedError(f"{urlsplit(url).path} answered {response.status_code}")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body += chunk
                    if len(body) > limit:
                        raise FeedError(f"{urlsplit(url).path} is larger than {limit} bytes")
                return bytes(body)
        except FeedError:
            raise
        except Exception as exc:  # network errors are the feed's, whatever the HTTP library calls them
            raise FeedError(f"{type(exc).__name__} reading {urlsplit(url).path}") from exc

    async def modified_since(self, ecosystem: str) -> list[tuple[Any, str]]:
        async with self._client() as client:
            raw = await self._get(client, f"{self._base}/{self._dir(ecosystem)}/modified_id.csv", _MAX_INDEX_BYTES)
        if raw is None:
            raise FeedError(f"{ecosystem}: the modified_id.csv index is missing")
        out = []
        for line in raw.decode("utf-8", "replace").splitlines():
            stamp, _, adv_id = line.partition(",")
            when = parse_osv_time(stamp.strip())
            if when is not None and _ID.match(adv_id.strip()):
                out.append((when, adv_id.strip()))
        return out

    async def fetch(self, ecosystem: str, advisory_id: str) -> Mapping[str, Any] | None:
        if not _ID.match(advisory_id):
            raise FeedError(f"refusing to fetch an advisory with the id {advisory_id[:40]!r}")
        async with self._client() as client:
            raw = await self._get(client, f"{self._base}/{self._dir(ecosystem)}/{advisory_id}.json", _MAX_DOCUMENT_BYTES)
        if raw is None:
            return None
        try:
            doc = json.loads(raw)
        except ValueError as exc:
            raise FeedError(f"{advisory_id} is not valid JSON") from exc
        return doc if isinstance(doc, dict) else None

    async def archive(self, ecosystem: str, *, batch: int = 500) -> AsyncIterator[list[Mapping[str, Any]]]:
        url = f"{self._base}/{self._dir(ecosystem)}/all.zip"
        with tempfile.TemporaryDirectory(prefix="aura-osv-") as tmp:
            path = Path(tmp) / "all.zip"
            async with self._client() as client:
                try:
                    async with client.stream("GET", url) as response:
                        if response.status_code != 200:
                            raise FeedError(f"{urlsplit(url).path} answered {response.status_code}")
                        declared = int(response.headers.get("content-length") or 0)
                        if declared > self._archive_max:
                            raise FeedError(f"{ecosystem} archive is {declared} bytes, over the {self._archive_max} cap")
                        written = 0
                        with path.open("wb") as out:
                            async for chunk in response.aiter_bytes():
                                written += len(chunk)
                                if written > self._archive_max:
                                    raise FeedError(f"{ecosystem} archive grew past the {self._archive_max} byte cap")
                                out.write(chunk)
                except FeedError:
                    raise
                except Exception as exc:
                    raise FeedError(f"{type(exc).__name__} downloading the {ecosystem} archive") from exc
            names = await asyncio.to_thread(self._member_names, path)
            for start in range(0, len(names), batch):
                yield await asyncio.to_thread(self._read_members, path, names[start:start + batch])

    @staticmethod
    def _member_names(path: Path) -> list[str]:
        try:
            with zipfile.ZipFile(path) as archive:
                infos = archive.infolist()
                if sum(i.file_size for i in infos) > _MAX_UNCOMPRESSED_BYTES:
                    raise FeedError("the archive would expand past its size limit")
                return [i.filename for i in infos if i.filename.endswith(".json") and i.file_size <= _MAX_DOCUMENT_BYTES]
        except zipfile.BadZipFile as exc:
            raise FeedError("the archive is not a valid zip file") from exc

    @staticmethod
    def _read_members(path: Path, names: list[str]) -> list[Mapping[str, Any]]:
        docs: list[Mapping[str, Any]] = []
        with zipfile.ZipFile(path) as archive:
            for name in names:
                try:
                    doc = json.loads(archive.read(name))
                except (ValueError, KeyError):
                    logger.warning("skipping an unreadable archive entry")
                    continue
                if isinstance(doc, dict):
                    docs.append(doc)
        return docs
