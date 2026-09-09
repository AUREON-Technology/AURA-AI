import os
import json
import logging
from contextvars import ContextVar
from typing import Optional, Dict, Any
from config import debug_bypass_allowed, settings
from googleapiclient.discovery import build
from google.oauth2 import service_account
from infrastructure.redis_client import get_redis_client

logger = logging.getLogger("aura_backend.security")

# Shared connection (infrastructure/redis_client.py) used for attestation
# nonce replay-protection storage.
redis_client = get_redis_client()

_local_nonce_cache = set()
_apple_app_attest_client = None
_warned_no_redis_nonce = False
attestation_failure_reason: ContextVar[str] = ContextVar(
    "attestation_failure_reason", default="unknown"
)


def _get_apple_app_attest_client():
    if _apple_app_attest_client is not None:
        return _apple_app_attest_client
    from services.attestation.apple_client import default_apple_client
    return default_apple_client()

def _get_play_integrity_service():
    """Builds and returns the Google Play Integrity API client service using credentials.json."""
    creds = None
    if settings.firebase_credentials_json:
        try:
            info = json.loads(settings.firebase_credentials_json)
            creds = service_account.Credentials.from_service_account_info(info)
        except Exception as e:
            logger.error(f"Error loading Play Integrity credentials from env: {e}")
            
    if not creds and settings.firebase_service_account_path and os.path.exists(settings.firebase_service_account_path):
        try:
            creds = service_account.Credentials.from_service_account_file(settings.firebase_service_account_path)
        except Exception as e:
            logger.error(f"Error loading Play Integrity credentials from file: {e}")

    if not creds and os.path.exists("credentials.json"):
        try:
            creds = service_account.Credentials.from_service_account_file("credentials.json")
        except Exception as e:
            logger.error(f"Error loading Play Integrity credentials from credentials.json: {e}")

    if not creds and os.path.exists("../credentials.json"):
        try:
            creds = service_account.Credentials.from_service_account_file("../credentials.json")
        except Exception as e:
            logger.error(f"Error loading Play Integrity credentials from ../credentials.json: {e}")

    if creds:
        try:
            scoped_creds = creds.with_scopes(["https://www.googleapis.com/auth/playintegrity"])
            return build("playintegrity", "v1", credentials=scoped_creds)
        except Exception as e:
            logger.error(f"Failed to build Play Integrity API client: {e}")
    return None


def _reject_attestation(reason: str) -> bool:
    attestation_failure_reason.set(reason)
    return False


def store_nonce(value: str, ttl_seconds: int = 300) -> None:
    """Records a server-issued attestation nonce as usable exactly once.

    Closes the gap where verify_device_integrity's nonce-replay check below
    was fully built (Redis-backed single-use lookup, Play Integrity/App
    Attest cross-checks) but never had anything to check against: nothing
    ever wrote a `nonce:{value}` key, so `expected_nonce` was never truthy
    for a real client and a captured attestation could be replayed
    indefinitely. Called from the new POST /api/v3/auth/nonce endpoint
    (api/v2/auth.py) before the value is handed to a client.
    """
    global _warned_no_redis_nonce
    client = get_redis_client()
    if client is not None:
        client.setex(f"nonce:{value}", ttl_seconds, "1")
        return
    if not _warned_no_redis_nonce:
        logger.warning(
            "Redis unavailable: attestation nonces are stored in-process only "
            "(lost on restart, not shared across workers)."
        )
        _warned_no_redis_nonce = True
    _local_nonce_cache.add(value)


def verify_device_integrity(attestation_token: Optional[str], platform: Optional[str], expected_nonce: Optional[str] = None) -> bool:
    """
    Verifies Android Play Integrity or iOS AppAttest/DeviceCheck tokens.
    In debug_mode or testing, bypasses check.
    In production, attestation is strictly evaluated.
    """
    is_testing = debug_bypass_allowed(settings.debug_mode)
    if is_testing:
        logger.info("Device integrity bypass: running in testing/debug mode.")
        attestation_failure_reason.set("ok")
        return True

    # Explicit debug marker from current clients, plus legacy mock strings
    # from older APKs. `aura_attestation_unsupported` is never listed — that
    # is the release-build "we have no Play Integrity SDK" marker.
    if settings.allow_mock_attestation and attestation_token in [
        "aura_dev_attestation",
        "android_play_integrity_mock_assertion_token_string",
        "ios_app_attest_mock_assertion_token_string",
        "windows_hello_mock_attestation_token",
        "macos_device_mock_attestation_token",
        "linux_desktop_mock_attestation_token",
        "local_developer_trust_token",
    ]:
        logger.info("Device integrity bypass: allowed configured debug/mock attestation token")
        attestation_failure_reason.set("ok")
        return True
        
    if not attestation_token:
        logger.warning("Production mode: missing attestation token header.")
        return _reject_attestation("missing_token")

    # Nonce Replay Protection validation
    if expected_nonce:
        if redis_client:
            nonce_key = f"nonce:{expected_nonce}"
            if not redis_client.exists(nonce_key):
                logger.warning("Attestation failure: nonce is invalid or has already been used.")
                return _reject_attestation("nonce_replay")
            # Single-use nonce: remove to prevent replay attacks
            redis_client.delete(nonce_key)
        else:
            if expected_nonce not in _local_nonce_cache:
                logger.warning("Attestation failure: nonce not found in local cache.")
                return _reject_attestation("nonce_replay")
            _local_nonce_cache.remove(expected_nonce)

    try:
        if platform == "android":
            logger.info("Verifying Android Play Integrity Token...")
            
            # Try Google Play Integrity API for cryptographic validation.
            # If the API is unavailable or the call fails, refuse — never decode
            # an unsigned JWS locally (that accepts a crafted payload).
            service = _get_play_integrity_service()
            if not service:
                logger.warning(
                    "Android Integrity failure: Play Integrity API is unavailable."
                )
                return _reject_attestation("play_integrity_unavailable")

            try:
                logger.info("Calling Google Play Integrity API for cryptographic verification...")
                result = service.v1().decodeIntegrityToken(
                    packageName=settings.android_package_name,
                    body={"integrityToken": attestation_token}
                ).execute()
                payload = result.get("tokenPayloadExternal", {})
                logger.info("Google Play Integrity API cryptographic verification call succeeded.")
            except Exception as api_err:
                logger.warning(
                    f"Google Play Integrity API cryptographic verification call failed: {api_err}."
                )
                return _reject_attestation("play_integrity_decode")

            if not payload:
                logger.warning("Android Integrity failure: empty Play Integrity payload.")
                return _reject_attestation("empty_payload")

            # Check app integrity package
            request_details = payload.get("requestDetails", {})
            pkg_name = request_details.get("packageName")
            token_nonce = request_details.get("nonce")

            if pkg_name != settings.android_package_name:
                logger.warning(f"Play Integrity error: package name mismatch '{pkg_name}'")
                return _reject_attestation("package_mismatch")

            if expected_nonce and token_nonce != expected_nonce:
                logger.warning(f"Play Integrity error: nonce mismatch '{token_nonce}' != expected '{expected_nonce}'")
                return _reject_attestation("nonce_mismatch")

            # Check device integrity verdicts
            device_integrity = payload.get("deviceIntegrity", {})
            verdicts = device_integrity.get("deviceRecognitionVerdict", [])
            
            if not any(v in verdicts for v in ["MEETS_STRONG_INTEGRITY", "MEETS_DEVICE_INTEGRITY", "MEETS_VIRTUAL_INTEGRITY"]):
                logger.warning(f"Play Integrity error: device recognition failure verdicts {verdicts}")
                return _reject_attestation("device_verdict")

            logger.info("Android Play Integrity verification successful.")
            attestation_failure_reason.set("ok")
            return True


        elif platform == "ios":
            logger.info("Verifying iOS App Attest token...")
            from services.attestation.app_attest import verify_app_attest

            ok = verify_app_attest(
                attestation_token,
                bundle_id=settings.ios_bundle_id,
                team_id=settings.apple_team_id,
                apple_client=_get_apple_app_attest_client(),
                expected_nonce=expected_nonce,
            )
            if not ok:
                return _reject_attestation("ios_attest")
            attestation_failure_reason.set("ok")
            return True

        else:
            logger.warning(f"Unsupported device validation platform: {platform}")
            return _reject_attestation("unsupported_platform")
            
    except Exception as e:
        logger.error(f"Device validation parsing error: {e}")
        return _reject_attestation("parse_error")
import anyio
from fastapi import Request, Security, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests

from config import settings

logger = logging.getLogger("aura_backend.security")

# HTTP Bearer scheme helper
security_scheme = HTTPBearer(auto_error=False)

# Optional Firebase Admin SDK initialization
firebase_app = None
try:
    import firebase_admin
    from firebase_admin import credentials, auth
    
    # Load Firebase credentials
    cred = None
    if settings.firebase_credentials_json:
        try:
            cred_dict = json.loads(settings.firebase_credentials_json)
            cred = credentials.Certificate(cred_dict)
            logger.info("Loaded Firebase credentials from JSON environment variable.")
        except Exception as json_err:
            logger.error(f"Failed to parse firebase_credentials_json: {json_err}")
            
    elif settings.firebase_service_account_path and os.path.exists(settings.firebase_service_account_path):
        cred = credentials.Certificate(settings.firebase_service_account_path)
        logger.info(f"Loaded Firebase credentials from file: {settings.firebase_service_account_path}")
        
    elif os.path.exists("../credentials.json"):
        # Fallback to parent directory credentials.json if exists
        cred = credentials.Certificate("../credentials.json")
        logger.info("Loaded Firebase credentials from parent credentials.json")

    if cred:
        firebase_app = firebase_admin.initialize_app(cred)
        logger.info("Firebase Admin SDK successfully initialized.")
    else:
        logger.warning("No Firebase Service Account credentials found. Firebase Auth verification is disabled.")
except ImportError:
    logger.warning("firebase-admin package is not installed. Firebase ID token verification will be skipped.")
except Exception as init_err:
    logger.error(f"Error during Firebase Admin SDK initialization: {init_err}")


async def verify_identity_token(
    request: Request,
    authorization: Optional[HTTPAuthorizationCredentials] = Security(security_scheme),
) -> Dict[str, Any]:
    """Verify a Google or Firebase ID token. Does **not** check device attestation.

    Used by the operator web console. Mobile session exchange must go through
    :func:`verify_token`, which still requires Play Integrity / App Attest.
    """
    is_testing = debug_bypass_allowed(settings.debug_mode)

    if not authorization:
        if is_testing:
            logger.debug("No auth header provided, returning mock user in testing/debug mode.")
            return {
                "uid": "mock_user_123",
                "email": "mock_user@aura.ai",
                "name": "Mock Developer",
                "picture": "https://api.dicebear.com/7.x/bottts/png?seed=mock",
            }
        raise HTTPException(
            status_code=401,
            detail="Authorization header is required.",
        )

    token = authorization.credentials

    try:
        # `audience` deliberately omitted here: verify_oauth2_token only
        # accepts a single string, but a valid ID token may carry either the
        # mobile/web client ID or the desktop loopback client ID as `aud`
        # (see Settings.google_desktop_client_id). Signature/issuer/expiry
        # are still fully verified by the library; the audience is checked
        # explicitly below against the allowlist instead.
        id_info = await anyio.to_thread.run_sync(
            lambda: id_token.verify_oauth2_token(
                token,
                google_requests.Request(),
            )
        )
        accepted_audiences = {settings.google_client_id}
        if settings.google_desktop_client_id:
            accepted_audiences.add(settings.google_desktop_client_id)
        if id_info.get("aud") not in accepted_audiences:
            raise ValueError(f"Unrecognized token audience: {id_info.get('aud')}")
        logger.debug(f"Google ID token verified successfully for: {id_info.get('email')}")
        return {
            "uid": id_info.get("sub"),
            "email": id_info.get("email"),
            "name": id_info.get("name"),
            "picture": id_info.get("picture"),
        }
    except Exception as google_err:
        logger.warning(f"Token is not a valid Google ID token: {google_err}. Trying Firebase...")

    if firebase_app:
        try:
            from firebase_admin import auth
            decoded_token = await anyio.to_thread.run_sync(
                auth.verify_id_token,
                token,
            )
            logger.debug(f"Firebase ID token verified successfully for: {decoded_token.get('email')}")
            return {
                "uid": decoded_token.get("uid"),
                "email": decoded_token.get("email"),
                "name": decoded_token.get("name"),
                "picture": decoded_token.get("picture"),
            }
        except Exception as firebase_err:
            logger.error(f"Firebase verification failed: {firebase_err}")

    if is_testing and token == "mock_token":
        return {
            "uid": "mock_user_123",
            "email": "mock_user@aura.ai",
            "name": "Mock Developer",
            "picture": "https://api.dicebear.com/7.x/bottts/png?seed=mock",
        }

    raise HTTPException(
        status_code=401,
        detail="Invalid or expired credentials.",
    )


async def verify_token(
    request: Request,
    authorization: Optional[HTTPAuthorizationCredentials] = Security(security_scheme)
) -> Dict[str, Any]:
    """
    Decodes and verifies JWT Bearer tokens from incoming requests.
    Supports:
    1. Google Sign-In ID tokens (via google-auth)
    2. Firebase ID tokens (via firebase-admin)
    3. Mock authentication in debug/test mode

    Device attestation is required here. Browser operators use /api/v3/auth/web/token.
    """
    attestation_token = request.headers.get("X-Device-Attestation")
    platform = request.headers.get("X-Device-Platform")
    attestation_nonce = request.headers.get("X-Device-Attestation-Nonce")
    if not verify_device_integrity(attestation_token, platform, attestation_nonce):
        raise HTTPException(
            status_code=403,
            detail=(
                "Device attestation validation failed. "
                f"({attestation_failure_reason.get()})"
            ),
        )

    return await verify_identity_token(request, authorization)
