"""GitHub REST API adapter for the engineer agent's code-hosting tools.

Talks to ``api.github.com`` directly over ``httpx`` (already a real, non-dev dependency) rather
than adding PyGithub -- the surface this needs (six calls) does not justify a second HTTP client
library. ``httpx`` is imported lazily inside :meth:`_client`, matching every other adapter's
"construction never makes a network call" rule (``PostgresPool._get_pool()`` is the canonical
example, ``container.py``).

Every write here (``create_branch``, ``commit_files``, ``create_draft_pull_request``) is reached
only through a ``side_effecting=True, sensitivity=CONFIDENTIAL`` tool (``tools/coding.py``), which
``ToolSpec.__post_init__`` already forces into ``requires_confirmation`` -- this adapter has no
opinion about confirmation and does not need one.
"""

from __future__ import annotations

import base64
import re
from typing import Any, Mapping
from urllib.parse import quote

from ..ports.coding import RepoFile, RepoTree

__all__ = ["GitHubClient", "GitHubApiError"]


class GitHubApiError(RuntimeError):
    """Raised on any non-2xx response; carries enough detail for a tool to explain the failure."""


_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def _path_segments(value: str, *, what: str) -> str:
    """A model-supplied path (file path, branch name) as URL path segments that cannot leave
    the repository the request is scoped to.

    Interpolated raw, ``..`` segments were resolved by the HTTP client before sending, so a file
    path on an allowlisted repository could address ``/repos/<any>/<repo>/...`` -- a complete
    bypass of ``allowed_repos``, writes included (tests/security/test_github_boundary.py). Dot
    segments are refused rather than normalised: nothing legitimate in a repository path needs
    them. Every segment is then percent-encoded, so ``?``, ``#`` and an already-encoded
    ``%2e%2e`` stay part of a filename.
    """
    segments = value.lstrip("/").split("/")
    if any(segment in ("", ".", "..") for segment in segments):
        raise GitHubApiError(f"{what} {value!r} is not a plain repository path")
    return "/".join(quote(segment, safe="") for segment in segments)


def _query_value(value: str) -> str:
    return quote(value, safe="")


class GitHubClient:
    def __init__(
        self,
        *,
        token: str,
        allowed_repos: tuple[str, ...] = (),
        base_url: str = "https://api.github.com",
        timeout_s: float = 20.0,
    ) -> None:
        self._token = token
        self._allowed_repos = frozenset(allowed_repos)
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s
        self._client: Any = None

    def _assert_repo_allowed(self, repo: str) -> None:
        owner, _, name = repo.partition("/")
        if not _REPO.match(repo) or owner in (".", "..") or name in (".", ".."):
            raise GitHubApiError(f"repo {repo!r} is not an owner/name repository reference")
        if self._allowed_repos and repo not in self._allowed_repos:
            raise GitHubApiError(
                f"repo {repo!r} is not in the configured allowlist "
                f"({sorted(self._allowed_repos)}); refusing to act against it"
            )

    async def _get_client(self) -> Any:
        if self._client is None:
            import httpx

            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout_s,
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": "AURA-engineer-agent",
                },
            )
        return self._client

    async def _request(self, method: str, path: str, *, json: Mapping[str, Any] | None = None) -> Any:
        client = await self._get_client()
        response = await client.request(method, path, json=json)
        if response.status_code >= 400:
            raise GitHubApiError(
                f"GitHub API {method} {path} failed: HTTP {response.status_code} "
                f"{response.text[:300]}"
            )
        return response.json() if response.content else {}

    # -- read ----------------------------------------------------------------------

    async def get_file_contents(self, repo: str, path: str, *, ref: str | None = None) -> str:
        self._assert_repo_allowed(repo)
        params = f"?ref={_query_value(ref)}" if ref else ""
        safe_path = _path_segments(path, what="path")
        data = await self._request("GET", f"/repos/{repo}/contents/{safe_path}{params}")
        if isinstance(data, list):
            return "\n".join(entry.get("name", "") for entry in data)
        if data.get("encoding") == "base64":
            return base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        return str(data.get("content", ""))

    async def list_files(self, repo: str, *, ref: str | None = None) -> RepoTree:
        """The repository's whole file list at ``ref``, in one call.

        Uses the git trees API with ``recursive=1`` rather than walking ``/contents`` directory
        by directory: one request for a repository of any depth instead of one per directory,
        which is the difference between "analyse this repo" being a single tool call and being
        dozens. ``HEAD`` stands in for the default branch, so no extra call is needed to discover
        its name. GitHub caps a recursive tree response and reports the cap in ``truncated``;
        that flag is passed through rather than swallowed (see :class:`RepoTree`).
        """
        self._assert_repo_allowed(repo)
        tree_ref = quote(ref or "HEAD", safe="")
        data = await self._request("GET", f"/repos/{repo}/git/trees/{tree_ref}?recursive=1")
        files = tuple(
            RepoFile(path=str(entry.get("path", "")), size=int(entry.get("size") or 0))
            for entry in data.get("tree", [])
            if entry.get("type") == "blob" and entry.get("path")
        )
        return RepoTree(files=files, truncated=bool(data.get("truncated")))

    async def get_issue(self, repo: str, number: int) -> str:
        self._assert_repo_allowed(repo)
        data = await self._request("GET", f"/repos/{repo}/issues/{number}")
        return (
            f"#{data.get('number')} {data.get('title')} ({data.get('state')})\n"
            f"{data.get('body') or '(no description)'}"
        )

    async def get_pull_request(self, repo: str, number: int) -> str:
        self._assert_repo_allowed(repo)
        data = await self._request("GET", f"/repos/{repo}/pulls/{number}")
        return (
            f"#{data.get('number')} {data.get('title')} ({data.get('state')}) "
            f"{data.get('head', {}).get('ref')} -> {data.get('base', {}).get('ref')}\n"
            f"{data.get('body') or '(no description)'}"
        )

    # -- write ---------------------------------------------------------------------

    async def create_branch(self, repo: str, *, branch: str, from_ref: str) -> str:
        self._assert_repo_allowed(repo)
        from_path = _path_segments(from_ref, what="from_ref")
        ref_data = await self._request("GET", f"/repos/{repo}/git/ref/heads/{from_path}")
        sha = ref_data["object"]["sha"]
        await self._request(
            "POST", f"/repos/{repo}/git/refs",
            json={"ref": f"refs/heads/{branch}", "sha": sha},
        )
        return f"Created branch {branch!r} on {repo} from {from_ref!r} ({sha[:7]})"

    async def default_branch(self, repo: str) -> str:
        """The repository's own default branch, as GitHub reports it (WP5, D-07)."""
        self._assert_repo_allowed(repo)
        data = await self._request("GET", f"/repos/{repo}")
        branch = data.get("default_branch") if isinstance(data, Mapping) else None
        if not isinstance(branch, str) or not branch:
            raise GitHubApiError(f"GitHub did not report a default branch for {repo}")
        return branch

    async def commit_files(
        self, repo: str, *, branch: str, message: str, files: Mapping[str, str]
    ) -> str:
        """One commit touching every file in ``files``, fast-forwarded onto ``branch``.

        Built through the Git Data API (module readiness WP5, FR-07): one tree on top of the
        branch head's tree, one commit whose parent is that head, then a ref update with
        ``force: false``. The per-file contents API this replaced made one commit per file, and
        a failure halfway left half a change on the branch. Now nothing is visible until the
        last request, and a branch that moved meanwhile is refused (422), never overwritten.
        """
        self._assert_repo_allowed(repo)
        # Every path is validated before the first request, so one bad path cannot leave a
        # partial change behind. The tree takes the plain repository path; only URLs take the
        # percent-encoded form.
        for path in files:
            _path_segments(path, what="path")
        tree_paths = {path: path.lstrip("/") for path in files}
        branch_path = _path_segments(branch, what="branch")

        head = await self._request("GET", f"/repos/{repo}/git/ref/heads/{branch_path}")
        parent_sha = head["object"]["sha"]
        parent = await self._request("GET", f"/repos/{repo}/git/commits/{parent_sha}")
        base_tree = parent["tree"]["sha"]
        modes = await self._file_modes(repo, base_tree)

        entries = [
            {
                "path": tree_paths[path],
                # An existing file keeps its mode, so an executable script stays executable;
                # anything new (or unknown in a truncated tree) is an ordinary file.
                "mode": modes.get(tree_paths[path], "100644"),
                "type": "blob",
                "content": content,
            }
            for path, content in files.items()
        ]
        tree = await self._request(
            "POST", f"/repos/{repo}/git/trees", json={"base_tree": base_tree, "tree": entries}
        )
        commit = await self._request(
            "POST", f"/repos/{repo}/git/commits",
            json={"message": message, "tree": tree["sha"], "parents": [parent_sha]},
        )
        await self._request(
            "PATCH", f"/repos/{repo}/git/refs/heads/{branch_path}",
            json={"sha": commit["sha"], "force": False},
        )
        return (
            f"Committed {len(entries)} file(s) to {repo}@{branch} as {commit['sha'][:7]}: "
            f"{', '.join(files)}"
        )

    async def _file_modes(self, repo: str, tree_sha: str) -> dict[str, str]:
        """Path -> git file mode for every blob in ``tree_sha`` (empty when unknowable)."""
        data = await self._request("GET", f"/repos/{repo}/git/trees/{tree_sha}?recursive=1")
        tree = data.get("tree") if isinstance(data, Mapping) else None
        if not isinstance(tree, list):
            return {}
        return {
            entry["path"]: entry["mode"]
            for entry in tree
            if isinstance(entry, Mapping) and entry.get("type") == "blob"
            and isinstance(entry.get("path"), str) and isinstance(entry.get("mode"), str)
        }

    async def create_draft_pull_request(
        self, repo: str, *, branch: str, base: str, title: str, body: str
    ) -> str:
        self._assert_repo_allowed(repo)
        data = await self._request(
            "POST", f"/repos/{repo}/pulls",
            json={"title": title, "head": branch, "base": base, "body": body, "draft": True},
        )
        return f"Opened draft PR #{data.get('number')}: {data.get('html_url')}"

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
