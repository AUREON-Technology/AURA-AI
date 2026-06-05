import httpx
import logging
from typing import List, Dict, Any

logger = logging.getLogger("aura_backend.services.drive")

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

def download_drive_file(access_token: str, file_id: str) -> bytes:
    """Downloads a file from Google Drive by ID."""
    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"https://www.googleapis.com/drive/v3/files/{file_id}"
    params = {"alt": "media"}
    
    try:
        with httpx.Client() as client:
            response = client.get(url, headers=headers, params=params, timeout=30.0)
            if response.status_code == 200:
                logger.info(f"Successfully downloaded file {file_id} ({len(response.content)} bytes)")
                return response.content
            else:
                logger.error(f"Google Drive download failed: {response.status_code} - {response.text}")
                return b""
    except Exception as e:
        logger.error(f"Exception during Google Drive download: {e}")
        return b""
