"""``GitHubClient.commit_files`` makes one commit, fast-forward only (module readiness WP5, FR-07).

It used to PUT each file through the contents API, which makes one commit *per file*: a change to
five files became five commits, and a failure halfway left a branch with half a change on it.
Now it builds one tree and one commit through the Git Data API and moves the branch to it without
``force`` -- so a branch that moved while the agent worked is refused rather than overwritten.

api.github.com is the one thing faked, with ``httpx.MockTransport`` answering like GitHub.
"""

from __future__ import annotations

import json

import httpx
import pytest

from aura_core.adapters.github_client import GitHubApiError, GitHubClient

REPO = "acme/app"
PARENT = "p" * 40
BASE_TREE = "t" * 40
NEW_TREE = "n" * 40
NEW_COMMIT = "c" * 40


class _GitHub:
    """Answers the Git Data API calls ``commit_files`` makes; records each with its body."""

    def __init__(self, *, existing: dict[str, str] | None = None, ref_update_status: int = 200,
                 truncated: bool = False) -> None:
        self.calls: list[tuple[str, str, dict]] = []
        self._existing = existing or {"README.md": "100644", "bin/run.sh": "100755"}
        self._ref_update_status = ref_update_status
        self._truncated = truncated

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.raw_path.decode()
        body = json.loads(request.content) if request.content else {}
        self.calls.append((request.method, path, body))
        base = f"/repos/{REPO}"
        if request.method == "GET" and path == base:
            return httpx.Response(200, json={"default_branch": "main"})
        if request.method == "GET" and path == f"{base}/git/ref/heads/feature/x":
            return httpx.Response(200, json={"object": {"sha": PARENT}})
        if request.method == "GET" and path == f"{base}/git/commits/{PARENT}":
            return httpx.Response(200, json={"tree": {"sha": BASE_TREE}})
        if request.method == "GET" and path == f"{base}/git/trees/{BASE_TREE}?recursive=1":
            tree = [{"path": p, "mode": m, "type": "blob"} for p, m in self._existing.items()]
            return httpx.Response(200, json={"tree": tree, "truncated": self._truncated})
        if request.method == "POST" and path == f"{base}/git/trees":
            return httpx.Response(201, json={"sha": NEW_TREE})
        if request.method == "POST" and path == f"{base}/git/commits":
            return httpx.Response(201, json={"sha": NEW_COMMIT})
        if request.method == "PATCH" and path == f"{base}/git/refs/heads/feature/x":
            if self._ref_update_status != 200:
                return httpx.Response(self._ref_update_status, json={"message": "Update is not a fast forward"})
            return httpx.Response(200, json={"object": {"sha": NEW_COMMIT}})
        return httpx.Response(404, json={"message": f"unexpected {request.method} {path}"})

    def methods(self) -> list[str]:
        return [method for method, _, _ in self.calls]

    def body_of(self, method: str, suffix: str) -> dict:
        return next(body for m, p, body in self.calls if m == method and p.endswith(suffix))


def _client(api: _GitHub) -> GitHubClient:
    client = GitHubClient(token="t", allowed_repos=(REPO,))
    client._client = httpx.AsyncClient(
        base_url="https://api.github.com", transport=httpx.MockTransport(api)
    )
    return client


class TestOneCommit:
    async def test_several_files_become_one_commit_on_the_branch_head(self) -> None:
        api = _GitHub()
        result = await _client(api).commit_files(
            REPO, branch="feature/x", message="Fix login",
            files={"README.md": "new readme", "src/login.py": "print('hi')\n"},
        )

        assert "PUT" not in api.methods(), "the per-file contents API is gone"
        assert api.methods().count("POST") == 2  # one tree, one commit
        tree = api.body_of("POST", "/git/trees")
        assert tree["base_tree"] == BASE_TREE
        assert {e["path"] for e in tree["tree"]} == {"README.md", "src/login.py"}
        commit = api.body_of("POST", "/git/commits")
        assert commit == {"message": "Fix login", "tree": NEW_TREE, "parents": [PARENT]}
        assert NEW_COMMIT[:7] in result and "2 file(s)" in result

    async def test_the_branch_moves_fast_forward_only(self) -> None:
        api = _GitHub()
        await _client(api).commit_files(REPO, branch="feature/x", message="m", files={"a": "b"})
        assert api.body_of("PATCH", "/git/refs/heads/feature/x") == {"sha": NEW_COMMIT, "force": False}

    async def test_a_branch_that_moved_meanwhile_is_refused_not_overwritten(self) -> None:
        api = _GitHub(ref_update_status=422)
        with pytest.raises(GitHubApiError, match="422"):
            await _client(api).commit_files(REPO, branch="feature/x", message="m", files={"a": "b"})
        assert sum(1 for m in api.methods() if m == "PATCH") == 1, "never retried, never forced"

    async def test_an_executable_file_stays_executable(self) -> None:
        api = _GitHub()
        await _client(api).commit_files(
            REPO, branch="feature/x", message="m",
            files={"bin/run.sh": "#!/bin/sh\necho hi\n", "new.txt": "x"},
        )
        modes = {e["path"]: e["mode"] for e in api.body_of("POST", "/git/trees")["tree"]}
        assert modes == {"bin/run.sh": "100755", "new.txt": "100644"}

    async def test_content_is_sent_as_utf8_text_blobs(self) -> None:
        api = _GitHub()
        await _client(api).commit_files(REPO, branch="feature/x", message="m", files={"a.md": "é"})
        [entry] = api.body_of("POST", "/git/trees")["tree"]
        assert entry == {"path": "a.md", "mode": "100644", "type": "blob", "content": "é"}

    async def test_a_bad_path_is_refused_before_any_request(self) -> None:
        api = _GitHub()
        with pytest.raises(GitHubApiError):
            await _client(api).commit_files(
                REPO, branch="feature/x", message="m", files={"ok.txt": "a", "../../x": "b"},
            )
        assert api.calls == []


class TestDefaultBranch:
    async def test_it_is_read_from_the_repository(self) -> None:
        api = _GitHub()
        assert await _client(api).default_branch(REPO) == "main"
        assert api.calls == [("GET", f"/repos/{REPO}", {})]

    async def test_it_honours_the_allowlist(self) -> None:
        api = _GitHub()
        with pytest.raises(GitHubApiError):
            await _client(api).default_branch("victim/secret")
        assert api.calls == []
