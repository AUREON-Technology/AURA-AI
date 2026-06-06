import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    port: int = 8000
    gemini_api_key: str = ""
    qdrant_host: str = ""
    qdrant_api_key: str = ""
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "llama3"
    local_embedding_model: str = "all-MiniLM-L6-v2"
    allowed_origins: str = "http://localhost,http://127.0.0.1"
    
    # Production Authentication Settings
    google_client_id: str = "87789110323-5picq501c0834g6g0amjqqpk628jaoh2.apps.googleusercontent.com"
    firebase_service_account_path: str = "credentials.json"
    firebase_credentials_json: str = ""
    debug_mode: bool = False
    allow_mock_attestation: bool = True

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"

settings = Settings()

