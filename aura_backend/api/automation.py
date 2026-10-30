"""Email and issue analysis — remounted under ``/api/v3/automation``."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from api.http_errors import http_internal_error
from services.automation import AnalysisUnavailable, automation_service
from services.budget import InferenceBudgetExceededException
from services.jwt_auth import verify_access_token

router = APIRouter(prefix="/api/v3/automation", tags=["automation"])


class EmailAnalyzeRequest(BaseModel):
    subject: str
    sender: str
    body: str


class IssueAnalyzeRequest(BaseModel):
    title: str
    body: str


def _router(request: Request):
    """The gateway's ``ProviderRouter`` -- ``aura_service.create_app`` sets ``app.state.aura``."""
    container = getattr(request.app.state, "aura", None)
    if container is None:
        raise HTTPException(status_code=503, detail="analysis_unavailable")
    return container.router


def _unavailable(exc: AnalysisUnavailable) -> HTTPException:
    # 503, not a 200 carrying a placeholder: the client shows an error it can retry rather than
    # rendering "analysis unavailable" as though it were the analysis.
    return HTTPException(
        status_code=503, detail={"error": "analysis_unavailable", "reason": str(exc)}
    )


@router.post("/analyze-email")
async def analyze_email(
    request: EmailAnalyzeRequest, http_request: Request,
    user_info: dict = Depends(verify_access_token),
):
    """Analyzes email content and returns structured JSON details."""
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


@router.post("/analyze-issue")
async def analyze_issue(
    request: IssueAnalyzeRequest, http_request: Request,
    user_info: dict = Depends(verify_access_token),
):
    """Analyzes GitHub issue content and returns structured checklist details."""
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
