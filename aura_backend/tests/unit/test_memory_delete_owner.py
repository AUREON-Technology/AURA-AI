"""v1 memory delete is owner-scoped (AURA-001).

``list_all_memories`` already filtered by ``user_id``. Delete did not, so any
authenticated caller who knew a point id could remove another user's memory.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from qdrant_client.http import models

from services.memory import MemoryService


class _FakeQdrant:
    def __init__(self, *, hits=()) -> None:
        self.hits = list(hits)
        self.scroll_filters: list = []
        self.delete_selectors: list = []

    def scroll(self, collection_name, scroll_filter, limit, with_payload, with_vectors):
        self.scroll_filters.append(scroll_filter)
        return self.hits, None

    def delete(self, collection_name, points_selector):
        self.delete_selectors.append(points_selector)


def _must_keys(filt) -> set[str]:
    keys: set[str] = set()
    for cond in filt.must:
        key = getattr(cond, "key", None)
        if key:
            keys.add(key)
        if getattr(cond, "has_id", None) is not None:
            keys.add("has_id")
    return keys


def test_delete_sends_an_owner_and_type_filter(monkeypatch) -> None:
    """Qdrant must see user_id (and memory type) the same way list does."""
    client = _FakeQdrant(hits=[MagicMock()])
    monkeypatch.setattr("services.memory.rag_service.client", client)

    assert MemoryService().delete_memory(99, user_id="alice") is True
    assert _must_keys(client.scroll_filters[0]) >= {"user_id", "type", "has_id"}
    selector = client.delete_selectors[0]
    assert isinstance(selector, models.FilterSelector)
    assert _must_keys(selector.filter) >= {"user_id", "type", "has_id"}


def test_delete_of_another_users_point_is_false(monkeypatch) -> None:
    """A memory id in a URL is not a capability. Same 404 as missing."""
    client = _FakeQdrant(hits=[])
    monkeypatch.setattr("services.memory.rag_service.client", client)

    assert MemoryService().delete_memory(99, user_id="mallory") is False
    assert client.delete_selectors == []


def test_delete_of_an_unknown_point_is_false(monkeypatch) -> None:
    client = _FakeQdrant(hits=[])
    monkeypatch.setattr("services.memory.rag_service.client", client)
    assert MemoryService().delete_memory(1, user_id="alice") is False
    assert client.delete_selectors == []
