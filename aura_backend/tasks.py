import asyncio
import logging
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
def ingest_pdf_task(file_id: str, file_name: str, local_path: str, user_id: str) -> bool:
    """Ingest a Drive PDF already downloaded into the shared workspace.

    ``local_path`` is a file under ``settings.data_dir``. The Google OAuth token
    and the PDF bytes must never appear on the Redis broker.
    """
    logger.info(f"Celery task: Ingesting Google Drive PDF '{file_name}' ({file_id}) for user {user_id}...")
    workspace = os.path.abspath(settings.data_dir)
    resolved = os.path.abspath(local_path)
    if resolved != workspace and not resolved.startswith(workspace + os.sep):
        logger.error(f"Refusing ingest path outside workspace: {local_path}")
        return False

    try:
        with open(resolved, "rb") as handle:
            pdf_bytes = handle.read()
        if not pdf_bytes:
            logger.error(f"Staged PDF {file_id} is empty.")
            return False

        metadata = {
            "source": "google_drive",
            "file_name": file_name,
            "file_id": file_id,
            "user_id": user_id,
        }
        return rag_service.ingest_pdf(doc_id=file_id, pdf_bytes=pdf_bytes, metadata=metadata)
    except Exception as e:
        logger.error(f"Failed to ingest PDF in task: {e}")
        return False
    finally:
        try:
            os.remove(resolved)
        except OSError as exc:
            logger.warning(f"Could not delete staged PDF {resolved}: {exc}")

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


@celery_app.task(name="tasks.generate_insights_for_all_users_task", queue="realtime")
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


_worker_container = None
_worker_started = False


async def _ensure_worker_container():
    """Build the v3 container once per worker process.

    Gateway workers already call ``build_container()``; Celery workers did not,
    so refresh/consolidate had nowhere to run.
    """
    global _worker_container, _worker_started
    from infrastructure.secret_hydration import hydrate_secrets_from_files
    from aura_core.config import load_settings
    from aura_core.container import build_container

    hydrate_secrets_from_files()
    if _worker_container is None:
        _worker_container = build_container(load_settings(refresh=True))
    if not _worker_started:
        await _worker_container.startup()
        _worker_started = True
    return _worker_container


async def _memory_owner_ids(container) -> list:
    from aura_core.domain.identifiers import UserId

    store = container.memory_store
    pool = getattr(store, "_pool", None)
    if pool is not None:
        pg = await pool._get_pool()
        async with pg.acquire() as conn:
            rows = await conn.fetch(
                "SELECT DISTINCT owner_id FROM aura_memories "
                "WHERE owner_id IS NOT NULL AND owner_id <> ''"
            )
        return [UserId(row["owner_id"]) for row in rows]
    records = getattr(store, "_records", None)
    if records:
        seen = {record.owner_id for record in records.values() if record.owner_id}
        return list(seen)
    return []


async def _run_knowledge_refresh() -> dict:
    from aura_core.learning.jobs import run_knowledge_refresh

    container = await _ensure_worker_container()
    return await run_knowledge_refresh(
        container.refresh_pipeline,
        enabled=container.settings.learning.refresh_enabled,
    )


async def _run_memory_consolidate() -> dict:
    from aura_core.learning.jobs import run_memory_consolidate

    container = await _ensure_worker_container()
    owners = await _memory_owner_ids(container)
    return await run_memory_consolidate(
        container.memory,
        owners,
        enabled=container.settings.memory.consolidation_enabled,
    )


@celery_app.task(name="tasks.knowledge_refresh_task", queue="learning")
def knowledge_refresh_task() -> dict:
    """02:00 UTC knowledge refresh (ADR-008). Empty registry is a successful no-op."""
    logger.info("Celery task: Running knowledge refresh...")
    try:
        return asyncio.run(_run_knowledge_refresh())
    except Exception as e:
        logger.error("Knowledge refresh task failed: %s", e, exc_info=True)
        return {"status": "error", "error": type(e).__name__}


@celery_app.task(name="tasks.memory_consolidate_task", queue="learning")
def memory_consolidate_task() -> dict:
    """Daily memory consolidation at 02:30 UTC, after refresh."""
    logger.info("Celery task: Consolidating memories...")
    try:
        return asyncio.run(_run_memory_consolidate())
    except Exception as e:
        logger.error("Memory consolidate task failed: %s", e, exc_info=True)
        return {"status": "error", "error": type(e).__name__}
