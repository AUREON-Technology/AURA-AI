"""The signed-in user's own account: ``POST /api/v3/account/delete``.

Both app stores require an app that creates accounts to let the user delete theirs from inside
the app (Apple App Store Review Guideline 5.1.1(v); Google Play's account deletion policy).
Until this route existed the only erasure was the SuperAdmin-only operator route,
``POST /api/v3/admin/users/{subject}/forget``. This one runs the same ``erase_account`` on the
caller -- nothing more, nothing less -- so the two cannot drift apart.

Lives outside ``aura_core`` for the same reason the operator route does: it talks to SQLAlchemy
and the JWT refresh store (ADR-002).

Who can call it: an access token this server issued, for the account it names. A token limited
to a scope (a paired daemon's host-agent token) is refused by ``verify_access_token``, and a
service key (ADR-029) is not a JWT at all, so neither can delete the account it belongs to. The
body must say ``{"confirm": "DELETE"}``: an accidental or replayed call with an empty body does
nothing.
"""

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api.admin_platform import erase_account
from services.jwt_auth import verify_access_token

logger = logging.getLogger("aura.account")

router = APIRouter(prefix="/api/v3/account", tags=["account"])


class DeleteAccountRequest(BaseModel):
    confirm: Literal["DELETE"]


@router.post("/delete")
async def delete_my_account(
    payload: DeleteAccountRequest,
    request: Request,
    user_info: dict = Depends(verify_access_token),
):
    """Erase the caller's account and everything it owns. Returns counts only, never content.

    ``forgotten: false`` with ``failed`` naming stores means the ``users`` row was kept and the
    same call can be retried; the response is 200 either way, so the client reads the flag. The
    caller's refresh tokens and devices are revoked even then, so the app must sign out locally
    whatever the answer.
    """
    subject = user_info.get("uid")
    if not subject:
        raise HTTPException(status_code=401, detail="This token does not name an account.")
    container = getattr(request.app.state, "aura", None)
    tenancy = getattr(container, "tenancy", None)
    if tenancy is not None:
        # Spec 5.1 (SaaS P1-F1): a team cannot lose its only Owner to an account deletion. The
        # person hands each team over or deletes it first; nothing is erased until then.
        owned = await tenancy.sole_ownerships(str(subject))
        if owned:
            return JSONResponse(status_code=409, content={
                "error": "owns_team_workspaces",
                "detail": "Transfer or delete these team workspaces before deleting your account.",
                "workspaces": [{"id": ws.id, "name": ws.name} for ws in owned],
            })
    result = await erase_account(container, str(subject), unknown_user_ok=True)
    # No subject or email in the log line: the account is being erased, and the audit log
    # pseudonymises it for the same reason (FSA-016).
    logger.info(
        "account self-deletion",
        extra={"forgotten": result["forgotten"], "failed": result["failed"]},
    )
    return result
