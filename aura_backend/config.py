import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    port: int = 8000
    gemini_api_key: str = ""
    qdrant_host: str = ""
    qdrant_api_key: str = ""

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"

settings = Settings()
