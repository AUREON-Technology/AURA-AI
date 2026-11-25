"""``/api/v3/chats`` against a real Postgres, over HTTP, beside the real ``/api/v3/sync`` (migration 0035).

Opt-in (``-m load``); skips unless ``AURA_TEST_DATABASE_URL`` is set. The chat list is a FULL JOIN
of the messages' aggregate to the ``conversations`` metadata, filtered and keyset-paged over
computed columns; none of that can be shown on the in-memory substrate, and the sync's tombstone
and natural-key rules are what make a pin or a delete reach the other devices. So this drives the
real routers (``api.chats`` and ``api.v2.sync``) on a database built by ``alembic upgrade head``,
with two signed-in people, and asserts what the response *and the database* say.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import FastAPI, Request
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import api.chats as chats
import api.v2.sync as sync
from infrastructure.db.base import get_db
from infrastructure.db.models import Conversation, Message, User
from services.jwt_auth import verify_access_token
from tests.postgres_migrations import migrated_database

pytestmark = pytest.mark.load
_URL = os.environ.get("AURA_TEST_DATABASE_URL", "")
NOW = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
ALICE, BOB = {"Authorization": "Bearer alice"}, {"Authorization": "Bearer bob"}


@pytest.fixture
async def database_url():
    if not _URL:
        pytest.skip("AURA_TEST_DATABASE_URL is not set")
    async with migrated_database(_URL, "chats") as url:
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
            from fastapi import HTTPException

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


async def _seed(sessions, subject: str, key: str, *, count: int = 1, start: datetime = NOW, step_minutes: int = 1):
    """Live messages for ``subject`` in chat ``key``, ``step_minutes`` apart from ``start``."""
    async with sessions() as db:
        user = (await db.execute(select(User).where(User.auth_subject == subject))).scalar_one_or_none()
        if user is None:
            user = User(auth_subject=subject, email=f"{subject}@example.com")
            db.add(user)
            await db.flush()
        for i in range(count):
            db.add(Message(
                id=uuid.uuid4(), user_id=user.id, conversation_id=key, role="user",
                content_enc=f"ciphertext-{key}-{i}", created_at=start + timedelta(minutes=i * step_minutes),
            ))
        await db.commit()


def _ids(response) -> list[str]:
    assert response.status_code == 200, response.text
    return [c["id"] for c in response.json()["chats"]]


# --- derived list, filters, ordering ---

async def test_existing_chats_are_listed_from_their_messages_with_no_backfill(env) -> None:
    client, sessions = env
    await _seed(sessions, "alice", "chat-a", count=3, start=NOW)
    await _seed(sessions, "alice", "chat-b", count=1, start=NOW + timedelta(hours=1))

    response = await client.get("/api/v3/chats", headers=ALICE)

    assert response.status_code == 200, response.text
    chats_ = {c["id"]: c for c in response.json()["chats"]}
    assert list(chats_) == ["chat-b", "chat-a"], "newest activity first"
    assert chats_["chat-a"]["message_count"] == 3
    assert chats_["chat-a"]["created_at"] == NOW.isoformat()
    assert chats_["chat-a"]["last_activity_at"] == (NOW + timedelta(minutes=2)).isoformat()
    assert chats_["chat-a"]["title_enc"] is None and chats_["chat-a"]["pinned"] is False
    async with sessions() as db:
        assert (await db.execute(text("SELECT count(*) FROM conversations"))).scalar_one() == 0, "listing wrote rows"


async def test_title_pin_and_archive_are_set_returned_and_filtered(env) -> None:
    client, sessions = env
    for i, key in enumerate(("c1", "c2", "c3")):
        await _seed(sessions, "alice", key, start=NOW + timedelta(hours=i))

    titled = await client.patch("/api/v3/chats/c1", headers=ALICE, json={"title_enc": "gcm2:iv:body", "pinned": True})
    assert titled.status_code == 200, titled.text
    assert titled.json()["title_enc"] == "gcm2:iv:body" and titled.json()["pinned"] is True
    assert (await client.post("/api/v3/chats/c2/archive", headers=ALICE)).json()["archived"] is True

    assert _ids(await client.get("/api/v3/chats", headers=ALICE)) == ["c3", "c1"], "archived hidden by default"
    assert _ids(await client.get("/api/v3/chats?archived=true", headers=ALICE)) == ["c2"]
    assert _ids(await client.get("/api/v3/chats?archived=any", headers=ALICE)) == ["c3", "c2", "c1"]
    assert _ids(await client.get("/api/v3/chats?pinned=true", headers=ALICE)) == ["c1"]
    assert _ids(await client.get("/api/v3/chats?pinned=false&archived=any", headers=ALICE)) == ["c3", "c2"]
    assert _ids(await client.get("/api/v3/chats?pinned_first=true", headers=ALICE)) == ["c1", "c3"]
    assert _ids(await client.get("/api/v3/chats?order=asc&archived=any", headers=ALICE)) == ["c1", "c2", "c3"]
    assert _ids(await client.get("/api/v3/chats?sort=created_at&archived=any", headers=ALICE)) == ["c3", "c2", "c1"]
    since = (NOW + timedelta(hours=1)).isoformat()
    assert _ids(await client.get("/api/v3/chats", headers=ALICE, params={"archived": "any", "active_since": since})) == ["c3", "c2"]
    assert _ids(await client.get("/api/v3/chats", headers=ALICE, params={"archived": "any", "active_until": since})) == ["c1"]

    unarchived = await client.post("/api/v3/chats/c2/unarchive", headers=ALICE)
    assert unarchived.json()["archived"] is False
    assert (await client.post("/api/v3/chats/c1/unpin", headers=ALICE)).json()["pinned"] is False
    assert (await client.patch("/api/v3/chats/c1", headers=ALICE, json={"title_enc": None})).json()["title_enc"] is None


async def test_keyset_pages_cover_every_chat_once_in_every_ordering(env) -> None:
    client, sessions = env
    keys = [f"chat-{i:02d}" for i in range(11)]
    for i, key in enumerate(keys):
        # chat-05 and chat-06 share an activity instant: the key breaks the tie.
        await _seed(sessions, "alice", key, start=NOW + timedelta(minutes=6 if i == 6 else i))
    await client.patch("/api/v3/chats/chat-02", headers=ALICE, json={"pinned": True})
    await client.patch("/api/v3/chats/chat-09", headers=ALICE, json={"pinned": True})

    for query in ("", "order=asc", "sort=created_at", "pinned_first=true", "pinned_first=true&order=asc"):
        seen: list[str] = []
        cursor = None
        for _ in range(10):
            url = f"/api/v3/chats?limit=3&{query}" + (f"&cursor={cursor}" if cursor else "")
            body = (await client.get(url, headers=ALICE)).json()
            seen += [c["id"] for c in body["chats"]]
            cursor = body["next_cursor"]
            if cursor is None:
                break
        whole = _ids(await client.get(f"/api/v3/chats?limit=100&{query}", headers=ALICE))
        assert seen == whole, f"paging disagrees with the unpaged list for {query!r}"
        assert sorted(seen) == keys, f"a chat was skipped or repeated for {query!r}"

    page = (await client.get("/api/v3/chats?limit=2", headers=ALICE)).json()
    assert page["next_cursor"]
    for other in ("order=asc", "sort=created_at", "archived=any", "pinned_first=true"):
        mismatch = await client.get(f"/api/v3/chats?limit=2&{other}&cursor={page['next_cursor']}", headers=ALICE)
        assert mismatch.status_code == 422, f"a cursor for another list was accepted ({other})"
    for junk in ("not-a-cursor", "e30", "%00"):
        assert (await client.get(f"/api/v3/chats?cursor={junk}", headers=ALICE)).status_code == 422


# --- create / get / update validation ---

async def test_create_is_idempotent_and_pre_titles_a_chat_before_its_first_message(env) -> None:
    client, sessions = env
    first = await client.post("/api/v3/chats", headers=ALICE, json={"id": "fresh-chat", "title_enc": "gcm2:a:b"})
    assert first.status_code == 201 and first.json()["id"] == "fresh-chat" and first.json()["message_count"] == 0
    again = await client.post("/api/v3/chats", headers=ALICE, json={"id": "fresh-chat"})
    assert again.status_code == 200 and again.json()["title_enc"] == "gcm2:a:b", "a retry returns the chat, untouched"
    retitled = await client.post("/api/v3/chats", headers=ALICE, json={"id": "fresh-chat", "title_enc": "gcm2:c:d"})
    assert retitled.status_code == 200 and retitled.json()["title_enc"] == "gcm2:c:d"
    assert "fresh-chat" in _ids(await client.get("/api/v3/chats", headers=ALICE)), "an empty chat is listed"

    generated = await client.post("/api/v3/chats", headers=ALICE, json={})
    assert generated.status_code == 201 and uuid.UUID(generated.json()["id"])

    await _seed(sessions, "alice", "has-messages", count=2)
    over_messages = await client.post("/api/v3/chats", headers=ALICE, json={"id": "has-messages", "title_enc": "gcm2:x:y"})
    assert over_messages.status_code == 200 and over_messages.json()["message_count"] == 2
    assert over_messages.json()["title_enc"] == "gcm2:x:y", "a title sent for an existing chat is applied"


@pytest.mark.parametrize("method, path, body, status", [
    ("GET", "/api/v3/chats/has space", None, 422),
    ("GET", "/api/v3/chats/" + "x" * 129, None, 422),
    ("GET", "/api/v3/chats/nope", None, 404),
    ("PATCH", "/api/v3/chats/nope", {"pinned": True}, 404),
    ("PATCH", "/api/v3/chats/known", {}, 422),
    ("PATCH", "/api/v3/chats/known", {"message_count": 9}, 422),
    ("PATCH", "/api/v3/chats/known", {"pinned": None}, 422),
    ("PATCH", "/api/v3/chats/known", {"title_enc": "x" * 5000}, 422),
    ("POST", "/api/v3/chats", {"id": "bad id"}, 422),
    ("POST", "/api/v3/chats", {"id": "x" * 129}, 422),
    ("POST", "/api/v3/chats", {"surprise": 1}, 422),
    ("POST", "/api/v3/chats/nope/pin", None, 404),
    ("DELETE", "/api/v3/chats/nope", None, 404),
])
async def test_what_it_refuses(env, method, path, body, status) -> None:
    client, sessions = env
    await _seed(sessions, "alice", "known")
    response = await client.request(method, path, headers=ALICE, json=body)
    assert response.status_code == status, response.text


# --- delete ---

async def test_delete_tombstones_the_messages_and_the_metadata_and_is_idempotent(env) -> None:
    client, sessions = env
    await _seed(sessions, "alice", "doomed", count=3)
    await _seed(sessions, "alice", "kept", count=1)
    await client.patch("/api/v3/chats/doomed", headers=ALICE, json={"title_enc": "gcm2:t:t", "pinned": True})

    response = await client.delete("/api/v3/chats/doomed", headers=ALICE)

    assert response.status_code == 200 and response.json() == {"deleted": True, "messages_deleted": 3}
    assert _ids(await client.get("/api/v3/chats?archived=any", headers=ALICE)) == ["kept"]
    assert (await client.get("/api/v3/chats/doomed", headers=ALICE)).status_code == 404
    async with sessions() as db:
        rows = (await db.execute(select(Message).where(Message.conversation_id == "doomed"))).scalars().all()
        assert len(rows) == 3 and all(r.deleted_at is not None for r in rows), "tombstones stay so other devices learn"
        assert all(r.content_enc == "" for r in rows), "the ciphertext is cleared (audit F-07)"
        meta = (await db.execute(select(Conversation).where(Conversation.conversation_key == "doomed"))).scalar_one()
        assert meta.deleted_at is not None and meta.title_enc is None and meta.pinned is False
        kept = (await db.execute(select(Message).where(Message.conversation_id == "kept"))).scalars().all()
        assert all(r.deleted_at is None and r.content_enc.startswith("ciphertext") for r in kept)

    again = await client.delete("/api/v3/chats/doomed", headers=ALICE)
    assert again.status_code == 200 and again.json()["messages_deleted"] == 0, "a retried delete is not an error"

    pulled = (await client.get("/api/v3/sync", headers=ALICE)).json()["data"]
    tombstoned = [m for m in pulled["messages"] if m["conversation_id"] == "doomed"]
    assert len(tombstoned) == 3 and all(m["deleted_at"] for m in tombstoned), "other devices pull the delete"
    assert [c for c in pulled["conversations"] if c["conversation_key"] == "doomed"][0]["deleted_at"]

    recreated = await client.post("/api/v3/chats", headers=ALICE, json={"id": "doomed"})
    assert recreated.status_code == 201 and recreated.json()["pinned"] is False and recreated.json()["title_enc"] is None


# --- ownership ---

async def test_bob_cannot_see_change_or_delete_alices_chat_and_the_same_key_is_separate(env) -> None:
    client, sessions = env
    await _seed(sessions, "alice", "shared-guessable-key", count=2)
    await client.patch("/api/v3/chats/shared-guessable-key", headers=ALICE, json={"title_enc": "gcm2:a:a", "pinned": True})
    await _seed(sessions, "bob", "bobs-own")

    missing = await client.get("/api/v3/chats/never-existed", headers=BOB)
    for method, path, body in (
        ("GET", "/api/v3/chats/shared-guessable-key", None),
        ("PATCH", "/api/v3/chats/shared-guessable-key", {"title_enc": "gcm2:evil:evil", "pinned": False}),
        ("POST", "/api/v3/chats/shared-guessable-key/archive", None),
        ("POST", "/api/v3/chats/shared-guessable-key/unarchive", None),
        ("POST", "/api/v3/chats/shared-guessable-key/pin", None),
        ("POST", "/api/v3/chats/shared-guessable-key/unpin", None),
        ("DELETE", "/api/v3/chats/shared-guessable-key", None),
    ):
        theirs = await client.request(method, path, headers=BOB, json=body)
        assert theirs.status_code == missing.status_code == 404, f"Bob reached Alice's chat via {method} {path}"
        assert theirs.json() == missing.json(), "the answer must not tell a stranger's chat from a missing one"
    assert _ids(await client.get("/api/v3/chats", headers=BOB)) == ["bobs-own"]

    mine = (await client.get("/api/v3/chats/shared-guessable-key", headers=ALICE)).json()
    assert mine["title_enc"] == "gcm2:a:a" and mine["pinned"] is True and mine["archived"] is False
    assert mine["message_count"] == 2, "Bob's delete touched Alice's messages"

    # Bob may use the same key for his own chat; it is a different chat.
    await _seed(sessions, "bob", "shared-guessable-key")
    bobs = await client.patch("/api/v3/chats/shared-guessable-key", headers=BOB, json={"title_enc": "gcm2:b:b"})
    assert bobs.json()["message_count"] == 1 and bobs.json()["title_enc"] == "gcm2:b:b"
    assert (await client.get("/api/v3/chats/shared-guessable-key", headers=ALICE)).json()["title_enc"] == "gcm2:a:a"


async def test_every_route_needs_a_signed_in_caller(env) -> None:
    client, _ = env
    for method, path in (
        ("GET", "/api/v3/chats"), ("POST", "/api/v3/chats"), ("GET", "/api/v3/chats/x"),
        ("PATCH", "/api/v3/chats/x"), ("DELETE", "/api/v3/chats/x"), ("POST", "/api/v3/chats/x/pin"),
    ):
        assert (await client.request(method, path, json={})).status_code == 401, (method, path)


# --- the sync is the other half ---

async def test_a_pin_made_through_the_api_reaches_the_other_devices_and_one_made_there_reaches_the_api(env) -> None:
    client, sessions = env
    await _seed(sessions, "alice", "synced-chat", count=1)
    before = (await client.get("/api/v3/sync", headers=ALICE)).json()["cursor"]

    await client.patch("/api/v3/chats/synced-chat", headers=ALICE, json={"title_enc": "gcm2:s:s", "pinned": True})
    pulled = (await client.get("/api/v3/sync", headers=ALICE, params={"since": before})).json()["data"]
    row = [c for c in pulled["conversations"] if c["conversation_key"] == "synced-chat"][0]
    assert row["pinned"] is True and row["title_enc"] == "gcm2:s:s", "the API's pin reaches a device pulling"

    # A device archives the chat offline and pushes it under a uuid of its own.
    device_row = str(uuid.uuid4())
    pushed = await client.post("/api/v3/sync", headers=ALICE, json={"mutations": [{
        "table": "conversations", "op": "upsert", "id": device_row,
        "client_updated_at": (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat(),
        "payload": {"conversation_key": "synced-chat", "archived": True, "pinned": True, "title_enc": "gcm2:s:s"},
    }]})
    assert pushed.status_code == 200 and pushed.json()[0]["status"] == "applied", pushed.text
    assert pushed.json()[0].get("server_id") == str(row["id"]), (
        "two devices' rows for one chat merge onto the existing one (natural key)"
    )
    assert (await client.get("/api/v3/chats/synced-chat", headers=ALICE)).json()["archived"] is True
    async with sessions() as db:
        assert (await db.execute(text(
            "SELECT count(*) FROM conversations WHERE conversation_key = 'synced-chat'"))).scalar_one() == 1


async def test_two_devices_creating_the_same_chats_row_offline_leave_one_row(env) -> None:
    client, _ = env
    for _ in range(2):
        pushed = await client.post("/api/v3/sync", headers=ALICE, json={"mutations": [{
            "table": "conversations", "op": "upsert", "id": str(uuid.uuid4()),
            "client_updated_at": datetime.now(timezone.utc).isoformat(),
            "payload": {"conversation_key": "offline-twin", "pinned": True},
        }]})
        assert pushed.status_code == 200 and pushed.json()[0]["status"] in ("applied", "conflict"), pushed.text
    detail = await client.get("/api/v3/chats/offline-twin", headers=ALICE)
    assert detail.status_code == 200 and detail.json()["pinned"] is True and detail.json()["message_count"] == 0
