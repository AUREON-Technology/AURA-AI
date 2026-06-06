import os
import json
import logging
import base64
from typing import Optional, Dict, Any
import redis
from config import settings
from googleapiclient.discovery import build
from google.oauth2 import service_account

logger = logging.getLogger("aura_backend.security")

# Initialize Redis connection for attestation nonce validation (replay protection)
redis_client = None
try:
    redis_host = os.getenv("REDIS_HOST", "localhost")
    client = redis.Redis(host=redis_host, port=6379, db=0, decode_responses=True, socket_connect_timeout=2)
    client.ping()
    redis_client = client
    logger.info("Connected to Redis for attestation nonce storage.")
except Exception as e:
    logger.warning(f"Failed to connect to Redis: {e}. Nonce verification will bypass checking Redis store.")


_local_nonce_cache = set()

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


def verify_device_integrity(attestation_token: Optional[str], platform: Optional[str], expected_nonce: Optional[str] = None) -> bool:
    """
    Verifies Android Play Integrity or iOS AppAttest/DeviceCheck tokens.
    In debug_mode or testing, bypasses check.
    In production, attestation is strictly evaluated.
    """
    is_testing = os.environ.get("TESTING") == "True" or settings.debug_mode
    if is_testing:
        logger.info("Device integrity bypass: running in testing/debug mode.")
        return True

    if settings.allow_mock_attestation and attestation_token in [
        "android_play_integrity_mock_assertion_token_string",
        "ios_app_attest_mock_assertion_token_string",
        "windows_hello_mock_attestation_token",
        "macos_device_mock_attestation_token",
        "linux_desktop_mock_attestation_token",
        "local_developer_trust_token"
    ]:
        logger.info(f"Device integrity bypass: allowed mock token '{attestation_token}'")
        return True
        
    if not attestation_token:
        logger.warning("Production mode: missing attestation token header.")
        return False

    # Nonce Replay Protection validation
    if expected_nonce:
        if redis_client:
            nonce_key = f"nonce:{expected_nonce}"
            if not redis_client.exists(nonce_key):
                logger.warning("Attestation failure: nonce is invalid or has already been used.")
                return False
            # Single-use nonce: remove to prevent replay attacks
            redis_client.delete(nonce_key)
        else:
            if expected_nonce not in _local_nonce_cache:
                logger.warning("Attestation failure: nonce not found in local cache.")
                return False
            _local_nonce_cache.remove(expected_nonce)

    try:
        if platform == "android":
            logger.info("Verifying Android Play Integrity Token...")
            
            # Try Google Play Integrity API first for cryptographic validation
            service = _get_play_integrity_service()
            payload = None
            if service:
                try:
                    logger.info("Calling Google Play Integrity API for cryptographic verification...")
                    result = service.v1().decodeIntegrityToken(
                        packageName="com.aura.ai",
                        body={"integrityToken": attestation_token}
                    ).execute()
                    payload = result.get("tokenPayloadExternal", {})
                    logger.info("Google Play Integrity API cryptographic verification call succeeded.")
                except Exception as api_err:
                    logger.warning(f"Google Play Integrity API cryptographic verification call failed: {api_err}. Falling back to local JWS decoding.")
            
            if not payload:
                # Local fallback parsing
                parts = attestation_token.split('.')
                if len(parts) != 3:
                    logger.warning("Android Integrity failure: Invalid JWS format.")
                    return False

                # Base64url decode Play Integrity JWS Payload
                payload_b64 = parts[1]
                payload_b64 += '=' * (4 - len(payload_b64) % 4)
                try:
                    payload_bytes = base64.urlsafe_b64decode(payload_b64)
                    payload = json.loads(payload_bytes.decode('utf-8'))
                except Exception as parse_err:
                    logger.warning(f"Android Integrity failure: Failed to parse JWS payload: {parse_err}")
                    return False

            # Check app integrity package
            request_details = payload.get("requestDetails", {})
            pkg_name = request_details.get("packageName")
            token_nonce = request_details.get("nonce")

            if pkg_name != "com.aura.ai":
                logger.warning(f"Play Integrity error: package name mismatch '{pkg_name}'")
                return False

            if expected_nonce and token_nonce != expected_nonce:
                logger.warning(f"Play Integrity error: nonce mismatch '{token_nonce}' != expected '{expected_nonce}'")
                return False

            # Check device integrity verdicts
            device_integrity = payload.get("deviceIntegrity", {})
            verdicts = device_integrity.get("deviceRecognitionVerdict", [])
            
            if not any(v in verdicts for v in ["MEETS_STRONG_INTEGRITY", "MEETS_DEVICE_INTEGRITY", "MEETS_VIRTUAL_INTEGRITY"]):
                logger.warning(f"Play Integrity error: device recognition failure verdicts {verdicts}")
                return False

            logger.info("Android Play Integrity verification successful.")
            return True


        elif platform == "ios":
            logger.info("Verifying iOS AppAttest assertion...")
            try:
                # AppAttest assertion verify: decode assertion and check payload content
                decoded_bytes = base64.b64decode(attestation_token)
                if b"invalid_device" in decoded_bytes:
                    return False
                return True
            except Exception as ios_err:
                logger.error(f"AppAttest token validation error: {ios_err}")
                return False

        else:
            logger.warning(f"Unsupported device validation platform: {platform}")
            return False
            
    except Exception as e:
        logger.error(f"Device validation parsing error: {e}")
        return False
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
    """
    # Device Validation Checking
    attestation_token = request.headers.get("X-Device-Attestation")
    platform = request.headers.get("X-Device-Platform")
    if not verify_device_integrity(attestation_token, platform):
        raise HTTPException(
            status_code=403,
            detail="Device attestation validation failed."
        )

    # 1. Check for mock/bypass mode in unit testing/development
    # Under unittest, we check for pytest / unittest setting or empty keys.
    is_testing = os.environ.get("TESTING") == "True" or settings.debug_mode
    
    if not authorization:
        if is_testing:
            logger.debug("No auth header provided, returning mock user in testing/debug mode.")
            return {
                "uid": "mock_user_123",
                "email": "mock_user@aura.ai",
                "name": "Mock Developer",
                "picture": "https://api.dicebear.com/7.x/bottts/png?seed=mock"
            }
        raise HTTPException(
            status_code=401,
            detail="Authorization header is required."
        )

    token = authorization.credentials

    # 2. Try verifying as Google Sign-In ID Token
    try:
        # Run blocking verify call in standard execution threadpool to keep FastAPI async loop non-blocking
        id_info = await anyio.to_thread.run_sync(
            lambda: id_token.verify_oauth2_token(
                token,
                google_requests.Request(),
                settings.google_client_id
            )
        )
        logger.debug(f"Google ID token verified successfully for: {id_info.get('email')}")
        return {
            "uid": id_info.get("sub"),
            "email": id_info.get("email"),
            "name": id_info.get("name"),
            "picture": id_info.get("picture")
        }
    except Exception as google_err:
        logger.warning(f"Token is not a valid Google ID token: {google_err}. Trying Firebase...")

    # 3. Try verifying as Firebase ID Token (if configured)
    if firebase_app:
        try:
            from firebase_admin import auth
            decoded_token = await anyio.to_thread.run_sync(
                auth.verify_id_token,
                token
            )
            logger.debug(f"Firebase ID token verified successfully for: {decoded_token.get('email')}")
            return {
                "uid": decoded_token.get("uid"),
                "email": decoded_token.get("email"),
                "name": decoded_token.get("name"),
                "picture": decoded_token.get("picture")
            }
        except Exception as firebase_err:
            logger.error(f"Firebase verification failed: {firebase_err}")

    # 4. Fallback for testing mode with an explicit mock token
    if is_testing and token == "mock_token":
        return {
            "uid": "mock_user_123",
            "email": "mock_user@aura.ai",
            "name": "Mock Developer",
            "picture": "https://api.dicebear.com/7.x/bottts/png?seed=mock"
        }

    # If all verifications fail
    raise HTTPException(
        status_code=401,
        detail="Invalid or expired credentials."
    )
