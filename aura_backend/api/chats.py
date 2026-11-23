"""The signed-in user's chats: ``/api/v3/chats`` (list, create, get, update, delete, archive, pin).

**A chat is not a row.** It is the ``conversation_id`` its ``messages`` share (the client mints
it). So the list is *derived*: one entry per ``conversation_id`` that has live messages, plus any
chat created before its first message. Every chat that already exists is in the list on day one,
with no backfill. What a person *sets* on a chat -- a title, a pin, an archive flag -- lives in
the small ``conversations`` table (migration 0034, ``SYNCABLE_MODELS``), created the first time
one of them is set. It rides the same cursor sync as everything else, so a pin made through this
API reaches the other devices on their next pull, and one made offline on a device reaches here.

**The server cannot read a title.** ``title_enc`` is ciphertext like every other synced text; it
is stored, returned and cleared, never read. Searching chat content or titles therefore stays on
the client, and sorting is by activity and creation time, never by title. ``pinned`` and
``archived`` are plaintext because the list is filtered and ordered by them.

**Ownership.** Every statement is scoped to the caller's own user row. A chat id someone else
owns finds nothing, and answers exactly what a chat that does not exist answers (404), so an id
cannot be probed. Only a user-issued access token reaches these routes (``verify_access_token``):
a paired daemon's scoped token and a service key do not.

**Messages (``/chats/{id}/messages``) are stored and returned as the ciphertext they are.** The
routes list, read, edit and delete a chat's messages; the text of a message (and its citations and
tool calls) is ``*_enc`` and the server neither reads nor searches it. An edit is the client's own
new ciphertext, accepted only in an envelope shape (``gcm2:`` or ``gcm:``) so plaintext cannot be
stored in a ciphertext column by mistake. A delete is the sync's tombstone. The server's working
transcript of the conversation (what the model is shown next turn) has no message ids and is not
edited by either; ``DELETE /api/v3/sessions/{id}`` forgets it.

**Last write wins by the server clock.** A change here stamps ``client_updated_at`` with the
server's ``now``. A device with a *newer* offline edit of the same row will win its next push;
one with an older edit will not. That is the sync's own rule, not a new one.

Lives outside ``aura_core`` for the same reason ``api/me.py`` and ``api/account.py`` do: it talks
to SQLAlchemy and the sync tables (ADR-002).
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, false, func, literal, or_, select, true, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from aura_core.domain.identifiers import SessionId, UserId
from infrastructure.db.base import get_db, is_configured
from infrastructure.db.models import Conversation, Message
from infrastructure.db.user_repo import get_or_create_user
from services.jwt_auth import verify_access_token

logger = logging.getLogger("aura.chats")

router = APIRouter(prefix="/api/v3/chats", tags=["chats"])

DEFAULT_LIMIT = 30
MAX_LIMIT = 100
#: ``messages.conversation_id`` is VARCHAR(128). The client mints it (a UUID today); the charset
#: keeps a path segment, a log line and a cursor free of anything but identifier characters.
_CHAT_KEY = re.compile(r"^[A-Za-z0-9._:\-]{1,128}$")
#: A title is ciphertext (``gcm2:<iv>:<body>``); this bounds the stored size, not the title.
MAX_TITLE_ENC_CHARS = 4096


def _require_db_configured() -> None:
    if not is_configured():
        raise HTTPException(status_code=503, detail="Postgres is not configured (DATABASE_URL unset).")


def _not_found(chat_id: str) -> HTTPException:
    return HTTPException(status_code=404, detail={"error": "not_found", "detail": "chat not found"})


def _invalid(field: str, message: str) -> HTTPException:
    return HTTPException(status_code=422, detail={"error": "invalid_field", "field": field, "detail": message})


def _chat_key(value: str) -> str:
    if not _CHAT_KEY.match(value):
        raise _invalid("chat_id", "1-128 characters from A-Z a-z 0-9 . _ : -")
    return value


# --- models ---

class ChatItem(BaseModel):
    #: The chat's key: the ``conversation_id`` its messages carry.
    id: str
    #: Ciphertext (``gcm2:...``) or ``None``. The server cannot read it.
    title_enc: str | None
    pinned: bool
    archived: bool
    message_count: int
    created_at: str
    last_activity_at: str


class ChatList(BaseModel):
    chats: list[ChatItem]
    #: Pass back as ``cursor`` for the next page; ``None`` on the last.
    next_cursor: str | None


class ChatCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: The client's own chat key. Omit to have one generated.
    id: str | None = Field(default=None, max_length=128)
    title_enc: str | None = Field(default=None, max_length=MAX_TITLE_ENC_CHARS)


class ChatUpdate(BaseModel):
    """Only what a person sets on a chat. ``title_enc: null`` clears the title."""

    model_config = ConfigDict(extra="forbid")

    title_enc: str | None = Field(default=None, max_length=MAX_TITLE_ENC_CHARS)
    pinned: bool | None = None
    archived: bool | None = None


# --- the derived chat query ---

def _chat_selectable(user_id: uuid.UUID, key: str | None = None):
    """One row per chat: the live messages' aggregate FULL-joined to the live metadata row.

    FULL, because a chat can exist as messages only (never titled, pinned or archived) or as a
    metadata row only (created before its first message).
    """
    messages = select(
        Message.conversation_id.label("key"),
        func.min(Message.created_at).label("first_at"),
        func.max(Message.created_at).label("last_at"),
        func.count(Message.id).label("n"),
    ).where(Message.user_id == user_id, Message.deleted_at.is_(None))
    meta = select(
        Conversation.conversation_key.label("key"),
        Conversation.title_enc.label("title_enc"),
        Conversation.pinned.label("pinned"),
        Conversation.archived.label("archived"),
        Conversation.created_at.label("meta_created"),
        Conversation.updated_at.label("meta_updated"),
    ).where(Conversation.user_id == user_id, Conversation.deleted_at.is_(None))
    if key is not None:
        messages = messages.where(Message.conversation_id == key)
        meta = meta.where(Conversation.conversation_key == key)
    m = messages.group_by(Message.conversation_id).subquery("m")
    c = meta.subquery("c")

    cols = {
        "key": func.coalesce(m.c.key, c.c.key),
        "title_enc": c.c.title_enc,
        "pinned": func.coalesce(c.c.pinned, false()),
        "archived": func.coalesce(c.c.archived, false()),
        "message_count": func.coalesce(m.c.n, 0),
        "created_at": func.coalesce(m.c.first_at, c.c.meta_created),
        "last_activity_at": func.coalesce(m.c.last_at, c.c.meta_updated),
    }
    stmt = select(
        cols["key"].label("id"), cols["title_enc"].label("title_enc"), cols["pinned"].label("pinned"),
        cols["archived"].label("archived"), cols["message_count"].label("message_count"),
        cols["created_at"].label("created_at"), cols["last_activity_at"].label("last_activity_at"),
    ).select_from(m.outerjoin(c, m.c.key == c.c.key, full=True))
    return stmt, cols


def _item(row: Any) -> ChatItem:
    return ChatItem(
        id=row.id, title_enc=row.title_enc, pinned=bool(row.pinned), archived=bool(row.archived),
        message_count=int(row.message_count), created_at=row.created_at.isoformat(),
        last_activity_at=row.last_activity_at.isoformat(),
    )


async def _load(db: AsyncSession, user_id: uuid.UUID, key: str) -> ChatItem | None:
    stmt, _ = _chat_selectable(user_id, key)
    row = (await db.execute(stmt)).first()
    return _item(row) if row is not None else None


# --- cursor ---

def _encode_cursor(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str, expected: dict[str, Any]) -> dict[str, Any]:
    """The position a page token names, refused unless it was made for *these* list parameters.

    A cursor from a different sort, order, filter or pin setting would resume at a position in a
    different ordering and silently skip or repeat chats, so it is a 422, not a best effort.
    """
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        if not isinstance(data, dict) or data.get("q") != expected:
            raise ValueError("parameters differ")
        if not isinstance(data.get("v"), str) or not isinstance(data.get("k"), str):
            raise ValueError("malformed")
        datetime.fromisoformat(data["v"])
        if not isinstance(data.get("p"), bool):
            raise ValueError("malformed")
        return data
    except (ValueError, TypeError, binascii.Error, UnicodeDecodeError):
        raise _invalid("cursor", "not a cursor for this list; start again without one") from None


def _after(terms: list[tuple[Any, bool, Any]]):
    """``WHERE`` for "strictly after this position" over an ORDER BY of mixed directions.

    ``terms`` is ``[(expression, descending, cursor_value), ...]`` in ORDER BY order. Row-value
    comparison would do for one direction; pinned-first with an ascending date is mixed.
    """
    # `literal`: a bare Python bool is a SQL constant to SQLAlchemy, which refuses `<` on it; a bind
    # parameter compares like any other value.
    bound = [(expression, descending, literal(value)) for expression, descending, value in terms]
    clauses = []
    for index, (expression, descending, value) in enumerate(bound):
        later = expression < value if descending else expression > value
        clauses.append(and_(*[bound[j][0] == bound[j][2] for j in range(index)], later))
    return or_(*clauses)


# --- routes ---

@router.get("", response_model=ChatList)
async def list_chats(
    user_info: dict = Depends(verify_access_token),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    cursor: str | None = Query(default=None, max_length=512),
    sort: Literal["last_activity", "created_at"] = "last_activity",
    order: Literal["desc", "asc"] = "desc",
    archived: Literal["false", "true", "any"] = Query(
        default="false", description="'false' hides archived chats, 'true' shows only them, 'any' both."),
    pinned: bool | None = Query(default=None, description="Only pinned (true) or only unpinned (false) chats."),
    pinned_first: bool = Query(default=False, description="List pinned chats before the rest."),
    active_since: datetime | None = Query(default=None, description="Only chats with activity at or after this instant."),
    active_until: datetime | None = Query(default=None, description="Only chats with activity before this instant."),
) -> ChatList:
    """The caller's chats, newest activity first by default.

    Keyset-paged: pass ``next_cursor`` back as ``cursor`` with the same parameters. Chat titles
    and content cannot be searched here (they are ciphertext); filter by state and time only.
    """
    _require_db_configured()
    user = await get_or_create_user(db, user_info)
    stmt, cols = _chat_selectable(user.id)

    if archived == "false":
        stmt = stmt.where(cols["archived"] == false())
    elif archived == "true":
        stmt = stmt.where(cols["archived"] == true())
    if pinned is not None:
        stmt = stmt.where(cols["pinned"] == (true() if pinned else false()))
    if active_since is not None:
        stmt = stmt.where(cols["last_activity_at"] >= active_since)
    if active_until is not None:
        stmt = stmt.where(cols["last_activity_at"] < active_until)

    descending = order == "desc"
    value_col = cols["last_activity_at"] if sort == "last_activity" else cols["created_at"]
    ordering = ([(cols["pinned"], True)] if pinned_first else []) + [(value_col, descending), (cols["key"], descending)]

    params = {"sort": sort, "order": order, "archived": archived, "pinned": pinned,
              "pinned_first": pinned_first,
              "since": active_since.isoformat() if active_since else None,
              "until": active_until.isoformat() if active_until else None}
    if cursor is not None:
        position = _decode_cursor(cursor, params)
        values = ([position["p"]] if pinned_first else []) + [datetime.fromisoformat(position["v"]), position["k"]]
        stmt = stmt.where(_after([(e, d, v) for (e, d), v in zip(ordering, values)]))

    stmt = stmt.order_by(*[e.desc() if d else e.asc() for e, d in ordering]).limit(limit + 1)
    rows = (await db.execute(stmt)).all()
    page, more = rows[:limit], len(rows) > limit

    next_cursor = None
    if more:
        last = page[-1]
        sort_value = last.last_activity_at if sort == "last_activity" else last.created_at
        next_cursor = _encode_cursor({"q": params, "p": bool(last.pinned), "v": sort_value.isoformat(), "k": last.id})
    return ChatList(chats=[_item(r) for r in page], next_cursor=next_cursor)


@router.post("", response_model=ChatItem, status_code=201)
async def create_chat(
    body: ChatCreate,
    user_info: dict = Depends(verify_access_token),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """Create a chat before its first message (a pre-titled one).

    Idempotent on ``id``: creating a chat that already exists returns it (200) instead of failing,
    so a client that timed out and retried is not told it conflicts with itself. A ``title_enc``
    sent with it is applied.
    """
    _require_db_configured()
    key = _chat_key(body.id) if body.id else str(uuid.uuid4())
    user = await get_or_create_user(db, user_info)

    existing = await _load(db, user.id, key)
    if existing is not None:
        item = await _apply(db, user.id, key, {"title_enc": body.title_enc}) if body.title_enc is not None else existing
        return JSONResponse(status_code=200, content=item.model_dump())

    now = datetime.now(timezone.utc)
    row = (await db.execute(
        select(Conversation).where(Conversation.user_id == user.id, Conversation.conversation_key == key)
        .with_for_update()
    )).scalar_one_or_none()
    if row is None:
        db.add(Conversation(user_id=user.id, conversation_key=key, title_enc=body.title_enc, client_updated_at=now))
    else:  # a tombstone from a deleted chat with this key: bring it back
        row.deleted_at, row.title_enc, row.pinned, row.archived = None, body.title_enc, False, False
        row.client_updated_at = now
    try:
        await db.commit()
    except IntegrityError:  # a concurrent create of the same key won: that is the chat
        await db.rollback()
        winner = await _load(db, user.id, key)
        if winner is None:
            raise
        return JSONResponse(status_code=200, content=winner.model_dump())
    created = await _load(db, user.id, key)
    assert created is not None
    return created


@router.get("/{chat_id}", response_model=ChatItem)
async def get_chat(
    chat_id: str,
    user_info: dict = Depends(verify_access_token),
    db: AsyncSession = Depends(get_db),
) -> ChatItem:
    _require_db_configured()
    key = _chat_key(chat_id)
    user = await get_or_create_user(db, user_info)
    item = await _load(db, user.id, key)
    if item is None:
        raise _not_found(key)
    return item


async def _apply(db: AsyncSession, user_id: uuid.UUID, key: str, changes: dict[str, Any]) -> ChatItem:
    """Set ``changes`` on the chat's metadata row, creating the row if the chat has none yet."""
    if await _load(db, user_id, key) is None:
        raise _not_found(key)
    now = datetime.now(timezone.utc)
    row = (await db.execute(
        select(Conversation).where(Conversation.user_id == user_id, Conversation.conversation_key == key)
        .with_for_update()
    )).scalar_one_or_none()
    if row is None:
        row = Conversation(user_id=user_id, conversation_key=key)
        db.add(row)
    elif row.deleted_at is not None:
        row.deleted_at, row.title_enc, row.pinned, row.archived = None, None, False, False
    for name, value in changes.items():
        setattr(row, name, value)
    row.client_updated_at = now
    await db.commit()
    item = await _load(db, user_id, key)
    assert item is not None
    return item


@router.patch("/{chat_id}", response_model=ChatItem)
async def update_chat(
    chat_id: str,
    body: ChatUpdate,
    user_info: dict = Depends(verify_access_token),
    db: AsyncSession = Depends(get_db),
) -> ChatItem:
    """Set a title, pin or archive state. Send only what changes; at least one field."""
    _require_db_configured()
    key = _chat_key(chat_id)
    sent = body.model_fields_set
    if not sent:
        raise HTTPException(status_code=422, detail={"error": "empty_update", "detail": "Send at least one field."})
    changes: dict[str, Any] = {}
    if "title_enc" in sent:
        changes["title_enc"] = body.title_enc
    for flag in ("pinned", "archived"):
        if flag in sent:
            if getattr(body, flag) is None:
                raise _invalid(flag, "true or false")
            changes[flag] = getattr(body, flag)
    user = await get_or_create_user(db, user_info)
    return await _apply(db, user.id, key, changes)


def _flag_route(path: str, flag: str, value: bool, summary: str) -> None:
    async def handler(
        chat_id: str,
        user_info: dict = Depends(verify_access_token),
        db: AsyncSession = Depends(get_db),
    ) -> ChatItem:
        _require_db_configured()
        key = _chat_key(chat_id)
        user = await get_or_create_user(db, user_info)
        return await _apply(db, user.id, key, {flag: value})

    handler.__name__ = f"chat_{path.strip('/')}"
    handler.__doc__ = summary
    router.add_api_route(f"/{{chat_id}}/{path}", handler, methods=["POST"], response_model=ChatItem)


_flag_route("archive", "archived", True, "Archive the chat (hidden from the default list).")
_flag_route("unarchive", "archived", False, "Bring an archived chat back to the default list.")
_flag_route("pin", "pinned", True, "Pin the chat.")
_flag_route("unpin", "pinned", False, "Unpin the chat.")


@router.delete("/{chat_id}")
async def delete_chat(
    chat_id: str,
    request: Request,
    user_info: dict = Depends(verify_access_token),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Delete the chat: its messages and its title/pin/archive row, on every device.

    The rows become sync tombstones, so every other device learns of the delete on its next pull,
    and their ciphertext is cleared (the same rule the sync's own delete follows, audit F-07). The
    server's copy of the conversation transcript is forgotten too, like ``DELETE
    /api/v3/sessions/{id}``. Idempotent: deleting a chat that is already gone is another 200;
    only a key that never existed for the caller is a 404.
    """
    _require_db_configured()
    key = _chat_key(chat_id)
    user = await get_or_create_user(db, user_info)
    had_messages = (await db.execute(
        select(func.count(Message.id)).where(Message.user_id == user.id, Message.conversation_id == key)
    )).scalar_one() > 0
    had_meta = (await db.execute(
        select(func.count(Conversation.id)).where(Conversation.user_id == user.id,
                                                  Conversation.conversation_key == key)
    )).scalar_one() > 0
    if not had_messages and not had_meta:
        raise _not_found(key)

    now = datetime.now(timezone.utc)
    cleared = {
        column.name: (None if column.nullable else "")
        for column in Message.__table__.columns if column.name.endswith("_enc")
    }
    deleted = await db.execute(
        update(Message)
        .where(Message.user_id == user.id, Message.conversation_id == key, Message.deleted_at.is_(None))
        .values(deleted_at=now, client_updated_at=now, **cleared)
    )
    await db.execute(
        update(Conversation)
        .where(Conversation.user_id == user.id, Conversation.conversation_key == key,
               Conversation.deleted_at.is_(None))
        .values(deleted_at=now, client_updated_at=now, title_enc=None, pinned=False, archived=False)
    )
    await db.commit()

    container = getattr(request.app.state, "aura", None)
    episodic = getattr(container, "episodic", None)
    uid = user_info.get("uid")
    if episodic is not None and uid:
        try:
            await episodic.clear(SessionId(key), UserId(str(uid)))
        except Exception:  # the rows are already tombstoned; a stale transcript is not a failed delete
            logger.warning("chat deleted but its server transcript could not be cleared", exc_info=True)
    logger.info("chat deleted", extra={"messages": deleted.rowcount})
    return {"deleted": True, "messages_deleted": deleted.rowcount}



# --- messages ---

DEFAULT_MESSAGE_LIMIT = 50
MAX_MESSAGE_LIMIT = 100
#: A message's text is ciphertext: up to 32,000 characters of text become well under this once
#: sealed and base64-encoded. It bounds what is stored, not what the message says.
MAX_MESSAGE_ENC_CHARS = 512_000
#: How the client seals a value (``EncryptionService.isEnvelope``): the account data key (``gcm2``)
#: or the legacy device key (``gcm``). Anything else in a ``*_enc`` column is plaintext someone
#: forgot to seal, and is refused.
_ENVELOPES = ("gcm2:", "gcm:")
_ROLE = re.compile(r"^[a-z_]{1,16}$")


class MessageItem(BaseModel):
    id: str
    chat_id: str
    role: str
    #: Ciphertext the server cannot read (``gcm2:...``; a row an old device sealed with its own key
    #: starts ``gcm:``).
    content_enc: str
    citations_enc: str | None
    tool_calls_enc: str | None
    model_used: str | None
    tokens_used: int | None
    persona: str | None
    created_at: str
    updated_at: str
    #: The clock of whoever last wrote the row: the device's for a sync push, the server's for an
    #: edit made here. ``None`` for rows older than the column.
    client_updated_at: str | None


class MessageList(BaseModel):
    messages: list[MessageItem]
    #: Pass back as ``cursor`` for the next page; ``None`` on the last.
    next_cursor: str | None


class MessageUpdate(BaseModel):
    """The ciphertext fields a person may replace. ``null`` clears citations or tool calls; the text
    itself cannot be cleared (delete the message instead). The role is never editable."""

    model_config = ConfigDict(extra="forbid")

    content_enc: str | None = Field(default=None, max_length=MAX_MESSAGE_ENC_CHARS)
    citations_enc: str | None = Field(default=None, max_length=MAX_MESSAGE_ENC_CHARS)
    tool_calls_enc: str | None = Field(default=None, max_length=MAX_MESSAGE_ENC_CHARS)


def _message_key(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise _invalid("message_id", "a message id is a UUID") from None


def _message_not_found() -> HTTPException:
    return HTTPException(status_code=404, detail={"error": "not_found", "detail": "message not found"})


def _message_item(row: Message) -> MessageItem:
    return MessageItem(
        id=str(row.id), chat_id=row.conversation_id, role=row.role, content_enc=row.content_enc,
        citations_enc=row.citations_enc, tool_calls_enc=row.tool_calls_enc, model_used=row.model_used,
        tokens_used=row.tokens_used, persona=row.persona, created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
        client_updated_at=row.client_updated_at.isoformat() if row.client_updated_at else None,
    )


@router.get("/{chat_id}/messages", response_model=MessageList)
async def list_messages(
    chat_id: str,
    user_info: dict = Depends(verify_access_token),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=DEFAULT_MESSAGE_LIMIT, ge=1, le=MAX_MESSAGE_LIMIT),
    cursor: str | None = Query(default=None, max_length=512),
    order: Literal["asc", "desc"] = Query(default="asc", description="By creation time: oldest first (default) or newest first."),
    role: str | None = Query(default=None, description="Only messages of this role (user, assistant, ...)."),
) -> MessageList:
    """A chat's live messages, as ciphertext, oldest first by default.

    Keyset-paged on ``(created_at, id)``: pass ``next_cursor`` back as ``cursor`` with the same
    parameters. The text cannot be searched here (it is ciphertext); filter by role and order only.
    """
    _require_db_configured()
    key = _chat_key(chat_id)
    if role is not None and not _ROLE.match(role):
        raise _invalid("role", "lower-case letters and underscores, up to 16 characters")
    user = await get_or_create_user(db, user_info)
    if await _load(db, user.id, key) is None:
        raise _not_found(key)

    stmt = select(Message).where(
        Message.user_id == user.id, Message.conversation_id == key, Message.deleted_at.is_(None))
    if role is not None:
        stmt = stmt.where(Message.role == role)
    descending = order == "desc"
    params = {"chat": key, "order": order, "role": role}
    ordering = [(Message.created_at, descending), (Message.id, descending)]
    if cursor is not None:
        position = _decode_cursor(cursor, params)
        try:
            after = [datetime.fromisoformat(position["v"]), uuid.UUID(position["k"])]
        except ValueError:
            raise _invalid("cursor", "not a cursor for this list; start again without one") from None
        stmt = stmt.where(_after([(e, d, v) for (e, d), v in zip(ordering, after)]))
    stmt = stmt.order_by(*[e.desc() if d else e.asc() for e, d in ordering]).limit(limit + 1)
    rows = (await db.execute(stmt)).scalars().all()
    page, more = rows[:limit], len(rows) > limit
    next_cursor = None
    if more:
        last = page[-1]
        next_cursor = _encode_cursor({"q": params, "p": False, "v": last.created_at.isoformat(), "k": str(last.id)})
    return MessageList(messages=[_message_item(r) for r in page], next_cursor=next_cursor)


async def _own_message(db: AsyncSession, user_id: uuid.UUID, key: str, message_id: uuid.UUID, *,
                       live_only: bool, lock: bool = False) -> Message | None:
    stmt = select(Message).where(
        Message.user_id == user_id, Message.conversation_id == key, Message.id == message_id)
    if live_only:
        stmt = stmt.where(Message.deleted_at.is_(None))
    if lock:
        stmt = stmt.with_for_update()
    return (await db.execute(stmt)).scalar_one_or_none()


@router.get("/{chat_id}/messages/{message_id}", response_model=MessageItem)
async def get_message(
    chat_id: str,
    message_id: str,
    user_info: dict = Depends(verify_access_token),
    db: AsyncSession = Depends(get_db),
) -> MessageItem:
    """One live message of the caller's chat, as ciphertext."""
    _require_db_configured()
    key, mid = _chat_key(chat_id), _message_key(message_id)
    user = await get_or_create_user(db, user_info)
    row = await _own_message(db, user.id, key, mid, live_only=True)
    if row is None:
        raise _message_not_found()
    return _message_item(row)


@router.patch("/{chat_id}/messages/{message_id}", response_model=MessageItem)
async def update_message(
    chat_id: str,
    message_id: str,
    body: MessageUpdate,
    user_info: dict = Depends(verify_access_token),
    db: AsyncSession = Depends(get_db),
) -> MessageItem:
    """Replace a message's ciphertext (text, citations, tool calls). Send only what changes.

    The new value is the client's own ciphertext; the server checks only that it is shaped like one
    (``gcm2:`` or ``gcm:``) and is not too large. A deleted message is not edited back to life.
    """
    _require_db_configured()
    key, mid = _chat_key(chat_id), _message_key(message_id)
    sent = body.model_fields_set
    if not sent:
        raise HTTPException(status_code=422, detail={"error": "empty_update", "detail": "Send at least one field."})
    if "content_enc" in sent and body.content_enc is None:
        raise _invalid("content_enc", "the text cannot be cleared; delete the message instead")
    for name in sent:
        value = getattr(body, name)
        if value is not None and not value.startswith(_ENVELOPES):
            raise _invalid(name, "not a sealed value (expected gcm2: or gcm:); the server stores no plaintext here")
    user = await get_or_create_user(db, user_info)
    row = await _own_message(db, user.id, key, mid, live_only=True, lock=True)
    if row is None:
        raise _message_not_found()
    for name in sent:
        setattr(row, name, getattr(body, name))
    row.client_updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(row)
    return _message_item(row)


@router.delete("/{chat_id}/messages/{message_id}")
async def delete_message(
    chat_id: str,
    message_id: str,
    user_info: dict = Depends(verify_access_token),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Delete one message on every device: its row becomes a sync tombstone and its ciphertext is cleared.

    Idempotent: a message that is already deleted is another 200; only an id that never existed
    for the caller is a 404. The server's working transcript of the conversation is not edited
    (it has no message ids); ``DELETE /api/v3/sessions/{id}`` forgets it.
    """
    _require_db_configured()
    key, mid = _chat_key(chat_id), _message_key(message_id)
    user = await get_or_create_user(db, user_info)
    row = await _own_message(db, user.id, key, mid, live_only=False, lock=True)
    if row is None:
        raise _message_not_found()
    if row.deleted_at is None:
        now = datetime.now(timezone.utc)
        row.deleted_at, row.client_updated_at = now, now
        for column in Message.__table__.columns:
            if column.name.endswith("_enc"):
                setattr(row, column.name, None if column.nullable else "")
        await db.commit()
    return {"deleted": True}
