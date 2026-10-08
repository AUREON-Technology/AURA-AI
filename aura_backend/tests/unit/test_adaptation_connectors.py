"""AP2: connector descriptors, the credential vault, the registry lifecycle and its HTTP surface.

The registry runs on its real collaborators: the real ``ApprovalService``, the real encrypted
vault over an in-memory ciphertext store, the in-memory connector store and the container's audit
log. The one stand-in is the *connected system itself* (``StubConnector``) -- an external
boundary, not AURA code -- so each test can say exactly what the target database answers.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest

from aura_core.adaptation.connectors import ConnectorRegistry
from aura_core.adapters.connectors.postgres import PostgresConnectorFactory
from aura_core.adapters.credential_vault import EncryptedCredentialVault, InMemoryCiphertextStore
from aura_core.adapters.field_crypto import FieldCipher
from aura_core.adapters.memory_adaptation import InMemoryConnectorStore
from aura_core.domain.connectors import (
    CallerIdentity,
    ConnectorDescriptor,
    ConnectorHealth,
    ConnectorKind,
    ConnectorState,
    Credential,
    HealthStatus,
    IdentityMode,
)
from aura_core.domain.errors import (
    ApprovalRequiredError,
    ConfigurationError,
    ConflictError,
    DataIntegrityError,
    NotFoundError,
    ValidationError,
)
from aura_core.domain.identifiers import UserId
from aura_core.learning.approval import ApprovalKind, ApprovalService, InMemoryApprovalStore

SECRET = "s3cr3t-Pa55word-that-must-never-leak"


def _key() -> str:
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode()


def _descriptor(**overrides: Any) -> ConnectorDescriptor:
    fields: dict[str, Any] = dict(
        id="hr-db",
        kind=ConnectorKind.POSTGRES,
        identity_mode=IdentityMode.SERVICE_ACCOUNT,
        display_name="Reference HR",
        endpoint="postgresql://hr.internal:5432/hr",
        options={"user": "aura_reader", "sslmode": "require"},
    )
    fields.update(overrides)
    return ConnectorDescriptor(**fields)


class StubConnector:
    """The connected system. Answers whatever the test says it answers."""

    def __init__(self, descriptor: ConnectorDescriptor, credential: Credential | None, behaviour: Any) -> None:
        self.descriptor = descriptor
        self.credential = credential
        self._behaviour = behaviour
        self.closed = False

    async def health(self) -> ConnectorHealth:
        if isinstance(self._behaviour, BaseException):
            raise self._behaviour
        if self._behaviour == "hang":
            await asyncio.sleep(30)
        return self._behaviour

    async def aclose(self) -> None:
        self.closed = True


class StubFactory:
    def __init__(self, behaviour: Any = None) -> None:
        self.behaviour = behaviour or ConnectorHealth(HealthStatus.HEALTHY, write_capable=False)
        self.opened: list[StubConnector] = []

    async def open(self, descriptor: ConnectorDescriptor, credential: Credential | None) -> StubConnector:
        connector = StubConnector(descriptor, credential, self.behaviour)
        self.opened.append(connector)
        return connector


class RecordingMetrics:
    def __init__(self) -> None:
        self.health: dict[str, str] = {}

    def set_connector_health(self, connector: str, kind: str, status: str) -> None:
        self.health[connector] = status


@pytest.fixture
def ciphertexts() -> InMemoryCiphertextStore:
    return InMemoryCiphertextStore()


@pytest.fixture
def vault(ciphertexts: InMemoryCiphertextStore) -> EncryptedCredentialVault:
    return EncryptedCredentialVault(cipher=FieldCipher([_key()]), store=ciphertexts)


@pytest.fixture
def approvals(audit) -> ApprovalService:
    return ApprovalService(store=InMemoryApprovalStore(), audit=audit)


@pytest.fixture
def factory() -> StubFactory:
    return StubFactory()


@pytest.fixture
def registry(vault, approvals, factory, clock, audit) -> ConnectorRegistry:
    return ConnectorRegistry(
        store=InMemoryConnectorStore(),
        vault=vault,
        approvals=approvals,
        factories={ConnectorKind.POSTGRES: factory},
        clock=clock,
        audit=audit,
        metrics=RecordingMetrics(),
    )


async def _registered_and_approved(registry: ConnectorRegistry, approvals: ApprovalService):
    record, approval = await registry.register(_descriptor(), secret=Credential(SECRET), requested_by="sa@example.com")
    await approvals.decide(approval.id, approved=True, by="reviewer@example.com")
    return record, approval


# ---------------------------------------------------------------------------------- descriptor


class TestDescriptor:
    @pytest.mark.parametrize(
        "endpoint",
        [
            "postgresql://aura:hunter2@hr.internal/hr",
            "postgresql://aura@hr.internal/hr",
            "https://api.example.com/v1?api_key=abc",
            "postgresql://hr.internal/hr ; DROP",
        ],
    )
    def test_endpoints_cannot_carry_credentials(self, endpoint: str) -> None:
        with pytest.raises(ValidationError):
            _descriptor(endpoint=endpoint)

    @pytest.mark.parametrize("key", ["password", "db_password", "api_key", "token", "client_secret", "auth_header"])
    def test_secretish_options_are_refused(self, key: str) -> None:
        with pytest.raises(ValidationError, match="credential vault"):
            _descriptor(options={"user": "u", key: "x"})

    @pytest.mark.parametrize("kind", [ConnectorKind.POSTGRES, ConnectorKind.FILES, ConnectorKind.SQL_SERVER])
    def test_raw_sources_cannot_be_delegated(self, kind: ConnectorKind) -> None:
        with pytest.raises(ValidationError, match="service account"):
            _descriptor(kind=kind, identity_mode=IdentityMode.DELEGATED)

    def test_delegated_is_allowed_where_the_target_has_users(self) -> None:
        d = _descriptor(kind=ConnectorKind.OPENAPI, identity_mode=IdentityMode.DELEGATED,
                        endpoint="https://hr.example.com/openapi.json", options={})
        assert not d.needs_credential

    def test_digest_covers_the_secret_ref_but_not_the_secret(self) -> None:
        base = _descriptor()
        with_ref = replace(base, secret_ref="cred_" + "a" * 32)
        assert base.digest != with_ref.digest
        assert replace(base, secret_ref="cred_" + "b" * 32).digest != with_ref.digest

    def test_credentials_never_render(self, caplog) -> None:
        credential = Credential(SECRET)
        identity = CallerIdentity(UserId("u1"), delegated_token=credential)
        rendered = [repr(credential), str(credential), f"{credential}", f"{credential!r:>40}", repr(identity)]
        with caplog.at_level(logging.DEBUG):
            logging.getLogger("aura.test").info("credential %s %r", credential, credential)
        assert all(SECRET not in text for text in rendered)
        assert SECRET not in caplog.text
        assert credential.reveal() == SECRET


# --------------------------------------------------------------------------------------- vault


class TestVault:
    async def test_round_trip_stores_only_ciphertext(self, vault, ciphertexts) -> None:
        ref = await vault.put(Credential(SECRET))
        assert ref.startswith("cred_")
        (stored,) = ciphertexts.raw_rows().values()
        assert SECRET not in stored and stored.startswith("aura-enc:v1:")
        assert (await vault.get(ref)).reveal() == SECRET

    async def test_ciphertext_moved_to_another_ref_fails_authentication(self, vault, ciphertexts) -> None:
        first = await vault.put(Credential(SECRET))
        second = await vault.put(Credential("other"))
        await ciphertexts.put(second, ciphertexts.raw_rows()[first])
        with pytest.raises(DataIntegrityError):
            await vault.get(second)

    async def test_plaintext_at_rest_is_refused_not_used(self, vault, ciphertexts) -> None:
        ref = "cred_" + "c" * 32
        await ciphertexts.put(ref, SECRET)
        with pytest.raises(DataIntegrityError, match="not encrypted"):
            await vault.get(ref)

    async def test_no_key_means_no_storage_and_no_reads(self, ciphertexts) -> None:
        keyless = EncryptedCredentialVault(cipher=FieldCipher(()), store=ciphertexts)
        with pytest.raises(ConfigurationError, match="AURA_SECRETS_ENCRYPTION_KEY"):
            await keyless.put(Credential(SECRET))
        assert ciphertexts.raw_rows() == {}
        await ciphertexts.put("cred_" + "d" * 32, "aura-enc:v1:deadbeef:token")
        with pytest.raises(ConfigurationError):
            await keyless.get("cred_" + "d" * 32)

    async def test_rotation_keeps_old_credentials_readable(self, ciphertexts) -> None:
        old, new = _key(), _key()
        ref = await EncryptedCredentialVault(cipher=FieldCipher([old]), store=ciphertexts).put(Credential(SECRET))
        rotated = EncryptedCredentialVault(cipher=FieldCipher([new, old]), store=ciphertexts)
        assert (await rotated.get(ref)).reveal() == SECRET
        dropped = EncryptedCredentialVault(cipher=FieldCipher([new]), store=ciphertexts)
        with pytest.raises(DataIntegrityError):
            await dropped.get(ref)

    async def test_refs_are_validated(self, vault) -> None:
        with pytest.raises(ValidationError):
            await vault.get("../../etc/passwd")


# ------------------------------------------------------------------------------------ registry


class TestRegistration:
    async def test_register_vaults_the_secret_and_requests_a_bound_approval(self, registry, approvals, ciphertexts) -> None:
        record, approval = await registry.register(_descriptor(), secret=Credential(SECRET), requested_by="sa@example.com")
        assert record.state is ConnectorState.PENDING_APPROVAL
        assert record.descriptor.secret_ref in ciphertexts.raw_rows()
        assert approval.kind is ApprovalKind.CONNECTOR_REGISTRATION
        assert approval.artefact_digest == record.descriptor.digest
        assert approval.payload["connector_id"] == "hr-db" and approval.payload["has_credential"] is True
        assert SECRET not in repr(approval.as_dict())

    @pytest.mark.parametrize(
        "descriptor, secret, message",
        [
            (_descriptor(secret_ref="cred_" + "e" * 32), Credential(SECRET), "assigned by the vault"),
            (_descriptor(), None, "needs a credential"),
            (_descriptor(kind=ConnectorKind.MYSQL), Credential(SECRET), "no mysql connector"),
            (
                _descriptor(kind=ConnectorKind.OPENAPI, identity_mode=IdentityMode.DELEGATED,
                            endpoint="https://x.example.com/openapi.json", options={}),
                Credential(SECRET),
                "no openapi connector",
            ),
        ],
    )
    async def test_invalid_registrations_are_refused(self, registry, ciphertexts, descriptor, secret, message) -> None:
        with pytest.raises(ValidationError, match=message):
            await registry.register(descriptor, secret=secret, requested_by="sa@example.com")
        assert ciphertexts.raw_rows() == {}

    async def test_delegated_connectors_store_no_credential(self, registry) -> None:
        registry.register_factory(ConnectorKind.OPENAPI, StubFactory())
        delegated = _descriptor(kind=ConnectorKind.OPENAPI, identity_mode=IdentityMode.DELEGATED,
                                endpoint="https://x.example.com/openapi.json", options={})
        with pytest.raises(ValidationError, match="stores no credential"):
            await registry.register(delegated, secret=Credential(SECRET), requested_by="sa@example.com")
        record, _ = await registry.register(delegated, secret=None, requested_by="sa@example.com")
        assert record.descriptor.secret_ref is None

    async def test_duplicate_id_conflicts_and_leaves_no_orphaned_credential(self, registry, ciphertexts) -> None:
        await registry.register(_descriptor(), secret=Credential(SECRET), requested_by="sa@example.com")
        with pytest.raises(ConflictError):
            await registry.register(_descriptor(), secret=Credential("second"), requested_by="sa@example.com")
        assert len(ciphertexts.raw_rows()) == 1

    async def test_failed_registration_rolls_back_the_vaulted_secret(self, vault, ciphertexts, factory, clock, audit) -> None:
        class BrokenApprovals:
            async def request(self, **_: Any):
                raise RuntimeError("approval store down")

        broken = ConnectorRegistry(store=InMemoryConnectorStore(), vault=vault, approvals=BrokenApprovals(),  # type: ignore[arg-type]
                                   factories={ConnectorKind.POSTGRES: factory}, clock=clock, audit=audit)
        with pytest.raises(RuntimeError):
            await broken.register(_descriptor(), secret=Credential(SECRET), requested_by="sa@example.com")
        assert ciphertexts.raw_rows() == {}


class TestActivation:
    async def test_approved_healthy_read_only_connector_activates(self, registry, approvals, factory) -> None:
        record, approval = await _registered_and_approved(registry, approvals)
        active = await registry.activate("hr-db", approval_id=approval.id, by="sa@example.com")
        assert active.state is ConnectorState.ACTIVE and active.activated_by == "sa@example.com"
        # The connector received the decrypted credential, and only the connector did.
        assert factory.opened[-1].credential.reveal() == SECRET
        assert factory.opened[-1].closed
        assert (await registry.open_active("hr-db")).descriptor.id == "hr-db"

    async def test_an_undecided_approval_does_not_activate(self, registry) -> None:
        _, approval = await registry.register(_descriptor(), secret=Credential(SECRET), requested_by="sa@example.com")
        with pytest.raises(ApprovalRequiredError):
            await registry.activate("hr-db", approval_id=approval.id, by="sa@example.com")

    async def test_an_expired_approval_does_not_activate(self, registry, approvals) -> None:
        record, _ = await registry.register(_descriptor(), secret=Credential(SECRET), requested_by="sa@example.com")
        stale = await approvals.request(
            kind=ApprovalKind.CONNECTOR_REGISTRATION, summary="stale", artefact_digest=record.descriptor.digest,
            payload={"connector_id": "hr-db"}, ttl=timedelta(seconds=-1),
        )
        with pytest.raises(ConflictError, match="expired"):
            await approvals.decide(stale.id, approved=True, by="reviewer@example.com")
        with pytest.raises(ApprovalRequiredError):
            await registry.activate("hr-db", approval_id=stale.id, by="sa@example.com")

    async def test_a_descriptor_changed_after_approval_is_refused(self, registry, approvals) -> None:
        record, approval = await _registered_and_approved(registry, approvals)
        tampered = replace(record, descriptor=replace(record.descriptor, endpoint="postgresql://evil.example.com/hr"))
        await registry._store.put(tampered)  # someone with database access edits the row
        with pytest.raises(ConflictError, match="different artefact"):
            await registry.activate("hr-db", approval_id=approval.id, by="sa@example.com")

    async def test_an_approval_for_something_else_is_refused(self, registry, approvals) -> None:
        record, _ = await registry.register(_descriptor(), secret=Credential(SECRET), requested_by="sa@example.com")
        other = await approvals.request(
            kind=ApprovalKind.SOURCE_REGISTRATION, summary="x", artefact_digest=record.descriptor.digest
        )
        await approvals.decide(other.id, approved=True, by="reviewer@example.com")
        with pytest.raises(ConflictError, match="not granted for this connector"):
            await registry.activate("hr-db", approval_id=other.id, by="sa@example.com")

    @pytest.mark.parametrize(
        "health, message",
        [
            (ConnectorHealth(HealthStatus.HEALTHY, write_capable=True, findings=("table_write",)), "read-only"),
            (ConnectorHealth(HealthStatus.HEALTHY, write_capable=None), "read-only"),
            (ConnectorHealth(HealthStatus.UNHEALTHY, "auth_failed"), "not healthy"),
        ],
    )
    async def test_unhealthy_or_write_capable_connectors_stay_pending(self, registry, approvals, factory, audit, health, message) -> None:
        factory.behaviour = health
        _, approval = await _registered_and_approved(registry, approvals)
        with pytest.raises(ConflictError, match=message):
            await registry.activate("hr-db", approval_id=approval.id, by="sa@example.com")
        assert (await registry.get("hr-db")).state is ConnectorState.PENDING_APPROVAL
        refusals = await audit.query(action="adaptation.connector.activate", limit=10)
        assert refusals and refusals[0].outcome.startswith("refused_")

    async def test_only_active_connectors_are_handed_out(self, registry) -> None:
        await registry.register(_descriptor(), secret=Credential(SECRET), requested_by="sa@example.com")
        with pytest.raises(ConflictError, match="pending_approval"):
            await registry.open_active("hr-db")
        with pytest.raises(NotFoundError):
            await registry.open_active("missing")

    async def test_disable_takes_it_out_of_service_and_reactivation_needs_an_approval(self, registry, approvals) -> None:
        _, approval = await _registered_and_approved(registry, approvals)
        await registry.activate("hr-db", approval_id=approval.id, by="sa@example.com")
        await registry.disable("hr-db", by="sa@example.com")
        with pytest.raises(ConflictError):
            await registry.open_active("hr-db")
        # The original approval was granted -- and is still unexpired -- so re-activation works
        # with it; once it expires a new one is needed.
        reactivated = await registry.activate("hr-db", approval_id=approval.id, by="sa@example.com")
        assert reactivated.state is ConnectorState.ACTIVE

    async def test_remove_deletes_the_credential(self, registry, ciphertexts) -> None:
        await registry.register(_descriptor(), secret=Credential(SECRET), requested_by="sa@example.com")
        assert await registry.remove("hr-db", by="sa@example.com")
        assert ciphertexts.raw_rows() == {}
        assert not await registry.remove("hr-db", by="sa@example.com")


class TestHealth:
    async def test_vault_failure_is_unhealthy_never_a_plaintext_fallback(self, registry, approvals, ciphertexts) -> None:
        record, _ = await registry.register(_descriptor(), secret=Credential(SECRET), requested_by="sa@example.com")
        await ciphertexts.put(record.descriptor.secret_ref, SECRET)  # plaintext appears at rest
        health = await registry.health("hr-db")
        assert health.status is HealthStatus.UNHEALTHY and health.reason == "credential_unavailable"

    async def test_a_raising_connector_reports_its_type_only(self, registry, factory, caplog) -> None:
        factory.behaviour = RuntimeError(f"password={SECRET} rejected")
        await registry.register(_descriptor(), secret=Credential(SECRET), requested_by="sa@example.com")
        with caplog.at_level(logging.DEBUG):
            health = await registry.health("hr-db")
        assert health.reason == "connector_error"
        assert SECRET not in caplog.text

    async def test_a_hanging_connector_times_out(self, registry, factory) -> None:
        factory.behaviour = "hang"
        await registry.register(_descriptor(timeout_s=0.5), secret=Credential(SECRET), requested_by="sa@example.com")
        health = await registry.health("hr-db")
        assert health.reason == "timeout"
        assert factory.opened[-1].closed

    async def test_health_is_published_as_a_metric(self, registry) -> None:
        await registry.register(_descriptor(), secret=Credential(SECRET), requested_by="sa@example.com")
        await registry.health("hr-db")
        assert registry._metrics.health == {"hr-db": "healthy"}


class TestNothingLeaks:
    async def test_full_lifecycle_never_writes_the_secret_to_audit_or_logs(self, registry, approvals, audit, caplog) -> None:
        with caplog.at_level(logging.DEBUG):
            _, approval = await _registered_and_approved(registry, approvals)
            await registry.activate("hr-db", approval_id=approval.id, by="sa@example.com")
            await registry.health("hr-db")
            await registry.disable("hr-db", by="sa@example.com")
            await registry.remove("hr-db", by="sa@example.com")
        events = await audit.query(limit=100)
        assert len(events) >= 5
        assert all(SECRET not in repr(e.as_dict()) for e in events)
        assert SECRET not in caplog.text


class TestAttestedReadOnlyOpenApi:
    """AR-3: a credentialed OpenAPI connector activates only on a Super Admin's signed attestation.

    Runs the real OpenAPI adapter against a mock transport (the connected API is the one
    stand-in), so health, the approval digest and activation are the code production runs.
    """

    TOKEN = "flame-read-token-that-must-never-leak"

    def _registry(self, vault, approvals, clock, audit, seen: list) -> ConnectorRegistry:
        import httpx

        from aura_core.adapters.connectors.openapi import OpenApiConnectorFactory

        spec = b'{"openapi": "3.1.0", "info": {"title": "Flame", "version": "0.1.0"}, "paths": {}}'

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, content=spec)

        async def resolve(host: str) -> list[str]:
            return ["10.0.0.7"]

        factory = OpenApiConnectorFactory(transport=httpx.MockTransport(handler), resolver=resolve)
        return ConnectorRegistry(
            store=InMemoryConnectorStore(), vault=vault, approvals=approvals,
            factories={ConnectorKind.OPENAPI: factory}, clock=clock, audit=audit, metrics=RecordingMetrics(),
        )

    @staticmethod
    def _flame(**options: Any) -> ConnectorDescriptor:
        return ConnectorDescriptor(
            id="flame-api", kind=ConnectorKind.OPENAPI, identity_mode=IdentityMode.SERVICE_ACCOUNT,
            display_name="Flame in Freefall", endpoint="https://flame.internal/api/openapi.json",
            options={"allow_private_network": True, **options},
        )

    async def _activate(self, registry, approvals, descriptor):
        record, approval = await registry.register(descriptor, secret=Credential(self.TOKEN), requested_by="sa@example.com")
        assert approval.payload["options"].get("read_only_attested") is descriptor.options.get("read_only_attested")
        await approvals.decide(approval.id, approved=True, by="reviewer@example.com")
        return record, await registry.activate("flame-api", approval_id=approval.id, by="sa@example.com")

    async def test_an_attested_credential_activates_and_never_leaks(self, vault, approvals, clock, audit, caplog) -> None:
        seen: list = []
        registry = self._registry(vault, approvals, clock, audit, seen)
        with caplog.at_level(logging.DEBUG):
            record, active = await self._activate(registry, approvals, self._flame(read_only_attested=True))
        assert active.state is ConnectorState.ACTIVE
        assert seen and seen[-1].headers["authorization"] == f"Bearer {self.TOKEN}"
        events = await audit.query(action="adaptation.connector.activate", limit=10)
        assert events[0].outcome == "ok" and events[0].detail["findings"] == ["credential_scope_attested"]
        everything = [repr(record), record.descriptor.digest, caplog.text, *(repr(e.as_dict()) for e in await audit.query(limit=100))]
        assert all(self.TOKEN not in text for text in everything)

    async def test_without_the_attestation_it_is_still_refused(self, vault, approvals, clock, audit) -> None:
        registry = self._registry(vault, approvals, clock, audit, [])
        with pytest.raises(ConflictError, match="read-only"):
            await self._activate(registry, approvals, self._flame())

    async def test_attestation_after_approval_breaks_the_digest(self, vault, approvals, clock, audit) -> None:
        registry = self._registry(vault, approvals, clock, audit, [])
        record, approval = await registry.register(self._flame(), secret=Credential(self.TOKEN), requested_by="sa@example.com")
        await approvals.decide(approval.id, approved=True, by="reviewer@example.com")
        attested = replace(record, descriptor=replace(record.descriptor, options={
            **record.descriptor.options, "read_only_attested": True}))
        await registry._store.put(attested)  # someone flips the flag in the database
        with pytest.raises(ConflictError, match="different artefact"):
            await registry.activate("flame-api", approval_id=approval.id, by="sa@example.com")

    @pytest.mark.parametrize("value", ["yes", 1])
    def test_the_attestation_is_a_boolean(self, value) -> None:
        with pytest.raises(ValidationError):
            self._flame(read_only_attested=value)

    def test_the_attestation_means_nothing_for_a_database(self) -> None:
        with pytest.raises(ValidationError, match="does not apply"):
            _descriptor(options={"user": "aura_reader", "read_only_attested": True})


# ------------------------------------------------------------------------------ postgres adapter


class TestPostgresConnectorWithoutADatabase:
    """The refusals that happen before any driver call (the live behaviour is in tests/load)."""

    async def _health(self, descriptor: ConnectorDescriptor, forbidden: tuple[str, ...] = ()) -> ConnectorHealth:
        connector = await PostgresConnectorFactory(forbidden_targets=forbidden).open(descriptor, Credential(SECRET))
        return await connector.health()

    async def test_refuses_auras_own_database(self) -> None:
        own = "postgresql://aura:pw@db:5432/aura"
        health = await self._health(_descriptor(endpoint="postgresql://DB:5432/aura"), forbidden=(own,))
        assert health.reason == "own_database"

    async def test_refuses_a_non_postgres_endpoint(self) -> None:
        assert (await self._health(_descriptor(endpoint="https://hr.example.com/db"))).reason == "invalid_endpoint"

    @pytest.mark.parametrize("options", [{"sslmode": "require"}, {"user": "r", "sslmode": "sometimes"}])
    async def test_refuses_incomplete_options(self, options) -> None:
        assert (await self._health(_descriptor(options=options))).reason == "invalid_options"


def test_adapter_schema_matches_the_migration() -> None:
    import importlib.util
    from pathlib import Path

    from aura_core.adapters.postgres_adaptation import ADAPTATION_SCHEMA

    path = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0009_adaptation_connectors.py"
    spec = importlib.util.spec_from_file_location("migration_0009", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert tuple(s.strip() for s in module.UPGRADE_STATEMENTS) == tuple(s.strip() for s in ADAPTATION_SCHEMA)


# -------------------------------------------------------------------------------------- HTTP API


@pytest.fixture
def enabled_container(monkeypatch):
    from aura_core.config.settings import load_settings
    from aura_core.container import Container

    monkeypatch.setenv("AURA_ADAPTATION_ENABLED", "true")
    monkeypatch.setenv("AURA_SECRETS_ENCRYPTION_KEY", _key())
    container = Container(load_settings(refresh=True, validate=False))
    container.connectors.register_factory(ConnectorKind.POSTGRES, StubFactory())
    return container


def _client(container, *, role: str):
    from fastapi.testclient import TestClient

    from aura_core.api import Principal, build_app

    async def authenticator(request):
        return Principal(user_id=UserId("op-1"), email="op@example.com", role=role, capabilities=())

    return TestClient(build_app(container, authenticator=authenticator))


_BODY = {
    "id": "hr-db",
    "kind": "postgres",
    "display_name": "Reference HR",
    "endpoint": "postgresql://hr.internal:5432/hr",
    "options": {"user": "aura_reader", "sslmode": "require"},
    "secret": SECRET,
}


class TestHttp:
    def test_super_admin_runs_the_whole_lifecycle(self, enabled_container) -> None:
        with _client(enabled_container, role="SuperAdmin") as client:
            created = client.post("/api/v3/admin/connectors", json=_BODY)
            assert created.status_code == 201, created.text
            assert SECRET not in created.text and "secret_ref" not in created.text
            assert created.json()["connector"]["has_credential"] is True
            approval_id = created.json()["approval"]["id"]

            blocked = client.post("/api/v3/admin/connectors/hr-db/activate", json={"approval_id": approval_id})
            assert blocked.status_code == 409  # nobody has decided yet
            assert blocked.json()["error"] == "approval_required"

            decided = client.post(f"/api/v3/admin/approvals/{approval_id}/decide", json={"approved": True})
            assert decided.status_code == 200, decided.text
            activated = client.post("/api/v3/admin/connectors/hr-db/activate", json={"approval_id": approval_id})
            assert activated.status_code == 200, activated.text
            assert activated.json()["connector"]["state"] == "active"

            health = client.post("/api/v3/admin/connectors/hr-db/health")
            assert health.json()["health"]["status"] == "healthy"
            listed = client.get("/api/v3/admin/connectors")
            assert [c["id"] for c in listed.json()["connectors"]] == ["hr-db"]
            assert client.delete("/api/v3/admin/connectors/hr-db").json() == {"deleted": True}

    @pytest.mark.parametrize("role", ["User", "Developer", "Admin"])
    def test_only_super_admins_reach_connectors(self, enabled_container, role: str) -> None:
        with _client(enabled_container, role=role) as client:
            assert client.get("/api/v3/admin/connectors").status_code == 403
            assert client.post("/api/v3/admin/connectors", json=_BODY).status_code == 403

    def test_validation_errors_never_echo_the_secret(self, enabled_container) -> None:
        with _client(enabled_container, role="SuperAdmin") as client:
            for broken in (
                {k: v for k, v in _BODY.items() if k != "kind"},
                {**_BODY, "options": {"password": "x"}},
                {**_BODY, "endpoint": f"postgresql://u:{SECRET}@h/db"},
                {**_BODY, "unexpected": 1},
                {**_BODY, "timeout_s": "slow"},
            ):
                response = client.post("/api/v3/admin/connectors", json=broken)
                assert response.status_code == 422, response.text
                assert SECRET not in response.text

    def test_disabled_layer_answers_503(self, container) -> None:
        assert container.connectors is None
        with _client(container, role="SuperAdmin") as client:
            assert client.get("/api/v3/admin/connectors").status_code == 503
