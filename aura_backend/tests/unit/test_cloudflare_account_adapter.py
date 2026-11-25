"""The Cloudflare account adapter: the Workers AI model catalog (console plan, stage 6, B3).

``HttpCloudflareAccount`` is driven over an in-process transport with a *recorded* exchange. The
shape was probed live on 2026-11-23: each model has ``id`` (a UUID), ``name`` (the model id the
gateway uses, ``@cf/...``), ``task``, ``description`` and a ``properties`` list of
``{property_id, value}``. Cloudflare's published docs do not document this endpoint, so the parser
is written to tolerate values that arrive as strings, booleans or numbers, and to skip an entry it
cannot read rather than fail the whole list.

``InMemoryCloudflareAccount`` is the same contract with real state.
"""

from __future__ import annotations

import httpx
import pytest

from aura_core.adapters.cloudflare_account import HttpCloudflareAccount, InMemoryCloudflareAccount
from aura_core.config.external_inference import WORKERS_AI_API_ROOT
from aura_core.domain.errors import DependencyUnavailableError, NotFoundError, ValidationError
from aura_core.ports.control_plane import CloudflareAccount, CloudflareModel

ACCOUNT = "0123456789abcdef0123456789abcdef"
TOKEN = "mgmt-token-zq19-never-shown"

RECORDED = {
    "result": [
        {
            "id": "8f1d9c1e-0000-4000-8000-000000000001",
            "source": 1,
            "name": "@cf/openai/gpt-oss-20b",
            "description": "OpenAI's open-weight 20B model.",
            "task": {"id": "c329a1f9-323d-4e91-b2aa-582dd4188d34", "name": "Text Generation",
                     "description": "Family of generative text models."},
            "created_at": "2025-08-05 10:00:00.000",
            "tags": [],
            "properties": [
                {"property_id": "context_window", "value": "128000"},
                {"property_id": "function_calling", "value": "true"},
                {"property_id": "reasoning", "value": "true"},
                {"property_id": "price", "value": [
                    {"unit": "per M input tokens", "price": "0.20", "currency": "USD"},
                    {"unit": "per M output tokens", "price": "0.30", "currency": "USD"},
                ]},
            ],
        },
        {
            "id": "8f1d9c1e-0000-4000-8000-000000000002",
            "name": "@cf/meta/llama-3.2-11b-vision-instruct",
            "description": "Vision model.",
            "task": {"id": "t1", "name": "Text Generation"},
            "properties": [
                {"property_id": "context_window", "value": 131072},
                {"property_id": "vision", "value": True},
            ],
        },
        {
            "id": "8f1d9c1e-0000-4000-8000-000000000003",
            "name": "@cf/moonshotai/kimi-k2.6",
            "description": "Needs a paid plan.",
            "task": {"id": "t1", "name": "Text Generation"},
            "properties": [{"property_id": "require_workers_paid", "value": "true"}],
        },
        {"id": "no-name-here", "task": {"name": "Text Generation"}},  # unreadable: skipped
        "not-an-object",                                              # unreadable: skipped
    ],
    "result_info": {"count": 5, "page": 1, "per_page": 100, "total_count": 336},
    "success": True, "errors": [], "messages": [],
}


async def _token() -> str:
    return TOKEN


def _adapter(handler) -> HttpCloudflareAccount:
    return HttpCloudflareAccount(token_source=_token, api_root=WORKERS_AI_API_ROOT,
                                 transport=httpx.MockTransport(handler))


def _json(status: int, body) -> httpx.Response:
    return httpx.Response(status, json=body)


class TestHttpModels:
    async def test_the_recorded_catalog_is_parsed(self) -> None:
        page = await _adapter(lambda request: _json(200, RECORDED)).models(ACCOUNT)
        by_id = {m.model: m for m in page.models}
        assert set(by_id) == {"@cf/openai/gpt-oss-20b", "@cf/meta/llama-3.2-11b-vision-instruct", "@cf/moonshotai/kimi-k2.6"}
        gpt = by_id["@cf/openai/gpt-oss-20b"]
        assert gpt.task == "Text Generation" and gpt.context_window == 128000
        assert gpt.function_calling is True and gpt.reasoning is True
        assert gpt.vision is False and gpt.paid_only is False
        assert gpt.price and gpt.price[0]["unit"] == "per M input tokens"
        assert by_id["@cf/meta/llama-3.2-11b-vision-instruct"].vision is True
        assert by_id["@cf/meta/llama-3.2-11b-vision-instruct"].context_window == 131072
        assert by_id["@cf/moonshotai/kimi-k2.6"].paid_only is True
        assert (page.total, page.page, page.per_page) == (336, 1, 100)

    async def test_the_request_is_a_get_with_the_management_token_and_capped_paging(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return _json(200, RECORDED)

        await _adapter(handler).models(ACCOUNT, task="Text Generation", search="gpt", page=2, per_page=500)
        (request,) = seen
        assert request.method == "GET"
        assert request.url.path == f"/client/v4/accounts/{ACCOUNT}/ai/models/search"
        assert dict(request.url.params) == {"task": "Text Generation", "search": "gpt", "page": "2", "per_page": "100"}
        assert request.headers["authorization"] == f"Bearer {TOKEN}"

    async def test_empty_filters_are_not_sent(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return _json(200, RECORDED)

        await _adapter(handler).models(ACCOUNT)
        assert set(seen[0].url.params) == {"page", "per_page"}

    @pytest.mark.parametrize("account", ["", "acct-1", "G" * 32, ACCOUNT.upper(), ACCOUNT + "0"])
    async def test_a_malformed_account_id_is_refused_before_any_request(self, account: str) -> None:
        def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
            raise AssertionError("no request may be made")

        with pytest.raises(ValidationError):
            await _adapter(handler).models(account)

    @pytest.mark.parametrize("status", [401, 403])
    async def test_a_refused_token_is_a_named_reason_and_vendor_text_is_not_passed_on(self, status: int) -> None:
        body = {"success": False, "errors": [{"code": 9109, "message": "vendor detail that must not leak"}]}
        with pytest.raises(ValidationError) as caught:
            await _adapter(lambda request: _json(status, body)).models(ACCOUNT)
        assert caught.value.details["reason"] == "cloudflare_refused"
        assert "vendor detail" not in str(caught.value)

    async def test_not_found_server_errors_and_a_failed_connection(self) -> None:
        with pytest.raises(NotFoundError):
            await _adapter(lambda request: _json(404, {"success": False})).models(ACCOUNT)
        with pytest.raises(DependencyUnavailableError):
            await _adapter(lambda request: _json(500, {"success": False})).models(ACCOUNT)

        def boom(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down")

        with pytest.raises(DependencyUnavailableError) as caught:
            await _adapter(boom).models(ACCOUNT)
        assert caught.value.details["error"] == "ConnectError"

    @pytest.mark.parametrize(
        "body", [{"success": True, "result": {"models": []}}, {"success": True}, ["x"], {"success": False, "result": []}]
    )
    async def test_an_unexpected_shape_is_reported_not_shown_as_an_empty_list(self, body) -> None:
        with pytest.raises(DependencyUnavailableError):
            await _adapter(lambda request: _json(200, body)).models(ACCOUNT)

    async def test_a_body_that_is_not_json_is_reported(self) -> None:
        with pytest.raises(DependencyUnavailableError):
            await _adapter(lambda request: httpx.Response(200, text="<html>")).models(ACCOUNT)

    async def test_no_api_root_means_not_configured(self) -> None:
        adapter = HttpCloudflareAccount(token_source=_token, api_root="")
        with pytest.raises(ValidationError) as caught:
            await adapter.models(ACCOUNT)
        assert caught.value.details["reason"] == "cloudflare_not_configured"

    async def test_the_adapter_satisfies_the_port(self) -> None:
        assert isinstance(_adapter(lambda request: _json(200, RECORDED)), CloudflareAccount)
        assert isinstance(InMemoryCloudflareAccount(), CloudflareAccount)


class TestInMemory:
    def _account(self) -> InMemoryCloudflareAccount:
        account = InMemoryCloudflareAccount()
        account.add_model(CloudflareModel(model="@cf/openai/gpt-oss-20b", task="Text Generation"))
        account.add_model(CloudflareModel(model="@cf/openai/gpt-oss-120b", task="Text Generation"))
        account.add_model(CloudflareModel(model="@cf/baai/bge-m3", task="Text Embeddings"))
        return account

    async def test_it_filters_by_task_and_search_case_insensitively(self) -> None:
        account = self._account()
        assert [m.model for m in (await account.models(ACCOUNT, task="Text Embeddings")).models] == ["@cf/baai/bge-m3"]
        names = [m.model for m in (await account.models(ACCOUNT, search="GPT-OSS")).models]
        assert names == ["@cf/openai/gpt-oss-20b", "@cf/openai/gpt-oss-120b"]

    async def test_it_pages(self) -> None:
        account = self._account()
        first = await account.models(ACCOUNT, per_page=2, page=1)
        second = await account.models(ACCOUNT, per_page=2, page=2)
        assert [len(first.models), len(second.models)] == [2, 1]
        assert first.total == 3 and second.page == 2

    async def test_it_records_what_it_was_asked(self) -> None:
        account = self._account()
        await account.models(ACCOUNT, task="Text Generation", search="x")
        assert account.calls == [{"account_id": ACCOUNT, "task": "Text Generation", "search": "x", "page": 1, "per_page": 100}]
