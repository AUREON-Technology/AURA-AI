"""INT-101 (stream B3): rollback restores a version that was approved for production, nothing else.

``ModelRegistry.rollback`` needs no fresh approval (ADR-008: reverting to a known-good state is
safe by definition). It used to put *any* registered version into production -- a candidate that
never passed the promotion gate included -- so "rollback" was a way round the gate. Now the target
must carry the approval that promoted it: an approved decision bound to exactly that version
(``model_id@version``, the digest ``transition`` requires), on a version that has left the
candidate track (archived, or still production). The approval may have expired since; it is the
record that a person once put this version live, not a fresh permission.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from aura_core.domain.errors import ConflictError
from aura_core.learning import ApprovalKind, ApprovalService, InMemoryApprovalStore
from aura_core.mlops import InMemoryModelStore, ModelRegistry, ModelStage, ModelVersion


@pytest.fixture
def approvals() -> ApprovalService:
    return ApprovalService(store=InMemoryApprovalStore())


@pytest.fixture
def store() -> InMemoryModelStore:
    return InMemoryModelStore()


@pytest.fixture
def registry(store, approvals) -> ModelRegistry:
    return ModelRegistry(store=store, approvals=approvals)


async def _register(registry: ModelRegistry, version: str) -> None:
    await registry.register(ModelVersion(model_id="m", version=version, provider_kind="ollama",
                                         dataset_digest="d"))


async def _promote(registry: ModelRegistry, approvals: ApprovalService, version: str,
                   *, ttl: timedelta = timedelta(days=14)) -> str:
    request = await approvals.request(kind=ApprovalKind.MODEL_PROMOTION, summary=f"promote {version}",
                                      artefact_digest=f"m@{version}", ttl=ttl)
    await approvals.decide(request.id, approved=True, by="reviewer@example.com")
    await registry.transition("m", version, to=ModelStage.CANARY, canary_percent=10)
    await registry.transition("m", version, to=ModelStage.PRODUCTION, approval_id=request.id)
    return request.id


class TestRollbackOnlyRestoresAnApprovedProductionVersion:
    async def test_a_previous_production_version_is_restored_without_a_new_approval(
        self, registry, approvals,
    ) -> None:
        await _register(registry, "1.0.0")
        await _register(registry, "2.0.0")
        await _promote(registry, approvals, "1.0.0")
        await _promote(registry, approvals, "2.0.0")
        assert (await registry.get("m", "1.0.0")).stage is ModelStage.ARCHIVED

        restored = await registry.rollback("m", to_version="1.0.0", actor="oncall")

        assert restored.stage is ModelStage.PRODUCTION
        assert (await registry.production("m")).version == "1.0.0"
        assert (await registry.get("m", "2.0.0")).stage is ModelStage.ARCHIVED

    async def test_an_expired_promotion_approval_still_counts(self, registry, approvals, store) -> None:
        """A rollback is an incident action; a reviewer's approval from last month is the record
        that this version was put live, not a permission that must still be fresh."""
        await _register(registry, "1.0.0")
        await _register(registry, "2.0.0")
        approval_id = await _promote(registry, approvals, "1.0.0")
        await _promote(registry, approvals, "2.0.0")
        granted = await approvals.get(approval_id)
        await approvals._store.put(replace(granted, expires_at=granted.decided_at))

        restored = await registry.rollback("m", to_version="1.0.0")

        assert restored.stage is ModelStage.PRODUCTION

    async def test_a_candidate_that_was_never_approved_is_refused(self, registry, approvals) -> None:
        await _register(registry, "1.0.0")
        await _register(registry, "2.0.0")
        await _promote(registry, approvals, "2.0.0")

        with pytest.raises(ConflictError):
            await registry.rollback("m", to_version="1.0.0")

        assert (await registry.production("m")).version == "2.0.0", "nothing may change on a refusal"
        assert (await registry.get("m", "1.0.0")).stage is ModelStage.CANDIDATE

    async def test_an_archived_version_that_never_reached_production_is_refused(
        self, registry, approvals,
    ) -> None:
        await _register(registry, "1.0.0")
        await _register(registry, "2.0.0")
        await _promote(registry, approvals, "2.0.0")
        await registry.transition("m", "1.0.0", to=ModelStage.ARCHIVED)

        with pytest.raises(ConflictError):
            await registry.rollback("m", to_version="1.0.0")

    async def test_an_approval_for_another_artefact_does_not_count(self, registry, approvals, store) -> None:
        """An imported candidate carries its FINETUNE approval (bound to the dataset digest), and
        a canary can be handed any approval id: neither is a production approval of *this*
        version."""
        await _register(registry, "1.0.0")
        await _register(registry, "2.0.0")
        await _promote(registry, approvals, "2.0.0")
        finetune = await approvals.request(kind=ApprovalKind.FINETUNE, summary="train", artefact_digest="d")
        await approvals.decide(finetune.id, approved=True, by="reviewer@example.com")
        archived = replace(await registry.get("m", "1.0.0"), stage=ModelStage.ARCHIVED,
                           approval_id=finetune.id)
        await store.put(archived)

        with pytest.raises(ConflictError):
            await registry.rollback("m", to_version="1.0.0")

    async def test_a_rejected_approval_does_not_count(self, registry, approvals, store) -> None:
        await _register(registry, "1.0.0")
        await _register(registry, "2.0.0")
        await _promote(registry, approvals, "2.0.0")
        refused = await approvals.request(kind=ApprovalKind.MODEL_PROMOTION, summary="promote 1.0.0",
                                          artefact_digest="m@1.0.0")
        await approvals.decide(refused.id, approved=False, by="reviewer@example.com")
        await store.put(replace(await registry.get("m", "1.0.0"), stage=ModelStage.ARCHIVED,
                                approval_id=refused.id))

        with pytest.raises(ConflictError):
            await registry.rollback("m", to_version="1.0.0")

    async def test_without_an_approval_service_nothing_can_be_verified(self, store) -> None:
        registry = ModelRegistry(store=store)
        await store.put(ModelVersion(model_id="m", version="1.0.0", provider_kind="ollama",
                                     stage=ModelStage.ARCHIVED, approval_id="apr_x"))

        with pytest.raises(ConflictError):
            await registry.rollback("m", to_version="1.0.0")
