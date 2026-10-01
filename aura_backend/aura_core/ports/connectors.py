"""Ports for connectors to connected systems and the credential vault (ADR-021, Track AP).

Stdlib only (ADR-001), async throughout. Vendor drivers live in ``aura_core/adapters/connectors/``
and are imported lazily inside them.

What is deliberately *not* here yet: templated reads (``read(QueryTemplate, ...)``) arrive with
AP6 as their own port, so a connector gains a capability only when the code that needs it exists. There is no write port in v1 at all
(ADR-021): a ``WriteConnector`` is AP12c, behind argument-bound confirmation.
"""

from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

from ..domain.connectors import ConnectorDescriptor, ConnectorHealth, ConnectorRecord, ConnectorState, Credential
from ..domain.discovery import DiscoveryBudget, DiscoveryReport

__all__ = ["Connector", "DiscoveryConnector", "ConnectorFactory", "CredentialVault", "ConnectorStore"]


@runtime_checkable
class Connector(Protocol):
    """An open handle on one connected system."""

    descriptor: ConnectorDescriptor

    async def health(self) -> ConnectorHealth:
        """Reach the system and report whether the credential could write to it.

        Never raises for an unreachable or misconfigured system -- that is an ``UNHEALTHY``
        result with a reason code. Vendor error text never appears in the result.
        """
        ...

    async def aclose(self) -> None: ...


@runtime_checkable
class DiscoveryConnector(Connector, Protocol):
    """A connector that can describe its system deterministically (AP3, ADR-026).

    Bounded by ``budget``; a budget stop sets ``truncated`` and an object it may not read is an
    issue in the report, not an exception. Raises only when nothing could be discovered at all
    (unreachable, credential refused) -- as a typed ``AuraError``, never vendor text.
    """

    async def discover(self, budget: DiscoveryBudget) -> DiscoveryReport: ...


@runtime_checkable
class ConnectorFactory(Protocol):
    """Builds connectors of one kind. Building opens nothing; the first call does."""

    async def open(self, descriptor: ConnectorDescriptor, credential: Credential | None) -> Connector: ...


@runtime_checkable
class CredentialVault(Protocol):
    """Holds connector credentials, encrypted at rest, addressed by an opaque ``secret_ref``.

    Implementations must never fall back to plaintext: with no key configured, :meth:`put`
    refuses and :meth:`get` raises rather than returning anything stored unencrypted.
    """

    async def put(self, secret: Credential) -> str:
        """Store ``secret`` and return its new ``secret_ref``."""
        ...

    async def get(self, secret_ref: str) -> Credential: ...

    async def delete(self, secret_ref: str) -> bool: ...


@runtime_checkable
class ConnectorStore(Protocol):
    """Registered connector records (descriptor + lifecycle). Holds no secrets."""

    async def put(self, record: ConnectorRecord) -> None: ...

    async def get(self, connector_id: str) -> ConnectorRecord | None: ...

    async def list(self, *, state: ConnectorState | None = None) -> Sequence[ConnectorRecord]: ...

    async def delete(self, connector_id: str) -> bool: ...
