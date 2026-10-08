"""``RepositoryIdentity``: a validated ``owner/name`` GitHub repository slug.

Extracted from two ad hoc, drifting implementations in ``tools/coding.py``
(``_normalize_repo`` and ``build_github_tools``'s inner ``_repo()``) so there is exactly one
place that decides whether a model-supplied string actually names a repository, used both by
that module's tool bodies and by the pre-dispatch schema check in ``tools/schema.py``
(``ParameterSpec(format="github_repo")``).

This is the fix for the class of bug where an LLM invents a plausible-looking but wrong
identifier (e.g. ``repo_for_README`` for a local file the model was actually asked to
summarise) and that string reaches a real network call before anything notices it is not a
repository at all -- see ``docs/AURA_UPGRADE_ROADMAP.md``'s engineer-orchestration hardening
notes. Rejecting it here is cheap (no I/O) and happens before a tool is ever dispatched.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["RepositoryIdentity"]

_URL_PREFIXES = ("https://", "http://", "ssh://", "git@", "www.")


@dataclass(frozen=True, slots=True)
class RepositoryIdentity:
    """A parsed ``owner/name`` repository identifier."""

    owner: str
    name: str

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.name}"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.slug

    @staticmethod
    def _segments(value: str) -> list[str]:
        """Best-effort ``[owner, name, ...]`` split from whatever shape a user pasted:
        ``owner/name``, a GitHub URL with or without ``.git``, ``/tree/<ref>/<path>``, a
        trailing slash, or an ``ssh://``/``git@`` remote. Never raises -- an unparsable string
        just yields fewer than two segments, which both call sites below treat as "invalid".
        """
        repo = value.strip()
        for prefix in _URL_PREFIXES:
            if repo.startswith(prefix):
                repo = repo[len(prefix):]
        repo = repo.replace("github.com:", "github.com/")
        if repo.startswith("github.com/"):
            repo = repo[len("github.com/"):]
        repo = repo.strip("/")
        if repo.endswith(".git"):
            repo = repo[: -len(".git")]
        return [p for p in repo.split("/") if p]

    @classmethod
    def normalize_slug(cls, value: str) -> str:
        """Best-effort ``owner/name`` (or a shorter, still-invalid fragment) from ``value``.

        Preserves the permissive behaviour the original ``_normalize_repo`` had: this can
        return a string that is *not* a valid two-part slug (e.g. a bare word with no ``/``) --
        callers that need a hard guarantee should use :meth:`parse_or_none` instead, which
        returns ``None`` rather than a half-formed slug.
        """
        return "/".join(cls._segments(value)[:2])

    @classmethod
    def parse_or_none(cls, value: str | None) -> "RepositoryIdentity | None":
        """Strict parse: only a real ``owner/name`` shape (plain slug or GitHub URL) succeeds.

        Returns ``None`` for anything else, including a bare word with no owner segment at all
        (e.g. ``"repo_for_README"``) -- the exact shape an LLM invents when it is asked about a
        local file and fabricates a GitHub repository identifier instead.
        """
        if not value or not isinstance(value, str):
            return None
        segments = cls._segments(value)
        if len(segments) < 2:
            return None
        owner, name = segments[0], segments[1]
        if not owner or not name:
            return None
        if any(ch.isspace() for ch in owner) or any(ch.isspace() for ch in name):
            return None
        return cls(owner=owner, name=name)
