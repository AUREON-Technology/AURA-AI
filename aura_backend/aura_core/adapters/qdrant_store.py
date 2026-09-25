"""Qdrant implementation of the :class:`~aura_core.ports.storage.VectorStore` port.

Addressed by :class:`~aura_core.domain.knowledge.IndexIdentity` throughout, never by a bare
collection name. ``identity.physical_name`` encodes the logical name, schema version, embedding
model and dimension, so writing 768-dimensional vectors into a 384-dimensional index is not a
mistake this adapter can make -- the two identities name two different collections (SEC-03).

Three behaviours here are load-bearing and worth reading before changing anything:

**``ensure_index`` never recreates.** A dimension mismatch on an existing collection raises
``ConflictError``. The legacy ``services/rag.py`` deleted and recreated the collection on a
mismatch, which meant a configuration change silently destroyed the corpus -- and, because the
same helper ran on the read path, so did a single search. Destroying data is an explicit admin
operation, never a side effect.

**Aliases are the promotion primitive.** ``set_alias`` uses Qdrant's atomic alias operations, so
promoting a freshly built index and rolling back are the same O(1) action with no data movement
(ADR-007). ``drop_index`` refuses while an alias still points at the collection, because that is
the difference between "retire an old generation" and "delete production".

**Tenant isolation is applied in the query, not after it.** The owner filter goes into Qdrant's
``query_filter`` rather than being applied to the returned rows. Post-filtering a top-k result
would let another tenant's document displace a legitimate one out of the k, which is a correctness
bug that reads as a relevance problem.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Mapping, Sequence

from ..domain.errors import ConfigurationError, ConflictError, DependencyUnavailableError
from ..domain.identifiers import UserId
from ..domain.knowledge import IndexIdentity
from ..ports.storage import VectorPoint

logger = logging.getLogger(__name__)

__all__ = ["QdrantVectorStore"]

#: Payload values that mean "not owned by any single tenant". Kept identical to
#: InMemoryVectorStore._matches so the two adapters cannot disagree about what "global" means --
#: a difference here would show up as content visible in one deployment and not the other.
_GLOBAL_OWNERS = (None, "", "global")

#: Qdrant's distance enum names, keyed by the port's vocabulary.
_DISTANCES = {"cosine": "Cosine", "dot": "Dot", "euclid": "Euclid"}


def _unavailable(message: str, **details: Any) -> DependencyUnavailableError:
    """Every backend failure in this module is the same kind of event: Qdrant did not answer.

    A helper rather than ten call sites repeating ``dependency="qdrant"`` -- the argument is
    required, and a missed one would turn a 503 into a TypeError inside an exception handler, which
    is the worst place to discover it.
    """
    return DependencyUnavailableError(
        message, dependency="qdrant", details=details or None
    )


class QdrantVectorStore:
    """Implements the ``VectorStore`` port against Qdrant."""

    def __init__(
        self,
        url: str,
        *,
        api_key: str | None = None,
        timeout_s: float = 30.0,
        metrics: Any = None,
    ) -> None:
        if not url:
            raise ConfigurationError(
                "vector_backend='qdrant' requires AURA_QDRANT_URL",
                details={"setting": "AURA_QDRANT_URL"},
            )
        self._url = url
        self._api_key = api_key or None
        self._timeout = timeout_s
        self._metrics = metrics
        self._client: Any = None

    # -- connection -------------------------------------------------------------------

    def _get_client(self) -> Any:
        """Built on first use, so constructing the container needs no reachable Qdrant."""
        if self._client is None:
            try:
                from qdrant_client import AsyncQdrantClient
            except ImportError as exc:  # pragma: no cover - dependency is declared
                raise ConfigurationError(
                    "vector_backend='qdrant' requires the qdrant-client package",
                    details={"install": "pip install qdrant-client"},
                ) from exc
            self._client = AsyncQdrantClient(
                url=self._url,
                api_key=self._api_key,
                timeout=int(self._timeout),
                port=6333,
                prefer_grpc=False,
                # The client's default (True) spawns a background-thread HTTP call inside
                # __init__ to compare client/server versions -- a network call at construction
                # time, which is exactly what every other adapter in this file avoids (see the
                # class docstring's "built on first use" comment above). It isn't what raised
                # the DependencyUnavailableError this replaces (that trace is a synchronous
                # httpx.ConnectError from the first real request, not this check), but it's a
                # real violation of the lazy-construction rule regardless, worth removing on its
                # own terms rather than leaving a second, unrelated eager network call in place.
                check_compatibility=False,
            )
        return self._client

    # -- index lifecycle --------------------------------------------------------------

    async def ensure_index(self, identity: IndexIdentity) -> None:
        """Create the collection if absent; refuse to alter one that exists.

        The mismatch branch raises rather than recreating. See the module docstring: this is the
        defect that let a config change -- or a mere search -- wipe the corpus in the legacy path.
        """
        from qdrant_client import models as qmodels

        client = self._get_client()
        name = identity.physical_name

        if await self.index_exists(identity):
            info = await client.get_collection(name)
            existing_size, existing_distance = self._describe_vectors(info)
            if existing_size is not None and existing_size != identity.dimension:
                raise ConflictError(
                    f"index {name!r} stores {existing_size}-dimensional vectors but the requested "
                    f"identity declares {identity.dimension}. Refusing to alter it -- build a new "
                    f"identity and promote it with an alias instead.",
                    details={
                        "index": name,
                        "existing_dimension": existing_size,
                        "requested_dimension": identity.dimension,
                    },
                )
            wanted_distance = _DISTANCES.get(identity.distance, "Cosine")
            if existing_distance and existing_distance.lower() != wanted_distance.lower():
                raise ConflictError(
                    f"index {name!r} uses {existing_distance} distance but the requested identity "
                    f"declares {identity.distance}. Scores from two metrics are not comparable, "
                    f"so this must be a new index rather than a mutation.",
                    details={"index": name, "existing": existing_distance},
                )
        else:
            try:
                await client.create_collection(
                    collection_name=name,
                    vectors_config=qmodels.VectorParams(
                        size=identity.dimension,
                        distance=getattr(
                            qmodels.Distance, _DISTANCES.get(identity.distance, "Cosine").upper()
                        ),
                    ),
                )
            except Exception as exc:
                # A concurrent creator is not an error: two workers starting together both find the
                # collection absent and both try. Re-check rather than failing the caller.
                if await self.index_exists(identity):
                    logger.info("index %s was created concurrently", name)
                else:
                    raise _unavailable(
                        f"could not create index {name!r}: {exc}", index=name
                    ) from exc

        # Payload indexes for the fields queries filter on. Without them Qdrant refuses the query
        # outright (400 "Index required but not found") rather than scanning unindexed -- ensured
        # on every call, not only on first creation, so a collection that predates a newly-filtered
        # field (e.g. "kind", added after this collection already existed in production) self-heals
        # instead of every matching search failing forever. _ensure_index() memoizes this per
        # process, so it still runs at most once per worker's lifetime, not once per search.
        for field in ("owner_id", "document_id", "kind"):
            try:
                await client.create_payload_index(
                    collection_name=name,
                    field_name=field,
                    field_schema=qmodels.PayloadSchemaType.KEYWORD,
                )
            except Exception as exc:  # pragma: no cover - already-exists is the common case
                logger.debug("payload index %s on %s: %s", field, name, exc)

    @staticmethod
    def _describe_vectors(info: Any) -> tuple[int | None, str | None]:
        """Pull (size, distance) out of a collection description.

        Qdrant reports either a single unnamed vector config or a mapping of named ones, and the
        shape has changed across client versions, so this tolerates both rather than assuming.
        """
        try:
            vectors = info.config.params.vectors
        except AttributeError:
            return None, None
        size = getattr(vectors, "size", None)
        distance = getattr(vectors, "distance", None)
        if size is None and isinstance(vectors, Mapping):
            for config in vectors.values():
                size = getattr(config, "size", None)
                distance = getattr(config, "distance", None)
                break
        return size, str(distance) if distance is not None else None

    async def index_exists(self, identity: IndexIdentity) -> bool:
        try:
            return bool(await self._get_client().collection_exists(identity.physical_name))
        except Exception as exc:
            raise _unavailable(
                f"could not check index {identity.physical_name!r}: {exc}"
            ) from exc

    async def drop_index(self, identity: IndexIdentity) -> None:
        """Destroy a collection, unless an alias still points at it.

        The alias check is the guard between retiring a superseded generation and deleting the one
        currently serving traffic. Without it, pruning old indexes could take production with it.
        """
        client = self._get_client()
        name = identity.physical_name
        for alias, target in (await self._alias_map()).items():
            if target == name:
                raise ConflictError(
                    f"refusing to drop index {name!r}: alias {alias!r} still points at it. "
                    f"Repoint the alias first.",
                    details={"index": name, "alias": alias},
                )
        try:
            await client.delete_collection(name)
        except Exception as exc:
            raise _unavailable(f"could not drop index {name!r}: {exc}") from exc

    # -- aliases ----------------------------------------------------------------------

    async def _alias_map(self) -> dict[str, str]:
        """alias -> collection name, for every alias Qdrant knows about."""
        try:
            response = await self._get_client().get_aliases()
        except Exception as exc:
            raise _unavailable(f"could not list aliases: {exc}") from exc
        return {
            description.alias_name: description.collection_name
            for description in getattr(response, "aliases", []) or []
        }

    async def set_alias(self, alias: str, identity: IndexIdentity) -> None:
        """Atomically point ``alias`` at ``identity``.

        One ``update_collection_aliases`` call carrying both the delete and the create, so there is
        no instant where the alias resolves to nothing. Two separate calls would give readers a
        window in which retrieval returned an empty corpus -- which the pipeline would faithfully
        report as "searched, found nothing", indistinguishable from a genuine empty result.

        This is both promotion and rollback (ADR-007): the same operation in either direction, O(1),
        no data movement.
        """
        from qdrant_client import models as qmodels

        target = identity.physical_name
        if not await self.index_exists(identity):
            raise ConflictError(
                f"refusing to point alias {alias!r} at {target!r}: that index does not exist. "
                f"Promoting a missing index would make every search return nothing.",
                details={"alias": alias, "index": target},
            )

        actions: list[Any] = []
        if alias in await self._alias_map():
            actions.append(
                qmodels.DeleteAliasOperation(delete_alias=qmodels.DeleteAlias(alias_name=alias))
            )
        actions.append(
            qmodels.CreateAliasOperation(
                create_alias=qmodels.CreateAlias(collection_name=target, alias_name=alias)
            )
        )
        try:
            await self._get_client().update_collection_aliases(change_aliases_operations=actions)
        except Exception as exc:
            raise _unavailable(
                f"could not point alias {alias!r} at {target!r}: {exc}",
                alias=alias, index=target,
            ) from exc

    async def resolve_alias(self, alias: str) -> IndexIdentity | None:
        """Reverse ``physical_name`` back into an identity.

        Qdrant stores only the collection name, so the identity is reconstructed by parsing it.
        That works because ``physical_name`` is a total encoding of the identity's fields -- if it
        ever stops being one, this must start reading a stored manifest instead.
        """
        target = (await self._alias_map()).get(alias)
        if target is None:
            return None
        identity = _parse_physical_name(target)
        if identity is None:
            logger.warning(
                "alias %s points at %s, which is not a recognisable index name", alias, target
            )
        return identity

    # -- writes -----------------------------------------------------------------------

    async def upsert(self, identity: IndexIdentity, points: Sequence[VectorPoint]) -> int:
        if not points:
            return 0
        from qdrant_client import models as qmodels

        for point in points:
            if len(point.vector) != identity.dimension:
                raise ConflictError(
                    f"point {point.id!r} has {len(point.vector)} dimensions but index "
                    f"{identity.physical_name!r} expects {identity.dimension}",
                    details={"point": point.id, "expected": identity.dimension},
                )

        try:
            await self._get_client().upsert(
                collection_name=identity.physical_name,
                points=[
                    qmodels.PointStruct(
                        id=_point_id(point.id),
                        vector=list(point.vector),
                        # The original string id is preserved in the payload: Qdrant requires an
                        # int or UUID id, so `_point_id` hashes anything else, and search results
                        # must return the id the caller gave us, not the hash.
                        payload={**point.payload, "_point_key": point.id},
                    )
                    for point in points
                ],
                wait=True,
            )
        except Exception as exc:
            raise _unavailable(
                f"could not upsert into {identity.physical_name!r}: {exc}",
                index=identity.physical_name, points=len(points),
            ) from exc
        return len(points)

    async def delete(self, identity: IndexIdentity, *, point_ids: Sequence[str]) -> int:
        if not point_ids:
            return 0
        from qdrant_client import models as qmodels

        try:
            await self._get_client().delete(
                collection_name=identity.physical_name,
                points_selector=qmodels.PointIdsList(
                    points=[_point_id(pid) for pid in point_ids]
                ),
                wait=True,
            )
        except Exception as exc:
            raise _unavailable(
                f"could not delete from {identity.physical_name!r}: {exc}"
            ) from exc
        # Qdrant does not report how many of the named points existed. Returning the requested
        # count would overstate deletions; the callers that care (forget_source) treat this as
        # "the delete was accepted".
        return len(point_ids)

    async def delete_by_filter(
        self, identity: IndexIdentity, *, filters: Mapping[str, Any], owner_id: UserId | None
    ) -> int:
        from qdrant_client import models as qmodels

        conditions = _filter_conditions(filters)
        if owner_id is not None:
            conditions.append(
                qmodels.FieldCondition(
                    key="owner_id", match=qmodels.MatchValue(value=owner_id.value)
                )
            )
        if not conditions:
            # An unfiltered delete_by_filter would empty the collection. The in-memory adapter has
            # the same shape but is not reachable from a request; refusing here keeps a caller bug
            # from becoming data loss.
            raise ConflictError(
                "delete_by_filter requires at least one condition; an unconditional call would "
                "empty the index",
                details={"index": identity.physical_name},
            )

        query = qmodels.Filter(must=conditions)
        try:
            before = await self._count_with_filter(identity, query)
            await self._get_client().delete(
                collection_name=identity.physical_name,
                points_selector=qmodels.FilterSelector(filter=query),
                wait=True,
            )
        except Exception as exc:
            raise _unavailable(
                f"could not delete by filter from {identity.physical_name!r}: {exc}"
            ) from exc
        return before

    # -- reads ------------------------------------------------------------------------

    def _owner_filter(self, owner_id: UserId | None, include_global: bool) -> list[Any]:
        """The tenant-isolation clause, matching InMemoryVectorStore exactly.

        ``owner_id=None`` means *global content only*, not "everything". There is deliberately no
        way to express an unfiltered cross-tenant search through this port.
        """
        from qdrant_client import models as qmodels

        global_match = qmodels.FieldCondition(
            key="owner_id", match=qmodels.MatchAny(any=[v for v in _GLOBAL_OWNERS if v is not None])
        )
        # A missing owner_id key also counts as global, which MatchAny cannot express, so pair it
        # with an is-empty check.
        global_missing = qmodels.IsEmptyCondition(is_empty=qmodels.PayloadField(key="owner_id"))

        if owner_id is None:
            return [qmodels.Filter(should=[global_match, global_missing])]

        owned = qmodels.FieldCondition(
            key="owner_id", match=qmodels.MatchValue(value=owner_id.value)
        )
        if include_global:
            return [qmodels.Filter(should=[owned, global_match, global_missing])]
        return [owned]

    async def search(
        self,
        identity: IndexIdentity,
        vector: Sequence[float],
        *,
        limit: int,
        owner_id: UserId | None,
        include_global: bool = True,
        filters: Mapping[str, Any] | None = None,
        score_threshold: float | None = None,
    ) -> Sequence[tuple[str, float, Mapping[str, Any]]]:
        from qdrant_client import models as qmodels

        if len(vector) != identity.dimension:
            raise ConflictError(
                f"query vector has {len(vector)} dimensions but index "
                f"{identity.physical_name!r} expects {identity.dimension}",
                details={"expected": identity.dimension, "got": len(vector)},
            )

        conditions = _filter_conditions(filters) + self._owner_filter(owner_id, include_global)
        query_filter = qmodels.Filter(must=conditions) if conditions else None

        # One retry after a short delay. Every reproduction attempt against the live deployment
        # (idle-reuse, concurrent tasks, concurrent processes, matched event loop) came back clean,
        # which points at a narrow, one-off timing window rather than a persistent fault -- so a
        # transient hiccup here is worth absorbing rather than degrading the whole turn on it.
        last_exc: Exception | None = None
        for attempt in range(2):
            if attempt:
                await asyncio.sleep(0.5)
            try:
                response = await self._get_client().query_points(
                    collection_name=identity.physical_name,
                    query=list(vector),
                    limit=limit,
                    query_filter=query_filter,
                    score_threshold=score_threshold,
                    with_payload=True,
                )
                break
            except Exception as exc:
                last_exc = exc
        else:
            raise _unavailable(
                f"could not search {identity.physical_name!r}: {last_exc}",
                index=identity.physical_name,
            ) from last_exc

        results = []
        for scored in getattr(response, "points", response) or []:
            payload = dict(scored.payload or {})
            # Return the caller's original id, not Qdrant's numeric one.
            key = payload.pop("_point_key", None) or str(scored.id)
            results.append((key, float(scored.score), payload))
        return results

    async def _count_with_filter(self, identity: IndexIdentity, query: Any) -> int:
        response = await self._get_client().count(
            collection_name=identity.physical_name, count_filter=query, exact=True
        )
        return int(getattr(response, "count", 0))

    async def count(self, identity: IndexIdentity, *, owner_id: UserId | None = None) -> int:
        from qdrant_client import models as qmodels

        try:
            if owner_id is None:
                response = await self._get_client().count(
                    collection_name=identity.physical_name, exact=True
                )
                return int(getattr(response, "count", 0))
            query = qmodels.Filter(
                must=[
                    qmodels.FieldCondition(
                        key="owner_id", match=qmodels.MatchValue(value=owner_id.value)
                    )
                ]
            )
            return await self._count_with_filter(identity, query)
        except Exception as exc:
            raise _unavailable(
                f"could not count {identity.physical_name!r}: {exc}"
            ) from exc

    # -- lifecycle --------------------------------------------------------------------

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.close()
            except Exception as exc:  # pragma: no cover - shutdown path
                logger.debug("qdrant close: %s", exc)
            finally:
                self._client = None

    async def ping(self) -> bool:
        """For the readiness probe. False rather than raising."""
        try:
            await self._get_client().get_collections()
            return True
        except Exception as exc:
            logger.warning("qdrant ping failed: %s", type(exc).__name__)
            return False


# -- helpers ------------------------------------------------------------------------------


def _point_id(raw: str) -> Any:
    """Qdrant point ids must be an unsigned int or a UUID; ours are arbitrary strings.

    A UUID passes through unchanged so ids stay readable in the Qdrant UI. Anything else is hashed
    to a stable 64-bit integer -- stable being the requirement, since upsert must overwrite the same
    point on re-ingestion rather than accumulating duplicates. blake2b keyed on the raw id gives
    that deterministically across processes and Python versions, which `hash()` does not.
    """
    import hashlib
    import uuid as _uuid

    try:
        return str(_uuid.UUID(raw))
    except (ValueError, AttributeError, TypeError):
        digest = hashlib.blake2b(raw.encode("utf-8"), digest_size=8).digest()
        return int.from_bytes(digest, "big")


def _filter_conditions(filters: Mapping[str, Any] | None) -> list[Any]:
    """Translate the port's plain-mapping filters into Qdrant conditions.

    A list/tuple/set value means "any of", matching InMemoryVectorStore._matches.
    """
    if not filters:
        return []
    from qdrant_client import models as qmodels

    conditions: list[Any] = []
    for key, wanted in filters.items():
        if isinstance(wanted, (list, tuple, set)):
            conditions.append(
                qmodels.FieldCondition(key=key, match=qmodels.MatchAny(any=list(wanted)))
            )
        else:
            conditions.append(
                qmodels.FieldCondition(key=key, match=qmodels.MatchValue(value=wanted))
            )
    return conditions


def _parse_physical_name(name: str) -> IndexIdentity | None:
    """Inverse of ``IndexIdentity.physical_name``.

    Format: ``{logical}__v{schema}__{model}__{dimension}``. Split from the right, because a logical
    name may itself contain the separator while the three trailing fields may not.
    """
    parts = name.rsplit("__", 3)
    if len(parts) != 4:
        return None
    logical, schema_part, model, dimension_part = parts
    if not schema_part.startswith("v"):
        return None
    try:
        schema_version = int(schema_part[1:])
        dimension = int(dimension_part)
    except ValueError:
        return None
    try:
        return IndexIdentity(
            logical_name=logical,
            schema_version=schema_version,
            embedding_model_id=model,
            dimension=dimension,
        )
    except ValueError:
        # physical_name slugs its inputs, so a round trip cannot recover the original casing or
        # punctuation. That is fine for addressing -- the slug is what names the collection -- but
        # an out-of-range value here means the name was not ours.
        return None
