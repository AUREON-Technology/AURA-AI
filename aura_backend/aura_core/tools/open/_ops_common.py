"""Shared guards for the process / ops / network tools in this folder.

Three things the individual classes each got wrong in their own way:

* **Child processes.** ``Popen`` without a process group leaves a server's children behind when the
  parent is stopped (``npm run dev`` is a shell plus node plus a watcher), and an un-drained
  ``stdout=PIPE`` blocks the child as soon as the pipe buffer (about 64 KiB) fills -- a chatty dev
  server then "hangs" while looking alive. :func:`run_group` and :class:`StreamTail` fix both.
* **Outbound URLs.** ``urllib.request.urlopen`` happily opens ``file://`` and follows redirects to any
  host, so a "health check" or "webhook" tool becomes a way to read local files or reach cloud
  metadata endpoints. :func:`check_url` and :func:`open_guarded` apply one policy everywhere.
* **Path confinement.** :func:`confine` is the one place a user-supplied directory is resolved and
  held inside an optional ``base_dir``.

Stdlib only: this module sits in the ``tools`` engine layer.
"""

from __future__ import annotations

import ipaddress
import os
import signal
import socket
import subprocess
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, Callable, Optional, Sequence, Union

#: Output kept per stream. A test run or dev server can print gigabytes; the tail is what matters.
DEFAULT_TAIL_BYTES = 64 * 1024

#: Variables a CLI such as ``docker`` needs in addition to :func:`_process.scrubbed_env`'s allowlist.
CLI_ENV_PASSTHROUGH: tuple[str, ...] = (
    "HOME",
    "USERPROFILE",
    "DOCKER_HOST",
    "DOCKER_CONFIG",
    "DOCKER_CONTEXT",
    "XDG_RUNTIME_DIR",
    "XDG_CONFIG_HOME",
)


# -- paths ------------------------------------------------------------------------------------


class PathRefused(Exception):
    """A path was invalid or outside ``base_dir``. ``str(exc)`` is safe to show the user."""


def confine(
    path: Union[str, os.PathLike[str]],
    base_dir: Union[str, os.PathLike[str], None] = None,
    *,
    must_exist: bool = True,
    kind: str = "any",
) -> Path:
    """Resolve ``path`` and require that it lies inside ``base_dir`` (when given).

    ``kind`` is ``"file"``, ``"dir"`` or ``"any"``. Symlinks are resolved first, so a link inside
    ``base_dir`` that points outside it is refused.
    """
    if isinstance(path, bytes) or not isinstance(path, (str, os.PathLike)):
        raise PathRefused(f"A path is required (got {type(path).__name__}).")
    text = os.fspath(path)
    if not text.strip():
        raise PathRefused("A path is required (got an empty string).")
    if "\x00" in text:
        raise PathRefused("Invalid path: contains a NUL byte.")
    try:
        resolved = Path(text).expanduser().resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise PathRefused(f"Invalid path: {text!r}") from exc
    if base_dir is not None:
        try:
            root = Path(base_dir).expanduser().resolve()
            resolved.relative_to(root)
        except (OSError, RuntimeError, ValueError):
            raise PathRefused(f"Path is outside the allowed directory: {text}") from None
    if must_exist and not resolved.exists():
        raise PathRefused(f"Path not found: {text}")
    if resolved.exists():
        if kind == "file" and not resolved.is_file():
            raise PathRefused(f"Path is not a file: {text}")
        if kind == "dir" and not resolved.is_dir():
            raise PathRefused(f"Path is not a directory: {text}")
    return resolved


# -- bounded text -----------------------------------------------------------------------------


def tail_text(data: Union[str, bytes, None], limit: int = DEFAULT_TAIL_BYTES) -> str:
    """The last ``limit`` characters of ``data``, with a marker when something was cut."""
    if data is None:
        return ""
    if isinstance(data, bytes):
        data = data.decode("utf-8", errors="replace")
    if len(data) <= limit:
        return data
    return f"[... {len(data) - limit} earlier characters omitted ...]\n" + data[-limit:]


class StreamTail:
    """Drain a pipe on a thread, keeping only the last ``limit`` bytes.

    Reading continuously is what stops the child from blocking on a full pipe; keeping only the tail
    is what stops a noisy child from exhausting the gateway's memory.
    """

    def __init__(self, stream: Optional[IO[bytes]], limit: int = DEFAULT_TAIL_BYTES) -> None:
        self._limit = limit
        self._chunks: deque[bytes] = deque()
        self._size = 0
        self._dropped = 0
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        if stream is not None:
            self._thread = threading.Thread(target=self._pump, args=(stream,), daemon=True)
            self._thread.start()

    def _pump(self, stream: IO[bytes]) -> None:
        try:
            while True:
                chunk = stream.read1(8192) if hasattr(stream, "read1") else stream.read(8192)
                if not chunk:
                    break
                with self._lock:
                    self._chunks.append(chunk)
                    self._size += len(chunk)
                    while self._size > self._limit and self._chunks:
                        dropped = self._chunks.popleft()
                        self._size -= len(dropped)
                        self._dropped += len(dropped)
        except (OSError, ValueError):
            pass  # the pipe was closed under us: that is the normal end of a stopped child

    def join(self, timeout: float = 2.0) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def text(self) -> str:
        with self._lock:
            raw = b"".join(self._chunks)
            dropped = self._dropped
        out = raw.decode("utf-8", errors="replace")
        if dropped:
            out = f"[... {dropped} earlier bytes omitted ...]\n" + out
        return out


# -- processes --------------------------------------------------------------------------------


def group_popen_kwargs() -> dict[str, Any]:
    """``Popen`` kwargs that put the child in its own process group / session."""
    if os.name == "nt":  # pragma: no cover - exercised on Windows only
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


def signal_group(proc: "subprocess.Popen[Any]", sig: int) -> None:
    """Send ``sig`` to the child's whole process group (POSIX) or to the child (elsewhere)."""
    try:
        if os.name == "nt":  # pragma: no cover
            if sig == getattr(signal, "SIGKILL", signal.SIGTERM):
                proc.kill()
            else:
                proc.terminate()
        else:
            os.killpg(proc.pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def stop_group(proc: "subprocess.Popen[Any]", grace_s: float = 5.0) -> Optional[int]:
    """SIGTERM the group, wait ``grace_s``, then SIGKILL it. Returns the exit code."""
    if proc.poll() is None:
        signal_group(proc, signal.SIGTERM)
        try:
            proc.wait(timeout=max(0.0, grace_s))
        except subprocess.TimeoutExpired:
            pass
    # Kill stragglers in the group even when the leader exited promptly: a dev server's children
    # routinely outlive the shell that started them.
    signal_group(proc, getattr(signal, "SIGKILL", signal.SIGTERM))
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:  # pragma: no cover - SIGKILL does not time out
        pass
    return proc.returncode


@dataclass(frozen=True)
class GroupResult:
    returncode: Optional[int]
    stdout: str
    stderr: str
    timed_out: bool


def _read_tail(handle: IO[bytes], limit: int) -> str:
    handle.flush()
    size = handle.seek(0, os.SEEK_END)
    start = max(0, size - limit)
    handle.seek(start)
    text = handle.read().decode("utf-8", errors="replace")
    return (f"[... {start} earlier bytes omitted ...]\n" + text) if start else text


def run_group(
    argv: Sequence[str],
    *,
    cwd: Union[str, os.PathLike[str], None],
    timeout_s: float,
    env: Optional[dict[str, str]],
    max_output: int = DEFAULT_TAIL_BYTES,
) -> GroupResult:
    """Run ``argv`` (never through a shell) in its own process group with a hard timeout.

    On timeout the *whole group* is killed, so a test or build that spawned its own children leaves
    nothing behind. Output goes to temp files and only the tail is returned, so a runaway child cannot
    exhaust memory. ``FileNotFoundError`` / ``NotADirectoryError`` / ``PermissionError`` propagate.
    """
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(
            list(argv),
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=err,
            **group_popen_kwargs(),
        )
        timed_out = False
        try:
            try:
                proc.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                timed_out = True
        finally:
            # Always: leaves nothing running whether we finished, timed out or were interrupted.
            signal_group(proc, getattr(signal, "SIGKILL", signal.SIGTERM))
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:  # pragma: no cover
                pass
        return GroupResult(
            returncode=None if timed_out else proc.returncode,
            stdout=_read_tail(out, max_output),
            stderr=_read_tail(err, max_output),
            timed_out=timed_out,
        )


# -- outbound URLs ----------------------------------------------------------------------------


class UrlRefused(Exception):
    """The URL violates the outbound policy. ``str(exc)`` is safe to show the user."""


_MAX_URL_LEN = 2048
_METADATA_HOSTS = {"metadata.google.internal", "metadata", "instance-data"}


def redact_url(url: Any) -> str:
    """``scheme://host`` plus an ellipsis. Webhook URLs carry their secret in the path or query."""
    try:
        parts = urllib.parse.urlsplit(str(url))
        if parts.scheme and parts.hostname:
            port = f":{parts.port}" if parts.port else ""
            return f"{parts.scheme}://{parts.hostname}{port}/..." if (parts.path or parts.query) else (
                f"{parts.scheme}://{parts.hostname}{port}"
            )
    except ValueError:
        pass
    return "<invalid url>"


def _classify(ip: Union[ipaddress.IPv4Address, ipaddress.IPv6Address]) -> str:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if (
        ip.is_unspecified
        or ip.is_multicast
        or ip.is_link_local
        or ip.is_reserved
        or str(ip) in {"169.254.169.254", "fd00:ec2::254"}
    ):
        return "blocked"
    if ip.is_loopback:
        return "loopback"
    if ip.is_global:
        return "public"
    return "private"


def check_url(url: Any, *, allow_loopback: bool = False, allow_private: bool = False) -> str:
    """Validate ``url`` against the outbound policy and return it unchanged. Raises :class:`UrlRefused`.

    * only ``http`` and ``https``; no embedded credentials; no control characters;
    * every address the host resolves to is checked, so a name that points at ``127.0.0.1`` or
      ``169.254.169.254`` is caught as well as a literal IP;
    * link-local, multicast, unspecified and cloud-metadata addresses are always refused;
      loopback and private ranges only when the caller opts in.

    Known limit: the name is resolved here and again by the HTTP client, so a hostile DNS server can
    answer differently the second time (rebinding). A gateway that must withstand a hostile resolver
    needs an egress proxy; this stops the direct and the redirect routes.
    """
    if not isinstance(url, str) or not url.strip():
        raise UrlRefused("A URL is required.")
    if len(url) > _MAX_URL_LEN:
        raise UrlRefused("URL is too long.")
    if any(ord(c) < 33 or ord(c) == 127 for c in url):
        raise UrlRefused("URL contains whitespace or control characters.")
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError:
        raise UrlRefused("URL is malformed.") from None
    if parts.scheme.lower() not in ("http", "https"):
        raise UrlRefused(f"Only http and https URLs are allowed (got {parts.scheme or 'none'!r}).")
    host = parts.hostname
    if not host:
        raise UrlRefused("URL has no host.")
    if parts.username or parts.password:
        raise UrlRefused("URLs with embedded credentials are not allowed.")
    if host.lower().rstrip(".") in _METADATA_HOSTS:
        raise UrlRefused("Cloud metadata hosts are not allowed.")
    try:
        infos = socket.getaddrinfo(host, port or (443 if parts.scheme.lower() == "https" else 80),
                                   type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, OSError):
        raise UrlRefused(f"Cannot resolve host {host!r}.") from None
    if not infos:
        raise UrlRefused(f"Cannot resolve host {host!r}.")
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0].split("%", 1)[0])
        except ValueError:
            raise UrlRefused(f"Host {host!r} resolved to an unreadable address.") from None
        kind = _classify(ip)
        if kind == "blocked":
            raise UrlRefused(f"Host {host!r} resolves to a blocked address (link-local, metadata or reserved).")
        if kind == "loopback" and not allow_loopback:
            raise UrlRefused(f"Host {host!r} is a loopback address; not allowed for this call.")
        if kind == "private" and not allow_private:
            raise UrlRefused(f"Host {host!r} is on a private network; not allowed for this call.")
    return url


class _GuardedRedirect(urllib.request.HTTPRedirectHandler):
    """Re-applies the URL policy to every redirect hop (or refuses redirects outright)."""

    max_redirections = 3

    def __init__(self, validate: Optional[Callable[[str], Any]]) -> None:
        self._validate = validate

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        if self._validate is None:
            raise urllib.error.HTTPError(
                req.full_url, code, f"redirect to {redact_url(newurl)} was not followed", headers, fp
            )
        try:
            self._validate(newurl)
        except UrlRefused as exc:
            raise urllib.error.HTTPError(
                req.full_url, code, f"redirect refused: {exc}", headers, fp
            ) from None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_guarded(
    request: Union[str, urllib.request.Request],
    *,
    timeout: float,
    allow_loopback: bool = False,
    allow_private: bool = False,
    follow_redirects: bool = True,
):
    """``urlopen`` under :func:`check_url`'s policy, for the first URL and for every redirect."""
    url = request if isinstance(request, str) else request.full_url
    check_url(url, allow_loopback=allow_loopback, allow_private=allow_private)

    def validate(new_url: str) -> None:
        check_url(new_url, allow_loopback=allow_loopback, allow_private=allow_private)

    opener = urllib.request.build_opener(_GuardedRedirect(validate if follow_redirects else None))
    return opener.open(request, timeout=clamp_timeout(timeout))


def clamp_timeout(value: Any, default: float = 5.0, maximum: float = 30.0) -> float:
    """A positive, finite, bounded timeout. ``None`` would mean "wait forever" to urllib."""
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return default
    if seconds != seconds or seconds <= 0:  # NaN or non-positive
        return default
    return min(seconds, maximum)
