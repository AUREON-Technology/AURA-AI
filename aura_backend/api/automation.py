"""Email and issue analysis — remounted under ``/api/v3/automation``."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from api.http_errors import http_internal_error
from services.automation import (
    AnalysisUnavailable,
    EmailAnalysis,
    IssueAnalysis,
    automation_service,
)
from services.budget import InferenceBudgetExceededException
from services.jwt_auth import verify_access_token

router = APIRouter(prefix="/api/v3/automation", tags=["automation"])


# F36: every field is model input the caller chooses, so each is bounded. 50,000 characters is
# a long email's text (~12k tokens of prompt on the CPU model); 65,536 is GitHub's own ceiling
# for an issue body, so any real issue fits.
MAX_EMAIL_BODY_CHARS = 50_000
MAX_ISSUE_BODY_CHARS = 65_536


class EmailAnalyzeRequest(BaseModel):
    subject: str = Field(max_length=2_000)
    sender: str = Field(max_length=512)
    body: str = Field(max_length=MAX_EMAIL_BODY_CHARS)


class IssueAnalyzeRequest(BaseModel):
    title: str = Field(max_length=1_024)
    body: str = Field(max_length=MAX_ISSUE_BODY_CHARS)


def _router(request: Request):
    """The gateway's ``ProviderRouter`` -- ``aura_service.create_app`` sets ``app.state.aura``."""
    container = getattr(request.app.state, "aura", None)
    if container is None:
        raise HTTPException(status_code=503, detail="analysis_unavailable")
    return container.router


async def _enforce_rate_limit(request: Request, user_info: dict) -> None:
    """F36: one analysis is one model generation over caller-chosen text, so it is charged like a
    chat turn against the gateway's own limiter -- the one the v3 routes use, per user and per
    client address (``aura_service`` exposes it as ``app.state.rate_limiter``). ``None`` when
    the deployment turned rate limiting off, as for every v3 route."""
    limiter = getattr(request.app.state, "rate_limiter", None)
    if limiter is None:
        return
    from aura_core.domain.errors import RateLimitedError
    from aura_core.security.rate_limit import extract_client_ip

    container = getattr(request.app.state, "aura", None)
    trusted = container.settings.security.trusted_proxy_count if container is not None else 1
    client_ip = extract_client_ip(
        request.headers, peer=request.client.host if request.client else None,
        trusted_proxies=trusted,
    )
    decision = await limiter.check_all(
        user_key=str(user_info.get("uid") or "") or None, ip_key=client_ip, route_class="chat",
    )
    if not decision.allowed:
        raise HTTPException(
            status_code=429,
            detail=RateLimitedError("rate limit exceeded", retry_after=decision.retry_after_s).to_dict(),
            headers={"Retry-After": str(decision.retry_after_s)},
        )


def _unavailable(exc: AnalysisUnavailable) -> HTTPException:
    # 503, not a 200 carrying a placeholder: the client shows an error it can retry rather than
    # rendering "analysis unavailable" as though it were the analysis.
    detail = {"error": "analysis_unavailable", "reason": str(exc)}
    if exc.message:
        # INT-115: a refusal that retrying cannot clear says so, in words.
        detail.update(message=exc.message, retryable=exc.retryable)
    return HTTPException(status_code=503, detail=detail)


@router.post("/analyze-email", response_model=EmailAnalysis)
async def analyze_email(
    request: EmailAnalyzeRequest, http_request: Request,
    user_info: dict = Depends(verify_access_token),
):
    """Analyzes email content and returns structured JSON details."""
    await _enforce_rate_limit(http_request, user_info)
    try:
        result = await automation_service.analyze_email(
            _router(http_request),
            subject=request.subject,
            sender=request.sender,
            body=request.body
        )
        return result
    except InferenceBudgetExceededException as ge:
        raise ge
    except AnalysisUnavailable as exc:
        raise _unavailable(exc)
    except HTTPException:
        raise
    except Exception as e:
        raise http_internal_error(
            e, where="analyze_email", user_email=user_info.get("email")
        )


@router.post("/analyze-issue", response_model=IssueAnalysis)
async def analyze_issue(
    request: IssueAnalyzeRequest, http_request: Request,
    user_info: dict = Depends(verify_access_token),
):
    """Analyzes GitHub issue content and returns structured checklist details."""
    await _enforce_rate_limit(http_request, user_info)
    try:
        result = await automation_service.analyze_github_issue(
            _router(http_request),
            title=request.title,
            body=request.body
        )
        return result
    except InferenceBudgetExceededException as ge:
        raise ge
    except AnalysisUnavailable as exc:
        raise _unavailable(exc)
    except HTTPException:
        raise
    except Exception as e:
        raise http_internal_error(
            e, where="analyze_issue", user_email=user_info.get("email")
        )
