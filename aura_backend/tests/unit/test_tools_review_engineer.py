"""Review of the engineer / sandbox / desktop tools: correctness, safety, and the PDF rule.

Every test here pins a bug that was found by driving the real tools (``coding.py``,
``desktop_ops.py``, ``open_exec.py``), the real sandbox substrates (``InMemorySandboxRunner`` and
``DockerSandboxRunner``'s file I/O, neither of which needs a daemon), real ``git``, and
``httpx.MockTransport`` for api.github.com. Nothing internal is mocked; each of these failed on the
code it replaced.

PDFs are built with the same valid-xref writer the extraction tests use, with a marker on the LAST
page of a 40+ page document, so "every page was read" is asserted by finding that marker.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from aura_core.adapters.coding_fakes import (
    InMemoryGitHubClient,
    InMemoryMcpClient,
    InMemorySandboxRunner,
)
from aura_core.adapters.docker_sandbox import DockerSandboxRunner
from aura_core.adapters.github_client import GitHubApiError, GitHubClient
from aura_core.adapters.host_agent import HostAgentSandboxRunner, HostAgentSession, HostAgentRegistry
from aura_core.adapters.host_git import GitCommandError, HostGitService
from aura_core.adapters.memory_stores import InMemoryWorkspaceStore
from aura_core.domain.errors import ToolExecutionError
from aura_core.domain.identifiers import ToolCallId, UserId
from aura_core.domain.messages import ToolCall
from aura_core.ports.coding import (
    FileWriteOp,
    McpToolDescriptor,
    RepoFile,
    RepoTree,
    SandboxHandle,
    SandboxOutputEvent,
    SandboxResult,
)
from aura_core.ports.runtime import ToolContext
from aura_core.tools import desktop_ops, open_exec
from aura_core.tools.coding import (
    build_github_tools,
    build_mcp_bridge_tools,
    build_sandbox_tools,
)
from aura_core.tools.policy import ToolPolicy
from aura_core.tools.registry import ToolRegistry
from aura_core.tools.workspaces import WorkspaceCapacityError, WorkspaceManager
from tests.unit.test_document_extraction import _pdf

_ALICE = UserId("alice")
LAST_MARKER = "ZZ-LAST-PAGE-MARKER-7431"


def _ctx(correlation_id: str = "c1") -> ToolContext:
    return ToolContext(user_id=_ALICE, correlation_id=correlation_id, deadline_s=60.0)


def _by_name(tools) -> dict:
    return {t.spec.name: t for t in tools}


def _big_pdf(pages: int = 45, lines: int = 3) -> bytes:
    """``pages`` pages of distinct text, the marker alone on the last one."""
    specs = [
        {"text": [f"Page {n} line {i} unique-token-{n}-{i}" for i in range(lines)]}
        for n in range(1, pages)
    ]
    specs.append({"text": [LAST_MARKER]})
    return _pdf(specs)


def _long_pdf(pages: int = 60, lines: int = 40) -> tuple[bytes, list[str]]:
    """A PDF whose text is far longer than one result page; also the per-page tokens."""
    specs, tokens = [], []
    for n in range(1, pages + 1):
        token = f"PAGETOKEN{n:03d}"
        tokens.append(token)
        specs.append({"text": [f"{token} line {i} " + "lorem ipsum " * 4 for i in range(lines)]})
    specs[-1]["text"][-1] = LAST_MARKER
    return _pdf(specs), tokens


@pytest.fixture
def runner() -> InMemorySandboxRunner:
    return InMemorySandboxRunner()


@pytest.fixture
def tools(runner) -> dict:
    return _by_name(build_sandbox_tools(runner))


@pytest.fixture(params=["in_memory", "docker"])
def any_runner(request, tmp_path):
    if request.param == "docker":
        return DockerSandboxRunner(
            image="python:3.12-slim", network="", host_root=str(tmp_path / "sandboxes")
        )
    return InMemorySandboxRunner()


async def _run(tools, command: str, ctx: ToolContext) -> str:
    return await tools["run_command"].execute({"command": command}, ctx)


# --------------------------------------------------------------------------------------
# write_file / edit_file / write_files
# --------------------------------------------------------------------------------------


class TestEditPreservesBytes:
    async def test_editing_a_crlf_file_keeps_every_line_ending(self, tools) -> None:
        ctx = _ctx()
        await _run(tools, "printf 'a\\r\\nb\\r\\nc\\r\\n' > crlf.txt", ctx)

        await tools["edit_file"].execute({"path": "crlf.txt", "find": "b", "replace": "B"}, ctx)

        hexdump = await _run(tools, "od -An -tx1 crlf.txt", ctx)
        assert hexdump.count("0d") == 3, hexdump
        assert "41 0d 0a" not in hexdump  # (b is the edited line, a is not)
        assert "42 0d 0a" in hexdump

    async def test_an_lf_find_matches_a_crlf_file_and_the_replacement_follows_its_endings(
        self, tools
    ) -> None:
        ctx = _ctx()
        await _run(tools, "printf 'one\\r\\ntwo\\r\\nthree\\r\\n' > f.txt", ctx)

        await tools["edit_file"].execute(
            {"path": "f.txt", "find": "one\ntwo", "replace": "uno\ndos\ndos-bis"}, ctx
        )

        hexdump = await _run(tools, "od -An -tx1 f.txt", ctx)
        assert hexdump.count("0d") == 4  # uno, dos, dos-bis and three all still end CRLF
        assert "uno" in await _run(tools, "cat f.txt", ctx)

    async def test_a_utf8_bom_survives_an_edit(self, tools, runner) -> None:
        ctx = _ctx()
        await _run(tools, "printf '\\357\\273\\277hello\\n' > bom.txt", ctx)

        await tools["edit_file"].execute({"path": "bom.txt", "find": "hello", "replace": "hi"}, ctx)

        hexdump = await _run(tools, "od -An -tx1 bom.txt", ctx)
        assert hexdump.split("--- stdout ---")[1].split()[:3] == ["ef", "bb", "bf"]

    async def test_a_non_utf8_file_is_refused_unchanged_with_a_clear_message(self, tools) -> None:
        ctx = _ctx()
        await _run(tools, "printf 'caf\\351 latin one\\n' > latin.txt", ctx)
        before = await _run(tools, "od -An -tx1 latin.txt", ctx)

        with pytest.raises(ToolExecutionError, match="not valid UTF-8"):
            await tools["edit_file"].execute(
                {"path": "latin.txt", "find": "latin", "replace": "LATIN"}, ctx
            )

        assert await _run(tools, "od -An -tx1 latin.txt", ctx) == before

    async def test_write_file_reports_utf8_bytes_not_characters(self, tools) -> None:
        result = await tools["write_file"].execute({"path": "u.txt", "content": "é"}, _ctx())
        assert "Wrote 2 bytes" in result


class TestEditRules:
    async def test_an_empty_find_is_refused_plainly(self, tools) -> None:
        ctx = _ctx()
        await tools["write_file"].execute({"path": "a.txt", "content": "abc"}, ctx)
        with pytest.raises(ToolExecutionError, match="`find` is empty"):
            await tools["edit_file"].execute({"path": "a.txt", "find": "", "replace": "x"}, ctx)

    async def test_find_equal_to_replace_is_not_reported_as_an_edit(self, tools) -> None:
        ctx = _ctx()
        await tools["write_file"].execute({"path": "a.txt", "content": "abc"}, ctx)
        with pytest.raises(ToolExecutionError, match="identical"):
            await tools["edit_file"].execute({"path": "a.txt", "find": "abc", "replace": "abc"}, ctx)

    async def test_a_changed_file_is_re_read_so_a_stale_find_fails_instead_of_clobbering(
        self, tools
    ) -> None:
        ctx = _ctx()
        await tools["write_file"].execute({"path": "a.txt", "content": "version one"}, ctx)
        await tools["write_file"].execute({"path": "a.txt", "content": "version two"}, ctx)
        with pytest.raises(ToolExecutionError, match="found 0"):
            await tools["edit_file"].execute(
                {"path": "a.txt", "find": "version one", "replace": "x"}, ctx
            )
        assert "version two" in await _run(tools, "cat a.txt", ctx)

    async def test_two_concurrent_edits_to_one_file_both_land(self, tools) -> None:
        ctx = _ctx()
        await tools["write_file"].execute({"path": "a.txt", "content": "alpha\nbeta\n"}, ctx)

        await asyncio.gather(
            tools["edit_file"].execute({"path": "a.txt", "find": "alpha", "replace": "ALPHA"}, ctx),
            tools["edit_file"].execute({"path": "a.txt", "find": "beta", "replace": "BETA"}, ctx),
        )

        content = await _run(tools, "cat a.txt", ctx)
        assert "ALPHA" in content and "BETA" in content, content


class TestTextToolsRefuseBinaryFiles:
    @pytest.mark.parametrize("path", ["spec.pdf", "docs/SPEC.PDF", "logo.png", "a.zip"])
    async def test_write_file_refuses_a_pdf_or_binary_path(self, tools, path) -> None:
        with pytest.raises(ToolExecutionError, match="PDF|binary"):
            await tools["write_file"].execute({"path": path, "content": "text"}, _ctx())

    async def test_the_pdf_refusal_says_why_and_what_to_do(self, tools) -> None:
        with pytest.raises(ToolExecutionError) as caught:
            await tools["write_file"].execute({"path": "spec.pdf", "content": "t"}, _ctx())
        assert "text cannot be written into a PDF" in str(caught.value)
        assert "github_get_file_contents" in str(caught.value)

    async def test_edit_file_refuses_a_pdf_without_touching_it(self, tools) -> None:
        ctx = _ctx()
        await _run(tools, "printf '%%PDF-1.4 body body body' > x.pdf", ctx)
        with pytest.raises(ToolExecutionError, match="text cannot be written into a PDF"):
            await tools["edit_file"].execute({"path": "x.pdf", "find": "body", "replace": "b"}, ctx)
        assert "\n1\n" in await _run(tools, "grep -c 'body body body' x.pdf", ctx)

    async def test_write_files_refuses_a_pdf_and_writes_nothing(self, tools) -> None:
        ctx = _ctx()
        with pytest.raises(ToolExecutionError, match="PDF"):
            await tools["write_files"].execute(
                {"operations": [
                    {"path": "ok.txt", "content": "fine"},
                    {"path": "doc.pdf", "content": "text"},
                ]},
                ctx,
            )
        assert "ok.txt" not in await _run(tools, "ls", ctx)


class TestWriteFilesIsReallyAtomic:
    async def test_null_content_is_refused_not_written_as_the_word_None(self, tools) -> None:
        ctx = _ctx()
        with pytest.raises(ToolExecutionError, match="must be a string"):
            await tools["write_files"].execute(
                {"operations": [{"path": "n.txt", "content": None}]}, ctx
            )
        assert "n.txt" not in await _run(tools, "ls", ctx)

    @pytest.mark.parametrize("value", [5, ["a"], {"k": "v"}, True])
    async def test_non_string_content_is_refused(self, tools, value) -> None:
        with pytest.raises(ToolExecutionError, match="must be a string"):
            await tools["write_files"].execute(
                {"operations": [{"path": "n.txt", "content": value}]}, _ctx()
            )

    async def test_two_operations_on_one_file_are_refused_not_silently_merged_wrong(
        self, tools
    ) -> None:
        ctx = _ctx()
        await tools["write_file"].execute({"path": "d.txt", "content": "one two"}, ctx)

        with pytest.raises(ToolExecutionError, match="both target"):
            await tools["write_files"].execute(
                {"operations": [
                    {"path": "d.txt", "find": "one", "replace": "1"},
                    {"path": "./d.txt", "find": "two", "replace": "2"},
                ]},
                ctx,
            )

        assert (await _run(tools, "cat d.txt", ctx)).count("one two") == 1  # untouched

    async def test_mixed_shapes_unknown_fields_and_empty_batches_are_refused(self, tools) -> None:
        ctx = _ctx()
        bad = [
            [{"path": "a", "content": "x", "find": "y", "replace": "z"}],
            [{"path": "a", "contents": "x"}],
            [{"path": "a", "find": "", "replace": "z"}],
            [{"path": "a", "find": "q", "replace": None}],
            [{"path": "", "content": "x"}],
            [],
        ]
        for operations in bad:
            with pytest.raises(ToolExecutionError):
                await tools["write_files"].execute({"operations": operations}, ctx)
        assert "a" not in (await _run(tools, "ls", ctx)).split()

    async def test_a_batch_over_the_cap_is_refused(self, tools) -> None:
        operations = [{"path": f"f{i}.txt", "content": "x"} for i in range(101)]
        with pytest.raises(ToolExecutionError, match="at most 100"):
            await tools["write_files"].execute({"operations": operations}, _ctx())

    async def test_a_validation_failure_says_nothing_was_written(self, tools) -> None:
        ctx = _ctx()
        await tools["write_file"].execute({"path": "e.txt", "content": "hello"}, ctx)
        with pytest.raises(ToolExecutionError, match="nothing in this call was written"):
            await tools["write_files"].execute(
                {"operations": [{"path": "e.txt", "find": "absent", "replace": "x"}]}, ctx
            )

    async def test_a_write_that_fails_part_way_restores_the_files_already_written(
        self, any_runner
    ) -> None:
        """The second target is a directory: validation passes (it only checks content ops are
        resolvable), the write then fails. The first file must not be left changed."""
        handle = await any_runner.create_task()
        await any_runner.write_file(handle, "first.txt", "ORIGINAL")
        await any_runner.write_file(handle, "adir/inner.txt", "x")

        with pytest.raises(Exception) as caught:
            await any_runner.write_files(
                handle,
                [
                    FileWriteOp(path="first.txt", content="CHANGED"),
                    FileWriteOp(path="brand/new.txt", content="NEW"),
                    FileWriteOp(path="adir", content="cannot replace a directory"),
                ],
            )

        assert "restored" in str(caught.value)
        assert await any_runner.read_file(handle, "first.txt") == "ORIGINAL"
        scratch = any_runner._scratch(handle)
        assert not (scratch / "brand").exists(), "a directory created for a rolled-back file remained"
        assert not list(scratch.rglob(".aura-*.tmp")), "a temp file was left behind"


class TestRunnerFileIO:
    async def test_read_file_bytes_returns_exact_bytes_and_read_file_keeps_crlf(
        self, any_runner
    ) -> None:
        handle = await any_runner.create_task()
        scratch = any_runner._scratch(handle)
        (scratch / "bin.dat").write_bytes(b"\x00\x01\xff\xfe")
        (scratch / "win.txt").write_bytes(b"a\r\nb\r\n")

        assert await any_runner.read_file_bytes(handle, "bin.dat") == b"\x00\x01\xff\xfe"
        assert await any_runner.read_file(handle, "win.txt") == "a\r\nb\r\n"
        with pytest.raises(Exception, match="binary"):
            await any_runner.read_file(handle, "bin.dat")

    async def test_an_oversized_file_is_refused_for_editing(self, any_runner, monkeypatch) -> None:
        import aura_core.adapters.coding_fakes as fakes
        import aura_core.adapters.docker_sandbox as docker

        monkeypatch.setattr(fakes, "_MAX_EDIT_BYTES", 10)
        monkeypatch.setattr(docker, "_MAX_EDIT_BYTES", 10)
        handle = await any_runner.create_task()
        (any_runner._scratch(handle) / "big.txt").write_text("x" * 100)
        with pytest.raises(Exception, match="too large"):
            await any_runner.read_file(handle, "big.txt")

    async def test_the_workspace_root_itself_is_not_a_writable_path(self, any_runner) -> None:
        handle = await any_runner.create_task()
        for path in (".", "", "./"):
            with pytest.raises(Exception):
                await any_runner.write_file(handle, path, "x")
        outside = any_runner._scratch(handle).parent
        assert not [p for p in outside.iterdir() if p.name.startswith(".aura-")]


class TestDockerRunnerSpecifics:
    @pytest.fixture
    def docker(self, tmp_path) -> DockerSandboxRunner:
        return DockerSandboxRunner(image="img", network="", host_root=str(tmp_path / "ws"))

    async def test_an_absolute_workspace_path_names_the_same_file_the_container_sees(
        self, docker
    ) -> None:
        handle = await docker.create_task()
        await docker.write_file(handle, "/workspace/src/app.py", "print(1)")

        scratch = docker._scratch(handle)
        assert (scratch / "src" / "app.py").read_text() == "print(1)"
        assert not (scratch / "workspace").exists(), "nested a workspace/ inside the workspace"

    async def test_a_file_written_for_the_sandbox_user_can_be_modified_by_it(self, docker) -> None:
        """New files and directories are made by the gateway's uid; the container runs as 10001.
        Either they are handed to that uid, or they are world-writable -- never gateway-only."""
        handle = await docker.create_task()
        await docker.write_file(handle, "pkg/mod.py", "x = 1")
        scratch = docker._scratch(handle)

        for path, world in ((scratch / "pkg", 0o002), (scratch / "pkg" / "mod.py", 0o002)):
            info = path.stat()
            assert info.st_uid == 10001 or info.st_mode & world, oct(info.st_mode)

    async def test_command_output_is_bounded_with_head_and_tail_kept(self, docker, monkeypatch) -> None:
        import aura_core.adapters.docker_sandbox as module

        monkeypatch.setattr(module, "_MAX_LOG_BYTES", 100)
        monkeypatch.setattr(module, "_LOG_HEAD_BYTES", 20)
        data = b"H" * 20 + b"m" * 1000 + b"T" * 80
        capped = module._cap_log(data)
        assert capped.startswith(b"H" * 20) and capped.endswith(b"T" * 80)
        assert b"omitted from the middle" in capped
        assert module._cap_log(b"short") == b"short"


# --------------------------------------------------------------------------------------
# run_command
# --------------------------------------------------------------------------------------


class TestRunCommand:
    async def test_an_empty_or_nul_command_is_refused(self, tools) -> None:
        for command in ("", "   \n", "echo hi\x00x"):
            with pytest.raises(ToolExecutionError):
                await tools["run_command"].execute({"command": command}, _ctx())

    async def test_long_output_keeps_its_tail_and_its_stderr_through_the_registry(self, runner) -> None:
        registry = ToolRegistry(policy=ToolPolicy())
        registry.register_all(build_sandbox_tools(runner))
        command = (
            f"{sys.executable} -c \"import sys; print('noise ' * 8000); "
            "print('FINAL-VERDICT 3 failed'); sys.stderr.write('TRACEBACK-IN-STDERR')\""
        )

        result, _ = await registry.execute(
            ToolCall(id=ToolCallId("1"), name="run_command", arguments={"command": command}),
            _ctx(), capabilities=("engineer_agent",),
        )

        assert result.ok
        assert "FINAL-VERDICT 3 failed" in result.content
        assert "TRACEBACK-IN-STDERR" in result.content
        assert "omitted from the middle" in result.content

    async def test_a_stream_that_ends_without_an_exit_status_says_so(self) -> None:
        class _NoDone:
            async def create_task(self, **kwargs):
                return SandboxHandle(task_id="t")

            async def run(self, *a, **k):  # pragma: no cover
                raise AssertionError

            async def run_streaming(self, handle, command, *, timeout_s):
                yield SandboxOutputEvent(kind="stdout", data="partial")

            async def write_file(self, *a): ...
            async def read_file(self, *a): ...
            async def destroy(self, *a): ...

        tools = _by_name(build_sandbox_tools(_NoDone()))
        result = await tools["run_command"].execute({"command": "x"}, _ctx())
        assert "ended without an exit status" in result

    async def test_a_pdfs_raw_bytes_are_never_dumped_as_stdout(self, tools) -> None:
        ctx = _ctx()
        await tools["write_file"].execute({"path": "t.txt", "content": "x"}, ctx)
        await _run(tools, "printf '%%PDF-1.4 binary-ish' > a.pdf", ctx)

        result = await _run(tools, "head -c 100 a.pdf", ctx)

        assert "raw bytes" in result and "%PDF-1.4 binary-ish" not in result


class TestCatReadsAWholePdf:
    async def _put(self, runner, handle, name: str, data: bytes) -> None:
        (runner._scratch(handle) / name).write_bytes(data)

    async def test_cat_of_a_45_page_pdf_returns_every_page_including_the_last(self, runner) -> None:
        tools = _by_name(build_sandbox_tools(runner))
        ctx = _ctx()
        await tools["write_file"].execute({"path": "seed.txt", "content": "x"}, ctx)
        handle = await tools["write_file"].execute({"path": "seed2.txt", "content": "x"}, ctx) and next(
            iter(runner._scratch_dirs and [SandboxHandle(task_id=next(iter(runner._scratch_dirs)))])
        )
        await self._put(runner, handle, "report.pdf", _big_pdf(45))

        result = await _run(tools, "cat report.pdf", ctx)

        assert LAST_MARKER in result
        assert "45 page(s), 45 read" in result
        assert "unique-token-1-0" in result and "unique-token-22-1" in result
        assert result.startswith("exit_code=0")
        assert "%PDF" not in result and "endobj" not in result

    async def test_the_same_with_dashes_and_quotes(self, runner) -> None:
        tools = _by_name(build_sandbox_tools(runner))
        ctx = _ctx()
        await tools["write_file"].execute({"path": "seed.txt", "content": "x"}, ctx)
        handle = SandboxHandle(task_id=next(iter(runner._scratch_dirs)))
        await self._put(runner, handle, "my report.pdf", _big_pdf(41))

        result = await _run(tools, "cat -- 'my report.pdf'", ctx)

        assert LAST_MARKER in result
        assert "41 page(s), 41 read" in result  # extracted text, not the file's raw bytes
        assert "%PDF" not in result

    async def test_a_pdf_longer_than_one_screen_is_saved_beside_itself_and_readable_to_the_end(
        self, runner
    ) -> None:
        tools = _by_name(build_sandbox_tools(runner))
        ctx = _ctx()
        await tools["write_file"].execute({"path": "seed.txt", "content": "x"}, ctx)
        handle = SandboxHandle(task_id=next(iter(runner._scratch_dirs)))
        data, tokens = _long_pdf(60, 40)
        await self._put(runner, handle, "big.pdf", data)

        result = await _run(tools, "cat big.pdf", ctx)

        assert "big.pdf.txt" in result and "60 page(s), 60 read" in result
        assert len(result) < 20_000
        full = await _run(tools, "cat big.pdf.txt", ctx)
        sidecar = (runner._scratch(handle) / "big.pdf.txt").read_text()
        for token in tokens:
            assert token in sidecar, f"{token} missing from the extracted text"
        assert LAST_MARKER in sidecar
        assert "exit_code=0" in full

    async def test_scanned_pages_are_named_not_silently_dropped(self, runner) -> None:
        tools = _by_name(build_sandbox_tools(runner))
        ctx = _ctx()
        await tools["write_file"].execute({"path": "seed.txt", "content": "x"}, ctx)
        handle = SandboxHandle(task_id=next(iter(runner._scratch_dirs)))
        specs = [{"text": [f"readable page {n}"]} for n in range(1, 41)]
        specs[6] = {"image": True}  # page 7 is a scan
        specs.append({"text": [LAST_MARKER]})
        await self._put(runner, handle, "scan.pdf", _pdf(specs))

        result = await _run(tools, "cat scan.pdf", ctx)

        assert LAST_MARKER in result
        assert "NOT read: 7" in result
        assert "scanned images" in result  # the stderr warning

    async def test_a_piped_cat_is_left_to_the_shell(self, runner) -> None:
        tools = _by_name(build_sandbox_tools(runner))
        ctx = _ctx()
        await tools["write_file"].execute({"path": "seed.txt", "content": "x"}, ctx)
        handle = SandboxHandle(task_id=next(iter(runner._scratch_dirs)))
        await self._put(runner, handle, "a.pdf", _big_pdf(41))

        result = await _run(tools, "cat a.pdf | wc -c", ctx)

        assert "page(s)" not in result

    async def test_an_encrypted_or_corrupt_pdf_is_reported_not_crashed(self, runner) -> None:
        tools = _by_name(build_sandbox_tools(runner))
        ctx = _ctx()
        await tools["write_file"].execute({"path": "seed.txt", "content": "x"}, ctx)
        handle = SandboxHandle(task_id=next(iter(runner._scratch_dirs)))
        await self._put(runner, handle, "bad.pdf", b"%PDF-1.4\nthis is not a pdf at all")

        result = await _run(tools, "cat bad.pdf", ctx)

        assert result.startswith("exit_code=1")

    async def test_the_production_composite_runner_passes_the_exact_bytes_through(self, runner) -> None:
        from aura_core.adapters.host_agent import CompositeSandboxRunner, HostAgentSandboxRunner, HostAgentRegistry

        composite = CompositeSandboxRunner(
            host_runner=HostAgentSandboxRunner(HostAgentRegistry()), fallback=runner
        )
        tools = _by_name(build_sandbox_tools(composite))
        ctx = _ctx()
        await tools["write_file"].execute({"path": "seed.txt", "content": "x"}, ctx)
        handle = SandboxHandle(task_id=next(iter(runner._scratch_dirs)))
        await self._put(runner, handle, "r.pdf", _big_pdf(42))

        result = await _run(tools, "cat r.pdf", ctx)

        assert "42 page(s), 42 read" in result and LAST_MARKER in result

    async def test_a_runner_that_cannot_return_bytes_falls_through_to_the_command(self) -> None:
        class _Plain:
            async def create_task(self, **kwargs):
                return SandboxHandle(task_id="t")

            async def run(self, handle, command, *, timeout_s):
                return SandboxResult(stdout="ran:" + command[-1], stderr="", exit_code=0)

            async def write_file(self, *a): ...
            async def read_file(self, *a): ...
            async def destroy(self, *a): ...

        tools = _by_name(build_sandbox_tools(_Plain()))
        assert "ran:cat x.pdf" in await tools["run_command"].execute({"command": "cat x.pdf"}, _ctx())

    async def test_on_a_runner_without_the_extractor_the_raw_dump_advice_does_not_loop(self) -> None:
        class _Desktop:
            async def create_task(self, **kwargs):
                return SandboxHandle(task_id="t")

            async def run(self, handle, command, *, timeout_s):
                return SandboxResult(stdout="%PDF-1.4 \x00 binary", stderr="", exit_code=0)

            async def write_file(self, *a): ...
            async def read_file(self, *a): ...
            async def destroy(self, *a): ...

        tools = _by_name(build_sandbox_tools(_Desktop()))
        result = await tools["run_command"].execute({"command": "cat x.pdf"}, _ctx())
        assert "cannot extract PDF text" in result and "pdftotext" in result
        assert "`cat FILE.pdf` on its own" not in result  # that is what it just ran


# --------------------------------------------------------------------------------------
# git tools
# --------------------------------------------------------------------------------------


class _IsolatedHomeRunner(InMemorySandboxRunner):
    """The sandbox's real shape for git: a HOME with no config at all (a fresh tmpfs per command)."""

    def __init__(self, home: Path) -> None:
        super().__init__()
        self._home = home

    async def run(self, handle, command, *, timeout_s):
        env = {
            "PATH": os.environ["PATH"], "HOME": str(self._home),
            "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
        }
        process = await asyncio.create_subprocess_exec(
            *command, cwd=str(self._scratch(handle)), env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(process.communicate(), timeout=timeout_s)
        return SandboxResult(out.decode(), err.decode(), process.returncode or 0)


@pytest.fixture
def git_tools(tmp_path) -> dict:
    home = tmp_path / "home"
    home.mkdir()
    return _by_name(build_sandbox_tools(_IsolatedHomeRunner(home)))


class TestGitTools:
    async def test_checking_out_an_existing_branch_does_not_reset_it(self, git_tools) -> None:
        ctx = _ctx()
        t = git_tools
        await _run(t, "git init -q -b main", ctx)
        await t["write_file"].execute({"path": "f.txt", "content": "1"}, ctx)
        await t["git_commit"].execute({"message": "on main"}, ctx)
        main_sha = (await _run(t, "git rev-parse main", ctx)).split("--- stdout ---")[1].split()[0]
        await t["git_checkout_branch"].execute({"branch": "feature"}, ctx)
        await t["write_file"].execute({"path": "f.txt", "content": "2"}, ctx)
        await t["git_commit"].execute({"message": "on feature"}, ctx)

        await t["git_checkout_branch"].execute({"branch": "main"}, ctx)

        after = (await _run(t, "git rev-parse main", ctx)).split("--- stdout ---")[1].split()[0]
        assert after == main_sha, "main was reset onto the feature branch's commits"
        assert "main" in (await t["git_status"].execute({}, ctx))

    @pytest.mark.parametrize("name", ["-f", "--orphan", "a b", "x..y", "bad\nname", "a~1", "@{u}", "", "x.lock"])
    async def test_an_option_shaped_or_malformed_branch_name_is_refused(self, git_tools, name) -> None:
        with pytest.raises(ToolExecutionError, match="not a valid branch name"):
            await git_tools["git_checkout_branch"].execute({"branch": name}, _ctx())

    async def test_commit_works_where_no_git_identity_is_configured(self, git_tools) -> None:
        ctx = _ctx()
        t = git_tools
        await _run(t, "git init -q -b main", ctx)
        await t["write_file"].execute({"path": "f.txt", "content": "1"}, ctx)

        result = await t["git_commit"].execute({"message": "first"}, ctx)

        assert result
        author = await _run(t, "git log -1 --format=%an", ctx)
        assert "AURA Engineer Agent" in author

    async def test_a_configured_identity_is_never_overridden(self, git_tools) -> None:
        ctx = _ctx()
        t = git_tools
        await _run(t, "git init -q -b main && git config user.name Real && git config user.email r@x.io", ctx)
        await t["write_file"].execute({"path": "f.txt", "content": "1"}, ctx)

        await t["git_commit"].execute({"message": "first"}, ctx)

        assert "Real" in await _run(t, "git log -1 --format=%an", ctx)

    async def test_nothing_to_commit_is_reported_in_words(self, git_tools) -> None:
        ctx = _ctx()
        t = git_tools
        await _run(t, "git init -q -b main", ctx)
        await t["write_file"].execute({"path": "f.txt", "content": "1"}, ctx)
        await t["git_commit"].execute({"message": "first"}, ctx)

        with pytest.raises(ToolExecutionError, match="nothing to commit"):
            await t["git_commit"].execute({"message": "again"}, ctx)

    async def test_an_empty_commit_message_is_refused(self, git_tools) -> None:
        with pytest.raises(ToolExecutionError, match="must not be empty"):
            await git_tools["git_commit"].execute({"message": "  "}, _ctx())

    async def test_a_message_that_looks_like_an_option_is_just_a_message(self, git_tools) -> None:
        ctx = _ctx()
        t = git_tools
        await _run(t, "git init -q -b main", ctx)
        await t["write_file"].execute({"path": "f.txt", "content": "1"}, ctx)

        await t["git_commit"].execute({"message": "--amend --no-verify"}, ctx)

        assert "--amend --no-verify" in await _run(t, "git log -1 --format=%s", ctx)

    async def test_git_diff_shows_staged_work_in_a_repository_with_no_commit_yet(self, git_tools) -> None:
        ctx = _ctx()
        t = git_tools
        await _run(t, "git init -q -b main", ctx)
        await t["write_file"].execute({"path": "f.txt", "content": "staged content\n"}, ctx)
        await _run(t, "git add f.txt", ctx)

        diff = await t["git_diff"].execute({}, ctx)

        assert "staged content" in diff
        assert diff != "(no changes)"

    async def test_git_diff_lists_untracked_files_it_cannot_diff(self, git_tools) -> None:
        ctx = _ctx()
        t = git_tools
        await _run(t, "git init -q -b main", ctx)
        await t["write_file"].execute({"path": "tracked.txt", "content": "1"}, ctx)
        await t["git_commit"].execute({"message": "m"}, ctx)
        await t["write_file"].execute({"path": "brand_new.txt", "content": "n"}, ctx)

        diff = await t["git_diff"].execute({}, ctx)

        assert "brand_new.txt" in diff and "Untracked" in diff

    async def test_git_diff_outside_a_repository_is_an_error_not_no_changes(self, git_tools) -> None:
        ctx = _ctx()
        await git_tools["write_file"].execute({"path": "f.txt", "content": "1"}, ctx)
        with pytest.raises(ToolExecutionError, match="not a git repository"):
            await git_tools["git_diff"].execute({}, ctx)

    async def test_git_status_outside_a_repository_is_an_error(self, git_tools) -> None:
        ctx = _ctx()
        await git_tools["write_file"].execute({"path": "f.txt", "content": "1"}, ctx)
        with pytest.raises(ToolExecutionError, match="git status failed"):
            await git_tools["git_status"].execute({}, ctx)


# --------------------------------------------------------------------------------------
# GitHub tools and client
# --------------------------------------------------------------------------------------


class _BytesGitHub(InMemoryGitHubClient):
    """The in-memory forge, plus the exact-bytes read the real client has."""

    def __init__(self) -> None:
        super().__init__()
        self.blobs: dict[tuple[str, str], bytes] = {}
        self.sizes: dict[tuple[str, str], int] = {}

    def put(self, repo: str, path: str, data: bytes) -> None:
        self.blobs[(repo, path)] = data

    async def get_file_bytes(self, repo, path, *, ref=None) -> bytes:
        if (repo, path) in self.blobs:
            return self.blobs[(repo, path)]
        return (await self.get_file_contents(repo, path, ref=ref)).encode("utf-8")

    async def get_file_contents(self, repo, path, *, ref=None) -> str:
        if (repo, path) in self.blobs:
            return self.blobs[(repo, path)].decode("utf-8", errors="replace")
        return await super().get_file_contents(repo, path, ref=ref)

    async def list_files(self, repo, *, ref=None) -> RepoTree:
        tree = await super().list_files(repo, ref=ref)
        extra = tuple(
            RepoFile(path=p, size=len(d)) for (r, p), d in sorted(self.blobs.items()) if r == repo
        )
        return RepoTree(files=tree.files + extra, truncated=tree.truncated)


def _gh(client) -> dict:
    async def resolve(user_id):
        return client

    return _by_name(build_github_tools(resolve, default_repo="acme/widgets"))


class TestGithubGetFileContentsReadsWholePdfs:
    async def test_a_45_page_pdf_is_returned_with_every_page_and_the_last_marker(self) -> None:
        client = _BytesGitHub()
        client.put("acme/widgets", "docs/spec.pdf", _big_pdf(45))

        result = await _gh(client)["github_get_file_contents"].execute({"path": "docs/spec.pdf"}, _ctx())

        assert LAST_MARKER in result
        assert "45 page(s), 45 read" in result
        assert "unique-token-30-2" in result
        assert "%PDF" not in result and "\ufffd" not in result

    async def test_a_pdf_far_longer_than_one_result_is_read_in_full_by_continuing_with_offset(
        self,
    ) -> None:
        client = _BytesGitHub()
        data, tokens = _long_pdf(60, 40)
        client.put("acme/widgets", "big.pdf", data)
        tool = _gh(client)["github_get_file_contents"]

        collected, offset, calls = [], 0, 0
        while True:
            calls += 1
            part = await tool.execute({"path": "big.pdf", "offset": offset}, _ctx())
            assert len(part) <= 40_000, "a part exceeded the result cap and would be truncated"
            collected.append(part)
            match = re.search(r"call again with offset=(\d+)", part)
            if match is None:
                assert "end of file" in part
                break
            offset = int(match.group(1))
            assert calls < 100
        text = "\n".join(collected)

        assert calls > 1
        for token in tokens:
            assert token in text, f"{token} was dropped"
        assert LAST_MARKER in text

    async def test_scanned_pages_are_named_in_the_result(self) -> None:
        client = _BytesGitHub()
        specs = [{"text": [f"readable {n}"]} for n in range(1, 41)]
        specs[11] = {"image": True}
        specs.append({"text": [LAST_MARKER]})
        client.put("acme/widgets", "scan.pdf", _pdf(specs))

        result = await _gh(client)["github_get_file_contents"].execute({"path": "scan.pdf"}, _ctx())

        assert LAST_MARKER in result
        assert "page(s) 12" in result and "NOT read" in result

    async def test_a_binary_file_that_is_not_a_pdf_gets_a_clear_message(self) -> None:
        client = _BytesGitHub()
        client.put("acme/widgets", "logo.png", b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 64)

        with pytest.raises(ToolExecutionError, match="binary file"):
            await _gh(client)["github_get_file_contents"].execute({"path": "logo.png"}, _ctx())

    async def test_a_short_text_file_is_returned_exactly_as_before(self) -> None:
        client = InMemoryGitHubClient()
        client.seed_file("acme/widgets", "main", "README.md", "# Title\r\nbody\n")

        result = await _gh(client)["github_get_file_contents"].execute({"path": "README.md"}, _ctx())

        assert result == "# Title\r\nbody\n"

    async def test_a_long_text_file_continues_with_offset_and_an_offset_past_the_end_is_an_error(
        self,
    ) -> None:
        client = InMemoryGitHubClient()
        text = "".join(f"line {i:05d}\n" for i in range(8000))  # ~96k characters
        client.seed_file("acme/widgets", "main", "big.txt", text)
        tool = _gh(client)["github_get_file_contents"]

        pieces, offset = [], 0
        while True:
            part = await tool.execute({"path": "big.txt", "offset": offset}, _ctx())
            body = part.split("\n", 1)[1] if part.startswith("[characters") else part
            pieces.append(body)
            match = re.search(r"call again with offset=(\d+)", part)
            if match is None:
                break
            offset = int(match.group(1))

        assert "".join(pieces) == text
        with pytest.raises(ToolExecutionError, match="past the end"):
            await tool.execute({"path": "big.txt", "offset": len(text) + 5}, _ctx())

    async def test_an_empty_text_file_is_an_empty_result_not_an_error(self) -> None:
        client = InMemoryGitHubClient()
        client.seed_file("acme/widgets", "main", "empty.txt", "")
        assert await _gh(client)["github_get_file_contents"].execute({"path": "empty.txt"}, _ctx()) == ""

    async def test_a_non_utf8_text_file_says_how_it_was_decoded(self) -> None:
        client = _BytesGitHub()
        client.put("acme/widgets", "legacy.txt", "café\n".encode("latin-1"))

        result = await _gh(client)["github_get_file_contents"].execute({"path": "legacy.txt"}, _ctx())

        assert "not valid UTF-8" in result and "café" in result


def _client_with(handler) -> GitHubClient:
    client = GitHubClient(token="tkn", allowed_repos=("acme/app",))
    client._client = httpx.AsyncClient(
        base_url="https://api.github.com", transport=httpx.MockTransport(handler)
    )
    return client


class TestGitHubClientFiles:
    async def test_a_file_over_one_megabyte_is_fetched_raw_not_returned_empty(self) -> None:
        payload = b"%PDF-1.4 " + os.urandom(2048)
        seen: list[tuple[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append((request.method, request.headers.get("accept", "")))
            if "raw" in request.headers.get("accept", ""):
                return httpx.Response(200, content=payload)
            # What GitHub returns for 1-100 MB: metadata, no content.
            return httpx.Response(
                200, json={"type": "file", "encoding": "none", "content": "", "size": len(payload)}
            )

        client = _client_with(handler)

        assert await client.get_file_bytes("acme/app", "big.pdf") == payload
        assert (await client.get_file_contents("acme/app", "big.pdf")) != ""
        assert any("raw" in accept for _, accept in seen)

    async def test_a_small_file_is_still_one_request(self) -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.raw_path.decode())
            return httpx.Response(
                200,
                json={"type": "file", "encoding": "base64", "size": 3,
                      "content": base64.b64encode(b"abc").decode()},
            )

        client = _client_with(handler)
        assert await client.get_file_contents("acme/app", "a.txt") == "abc"
        assert len(seen) == 1

    async def test_a_rate_limit_says_when_it_lifts(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                403, json={"message": "API rate limit exceeded"},
                headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1800000000"},
            )

        with pytest.raises(GitHubApiError, match=r"rate limited; limit resets at epoch 1800000000"):
            await _client_with(handler).get_issue("acme/app", 1)

    async def test_a_plain_forbidden_is_not_called_a_rate_limit(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, json={"message": "Resource not accessible"})

        with pytest.raises(GitHubApiError) as caught:
            await _client_with(handler).get_issue("acme/app", 1)
        assert "rate limited" not in str(caught.value)

    async def test_symlinks_and_directories_are_described_not_crashed_on(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/link"):
                return httpx.Response(200, json={"type": "symlink", "target": "../x"})
            return httpx.Response(200, json=[{"name": "a"}, {"name": "b"}])

        client = _client_with(handler)
        assert "symlink to ../x" in await client.get_file_contents("acme/app", "link")
        assert await client.get_file_contents("acme/app", "dir") == "a\nb"


class TestGithubWriteTools:
    @pytest.fixture
    def setup(self):
        client = InMemoryGitHubClient()
        client.seed_file("acme/widgets", "main", "README.md", "hi\n")
        client.set_default_branch("acme/widgets", "main")
        return client, _gh(client)

    async def test_committing_text_to_a_pdf_or_binary_path_is_refused(self, setup) -> None:
        client, tools = setup
        for path in ("docs/spec.pdf", "logo.png"):
            with pytest.raises(ToolExecutionError, match="PDF|binary"):
                await tools["github_commit_files"].execute(
                    {"branch": "feature", "message": "m", "files": {path: "text"}}, _ctx()
                )
        assert ("acme/widgets", "feature", "docs/spec.pdf") not in client._files

    @pytest.mark.parametrize("files", [{"a.txt": None}, {"a.txt": 5}, {"a.txt": ["x"]}, {}])
    async def test_non_text_or_empty_file_maps_are_refused_before_any_write(self, setup, files) -> None:
        client, tools = setup
        with pytest.raises(ToolExecutionError):
            await tools["github_commit_files"].execute(
                {"branch": "feature", "message": "m", "files": files}, _ctx()
            )
        assert not any(branch == "feature" for _, branch, _ in client._files)

    async def test_an_oversized_or_over_numerous_commit_is_refused(self, setup) -> None:
        _, tools = setup
        many = {f"f{i}.txt": "x" for i in range(101)}
        with pytest.raises(ToolExecutionError, match="at most 100"):
            await tools["github_commit_files"].execute(
                {"branch": "feature", "message": "m", "files": many}, _ctx()
            )
        huge = {f"h{i}.txt": "x" * 200_000 for i in range(16)}
        with pytest.raises(ToolExecutionError, match="limit is"):
            await tools["github_commit_files"].execute(
                {"branch": "feature", "message": "m", "files": huge}, _ctx()
            )

    @pytest.mark.parametrize("branch", ["feat ure", "a..b", "-x", "x\nmain", "a~1"])
    async def test_a_malformed_branch_is_refused(self, setup, branch) -> None:
        _, tools = setup
        with pytest.raises(ToolExecutionError, match="not a valid branch"):
            await tools["github_commit_files"].execute(
                {"branch": branch, "message": "m", "files": {"a.txt": "x"}}, _ctx()
            )

    async def test_the_default_branch_is_still_refused_in_every_spelling(self, setup) -> None:
        _, tools = setup
        for branch in ("main", "refs/heads/main", "  main  "):
            with pytest.raises(ToolExecutionError, match="refusing to commit"):
                await tools["github_commit_files"].execute(
                    {"branch": branch, "message": "m", "files": {"a.txt": "x"}}, _ctx()
                )

    async def test_the_confirmation_preview_does_not_diff_a_pdfs_bytes(self, setup) -> None:
        client, tools = setup
        spec = tools["github_commit_files"].spec
        preview = await spec.preview(
            {"branch": "feature", "files": {"spec.pdf": "text", "README.md": "new\n"}}, _ctx()
        )
        assert "binary file type" in preview and "will refuse" in preview
        assert "+new" in preview

    async def test_a_pull_request_from_a_branch_to_itself_is_refused(self, setup) -> None:
        _, tools = setup
        with pytest.raises(ToolExecutionError, match="both 'main'"):
            await tools["github_create_draft_pull_request"].execute(
                {"branch": "main", "base": "refs/heads/main", "title": "t", "body": "b"}, _ctx()
            )
        with pytest.raises(ToolExecutionError, match="title must not be empty"):
            await tools["github_create_draft_pull_request"].execute(
                {"branch": "f", "base": "main", "title": " ", "body": "b"}, _ctx()
            )

    async def test_create_branch_accepts_refs_heads_spellings(self, setup) -> None:
        client, tools = setup
        result = await tools["github_create_branch"].execute(
            {"branch": "refs/heads/work", "from_ref": "refs/heads/main"}, _ctx()
        )
        assert "'work'" in result and "'main'" in result

    @pytest.mark.parametrize("repo", ["acme/..", "../widgets", "acme/widgets?x=1", "a b/c"])
    async def test_a_dotty_or_odd_repository_never_reaches_the_client(self, repo) -> None:
        calls: list = []

        class _Spy(InMemoryGitHubClient):
            async def list_files(self, repo, *, ref=None):
                calls.append(repo)
                return await super().list_files(repo, ref=ref)

        tools = _gh(_Spy())
        with pytest.raises(ToolExecutionError):
            await tools["github_list_repository_files"].execute({"repo": repo}, _ctx())
        assert calls == []

    async def test_an_empty_listing_of_a_truncated_tree_does_not_claim_the_path_is_absent(self) -> None:
        class _Truncated(InMemoryGitHubClient):
            async def list_files(self, repo, *, ref=None):
                return RepoTree(files=(RepoFile("a/x.py", 1),), truncated=True)

        result = await _gh(_Truncated())["github_list_repository_files"].execute(
            {"path_prefix": "zzz/"}, _ctx()
        )
        assert "truncated" in result and "may exist" in result


# --------------------------------------------------------------------------------------
# clone_repository
# --------------------------------------------------------------------------------------


class _RecordingGit:
    """Records what the gateway would have run git against; nothing is cloned."""

    def __init__(self) -> None:
        self.clones: list[Path] = []

    async def clone(self, slug, *, dest, token=None, ref=None, depth=None):
        self.clones.append(dest)
        dest.mkdir(parents=True, exist_ok=True)

    async def log(self, path, *, limit=5):
        return "abc123 initial"

    async def current_branch(self, path):
        return "main"

    async def is_repository(self, path):
        return True

    async def default_branch(self, path):
        return "main"

    async def push(self, *a, **k):  # pragma: no cover
        raise AssertionError("must not push")


@pytest.fixture
def durable(tmp_path):
    runner = InMemorySandboxRunner()
    manager = WorkspaceManager(runner=runner, store=InMemoryWorkspaceStore(), min_free_memory_mb=0)
    git = _RecordingGit()

    async def resolver(user_id):
        return InMemoryGitHubClient()

    built = _by_name(build_sandbox_tools(
        runner, code_host_resolver=resolver, workspace_manager=manager, host_git=git,
        token_resolver=None,
    ))
    return built, runner, manager, git


class TestCloneDestinationCannotEscapeTheWorkspace:
    @pytest.mark.parametrize("destination", [
        "../../escape", "..", "/etc/cron.d", "/tmp/x", "C:\\Windows", "-x", "a/../../b", "~/x",
        "a/.git", ".git", "x\x00y", "a\nb",
    ])
    async def test_the_host_side_clone_refuses_a_destination_outside_the_workspace(
        self, durable, destination
    ) -> None:
        built, _, _, git = durable
        with pytest.raises(ToolExecutionError, match="destination"):
            await built["clone_repository"].execute(
                {"repo": "acme/widgets", "destination": destination}, _ctx()
            )
        assert git.clones == [], f"git was asked to clone into {git.clones}"

    async def test_a_plain_nested_destination_is_still_fine(self, durable) -> None:
        built, runner, manager, git = durable
        result = await built["clone_repository"].execute(
            {"repo": "acme/widgets", "destination": "work/widgets"}, _ctx()
        )
        opened = await manager.open(_ALICE, "acme/widgets")
        root = Path(await runner.workspace_path(opened.handle))
        assert git.clones == [root / "work" / "widgets"]
        assert "work/widgets/" in result

    @pytest.mark.parametrize("repo", ["acme/..", "acme/.", "owner/na me"])
    async def test_a_repository_whose_name_is_a_path_component_is_refused(self, durable, repo) -> None:
        built, _, _, git = durable
        with pytest.raises(ToolExecutionError):
            await built["clone_repository"].execute({"repo": repo}, _ctx())
        assert git.clones == []

    async def test_the_rest_copy_refuses_it_too_instead_of_reporting_zero_files_copied(self, tmp_path) -> None:
        client = InMemoryGitHubClient()
        client.seed_file("acme/widgets", "main", "a.txt", "x")

        async def resolver(user_id):
            return client

        tools = _by_name(build_sandbox_tools(InMemorySandboxRunner(), code_host_resolver=resolver))
        with pytest.raises(ToolExecutionError, match="destination"):
            await tools["clone_repository"].execute(
                {"repo": "acme/widgets", "destination": "../x"}, _ctx()
            )

    @pytest.mark.parametrize("ref", ["--upload-pack=touch /tmp/pwn", "-f", "a b", "x\ny"])
    async def test_an_option_shaped_ref_is_refused(self, durable, ref) -> None:
        built, _, _, git = durable
        with pytest.raises(ToolExecutionError, match="ref"):
            await built["clone_repository"].execute({"repo": "acme/widgets", "ref": ref}, _ctx())
        assert git.clones == []

    async def test_a_workspace_over_its_disk_quota_cannot_clone_more_into_it(self, tmp_path) -> None:
        runner = InMemorySandboxRunner()
        manager = WorkspaceManager(
            runner=runner, store=InMemoryWorkspaceStore(), min_free_memory_mb=0, disk_quota_mb=0
        )
        git = _RecordingGit()

        async def resolver(user_id):
            return InMemoryGitHubClient()

        built = _by_name(build_sandbox_tools(
            runner, code_host_resolver=resolver, workspace_manager=manager, host_git=git
        ))
        with pytest.raises(WorkspaceCapacityError, match="disk quota"):
            await built["clone_repository"].execute({"repo": "acme/widgets"}, _ctx())
        assert git.clones == []


class TestRestCloneReportsWhatItDidNotCopy:
    async def test_pdfs_are_named_and_pointed_at_the_tool_that_reads_them_in_full(self) -> None:
        client = _BytesGitHub()
        client.seed_file("acme/widgets", "main", "README.md", "# W")
        client.put("acme/widgets", "docs/spec.pdf", _big_pdf(41))

        async def resolver(user_id):
            return client

        tools = _by_name(build_sandbox_tools(InMemorySandboxRunner(), code_host_resolver=resolver))
        result = await tools["clone_repository"].execute({"repo": "acme/widgets"}, _ctx())

        assert "Copied 1 file(s)" in result
        assert "1 PDF file(s) not copied" in result and "docs/spec.pdf" in result
        assert "github_get_file_contents" in result and "partial" in result

    async def test_a_non_utf8_file_is_not_copied_with_replacement_characters(self) -> None:
        client = _BytesGitHub()
        client.put("acme/widgets", "legacy.txt", "caf\xe9".encode("latin-1"))
        client.seed_file("acme/widgets", "main", "ok.txt", "fine")

        async def resolver(user_id):
            return client

        runner = InMemorySandboxRunner()
        tools = _by_name(build_sandbox_tools(runner, code_host_resolver=resolver))
        result = await tools["clone_repository"].execute({"repo": "acme/widgets"}, _ctx())

        assert "not UTF-8 text" in result and "legacy.txt" in result
        assert "legacy.txt" not in await _run(tools, "ls widgets", _ctx())


class TestGitPush:
    async def test_push_without_a_connected_github_is_refused_before_git_runs(self, tmp_path) -> None:
        runner = InMemorySandboxRunner()
        manager = WorkspaceManager(runner=runner, store=InMemoryWorkspaceStore(), min_free_memory_mb=0)
        git = _RecordingGit()

        async def no_token(user_id):
            return None

        built = _by_name(build_sandbox_tools(
            runner, workspace_manager=manager, host_git=git, token_resolver=no_token
        ))
        await built["open_workspace"].execute({"repo": "acme/widgets"}, _ctx())

        with pytest.raises(ToolExecutionError, match="not connected"):
            await built["git_push"].execute({"branch": "feature/x"}, _ctx())

    async def test_a_tampered_origin_head_does_not_unprotect_the_real_default_branch(self) -> None:
        """The checkout's default branch is a ref the workspace can rewrite. GitHub's answer is
        asked too, so pointing ``origin/HEAD`` at a decoy does not make ``main`` pushable."""

        class _Decoy(_RecordingGit):
            async def default_branch(self, path):
                return "decoy"  # what a rewritten refs/remotes/origin/HEAD would say

        class _Forge(InMemoryGitHubClient):
            pass

        forge = _Forge()
        forge.set_default_branch("acme/widgets", "main")

        async def resolver(user_id):
            return forge

        runner = InMemorySandboxRunner()
        manager = WorkspaceManager(runner=runner, store=InMemoryWorkspaceStore(), min_free_memory_mb=0)
        built = _by_name(build_sandbox_tools(
            runner, code_host_resolver=resolver, workspace_manager=manager, host_git=_Decoy()
        ))
        await built["open_workspace"].execute({"repo": "acme/widgets"}, _ctx())

        for branch in ("main", "decoy"):
            with pytest.raises(ToolExecutionError, match="refusing to push"):
                await built["git_push"].execute({"branch": branch}, _ctx())

    async def test_push_does_not_go_ahead_when_github_cannot_say_which_branch_is_protected(self) -> None:
        class _Down(InMemoryGitHubClient):
            async def default_branch(self, repo):
                raise RuntimeError("503")

        async def resolver(user_id):
            return _Down()

        runner = InMemorySandboxRunner()
        manager = WorkspaceManager(runner=runner, store=InMemoryWorkspaceStore(), min_free_memory_mb=0)
        git = _RecordingGit()
        built = _by_name(build_sandbox_tools(
            runner, code_host_resolver=resolver, workspace_manager=manager, host_git=git
        ))
        await built["open_workspace"].execute({"repo": "acme/widgets"}, _ctx())

        with pytest.raises(ToolExecutionError, match="not pushing"):
            await built["git_push"].execute({"branch": "feature/x"}, _ctx())

    async def test_push_refuses_a_dotty_repository(self, tmp_path) -> None:
        runner = InMemorySandboxRunner()
        manager = WorkspaceManager(runner=runner, store=InMemoryWorkspaceStore(), min_free_memory_mb=0)
        built = _by_name(build_sandbox_tools(runner, workspace_manager=manager, host_git=_RecordingGit()))
        with pytest.raises(ToolExecutionError, match="not a valid GitHub repository"):
            await built["git_push"].execute({"branch": "x", "repo": "acme/.."}, _ctx())

    async def test_paired_desktop_can_push_a_feature_branch_without_docker_or_host_git(
        self, tmp_path
    ) -> None:
        """Audit T-14: on a deployment without Docker (`workspace_manager=None, host_git=None`),
        `git_push` registers when `host_agent_registry` is wired and pushes from the paired
        desktop checkout while still refusing refspecs and the default branch."""
        from aura_core.adapters.host_agent import HostAgentRegistry, HostAgentSession

        runner = InMemorySandboxRunner()
        registry = HostAgentRegistry()

        async def _noop_send(_frame):
            return None

        await registry.register(HostAgentSession(_ALICE, _noop_send))
        built = _by_name(
            build_sandbox_tools(runner, workspace_manager=None, host_git=None, host_agent_registry=registry)
        )
        assert "git_push" in built

        ctx = _ctx()
        origin = tmp_path / "origin.git"
        git = HostGitService(timeout_s=60.0)
        await git._checked(["init", "--bare", "-b", "main", str(origin)], cwd=tmp_path)

        await _run(built, f"git init -b main && git remote add origin {origin}", ctx)
        await built["write_file"].execute({"path": "README.md", "content": "# hello\n"}, ctx)
        await built["git_checkout_branch"].execute({"branch": "feature/desktop-push"}, ctx)
        await built["git_commit"].execute({"message": "initial commit"}, ctx)

        for refused_branch in ("main", "master", "HEAD:main", "--mirror"):
            with pytest.raises(ToolExecutionError):
                await built["git_push"].execute({"branch": refused_branch}, ctx)

        pushed = await built["git_push"].execute({"branch": "feature/desktop-push"}, ctx)
        assert "Pushed feature/desktop-push" in pushed
        assert _heads(origin) == ["refs/heads/feature/desktop-push"]

    async def test_paired_desktop_push_also_respects_github_default_branch(self, tmp_path) -> None:
        from aura_core.adapters.host_agent import HostAgentRegistry, HostAgentSession

        forge = InMemoryGitHubClient()
        forge.set_default_branch("acme/widgets", "develop")

        async def resolver(user_id):
            return forge

        runner = InMemorySandboxRunner()
        registry = HostAgentRegistry()

        async def _noop_send(_frame):
            return None

        await registry.register(HostAgentSession(_ALICE, _noop_send))
        built = _by_name(
            build_sandbox_tools(
                runner,
                code_host_resolver=resolver,
                workspace_manager=None,
                host_git=None,
                host_agent_registry=registry,
            )
        )
        ctx = _ctx()
        await _run(
            built,
            "git init -b develop && git remote add origin https://github.com/acme/widgets.git",
            ctx,
        )
        with pytest.raises(ToolExecutionError, match="refusing to push to 'develop'"):
            await built["git_push"].execute({"branch": "develop"}, ctx)


# --------------------------------------------------------------------------------------
# host-side git: the workspace's .git is writable by the container
# --------------------------------------------------------------------------------------


async def _local_remote(tmp_path: Path, git: HostGitService):
    """A bare origin, a work checkout with a commit on branch ``feat``, and ``remote_url`` pointed
    at the origin so a real push can be observed without the network."""
    origin = tmp_path / "origin.git"
    await git._checked(["init", "--bare", "-b", "main", str(origin)], cwd=tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    await git._checked(["init", "-b", "main"], cwd=work)
    (work / "f.txt").write_text("1\n")
    await git.commit(work, message="first")
    await git.checkout_branch(work, branch="feat")
    (work / "f.txt").write_text("2\n")
    await git.commit(work, message="second")
    git.remote_url = lambda repo: str(origin)  # type: ignore[method-assign]
    return origin, work


def _heads(origin: Path) -> list[str]:
    out = subprocess.run(
        ["git", "for-each-ref", "--format=%(refname)", "refs/heads"],
        cwd=origin, capture_output=True, text=True, check=True,
    ).stdout
    return sorted(out.split())


class TestHostGitIsNotAnExecutionOrExfiltrationPath:
    async def test_a_hook_the_workspace_wrote_is_not_run_by_the_gateway(self, tmp_path) -> None:
        git = HostGitService(timeout_s=60.0)
        origin, work = await _local_remote(tmp_path, git)
        marker = tmp_path / "hook-ran"
        hook = work / ".git" / "hooks" / "pre-push"
        hook.write_text(f"#!/bin/sh\necho ran > {marker}\nexit 0\n")
        hook.chmod(0o755)

        await git.push(work, branch="feat", repo="acme/widgets")

        assert _heads(origin) == ["refs/heads/feat"]
        assert not marker.exists(), "a hook from the workspace executed in the gateway"

    async def test_a_hook_cannot_see_the_token_either(self, tmp_path) -> None:
        git = HostGitService(timeout_s=60.0)
        origin, work = await _local_remote(tmp_path, git)
        stolen = tmp_path / "stolen"
        hook = work / ".git" / "hooks" / "pre-push"
        hook.write_text(f"#!/bin/sh\nprintenv > {stolen}\nexit 0\n")
        hook.chmod(0o755)

        await git.push(work, branch="feat", token="ghp_SECRET_TOKEN", repo="acme/widgets")

        assert not stolen.exists()

    async def test_a_rewritten_remote_cannot_redirect_a_credentialed_push(self, tmp_path) -> None:
        git = HostGitService(timeout_s=60.0)
        origin, work = await _local_remote(tmp_path, git)
        evil = tmp_path / "evil.git"
        await git._checked(["init", "--bare", "-b", "main", str(evil)], cwd=tmp_path)
        await git._checked(
            ["config", f"url.{evil}.insteadOf", str(origin)], cwd=work
        )

        with pytest.raises(GitCommandError, match="refusing to use the GitHub credential"):
            await git.push(work, branch="feat", token="ghp_SECRET_TOKEN", repo="acme/widgets")

        assert _heads(evil) == [] and _heads(origin) == []

    @pytest.mark.parametrize("key,value", [
        ("credential.helper", "!echo pwned"),
        ("credential.https://github.com.helper", "!echo pwned"),
        ("http.proxy", "http://attacker.example:3128"),
        ("http.sslVerify", "false"),
        ("http.extraHeader", "X: y"),
        ("core.sshCommand", "touch /tmp/x"),
        ("core.fsmonitor", "touch /tmp/x"),
        ("include.path", "/etc/passwd"),
        ("remote.origin.pushurl", "https://evil.example/x.git"),
        ("filter.x.clean", "touch /tmp/x"),
    ])
    async def test_credential_bearing_config_keys_are_refused(self, tmp_path, key, value) -> None:
        git = HostGitService(timeout_s=60.0)
        origin, work = await _local_remote(tmp_path, git)
        await git._checked(["config", key, value], cwd=work)

        with pytest.raises(GitCommandError, match="refusing to use the GitHub credential"):
            await git.push(work, branch="feat", token="ghp_SECRET_TOKEN", repo="acme/widgets")

        assert _heads(origin) == []

    async def test_ordinary_config_a_person_would_set_is_accepted(self, tmp_path) -> None:
        git = HostGitService(timeout_s=60.0)
        origin, work = await _local_remote(tmp_path, git)
        for key, value in (("user.name", "Dev"), ("user.email", "d@x.io"), ("core.autocrlf", "false"),
                           ("branch.feat.description", "work")):
            await git._checked(["config", key, value], cwd=work)

        await git.push(work, branch="feat", token="ghp_SECRET_TOKEN", repo="acme/widgets")

        assert _heads(origin) == ["refs/heads/feat"]

    async def test_a_fetch_with_a_credential_only_talks_to_github(self, tmp_path) -> None:
        git = HostGitService(timeout_s=60.0)
        origin, work = await _local_remote(tmp_path, git)
        await git._checked(["remote", "add", "origin", str(origin)], cwd=work)

        with pytest.raises(GitCommandError, match="not a GitHub repository URL"):
            await git.fetch(work, token="ghp_SECRET_TOKEN")

    async def test_a_failed_clone_leaves_no_partial_directory_behind(self, tmp_path) -> None:
        git = HostGitService(timeout_s=60.0)
        git.remote_url = lambda repo: str(tmp_path / "no-such-repo")  # type: ignore[method-assign]
        dest = tmp_path / "ws" / "widgets"

        with pytest.raises(GitCommandError):
            await git.clone("acme/widgets", dest=dest)

        assert not dest.exists()

    async def test_a_clone_never_deletes_a_directory_that_was_already_there(self, tmp_path) -> None:
        git = HostGitService(timeout_s=60.0)
        git.remote_url = lambda repo: str(tmp_path / "no-such-repo")  # type: ignore[method-assign]
        dest = tmp_path / "ws" / "widgets"
        dest.mkdir(parents=True)

        with pytest.raises(GitCommandError):
            await git.clone("acme/widgets", dest=dest)

        assert dest.is_dir()

    async def test_a_dot_git_symlink_or_file_is_not_a_repository(self, tmp_path) -> None:
        git = HostGitService(timeout_s=60.0)
        real = tmp_path / "real"
        real.mkdir()
        await git._checked(["init", "-b", "main"], cwd=real)
        linked = tmp_path / "linked"
        linked.mkdir()
        (linked / ".git").symlink_to(real / ".git", target_is_directory=True)
        filed = tmp_path / "filed"
        filed.mkdir()
        (filed / ".git").write_text(f"gitdir: {real / '.git'}\n")

        assert await git.is_repository(real)
        assert not await git.is_repository(linked)
        assert not await git.is_repository(filed)


# --------------------------------------------------------------------------------------
# MCP bridge
# --------------------------------------------------------------------------------------


class TestMcpBridgeSchemas:
    async def test_a_union_type_does_not_take_down_every_tool(self) -> None:
        client = InMemoryMcpClient(
            tools=[
                McpToolDescriptor(
                    server="s", name="optional", description="d",
                    input_schema={"type": "object", "properties": {
                        "a": {"type": ["string", "null"]},
                        "b": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
                        "c": "not-a-schema",
                    }},
                ),
                McpToolDescriptor(server="s", name="plain", description="d"),
            ],
        )

        tools = _by_name(await build_mcp_bridge_tools(client))

        params = {p.name: p.type for p in tools["mcp_s_optional"].spec.parameters}
        assert params == {"a": "string", "b": "integer", "c": "string"}
        assert "mcp_s_plain" in tools

    async def test_a_garbled_schema_does_not_break_registration(self) -> None:
        client = InMemoryMcpClient(
            tools=[McpToolDescriptor(
                server="s", name="odd", description="d",
                input_schema={"properties": ["x"], "required": "nope"},  # type: ignore[arg-type]
            )],
        )
        assert [t.spec.name for t in await build_mcp_bridge_tools(client)] == ["mcp_s_odd"]

    async def test_integer_arrays_stay_integers_and_object_arrays_are_accepted(self) -> None:
        seen: dict = {}
        client = InMemoryMcpClient(
            tools=[McpToolDescriptor(
                server="s", name="bulk", description="d",
                input_schema={"type": "object", "properties": {
                    "ids": {"type": "array", "items": {"type": "integer"}},
                    "rows": {"type": "array", "items": {"type": "object"}},
                    "tags": {"type": "array"},
                }},
            )],
            handlers={("s", "bulk"): lambda args: seen.update(args) or "ok"},
        )
        registry = ToolRegistry(policy=ToolPolicy())
        registry.register_all(await build_mcp_bridge_tools(client, safe_tools=["mcp_s_bulk"]))

        result, _ = await registry.execute(
            ToolCall(id=ToolCallId("1"), name="mcp_s_bulk", arguments={
                "ids": [1, 2], "rows": [{"k": "v"}], "tags": ["x"],
            }),
            _ctx(), capabilities=("engineer_agent",),
        )

        assert result.ok, result.content
        assert seen == {"ids": [1, 2], "rows": [{"k": "v"}], "tags": ["x"]}

    async def test_a_parameter_named_context_reaches_the_server(self) -> None:
        seen: dict = {}
        client = InMemoryMcpClient(
            tools=[McpToolDescriptor(
                server="s", name="ask", description="d",
                input_schema={"type": "object", "properties": {"context": {"type": "string"}}},
            )],
            handlers={("s", "ask"): lambda args: seen.update(args) or "ok"},
        )
        (tool,) = await build_mcp_bridge_tools(client, safe_tools=["mcp_s_ask"])

        await tool.execute({"context": "user supplied"}, _ctx())

        assert seen == {"context": "user supplied"}

    async def test_two_tools_whose_names_sanitise_alike_do_not_lose_the_rest(self) -> None:
        client = InMemoryMcpClient(
            tools=[
                McpToolDescriptor(server="s", name="a-b", description="first"),
                McpToolDescriptor(server="s", name="a_b", description="second"),
                McpToolDescriptor(server="a", name="b_c", description="cross-1"),
                McpToolDescriptor(server="a_b", name="c", description="cross-2"),
                McpToolDescriptor(server="s", name="other", description="third"),
            ],
        )
        collisions: list[dict[str, str]] = []
        names = [t.spec.name for t in await build_mcp_bridge_tools(client, collisions=collisions)]
        assert names == ["mcp_s_a_b", "mcp_a_b_c", "mcp_s_other"]
        assert collisions == [
            {
                "tool": "mcp_s_a_b",
                "server": "s",
                "mcp_name": "a_b",
                "collides_with_server": "s",
                "collides_with_name": "a-b",
            },
            {
                "tool": "mcp_a_b_c",
                "server": "a_b",
                "mcp_name": "c",
                "collides_with_server": "a",
                "collides_with_name": "b_c",
            },
        ]

    async def test_a_tool_name_longer_than_providers_accept_is_skipped_and_reported(self) -> None:
        client = InMemoryMcpClient(
            tools=[
                McpToolDescriptor(server="s" * 40, name="n" * 40, description="too long"),
                McpToolDescriptor(server="s", name="short", description="fine"),
            ],
        )
        collisions: list[dict[str, str]] = []
        tools = await build_mcp_bridge_tools(client, collisions=collisions)
        assert [t.spec.name for t in tools] == ["mcp_s_short"]
        assert [(c["reason"], c["mcp_name"]) for c in collisions] == [("name_too_long", "n" * 40)]
        assert all(len(t.spec.name) <= 64 for t in tools)

    def test_mcp_servers_whose_names_sanitise_alike_are_refused(self) -> None:
        from aura_core.config.settings import ConfigurationError, McpServerConfig, McpSettings, _parse_mcp_servers

        with pytest.raises(ConfigurationError, match="collides with 'My-Server'"):
            _parse_mcp_servers(json.dumps([
                {"name": "My-Server", "transport": "stdio", "command": "x"},
                {"name": "my_server", "transport": "stdio", "command": "y"},
            ]))
        with pytest.raises(ConfigurationError, match="both sanitise to 'my_server'"):
            McpSettings(
                enabled=True,
                servers=(
                    McpServerConfig(name="My-Server", transport="stdio", command="x"),
                    McpServerConfig(name="my_server", transport="stdio", command="y"),
                ),
            )

    async def test_a_server_failure_becomes_a_tool_error_not_a_crash(self) -> None:
        def boom(args):
            raise RuntimeError("server went away")

        client = InMemoryMcpClient(
            tools=[McpToolDescriptor(server="s", name="x", description="d")],
            handlers={("s", "x"): boom},
        )
        (tool,) = await build_mcp_bridge_tools(client)
        with pytest.raises(ToolExecutionError, match="server went away"):
            await tool.execute({}, _ctx())


# --------------------------------------------------------------------------------------
# desktop_ops and open_exec
# --------------------------------------------------------------------------------------


class _FakeRunner:
    def __init__(self, *results: SandboxResult) -> None:
        self.calls: list[list[str]] = []
        self._results = list(results)

    async def run(self, handle, command, *, timeout_s):
        self.calls.append(list(command))
        return self._results.pop(0) if self._results else SandboxResult("ok", "", 0)


class _Sessions:
    async def handle(self, context, repo=None):
        return "h"


class _Paired:
    async def resolve(self, user_id):
        return object()


def _ops(runner) -> dict:
    return _by_name(desktop_ops.build_desktop_ops_tools(runner, _Sessions(), _Paired()))


class TestDesktopOps:
    async def test_a_builds_failure_at_the_end_of_long_output_is_kept(self) -> None:
        runner = _FakeRunner(SandboxResult(
            stdout="step ok\n" * 5000 + "ERROR: COPY failed: file not found in build context",
            stderr="", exit_code=1,
        ))

        out = await _ops(runner)["docker_build"].execute({"image_tag": "app:dev"}, _ctx())

        assert "COPY failed: file not found in build context" in out
        assert "[truncated]" in out and "omitted from the middle" in out
        assert len(out) < 9_000

    async def test_the_windows_store_python_stub_falls_back_to_python(self) -> None:
        runner = _FakeRunner(
            SandboxResult("", "Python was not found; run without arguments to install from the "
                          "Microsoft Store", 9009),
            SandboxResult("  ncalls  tottime", "", 0),
        )
        out = await _ops(runner)["profile_python"].execute({"source_code": "1"}, _ctx())
        assert [c[0] for c in runner.calls] == ["python3", "python"]
        assert "ncalls" in out

    async def test_a_snippet_that_exits_nonzero_is_not_rerun(self) -> None:
        runner = _FakeRunner(SandboxResult("", "Traceback...", 9009 - 9008))
        await _ops(runner)["profile_python"].execute({"source_code": "raise SystemExit(1)"}, _ctx())
        assert [c[0] for c in runner.calls] == ["python3"]

    @pytest.mark.parametrize("code,expected_rc", [
        ("sum(range(1000))", 0), ("import sys; sys.exit(3)", 3), ("raise ValueError('x')", 1),
    ])
    def test_the_profile_is_printed_even_when_the_snippet_fails(self, code, expected_rc) -> None:
        proc = subprocess.run(
            [sys.executable, "-c", desktop_ops._PROFILE_WRAPPER, code, "5"],
            capture_output=True, text=True, timeout=60,
        )
        assert proc.returncode == expected_rc
        assert "function calls" in proc.stdout

    @pytest.mark.parametrize("path", ["a\nb", "a\tb", "   ", "a\x1bb"])
    async def test_control_characters_in_a_path_are_refused(self, path) -> None:
        runner = _FakeRunner()
        with pytest.raises(ToolExecutionError, match="inside the workspace"):
            await _ops(runner)["docker_build"].execute({"image_tag": "a", "dockerfile": path}, _ctx())
        assert runner.calls == []


class TestOpenExecRegistration:
    @pytest.mark.parametrize("name", ["test_generated", "conftest", "class", "import"])
    async def test_a_module_name_that_collides_with_the_test_file_is_refused(self, name) -> None:
        with pytest.raises(ToolExecutionError, match="reserved"):
            await open_exec.analyze_test_quality.execute(
                {"source_code": "x = 1", "test_code": "def test_a():\n    pass\n",
                 "module_name": name, "operations": "coverage"},
                _ctx(),
            )


# --------------------------------------------------------------------------------------
# durable workspaces
# --------------------------------------------------------------------------------------


@pytest.fixture
def durable_tools():
    runner = InMemorySandboxRunner()
    manager = WorkspaceManager(runner=runner, store=InMemoryWorkspaceStore(), min_free_memory_mb=0)
    built = _by_name(build_sandbox_tools(runner, workspace_manager=manager))
    return built, runner, manager


class TestWorkspaces:
    async def test_after_destroy_the_same_turn_does_not_silently_recreate_the_workspace(
        self, durable_tools
    ) -> None:
        built, _, manager = durable_tools
        ctx = _ctx("one-turn")
        await built["open_workspace"].execute({"repo": "acme/widgets"}, ctx)
        await built["write_file"].execute({"path": "a.txt", "content": "x"}, ctx)

        await built["destroy_workspace"].execute({"repo": "acme/widgets"}, ctx)
        await built["write_file"].execute({"path": "b.txt", "content": "y"}, ctx)

        assert await manager.get(_ALICE, "acme/widgets") is None, (
            "the destroyed repository's workspace was re-created behind the user's back"
        )

    async def test_the_disk_quota_blocks_writes_but_not_the_command_that_frees_space(self) -> None:
        runner = InMemorySandboxRunner()
        manager = WorkspaceManager(
            runner=runner, store=InMemoryWorkspaceStore(), min_free_memory_mb=0, disk_quota_mb=1
        )
        built = _by_name(build_sandbox_tools(runner, workspace_manager=manager))
        ctx = _ctx()
        await built["open_workspace"].execute({"repo": "acme/widgets"}, ctx)
        await _run(built, "head -c 2000000 /dev/zero > big.bin", ctx)

        for name, args in (
            ("write_file", {"path": "a.txt", "content": "x"}),
            ("edit_file", {"path": "a.txt", "find": "x", "replace": "y"}),
            ("write_files", {"operations": [{"path": "a.txt", "content": "x"}]}),
            ("git_commit", {"message": "feat: add a"}),
            ("git_checkout_branch", {"branch": "feature/a"}),
            ("workspace_snapshot", {"label": "before change"}),
            ("workspace_restore", {"snapshot_id": "snap-0123456789ab"}),
        ):
            with pytest.raises(WorkspaceCapacityError, match="disk quota"):
                await built[name].execute(args, ctx)

        assert "exit_code=0" in await _run(built, "rm -f big.bin", ctx)
        await built["write_file"].execute({"path": "a.txt", "content": "x"}, ctx)

    async def test_a_workspace_limit_of_zero_is_a_refusal_not_an_index_error(self) -> None:
        runner = InMemorySandboxRunner()
        manager = WorkspaceManager(
            runner=runner, store=InMemoryWorkspaceStore(), min_free_memory_mb=0, max_workspaces=0
        )
        with pytest.raises(WorkspaceCapacityError, match="limit is 0"):
            await manager.open(_ALICE, "acme/widgets")

    @pytest.mark.parametrize("repo", ["acme/..", "acme/na me", "../x"])
    async def test_open_and_destroy_refuse_a_dotty_repository(self, durable_tools, repo) -> None:
        built, _, manager = durable_tools
        for name in ("open_workspace", "destroy_workspace"):
            with pytest.raises(ToolExecutionError, match="not a valid GitHub repository"):
                await built[name].execute({"repo": repo}, _ctx())
        assert list(await manager.list_for_user(_ALICE)) == []


# --------------------------------------------------------------------------------------
# host-agent transport
# --------------------------------------------------------------------------------------


class _CannedSession(HostAgentSession):
    def __init__(self, payload) -> None:
        super().__init__(_ALICE, send=self._noop)
        self._payload = payload

    async def _noop(self, frame) -> None:
        return None

    async def call(self, method, params, *, timeout_s):
        return self._payload


class TestHostAgentReplies:
    @pytest.mark.parametrize("payload,code,out", [
        ({"stdout": "o", "stderr": "e", "exit_code": None}, -1, "o"),
        ({"stdout": None, "stderr": None, "exit_code": 7}, 7, ""),
        ({"stdout": "x", "exit_code": "not-a-number"}, -1, "x"),
        ("not an object", -1, ""),
        (None, -1, ""),
        ({"stdout": "x", "exit_code": True}, -1, "x"),
    ])
    async def test_a_malformed_run_result_is_a_result_not_an_exception(self, payload, code, out) -> None:
        registry = HostAgentRegistry()
        await registry.register(_CannedSession(payload))
        runner = HostAgentSandboxRunner(registry)
        runner._handle_owners["t"] = _ALICE

        result = await runner.run(SandboxHandle(task_id="t"), ["x"], timeout_s=5)

        assert (result.exit_code, result.stdout) == (code, out)

    async def test_a_null_file_content_is_empty_not_the_word_None(self) -> None:
        registry = HostAgentRegistry()
        await registry.register(_CannedSession({"content": None}))
        runner = HostAgentSandboxRunner(registry)
        runner._handle_owners["t"] = _ALICE
        assert await runner.read_file(SandboxHandle(task_id="t"), "f") == ""
