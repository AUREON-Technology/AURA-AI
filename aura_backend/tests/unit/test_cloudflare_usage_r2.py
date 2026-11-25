"""Workers AI usage and R2 bucket status: the adapter, the in-memory account and the engine
(console plan, stage 6, C3).

``HttpCloudflareAccount`` is driven over an in-process transport with *recorded* exchanges.

* **Usage** is Cloudflare's GraphQL dataset ``aiInferenceAdaptiveGroups`` (probed live on
  2026-11-23: it answers a ``count`` query filtered by ``datetimeHour_geq``). The name of its neuron
  field is **not confirmed**, so the adapter discovers a sum field matching ``/neuron/i`` by
  introspecting ``AccountAiInferenceAdaptiveGroupsSum``; the field names used below are synthetic
  stand-ins for that discovery and prove only that discovery is followed, not what Cloudflare calls
  the field. With none, usage is requests only and says so.
* **R2** is ``GET /accounts/{id}/r2/buckets``. Probed live: this account answers HTTP 403 with error
  code 10042 ("enable R2 through the Cloudflare Dashboard"), which is the state ``not_enabled``, not
  an error and not a permission problem.

Every state is a named result, never an exception.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from aura_core.adapters.cloudflare_account import HttpCloudflareAccount, InMemoryCloudflareAccount
from aura_core.config.external_inference import WORKERS_AI_API_ROOT
from aura_core.control.cloudflare import FREE_ALLOWANCE_NEURONS, CloudflareControl
from aura_core.domain.control import Platform, PlatformCredential
from aura_core.domain.errors import DependencyUnavailableError, ValidationError
from aura_core.ports.control_plane import (
    CloudflareAccount,
    CloudflareBucket,
    CloudflareR2,
    CloudflareUsage,
)

ACCOUNT = "0123456789abcdef0123456789abcdef"
TOKEN = "mgmt-token-zq19-never-shown"
DAY = date(2026, 11, 23)
LEAK = "vendor detail that must not leak"


async def _token() -> str:
    return TOKEN


def _adapter(handler) -> HttpCloudflareAccount:
    return HttpCloudflareAccount(token_source=_token, api_root=WORKERS_AI_API_ROOT,
                                 transport=httpx.MockTransport(handler))


def _json(status: int, body) -> httpx.Response:
    return httpx.Response(status, json=body)


# -- usage ---------------------------------------------------------------------------------------


def _introspection(*fields: str) -> dict:
    return {"data": {"__type": {"name": "AccountAiInferenceAdaptiveGroupsSum",
                                "fields": [{"name": f} for f in fields]}}}


def _groups(*rows: dict) -> dict:
    return {"data": {"viewer": {"accounts": [{"aiInferenceAdaptiveGroups": list(rows)}]}}, "errors": None}


class Graphql:
    """A recorded GraphQL exchange: the introspection answer and the data answer."""

    def __init__(self, *, introspect=None, data=None, introspect_status=200, data_status=200) -> None:
        self.introspect = introspect if introspect is not None else _introspection("totalInputTokens", "totalNeurons")
        self.data = data if data is not None else _groups({"count": 42, "sum": {"totalNeurons": 1234.5}})
        self.introspect_status, self.data_status = introspect_status, data_status
        self.requests: list[httpx.Request] = []

    def queries(self) -> list[str]:
        return [json.loads(r.content)["query"] for r in self.requests]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        query = json.loads(request.content)["query"]
        if "__type" in query:
            return _json(self.introspect_status, self.introspect)
        return _json(self.data_status, self.data)


class TestHttpUsage:
    async def test_requests_and_neurons_for_the_utc_day(self) -> None:
        graphql = Graphql()
        usage = await _adapter(graphql).usage(ACCOUNT, day=DAY)
        assert usage == CloudflareUsage(state="ok", day="2026-11-23", requests=42, neurons=1234.5,
                                        neurons_field="totalNeurons")

    async def test_the_requests_are_graphql_posts_with_the_management_token(self) -> None:
        graphql = Graphql()
        await _adapter(graphql).usage(ACCOUNT, day=DAY)
        assert len(graphql.requests) == 2
        for request in graphql.requests:
            assert request.method == "POST"
            assert request.url.path == "/client/v4/graphql"
            assert request.headers["authorization"] == f"Bearer {TOKEN}"
        introspection, data = graphql.queries()
        assert "AccountAiInferenceAdaptiveGroupsSum" in introspection
        assert "aiInferenceAdaptiveGroups" in data and f'"{ACCOUNT}"' in data
        assert 'datetimeHour_geq: "2026-11-23T00:00:00Z"' in data
        assert "count" in data and "totalNeurons" in data

    async def test_the_neuron_field_is_whichever_field_introspection_names(self) -> None:
        graphql = Graphql(introspect=_introspection("totalInputTokens", "sumNeuronCost"),
                          data=_groups({"count": 3, "sum": {"sumNeuronCost": 7}}))
        usage = await _adapter(graphql).usage(ACCOUNT, day=DAY)
        assert (usage.neurons, usage.neurons_field) == (7.0, "sumNeuronCost")

    @pytest.mark.parametrize(
        "introspect",
        [_introspection("totalInputTokens", "totalOutputTokens"), _introspection(),
         {"data": {"__type": None}}, {"errors": [{"message": LEAK}]}, {"data": None}, ["x"]],
    )
    async def test_without_a_neuron_field_it_is_requests_only_and_says_so(self, introspect) -> None:
        graphql = Graphql(introspect=introspect, data=_groups({"count": 9}))
        usage = await _adapter(graphql).usage(ACCOUNT, day=DAY)
        assert (usage.state, usage.requests, usage.neurons, usage.neurons_field) == ("ok", 9, None, "")
        assert "sum" not in graphql.queries()[1]

    async def test_introspection_that_fails_outright_still_counts_requests(self) -> None:
        graphql = Graphql(introspect_status=500, introspect={"errors": [{"message": LEAK}]})
        usage = await _adapter(graphql).usage(ACCOUNT, day=DAY)
        assert (usage.state, usage.requests, usage.neurons) == ("ok", 42, None)

    async def test_a_day_with_no_traffic_is_zero_requests(self) -> None:
        graphql = Graphql(data=_groups())
        usage = await _adapter(graphql).usage(ACCOUNT, day=DAY)
        assert (usage.state, usage.requests, usage.neurons) == ("ok", 0, 0.0)
        quiet = await _adapter(Graphql(introspect=_introspection("totalInputTokens"), data=_groups())).usage(ACCOUNT, day=DAY)
        assert (quiet.requests, quiet.neurons) == (0, None)

    async def test_a_wrong_guess_at_the_neuron_field_falls_back_to_requests(self) -> None:
        graphql = Graphql()
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            query = json.loads(request.content)["query"]
            if "__type" in query:
                return graphql(request)
            calls["n"] += 1
            if "totalNeurons" in query:
                return _json(200, {"data": None, "errors": [{"message": "unknown field totalNeurons"}]})
            return _json(200, _groups({"count": 5}))

        usage = await _adapter(handler).usage(ACCOUNT, day=DAY)
        assert (usage.state, usage.requests, usage.neurons, usage.neurons_field) == ("ok", 5, None, "")
        assert calls["n"] == 2

    async def test_the_default_day_is_today_in_utc(self) -> None:
        graphql = Graphql()
        usage = await _adapter(graphql).usage(ACCOUNT)
        today = datetime.now(timezone.utc).date()
        assert usage.day in {today.isoformat(), (today - timedelta(days=1)).isoformat()}
        assert f'datetimeHour_geq: "{usage.day}T00:00:00Z"' in graphql.queries()[1]

    @pytest.mark.parametrize("status", [401, 403])
    async def test_a_refused_token_is_no_permission(self, status: int) -> None:
        body = {"success": False, "errors": [{"code": 10000, "message": LEAK}]}
        usage = await _adapter(lambda request: _json(status, body)).usage(ACCOUNT, day=DAY)
        assert (usage.state, usage.requests, usage.neurons) == ("no_permission", None, None)
        assert LEAK not in repr(usage)

    @pytest.mark.parametrize("message", [
        "the viewer does not have access to the path", "not authorized to access this resource",
        "authz", "Permission denied",
    ])
    async def test_a_graphql_permission_error_is_no_permission(self, message: str) -> None:
        graphql = Graphql(data={"data": None, "errors": [{"message": message, "extensions": {"code": "authz"}}]})
        usage = await _adapter(graphql).usage(ACCOUNT, day=DAY)
        assert usage.state == "no_permission"

    async def test_a_permission_error_that_arrives_on_the_introspection_step_counts_too(self) -> None:
        graphql = Graphql(introspect_status=403, introspect={"success": False})
        usage = await _adapter(graphql).usage(ACCOUNT, day=DAY)
        assert usage.state == "no_permission" and len(graphql.requests) == 1

    @pytest.mark.parametrize("status", [429, 500, 502])
    async def test_a_server_error_is_unavailable_and_vendor_text_is_not_passed_on(self, status: int) -> None:
        graphql = Graphql(data_status=status, data={"errors": [{"message": LEAK}]})
        usage = await _adapter(graphql).usage(ACCOUNT, day=DAY)
        assert usage.state == "unavailable" and usage.reason == f"http_{status}"
        assert LEAK not in repr(usage)

    async def test_a_failed_connection_is_unavailable(self) -> None:
        def boom(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError(LEAK)

        usage = await _adapter(boom).usage(ACCOUNT, day=DAY)
        assert (usage.state, usage.reason) == ("unavailable", "connection_failed")
        assert LEAK not in repr(usage)

    @pytest.mark.parametrize("data", [
        {"data": {"viewer": {"accounts": []}}}, {"data": {"viewer": None}}, {"data": None},
        {"data": {"viewer": {"accounts": [{"aiInferenceAdaptiveGroups": "x"}]}}},
        {"data": {"viewer": {"accounts": [{"aiInferenceAdaptiveGroups": [{"count": "many"}]}]}}},
        ["x"], {"errors": [{"message": "something broke"}]},
    ])
    async def test_an_unexpected_shape_is_unavailable_never_zero(self, data) -> None:
        usage = await _adapter(Graphql(data=data)).usage(ACCOUNT, day=DAY)
        assert usage.state == "unavailable" and usage.requests is None

    async def test_a_body_that_is_not_json_is_unavailable(self) -> None:
        usage = await _adapter(lambda request: httpx.Response(200, text="<html>")).usage(ACCOUNT, day=DAY)
        assert usage.state == "unavailable"

    @pytest.mark.parametrize("account", ["", "acct-1", ACCOUNT.upper(), ACCOUNT + "0", ACCOUNT[:-1] + '"}'])
    async def test_a_malformed_account_id_is_refused_before_any_request(self, account: str) -> None:
        def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
            raise AssertionError("no request may be made")

        with pytest.raises(ValidationError):
            await _adapter(handler).usage(account, day=DAY)

    async def test_no_api_root_means_not_configured(self) -> None:
        adapter = HttpCloudflareAccount(token_source=_token, api_root="")
        with pytest.raises(ValidationError) as caught:
            await adapter.usage(ACCOUNT, day=DAY)
        assert caught.value.details["reason"] == "cloudflare_not_configured"
        with pytest.raises(ValidationError):
            await adapter.r2_buckets(ACCOUNT)


# -- R2 ------------------------------------------------------------------------------------------

BUCKETS = {
    "success": True, "errors": [], "messages": [],
    "result": {"buckets": [
        {"name": "aura-backups", "creation_date": "2026-10-01T09:30:00.000Z", "location": "WEUR",
         "storage_class": "Standard"},
        {"name": "scratch", "creation_date": "2026-11-02T00:00:00.000Z"},
        {"name": "no-lock-api", "creation_date": "2026-11-03T00:00:00.000Z"},
        {"name": "lock-errors", "creation_date": "2026-11-04T00:00:00.000Z"},
        {"creation_date": "no-name-here"},  # unreadable: skipped
        "not-an-object",                    # unreadable: skipped
    ]},
}
LOCKS = {
    "aura-backups": (200, {"success": True, "result": {"rules": [{"id": "keep-90", "enabled": True}]}}),
    "scratch": (200, {"success": True, "result": {"rules": []}}),
    "no-lock-api": (404, {"success": False}),
    "lock-errors": (500, {"success": False}),
}


class R2Exchange:
    def __init__(self, *, listing=(200, BUCKETS), locks=LOCKS) -> None:
        self.listing, self.locks = listing, locks
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/r2/buckets"):
            return _json(*self.listing)
        assert path.endswith("/lock"), path
        name = path.split("/r2/buckets/", 1)[1].split("/", 1)[0]
        return _json(*self.locks[name])


class TestHttpR2:
    async def test_the_recorded_bucket_list_is_parsed_with_each_buckets_lock_rules(self) -> None:
        r2 = await _adapter(R2Exchange()).r2_buckets(ACCOUNT)
        assert r2.state == "ok"
        by_name = {b.name: b for b in r2.buckets}
        assert list(by_name) == ["aura-backups", "scratch", "no-lock-api", "lock-errors"]
        backups = by_name["aura-backups"]
        assert (backups.created, backups.location, backups.storage_class) == (
            "2026-10-01T09:30:00.000Z", "WEUR", "Standard")
        assert [by_name[n].lock_rules for n in by_name] == [1, 0, None, None]

    async def test_it_is_read_only_gets_with_the_management_token(self) -> None:
        exchange = R2Exchange()
        await _adapter(exchange).r2_buckets(ACCOUNT)
        assert {r.method for r in exchange.requests} == {"GET"}
        listing = exchange.requests[0]
        assert listing.url.path == f"/client/v4/accounts/{ACCOUNT}/r2/buckets"
        assert all(r.headers["authorization"] == f"Bearer {TOKEN}" for r in exchange.requests)
        assert {r.url.path for r in exchange.requests[1:]} == {
            f"/client/v4/accounts/{ACCOUNT}/r2/buckets/{n}/lock" for n in LOCKS
        }

    async def test_a_lock_lookup_that_fails_never_fails_the_list(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/lock"):
                raise httpx.ConnectError("down")
            return _json(200, BUCKETS)

        r2 = await _adapter(handler).r2_buckets(ACCOUNT)
        assert r2.state == "ok" and len(r2.buckets) == 4 and all(b.lock_rules is None for b in r2.buckets)

    async def test_an_empty_account_has_no_buckets(self) -> None:
        r2 = await _adapter(R2Exchange(listing=(200, {"success": True, "result": {"buckets": []}}))).r2_buckets(ACCOUNT)
        assert (r2.state, r2.buckets) == ("ok", ())

    async def test_a_hostile_bucket_name_never_reaches_a_url(self) -> None:
        body = {"success": True, "result": {"buckets": [{"name": "../../accounts"}, {"name": "ok-name"}]}}
        exchange = R2Exchange(listing=(200, body), locks={"ok-name": (200, {"result": {"rules": []}})})
        r2 = await _adapter(exchange).r2_buckets(ACCOUNT)
        assert [b.lock_rules for b in r2.buckets] == [None, 0]
        assert not any(".." in r.url.path for r in exchange.requests)

    @pytest.mark.parametrize("status", [400, 403])
    async def test_error_10042_is_not_enabled_not_an_error(self, status: int) -> None:
        body = {"success": False, "errors": [{"code": 10042, "message": "Please enable R2 through the Cloudflare Dashboard."}]}
        r2 = await _adapter(R2Exchange(listing=(status, body))).r2_buckets(ACCOUNT)
        assert (r2.state, r2.buckets) == ("not_enabled", ())

    @pytest.mark.parametrize("status", [401, 403])
    async def test_a_refused_token_is_no_permission(self, status: int) -> None:
        body = {"success": False, "errors": [{"code": 10000, "message": LEAK}]}
        r2 = await _adapter(R2Exchange(listing=(status, body))).r2_buckets(ACCOUNT)
        assert r2.state == "no_permission" and LEAK not in repr(r2)

    @pytest.mark.parametrize("status", [404, 429, 500])
    async def test_other_failures_are_unavailable(self, status: int) -> None:
        r2 = await _adapter(R2Exchange(listing=(status, {"errors": [{"message": LEAK}]}))).r2_buckets(ACCOUNT)
        assert (r2.state, r2.reason) == ("unavailable", f"http_{status}") and LEAK not in repr(r2)

    async def test_a_failed_connection_is_unavailable(self) -> None:
        def boom(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError(LEAK)

        r2 = await _adapter(boom).r2_buckets(ACCOUNT)
        assert (r2.state, r2.reason) == ("unavailable", "connection_failed")

    @pytest.mark.parametrize("body", [
        {"success": True, "result": {"buckets": "x"}}, {"success": True, "result": {}}, {"success": True},
        {"success": False, "result": {"buckets": []}}, ["x"],
    ])
    async def test_an_unexpected_shape_is_unavailable_never_an_empty_list(self, body) -> None:
        r2 = await _adapter(R2Exchange(listing=(200, body))).r2_buckets(ACCOUNT)
        assert r2.state == "unavailable"

    async def test_a_body_that_is_not_json_is_unavailable(self) -> None:
        r2 = await _adapter(lambda request: httpx.Response(200, text="<html>")).r2_buckets(ACCOUNT)
        assert r2.state == "unavailable"

    async def test_a_malformed_account_id_is_refused_before_any_request(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
            raise AssertionError("no request may be made")

        with pytest.raises(ValidationError):
            await _adapter(handler).r2_buckets("acct-1")

    async def test_lock_lookups_are_capped(self) -> None:
        body = {"success": True, "result": {"buckets": [{"name": f"bucket-{i:03d}"} for i in range(80)]}}
        exchange = R2Exchange(listing=(200, body), locks={f"bucket-{i:03d}": (200, {"result": {"rules": []}}) for i in range(80)})
        r2 = await _adapter(exchange).r2_buckets(ACCOUNT)
        assert len(r2.buckets) == 80
        assert len(exchange.requests) <= 1 + 50
        assert sum(1 for b in r2.buckets if b.lock_rules is None) >= 30

    async def test_the_adapter_satisfies_the_port(self) -> None:
        assert isinstance(_adapter(R2Exchange()), CloudflareAccount)
        assert isinstance(InMemoryCloudflareAccount(), CloudflareAccount)


# -- the in-memory account -----------------------------------------------------------------------


class TestInMemory:
    async def test_it_reports_what_was_recorded_for_the_day(self) -> None:
        account = InMemoryCloudflareAccount()
        account.record_usage(DAY, requests=10, neurons=250.0)
        account.record_usage(DAY, requests=5, neurons=50.0)
        today = await account.usage(ACCOUNT, day=DAY)
        other = await account.usage(ACCOUNT, day=DAY + timedelta(days=1))
        assert (today.state, today.requests, today.neurons) == ("ok", 15, 300.0)
        assert (other.requests, other.neurons) == (0, 0.0)

    async def test_it_can_have_no_neuron_figure_or_no_permission_or_be_down(self) -> None:
        account = InMemoryCloudflareAccount()
        account.record_usage(DAY, requests=4, neurons=9.0)
        account.neurons_reported = False
        assert (await account.usage(ACCOUNT, day=DAY)).neurons is None
        account.analytics_permission = False
        assert (await account.usage(ACCOUNT, day=DAY)).state == "no_permission"
        account.analytics_permission, account.available = True, False
        assert (await account.usage(ACCOUNT, day=DAY)).state == "unavailable"
        assert (await account.r2_buckets(ACCOUNT)).state == "unavailable"

    async def test_r2_lists_its_buckets_or_is_not_enabled_or_refused(self) -> None:
        account = InMemoryCloudflareAccount()
        account.add_bucket(CloudflareBucket(name="aura-backups", created="2026-10-01T00:00:00Z", lock_rules=2))
        listed = await account.r2_buckets(ACCOUNT)
        assert (listed.state, [b.name for b in listed.buckets]) == ("ok", ["aura-backups"])
        account.r2_enabled = False
        assert (await account.r2_buckets(ACCOUNT)).state == "not_enabled"
        account.r2_enabled, account.r2_permission = True, False
        assert (await account.r2_buckets(ACCOUNT)).state == "no_permission"

    async def test_it_records_the_calls(self) -> None:
        account = InMemoryCloudflareAccount()
        await account.usage(ACCOUNT, day=DAY)
        await account.r2_buckets(ACCOUNT)
        assert account.usage_calls == [{"account_id": ACCOUNT, "day": DAY}]
        assert account.r2_calls == [{"account_id": ACCOUNT}]


# -- the engine ----------------------------------------------------------------------------------


def _engine(account=None, *, now=None) -> tuple[CloudflareControl, InMemoryCloudflareAccount]:
    from aura_core.config import load_settings
    from aura_core.container import Container

    container = Container(load_settings(refresh=True, validate=False))
    account = account if account is not None else InMemoryCloudflareAccount()
    engine = CloudflareControl(
        control=container.control, store=container.control_store, railway=container.railway,
        tokens=container.cloudflare_tokens, account=account, api_root=WORKERS_AI_API_ROOT,
        provider_health=lambda: [], **({"now": now} if now else {}),
    )
    engine.container = container  # type: ignore[attr-defined]
    return engine, account


async def _connect(engine: CloudflareControl, target: str = ACCOUNT) -> None:
    await engine.container.control_store.put_credential(PlatformCredential(  # type: ignore[attr-defined]
        platform=Platform.CLOUDFLARE, secret_ref="ref-cloudflare", configured_by="root@aura.ai",
        configured_at=datetime.now(timezone.utc), target=target,
    ))


class TestEngineUsage:
    async def test_it_reports_today_against_the_free_allowance(self) -> None:
        now = datetime(2026, 11, 23, 14, 5, tzinfo=timezone.utc)
        engine, account = _engine(now=lambda: now)
        await _connect(engine)
        account.record_usage(DAY, requests=42, neurons=1234.5)
        result = await engine.usage()
        assert result == {
            "state": "ok", "account_id": ACCOUNT, "day": "2026-11-23", "requests": 42, "neurons": 1234.5,
            "neurons_available": True, "neurons_field": "", "reason": "",
            "allowance_neurons": FREE_ALLOWANCE_NEURONS, "allowance_basis": "free_plan",
            "resets_at": "2026-11-24T00:00:00Z", "permission": "Account Analytics: Read",
        }
        assert FREE_ALLOWANCE_NEURONS == 10_000

    async def test_the_day_is_the_utc_day_whatever_the_clock_zone(self) -> None:
        late = datetime(2026, 11, 24, 1, 0, tzinfo=timezone(timedelta(hours=3)))  # 22:00 UTC on the 23rd
        engine, account = _engine(now=lambda: late)
        await _connect(engine)
        result = await engine.usage()
        assert result["day"] == "2026-11-23" and result["resets_at"] == "2026-11-24T00:00:00Z"
        assert account.usage_calls == [{"account_id": ACCOUNT, "day": DAY}]

    async def test_without_a_neuron_figure_it_says_so(self) -> None:
        engine, account = _engine()
        await _connect(engine)
        account.record_usage(datetime.now(timezone.utc).date(), requests=7, neurons=3.0)
        account.neurons_reported = False
        result = await engine.usage()
        assert (result["state"], result["requests"], result["neurons"], result["neurons_available"]) == ("ok", 7, None, False)

    @pytest.mark.parametrize("flag,state", [("analytics_permission", "no_permission"), ("available", "unavailable")])
    async def test_a_named_state_is_returned_never_raised(self, flag: str, state: str) -> None:
        engine, account = _engine()
        await _connect(engine)
        setattr(account, flag, False)
        result = await engine.usage()
        assert result["state"] == state and result["requests"] is None and result["neurons"] is None
        assert result["permission"] == "Account Analytics: Read"

    async def test_an_adapter_that_cannot_reach_cloudflare_is_unavailable(self) -> None:
        class Down:
            async def usage(self, account_id, *, day=None):
                raise DependencyUnavailableError("down", dependency="cloudflare")

            async def r2_buckets(self, account_id):
                raise DependencyUnavailableError("down", dependency="cloudflare")

        engine, _ = _engine(Down())
        await _connect(engine)
        assert (await engine.usage())["state"] == "unavailable"
        assert (await engine.r2())["state"] == "unavailable"

    async def test_it_needs_cloudflare_connected_with_an_account(self) -> None:
        engine, _ = _engine()
        for call in (engine.usage, engine.r2):
            with pytest.raises(ValidationError) as caught:
                await call()
            assert caught.value.details["reason"] == "cloudflare_not_connected"
        await _connect(engine, target="")
        for call in (engine.usage, engine.r2):
            with pytest.raises(ValidationError) as caught:
                await call()
            assert caught.value.details["reason"] == "no_account"


class TestEngineR2:
    async def test_it_lists_buckets_with_what_is_known_of_their_locks(self) -> None:
        engine, account = _engine()
        await _connect(engine)
        account.add_bucket(CloudflareBucket(name="aura-backups", created="2026-10-01T09:30:00Z", location="WEUR",
                                            storage_class="Standard", lock_rules=1))
        account.add_bucket(CloudflareBucket(name="scratch", created="2026-11-02T00:00:00Z"))
        result = await engine.r2()
        assert result == {
            "state": "ok", "account_id": ACCOUNT, "reason": "", "permission": "Workers R2 Storage: Read",
            "more": False, "buckets": [
                {"name": "aura-backups", "created": "2026-10-01T09:30:00Z", "location": "WEUR",
                 "storage_class": "Standard", "lock_rules": 1},
                {"name": "scratch", "created": "2026-11-02T00:00:00Z", "location": "",
                 "storage_class": "", "lock_rules": None},
            ],
        }

    @pytest.mark.parametrize("setup,state", [
        (lambda a: setattr(a, "r2_enabled", False), "not_enabled"),
        (lambda a: setattr(a, "r2_permission", False), "no_permission"),
        (lambda a: setattr(a, "available", False), "unavailable"),
    ])
    async def test_every_state_is_a_result(self, setup, state: str) -> None:
        engine, account = _engine()
        await _connect(engine)
        setup(account)
        result = await engine.r2()
        assert result["state"] == state and result["buckets"] == []
