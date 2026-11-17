"""Learning pipeline HTTP surface — remounted under ``/api/v3/learning``."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.http_errors import http_internal_error
from services.learning_pipeline import learning_pipeline
from services.rbac import require_role
from services.jwt_auth import verify_access_token

router = APIRouter(prefix="/api/v3/learning", tags=["learning"])


class LearningLogSchema(BaseModel):
    id: str
    prompt: str
    response: str
    rating: int
    correction: Optional[str] = None
    model: Optional[str] = None
    timestamp: str


@router.post("/log")
async def log_learning_record(
    request: LearningLogSchema, user_info: dict = Depends(require_role("Admin"))
):
    """Adds one curated record to the training corpus (INT-039).

    Admin only, and stamped with the caller. It used to accept any signed-in user's arbitrary
    prompt/response pairs, unattributed, straight into the data ``/export`` and
    ``/build_dataset`` turn into a LoRA set -- a training-data poisoning inlet with no client
    caller at all (the app's own learning logs are local-only). Keeping it for operators rather
    than deleting it leaves the route the platform tests pin mounted, and ``submitted_by`` comes
    from the token, never the body: the schema has no such field, and extra keys are dropped.
    """
    record = request.model_dump()
    record["submitted_by"] = str(user_info.get("uid") or user_info.get("sub") or "")
    success = learning_pipeline.log_feedback_record(record)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to log learning record")
    return {"status": "success"}


@router.get("/export")
async def export_learning_dataset(format: str = "alpaca", user_info: dict = Depends(require_role("Admin"))):
    """Exports learning dataset in selected format (alpaca, chatml, openai)."""
    try:
        dataset = learning_pipeline.build_lora_dataset(format)
        return dataset
    except HTTPException:
        raise
    except Exception as e:
        raise http_internal_error(e, where="export_learning_dataset", user_email=user_info.get("email"))


@router.post("/build_dataset")
async def build_dataset(user_info: dict = Depends(require_role("Admin"))):
    """Processes learning logs and compiles local lora training datasets in the background."""
    try:
        from tasks import build_lora_dataset_task
        task = build_lora_dataset_task.delay(user_info["uid"])
        return {
            "status": "success",
            "message": "LoRA dataset build queued in background.",
            "task_id": task.id
        }
    except HTTPException:
        raise
    except Exception as e:
        raise http_internal_error(e, where="build_dataset", user_email=user_info.get("email"))


@router.get("/evaluate")
async def evaluate_dataset(user_info: dict = Depends(require_role("Developer"))):
    """Retrieves telemetry evaluation statistics regarding learning loops."""
    try:
        metrics = learning_pipeline.evaluate_metrics()
        return metrics
    except HTTPException:
        raise
    except Exception as e:
        raise http_internal_error(e, where="evaluate_dataset", user_email=user_info.get("email"))
