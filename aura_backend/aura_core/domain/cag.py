"""Cache-Augmented Generation prefix value objects (ADR-010).

A CAG prefix is a small, versioned, byte-stable knowledge block that sits in a fixed
prompt slot after the system instructions. It is *not* retrieved per query -- it is
assembled from pinned documents and reused until those pins (or their versions) change.

``prefix_nonce`` is generated once when the prefix is built and reused for its lifetime so
the rendered bytes stay identical across turns (required for a later KV-prefix cache). It is
a random secret, never derived from document text, so a writer cannot close the fence.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Sequence

from .identifiers import DocumentId, UserId

__all__ = [
    "CagDocRef",
    "CagPrefixIdentity",
    "CagPrefix",
]


@dataclass(frozen=True, slots=True)
class CagDocRef:
    """One document that contributed to a prefix."""

    document_id: DocumentId
    version: int
    content_hash: str
    title: str
    is_global: bool
    tokens: int


@dataclass(frozen=True, slots=True)
class CagPrefixIdentity:
    """Stable key for a prefix: same pins + versions + budget → same identity."""

    value: str

    def __str__(self) -> str:
        return self.value

    @classmethod
    def compute(
        cls,
        *,
        owner_id: UserId | None,
        global_budget: int,
        user_budget: int,
        refs: Sequence[CagDocRef],
    ) -> "CagPrefixIdentity":
        payload = {
            "owner": owner_id.value if owner_id else None,
            "global_budget": global_budget,
            "user_budget": user_budget,
            "docs": [
                {
                    "id": r.document_id.value,
                    "version": r.version,
                    "hash": r.content_hash,
                    "global": r.is_global,
                }
                for r in sorted(
                    refs,
                    key=lambda r: (not r.is_global, r.document_id.value, r.version),
                )
            ],
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return cls(hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32])


@dataclass(frozen=True, slots=True)
class CagPrefix:
    """Assembled prefix ready for the context engine.

    ``text`` is the raw concatenated knowledge (without fence wrappers). The context
    engine wraps it with ``prefix_nonce`` so the fences themselves stay byte-stable.
    """

    identity: CagPrefixIdentity
    text: str
    prefix_nonce: str
    tokens: int
    docs: tuple[CagDocRef, ...] = ()
    citations: tuple[str, ...] = ()
    cache_hit: bool = False
    global_tokens: int = 0
    user_tokens: int = 0
    budget: int = 0

    @property
    def document_ids(self) -> frozenset[str]:
        return frozenset(d.document_id.value for d in self.docs)

    def preview(self) -> dict[str, object]:
        """Content-light summary for the preview endpoint -- not a corpus dump."""
        return {
            "identity": self.identity.value,
            "tokens": self.tokens,
            "budget": self.budget,
            "global_tokens": self.global_tokens,
            "user_tokens": self.user_tokens,
            "cache_hit": self.cache_hit,
            "documents": [
                {
                    "document_id": d.document_id.value,
                    "version": d.version,
                    "title": d.title,
                    "is_global": d.is_global,
                    "tokens": d.tokens,
                }
                for d in self.docs
            ],
            "citations": list(self.citations),
            "text": self.text,
        }
