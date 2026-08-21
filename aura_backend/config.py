import os

from pydantic_settings import BaseSettings


def native_ollama_base_url(configured_host: str = "") -> str:
    """Native Ollama API origin (no OpenAI ``/v1`` suffix).

    Production compose already sets ``AURA_PROVIDER_LOCAL_BASE_URL`` to the
    OpenAI-compatible endpoint on the compose service (``http://ollama:11434/v1``).
    The legacy client and ``/api/tags`` health probe talk to the native API, so
    that URL is reused with ``/v1`` stripped rather than falling back to
    localhost — which is unreachable from a Docker service.
    """
    provider = os.environ.get("AURA_PROVIDER_LOCAL_BASE_URL", "").strip()
    if provider:
        host = provider.rstrip("/")
        if host.endswith("/v1"):
            host = host[:-3].rstrip("/")
        return host
    host = (configured_host or "").strip() or "http://localhost:11434"
    return host.rstrip("/")


class Settings(BaseSettings):
    port: int = 8000
    # `gemini_api_key` used to live here. Removed with the provider: a settings field for a
    # banned API is an invitation to wire it back up, and the /health endpoint was reporting
    # `gemini: {configured: ...}` off the back of it long after nothing read the value.
    qdrant_host: str = ""
    qdrant_api_key: str = ""
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "llama3"
    local_embedding_model: str = "all-MiniLM-L6-v2"
    # Native Android/iOS ignore CORS. Flutter web (local :8080 and production
    # https://$AURA_DOMAIN) does not. Compose overrides this with ALLOWED_ORIGINS.
    allowed_origins: str = (
        "http://localhost,http://127.0.0.1,"
        "http://localhost:8080,http://127.0.0.1:8080"
    )
    
    # Production Authentication Settings
    google_client_id: str = "87789110323-5picq501c0834g6g0amjqqpk628jaoh2.apps.googleusercontent.com"
    # Must match aura_client/android/app/build.gradle.kts `applicationId`.
    # Play Integrity rejects any token whose requestDetails.packageName differs,
    # so a drift between these two values silently breaks all Android auth —
    # single-sourced here rather than hardcoded at the call sites.
    android_package_name: str = "com.auraai.aura_client"
    firebase_service_account_path: str = "credentials.json"
    firebase_credentials_json: str = ""
    debug_mode: bool = False
    # Default to False for production safety; enable explicitly via .env for
    # local development/testing (tests already override this per-case).
    allow_mock_attestation: bool = False

    # V2: Postgres is the relational source of truth for tasks/calendar/
    # finance/health/memories (see Doc/V2_BLUEPRINT.md §6). Empty by default
    # so the app still boots without Postgres configured — the /api/v2/sync
    # router degrades to a clear 503 rather than crashing at import time.
    database_url: str = ""

    # V2: signs access/refresh JWTs issued by /api/v2/auth/token (§9).
    # MUST be set to a stable, random value in production — if left empty,
    # services/jwt_auth.py generates a random one at process startup as a
    # dev-mode convenience, which invalidates every session on each
    # restart/redeploy (acceptable for local dev, not for production).
    jwt_secret: str = ""

    # Writable directory for the JSON-file state the learning pipeline keeps
    # (feedback logs, compiled LoRA datasets, telemetry). Defaults to the app
    # working directory, which is INSIDE the container image — anything written
    # there is destroyed by `docker compose up --force-recreate`. Point this at
    # a mounted volume in any deployment where the feedback corpus matters.
    data_dir: str = "."

    # V2 observability (§10). Both no-op when unset — safe defaults for
    # local dev, wire real values in the target deploy environment.
    sentry_dsn: str = ""
    otel_exporter_otlp_endpoint: str = ""
    otel_service_name: str = "aura-backend"
    log_format: str = "text"  # "json" for structured logs (Cloud Run/any log aggregator)

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"

settings = Settings()

