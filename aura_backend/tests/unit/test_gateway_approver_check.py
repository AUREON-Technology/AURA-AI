"""INT-106 wiring (stream B3's check, wired by the gateway): the served container's approvals
refuse a self-approval while another eligible approver exists.

``ApprovalService`` only asks when ``use_other_approver_check`` attached the operator-directory
check; the directory lives outside ``aura_core`` (``api.admin_platform``), so the gateway attaches
it once the container is built. Without the wiring the check is ``None`` and every
self-approval passes.
"""

from __future__ import annotations


def test_the_gateway_container_has_the_operator_directory_check_wired() -> None:
    from api.admin_platform import other_approver_exists
    from aura_service import app

    assert app.state.aura.approvals._other_approver_exists is other_approver_exists
