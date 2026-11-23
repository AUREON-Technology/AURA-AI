"""``/api/v3/chats`` without a database: the parts that must hold whatever Postgres says.

The list, the filters, the keyset paging, the ownership and the sync visibility are
``tests/load/test_chats_postgres.py``'s (they need a real FULL JOIN and the real sync routes).
What is pinned here runs in the default suite: the paged pull's table order (a protocol
property), the cursor's refusal of a token made for another list, the chat key's charset, and
that no route answers an anonymous caller.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import api.chats as chats
from api.v2.sync import _LEGACY_PULL_TABLES, _PULL_TABLES
from infrastructure.db.models import SYNCABLE_MODELS


class TestPullOrderIsStable:
    def test_the_tables_that_existed_keep_their_positions_and_new_ones_come_after(self) -> None:
        """A page token records an index into this order and is read by whichever gateway instance
        serves the next page, mid-deploy. Sorting the whole set slid every index when
        `conversations` (which sorts before `finance_records`) was added."""
        legacy = ("calendar_events", "finance_records", "goals", "health_records", "memories",
                  "messages", "projects", "tasks")
        assert _PULL_TABLES[: len(legacy)] == legacy, "an existing table moved: in-flight page tokens would resume wrongly"
        assert _PULL_TABLES[len(legacy):] == ("conversations",)
        assert sorted(_PULL_TABLES) == sorted(SYNCABLE_MODELS), "a syncable table is missing from the pull order"
        assert set(_LEGACY_PULL_TABLES) <= set(SYNCABLE_MODELS)


class TestCursor:
    PARAMS = {"sort": "last_activity", "order": "desc", "archived": "false", "pinned": None,
              "pinned_first": False, "since": None, "until": None}
    POSITION = {"q": PARAMS, "p": False, "v": "2026-11-22T12:00:00+00:00", "k": "chat-1"}

    def test_a_cursor_round_trips_for_the_same_list(self) -> None:
        token = chats._encode_cursor(self.POSITION)
        assert chats._decode_cursor(token, self.PARAMS) == self.POSITION

    @pytest.mark.parametrize("changed", [
        {"order": "asc"}, {"sort": "created_at"}, {"archived": "any"}, {"pinned": True},
        {"pinned_first": True}, {"since": "2026-01-01T00:00:00+00:00"},
    ])
    def test_a_cursor_for_another_list_is_refused(self, changed) -> None:
        token = chats._encode_cursor(self.POSITION)
        with pytest.raises(HTTPException) as refused:
            chats._decode_cursor(token, {**self.PARAMS, **changed})
        assert refused.value.status_code == 422

    @pytest.mark.parametrize("junk", ["", "not-a-cursor", "e30", "%%%", "eyJxIjoxfQ"])
    def test_junk_is_refused_not_crashed_on(self, junk) -> None:
        with pytest.raises(HTTPException) as refused:
            chats._decode_cursor(junk, self.PARAMS)
        assert refused.value.status_code == 422

    def test_a_cursor_whose_position_is_malformed_is_refused(self) -> None:
        for bad in ({**self.POSITION, "v": "yesterday"}, {**self.POSITION, "p": "no"}, {**self.POSITION, "k": 7}):
            with pytest.raises(HTTPException):
                chats._decode_cursor(chats._encode_cursor(bad), self.PARAMS)


class TestChatKey:
    @pytest.mark.parametrize("key", ["c1", "0f9c2a56-7c1e-4a8e-9b53-2d1e0f6a7b11", "a.b_c:d-e", "x" * 128])
    def test_identifier_characters_are_accepted(self, key) -> None:
        assert chats._chat_key(key) == key

    @pytest.mark.parametrize("key", ["", "has space", "slash/inside", "semi;colon", "quote'", "x" * 129, "new\nline", "ünïcode"])
    def test_anything_else_is_a_422(self, key) -> None:
        with pytest.raises(HTTPException) as refused:
            chats._chat_key(key)
        assert refused.value.status_code == 422


def test_no_route_answers_an_anonymous_caller(monkeypatch) -> None:
    monkeypatch.setattr(chats, "is_configured", lambda: True)
    app = FastAPI()
    app.include_router(chats.router)
    with TestClient(app) as client:
        for method, path in (
            ("GET", "/api/v3/chats"), ("POST", "/api/v3/chats"), ("GET", "/api/v3/chats/x"),
            ("PATCH", "/api/v3/chats/x"), ("DELETE", "/api/v3/chats/x"), ("POST", "/api/v3/chats/x/archive"),
            ("POST", "/api/v3/chats/x/unarchive"), ("POST", "/api/v3/chats/x/pin"), ("POST", "/api/v3/chats/x/unpin"),
            ("GET", "/api/v3/chats/x/messages"), ("GET", "/api/v3/chats/x/messages/m"),
            ("PATCH", "/api/v3/chats/x/messages/m"), ("DELETE", "/api/v3/chats/x/messages/m"),
        ):
            response = client.request(method, path, json={})
            assert response.status_code == 401, (method, path, response.status_code)


def test_the_router_exposes_exactly_the_agreed_surface() -> None:
    surface = sorted((sorted(r.methods)[0], r.path) for r in chats.router.routes)
    assert surface == sorted([
        ("GET", "/api/v3/chats"), ("POST", "/api/v3/chats"), ("GET", "/api/v3/chats/{chat_id}"),
        ("PATCH", "/api/v3/chats/{chat_id}"), ("DELETE", "/api/v3/chats/{chat_id}"),
        ("POST", "/api/v3/chats/{chat_id}/archive"), ("POST", "/api/v3/chats/{chat_id}/unarchive"),
        ("POST", "/api/v3/chats/{chat_id}/pin"), ("POST", "/api/v3/chats/{chat_id}/unpin"),
        ("GET", "/api/v3/chats/{chat_id}/messages"), ("GET", "/api/v3/chats/{chat_id}/messages/{message_id}"),
        ("PATCH", "/api/v3/chats/{chat_id}/messages/{message_id}"), ("DELETE", "/api/v3/chats/{chat_id}/messages/{message_id}"),
    ])
