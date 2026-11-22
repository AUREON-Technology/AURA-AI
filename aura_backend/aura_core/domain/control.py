"""The console's Super-Admin control center: platforms, changes and step-up grants (ADR-037).

Stdlib only, like every domain module. Nothing here holds a secret value: a platform credential
is a ``secret_ref`` into the credential vault, a change records which variables it touched and
whether each was secret, never what it was set to, and a step-up grant is stored as a hash.
"""

from __future__ import annotations

import enum
import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

__all__ = [
    "ChangeRecord",
    "ChangeStatus",
    "Platform",
    "PlatformCredential",
    "StepUpGrant",
    "PROTECTED_VARIABLES",
    "SPACE_HARDWARE",
    "SECRET_NAME_PATTERN",
    "grant_digest",
    "is_secret_name",
    "is_secret_variable",
]


class Platform(str, enum.Enum):
    """What the control center can reach. Each needs one token, entered once in the console."""

    RAILWAY = "railway"
    HF_SPACE = "hf_space"
    GRAFANA = "grafana"
    QDRANT = "qdrant"
    CLOUDFLARE = "cloudflare"

    def __str__(self) -> str:
        return self.value


#: What each platform's token must be, shown on its "Connect" card. Least privilege on purpose:
#: the token decides what a stolen gateway could do, not the UI.
PLATFORM_TOKEN_HINTS: Mapping[Platform, str] = {
    Platform.RAILWAY: "A Railway project token for the production environment (Project settings "
                      "-> Tokens). Not an account or team token.",
    Platform.HF_SPACE: "A Hugging Face fine-grained token with write access to the inference "
                       "Space only (Settings -> Access Tokens -> Fine-grained).",
    Platform.GRAFANA: "A Grafana Cloud access policy token with metrics, rules and synthetic "
                      "monitoring scopes for this stack only.",
    Platform.QDRANT: "The Qdrant Cloud cluster API key (the one the gateway already uses).",
    Platform.CLOUDFLARE: "A Cloudflare API token with 'API Tokens: Edit' and 'Workers AI: Read', "
                         "for creating and revoking the Workers AI key.",
}


#: Hardware a Hugging Face Space can request (``huggingface_hub.SpaceHardware``, 1.28), cheapest
#: first. Kept here so the engine and the console can offer it without the Hub client.
SPACE_HARDWARE: tuple[str, ...] = (
    "cpu-basic", "cpu-upgrade", "zero-a10g", "t4-small", "t4-medium", "l4x1", "l4x4",
    "a10g-small", "a10g-large", "a10g-largex2", "a10g-largex4", "l40sx1", "l40sx4", "l40sx8",
    "a100-large", "a100x4", "a100x8",
)


class ChangeStatus(str, enum.Enum):
    """A change's life. Terminal states: SUCCEEDED, ROLLED_BACK, FAILED."""

    RECORDED = "recorded"        # done, nothing to watch (e.g. a token saved)
    WATCHING = "watching"        # applied; its deployment is being watched for 5 minutes
    SUCCEEDED = "succeeded"      # its deployment stayed healthy through the watch
    ROLLING_BACK = "rolling_back"
    ROLLED_BACK = "rolled_back"  # unhealthy: deployment rolled back, variables restored
    FAILED = "failed"            # could not be applied, or the rollback itself failed

    def __str__(self) -> str:
        return self.value

    @property
    def open(self) -> bool:
        return self in (ChangeStatus.WATCHING, ChangeStatus.ROLLING_BACK)


#: Variables the console never changes: losing one cuts the control center off from its own data.
#: The secrets key decrypts the platform tokens, the data key every user's encrypted fields, and
#: the database URL holds the change journal. Their rows say "change in the Railway dashboard".
PROTECTED_VARIABLES = frozenset({
    "AURA_SECRETS_ENCRYPTION_KEY",
    "AURA_DATA_ENCRYPTION_KEYS",
    "AURA_DATABASE_URL",
})

#: Names whose values are secret whatever the platform says: write-only in the console.
SECRET_NAME_PATTERN = re.compile(
    r"(KEY|KEYS|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIALS?|PRIVATE|DSN)$"
)
_URL_USERINFO = re.compile(r"^[a-z][a-z0-9+.-]*://[^/@\s]+@", re.IGNORECASE)


def is_secret_name(name: str) -> bool:
    """Whether a variable is secret by its name alone. ``*_ENV`` names another variable."""
    upper = name.strip().upper()
    if upper.endswith("_ENV"):
        return False
    return bool(SECRET_NAME_PATTERN.search(upper))


def is_secret_variable(name: str, value: str | None) -> bool:
    """Secret by name, or a URL that carries credentials (a DSN with a password). A plain URL
    such as a public base URL is not secret and stays readable."""
    return is_secret_name(name) or bool(value and _URL_USERINFO.match(value.strip()))


@dataclass(frozen=True, slots=True)
class PlatformCredential:
    """Which vault entry holds a platform's token, and what is known about it. Never the token."""

    platform: Platform
    secret_ref: str
    configured_by: str
    configured_at: datetime
    verified_at: datetime | None = None
    #: What verification learned (the Space it reaches, the token's account), for display.
    verified_detail: str = ""
    #: The non-secret thing the token is for: the HF Space id, the Grafana stack URL, the Qdrant
    #: cluster URL. Entered with the token, so no other setting has to be made elsewhere.
    target: str = ""


@dataclass(frozen=True, slots=True)
class ChangeRecord:
    """One entry in the change journal: who changed what, and what became of it.

    ``detail`` is redacted before it is built (variable names and whether each was secret, the
    deployment ids), so a record can be shown and audited as it is. Values a rollback needs to
    restore are kept in the vault under ``restore_ref`` and deleted once the change closes.
    """

    change_id: str
    actor: str
    platform: Platform
    target: str
    action: str
    status: ChangeStatus
    created_at: datetime
    updated_at: datetime
    detail: Mapping[str, Any] = field(default_factory=dict)
    deployment_id: str | None = None
    previous_deployment_id: str | None = None
    restore_ref: str | None = None
    reason: str = ""


@dataclass(frozen=True, slots=True)
class StepUpGrant:
    """A short-lived permission to make control-center changes, bought with a fresh sign-in.

    Only ``digest`` (SHA-256 of the opaque grant) is stored, so a database read cannot replay
    one. It is bound to the subject that signed in, and revoked with that subject's sessions.
    """

    digest: str
    subject: str
    email: str
    expires_at: datetime
    created_at: datetime


def grant_digest(grant: str) -> str:
    return hashlib.sha256(grant.encode("utf-8")).hexdigest()
