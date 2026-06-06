import logging
import json
from celery_app import celery_app
from services.rag import rag_service
from services.learning_pipeline import learning_pipeline

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
        filename = "train_lora.json"
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(dataset, f, indent=2, ensure_ascii=False)
        return {"status": "success", "total_records": len(dataset), "file_saved": filename}
    except Exception as e:
        logger.error(f"Failed to build LoRA dataset in task: {e}")
        return {"status": "error", "message": str(e)}
