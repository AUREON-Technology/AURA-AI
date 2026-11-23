"""Simulated end-to-end proof: the analysis, review, memory, drift, release and transcription tools.

Rows covered: 5, 17, 27, 28, 69, 79, 81, 82, 92, 94, 95, 103 of the incomplete-tools sheet.

Each call goes through the container's real ``ToolRegistry.execute`` as the Engineer persona
(``capabilities=("engineer_agent",)``). What is SIMULATED, and nothing else:

* the workspace for the file-reading analysis tools: a real temp directory standing in for
  ``AURA_TOOLS_FILES_ROOT`` (real ``build_analysis_tools(workspace=...)``);
* the HTTPS health endpoints for ``verify_release_health``: a loopback HTTP server. The SSRF guard
  (``open_guarded``) refuses loopback and TLS is unavailable, so ONLY that seam is replaced -- the
  URL allow-list, the body judging, retries and version checks are the real code;
* the speech model for ``transcribe_audio``: a scripted faster-whisper ``WhisperModel`` behind the
  real ``FasterWhisperEngine`` (no faster-whisper install, no audio decoding);
* Redis for the drift history: the container's in-memory ``Cache`` port.

What is NOT exercised: a real model, real telemetry, a real CVE database, real speaker diarization.
"""

from __future__ import annotations

import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace as NS

import pytest

from aura_core.tools.analysis import build_analysis_tools
from aura_core.tools.drift import build_drift_tools
from aura_core.tools.media import build_media_tools
from aura_core.tools.release import build_release_tools
from tests.e2e.sim_support import call, context, ok_json

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def reg(container, tmp_path):
    """The container's registry, with the file-reading analysis tools rooted at a temp workspace."""
    await container.startup()
    root = tmp_path / "ws"
    root.mkdir()
    for tool in build_analysis_tools(root):
        container.tools.register(tool, overwrite=True)
    container.ws = root
    return container.tools


# -------------------------------------------------------------------- row 5: peer_code_reviewer


async def test_row5_review_code_diff_finds_bugs_architecture_violations_and_complexity(reg) -> None:
    out = await ok_json(
        reg, "review_code_diff", filename="aura_core/domain/x.py",
        source_code=(
            "import aura_core.adapters.foo\n\n"
            "def f(a, items=[]):\n    if a == None:\n        pass\n    return items\n"
        ),
    )
    deep = out["deep_review"]
    codes = {f["code"]: f for f in deep["findings"]}
    assert {"ARCH-LAYER-VIOLATION", "BUG-MUTABLE-DEFAULT", "BUG-EQ-NONE"} <= set(codes)
    assert codes["BUG-MUTABLE-DEFAULT"]["line"] == 3 and codes["BUG-EQ-NONE"]["line"] == 4
    assert "domain" in codes["ARCH-LAYER-VIOLATION"]["message"] and "adapters" in codes["ARCH-LAYER-VIOLATION"]["message"]
    assert deep["blocking"] == 2 and out["approved"] is False
    assert "not an independent or model-based" in deep["method"], "the tool says what it is"


async def test_row5_diff_mode_maps_findings_to_new_side_lines_and_deep_can_be_turned_off(reg) -> None:
    diff = (
        "--- a/m.py\n+++ b/m.py\n@@ -1,2 +1,5 @@\n def g():\n-    return 1\n"
        "+    d = {'a': 1, 'a': 2}\n+    assert (d, 'msg')\n+    return d\n"
    )
    out = await ok_json(reg, "review_code_diff", diff_text=diff)
    (finding,) = out["deep_review"]["findings"]
    assert finding["code"] == "BUG-ASSERT-TUPLE" and finding["line"] == 3
    shallow = await ok_json(reg, "review_code_diff", diff_text=diff, deep=False)
    assert not shallow.get("deep_review")


async def test_row5_complexity_threshold_is_honoured(reg) -> None:
    branches = "\n".join(f"    if x == {i}:\n        return {i}" for i in range(12))
    out = await ok_json(reg, "review_code_diff", filename="m.py", source_code=f"def big(x):\n{branches}\n    return -1\n",
                        max_complexity=5)
    assert any("complex" in f["message"].lower() for f in out["deep_review"]["findings"]), out["deep_review"]


# ----------------------------------------------------------- row 17: security_vulnerability_scanner


async def test_row17_scan_files_for_code_issues_and_secrets_without_returning_secret_values(reg, container) -> None:
    ws = container.ws
    secret = "sk-" + "a1B2c3D4" * 5  # built at runtime: a fake OpenAI-shaped key
    (ws / "app").mkdir()
    (ws / "app" / "run.py").write_text("import os\nuser = input()\nresult = eval(user)\n", encoding="utf-8")
    (ws / "app" / "settings.env").write_text(f"OPENAI_API_KEY={secret}\nDEBUG=1\n", encoding="utf-8")
    out = await ok_json(reg, "scan_code_security", paths=["app"])
    rules = {(f["rule_id"], f["file"]) for f in out["findings"]}
    assert ("SEC-EVAL-001", "app/run.py") in rules
    assert any(file == "app/settings.env" and rule.startswith("SEC-KEY") for rule, file in rules), rules
    assert secret not in json.dumps(out), "a secret's value is never returned"
    assert out["dependency_vulnerabilities"]["checked"] is False
    assert "no advisory data" in out["dependency_vulnerabilities"]["reason"]


async def test_row17_supplied_osv_advisories_are_matched_against_lockfile_versions(reg, container) -> None:
    """SIMULATED data: a hand-written OSV advisory (not a real CVE) -- the matching engine is real."""
    ws = container.ws
    (ws / "package-lock.json").write_text(json.dumps({
        "lockfileVersion": 3,
        "packages": {"": {}, "node_modules/leftpadx": {"version": "1.2.0"}, "node_modules/safe": {"version": "9.0.0"}},
    }), encoding="utf-8")
    (ws / "requirements.lock").write_text("oldlib==0.9.1 \\\n    --hash=sha256:aa\nfine==3.0.0\n", encoding="utf-8")
    (ws / "advisories.json").write_text(json.dumps([
        {"id": "SIM-0001", "summary": "simulated npm issue", "affected": [{
            "package": {"ecosystem": "npm", "name": "leftpadx"},
            "ranges": [{"type": "SEMVER", "events": [{"introduced": "0"}, {"fixed": "1.3.0"}]}]}]},
        {"id": "SIM-0002", "summary": "simulated pypi issue", "affected": [{
            "package": {"ecosystem": "PyPI", "name": "oldlib"},
            "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "1.0.0"}]}]}]},
        {"id": "SIM-0003", "summary": "fixed already", "affected": [{
            "package": {"ecosystem": "PyPI", "name": "fine"},
            "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "2.0.0"}]}]}]},
    ]), encoding="utf-8")
    out = await ok_json(reg, "scan_code_security", paths=["."], advisories_path="advisories.json")
    dep = out["dependency_vulnerabilities"]
    assert dep["checked"] is True and dep["advisories_loaded"] == 3
    found = {(v["package"], v["installed"], tuple(v["fixed_in"])) for v in dep["vulnerable"]}
    assert found == {("leftpadx", "1.2.0", ("1.3.0",)), ("oldlib", "0.9.1", ("1.0.0",))}
    assert out["secure"] is False

    bad, _ = await call(reg, "scan_code_security", paths=["."], advisories_path="../../etc/passwd")
    assert not bad.ok, "the advisory file is confined to the workspace"


# --------------------------------------------------------------------- row 27: long_term_memory_store


async def test_row27_remember_and_recall_in_the_engineer_scope_and_credential_guard(container) -> None:
    await container.startup()
    engineer = context("mem-1", memory_scope="engineer")
    fact = "The user prefers tabs over spaces in the acme widgets repository"
    stored, _ = await call(container.tools, "remember_fact", fact=fact, subject="repo conventions", ctx=engineer)
    assert stored.ok and "Recorded" in stored.content
    hit, _ = await call(container.tools, "recall_memory", query="tabs over spaces in the acme widgets repository", ctx=engineer)
    assert hit.ok and "prefers tabs over spaces" in hit.content, hit.content

    other, _ = await call(container.tools, "recall_memory", query="tabs over spaces in the acme widgets repository", ctx=context("mem-2"))
    assert "prefers tabs" not in other.content, "an Engineer-scope memory is not shared into the default scope"

    long_fact, _ = await call(container.tools, "remember_fact", fact="x " * 400, ctx=engineer)
    assert not long_fact.ok, "a fact is limited to 500 characters"
    token = "ghp_" + "Z9y8X7w6" * 5  # fake credential built at runtime
    guarded, _ = await call(container.tools, "remember_fact", fact=f"The user's token is {token}", ctx=engineer)
    assert not guarded.ok and "credential" in guarded.content and token not in guarded.content


# ------------------------------------------------------------------- row 28: token_budget_monitor


async def test_row28_clipped_tool_output_keeps_a_digest_of_the_failures_it_dropped() -> None:
    from aura_core.execution.token_monitor import _clip_middle, digest_omitted

    head = "".join(f"collecting test {i}\n" for i in range(300))
    middle = "".join(f"noise {i}\n" for i in range(2000)) + "ERROR: database connection refused\n" * 5 + "FAILED tests/test_pay.py::test_total\n"
    tail = "".join(f"tail {i}\n" for i in range(300))
    output = head + middle + tail
    clipped = _clip_middle(output)
    assert len(clipped) < len(output) and "omitted to save context" in clipped
    assert "database connection refused (x5)" in clipped and "FAILED tests/test_pay.py::test_total" in clipped
    assert _clip_middle(clipped) == clipped, "clipping is idempotent"
    assert digest_omitted("just\nplain\nnoise\n") == "", "output with nothing salient gets no digest"
    assert len(digest_omitted("\n".join(f"Error number {i} happened here" for i in range(50)))) <= 520


# ------------------------------------------------------------------ row 69: mobile_artifact_summarizer


async def test_row69_summarize_for_mobile_flags_risk_and_never_calls_unrecognised_tests_ready(reg) -> None:
    failing = await ok_json(
        reg, "summarize_for_mobile", task="Fix cart bug",
        diff_text="--- a/.env\n+++ b/.env\n@@ -1 +1 @@\n-A=1\n+A=2\n",
        test_output="===== 2 failed, 10 passed in 1.2s =====\nFAILED tests/test_cart.py::test_pay")
    assert failing["headline"] == "FAILED"
    assert "touches sensitive file .env" in failing["risk_flags"] and "2 test(s) failing" in failing["risk_flags"]
    assert failing["parts"]["tests"] == {"status": "FAILED", "passed": 10, "failed": 2, "errors": 0, "duration": "1.2s",
                                         "banner": failing["parts"]["tests"]["banner"]}
    assert {a["action_id"] for a in failing["card"]["actions"]} == {"approve", "reject"}
    assert len(failing["notification"]) <= 140 and failing["character_count"] <= 600

    passing = await ok_json(reg, "summarize_for_mobile", task="Rename helper",
                            diff_text="--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x\n+y\n",
                            test_output="===== 12 passed in 0.5s =====")
    assert passing["headline"] == "READY" and passing["risk_flags"] == []
    unknown = await ok_json(reg, "summarize_for_mobile", task="t", test_output="everything looks great, trust me")
    assert unknown["headline"] != "READY", "unrecognised test text is never READY"
    short = await ok_json(reg, "summarize_for_mobile", task="t" * 300, max_chars=120)
    assert short["character_count"] <= 120


# --------------------------------------------------------------------- row 79: audio_video_transcriber


class _ScriptedWhisperModel:
    """What ``faster_whisper.WhisperModel.transcribe`` returns: lazy segments and an info object."""

    def transcribe(self, audio, **kwargs):
        words = [NS(start=0.0, end=0.4, word=" Hello", probability=0.95),
                 NS(start=0.4, end=0.9, word=" team", probability=0.9)] if kwargs.get("word_timestamps") else None
        segments = iter([
            NS(start=0.0, end=1.2, text=" Hello team ", avg_logprob=-0.1, no_speech_prob=0.01, words=words),
            NS(start=1.5, end=3.0, text=" the build is green ", avg_logprob=-0.2, no_speech_prob=0.02, words=None),
        ])
        return segments, NS(language="en", language_probability=0.99, duration=3.2)


def _whisper():
    import asyncio

    from aura_core.adapters.whisper_stt import FasterWhisperEngine

    engine = FasterWhisperEngine.__new__(FasterWhisperEngine)  # the model is injected: no faster-whisper import
    engine._model_size, engine._revision = "small", ""
    engine._model, engine._lock = _ScriptedWhisperModel(), asyncio.Lock()
    return engine


async def test_row79_transcribe_audio_returns_timestamped_segments_and_says_there_is_no_diarization(container, tmp_path) -> None:
    await container.startup()
    ws = tmp_path / "media"
    ws.mkdir()
    (ws / "standup.wav").write_bytes(b"RIFF-not-really-a-wav")
    for tool in build_media_tools(ws, stt_engine=_whisper()):
        container.tools.register(tool, overwrite=True)

    out = await ok_json(container.tools, "transcribe_audio", path="standup.wav", include_words=True)
    assert out["text"] == "Hello team the build is green" and out["language"] == "en" and out["duration"] == 3.2
    first, second = out["segments"]
    assert (first["start"], first["end"], first["text"]) == (0.0, 1.2, "Hello team")
    assert [w["text"] for w in first["words"]] == ["Hello", "team"] and second["words"] == []
    assert 0.8 < first["confidence"] <= 1.0
    assert out["window"]["complete"] is True and out["window"]["total_segments"] == 2
    assert out["diarization"]["available"] is False

    bad_type, _ = await call(container.tools, "transcribe_audio", path="standup.txt")
    assert not bad_type.ok
    escape, _ = await call(container.tools, "transcribe_audio", path="../../etc/passwd")
    assert not escape.ok


@pytest.mark.xfail(strict=True, reason="design limit: faster-whisper does no speaker diarization, so every segment's "
                   "speaker is null (a separate model such as pyannote, merged by timestamp, would be needed)")
async def test_row79_speakers_are_identified(container, tmp_path) -> None:
    await container.startup()
    ws = tmp_path / "media"
    ws.mkdir()
    (ws / "call.wav").write_bytes(b"RIFF-not-really-a-wav")
    for tool in build_media_tools(ws, stt_engine=_whisper()):
        container.tools.register(tool, overwrite=True)
    out = await ok_json(container.tools, "transcribe_audio", path="call.wav")
    assert all(segment["speaker"] is not None for segment in out["segments"])


# ----------------------------------------------------------------- row 81: multi_language_ast_analyzer


_SAMPLES = {
    "app.js": ("javascript", "import fs from 'fs';\nclass Cart {\n  total(items) {\n    return items.length;\n  }\n}\n"
               "function checkout(cart) {\n  return cart.total([]);\n}\n", {"Cart", "total", "checkout"}),
    "app.ts": ("typescript", "interface Item { id: number }\nexport class Store {\n  find(id: number): Item | null {\n"
               "    return null;\n  }\n}\nexport function boot(): void {}\n", {"Item", "Store", "find", "boot"}),
    "lib.rs": ("rust", "struct Point { x: i32 }\nimpl Point {\n    fn norm(&self) -> i32 {\n        self.x\n    }\n}\n"
               "fn main() {}\n", {"Point", "norm", "main"}),
    "Svc.java": ("java", "package a;\npublic class Svc {\n    public int run(String a) {\n        return 1;\n    }\n}\n",
                 {"Svc", "run"}),
    "Svc.kt": ("kotlin", "class Svc {\n    fun run(a: String): Int {\n        return 1\n    }\n}\nfun top() {}\n", {"Svc", "run", "top"}),
    "Svc.cs": ("csharp", "namespace A {\n    public class Svc {\n        public int Run(string a) {\n            return 1;\n        }\n    }\n}\n",
               {"Svc", "Run"}),
    "svc.dart": ("dart", "class Svc {\n  int run(String a) {\n    return 1;\n  }\n}\nvoid main() {}\n", {"Svc", "run", "main"}),
}


@pytest.mark.parametrize("filename", sorted(_SAMPLES))
async def test_row81_declarations_are_found_in_every_supported_language(reg, filename) -> None:
    language, source, expected = _SAMPLES[filename]
    out = await ok_json(reg, "analyze_source_structure", source_code=source, filename=filename)
    assert out["language"] == language
    names = {s["name"] for s in out["symbols"]}
    assert expected <= names, (language, names)
    assert all(s["line"] >= 1 and s["end_line"] >= s["line"] for s in out["symbols"])


async def test_row81_python_uses_the_real_ast_with_signatures_and_go_has_receivers_as_parents(reg) -> None:
    py = await ok_json(reg, "analyze_source_structure", filename="a.py", source_code=(
        "import json\n\nclass A(Base):\n    '''Doc line.'''\n    def m(self, x: int = 3) -> str:\n        return json.dumps(x)\n\n"
        "def top(y):\n    return A().m(y)\n"))
    by_name = {s["name"]: s for s in py["symbols"]}
    assert by_name["m"]["parent"] == "A" and by_name["m"]["signature"] == "def m(self, x: int=3) -> str"
    assert by_name["A"]["doc"] == "Doc line." and "json.dumps" in by_name["m"]["calls"]
    assert py["imports"] == [{"module": "json", "line": 1}]

    go = await ok_json(reg, "analyze_source_structure", filename="s.go", source_code=(
        "package main\n\ntype Server struct {\n    Port int\n}\n\nfunc (s *Server) Start() error {\n    return nil\n}\n"))
    start = next(s for s in go["symbols"] if s["name"] == "Start")
    assert start["kind"] == "method" and start["parent"] == "Server"
    assert "not a parser" in go["method"], "the non-Python path says it is declaration scanning"


async def test_row81_comments_and_strings_do_not_create_symbols(reg) -> None:
    out = await ok_json(reg, "analyze_source_structure", filename="x.js", source_code=(
        "// function ghost() {}\nconst s = 'function phantom() {}';\n/* class Nope {} */\nfunction real() {}\n"))
    assert [s["name"] for s in out["symbols"]] == ["real"]


# -------------------------------------------------------------------------- row 82: large_file_chunker


async def test_row82_long_file_windows_reassemble_exactly_and_ingested_docs_retrieve_only_the_right_section(container, tmp_path) -> None:
    from aura_core.domain import DocumentId, UserId
    from aura_core.tools.builtin import build_file_tools

    await container.startup()
    root = tmp_path / "files"
    root.mkdir()
    text = "".join(f"line {i:06d} {'x' * 30}\n" for i in range(5000))  # ~200k characters
    (root / "big.txt").write_text(text, encoding="utf-8")
    container.tools.register_all(build_file_tools(root))

    pieces, offset = [], 0
    for _ in range(20):
        result, _ = await call(container.tools, "read_file", path="big.txt", offset=offset)
        assert result.ok, result.content
        assert len(result.content) <= 40_000
        body = result.content
        pieces.append(body)
        marker = "offset="
        if marker not in body or "NOT the whole" not in body and "continue" not in body.lower():
            break
        import re

        found = re.findall(r"offset[= ](\d+)", body)
        if not found:
            break
        offset = int(found[-1])
    joined = "".join(pieces)
    assert "line 000000" in joined and "line 004999" in joined, "every window together covers the whole file"

    owner = UserId("chunk-user")
    sections = "".join(
        f"## Section {name}\n\n" + (f"{name} details: " + f"the {name} subsystem handles {name} work. " * 40 + "\n\n")
        for name in ("alpha", "bravo", "charlie", "delta", "echo")
    )
    await container.knowledge.ingest(content="# Manual\n\n" + sections, title="Manual", document_id=DocumentId("manual"), owner_id=owner)
    found, _ = await call(container.tools, "search_knowledge", query="charlie subsystem handles charlie work", limit=1,
                          ctx=context("kn", user=owner))
    assert found.ok and "charlie" in found.content and "Section charlie" in found.content
    assert "alpha details" not in found.content, "only the relevant section comes back"


@pytest.mark.xfail(strict=True, reason="design limit: workspace/code files are not chunked into logical units or indexed; "
                   "retrieval of only the necessary sections works for ingested documents only")
async def test_row82_a_workspace_code_file_is_retrievable_by_section_without_ingesting_it(container, tmp_path) -> None:
    from aura_core.domain import UserId
    from aura_core.tools.builtin import build_file_tools

    await container.startup()
    (tmp_path / "svc.py").write_text("def alpha():\n    return 1\n\n\ndef zebra_unique_symbol():\n    return 2\n", encoding="utf-8")
    container.tools.register_all(build_file_tools(tmp_path))
    result, _ = await call(container.tools, "search_knowledge", query="zebra_unique_symbol", ctx=context("w", user=UserId("nobody")))
    assert "return 2" in result.content


# ---------------------------------------------------------------------------- row 92: database_intelligence


_DDL = (
    "CREATE TABLE users (id INT PRIMARY KEY, email TEXT UNIQUE);\n"
    "CREATE TABLE orders (id INT PRIMARY KEY, user_id INT REFERENCES users(id), total INT);\n"
    "CREATE TABLE logs (msg TEXT);\n"
    "CREATE INDEX ix_a ON users (email);\nCREATE INDEX ix_b ON users (email);\n"
)


async def test_row92_schema_from_ddl_flags_missing_fk_index_missing_pk_and_duplicate_indexes(reg) -> None:
    out = await ok_json(reg, "inspect_sql", operation="schema", query=_DDL)
    assert set(out["tables"]) == {"users", "orders", "logs"}
    assert out["tables"]["orders"]["foreign_keys"] == [{"columns": ["user_id"], "ref_table": "users", "ref_columns": ["id"]}]
    assert out["relationships"] == [{"from_table": "orders", "from_columns": ["user_id"], "to_table": "users", "to_columns": ["id"]}]
    codes = {(i["code"], i["table"]) for i in out["issues"]}
    assert ("fk_missing_index", "orders") in codes
    assert any(code == "no_primary_key" and table == "logs" for code, table in codes), codes
    assert any(code.startswith(("duplicate", "redundant")) for code, _t in codes), codes


async def test_row92_schema_from_an_alembic_migration(reg) -> None:
    migration = (
        "def upgrade():\n"
        "    op.create_table('invoices', sa.Column('id', sa.Integer, primary_key=True), sa.Column('customer_id', sa.Integer))\n"
        "    op.create_index('ix_inv_cust', 'invoices', ['customer_id'])\n"
    )
    out = await ok_json(reg, "inspect_sql", operation="schema", schema_info=migration)
    assert "invoices" in out["tables"] and out["tables"]["invoices"]["indexes"]


async def test_row92_query_smells_and_safe_select_generation(reg) -> None:
    smells = await ok_json(reg, "inspect_sql", operation="smells",
                           query="SELECT * FROM orders WHERE lower(email) = 'a' AND name LIKE '%x'")
    assert {s["code"] for s in smells["smells"]} >= {"leading_wildcard_like", "function_on_column"}
    assert smells["plan_available"] is False, "no database connection, so no EXPLAIN plan"

    gen = await ok_json(reg, "inspect_sql", operation="generate_select", table="orders", columns=["id", "total"],
                        schema_info="CREATE TABLE orders (id INT PRIMARY KEY, user_id INT, total INT);",
                        filters=["user_id"], order_by="id", limit=10)
    assert gen["sql"] == 'SELECT "id", "total" FROM "orders" WHERE "user_id" = :user_id ORDER BY "id" LIMIT 10'
    assert gen["parameters"] == ["user_id"] and gen["read_only"] is True
    for table, columns in (("orders; DROP TABLE users", None), ("orders", ["id; DROP TABLE x"]), ("nope", None)):
        args = {"columns": columns} if columns else {}
        refused = await ok_json(reg, "inspect_sql", operation="generate_select", table=table,
                                schema_info="CREATE TABLE orders (id INT PRIMARY KEY);", **args)
        assert "error" in refused and "sql" not in refused, "only names present in the supplied schema are ever put into SQL"


# ------------------------------------------------------------------- row 94: deployment_release_manager


async def test_row94_plan_rollout_gives_a_go_no_go_checklist_and_never_executes(reg) -> None:
    out = await ok_json(
        reg, "plan_rollout", service_name="gateway", target_version="1.4.0", previous_version="1.3.2",
        changelog="## [1.4.0] - 2026-11-20\n### Added\n- BREAKING: removed /v2/legacy endpoint\n",
        migrations=[{"name": "0020_drop_col", "text": "def upgrade():\n    op.drop_table('old')\n\ndef downgrade():\n    pass\n"}],
        strategy="canary", changed_files_count=80, test_coverage_pct=55.0)
    items = {i["item"]: i for i in out["release_checklist"]["items"]}
    assert items["version increases"]["status"] == "pass" and "minor" in items["version increases"]["detail"]
    assert items["semver matches content"]["status"] == "warn"
    assert items["migrations are reversible"]["status"] == "fail"
    assert out["release_checklist"]["go"] is False and out["release_checklist"]["blockers"] == ["migrations are reversible"]
    checklist = out["release_checklist"]
    assert checklist["risk"]["risk_level"] == "CRITICAL" and out["executed"] is False
    assert checklist["rollback_plan"]["rollback_target"] == "1.3.2" and checklist["rollback_plan"]["executed"] is False

    clean = await ok_json(reg, "plan_rollout", service_name="gateway", target_version="1.4.1", previous_version="1.4.0",
                          changelog="## [1.4.1] - 2026-11-22\n### Fixed\n- cart total\n", changed_files_count=3, test_coverage_pct=91.0)
    assert clean["release_checklist"]["go"] is True and clean["release_checklist"]["blockers"] == []


class _Health(BaseHTTPRequestHandler):
    bodies: dict = {}

    def do_GET(self) -> None:  # noqa: N802
        status, body = self.bodies.get(self.path, (404, {}))
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args) -> None:
        return None


async def test_row94_verify_release_health_judges_the_body_version_and_fields(container, monkeypatch) -> None:
    """SIMULATED: a loopback HTTP server stands in for the https health endpoints; only
    ``open_guarded`` (SSRF guard + TLS) is replaced -- it rewrites the allow-listed https URL to the loopback
    server. The allow-list, body judging, version/field checks and retry loop are the real code."""
    from aura_core.tools.open import release_ext

    await container.startup()
    _Health.bodies = {
        "/health": (200, {"healthy": True, "version": "1.4.0", "schema": {"revision": "0020"}}),
        "/old": (200, {"healthy": True, "version": "1.3.2"}),
        "/sick": (200, {"status": "degraded"}),
        "/down": (503, {}),
    }
    server = HTTPServer(("127.0.0.1", 0), _Health)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]

    def loopback(request, *, timeout, follow_redirects=True):
        url = request.full_url
        assert url.startswith("https://status.example.test/"), url
        rewritten = urllib.request.Request(url.replace("https://status.example.test", f"http://127.0.0.1:{port}"),
                                           headers=dict(request.header_items()))
        return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(rewritten, timeout=timeout)

    monkeypatch.setattr(release_ext, "open_guarded", loopback)
    for tool in build_release_tools({"status.example.test"}):
        container.tools.register(tool, overwrite=True)
    base = "https://status.example.test"
    try:
        good = await ok_json(container.tools, "verify_release_health", urls=[f"{base}/health"], expected_version="1.4.0",
                             required_fields=["schema.revision"], attempts=1, interval_s=0.1)
        assert good["all_healthy"] is True and good["verdict"] == "release is serving"
        assert good["endpoints"][0]["last"]["version"] == "1.4.0" and good["executed"] is False

        mixed = await ok_json(container.tools, "verify_release_health",
                              urls=[f"{base}/old", f"{base}/sick", f"{base}/down"], expected_version="1.4.0",
                              attempts=2, interval_s=0.1)
        assert mixed["all_healthy"] is False
        problems = {e["url"].rsplit("/", 1)[1]: e["last"] for e in mixed["endpoints"]}
        assert "serving version '1.3.2', expected '1.4.0'" in problems["old"]["problems"]
        assert any("degraded" in p for p in problems["sick"]["problems"])
        assert problems["down"]["error"] == "HTTP 503"
        assert all(e["attempts"] == 2 for e in mixed["endpoints"]), "unhealthy endpoints are retried"
    finally:
        server.shutdown()

    for refused in ("https://evil.example.org/health", "http://status.example.test/health", "https://127.0.0.1/health",
                    "https://status.example.test:8443/health", "https://user@status.example.test/health"):
        result, _ = await call(container.tools, "verify_release_health", urls=[refused], attempts=1)
        assert not result.ok, refused


# ----------------------------------------------------------------- row 95: observability_incident_analyzer


async def test_row95_incident_analysis_ranks_a_hypothesis_from_logs_and_metrics(reg) -> None:
    logs = [
        "2026-11-23T10:00:01Z ERROR db: connection refused",
        "2026-11-23T10:00:02Z ERROR db: connection refused",
        "2026-11-23T10:00:02Z ERROR db: connection refused",
        "2026-11-23T10:00:03Z INFO api: request ok",
    ]
    out = await ok_json(reg, "analyze_logs", operation="incident", logs=logs,
                        metrics_text="# TYPE app_up gauge\napp_up 0\n# TYPE http_requests_total counter\n"
                                     'http_requests_total{code="200"} 90\nhttp_requests_total{code="500"} 10\n')
    assert out["logs"]["error_count"] == 3 and out["logs"]["levels"] == {"error": 3, "info": 1}
    assert out["logs"]["top_signatures"][0] == {"signature": "db: connection refused", "count": 3, "first_line": 1}
    kinds = {a["kind"] for a in out["metrics"]["anomalies"]}
    assert {"down", "error_share"} <= kinds
    assert out["top_hypothesis"] == "dependency_unreachable"
    top = out["hypotheses"][0]
    assert top["evidence_count"] == 3 and top["supporting_metrics"][0]["metric"] == "app_up"
    assert "no live telemetry connection" in out["source"]


async def test_row95_metrics_diff_against_a_baseline_and_structured_logs_burst(reg) -> None:
    before = "# TYPE http_errors_total counter\nhttp_errors_total 5\n"
    after = "# TYPE http_errors_total counter\nhttp_errors_total 500\n"
    jumped = await ok_json(reg, "analyze_logs", operation="metrics", metrics_text=after, baseline_metrics_text=before)
    assert any("http_errors_total" in a["message"] and a["kind"] != "down" for a in jumped["metrics"]["anomalies"]), jumped

    lines = [json.dumps({"ts": f"2026-11-23T10:00:{s:02d}Z", "level": "error", "msg": "upstream timeout", "component": "gateway"})
             for s in (1, 1, 2, 2, 2, 2, 3)]
    lines.append(json.dumps({"ts": "2026-11-23T10:09:00Z", "level": "info", "msg": "ok", "component": "gateway"}))
    structured = await ok_json(reg, "analyze_logs", operation="structured", logs=lines)
    assert structured["levels"]["error"] == 7 and structured["errors_by_component"] == {"gateway": 7}
    assert structured["top_signatures"][0]["count"] == 7


# ------------------------------------------------------------------- row 103: model_drift_and_performance_monitor


async def test_row103_record_baseline_then_detect_accuracy_drop_and_mean_shift(container) -> None:
    """The history lives in the container's in-memory ``Cache`` port (standing in for Redis)."""
    await container.startup()
    for tool in build_drift_tools(container.cache_backend):
        container.tools.register(tool, overwrite=True)
    reg = container.tools

    early, _ = await call(reg, "compare_model_metric", model="qwen", metric="accuracy")
    assert early.ok and json.loads(early.content)["status"] == "insufficient_history"
    pinned = await ok_json(reg, "record_model_metric", operation="record", model="qwen", metric="accuracy", value=0.91)
    assert pinned["recorded"] is True and pinned["entries"] == 1
    await ok_json(reg, "record_model_metric", operation="set_baseline", model="qwen", metric="accuracy")
    await ok_json(reg, "record_model_metric", operation="record", model="qwen", metric="accuracy", value=0.70, note="nightly eval")
    cmp = await ok_json(reg, "compare_model_metric", model="qwen", metric="accuracy", include_history=True)
    assert cmp["drift_detected"] is True and cmp["evaluation_triggered"] is False
    assert [h["value"] for h in cmp["history"]] == [0.91, 0.70]

    await ok_json(reg, "record_model_metric", operation="record", model="qwen", metric="latency_ms", values=[100, 110, 105, 95])
    await ok_json(reg, "record_model_metric", operation="record", model="qwen", metric="latency_ms", values=[210, 230, 220, 205])
    shifted = await ok_json(reg, "compare_model_metric", model="qwen", metric="latency_ms")
    assert shifted["drift_detected"] is True

    mine = await ok_json(reg, "check_model_drift", operation="mean_shift", baseline_values=[1, 2, 3, 4], current_values=[3, 4, 5, 6])
    assert mine["drift_detected"] is True and mine["relative_shift"] == 0.8
    stable = await ok_json(reg, "check_model_drift", operation="accuracy", baseline_accuracy=0.90, current_accuracy=0.88)
    assert stable["drift_detected"] is False

    other_user = context("o", user=type(context().user_id)("someone-else"))
    isolated, _ = await call(reg, "compare_model_metric", model="qwen", metric="accuracy", ctx=other_user)
    assert json.loads(isolated.content)["status"] == "insufficient_history", "history is per owner"
    cleared = await ok_json(reg, "record_model_metric", operation="clear", model="qwen", metric="accuracy")
    assert cleared
