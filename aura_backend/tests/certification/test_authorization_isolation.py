"""Authorization and isolation, against a running deployment. A mandatory release gate.

`tests/security/test_idor.py` already asserts these invariants locally, through the real ASGI app
with two principals, and it has a completeness guard so a new id-taking route cannot slip in
unclassified. This file does not duplicate that. It answers the question local tests structurally
cannot: **does the deployed system behave that way?**

Two decisions in here are worth reading before changing anything.

**Negative controls come first.** A suite where every probe expects a refusal passes perfectly
against a target that refuses everything -- an expired token, a wrong base URL, a gateway that is
down. So `TestPreflight` proves both identities are live and distinct, and each isolation test
pairs "B is refused" with "A still succeeds". Without the second half, a blanket 500 would
certify as airtight isolation.

**Destructive admin endpoints are not probed, even expecting 403.** If authorization is broken,
the probe *performs* the action -- `/admin/users/{subject}/forget` would delete someone's data to
prove it should not have been able to. Endpoints are therefore classified: probed with a
deliberately non-existent synthetic target (so a failure of authorization is still a no-op), or
refused entirely with the reason recorded. `test_every_admin_route_is_classified` makes that
decision explicit for every route the live deployment exposes, so a new admin route cannot arrive
unexamined.
"""

from __future__ import annotations

import time
import uuid

import pytest

from .conftest import SYNTHETIC_PREFIX, Target, call, explain

pytestmark = pytest.mark.cert

#: Refusals. 401 means the token was rejected outright, which the preflight rules out, so in an
#: isolation probe a 401 is still a refusal but a suspicious one -- it is accepted here and the
#: preflight is what distinguishes "refused because not yours" from "refused because unauthenticated".
REFUSED = frozenset({401, 403, 404})

#: Admin routes safe to probe with a User-role token, because the target named in the path does
#: not exist. If authorization were broken the call is still a no-op.
_ADMIN_SAFE_WITH_FAKE_TARGET: dict[str, tuple[str, str]] = {
    "/api/v3/admin/me": ("GET", ""),
    "/api/v3/admin/status": ("GET", ""),
    "/api/v3/admin/audit": ("GET", ""),
    "/api/v3/admin/approvals": ("GET", ""),
    "/api/v3/admin/indexes": ("GET", ""),
    "/api/v3/admin/scorecard": ("GET", ""),
    "/api/v3/admin/operators": ("GET", ""),
    "/api/v3/admin/models": ("GET", ""),
    "/api/v3/admin/local-models": ("GET", ""),
    "/api/v3/admin/sources": ("GET", ""),
    "/api/v3/admin/learning/jobs": ("GET", ""),
    "/api/v3/admin/learning/recommendation": ("GET", ""),
    "/api/v3/admin/refresh/latest": ("GET", ""),
    # Reads keyed by an id that cannot exist.
    "/api/v3/admin/jobs/{task_id}": ("GET", f"{SYNTHETIC_PREFIX}-no-such-task"),
    "/api/v3/admin/learning/jobs/{job_id}": ("GET", f"{SYNTHETIC_PREFIX}-no-such-job"),
    "/api/v3/admin/learning/datasets/{digest}": ("GET", f"{SYNTHETIC_PREFIX}-no-such-digest"),
    # Track AP (ADR-018..028) and service keys (ADR-029): reads only. The route table these are
    # checked against is the local app's since 2026-11-08, which is what surfaced them.
    "/api/v3/admin/blueprints": ("GET", ""),
    "/api/v3/admin/blueprints/active": ("GET", ""),
    "/api/v3/admin/blueprints/{version}": ("GET", f"{SYNTHETIC_PREFIX}-no-such-version"),
    "/api/v3/admin/blueprints/{version}/evaluation": ("GET", f"{SYNTHETIC_PREFIX}-no-such-version"),
    "/api/v3/admin/connectors": ("GET", ""),
    "/api/v3/admin/connectors/{connector_id}": ("GET", f"{SYNTHETIC_PREFIX}-no-such-connector"),
    "/api/v3/admin/service-keys": ("GET", ""),
    "/api/v3/admin/twin/diff": ("GET", ""),
    "/api/v3/admin/twin/versions": ("GET", ""),
    "/api/v3/admin/twin/versions/{version}": ("GET", f"{SYNTHETIC_PREFIX}-no-such-version"),
    "/api/v3/admin/twin/versions/{version}/plan": ("GET", f"{SYNTHETIC_PREFIX}-no-such-version"),
}

#: Not probed, and why. Each would act on real state if authorization failed, so asking the
#: question is itself the risk. They need an isolated environment with an admin identity, which
#: is a separate exercise from this one.
_ADMIN_UNSAFE_TO_PROBE: dict[str, str] = {
    "/api/v3/admin/index/rebuild": "would rebuild the live vector index",
    "/api/v3/admin/index/rollback": "would roll the live index back to a previous generation",
    "/api/v3/admin/models/import": "would import a model into the live registry",
    "/api/v3/admin/local-models/pull": "would pull a model onto the inference host",
    "/api/v3/admin/local-models/{name:path}": "DELETE would remove a model from the inference host",
    "/api/v3/admin/learning/jobs": "POST would launch a training job",
    "/api/v3/admin/learning/request-approval": "would create a real approval request",
    "/api/v3/admin/approvals/{approval_id}/decide": "would decide a real pending approval",
    "/api/v3/admin/models/{model_id}/transition": "would change a model's lifecycle stage",
    "/api/v3/admin/models/{model_id}/rollback": "would roll a served model back",
    "/api/v3/admin/models/{model_id}/request-promotion": "would request a promotion",
    "/api/v3/admin/operators/{email}/role": "would change a real operator's role",
    "/api/v3/admin/operators/{email}/revoke-sessions": "would sign a real operator out",
    "/api/v3/admin/users/{subject}/forget": "would erase a real user's data",
    "/api/v3/admin/prompts/{template_id}/pin": "would repin the live prompt template",
    "/api/v3/admin/providers/{name}/reset-circuit": "would reset a live provider circuit breaker",
    "/api/v3/admin/sources/{source_id}": "PATCH/DELETE would modify a real knowledge source",
    "/api/v3/admin/knowledge/documents/{document_id}/cag-pin": "would repin live CAG content",
    # Track AP (ADR-018..028) and service keys (ADR-029). Each acts on the live Project Blueprint,
    # a connected system or the key registry if authorization failed.
    "/api/v3/admin/blueprints/rollback": "would roll the active blueprint back",
    "/api/v3/admin/blueprints/{version}/approve": "would approve a blueprint version",
    "/api/v3/admin/blueprints/{version}/deploy": "would deploy a blueprint version",
    "/api/v3/admin/blueprints/{version}/evaluate": "would run an evaluation against a real version",
    "/api/v3/admin/blueprints/{version}/request-approval": "would create a real approval request",
    "/api/v3/admin/connectors": "POST would register a real connector",
    "/api/v3/admin/connectors/{connector_id}": "DELETE would remove a real connector",
    "/api/v3/admin/connectors/{connector_id}/activate": "would activate a real connector",
    "/api/v3/admin/connectors/{connector_id}/disable": "would disable a real connector",
    "/api/v3/admin/connectors/{connector_id}/discover": "would run discovery against a real system",
    "/api/v3/admin/connectors/{connector_id}/health": "POST would probe a real connected system",
    "/api/v3/admin/service-keys": "POST would mint a real service key",
    "/api/v3/admin/service-keys/{key_id}/revoke": "would revoke a real service key",
    "/api/v3/admin/twin/snapshot": "would snapshot the live Project Twin",
    "/api/v3/admin/twin/versions/{version}/compile": "would compile a blueprint from a twin version",
}


def _tag() -> str:
    """A unique, obviously-synthetic label, so anything left behind is attributable."""
    return f"{SYNTHETIC_PREFIX}-{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------------------
# Negative controls. Everything below is worthless if these do not hold.
# ---------------------------------------------------------------------------------------


class TestPreflight:
    def test_both_identities_authenticate(self, client, target: Target) -> None:
        """Without this, a suite of expected-refusals passes against a dead target."""
        for label, token in (("A", target.token_a), ("B", target.token_b)):
            response = call(client, target, "GET", "/api/v3/knowledge/documents", token)
            assert response.status_code == 200, (
                f"token {label} cannot read its own documents, so no isolation result from this "
                f"run means anything: {explain(response, target)}"
            )

    def test_the_two_identities_are_distinct(self, client, target: Target) -> None:
        """Two tokens for the same subject would make every cross-user probe a self-access probe.

        Established behaviourally rather than by reading the `sub` claim: what matters is that the
        server scopes them apart, not what the tokens say.
        """
        tag = _tag()
        created = call(
            client,
            target,
            "POST",
            "/api/v3/knowledge/documents",
            target.token_a,
            json={"title": tag, "content": f"{tag} distinctness probe"},
        )
        assert created.status_code in (200, 201), explain(created, target)
        try:
            listing = call(client, target, "GET", "/api/v3/knowledge/documents", target.token_b)
            assert listing.status_code == 200, explain(listing, target)
            assert tag not in listing.text, (
                "B can see a document A just created, so these two tokens resolve to the same "
                "user or isolation is broken at the listing level"
            )
        finally:
            _cleanup_documents(client, target, target.token_a, tag)

    def test_an_unauthenticated_request_is_refused(self, client, target: Target) -> None:
        """The floor. If this passes unauthenticated, nothing else is worth measuring."""
        response = client.get("/api/v3/knowledge/documents")
        assert response.status_code in (401, 403), explain(response, target)

    def test_a_forged_token_is_refused(self, client, target: Target) -> None:
        """A syntactically plausible bearer token with no valid signature."""
        response = call(
            client, target, "GET", "/api/v3/knowledge/documents", "not.a.real.token"
        )
        assert response.status_code in (401, 403), explain(response, target)


# ---------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------


def _cleanup_documents(client, target: Target, token: str, tag: str) -> None:
    """Best effort, and deliberately scoped to our own tag.

    Never raises: a cleanup failure must not mask or replace the assertion that was being made.
    """
    try:
        listing = call(client, target, "GET", "/api/v3/knowledge/documents", token)
        if listing.status_code != 200:
            return
        for doc in listing.json().get("documents", []) or []:
            ident = doc.get("document_id") or doc.get("id")
            title = doc.get("title") or ""
            if ident and tag in (title + str(ident)):
                call(client, target, "DELETE", f"/api/v3/knowledge/documents/{ident}", token)
    except Exception:  # noqa: BLE001 - cleanup must never fail a test
        pass


@pytest.fixture()
def alice_document(client, target: Target):
    """A document owned by A, removed afterwards whatever the test did."""
    tag = _tag()
    response = call(
        client,
        target,
        "POST",
        "/api/v3/knowledge/documents",
        target.token_a,
        json={
            "title": tag,
            "content": (
                f"{tag}. Synthetic certification content owned by user A. "
                "Reference marker PELICAN-CERT-7731."
            ),
        },
    )
    assert response.status_code in (200, 201), explain(response, target)
    payload = response.json()
    document_id = payload.get("document_id") or payload.get("id")
    assert document_id, f"ingest returned no document id: {explain(response, target)}"
    try:
        yield {"id": document_id, "tag": tag, "marker": "PELICAN-CERT-7731"}
    finally:
        _cleanup_documents(client, target, target.token_a, tag)


# ---------------------------------------------------------------------------------------
# Cross-user object access
# ---------------------------------------------------------------------------------------


class TestUserBCannotReachUserAsData:
    def test_b_cannot_see_as_document_in_a_listing(self, client, target, alice_document) -> None:
        response = call(client, target, "GET", "/api/v3/knowledge/documents", target.token_b)
        assert response.status_code == 200, explain(response, target)
        assert alice_document["tag"] not in response.text
        assert alice_document["id"] not in response.text

    def test_b_cannot_retrieve_as_content_by_search(self, client, target, alice_document) -> None:
        """The leak that matters most: retrieval reaching across the tenant boundary, where the
        content would then be fed to a model as context."""
        response = call(
            client,
            target,
            "POST",
            "/api/v3/knowledge/search",
            target.token_b,
            json={"query": alice_document["marker"], "limit": 10},
        )
        assert response.status_code == 200, explain(response, target)
        assert alice_document["marker"] not in response.text, (
            "B's search returned A's private content -- a cross-tenant retrieval leak"
        )

    def test_b_cannot_delete_as_document(self, client, target, alice_document) -> None:
        response = call(
            client,
            target,
            "DELETE",
            f"/api/v3/knowledge/documents/{alice_document['id']}",
            target.token_b,
        )
        assert response.status_code in REFUSED, explain(response, target)

        # The other half: it must still be there for its owner.
        listing = call(client, target, "GET", "/api/v3/knowledge/documents", target.token_a)
        assert listing.status_code == 200, explain(listing, target)
        assert alice_document["tag"] in listing.text, (
            "B's delete was reported as refused but A's document is gone"
        )

    def test_a_can_delete_her_own_document(self, client, target) -> None:
        """Negative control for the test above: the refusal must be about ownership, not a
        broken delete route."""
        tag = _tag()
        created = call(
            client,
            target,
            "POST",
            "/api/v3/knowledge/documents",
            target.token_a,
            json={"title": tag, "content": f"{tag} own-delete control"},
        )
        assert created.status_code in (200, 201), explain(created, target)
        document_id = created.json().get("document_id") or created.json().get("id")

        deleted = call(
            client, target, "DELETE", f"/api/v3/knowledge/documents/{document_id}", target.token_a
        )
        assert deleted.status_code in (200, 202, 204), explain(deleted, target)

    def test_b_cannot_overwrite_as_document_by_reusing_its_id(
        self, client, target, alice_document
    ) -> None:
        """Ingest accepts a caller-supplied `document_id`, which makes id reuse a write-side IDOR."""
        response = call(
            client,
            target,
            "POST",
            "/api/v3/knowledge/documents",
            target.token_b,
            json={
                "title": f"{_tag()}-overwrite-attempt",
                "content": "Content written by B over A's document id.",
                "document_id": alice_document["id"],
            },
        )
        # Either refused, or accepted into B's own namespace -- both are correct. What must not
        # happen is A's content changing.
        assert response.status_code in REFUSED or response.status_code in (200, 201), explain(
            response, target
        )

        search = call(
            client,
            target,
            "POST",
            "/api/v3/knowledge/search",
            target.token_a,
            json={"query": alice_document["marker"], "limit": 10},
        )
        assert search.status_code == 200, explain(search, target)
        assert alice_document["marker"] in search.text, (
            "B's id reuse destroyed or replaced A's content"
        )

    def test_b_cannot_read_as_conversation_by_session_id(self, client, target) -> None:
        """A session id is guessable and arrives in a body rather than a path, which makes it the
        easiest identifier in the API to substitute."""
        session_id = _tag()
        opened = call(
            client,
            target,
            "POST",
            "/api/v3/chat",
            target.token_a,
            json={
                "message": f"Synthetic certification probe {session_id}. Marker HERON-CERT-4412.",
                "session_id": session_id,
                "use_knowledge": False,
                "use_memory": False,
                "use_tools": False,
                "max_output_tokens": 16,
            },
        )
        if opened.status_code not in (200, 201):
            pytest.skip(
                "could not open a conversation as A, so conversation isolation is untested this "
                f"run (not a pass): {explain(opened, target)}"
            )

        response = call(
            client,
            target,
            "POST",
            "/api/v3/chat",
            target.token_b,
            json={
                "message": "What did the previous message in this session say?",
                "session_id": session_id,
                "use_knowledge": False,
                "use_memory": False,
                "use_tools": False,
                "max_output_tokens": 64,
            },
        )
        if response.status_code in REFUSED:
            return
        assert response.status_code == 200, explain(response, target)
        assert "HERON-CERT-4412" not in response.text, (
            "B's turn was answered with A's conversation history -- session ids are not scoped "
            "to their owner"
        )

    def test_b_cannot_read_as_memory(self, client, target) -> None:
        marker = "KESTREL-CERT-9920"
        written = call(
            client,
            target,
            "POST",
            "/api/v3/memory",
            target.token_a,
            json={"content": f"{_tag()} synthetic memory. Marker {marker}.", "kind": "semantic"},
        )
        if written.status_code not in (200, 201):
            pytest.skip(
                f"could not write a memory as A; memory isolation untested this run: "
                f"{explain(written, target)}"
            )

        response = call(
            client, target, "GET", f"/api/v3/memory?query={marker}&limit=10", target.token_b
        )
        assert response.status_code == 200, explain(response, target)
        assert marker not in response.text, "B's memory search returned A's memory"

    def test_b_cannot_read_as_agent_run(self, client, target) -> None:
        """No run is created for this: a plausible-looking id owned by nobody must not leak, and
        a real id owned by A must not either. The first is what is safe to assert here."""
        response = call(
            client,
            target,
            "GET",
            f"/api/v3/agents/runs/{_tag()}",
            target.token_b,
        )
        assert response.status_code in REFUSED, explain(response, target)

    def test_b_cannot_delete_as_workspace(self, client, target) -> None:
        response = call(
            client,
            target,
            "DELETE",
            f"/api/v3/engineering/workspaces/{_tag()}",
            target.token_b,
        )
        assert response.status_code in REFUSED, explain(response, target)


# ---------------------------------------------------------------------------------------
# Privilege escalation
# ---------------------------------------------------------------------------------------


class TestANormalUserCannotActAsAdmin:
    @pytest.mark.parametrize(
        "path,method,fake",
        [(p, m, f) for p, (m, f) in sorted(_ADMIN_SAFE_WITH_FAKE_TARGET.items())],
        ids=lambda v: v if isinstance(v, str) and v.startswith("/") else "",
    )
    def test_admin_route_is_refused(self, client, target, path: str, method: str, fake: str) -> None:
        """Probed with a non-existent target where the route takes one, so a broken check is
        still a no-op rather than an action taken to prove it could be."""
        concrete = path
        if "{" in path:
            head, _, rest = path.partition("{")
            _, _, tail = rest.partition("}")
            concrete = f"{head}{fake}{tail}"

        response = call(client, target, method, concrete, target.token_a)
        assert response.status_code in (401, 403), (
            f"a User-role token reached an admin route: {explain(response, target)}"
        )

    def test_every_admin_route_is_classified(self) -> None:
        """Mirrors the local suite's completeness guard against this release's own route table.

        A new admin route must be declared either safe to probe or unsafe-with-a-reason before
        this passes. Silence is the failure mode -- an admin route nobody classified is an admin
        route nobody checked.

        The table comes from the app this checkout builds, not from the deployment: since
        2026-11-08 `/openapi.json` is not served in staging or production
        (`tests/unit/test_openapi_exposure.py`). The suite is run from the release commit it
        certifies -- the operator's responsibility, which this test cannot check from here -- so
        the local table is the deployed table.
        """
        from aura_core.api import Principal, build_app
        from aura_core.config import load_settings
        from aura_core.container import Container
        from aura_core.domain import UserId

        async def authenticate(request) -> Principal:
            return Principal(user_id=UserId("cert-route-table"), role="Admin")

        app = build_app(Container(load_settings(refresh=True, validate=False)), authenticator=authenticate)
        live_admin = {p for p in app.openapi().get("paths", {}) if "/admin/" in p}
        classified = set(_ADMIN_SAFE_WITH_FAKE_TARGET) | set(_ADMIN_UNSAFE_TO_PROBE)
        unclassified = sorted(live_admin - classified)

        assert not unclassified, (
            "admin routes on the deployment that this suite neither probes nor justifies "
            "skipping:\n  " + "\n  ".join(unclassified)
        )

    def test_the_unsafe_list_carries_a_reason_for_each_entry(self) -> None:
        """A skip with no stated reason is how a gap becomes permanent."""
        for path, reason in _ADMIN_UNSAFE_TO_PROBE.items():
            assert reason.strip(), f"{path} is skipped without a recorded reason"


# ---------------------------------------------------------------------------------------
# Cleanup accounting
# ---------------------------------------------------------------------------------------


class TestSyntheticDataIsCleanedUp:
    def test_no_synthetic_documents_remain_for_either_user(self, client, target) -> None:
        """Runs last by name. Anything this suite created carries SYNTHETIC_PREFIX, so a leftover
        is both detectable and attributable -- which is the point of tagging it that way."""
        time.sleep(1)  # let any async delete settle before accounting
        leftovers: list[str] = []
        for label, token in (("A", target.token_a), ("B", target.token_b)):
            listing = call(client, target, "GET", "/api/v3/knowledge/documents", token)
            if listing.status_code != 200:
                continue
            for doc in listing.json().get("documents", []) or []:
                title = str(doc.get("title") or "")
                if SYNTHETIC_PREFIX in title:
                    leftovers.append(f"{label}: {title}")

        assert not leftovers, (
            "synthetic certification documents were left on the target. Remove them, and treat "
            "this as a bug in this suite's cleanup rather than in the product:\n  "
            + "\n  ".join(leftovers)
        )
