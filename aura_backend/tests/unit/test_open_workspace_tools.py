"""ADR-045: the workspace-reading tools, run through a real ToolRegistry against a real temp workspace.

Nothing is mocked: the workspace holds real-format files (poetry.lock, package-lock.json, a virtualenv's
``*.dist-info``, a notebook with an image output, an ELF binary, a tarball with a checksum file), the
tools are the ones ``default_tools`` assembles, and every call goes through ``ToolRegistry.execute`` as the
Engineer persona would make it.
"""

from __future__ import annotations

import ast
import json
import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

from aura_core.adapters.coding_fakes import InMemorySandboxRunner
from aura_core.adapters.memory_stores import InMemoryEngineerRunStore
from aura_core.agents.registry import build_default_agent_registry
from aura_core.domain.engineering import EngineerRun
from aura_core.domain.identifiers import EngineerRunId, ToolCallId, UserId
from aura_core.domain.messages import ToolCall
from aura_core.ports.runtime import ToolContext
from aura_core.tools import analysis as analysis_module
from aura_core.tools import workspace_deps as wd
from aura_core.tools.builtin import default_tools
from aura_core.tools.open_workspace import (
    EXTENDED_ANALYSIS_TOOL_NAMES,
    OPEN_WORKSPACE_FILE_TOOL_NAMES,
    OPEN_WORKSPACE_TOOL_NAMES,
    FileReadLedger,
)
from aura_core.tools.policy import ToolPolicy
from aura_core.tools.registry import ToolRegistry
from tests.unit.open_workspace_fixture import build

ENGINEER = build_default_agent_registry().get("engineer")
CAPS = ("engineer_agent",)


@pytest.fixture(scope="module")
def ws(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("ws")
    build(root)
    return root


def engineer_ctx(user: str = "eng") -> ToolContext:
    return ToolContext(
        user_id=UserId(user), correlation_id="c", deadline_s=60.0, agent_id="engineer",
        allowed_tools=ENGINEER.allowed_tools, allowed_tool_prefixes=ENGINEER.allowed_tool_prefixes,
        max_sensitivity=ENGINEER.risk_ceiling, capabilities=CAPS,
    )


def make_registry(root: Path, **kwargs) -> ToolRegistry:
    registry = ToolRegistry(policy=ToolPolicy())
    registry.register_all(default_tools(workspace=root, enable_analysis_tools=True, **kwargs))
    return registry


_COUNTER = [0]


async def run(registry: ToolRegistry, name: str, ctx: ToolContext | None = None, **arguments):
    _COUNTER[0] += 1
    result, _report = await registry.execute(
        ToolCall(id=ToolCallId(f"t{_COUNTER[0]}"), name=name, arguments=arguments), ctx or engineer_ctx(),
        capabilities=CAPS)
    return result


async def ok(registry: ToolRegistry, name: str, ctx: ToolContext | None = None, **arguments) -> dict:
    result = await run(registry, name, ctx, **arguments)
    assert result.ok, result.content
    return json.loads(result.content)


@pytest.fixture
async def reg(ws) -> ToolRegistry:
    return make_registry(ws)


# -- registration and grants ------------------------------------------------------------------------


class TestRegistrationAndGrants:
    def test_file_tools_register_only_with_a_files_root(self, ws) -> None:
        without = {t.spec.name for t in default_tools(enable_analysis_tools=True)}
        with_root = {t.spec.name for t in default_tools(workspace=ws, enable_analysis_tools=True)}
        assert not (OPEN_WORKSPACE_FILE_TOOL_NAMES & without)
        assert OPEN_WORKSPACE_FILE_TOOL_NAMES <= with_root
        assert "project_status" not in with_root

    def test_project_status_registers_with_a_run_store_and_no_root(self) -> None:
        names = {t.spec.name for t in default_tools(engineer_runs=InMemoryEngineerRunStore())}
        assert "project_status" in names
        assert not (OPEN_WORKSPACE_FILE_TOOL_NAMES & names)

    def test_every_tool_is_tagged_untrusted_content(self, ws) -> None:
        specs = {t.spec.name: t.spec for t in default_tools(
            workspace=ws, engineer_runs=InMemoryEngineerRunStore(), enable_analysis_tools=True)}
        for name in OPEN_WORKSPACE_TOOL_NAMES:
            assert "untrusted_content" in specs[name].tags, name

    def test_the_engineer_holds_them_and_coding_agent_holds_none(self) -> None:
        agents = build_default_agent_registry()
        assert OPEN_WORKSPACE_TOOL_NAMES <= agents.get("engineer").allowed_tools
        assert not (OPEN_WORKSPACE_TOOL_NAMES & agents.get("coding_agent").allowed_tools)

    def test_the_extended_analysis_tools_stay_untagged_and_keep_their_names(self, ws) -> None:
        plain = {t.spec.name: t.spec for t in analysis_module.build_analysis_tools()}
        extended = {t.spec.name: t.spec for t in analysis_module.build_analysis_tools(workspace=ws)}
        assert set(plain) == set(extended)
        for name in EXTENDED_ANALYSIS_TOOL_NAMES:
            assert "untrusted_content" not in extended[name].tags
            old = {p.name for p in plain[name].parameters}
            new = {p.name for p in extended[name].parameters}
            assert old < new, name  # every original parameter is still there, plus new ones
            assert all(not p.required for p in extended[name].parameters if p.name not in {q.name for q in plain[name].parameters})


# -- row 25: repository index ----------------------------------------------------------------------


class TestRepositoryIndex:
    async def test_index_reports_what_was_indexed_and_skips_installed_trees(self, reg) -> None:
        out = await ok(reg, "index_repository")
        assert out["complete"] is True and out["files"] >= 20
        assert out["by_extension"][".py"] >= 8
        assert "node_modules" not in json.dumps(out) and ".venv" not in json.dumps(out)

    async def test_symbol_search_finds_python_and_typescript_definitions(self, reg) -> None:
        py = await ok(reg, "search_repository", query="UserStore")
        assert {"name": "UserStore", "type": "class", "file": "app/users.py", "line": 5} == {
            k: py["symbols"][0][k] for k in ("name", "type", "file", "line")}
        top = py["files"][0]
        assert top["matches"] and "UserStore" in top["matches"][0]["text"]
        ts = await ok(reg, "search_repository", query="slug", extensions=["ts", "tsx"])
        assert ts["symbols"][0]["file"] == "web/src/util.ts" and ts["symbols"][0]["via"] == "regex"

    async def test_the_index_is_reused_until_a_file_changes(self, ws, tmp_path) -> None:
        build(tmp_path)
        registry = make_registry(tmp_path)
        first = await ok(registry, "index_repository", path="app")
        again = await ok(registry, "index_repository", path="app")
        assert first["cached"] is False and again["cached"] is True
        (tmp_path / "app/new_mod.py").write_text("def brand_new_function():\n    return 1\n")
        found = await ok(registry, "search_repository", query="brand_new_function", path="app")
        assert found["symbols"] and found["symbols"][0]["file"] == "new_mod.py"

    async def test_path_outside_the_root_and_a_file_are_refused(self, reg) -> None:
        for bad in ("../etc", "/etc"):
            result = await run(reg, "search_repository", query="x", path=bad)
            assert not result.ok
        assert not (await run(reg, "index_repository", path="notebooks/analysis.ipynb")).ok

    async def test_a_symlinked_directory_is_not_followed_out_of_the_root(self, tmp_path) -> None:
        outside = Path(tempfile.mkdtemp())
        (outside / "secret_module.py").write_text("def stolen_symbol():\n    return 1\n")
        root = tmp_path / "root"
        root.mkdir()
        (root / "a.py").write_text("def mine():\n    return 1\n")
        os.symlink(outside, root / "link")
        registry = make_registry(root)
        out = await ok(registry, "search_repository", query="stolen_symbol")
        assert out["symbols"] == [] and out["files"] == []


# -- row 29: stale context -------------------------------------------------------------------------


class TestStaleContext:
    async def test_no_baseline_is_never_reported_fresh(self, reg) -> None:
        out = await ok(reg, "check_file_staleness", path="app/cli.py")
        assert out["files"][0]["status"] == "no_baseline" and out["stale_count"] == 0

    async def test_record_then_change_then_check(self, tmp_path) -> None:
        build(tmp_path)
        registry = make_registry(tmp_path)
        await ok(registry, "check_file_staleness", path="app/cli.py", operation="record")
        assert (await ok(registry, "check_file_staleness", path="app/cli.py"))["files"][0]["status"] == "fresh"
        (tmp_path / "app/cli.py").write_text("changed = True\n")
        row = (await ok(registry, "check_file_staleness", path="app/cli.py", cached_content="old\n"))
        assert row["files"][0]["status"] == "changed" and row["stale_count"] == 1
        assert "changed = True" in row["diff"]["unified_diff"]
        (tmp_path / "app/cli.py").unlink()
        assert (await ok(registry, "check_file_staleness", path="app/cli.py"))["files"][0]["status"] == "deleted"

    async def test_a_touch_without_a_content_change_is_not_stale(self, tmp_path) -> None:
        build(tmp_path)
        registry = make_registry(tmp_path)
        await ok(registry, "check_file_staleness", path="app/cli.py", operation="record")
        os.utime(tmp_path / "app/cli.py", (1_000_000_000, 1_000_000_000))
        row = (await ok(registry, "check_file_staleness", path="app/cli.py"))["files"][0]
        assert row["status"] == "touched_only" and row["stale"] is False

    async def test_baselines_are_per_user(self, tmp_path) -> None:
        build(tmp_path)
        registry = make_registry(tmp_path)
        await ok(registry, "check_file_staleness", ctx=engineer_ctx("alice"), path="app/cli.py", operation="record")
        other = await ok(registry, "check_file_staleness", ctx=engineer_ctx("bob"), path="app/cli.py")
        assert other["files"][0]["status"] == "no_baseline"

    async def test_reading_a_notebook_records_a_baseline(self, tmp_path) -> None:
        build(tmp_path)
        registry = make_registry(tmp_path)
        await ok(registry, "read_notebook", path="notebooks/analysis.ipynb")
        row = (await ok(registry, "check_file_staleness", path="notebooks/analysis.ipynb"))["files"][0]
        assert row["status"] == "fresh" and row["baseline_how"] == "read"

    async def test_escaping_paths_are_refused_per_file(self, reg) -> None:
        out = await ok(reg, "check_file_staleness", paths=["../../etc/passwd", "app/cli.py"])
        assert out["files"][0]["status"] == "refused"

    async def test_write_and_edit_warn_but_never_block(self, tmp_path) -> None:
        registry = ToolRegistry(policy=ToolPolicy())
        registry.register_all(default_tools(workspace=tmp_path, sandbox_runner=InMemorySandboxRunner()))
        ctx = engineer_ctx()

        async def call(name, **arguments):
            result = await run(registry, name, ctx, **arguments)
            assert result.ok, result.content
            return result.content

        assert "warning" not in await call("write_file", path="src/app.py", content="def f():\n    return 1\n")
        assert "warning" not in await call("edit_file", path="src/app.py", find="return 1", replace="return 2")
        await call("run_command", command="sh -c 'echo \"# formatter\" >> src/app.py'")
        edited = await call("edit_file", path="src/app.py", find="return 2", replace="return 3")
        assert edited.startswith("Edited src/app.py (1 replacement)") and "[stale-context warning]" in edited
        assert "warning" not in await call("edit_file", path="src/app.py", find="return 3", replace="return 4")
        await call("run_command", command="sh -c 'echo \"# user edit\" >> src/app.py'")
        written = await call("write_file", path="src/app.py", content="def f():\n    return 99\n")
        assert written.startswith("Wrote ") and "overwriting content you have not seen" in written
        assert "warning" not in await call("write_file", path="src/other.py", content="x = 1\n")  # new file

    async def test_write_files_does_not_leave_a_stale_baseline_behind(self, tmp_path) -> None:
        registry = ToolRegistry(policy=ToolPolicy())
        registry.register_all(default_tools(workspace=tmp_path, sandbox_runner=InMemorySandboxRunner()))
        ctx = engineer_ctx()
        await run(registry, "write_file", ctx, path="a.py", content="v = 1\n")
        await run(registry, "write_files", ctx, operations=[{"path": "a.py", "content": "v = 2\n"}])
        edited = await run(registry, "edit_file", ctx, path="a.py", find="v = 2", replace="v = 3")
        assert edited.ok and "warning" not in edited.content

    def test_ledger_is_bounded_and_forgets(self) -> None:
        ledger = FileReadLedger(max_entries_per_user=3, max_users=2)
        for i in range(5):
            ledger.record("u", "files", f"f{i}", digest="x", size=1, how="read")
        assert ledger.baseline("u", "files", "f0") is None and ledger.baseline("u", "files", "f4")
        ledger.record("a", "files", "x", digest="x", size=1, how="read")
        ledger.record("b", "files", "x", digest="x", size=1, how="read")
        assert ledger.baseline("u", "files", "f4") is None  # the oldest user was dropped
        assert ledger.stale_note("a", "files", "x", "anything", overwriting=False) is not None
        assert ledger.stale_note("a", "sandbox", "x", "anything", overwriting=False) is None  # other space


# -- row 30 and 61: conventions, skills ---------------------------------------------------------------


class TestConventionsAndSkills:
    async def test_conventions_are_loaded_nearest_last_with_directives(self, reg) -> None:
        out = await ok(reg, "load_project_conventions", path="services/billing",
                       check_file="services/billing/styled.py")
        assert out["searched"] == [".", "services", "services/billing"]
        assert {"AGENTS.md", "services/billing/AGENTS.md", ".editorconfig"} <= set(out["found"])
        assert "Always run pytest before you finish." in out["directives"]["AGENTS.md"]["must_do"]
        assert "Never change invoice numbering." in out["directives"]["services/billing/AGENTS.md"]["forbidden"]
        violations = out["check"]["violations"]
        assert any("tab indentation" in v for v in violations) and any("trailing whitespace" in v for v in violations)
        assert "never override" in out["note"]

    async def test_a_file_outside_the_workspace_cannot_be_checked(self, reg) -> None:
        assert not (await run(reg, "load_project_conventions", check_file="../x.py")).ok

    async def test_skills_are_named_from_frontmatter_not_the_file_stem(self, reg) -> None:
        out = await ok(reg, "load_project_skills")
        by_path = {r["path"]: r for r in out["resources"]}
        assert by_path[".aura/skills/deploy/SKILL.md"]["name"] == "deploy-railway"
        assert by_path[".claude/skills/review/SKILL.md"]["name"] == "pr-review"  # .claude/ is searched too
        assert by_path[".aura/workflows/release.md"]["name"] == "release"

    async def test_match_read_and_workflow(self, reg) -> None:
        match = await ok(reg, "load_project_skills", operation="match", task="deploy the backend to railway")
        assert match["matches"][0]["name"] == "deploy-railway"
        read = await ok(reg, "load_project_skills", operation="read", path=".aura/skills/deploy/SKILL.md")
        assert read["frontmatter"]["tags"] == ["deploy", "railway"] and "/health/ready" in read["body"]
        wf = await ok(reg, "load_project_skills", operation="workflow", path=".aura/workflows/release.md")
        assert [s["commands"] for s in wf["steps"]] == [["pytest"], ["git tag v1"]]

    async def test_read_is_limited_to_resource_folders(self, reg) -> None:
        for path in ("app/users.py", "../x.md"):
            assert not (await run(reg, "load_project_skills", operation="read", path=path)).ok


# -- rows 77 and 83: notebook, binary ----------------------------------------------------------------


class TestNotebookAndBinary:
    async def test_notebook_cells_outputs_and_images(self, reg) -> None:
        out = await ok(reg, "read_notebook", path="notebooks/analysis.ipynb")
        assert out["kernel"] == "python3" and out["total_cells"] == 5 and out["other_cells"] == 1
        types = [c["type"] for c in out["cells"]]
        assert types == ["markdown", "code", "code", "code"]
        assert out["cells"][1]["outputs"] == [{"output_type": "stream", "stream": "stdout", "text": "2"}]
        image = [o for o in out["cells"][2]["outputs"] if o["output_type"] == "image"][0]
        assert image["mime"] == "image/png" and image["returned"] is False and "base64" not in json.dumps(out["cells"][2])[:0]
        assert out["cells"][3]["outputs"][0]["text"] == "ZeroDivisionError: division by zero"

    async def test_script_mode_comments_out_ipython_magics(self, reg) -> None:
        out = await ok(reg, "read_notebook", path="notebooks/analysis.ipynb", as_script=True)
        assert "# %matplotlib inline  # ipython magic" in out["script"]
        compile(out["script"], "nb", "exec")

    async def test_cell_range_and_bad_input(self, reg) -> None:
        out = await ok(reg, "read_notebook", path="notebooks/analysis.ipynb", cells="3")
        assert [c["index"] for c in out["cells"]] == [3]
        assert not (await run(reg, "read_notebook", path="app/users.py")).ok
        assert not (await run(reg, "read_notebook", path="notebooks/analysis.ipynb", cells="x")).ok

    async def test_a_malformed_notebook_is_reported_not_raised(self, tmp_path) -> None:
        (tmp_path / "bad.ipynb").write_text("{not json")
        out = await ok(make_registry(tmp_path), "read_notebook", path="bad.ipynb")
        assert out["valid"] is False and "Failed to parse notebook JSON" in out["error"]

    async def test_elf_png_sqlite_and_random_bytes(self, reg) -> None:
        elf = await ok(reg, "inspect_binary_file", path="bin/tool.bin")
        assert elf["type"] == "Linux ELF Binary" and elf["is_executable"] and elf["metadata"]["container"] == "ELF"
        assert set(elf["hashes"]) == {"md5", "sha1", "sha256", "sha512"}
        png = await ok(reg, "inspect_binary_file", path="assets/logo.png")
        assert png["metadata"] == {"container": "PNG", "width": 32, "height": 16, "bit_depth": 8, "color_type": "RGB"}
        db = await ok(reg, "inspect_binary_file", path="assets/app.db", include_strings=True)
        assert db["metadata"]["container"] == "SQLite 3" and db["metadata"]["page_size"] == 4096
        assert any("CREATE TABLE" in s for s in db["strings"]) and "not instructions" in db["strings_note"]
        rnd = await ok(reg, "inspect_binary_file", path="assets/random.dat", hashes=["sha256", "blake2b", "nope"])
        assert rnd["entropy"]["bits_per_byte"] > 7.5 and rnd["unsupported_hashes"] == ["nope"]

    async def test_hashes_are_the_files_real_digests(self, ws, reg) -> None:
        import hashlib
        out = await ok(reg, "inspect_binary_file", path="bin/tool.bin")
        data = (ws / "bin/tool.bin").read_bytes()
        assert out["hashes"]["sha256"] == hashlib.sha256(data).hexdigest() and out["size_bytes"] == len(data)

    async def test_symlinks_out_of_the_root_are_refused(self, tmp_path) -> None:
        outside = Path(tempfile.mkdtemp()) / "secret.bin"
        outside.write_bytes(b"\x00\x01secret")
        (tmp_path / "link.bin").symlink_to(outside)
        (tmp_path / "link.ipynb").symlink_to(outside)
        registry = make_registry(tmp_path)
        assert not (await run(registry, "inspect_binary_file", path="link.bin")).ok
        assert not (await run(registry, "read_notebook", path="link.ipynb")).ok


# -- row 96: backups ------------------------------------------------------------------------------


class TestVerifyBackup:
    async def test_checksum_file_restore_drill_and_comparison(self, ws, reg) -> None:
        before = sorted(p.relative_to(ws) for p in ws.rglob("*"))
        out = await ok(reg, "verify_backup", path="backups/billing.tar.gz",
                       checksum_file="backups/billing.tar.gz.sha256", compare_to="billing-proj", strip_components=1)
        assert out["recovery_ready"] is True and out["checksum"]["verified"] is True
        assert out["restore_drill"]["restored"] and out["restore_drill"]["temp_directory_removed"]
        cmp_ = out["compare_to_workspace"]
        assert (cmp_["identical"], cmp_["changed_files"], cmp_["missing_files"], cmp_["only_in_workspace_files"]) == (
            1, ["src/main.py"], ["data.csv"], ["NEW.txt"])
        assert cmp_["restored_workspace_matches_archive"] is False
        assert before == sorted(p.relative_to(ws) for p in ws.rglob("*"))  # nothing written to the workspace
        assert not [p for p in os.listdir(tempfile.gettempdir()) if p.startswith("aura_restore_drill_")]

    async def test_wrong_checksum_and_truncated_archive_are_not_ready(self, reg) -> None:
        wrong = await ok(reg, "verify_backup", path="backups/billing.tar.gz", expected_sha256="0" * 64)
        assert wrong["recovery_ready"] is False and wrong["checksum"]["verified"] is False
        cut = await ok(reg, "verify_backup", path="backups/truncated.tar.gz")
        assert cut["recovery_ready"] is False and cut["archive"]["valid_archive"] is False
        assert "restore_drill" not in cut

    async def test_sql_dumps_are_checked_statically_and_no_database_is_opened(self, reg, monkeypatch) -> None:
        def boom(*_a, **_k):
            raise AssertionError("verify_backup connected to a database")

        monkeypatch.setattr(sqlite3, "connect", boom)
        good = await ok(reg, "verify_backup", path="backups/dump.sql")
        assert good["recovery_ready"] is True and good["sql"]["tables_found"] == ["users"]
        assert any("not replayed" in n for n in good["not_verified"])
        cut = await ok(reg, "verify_backup", path="backups/cut.sql")
        assert cut["recovery_ready"] is False and cut["sql"]["truncation_suspected"] is True

    async def test_expected_checksum_and_file_together_are_refused(self, reg) -> None:
        result = await run(reg, "verify_backup", path="backups/billing.tar.gz", expected_sha256="0" * 64,
                           checksum_file="backups/billing.tar.gz.sha256")
        assert not result.ok

    async def test_a_zip_with_an_unsafe_member_is_not_restored(self, tmp_path) -> None:
        import zipfile
        with zipfile.ZipFile(tmp_path / "evil.zip", "w") as zf:
            zf.writestr("../../escape.txt", "x")
        out = await ok(make_registry(tmp_path), "verify_backup", path="evil.zip")
        assert out["recovery_ready"] is False and "restore_drill" not in out
        assert not (tmp_path.parent / "escape.txt").exists()


# -- row 70: project status -----------------------------------------------------------------------


class TestProjectStatus:
    async def _store(self) -> InMemoryEngineerRunStore:
        from datetime import datetime, timedelta, timezone
        store = InMemoryEngineerRunStore()
        base = datetime.now(timezone.utc) - timedelta(minutes=10)
        rows = [("edit_file", "succeeded", "", "eng"),
                ("run_command", "failed", "FAILED tests/test_a.py - AssertionError: 1 != 2", "eng"),
                ("run_command", "succeeded", "3 passed", "eng"),
                ("run_command", "failed", "OTHER USERS SECRET FAILURE", "other")]
        for i, (tool, status, transcript, who) in enumerate(rows):
            await store.record(EngineerRun(
                id=EngineerRunId.new(), user_id=UserId(who), turn_id="t", correlation_id="c", persona="engineer",
                tool=tool, call_id=f"k{i}", status=status, created_at=base + timedelta(minutes=i),
                updated_at=base + timedelta(minutes=i), sandbox_transcript=transcript or None))
        return store

    async def test_answers_from_this_users_recorded_runs_only(self) -> None:
        registry = ToolRegistry(policy=ToolPolicy())
        registry.register_all(default_tools(engineer_runs=await self._store()))
        errors = await ok(registry, "project_status", question="what errors happened?")
        assert errors["intent"] == "error_query" and errors["recorded"] == 3
        assert "AssertionError" in errors["answer"] and "OTHER USERS" not in json.dumps(errors)
        last = await ok(registry, "project_status", question="how did my last run go?")
        assert last["intent"] == "latest_run" and "run_command succeeded" in last["answer"]

    async def test_no_history_is_nothing_recorded_not_no_errors(self) -> None:
        registry = ToolRegistry(policy=ToolPolicy())
        registry.register_all(default_tools(engineer_runs=InMemoryEngineerRunStore()))
        out = await ok(registry, "project_status", question="were there any errors?")
        assert out["recorded"] == 0 and "Nothing was recorded" in out["answer"]
        assert "no errors" not in out["answer"].lower()


# -- rows 19 and 101: dependencies ----------------------------------------------------------------


class TestDependencies:
    async def test_without_paths_the_tool_behaves_exactly_as_before(self, ws) -> None:
        plain = {t.spec.name: t for t in analysis_module.build_analysis_tools()}
        registry = make_registry(ws)
        ctx = engineer_ctx()
        cases = [
            ("check_requirements", {"requirements_text": "flask==2.0\nflask==2.1\nrequests\n"}),
            ("scan_dependency_licenses", {"packages": [{"package": "a", "license": "MIT"},
                                                       {"package": "b", "license": "GPL-3.0"}]}),
            ("scan_code_security", {"source_code": "import os\nos.system('x')\n"}),
            ("verify_python_symbols", {"source_code": "def f(a):\n    return bb\nf(1, 2)\n",
                                       "expected_arities": {"f": {"min_args": 1, "max_args": 1}}}),
            ("analyze_change_impact", {"modified_files": ["a.py"], "extra_dependents": ["b.py"]}),
        ]
        for name, args in cases:
            before = json.loads(await plain[name].execute(args, ctx))
            after = await ok(registry, name, **args)
            assert after == before, name

    async def test_the_original_required_arguments_still_fail_cleanly(self, reg) -> None:
        assert not (await run(reg, "scan_dependency_licenses")).ok
        assert not (await run(reg, "check_requirements")).ok
        assert not (await run(reg, "verify_python_symbols")).ok

    async def test_lockfile_consistency_and_the_projects_own_virtualenv(self, reg) -> None:
        out = (await ok(reg, "check_requirements", paths=["."]))["workspace"]
        assert {"package-lock.json", "poetry.lock", "requirements.lock"} <= set(out["files"]["lockfiles"])
        project = out["consistency"]["projects"][0]
        mismatched = {(m["package"].lower(), m["in"]) for m in project["version_mismatches"]}
        assert ("lodash", "package.json") in mismatched and ("pyyaml", "pyproject.toml") in mismatched
        assert "missinglib" in {d["package"] for d in project["declared_not_locked"]}
        env = out["environment"]
        assert env["performed"] and env["healthy"] is False
        assert {(b["requires"], b["problem"]) for b in env["broken_requirements"]} == {
            ("urllib3>=1.21.1,<3", "installed 0.9"), ("charset-normalizer>=2", "not installed")}
        imports = out["imports"]
        assert "nonexistent_pkg_xyz" in imports["python"]["imports_not_declared"]
        assert {"unusedlib", "numpy"} <= set(imports["python"]["possibly_unused_declared"])
        assert "left-pad" in imports["javascript"]["possibly_unused_declared"]

    async def test_without_a_virtualenv_the_environment_is_not_checked_and_says_so(self, tmp_path) -> None:
        (tmp_path / "requirements.txt").write_text("requests==2.31.0\n")
        out = (await ok(make_registry(tmp_path), "check_requirements", paths=["requirements.txt"]))["workspace"]
        assert out["environment"]["performed"] is False and "gateway's own environment" in out["environment"]["reason"]

    async def test_licences_come_from_the_projects_own_files_and_say_where(self, reg) -> None:
        out = await ok(reg, "scan_dependency_licenses", paths=["."], project_license="MIT")
        bad = {v["package"]: v for v in out["violations"]}
        assert bad["gplpkg==2.0.0"]["license_source"] == "lockfile"
        assert bad["licensed-gpl==1.0.0"]["license_source"] == "workspace site-packages metadata"
        assert out["workspace"]["supply_chain"]["outdated_check"]["performed"] is False
        assert out["workspace"]["supply_chain"]["without_integrity_hash"] >= 1
        assert out["workspace"]["unresolved_licenses"] == 0
        assert "compatibility" in out and out["compatibility"]["host_license"] == "MIT"

    async def test_an_allowlist_applies_to_workspace_packages(self, reg) -> None:
        out = await ok(reg, "scan_dependency_licenses", paths=["package-lock.json"], allowed=["MIT"])
        assert {v["package"] for v in out["violations"]} == {"left-pad==1.3.0", "gplpkg==2.0.0"}

    async def test_paths_with_no_packages_is_an_error(self, tmp_path) -> None:
        (tmp_path / "notes.txt").write_text("hello")
        assert not (await run(make_registry(tmp_path), "scan_dependency_licenses", paths=["notes.txt"])).ok

    async def test_paths_are_refused_to_a_persona_without_workspace_access(self, ws) -> None:
        coding = build_default_agent_registry().get("coding_agent")
        ctx = ToolContext(user_id=UserId("u"), correlation_id="c", deadline_s=30.0, agent_id="coding_agent",
                          allowed_tools=coding.allowed_tools, max_sensitivity=coding.risk_ceiling)
        registry = make_registry(ws)
        for name, args in (("check_requirements", {"paths": ["."]}), ("scan_code_security", {"paths": ["."]}),
                           ("scan_dependency_licenses", {"paths": ["."]}), ("verify_python_symbols", {"paths": ["app"]}),
                           ("analyze_change_impact", {"modified_files": ["a.py"], "repo_dir": "."})):
            result = await run(registry, name, ctx, **args)
            assert not result.ok and "only available to a persona that may read the workspace" in result.content, name
        assert (await run(registry, "check_requirements", ctx, requirements_text="requests==1\n")).ok

    async def test_escaping_paths_are_refused(self, reg) -> None:
        for name, args in (("check_requirements", {"paths": ["../.."]}), ("scan_dependency_licenses", {"paths": ["/etc"]}),
                           ("scan_code_security", {"paths": ["../x"]}), ("verify_python_symbols", {"paths": ["../x.py"]}),
                           ("analyze_change_impact", {"modified_files": ["a"], "repo_dir": "../.."})):
            assert not (await run(reg, name, **args)).ok, name


class TestLockfileParsers:
    def test_each_format(self) -> None:
        poetry = wd.parse_lock("poetry.lock", '[[package]]\nname = "a"\nversion = "1.0"\nfiles = [{file="x", hash="sha256:1"}]\n'
                                              '[[package]]\nname = "b"\nversion = "2.0"\nfiles = []\n')
        assert [(p.name, p.version, p.hashed) for p in poetry.packages] == [("a", "1.0", True), ("b", "2.0", False)]
        uv = wd.parse_lock("uv.lock", '[[package]]\nname = "demo"\nversion = "0.1"\nsource = { virtual = "." }\n'
                                      '[[package]]\nname = "c"\nversion = "3.0"\nsdist = { hash = "sha256:2" }\n')
        assert {p.name: p.kind for p in uv.packages} == {"demo": "path", "c": "registry"}
        pipfile = wd.parse_lock("Pipfile.lock", json.dumps({"default": {"d": {"version": "==4.0", "hashes": ["h"]}},
                                                             "develop": {"e": {"version": "==5.0"}}}))
        assert [(p.name, p.version, p.dev) for p in pipfile.packages] == [("d", "4.0", False), ("e", "5.0", True)]
        npm1 = wd.parse_lock("package-lock.json", json.dumps({"lockfileVersion": 1, "dependencies": {
            "x": {"version": "1.0.0", "integrity": "i", "dependencies": {"y": {"version": "2.0.0"}}}}}))
        assert {p.name for p in npm1.packages} == {"x", "y"}
        yarn = wd.parse_lock("yarn.lock", '"@s/z@^1.0.0", "@s/z@^1.1.0":\n  version "1.1.2"\n  integrity sha512-q\n\n'
                                          'left@^2:\n  version "2.0.0"\n')
        assert {(p.name, p.version) for p in yarn.packages} == {("@s/z", "1.1.2"), ("left", "2.0.0")}
        pnpm = wd.parse_lock("pnpm-lock.yaml", "packages:\n  /react@18.2.0:\n    resolution: {integrity: sha512-a}\n"
                                               "  '@types/node@20.1.0':\n    resolution: {integrity: sha512-b}\n")
        assert {(p.name, p.version) for p in pnpm.packages} == {("react", "18.2.0"), ("@types/node", "20.1.0")}
        req = wd.parse_lock("requirements.lock", "a==1.0 \\\n   --hash=sha256:aa\nb>=2\n")
        assert [(p.name, p.hashed) for p in req.packages] == [("a", True)]

    def test_poetry_and_node_ranges(self) -> None:
        assert wd._py_satisfies("1.2.5", wd._poetry_spec("^1.2")) and not wd._py_satisfies("2.0.0", wd._poetry_spec("^1.2"))
        assert wd._py_satisfies("0.2.9", wd._poetry_spec("^0.2.3")) and not wd._py_satisfies("0.3.0", wd._poetry_spec("^0.2.3"))
        assert wd._node_satisfies("4.17.21", "^4.17.0") and not wd._node_satisfies("3.10.1", "^4.17.0")
        assert wd._node_satisfies("1.5.0", "~1.5.0") and not wd._node_satisfies("1.6.0", "~1.5.0")
        assert wd._node_satisfies("2.0.0", "^1.0.0 || ^2.0.0") and wd._node_satisfies("1.9.9", "1.x")
        assert wd._node_satisfies("1.0.0", "git+https://x/y.git") is None


def test_pep440_matching_agrees_with_the_packaging_library() -> None:
    """workspace_deps carries its own small PEP 440 code (``packaging`` is not a direct dependency);
    pin it against the reference implementation across versions x operators."""
    packaging_specifiers = pytest.importorskip("packaging.specifiers")
    packaging_version = pytest.importorskip("packaging.version")
    versions = ["1.0", "1.0rc1", "1.0.post1", "1.0.dev1", "1.0a2", "1.0b1", "0.9", "1.0.1", "1.10", "1.2", "2.0",
                "1!0.5", "1.0+abc", "5.4.1", "2.31.0"]
    specs = [f"{op}{rhs}" for op in ("==", "!=", "<", "<=", ">", ">=", "~=") for rhs in ("1.0", "1.0.1", "1.2", "0.9", "2.0")]
    specs += ["==1.2.*", "!=1.2.*", ">=1.0,<2", ">=6.0", ">=2,<3", "~=1.4.1"]
    for version in versions:
        for spec in specs:
            expected = packaging_specifiers.SpecifierSet(spec).contains(
                packaging_version.Version(version), prereleases=True)
            assert wd._py_satisfies(version, spec) == expected, (version, spec)
    ordered = sorted(versions, key=wd.pep440_key)
    assert ordered == sorted(versions, key=packaging_version.Version)


# -- row 22: change impact ----------------------------------------------------------------------------


class TestChangeImpact:
    async def test_importers_are_found_transitively_and_tests_are_real(self, reg) -> None:
        out = await ok(reg, "analyze_change_impact", modified_files=["app/users.py", "web/src/util.ts"], repo_dir=".")
        assert out["dependents_scanned"] is True and out["files_scanned"] >= 10
        by = out["affected_by_distance"]
        assert {"app/api.py", "app/cli.py", "app/store.py", "tests/test_users.py", "web/src/page.tsx"} <= set(by["1"])
        assert by["2"] == ["tests/test_cli.py"]  # imports app.cli, which imports the change
        tests = out["tests"]
        assert set(tests["import_the_changed_code"]) == {"tests/test_cli.py", "tests/test_users.py"}
        assert "tests/test_api.py" in tests["naming_guesses_that_do_not_exist"]
        assert "does not run anything" in out["not_detected"] or "cannot detect" in out["not_detected"] or "Run the tests" in out["not_detected"]
        assert out["risk_level"] == "HIGH" and not any("No repository" in w for w in out.get("warnings", []))

    async def test_a_leaf_change_has_a_small_blast_radius(self, reg) -> None:
        out = await ok(reg, "analyze_change_impact", modified_files=["tests/test_other_billing.py"], repo_dir=".")
        assert out["risk_level"] == "LOW" and out["affected_modules"] == []


# -- row 18: symbols --------------------------------------------------------------------------------


class TestSymbolVerification:
    async def test_missing_modules_symbols_and_wrong_arity_in_a_real_file(self, reg) -> None:
        out = await ok(reg, "verify_python_symbols", paths=["app/api.py"])
        row = out["files"][0]
        assert row["imports"]["missing_modules"] == [{"module": "nonexistent_pkg_xyz", "line": 4}]
        assert row["imports"]["missing_symbols"][0]["symbol"] == "missing_fn"
        os_nope = [m for m in row["imports"]["missing_symbols"] if m["symbol"] == "nope"]
        assert os_nope and "standard-library" in os_nope[0]["basis"]
        assert [m["total_args_passed"] for m in row["arity_mismatches"]] == [1, 4]
        assert {u["name"] for u in row["undefined_names"]} == {"undefined_helper"}
        # requests and yaml resolve: they are installed in the workspace's own virtualenv
        assert all(m["module"] not in ("requests", "yaml") for m in row["imports"]["missing_modules"])
        assert out["import_resolution"]["nothing_imported"] is True

    async def test_clean_files_pass_and_a_folder_is_walked(self, reg) -> None:
        out = await ok(reg, "verify_python_symbols", paths=["app"])
        assert out["files_checked"] == 5 and out["files_with_problems"] == 1
        clean = await ok(reg, "verify_python_symbols", paths=["app/store.py", "app/cli.py"])
        assert clean["valid"] is True

    async def test_declared_but_not_installed_is_unverifiable_not_present(self, tmp_path) -> None:
        (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="1"\ndependencies=["numpy>=1"]\n')
        (tmp_path / "a.py").write_text("import numpy\nfrom numpy import anything_at_all\nimport totally_unknown\n")
        out = await ok(make_registry(tmp_path), "verify_python_symbols", paths=["a.py"])
        imports = out["files"][0]["imports"]
        assert [m["module"] for m in imports["unresolved_third_party"]] == ["numpy", "numpy"]
        assert [m["module"] for m in imports["missing_modules"]] == ["totally_unknown"]

    async def test_relative_imports_and_import_of_a_submodule(self, tmp_path) -> None:
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        (pkg / "sub.py").write_text("def real():\n    return 1\n")
        (pkg / "use.py").write_text("from . import sub\nfrom .sub import real, ghost\nfrom .nowhere import q\n")
        out = await ok(make_registry(tmp_path), "verify_python_symbols", paths=["pkg/use.py"])
        imports = out["files"][0]["imports"]
        assert [m["symbol"] for m in imports["missing_symbols"]] == ["ghost"]
        assert [m["module"] for m in imports["missing_modules"]] == [".nowhere"]

    async def test_malformed_arity_spec_is_a_clear_error(self, reg) -> None:
        result = await run(reg, "verify_python_symbols", source_code="f(1)\n", expected_arities={"f": 3})
        assert not result.ok and "must be an object" in result.content


# -- row 17: security -----------------------------------------------------------------------------


class TestSecurityScan:
    async def test_files_are_scanned_and_secret_values_are_never_returned(self, ws, reg) -> None:
        key = (ws / "config/settings.env").read_text().split("=")[1].split("\n")[0]
        out = await ok(reg, "scan_code_security", paths=["scripts", "config"])
        rules = {(f["rule_id"], f["file"]) for f in out["findings"]}
        assert ("SEC-KEY-OPENAI", "config/settings.env") in rules and ("SEC-EVAL-001", "scripts/run.py") in rules
        assert key not in json.dumps(out) and "hunter2hunter2" not in json.dumps(out)
        assert out["dependency_vulnerabilities"]["checked"] is False
        assert "no advisory data" in out["dependency_vulnerabilities"]["reason"]

    async def test_advisories_are_matched_against_locked_versions_only(self, reg) -> None:
        out = await ok(reg, "scan_code_security", paths=["."], advisories_path="advisories.json")
        dep = out["dependency_vulnerabilities"]
        assert dep["checked"] is True and dep["advisories_loaded"] == 2
        # lodash 3.10.1 is below the fix; PyYAML 5.4.1 is at/after the fix of its advisory, so it is not listed
        assert [(v["package"], v["installed"], v["fixed_in"]) for v in dep["vulnerable"]] == [
            ("lodash", "3.10.1", ["4.17.21"])]
        assert out["secure"] is False

    async def test_a_bad_advisory_file_is_an_error(self, tmp_path) -> None:
        (tmp_path / "adv.json").write_text("{\"nothing\": 1}")
        (tmp_path / "a.py").write_text("x = 1\n")
        assert not (await run(make_registry(tmp_path), "scan_code_security", paths=["a.py"], advisories_path="adv.json")).ok

    async def test_lockfiles_and_minified_files_are_not_scanned_for_secrets(self, tmp_path) -> None:
        (tmp_path / "package-lock.json").write_text('{"integrity": "sk-' + "a" * 40 + '"}')
        out = await ok(make_registry(tmp_path), "scan_code_security", paths=["."])
        assert out["findings"] == [] and out["files_scanned"] == 0

    def test_osv_ranges(self) -> None:
        lock = wd.LockInfo("l", "x", "pypi", [wd.Pkg("pkg", "1.5", "pypi", source="l"), wd.Pkg("pkg", "2.0", "pypi", source="l"),
                                              wd.Pkg("pkg", "0.9", "pypi", source="l")])
        adv = [{"id": "A", "affected": [{"package": {"name": "pkg", "ecosystem": "PyPI"}, "ranges": [
            {"type": "ECOSYSTEM", "events": [{"introduced": "1.0"}, {"fixed": "2.0"}]}]}]}]
        assert [h["installed"] for h in wd.match_advisories([lock], adv)] == ["1.5"]
        listed = [{"id": "B", "affected": [{"package": {"name": "pkg", "ecosystem": "PyPI"}, "versions": ["2.0"]}]}]
        assert [h["installed"] for h in wd.match_advisories([lock], listed)] == ["2.0"]


# -- layering: no path to the gateway, the network or a subprocess --------------------------------------

_FORBIDDEN = ("aura_core.api", "aura_core.container", "aura_core.adapters", "aura_core.config", "aura_core.auth",
              "aura_core.kernel", "aura_service", "fastapi", "starlette")
_NETWORK = {"urllib.request", "http.client", "socket", "requests", "httpx", "aiohttp", "websocket", "websockets",
            "smtplib", "ftplib", "telnetlib", "ssl", "subprocess", "sqlite3", "asyncpg", "psycopg2"}


@pytest.mark.parametrize("module", ["open_workspace.py", "workspace_deps.py"])
def test_new_modules_cannot_reach_the_gateway_the_network_a_process_or_a_database(module) -> None:
    path = Path(__file__).resolve().parents[2] / "aura_core" / "tools" / module
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                names = [node.module or ""]
            elif node.level >= 2:
                top = (node.module or "").split(".")[0]
                assert top in {"domain", "ports"}, f"{module} imports aura_core.{top}"
        for name in names:
            assert not any(name == bad or name.startswith(bad + ".") for bad in _FORBIDDEN), (module, name)
            assert name not in _NETWORK, (module, name)
