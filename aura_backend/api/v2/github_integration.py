"""Per-user GitHub OAuth connect flow for the engineer agent.

Distinct from the deployment-wide admin PAT (`aura_core/config/settings.py`'s `GitHubSettings`,
configured once by an operator via `AURA_GITHUB_TOKEN`): this lets each logged-in user connect
their *own* GitHub account, so the engineer agent's GitHub tools act on exactly the repositories
that user granted the GitHub App during installation, not a deployment-wide token.

Routed like `api/v2/auth.py` and `api/v2/sync.py`: file lives under `api/v2/` but the URL prefix
is `/api/v3/...` (the v3-only-surface fitness test enforces this), and it talks to the legacy
Postgres schema directly via SQLAlchemy rather than going through `aura_core`'s ports -- the same
"permanent platform adapter mounted outside aura_core" exception those routers document.

Three of the four routes need the caller's identity from the existing backend JWT
(`services.jwt_auth.verify_access_token`), same as any other v2 endpoint. `/callback` is the
exception: GitHub redirects a plain browser here with no Authorization header at all, so the
acting user has to come back out of the `state` value `/connect` handed out
(`services/github_oauth_state.py`), not a bearer token.
"""
import logging
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aura_core.adapters.github_oauth import (
    GitHubOAuthError,
    exchange_code,
    fetch_authenticated_user,
)
from aura_core.config.settings import GitHubSettings
from infrastructure.db import github_identity_repo
from infrastructure.db.base import get_db
from infrastructure.db.models import User
from services.github_oauth_state import consume_state, store_state
from services.jwt_auth import verify_access_token

logger = logging.getLogger("aura.github_integration")

router = APIRouter(prefix="/api/v3/integrations/github", tags=["integrations"])


def _settings() -> GitHubSettings:
    return GitHubSettings.from_env()


def _require_configured(settings: GitHubSettings) -> None:
    if not (settings.oauth_enabled and settings.oauth_client_id and settings.oauth_callback_url):
        raise HTTPException(
            status_code=503,
            detail=(
                "GitHub OAuth is not configured on this deployment "
                "(AURA_GITHUB_OAUTH_ENABLED / _CLIENT_ID / _CALLBACK_URL)."
            ),
        )


async def _get_user(db: AsyncSession, auth_subject: str) -> User:
    result = await db.execute(select(User).where(User.auth_subject == auth_subject))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=404,
            detail="No user record for this session -- sign in again before connecting GitHub.",
        )
    return user


@router.get("/connect")
async def connect(user_info: dict = Depends(verify_access_token)):
    """Returns the GitHub authorization URL for the client to open itself, in a system browser
    or custom tab -- deliberately *not* a 302. A plain HTTP redirect only carries the caller's
    identity along when a browser session cookie does the authenticating; here the caller
    authenticates with a bearer JWT on this one authenticated call, and nothing forwards that
    header through a client-followed redirect. The client is expected to fetch this as JSON,
    then hand ``authorize_url`` to its own browser-launching code.
    """
    settings = _settings()
    _require_configured(settings)

    state = secrets.token_urlsafe(32)
    store_state(state, user_info["uid"])

    query = urlencode(
        {
            "client_id": settings.oauth_client_id,
            "redirect_uri": settings.oauth_callback_url,
            "state": state,
        }
    )
    return {"authorize_url": f"https://github.com/login/oauth/authorize?{query}"}


@router.get("/callback", response_class=HTMLResponse)
async def callback(
    code: str = Query(...),
    state: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """GitHub redirects here after the user approves (or denies) the authorization request.
    Renders a minimal static page rather than JSON -- the caller is a browser, not the app."""
    settings = _settings()
    _require_configured(settings)

    auth_subject = consume_state(state)
    if auth_subject is None:
        raise HTTPException(status_code=400, detail="This connection link has expired or was already used.")

    try:
        token = await exchange_code(
            client_id=settings.oauth_client_id,
            client_secret=settings.oauth_client_secret,
            code=code,
            redirect_uri=settings.oauth_callback_url,
        )
        github_user = await fetch_authenticated_user(token.access_token)
    except GitHubOAuthError as exc:
        logger.warning("GitHub OAuth exchange failed: %s", exc)
        raise HTTPException(status_code=502, detail="GitHub did not accept the connection request.")

    user = await _get_user(db, auth_subject)
    now = datetime.now(timezone.utc)
    await github_identity_repo.upsert(
        db,
        user,
        github_user_id=github_user["id"],
        github_login=github_user.get("login", ""),
        access_token=token.access_token,
        refresh_token=token.refresh_token,
        token_expires_at=(now + timedelta(seconds=token.expires_in)) if token.expires_in else None,
        refresh_token_expires_at=(
            now + timedelta(seconds=token.refresh_token_expires_in)
            if token.refresh_token_expires_in else None
        ),
        installation_id=None,
        scopes=token.scope,
    )
    await db.commit()

    login = github_user.get("login", "your GitHub account")
    return (
        "<html><body style=\"font-family: sans-serif; text-align: center; padding-top: 4rem;\">"
        f"<h2>Connected to GitHub as {login}</h2>"
        "<p>You can close this window and return to AURA.</p>"
        "</body></html>"
    )


@router.get("/status")
async def status(
    user_info: dict = Depends(verify_access_token),
    db: AsyncSession = Depends(get_db),
):
    user = await _get_user(db, user_info["uid"])
    identity = await github_identity_repo.get_by_user(db, user)
    if identity is None:
        return {"connected": False}
    return github_identity_repo.to_status_dict(identity)


@router.post("/disconnect")
async def disconnect(
    user_info: dict = Depends(verify_access_token),
    db: AsyncSession = Depends(get_db),
):
    user = await _get_user(db, user_info["uid"])
    removed = await github_identity_repo.delete(db, user)
    await db.commit()
    return {"disconnected": removed}
