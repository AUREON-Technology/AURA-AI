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


def _workflow_file(name: str) -> str:
    """A workflow *file name* (``deploy.yml``), the only form we send: never an id or a path."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}\.ya?ml", name or ""):
        raise GitHubApiError(f"{name!r} is not a workflow file name such as deploy.yml")
    return quote(name, safe="")


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

    @staticmethod
    def _check(response: Any, method: str, path: str) -> None:
        if response.status_code < 400:
            return
        note = ""
        if response.status_code in (403, 429):
            # GitHub answers a rate limit with 403 or 429 and a body that says so only in prose;
            # the headers say when it lifts, which is what a caller deciding whether to wait needs.
            retry = response.headers.get("retry-after")
            reset = response.headers.get("x-ratelimit-reset")
            if response.headers.get("x-ratelimit-remaining") == "0" or retry:
                when = f"retry after {retry}s" if retry else f"limit resets at epoch {reset}"
                note = f" (rate limited; {when})"
        raise GitHubApiError(
            f"GitHub API {method} {path} failed: HTTP {response.status_code}{note} "
            f"{response.text[:300]}"
        )

    async def _request(self, method: str, path: str, *, json: Mapping[str, Any] | None = None) -> Any:
        client = await self._get_client()
        response = await client.request(method, path, json=json)
        self._check(response, method, path)
        return response.json() if response.content else {}

    async def _request_raw(self, path: str) -> bytes:
        """A file's exact bytes. The JSON contents API returns ``content: ""`` for anything over
        1 MB; the ``raw`` media type is how GitHub serves those (up to 100 MB)."""
        client = await self._get_client()
        response = await client.request(
            "GET", path, headers={"Accept": "application/vnd.github.raw+json"}
        )
        self._check(response, "GET", path)
        return bytes(response.content)

    # -- read ----------------------------------------------------------------------

    async def get_file_bytes(self, repo: str, path: str, *, ref: str | None = None) -> bytes:
        """The file's exact bytes -- what a PDF or any other binary needs, and what makes a file
        over 1 MB readable at all (the JSON response carries no content for those)."""
        self._assert_repo_allowed(repo)
        params = f"?ref={_query_value(ref)}" if ref else ""
        safe_path = _path_segments(path, what="path")
        url = f"/repos/{repo}/contents/{safe_path}{params}"
        data = await self._request("GET", url)
        if isinstance(data, list):
            return "\n".join(entry.get("name", "") for entry in data).encode("utf-8")
        kind = data.get("type")
        if kind == "symlink":
            return f"(symlink to {data.get('target')})".encode("utf-8")
        if kind == "submodule":
            return f"(submodule: {data.get('submodule_git_url')})".encode("utf-8")
        if data.get("encoding") == "base64":
            return base64.b64decode(data.get("content") or "")
        if int(data.get("size") or 0) > 0 and not data.get("content"):
            return await self._request_raw(url)
        return str(data.get("content", "")).encode("utf-8")

    async def get_file_contents(self, repo: str, path: str, *, ref: str | None = None) -> str:
        return (await self.get_file_bytes(repo, path, ref=ref)).decode("utf-8", errors="replace")

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

    async def list_workflow_runs(self, repo: str, workflow: str, *, limit: int = 10) -> list[dict[str, Any]]:
        """Recent runs of one workflow file (newest first): enough to say what ran, on which commit,
        and how it ended."""
        self._assert_repo_allowed(repo)
        data = await self._request(
            "GET", f"/repos/{repo}/actions/workflows/{_workflow_file(workflow)}/runs?per_page={max(1, min(limit, 30))}")
        return [
            {"id": r.get("id"), "status": r.get("status"), "conclusion": r.get("conclusion"),
             "branch": r.get("head_branch"), "sha": r.get("head_sha"), "event": r.get("event"),
             "created_at": r.get("created_at"), "url": r.get("html_url")}
            for r in data.get("workflow_runs", [])
        ]

    async def dispatch_workflow(
        self, repo: str, workflow: str, *, ref: str, inputs: Mapping[str, str] | None = None
    ) -> str:
        """``workflow_dispatch`` on one workflow file. GitHub answers 204 with no run id; the run
        shows up in :meth:`list_workflow_runs` a moment later."""
        self._assert_repo_allowed(repo)
        body: dict[str, Any] = {"ref": ref}
        if inputs:
            body["inputs"] = {str(k): str(v) for k, v in inputs.items()}
        await self._request(
            "POST", f"/repos/{repo}/actions/workflows/{_workflow_file(workflow)}/dispatches", json=body)
        return f"Dispatched {workflow} on {ref}"

    async def commit_is_ancestor(self, repo: str, sha: str, ref: str) -> bool:
        """``sha`` is in ``ref``'s history when comparing sha...ref says ref is ahead of it or identical.
        GitHub resolves a fork's commit by SHA inside the parent repository, so reachability from the ref
        is the check that matters, not that the SHA exists."""
        self._assert_repo_allowed(repo)
        if not re.fullmatch(r"[0-9a-fA-F]{7,40}", sha or ""):
            raise GitHubApiError("sha must be 7-40 hexadecimal characters")
        data = await self._request("GET", f"/repos/{repo}/compare/{sha}...{quote(ref, safe='')}")
        return data.get("status") in ("ahead", "identical")

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
