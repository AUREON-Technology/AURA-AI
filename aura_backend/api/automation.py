"""Email and issue analysis — remounted under ``/api/v3/automation``."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.http_errors import http_internal_error
from services.automation import automation_service
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


@router.post("/analyze-email")
async def analyze_email(request: EmailAnalyzeRequest, user_info: dict = Depends(verify_access_token)):
    """Analyzes email content and returns structured JSON details."""
    try:
        result = automation_service.analyze_email(
            subject=request.subject,
            sender=request.sender,
            body=request.body
        )
        return result
    except InferenceBudgetExceededException as ge:
        raise ge
    except HTTPException:
        raise
    except Exception as e:
        raise http_internal_error(
            e, where="analyze_email", user_email=user_info.get("email")
        )


@router.post("/analyze-issue")
async def analyze_issue(request: IssueAnalyzeRequest, user_info: dict = Depends(verify_access_token)):
    """Analyzes GitHub issue content and returns structured checklist details."""
    try:
        result = automation_service.analyze_github_issue(
            title=request.title,
            body=request.body
        )
        return result
    except InferenceBudgetExceededException as ge:
        raise ge
    except HTTPException:
        raise
    except Exception as e:
        raise http_internal_error(
            e, where="analyze_issue", user_email=user_info.get("email")
        )
