"""``/api/v3/chats/{id}/messages`` against a real Postgres, over HTTP, beside the real ``/api/v3/sync``.

Opt-in (``-m load``); skips unless ``AURA_TEST_DATABASE_URL`` is set. The routes list, read, edit and
delete a chat's messages as the ciphertext they are. What only a real database can show: the keyset
paging over ``(created_at, id)`` when many rows share an instant, the row lock an edit and a delete
take, the tombstone the delete leaves (and that its ciphertext is cleared, audit F-07), and that an
edit and a delete reach the other devices through the real sync pull. Two signed-in people; every
assertion reads the response *and* the database.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import api.chats as chats
import api.v2.sync as sync
from infrastructure.db.base import get_db
from infrastructure.db.models import Message, User
from services.jwt_auth import verify_access_token
from tests.postgres_migrations import migrated_database

pytestmark = pytest.mark.load
_URL = os.environ.get("AURA_TEST_DATABASE_URL", "")
NOW = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
ALICE, BOB = {"Authorization": "Bearer alice"}, {"Authorization": "Bearer bob"}
BASE = "/api/v3/chats"


@pytest.fixture
async def database_url():
    if not _URL:
        pytest.skip("AURA_TEST_DATABASE_URL is not set")
    async with migrated_database(_URL, "messages") as url:
        yield url


@pytest.fixture
async def env(database_url, monkeypatch):
    engine = create_async_engine(database_url.replace("postgresql://", "postgresql+asyncpg://", 1))
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def db():
        async with sessions() as session:
            yield session

    async def person(request: Request) -> dict:
        name = request.headers.get("authorization", "").removeprefix("Bearer ")
        if name not in ("alice", "bob"):
            raise HTTPException(status_code=401, detail="unknown principal")
        return {"uid": name, "email": f"{name}@example.com"}

    monkeypatch.setattr(chats, "is_configured", lambda: True)
    monkeypatch.setattr(sync, "is_configured", lambda: True)
    app = FastAPI()
    app.include_router(chats.router)
    app.include_router(sync.router)
    app.dependency_overrides[get_db] = db
    app.dependency_overrides[verify_access_token] = person
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client, sessions
    await engine.dispose()


async def _seed(sessions, subject: str, chat: str, rows: list[tuple[str, str, datetime]]) -> list[str]:
    """``(role, text, created_at)`` rows in ``chat`` for ``subject``; returns their ids in order."""
    ids = []
    async with sessions() as db:
        user = (await db.execute(select(User).where(User.auth_subject == subject))).scalar_one_or_none()
        if user is None:
            user = User(auth_subject=subject, email=f"{subject}@example.com")
            db.add(user)
            await db.flush()
        for role, text, at in rows:
            mid = uuid.uuid4()
            db.add(Message(id=mid, user_id=user.id, conversation_id=chat, role=role, content_enc=f"gcm2:k:{text}",
                           citations_enc="gcm2:k:cites", tool_calls_enc=None, model_used="m1", tokens_used=7,
                           persona="orchestrator", created_at=at))
            ids.append(str(mid))
        await db.commit()
    return ids


def _texts(response) -> list[str]:
    assert response.status_code == 200, response.text
    return [m["content_enc"].removeprefix("gcm2:k:") for m in response.json()["messages"]]


async def _row(sessions, mid: str) -> Message:
    async with sessions() as db:
        return (await db.execute(select(Message).where(Message.id == uuid.UUID(mid)))).scalar_one()


# --- list ---

async def test_messages_are_listed_oldest_first_as_ciphertext_with_their_metadata(env) -> None:
    client, sessions = env
    await _seed(sessions, "alice", "c1", [("user", "one", NOW), ("assistant", "two", NOW + timedelta(minutes=1)),
                                          ("user", "three", NOW + timedelta(minutes=2))])
    await _seed(sessions, "alice", "other-chat", [("user", "elsewhere", NOW)])

    listed = await client.get(f"{BASE}/c1/messages", headers=ALICE)

    assert _texts(listed) == ["one", "two", "three"]
    first = listed.json()["messages"][0]
    assert first["role"] == "user" and first["chat_id"] == "c1" and first["citations_enc"] == "gcm2:k:cites"
    assert (first["model_used"], first["tokens_used"], first["persona"]) == ("m1", 7, "orchestrator")
    assert first["tool_calls_enc"] is None and first["created_at"] == NOW.isoformat()
    assert listed.json()["next_cursor"] is None
    assert _texts(await client.get(f"{BASE}/c1/messages?order=desc", headers=ALICE)) == ["three", "two", "one"]
    assert _texts(await client.get(f"{BASE}/c1/messages?role=assistant", headers=ALICE)) == ["two"]
    assert _texts(await client.get(f"{BASE}/c1/messages?role=tool", headers=ALICE)) == []


async def test_paging_walks_every_message_once_even_when_they_share_a_timestamp(env) -> None:
    client, sessions = env
    # Twenty-three messages at one instant: only the id can order them, so a page boundary that used
    # the timestamp alone would repeat some and skip others.
    ids = await _seed(sessions, "alice", "tied", [("user", f"m{i:02d}", NOW) for i in range(23)])
    for order in ("asc", "desc"):
        seen, cursor = [], None
        for _ in range(10):
            params = {"limit": 5, "order": order, **({"cursor": cursor} if cursor else {})}
            page = await client.get(f"{BASE}/tied/messages", headers=ALICE, params=params)
            assert page.status_code == 200, page.text
            seen += [m["id"] for m in page.json()["messages"]]
            cursor = page.json()["next_cursor"]
            if cursor is None:
                break
        assert sorted(seen) == sorted(ids) and len(seen) == len(set(seen)) == 23, f"{order}: each message exactly once"
        assert seen == sorted(seen, reverse=(order == "desc")), f"{order}: ordered by id within the instant"


async def test_a_cursor_made_for_another_list_is_refused(env) -> None:
    client, sessions = env
    await _seed(sessions, "alice", "c1", [("user", f"m{i}", NOW + timedelta(minutes=i)) for i in range(4)])
    cursor = (await client.get(f"{BASE}/c1/messages?limit=2", headers=ALICE)).json()["next_cursor"]
    assert cursor
    for params in ({"order": "desc"}, {"role": "user"}):
        refused = await client.get(f"{BASE}/c1/messages", headers=ALICE, params={"cursor": cursor, **params})
        assert refused.status_code == 422, params
    assert (await client.get(f"{BASE}/c2/messages", headers=ALICE, params={"cursor": cursor})).status_code == 404
    assert (await client.get(f"{BASE}/c1/messages", headers=ALICE, params={"cursor": "junk"})).status_code == 422


async def test_bad_parameters_are_422_and_an_unknown_chat_is_404(env) -> None:
    client, sessions = env
    await _seed(sessions, "alice", "c1", [("user", "x", NOW)])
    assert (await client.get(f"{BASE}/bad key/messages", headers=ALICE)).status_code == 422
    assert (await client.get(f"{BASE}/c1/messages?role=BAD!", headers=ALICE)).status_code == 422
    assert (await client.get(f"{BASE}/c1/messages?limit=0", headers=ALICE)).status_code == 422
    assert (await client.get(f"{BASE}/c1/messages?limit=101", headers=ALICE)).status_code == 422
    assert (await client.get(f"{BASE}/never/messages", headers=ALICE)).status_code == 404
    assert (await client.get(f"{BASE}/c1/messages/not-a-uuid", headers=ALICE)).status_code == 422


# --- one message ---

async def test_one_message_is_read_by_its_id_inside_its_own_chat(env) -> None:
    client, sessions = env
    (mid,) = await _seed(sessions, "alice", "c1", [("user", "hello", NOW)])
    (other,) = await _seed(sessions, "alice", "c2", [("user", "elsewhere", NOW)])

    got = await client.get(f"{BASE}/c1/messages/{mid}", headers=ALICE)

    assert got.status_code == 200 and got.json()["content_enc"] == "gcm2:k:hello" and got.json()["id"] == mid
    assert (await client.get(f"{BASE}/c2/messages/{mid}", headers=ALICE)).status_code == 404, "right id, wrong chat"
    assert (await client.get(f"{BASE}/c1/messages/{other}", headers=ALICE)).status_code == 404
    assert (await client.get(f"{BASE}/c1/messages/{uuid.uuid4()}", headers=ALICE)).status_code == 404


# --- edit ---

async def test_an_edit_replaces_the_ciphertext_and_reaches_the_other_devices(env) -> None:
    client, sessions = env
    (mid,) = await _seed(sessions, "alice", "c1", [("assistant", "draft", NOW)])
    before = await _row(sessions, mid)

    edited = await client.patch(f"{BASE}/c1/messages/{mid}", headers=ALICE,
                                json={"content_enc": "gcm2:k:final", "citations_enc": None})

    assert edited.status_code == 200, edited.text
    body = edited.json()
    assert body["content_enc"] == "gcm2:k:final" and body["citations_enc"] is None and body["role"] == "assistant"
    assert body["client_updated_at"] and datetime.fromisoformat(body["client_updated_at"]) > NOW
    after = await _row(sessions, mid)
    assert (after.content_enc, after.citations_enc, after.role) == ("gcm2:k:final", None, "assistant")
    assert (after.model_used, after.tokens_used, after.persona) == ("m1", 7, "orchestrator"), "only what was sent changed"
    assert after.sync_txid > before.sync_txid, "the row was re-stamped for the sync cursor"
    pulled = (await client.get("/api/v3/sync", headers=ALICE)).json()["data"]["messages"]
    assert [m["content_enc"] for m in pulled if m["id"] == mid] == ["gcm2:k:final"], "another device pulls the edit"


async def test_an_edit_touches_only_the_fields_sent(env) -> None:
    client, sessions = env
    (mid,) = await _seed(sessions, "alice", "c1", [("user", "keep", NOW)])
    edited = await client.patch(f"{BASE}/c1/messages/{mid}", headers=ALICE, json={"tool_calls_enc": "gcm:legacy:calls"})
    assert edited.status_code == 200, edited.text
    row = await _row(sessions, mid)
    assert (row.content_enc, row.citations_enc, row.tool_calls_enc) == ("gcm2:k:keep", "gcm2:k:cites", "gcm:legacy:calls")


async def test_an_edit_that_is_not_ciphertext_or_not_an_edit_is_refused_and_changes_nothing(env) -> None:
    client, sessions = env
    (mid,) = await _seed(sessions, "alice", "c1", [("user", "safe", NOW)])
    for body in ({}, {"content_enc": None}, {"content_enc": "plain text"}, {"citations_enc": "plain"},
                 {"content_enc": "gcm3:k:x"}, {"role": "assistant"}, {"content_enc": "gcm2:" + "x" * 512_000}):
        refused = await client.patch(f"{BASE}/c1/messages/{mid}", headers=ALICE, json=body)
        assert refused.status_code == 422, body
    row = await _row(sessions, mid)
    assert (row.content_enc, row.role, row.client_updated_at) == ("gcm2:k:safe", "user", None)


async def test_a_deleted_message_is_not_edited_back_to_life(env) -> None:
    client, sessions = env
    (mid,) = await _seed(sessions, "alice", "c1", [("user", "gone", NOW)])
    assert (await client.delete(f"{BASE}/c1/messages/{mid}", headers=ALICE)).status_code == 200

    revived = await client.patch(f"{BASE}/c1/messages/{mid}", headers=ALICE, json={"content_enc": "gcm2:k:back"})

    assert revived.status_code == 404
    row = await _row(sessions, mid)
    assert row.deleted_at is not None and row.content_enc == ""


# --- delete ---

async def test_delete_leaves_a_tombstone_clears_the_ciphertext_and_is_idempotent(env) -> None:
    client, sessions = env
    mids = await _seed(sessions, "alice", "c1", [("user", "a", NOW), ("assistant", "b", NOW + timedelta(minutes=1))])

    deleted = await client.delete(f"{BASE}/c1/messages/{mids[0]}", headers=ALICE)

    assert deleted.status_code == 200 and deleted.json() == {"deleted": True}
    row = await _row(sessions, mids[0])
    assert row.deleted_at is not None and row.client_updated_at is not None, "the tombstone stays so devices learn"
    assert (row.content_enc, row.citations_enc, row.tool_calls_enc) == ("", None, None), "ciphertext cleared (F-07)"
    kept = await _row(sessions, mids[1])
    assert kept.deleted_at is None and kept.content_enc == "gcm2:k:b"
    assert _texts(await client.get(f"{BASE}/c1/messages", headers=ALICE)) == ["b"]
    assert (await client.get(f"{BASE}/c1/messages/{mids[0]}", headers=ALICE)).status_code == 404
    assert (await client.get(f"{BASE}/c1", headers=ALICE)).json()["message_count"] == 1
    pulled = (await client.get("/api/v3/sync", headers=ALICE)).json()["data"]["messages"]
    assert [m["deleted_at"] is not None for m in pulled if m["id"] == mids[0]] == [True], "another device pulls the delete"

    again = await client.delete(f"{BASE}/c1/messages/{mids[0]}", headers=ALICE)
    assert again.status_code == 200 and again.json() == {"deleted": True}, "a retried delete is not an error"
    assert (await client.delete(f"{BASE}/c1/messages/{uuid.uuid4()}", headers=ALICE)).status_code == 404
    assert (await client.delete(f"{BASE}/c2/messages/{mids[1]}", headers=ALICE)).status_code == 404, "wrong chat"


# --- ownership ---

async def test_bob_cannot_list_read_edit_or_delete_alices_messages(env) -> None:
    client, sessions = env
    (mid,) = await _seed(sessions, "alice", "shared-guessable-key", [("user", "private", NOW)])
    await _seed(sessions, "bob", "bobs-own", [("user", "mine", NOW)])

    missing_chat = await client.get(f"{BASE}/never-existed/messages", headers=BOB)
    missing_msg = await client.get(f"{BASE}/bobs-own/messages/{uuid.uuid4()}", headers=BOB)
    attempts = (
        ("GET", f"{BASE}/shared-guessable-key/messages", None, missing_chat),
        ("GET", f"{BASE}/shared-guessable-key/messages/{mid}", None, missing_msg),
        ("PATCH", f"{BASE}/shared-guessable-key/messages/{mid}", {"content_enc": "gcm2:k:evil"}, missing_msg),
        ("DELETE", f"{BASE}/shared-guessable-key/messages/{mid}", None, missing_msg),
    )
    for method, path, body, baseline in attempts:
        theirs = await client.request(method, path, headers=BOB, json=body)
        assert theirs.status_code == baseline.status_code == 404, f"Bob reached Alice's message via {method} {path}"
        assert theirs.json() == baseline.json(), "the answer must not tell a stranger's message from a missing one"
        assert "private" not in theirs.text

    row = await _row(sessions, mid)
    assert row.content_enc == "gcm2:k:private" and row.deleted_at is None and row.client_updated_at is None
    assert _texts(await client.get(f"{BASE}/shared-guessable-key/messages", headers=ALICE)) == ["private"]
    assert _texts(await client.get(f"{BASE}/bobs-own/messages", headers=BOB)) == ["mine"]


async def test_two_people_with_the_same_chat_key_each_see_only_their_own_messages(env) -> None:
    """A chat key is minted by the client, so two people can hold the same one. The chat existing for
    Bob must not let his list reach the messages Alice put under that key."""
    client, sessions = env
    await _seed(sessions, "alice", "same-key", [("user", "alice-1", NOW), ("assistant", "alice-2", NOW + timedelta(minutes=1))])
    (bobs,) = await _seed(sessions, "bob", "same-key", [("user", "bob-1", NOW + timedelta(minutes=2))])

    assert _texts(await client.get(f"{BASE}/same-key/messages", headers=ALICE)) == ["alice-1", "alice-2"]
    assert _texts(await client.get(f"{BASE}/same-key/messages", headers=BOB)) == ["bob-1"]
    assert (await client.get(f"{BASE}/same-key/messages/{bobs}", headers=ALICE)).status_code == 404
