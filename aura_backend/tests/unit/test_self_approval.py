"""INT-106 (stream B3): a person may not approve their own request while someone else could.

``ApprovalService.decide`` only logged and audited a self-approval, and ``require`` honoured it,
so the four-eyes gate in front of production models, connectors and blueprints was one pair of
eyes whenever the requester was also an approver. The rule now:

* a self-approval is refused when any *other* eligible approver exists;
* a sole operator (nobody else could decide) may still self-approve, and it stays audited as one;
* rejecting your own request is always allowed -- it only ever makes the system do less.

Whether another approver exists is not the approval service's knowledge: it is injected
(``other_approver_exists``). With none injected the service keeps the sole-operator behaviour.
"""

from __future__ import annotations

import pytest

from aura_core.adapters.runtime import InMemoryAuditSink
from aura_core.domain.errors import (
    ApprovalRequiredError,
    AuthorizationError,
    DependencyUnavailableError,
)
from aura_core.learning import ApprovalKind, ApprovalService, InMemoryApprovalStore

REQUESTER = "sa@example.com"


class _Directory:
    """Stands in for the operator directory: who else could decide, and what was asked."""

    def __init__(self, others: bool) -> None:
        self.others = others
        self.asked: list = []

    async def __call__(self, requester: str, kind) -> bool:
        self.asked.append((requester, str(kind)))
        return self.others


async def _requested(service: ApprovalService, kind=ApprovalKind.MODEL_PROMOTION):
    return await service.request(kind=kind, summary="promote", artefact_digest="m@1",
                                 requested_by=REQUESTER)


class TestSelfApproval:
    async def test_refused_when_another_approver_exists(self) -> None:
        audit = InMemoryAuditSink()
        directory = _Directory(others=True)
        service = ApprovalService(store=InMemoryApprovalStore(), audit=audit,
                                  other_approver_exists=directory)
        request = await _requested(service)

        with pytest.raises(AuthorizationError):
            await service.decide(request.id, approved=True, by=REQUESTER)

        assert (await service.get(request.id)).status.value == "pending", "nothing was decided"
        assert directory.asked == [(REQUESTER, "model_promotion")]
        refused = [e for e in await audit.query() if e.outcome == "refused_self_approval"]
        assert refused and refused[0].actor == REQUESTER

    async def test_a_sole_operator_may_still_self_approve_and_it_is_audited(self) -> None:
        audit = InMemoryAuditSink()
        service = ApprovalService(store=InMemoryApprovalStore(), audit=audit,
                                  other_approver_exists=_Directory(others=False))
        request = await _requested(service)

        decided = await service.decide(request.id, approved=True, by=REQUESTER)

        assert decided.is_actionable
        await service.require(request.id, expected_digest="m@1")
        [event] = await audit.query(action="approval.decide")
        assert event.detail["self_approved"] is True

    async def test_without_a_directory_the_sole_operator_path_is_kept(self) -> None:
        service = ApprovalService(store=InMemoryApprovalStore())
        request = await _requested(service)

        assert (await service.decide(request.id, approved=True, by=REQUESTER)).is_actionable

    async def test_another_person_approves_without_consulting_the_directory(self) -> None:
        directory = _Directory(others=True)
        service = ApprovalService(store=InMemoryApprovalStore(), other_approver_exists=directory)
        request = await _requested(service)

        assert (await service.decide(request.id, approved=True, by="reviewer@example.com")).is_actionable
        assert directory.asked == []

    async def test_rejecting_your_own_request_is_always_allowed(self) -> None:
        service = ApprovalService(store=InMemoryApprovalStore(),
                                  other_approver_exists=_Directory(others=True))
        request = await _requested(service)

        decided = await service.decide(request.id, approved=False, by=REQUESTER)

        assert decided.status.value == "rejected"

    async def test_an_unanswerable_directory_refuses_with_a_retryable_error(self) -> None:
        async def broken(requester, kind):
            raise ConnectionError("database unavailable")

        service = ApprovalService(store=InMemoryApprovalStore(), other_approver_exists=broken)
        request = await _requested(service)

        with pytest.raises(DependencyUnavailableError):
            await service.decide(request.id, approved=True, by=REQUESTER)
        assert (await service.get(request.id)).status.value == "pending"

    async def test_a_synchronous_directory_works_too(self) -> None:
        service = ApprovalService(store=InMemoryApprovalStore(),
                                  other_approver_exists=lambda requester, kind: True)
        request = await _requested(service)

        with pytest.raises(AuthorizationError):
            await service.decide(request.id, approved=True, by=REQUESTER)


class TestRequireRefusesASelfApprovalOnceSomeoneElseCouldDecide:
    async def test_a_sole_operator_self_approval_stops_counting_when_a_second_approver_exists(self) -> None:
        """Granted while its requester was the only approver; by the time it is used, someone
        else could have decided. It must be decided again, by them."""
        directory = _Directory(others=False)
        service = ApprovalService(store=InMemoryApprovalStore(), other_approver_exists=directory)
        request = await _requested(service)
        await service.decide(request.id, approved=True, by=REQUESTER)

        directory.others = True

        with pytest.raises(ApprovalRequiredError):
            await service.require(request.id, expected_digest="m@1")

    async def test_an_approval_by_someone_else_is_unaffected(self) -> None:
        service = ApprovalService(store=InMemoryApprovalStore(),
                                  other_approver_exists=_Directory(others=True))
        request = await _requested(service)
        await service.decide(request.id, approved=True, by="reviewer@example.com")

        await service.require(request.id, expected_digest="m@1")

    async def test_the_injected_check_can_be_set_after_construction(self) -> None:
        """The container builds the service before the legacy operator directory exists; the
        gateway attaches the check once it does."""
        service = ApprovalService(store=InMemoryApprovalStore())
        service.use_other_approver_check(_Directory(others=True))
        request = await _requested(service)

        with pytest.raises(AuthorizationError):
            await service.decide(request.id, approved=True, by=REQUESTER)
