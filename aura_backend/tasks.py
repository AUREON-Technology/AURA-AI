import asyncio
import logging
import json
import os
from celery_app import celery_app
from config import settings
from services.rag import rag_service
from services.learning_pipeline import learning_pipeline, _atomic_write

logger = logging.getLogger("aura_backend.tasks")

@celery_app.task(name="tasks.ingest_document_task")
def ingest_document_task(doc_id: str, content: str, metadata: dict) -> bool:
    """Task to ingest plain text document into Qdrant vector store in the background."""
    logger.info(f"Celery task: Ingesting document {doc_id} in background...")
    try:
        success = rag_service.ingest_document(doc_id=doc_id, content=content, metadata=metadata)
        return success
    except Exception as e:
        logger.error(f"Failed to ingest document in task: {e}")
        return False

@celery_app.task(name="tasks.ingest_pdf_task")
def ingest_pdf_task(file_id: str, file_name: str, google_access_token: str, user_id: str) -> bool:
    """Task to download a Google Drive PDF and ingest it into Qdrant in the background."""
    logger.info(f"Celery task: Ingesting Google Drive PDF '{file_name}' ({file_id}) for user {user_id}...")
    try:
        from services.drive import download_drive_file
        pdf_bytes = download_drive_file(google_access_token, file_id)
        if not pdf_bytes:
            logger.error(f"Failed to download PDF {file_id} from Google Drive.")
            return False
        
        metadata = {
            "source": "google_drive", 
            "file_name": file_name, 
            "file_id": file_id, 
            "user_id": user_id
        }
        success = rag_service.ingest_pdf(doc_id=file_id, pdf_bytes=pdf_bytes, metadata=metadata)
        return success
    except Exception as e:
        logger.error(f"Failed to ingest PDF in task: {e}")
        return False

@celery_app.task(name="tasks.build_lora_dataset_task")
def build_lora_dataset_task(user_id: str) -> dict:
    """Task to build the LoRA dataset in the background."""
    logger.info(f"Celery task: Building LoRA dataset in background...")
    try:
        dataset = learning_pipeline.build_lora_dataset("alpaca")
        # Written under settings.data_dir, not the container-local cwd — the
        # Celery worker and the API run in *separate containers*, so a file
        # written to either one's cwd is invisible to the other and is lost on
        # the next redeploy. Only a shared mounted volume makes this artifact
        # retrievable at all.
        filename = os.path.join(settings.data_dir, "train_lora.json")
        _atomic_write(filename, dataset)
        return {"status": "success", "total_records": len(dataset), "file_saved": filename}
    except Exception as e:
        logger.error(f"Failed to build LoRA dataset in task: {e}")
        return {"status": "error", "message": str(e)}


async def _generate_insights_for_all_users() -> int:
    from sqlalchemy import select
    from infrastructure.db.base import get_session_factory, is_configured
    from infrastructure.db.models import User
    from services.insights import generate_insights_for_user

    if not is_configured():
        logger.info("Skipping insights generation: DATABASE_URL not configured.")
        return 0

    session_factory = get_session_factory()
    count = 0
    async with session_factory() as db:
        users = (await db.execute(select(User))).scalars().all()
        for user in users:
            try:
                await generate_insights_for_user(db, user.id)
                count += 1
            except Exception as e:
                logger.error(f"Insight generation failed for user {user.id}: {e}")
        await db.commit()
    return count


@celery_app.task(name="tasks.generate_insights_for_all_users_task")
def generate_insights_for_all_users_task() -> dict:
    """Beat-scheduled wrapper bridging Celery's sync task execution into
    the async SQLAlchemy service (services/insights.py) — each run gets
    its own event loop, same pattern as any sync-context-calls-async-code
    integration."""
    logger.info("Celery task: Generating insights for all users...")
    try:
        count = asyncio.run(_generate_insights_for_all_users())
        return {"status": "success", "users_processed": count}
    except Exception as e:
        logger.error(f"Failed to generate insights for all users: {e}")
        return {"status": "error", "message": str(e)}
