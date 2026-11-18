"""GitHub boundary: the engineer agent acts only on allowlisted repositories.

``GitHubClient.allowed_repos`` is the deployment's defence in depth on top of the (user-scoped)
credential: even a token that can reach every repository of an organisation may only be used
against the listed ones. The check compared the ``repo`` argument -- but every other
model-supplied string (a file ``path``, a ``from_ref``, a ``ref``) was interpolated into the
request URL raw. ``..`` segments in a URL path are resolved by the HTTP client before the request
is sent, so ``path="../../../victim/secret/contents/x"`` on an allowlisted repo became a request
against ``/repos/victim/secret/...`` -- reads and, through ``commit_files``, a PUT.

api.github.com is the external boundary and is the one thing faked here, with
``httpx.MockTransport`` recording exactly what would have been sent.
"""

from __future__ import annotations

import httpx
import pytest

from aura_core.adapters.github_client import GitHubApiError, GitHubClient

ALLOWED = "acme/app"


class _Recorder:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.method, request.url.raw_path.decode()))
        body = {"object": {"sha": "a" * 40}, "sha": None, "content": "", "encoding": "none",
                "tree": [], "number": 1, "html_url": "https://github.com/acme/app/pull/1"}
        if "/git/commits" in request.url.raw_path.decode():
            # A commit object, as GitHub returns it: commit_files reads its tree's sha (WP5).
            body = {"sha": "c" * 40, "tree": {"sha": "t" * 40}}
        return httpx.Response(200, json=body)

    def paths(self) -> list[str]:
        return [path for _, path in self.requests]


@pytest.fixture
def github():
    recorder = _Recorder()
    client = GitHubClient(token="t", allowed_repos=(ALLOWED,))
    client._client = httpx.AsyncClient(
        base_url="https://api.github.com", transport=httpx.MockTransport(recorder)
    )
    return client, recorder


def _only_allowed_repo_touched(recorder: _Recorder) -> bool:
    return all(path.startswith(f"/repos/{ALLOWED}/") for path in recorder.paths())


class TestAllowlist:
    @pytest.mark.parametrize("repo", ["victim/secret", "acme/app2", "ACME/APP/../victim/x",
                                      "acme/app/../../victim/secret"])
    async def test_a_repo_outside_the_allowlist_is_refused_before_any_request(
        self, github, repo: str
    ) -> None:
        client, recorder = github
        calls = [
            lambda: client.get_file_contents(repo, "README.md"),
            lambda: client.list_files(repo),
            lambda: client.get_issue(repo, 1),
            lambda: client.get_pull_request(repo, 1),
            lambda: client.create_branch(repo, branch="b", from_ref="main"),
            lambda: client.commit_files(repo, branch="b", message="m", files={"a": "b"}),
            lambda: client.create_draft_pull_request(repo, branch="b", base="main", title="t",
                                                     body=""),
        ]
        for call in calls:
            with pytest.raises(GitHubApiError):
                await call()
        assert recorder.requests == []

    async def test_an_allowlisted_repo_is_served(self, github) -> None:
        client, recorder = github
        await client.get_file_contents(ALLOWED, "src/main.py")
        assert recorder.paths() == [f"/repos/{ALLOWED}/contents/src/main.py"]


class TestTraversalOutOfTheAllowlistedRepo:
    @pytest.mark.parametrize(
        "path",
        [
            "../../../victim/secret/contents/README.md",
            "docs/../../../../victim/secret/contents/README.md",
            "./../../../victim/secret/contents/README.md",
            "/../../../victim/secret/contents/README.md",
        ],
    )
    async def test_a_file_path_cannot_leave_the_repository(self, github, path: str) -> None:
        client, recorder = github
        with pytest.raises(GitHubApiError):
            await client.get_file_contents(ALLOWED, path)
        assert _only_allowed_repo_touched(recorder), recorder.paths()

    async def test_a_commit_cannot_write_into_another_repository(self, github) -> None:
        """The write half: a PUT against a repository that is not on the allowlist."""
        client, recorder = github
        with pytest.raises(GitHubApiError):
            await client.commit_files(
                ALLOWED, branch="main", message="m",
                files={"../../../victim/secret/contents/.github/workflows/x.yml": "evil"},
            )
        assert _only_allowed_repo_touched(recorder), recorder.requests
        assert not any(method == "PUT" for method, _ in recorder.requests)

    async def test_a_from_ref_cannot_leave_the_repository(self, github) -> None:
        client, recorder = github
        with pytest.raises(GitHubApiError):
            await client.create_branch(
                ALLOWED, branch="feature", from_ref="../../../../../victim/secret/git/ref/heads/main"
            )
        assert _only_allowed_repo_touched(recorder), recorder.paths()

    async def test_a_ref_cannot_inject_query_parameters(self, github) -> None:
        client, recorder = github
        await client.get_file_contents(ALLOWED, "README.md", ref="main&per_page=100#frag")
        ((_, path),) = recorder.requests
        assert path.startswith(f"/repos/{ALLOWED}/contents/README.md?ref=")
        assert "&per_page" not in path and "#" not in path

    async def test_an_encoded_dot_segment_stays_a_filename(self, github) -> None:
        """A literal ``%2e%2e`` is a (strange) filename, not a traversal -- it must reach GitHub
        still encoded, inside the allowlisted repository."""
        client, recorder = github
        await client.get_file_contents(ALLOWED, "%2e%2e/%2e%2e/victim")
        assert _only_allowed_repo_touched(recorder), recorder.paths()

    async def test_ordinary_nested_paths_and_branch_names_still_work(self, github) -> None:
        client, recorder = github
        await client.get_file_contents(ALLOWED, "src/pkg/module.py", ref="feature/login")
        await client.create_branch(ALLOWED, branch="fix/x", from_ref="release/1.2")
        await client.commit_files(ALLOWED, branch="fix/x", message="m",
                                  files={"src/pkg/module.py": "print(1)"})
        assert _only_allowed_repo_touched(recorder)
        assert f"/repos/{ALLOWED}/contents/src/pkg/module.py?ref=feature%2Flogin" in recorder.paths()
        assert f"/repos/{ALLOWED}/git/ref/heads/release/1.2" in recorder.paths()
        assert ("PATCH", f"/repos/{ALLOWED}/git/refs/heads/fix/x") in recorder.requests
