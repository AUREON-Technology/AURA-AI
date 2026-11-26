"""``/api/v3/chats/{id}/messages``: the rules that hold before any database is asked.

The listing, the paging, the edit, the tombstone and the ownership are
``tests/load/test_messages_postgres.py``'s (they need a real database and the real sync). Here, in the
default suite: what is refused up front (a malformed chat key, message id, role or cursor; an edit that
is not ciphertext, not an edit, or tries to clear the text; fields the route does not accept) and that
the refusal happens *before* the database is touched, so nothing can have been written.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.chats as chats
from infrastructure.db.base import get_db
from services.jwt_auth import verify_access_token

MID = "0f9c2a56-7c1e-4a8e-9b53-2d1e0f6a7b11"
BASE = "/api/v3/chats/c1/messages"


class _NoDatabase:
    """A session that fails the test if anything is asked of it."""

    def __getattr__(self, name):  # pragma: no cover - reaching it is the failure
        raise AssertionError(f"the database was touched ({name}) by a request that must be refused first")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(chats, "is_configured", lambda: True)
    app = FastAPI()
    app.include_router(chats.router)

    async def no_db():
        yield _NoDatabase()

    async def person():
        return {"uid": "alice", "email": "alice@example.com"}

    async def untouched(db, info):  # pragma: no cover
        raise AssertionError("a user row was looked up for a request that must be refused first")

    monkeypatch.setattr(chats, "get_or_create_user", untouched)
    app.dependency_overrides[get_db] = no_db
    app.dependency_overrides[verify_access_token] = person
    with TestClient(app) as test_client:
        yield test_client


class TestMessageKey:
    def test_a_uuid_is_accepted(self) -> None:
        assert str(chats._message_key(MID)) == MID

    @pytest.mark.parametrize("bad", ["", "x", "1234", MID + "0", "../etc", "0f9c2a56-7c1e-4a8e-9b53-2d1e0f6a7b1g"])
    def test_anything_else_is_a_422(self, bad) -> None:
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as refused:
            chats._message_key(bad)
        assert refused.value.status_code == 422


class TestRefusedBeforeTheDatabase:
    @pytest.mark.parametrize("method", ["GET", "PATCH", "DELETE"])
    def test_a_message_id_that_is_not_a_uuid(self, client, method) -> None:
        kwargs = {"json": {"content_enc": "gcm2:k:x"}} if method == "PATCH" else {}
        assert client.request(method, f"{BASE}/not-a-uuid", **kwargs).status_code == 422

    @pytest.mark.parametrize("path", ["/api/v3/chats/has space/messages", "/api/v3/chats/semi;colon/messages/" + MID])
    def test_a_chat_key_that_is_not_an_identifier(self, client, path) -> None:
        assert client.get(path).status_code == 422

    @pytest.mark.parametrize("role", ["USER", "has space", "x" * 17, "a-b", "1"])
    def test_a_role_that_is_not_a_role(self, client, role) -> None:
        assert client.get(BASE, params={"role": role}).status_code == 422

    @pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"order": "sideways"}])
    def test_paging_parameters_out_of_range(self, client, params) -> None:
        assert client.get(BASE, params=params).status_code == 422

    @pytest.mark.parametrize("body", [
        {},
        {"content_enc": None},
        {"content_enc": "plain text"},
        {"content_enc": ""},
        {"citations_enc": "plain"},
        {"tool_calls_enc": "Gcm2:k:x"},
        {"content_enc": "gcm3:k:x"},
        {"content_enc": "gcm2:k:ok", "citations_enc": "plain"},
        {"role": "assistant"},
        {"content_enc": "gcm2:k:ok", "extra": 1},
        {"content_enc": "gcm2:" + "x" * chats.MAX_MESSAGE_ENC_CHARS},
    ])
    def test_an_edit_that_is_not_a_sealed_edit(self, client, body) -> None:
        refused = client.patch(f"{BASE}/{MID}", json=body)
        assert refused.status_code == 422, body

    def test_the_refusal_says_which_field_and_why(self, client) -> None:
        body = client.patch(f"{BASE}/{MID}", json={"content_enc": "plain"}).json()
        assert body["detail"]["field"] == "content_enc" and "plaintext" in body["detail"]["detail"]
        assert client.patch(f"{BASE}/{MID}", json={}).json()["detail"]["error"] == "empty_update"
        assert "cannot be cleared" in client.patch(f"{BASE}/{MID}", json={"content_enc": None}).json()["detail"]["detail"]


class TestEnvelopes:
    def test_the_two_shapes_the_client_seals_with(self) -> None:
        assert chats._ENVELOPES == ("gcm2:", "gcm:")
        assert "gcm2:k:i:body".startswith(chats._ENVELOPES) and "gcm:iv:body".startswith(chats._ENVELOPES)
        assert not "plain".startswith(chats._ENVELOPES)
