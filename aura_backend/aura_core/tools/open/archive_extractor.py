"""Not exposed (ADR-036, 2026-11-21) -- deliberately NOT registered as a tool. It lists, extracts and
creates archives at filesystem paths: on the gateway that reads and writes the gateway's own
disk.

Reachable from no persona: nothing under ``aura_core`` imports this module, which
``tests/architecture/test_open_tools_classification.py`` checks against
``tools/open/_classification.py``. Kept as reference code; change that classification and
ADR-036 before wiring it in.

Archive listing, auditing, extraction and creation for ZIP and TAR (``.tar``, ``.tar.gz``, ...).

Hardening notes (review of 2026-11):

* Every method returns a result dict; no input -- wrong types, a directory where a file is expected, a
  file without read permission, a corrupt archive -- raises into the caller.
* Extraction is **two-phase**: every member is validated first (path traversal, links, device files,
  member count, declared size), and only then is anything written. A rejected archive leaves nothing
  behind. Files are streamed out with a *byte counter* on what is actually written, so a member that
  lies about its size cannot exceed the cap either.
* Symlinks, hardlinks, devices and FIFOs are never created. A symlink member followed by a file
  "inside" it is the classic way to write outside the destination; refusing links closes it.
* ``base_dir`` (optional on every method that takes a path) confines archive, source and destination
  paths to a directory, for callers that are not trusted.
* A **PDF inside an archive** is read in full, page by page, by :meth:`read_archive_member` and
  :meth:`read_archive_documents` (through ``_input``); coverage is reported under ``input``.
  A PDF handed in *as* the archive is reported as "a PDF, not an archive" after it has been read.
* Only ZIP and TAR (optionally gzip/bz2/xz compressed) are supported. A bare ``.gz`` that is not a
  tarball is not.
"""

from __future__ import annotations

import math
import os
import stat as _stat
import tarfile
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ._input import SourceReadError, is_pdf_bytes, read_source_bytes, read_source_file
from ._time_budget import DEFAULT_BUDGET_S, Deadline

#: Members listed / extracted per call. A zip can hold millions of tiny entries.
DEFAULT_MAX_MEMBERS = 100_000
#: Ratio and size above which an archive is called a decompression bomb by :meth:`audit_archive_safety`.
BOMB_RATIO = 100.0
BOMB_MIN_BYTES = 50 * 1024 * 1024
_CHUNK = 64 * 1024
#: One member read into memory for text/PDF extraction is capped separately from disk extraction.
DEFAULT_MAX_MEMBER_MB = 100.0

_Member = Tuple[str, int, str]  # (name, declared size, kind) with kind in file|dir|link|special


class _SizeLimit(Exception):
    """Internal: the byte budget was exceeded while streaming a member."""


class _TimedOut(Exception):
    """Internal: the time budget ran out while streaming."""


def _to_path(value: Any) -> Optional[Path]:
    if isinstance(value, Path):
        return value
    if isinstance(value, str) and value and "\x00" not in value:
        try:
            return Path(value).expanduser()
        except (OSError, RuntimeError, ValueError):
            return None
    return None


def _inside(path: Path, base_dir: Any) -> bool:
    """True when ``path`` resolves inside ``base_dir`` (or no ``base_dir`` is set)."""
    if base_dir is None:
        return True
    try:
        path.resolve().relative_to(Path(base_dir).resolve())
        return True
    except (OSError, RuntimeError, ValueError, TypeError):
        return False


def _safe_name(name: str) -> bool:
    """A member name that stays relative and never climbs out. Backslashes count as separators."""
    if not name or "\x00" in name:
        return False
    normalised = name.replace("\\", "/")
    if normalised.startswith("/") or (len(normalised) > 1 and normalised[1] == ":"):
        return False
    return ".." not in normalised.split("/")


def _zip_kind(info: zipfile.ZipInfo) -> str:
    if info.is_dir():
        return "dir"
    mode = info.external_attr >> 16
    if mode:
        if _stat.S_ISLNK(mode):
            return "link"
        if _stat.S_ISCHR(mode) or _stat.S_ISBLK(mode) or _stat.S_ISFIFO(mode) or _stat.S_ISSOCK(mode):
            return "special"
    return "file"


def _tar_kind(member: tarfile.TarInfo) -> str:
    if member.isdir():
        return "dir"
    if member.isreg():
        return "file"
    if member.issym() or member.islnk():
        return "link"
    return "special"


def _kind_of(archive: Path) -> Optional[str]:
    try:
        if zipfile.is_zipfile(archive):
            return "zip"
        if tarfile.is_tarfile(archive):
            return "tar"
    except (OSError, ValueError):
        return None
    return None


def _not_an_archive(src: Path, key: str, **extra: Any) -> Dict[str, Any]:
    """The error for a file that is neither zip nor tar. A PDF is read in full so the answer says so."""
    result: Dict[str, Any] = {key: False, **extra}
    try:
        with src.open("rb") as handle:
            head = handle.read(1100)
        if is_pdf_bytes(head):
            try:
                read = read_source_file(src)
                result["error"] = (
                    f"This is a PDF document ({read.pages} page(s), read in full), not an archive. "
                    "Use a document reader on it instead."
                )
                result["input"] = read.describe()
                if read.warnings():
                    result["warnings"] = read.warnings()
            except SourceReadError as exc:
                result["error"] = f"This is a PDF, not an archive, and it could not be read: {exc}"
            return result
    except OSError:
        pass
    result["error"] = "Unsupported archive format (expected zip or tar)."
    return result


def _scan(archive: Path, kind: str, max_members: int) -> Tuple[List[_Member], bool]:
    """``(members, truncated)``. Raises on a corrupt archive; callers turn that into an error dict."""
    members: List[_Member] = []
    if kind == "zip":
        with zipfile.ZipFile(archive, "r") as zf:
            for info in zf.infolist():
                if len(members) >= max_members:
                    return members, True
                members.append((info.filename, info.file_size, _zip_kind(info)))
    else:
        with tarfile.open(archive, "r:*") as tf:
            for member in tf:  # iterate lazily: getmembers() would read a multi-million-entry tar whole
                if len(members) >= max_members:
                    return members, True
                members.append((member.name, member.size, _tar_kind(member)))
    return members, False


def _max_members(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else DEFAULT_MAX_MEMBERS


def _max_bytes(mb: Any, default_mb: float) -> int:
    try:
        value = float(mb)
    except (TypeError, ValueError):
        value = default_mb
    if not math.isfinite(value):
        value = default_mb
    return int(max(0.1, value) * 1024 * 1024)


class ArchiveExtractor:
    """Safe extractor and manager for ZIP and TAR archives with Zip-Slip and decompression-bomb defenses."""

    @staticmethod
    def list_archive_contents(
        archive_path: str,
        max_members: int = DEFAULT_MAX_MEMBERS,
        base_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List files, directories, uncompressed sizes, and file count inside an archive without extraction.

        ``truncated`` is true when the archive holds more than ``max_members`` entries.
        """
        src = _to_path(archive_path)
        if src is None:
            return {"valid": False, "error": f"Invalid archive path: {archive_path!r}"}
        if not _inside(src, base_dir):
            return {"valid": False, "error": f"Path is outside the allowed directory: {archive_path}"}
        if not src.exists() or not src.is_file():
            return {"valid": False, "error": f"Archive file not found: {archive_path}"}

        kind = _kind_of(src)
        if kind is None:
            return _not_an_archive(src, "valid")
        try:
            scanned, truncated = _scan(src, kind, _max_members(max_members))
        except Exception as exc:  # corrupt archive, permissions, ...
            return {"valid": False, "error": f"Could not read archive: {type(exc).__name__}: {exc}"}

        members = [
            {
                "name": name,
                "size_bytes": size,
                "is_dir": mtype == "dir",
                "member_type": mtype,
                "is_pdf": mtype == "file" and name.lower().endswith(".pdf"),
            }
            for name, size, mtype in scanned
        ]
        total_uncompressed = sum(size for _, size, mtype in scanned if mtype == "file")
        result: Dict[str, Any] = {
            "valid": True,
            "file_path": str(src.resolve()),
            "archive_format": kind,
            "total_members": len(members),
            "uncompressed_bytes": total_uncompressed,
            "uncompressed_mb": round(total_uncompressed / (1024 * 1024), 2),
            "members": members,
            "truncated": truncated,
        }
        if truncated:
            result["warnings"] = [f"Only the first {len(members)} entries are listed; the archive has more."]
        return result

    @staticmethod
    def audit_archive_safety(archive_path: str, base_dir: Optional[str] = None) -> Dict[str, Any]:
        """Audit an archive for Zip Slip path traversal, links/devices and decompression-bomb attacks.

        ``safe`` is true only when the archive was read *completely* and nothing suspicious was found.
        An unsupported, unreadable or oversized-entry-count archive is ``safe: False`` with an error --
        never an all-clear for something that was not inspected.
        """
        src = _to_path(archive_path)
        if src is None:
            return {"safe": False, "error": f"Invalid archive path: {archive_path!r}"}
        if not _inside(src, base_dir):
            return {"safe": False, "error": f"Path is outside the allowed directory: {archive_path}"}
        if not src.exists():
            return {"safe": False, "error": "File does not exist"}
        if not src.is_file():
            return {"safe": False, "error": f"Path is not a file: {archive_path}"}

        kind = _kind_of(src)
        if kind is None:
            result = _not_an_archive(src, "safe")
            result["audited"] = False
            return result

        try:
            compressed_size = src.stat().st_size
            members, truncated = _scan(src, kind, DEFAULT_MAX_MEMBERS)
        except Exception as exc:
            return {"safe": False, "audited": False, "error": f"Could not read archive: {type(exc).__name__}: {exc}"}

        suspicious: List[str] = []
        links: List[str] = []
        specials: List[str] = []
        total_uncompressed = 0
        for name, size, mtype in members:
            if mtype == "file":
                total_uncompressed += size
            if not _safe_name(name):
                suspicious.append(name)
            if mtype == "link":
                links.append(name)
            elif mtype == "special":
                specials.append(name)

        ratio = round(total_uncompressed / max(1, compressed_size), 2)
        is_bomb = ratio > BOMB_RATIO and total_uncompressed > BOMB_MIN_BYTES
        is_safe = not suspicious and not links and not specials and not is_bomb and not truncated

        result: Dict[str, Any] = {
            "safe": is_safe,
            "audited": True,
            "archive_format": kind,
            "members_checked": len(members),
            "compression_ratio": ratio,
            "is_decompression_bomb": is_bomb,
            "path_traversal_attempts": suspicious,
            "link_members": links,
            "special_members": specials,
            "uncompressed_size_bytes": total_uncompressed,
            "truncated": truncated,
        }
        if truncated:
            result["error"] = (
                f"Archive has more than {DEFAULT_MAX_MEMBERS} entries; only the first {len(members)} were "
                "audited, so it is not reported safe."
            )
        return result

    @staticmethod
    def extract_archive(
        archive_path: str,
        destination_dir: str,
        max_size_mb: float = 100.0,
        max_members: int = DEFAULT_MAX_MEMBERS,
        base_dir: Optional[str] = None,
        time_budget_s: float = DEFAULT_BUDGET_S,
    ) -> Dict[str, Any]:
        """Safely extract a .zip or .tar.* archive into destination_dir.

        ``time_budget_s`` (default 300, at most 600) is checked while streaming; if it runs out the
        result is ``extracted: False``, ``timed_out: True`` with the members written so far listed.

        All members are validated before anything is written (path traversal, links, devices, member
        count, declared total size). Links and devices are refused, never created. Written bytes are
        counted, so the size cap holds even if an entry lies about its size.
        """
        src, dest = _to_path(archive_path), _to_path(destination_dir)
        if src is None or dest is None:
            return {"extracted": False, "error": "Invalid archive or destination path."}
        if not _inside(src, base_dir) or not _inside(dest, base_dir):
            return {"extracted": False, "error": "Path is outside the allowed directory."}
        if not src.exists() or not src.is_file():
            return {"extracted": False, "error": f"Archive file not found: {archive_path}"}

        kind = _kind_of(src)
        if kind is None:
            return _not_an_archive(src, "extracted")

        max_bytes = _max_bytes(max_size_mb, 100.0)
        limit = _max_members(max_members)

        try:
            members, truncated = _scan(src, kind, limit)
            if truncated:
                return {"extracted": False, "error": f"Archive has more than {limit} entries; refusing to extract."}

            # ---- phase 1: validate everything before touching the disk ----
            declared = 0
            for name, size, mtype in members:
                if not _safe_name(name):
                    return {"extracted": False, "error": f"Path traversal blocked for entry: {name}"}
                if mtype in ("link", "special"):
                    return {
                        "extracted": False,
                        "error": f"Refusing archive: entry '{name}' is a link or special file (not extracted).",
                    }
                if mtype == "file":
                    declared += size
                    if declared > max_bytes:
                        return {"extracted": False, "error": f"Archive exceeds max size limit ({max_size_mb} MB)."}

            if dest.exists() and not dest.is_dir():
                return {"extracted": False, "error": f"Destination is not a directory: {destination_dir}"}
            dest.mkdir(parents=True, exist_ok=True)
            resolved_dest = dest.resolve()

            targets: Dict[str, Path] = {}
            for name, _size, _mtype in members:
                target = (resolved_dest / name.replace("\\", "/")).resolve()
                if target != resolved_dest and resolved_dest not in target.parents:
                    return {"extracted": False, "error": f"Path traversal blocked for entry: {name}"}
                targets[name] = target

            # ---- phase 2: write ----
            total_bytes = 0
            extracted: List[str] = []
            deadline = Deadline(time_budget_s)
            if kind == "zip":
                with zipfile.ZipFile(src, "r") as zf:
                    for info in zf.infolist():
                        target = targets[info.filename]
                        if info.is_dir():
                            target.mkdir(parents=True, exist_ok=True)
                            continue
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(info) as s_file:
                            total_bytes = ArchiveExtractor._write_stream(
                                s_file, target, total_bytes, max_bytes, deadline
                            )
                        extracted.append(info.filename)
            else:
                with tarfile.open(src, "r:*") as tf:
                    for member in tf:
                        target = targets[member.name]
                        if member.isdir():
                            target.mkdir(parents=True, exist_ok=True)
                            continue
                        stream = tf.extractfile(member)
                        if stream is None:
                            continue
                        target.parent.mkdir(parents=True, exist_ok=True)
                        total_bytes = ArchiveExtractor._write_stream(stream, target, total_bytes, max_bytes, deadline)
                        extracted.append(member.name)

            return {
                "extracted": True,
                "destination": str(resolved_dest),
                "extracted_count": len(extracted),
                "total_bytes": total_bytes,
                "members": extracted,
            }
        except _TimedOut:
            return {
                "extracted": False,
                "timed_out": True,
                "error": Deadline(time_budget_s).message("extraction is incomplete."),
                "members": extracted,
            }
        except _SizeLimit:
            return {"extracted": False, "error": f"Archive exceeds max size limit ({max_size_mb} MB)."}
        except Exception as exc:
            return {"extracted": False, "error": f"{type(exc).__name__}: {exc}"}

    @staticmethod
    def _write_stream(
        source: Any, target: Path, written_so_far: int, max_bytes: int, deadline: Optional[Deadline] = None
    ) -> int:
        """Stream ``source`` to ``target`` counting real bytes; never follows an existing symlink."""
        if target.is_symlink():
            raise OSError(f"refusing to write through a symlink: {target.name}")
        total = written_so_far
        try:
            with open(target, "wb") as out:
                while chunk := source.read(_CHUNK):
                    total += len(chunk)
                    if total > max_bytes:
                        raise _SizeLimit()
                    if deadline is not None and deadline.expired():
                        raise _TimedOut()
                    out.write(chunk)
        except (_SizeLimit, _TimedOut):
            try:
                target.unlink()
            except OSError:
                pass
            raise
        return total

    @staticmethod
    def extract_single_file(
        archive_path: str,
        target_filename: str,
        destination_path: str,
        max_size_mb: float = DEFAULT_MAX_MEMBER_MB,
        base_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Safely extract a single regular-file member from an archive to ``destination_path``."""
        src, dest = _to_path(archive_path), _to_path(destination_path)
        if src is None or dest is None or not isinstance(target_filename, str) or not target_filename:
            return {"extracted": False, "error": "Invalid archive, member or destination path."}
        if not _inside(src, base_dir) or not _inside(dest, base_dir):
            return {"extracted": False, "error": "Path is outside the allowed directory."}
        if not src.exists() or not src.is_file():
            return {"extracted": False, "error": f"Archive not found: {archive_path}"}
        kind = _kind_of(src)
        if kind is None:
            return _not_an_archive(src, "extracted")
        if dest.is_dir():
            return {"extracted": False, "error": f"Destination is a directory: {destination_path}"}

        max_bytes = _max_bytes(max_size_mb, DEFAULT_MAX_MEMBER_MB)
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            if kind == "zip":
                with zipfile.ZipFile(src, "r") as zf:
                    try:
                        info = zf.getinfo(target_filename)
                    except KeyError:
                        return {"extracted": False, "error": f"File '{target_filename}' not in archive."}
                    if _zip_kind(info) != "file":
                        return {"extracted": False, "error": f"'{target_filename}' is not a regular file."}
                    if info.file_size > max_bytes:
                        return {"extracted": False, "error": f"Member exceeds max size limit ({max_size_mb} MB)."}
                    with zf.open(info) as s_file:
                        written = ArchiveExtractor._write_stream(s_file, dest, 0, max_bytes)
            else:
                with tarfile.open(src, "r:*") as tf:
                    try:
                        member = tf.getmember(target_filename)
                    except KeyError:
                        return {"extracted": False, "error": f"File '{target_filename}' not in archive."}
                    if _tar_kind(member) != "file":
                        return {"extracted": False, "error": f"'{target_filename}' is not a regular file."}
                    if member.size > max_bytes:
                        return {"extracted": False, "error": f"Member exceeds max size limit ({max_size_mb} MB)."}
                    stream = tf.extractfile(member)
                    if stream is None:
                        return {"extracted": False, "error": "Cannot read tar member file."}
                    written = ArchiveExtractor._write_stream(stream, dest, 0, max_bytes)
            return {
                "extracted": True,
                "file_path": str(dest.resolve()),
                "bytes_written": written,
                "is_pdf": target_filename.lower().endswith(".pdf"),
            }
        except _SizeLimit:
            return {"extracted": False, "error": f"Member exceeds max size limit ({max_size_mb} MB)."}
        except Exception as exc:
            return {"extracted": False, "error": str(exc)}

    # ------------------------------------------------------------------ reading member contents

    @staticmethod
    def _open_member_bytes(src: Path, kind: str, name: str, max_bytes: int) -> bytes:
        """Bytes of one regular member, capped. Raises ``SourceReadError`` with a user-safe message."""
        try:
            if kind == "zip":
                with zipfile.ZipFile(src, "r") as zf:
                    try:
                        info = zf.getinfo(name)
                    except KeyError:
                        raise SourceReadError(f"'{name}' is not in the archive.") from None
                    if _zip_kind(info) != "file":
                        raise SourceReadError(f"'{name}' is not a regular file.")
                    if info.file_size > max_bytes:
                        raise SourceReadError(f"'{name}' is too large to read ({info.file_size} bytes).")
                    with zf.open(info) as handle:
                        data = handle.read(max_bytes + 1)
            else:
                with tarfile.open(src, "r:*") as tf:
                    try:
                        member = tf.getmember(name)
                    except KeyError:
                        raise SourceReadError(f"'{name}' is not in the archive.") from None
                    if _tar_kind(member) != "file":
                        raise SourceReadError(f"'{name}' is not a regular file.")
                    if member.size > max_bytes:
                        raise SourceReadError(f"'{name}' is too large to read ({member.size} bytes).")
                    stream = tf.extractfile(member)
                    if stream is None:
                        raise SourceReadError(f"'{name}' cannot be read.")
                    data = stream.read(max_bytes + 1)
        except SourceReadError:
            raise
        except RuntimeError as exc:  # encrypted zip member
            raise SourceReadError(f"'{name}' cannot be read: {exc}") from exc
        except Exception as exc:
            raise SourceReadError(f"Could not read '{name}': {type(exc).__name__}: {exc}") from exc
        if len(data) > max_bytes:
            raise SourceReadError(f"'{name}' is too large to read (more than {max_bytes} bytes).")
        return data

    @classmethod
    def read_archive_member(
        cls,
        archive_path: str,
        member_name: str,
        max_size_mb: float = DEFAULT_MAX_MEMBER_MB,
        base_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return the text of one archive member without writing it to disk.

        A PDF member is read in full (every page); ``input`` reports pages read and any scanned page
        that needs OCR, and ``warnings`` says so. Binary non-PDF members are refused, not decoded.
        """
        src = _to_path(archive_path)
        if src is None or not isinstance(member_name, str) or not member_name:
            return {"read": False, "error": "Invalid archive path or member name."}
        if not _inside(src, base_dir):
            return {"read": False, "error": f"Path is outside the allowed directory: {archive_path}"}
        if not src.exists() or not src.is_file():
            return {"read": False, "error": f"Archive not found: {archive_path}"}
        kind = _kind_of(src)
        if kind is None:
            return _not_an_archive(src, "read")
        try:
            data = cls._open_member_bytes(src, kind, member_name, _max_bytes(max_size_mb, DEFAULT_MAX_MEMBER_MB))
            read = read_source_bytes(data, name=member_name)
        except SourceReadError as exc:
            return {"read": False, "member": member_name, "error": str(exc)}
        result: Dict[str, Any] = {
            "read": True,
            "member": member_name,
            "text": read.text,
            "input": read.describe(),
            "complete": read.complete,
        }
        if read.warnings():
            result["warnings"] = read.warnings()
        return result

    @classmethod
    def read_archive_documents(
        cls,
        archive_path: str,
        max_members: int = 1000,
        max_size_mb: float = DEFAULT_MAX_MEMBER_MB,
        base_dir: Optional[str] = None,
        time_budget_s: float = DEFAULT_BUDGET_S,
    ) -> Dict[str, Any]:
        """Read every readable text/PDF member of an archive; PDFs are read in full.

        ``time_budget_s`` (default 300, at most 600) is checked between members; members not reached
        are listed under ``skipped`` and ``timed_out`` is true.

        Members that cannot be read (binary, too large, corrupt or password-protected PDF) are listed
        under ``skipped`` with the reason -- never dropped silently. ``complete`` is true only when
        nothing was skipped, no PDF page was unreadable and no member was left past ``max_members``.
        """
        src = _to_path(archive_path)
        if src is None:
            return {"read": False, "error": f"Invalid archive path: {archive_path!r}"}
        if not _inside(src, base_dir):
            return {"read": False, "error": f"Path is outside the allowed directory: {archive_path}"}
        if not src.exists() or not src.is_file():
            return {"read": False, "error": f"Archive not found: {archive_path}"}
        kind = _kind_of(src)
        if kind is None:
            return _not_an_archive(src, "read")

        try:
            members, scan_truncated = _scan(src, kind, DEFAULT_MAX_MEMBERS)
        except Exception as exc:
            return {"read": False, "error": f"Could not read archive: {type(exc).__name__}: {exc}"}

        limit = _max_members(max_members) if max_members is not None else 1000
        documents: List[Dict[str, Any]] = []
        skipped: List[Dict[str, str]] = []
        warnings: List[str] = []
        files = [name for name, _size, mtype in members if mtype == "file"]
        deadline = Deadline(time_budget_s)
        timed_out = False
        for name in files[:limit]:
            if deadline.expired():
                timed_out = True
                skipped.append({"member": name, "reason": "time budget used up before this member was read"})
                continue
            if not _safe_name(name):
                skipped.append({"member": name, "reason": "unsafe path"})
                continue
            item = cls.read_archive_member(str(src), name, max_size_mb=max_size_mb)
            if not item.get("read"):
                skipped.append({"member": name, "reason": str(item.get("error"))})
                continue
            documents.append(
                {"member": name, "text": item["text"], "input": item["input"], "complete": item["complete"]}
            )
            warnings.extend(f"{name}: {w}" for w in item.get("warnings", []))
        left_over = max(0, len(files) - limit)
        if left_over:
            warnings.append(f"{left_over} member(s) beyond max_members={limit} were not read.")
        if scan_truncated:
            warnings.append(f"Archive has more than {DEFAULT_MAX_MEMBERS} entries; the rest were not read.")

        if timed_out:
            warnings.append(deadline.message("the members listed under 'skipped' were not read."))
        complete = (
            not skipped and not left_over and not scan_truncated and all(d["complete"] for d in documents)
        )
        return {
            "read": True,
            "archive_format": kind,
            "documents_read": len(documents),
            "documents": documents,
            "skipped": skipped,
            "timed_out": timed_out,
            "complete": complete,
            "warnings": warnings,
        }

    # ------------------------------------------------------------------ creation

    @staticmethod
    def create_archive(
        source_dir: str,
        output_archive_path: str,
        format_type: str = "zip",
        max_files: int = 50_000,
        max_total_mb: float = 1024.0,
        base_dir: Optional[str] = None,
        time_budget_s: float = DEFAULT_BUDGET_S,
    ) -> Dict[str, Any]:
        """Create a zip or tar(.gz) archive from a directory.

        ``time_budget_s`` (default 300, at most 600) is checked between files; if it runs out nothing
        is left behind and the result is ``created: False`` with ``timed_out: True``.

        Symlinks are skipped (a link inside the tree could pull a file from outside it into the
        archive) and reported under ``skipped_links``; the output file itself is never archived.
        """
        src, out = _to_path(source_dir), _to_path(output_archive_path)
        if src is None or out is None:
            return {"created": False, "error": "Invalid source or output path."}
        if not _inside(src, base_dir) or not _inside(out, base_dir):
            return {"created": False, "error": "Path is outside the allowed directory."}
        if not src.exists() or not src.is_dir():
            return {"created": False, "error": f"Source directory not found: {source_dir}"}
        if not isinstance(format_type, str):
            return {"created": False, "error": f"Unsupported format: {format_type!r}"}
        fmt = format_type.strip().lower()
        if fmt not in ("zip", "tar.gz", "tgz", "tar"):
            return {"created": False, "error": f"Unsupported format: {fmt}"}
        if out.is_dir():
            return {"created": False, "error": f"Output path is a directory: {output_archive_path}"}

        max_total = _max_bytes(max_total_mb, 1024.0)
        out_resolved = out.resolve()
        src_resolved = src.resolve()
        files: List[Tuple[Path, str]] = []
        skipped_links: List[str] = []
        total = 0
        deadline = Deadline(time_budget_s)
        try:
            for current, dirs, names in os.walk(src_resolved, followlinks=False):
                dirs.sort()
                for name in sorted(names):
                    path = Path(current) / name
                    rel = path.relative_to(src_resolved).as_posix()
                    if path.is_symlink():
                        skipped_links.append(rel)
                        continue
                    if path.resolve() == out_resolved or not path.is_file():
                        continue
                    if len(files) >= max_files:
                        return {"created": False, "error": f"Source has more than {max_files} files; refusing."}
                    total += path.stat().st_size
                    if total > max_total:
                        return {"created": False, "error": f"Source exceeds the size limit ({max_total_mb} MB)."}
                    files.append((path, rel))
                skipped_links.extend(
                    (Path(current) / d).relative_to(src_resolved).as_posix()
                    for d in dirs
                    if (Path(current) / d).is_symlink()
                )

            out.parent.mkdir(parents=True, exist_ok=True)
            try:
                if fmt == "zip":
                    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, strict_timestamps=False) as zf:
                        for path, rel in files:
                            if deadline.expired():
                                raise _TimedOut()
                            zf.write(path, rel)
                else:
                    mode = "w:gz" if "gz" in fmt else "w"
                    with tarfile.open(out, mode) as tf:
                        for path, rel in files:
                            if deadline.expired():
                                raise _TimedOut()
                            tf.add(path, arcname=rel, recursive=False)
            except BaseException:
                try:
                    out.unlink()
                except OSError:
                    pass
                raise

            return {
                "created": True,
                "archive_path": str(out.resolve()),
                "files_count": len(files),
                "archive_size_bytes": out.stat().st_size,
                "skipped_links": skipped_links,
            }
        except _TimedOut:
            return {"created": False, "timed_out": True, "error": deadline.message("no archive was written.")}
        except Exception as exc:
            return {"created": False, "error": str(exc)}
