import bz2
import gzip
import hashlib
import hmac
import json
import lzma
import re
import shutil
import sqlite3
import tarfile
import tempfile
import time
import zipfile
import zlib
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from ._input import SourceReadError, is_pdf_bytes, read_source_file
from ._ops_common import (
    DEFAULT_READ_BUDGET_S,
    BudgetExceeded,
    PathRefused,
    clamp_budget,
    confine,
    run_with_budget,
)

_HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")
_CHUNK = 64 * 1024
#: Entries / bytes a verification or drill will process. A backup is trusted data, but "trusted"
#: archives are exactly where decompression bombs come from (a corrupt or hostile upload).
MAX_ARCHIVE_ENTRIES = 100_000
MAX_VERIFY_BYTES = 10 * 1024**3
MAX_EXTRACT_BYTES = 2 * 1024**3
MAX_SQL_REPLAY_BYTES = 256 * 1024**2
_ARCHIVE_SUFFIXES = {".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz"}
_SQLITE_SAFE_PRAGMAS = {"foreign_keys", "synchronous", "journal_mode", "encoding", "user_version",
                        "application_id", "defer_foreign_keys"}
_COMPLETE_MARKER = re.compile(r"dump\s+complete|dump\s+completed", re.I)


class _Refused(Exception):
    """Internal: an operation was refused or failed; ``str(exc)`` is the user-facing reason."""


def _unsafe_name(name: str) -> bool:
    """True for an archive member name that would land outside the extraction directory."""
    n = name.replace("\\", "/")
    if not n or n.startswith("/") or "\x00" in n or re.match(r"^[A-Za-z]:", n):
        return True
    return any(part == ".." for part in n.split("/"))


def _unsafe_link(target: str) -> bool:
    return _unsafe_name(target) or target.startswith("~")


def _file(path: Any, base_dir: Optional[str]) -> Tuple[Optional[Path], Optional[str]]:
    """Resolve ``path`` to an existing regular file inside ``base_dir`` or return a message."""
    try:
        return confine(path, base_dir, kind="file"), None
    except PathRefused as exc:
        return None, str(exc)


def _looks_pdf(p: Path) -> bool:
    try:
        with p.open("rb") as handle:
            head = handle.read(1100)
    except OSError:
        return p.suffix.lower() == ".pdf"
    return is_pdf_bytes(head) or p.suffix.lower() == ".pdf"


def _compression_of(p: Path) -> Optional[str]:
    try:
        with p.open("rb") as handle:
            head = handle.read(6)
    except OSError:
        return None
    if head[:2] == b"\x1f\x8b":
        return "gzip"
    if head[:3] == b"BZh":
        return "bzip2"
    if head[:6] == b"\xfd7zXZ\x00":
        return "xz"
    return None


def _read_pdf_in_full(p: Path, budget: float = DEFAULT_READ_BUDGET_S) -> Tuple[Optional[Any], Optional[str]]:
    """Read every page of the PDF at ``p`` through the shared reader, within ``budget`` seconds.

    A long PDF is slow to extract, so the budget is generous (default 300 s, at most 600 s); running
    out of it is an error, never a partial read.
    """
    try:
        return run_with_budget(lambda: read_source_file(p), clamp_budget(budget), "Reading the PDF"), None
    except (SourceReadError, BudgetExceeded) as exc:
        return None, str(exc)


def _deadline(seconds: Any) -> float:
    return time.monotonic() + clamp_budget(seconds)


def _check_deadline(deadline: Optional[float], what: str = "The verification") -> None:
    if deadline is not None and time.monotonic() > deadline:
        raise _Refused(
            f"{what} ran past its time budget and was stopped; nothing was reported as verified. "
            "Raise timeout_seconds (at most 600) for a very large backup."
        )


def _stream_count(reader: Any, limit: int, used: int = 0, deadline: Optional[float] = None) -> int:
    """Read ``reader`` to EOF, returning bytes read; raise _Refused past ``limit`` (a bomb)."""
    total = 0
    while True:
        chunk = reader.read(_CHUNK)
        if not chunk:
            return total
        total += len(chunk)
        if used + total > limit:
            raise _Refused(f"Archive expands to more than {limit} bytes; refusing to read it all.")
        _check_deadline(deadline)


def _copy_limited(src: Any, dest: Path, remaining: int, deadline: Optional[float] = None) -> int:
    written = 0
    with dest.open("wb") as out:
        while True:
            chunk = src.read(_CHUNK)
            if not chunk:
                return written
            written += len(chunk)
            if written > remaining:
                raise _Refused(f"Archive expands to more than the {MAX_EXTRACT_BYTES}-byte drill limit.")
            _check_deadline(deadline, "The restore")
            out.write(chunk)


class BackupRestoreVerifier:
    """Verifier for backup file existence, SHA-256 checksums, archive integrity, and disaster recovery simulation.

    What each answer means (the previous version over-claimed in several places):

    * ``compute_sha256`` / ``verify_checksum`` hash **every byte** of the file, so they are valid
      for any file type, a PDF included.
    * ``verify_archive_integrity`` decompresses **every member** (CRC / gzip trailer checked),
      refuses decompression bombs, and reports unsafe member names (absolute paths, ``..``, links,
      devices). An empty archive is *not* valid.
    * ``verify_sql_dump_integrity`` streams the whole file (no size cap, no memory blow-up) and calls
      a dump that stops mid-statement truncated.
    * ``simulate_restore_to_temp_dir`` extracts only regular files, only below an **empty** target
      directory, never follows or creates links, and replays ``.sql`` in an in-memory SQLite database
      with ATTACH/VACUUM/PRAGMA fenced off and a time limit -- so it can neither write outside the
      target nor run forever. It never deletes anything but the temp directory it made itself.
      An unsupported format is ``restored: False`` (it used to be ``True`` with zero items).
    * ``verify_backup`` is ``recovery_ready`` only if some real check passed; a file that merely
      exists proves nothing.

    A **PDF** is read in full through the shared reader (``_input``): a PDF is hashed and restored
    like any other file, its pages are all read to prove it opens, scanned pages are named, and as a
    SQL dump or an archive it is rejected with a message saying it was read in full and is not that
    kind of input. Every path method accepts an optional ``base_dir`` that confines it.
    """

    # ------------------------------------------------------------------
    @staticmethod
    def compute_sha256(
        file_path: str, *, base_dir: Optional[str] = None, timeout_seconds: float = DEFAULT_READ_BUDGET_S
    ) -> Dict[str, Any]:
        """Compute the SHA-256 digest and byte size of a file on disk (every byte, streamed).

        ``timeout_seconds`` (default 300, at most 600) bounds the read; a file that cannot be hashed
        in time is an error, never a digest of part of it.
        """
        path, err = _file(file_path, base_dir)
        if path is None:
            return {"exists": False, "error": err}

        h = hashlib.sha256()
        size_bytes = 0
        deadline = _deadline(timeout_seconds)
        try:
            with path.open("rb") as f:
                while True:
                    chunk = f.read(_CHUNK)
                    if not chunk:
                        break
                    size_bytes += len(chunk)
                    h.update(chunk)
                    _check_deadline(deadline, "Hashing")
        except _Refused as exc:
            return {"exists": True, "error": str(exc)}
        except OSError as exc:
            return {"exists": True, "error": f"Failed to read file: {exc.strerror or type(exc).__name__}"}

        return {
            "exists": True,
            "file": str(path),
            "size_bytes": size_bytes,
            "sha256": h.hexdigest(),
        }

    @classmethod
    def verify_checksum(
        cls,
        file_path: str,
        expected_sha256: str,
        *,
        base_dir: Optional[str] = None,
        timeout_seconds: float = DEFAULT_READ_BUDGET_S,
    ) -> Dict[str, Any]:
        """Verify a file's SHA-256 digest against an expected 64-digit hex string."""
        if not isinstance(expected_sha256, str):
            return {"verified": False, "error": "expected_sha256 must be a string of 64 hex digits."}
        expected = expected_sha256.strip().lower()
        if expected.startswith("sha256:"):
            expected = expected[len("sha256:"):]
        if not _HEX64.match(expected):
            return {"verified": False, "error": "expected_sha256 must be 64 hexadecimal digits."}

        info = cls.compute_sha256(file_path, base_dir=base_dir, timeout_seconds=timeout_seconds)
        if "sha256" not in info:
            return {"verified": False, "error": info.get("error")}

        actual = info["sha256"].lower()
        return {
            "verified": hmac.compare_digest(actual, expected),
            "file": info["file"],
            "actual_sha256": actual,
            "expected_sha256": expected,
        }

    @classmethod
    def verify_archive_integrity(
        cls,
        archive_path: str,
        *,
        base_dir: Optional[str] = None,
        max_uncompressed_bytes: int = MAX_VERIFY_BYTES,
        timeout_seconds: float = DEFAULT_READ_BUDGET_S,
    ) -> Dict[str, Any]:
        """Verify that a .zip / .tar(.gz|.bz2|.xz) / single-file .gz|.bz2|.xz decompresses end to end.

        Every member is read, so a corrupt member or truncated stream is found, not just a bad header.
        ``timeout_seconds`` (default 300, at most 600) bounds the work; running out of it fails the
        check rather than verifying only part of the archive.
        """
        path, err = _file(archive_path, base_dir)
        if path is None:
            return {"valid_archive": False, "error": err}

        if _looks_pdf(path):
            read, perr = _read_pdf_in_full(path, timeout_seconds)
            detail = f"{read.pages} page(s) read" if read is not None else f"could not be read: {perr}"
            return {
                "valid_archive": False,
                "format": "pdf",
                "error": f"{path.name} is a PDF document ({detail}), not an archive.",
                **({"input": read.describe(), "warnings": read.warnings()} if read is not None else {}),
            }

        try:
            limit = int(max_uncompressed_bytes)
        except (TypeError, ValueError):
            limit = MAX_VERIFY_BYTES
        deadline = _deadline(timeout_seconds)
        try:
            if zipfile.is_zipfile(path):
                return cls._verify_zip(path, limit, deadline)
            if tarfile.is_tarfile(path):
                return cls._verify_tar(path, limit, deadline)
            kind = _compression_of(path)
            if kind:
                return cls._verify_compressed(path, kind, limit, deadline)
            return {"valid_archive": False,
                    "error": "Unsupported archive format (not zip, tar, gzip, bzip2 or xz)."}
        except _Refused as exc:
            return {"valid_archive": False, "error": str(exc)}
        except (zipfile.BadZipFile, tarfile.TarError, EOFError, OSError, zlib.error, lzma.LZMAError,
                RuntimeError, ValueError) as exc:
            return {"valid_archive": False, "error": f"Corrupt or unreadable archive: {type(exc).__name__}: {exc}"}

    @staticmethod
    def _unsafe_result(unsafe: List[str]) -> Dict[str, Any]:
        return {"unsafe_entries": unsafe[:20], "unsafe_count": len(unsafe), "safe_to_extract": not unsafe}

    @classmethod
    def _verify_zip(cls, path: Path, limit: int, deadline: Optional[float] = None) -> Dict[str, Any]:
        with zipfile.ZipFile(path, "r") as zf:
            infos = zf.infolist()
            if len(infos) > MAX_ARCHIVE_ENTRIES:
                raise _Refused(f"Archive has more than {MAX_ARCHIVE_ENTRIES} entries.")
            if sum(i.file_size for i in infos) > limit:
                raise _Refused(f"Archive expands to more than {limit} bytes; refusing to read it all.")
            if not infos:
                return {"valid_archive": False, "format": "zip", "entries_count": 0,
                        "error": "Archive contains no entries.", **cls._unsafe_result([])}
            unsafe: List[str] = []
            total = 0
            for info in infos:
                mode = (info.external_attr >> 16) & 0o170000
                if _unsafe_name(info.filename) or mode == 0o120000:
                    unsafe.append(info.filename)
                if info.is_dir():
                    continue
                try:
                    with zf.open(info) as member:
                        total += _stream_count(member, limit, total, deadline)
                except (zipfile.BadZipFile, zlib.error, EOFError, RuntimeError, NotImplementedError) as exc:
                    return {"valid_archive": False, "format": "zip", "entries_count": len(infos),
                            "error": f"Corrupt zip member {info.filename!r}: {type(exc).__name__}: {exc}",
                            **cls._unsafe_result(unsafe)}
            return {"valid_archive": True, "format": "zip", "entries_count": len(infos),
                    "uncompressed_bytes": total, **cls._unsafe_result(unsafe)}

    @classmethod
    def _verify_tar(cls, path: Path, limit: int, deadline: Optional[float] = None) -> Dict[str, Any]:
        unsafe: List[str] = []
        count = 0
        total = 0
        with tarfile.open(path, "r:*") as tf:
            for member in tf:
                count += 1
                if count > MAX_ARCHIVE_ENTRIES:
                    raise _Refused(f"Archive has more than {MAX_ARCHIVE_ENTRIES} entries.")
                if _unsafe_name(member.name):
                    unsafe.append(member.name)
                if member.issym() or member.islnk():
                    if _unsafe_link(member.linkname):
                        unsafe.append(f"{member.name} -> {member.linkname}")
                elif not (member.isreg() or member.isdir()):
                    unsafe.append(f"{member.name} (special file)")
                if member.isreg():
                    handle = tf.extractfile(member)
                    if handle is not None:
                        total += _stream_count(handle, limit, total, deadline)
        if count == 0:
            return {"valid_archive": False, "format": "tar", "entries_count": 0,
                    "error": "Archive contains no entries.", **cls._unsafe_result([])}
        return {"valid_archive": True, "format": "tar", "entries_count": count,
                "uncompressed_bytes": total, **cls._unsafe_result(unsafe)}

    @classmethod
    def _verify_compressed(
        cls, path: Path, kind: str, limit: int, deadline: Optional[float] = None
    ) -> Dict[str, Any]:
        opener: Callable[..., Any] = {"gzip": gzip.open, "bzip2": bz2.open, "xz": lzma.open}[kind]
        with opener(path, "rb") as stream:
            total = _stream_count(stream, limit, 0, deadline)
        if total == 0:
            return {"valid_archive": False, "format": kind, "entries_count": 0,
                    "error": "The compressed file is empty.", **cls._unsafe_result([])}
        return {"valid_archive": True, "format": kind, "entries_count": 1, "uncompressed_bytes": total,
                **cls._unsafe_result([])}

    @classmethod
    def verify_sql_dump_integrity(
        cls,
        sql_dump_path: str,
        *,
        base_dir: Optional[str] = None,
        timeout_seconds: float = DEFAULT_READ_BUDGET_S,
    ) -> Dict[str, Any]:
        """Validate an SQL backup dump for structure, table definitions and truncation.

        ``timeout_seconds`` (default 300, at most 600) bounds the scan; running out of it is an error,
        never a verdict on part of the dump. The whole file is streamed; ``valid`` additionally requires that the dump does not stop in
        the middle of a statement.
        """
        p, err = _file(sql_dump_path, base_dir)
        if p is None:
            return {"valid": False, "error": err}

        if _looks_pdf(p):
            read, perr = _read_pdf_in_full(p, timeout_seconds)
            detail = f"{read.pages} page(s) read in full" if read is not None else f"could not be read: {perr}"
            return {
                "valid": False,
                "file": str(p),
                "error": f"{p.name} is a PDF document ({detail}); a PDF is not a SQL dump and cannot be restored as one.",
                **({"input": read.describe(), "warnings": read.warnings()} if read is not None else {}),
            }

        tables: List[str] = []
        tables_total = 0
        has_commit = False
        has_inserts = False
        marker = False
        last_significant = ""
        size = 0
        deadline = _deadline(timeout_seconds)
        table_re = re.compile(r"CREATE\s+(?:TEMP(?:ORARY)?\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z0-9_`\"'.]+)", re.I)
        try:
            with p.open("rb") as handle:
                head = handle.read(8192)
                if b"\x00" in head:
                    return {"valid": False, "file": str(p),
                            "error": f"{p.name} is a binary file, not a text SQL dump (a custom-format or "
                                     "compressed dump must be restored with its own tool)."}
                handle.seek(0)
                for raw in iter(lambda: handle.readline(1 << 20), b""):
                    size += len(raw)
                    _check_deadline(deadline, "The scan")
                    line = raw.decode("utf-8", errors="replace")
                    stripped = line.strip()
                    if not stripped:
                        continue
                    if stripped.startswith(("--", "#")) or (stripped.startswith("/*") and stripped.endswith("*/")
                                                           and not stripped.endswith(";")):
                        marker = marker or bool(_COMPLETE_MARKER.search(stripped))
                        continue
                    last_significant = stripped
                    if not has_commit and re.search(r"\bCOMMIT\b", stripped, re.I):
                        has_commit = True
                    if not has_inserts and re.search(r"\bINSERT\s+INTO\b", stripped, re.I):
                        has_inserts = True
                    m = table_re.search(stripped)
                    if m:
                        tables_total += 1
                        if len(tables) < 1000:
                            tables.append(m.group(1))
        except _Refused as exc:
            return {"valid": False, "file": str(p), "error": str(exc)}
        except OSError as exc:
            return {"valid": False, "file": str(p), "error": f"Failed to read file: {exc.strerror or type(exc).__name__}"}

        if size == 0 or not last_significant:
            return {"valid": False, "file": str(p), "size_bytes": size, "tables_found": [],
                    "has_transaction_commit": False, "has_inserts": False,
                    "error": "The dump is empty or contains only comments."}

        truncated = not (last_significant.endswith(";") or last_significant.endswith("\\."))
        has_structure = tables_total > 0 or has_inserts
        result: Dict[str, Any] = {
            "valid": has_structure and not truncated,
            "file": str(p),
            "size_bytes": size,
            "tables_found": tables,
            "tables_total": tables_total,
            "has_transaction_commit": has_commit,
            "has_inserts": has_inserts,
            "completion_marker": marker,
            "truncation_suspected": truncated,
        }
        if truncated:
            result["error"] = "The dump appears truncated: its last statement is not terminated by ';'."
        elif not has_structure:
            result["error"] = "No CREATE TABLE or INSERT statements were found."
        return result

    # ------------------------------------------------------------------
    @staticmethod
    def _dest(target: Path, name: str) -> Path:
        n = name.replace("\\", "/")
        if _unsafe_name(n):
            raise _Refused(f"unsafe member name {name!r}")
        parts = [part for part in n.split("/") if part not in ("", ".")]
        if not parts:
            raise _Refused(f"empty member name {name!r}")
        dest = target.joinpath(*parts)
        try:
            dest.resolve().relative_to(target)
        except ValueError:
            raise _Refused(f"member {name!r} resolves outside the target") from None
        return dest

    @classmethod
    def _extract_zip(cls, path: Path, target: Path, deadline: Optional[float] = None) -> Dict[str, Any]:
        files = dirs = written = 0
        skipped: List[str] = []
        with zipfile.ZipFile(path, "r") as zf:
            infos = zf.infolist()
            if len(infos) > MAX_ARCHIVE_ENTRIES:
                raise _Refused(f"Archive has more than {MAX_ARCHIVE_ENTRIES} entries.")
            if sum(i.file_size for i in infos) > MAX_EXTRACT_BYTES:
                raise _Refused(f"Archive expands to more than the {MAX_EXTRACT_BYTES}-byte drill limit.")
            for info in infos:
                mode = (info.external_attr >> 16) & 0o170000
                try:
                    dest = cls._dest(target, info.filename)
                except _Refused:
                    skipped.append(info.filename)
                    continue
                if mode == 0o120000:
                    skipped.append(info.filename)
                    continue
                if info.is_dir():
                    dest.mkdir(parents=True, exist_ok=True)
                    dirs += 1
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as member:
                    written += _copy_limited(member, dest, MAX_EXTRACT_BYTES - written, deadline)
                files += 1
        return {"files": files, "dirs": dirs, "bytes": written, "skipped": skipped}

    @classmethod
    def _extract_tar(cls, path: Path, target: Path, deadline: Optional[float] = None) -> Dict[str, Any]:
        files = dirs = written = count = 0
        skipped: List[str] = []
        with tarfile.open(path, "r:*") as tf:
            for member in tf:
                count += 1
                if count > MAX_ARCHIVE_ENTRIES:
                    raise _Refused(f"Archive has more than {MAX_ARCHIVE_ENTRIES} entries.")
                try:
                    dest = cls._dest(target, member.name)
                except _Refused:
                    skipped.append(member.name)
                    continue
                if member.isdir():
                    dest.mkdir(parents=True, exist_ok=True)
                    dirs += 1
                elif member.isreg():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    handle = tf.extractfile(member)
                    if handle is None:
                        skipped.append(member.name)
                        continue
                    written += _copy_limited(handle, dest, MAX_EXTRACT_BYTES - written, deadline)
                    files += 1
                else:  # symlink, hardlink, device, fifo: never recreated
                    skipped.append(member.name)
        return {"files": files, "dirs": dirs, "bytes": written, "skipped": skipped}

    @staticmethod
    def _replay_sql(path: Path, timeout_s: float) -> Dict[str, Any]:
        size = path.stat().st_size
        if size > MAX_SQL_REPLAY_BYTES:
            raise _Refused(f"The dump is too large to replay in memory ({size} bytes; limit {MAX_SQL_REPLAY_BYTES}).")
        raw = path.read_bytes()
        if b"\x00" in raw[:8192]:
            raise _Refused("The file is binary, not a text SQL dump.")
        text = raw.decode("utf-8", errors="replace")
        if re.search(r"\bVACUUM\b", text, re.I):
            raise _Refused("The dump contains VACUUM (which can write files); refusing to replay it.")

        deadline = time.monotonic() + timeout_s
        deny = getattr(sqlite3, "SQLITE_DENY", 1)
        ok = getattr(sqlite3, "SQLITE_OK", 0)
        a_attach, a_detach = getattr(sqlite3, "SQLITE_ATTACH", 24), getattr(sqlite3, "SQLITE_DETACH", 25)
        a_pragma, a_function = getattr(sqlite3, "SQLITE_PRAGMA", 19), getattr(sqlite3, "SQLITE_FUNCTION", 31)

        def authorizer(action: int, arg1: Optional[str], arg2: Optional[str], dbname: Optional[str],
                       source: Optional[str]) -> int:
            if action in (a_attach, a_detach):
                return deny
            if action == a_pragma and (arg1 or "").lower() not in _SQLITE_SAFE_PRAGMAS:
                return deny
            if action == a_function and (arg1 or "").lower() in ("load_extension", "readfile", "writefile"):
                return deny
            return ok

        conn = sqlite3.connect(":memory:")
        try:
            conn.execute("PRAGMA max_page_count=25600")  # ~100 MB: a dump cannot balloon the heap
            if hasattr(conn, "setlimit") and hasattr(sqlite3, "SQLITE_LIMIT_LENGTH"):
                conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 50_000_000)
            conn.set_authorizer(authorizer)
            conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 1000)
            conn.executescript(text)
            tables = conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
        except sqlite3.Error as exc:
            if "interrupted" in str(exc).lower():
                raise _Refused(f"SQL replay did not finish within {timeout_s:g}s.") from None
            raise _Refused(
                f"SQL replay failed against SQLite: {exc}. A dump for PostgreSQL or MySQL cannot be proven "
                "restorable by a SQLite replay; restore it into a scratch instance of its own engine."
            ) from None
        finally:
            conn.close()
        return {"tables": tables}

    @classmethod
    def _restore_pdf(cls, path: Path, target: Path, budget: float = DEFAULT_READ_BUDGET_S) -> Dict[str, Any]:
        dest = target / (path.name if path.name else "restored.pdf")
        with path.open("rb") as src:
            written = _copy_limited(src, dest, MAX_EXTRACT_BYTES)
        src_hash = cls.compute_sha256(str(path), timeout_seconds=budget).get("sha256")
        dst_hash = cls.compute_sha256(str(dest), timeout_seconds=budget).get("sha256")
        identical = bool(src_hash) and src_hash == dst_hash
        out: Dict[str, Any] = {"files": 1, "dirs": 0, "bytes": written, "skipped": [], "bytes_identical": identical}
        if not identical:
            raise _Refused("The restored copy does not match the original byte for byte.")
        read, perr = _read_pdf_in_full(dest, budget)
        if read is None:
            out["content_error"] = perr
            raise _Refused(f"The restored bytes are identical, but the PDF could not be read in full: {perr}")
        out["input"] = read.describe()
        out["warnings"] = read.warnings()
        return out

    @classmethod
    def simulate_restore_to_temp_dir(
        cls,
        archive_or_dump_path: str,
        temp_target_dir: Optional[str] = None,
        *,
        base_dir: Optional[str] = None,
        timeout_seconds: float = DEFAULT_READ_BUDGET_S,
    ) -> Dict[str, Any]:
        """Test a restore inside an isolated directory to prove recovery readiness.

        ``timeout_seconds`` (default 300, at most 600) is the budget for the whole drill -- reading,
        extracting or replaying; exceeding it fails the drill, it never restores "part".

        Supports zip, tar (any compression), ``.sql`` (replayed in memory in SQLite) and PDF (copied,
        hash-compared, every page read). ``temp_target_dir`` must be absent or empty. Returns
        ``restored: True`` only when something was actually recovered and nothing was left out.
        """
        p, err = _file(archive_or_dump_path, base_dir)
        if p is None:
            return {"restored": False, "error": err}
        seconds = max(1.0, clamp_budget(timeout_seconds))
        deadline = time.monotonic() + seconds

        owns_target = temp_target_dir is None
        start_time = time.perf_counter()
        try:
            if owns_target:
                target = Path(tempfile.mkdtemp(prefix="aura_restore_drill_")).resolve()
            else:
                target = confine(temp_target_dir, base_dir, must_exist=False)
                if target.exists():
                    if not target.is_dir():
                        return {"restored": False, "file": str(p), "error": "temp_target_dir is not a directory."}
                    if any(target.iterdir()):
                        return {"restored": False, "file": str(p),
                                "error": "temp_target_dir is not empty; refusing to restore over existing files."}
                else:
                    target.mkdir(parents=True)
        except PathRefused as exc:
            return {"restored": False, "file": str(p), "error": str(exc)}
        except OSError as exc:
            return {"restored": False, "file": str(p), "error": f"Could not prepare the target: {exc}"}

        result: Dict[str, Any]
        try:
            skipped: List[str] = []
            extra: Dict[str, Any] = {}
            if _looks_pdf(p):
                stats = cls._restore_pdf(p, target, seconds)
                extra = {k: stats[k] for k in ("input", "warnings", "bytes_identical") if k in stats}
                items, files = 1, 1
            elif zipfile.is_zipfile(p):
                stats = cls._extract_zip(p, target, deadline)
                skipped, files = stats["skipped"], stats["files"]
                items = stats["files"] + stats["dirs"]
            elif tarfile.is_tarfile(p):
                stats = cls._extract_tar(p, target, deadline)
                skipped, files = stats["skipped"], stats["files"]
                items = stats["files"] + stats["dirs"]
            elif p.suffix.lower() == ".sql":
                stats = cls._replay_sql(p, seconds)
                items, files = 1, 1
                extra = {"engine": "sqlite", "tables_created": stats["tables"]}
            else:
                raise _Refused(
                    "Unsupported backup format: expected a zip, tar, .sql dump or PDF. Nothing was restored."
                )

            elapsed = round((time.perf_counter() - start_time) * 1000.0, 2)
            result = {
                "restored": files > 0 and not skipped,
                "file": str(p),
                "recovered_items_count": items,
                "elapsed_ms": elapsed,
                "temp_target_dir": str(target),
                "temp_target_dir_removed": owns_target,
                **extra,
            }
            if skipped:
                result["skipped_entries"] = skipped[:20]
                result["skipped_count"] = len(skipped)
                result["error"] = (
                    f"{len(skipped)} entr{'y' if len(skipped) == 1 else 'ies'} (unsafe names, links or "
                    "special files) were not restored; the restore is incomplete."
                )
            elif files == 0:
                result["error"] = "The backup contained no files to restore."
        except _Refused as exc:
            result = {"restored": False, "file": str(p), "error": str(exc), "temp_target_dir": str(target),
                      "temp_target_dir_removed": owns_target}
            if not owns_target and any(target.iterdir()):
                result["partial_files_left_in_target"] = True
        except (zipfile.BadZipFile, tarfile.TarError, EOFError, OSError, zlib.error, lzma.LZMAError,
                RuntimeError, ValueError) as exc:
            result = {"restored": False, "file": str(p), "error": f"{type(exc).__name__}: {exc}",
                      "temp_target_dir": str(target), "temp_target_dir_removed": owns_target}
        finally:
            # Only a directory this method created itself is ever removed.
            if owns_target and target.exists():
                shutil.rmtree(target, ignore_errors=True)
        return result

    # ------------------------------------------------------------------
    @classmethod
    def verify_backup(
        cls,
        backup_path: str,
        expected_sha256: Optional[str] = None,
        *,
        base_dir: Optional[str] = None,
        timeout_seconds: float = DEFAULT_READ_BUDGET_S,
    ) -> Dict[str, Any]:
        """Combined backup verification: checksum (optional), format-specific integrity, and PDF full read.

        ``recovery_ready`` is True only when at least one real check ran and everything that ran
        passed. A file that merely exists, or an empty one, is not ready.
        """
        path, err = _file(backup_path, base_dir)
        if path is None:
            return {"recovery_ready": False, "error": err}

        size = path.stat().st_size
        checks: List[str] = []
        reasons: List[str] = []
        warnings: List[str] = []
        checksum_res = archive_res = sql_res = doc_res = None
        ready = True
        checksum_ok = False

        if size == 0:
            ready = False
            reasons.append("The backup file is empty.")

        if expected_sha256:
            checksum_res = cls.verify_checksum(str(path), expected_sha256, timeout_seconds=timeout_seconds)
            checks.append("checksum")
            checksum_ok = bool(checksum_res.get("verified"))
            if not checksum_ok:
                ready = False
                reasons.append(checksum_res.get("error") or "The SHA-256 digest does not match.")

        suffix = path.suffix.lower()
        if _looks_pdf(path):
            read, perr = _read_pdf_in_full(path, timeout_seconds)
            checks.append("pdf_full_read")
            if read is None:
                ready = False
                reasons.append(perr or "The PDF could not be read.")
                doc_res = {"readable": False, "error": perr}
            else:
                doc_res = read.describe()
                warnings.extend(read.warnings())
                if not read.complete and not checksum_ok:
                    ready = False
                    reasons.append("Some PDF pages could not be read; pass expected_sha256 to prove the file's bytes.")
        elif suffix == ".sql":
            sql_res = cls.verify_sql_dump_integrity(str(path), timeout_seconds=timeout_seconds)
            checks.append("sql_dump")
            if not sql_res.get("valid"):
                ready = False
                reasons.append(sql_res.get("error") or "The SQL dump is not valid.")
        elif suffix in _ARCHIVE_SUFFIXES or zipfile.is_zipfile(path) or tarfile.is_tarfile(path):
            archive_res = cls.verify_archive_integrity(str(path), timeout_seconds=timeout_seconds)
            checks.append("archive_integrity")
            if not archive_res.get("valid_archive"):
                ready = False
                reasons.append(archive_res.get("error") or "The archive is not valid.")
            elif not archive_res.get("safe_to_extract", True):
                ready = False
                reasons.append("The archive contains unsafe entries (absolute paths, '..', links or special files).")

        if not checks:
            ready = False
            reasons.append(
                "Nothing beyond the file's existence could be verified (unknown format, no expected_sha256)."
            )

        return {
            "recovery_ready": ready,
            "file": str(path),
            "size_bytes": size,
            "checksum": checksum_res,
            "archive": archive_res,
            "sql": sql_res,
            "document": doc_res,
            "checks_performed": checks,
            "reasons": reasons,
            "warnings": warnings,
        }

    @classmethod
    def generate_restore_drill_report(cls, drill_result: Dict[str, Any]) -> str:
        """Render a Disaster Recovery Drill audit report in Markdown.

        "RTO VERIFIED" is claimed only for a result that records an actual restore
        (``restored: True``); a ``verify_backup`` result proves integrity, not recovery time.
        """
        if not isinstance(drill_result, dict):
            return "# Disaster Recovery (DR) Drill Report: FAILED (NO VALID RESULT)\n\nThe drill result was not a dict."
        restored = drill_result.get("restored") is True
        integrity = drill_result.get("recovery_ready") is True
        warnings = [str(w) for w in (drill_result.get("warnings") or []) if w]
        if restored:
            status_str = "SUCCESS (RTO VERIFIED)"
        elif integrity:
            status_str = "INTEGRITY VERIFIED (NO RESTORE PERFORMED)"
        else:
            status_str = "FAILED (RESTORE DEFECT)"
        if (restored or integrity) and warnings:
            status_str += " WITH WARNINGS"
        ready = restored or integrity

        file_name = str(drill_result.get("file", "N/A")).replace("`", "'")
        audit = json.dumps(drill_result, indent=2, default=str, ensure_ascii=False).replace("```", "`​``")
        lines = [
            f"# Disaster Recovery (DR) Drill Report: {status_str}",
            "",
            f"- **Target File:** `{file_name}`",
            f"- **Recovery Duration:** `{drill_result.get('elapsed_ms', 'N/A')} ms`",
            f"- **Items Recovered:** `{drill_result.get('recovered_items_count', 'N/A')}`",
            f"- **Integrity Verification:** {'Verified' if ready else 'Unverified / Error'}",
        ]
        if warnings:
            lines += ["", "## Warnings", ""] + [f"- {w}" for w in warnings]
        error = drill_result.get("error")
        if error and not ready:
            lines += ["", f"**Error:** {str(error)[:500]}"]
        lines += ["", "## Audit Log", "", f"```json\n{audit}\n```"]
        return "\n".join(lines)
