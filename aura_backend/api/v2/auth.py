"""Token exchange endpoints (Doc/V2_BLUEPRINT.md §9). The client
authenticates with its existing Google/Firebase ID token exactly once
(via services.security.verify_token, which also enforces device
attestation), then uses the returned access/refresh JWT pair for every v2
API call going forward instead of re-sending the Google/Firebase token.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from services.jwt_auth import issue_token_pair, refresh_token_pair, revoke_refresh_token
from services.security import verify_token

router = APIRouter(prefix="/api/v2/auth", tags=["auth (v2)"])


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str
    expires_in: int


class RefreshRequest(BaseModel):
    refresh_token: str


class RevokeRequest(BaseModel):
    refresh_token: str


@router.post("/token", response_model=TokenResponse)
async def exchange_token(user_info: dict = Depends(verify_token)):
    """Exchanges a verified Google/Firebase ID token (+ device attestation,
    both checked by verify_token) for a backend-issued access/refresh pair."""
    return issue_token_pair(user_info)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(request: RefreshRequest):
    result = refresh_token_pair(request.refresh_token)
    if result is None:
        raise HTTPException(status_code=401, detail="Refresh token is invalid, expired, or already used.")
    return result


@router.post("/revoke")
async def revoke(request: RevokeRequest):
    revoke_refresh_token(request.refresh_token)
    return {"status": "revoked"}
