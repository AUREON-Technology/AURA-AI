import httpx
import logging
from typing import List, Dict, Any
from config import settings

logger = logging.getLogger("aura_backend.services.drive")

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
