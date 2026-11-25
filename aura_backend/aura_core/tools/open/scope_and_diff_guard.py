"""Scope, size and secret checks over a unified diff.

Review notes (what this file used to get wrong, so the next reader does not reintroduce it):

* Paths were normalised with ``str.lstrip("./")`` / ``lstrip("b/")``, which strips *characters*, not a
  prefix: ``.env`` became ``env`` (so the forbidden-file list never matched it) and ``b/backend/x.py``
  became ``ackend/x.py``. :func:`_norm_path` and :func:`_strip_prefix` do it properly.
* Only ``+++ b/<path>`` lines were read, so a deleted file, a pure rename and a binary change were
  invisible to the scope check. The ``diff --git`` header is now the primary source of paths.
* ``strip()`` was applied to every diff line, so a context line such as `` +x`` was counted as an
  addition, and a removed line ``-- comment`` (shown as ``--- comment``) was skipped as a header.
  Hunk headers carry the line counts, so lines inside a hunk are now classified by position.
* An empty or unparseable diff was reported as ``APPROVED_SCOPE``.

Only text is accepted. A ``bytes`` argument is read through ``_input`` (a PDF is read in full) and a
PDF is then refused with a clear error, because it is not a diff.
"""

import fnmatch
import posixpath
import re
import textwrap
from typing import Any, Dict, Iterator, List, Optional, Tuple

from ._input import SourceReadError, read_source_bytes


class DiffInputError(ValueError):
    """The diff argument is not usable text (wrong type, or a document that is not a diff)."""


_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_MAX_SCAN_LINE = 20_000


def coerce_diff_text(value: Any) -> str:
    """``str`` -> itself, ``None`` -> ``""``, ``bytes`` -> decoded text. PDFs are read in full, then refused."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (bytes, bytearray)):
        try:
            read = read_source_bytes(bytes(value), name="the diff input")
        except SourceReadError as exc:
            raise DiffInputError(str(exc)) from exc
        if read.kind == "pdf":
            raise DiffInputError(
                f"The input is a PDF ({read.pages} page(s), read in full) - a PDF is not a unified diff. "
                "Pass the diff text instead."
            )
        return read.text
    raise DiffInputError(f"diff_text must be a string, got {type(value).__name__}.")


def _unquote_git(path: str) -> str:
    """Undo git's C-style quoting (``"\\330\\247"`` for non-ASCII names, ``\\"``, ``\\\\``, ``\\t``)."""
    if len(path) >= 2 and path.startswith('"') and path.endswith('"'):
        body = path[1:-1]
        raw = bytearray()
        i = 0
        simple = {"n": 10, "t": 9, "r": 13, '"': 34, "\\": 92, "a": 7, "b": 8, "f": 12, "v": 11}
        while i < len(body):
            ch = body[i]
            if ch == "\\" and i + 1 < len(body):
                nxt = body[i + 1]
                if re.fullmatch(r"[0-7]{3}", body[i + 1 : i + 4]):
                    raw.append(int(body[i + 1 : i + 4], 8) & 0xFF)
                    i += 4
                    continue
                if nxt in simple:
                    raw.append(simple[nxt])
                    i += 2
                    continue
            raw.extend(ch.encode("utf-8"))
            i += 1
        return raw.decode("utf-8", errors="replace")
    return path


def _norm_path(path: str) -> str:
    """Normalise to a clean POSIX relative path. ``/dev/null`` is preserved; ``.env`` stays ``.env``."""
    p = path.strip().replace("\\", "/")
    if not p or p == "/dev/null":
        return p
    n = posixpath.normpath(p)
    return "" if n == "." else n


def _strip_prefix(path: str, side: str) -> str:
    """Remove git's ``a/`` or ``b/`` marker (one prefix, not a character class)."""
    path = path.strip()
    if not path.startswith('"'):
        path = path.split("\t")[0]  # ``diff -u`` appends a timestamp after a tab
    path = _unquote_git(path)
    if path.startswith(side + "/"):
        return path[2:]
    return path


def _split_git_header(rest: str) -> Tuple[str, str]:
    """``a/X b/Y`` -> (X, Y). Handles quoted names and the common ``a/P b/P`` form with spaces."""
    rest = rest.strip()
    if rest.startswith('"'):
        m = re.match(r'^("(?:[^"\\]|\\.)*")\s+(.+)$', rest)
        if m:
            return _strip_prefix(m.group(1), "a"), _strip_prefix(m.group(2), "b")
    positions = [m.start() for m in re.finditer(r" b/", rest)]
    for pos in positions:
        a, b = rest[:pos], rest[pos + 1 :]
        if _strip_prefix(a, "a") == _strip_prefix(b, "b"):
            return _strip_prefix(a, "a"), _strip_prefix(b, "b")
    if positions:
        pos = positions[0]
        return _strip_prefix(rest[:pos], "a"), _strip_prefix(rest[pos + 1 :], "b")
    parts = rest.split()
    if len(parts) >= 2:
        return _strip_prefix(parts[0], "a"), _strip_prefix(parts[-1], "b")
    return "", ""


def _lines(diff_text: str) -> List[str]:
    # ``splitlines`` would also split on \x0b, \x0c, \x1c-\x1e, \x85 and U+2028/9, which are legal
    # *inside* a diff line, so a crafted line could forge a header. Split on \n only.
    text = textwrap.dedent(diff_text)
    return [ln[:-1] if ln.endswith("\r") else ln for ln in text.split("\n")]


def _walk(diff_text: str) -> Iterator[Tuple[int, str, Any]]:
    """Yield ``(line_no, kind, payload)``. Kinds: git, old, new, hunk, add, del, ctx, meta."""
    old_left = new_left = 0
    for no, line in enumerate(_lines(diff_text), start=1):
        if old_left > 0 or new_left > 0:
            if line.startswith("\\"):
                yield no, "meta", ("no_newline", "")
                continue
            if line == "" or line[0] in " +-":
                if line.startswith("+"):
                    new_left -= 1
                    yield no, "add", line[1:]
                elif line.startswith("-"):
                    old_left -= 1
                    yield no, "del", line[1:]
                else:
                    old_left -= 1
                    new_left -= 1
                    yield no, "ctx", line[1:]
                continue
            old_left = new_left = 0  # truncated or malformed hunk: fall through to header handling
        m = _HUNK_RE.match(line)
        if m:
            old_left = int(m.group(2)) if m.group(2) is not None else 1
            new_left = int(m.group(4)) if m.group(4) is not None else 1
            yield no, "hunk", line
            continue
        if line.startswith("diff --git "):
            yield no, "git", _split_git_header(line[len("diff --git ") :])
        elif line.startswith("--- "):
            yield no, "old", _strip_prefix(line[4:], "a")
        elif line.startswith("+++ "):
            yield no, "new", _strip_prefix(line[4:], "b")
        elif line.startswith("rename from "):
            yield no, "meta", ("rename_from", _unquote_git(line[12:].strip()))
        elif line.startswith("rename to "):
            yield no, "meta", ("rename_to", _unquote_git(line[10:].strip()))
        elif line.startswith("copy from "):
            yield no, "meta", ("rename_from", _unquote_git(line[10:].strip()))
        elif line.startswith("copy to "):
            yield no, "meta", ("rename_to", _unquote_git(line[8:].strip()))
        elif line.startswith("new file mode"):
            yield no, "meta", ("new", "")
        elif line.startswith("deleted file mode"):
            yield no, "meta", ("deleted", "")
        elif line.startswith("similarity index"):
            yield no, "meta", ("similarity", "")
        elif line.startswith("Binary files ") or line.startswith("GIT binary patch"):
            yield no, "meta", ("binary", "")
        elif line.startswith("+"):
            yield no, "add", line[1:]  # header-less fragment
        elif line.startswith("-"):
            yield no, "del", line[1:]


def _blank_file(path: str = "unknown") -> Dict[str, Any]:
    return {
        "path": path,
        "old_path": None,
        "is_new": False,
        "is_deleted": False,
        "is_rename": False,
        "is_binary": False,
        "lines_added": 0,
        "lines_deleted": 0,
        "hunks_count": 0,
        "_old_seen": False,
        "_new_seen": False,
    }


def _parse(diff_text: str) -> List[Dict[str, Any]]:
    files: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None

    for _no, kind, payload in _walk(diff_text):
        if kind == "git":
            old, new = payload
            cur = _blank_file(_norm_path(new) or "unknown")
            cur["old_path"] = _norm_path(old) or None
            files.append(cur)
        elif kind in ("old", "new"):
            marker = "_old_seen" if kind == "old" else "_new_seen"
            if cur is None or cur[marker] or cur["hunks_count"] > 0:
                cur = _blank_file()
                files.append(cur)
            cur[marker] = True
            path = _norm_path(payload)
            if kind == "old":
                if path == "/dev/null":
                    cur["is_new"] = True
                else:
                    cur["old_path"] = path
                    if cur["path"] == "unknown":
                        cur["path"] = path or "unknown"
            else:
                if path == "/dev/null":
                    cur["is_deleted"] = True
                    if cur["old_path"]:
                        cur["path"] = cur["old_path"]
                else:
                    cur["path"] = path or cur["path"]
        elif cur is None and kind in ("add", "del", "hunk"):
            cur = _blank_file()
            files.append(cur)
        if cur is None:
            continue
        if kind == "hunk":
            cur["hunks_count"] += 1
        elif kind == "add":
            cur["lines_added"] += 1
        elif kind == "del":
            cur["lines_deleted"] += 1
        elif kind == "meta":
            tag, value = payload
            if tag == "new":
                cur["is_new"] = True
            elif tag == "deleted":
                cur["is_deleted"] = True
                if cur["old_path"]:
                    cur["path"] = cur["old_path"]
            elif tag in ("similarity", "rename_from", "rename_to"):
                cur["is_rename"] = True
                if tag == "rename_from":
                    cur["old_path"] = _norm_path(value) or cur["old_path"]
                elif tag == "rename_to":
                    cur["path"] = _norm_path(value) or cur["path"]
            elif tag == "binary":
                cur["is_binary"] = True

    for f in files:
        f.pop("_old_seen", None)
        f.pop("_new_seen", None)
    return files


def _int_param(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        raise DiffInputError(f"{name} must be a number, got {value!r}.")
    return int(value)


def _matches(path: str, pattern: str) -> bool:
    """Case-insensitive glob against the full path and the file name; ``**/x`` also matches a root ``x``."""
    p = path.lower()
    pat = pattern.replace("\\", "/").lower()
    name = posixpath.basename(p)
    candidates = [pat]
    if pat.startswith("**/"):
        candidates.append(pat[3:])
    for c in candidates:
        if fnmatch.fnmatchcase(p, c) or fnmatch.fnmatchcase(name, c):
            return True
    return False


class ScopeAndDiffGuard:
    """Guard for analyzing git diffs against task scope, change budgets, forbidden paths, and secret leaks."""

    SECRET_PATTERNS = [
        (re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED |PGP )?PRIVATE KEY(?: BLOCK)?-----"), "Private cryptographic key"),
        (re.compile(r"(?i)\b\w*(?:api[_-]?key|secret|access[_-]?token|auth[_-]?token|passw(?:or)?d)\w*\s*[:=]\s*['\"][^'\"\s]{12,}['\"]"), "Hardcoded API secret token"),
        (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS Access Key ID"),
        (re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"), "GitHub token"),
        (re.compile(r"github_pat_[A-Za-z0-9_]{22,}"), "GitHub fine-grained token"),
        (re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"), "Slack token"),
        (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"), "Secret API key (sk-...)"),
        (re.compile(r"AIza[0-9A-Za-z_-]{35}"), "Google API key"),
        (re.compile(r"eyJ[A-Za-z0-9-_=]+\.[A-Za-z0-9-_=]+\.?[A-Za-z0-9-_.+/=]*"), "Potential JWT token"),
    ]

    DEFAULT_FORBIDDEN_PATTERNS = [
        ".env*",
        "*.pem",
        "*.key",
        "*.p12",
        "*.pfx",
        "id_rsa*",
        "id_ed25519*",
        ".netrc",
        "**/credentials.json",
        "**/.secrets*",
    ]

    @staticmethod
    def check_diff(
        diff_text: Any,
        allowed_files: Optional[List[str]] = None,
        max_lines_changed: int = 300,
    ) -> Dict[str, Any]:
        """Validate modified paths and total added/deleted line counts against task scope."""
        try:
            text = coerce_diff_text(diff_text)
            limit = _int_param(max_lines_changed, "max_lines_changed")
        except DiffInputError as exc:
            return {
                "within_scope": False, "violations_count": 1, "violations": [str(exc)],
                "modified_files": [], "lines_added": 0, "lines_deleted": 0, "total_lines_changed": 0,
                "status": "INVALID_INPUT", "error": str(exc),
            }
        if isinstance(allowed_files, str):
            allowed_files = [allowed_files]
        allowed_norm = [_norm_path(str(p)) for p in (allowed_files or []) if str(p).strip()]
        allowed_norm = [a if a else "." for a in allowed_norm]  # "." / "./" means the whole repository

        if not text.strip():
            return {
                "within_scope": True, "violations_count": 0, "violations": [], "modified_files": [],
                "lines_added": 0, "lines_deleted": 0, "total_lines_changed": 0,
                "status": "EMPTY_DIFF", "scope_checked": bool(allowed_norm),
                "warnings": ["The diff is empty: nothing was changed, so nothing was checked."],
            }

        files = _parse(text)
        modified_files: List[str] = []
        violations: List[str] = []
        added_lines = sum(f["lines_added"] for f in files)
        deleted_lines = sum(f["lines_deleted"] for f in files)

        def in_scope(path: str) -> bool:
            for allow in allowed_norm:
                if allow == ".":
                    return True
                if any(ch in allow for ch in "*?["):
                    if fnmatch.fnmatchcase(path, allow):
                        return True
                elif path == allow or path.startswith(allow.rstrip("/") + "/"):
                    return True
            return False

        unattributed = False
        for f in files:
            paths = [f["path"]]
            if f["old_path"] and f["old_path"] != f["path"]:
                paths.append(f["old_path"])  # a rename touches both ends
            if f["path"] == "unknown":
                if f["lines_added"] or f["lines_deleted"] or f["hunks_count"]:
                    unattributed = True
                continue
            for p in paths:
                if p not in modified_files:
                    modified_files.append(p)
                    if p.startswith("../") or p == ".." or p.startswith("/"):
                        violations.append(f"Path escapes the repository: '{p}'")
                    elif allowed_norm and not in_scope(p):
                        violations.append(f"Out-of-scope file modified: '{p}'")
        if unattributed and allowed_norm:
            violations.append("Changes found that cannot be attributed to any file (no file header in the diff).")

        total_changed = added_lines + deleted_lines
        if total_changed > limit:
            violations.append(f"Diff size ({total_changed} changed lines) exceeds max_lines_changed ({limit}).")

        recognised = bool(files) and (modified_files or unattributed or total_changed)
        warnings: List[str] = []
        status: str
        if not recognised:
            violations.append("No file changes could be recognised in the input; it does not look like a unified diff.")
            status = "UNPARSEABLE_DIFF"
        elif violations:
            status = "SCOPE_VIOLATIONS_DETECTED"
        elif not allowed_norm:
            status = "NO_SCOPE_DEFINED"
            warnings.append("No allowed_files were given, so only the size limit was checked, not scope.")
        else:
            status = "APPROVED_SCOPE"

        within_scope = len(violations) == 0
        result: Dict[str, Any] = {
            "within_scope": within_scope,
            "violations_count": len(violations),
            "violations": violations,
            "modified_files": modified_files,
            "lines_added": added_lines,
            "lines_deleted": deleted_lines,
            "total_lines_changed": total_changed,
            "status": status,
            "scope_checked": bool(allowed_norm),
        }
        if warnings:
            result["warnings"] = warnings
        return result

    @staticmethod
    def parse_diff_files(diff_text: Any) -> List[Dict[str, Any]]:
        """Parse git diff text into structured file-level metrics.

        Raises :class:`DiffInputError` for a non-text argument (including a PDF).
        """
        return _parse(coerce_diff_text(diff_text))

    @classmethod
    def check_scope_compliance(
        cls,
        modified_files: List[str],
        allowed_patterns: List[str],
        forbidden_patterns: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Check list of file paths against allowed whitelist and forbidden blacklist patterns."""
        if isinstance(modified_files, str):
            modified_files = [modified_files]
        if isinstance(allowed_patterns, str):
            allowed_patterns = [allowed_patterns]
        if isinstance(forbidden_patterns, str):
            forbidden_patterns = [forbidden_patterns]

        def seq(v: Any) -> List[Any]:
            return list(v) if isinstance(v, (list, tuple, set)) else []

        modified_files, allowed_patterns, forbidden_patterns = seq(modified_files), seq(allowed_patterns), seq(forbidden_patterns)
        forbidden = [str(p) for p in (forbidden_patterns or [])] + cls.DEFAULT_FORBIDDEN_PATTERNS
        allowed = [_norm_path(str(p)) for p in (allowed_patterns or []) if str(p).strip()]
        violations: List[str] = []
        out_of_scope: List[str] = []
        forbidden_hits: List[str] = []
        checked = 0

        for f in modified_files or []:
            if not isinstance(f, str) or not f.strip():
                violations.append(f"Unusable path entry: {f!r}")
                continue
            checked += 1
            norm_f = _norm_path(f)

            for pat in forbidden:
                if _matches(norm_f, pat):
                    forbidden_hits.append(norm_f)
                    violations.append(f"Forbidden file modified: '{norm_f}' matches pattern '{pat}'")
                    break

            if norm_f.startswith("../") or norm_f == ".." or norm_f.startswith("/"):
                out_of_scope.append(norm_f)
                violations.append(f"Path escapes the repository: '{norm_f}'")
            elif allowed:
                matched = any(
                    fnmatch.fnmatchcase(norm_f, a) or norm_f.startswith(a.rstrip("/") + "/") for a in allowed
                )
                if not matched:
                    out_of_scope.append(norm_f)
                    violations.append(f"File '{norm_f}' outside permitted scope")

        result: Dict[str, Any] = {
            "compliant": len(violations) == 0,
            "violations": violations,
            "out_of_scope_files": out_of_scope,
            "forbidden_files": forbidden_hits,
            "checked_files": checked,
        }
        if checked == 0 and not violations:
            result["warnings"] = ["No file paths were supplied, so nothing was checked."]
        return result

    @classmethod
    def detect_accidental_secrets_in_diff(cls, diff_text: Any) -> List[Dict[str, Any]]:
        """Scan added diff lines for prospective credentials or confidential tokens.

        A very long added line is scanned up to a bound and reported as a finding of type
        ``"Line too long to scan completely"`` rather than skipped silently. Raises
        :class:`DiffInputError` for a non-text argument (including a PDF).
        """
        findings: List[Dict[str, Any]] = []
        current_file = "unknown"

        for no, kind, payload in _walk(coerce_diff_text(diff_text)):
            if kind == "git":
                current_file = _norm_path(payload[1]) or current_file
            elif kind == "new" and _norm_path(payload) not in ("", "/dev/null"):
                current_file = _norm_path(payload)
            elif kind == "add":
                content = payload.strip()
                if len(content) > _MAX_SCAN_LINE:
                    findings.append({
                        "file": current_file,
                        "line_in_diff": no,
                        "type": "Line too long to scan completely",
                        "snippet": "",
                    })
                    content = content[:_MAX_SCAN_LINE]
                for pattern, desc in cls.SECRET_PATTERNS:
                    match = pattern.search(content)
                    if match:
                        snippet = match.group(0)
                        redacted = snippet[:4] + "***" + snippet[-2:] if len(snippet) > 12 else "***"
                        findings.append({
                            "file": current_file,
                            "line_in_diff": no,
                            "type": desc,
                            "snippet": redacted,
                        })

        return findings

    @classmethod
    def check_diff_size_budget(
        cls,
        diff_text: Any,
        max_files: int = 15,
        max_lines_per_file: int = 250,
        max_total_lines: int = 600,
    ) -> Dict[str, Any]:
        """Verify whether a diff is atomic and bounded according to best practice size budgets."""
        try:
            text = coerce_diff_text(diff_text)
            max_files = _int_param(max_files, "max_files")
            max_lines_per_file = _int_param(max_lines_per_file, "max_lines_per_file")
            max_total_lines = _int_param(max_total_lines, "max_total_lines")
        except DiffInputError as exc:
            return {
                "within_budget": False, "total_files": 0, "total_lines_changed": 0,
                "blockers": [str(exc)], "warnings": [], "files_summary": [], "error": str(exc),
            }
        files = _parse(text)
        total_files = len(files)
        total_lines = sum(f["lines_added"] + f["lines_deleted"] for f in files)

        warnings: List[str] = []
        blockers: List[str] = []

        if text.strip() and not files:
            blockers.append("No file changes could be recognised in the input; it does not look like a unified diff.")
        elif not text.strip():
            warnings.append("The diff is empty: nothing was measured.")

        if total_files > max_files:
            blockers.append(f"Too many modified files ({total_files} > max {max_files})")
        elif total_files > max_files * 0.8:
            warnings.append(f"File count approaching limit ({total_files}/{max_files})")

        if total_lines > max_total_lines:
            blockers.append(f"Total diff volume too large ({total_lines} lines > max {max_total_lines})")

        for f in files:
            f_changed = f["lines_added"] + f["lines_deleted"]
            if f_changed > max_lines_per_file:
                blockers.append(f"File '{f['path']}' has {f_changed} changes (max per file {max_lines_per_file})")

        return {
            "within_budget": len(blockers) == 0,
            "total_files": total_files,
            "total_lines_changed": total_lines,
            "blockers": blockers,
            "warnings": warnings,
            "files_summary": files,
        }
