"""Load-test proof that GitHubClient performs real branch/commit/PR operations against a real
GitHub repository, not InMemoryGitHubClient's in-process dict.

Requires an operator-owned, already-existing throwaway repo the token can write to -- this suite
never creates or deletes repositories. Every run creates a uniquely-suffixed branch, commits one
file, and opens a draft PR, then deletes that branch in teardown; deleting a PR's head branch
also closes it (documented GitHub behaviour), so no separate PR-close call is needed. Cleanup is
a raw REST call made directly here, not a new `GitHubClient` method -- the engineer agent's own
write surface intentionally has no delete capability, live-test hygiene or not.

    export AURA_GITHUB_TOKEN=<PAT with repo scope on the scratch repo>
    export AURA_GITHUB_TEST_REPO=<owner>/<scratch-repo>   # must already exist, operator-owned
    export AURA_GITHUB_TEST_BASE_REF=main                 # optional, defaults to main
    pytest tests/load/test_github_live.py -m load -s
"""

from __future__ import annotations

import os
import uuid

import pytest

from aura_core.adapters.github_client import GitHubApiError, GitHubClient

pytestmark = pytest.mark.load

_REQUIRED_ENV = ("AURA_GITHUB_TOKEN", "AURA_GITHUB_TEST_REPO")


def _missing_env() -> list[str]:
    return [key for key in _REQUIRED_ENV if not os.environ.get(key)]


async def _cleanup_branches(token: str, repo: str, branches: list[str]) -> None:
    if not branches:
        return
    import httpx

    async with httpx.AsyncClient(
        base_url="https://api.github.com", timeout=20.0,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
    ) as client:
        for branch in branches:
            try:
                await client.delete(f"/repos/{repo}/git/refs/heads/{branch}")
            except Exception as exc:
                print(
                    f"WARNING: failed to delete branch {branch!r} on {repo}: {exc} -- "
                    "delete it manually"
                )


def _unique_branch() -> str:
    return f"aura-load-test-{uuid.uuid4().hex[:12]}"


@pytest.fixture
async def github_repo():
    missing = _missing_env()
    if missing:
        pytest.skip(f"github live test not configured, missing: {', '.join(missing)}")

    token = os.environ["AURA_GITHUB_TOKEN"]
    repo = os.environ["AURA_GITHUB_TEST_REPO"]
    base_ref = os.environ.get("AURA_GITHUB_TEST_BASE_REF", "main")
    client = GitHubClient(token=token, allowed_repos=(repo,))
    created_branches: list[str] = []
    try:
        yield client, repo, base_ref, created_branches
    finally:
        await _cleanup_branches(token, repo, created_branches)
        await client.close()


class TestBranchCommitPullRequestLifecycle:
    async def test_create_branch_commit_file_open_draft_pr(self, github_repo) -> None:
        client, repo, base_ref, created_branches = github_repo
        branch = _unique_branch()
        created_branches.append(branch)

        create_result = await client.create_branch(repo, branch=branch, from_ref=base_ref)
        assert branch in create_result

        marker = uuid.uuid4().hex
        commit_result = await client.commit_files(
            repo, branch=branch, message="aura load test: scratch commit (auto-cleaned)",
            files={"aura-load-test.txt": f"load test marker {marker}\n"},
        )
        assert "aura-load-test.txt" in commit_result

        read_back = await client.get_file_contents(repo, "aura-load-test.txt", ref=branch)
        assert marker in read_back

        pr_result = await client.create_draft_pull_request(
            repo, branch=branch, base=base_ref,
            title="AURA load test (auto-cleaned by tests/load/test_github_live.py)",
            body="Closed automatically when its branch is deleted in test teardown.",
        )
        assert "draft PR" in pr_result

    async def test_allowed_repos_rejects_a_repo_outside_the_allowlist(self, github_repo) -> None:
        client, _repo, _base_ref, _created = github_repo
        with pytest.raises(GitHubApiError, match="not in the configured allowlist"):
            await client.get_issue("octocat/Hello-World", 1)
