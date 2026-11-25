"""Two-tier LLM response cache (ADR-003).

There is no response cache in the current system: identical questions re-run four embeddings,
two vector searches, and a full generation. For an assistant, where users genuinely repeat
themselves, this is the highest-leverage optimisation available -- and the only one that reduces
latency *and* cost at the same time.

Tier 1, **exact**: SHA-256 over normalised messages + model + sampling parameters + tool
declarations. Sub-millisecond, no false positives.

Tier 2, **semantic**: embed the final user message, compare against recent entries, accept above
a high threshold (0.97 by default). Catches "what's my schedule?" vs "what is my schedule".

Two invariants make this safe:

* **Only deterministic requests are cached.** ``temperature > 0`` means the caller asked for
  sampling; serving a stored sample would make non-determinism *inconsistent* rather than
  absent, which is a worse contract than either extreme.
* **Cache keys are namespaced per user for anything with context.** A response grounded in one
  user's documents must never be served to another. The namespace is part of the key, not a
  filter applied afterwards.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
from dataclasses import dataclass
from typing import Any, Sequence

from ..domain.llm import CompletionRequest, CompletionResponse, FinishReason, TokenUsage
from ..domain.messages import Role
from ..ports.storage import Cache
from .embeddings import cosine_similarity
from .turn_embeddings import embed_query

__all__ = ["LLMCache", "CacheHit", "InMemoryCache", "semantic_tier_useful"]

#: The per-turn DATA fence delimiter (``<<<AURA-DATA:<nonce>``), nonce captured separately.
_FENCE_NONCE = re.compile(r"(<<<(?:END-)?AURA-DATA:)[0-9A-Za-z]+")


def semantic_tier_useful(embedder: Any) -> bool:
    """Whether the semantic tier can ever hit with ``embedder`` (PP-08).

    An embedder declares ``is_semantic = False`` when its similarity is not similarity of
    meaning. With the hash embedder the tier never fires at the default 0.97 threshold --
    measured 2026-11-08: this module's own example pair, "what's my schedule?" vs "what is my
    schedule", scores 0.64 -- yet every single-turn miss still paid an embedding and a cache read
    for it. Undeclared counts as semantic, so a real embedding model keeps the tier on.
    """
    return embedder is not None and bool(getattr(embedder, "is_semantic", True))


@dataclass(frozen=True, slots=True)
class CacheHit:
    response: CompletionResponse
    tier: str
    similarity: float | None = None


class InMemoryCache:
    """Bounded TTL-LRU implementing the :class:`Cache` port.

    Bounded is the operative word. The legacy code has five unbounded in-process dicts
    (embedding cache, memory cache, rate-limit history, nonce set, refresh-token store) that grow
    until the process is OOM-killed -- see the Performance Report §3. Every cache in this
    codebase has a maximum size and a TTL.
    """

    def __init__(self, *, max_entries: int = 2048, default_ttl_s: int = 3600) -> None:
        self._max = max_entries
        self._default_ttl = default_ttl_s
        self._data: dict[str, tuple[float, bytes]] = {}
        self._lists: dict[str, tuple[float, list[bytes]]] = {}

    def _evict_if_needed(self) -> None:
        while len(self._data) > self._max:
            # dicts preserve insertion order, and every write re-inserts, so the first key is the
            # least recently written -- an LRU without a second data structure.
            self._data.pop(next(iter(self._data)))

    async def get(self, key: str) -> bytes | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at and expires_at < time.time():
            self._data.pop(key, None)
            return None
        # Refresh recency.
        self._data.pop(key)
        self._data[key] = entry
        return value

    async def set(self, key: str, value: bytes, *, ttl_s: int | None = None) -> None:
        ttl = self._default_ttl if ttl_s is None else ttl_s
        self._data.pop(key, None)
        self._data[key] = (time.time() + ttl if ttl else 0.0, value)
        self._evict_if_needed()

    async def delete(self, key: str) -> None:
        self._data.pop(key, None)
        self._lists.pop(key, None)

    async def incr(self, key: str, *, ttl_s: int | None = None, amount: int = 1) -> int:
        raw = await self.get(key)
        value = (int(raw) if raw else 0) + int(amount)
        await self.set(key, str(value).encode(), ttl_s=ttl_s)
        return value

    async def append_capped(self, key: str, value: bytes, *, max_len: int, ttl_s: int | None = None) -> int:
        # No ``await`` between the read and the write below, so on one event loop this is atomic --
        # the same guarantee Redis' RPUSH + LTRIM transaction gives across processes.
        ttl = self._default_ttl if ttl_s is None else ttl_s
        expires_at, items = self._lists.pop(key, (0.0, []))
        if expires_at and expires_at < time.time():
            items = []
        items = [*items, value][-max(1, int(max_len)):]
        self._lists[key] = (time.time() + ttl if ttl else 0.0, items)
        while len(self._lists) > self._max:
            self._lists.pop(next(iter(self._lists)))
        return len(items)

    async def get_list(self, key: str) -> list[bytes]:
        entry = self._lists.get(key)
        if entry is None:
            return []
        expires_at, items = entry
        if expires_at and expires_at < time.time():
            self._lists.pop(key, None)
            return []
        return list(items)

    async def get_many(self, keys: Sequence[str]) -> dict[str, bytes]:
        found = {}
        for key in keys:
            value = await self.get(key)
            if value is not None:
                found[key] = value
        return found

    def __len__(self) -> int:
        return len(self._data) + len(self._lists)

    def clear(self) -> None:
        self._data.clear()
        self._lists.clear()


class LLMCache:
    """Exact + semantic caching in front of the provider router."""

    _EXACT_PREFIX = "llm:exact:"
    _SEM_INDEX_PREFIX = "llm:semidx:"
    _SEM_ENTRY_PREFIX = "llm:sem:"
    _SEM_GEN_PREFIX = "llm:semgen:"

    def __init__(
        self,
        cache: Cache,
        *,
        embedder: Any = None,
        enabled: bool = True,
        semantic_enabled: bool = True,
        similarity_threshold: float = 0.97,
        ttl_s: int = 3600,
        semantic_index_size: int = 64,
        metrics: Any = None,
    ) -> None:
        self._cache = cache
        self._embedder = embedder
        self._enabled = enabled
        self._semantic_enabled = semantic_enabled and embedder is not None
        self._threshold = similarity_threshold
        self._ttl = ttl_s
        self._index_size = semantic_index_size
        self._metrics = metrics

    # -- keys --------------------------------------------------------------------------

    @staticmethod
    def _normalise(content: str) -> str:
        # Whitespace-insensitive: "hello  world" and "hello world" are the same request. The
        # per-turn fence nonce (ADR-006) is masked: it changes every turn while the fenced
        # content -- which is what the answer rests on -- stays in the key byte for byte.
        return _FENCE_NONCE.sub(r"\1*", " ".join(content.split()))

    @classmethod
    def _key_payload(cls, request: CompletionRequest, *, skip_last_user: bool = False) -> dict:
        """Everything about ``request`` that can change the answer, as a JSON-able dict."""
        last_user = request.conversation.last_user_message if skip_last_user else None
        return {
            "model": request.model or "",
            "policy": str(request.policy),
            "sampling": list(request.sampling.cache_key_parts()),
            "schema": request.response_schema or None,
            "tool_choice": str(request.tool_choice),
            "tools": sorted(t.get("name", "") for t in request.tools),
            "messages": [
                {"role": str(m.role), "content": cls._normalise(m.content)}
                for m in request.conversation
                if m is not last_user
            ],
        }

    @classmethod
    def _fingerprint(cls, request: CompletionRequest, namespace: str) -> str:
        """Stable hash of everything that can change the answer.

        Tool *declarations* are included because their presence changes model behaviour, but
        their descriptions are hashed rather than stored, so a prompt is never reconstructible
        from a cache key.
        """
        payload = {"ns": namespace, **cls._key_payload(request)}
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    @classmethod
    def _context_digest(cls, request: CompletionRequest) -> str:
        """Hash of everything except the final user message (F13).

        The semantic tier compares only the user's words, so everything else -- the system
        message carrying memory, retrieved chunks and caller evidence, the response schema,
        sampling, policy -- has to be *identical* for a semantic match to be allowed at all.
        Otherwise "summarise order 1042" is answered with 1043's cached summary.
        """
        blob = json.dumps(
            cls._key_payload(request, skip_last_user=True),
            sort_keys=True, ensure_ascii=False, default=str,
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    @staticmethod
    def _scopes(namespace: str) -> list[str]:
        # The kernel keys a team turn ``member|team`` (ADR-034); either half changing must
        # invalidate it.
        return [part for part in namespace.split("|") if part] or [namespace]

    @classmethod
    def _generation_key(cls, scope: str) -> str:
        return cls._SEM_GEN_PREFIX + hashlib.sha256(scope.encode("utf-8")).hexdigest()

    async def _semantic_index_key(self, request: CompletionRequest, namespace: str) -> str:
        """The semantic index for this namespace, this exact context, and its generations."""
        scopes = self._scopes(namespace)
        found = await self._cache.get_many([self._generation_key(s) for s in scopes])
        generations = []
        for scope in scopes:
            raw = found.get(self._generation_key(scope))
            generations.append(
                raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw or "0")
            )
        material = "|".join([namespace, self._context_digest(request), *generations])
        return self._SEM_INDEX_PREFIX + hashlib.sha256(material.encode("utf-8")).hexdigest()

    @staticmethod
    def _serialise(response: CompletionResponse) -> bytes:
        return json.dumps(
            {
                "text": response.text,
                "provider": response.provider,
                "model": response.model,
                "finish_reason": str(response.finish_reason),
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "cost_usd": response.cost_usd,
            }
        ).encode("utf-8")

    @staticmethod
    def _deserialise(raw: bytes, *, tier: str) -> CompletionResponse | None:
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError, ValueError):
            # A corrupt entry is a miss, never an exception: the cache must not be able to fail
            # a request.
            return None
        return CompletionResponse(
            text=data.get("text", ""),
            provider=data.get("provider", "cache"),
            model=data.get("model", ""),
            finish_reason=FinishReason(data.get("finish_reason", "stop")),
            usage=TokenUsage(
                prompt_tokens=int(data.get("prompt_tokens", 0)),
                completion_tokens=int(data.get("completion_tokens", 0)),
            ),
            cached=True,
            cache_tier=tier,
            # A cache hit costs nothing. Reporting the original cost would double-count spend.
            cost_usd=0.0,
            raw_metadata={"original_cost_usd": data.get("cost_usd", 0.0)},
        )

    def _record(self, tier: str, result: str) -> None:
        if self._metrics is not None:
            self._metrics.cache_lookups_total.labels(cache="llm", tier=tier, result=result).inc()

    # -- api ---------------------------------------------------------------------------

    def is_cacheable(self, request: CompletionRequest) -> bool:
        """Only deterministic, tool-free requests.

        Tool-calling turns are excluded because a cached tool call would be replayed against
        possibly-changed external state -- serving a stale "your calendar says X" is worse than
        a cache miss.
        """
        if not self._enabled:
            return False
        if not request.sampling.is_deterministic:
            return False
        if request.tools:
            return False
        return True

    async def lookup(self, request: CompletionRequest, *, namespace: str = "global") -> CacheHit | None:
        if not self.is_cacheable(request):
            return None

        key = self._EXACT_PREFIX + self._fingerprint(request, namespace)
        raw = await self._cache.get(key)
        if raw is not None:
            response = self._deserialise(raw, tier="exact")
            if response is not None:
                self._record("exact", "hit")
                return CacheHit(response=response, tier="exact")
        self._record("exact", "miss")

        if not self._semantic_enabled:
            return None
        return await self._semantic_lookup(request, namespace)

    async def _semantic_lookup(self, request: CompletionRequest, namespace: str) -> CacheHit | None:
        last_user = request.conversation.last_user_message
        if last_user is None or not last_user.content.strip():
            return None

        # Semantic matching only applies to single-turn questions. In a multi-turn conversation
        # the same final message can mean entirely different things depending on what preceded
        # it, so similarity on the last message alone is not a safe equivalence.
        non_system = [m for m in request.conversation if m.role is not Role.SYSTEM]
        if len(non_system) != 1:
            return None

        index_key = await self._semantic_index_key(request, namespace)
        raw_index = await self._cache.get(index_key)
        if not raw_index:
            self._record("semantic", "miss")
            return None
        try:
            index: list[dict[str, Any]] = json.loads(raw_index)
        except (json.JSONDecodeError, TypeError):
            return None
        if not index:
            self._record("semantic", "miss")
            return None

        try:
            # The turn's message was usually embedded already, by recall or retrieval (KAN-111).
            embedding = await embed_query(self._embedder, last_user.content)
        except Exception:
            # Embedding failure must degrade to a miss, not fail the turn.
            self._record("semantic", "error")
            return None

        query_vector = embedding.first
        best_entry: dict[str, Any] | None = None
        best_score = 0.0
        for entry in index:
            vector = entry.get("v")
            if not vector or len(vector) != len(query_vector):
                continue
            score = cosine_similarity(query_vector, vector)
            if score > best_score:
                best_score, best_entry = score, entry

        if best_entry is None or best_score < self._threshold:
            self._record("semantic", "miss")
            return None

        raw = await self._cache.get(self._SEM_ENTRY_PREFIX + best_entry["k"])
        if raw is None:
            self._record("semantic", "miss")
            return None
        response = self._deserialise(raw, tier="semantic")
        if response is None:
            return None
        self._record("semantic", "hit")
        return CacheHit(response=response, tier="semantic", similarity=round(best_score, 4))

    async def store(
        self, request: CompletionRequest, response: CompletionResponse, *, namespace: str = "global"
    ) -> None:
        """Write through both tiers. Never raises -- a cache write failure is not a turn failure."""
        if not self.is_cacheable(request) or not response.text:
            return
        try:
            fingerprint = self._fingerprint(request, namespace)
            payload = self._serialise(response)
            await self._cache.set(self._EXACT_PREFIX + fingerprint, payload, ttl_s=self._ttl)

            if not self._semantic_enabled:
                return
            non_system = [m for m in request.conversation if m.role is not Role.SYSTEM]
            if len(non_system) != 1:
                return
            last_user = request.conversation.last_user_message
            if last_user is None:
                return

            embedding = await embed_query(self._embedder, last_user.content)
            await self._cache.set(self._SEM_ENTRY_PREFIX + fingerprint, payload, ttl_s=self._ttl)

            index_key = await self._semantic_index_key(request, namespace)
            raw_index = await self._cache.get(index_key)
            try:
                index = json.loads(raw_index) if raw_index else []
            except (json.JSONDecodeError, TypeError):
                index = []
            index = [e for e in index if e.get("k") != fingerprint]
            index.append({"k": fingerprint, "v": list(embedding.first)})
            # Bounded: the semantic index is scanned linearly on every lookup, so its size is a
            # direct latency cost. 64 entries is ~25 us of float math.
            index = index[-self._index_size :]
            await self._cache.set(
                index_key, json.dumps(index).encode("utf-8"), ttl_s=self._ttl
            )
        except Exception:
            return

    async def invalidate_namespace(self, namespace: str, *, model: str = "") -> None:
        """Retire every semantic index that rests on ``namespace``.

        Called when a user's knowledge changes: a cached answer grounded in a document that has
        since been superseded is stale in a way TTL alone would not catch quickly enough.

        Index keys carry a generation token per scope (F13), so this rotates the token rather
        than deleting one key: it reaches every model and every context digest, and every team
        namespace (``member|team``) the scope is part of. ``model`` is accepted for
        compatibility and no longer narrows anything. The token is random, never a counter, so
        a token that expires can never come back and resurrect an index written under it; it
        outlives every index written under the previous one.
        """
        del model
        for scope in self._scopes(namespace):
            await self._cache.set(
                self._generation_key(scope),
                secrets.token_hex(8).encode("utf-8"),
                ttl_s=self._ttl + 60 if self._ttl else 0,
            )
