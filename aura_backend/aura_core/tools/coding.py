"""Engineer agent tools: sandboxed filesystem/command access, git, GitHub, and bridged MCP tools.

Everything here is reached only through the same planning -> execution -> ``ToolRegistry``/
``ToolPolicy`` path every other tool uses (ADR-006) -- nothing in this module dispatches a tool
itself, and nothing bypasses ``ToolPolicy.evaluate``'s capability, taint, and confirmation rules.

Two things are worth stating explicitly because they look, at a glance, like they contradict
existing decisions elsewhere in ``aura_core/tools/``:

* :func:`build_sandbox_tools`'s ``write_file``/``edit_file`` operate on a *separate, per-task
  workspace root* -- provisioned (and, for the server-side sandbox, torn down) by whatever
  :class:`~aura_core.ports.coding.SandboxRunner` is wired in -- never the read-only workspace
  ``tools/builtin.py::build_file_tools`` guards. That function's docstring says writing is
  "deliberately not offered" for *that* root; this module does not reverse that decision, because
  it is a different root entirely. Since ADR-011 that root is not always ephemeral or
  container-isolated: ``container.py`` wires a :class:`~aura_core.adapters.host_agent.
  CompositeSandboxRunner`, which routes each task to the caller's own paired desktop app
  (``aura_hostd``) when one is connected, and only falls back to the ephemeral Docker sandbox
  otherwise -- decided per task, not fixed per deployment. The tool descriptions below say so,
  rather than unconditionally claiming "not the user's real files": that claim is only true for
  the fallback path. See ``docs/architecture/011-desktop-engineer-agent.md``.
* Every tool below declares ``required_capability="engineer_agent"``. A caller without that grant
  never sees these tools at all (``ToolRegistry.available()`` filters them out before they reach
  a model's tool list), regardless of what sensitivity ceiling the caller otherwise has.
* :func:`build_sandbox_tools`'s ``clone_repository`` does not shell out to ``git clone``: it
  copies files through the code-host port into the workspace. That keeps network egress and a
  forge credential out of a sandbox container that runs model-chosen shell commands, and it is
  why "analyse this repository" works on a deployment whose Docker sandbox has no network at all
  (a paired desktop app's own network is a separate matter -- see the point above).
"""

from __future__ import annotations

import asyncio
import contextlib
import difflib
import hashlib
import posixpath
import re
import unicodedata
import shlex
import time
import weakref
from collections import OrderedDict
from typing import Any, Mapping, Sequence

from pathlib import Path

from ..domain.errors import ToolExecutionError, ValidationError
from ..domain.output_schema import check_output_schema
from ..domain.repository_identity import RepositoryIdentity
from ..domain.tools import NETWORK_TAG, ParameterSpec, ToolCategory, ToolSensitivity, ToolSpec
from ..observability.logging import get_logger
from ..ports.coding import (
    TOOL_CALL_CONFIRMED,
    FileWriteOp,
    McpToolDescriptor,
    SandboxResult,
    StreamingSandboxRunner,
)
from ..ports.runtime import Tool, ToolContext
from .base import param, tool
from .desktop_ops import build_desktop_ops_tools
from .devtools import build_devtools
from .open._input import MAX_PDF_BYTES, SourceReadError, read_source_bytes

__all__ = [
    "build_sandbox_tools",
    "build_github_tools",
    "build_mcp_bridge_tools",
    "REPOSITORY_TOOL_NAMES",
    "matching_repository_tool",
]

logger = get_logger("tools.coding")

_ENGINEER_CAPABILITY = "engineer_agent"

#: Caps for `clone_repository`. A repository is arbitrary third-party data of unbounded size, and
#: the workspace may be a scratch directory inside a memory-limited container -- copying a
#: multi-gigabyte monorepo into it would fail the turn slowly instead of quickly. (A paired
#: desktop app's disk is not memory-limited the same way, but these caps apply uniformly rather
#: than varying by which surface a given task happened to route to.) These bound the work and are
#: reported in the tool's result, so the agent knows the snapshot is partial rather than
#: concluding a skipped file does not exist.
_CLONE_MAX_FILES = 400
_CLONE_MAX_FILE_BYTES = 256_000
_CLONE_MAX_TOTAL_BYTES = 8_000_000
_CLONE_CONCURRENCY = 6

#: Workspace snapshots (ADR-041): refs under refs/aura/snapshots/, one commit object each.
_SNAPSHOT_REF_PREFIX = "refs/aura/snapshots/"
#: ``aura-snapshot:<nanosecond clock>: <label>``. Git's own dates have one-second resolution, so
#: two snapshots taken in the same second could not be told apart by age; the clock in the subject
#: is what "newest first" and "keep the newest 20" order by.
_SNAPSHOT_SUBJECT = re.compile(r"aura-snapshot:(\d+): (.*)", re.DOTALL)
_SNAPSHOT_ID = re.compile(r"snap-[0-9a-f]{12}")
_SNAPSHOT_LABEL_MAX = 100
#: How many snapshots a workspace keeps; the oldest beyond this are unreferenced (git collects
#: them later). Bounds the disk a model that snapshots in a loop can use.
_SNAPSHOT_KEEP = 20

# Shared head of every snapshot script: this must be a git repository and the workspace must BE
# its root. Restoring rewrites the index and the working tree of the whole repository, so a
# workspace that is a subdirectory of a bigger one (a folder granted on a desktop) would have
# files outside it rewritten -- refused (exit 4) rather than guarded.
_SNAPSHOT_PRELUDE = (
    'git rev-parse --git-dir >/dev/null 2>&1 || exit 3\n'
    '[ -z "$(git rev-parse --show-cdup)" ] || exit 4\n'
)

#: ``$1`` label, ``$2`` the nanosecond clock for the subject. Prints the new snapshot id.
#: The tree is built in a private index file: ``git add -A`` there stages everything git would
#: not ignore, ``write-tree`` makes the tree, ``commit-tree`` a parent-linked commit, and
#: ``update-ref`` names it. The real index and the working tree are never read for writing.
_SNAPSHOT_SCRIPT = (
    "set -e\n" + _SNAPSHOT_PRELUDE +
    'gitdir=$(git rev-parse --absolute-git-dir)\n'
    'idx="$gitdir/aura-snapshot-index.$$"\n'
    'trap \'rm -f "$idx"\' EXIT\n'
    'GIT_INDEX_FILE="$idx" git add -A >/dev/null\n'
    'tree=$(GIT_INDEX_FILE="$idx" git write-tree)\n'
    'if git rev-parse -q --verify HEAD >/dev/null; then parent=$(git rev-parse HEAD); '
    'commit=$(GIT_AUTHOR_NAME="AURA Engineer Agent" GIT_AUTHOR_EMAIL=engineer-agent@aura.local '
    'GIT_COMMITTER_NAME="AURA Engineer Agent" GIT_COMMITTER_EMAIL=engineer-agent@aura.local '
    'git commit-tree "$tree" -p "$parent" -m "aura-snapshot:$2: $1"); '
    'else commit=$(GIT_AUTHOR_NAME="AURA Engineer Agent" GIT_AUTHOR_EMAIL=engineer-agent@aura.local '
    'GIT_COMMITTER_NAME="AURA Engineer Agent" GIT_COMMITTER_EMAIL=engineer-agent@aura.local '
    'git commit-tree "$tree" -m "aura-snapshot:$2: $1"); fi\n'
    'id="snap-$(printf %s "$commit" | cut -c1-12)"\n'
    'git update-ref "refs/aura/snapshots/$id" "$commit"\n'
    'printf \'%s\\n\' "$id"\n'
)

_SNAPSHOT_LIST_SCRIPT = (
    "set -e\n" + _SNAPSHOT_PRELUDE +
    "git for-each-ref --format='%(refname)%09%(committerdate:iso-strict)%09%(subject)' refs/aura/snapshots\n"
)

#: ``$@`` snapshot ids to forget (the objects are collected by git's own gc later).
_SNAPSHOT_FORGET_SCRIPT = (
    "set -e\n" + _SNAPSHOT_PRELUDE +
    'for id in "$@"; do git update-ref -d "refs/aura/snapshots/$id"; done\n'
)

#: ``$1`` snapshot id. Untracked, un-ignored files go first (anything the snapshot had comes back
#: with it); then index and working tree are made equal to the snapshot's tree -- tracked files
#: not in it are removed, changed ones rewritten -- and finally the index is put back to HEAD so
#: the restored differences read as ordinary uncommitted changes. HEAD never moves. Ignored files
#: (``node_modules``, build output) are left alone: ``clean`` runs without ``-x``.
_RESTORE_SCRIPT = (
    "set -e\n" + _SNAPSHOT_PRELUDE +
    'commit=$(git rev-parse -q --verify "refs/aura/snapshots/$1^{commit}") || exit 5\n'
    'git clean -fdq\n'
    'git read-tree --reset -u "$commit"\n'
    'if git rev-parse -q --verify HEAD >/dev/null; then git reset -q; else git read-tree --empty; fi\n'
)

#: Bounds how much of a streamed command's output this side ever holds in memory while
#: accumulating it, independent of whatever cap the daemon itself applies
#: (`aura_hostd/lib/src/task_handler.dart`) and independent of `run_command`'s own
#: `max_result_chars=20_000` (`ToolSpec`, which truncates the *returned* string after this
#: function is done). Deliberately larger than that 20k so a command whose useful output is near
#: the end (a test runner's summary, say) is not truncated at the wrong end by two caps fighting
#: each other -- this one exists purely so a runaway command cannot grow this process's memory
#: without bound while its output is still arriving.
_RUN_COMMAND_STREAM_CAP_CHARS = 200_000

#: Paths per `github_list_repository_files` response. A large repository's full tree is tens of
#: thousands of paths -- more context than the listing is worth, and `max_result_chars` would
#: truncate it mid-path anyway; the tool says how many it withheld and how to narrow the listing.
_LIST_FILES_MAX_ENTRIES = 1_000

#: Extensions whose contents are not text and so cannot survive `SandboxRunner.write_file`'s
#: UTF-8 contract. Skipped by name rather than by sniffing bytes: the code-host port hands back
#: `str`, so by the time a binary blob reached here it would already have been lossily decoded.
_BINARY_SUFFIXES = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".icns", ".svgz",
    ".pdf", ".zip", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".jar", ".war",
    ".mp3", ".mp4", ".mov", ".avi", ".wav", ".ogg", ".webm", ".flac",
    ".ttf", ".otf", ".woff", ".woff2", ".eot",
    ".so", ".dylib", ".dll", ".exe", ".bin", ".o", ".a", ".class", ".pyc", ".wasm",
    ".db", ".sqlite", ".sqlite3", ".keystore", ".jks", ".p12", ".pack", ".idx",
})


def _clone_dir(slug: str, destination: str | None) -> str:
    """Directory a clone lands in: the caller's choice, else the repository's own name."""
    return destination or slug.split("/")[-1]


def _find_checkout_dir(root: Path, slug: str) -> Path | None:
    """The checkout of ``slug`` below ``root`` (one or two levels down), by directory name first.

    A real directory holding a ``.git``; symlinks are never followed out of the workspace.
    """
    name = slug.split("/")[-1].lower()
    found: list[Path] = []
    try:
        for child in sorted(root.iterdir()):
            if child.is_symlink() or not child.is_dir():
                continue
            if (child / ".git").exists():
                found.append(child)
                continue
            for grand in sorted(child.iterdir()):
                if not grand.is_symlink() and grand.is_dir() and (grand / ".git").exists():
                    found.append(grand)
    except OSError:
        return None
    named = [d for d in found if d.name.lower() == name]
    if len(named) == 1:
        return named[0]
    return found[0] if len(found) == 1 else None


def _is_binary_path(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    dot = name.rfind(".")
    return dot > 0 and name[dot:].lower() in _BINARY_SUFFIXES


def _is_pdf_path(path: str) -> bool:
    return path.rsplit("/", 1)[-1].lower().endswith(".pdf")


def _refuse_binary_path(path: str, tool_name: str) -> None:
    """Text tools never write to a PDF or other binary file type.

    ``write_file``/``edit_file``/``github_commit_files`` take and return *text*. Pointed at a
    ``.pdf`` they would replace a real document with a few lines of prose (or, for an edit, fail
    with a decode error that says nothing about why), and the result still opens as "a file".
    """
    if _is_pdf_path(path):
        raise ToolExecutionError(
            f"{path} is a PDF: text cannot be written into a PDF file. Write the content to a "
            "text file (for example a .md or .txt) instead; to read a PDF's text, use "
            "github_get_file_contents, or `cat` the .pdf in run_command.",
            tool=tool_name,
        )
    if _is_binary_path(path):
        raise ToolExecutionError(
            f"{path} has a binary file type; this tool only writes text, and overwriting a binary "
            "file with text would destroy it.",
            tool=tool_name,
        )


def _refuse_directory_path(path: str, tool_name: str) -> None:
    """A path that names a directory is not a file to write.

    ``write_file("dir/", ...)`` used to be accepted by the in-memory runner, which treated the
    trailing slash as noise and wrote a *file* called ``dir`` -- reported as "Wrote ... to dir/". A
    real daemon fails the same call with an operating-system error that says nothing about why.
    """
    stripped = path.strip()
    last = re.split(r"[/\\]", stripped.rstrip("/\\"))[-1] if stripped.rstrip("/\\") else ""
    if stripped.endswith(("/", "\\")) or last in {".", ".."} or not stripped.strip("/\\"):
        base = stripped.rstrip("/\\") or "src"
        raise ToolExecutionError(
            f"{path!r} names a directory, not a file; give a file path such as {base}/name.txt",
            tool=tool_name,
        )


def _require_encodable(text: str, what: str, tool_name: str) -> None:
    """``text`` must be valid Unicode that UTF-8 can carry (no lone surrogates).

    A surrogate half is legal in a Python string and not in a file; without this the failure was
    the codec's own ``'utf-8' codec can't encode character '\\ud800' in position 4``, which names
    neither the argument nor what to do.
    """
    try:
        text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ToolExecutionError(
            f"{what} contains a character that cannot be written as UTF-8 (a lone surrogate at "
            f"position {exc.start}); remove it or replace it with the intended character",
            tool=tool_name,
        ) from exc


def _norm_path(path: str) -> str:
    """A path in the one spelling two operations on the same file would share."""
    return posixpath.normpath(path.lstrip("/")) if path else path


_SLUG_PART = re.compile(r"^[A-Za-z0-9_.-]+$")


def _strict_slug(value: str, tool_name: str) -> str:
    """``owner/name`` that is safe to use as a directory name, a URL path and a workspace key.

    ``RepositoryIdentity`` accepts any two non-blank segments, so ``owner/..`` parsed as a valid
    repository whose *name* is the parent directory -- and ``clone_repository`` joins the name onto
    the workspace path. Both halves are now plain GitHub-name characters and never ``.``/``..``.
    """
    identity = RepositoryIdentity.parse_or_none(value)
    if identity is not None:
        owner, name = identity.owner, identity.name
        if (
            _SLUG_PART.match(owner) and _SLUG_PART.match(name)
            and owner not in (".", "..") and name not in (".", "..")
        ):
            return identity.slug
    raise ToolExecutionError(
        f"{value!r} is not a valid GitHub repository; expected 'owner/name' (letters, digits, "
        "'-', '_' and '.' only) or a GitHub URL like https://github.com/owner/name",
        tool=tool_name,
    )


def _workspace_subdir(destination: str | None, slug: str, tool_name: str) -> str:
    """The directory a clone lands in, as a plain relative path inside the workspace.

    The caller's ``destination`` used to be joined onto the workspace's *host* path unchecked, so
    ``../../x`` or ``/etc/cron.d`` made the gateway run ``git clone`` outside the workspace.
    """
    if destination is None or not destination.strip():
        return slug.split("/")[-1]
    value = destination.strip()
    parts = [part for part in value.replace("\\", "/").split("/") if part not in ("", ".")]
    bad = (
        "\x00" in value
        or value.startswith(("/", "\\", "-", "~"))
        or re.match(r"^[A-Za-z]:", value) is not None
        or not parts
        or any(part == ".." or part == ".git" or part.startswith("-") for part in parts)
        or any(ord(ch) < 32 for ch in value)
    )
    if bad:
        raise ToolExecutionError(
            f"destination must be a plain folder name inside the workspace (relative, no '..', "
            f"not '.git', not starting with '-'), got {destination!r}",
            tool=tool_name,
        )
    return "/".join(parts)


_REF_FORBIDDEN = re.compile(r"[\x00-\x20\x7f~^:?*\[\\]")


def _valid_ref_name(name: str) -> bool:
    """A branch/tag/ref name by git's own rules (``git check-ref-format``), minus the exotic.

    Refused here so a model-chosen name is never an option (``-f``), never revision syntax
    (``main~3``, ``a..b``, ``@{u}``), and never contains whitespace or a newline that would split
    a command or a ``.git/config`` line.
    """
    if not name or _REF_FORBIDDEN.search(name) or name in ("@", "HEAD"):
        return False
    if name.startswith(("-", "/", ".")) or name.endswith(("/", ".", ".lock")):
        return False
    if ".." in name or "//" in name or "@{" in name:
        return False
    return not any(part.startswith(".") or part.endswith(".lock") for part in name.split("/"))


def _check_ref(value: str | None, label: str, tool_name: str) -> None:
    if value == "HEAD":
        return
    if value is not None and value != "" and not _valid_ref_name(value.removeprefix("refs/heads/")):
        # A commit SHA or tag passes: only syntax that git or a URL would misread is refused.
        raise ToolExecutionError(
            f"{label} {value!r} is not a valid branch, tag or commit name", tool=tool_name
        )


def _fit_output(text: str, limit: int, label: str) -> str:
    """``text`` unchanged when it fits, else its head and *tail* around an explicit marker.

    A command's cause is at the top and its verdict (the failing test, the build error) at the
    bottom. The registry cuts a long result at its start, which for a test run keeps the progress
    dots and drops the traceback; cutting the middle here keeps both ends and says how much went.
    """
    if len(text) <= limit:
        return text
    head = limit // 4
    tail = limit - head
    omitted = len(text) - head - tail
    return (
        f"{text[:head]}\n[aura] {omitted} characters of {label} omitted from the middle "
        f"(output was {len(text)} characters)\n{text[-tail:]}"
    )


def _to_crlf(text: str) -> str:
    return re.sub(r"(?<!\r)\n", "\r\n", text)


def _prefers_crlf(text: str) -> bool:
    """True when most of the file's line breaks are CRLF."""
    crlf = text.count("\r\n")
    return crlf > text.count("\n") - crlf


#: Held across an edit's read-modify-write. Keyed by (sandbox task, normalised path) and weakly
#: referenced, so a lock lives exactly as long as a call is using or waiting on it.
_FILE_LOCKS: "weakref.WeakValueDictionary[tuple[str, str], asyncio.Lock]" = weakref.WeakValueDictionary()


def _file_lock(handle: Any, path: str) -> asyncio.Lock:
    key = (str(getattr(handle, "task_id", handle)), _norm_path(path))
    lock = _FILE_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _FILE_LOCKS[key] = lock
    return lock


#: Phrases in a runner's error that mean it refused the batch *before* writing anything.
_WRITE_FILES_NOTHING_WRITTEN = (
    "expected exactly one occurrence", "does not exist", "escapes the sandbox", "must set either",
    "null byte", "is not a file path", "is a PDF", "is a binary file", "not valid UTF-8",
)

#: A PDF whose text is at most this long is returned whole by ``cat``; longer is saved beside it.
_PDF_INLINE_CHARS = 14_000
#: OCR allowance for a scanned PDF read through ``cat`` (the tool's own timeout is 605 s).
_PDF_OCR_BUDGET_S = 240.0

#: Characters that make a command more than a plain ``cat <file>``.
_SHELL_META = re.compile(r"[;&|<>$`()\n\r*?{}\[\]~!#\\]")


def _pdf_cat_target(command: str) -> str | None:
    """The PDF path when ``command`` is exactly ``cat <file>.pdf`` (optionally with ``--``)."""
    if _SHELL_META.search(command):
        return None
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    if not tokens or tokens[0] != "cat":
        return None
    args = [token for token in tokens[1:] if token != "--"]
    if len(args) == 1 and not args[0].startswith("-") and _is_pdf_path(args[0]):
        return args[0]
    return None


_COMMAND_RESULT_BUDGET = 18_000


def _command_result(exit_code: int, stdout: str, stderr: str) -> str:
    """The ``run_command`` result text, with each stream cut in the middle if the pair is too long.

    The registry would otherwise cut the *whole* string at its start: the head of stdout survives
    and stderr -- where the traceback and the truncation notice are -- is simply gone.
    """
    err_budget = min(len(stderr), _COMMAND_RESULT_BUDGET // 3)
    out_budget = _COMMAND_RESULT_BUDGET - err_budget
    if len(stdout) < out_budget:
        err_budget = min(len(stderr), _COMMAND_RESULT_BUDGET - len(stdout))
    return (
        f"exit_code={exit_code}\n"
        f"--- stdout ---\n{_fit_output(stdout, out_budget, 'stdout')}\n"
        f"--- stderr ---\n{_fit_output(stderr, err_budget, 'stderr')}"
    )


_PLAIN_BRANCH = re.compile(r"^[A-Za-z0-9._/-]+$")


def _is_plain_branch_name(branch: str) -> bool:
    """A branch *name* and nothing else: no refspec syntax (``:``, ``+``), no revision syntax
    (``~ ^ @ ..``), no option (leading ``-``), no ``refs/`` path and not ``HEAD`` -- a subset of
    ``git check-ref-format --branch`` that is strict enough to compare against the default
    branch by name and mean it."""
    if not _PLAIN_BRANCH.match(branch) or branch in ("HEAD", "@"):
        return False
    if branch.startswith(("-", "/", "refs/")) or branch.endswith(("/", ".", ".lock")):
        return False
    if ".." in branch or "//" in branch:
        return False
    return not any(part.startswith(".") for part in branch.split("/"))


def _normalize_repo(value: str) -> str:
    """``owner/name`` from whatever form the user pasted.

    A user asking an agent to look at a repository pastes a URL
    (``https://github.com/owner/name``, with or without ``.git``, ``/tree/main``, or a trailing
    slash), and the model passes that string straight through as the ``repo`` argument. Every
    call below wants the ``owner/name`` slug the API is addressed by, so normalise once here
    rather than making each tool -- or the model -- get it right.

    Delegates to :class:`~aura_core.domain.repository_identity.RepositoryIdentity`, the single
    implementation this and :func:`_repo` (and the pre-dispatch schema check in
    ``tools/schema.py``) now share -- this call keeps its original, permissive contract: it can
    return a string that is *not* a valid ``owner/name`` slug, leaving the shape check to the
    caller (``_repo()``/``clone_repository``'s own check below).
    """
    return RepositoryIdentity.normalize_slug(value)


#: The tools that can answer "look at this repository", best first. `clone_repository` gives the
#: fullest answer (the files land in the sandbox, so tests and grep work), but it needs a sandbox;
#: the GitHub reads need nothing but a connected account.
_REPOSITORY_TOOLS_BY_PREFERENCE = (
    "clone_repository",
    "github_list_repository_files",
    "github_get_file_contents",
)
REPOSITORY_TOOL_NAMES = frozenset(_REPOSITORY_TOOLS_BY_PREFERENCE)

#: A bare platform mention ("check my github.com profile") used to match on its own -- combined
#: with a common verb like "review" or "read" in `_REPO_ACTION`, that fired on ordinary sentences
#: that merely name the platform, forcing a planning generation for a request with no repository
#: in it at all. Requiring an owner/repo path after the domain is what a real repository mention
#: always has and a platform-only mention never does, so the bare form now needs one.
#:
#: A full URL that merely *looks* like a repo (a GitHub Pages blog post, a gist) still matches --
#: telling those apart from a real repository needs the content, not the URL shape, so this stays
#: a false-positive risk. The cost is bounded and already the accepted one for this whole
#: function: forcing a planning generation does not choose a tool, so the worst case is one wasted
#: generation before the model itself declines to call `clone_repository` on it.
_REPO_REFERENCE = re.compile(
    r"https?://\S+|git@\S+|"
    r"\b(?:github|gitlab|bitbucket)\.(?:com|org)[:/][\w.-]+/[\w.-]+|"
    r"\b[\w.-]+/[\w.-]+\.git\b",
    re.I,
)
#: Verb stems, not exact forms: `\w*` after the alternation is deliberate. The original pattern
#: matched each verb's dictionary form only (`\breview\b`, `\bclone\b`), so `\b` immediately
#: after the stem required a non-word character there -- which "reviewing", "cloned", and
#: "analyzing" never have (the suffix is a word character too). That silently missed every
#: inflected phrasing of a repository request and reproduced this module's own reason for
#: existing, just for a different verb tense.
_REPO_ACTION = re.compile(
    r"\b(?:clon|fork|check(?:ing)? ?out|pull|inspect|explor|analy[sz]|review|audit|"
    r"summari[sz]|read|look(?:ing)? at|go(?:ing)? through|walk(?:ing)? through|"
    # Acting verbs, not just reading ones. A persistent workspace makes "run the tests on
    # <repo>" or "fix the failing build in <repo>" ordinary requests, and planning is the only
    # path that runs a tool (ADR-006) -- so a request that names a repository and asks for work
    # has to reach the planner, or the agent answers that it cannot do the thing it can do.
    r"run|build|test|fix|debug|install|deploy|refactor|implement|patch|migrat|upgrad|"
    r"lint|format|compil|benchmark)\w*\b",
    re.I,
)


def matching_repository_tool(message: str, available_tools: Sequence[str]) -> str | None:
    """The repository tool a "clone/analyse this repo" request should reach, or ``None``.

    The same shape and the same job as ``tools/personal_os.py``'s
    :func:`matching_personal_os_tool`: :meth:`PlanningEngine.needs_plan` skips planning for
    anything its prose heuristics do not recognise as multi-step, and without planning nothing
    ever runs a tool (ADR-006) -- so an unrecognised request is answered by the model alone,
    which can only say it is unable to reach a repository. "clone <url> and analyse it" is
    exactly that case: the verb precedes the conjunction, so the multi-step markers miss it.

    Returns ``None`` unless a tool that could actually satisfy the request is being offered,
    so a caller without the engineer grant does not pay for a planning generation that could
    only end in a plain answer.
    """
    offered = [name for name in _REPOSITORY_TOOLS_BY_PREFERENCE if name in set(available_tools)]
    if not offered:
        return None
    text = message.strip()
    if not text:
        return None
    if _REPO_REFERENCE.search(text) and _REPO_ACTION.search(text):
        return offered[0]
    return None


# --------------------------------------------------------------------------------------
# Sandbox session management (shared by filesystem, command, and git tools)
# --------------------------------------------------------------------------------------


class _SandboxSessionCache:
    """One sandbox handle per turn, keyed by ``ToolContext.correlation_id``.

    Tools have no turn-completion hook today -- a proper fix would destroy a task's sandbox when
    :class:`~aura_core.execution.agentic_loop.AgenticExecutor` finishes handling it, but that would
    couple a generic execution loop to one tool family's resource. Instead, any handle idle longer
    than ``idle_ttl_s`` is destroyed the next time a *different* correlation id asks for one --
    bounding leaked containers without new kernel plumbing. A handle already in use for the
    current correlation id is reused, so ``write_file`` followed by ``run_command`` in the same
    turn operate on the same container.
    """

    def __init__(self, runner: Any, *, idle_ttl_s: float = 900.0) -> None:
        self._runner = runner
        self._idle_ttl_s = idle_ttl_s
        self._handles: dict[str, tuple[Any, float]] = {}
        self._lock = asyncio.Lock()

    async def get(self, context: ToolContext) -> Any:
        correlation_id = context.correlation_id
        async with self._lock:
            now = time.monotonic()
            stale = [
                cid for cid, (_, last) in self._handles.items()
                if cid != correlation_id and now - last > self._idle_ttl_s
            ]
            for cid in stale:
                handle, _ = self._handles.pop(cid)
                try:
                    await self._runner.destroy(handle)
                except Exception:
                    logger.debug("idle sandbox cleanup failed", extra={"correlation_id": cid})

            if correlation_id in self._handles:
                handle, _ = self._handles[correlation_id]
            else:
                # `user_id` matters only to a runner that routes per-caller (the desktop
                # host-agent bridge) -- every other runner accepts and ignores it (ports/coding.py).
                #
                # D8: `session_id`/`isolate` are threaded through `ToolContext.metadata` rather
                # than dedicated `ToolContext` slots -- see `kernel.py::_tool_context`'s own
                # comment on why (avoids widening a `__slots__` contract documented as
                # policy-inputs-only for a Desktop-IDE-specific, opt-in capability). Every
                # non-Desktop caller's `context.metadata` simply lacks these keys, so `.get(...)`
                # below defaults to `isolate=False` -- identical behavior to before this existed.
                # FSA-006 (second half): the Desktop IDE's active workspace root, carried on
                # `ToolContext.metadata` from `TurnRequest.workspace_root`, binds the task once
                # here -- so run, write, read and the git tools on this handle all land in the
                # root the IDE shows, not in whichever root the daemon was granted first. Passed
                # only when present, so a runner that predates the parameter is called exactly as
                # before. The daemon decides whether the path lies inside a granted root; nothing
                # on this side interprets it.
                root = (context.metadata or {}).get("workspace_root") or None
                extra = {"workspace_root": root} if root else {}
                # WP8: a phone or web turn says so, and the daemon refuses it unless remote
                # control is on at the computer. Passed only when known, like the root above.
                origin = (context.metadata or {}).get("origin")
                if origin:
                    extra["origin"] = origin
                handle = await self._runner.create_task(
                    user_id=context.user_id,
                    session_id=context.metadata.get("session_id"),
                    isolate=bool(context.metadata.get("desktop_task_isolation", False)),
                    **extra,
                )
            self._handles[correlation_id] = (handle, now)
            return handle




class _WorkspaceResolver:
    """Resolves a tool call to a durable workspace, and records the activity that keeps it alive.

    Replaces ``_SandboxSessionCache`` wherever a :class:`WorkspaceManager` is wired. The
    difference that matters is lifetime: the cache handed out a container that died with the
    turn, so "clone it and fix the failing test" had to start over every message. Here the same
    ``(user, repo)`` resolves to the same checkout tomorrow.

    Two details carry weight:

    * **Every call touches.** The 72-hour TTL measures inactivity, so a workspace someone is
      actively using must never look idle. Touching on read as well as write is deliberate --
      reading a file is use.
    * **The repository is remembered per turn.** ``clone_repository`` and ``open_workspace``
      bind it; ``run_command`` afterwards needs no repository argument, which is both what a
      model naturally emits and what keeps a follow-up command in the workspace the clone
      landed in rather than silently opening a second one.

    A turn that never names a repository gets a per-user scratch workspace, so the tools still
    work for "write me a script and run it" -- the case that has nothing to do with a repository
    and should not be made to invent one.
    """

    #: Repository name for the no-repository case. Not a valid ``owner/name``, so it can never
    #: collide with a real repository a user also has open.
    SCRATCH = "(scratch)"

    #: A binding lives for one turn, but nothing tells this object the turn is over, so the map
    #: is bounded instead of growing by one entry per message for the life of the process.
    _MAX_BINDINGS = 2048

    def __init__(self, manager: Any, runner: Any) -> None:
        self._manager = manager
        self._runner = runner
        self._bindings: dict[str, str] = {}
        self._quota_ok: dict[str, float] = {}
        self._lock = asyncio.Lock()

    def bind(self, correlation_id: str, repo: str) -> None:
        self._bindings.pop(correlation_id, None)
        self._bindings[correlation_id] = repo
        while len(self._bindings) > self._MAX_BINDINGS:
            self._bindings.pop(next(iter(self._bindings)))

    def unbind_repo(self, repo: str) -> None:
        """Forget every turn's binding to ``repo``.

        After ``destroy_workspace`` a still-bound turn would silently re-create an empty workspace
        for the repository on its next write and report success.
        """
        for correlation_id in [c for c, r in self._bindings.items() if r == repo]:
            del self._bindings[correlation_id]

    #: Walking a checkout to total its size is O(files); a passing check is trusted this long.
    _QUOTA_RECHECK_S = 15.0

    async def enforce_quota(self, opened: Any, *, tool_name: str) -> None:
        enforce = getattr(self._manager, "enforce_disk_quota", None)
        if enforce is None:
            return
        key = opened.record.id.value
        checked = self._quota_ok.get(key)
        if checked is not None and time.monotonic() - checked < self._QUOTA_RECHECK_S:
            return
        self._quota_ok.pop(key, None)
        await enforce(opened.record, tool=tool_name)  # raises when over; only a pass is cached
        self._quota_ok[key] = time.monotonic()
        while len(self._quota_ok) > 256:
            self._quota_ok.pop(next(iter(self._quota_ok)))

    def bound_repo(self, correlation_id: str) -> str | None:
        return self._bindings.get(correlation_id)

    async def open(self, context: ToolContext, repo: str | None = None) -> Any:
        """The workspace for this call, running, with its activity recorded.

        When the call names no repository and this turn has not bound one, fall back to the
        user's most recently used workspace rather than to scratch. That fallback is what makes
        the feature work across messages at all: a second message is a new correlation id with no
        binding, so binding alone would send "now run the tests" to an empty scratch workspace
        while the checkout sat in another one, and the agent would report that the files it just
        created are missing. Scratch remains the answer only for a user who has no workspace --
        the "write me a script and run it" case, which should not have to invent a repository.
        """
        async with self._lock:
            target = repo or self._bindings.get(context.correlation_id)
            if target is None:
                target = await self._most_recent_repo(context) or self.SCRATCH
            opened = await self._manager.open(context.user_id, target)
            self.bind(context.correlation_id, target)
            await self._manager.touch(opened.record.id)
            return opened

    async def _most_recent_repo(self, context: ToolContext) -> str | None:
        try:
            existing = await self._manager.list_for_user(context.user_id)
        except Exception:  # pragma: no cover - a store outage must not fail the tool call
            return None
        return existing[0].repo if existing else None

    async def handle(self, context: ToolContext, repo: str | None = None) -> Any:
        return (await self.open(context, repo)).handle


class _SessionAdapter:
    """Presents the per-turn cache and the durable resolver behind one call.

    Keeps ``build_sandbox_tools`` usable without a manager -- the local/dev shape, and every test
    that predates workspaces -- rather than forcing a second copy of every tool body.
    """

    def __init__(
        self, *, cache: Any = None, resolver: Any = None, host_agent_registry: Any = None
    ) -> None:
        self._cache = cache
        self._resolver = resolver
        self._host_agent_registry = host_agent_registry

    @property
    def durable(self) -> bool:
        return self._resolver is not None

    async def _desktop_active(self, context: ToolContext) -> bool:
        """Whether this call's user has a live desktop (``aura_hostd``) pairing right now.

        A durable, Docker-only workspace (``_WorkspaceResolver``) and a desktop pairing
        (``CompositeSandboxRunner``, ADR-011) are two different places a file can live. Absent
        this check, ``handle()`` below would route a paired user's every filesystem/command call
        into their durable GitHub-repo workspace instead of the real folder they opened on their
        own machine, whenever both happen to be configured on the same deployment.
        """
        if self._host_agent_registry is None:
            return False
        return await self._host_agent_registry.resolve(context.user_id) is not None

    async def handle(self, context: ToolContext, repo: str | None = None) -> Any:
        if self._resolver is not None and self._cache is not None and await self._desktop_active(context):
            return await self._cache.get(context)
        if self._resolver is not None:
            return await self._resolver.handle(context, repo)
        return await self._cache.get(context)

    async def opened(self, context: ToolContext, repo: str | None = None) -> Any:
        if self._resolver is None:
            return None
        return await self._resolver.open(context, repo)

    async def write_handle(self, context: ToolContext, *, tool_name: str) -> Any:
        """The handle for a call that *adds* to the workspace, after its disk quota is checked.

        ``WorkspaceManager.enforce_disk_quota`` existed and documented itself as "checked before a
        write", but nothing called it, so the quota bounded nothing. It is checked here for the
        tools that write files (not ``run_command``: a workspace over quota must still be able to
        run ``rm -rf node_modules`` to get back under it).
        """
        if self._resolver is not None and not await self._desktop_active(context):
            opened = await self._resolver.open(context)
            await self._resolver.enforce_quota(opened, tool_name=tool_name)
            return opened.handle
        return await self.handle(context)

    def forget_repo(self, repo: str) -> None:
        if self._resolver is not None:
            self._resolver.unbind_repo(repo)

    async def enforce_quota(self, opened: Any, *, tool_name: str) -> None:
        if self._resolver is not None:
            await self._resolver.enforce_quota(opened, tool_name=tool_name)

    def bound_repo(self, correlation_id: str) -> str | None:
        return self._resolver.bound_repo(correlation_id) if self._resolver else None

async def _drain_streaming_run(
    runner: StreamingSandboxRunner, handle: Any, command: Sequence[str], *, timeout_s: float
) -> SandboxResult:
    """Consumes a `StreamingSandboxRunner.run_streaming` sequence into the same `SandboxResult`
    shape `SandboxRunner.run` returns, capping accumulated size at
    `_RUN_COMMAND_STREAM_CAP_CHARS` along the way rather than after the fact.

    A `"done"` event that never arrives (a runner that violates its own contract) would otherwise
    hang this forever; that is a bug in the runner, not something to paper over here, so this
    trusts the contract documented on `SandboxOutputEvent` rather than adding a second timeout on
    top of the one `run_streaming` already enforces internally.
    """
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    total_chars = 0
    truncated = False
    exit_code = -1
    saw_done = False
    async for event in runner.run_streaming(handle, command, timeout_s=timeout_s):
        if event.kind == "done":
            exit_code = event.exit_code
            saw_done = True
            break
        if total_chars >= _RUN_COMMAND_STREAM_CAP_CHARS:
            truncated = True
            continue
        target = stdout_parts if event.kind == "stdout" else stderr_parts
        remaining = _RUN_COMMAND_STREAM_CAP_CHARS - total_chars
        chunk = event.data[:remaining]
        target.append(chunk)
        total_chars += len(chunk)
        if len(chunk) < len(event.data):
            truncated = True
    stderr = "".join(stderr_parts)
    if not saw_done:
        stderr += "\n[aura] the command's output ended without an exit status; its outcome is unknown"
    if truncated:
        stderr += (
            f"\n[aura] output truncated after {_RUN_COMMAND_STREAM_CAP_CHARS} characters "
            "while the command was still running"
        )
    return SandboxResult(stdout="".join(stdout_parts), stderr=stderr, exit_code=exit_code)



def build_sandbox_tools(
    sandbox_runner: Any,
    *,
    code_host_resolver: Any = None,
    workspace_manager: Any = None,
    host_git: Any = None,
    token_resolver: Any = None,
    host_agent_registry: Any = None,
    desktop_ops: bool = False,
    read_ledger: Any = None,
) -> list[Tool]:
    """Filesystem, command-execution, and git tools against one shared per-task sandbox.

    ``read_ledger`` (ADR-045, ``tools/open_workspace.py::FileReadLedger``) makes ``write_file`` and
    ``edit_file`` *warn* -- never block -- when a file changed since the agent last read or wrote it.
    It needs nothing from the host-daemon protocol: both tools already read through the runner.

    ``desktop_ops`` (with a ``host_agent_registry``) additionally registers the ADR-036 group C
    tools (``tools/desktop_ops.py``: ``docker_build``, ``docker_compose``, ``profile_python``). They
    are built here, not by ``default_tools``, because they share this function's ``sessions``
    adapter -- the workspace the agent's own file and command tools just used.

    ``code_host_resolver`` -- the same ``(UserId) -> Awaitable[CodeHostClient | None]`` callable
    :func:`build_github_tools` takes -- additionally wires ``clone_repository``. It is built here,
    next to the sandbox tools rather than with the GitHub ones, because it must write into the
    *same* per-turn sandbox handle ``run_command`` will then read from: a clone that landed in a
    different container would be invisible to the very next tool call.
    """
    if workspace_manager is not None:
        # Both paths are built even though a manager exists: `_SessionAdapter.handle()` picks the
        # per-turn cache instead of the durable resolver, per call, whenever this user currently
        # has a desktop pairing -- see its own docstring for why (`host_agent_registry` is what
        # lets it tell).
        sessions = _SessionAdapter(
            cache=_SandboxSessionCache(sandbox_runner),
            resolver=_WorkspaceResolver(workspace_manager, sandbox_runner),
            host_agent_registry=host_agent_registry,
        )
    else:
        # No manager: per-turn sandboxes, exactly as before. This is the local/dev shape and the
        # one every pre-workspace caller still gets.
        sessions = _SessionAdapter(cache=_SandboxSessionCache(sandbox_runner))

    @tool(
        "write_file",
        "Write one UTF-8 text file in the workspace: the user's own folder when a paired desktop app "
        "(AURA Desktop) is connected, otherwise a throwaway sandbox directory torn down after the task. "
        "Creates missing parent directories and OVERWRITES an existing file. Text only: .pdf "
        "and other binary paths are refused. For a small change to an existing file use "
        "edit_file; for several files in one step use write_files.",
        param("path", "string", "Path relative to the workspace root.", max_length=300),
        param("content", "string", "The file's full new content.", max_length=200_000),
        category=ToolCategory.DEVELOPMENT,
        sensitivity=ToolSensitivity.PERSONAL,
        side_effecting=True,
        required_capability=_ENGINEER_CAPABILITY,
        timeout_s=15.0,
        workspace_local=True,
    )
    async def write_file(path: str, content: str, context: ToolContext) -> str:
        if not path or not path.strip():
            raise ToolExecutionError("path must not be empty", tool="write_file")
        _refuse_directory_path(path, "write_file")
        _refuse_binary_path(path, "write_file")
        _require_encodable(content, "content", "write_file")
        handle = await sessions.write_handle(context, tool_name="write_file")
        note = None
        async with _file_lock(handle, path):
            if read_ledger is not None:
                note = await _stale_note_overwrite(handle, path, context)
            await sandbox_runner.write_file(handle, path, content)
            if read_ledger is not None:
                _ledger_wrote(path, content, context)
        result = f"Wrote {len(content.encode('utf-8'))} bytes to {path}"
        return f"{result}\n{note}" if note else result

    async def _stale_note_overwrite(handle: Any, path: str, context: ToolContext) -> str | None:
        """ADR-045: a warning if the file about to be overwritten differs from what the agent last
        saw. Only files with a baseline cost the extra read; any failure means "no warning"."""
        from .open_workspace import stale_note_before_write

        user = str(getattr(context.user_id, "value", context.user_id))
        if read_ledger.baseline(user, "sandbox", path) is None:
            return None
        try:
            current = await sandbox_runner.read_file(handle, path)
        except Exception:
            return None  # missing, binary or unreadable: nothing to compare, and never a reason to block
        return stale_note_before_write(read_ledger, context, path, current, overwriting=True)

    def _ledger_wrote(path: str, text: str, context: ToolContext) -> None:
        from .open_workspace import note_sandbox_written

        note_sandbox_written(read_ledger, context, path, text)

    async def _read_text(handle: Any, path: str, tool_name: str) -> str:
        try:
            return await sandbox_runner.read_file(handle, path)
        except UnicodeDecodeError as exc:
            raise ToolExecutionError(
                f"{path} is not valid UTF-8 text (byte {exc.start}); it is binary or in another "
                "encoding, and editing it as text would corrupt it",
                tool=tool_name,
            ) from exc

    @tool(
        "edit_file",
        "Replace one exact piece of text in a workspace file. `find` must occur exactly once in "
        "the file, otherwise nothing changes and the error says why -- read the file first "
        "(e.g. run_command 'cat <path>') so you copy the text exactly. For several edits in "
        "one step use write_files.",
        param("path", "string", "Path relative to the workspace root.", max_length=300),
        param("find", "string", "Exact text to replace; must occur exactly once in the file.",
              max_length=50_000),
        param("replace", "string", "Replacement text.", max_length=50_000),
        category=ToolCategory.DEVELOPMENT,
        sensitivity=ToolSensitivity.PERSONAL,
        side_effecting=True,
        required_capability=_ENGINEER_CAPABILITY,
        timeout_s=15.0,
        workspace_local=True,
    )
    async def edit_file(path: str, find: str, replace: str, context: ToolContext) -> str:
        _refuse_directory_path(path, "edit_file")
        _refuse_binary_path(path, "edit_file")
        _require_encodable(find, "find", "edit_file")
        _require_encodable(replace, "replace", "edit_file")
        if find == "":
            raise ToolExecutionError(
                "`find` is empty; give the exact existing text to replace (to create or "
                "overwrite a whole file use write_file)",
                tool="edit_file",
            )
        if find == replace:
            raise ToolExecutionError(
                "`find` and `replace` are identical, so nothing would change", tool="edit_file"
            )
        handle = await sessions.write_handle(context, tool_name="edit_file")
        # One edit at a time per file: read-modify-write is two steps, and two edits that read
        # the same text would each write back a file that lacks the other's change.
        async with _file_lock(handle, path):
            current = await _read_text(handle, path, "edit_file")
            note = None
            if read_ledger is not None:
                from .open_workspace import stale_note_before_write

                note = stale_note_before_write(read_ledger, context, path, current, overwriting=False)
            needle, replacement = find, replace
            occurrences = current.count(needle)
            crlf_file = _prefers_crlf(current)
            if occurrences == 0 and crlf_file and "\n" in find:
                # The file uses CRLF and the model wrote LF: the same text, differently spelled.
                needle = _to_crlf(find)
                occurrences = current.count(needle)
            if crlf_file and "\n" in replace:
                replacement = _to_crlf(replace)
            if occurrences != 1:
                if occurrences > 1:
                    hint = " -- include more surrounding lines so it is unique"
                elif (
                    unicodedata.normalize("NFC", current).count(unicodedata.normalize("NFC", find)) > 0
                ):
                    # The text is there, spelled with different code points: "é" as one character or
                    # as "e" plus a combining accent, and the same for Arabic marks. Both look
                    # identical, which is why a model that copied it exactly still did not match.
                    hint = (
                        " -- the same text IS in the file, but spelled with different Unicode "
                        "code points (accents or marks composed differently); copy the exact "
                        "characters from the file with run_command `cat`, or use write_file"
                    )
                else:
                    hint = (
                        " -- check the exact text, indentation and line breaks (re-read the file; "
                        "it may have changed)"
                    )
                raise ToolExecutionError(
                    f"expected exactly one occurrence of the given text in {path}, "
                    f"found {occurrences}{hint}",
                    tool="edit_file",
                )
            updated = current.replace(needle, replacement, 1)
            await sandbox_runner.write_file(handle, path, updated)
            if read_ledger is not None:
                _ledger_wrote(path, updated, context)
        result = f"Edited {path} (1 replacement)"
        return f"{result}\n{note}" if note else result

    _WRITE_FILES_MAX_OPS = 100

    @tool(
        "write_files",
        "Write or edit several workspace files in one call, all or nothing. Every operation is "
        "validated first (the path is inside the workspace; each find/replace's `find` text is "
        "present in its file exactly once) and if any fails, nothing in the call is written -- "
        "not even the valid operations.",
        param(
            "operations", "array",
            "The operations, in order. Each is an object with `path` and either `content` (full "
            "overwrite, as write_file) or both `find` and `replace` (an edit, as edit_file: "
            "`find` must occur exactly once in the current file) -- never a mix.",
            item_type="object",
        ),
        category=ToolCategory.DEVELOPMENT,
        sensitivity=ToolSensitivity.PERSONAL,
        side_effecting=True,
        required_capability=_ENGINEER_CAPABILITY,
        timeout_s=30.0,
        # Same exemption as write_file/edit_file/run_command, for the same reason (ADR-014): this
        # is D6's multi-file sibling of those single-file tools, not a new kind of effect.
        workspace_local=True,
    )
    async def write_files(operations: list, context: ToolContext) -> str:
        if not isinstance(operations, list) or not operations:
            raise ToolExecutionError("operations must be a non-empty array", tool="write_files")
        if len(operations) > _WRITE_FILES_MAX_OPS:
            raise ToolExecutionError(
                f"at most {_WRITE_FILES_MAX_OPS} operations per call (got {len(operations)}); "
                "split the work into several calls",
                tool="write_files",
            )
        ops: list[FileWriteOp] = []
        seen: dict[str, str] = {}
        for index, raw in enumerate(operations, start=1):
            where = f"operation {index}"
            if not isinstance(raw, Mapping) or "path" not in raw:
                raise ToolExecutionError(
                    f"{where} must be an object with a 'path'", tool="write_files"
                )
            path = raw["path"]
            if not isinstance(path, str) or not path.strip() or len(path) > 300:
                raise ToolExecutionError(
                    f"{where}: 'path' must be a non-empty string of at most 300 characters",
                    tool="write_files",
                )
            extra = sorted(set(raw) - {"path", "content", "find", "replace"})
            if extra:
                raise ToolExecutionError(
                    f"{where} ({path!r}) has unknown field(s) {extra}; use 'content', or 'find' "
                    "and 'replace'",
                    tool="write_files",
                )
            _refuse_directory_path(path, "write_files")
            _refuse_binary_path(path, "write_files")
            key = _norm_path(path)
            if key in seen:
                raise ToolExecutionError(
                    f"{where} and {seen[key]} both target {path!r}. Each find/replace is checked "
                    "against the file as it is now, so the later one would silently undo the "
                    "earlier one; combine them into one operation, or make two calls.",
                    tool="write_files",
                )
            seen[key] = where
            has_content = "content" in raw
            has_edit = "find" in raw or "replace" in raw
            if has_content and has_edit:
                raise ToolExecutionError(
                    f"{where} ({path!r}) mixes 'content' with 'find'/'replace'; use exactly one "
                    "shape",
                    tool="write_files",
                )
            if has_content:
                content = raw["content"]
                if not isinstance(content, str) or len(content) > 200_000:
                    raise ToolExecutionError(
                        f"{where} ({path!r}): 'content' must be a string of at most 200000 "
                        f"characters (got {type(content).__name__})",
                        tool="write_files",
                    )
                _require_encodable(content, f"{where} ({path!r}) content", "write_files")
                ops.append(FileWriteOp(path=path, content=content))
            elif "find" in raw and "replace" in raw:
                find, replace = raw["find"], raw["replace"]
                if not isinstance(find, str) or not isinstance(replace, str):
                    raise ToolExecutionError(
                        f"{where} ({path!r}): 'find' and 'replace' must both be strings",
                        tool="write_files",
                    )
                if find == "" or find == replace or len(find) > 50_000 or len(replace) > 50_000:
                    raise ToolExecutionError(
                        f"{where} ({path!r}): 'find' must be non-empty, differ from 'replace' and "
                        "be at most 50000 characters",
                        tool="write_files",
                    )
                _require_encodable(find, f"{where} ({path!r}) find", "write_files")
                _require_encodable(replace, f"{where} ({path!r}) replace", "write_files")
                ops.append(FileWriteOp(path=path, find=find, replace=replace))
            else:
                raise ToolExecutionError(
                    f"operation for {path!r} must have either 'content', or both 'find' and "
                    "'replace'",
                    tool="write_files",
                )
        handle = await sessions.write_handle(context, tool_name="write_files")
        async with contextlib.AsyncExitStack() as stack:
            for lock_key in sorted({_norm_path(op.path) for op in ops}):
                await stack.enter_async_context(_file_lock(handle, lock_key))
            try:
                await sandbox_runner.write_files(handle, ops)
            except ToolExecutionError:
                raise
            except Exception as exc:
                message = str(exc) or type(exc).__name__
                if any(marker in message for marker in _WRITE_FILES_NOTHING_WRITTEN):
                    message += " (nothing in this call was written)"
                raise ToolExecutionError(message, tool="write_files", cause=exc) from exc
        if read_ledger is not None:
            # Its content is not known here, so drop the baselines rather than leave stale ones that
            # would make the next edit warn about a change this call itself made.
            user = str(getattr(context.user_id, "value", context.user_id))
            for op in ops:
                read_ledger.forget(user, "sandbox", op.path)
        return f"Wrote/edited {len(ops)} file(s): {', '.join(op.path for op in ops)}"

    async def _pdf_cat(handle: Any, pdf_path: str) -> str | None:
        """``cat file.pdf`` -> the PDF's full text, in the same result shape ``run_command`` uses.

        The sandbox has no PDF tooling, so ``cat`` of a PDF is a screenful of binary noise and the
        model concludes the file is unreadable. A runner that can hand back a file's exact bytes
        (the server-side sandbox) lets the platform's own page-by-page extractor read it instead --
        every page, scanned pages named (and OCR'd when an engine is configured). A runner that
        cannot (a paired desktop) falls through to the real command.
        """
        reader = getattr(sandbox_runner, "read_file_bytes", None)
        if reader is None:
            return None
        try:
            data = await reader(handle, pdf_path)
        except Exception:
            return None  # let the real `cat` say why (missing file, a directory, ...)
        if len(data) > MAX_PDF_BYTES:
            return _command_result(
                1, "", f"{pdf_path} is larger than {MAX_PDF_BYTES // (1024 * 1024)} MiB; too big to read."
            )
        try:
            read = await asyncio.to_thread(
                read_source_bytes, data, name=pdf_path, ocr_time_budget_s=_PDF_OCR_BUDGET_S
            )
        except SourceReadError as exc:
            return _command_result(1, "", str(exc))
        text = read.text
        if read.kind == "pdf":
            info = read.describe()
            note = f"[PDF {pdf_path}: {info['pages']} page(s), {info['pages_read']} read, {len(text)} characters of text"
            if info["unreadable_pages"]:
                note += f"; page(s) NOT read: {', '.join(map(str, info['unreadable_pages']))}"
            note += "]"
        else:
            note = f"[{pdf_path} has a .pdf name but is plain text: {len(text)} characters]"
        warnings = "\n".join(read.warnings())
        if len(text) <= _PDF_INLINE_CHARS:
            return _command_result(0, f"{note}\n{text}", warnings)
        sidecar = f"{pdf_path}.txt"
        try:
            await sandbox_runner.write_file(handle, sidecar, text)
        except Exception:
            shown = text[:_PDF_INLINE_CHARS]
            return _command_result(
                0,
                f"{note}\n{shown}\n[aura] only the first {len(shown)} of {len(text)} characters are "
                "shown; the rest could not be saved to a file to read in parts.",
                warnings,
            )
        quoted = shlex.quote(sidecar)
        shown = text[:_PDF_INLINE_CHARS // 2]
        shown = shown[: shown.rfind("\n")] if "\n" in shown else shown
        shown_lines = shown.count("\n") + 1
        return _command_result(
            0,
            f"{note}\n[The full text ({len(text)} characters, {text.count(chr(10)) + 1} lines) is "
            f"saved to {sidecar}. Lines 1-{shown_lines} are shown below; read on with "
            f"`sed -n '{shown_lines + 1},{shown_lines + 150}p' {quoted}` (then the next range), or "
            f"search it with `grep -n WORD {quoted}`. It is an untracked helper file: delete it "
            f"when finished.]\n{shown}",
            warnings,
        )

    @tool(
        "run_command",
        "Run a shell command in the workspace and return its stdout, stderr and exit code. Use "
        "it to run tests, builds and linters or to inspect files. With a paired desktop app "
        "(AURA Desktop) connected it runs on the user's own machine (their real network, under the daemon's own path "
        "containment and command policy); otherwise in a throwaway, network-restricted sandbox "
        "container that cannot reach the user's network or host. Long output keeps its start "
        "and end. `cat file.pdf` alone returns the PDF's full text.",
        param("command", "string", "The shell command to run, e.g. 'pytest -q'.", max_length=2000),
        param("timeout_s", "number", "Max seconds to allow the command to run.", required=False,
              minimum=1, maximum=600, default=60),
        category=ToolCategory.DEVELOPMENT,
        sensitivity=ToolSensitivity.SYSTEM,
        side_effecting=True,
        required_capability=_ENGINEER_CAPABILITY,
        timeout_s=605.0,
        max_result_chars=20_000,
        # Command output can echo file contents, comments, or anything else in the sandbox
        # workspace -- attacker-influenceable exactly like fetch_url's page content.
        tags=("untrusted_content",),
        # SYSTEM + side_effecting would otherwise force confirmation on *every* command, which
        # in an eight-iteration engineering task is a prompt per step. The effect is confined to
        # the workspace container and its checkout; anything that leaves (git_push, forge
        # writes) is not marked and stays gated.
        workspace_local=True,
    )
    async def run_command(command: str, context: ToolContext, timeout_s: float = 60.0) -> str:
        if not command or not command.strip():
            raise ToolExecutionError("command is empty; give the shell command to run", tool="run_command")
        if "\x00" in command:
            raise ToolExecutionError("command contains a NUL byte", tool="run_command")
        handle = await sessions.handle(context)
        pdf_target = _pdf_cat_target(command)
        if pdf_target is not None:
            outcome = await _pdf_cat(handle, pdf_target)
            if outcome is not None:
                return outcome
        if isinstance(sandbox_runner, StreamingSandboxRunner):
            # Streaming and non-streaming runners return the same shape either way -- the
            # difference `run_streaming` makes today is bounding memory while output is still
            # arriving (see `_RUN_COMMAND_STREAM_CAP_CHARS`) rather than holding one unbounded
            # string until the command exits. Nothing downstream of this function observes the
            # difference yet; that is Unit 5/8's job (a client to actually show live output),
            # not this one's -- see docs/architecture/011-desktop-engineer-agent.md.
            result = await _drain_streaming_run(
                sandbox_runner, handle, ["sh", "-c", command], timeout_s=timeout_s
            )
        else:
            result = await sandbox_runner.run(handle, ["sh", "-c", command], timeout_s=timeout_s)
        stdout = result.stdout
        if stdout.startswith("%PDF-"):
            advice = (
                "this workspace cannot extract PDF text itself; use a PDF tool that is installed "
                "(e.g. `pdftotext FILE.pdf -`), or fetch the file with github_get_file_contents, "
                "which reads every page"
                if pdf_target is not None
                else "to read the PDF's text run `cat FILE.pdf` on its own (nothing piped or combined)"
            )
            stdout = (
                f"[aura] the command printed a PDF's raw bytes ({len(stdout)} characters), which "
                f"are not readable text; {advice}."
            )
        return _command_result(result.exit_code, stdout, result.stderr)

    async def _git(handle: Any, argv: list[str], *, timeout_s: float = 15.0) -> Any:
        return await sandbox_runner.run(handle, argv, timeout_s=timeout_s)

    # Where this call's git work happens ------------------------------------------------------
    #
    # `clone_repository` lands a repository in `<workspace>/<name>/` (or wherever `destination`
    # says), so in a durable workspace the workspace root is not itself a repository and a bare
    # `git status` there fails with "not a git repository" right after a successful clone. The git
    # tools therefore resolve a checkout: the `repo_path` the caller names, else the workspace root
    # when it is a repository, else the repository found one or two levels down (the one matching
    # the bound repository's name when there are several). Done with plain `sh` through the same
    # runner as every other command, so it behaves the same in a server-side workspace and on a
    # paired desktop. Several repositories and no way to tell which is an error that lists them.

    _REPO_SCAN = (
        'for g in */.git */*/.git; do [ -e "$g" ] && printf "%s\\n" "${g%/.git}"; done 2>/dev/null; true'
    )

    def _clean_repo_path(value: str, tool_name: str) -> str:
        text = value.strip().replace("\\", "/")
        parts = [part for part in text.split("/") if part not in ("", ".")]
        if (
            not text or "\x00" in text or text.startswith(("/", "-", "~")) or ".." in parts
            or len(text) > 200 or any(part.startswith("-") for part in parts)
        ):
            raise ToolExecutionError(
                f"repo_path {value!r} must be a relative path inside the workspace "
                "(no '..', no leading '/' or '-')",
                tool=tool_name,
            )
        return "/".join(parts) or "."

    async def _is_checkout(handle: Any, directory: str) -> bool:
        """``directory`` (relative; ``.`` = the root) is a real directory holding a ``.git``."""
        probe = await sandbox_runner.run(
            handle,
            ["sh", "-c", 'd="$1"; [ -d "$d" ] && [ ! -L "$d" ] && [ -e "$d/.git" ]', "aura-git", directory],
            timeout_s=10.0,
        )
        return probe.exit_code == 0

    async def _checkout_args(
        context: ToolContext, handle: Any, repo_path: str | None = None, *, tool_name: str = "git",
    ) -> list[str]:
        """``["-C", "<dir>"]`` when the git work belongs in a checkout below the root, else ``[]``."""
        if repo_path is not None and repo_path.strip():
            directory = _clean_repo_path(repo_path, tool_name)
            if not await _is_checkout(handle, directory):
                raise ToolExecutionError(
                    f"{directory!r} is not a git repository inside the workspace (no .git there); "
                    "clone one with clone_repository or run `git init` in it",
                    tool=tool_name,
                )
            return [] if directory == "." else ["-C", directory]
        # Asked with git itself (not a shell probe): on a paired desktop the daemon's command policy
        # sees a plain `git rev-parse`, the same kind of read the tools already made, instead of an
        # extra `sh -c` it would put to the user. Only when this fails -- which used to be the end of
        # the road ("not a git repository") -- does the search below run.
        inside = await _git(handle, ["git", "rev-parse", "--is-inside-work-tree"], timeout_s=10.0)
        if inside.exit_code == 0 and inside.stdout.strip() == "true":
            return []
        scan = await sandbox_runner.run(handle, ["sh", "-c", _REPO_SCAN], timeout_s=10.0)
        found = sorted({
            line.strip() for line in scan.stdout.splitlines()
            if line.strip() and not line.startswith(("/", "-"))
        })[:50]
        if not found:
            cloned = await _auto_clone_bound_repository(context, tool_name)
            # Nothing to clone either: the caller's own "not a git repository" message applies.
            return cloned or []
        slug = sessions.bound_repo(context.correlation_id)
        if slug and slug != _WorkspaceResolver.SCRATCH:
            name = slug.split("/")[-1].lower()
            named = [d for d in found if d.rsplit("/", 1)[-1].lower() == name]
            if len(named) == 1:
                return ["-C", named[0]]
        if len(found) == 1:
            return ["-C", found[0]]
        listed = ", ".join(found[:8]) + (" ..." if len(found) > 8 else "")
        raise ToolExecutionError(
            f"the workspace holds several git repositories ({listed}); pass repo_path to say which one",
            tool=tool_name,
        )

    _REV_RE = re.compile(r"^[A-Za-z0-9._/@{}~^+-]+$")

    def _valid_revision(value: str, what: str, tool_name: str) -> str:
        """A revision (branch, tag, sha, ``HEAD~2``, ``origin/main``) that is safe to hand to git."""
        text = value.strip()
        if (
            not text or len(text) > 200 or text.startswith(("-", "/", ".")) or ".." in text
            or not _REV_RE.match(text) or text.endswith(".lock")
        ):
            raise ToolExecutionError(
                f"{what} {value!r} is not a plain revision (a branch, tag, commit id or something "
                "like HEAD~2; no spaces, no '..' ranges, no leading '-')",
                tool=tool_name,
            )
        return text

    def _clean_paths(paths: Any, tool_name: str) -> list[str]:
        if paths is None:
            return []
        if not isinstance(paths, (list, tuple)) or not all(isinstance(p, str) for p in paths):
            raise ToolExecutionError("paths must be a list of path strings", tool=tool_name)
        if len(paths) > 200:
            raise ToolExecutionError("at most 200 paths per call", tool=tool_name)
        cleaned: list[str] = []
        for raw in paths:
            text = raw.strip().replace("\\", "/")
            parts = [part for part in text.split("/") if part not in ("", ".")]
            if not text or "\x00" in text or text.startswith(("/", "~")) or ".." in parts or len(text) > 300:
                raise ToolExecutionError(
                    f"path {raw!r} must be relative to the repository (no '..', no leading '/')",
                    tool=tool_name,
                )
            cleaned.append("/".join(parts) or ".")
        return cleaned

    async def _host_git_clone(
        context: ToolContext, slug: str, subdir: str, ref: str | None, destination: str | None,
        *, tool_name: str = "clone_repository",
    ) -> str:
        """The real ``git clone`` into ``<workspace>/<subdir>``; shared by ``clone_repository`` and
        by the git tools' own clone when a workspace has no checkout yet."""
        opened = await sessions.opened(context, slug)
        await sessions.enforce_quota(opened, tool_name=tool_name)
        checkout = Path(await sandbox_runner.workspace_path(opened.handle)) / subdir
        # Belt and braces under `_workspace_subdir`: whatever was joined, the checkout must
        # still be inside the workspace, because this is the gateway's own filesystem.
        workspace_root = Path(await sandbox_runner.workspace_path(opened.handle)).resolve()
        if workspace_root not in checkout.resolve().parents:
            raise ToolExecutionError(
                f"destination {destination!r} is outside the workspace", tool=tool_name
            )
        # The token travels on its own channel rather than being dug out of the code-host
        # client: a private attribute would tie this to one adapter and silently yield None
        # for any other, turning a private clone into a confusing auth failure.
        token = await token_resolver(context.user_id) if token_resolver else None

        await host_git.clone(slug, dest=checkout, token=token, ref=ref)
        log = await host_git.log(checkout, limit=5)
        branch = await host_git.current_branch(checkout)
        note = "\nNote: " + "; ".join(opened.evicted) if opened.evicted else ""
        return (
            f"Cloned {slug} into {subdir}/ with full git history "
            f"(branch {branch}). Recent commits:\n{log}{note}"
        )

    _auto_clone_locks: dict[str, asyncio.Lock] = {}
    _auto_clone_notes: dict[str, str] = {}

    async def _auto_clone_bound_repository(context: ToolContext, tool_name: str) -> list[str] | None:
        """Clone the workspace's own repository when the git tools find no checkout in it.

        A durable workspace is opened *for* a repository; if its files were never cloned (a new
        turn, a workspace opened without a clone, a checkout that was deleted) every git tool used
        to fail with "not a git repository" and leave the model to work out why. The repository is
        the one the workspace is bound to -- never a model-chosen name -- so this fetches nothing the
        user did not already attach to the workspace, with the same credentials, quota and real
        ``git clone`` as ``clone_repository``. ``None`` when there is nothing to clone (a scratch
        workspace, no bound repository, or host-side git is not wired), so the caller's own
        "not a git repository" message applies.
        """
        if host_git is None or not sessions.durable or await sessions._desktop_active(context):
            return None
        slug = sessions.bound_repo(context.correlation_id)
        if not slug or slug == _WorkspaceResolver.SCRATCH:
            return None
        subdir = _workspace_subdir(None, slug, tool_name)
        lock = _auto_clone_locks.setdefault(f"{context.user_id}:{slug}", asyncio.Lock())
        async with lock:
            # Another call may have cloned it while this one waited.
            opened = await sessions.opened(context, slug)
            root = Path(await sandbox_runner.workspace_path(opened.handle))
            existing = _find_checkout_dir(root, slug)
            if existing is not None:
                return ["-C", existing.relative_to(root).as_posix()]
            client = await code_host_resolver(context.user_id) if code_host_resolver else None
            if client is None:
                raise ToolExecutionError(
                    f"the workspace for {slug} has no checkout and GitHub is not connected, so it cannot "
                    "be cloned -- connect an account via /api/v3/integrations/github/connect",
                    tool=tool_name,
                )
            try:
                summary = await _host_git_clone(context, slug, subdir, None, None, tool_name=tool_name)
            except ToolExecutionError:
                raise
            except Exception as exc:
                raise ToolExecutionError(
                    f"the workspace for {slug} has no checkout and cloning it failed: {exc}", tool=tool_name
                ) from exc
            _auto_clone_notes[context.correlation_id] = (
                f"[The workspace had no checkout of {slug}, so it was cloned into {subdir}/ first. "
                f"{summary.splitlines()[0]}]"
            )
        return ["-C", subdir]

    def _with_clone_note(context: ToolContext, text: str) -> str:
        """Prefix a git tool's result with the note that it cloned the repository first, once."""
        note = _auto_clone_notes.pop(context.correlation_id, None)
        return f"{note}\n{text}" if note else text

    def _git_failure(result: Any) -> str:
        """What git said, from whichever stream it used (``nothing to commit`` goes to stdout)."""
        return (result.stderr.strip() or result.stdout.strip() or f"exit code {result.exit_code}")[:300]

    @tool(
        "git_diff",
        "Show changes in the workspace's git repository. With no options: uncommitted changes against "
        "HEAD (staged and unstaged) plus a list of untracked files. `staged` shows only what is staged; "
        "`base` (and optionally `head`) compares any two revisions, or a revision with the working tree; "
        "`paths` limits it to some files; `format` picks a full patch, a diffstat, or just the changed "
        "file names. Read-only."
        " If the workspace holds no checkout yet, the repository it was opened for is cloned first.",
        param("repo_path", "string", "Repository directory inside the workspace, when it holds more than one "
              "or the repository is not at the root (default: the root, else the repository found below it).",
              required=False, max_length=200),
        param("staged", "boolean", "Only the changes staged for the next commit (git diff --cached).",
              required=False, default=False),
        param("base", "string", "Compare against this revision (branch, tag, commit id, HEAD~2, origin/main).",
              required=False, max_length=200),
        param("head", "string", "With `base`: compare `base` to this revision instead of the working tree.",
              required=False, max_length=200),
        param("merge_base", "boolean", "With `base` and `head`: diff from their common ancestor (what a pull "
              "request shows), not from `base` itself.", required=False, default=False),
        param("paths", "array", "Only these files or directories, relative to the repository.",
              required=False, item_type="string"),
        param("format", "string", "patch (default), stat (a summary per file) or names (changed file names "
              "with A/M/D status).", required=False, default="patch", enum=("patch", "stat", "names")),
        category=ToolCategory.DEVELOPMENT,
        sensitivity=ToolSensitivity.PERSONAL,
        required_capability=_ENGINEER_CAPABILITY,
        timeout_s=45.0,
        max_result_chars=20_000,
    )
    async def git_diff(
        context: ToolContext, repo_path: str | None = None, staged: bool = False, base: str | None = None,
        head: str | None = None, merge_base: bool = False, paths: list | None = None, format: str = "patch",
    ) -> str:
        handle = await sessions.handle(context)
        where = await _checkout_args(context, handle, repo_path, tool_name="git_diff")
        fmt_flag = {"patch": [], "stat": ["--stat"], "names": ["--name-status"]}.get(format)
        if fmt_flag is None:
            raise ToolExecutionError("format must be patch, stat or names", tool="git_diff")
        base_rev = _valid_revision(base, "base", "git_diff") if base else None
        head_rev = _valid_revision(head, "head", "git_diff") if head else None
        if head_rev and not base_rev:
            raise ToolExecutionError("`head` needs a `base` to compare it with", tool="git_diff")
        if head_rev and staged:
            raise ToolExecutionError("`staged` compares the index; it cannot be combined with `head`", tool="git_diff")
        if merge_base and not (base_rev and head_rev):
            raise ToolExecutionError("`merge_base` needs both `base` and `head`", tool="git_diff")
        limit = _clean_paths(paths, "git_diff")
        tail = ["--", *limit] if limit else []
        has_head = await _git(handle, ["git", *where, "rev-parse", "--verify", "--quiet", "HEAD"])
        if has_head.exit_code != 0:
            inside = await _git(handle, ["git", *where, "rev-parse", "--is-inside-work-tree"])
            if inside.exit_code != 0 or inside.stdout.strip() != "true":
                raise ToolExecutionError(
                    "the workspace is not a git repository; clone one (clone_repository) or run "
                    "`git init` first",
                    tool="git_diff",
                )
        common = ["git", *where, "diff", "--no-ext-diff", "--no-color", *fmt_flag]
        explicit = bool(staged or base_rev or head_rev)
        if base_rev and head_rev:
            commands = [[*common, f"{base_rev}{'...' if merge_base else '..'}{head_rev}", *tail]]
        elif base_rev:
            commands = [[*common, *(["--cached"] if staged else []), base_rev, *tail]]
        elif staged:
            commands = [[*common, "--cached", *tail]]
        elif has_head.exit_code == 0:
            # Everything uncommitted: staged and unstaged together, against HEAD.
            commands = [[*common, "HEAD", *tail]]
        else:
            # A repository with no commit yet has no HEAD to diff against: its changes are what is
            # staged, plus what is edited since. (This used to run `git diff HEAD`, fail, and report
            # "(no changes)" for a workspace full of them.)
            commands = [[*common, "--cached", *tail], [*common, *tail]]
        parts: list[str] = []
        for argv in commands:
            result = await _git(handle, argv)
            if result.exit_code != 0:
                raise ToolExecutionError(
                    f"{' '.join(argv)} failed: {_git_failure(result)}", tool="git_diff"
                )
            if result.stdout:
                parts.append(result.stdout)
        diff = "".join(parts)
        if explicit or format != "patch":
            # An explicit comparison is about those revisions; untracked files are not part of it.
            return _with_clone_note(context, diff or "(no differences)")
        untracked = await _git(handle, ["git", *where, "ls-files", "--others", "--exclude-standard", *tail])
        names = [line for line in untracked.stdout.splitlines() if line] if untracked.exit_code == 0 else []
        if not names:
            return _with_clone_note(context, diff or "(no changes)")
        listed = "\n".join(f"- {name}" for name in names[:50])
        if len(names) > 50:
            listed += f"\n... and {len(names) - 50} more"
        header = diff or "(no changes to tracked files)\n"
        return _with_clone_note(context, f"{header}\n\nUntracked files (new, not in the diff above):\n{listed}")

    async def _refuse_default_branch_commit(handle: Any, where: list[str]) -> None:
        """git_commit's own default-branch protection (git_push has one; a commit made straight on
        ``main`` is the first step of a push to it, and of a pull request with nothing to review).

        The default is the checkout's ``origin/HEAD``; ``main`` and ``master`` count as the default
        whenever the repository has a remote, because that ref lives in the checkout where commands
        can rewrite it. A repository with no remote (a fresh ``git init``) has nothing to protect.
        """
        current = await _git(handle, ["git", *where, "symbolic-ref", "--quiet", "--short", "HEAD"], timeout_s=10.0)
        if current.exit_code != 0 or not current.stdout.strip():
            return  # detached HEAD: not on any branch
        branch = current.stdout.strip()
        remote = await _git(handle, ["git", *where, "remote"], timeout_s=10.0)
        has_remote = remote.exit_code == 0 and bool(remote.stdout.strip())
        protected: set[str] = set()
        origin_head = await _git(
            handle, ["git", *where, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"], timeout_s=10.0
        )
        if origin_head.exit_code == 0 and origin_head.stdout.strip():
            protected.add(origin_head.stdout.strip().split("/", 1)[-1])
        if has_remote:
            protected |= {"main", "master"}
        if branch in protected:
            raise ToolExecutionError(
                f"refusing to commit on {branch!r}, the repository's default branch. Create a branch "
                "first with git_checkout_branch, commit there, and open a pull request.",
                tool="git_commit",
            )

    @tool(
        "git_commit",
        "Commit changes in the workspace's git repository with `message`. By default every change is "
        "staged first (new files included); pass `paths` to commit only those files. Refuses to commit on "
        "the repository's default branch (create a branch with git_checkout_branch first). The commit "
        "stays local until git_push."
        " If the workspace holds no checkout yet, the repository it was opened for is cloned first.",
        param("message", "string", "Commit message.", max_length=2000),
        param("paths", "array", "Commit only these files or directories (relative to the repository); "
              "anything else that is staged or changed stays out of this commit. Omit to commit everything.",
              required=False, item_type="string"),
        param("repo_path", "string", "Repository directory inside the workspace, when it holds more than one "
              "or the repository is not at the root (default: the root, else the repository found below it).",
              required=False, max_length=200),
        category=ToolCategory.DEVELOPMENT,
        sensitivity=ToolSensitivity.PERSONAL,
        side_effecting=True,
        required_capability=_ENGINEER_CAPABILITY,
        timeout_s=45.0,
        # A commit is local to the checkout until something pushes it; git_push is the tool that
        # crosses the boundary, and it is deliberately not marked.
        workspace_local=True,
    )
    async def git_commit(
        message: str, context: ToolContext, paths: list | None = None, repo_path: str | None = None,
    ) -> str:
        if not message.strip():
            raise ToolExecutionError("commit message must not be empty", tool="git_commit")
        selected = _clean_paths(paths, "git_commit")
        handle = await sessions.write_handle(context, tool_name="git_commit")
        where = await _checkout_args(context, handle, repo_path, tool_name="git_commit")
        await _refuse_default_branch_commit(handle, where)
        added = await _git(handle, ["git", *where, "add", "-A", *(["--", *selected] if selected else [])])
        if added.exit_code != 0:
            raise ToolExecutionError(f"git add failed: {_git_failure(added)}", tool="git_commit")
        # The sandbox's HOME is a fresh tmpfs for every command, so there is no global identity
        # and `git commit` refuses ("Please tell me who you are"). Supply one for this commit
        # only, and only when none is configured -- never over a person's own.
        identity = await _git(
            handle,
            ["sh", "-c", "git " + " ".join(shlex.quote(a) for a in (*where, "config", "user.name")) + " >/dev/null && git "
                         + " ".join(shlex.quote(a) for a in (*where, "config", "user.email")) + " >/dev/null"],
        )
        argv = ["git"]
        if identity.exit_code != 0:
            argv += ["-c", "user.name=AURA Engineer Agent", "-c", "user.email=engineer-agent@aura.local"]
        # `--only -- <paths>` commits exactly those paths whatever else is already staged.
        result = await _git(
            handle, [*argv, *where, "commit", "-m", message, *(["--only", "--", *selected] if selected else [])],
        )
        if result.exit_code != 0:
            raise ToolExecutionError(f"git commit failed: {_git_failure(result)}", tool="git_commit")
        return _with_clone_note(context, result.stdout or "committed")

    @tool(
        "git_checkout_branch",
        "Create a git branch in the workspace's repository and switch to it, or just switch if it already "
        "exists. An existing branch is never reset. `start_point` makes a new branch start from a given "
        "revision instead of the current commit."
        " If the workspace holds no checkout yet, the repository it was opened for is cloned first.",
        param("branch", "string", "Branch name.", max_length=200),
        param("start_point", "string", "For a new branch: the revision to start from (a branch, tag or "
              "commit id). Default: the current commit.", required=False, max_length=200),
        param("repo_path", "string", "Repository directory inside the workspace, when it holds more than one "
              "or the repository is not at the root (default: the root, else the repository found below it).",
              required=False, max_length=200),
        category=ToolCategory.DEVELOPMENT,
        sensitivity=ToolSensitivity.PERSONAL,
        side_effecting=True,
        required_capability=_ENGINEER_CAPABILITY,
        timeout_s=30.0,
        workspace_local=True,
    )
    async def git_checkout_branch(
        branch: str, context: ToolContext, start_point: str | None = None, repo_path: str | None = None,
    ) -> str:
        if not _valid_ref_name(branch):
            raise ToolExecutionError(
                f"{branch!r} is not a valid branch name (no spaces, no '..', no leading '-', no "
                "'~^:?*[\\\\')",
                tool="git_checkout_branch",
            )
        start = _valid_revision(start_point, "start_point", "git_checkout_branch") if start_point else None
        handle = await sessions.write_handle(context, tool_name="git_checkout_branch")
        where = await _checkout_args(context, handle, repo_path, tool_name="git_checkout_branch")
        exists = await _git(
            handle, ["git", *where, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], timeout_s=10.0
        )
        if exists.exit_code == 0 and start:
            raise ToolExecutionError(
                f"branch {branch!r} already exists; start_point only applies to a new branch "
                "(an existing branch is never reset)",
                tool="git_checkout_branch",
            )
        # `checkout -B` -- which this used to run -- *resets* an existing branch to the current
        # HEAD: asking for `main` while on a feature branch moved `main` onto the feature's
        # commits. An existing branch is switched to; only a new one is created.
        argv = (
            ["git", *where, "checkout", branch, "--"] if exists.exit_code == 0
            else ["git", *where, "checkout", "-b", branch, *([start] if start else [])]
        )
        result = await _git(handle, argv, timeout_s=10.0)
        if result.exit_code != 0:
            raise ToolExecutionError(
                f"{' '.join(argv)} failed: {_git_failure(result)}", tool="git_checkout_branch"
            )
        return _with_clone_note(context, result.stdout or f"switched to {branch}")

    @tool(
        "open_workspace",
        "Open (or resume) your persistent workspace for a repository, without cloning it. Its "
        "files persist between turns and for 3 days after your last action, so a task can span "
        "several messages. Use it to go back to a repository you already worked on.",
        param("repo", "string", "Repository this workspace is for, as 'owner/name'.",
              max_length=400, format="github_repo"),
        category=ToolCategory.DEVELOPMENT,
        sensitivity=ToolSensitivity.PERSONAL,
        side_effecting=True,
        required_capability=_ENGINEER_CAPABILITY,
        timeout_s=60.0,
        workspace_local=True,
    )
    async def open_workspace(repo: str, context: ToolContext) -> str:
        if not sessions.durable:
            raise ToolExecutionError(
                "persistent workspaces are not enabled on this deployment; the sandbox is "
                "per-turn here",
                tool="open_workspace",
            )
        slug = _strict_slug(repo, "open_workspace")
        opened = await sessions.opened(context, slug)
        listing = await sandbox_runner.run(
            opened.handle, ["sh", "-c", "ls -A"], timeout_s=15.0
        )
        note = "\nNote: " + "; ".join(opened.evicted) if opened.evicted else ""
        contents = listing.stdout.strip() or "(empty -- nothing cloned into it yet)"
        return f"Workspace for {slug} is open. Contents:\n{contents}{note}"

    @tool(
        "list_workspaces",
        "List your persistent workspaces: the repository each is for, whether it is running, "
        "and when it was last used. A workspace is removed after 3 days without activity.",
        category=ToolCategory.DEVELOPMENT,
        sensitivity=ToolSensitivity.PERSONAL,
        required_capability=_ENGINEER_CAPABILITY,
        timeout_s=30.0,
    )
    async def list_workspaces(context: ToolContext) -> str:
        if not sessions.durable:
            return "Persistent workspaces are not enabled on this deployment."
        rows = await workspace_manager.list_for_user(context.user_id)
        if not rows:
            return "You have no workspaces open."
        lines = [
            f"- {w.repo} ({w.status}), last used {w.last_active_at.isoformat(timespec='minutes')}"
            for w in rows
        ]
        return "\n".join(lines)

    @tool(
        "destroy_workspace",
        "Permanently delete one of your persistent workspaces and everything in it, including "
        "uncommitted work. Use it when the task is finished or the checkout should start "
        "fresh. The user is always asked first.",
        param("repo", "string", "Repository whose workspace should be deleted.", max_length=400,
              format="github_repo"),
        category=ToolCategory.DEVELOPMENT,
        # CONFIDENTIAL + side_effecting is the existing idiom for "always confirm"
        # (ToolSpec.__post_init__). Deliberately NOT workspace_local: this destroys work the user
        # may not have pushed, and unlike everything else in a workspace it cannot be undone by
        # running the step again.
        sensitivity=ToolSensitivity.CONFIDENTIAL,
        side_effecting=True,
        required_capability=_ENGINEER_CAPABILITY,
        timeout_s=60.0,
    )
    async def destroy_workspace(repo: str, context: ToolContext) -> str:
        if not sessions.durable:
            raise ToolExecutionError(
                "persistent workspaces are not enabled on this deployment",
                tool="destroy_workspace",
            )
        slug = _strict_slug(repo, "destroy_workspace")
        record = await workspace_manager.get(context.user_id, slug)
        if record is None:
            return f"No workspace open for {slug}."
        await workspace_manager.destroy(record)
        # A turn still bound to this repository would otherwise re-create an empty workspace for
        # it on its next write and report success.
        sessions.forget_repo(slug)
        return f"Deleted the workspace for {slug} and everything in it."

    @tool(
        "git_status",
        "Show the workspace repository's git status: the current branch plus staged and unstaged changes."
        " If the workspace holds no checkout yet, the repository it was opened for is cloned first.",
        param("repo_path", "string", "Repository directory inside the workspace, when it holds more than one "
              "or the repository is not at the root (default: the root, else the repository found below it).",
              required=False, max_length=200),
        category=ToolCategory.DEVELOPMENT,
        sensitivity=ToolSensitivity.PERSONAL,
        required_capability=_ENGINEER_CAPABILITY,
        timeout_s=30.0,
    )
    async def git_status(context: ToolContext, repo_path: str | None = None) -> str:
        handle = await sessions.handle(context)
        where = await _checkout_args(context, handle, repo_path, tool_name="git_status")
        result = await _git(handle, ["git", *where, "status", "--short", "--branch"], timeout_s=15.0)
        if result.exit_code != 0:
            raise ToolExecutionError(
                f"git status failed: {_git_failure(result)}", tool="git_status"
            )
        return _with_clone_note(context, result.stdout or "(clean)")

    @tool(
        "git_push",
        "Push a branch of the workspace to GitHub. The user is always asked to confirm first, "
        "and the repository's default branch is refused -- open a pull request from a feature "
        "branch instead. Commit with git_commit before pushing.",
        param("branch", "string", "Branch to push; must not be the default branch.",
              max_length=200),
        param("repo", "string", "Repository as 'owner/name'. Defaults to the one you cloned.",
              required=False, max_length=400, format="github_repo"),
        category=ToolCategory.DEVELOPMENT,
        # The one tool here that publishes anything. CONFIDENTIAL + side_effecting makes
        # confirmation mandatory (ToolSpec.__post_init__), and it is deliberately not
        # workspace_local: this is precisely the boundary ADR-014 keeps the gate for.
        sensitivity=ToolSensitivity.CONFIDENTIAL,
        side_effecting=True,
        required_capability=_ENGINEER_CAPABILITY,
        timeout_s=300.0,
        tags=(NETWORK_TAG,),
    )
    async def git_push(branch: str, context: ToolContext, repo: str | None = None) -> str:
        if host_git is None or not sessions.durable:
            raise ToolExecutionError(
                "pushing is not available on this deployment; propose the change with "
                "github_create_draft_pull_request instead",
                tool="git_push",
            )
        slug = _strict_slug(repo, "git_push") if repo else sessions.bound_repo(context.correlation_id)
        if not slug or slug == _WorkspaceResolver.SCRATCH:
            raise ToolExecutionError(
                "no repository is associated with this workspace; clone one first",
                tool="git_push",
            )
        opened = await sessions.opened(context, slug)
        workspace_root = Path(await sandbox_runner.workspace_path(opened.handle))
        checkout = workspace_root / _clone_dir(slug, None)
        if not await host_git.is_repository(checkout):
            # Cloned under a `destination` of the caller's choosing, or one level further down:
            # find the checkout by what is on disk rather than by the name it would have had.
            checkout = _find_checkout_dir(workspace_root, slug) or checkout
        if not await host_git.is_repository(checkout):
            raise ToolExecutionError(
                f"{checkout.name} is not a git checkout; clone the repository first",
                tool="git_push",
            )

        if not _is_plain_branch_name(branch):
            # The default-branch check below compares *names*; a refspec (`HEAD:main`,
            # `+x:main`, `:main`) or an option (`--mirror`) would pass it and still write to or
            # delete the default branch (tests/security/test_workspace_boundaries.py).
            raise ToolExecutionError(
                f"{branch!r} is not a plain branch name; pass just the branch to push, "
                "e.g. 'feature/login-fix'",
                tool="git_push",
            )
        # Refused here rather than left to the forge's branch protection: not every repository
        # has any, and "the agent pushed straight to main" is not a thing to discover afterwards.
        default_branch = await host_git.default_branch(checkout)
        refused = {default_branch}
        if code_host_resolver is not None:
            # The checkout's idea of the default branch is a ref (`refs/remotes/origin/HEAD`) that
            # commands in the workspace can rewrite or delete, after which `main` would stop being
            # refused. GitHub's own answer cannot be edited from there, so it is asked as well and
            # both names are refused.
            client = await code_host_resolver(context.user_id)
            if client is not None:
                try:
                    refused.add(await client.default_branch(slug))
                except Exception as exc:
                    raise ToolExecutionError(
                        f"could not confirm {slug}'s default branch with GitHub ({exc}); not pushing "
                        "without knowing which branch is protected",
                        tool="git_push",
                    ) from exc
        if branch in refused:
            raise ToolExecutionError(
                f"refusing to push to {branch!r}, the default branch. Push a feature "
                "branch and open a pull request from it.",
                tool="git_push",
            )

        token = await token_resolver(context.user_id) if token_resolver else None
        if token_resolver is not None and not token:
            raise ToolExecutionError(
                "GitHub is not connected for this user, so there is no credential to push with -- "
                "connect an account via /api/v3/integrations/github/connect",
                tool="git_push",
            )
        await host_git.push(checkout, branch=branch, token=token, repo=slug)
        return f"Pushed {branch} to {slug}."


    # -- snapshots (ADR-041) --------------------------------------------------------------
    #
    # A snapshot is a git commit object holding the workspace's whole working tree -- tracked
    # files, and untracked files that are not ignored -- stored under refs/aura/snapshots/<id>.
    # It is built in a throwaway index, so taking one touches neither the working tree, the real
    # index, HEAD nor any branch, and shows up in neither `git status` nor `git log`. Restoring is
    # the reverse: remove the untracked files added since, then make index and working tree equal
    # the snapshot's tree, leaving HEAD where it is. Plain git run through the same
    # `sandbox_runner` as every other tool here, so it works the same in a server-side workspace
    # and on a paired desktop (where the daemon's own command policy still applies).

    async def _snapshot_git(handle: Any, script: str, *args: str, timeout_s: float) -> Any:
        """Run a snapshot script; its arguments travel as argv, never spliced into the script."""
        return await sandbox_runner.run(
            handle, ["sh", "-c", script, "aura-snapshot", *args], timeout_s=timeout_s
        )

    def _snapshot_failure(result: Any, tool_name: str) -> ToolExecutionError:
        reasons = {
            3: "the workspace is not a git repository; clone one (clone_repository) or run `git init` first",
            4: "the workspace is inside a larger repository; snapshots need the workspace itself to be "
               "the repository root, because restoring one must not touch anything outside the workspace",
            5: "no snapshot with that id; list_workspace_snapshots shows the ones that exist",
        }
        return ToolExecutionError(
            reasons.get(result.exit_code) or f"git failed: {_git_failure(result)}", tool=tool_name
        )

    async def _list_snapshots(handle: Any, tool_name: str) -> list[tuple[str, str, str]]:
        """``(id, committed-at, label)`` for every snapshot, newest first."""
        result = await _snapshot_git(handle, _SNAPSHOT_LIST_SCRIPT, timeout_s=20.0)
        if result.exit_code != 0:
            raise _snapshot_failure(result, tool_name)
        rows: list[tuple[int, str, str, str]] = []
        for line in result.stdout.splitlines():
            ref, _, rest = line.partition("\t")
            when, _, subject = rest.partition("\t")
            snapshot_id = ref.removeprefix(_SNAPSHOT_REF_PREFIX)
            parsed = _SNAPSHOT_SUBJECT.fullmatch(subject)
            if _SNAPSHOT_ID.fullmatch(snapshot_id):
                rows.append((int(parsed.group(1)) if parsed else 0, snapshot_id, when,
                             parsed.group(2) if parsed else subject))
        rows.sort(key=lambda row: (row[0], row[2]), reverse=True)
        return [(i, when, label) for _clock, i, when, label in rows]

    async def _take_snapshot(handle: Any, label: str, tool_name: str) -> str:
        result = await _snapshot_git(
            handle, _SNAPSHOT_SCRIPT, " ".join(label.split())[:_SNAPSHOT_LABEL_MAX],
            str(time.time_ns()), timeout_s=60.0,
        )
        snapshot_id = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
        if result.exit_code != 0 or not _SNAPSHOT_ID.fullmatch(snapshot_id):
            raise _snapshot_failure(result, tool_name)
        # Keep the newest _SNAPSHOT_KEEP. Best effort: a failed prune must not fail the snapshot.
        try:
            stale = [i for i, _, _ in (await _list_snapshots(handle, tool_name))[_SNAPSHOT_KEEP:]
                     if i != snapshot_id]
            if stale:
                await _snapshot_git(handle, _SNAPSHOT_FORGET_SCRIPT, *stale, timeout_s=30.0)
        except ToolExecutionError:
            logger.warning("could not prune old workspace snapshots", exc_info=True)
        return snapshot_id

    @tool(
        "workspace_snapshot",
        "Save the workspace's current state -- every file, including new ones you have not committed -- "
        "under a label, so you can return to it with workspace_restore if a change goes wrong. Takes "
        f"no commit and changes no file. The newest {_SNAPSHOT_KEEP} are kept. Returns the snapshot id.",
        param("label", "string", "What this state is, e.g. 'before refactoring the parser'.",
              max_length=_SNAPSHOT_LABEL_MAX),
        category=ToolCategory.DEVELOPMENT,
        sensitivity=ToolSensitivity.PERSONAL,
        side_effecting=True,
        required_capability=_ENGINEER_CAPABILITY,
        timeout_s=75.0,
        # Writes objects and a ref inside the workspace's own .git, nothing else: the same
        # confinement git_commit has.
        workspace_local=True,
    )
    async def workspace_snapshot(label: str, context: ToolContext) -> str:
        if not label.strip():
            raise ToolExecutionError("label must not be empty", tool="workspace_snapshot")
        handle = await sessions.write_handle(context, tool_name="workspace_snapshot")
        snapshot_id = await _take_snapshot(handle, label, "workspace_snapshot")
        return (
            f"Saved snapshot {snapshot_id} ({' '.join(label.split())[:_SNAPSHOT_LABEL_MAX]}). Nothing in "
            f"the workspace changed. workspace_restore with this id returns to this state."
        )

    @tool(
        "list_workspace_snapshots",
        "List the workspace's saved snapshots, newest first: id, when, and label.",
        category=ToolCategory.DEVELOPMENT,
        sensitivity=ToolSensitivity.PERSONAL,
        required_capability=_ENGINEER_CAPABILITY,
        timeout_s=30.0,
    )
    async def list_workspace_snapshots(context: ToolContext) -> str:
        handle = await sessions.handle(context)
        rows = [f"- {i}  {when}  {label}"
                for i, when, label in await _list_snapshots(handle, "list_workspace_snapshots")]
        return "\n".join(rows) if rows else "No snapshots yet. workspace_snapshot saves one."

    async def _preview_restore(arguments: Mapping[str, Any], context: ToolContext) -> str | None:
        """What the person approving is about to lose: the workspace's uncommitted changes."""
        handle = await sessions.handle(context)
        status = await _git(handle, ["git", "status", "--short"], timeout_s=15.0)
        if status.exit_code != 0:
            return None
        lines = [line for line in status.stdout.splitlines() if line]
        shown = "\n".join(lines[:40]) + (f"\n... and {len(lines) - 40} more" if len(lines) > 40 else "")
        return (
            f"Restoring {arguments.get('snapshot_id')} replaces the workspace's files with that saved state. "
            + (f"These changes, present now, will be discarded:\n{shown}" if lines
               else "The workspace has no uncommitted changes. ")
            + "\nThe current state is saved as a new snapshot first, so this can be undone."
        )

    @tool(
        "workspace_restore",
        "Return the workspace to a saved snapshot: files changed since come back as they were, files "
        "added since (and not ignored by git) are removed, and everything outside the workspace is "
        "left alone. HEAD and branches do not move. DISCARDS uncommitted work, so the user is always "
        "asked first; the current state is saved as a snapshot just before, and its id is returned so "
        "the restore itself can be undone.",
        param("snapshot_id", "string", "An id from workspace_snapshot or list_workspace_snapshots.",
              max_length=40),
        category=ToolCategory.DEVELOPMENT,
        # CONFIDENTIAL + side_effecting is the existing idiom for "always confirm"
        # (ToolSpec.__post_init__), and it is deliberately NOT workspace_local: this throws work
        # away, so no profile and no tainted-or-untainted rule may wave it through.
        sensitivity=ToolSensitivity.CONFIDENTIAL,
        side_effecting=True,
        required_capability=_ENGINEER_CAPABILITY,
        timeout_s=150.0,
        preview=_preview_restore,
    )
    async def workspace_restore(snapshot_id: str, context: ToolContext) -> str:
        snapshot_id = snapshot_id.strip()
        if not _SNAPSHOT_ID.fullmatch(snapshot_id):
            raise ToolExecutionError(
                f"{snapshot_id!r} is not a snapshot id (they look like snap-0123456789ab); "
                "list_workspace_snapshots shows them",
                tool="workspace_restore",
            )
        handle = await sessions.write_handle(context, tool_name="workspace_restore")
        status = await _git(handle, ["git", "status", "--porcelain"], timeout_s=20.0)
        if status.exit_code != 0:
            raise _snapshot_failure(status, "workspace_restore")
        if status.stdout.strip() and not TOOL_CALL_CONFIRMED.get():
            # The registry already holds this tool for confirmation, so this is the second lock:
            # a path that reaches the handler without the person's approval (a future caller, a
            # profile bug) still cannot discard uncommitted work.
            raise ToolExecutionError(
                "the workspace has uncommitted changes and restoring would discard them; "
                "this needs the user's confirmation",
                tool="workspace_restore",
            )
        # Fail closed: if the current state cannot be saved, nothing is restored.
        safety_id = await _take_snapshot(handle, f"automatic: before restoring {snapshot_id}", "workspace_restore")
        result = await _snapshot_git(handle, _RESTORE_SCRIPT, snapshot_id, timeout_s=120.0)
        if result.exit_code != 0:
            raise _snapshot_failure(result, "workspace_restore")
        after = await _git(handle, ["git", "status", "--short"], timeout_s=20.0)
        changed = [line for line in after.stdout.splitlines() if line][:20]
        return (
            f"Restored the workspace to snapshot {snapshot_id}. The state it replaced was saved as "
            f"{safety_id}; workspace_restore {safety_id} undoes this.\n"
            + ("Differences from HEAD now:\n" + "\n".join(changed) if changed else "The workspace matches HEAD.")
        )

    tools: list[Tool] = [
        write_file, edit_file, write_files, run_command, git_diff, git_commit, git_checkout_branch,
        git_status, workspace_snapshot, list_workspace_snapshots, workspace_restore,
    ]
    if host_git is not None:
        tools.append(git_push)
    if workspace_manager is not None:
        tools += [open_workspace, list_workspaces, destroy_workspace]
    # ADR-042: structured lint / format checks, in the same workspace run_command uses.
    tools += build_devtools(sandbox_runner, sessions)
    if desktop_ops and host_agent_registry is not None:
        tools += build_desktop_ops_tools(sandbox_runner, sessions, host_agent_registry)
    if code_host_resolver is None:
        return tools

    @tool(
        "clone_repository",
        "Copy a GitHub repository's text files into the workspace so you can explore, search, "
        "build or test them with run_command. Use it when asked to clone, analyse, review or "
        "work on a repository. Binaries and oversized files are skipped, up to a size and "
        "count budget, and every skip is reported.",
        param("repo", "string",
              "Repository to copy, as 'owner/name' or its GitHub URL.", max_length=400,
              format="github_repo"),
        param("ref", "string", "Branch, tag, or commit SHA. Omit for the default branch.",
              required=False, max_length=200),
        param("destination", "string",
              "Workspace subdirectory to copy into. Defaults to the repository's name.",
              required=False, max_length=200),
        category=ToolCategory.DEVELOPMENT,
        sensitivity=ToolSensitivity.CONFIDENTIAL,
        side_effecting=True,
        required_capability=_ENGINEER_CAPABILITY,
        timeout_s=300.0,
        max_result_chars=20_000,
        # Everything this pulls in is third-party repository content -- exactly as
        # attacker-influenceable as fetch_url's page bodies or run_command's output.
        tags=("untrusted_content", NETWORK_TAG),
    )
    async def clone_repository(
        repo: str, context: ToolContext, ref: str | None = None, destination: str | None = None,
    ) -> str:
        """A real ``git clone`` where host-side git is wired, and the old REST copy where it is not.

        The credential is why this is not simply run inside the container. Host-side git gives
        the workspace a complete checkout -- history, branches, tags -- while the token stays in
        the gateway (``adapters/host_git.py``); running git in the container would put a secret
        next to model-chosen shell commands operating on an attacker-controlled repository.

        Without ``host_git`` the original behaviour remains: files copied one by one through the
        already-authenticated code-host client, capped, with no ``.git``. That is a genuinely
        weaker result -- no history, no push -- and it says so in what it returns rather than
        leaving the agent to discover that ``git log`` is empty.
        """
        slug = _strict_slug(repo, "clone_repository")
        _check_ref(ref, "ref", "clone_repository")
        subdir = _workspace_subdir(destination, slug, "clone_repository")
        client = await code_host_resolver(context.user_id)
        if client is None:
            raise ToolExecutionError(
                "GitHub is not connected for this user -- connect an account via "
                "/api/v3/integrations/github/connect, or ask an operator to configure "
                "AURA_GITHUB_TOKEN, before cloning a repository",
                tool="clone_repository",
            )

        if host_git is not None and sessions.durable:
            return await _host_git_clone(context, slug, subdir, ref, destination)
        try:
            tree = await client.list_files(slug, ref=ref)
        except Exception as exc:
            raise ToolExecutionError(
                f"could not list {slug}{'@' + ref if ref else ''}: {exc}",
                tool="clone_repository",
            ) from exc
        if not tree.files:
            raise ToolExecutionError(
                f"{slug}{'@' + ref if ref else ''} has no files to copy (empty repository, "
                "wrong ref, or no access to it)",
                tool="clone_repository",
            )

        root = subdir
        selected: list[Any] = []
        skipped_binary = skipped_large = skipped_budget = 0
        pdf_names: list[str] = []
        total_bytes = 0
        for entry in tree.files:
            if _is_pdf_path(entry.path):
                pdf_names.append(entry.path)
            elif _is_binary_path(entry.path):
                skipped_binary += 1
            elif entry.size > _CLONE_MAX_FILE_BYTES:
                skipped_large += 1
            elif (
                len(selected) >= _CLONE_MAX_FILES
                or total_bytes + entry.size > _CLONE_MAX_TOTAL_BYTES
            ):
                skipped_budget += 1
            else:
                selected.append(entry)
                total_bytes += entry.size

        handle = await sessions.write_handle(context, tool_name="clone_repository")
        semaphore = asyncio.Semaphore(_CLONE_CONCURRENCY)
        failed: list[str] = []
        skipped_nontext: list[str] = []
        get_bytes = getattr(client, "get_file_bytes", None)

        async def _copy(entry: Any) -> None:
            async with semaphore:
                try:
                    if get_bytes is not None:
                        raw = await get_bytes(slug, entry.path, ref=ref)
                        if b"\x00" in raw[:8192]:
                            skipped_nontext.append(entry.path)
                            return
                        try:
                            content = raw.decode("utf-8")
                        except UnicodeDecodeError:
                            # Writing replacement characters into the copy would be a quiet
                            # corruption of the file; say it was not copied instead.
                            skipped_nontext.append(entry.path)
                            return
                    else:
                        content = await client.get_file_contents(slug, entry.path, ref=ref)
                    await sandbox_runner.write_file(handle, f"{root}/{entry.path}", content)
                except Exception as exc:  # one unreadable file must not lose the whole clone
                    failed.append(f"{entry.path}: {exc}"[:200])

        await asyncio.gather(*(_copy(entry) for entry in selected))

        copied = len(selected) - len(failed) - len(skipped_nontext)
        lines = [
            f"Copied {copied} file(s) from {slug}{'@' + ref if ref else ''} into "
            f"./{root} in the workspace.",
            "Explore it with run_command (e.g. 'ls -R {0}', 'cat {0}/README.md').".format(root),
        ]
        if (
            skipped_binary or skipped_large or skipped_budget or failed or tree.truncated
            or pdf_names or skipped_nontext
        ):
            lines.append(
                "Snapshot is partial -- do not conclude a file is absent from the repository "
                "because it is absent here:"
            )
        if skipped_binary:
            lines.append(f"- {skipped_binary} binary file(s) skipped")
        if pdf_names:
            shown_pdfs = ", ".join(pdf_names[:8]) + (", ..." if len(pdf_names) > 8 else "")
            lines.append(
                f"- {len(pdf_names)} PDF file(s) not copied (the copy is text-only): {shown_pdfs}. "
                "Read any of them in full with github_get_file_contents (every page is returned)."
            )
        if skipped_nontext:
            lines.append(
                f"- {len(skipped_nontext)} file(s) are not UTF-8 text and were not copied: "
                f"{', '.join(skipped_nontext[:5])}"
            )
        if skipped_large:
            lines.append(
                f"- {skipped_large} file(s) over {_CLONE_MAX_FILE_BYTES // 1000}kB skipped"
            )
        if skipped_budget:
            lines.append(
                f"- {skipped_budget} file(s) skipped after the {_CLONE_MAX_FILES}-file / "
                f"{_CLONE_MAX_TOTAL_BYTES // 1_000_000}MB budget was reached"
            )
        if failed:
            lines.append(f"- {len(failed)} file(s) failed to copy: {'; '.join(failed[:5])}")
        if tree.truncated:
            lines.append("- the repository's file listing was truncated by GitHub")
        return "\n".join(lines)

    tools.append(clone_repository)
    return tools


# --------------------------------------------------------------------------------------
# GitHub
# --------------------------------------------------------------------------------------


#: Characters of a file per ``github_get_file_contents`` result: under the tool's 40,000-character
#: result cap with room for the headers, so a part is never cut by the registry and the model is
#: never told "truncated" with no way to get the rest.
_FILE_PAGE_CHARS = 36_000
#: OCR allowance for a scanned PDF read through ``github_get_file_contents`` (tool timeout 150 s).
_GITHUB_PDF_OCR_BUDGET_S = 100.0
#: Parsed files, so reading a long PDF part by part (one call per ``offset``) extracts it once.
_FILE_TEXT_CACHE: "OrderedDict[str, tuple[str, str]]" = OrderedDict()
_FILE_TEXT_CACHE_SIZE = 4


async def _file_text(data: bytes, path: str) -> tuple[str, str]:
    """``(text, lead)`` for a file's bytes: a PDF's full text, or a text file's text.

    ``lead`` is a one-line description (with any pages that could not be read) for a PDF, empty for
    text. A binary file that is not a PDF is refused with a message saying so, rather than returned
    as the replacement characters it would decode to.
    """
    digest = hashlib.sha256(data).hexdigest()
    cached = _FILE_TEXT_CACHE.get(digest)
    if cached is not None:
        _FILE_TEXT_CACHE.move_to_end(digest)
        return cached
    if len(data) > MAX_PDF_BYTES:
        raise ToolExecutionError(
            f"{path} is {len(data)} bytes, larger than the {MAX_PDF_BYTES // (1024 * 1024)} MiB this "
            "tool reads",
            tool="github_get_file_contents",
        )
    try:
        read = await asyncio.to_thread(
            read_source_bytes, data, name=path, ocr_time_budget_s=_GITHUB_PDF_OCR_BUDGET_S
        )
    except SourceReadError as exc:
        raise ToolExecutionError(
            f"{exc} It is {len(data)} bytes; fetch it another way if you need its raw content.",
            tool="github_get_file_contents",
        ) from exc
    lead = ""
    if read.kind == "pdf":
        info = read.describe()
        lead = f"[PDF {path}: {info['pages']} page(s), {info['pages_read']} read, {len(read.text)} characters of text]"
        warnings = read.warnings()
        if warnings:
            lead += "\n" + "\n".join(f"[{warning}]" for warning in warnings)
        if not read.text:
            lead += "\n[no text could be extracted from this PDF]"
    if read.kind == "text":
        try:
            data.decode("utf-8-sig")
        except UnicodeDecodeError:
            lead = "[note: this file is not valid UTF-8; it is shown decoded as Latin-1]"
    result = (read.text, lead)
    _FILE_TEXT_CACHE[digest] = result
    while len(_FILE_TEXT_CACHE) > _FILE_TEXT_CACHE_SIZE:
        _FILE_TEXT_CACHE.popitem(last=False)
    return result


def build_github_tools(client_resolver: Any, *, default_repo: str = "") -> list[Tool]:
    """Read/write GitHub tools. Every write is ``side_effecting`` at ``CONFIDENTIAL``
    sensitivity, which ``ToolSpec.__post_init__`` already forces into
    ``requires_confirmation`` -- ``create_draft_pull_request`` always opens as a draft, so a
    confirmed call still leaves a human to mark it ready.

    ``client_resolver`` is ``Container._build_github_client_resolver``'s callable
    ``(UserId) -> Awaitable[GitHubClient | None]``, called with ``context.user_id`` on every
    invocation rather than closing over one fixed client -- whether that resolves to a
    deployment-wide admin token or the calling user's own OAuth-connected identity is a
    container-wiring decision these tools don't need to know about. ``None`` means the caller has
    not connected GitHub (or the admin token disappeared mid-process); every tool below surfaces
    that as a clear, actionable failure rather than a generic API error.
    """

    async def _client(context: ToolContext) -> Any:
        client = await client_resolver(context.user_id)
        if client is None:
            raise ToolExecutionError(
                "GitHub is not connected for this user -- connect an account via "
                "/api/v3/integrations/github/connect, or ask an operator to configure "
                "AURA_GITHUB_TOKEN, before using GitHub tools",
                tool="github",
            )
        return client

    def _repo(explicit: str | None) -> str:
        given = explicit or default_repo
        if not given:
            raise ToolExecutionError(
                "no repo given and no default configured "
                "(AURA_GITHUB_DEFAULT_OWNER/AURA_GITHUB_DEFAULT_REPO)",
                tool="github",
            )
        return _strict_slug(given, "github")

    _repo_param = param(
        "repo", "string",
        "Repository as 'owner/name' or its GitHub URL. Omit to use the configured default.",
        required=False, max_length=400, format="github_repo",
    )

    @tool(
        "github_list_repository_files",
        "List the file paths in a GitHub repository, without a sandbox. Use it first when asked "
        "to explore, analyse or review a repository: it turns a bare repository URL into paths "
        "that github_get_file_contents can read.",
        _repo_param,
        param("ref", "string", "Branch, tag, or commit SHA. Omit for the default branch.",
              required=False, max_length=200),
        param("path_prefix", "string",
              "Only list files under this directory, e.g. 'src/'. Omit for the whole repository.",
              required=False, max_length=300),
        category=ToolCategory.DEVELOPMENT,
        sensitivity=ToolSensitivity.CONFIDENTIAL,
        required_capability=_ENGINEER_CAPABILITY,
        timeout_s=30.0,
        max_result_chars=40_000,
        # Path names are repository-controlled text, same as file contents.
        tags=("untrusted_content", NETWORK_TAG),
    )
    async def github_list_repository_files(
        context: ToolContext, repo: str | None = None, ref: str | None = None,
        path_prefix: str | None = None,
    ) -> str:
        client = await _client(context)
        _check_ref(ref, "ref", "github_list_repository_files")
        tree = await client.list_files(_repo(repo), ref=ref)
        prefix = (path_prefix or "").lstrip("/")
        files = [f for f in tree.files if not prefix or f.path.startswith(prefix)]
        if not files:
            return (
                f"No files{' under ' + prefix if prefix else ''} in {_repo(repo)}"
                f"{'@' + ref if ref else ''}."
                + (
                    " (GitHub truncated this repository's file listing, so a file may exist that "
                    "is not listed)"
                    if tree.truncated else ""
                )
            )
        shown = files[:_LIST_FILES_MAX_ENTRIES]
        lines = [f"{f.path} ({f.size} bytes)" for f in shown]
        if len(files) > len(shown):
            lines.append(
                f"... and {len(files) - len(shown)} more file(s) not shown; narrow with "
                "path_prefix"
            )
        if tree.truncated:
            lines.append("(GitHub truncated this repository's file listing -- it is incomplete)")
        return "\n".join(lines)

    @tool(
        "github_get_file_contents",
        "Read one file from a GitHub repository, without a sandbox. A PDF is read in full (every "
        "page; scanned pages are named). A long file arrives in parts: continue with `offset` "
        "as the result says.",
        param("path", "string", "File path within the repo.", max_length=500),
        _repo_param,
        param("ref", "string", "Branch, tag, or commit SHA. Omit for the default branch.",
              required=False, max_length=200),
        param("offset", "integer", "Where to continue a long file, from the previous result's "
              "note. Default 0.", required=False, default=0, minimum=0),
        category=ToolCategory.DEVELOPMENT,
        sensitivity=ToolSensitivity.CONFIDENTIAL,
        required_capability=_ENGINEER_CAPABILITY,
        timeout_s=150.0,
        max_result_chars=40_000,
        tags=("untrusted_content", NETWORK_TAG),
    )
    async def github_get_file_contents(
        path: str, context: ToolContext, repo: str | None = None, ref: str | None = None,
        offset: int = 0,
    ) -> str:
        client = await _client(context)
        _check_ref(ref, "ref", "github_get_file_contents")
        slug = _repo(repo)
        get_bytes = getattr(client, "get_file_bytes", None)
        if get_bytes is None:
            text, lead = await client.get_file_contents(slug, path, ref=ref), ""
        else:
            data = await get_bytes(slug, path, ref=ref)
            text, lead = await _file_text(bytes(data), path)
        total = len(text)
        if offset > total or (offset == total and total):
            raise ToolExecutionError(
                f"offset {offset} is at or past the end of {path} ({total} characters)",
                tool="github_get_file_contents",
            )
        end = min(total, offset + _FILE_PAGE_CHARS)
        if offset == 0 and end == total and not lead:
            return text
        chunk = text[offset:end]
        lines = [lead] if lead else []
        if end < total or offset:
            lines.append(
                f"[characters {offset}-{end} of {total}"
                + (f"; call again with offset={end} to continue]" if end < total else "; end of file]")
            )
        return "\n".join([*lines, chunk])

    @tool(
        "github_get_issue",
        "Read a GitHub issue's title, state and description.",
        param("number", "integer", "Issue number.", minimum=1),
        _repo_param,
        category=ToolCategory.DEVELOPMENT,
        sensitivity=ToolSensitivity.CONFIDENTIAL,
        required_capability=_ENGINEER_CAPABILITY,
        timeout_s=20.0,
        max_result_chars=10_000,
        tags=("untrusted_content", NETWORK_TAG),
    )
    async def github_get_issue(number: int, context: ToolContext, repo: str | None = None) -> str:
        client = await _client(context)
        return await client.get_issue(_repo(repo), int(number))

    @tool(
        "github_get_pull_request",
        "Read a GitHub pull request's title, state, head and base branches, and description.",
        param("number", "integer", "Pull request number.", minimum=1),
        _repo_param,
        category=ToolCategory.DEVELOPMENT,
        sensitivity=ToolSensitivity.CONFIDENTIAL,
        required_capability=_ENGINEER_CAPABILITY,
        timeout_s=20.0,
        max_result_chars=10_000,
        tags=("untrusted_content", NETWORK_TAG),
    )
    async def github_get_pull_request(
        number: int, context: ToolContext, repo: str | None = None
    ) -> str:
        client = await _client(context)
        return await client.get_pull_request(_repo(repo), int(number))

    @tool(
        "github_create_branch",
        "Create a new branch in a GitHub repository, without a sandbox, starting from an existing branch, tag or commit.",
        param("branch", "string", "New branch name.", max_length=200),
        param("from_ref", "string", "Existing branch, tag or commit to start from, e.g. 'main'.", max_length=200),
        _repo_param,
        category=ToolCategory.DEVELOPMENT,
        sensitivity=ToolSensitivity.CONFIDENTIAL,
        side_effecting=True,
        required_capability=_ENGINEER_CAPABILITY,
        timeout_s=20.0,
        tags=(NETWORK_TAG,),
    )
    async def github_create_branch(
        branch: str, from_ref: str, context: ToolContext, repo: str | None = None
    ) -> str:
        client = await _client(context)
        new_branch = branch.strip().removeprefix("refs/heads/")
        base_ref = from_ref.strip().removeprefix("refs/heads/")
        if not _valid_ref_name(new_branch):
            raise ToolExecutionError(
                f"{branch!r} is not a valid branch name", tool="github_create_branch"
            )
        if not _valid_ref_name(base_ref):
            raise ToolExecutionError(
                f"from_ref {from_ref!r} is not a valid branch name (it must be an existing "
                "branch, e.g. 'main')",
                tool="github_create_branch",
            )
        return await client.create_branch(_repo(repo), branch=new_branch, from_ref=base_ref)

    # D6: caps how much diff text a single confirmation prompt ever carries -- mirrors
    # run_command's max_result_chars / git_diff's own cap (this file's other size-cap
    # precedents), so a bulk commit across many/large files cannot balloon a confirmation
    # response instead of just truncating with a clear marker.
    _MAX_DIFF_PREVIEW_CHARS = 20_000
    _COMMIT_MAX_FILES = 100
    _COMMIT_MAX_CHARS = 3_000_000

    async def _preview_github_commit_files(arguments: Mapping[str, Any], context: ToolContext) -> str | None:
        """D6: a unified diff of what `github_commit_files` would change, shown alongside the
        confirmation prompt this CONFIDENTIAL (non-workspace_local) tool already requires. Reads
        each target file's *current* content from the forge via the same
        `CodeHostClient.get_file_contents` port method already wired here -- a read failure
        (typically "not found") is treated as a new file, empty before-text, matching `git diff`'s
        own behavior for an added file.
        """
        try:
            client = await _client(context)
        except ToolExecutionError:
            return None  # no GitHub connection to diff against -- confirmation proceeds without one
        try:
            repo = _repo(arguments.get("repo") if isinstance(arguments.get("repo"), str) else None)
        except ToolExecutionError:
            return None
        branch = str(arguments.get("branch") or "")
        files = arguments.get("files")
        if not isinstance(files, Mapping):
            return None

        parts: list[str] = []
        total = 0
        remaining = len(files)
        for path, new_content in files.items():
            remaining -= 1
            if _is_pdf_path(str(path)) or _is_binary_path(str(path)):
                # A diff of a PDF's bytes is noise, and this call is going to be refused anyway
                # (it writes text only): say so where the person is deciding.
                parts.append(
                    f"--- a/{path}\n+++ b/{path}\n"
                    "(binary file type: github_commit_files writes text only and will refuse this)\n"
                )
                continue
            try:
                before = await client.get_file_contents(repo, str(path), ref=branch)
            except Exception:
                before = ""
            block = "".join(difflib.unified_diff(
                before.splitlines(keepends=True),
                str(new_content).splitlines(keepends=True),
                fromfile=f"a/{path}", tofile=f"b/{path}",
            ))
            if total + len(block) > _MAX_DIFF_PREVIEW_CHARS:
                parts.append(f"... diff truncated ({remaining + 1} more file(s) not shown)")
                break
            parts.append(block)
            total += len(block)
        return "\n".join(parts) if parts else None

    @tool(
        "github_commit_files",
        "Commit one or more files to a feature branch of a GitHub repository as a single "
        "commit. The default branch is refused: open a pull request instead.",
        param("branch", "string", "Branch to commit to.", max_length=200),
        param("message", "string", "Commit message.", max_length=2000),
        param("files", "object", "Mapping of file path to its full new text content."),
        _repo_param,
        category=ToolCategory.DEVELOPMENT,
        sensitivity=ToolSensitivity.CONFIDENTIAL,
        side_effecting=True,
        required_capability=_ENGINEER_CAPABILITY,
        timeout_s=60.0,
        preview=_preview_github_commit_files,
        tags=(NETWORK_TAG,),
    )
    async def github_commit_files(
        branch: str, message: str, files: Mapping[str, str], context: ToolContext,
        repo: str | None = None,
    ) -> str:
        client = await _client(context)
        slug = _repo(repo)
        if not isinstance(files, Mapping) or not files:
            raise ToolExecutionError(
                "files must be a non-empty object mapping each file path to its full new text",
                tool="github_commit_files",
            )
        if len(files) > _COMMIT_MAX_FILES:
            raise ToolExecutionError(
                f"at most {_COMMIT_MAX_FILES} files per commit (got {len(files)}); split the "
                "change into several commits",
                tool="github_commit_files",
            )
        total_chars = 0
        for file_path, text in files.items():
            if not isinstance(file_path, str) or not file_path.strip():
                raise ToolExecutionError(
                    "every key of files must be a non-empty file path", tool="github_commit_files"
                )
            if not isinstance(text, str):
                raise ToolExecutionError(
                    f"the content of {file_path!r} must be a string of text (got "
                    f"{type(text).__name__}); this tool commits text files only",
                    tool="github_commit_files",
                )
            _refuse_binary_path(file_path, "github_commit_files")
            total_chars += len(text)
        if total_chars > _COMMIT_MAX_CHARS:
            raise ToolExecutionError(
                f"the commit carries {total_chars} characters of content; the limit is "
                f"{_COMMIT_MAX_CHARS}. Split the change into several commits.",
                tool="github_commit_files",
            )
        if not message.strip():
            raise ToolExecutionError("commit message must not be empty", tool="github_commit_files")
        # D-07 / SEC-06, as `git_push` does: never the default branch. Changes reach it only
        # through a pull request a human merges. Compared on the bare name, so neither
        # `refs/heads/main` nor stray whitespace slips past it.
        target = branch.strip().removeprefix("refs/heads/")
        if not _valid_ref_name(target):
            raise ToolExecutionError(
                f"{branch!r} is not a valid branch name", tool="github_commit_files"
            )
        default_branch = await client.default_branch(slug)
        if target == default_branch:
            raise ToolExecutionError(
                f"refusing to commit to {default_branch!r}, the default branch. Create a feature "
                "branch with github_create_branch and open a pull request from it.",
                tool="github_commit_files",
            )
        return await client.commit_files(slug, branch=target, message=message, files=files)

    @tool(
        "github_create_draft_pull_request",
        "Open a draft pull request in a GitHub repository. It is always a draft, never ready "
        "for review: a human decides when it can merge.",
        param("branch", "string", "Head branch (the one with the changes).", max_length=200),
        param("base", "string", "Base branch to merge into, e.g. 'main'.", max_length=200),
        param("title", "string", "Pull request title.", max_length=300),
        param("body", "string", "Pull request description.", max_length=10_000),
        _repo_param,
        category=ToolCategory.DEVELOPMENT,
        sensitivity=ToolSensitivity.CONFIDENTIAL,
        side_effecting=True,
        required_capability=_ENGINEER_CAPABILITY,
        timeout_s=20.0,
        tags=(NETWORK_TAG,),
    )
    async def github_create_draft_pull_request(
        branch: str, base: str, title: str, body: str, context: ToolContext,
        repo: str | None = None,
    ) -> str:
        client = await _client(context)
        head = branch.strip().removeprefix("refs/heads/")
        base_branch = base.strip().removeprefix("refs/heads/")
        if not title.strip():
            raise ToolExecutionError("title must not be empty", tool="github_create_draft_pull_request")
        if head == base_branch:
            raise ToolExecutionError(
                f"the head branch and the base are both {head!r}; a pull request needs changes on "
                "a different branch (create one with github_create_branch and commit to it)",
                tool="github_create_draft_pull_request",
            )
        return await client.create_draft_pull_request(
            _repo(repo), branch=head, base=base_branch, title=title, body=body
        )

    return [
        github_list_repository_files, github_get_file_contents, github_get_issue,
        github_get_pull_request, github_create_branch, github_commit_files,
        github_create_draft_pull_request,
    ]


# --------------------------------------------------------------------------------------
# MCP bridging
# --------------------------------------------------------------------------------------

_NAME_OK = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_")


def _sanitize_name_part(value: str) -> str:
    """Tool names are pinned to lowercase snake_case (``ToolSpec._NAME_OK``); an MCP server or
    tool name is arbitrary text an operator configured, not guaranteed to already conform."""
    lowered = value.strip().lower()
    return "".join(c if c in _NAME_OK else "_" for c in lowered) or "tool"


_SCHEMA_TYPES = frozenset({"string", "number", "integer", "boolean", "array", "object"})


def _schema_type(prop: Mapping[str, Any]) -> str:
    """One supported JSON type for a property schema, whatever shape the server declared it in.

    ``"type"`` may be a string, a list (``["string", "null"]`` -- the usual spelling of an optional
    field) or absent in favour of ``anyOf``/``oneOf``. A list used to be tested for membership in a
    set, which raises ``TypeError: unhashable type`` and took down the registration of *every* tool
    from every server, not just the one with the odd schema.
    """
    declared = prop.get("type")
    candidates: list[Any] = list(declared) if isinstance(declared, (list, tuple)) else [declared]
    for key in ("anyOf", "oneOf"):
        variants = prop.get(key)
        if isinstance(variants, (list, tuple)):
            candidates += [v.get("type") for v in variants if isinstance(v, Mapping)]
    for candidate in candidates:
        if isinstance(candidate, str) and candidate in _SCHEMA_TYPES:
            return candidate
    return "string"


def _params_from_json_schema(schema: Mapping[str, Any]) -> list[ParameterSpec]:
    properties = schema.get("properties") if isinstance(schema, Mapping) else None
    if not isinstance(properties, Mapping):
        properties = {}
    required_raw = schema.get("required") if isinstance(schema, Mapping) else None
    required = set(required_raw) if isinstance(required_raw, (list, tuple)) else set()
    params: list[ParameterSpec] = []
    for name, raw_prop in properties.items():
        prop = raw_prop if isinstance(raw_prop, Mapping) else {}
        json_type = _schema_type(prop)
        item_type = None
        if json_type == "array":
            items = prop.get("items")
            # An array of integers or objects used to be declared as an array of strings, and the
            # validator then rewrote every number into a string (and refused every object) before
            # the call reached the server.
            item_type = _schema_type(items) if isinstance(items, Mapping) and items else "string"
        params.append(
            param(
                str(name),
                json_type,
                str(prop.get("description") or name),
                required=name in required,
                item_type=item_type,
            )
        )
    return params


class _McpTool:
    """A bridged MCP tool.

    Not a ``FunctionTool`` on purpose: that wrapper injects the tool context as a ``context``
    keyword argument, so an MCP tool with a parameter *named* ``context`` (not unusual) had the
    caller's value overwritten and never reached the server. Here the validated arguments go to the
    server exactly as given.
    """

    def __init__(self, spec: ToolSpec, mcp_client: Any, server: str, name: str) -> None:
        self._spec = spec
        self._client = mcp_client
        self._server = server
        self._name = name

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    async def execute(self, arguments: Mapping[str, Any], context: ToolContext) -> str:
        try:
            result = await self._client.call_tool(self._server, self._name, dict(arguments))
        except ToolExecutionError:
            raise
        except Exception as exc:
            raise ToolExecutionError(
                f"{self._spec.name} failed: {str(exc) or type(exc).__name__}",
                tool=self._spec.name,
                cause=exc,
            ) from exc
        return result if isinstance(result, str) else str(result)


async def build_mcp_bridge_tools(
    mcp_client: Any,
    *,
    safe_tools: Sequence[str] = (),
    collisions: list[dict[str, str]] | None = None,
) -> list[Tool]:
    """Discover every tool every configured MCP server advertises and wrap each as a tool.

    Default-deny: a discovered tool is ``sensitivity=CONFIDENTIAL, side_effecting=True`` (so
    ``ToolPolicy`` always requires confirmation) unless its fully qualified name
    (``mcp_{server}_{tool}``) is explicitly listed in ``safe_tools`` (``AURA_MCP_SAFE_TOOLS``),
    in which case it registers as ``PERSONAL``/non-side-effecting instead. An MCP server an
    operator has not individually reviewed is therefore inert-but-confirmable, never silently
    trusted, regardless of what the server itself claims about its own tools.

    If ``collisions`` is given, each skipped descriptor whose sanitised name collided with an
    earlier tool appends ``{"tool", "server", "mcp_name", "collides_with_server",
    "collides_with_name"}`` so callers (startup logging, tests) can report both sides.
    """
    safe = frozenset(safe_tools)
    descriptors: Sequence[McpToolDescriptor] = await mcp_client.list_tools()
    tools: list[Tool] = []
    taken: dict[str, McpToolDescriptor] = {}
    for descriptor in descriptors:
        qualified_name = (
            f"mcp_{_sanitize_name_part(descriptor.server)}_{_sanitize_name_part(descriptor.name)}"
        )
        # `a-b` and `a_b` sanitise to the same name (as do server `a` + tool `b_c` and server
        # `a_b` + tool `c`); the registry refuses a duplicate, and one odd name must not cost
        # the operator every other tool.
        winner = taken.get(qualified_name)
        if winner is not None:
            record = {
                "tool": qualified_name,
                "server": descriptor.server,
                "mcp_name": descriptor.name,
                "collides_with_server": winner.server,
                "collides_with_name": winner.name,
            }
            if collisions is not None:
                collisions.append(record)
            logger.warning(
                "MCP tool skipped: its sanitised name collides with another tool's",
                extra=record,
            )
            continue
        taken[qualified_name] = descriptor
        is_safe = qualified_name in safe
        try:
            params = tuple(_params_from_json_schema(descriptor.input_schema))
            spec = ToolSpec(
                name=qualified_name,
                description=f"[MCP:{descriptor.server}] {descriptor.description}"[:2000] or (
                    f"MCP tool {descriptor.name!r} from server {descriptor.server!r}."
                ),
                parameters=params,
                category=ToolCategory.DEVELOPMENT,
                sensitivity=ToolSensitivity.PERSONAL if is_safe else ToolSensitivity.CONFIDENTIAL,
                side_effecting=not is_safe,
                required_capability=_ENGINEER_CAPABILITY,
                timeout_s=30.0,
                max_result_chars=20_000,
                tags=(NETWORK_TAG,) if is_safe else ("untrusted_content", NETWORK_TAG),
                output_schema=_supported_output_schema(descriptor),
            )
        except (ValueError, TypeError) as exc:
            logger.warning(
                "MCP tool skipped: its declaration is not usable",
                extra={"tool": qualified_name, "server": descriptor.server, "error": str(exc)},
            )
            continue
        tools.append(_McpTool(spec, mcp_client, descriptor.server, descriptor.name))
    return tools


def _supported_output_schema(descriptor: McpToolDescriptor) -> Mapping[str, Any] | None:
    """The server's ``outputSchema`` when it fits the subset AURA checks (ADR-025), else ``None``.

    A schema outside the subset is dropped -- with a log line -- rather than trusted unchecked:
    declaring a constraint nobody enforces would be worse than declaring none.
    """
    schema = descriptor.output_schema
    if not schema:
        return None
    try:
        check_output_schema(schema)
        if schema.get("type") not in ("object", "array"):
            raise ValidationError("top-level type must be object or array")
    except ValidationError:
        logger.info(
            "MCP output schema outside the supported subset; result left unchecked",
            extra={"server": descriptor.server, "tool": descriptor.name},
        )
        return None
    return dict(schema)
