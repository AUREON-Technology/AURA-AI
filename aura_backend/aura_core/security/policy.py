"""Security policy: centralized enforcement of all production security requirements.

This is the single source of truth for security configuration. Every adapter and
service should consult this policy before performing security-sensitive operations.
"""

from __future__ import annotations

from enum import Enum
import json
import time
import re
import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple, FrozenSet

# Self-contained error classes
class ValidationError(Exception):
    """Minimal validation error for security module self-containment."""
    pass


class AuthenticationError(ValidationError):
    """Raised when authentication fails."""
    pass


# -- SecurityViolation types --

class SecurityViolationType(str, Enum):
    """Types of security violations that can be logged."""
    UNAUTHORIZED_ACCESS = "unauthorized_access"
    INVALID_AUTHENTICATION = "invalid_authentication"
    RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"
    SECRET_EXPOSURE = "secret_exposure"
    SSRF_ATTEMPT = "ssrf_attempt"
    PROMPT_INJECTION = "prompt_injection"
    TOOL_INJECTION = "tool_injection"
    SANDBOX_ESCAPE = "sandbox_escape"
    CREDENTIAL_LEAK = "credential_leak"
    UNAUTHORIZED_OPERATION = "unauthorized_operation"


@dataclass
class SecurityViolation:
    """A recorded security violation event."""
    id: str
    violation_type: SecurityViolationType
    severity: str  # "low", "medium", "high", "critical"
    timestamp: float
    principal_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    action_taken: str | None = None
    compliance_ref: str | None = None


# -- SecurityPolicy --

@dataclass
class SecurityPolicy:
    """Central security policy configuration for production deployment.

    All settings are optional with sensible defaults. When infrastructure
    (Postgres, Redis, etc.) is unavailable, in-memory fallbacks are used.
    """

    # Authentication
    require_auth: bool = True
    auth_token_ttl_s: int = 3600
    auth_refresh_token_ttl_s: int = 2592000
    password_min_length: int = 12
    password_require_uppercase: bool = True
    password_require_lowercase: bool = True
    password_require_numbers: bool = True
    password_require_symbols: bool = True

    # Authorization
    enforce_role_based_access: bool = True
    admin_role: str = "admin"
    viewer_role: str = "viewer"
    editor_role: str = "editor"

    # Secrets management
    secret_encryption_key_rotation_days: int = 90
    secrets_never_log: bool = True
    allow_plaintext_secrets_in_env: bool = False

    # Rate limiting
    default_rate_limit_per_minute: int = 60
    auth_rate_limit_per_minute: int = 10
    endpoint_rate_limits: dict[str, int] = field(default_factory=dict)

    # SSRF protection
    ssrf_blocked_schemes: tuple[str, ...] = field(
        default_factory=lambda: ("file", "ftp", "smb", "smbsp", "nfs", "swift", "webdav")
    )
    ssrf_allowed_hosts: tuple[str, ...] = field(default_factory=tuple)
    ssrf_strict_host_validation: bool = True

    # Prompt injection defenses
    prompt_injection_enabled: bool = True
    prompt_injection_score_threshold: float = 0.7
    sanitize_user_inputs: bool = True
    max_prompt_length: int = 100000

    # Tool injection defenses
    tool_injection_enabled: bool = True
    sandbox_isolation_enabled: bool = True
    require_capability_grant: bool = True

    # Audit logging
    audit_log_enabled: bool = True
    audit_retention_days: int = 90
    log_security_events: bool = True

    # Encryption at rest
    encryption_at_rest_enabled: bool = True
    key_management_system: str | None = None

    # Data classification
    classify_sensitive_data: bool = True
    sensitive_data_types: tuple[str, ...] = field(
        default_factory=lambda: (
            "api_key", "access_token", "refresh_token", "secret", "password",
            "credit_card", "ssn", "personal_identifiable"
        )
    )

    def validate(self) -> list[str]:
        """Validate policy configuration and return list of warnings."""
        warnings: list[str] = []

        if self.require_auth and not self.ssrf_allowed_hosts:
            warnings.append("Auth enabled but SSRF allowed hosts empty - review network config")

        if self.secret_encryption_key_rotation_days < 30:
            warnings.append("Secret key rotation interval very short (< 30 days)")

        if self.default_rate_limit_per_minute < 1:
            warnings.append("Rate limit too restrictive, may cause availability issues")

        if self.prompt_injection_score_threshold < 0 or self.prompt_injection_score_threshold > 1:
            warnings.append("Prompt injection score threshold must be between 0 and 1")

        return warnings

    def to_dict(self) -> dict[str, Any]:
        return {
            "require_auth": self.require_auth,
            "auth_token_ttl_s": self.auth_token_ttl_s,
            "enforce_role_based_access": self.enforce_role_based_access,
            "secret_encryption_key_rotation_days": self.secret_encryption_key_rotation_days,
            "secrets_never_log": self.secrets_never_log,
            "default_rate_limit_per_minute": self.default_rate_limit_per_minute,
            "ssrf_blocked_schemes": self.ssrf_blocked_schemes,
            "prompt_injection_enabled": self.prompt_injection_enabled,
            "tool_injection_enabled": self.tool_injection_enabled,
            "audit_log_enabled": self.audit_log_enabled,
            "encryption_at_rest_enabled": self.encryption_at_rest_enabled,
        }