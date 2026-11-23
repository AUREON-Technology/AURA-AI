"""Review of the product tools (builtin, records, personal_os, planning, analysis wrappers).

Every test here failed on the code before the review fix it pins. Real objects throughout: the
in-memory engines and stores, a real ``ToolRegistry``, real PDFs built by ``_pdf``.
"""

from __future__ import annotations

import functools
import json
import re
import socket
import time
from datetime import datetime, timedelta, timezone

import pytest

from aura_core.adapters import InMemoryPersonalOsStore
from aura_core.domain import ToolCall, ToolCallId
from aura_core.domain.errors import ToolExecutionError
from aura_core.domain.identifiers import UserId
from aura_core.ports.runtime import ToolContext
from aura_core.tools import ToolPolicy, ToolRegistry
from aura_core.tools.analysis import build_analysis_tools
from aura_core.tools.builtin import (
    _assert_public_url,
    _strip_markup,
    build_file_tools,
    build_http_tool,
    build_knowledge_tools,
    build_memory_tools,
    calculator,
    current_datetime,
)
from aura_core.tools.personal_os import personal_os_tools
from aura_core.tools.planning import find_calendar_slots, prioritize_tasks
from aura_core.tools.records import build_personal_os_crud_tools
from tests.unit.test_document_extraction import _pdf


def _ctx(user: str = "test-user") -> ToolContext:
    return ToolContext(user_id=UserId(user), correlation_id="corr", deadline_s=300.0)


@pytest.fixture
def ctx() -> ToolContext:
    return _ctx()


async def _call(registry: ToolRegistry, name: str, ctx: ToolContext, **arguments):
    result, _ = await registry.execute(ToolCall(id=ToolCallId("c1"), name=name, arguments=arguments), ctx)
    return result


# --------------------------------------------------------------------------------------
# calculator / current_datetime
# --------------------------------------------------------------------------------------


class TestCalculator:
    async def test_tuple_repetition_is_refused_not_allocated(self, ctx) -> None:
        """`(1,) * 10**8` was an 800 MB allocation (5 s) on the event loop."""
        start = time.perf_counter()
        with pytest.raises(ToolExecutionError):
            await calculator.execute({"expression": "(1,) * 10**8"}, ctx)
        assert time.perf_counter() - start < 1.0

    async def test_a_tuple_is_not_an_answer(self, ctx) -> None:
        with pytest.raises(ToolExecutionError):
            await calculator.execute({"expression": "(1, 2)"}, ctx)

    async def test_round_with_a_huge_digits_argument_is_refused(self, ctx) -> None:
        """`round(5, -10**7)` computes 10**10**7 inside CPython: seconds of CPU, then gigabytes."""
        start = time.perf_counter()
        with pytest.raises(ToolExecutionError, match="digits"):
            await calculator.execute({"expression": "round(5, -10**7)"}, ctx)
        assert time.perf_counter() - start < 1.0

    async def test_a_complex_result_is_refused(self, ctx) -> None:
        with pytest.raises(ToolExecutionError, match="not a real number"):
            await calculator.execute({"expression": "(-8) ** (1/3)"}, ctx)

    async def test_booleans_are_not_numbers(self, ctx) -> None:
        with pytest.raises(ToolExecutionError):
            await calculator.execute({"expression": "True + 1"}, ctx)

    async def test_money_keeps_its_cents(self, ctx) -> None:
        """10 significant digits turned 1234567890.12 into 1234567890."""
        assert await calculator.execute({"expression": "1234567890.12"}, ctx) == "1234567890.12 = 1234567890.12"
        assert "0.3" in await calculator.execute({"expression": "0.1 + 0.2"}, ctx)

    async def test_whole_floats_are_accepted_by_factorial_and_gcd(self, ctx) -> None:
        assert "= 120" in await calculator.execute({"expression": "factorial(5.0)"}, ctx)
        assert "= 6" in await calculator.execute({"expression": "gcd(12.0, 18)"}, ctx)

    async def test_a_float_overflow_is_a_clean_error(self, ctx) -> None:
        with pytest.raises(ToolExecutionError, match="too large"):
            await calculator.execute({"expression": "1e12 ** 1024"}, ctx)

    async def test_a_nul_byte_is_a_clean_error(self, ctx) -> None:
        with pytest.raises(ToolExecutionError, match="could not parse"):
            await calculator.execute({"expression": "1\x002"}, ctx)

    async def test_an_int_too_long_to_print_is_a_clean_error(self, ctx) -> None:
        expression = "*".join(["factorial(170)"] * 20)
        with pytest.raises(ToolExecutionError, match="too many digits"):
            await calculator.execute({"expression": expression}, ctx)


class TestCurrentDatetime:
    async def test_a_non_finite_offset_is_a_clean_error(self, ctx) -> None:
        with pytest.raises(ToolExecutionError, match="finite"):
            await current_datetime.execute({"timezone_offset_hours": float("nan")}, ctx)

    async def test_a_sub_minute_offset_yields_a_parseable_iso_string(self, ctx) -> None:
        text = await current_datetime.execute({"timezone_offset_hours": 1e-07}, ctx)
        iso = re.search(r"ISO: (\S+)\)", text).group(1)
        assert datetime.fromisoformat(iso).utcoffset() == timedelta(0)
        assert " UTC (ISO" in text  # a sub-minute offset is UTC, labelled as such

    async def test_a_half_hour_offset_is_applied_and_labelled(self, ctx) -> None:
        text = await current_datetime.execute({"timezone_offset_hours": 5.5}, ctx)
        assert "UTC+5.5" in text and "+05:30" in text


# --------------------------------------------------------------------------------------
# knowledge and memory: owner scoping, hostile facts
# --------------------------------------------------------------------------------------


class TestMemoryAndKnowledge:
    async def test_recall_never_returns_another_users_memory(self, memory) -> None:
        recall, remember = build_memory_tools(memory)
        await remember.execute({"fact": "The user's cat is called Biscuit."}, _ctx("alice"))
        mine = await recall.execute({"query": "the user's cat is called Biscuit"}, _ctx("alice"))
        theirs = await recall.execute({"query": "the user's cat is called Biscuit"}, _ctx("bob"))
        assert "Biscuit" in mine
        assert "confidence" not in theirs and "Nothing recorded" in theirs

    async def test_search_knowledge_never_returns_another_users_document(self, knowledge, handbook) -> None:
        from aura_core.domain import DocumentId

        await knowledge.ingest(
            content=handbook, title="Handbook", document_id=DocumentId("alice-doc"),
            owner_id=UserId("alice"),
        )
        (search,) = build_knowledge_tools(knowledge)
        assert "[1]" in await search.execute({"query": "annual leave"}, _ctx("alice"))
        assert "No passages" in await search.execute({"query": "annual leave"}, _ctx("bob"))

    async def test_a_credential_is_not_stored_as_a_fact(self, memory) -> None:
        _, remember = build_memory_tools(memory)
        with pytest.raises(ToolExecutionError, match="credential"):
            await remember.execute(
                {"fact": "The user's key is sk-abcdefghijklmnopqrstuvwxyz123456."}, _ctx()
            )

    async def test_a_blank_fact_and_a_nul_byte_are_handled(self, memory, user) -> None:
        _, remember = build_memory_tools(memory)
        with pytest.raises(ToolExecutionError, match="empty"):
            await remember.execute({"fact": " \x00 "}, _ctx())
        stored = await remember.execute({"fact": "Lives in Cai\x00ro."}, _ctx())
        assert "\x00" not in stored and "Cairo" in stored

    async def test_arabic_facts_survive(self, memory) -> None:
        recall, remember = build_memory_tools(memory)
        await remember.execute({"fact": "المستخدم يعمل في القاهرة 😀"}, _ctx())
        assert "القاهرة" in await recall.execute({"query": "المستخدم يعمل في القاهرة"}, _ctx())


# --------------------------------------------------------------------------------------
# read_file / list_files: PDFs in full
# --------------------------------------------------------------------------------------


def _long_pdf(pages: int = 60, scanned_at: int | None = None) -> bytes:
    specs = [{"text": [f"Page {i} line {j} " + "word " * 40 for j in range(30)]} for i in range(1, pages + 1)]
    specs[-1] = {"text": ["LAST_PAGE_MARKER_42"]}
    if scanned_at is not None:
        specs[scanned_at - 1] = {"image": True}
    return _pdf(specs)


async def _read_all(registry: ToolRegistry, ctx: ToolContext, path: str) -> str:
    """Page through ``read_file`` exactly as a model would: follow the offset it is told to."""
    collected, offset = [], 0
    for _ in range(500):
        result = await _call(registry, "read_file", ctx, path=path, offset=offset)
        assert result.ok, result.content
        assert "[…truncated at" not in result.content, "the registry cut the result off silently"
        collected.append(result.content)
        more = re.search(r"call read_file again with offset=(\d+)", result.content)
        if not more:
            return "\n".join(collected)
        offset = int(more.group(1))
    raise AssertionError("never reached the end")


class TestReadFilePdf:
    @pytest.fixture
    def registry(self, tmp_path):
        registry = ToolRegistry(policy=ToolPolicy())
        registry.register_all(build_file_tools(tmp_path))
        return registry

    async def test_every_page_of_a_long_pdf_is_readable_nothing_truncated(self, registry, tmp_path, ctx) -> None:
        (tmp_path / "report.pdf").write_bytes(_long_pdf(60))
        everything = await _read_all(registry, ctx, "report.pdf")
        for page in (1, 30, 59):
            assert f"Page {page} line 0 " in everything
        assert "LAST_PAGE_MARKER_42" in everything  # the final page of 60
        assert "60 page(s); 60 read in full" in everything

    async def test_a_single_call_says_it_is_not_the_whole_text(self, registry, tmp_path, ctx) -> None:
        (tmp_path / "report.pdf").write_bytes(_long_pdf(60))
        first = (await _call(registry, "read_file", ctx, path="report.pdf")).content
        assert "NOT the whole text" in first and "offset=" in first
        assert "LAST_PAGE_MARKER_42" not in first

    async def test_a_scanned_page_is_named_as_not_read(self, registry, tmp_path, ctx) -> None:
        (tmp_path / "mixed.pdf").write_bytes(_long_pdf(45, scanned_at=7))
        text = (await _call(registry, "read_file", ctx, path="mixed.pdf")).content
        assert "NOT READ" in text and "page(s) 7 " in text
        assert "44 read" in text
        assert "LAST_PAGE_MARKER_42" in await _read_all(registry, ctx, "mixed.pdf")

    async def test_a_short_pdf_comes_back_whole_with_its_coverage_line(self, registry, tmp_path, ctx) -> None:
        (tmp_path / "s.pdf").write_bytes(_pdf([{"text": ["alpha"]}, {"text": ["omega PASSED"]}]))
        text = (await _call(registry, "read_file", ctx, path="s.pdf")).content
        assert "2 page(s); 2 read in full" in text and "alpha" in text and "omega PASSED" in text
        assert "NOT the whole" not in text

    async def test_a_pdf_with_the_wrong_extension_is_still_a_pdf(self, registry, tmp_path, ctx) -> None:
        (tmp_path / "scan.bin").write_bytes(_pdf([{"text": ["hidden in a pdf"]}]))
        assert "hidden in a pdf" in (await _call(registry, "read_file", ctx, path="scan.bin")).content

    async def test_a_fake_pdf_is_a_clear_error_that_does_not_leak_the_host_path(self, registry, tmp_path, ctx) -> None:
        (tmp_path / "fake.pdf").write_bytes(b"not a pdf")
        result = await _call(registry, "read_file", ctx, path="fake.pdf")
        assert not result.ok and "not a valid PDF" in result.content
        assert str(tmp_path) not in result.content

    async def test_a_pdf_symlink_out_of_the_workspace_is_refused(self, registry, tmp_path, ctx) -> None:
        outside = tmp_path.parent / "outside.pdf"
        outside.write_bytes(_pdf([{"text": ["secret"]}]))
        (tmp_path / "link.pdf").symlink_to(outside)
        result = await _call(registry, "read_file", ctx, path="link.pdf")
        assert not result.ok and "escapes" in result.content and "secret" not in result.content

    async def test_a_binary_non_pdf_is_a_clear_error_not_mojibake(self, registry, tmp_path, ctx) -> None:
        (tmp_path / "pic.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
        result = await _call(registry, "read_file", ctx, path="pic.png")
        assert not result.ok and "binary file" in result.content

    async def test_text_beyond_one_window_is_paged_and_reported_not_cut(self, registry, tmp_path, ctx) -> None:
        lines = "".join(f"مرحبا {i} 😀\n" for i in range(6000))
        (tmp_path / "long.txt").write_text(lines + "END_MARKER", encoding="utf-8")
        first = (await _call(registry, "read_file", ctx, path="long.txt")).content
        assert "NOT the whole text" in first
        assert "END_MARKER" in await _read_all(registry, ctx, "long.txt")

    async def test_an_offset_past_the_end_is_an_error(self, registry, tmp_path, ctx) -> None:
        (tmp_path / "a.txt").write_text("hi")
        result = await _call(registry, "read_file", ctx, path="a.txt", offset=10)
        assert not result.ok and "past the end" in result.content

    async def test_a_small_text_file_is_returned_exactly(self, registry, tmp_path, ctx) -> None:
        (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
        assert (await _call(registry, "read_file", ctx, path="a.txt")).content == "hello"

    async def test_read_file_taints_the_turn_because_a_pdf_can_carry_instructions(self, tmp_path) -> None:
        (read_file,) = [t for t in build_file_tools(tmp_path) if t.spec.name == "read_file"]
        assert "untrusted_content" in read_file.spec.tags

    async def test_the_timeout_leaves_room_for_a_long_pdf(self, tmp_path) -> None:
        (read_file,) = [t for t in build_file_tools(tmp_path) if t.spec.name == "read_file"]
        assert read_file.spec.timeout_s >= 60


class TestListFiles:
    async def test_pdfs_are_flagged_and_broken_entries_do_not_fail_the_listing(self, tmp_path, ctx) -> None:
        (tmp_path / "a.pdf").write_bytes(_pdf([{"text": ["x"]}]))
        (tmp_path / "broken").symlink_to(tmp_path / "missing")
        (tmp_path / "out").symlink_to("/etc")
        (tmp_path / "new\nline.txt").write_text("x")
        list_files, _ = build_file_tools(tmp_path)
        text = await list_files.execute({}, ctx)
        assert re.search(r"a\.pdf \([\d,]+ bytes, PDF\)", text)
        assert "broken (unreadable)" in text
        assert "out (symlink leaving the workspace" in text
        assert "new\nline" not in text and "new?line.txt" in text

    async def test_a_huge_directory_is_listed_with_a_stated_limit(self, tmp_path, ctx) -> None:
        for i in range(1_010):
            (tmp_path / f"f{i}.txt").write_text("")
        list_files, _ = build_file_tools(tmp_path)
        text = await list_files.execute({}, ctx)
        assert "listing stopped after 1,000 entries" in text


# --------------------------------------------------------------------------------------
# fetch_url
# --------------------------------------------------------------------------------------


class TestFetchUrl:
    ALLOWED = frozenset({"example.com"})

    @pytest.mark.parametrize(
        "url",
        [
            "https://evil.test\\@example.com/",
            "https://user:pw@example.com/",
            "https://example.com@evil.test/",
            "https://example.com:6379/",
            "https://example.com/a b",
            "https://example.com/\x00",
            "https://example.com/\n",
        ],
    )
    def test_ambiguous_or_credentialed_urls_are_refused(self, url: str) -> None:
        with pytest.raises(ToolExecutionError):
            _assert_public_url(url, self.ALLOWED, "fetch_url")

    def test_the_default_port_and_an_explicit_443_pass(self) -> None:
        _assert_public_url("https://example.com/x", self.ALLOWED, "fetch_url")
        _assert_public_url("https://example.com:443/x", self.ALLOWED, "fetch_url")

    def test_markup_stripping_is_linear_on_hostile_pages(self) -> None:
        """`<script.*?</script>` with no closing tag is quadratic; so is `<[^>]+>` on a run of `<`."""
        start = time.perf_counter()
        _strip_markup("<script " * 140_000)
        _strip_markup("<" * 1_000_000)
        _strip_markup("<style>" * 100_000 + "text")
        assert time.perf_counter() - start < 3.0

    def test_markup_stripping_still_works(self) -> None:
        html = "<h1>Hi</h1><script>evil()</script><style>p{}</style><p>there &amp; <b>you</b></p>"
        assert _strip_markup(html) == "Hi there &amp; you"
        assert _strip_markup("a<script>never closed") == "a"

    @staticmethod
    def _serve(monkeypatch: pytest.MonkeyPatch, handler) -> None:
        import httpx

        real = httpx.AsyncClient
        monkeypatch.setattr(httpx, "AsyncClient", functools.partial(real, transport=httpx.MockTransport(handler)))
        monkeypatch.setattr(
            socket, "getaddrinfo",
            lambda host, port, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))],
        )

    async def test_a_page_cut_at_the_byte_budget_says_so(self, monkeypatch, ctx) -> None:
        import httpx

        class Chunked(httpx.AsyncByteStream):  # no Content-Length: the size is only learned by reading
            async def __aiter__(self):
                for _ in range(5):
                    yield b"<p>" + b"x" * 997

        self._serve(monkeypatch, lambda r: httpx.Response(200, headers={"content-type": "text/html"},
                                                           stream=Chunked()))
        (fetch,) = build_http_tool(allowed_hosts=["example.com"], max_body_bytes=1000)
        text = await fetch.execute({"url": "https://example.com/"}, ctx)
        assert "only its first part was read" in text

    async def test_an_unknown_charset_does_not_crash(self, monkeypatch, ctx) -> None:
        import httpx

        self._serve(monkeypatch, lambda r: httpx.Response(
            200, headers={"content-type": "text/html; charset=bogus-9"}, content="<p>café</p>".encode()))
        (fetch,) = build_http_tool(allowed_hosts=["example.com"])
        assert await fetch.execute({"url": "https://example.com/"}, ctx) == "café"

    async def test_a_pdf_is_read_in_full_across_windows(self, monkeypatch, ctx) -> None:
        import httpx

        body = _long_pdf(50)
        self._serve(monkeypatch, lambda r: httpx.Response(200, headers={"content-type": "application/pdf"}, content=body))
        (fetch,) = build_http_tool(allowed_hosts=["example.com"])
        registry = ToolRegistry(policy=ToolPolicy())
        registry.register(fetch)
        collected, offset = [], 0
        for _ in range(200):
            result = await _call(registry, "fetch_url", ctx, url="https://example.com/r.pdf", offset=offset)
            assert result.ok, result.content
            assert "[…truncated at" not in result.content
            collected.append(result.content)
            more = re.search(r"call fetch_url again with offset=(\d+)", result.content)
            if not more:
                break
            offset = int(more.group(1))
        assert "LAST_PAGE_MARKER_42" in "\n".join(collected)

    async def test_an_oversized_pdf_is_refused_never_cut(self, monkeypatch, ctx) -> None:
        import httpx

        body = _long_pdf(50)
        self._serve(monkeypatch, lambda r: httpx.Response(200, headers={"content-type": "application/pdf"}, content=body))
        (fetch,) = build_http_tool(allowed_hosts=["example.com"], max_pdf_bytes=1000)
        with pytest.raises(ToolExecutionError, match="budget"):
            await fetch.execute({"url": "https://example.com/r.pdf"}, ctx)

    async def test_a_binary_that_is_not_a_pdf_is_still_refused(self, monkeypatch, ctx) -> None:
        import httpx

        self._serve(monkeypatch, lambda r: httpx.Response(200, headers={"content-type": "image/png"}, content=b"\x89PNG"))
        (fetch,) = build_http_tool(allowed_hosts=["example.com"])
        with pytest.raises(ToolExecutionError, match="unsupported content type"):
            await fetch.execute({"url": "https://example.com/x.png"}, ctx)


class TestWeather:
    async def test_an_empty_reading_is_an_error_not_a_nonedegc_success(self, monkeypatch, ctx) -> None:
        import httpx

        from aura_core.tools.builtin import build_weather_tool

        def handler(request):
            if "geocoding" in request.url.host:
                return httpx.Response(200, json={"results": [{"latitude": 1, "longitude": 2, "name": "Cairo"}]})
            return httpx.Response(200, json={"current": {}})

        real = httpx.AsyncClient
        monkeypatch.setattr(httpx, "AsyncClient", functools.partial(real, transport=httpx.MockTransport(handler)))
        (weather,) = build_weather_tool()
        with pytest.raises(ToolExecutionError, match="no current weather"):
            await weather.execute({"location": "Cairo"}, ctx)


# --------------------------------------------------------------------------------------
# personal-OS proposals and CRUD
# --------------------------------------------------------------------------------------


class TestProposals:
    @pytest.fixture
    def tools(self):
        return {t.spec.name: t for t in personal_os_tools()}

    async def test_a_non_finite_amount_is_refused_instead_of_emitting_invalid_json(self, tools, ctx) -> None:
        with pytest.raises(ToolExecutionError, match="finite"):
            await tools["propose_expense"].execute({"amount": float("nan")}, ctx)
        with pytest.raises(ToolExecutionError, match="finite"):
            await tools["propose_income"].execute({"amount": float("inf")}, ctx)

    async def test_a_bad_date_fails_here_where_the_model_can_retry(self, tools, ctx) -> None:
        with pytest.raises(ToolExecutionError, match="ISO 8601"):
            await tools["propose_task"].execute({"title": "x", "dueDate": "next tuesday"}, ctx)
        with pytest.raises(ToolExecutionError, match="ISO 8601"):
            await tools["propose_event"].execute({"title": "x", "startTime": "tomorrow"}, ctx)

    async def test_an_event_ending_before_it_starts_is_refused(self, tools, ctx) -> None:
        with pytest.raises(ToolExecutionError, match="endTime"):
            await tools["propose_event"].execute(
                {"title": "x", "startTime": "2026-09-01T10:00:00", "endTime": "2026-09-01T09:00:00"}, ctx)

    async def test_a_blank_title_and_an_empty_health_log_are_refused(self, tools, ctx) -> None:
        with pytest.raises(ToolExecutionError):
            await tools["propose_task"].execute({"title": "   "}, ctx)
        with pytest.raises(ToolExecutionError, match="at least one metric"):
            await tools["propose_health_log"].execute({}, ctx)

    async def test_arabic_survives_and_nul_is_dropped(self, tools, ctx) -> None:
        payload = json.loads(await tools["propose_task"].execute({"title": "اشتر\x00ي حليب 😀"}, ctx))
        assert payload["params"]["title"] == "اشتري حليب 😀"


class TestRecords:
    @pytest.fixture
    def store(self):
        return InMemoryPersonalOsStore()

    @pytest.fixture
    def registry(self, store):
        registry = ToolRegistry(policy=ToolPolicy())
        registry.register_all(build_personal_os_crud_tools(store))
        return registry

    @staticmethod
    def _id(result) -> str:
        return re.search(r"([0-9a-f-]{32,36})\.$", result.content).group(1)

    @pytest.mark.parametrize(
        "noun,args,changes",
        [
            ("task", {"title": "Alice's secret task"}, {"title": "pwned"}),
            ("calendar_event", {"title": "e", "start_time": "2026-09-01T10:00:00", "end_time": "2026-09-01T11:00:00"},
             {"title": "pwned"}),
            ("finance_record", {"type": "expense", "amount": 5}, {"amount": 99999}),
            ("health_record", {"weight_kg": 70}, {"weight_kg": 1}),
        ],
    )
    async def test_one_user_cannot_update_or_delete_anothers_record_by_id(
        self, registry, store, noun, args, changes
    ) -> None:
        created = await _call(registry, f"create_{noun}", _ctx("alice"), **args)
        assert created.ok, created.content
        record_id = self._id(created)

        update = await _call(registry, f"update_{noun}", _ctx("mallory"), record_id=record_id, **changes)
        delete = await _call(registry, f"delete_{noun}", _ctx("mallory"), record_id=record_id)
        assert "No " in update.content and "Updated" not in update.content
        assert "No " in delete.content and "Deleted" not in delete.content

        from aura_core.domain.identifiers import PersonalOsRecordId

        survivor = await store.get(PersonalOsRecordId(record_id), owner_id=UserId("alice"))
        assert survivor is not None and survivor.fields == {**args, **({"priority": "medium"} if noun == "task" else {})} \
            or survivor is not None and all(survivor.fields[k] == v for k, v in args.items())

    async def test_a_record_of_another_kind_is_not_reachable_through_this_kind(self, registry) -> None:
        created = await _call(registry, "create_task", _ctx(), title="t")
        wrong = await _call(registry, "delete_calendar_event", _ctx(), record_id=self._id(created))
        assert "No calendar_event found" in wrong.content

    async def test_a_hostile_record_id_is_not_found_not_a_crash(self, registry) -> None:
        for bad in ("../../etc/passwd", "a b", "x" * 400, "\x00"):
            result = await _call(registry, "delete_task", _ctx(), record_id=bad)
            assert result.ok and result.content.startswith("No task found")
            assert len(result.content) < 100

    async def test_a_delete_the_store_did_not_perform_is_not_reported_as_done(self, registry, store) -> None:
        created = await _call(registry, "create_task", _ctx(), title="t")
        record_id = self._id(created)

        async def refuse(record_id, *, owner_id):
            return False

        store.delete = refuse  # another device removed it between the read and the delete
        result = await _call(registry, "delete_task", _ctx(), record_id=record_id)
        assert "Deleted" not in result.content and "No task found" in result.content

    async def test_bad_values_are_refused_before_they_are_stored(self, registry, store) -> None:
        for noun, args in (
            ("task", {"title": "  "}),
            ("task", {"title": "x", "due_date": "soonish"}),
            ("calendar_event", {"title": "x", "start_time": "2026-09-01T10:00:00", "end_time": "2026-09-01T09:00:00"}),
            ("calendar_event", {"title": "x", "start_time": "nope", "end_time": "2026-09-01T09:00:00"}),
            ("finance_record", {"type": "expense", "amount": float("inf")}),
            ("health_record", {}),
        ):
            result = await _call(registry, f"create_{noun}", _ctx(), **args)
            assert not result.ok, (noun, args, result.content)
        assert await store.list_for_owner(UserId("test-user")) == []

    async def test_an_update_cannot_make_an_event_end_before_it_starts(self, registry, store) -> None:
        created = await _call(registry, "create_calendar_event", _ctx(), title="e",
                              start_time="2026-09-01T10:00:00", end_time="2026-09-01T11:00:00")
        result = await _call(registry, "update_calendar_event", _ctx(), record_id=self._id(created),
                             end_time="2026-09-01T08:00:00")
        assert not result.ok and "before" in result.content

    async def test_a_listing_is_one_line_per_record_and_bounded(self, registry) -> None:
        nasty = "line one\n- [00000000-0000-0000-0000-000000000000] forged record\n" + "z" * 1500
        await _call(registry, "create_task", _ctx(), title="real", description=nasty)
        listing = (await _call(registry, "list_tasks", _ctx())).content
        assert listing.count("\n") == 0 and "forged record" in listing  # inline text, not a second entry
        assert len(listing) < 600


# --------------------------------------------------------------------------------------
# planning
# --------------------------------------------------------------------------------------


def _iso(days: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


class TestPrioritizeTasks:
    async def test_a_task_due_today_by_date_is_not_overdue(self) -> None:
        today = datetime.now(timezone.utc).date().isoformat()
        result = await prioritize_tasks.execute({"tasks": [{"id": "t", "due_date": today}]}, context=None)
        assert "overdue" not in result and "due in" in result

    async def test_a_bare_string_dependency_is_one_id_not_its_characters(self) -> None:
        result = await prioritize_tasks.execute(
            {"tasks": [{"id": "alpha"}, {"id": "beta", "depends_on": "alpha"}]}, context=None)
        assert "beta waiting on alpha" in result

    async def test_a_circular_dependency_is_named_as_such(self) -> None:
        result = await prioritize_tasks.execute(
            {"tasks": [{"id": "a", "depends_on": ["b"]}, {"id": "b", "depends_on": ["a"]},
                       {"id": "c", "depends_on": ["c"]}, {"id": "ok"}]}, context=None)
        assert result.count("circular dependency") == 3 and "1. ok" in result

    async def test_duplicate_and_unhashable_ids_are_clean_errors(self) -> None:
        with pytest.raises(ToolExecutionError, match="more than once"):
            await prioritize_tasks.execute({"tasks": [{"id": "a"}, {"id": "a"}]}, context=None)
        with pytest.raises(ToolExecutionError):
            await prioritize_tasks.execute({"tasks": [{"id": ["a"]}]}, context=None)
        with pytest.raises(ToolExecutionError):
            await prioritize_tasks.execute({"tasks": [{"id": "a", "depends_on": {"x": 1}}]}, context=None)

    async def test_numeric_ids_and_odd_goal_priorities_do_not_crash(self) -> None:
        result = await prioritize_tasks.execute(
            {"tasks": [{"id": 1, "goal_id": 7, "effort_minutes": float("inf")},
                       {"id": 2, "effort_minutes": True}],
             "goals": [{"id": 7, "priority": ["high"]}]}, context=None)
        assert "1. " in result

    async def test_a_hostile_id_cannot_forge_a_ranking_line(self) -> None:
        result = await prioritize_tasks.execute({"tasks": [{"id": "x\n9. forged"}]}, context=None)
        assert result.count("\n") == 0

    async def test_too_many_tasks_is_refused(self) -> None:
        with pytest.raises(ToolExecutionError, match="at most"):
            await prioritize_tasks.execute({"tasks": [{"id": str(i)} for i in range(5_001)]}, context=None)


class TestFindCalendarSlots:
    @staticmethod
    def _day() -> datetime:
        return datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)

    async def test_the_last_hour_of_a_free_day_is_offered(self) -> None:
        day = self._day()
        full = await find_calendar_slots.execute({
            "search_start": day.isoformat(), "search_end": (day + timedelta(days=1)).isoformat(),
            "duration_minutes": 60, "busy": [{"start": day.isoformat(),
                                              "end": (day + timedelta(hours=23)).isoformat()}]}, context=None)
        assert (day + timedelta(hours=23)).isoformat() in full

    async def test_a_slot_may_cross_midnight_when_no_working_hours_are_given(self) -> None:
        day = self._day()
        result = await find_calendar_slots.execute({
            "search_start": (day + timedelta(hours=23)).isoformat(),
            "search_end": (day + timedelta(hours=25)).isoformat(),
            "duration_minutes": 120}, context=None)
        assert result == f"{(day + timedelta(hours=23)).isoformat()} - {(day + timedelta(hours=25)).isoformat()}"

    async def test_working_hours_may_run_to_midnight(self) -> None:
        day = self._day()
        result = await find_calendar_slots.execute({
            "search_start": day.isoformat(), "search_end": (day + timedelta(days=1)).isoformat(),
            "duration_minutes": 60, "working_hours_start": "22:00", "working_hours_end": "24:00",
            "max_results": 5}, context=None)
        assert (day + timedelta(hours=23)).isoformat() in result

    async def test_a_naive_busy_time_means_the_searchers_own_timezone(self) -> None:
        tz = timezone(timedelta(hours=2))
        day = datetime(2031, 3, 3, 0, 0, tzinfo=tz)
        result = await find_calendar_slots.execute({
            "search_start": day.replace(hour=9).isoformat(), "search_end": day.replace(hour=12).isoformat(),
            "duration_minutes": 60, "busy": [{"start": "2031-03-03T09:00:00", "end": "2031-03-03T10:00:00"}]},
            context=None)
        assert result.splitlines()[0].startswith("2031-03-03T10:00:00+02:00")

    async def test_an_inverted_busy_interval_is_an_error_not_silently_dropped(self) -> None:
        day = self._day()
        with pytest.raises(ToolExecutionError, match="ends before it starts"):
            await find_calendar_slots.execute({
                "search_start": day.isoformat(), "search_end": (day + timedelta(hours=5)).isoformat(),
                "duration_minutes": 30,
                "busy": [{"start": (day + timedelta(hours=3)).isoformat(), "end": day.isoformat()}]}, context=None)

    async def test_an_absurd_duration_is_a_clean_error(self) -> None:
        day = self._day()
        with pytest.raises(ToolExecutionError, match="duration_minutes"):
            await find_calendar_slots.execute({
                "search_start": day.isoformat(), "search_end": (day + timedelta(hours=5)).isoformat(),
                "duration_minutes": 10**15}, context=None)

    async def test_adjacent_and_overlapping_busy_blocks_leave_no_phantom_gap(self) -> None:
        day = self._day()
        result = await find_calendar_slots.execute({
            "search_start": day.isoformat(), "search_end": (day + timedelta(hours=4)).isoformat(),
            "duration_minutes": 30, "max_results": 20,
            "busy": [{"start": day.isoformat(), "end": (day + timedelta(hours=1)).isoformat()},
                     {"start": (day + timedelta(hours=1)).isoformat(), "end": (day + timedelta(hours=2)).isoformat()},
                     {"start": (day + timedelta(minutes=90)).isoformat(), "end": (day + timedelta(hours=3)).isoformat()}]},
            context=None)
        assert result.splitlines()[0].startswith((day + timedelta(hours=3)).isoformat())

    async def test_an_empty_calendar_offers_the_start(self) -> None:
        day = self._day()
        result = await find_calendar_slots.execute({
            "search_start": day.isoformat(), "search_end": (day + timedelta(hours=1)).isoformat(),
            "duration_minutes": 60}, context=None)
        assert result.startswith(day.isoformat())


# --------------------------------------------------------------------------------------
# analysis wrappers
# --------------------------------------------------------------------------------------


class TestAnalysisWrappers:
    @pytest.fixture
    def registry(self):
        registry = ToolRegistry(policy=ToolPolicy())
        registry.register_all(build_analysis_tools())
        return registry

    @staticmethod
    def _strict(text: str):
        def refuse(constant):
            raise ValueError(f"not JSON: {constant}")

        return json.loads(text, parse_constant=refuse)

    async def test_non_finite_numbers_are_not_emitted_as_bare_nan(self, ctx) -> None:
        """Called directly: the registry's schema check may refuse NaN first, the wrapper must not rely on it."""
        tools = {t.spec.name: t for t in build_analysis_tools()}
        for name, args in (
            ("check_model_drift", {"operation": "accuracy", "baseline_accuracy": float("nan"), "current_accuracy": 0.5}),
            ("analyze_logs", {"operation": "latency", "latencies_ms": [1e308] * 50}),
            ("validate_data_records", {"operation": "numeric_bounds", "records": [{"a": float("nan")}], "field": "a",
                                       "min_value": 0}),
        ):
            self._strict(await tools[name].execute(args, ctx))

    async def test_a_big_result_is_shortened_as_valid_json_not_cut_mid_string(self, registry, ctx) -> None:
        for name, args in (
            ("analyze_logs", {"operation": "errors", "logs": ["ERROR مرحبا 😀 " + "x" * 40] * 3_000}),
            ("validate_data_records", {"operation": "records", "records": [{"a": i} for i in range(5_000)],
                                       "required_fields": ["a", "b"]}),
            ("scan_dependency_licenses", {"packages": [{"package": f"p{i}", "license": "GPL-3.0"} for i in range(3_000)]}),
            ("analyze_change_impact", {"modified_files": [f"pkg/module_{i}.py" for i in range(3_000)]}),
        ):
            result = await _call(registry, name, ctx, **args)
            assert result.ok, result.content
            assert "[…truncated" not in result.content
            data = self._strict(result.content)
            assert data["truncated"] is True and data["omitted_items"] > 0
            assert len(result.content) <= 15_000

    async def test_a_small_result_is_untouched(self, registry, ctx) -> None:
        data = self._strict((await _call(registry, "analyze_logs", ctx, operation="latency",
                                         latencies_ms=[1, 2, 5000])).content)
        assert "truncated" not in data

    async def test_scan_code_security_refuses_a_line_that_would_pin_a_thread(self, registry, ctx) -> None:
        """Ten thousand matches on one line cost ~27 s of un-cancellable CPU in the scanner."""
        start = time.perf_counter()
        result = await _call(registry, "scan_code_security", ctx, source_code="eval(" * 10_000)
        assert not result.ok and "line 1" in result.content and "limit is 2,000" in result.content
        assert time.perf_counter() - start < 1.0
        result = await _call(registry, "scan_code_security", ctx, diff_text="+" + "x" * 3_000)
        assert not result.ok

    async def test_scan_code_security_still_scans_normal_code(self, registry, ctx) -> None:
        data = self._strict((await _call(registry, "scan_code_security", ctx,
                                         source_code="import os\nos.system('x')\n")).content)
        assert data["secure"] is False

    async def test_oversized_structured_arguments_are_refused_before_work(self, registry, ctx) -> None:
        big_log = ["ERROR " + "a" * 100_000] * 10
        assert not (await _call(registry, "analyze_logs", ctx, operation="errors", logs=big_log)).ok
        assert not (await _call(registry, "generate_diagram", ctx, diagram_type="er",
                                spec={"entities": {"T": [{"name": "c" * 250_000, "type": "int"}]}})).ok
        assert not (await _call(registry, "scan_dependency_licenses", ctx,
                                packages=[{"package": "p", "license": "MIT OR " * 40_000}])).ok

    async def test_diagram_specs_of_the_wrong_shape_are_clear_errors(self, registry, ctx) -> None:
        for diagram_type, spec in (
            ("class", {"class_names": "AB"}),
            ("flowchart", {"nodes": "x"}),
            ("sequence", {"participants": "A", "messages": 5}),
            ("er", {"entities": [1]}),
            ("flowchart", {"nodes": ["a", "b"]}),
        ):
            result = await _call(registry, "generate_diagram", ctx, diagram_type=diagram_type, spec=spec)
            assert not result.ok and "spec." in result.content, (diagram_type, result.content)
        good = await _call(registry, "generate_diagram", ctx, diagram_type="flowchart",
                           spec={"nodes": [{"id": "a", "label": "مرحبا"}], "edges": []})
        assert good.ok and "مرحبا" in good.content

    async def test_a_non_finite_gate_threshold_is_refused(self, registry, ctx) -> None:
        gate = {t.spec.name: t for t in build_analysis_tools()}["evaluate_quality_gate"]
        with pytest.raises(ToolExecutionError, match="finite"):
            await gate.execute({"security_pass": True, "min_score_threshold": float("nan")}, ctx)

    async def test_rolling_back_to_the_version_being_deployed_is_flagged(self, registry, ctx) -> None:
        data = self._strict((await _call(registry, "plan_rollout", ctx, service_name="api",
                                         target_version="1.2.3", previous_version="1.2.3")).content)
        assert data["warnings"]
        data = self._strict((await _call(registry, "plan_rollout", ctx, service_name="api",
                                         target_version="1.2.3", previous_version="1.2.2")).content)
        assert "warnings" not in data

    async def test_arguments_reach_the_classes_under_the_right_names(self, registry, ctx) -> None:
        data = self._strict((await _call(registry, "analyze_code_complexity", ctx,
                                         source_code="def f(x):\n    return x\n", max_complexity=3,
                                         max_lines=7, duplicate_window=4)).content)
        assert data["valid_syntax"] is True
        flagged = self._strict((await _call(registry, "analyze_code_complexity", ctx,
                                            source_code="def f(a):\n" + "".join(f"    if a == {i}:\n        return {i}\n" for i in range(6)),
                                            max_complexity=3)).content)
        assert flagged["max_cyclomatic_complexity"] >= 7 and flagged["maintainable"] is False
