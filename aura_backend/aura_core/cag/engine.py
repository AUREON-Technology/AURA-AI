"""Cache-Augmented Generation engine (ADR-010).

Assembles a small, versioned, byte-stable knowledge prefix from pinned documents.
RAG remains the query-time path; this is the always-on handbook + user-pin slot.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import replace
from typing import Any

from ..domain.cag import CagDocRef, CagPrefix, CagPrefixIdentity
from ..domain.errors import NotFoundError, ValidationError
from ..domain.identifiers import DocumentId, UserId
from ..domain.knowledge import Document, DocumentStatus, TrustTier
from ..observability.logging import get_logger

__all__ = ["CagEngine"]

logger = get_logger("cag")

_CLIP_MARKER = " […truncated]"
_CACHE_PREFIX = "cag:prefix:"
_OWNER_KEY_PREFIX = "cag:owner:"
#: Global handbook docs must be at least this trusted; user uploads may be any tier.
_GLOBAL_MIN_TRUST = frozenset({TrustTier.INTERNAL, TrustTier.VERIFIED})


def _owner_cache_key(owner_id: UserId | None) -> str:
    return _OWNER_KEY_PREFIX + (owner_id.value if owner_id else "_global_")


def _clip_to_tokens(text: str, max_tokens: int, *, count) -> tuple[str, int]:
    """Fit ``text`` into ``max_tokens``, cutting at a word boundary when needed."""
    if max_tokens <= 0 or not text.strip():
        return "", 0
    tokens = count(text)
    if tokens <= max_tokens:
        return text, tokens
    # Approximate chars from tokens, then shrink until under budget.
    approx_chars = max(16, max_tokens * 4)
    cut = text[:approx_chars]
    if " " in cut[-48:]:
        cut = cut[: cut.rfind(" ")]
    cut = cut.rstrip() + _CLIP_MARKER
    while count(cut) > max_tokens and len(cut) > len(_CLIP_MARKER) + 8:
        cut = cut[: max(8, len(cut) - 24)].rstrip()
        if " " in cut[-32:]:
            cut = cut[: cut.rfind(" ")]
        cut = cut.rstrip() + _CLIP_MARKER
    return cut, count(cut)


def _heading_aware_body(document: Document) -> str:
    """Prefer title + content; keep heading structure when present as markdown."""
    title = document.title.strip()
    body = document.content.strip()
    if title and not body.startswith(title):
        return f"# {title}\n\n{body}" if body else f"# {title}"
    return body or title


class CagEngine:
    """Builds and caches CAG prefixes; owns pin/unpin with budget caps."""

    def __init__(
        self,
        *,
        documents: Any,
        cache: Any = None,
        llm_cache: Any = None,
        tokenizer: Any = None,
        enabled: bool = True,
        max_tokens: int = 80,
        global_tokens: int = 40,
        user_tokens: int = 40,
        max_global_docs: int = 2,
        max_user_docs: int = 3,
        metrics: Any = None,
    ) -> None:
        self._documents = documents
        self._cache = cache
        self._llm_cache = llm_cache
        self._tokenizer = tokenizer
        self._enabled = enabled
        self._max_tokens = max_tokens
        self._global_tokens = min(global_tokens, max_tokens)
        self._user_tokens = min(user_tokens, max(0, max_tokens - self._global_tokens))
        self._max_global_docs = max_global_docs
        self._max_user_docs = max_user_docs
        self._metrics = metrics

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def max_tokens(self) -> int:
        return self._max_tokens

    @property
    def global_tokens(self) -> int:
        return self._global_tokens

    @property
    def user_tokens(self) -> int:
        return self._user_tokens

    def _count(self, text: str) -> int:
        if self._tokenizer is not None:
            return self._tokenizer.count(text)
        return max(1, (len(text) + 3) // 4) if text else 0

    def _record(self, result: str) -> None:
        if self._metrics is not None:
            try:
                self._metrics.cache_lookups_total.labels(
                    cache="cag", tier="prefix", result=result
                ).inc()
            except Exception:
                pass

    async def prefix_for(self, owner_id: UserId | None) -> CagPrefix | None:
        """Assemble (or load from cache) the CAG prefix for this user."""
        if not self._enabled:
            return None
        try:
            pinned = await self._documents.list_cag_pinned(owner_id=owner_id)
        except Exception:
            logger.warning("cag pin listing failed; continuing without prefix")
            return None

        global_docs, user_docs = self._partition(pinned)
        if not global_docs and not user_docs:
            return None

        refs_for_id = [
            CagDocRef(
                document_id=d.id,
                version=d.version,
                content_hash=d.content_hash,
                title=d.title,
                is_global=d.is_global,
                tokens=0,
            )
            for d in (*global_docs, *user_docs)
        ]
        identity = CagPrefixIdentity.compute(
            owner_id=owner_id,
            global_budget=self._global_tokens,
            user_budget=self._user_tokens,
            refs=refs_for_id,
        )

        cached = await self._load_cached(identity)
        if cached is not None:
            self._record("hit")
            return replace(cached, cache_hit=True)

        self._record("miss")
        prefix = self._pack(identity, global_docs, user_docs)
        await self._store_cached(identity, prefix, owner_id=owner_id)
        return prefix

    def _partition(self, pinned: list[Document]) -> tuple[list[Document], list[Document]]:
        global_docs: list[Document] = []
        user_docs: list[Document] = []
        for doc in pinned:
            if doc.status is not DocumentStatus.ACTIVE or not doc.cag_pinned:
                continue
            if doc.is_global:
                if doc.trust_tier not in _GLOBAL_MIN_TRUST:
                    continue
                if doc.status is DocumentStatus.QUARANTINED:
                    continue
                global_docs.append(doc)
            else:
                user_docs.append(doc)

        global_docs.sort(
            key=lambda d: (-int(d.trust_tier), d.id.value, d.version)
        )
        user_docs.sort(
            key=lambda d: (-int(d.trust_tier), d.id.value, d.version)
        )
        return global_docs[: self._max_global_docs], user_docs[: self._max_user_docs]

    def _pack(
        self,
        identity: CagPrefixIdentity,
        global_docs: list[Document],
        user_docs: list[Document],
    ) -> CagPrefix:
        parts: list[str] = []
        refs: list[CagDocRef] = []
        citations: list[str] = []
        global_used = 0
        user_used = 0

        for doc in global_docs:
            remaining = self._global_tokens - global_used
            if remaining < 8:
                break
            body = _heading_aware_body(doc)
            clipped, used = _clip_to_tokens(body, remaining, count=self._count)
            if not clipped:
                continue
            parts.append(clipped)
            citation = f"{doc.id.value}@v{doc.version}"
            citations.append(citation)
            refs.append(
                CagDocRef(
                    document_id=doc.id,
                    version=doc.version,
                    content_hash=doc.content_hash,
                    title=doc.title,
                    is_global=True,
                    tokens=used,
                )
            )
            global_used += used

        for doc in user_docs:
            remaining = self._user_tokens - user_used
            if remaining < 8:
                break
            body = _heading_aware_body(doc)
            clipped, used = _clip_to_tokens(body, remaining, count=self._count)
            if not clipped:
                continue
            parts.append(clipped)
            citation = f"{doc.id.value}@v{doc.version}"
            citations.append(citation)
            refs.append(
                CagDocRef(
                    document_id=doc.id,
                    version=doc.version,
                    content_hash=doc.content_hash,
                    title=doc.title,
                    is_global=False,
                    tokens=used,
                )
            )
            user_used += used

        text = "\n\n".join(parts).strip()
        tokens = self._count(text) if text else 0
        return CagPrefix(
            identity=identity,
            text=text,
            prefix_nonce=secrets.token_hex(6),
            tokens=tokens,
            docs=tuple(refs),
            citations=tuple(citations),
            cache_hit=False,
            global_tokens=global_used,
            user_tokens=user_used,
            budget=self._max_tokens,
        )

    async def _load_cached(self, identity: CagPrefixIdentity) -> CagPrefix | None:
        if self._cache is None:
            return None
        try:
            raw = await self._cache.get(_CACHE_PREFIX + identity.value)
        except Exception:
            return None
        if not raw:
            return None
        try:
            data = json.loads(raw)
            docs = tuple(
                CagDocRef(
                    document_id=DocumentId(d["document_id"]),
                    version=int(d["version"]),
                    content_hash=d["content_hash"],
                    title=d["title"],
                    is_global=bool(d["is_global"]),
                    tokens=int(d["tokens"]),
                )
                for d in data.get("docs", ())
            )
            return CagPrefix(
                identity=identity,
                text=data["text"],
                prefix_nonce=data["prefix_nonce"],
                tokens=int(data["tokens"]),
                docs=docs,
                citations=tuple(data.get("citations", ())),
                cache_hit=True,
                global_tokens=int(data.get("global_tokens", 0)),
                user_tokens=int(data.get("user_tokens", 0)),
                budget=int(data.get("budget", self._max_tokens)),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    async def _store_cached(self, identity: CagPrefixIdentity, prefix: CagPrefix, *, owner_id: UserId | None) -> None:
        if self._cache is None or not prefix.text:
            return
        payload = {
            "text": prefix.text,
            "prefix_nonce": prefix.prefix_nonce,
            "tokens": prefix.tokens,
            "citations": list(prefix.citations),
            "global_tokens": prefix.global_tokens,
            "user_tokens": prefix.user_tokens,
            "budget": prefix.budget,
            "docs": [
                {
                    "document_id": d.document_id.value,
                    "version": d.version,
                    "content_hash": d.content_hash,
                    "title": d.title,
                    "is_global": d.is_global,
                    "tokens": d.tokens,
                }
                for d in prefix.docs
            ],
        }
        try:
            await self._cache.set(
                _CACHE_PREFIX + identity.value,
                json.dumps(payload).encode("utf-8"),
                ttl_s=86_400,
            )
            await self._cache.set(
                _owner_cache_key(owner_id),
                identity.value.encode("utf-8"),
                ttl_s=86_400,
            )
        except Exception:
            return

    async def invalidate(self, owner_id: UserId | None) -> None:
        """Drop cached prefixes that involve this owner and the LLM answer cache."""
        if owner_id is not None and self._llm_cache is not None:
            try:
                await self._llm_cache.invalidate_namespace(owner_id.value)
            except Exception:
                pass
        if self._cache is None:
            return
        try:
            pointer = await self._cache.get(_owner_cache_key(owner_id))
            if pointer:
                identity = pointer.decode("utf-8") if isinstance(pointer, bytes) else str(pointer)
                await self._cache.delete(_CACHE_PREFIX + identity)
            await self._cache.delete(_owner_cache_key(owner_id))
        except Exception:
            return

    async def set_pin(
        self,
        document_id: DocumentId,
        *,
        owner_id: UserId | None,
        pinned: bool,
    ) -> Document:
        """Pin or unpin with cap enforcement, then invalidate caches."""
        document = await self._documents.get(document_id)
        if document is None:
            raise NotFoundError(
                f"document {document_id.value!r} not found",
                details={"document_id": document_id.value},
            )
        doc_owner = document.owner_id.value if document.owner_id else None
        caller = owner_id.value if owner_id else None
        if doc_owner != caller:
            raise NotFoundError(
                f"document {document_id.value!r} not found",
                details={"document_id": document_id.value},
            )

        if pinned and not document.cag_pinned:
            await self._enforce_cap(document, owner_id=owner_id)

        if document.is_global and pinned and document.trust_tier not in _GLOBAL_MIN_TRUST:
            raise ValidationError(
                "global CAG pins require VERIFIED or INTERNAL trust",
                details={
                    "document_id": document_id.value,
                    "trust_tier": int(document.trust_tier),
                },
            )

        updated = await self._documents.set_cag_pin(
            document_id, owner_id=owner_id, pinned=pinned
        )
        # Invalidate both the previous identity (if any) and the caller's LLM cache.
        await self.invalidate(owner_id)
        # Global pin changes affect every user; also clear the global-only identity.
        if document.is_global:
            await self.invalidate(None)
        return updated

    async def _enforce_cap(self, document: Document, *, owner_id: UserId | None) -> None:
        pinned = await self._documents.list_cag_pinned(owner_id=owner_id)
        if document.is_global:
            count = sum(1 for d in pinned if d.is_global and d.cag_pinned)
            if count >= self._max_global_docs:
                raise ValidationError(
                    f"global CAG pin limit reached ({self._max_global_docs})",
                    details={"max_global_docs": self._max_global_docs, "current": count},
                )
        else:
            count = sum(
                1
                for d in pinned
                if not d.is_global
                and d.cag_pinned
                and d.owner_id is not None
                and owner_id is not None
                and d.owner_id.value == owner_id.value
            )
            if count >= self._max_user_docs:
                raise ValidationError(
                    f"user CAG pin limit reached ({self._max_user_docs})",
                    details={"max_user_docs": self._max_user_docs, "current": count},
                )

    async def on_document_changed(
        self, document_id: DocumentId, *, owner_id: UserId | None
    ) -> None:
        """Call after ingest/delete of a document that may be pinned."""
        document = await self._documents.get(document_id)
        if document is not None and document.cag_pinned:
            await self.invalidate(owner_id)
            if document.is_global:
                await self.invalidate(None)
        elif document is None:
            # Deleted — invalidate the owner's prefix regardless.
            await self.invalidate(owner_id)
