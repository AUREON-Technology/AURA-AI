"""Connector vocabulary shared by the Project Twin and the Project Blueprint (ADR-021).

Only the closed vocabularies live here for now (AP1). The connector *ports*, the descriptor with
its concurrency limits and ``secret_ref``, and the credential vault arrive in AP2.
"""

from __future__ import annotations

import enum

__all__ = ["ConnectorKind", "IdentityMode"]


class ConnectorKind(str, enum.Enum):
    """The kinds of source system a connector can read (v1 set, ADR-021)."""

    POSTGRES = "postgres"
    MYSQL = "mysql"
    SQL_SERVER = "sql_server"
    OPENAPI = "openapi"
    FILES = "files"
    GIT = "git"
    #: AURA's own server-readable store, used by Project 0 (ADR-028).
    NATIVE = "native"

    def __str__(self) -> str:
        return self.value


class IdentityMode(str, enum.Enum):
    """Whose permissions a connector read runs under (ADR-021)."""

    #: The end user's own OIDC/OAuth token is forwarded per call; the target system enforces its
    #: own permissions and AURA's mapped policy is an additional filter. Never persisted.
    DELEGATED = "delegated"
    #: A vaulted service credential; discovered roles are mapped to AURA policy, and AURA is the
    #: enforcing authority.
    SERVICE_ACCOUNT = "service_account"

    def __str__(self) -> str:
        return self.value
