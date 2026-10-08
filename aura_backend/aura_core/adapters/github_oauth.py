"""GitHub OAuth token exchange for the per-user GitHub App connect flow.

Separate from ``github_client.py`` (the REST API client used once a token already exists):
this talks to ``github.com``'s OAuth endpoints, not ``api.github.com`` -- a different host and a
different lifecycle. The initial exchange happens once, right after a user authorizes the app
(``api/v2/github_integration.py``'s callback route); refresh happens lazily, whenever
``container.py``'s per-user client resolver finds a stored token has expired. Both call the same
functions here rather than duplicating the HTTP shape twice.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

__all__ = [
    "GitHubOAuthError",
    "GitHubOAuthToken",
    "exchange_code",
    "refresh_access_token",
    "fetch_authenticated_user",
]

_TOKEN_URL = "https://github.com/login/oauth/access_token"
_USER_URL = "https://api.github.com/user"


class GitHubOAuthError(RuntimeError):
    """Raised on any OAuth failure -- a non-2xx response, or a 200 carrying an ``error`` field
    (GitHub's token endpoint reports failures that way, not with HTTP status)."""


@dataclass(frozen=True, slots=True)
class GitHubOAuthToken:
    access_token: str
    refresh_token: str | None
    expires_in: int | None
    refresh_token_expires_in: int | None
    scope: str


def _parse_token(payload: Mapping[str, Any]) -> GitHubOAuthToken:
    if "error" in payload:
        raise GitHubOAuthError(
            f"GitHub OAuth error: {payload.get('error_description') or payload['error']}"
        )
    if "access_token" not in payload:
        raise GitHubOAuthError("GitHub OAuth response did not include an access_token")
    return GitHubOAuthToken(
        access_token=payload["access_token"],
        refresh_token=payload.get("refresh_token"),
        expires_in=payload.get("expires_in"),
        refresh_token_expires_in=payload.get("refresh_token_expires_in"),
        scope=payload.get("scope", ""),
    )


async def _post_form(data: Mapping[str, str]) -> Mapping[str, Any]:
    import httpx

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(
            _TOKEN_URL, data=data, headers={"Accept": "application/json"}
        )
    if response.status_code >= 400:
        raise GitHubOAuthError(
            f"GitHub OAuth token request failed: HTTP {response.status_code} "
            f"{response.text[:300]}"
        )
    return response.json()


async def exchange_code(
    *, client_id: str, client_secret: str, code: str, redirect_uri: str
) -> GitHubOAuthToken:
    """The one-time exchange right after a user authorizes the app and GitHub redirects back
    with a ``code``."""
    payload = await _post_form(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        }
    )
    return _parse_token(payload)


async def refresh_access_token(
    *, client_id: str, client_secret: str, refresh_token: str
) -> GitHubOAuthToken:
    """Only meaningful when the GitHub App has refresh tokens enabled -- otherwise GitHub never
    issued one to begin with, and a caller with no stored ``refresh_token`` should not call this
    at all (``container.py``'s resolver checks first)."""
    payload = await _post_form(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
    )
    return _parse_token(payload)


async def fetch_authenticated_user(access_token: str) -> Mapping[str, Any]:
    """Who the token belongs to -- called once at connect time to record ``github_login`` and
    ``github_user_id`` against the newly stored identity."""
    import httpx

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(
            _USER_URL,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
    if response.status_code >= 400:
        raise GitHubOAuthError(
            f"could not fetch the authenticated GitHub user: HTTP {response.status_code}"
        )
    return response.json()
