import httpx
import logging
import os
import re
import uuid
from typing import List, Dict, Any, Optional
from config import settings

logger = logging.getLogger("aura_backend.services.drive")

# Cap Drive downloads so a large file cannot fill the shared workspace or the
# API process. The PDF bytes stay in this process; Celery only receives a path.
MAX_DRIVE_DOWNLOAD_BYTES = 20 * 1024 * 1024
_SAFE_FILE_ID = re.compile(r"[^A-Za-z0-9_-]+")

def check_google_drive_configuration() -> dict:
    """Checks and reports the Google Drive configuration status at startup."""
    client_id = settings.google_client_id
    has_client_id = bool(client_id and client_id.endswith(".apps.googleusercontent.com"))
    
    # 1. Check if Drive API is enabled/discoverable
    drive_api_enabled = "No"
    try:
        response = httpx.get("https://www.googleapis.com/discovery/v1/apis/drive/v3/rest", timeout=5.0)
        if response.status_code == 200:
            drive_api_enabled = "Yes"
    except Exception as e:
        logger.warning(f"Google Drive API discovery check failed: {e}")
        
    # 2. Check if OAuth Credentials (Client ID) are valid format
    oauth_creds_valid = "Yes" if has_client_id else "No"
    
    # 3. Check if Required Scopes are configured
    required_scopes_present = "Yes" if has_client_id else "No"
    
    print("\n" + "="*50)
    print("AURA SYSTEM INITIALIZATION HEALTH CHECK")
    print(f"Google Drive API Enabled: {drive_api_enabled}")
    print(f"OAuth Credentials Valid: {oauth_creds_valid}")
    print(f"Required Scopes Present: {required_scopes_present}")
    print("="*50 + "\n")
    
    return {
        "drive_api_enabled": drive_api_enabled == "Yes",
        "oauth_creds_valid": oauth_creds_valid == "Yes",
        "required_scopes_present": required_scopes_present == "Yes"
    }

def search_drive_pdfs(access_token: str, query: str = "") -> List[Dict[str, Any]]:
    """Searches the user's Google Drive for PDF files matching the query."""
    headers = {"Authorization": f"Bearer {access_token}"}
    q_str = "mimeType = 'application/pdf'"
    if query:
        safe_query = query.replace("'", "\\'")
        q_str += f" and name contains '{safe_query}'"
    
    url = "https://www.googleapis.com/drive/v3/files"
    params = {
        "q": q_str,
        "fields": "files(id, name, mimeType)",
        "pageSize": 10
    }
    
    try:
        with httpx.Client() as client:
            response = client.get(url, headers=headers, params=params, timeout=10.0)
            if response.status_code == 200:
                files = response.json().get("files", [])
                logger.info(f"Drive search returned {len(files)} files for query '{query}'")
                return files
            else:
                logger.error(f"Google Drive search failed: {response.status_code} - {response.text}")
                return []
    except Exception as e:
        logger.error(f"Exception during Google Drive search: {e}")
        return []

def download_drive_file(
    access_token: str,
    file_id: str,
    *,
    max_bytes: int = MAX_DRIVE_DOWNLOAD_BYTES,
) -> bytes:
    """Downloads a file from Google Drive by ID, capped at ``max_bytes``."""
    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"https://www.googleapis.com/drive/v3/files/{file_id}"
    params = {"alt": "media"}

    try:
        with httpx.Client() as client:
            with client.stream("GET", url, headers=headers, params=params, timeout=60.0) as response:
                if response.status_code != 200:
                    logger.error(
                        f"Google Drive download failed: {response.status_code} - {response.read()[:200]!r}"
                    )
                    return b""
                content_length = response.headers.get("Content-Length")
                if content_length:
                    try:
                        if int(content_length) > max_bytes:
                            logger.error(
                                f"Google Drive file {file_id} exceeds size cap "
                                f"({content_length} > {max_bytes})"
                            )
                            return b""
                    except ValueError:
                        pass
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        logger.error(
                            f"Google Drive download of {file_id} exceeded size cap ({max_bytes})"
                        )
                        return b""
                    chunks.append(chunk)
                content = b"".join(chunks)
                logger.info(f"Successfully downloaded file {file_id} ({len(content)} bytes)")
                return content
    except Exception as e:
        logger.error(f"Exception during Google Drive download: {e}")
        return b""


def stage_drive_pdf(access_token: str, file_id: str) -> str:
    """Download a Drive PDF in-process and write it under the shared workspace.

    Returns the absolute path, or an empty string on failure. The Celery task
    must receive only this path — never the OAuth token and never the PDF bytes.
    """
    pdf_bytes = download_drive_file(access_token, file_id)
    if not pdf_bytes:
        return ""

    dest_dir = os.path.join(settings.data_dir, "drive_ingest")
    os.makedirs(dest_dir, exist_ok=True)
    safe_id = _SAFE_FILE_ID.sub("_", file_id)[:80] or "file"
    local_path = os.path.abspath(os.path.join(dest_dir, f"{safe_id}_{uuid.uuid4().hex}.pdf"))
    with open(local_path, "wb") as handle:
        handle.write(pdf_bytes)
    return local_path


def queue_drive_pdf_ingest(
    file_id: str, file_name: str, access_token: str, user_id: Optional[str]
) -> dict:
    """Stage a Drive PDF locally, then queue ingestion with a workspace path only."""
    local_path = stage_drive_pdf(access_token, file_id)
    if not local_path:
        return {"error": "Failed to download PDF from Google Drive."}
    from tasks import ingest_pdf_task

    task = ingest_pdf_task.delay(file_id, file_name, local_path, user_id or "global")
    return {
        "status": "queued",
        "message": (
            f"Successfully queued download and background ingestion of PDF '{file_name}'. "
            f"Task ID: {task.id}"
        ),
    }
