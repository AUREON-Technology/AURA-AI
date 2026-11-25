"""The Engineer's file tools, end to end: ``write_file``, ``edit_file``, ``write_files``, ``run_command``.

Nothing about the tools is faked. A call goes through the container's real ``ToolRegistry``
(argument validation, ``ToolPolicy``, capability gate, timeout, result guard) into the real tool
handlers and onto a real directory on disk, and the assertions read that directory -- the thing a
user would open -- rather than the tool's own report of what it did. The one stand-in is the
planner that picks the tool (a model would), exactly as ``test_autonomy_profile`` does it.

What this pins that the per-tool tests do not is the *seams*: that a file written with one tool is
byte-for-byte what the next tool reads, with non-ASCII text, CRLF endings and a failed call in
between, and that a refused call leaves the workspace exactly as it was.

The daemon half of the same contract (a paired desktop) is ``aura_hostd/test/task_handler_test.dart``;
its output decoding is pinned there, in "command output is decoded as UTF-8".
"""

from __future__ import annotations

import pytest

from aura_core.adapters.coding_fakes import InMemorySandboxRunner
from aura_core.domain.identifiers import ToolCallId, UserId
from aura_core.domain.messages import ToolCall
from aura_core.kernel import TurnRequest
from aura_core.ports.runtime import ToolContext
from aura_core.tools.coding import build_sandbox_tools
from tests.unit.test_autonomy_profile import CAPS, ScriptedPlanner

pytestmark = pytest.mark.asyncio

ALICE = UserId("alice-e2e")
SESSION = "engineer-e2e"


@pytest.fixture
async def rig(container):
    """The container's real registry with the engineer tools over a real scratch directory."""
    await container.startup()
    runner = InMemorySandboxRunner()
    for tool in build_sandbox_tools(runner):
        container.tools.register(tool, overwrite=True)

    class Rig:
        registry = container.tools

        def __init__(self) -> None:
            self.runner = runner

        @property
        def root(self):
            (root,) = runner._scratch_dirs.values()
            return root

        async def call(self, name: str, **arguments):
            context = ToolContext(
                user_id=ALICE, correlation_id="e2e", deadline_s=60.0, capabilities=CAPS,
                metadata={"session_id": SESSION},
            )
            return await container.tools.execute(
                ToolCall(id=ToolCallId.new(), name=name, arguments=arguments), context,
                capabilities=CAPS,
            )

    return Rig()


async def test_write_edit_and_run_agree_on_the_same_bytes(rig) -> None:
    written, _ = await rig.call("write_file", path="src/notes.txt", content="café مرحبا 😀\nline two\n")
    assert written.ok, written.content
    assert (rig.root / "src" / "notes.txt").read_bytes() == "café مرحبا 😀\nline two\n".encode("utf-8")

    edited, _ = await rig.call("edit_file", path="src/notes.txt", find="line two", replace="line 2 ✓")
    assert edited.ok, edited.content
    assert (rig.root / "src" / "notes.txt").read_text(encoding="utf-8") == "café مرحبا 😀\nline 2 ✓\n"

    shown, _ = await rig.call("run_command", command="cat src/notes.txt && wc -l < src/notes.txt")
    assert shown.ok, shown.content
    assert "café مرحبا 😀" in shown.content and "line 2 ✓" in shown.content
    assert "exit_code=0" in shown.content, "the exit code is reported, not just the text"


async def test_a_crlf_file_stays_crlf_through_an_lf_edit(rig) -> None:
    (await rig.call("write_file", path="win.txt", content="a\r\nb\r\nc\r\n"))
    # The model wrote the find text with LF endings; the file has CRLF. Same text, different spelling.
    edited, _ = await rig.call("edit_file", path="win.txt", find="a\nb", replace="a\nB")
    assert edited.ok, edited.content
    assert (rig.root / "win.txt").read_bytes() == b"a\r\nB\r\nc\r\n"


async def test_a_failed_call_leaves_the_workspace_exactly_as_it_was(rig) -> None:
    await rig.call("write_file", path="a.txt", content="x = 1\nx = 1\n")
    before = (rig.root / "a.txt").read_bytes()

    ambiguous, _ = await rig.call("edit_file", path="a.txt", find="x = 1", replace="x = 2")
    assert not ambiguous.ok and "found 2" in ambiguous.content
    missing, _ = await rig.call("edit_file", path="a.txt", find="y = 1", replace="y = 2")
    assert not missing.ok and "found 0" in missing.content
    same, _ = await rig.call("edit_file", path="a.txt", find="x = 1\nx = 1", replace="x = 1\nx = 1")
    assert not same.ok and "identical" in same.content

    assert (rig.root / "a.txt").read_bytes() == before


async def test_a_directory_path_is_refused_not_written_as_a_file(rig) -> None:
    result, _ = await rig.call("write_file", path="build/", content="x")
    assert not result.ok and "directory" in result.content
    assert not [
        f for root in rig.runner._scratch_dirs.values() for f in root.rglob("*")
    ], "nothing was created"
    for path in (".", ".."):
        refused, _ = await rig.call("write_file", path=path, content="x")
        assert not refused.ok and "directory" in refused.content


async def test_text_that_cannot_be_utf8_is_named_not_left_to_the_codec(rig) -> None:
    result, _ = await rig.call("write_file", path="s.txt", content="bad \ud800 text")
    assert not result.ok
    assert "content" in result.content and "UTF-8" in result.content
    assert "codec can't encode" not in result.content
    assert not [f for root in rig.runner._scratch_dirs.values() for f in root.rglob("s.txt")]


async def test_an_edit_that_differs_only_in_unicode_normalisation_says_so(rig) -> None:
    import unicodedata

    await rig.call("write_file", path="c.txt", content=f"x {unicodedata.normalize('NFD', 'café')} y\n")
    result, _ = await rig.call("edit_file", path="c.txt", find=unicodedata.normalize("NFC", "café"), replace="cafe")
    assert not result.ok
    assert "Unicode" in result.content and "code points" in result.content


async def test_write_files_is_all_or_nothing(rig) -> None:
    await rig.call("write_file", path="keep.txt", content="original\n")
    result, _ = await rig.call("write_files", operations=[
        {"path": "new.txt", "content": "fresh\n"},
        {"path": "keep.txt", "find": "does not occur", "replace": "x"},
    ])
    assert not result.ok
    assert (rig.root / "keep.txt").read_text() == "original\n"
    assert not (rig.root / "new.txt").exists(), "the valid operation was not written either"

    ok, _ = await rig.call("write_files", operations=[
        {"path": "new.txt", "content": "fresh\n"},
        {"path": "keep.txt", "find": "original", "replace": "changed"},
    ])
    assert ok.ok, ok.content
    assert (rig.root / "new.txt").read_text() == "fresh\n"
    assert (rig.root / "keep.txt").read_text() == "changed\n"


async def test_run_command_reports_failure_and_output_from_both_streams(rig) -> None:
    result, _ = await rig.call("run_command", command="echo to-out; echo to-err >&2; exit 3")
    assert "exit_code=3" in result.content
    assert "to-out" in result.content and "to-err" in result.content


async def test_a_path_outside_the_workspace_is_refused(rig) -> None:
    await rig.call("write_file", path="ok.txt", content="x")
    result, _ = await rig.call("write_file", path="../escape.txt", content="x")
    assert not result.ok
    assert not (rig.root.parent / "escape.txt").exists()


async def test_the_same_tools_run_inside_a_real_kernel_turn(container) -> None:
    """A planned turn reaches the file through the kernel's own execution path, not just the registry."""
    await container.startup()
    runner = InMemorySandboxRunner()
    for tool in build_sandbox_tools(runner):
        container.tools.register(tool, overwrite=True)
    container.kernel._deps.planner = ScriptedPlanner(
        "write_file", {"path": "report/summary.md", "content": "# ملخص\nتم ✓\n"}
    )
    result = await container.kernel.run(
        TurnRequest(message="write the summary", user_id=ALICE, capabilities=CAPS)
    )
    assert result.tool_calls == ("write_file",) and result.needs_confirmation == ()
    (root,) = runner._scratch_dirs.values()
    assert (root / "report" / "summary.md").read_text(encoding="utf-8") == "# ملخص\nتم ✓\n"
