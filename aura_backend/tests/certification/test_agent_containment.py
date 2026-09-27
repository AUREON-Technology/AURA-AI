"""Agent containment against a running deployment. A mandatory release gate.

**Read this before adding escape tests here.** The escape invariants themselves --
relative-path escape, absolute-path confinement, symlink read and write, null bytes,
all-or-nothing writes, git refspec spelling -- are already covered by 11 tests in
`tests/security/test_workspace_boundaries.py`, against the real boundary code, plus 12 SSRF
tests in `test_ssrf.py`. Duplicating them over HTTP would add no invariant and would need a live
sandbox to say anything at all.

And a live sandbox is the problem. `SandboxSettings` requires three things together:
`AURA_SANDBOX_ENABLED`, the `docker` package importable *with a daemon actually reachable*
(`Container._build_sandbox_runner` probes it), and a `host_root` bind-mounted into the gateway at
the same absolute path. The Railway Control Plane is one container built from
`aura_backend/Dockerfile` with no Docker daemon, no `docker-socket-proxy` and no bind mounts, so
that probe cannot succeed there. The compose services carrying `profiles: [sandbox]` exist only in
`deploy/docker-compose.prod.yml`, on the VPS.

So what this file does is narrow and deliberate:

1. **Detect** whether the engineer/sandbox surface is live on the target, and report it. A target
   without it yields NOT APPLICABLE -- never PASS. A gate cannot be certified against a feature
   that is not deployed, and recording a skip as a pass is the exact failure this whole audit
   exists to prevent.
2. **Assert the authorization boundary around containment**, which *is* live everywhere: a
   User-role caller must not create, list or delete engineer workspaces, because
   `engineer_agent_roles` defaults empty for the stated reason that the persona runs shell
   commands and pushes commits.

Promoting the escape invariants to PRODUCTION TESTED needs a target with the sandbox genuinely
enabled, and the repository already has the right instrument for that:
`deploy/scripts/verify-engineer-workspaces.sh`, run on the VPS, which drives the checks against a
real daemon through the gateway's own wiring and writes a report to `docs/reports/`. CLAUDE.md is
explicit that nothing short of that report proves the phase, and that a green `pytest` cannot,
because the default suite's runner never touches the Docker API. This file does not try to
replace it.
"""

from __future__ import annotations

import uuid

import pytest

from .conftest import SYNTHETIC_PREFIX, Target, call, explain

pytestmark = pytest.mark.cert

REFUSED = frozenset({401, 403, 404})


def _tag() -> str:
    return f"{SYNTHETIC_PREFIX}-{uuid.uuid4().hex[:12]}"


@pytest.fixture(scope="module")
def engineer_surface(client, target: Target) -> dict:
    """What the target actually exposes, decided by asking it rather than by assuming.

    Returns the workspace-listing status so each test can distinguish "refused because this
    caller may not" from "absent because the feature is not deployed" -- two results that look
    identical in a status code and mean opposite things for certification.
    """
    listing = call(client, target, "GET", "/api/v3/engineering/workspaces", target.token_a)
    return {"status": listing.status_code, "body": listing.text[:400]}


class TestTheGateIsHonestAboutWhatIsDeployed:
    def test_the_sandbox_surface_is_reported_not_assumed(
        self, client, target: Target, engineer_surface
    ) -> None:
        """Records the target's capability. Never fails on absence -- absence is a finding for the
        readiness matrix, not a defect in the product."""
        status = engineer_surface["status"]
        if status in REFUSED:
            pytest.skip(
                "the engineer/sandbox surface is not available to this identity on this target "
                f"(GET /api/v3/engineering/workspaces -> {status}). Agent containment is "
                "NOT APPLICABLE here, not passing. The escape invariants are covered locally by "
                "tests/security/test_workspace_boundaries.py; promoting them to PRODUCTION "
                "TESTED requires deploy/scripts/verify-engineer-workspaces.sh on a host where "
                "AURA_SANDBOX_ENABLED is set and a Docker daemon is reachable."
            )
        assert status == 200, explain_status(engineer_surface)

    def test_a_reachable_sandbox_would_still_need_the_vps_drill(self, engineer_surface) -> None:
        """A guard against a future contributor reading a 200 here as containment certified.

        Listing workspaces proves the route is mounted. It proves nothing about whether a command
        can escape one, which needs a real daemon and is what the VPS script measures.
        """
        if engineer_surface["status"] != 200:
            pytest.skip("surface not available; nothing to caveat")
        pytest.skip(
            "workspace listing is reachable, which proves the route is mounted and NOT that "
            "containment holds. Run deploy/scripts/verify-engineer-workspaces.sh on the VPS for "
            "that evidence."
        )


def explain_status(surface: dict) -> str:
    return f"GET /api/v3/engineering/workspaces -> {surface['status']}\n{surface['body']}"


class TestAUserRoleCannotReachEngineerWorkspaces:
    """Live on every target, sandbox or not, because it is an authorization question rather than a
    runtime one. `engineer_agent_roles` defaults to empty specifically because this persona can run
    shell commands and push commits -- so a plain User reaching it is a privilege escalation into
    the most dangerous surface in the product.
    """

    def test_a_user_cannot_create_a_workspace(self, client, target: Target) -> None:
        response = call(
            client,
            target,
            "POST",
            "/api/v3/engineering/workspaces",
            target.token_a,
            json={"repo": f"{SYNTHETIC_PREFIX}/nonexistent", "ref": "main"},
        )
        assert response.status_code in REFUSED | {422}, (
            "a User-role caller was allowed to create an engineer workspace, which is the "
            f"entry point to shell execution: {explain(response, target)}"
        )

    def test_a_user_cannot_delete_another_identitys_workspace(
        self, client, target: Target
    ) -> None:
        """Named workspace does not exist, so a broken check is still a no-op."""
        response = call(
            client,
            target,
            "DELETE",
            f"/api/v3/engineering/workspaces/{_tag()}",
            target.token_b,
        )
        assert response.status_code in REFUSED, explain(response, target)

    def test_a_user_cannot_list_another_identitys_engineer_runs(
        self, client, target: Target
    ) -> None:
        """If runs are visible across identities, a workspace's command output and diffs are too."""
        response = call(client, target, "GET", "/api/v3/engineering/runs", target.token_b)
        if response.status_code in REFUSED:
            return
        assert response.status_code == 200, explain(response, target)
        assert SYNTHETIC_PREFIX not in response.text, (
            "B's engineer-run listing contains synthetic records created under A"
        )

    def test_the_host_agent_websocket_refuses_an_unauthenticated_upgrade(
        self, client, target: Target
    ) -> None:
        """`/api/v3/host/agent` is how the desktop daemon acts on a real machine's files and
        terminal. An unauthenticated upgrade reaching it would be the most serious containment
        failure available, so it is asserted even though the happy path needs a paired daemon.
        """
        response = client.get(
            "/api/v3/host/agent",
            headers={
                "Connection": "Upgrade",
                "Upgrade": "websocket",
                "Sec-WebSocket-Version": "13",
                "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
            },
        )
        assert response.status_code != 101, (
            "an unauthenticated WebSocket upgrade to the host agent succeeded"
        )
        assert response.status_code in REFUSED | {400, 426}, explain(response, target)
