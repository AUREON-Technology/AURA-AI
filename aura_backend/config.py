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

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"

settings = Settings()
