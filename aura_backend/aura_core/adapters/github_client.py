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
from typing import Any, Mapping

__all__ = ["GitHubClient", "GitHubApiError"]


class GitHubApiError(RuntimeError):
    """Raised on any non-2xx response; carries enough detail for a tool to explain the failure."""


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
        params = f"?ref={ref}" if ref else ""
        data = await self._request("GET", f"/repos/{repo}/contents/{path}{params}")
        if isinstance(data, list):
            return "\n".join(entry.get("name", "") for entry in data)
        if data.get("encoding") == "base64":
            return base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        return str(data.get("content", ""))

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
        ref_data = await self._request("GET", f"/repos/{repo}/git/ref/heads/{from_ref}")
        sha = ref_data["object"]["sha"]
        await self._request(
            "POST", f"/repos/{repo}/git/refs",
            json={"ref": f"refs/heads/{branch}", "sha": sha},
        )
        return f"Created branch {branch!r} on {repo} from {from_ref!r} ({sha[:7]})"

    async def commit_files(
        self, repo: str, *, branch: str, message: str, files: Mapping[str, str]
    ) -> str:
        """One commit touching one or more files, via the low-level contents API per file.

        Simpler and safer than building a tree/commit graph by hand for a first cut: each file is
        an independent PUT against ``contents/{path}``, so a partial failure leaves earlier files
        committed rather than corrupting a single hand-built commit.
        """
        self._assert_repo_allowed(repo)
        committed = []
        for path, content in files.items():
            existing_sha = None
            try:
                existing = await self._request(
                    "GET", f"/repos/{repo}/contents/{path}?ref={branch}"
                )
                existing_sha = existing.get("sha") if isinstance(existing, Mapping) else None
            except GitHubApiError:
                pass  # file does not exist yet on this branch
            payload: dict[str, Any] = {
                "message": message,
                "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
                "branch": branch,
            }
            if existing_sha:
                payload["sha"] = existing_sha
            await self._request("PUT", f"/repos/{repo}/contents/{path}", json=payload)
            committed.append(path)
        return f"Committed {len(committed)} file(s) to {repo}@{branch}: {', '.join(committed)}"

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
