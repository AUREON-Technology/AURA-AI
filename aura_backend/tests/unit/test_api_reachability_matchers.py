"""The reachability matrix credits only real API callers (audit FSA-083 / E2-02).

`scripts/api_reachability.py` decides which routes have a client caller by searching the client
sources for a string that builds the path. Three ways it over-credited:

- a UI route string such as `'/chat'` in the client's router matched the API route `/api/v3/chat`,
  because the whole-path matcher never asked for an API base in front of the tail;
- the admin console's `tests/` and the daemon's `test/` were scanned as if they were callers;
- a caller of one method on a path credited every method on it, and nothing said so.

The first two are fixed below; the third is stated in the generated document rather than guessed
at from source text (`METHOD_BLIND_NOTE`).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def reach():
    spec = importlib.util.spec_from_file_location(
        "api_reachability", BACKEND / "scripts" / "api_reachability.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _whole_path_matches(reach, path: str, line: str) -> bool:
    whole, suffix = reach.matchers(path)[0]
    assert suffix is None
    return whole.search(line) is not None


class TestAnApiBaseIsRequired:
    def test_a_ui_route_string_does_not_credit_the_api_route(self, reach) -> None:
        assert not _whole_path_matches(reach, "/api/v3/chat", "      path: '/chat',")

    def test_the_full_api_path_counts(self, reach) -> None:
        assert _whole_path_matches(reach, "/api/v3/chat", "Uri.parse('$backend/api/v3/chat')")

    @pytest.mark.parametrize("line", [
        "final url = '$_baseUrl/chat';",
        "fetch(`${base}/chat`)",
        "'${_ref.read(backendUrlProvider)}/api/v3/chat'",
    ])
    def test_a_path_built_on_an_interpolated_base_counts(self, reach, line: str) -> None:
        assert _whole_path_matches(reach, "/api/v3/chat", line)

    @pytest.mark.parametrize("line", [
        'api<{ approvals: Approval[] }>("/admin/approvals")',
        "api<{ events: AuditEvent[] }>(`/admin/audit${query}`)",
        "await api(`/admin/approvals/${row.id}/decide`, {",
    ])
    def test_the_admin_console_s_api_wrapper_is_a_base(self, reach, line: str) -> None:
        """`aura_admin/src/lib/api.ts` prepends `/api/v3` to whatever path a page passes it."""
        path = "/api/v3/admin/approvals" if "approvals" in line and "decide" not in line else (
            "/api/v3/admin/audit" if "audit" in line else "/api/v3/admin/approvals/{approval_id}/decide"
        )
        assert _whole_path_matches(reach, path, line)

    def test_a_parameterised_route_still_needs_a_base(self, reach) -> None:
        route = "/api/v3/knowledge/documents/{document_id}"
        assert _whole_path_matches(reach, route, "'$_base/knowledge/documents/$id'")
        assert not _whole_path_matches(reach, route, "path: '/knowledge/documents/:id',")


class TestACallSplitAcrossLinesStillCounts:
    def test_a_wrapper_call_whose_path_is_on_the_next_line_is_joined(self, reach, tmp_path) -> None:
        """Prettier puts a long argument on its own line:

            await api(
              `/admin/users/${encodeURIComponent(subject)}/forget`,

        The path line alone has no base; the wrapper is the line before. `sources()` carries a
        trailing `api(` onto the next line so the matcher sees one call."""
        page = tmp_path / "aura_admin" / "app" / "page.tsx"
        page.parent.mkdir(parents=True)
        page.write_text(
            "export default function Page() {\n"
            "  await api(\n"
            "    `/admin/users/${encodeURIComponent(subject)}/forget`,\n"
            "    { method: 'POST' },\n"
            "  );\n",
            encoding="utf-8",
        )
        lines = reach.scan_tree(page.parent, (".tsx",), root=tmp_path)
        texts = [text for _, _, text in lines]
        joined = [t for t in texts if "/forget" in t]
        assert joined and all("api(" in t for t in joined), texts
        whole, _ = reach.matchers("/api/v3/admin/users/{subject}/forget")[0]
        assert any(whole.search(t) for t in joined)


class TestTestsAreNotCallers:
    def test_test_directories_are_skipped(self, reach) -> None:
        skipped = reach.SKIP_PARTS
        assert {"test", "tests", "e2e", "integration_test"} <= skipped

    def test_no_scanned_source_lives_under_a_test_directory(self, reach) -> None:
        for name, lines in reach.sources().items():
            offenders = sorted({f for f, _, _ in lines if any(part in reach.SKIP_PARTS for part in f.split("/"))})
            assert offenders == [], f"{name} scanned test files: {offenders[:5]}"


class TestTheDocumentSaysWhatItCannotTell:
    def test_the_method_blind_note_is_part_of_the_rendered_header(self, reach) -> None:
        text, _counts = reach.render()
        assert reach.METHOD_BLIND_NOTE in text
        assert "method" in reach.METHOD_BLIND_NOTE.lower()
