"""What a person can take out of AURA: ``GET /sessions/{id}/export`` and ``GET /me/export``.

    GET /sessions/{id}/export?format=json|markdown   one conversation, as the server holds it
    GET /me/export                                   everything the server holds about the caller

**A conversation export is partial, and says so.** The synced chat rows (``content_enc``,
``title_enc``) are ciphertext only the person's devices can open: the server never decrypts them
(ADR-032), so it cannot export "the chat" and this route does not pretend to. What the server can
read is its own working transcript of the session (``EpisodicMemory``): a running **summary** of
the older turns plus the turns not yet folded into it, kept for 30 days. That is what is exported,
with ``complete: false`` and how many earlier messages the summary stands in for. A complete copy is
the client's job, from its own decrypted database.

**The bundle is the caller's personal scope, in plain text, and nothing else**: custom agents, model
and notification settings, the notification inbox, the tool-approval ledger (kinds and digests, no
arguments: ``execution/confirmations.py``), long-term memories and the *list* of personal knowledge
documents (title, version, status -- not their text, which is the document store's to serve). A
team's knowledge, plan and billing belong to the team, not to a person's download, and a person's
account is not a place to read them from. Usage has its own route (``/me/usage``). No credentials,
tokens or keys are ever in it: no store the bundle reads holds one.

Each section is read on its own. One store failing marks that section ``available: false`` and the
rest still come back, so a person is never told "no export" because the notification table was
unwell. Every list is capped; ``truncated`` says when the cap cut it.

Both routes are owner-scoped like every personal route: another person's session id is
indistinguishable from one that does not exist (404), and a service key has no person to export
(403). No ``from __future__ import annotations``, like the other API modules.
"""

import logging
import re
from datetime import datetime, timezone
from typing import Any, Callable, Literal

from ..domain.errors import AuthorizationError, NotFoundError, RateLimitedError, ValidationError
from ..domain.identifiers import SessionId

logger = logging.getLogger(__name__)

__all__ = ["register_export_ops", "EXPORT_VERSION", "BUNDLE_SECTIONS"]

#: Bumped when a field of the bundle changes meaning, so a reader can tell which shape it holds.
EXPORT_VERSION = 1

#: The sections of ``/me/export``, in the order they appear.
BUNDLE_SECTIONS = (
    "custom_agents",
    "model_settings",
    "notification_settings",
    "notifications",
    "approvals",
    "memories",
    "knowledge_documents",
)

#: Rows per list. Generous for a person; a cap all the same, because the response is built in memory.
MAX_MEMORIES = 1000
MAX_DOCUMENTS = 1000
MAX_APPROVALS = 500
_PAGE = 100

#: Exports per person per minute. A person downloads their data now and then; a loop is not that.
EXPORTS_PER_MINUTE = 5


def _safe_name(value: str) -> str:
    """A file name part that cannot carry a path, a quote or a newline into a header."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", value)[:64] or "conversation"


async def collect(fetch: Callable[..., Any], cap: int) -> tuple[list[Any], bool]:
    """Up to ``cap`` rows from a paged ``fetch(limit=, offset=)``, and whether there were more.

    Asks for one row beyond the cap, so a person with exactly ``cap`` rows is not told their list was
    cut: ``truncated`` is true only when a row was actually left out."""
    rows: list[Any] = []
    offset = 0
    while len(rows) <= cap:
        wanted = min(_PAGE, cap + 1 - len(rows))
        page = list(await fetch(limit=wanted, offset=offset))
        rows += page
        if len(page) < wanted:
            break
        offset += len(page)
    return rows[:cap], len(rows) > cap


def conversation_markdown(session_id: str, summary: str, messages: list[dict[str, str]], note: str) -> str:
    """The transcript as a Markdown document: the summary of earlier turns, then each message."""
    lines = [f"# Conversation {session_id}", "", f"> {note}", ""]
    if summary:
        lines += ["## Summary of earlier turns", "", summary, ""]
    for message in messages:
        speaker = "You" if message["role"] == "user" else "AURA"
        lines += [f"## {speaker}", "", message["content"], ""]
    return "\n".join(lines).rstrip() + "\n"


def register_export_ops(
    router: Any,
    container: Any,
    *,
    principal: Callable,
    idempotent: list,
    Depends: Any,
    JSONResponse: Any,
    enforce_rate_limit: Callable,
    Request: Any,
) -> None:
    from fastapi import Query
    from fastapi.responses import Response

    from ..security.rate_limit import SlidingWindowRateLimiter

    limiter = SlidingWindowRateLimiter(container.cache_backend, policies=(), metrics=getattr(container, "metrics", None))

    def now() -> datetime:
        clock = getattr(container, "clock", None)
        return clock.now() if clock is not None else datetime.now(timezone.utc)

    def person(caller: Any) -> Any:
        if caller.is_service:
            raise AuthorizationError("a service key has no data of its own to export")
        return caller.user_id

    async def export_budget(caller: Any) -> None:
        decision = await limiter.check_limit(key=str(caller.user_id), scope="data_export", limit=EXPORTS_PER_MINUTE)
        if not decision.allowed:
            raise RateLimitedError("too many exports; try again in a minute", retry_after=decision.retry_after_s)

    # -- one conversation ------------------------------------------------------------------

    @router.get("/sessions/{session_id}/export")
    async def export_session(
        request: Request,
        session_id: str,
        caller: Any = Depends(principal),
        format: Literal["json", "markdown"] = Query(default="json"),
    ) -> Any:
        await enforce_rate_limit(request, caller, "read")
        owner = person(caller)
        await export_budget(caller)
        try:
            session = SessionId(session_id)
        except ValueError as exc:
            raise ValidationError("session id is not a valid identifier") from exc
        state = await container.episodic.load(session, owner)
        # A session the caller does not own loads as a fresh, empty one: it is answered exactly as
        # one that never existed.
        if not state.messages and not state.summary:
            raise NotFoundError("no transcript of this session is held", details={"session_id": session_id})
        messages = [
            {"role": str(m.role), "content": m.content}
            for m in state.messages
            if str(m.role) in ("user", "assistant")
        ]
        note = (
            "Partial: this is the server's working transcript of the conversation -- a summary of "
            "older turns plus the turns not yet summarised, kept for 30 days. The full chat lives, "
            "encrypted, on your devices."
        )
        if format == "markdown":
            body = conversation_markdown(session.value, state.summary, messages, note)
            return Response(
                content=body,
                media_type="text/markdown; charset=utf-8",
                headers={"Content-Disposition": f'attachment; filename="aura-chat-{_safe_name(session.value)}.md"'},
            )
        return JSONResponse(content={
            "version": EXPORT_VERSION,
            "session_id": session.value,
            "complete": False,
            "note": note,
            "summary": state.summary,
            "earlier_messages_summarised": state.folded,
            "turn_count": state.turn_count,
            "messages": messages,
        })

    # -- everything held about the caller --------------------------------------------------

    async def section(read: Callable[[], Any]) -> dict[str, Any]:
        """One section of the bundle, or ``available: false`` when its store would not answer."""
        try:
            body = await read()
        except Exception:
            logger.warning("export section could not be read", exc_info=True)
            return {"available": False}
        return {"available": True, **body}

    async def custom_agents(owner: Any) -> dict[str, Any]:
        store = getattr(container, "custom_agents", None)
        if store is None:
            return {"available": False}
        rows = await store.list_for_owner(owner)
        return {"items": [a.as_dict() for a in rows]}

    async def model_settings(owner: Any) -> dict[str, Any]:
        store = getattr(container, "model_settings", None)
        found = await store.get(owner) if store is not None else None
        return {"settings": found.as_dict() if found is not None else None}

    async def notification_settings(owner: Any) -> dict[str, Any]:
        store = getattr(container, "notification_settings", None)
        found = await store.get(owner) if store is not None else None
        return {"disabled_kinds": sorted(found.disabled_kinds) if found is not None else []}

    async def notifications(owner: Any) -> dict[str, Any]:
        from ..ports.notifications import MAX_PER_OWNER

        store = getattr(container, "notification_store", None)
        if store is None:
            return {"available": False}
        rows = await store.list(owner, limit=MAX_PER_OWNER)
        return {"items": [n.as_dict() for n in rows]}

    async def approvals(owner: Any) -> dict[str, Any]:
        ledger = getattr(container, "approval_ledger", None)
        if ledger is None:
            return {"available": False}
        at = now()
        rows = list(await ledger.list(owner, now=at, limit=MAX_APPROVALS + 1))
        return {"items": [a.as_dict(at) for a in rows[:MAX_APPROVALS]], "truncated": len(rows) > MAX_APPROVALS}

    async def memories(owner: Any) -> dict[str, Any]:
        store = getattr(container, "memory_store", None)
        if store is None or getattr(container, "memory", None) is None:
            return {"available": False}
        rows, truncated = await collect(lambda **page: store.list_for_owner(owner, **page), MAX_MEMORIES)
        items = [
            {
                "id": r.id.value,
                "kind": str(r.kind),
                "subject": r.subject,
                "content": r.content,
                "confidence": round(r.confidence, 3),
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]
        return {"items": items, "truncated": truncated}

    async def documents(owner: Any) -> dict[str, Any]:
        store = getattr(container, "documents", None)
        if store is None:
            return {"available": False}
        rows, truncated = await collect(lambda **page: store.list_active(owner_id=owner, **page), MAX_DOCUMENTS)
        items = [
            {
                "document_id": d.id.value,
                "title": d.title,
                "version": d.version,
                "status": str(d.status),
                "cag_pinned": d.cag_pinned,
                "created_at": d.created_at.isoformat(),
            }
            for d in rows
        ]
        return {
            "items": items,
            "truncated": truncated,
            "note": "titles and status only; document text is served by /knowledge/documents/{id}",
        }

    @router.get("/me/export")
    async def export_my_data(request: Request, caller: Any = Depends(principal)) -> Any:
        await enforce_rate_limit(request, caller, "read")
        owner = person(caller)
        await export_budget(caller)
        readers: dict[str, Callable[[Any], Any]] = {
            "custom_agents": custom_agents,
            "model_settings": model_settings,
            "notification_settings": notification_settings,
            "notifications": notifications,
            "approvals": approvals,
            "memories": memories,
            "knowledge_documents": documents,
        }
        assert tuple(readers) == BUNDLE_SECTIONS
        at = now()
        body: dict[str, Any] = {
            "version": EXPORT_VERSION,
            "exported_at": at.isoformat(),
            "user_id": str(owner),
            "scope": "personal",
            "not_included": [
                "synced chat history (end-to-end encrypted; export it from the app)",
                "team knowledge, plan and billing (they belong to the team)",
                "usage (GET /me/usage)",
            ],
        }
        for name, read in readers.items():
            # ``read`` is bound per iteration through the default argument, not closed over.
            body[name] = await section(lambda read=read: read(owner))
        return JSONResponse(
            content=body,
            headers={"Content-Disposition": f'attachment; filename="aura-data-{at.date().isoformat()}.json"'},
        )
