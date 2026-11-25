"""Shared guards for the process / ops / network tools in this folder.

Three things the individual classes each got wrong in their own way:

* **Child processes.** ``Popen`` without a process group leaves a server's children behind when the
  parent is stopped (``npm run dev`` is a shell plus node plus a watcher), and an un-drained
  ``stdout=PIPE`` blocks the child as soon as the pipe buffer (about 64 KiB) fills -- a chatty dev
  server then "hangs" while looking alive. :func:`run_group` and :class:`StreamTail` fix both.
* **Outbound URLs.** ``urllib.request.urlopen`` happily opens ``file://`` and follows redirects to any
  host, so a "health check" or "webhook" tool becomes a way to read local files, reach cloud
  metadata endpoints or call the AURA gateway itself. :func:`check_url` and :func:`open_guarded`
  apply one policy everywhere, with **no opt-out**: only public hosts, never the platform's own.
* **Path confinement.** :func:`confine` is the one place a user-supplied directory is resolved and
  held inside an optional ``base_dir``.

Stdlib only: this module sits in the ``tools`` engine layer.
"""

from __future__ import annotations

import ipaddress
import os
import posixpath
import re
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
from typing import IO, Any, Callable, Optional, Sequence, TypeVar, Union

from ._process import scrubbed_env

_T = TypeVar("_T")
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
    env: Optional[dict[str, str]] = None,
    max_output: int = DEFAULT_TAIL_BYTES,
) -> GroupResult:
    """Run ``argv`` (never through a shell) in its own process group with a hard timeout.

    On timeout the *whole group* is killed, so a test or build that spawned its own children leaves
    nothing behind. Output goes to temp files and only the tail is returned, so a runaway child cannot
    exhaust memory. ``FileNotFoundError`` / ``NotADirectoryError`` / ``PermissionError`` propagate.
    ``env=None`` means a *scrubbed* environment, never the gateway's own.
    """
    if env is None:
        env = scrubbed_env()
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
#: Names that are never public hosts: docker/compose service names resolve to single labels
#: (``gateway``, ``postgres``), Railway's private network is ``*.railway.internal``, and the rest are
#: the usual intranet suffixes.
_DENIED_SUFFIXES = (
    ".internal", ".local", ".localhost", ".lan", ".home", ".corp", ".intranet", ".home.arpa",
    ".svc", ".cluster", ".localdomain",
)
#: The platform's own public hosts. A tool that could call them could call the AURA API as the
#: gateway's network identity; subdomains are covered.
_PLATFORM_HOSTS = ("aureon-ai.tech", "console.aureon-ai.tech")
_PLATFORM_ENV_VARS = (
    "AURA_PUBLIC_URL", "AURA_PUBLIC_BASE_URL", "AURA_BASE_URL", "PUBLIC_BASE_URL",
    "RAILWAY_PUBLIC_DOMAIN", "RAILWAY_PRIVATE_DOMAIN",
)
#: Path prefixes that are the gateway's API / health surface, refused on *any* host.
_DENIED_PATH_ROOTS = ("health",)


def redact_url(url: Any) -> str:
    """``scheme://host`` plus an ellipsis. Webhook URLs carry their secret in the path or query."""
    try:
        parts = urllib.parse.urlsplit(str(url))
        if parts.scheme and parts.hostname:
            port = f":{parts.port}" if parts.port else ""
            base = f"{parts.scheme}://{parts.hostname}{port}"
            return base + "/..." if (parts.path or parts.query) else base
    except ValueError:
        pass
    return "<invalid url>"


def _bare_host(value: str) -> str:
    """The lower-cased host of ``value`` (a URL, ``host:port`` or ``host``), or ''."""
    text = value.strip()
    if not text:
        return ""
    try:
        parts = urllib.parse.urlsplit(text if "://" in text else "//" + text)
        return (parts.hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def platform_hosts() -> set[str]:
    """Hosts the tools must never contact: the platform's own, from the environment and built in."""
    hosts = {h for h in _PLATFORM_HOSTS}
    for name in _PLATFORM_ENV_VARS:
        for item in os.environ.get(name, "").split(","):
            host = _bare_host(item)
            if host:
                hosts.add(host)
    return hosts


_NUMERIC_HOST = re.compile(r"^(?:0x[0-9a-f]+|\d+)(?:\.(?:0x[0-9a-f]+|\d+))*$", re.I)


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _host_refusal(host: str) -> Optional[str]:
    """Why ``host`` (lower-case, no trailing dot) is refused by name alone, else None."""
    if _is_ip_literal(host):
        return None
    if _NUMERIC_HOST.match(host):
        # "127.1", "0x7f.0.0.1", "2130706433": inet_aton spellings of an address. Refused outright
        # rather than resolved, so there is no lookup and no way to smuggle loopback past the check.
        return f"Host {host!r} is a non-standard numeric address."
    if host == "localhost" or host.endswith(_DENIED_SUFFIXES):
        return f"Host {host!r} is a local or internal name."
    if "." not in host:
        return f"Host {host!r} is a single-label name (an internal service name); only public hosts are allowed."
    for denied in platform_hosts():
        if host == denied or host.endswith("." + denied):
            return "The platform's own hosts are not reachable from these tools."
    return None


def path_refusal(path: str) -> Optional[str]:
    """Refuse the gateway's API and health paths on any host, however they are spelled.

    Percent-encoding, ``..``, repeated slashes, ``;params`` and case are all normalised first, so
    ``/%61pi/v3/``, ``/x/../api/v3/chat`` and ``//API//v3`` are all caught.
    """
    text = path or "/"
    for _ in range(3):
        decoded = urllib.parse.unquote(text)
        if decoded == text:
            break
        text = decoded
    text = text.replace("\\", "/").lower()
    segments = [seg.split(";", 1)[0] for seg in text.split("/")]
    normal = posixpath.normpath("/" + "/".join(segments).lstrip("/"))
    parts = [p for p in normal.split("/") if p]
    if any(parts[i] == "api" and parts[i + 1] == "v3" for i in range(len(parts) - 1)):
        return "The AURA API path (/api/v3/) is not reachable from these tools."
    if parts and parts[0].startswith(_DENIED_PATH_ROOTS):
        return "The health-check path is not reachable from these tools."
    return None


def check_url(url: Any) -> str:
    """Validate ``url`` against the outbound policy and return it unchanged. Raises :class:`UrlRefused`.

    There is **no opt-out**. A URL is accepted only when it is:

    * ``http`` or ``https``, without embedded credentials or control characters;
    * a public host: loopback, private, link-local, CGNAT, unique-local, multicast, reserved and cloud
      metadata addresses are refused (a name is resolved and *every* address checked; an IP literal is
      checked without any DNS lookup), as are ``localhost``, ``*.internal``, ``*.local``,
      ``*.localhost`` and bare single-label names such as docker service names;
    * not one of the platform's own hosts (``aureon-ai.tech`` and subdomains, plus whatever
      ``AURA_PUBLIC_URL`` / ``AURA_PUBLIC_BASE_URL`` / ``AURA_BASE_URL`` / ``PUBLIC_BASE_URL`` /
      ``RAILWAY_PUBLIC_DOMAIN`` / ``RAILWAY_PRIVATE_DOMAIN`` name);
    * not on the gateway's ``/api/v3/`` or ``/health`` paths, on any host.

    Name checks run before any lookup, so a refused URL never causes a DNS query or a connection.

    Known limit: the name is resolved here and again by the HTTP client, so a hostile DNS server can
    answer differently the second time (rebinding); stopping that needs an egress proxy.
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
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise UrlRefused(
            f"URL scheme {parts.scheme or 'none'!r} is not allowed; only http and https URLs are."
        )
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise UrlRefused("URL has no host.")
    if parts.username or parts.password:
        raise UrlRefused("URLs with embedded credentials are not allowed.")
    reason = _host_refusal(host) or path_refusal(parts.path)
    if reason:
        raise UrlRefused(reason)

    if _is_ip_literal(host):
        addresses = [host]
    else:
        try:
            infos = socket.getaddrinfo(host, port or (443 if scheme == "https" else 80), type=socket.SOCK_STREAM)
        except (socket.gaierror, UnicodeError, OSError):
            raise UrlRefused(f"Cannot resolve host {host!r}.") from None
        addresses = [info[4][0].split("%", 1)[0] for info in infos]
        if not addresses:
            raise UrlRefused(f"Cannot resolve host {host!r}.")
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            raise UrlRefused(f"Host {host!r} resolved to an unreadable address.") from None
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        if not ip.is_global or ip.is_multicast:
            raise UrlRefused(
                f"Host {host!r} is not a public address (loopback, private, link-local, reserved or metadata)."
            )
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
            self._validate(urllib.parse.urljoin(req.full_url, newurl))
        except UrlRefused as exc:
            raise urllib.error.HTTPError(
                req.full_url, code, f"redirect refused: {exc}", headers, fp
            ) from None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_guarded(
    request: Union[str, urllib.request.Request],
    *,
    timeout: float,
    follow_redirects: bool = True,
):
    """``urlopen`` under :func:`check_url`'s policy, for the first URL and for every redirect hop."""
    url = request if isinstance(request, str) else request.full_url
    check_url(url)
    opener = urllib.request.build_opener(_GuardedRedirect(check_url if follow_redirects else None))
    return opener.open(request, timeout=clamp_timeout(timeout))


def open_owned_local(url: Any, port_ok: Callable[[int], bool], *, timeout: float):
    """GET a URL on **this machine's loopback**, only on a port ``port_ok`` vouches for.

    Used by the dev-server manager to probe a server it spawned itself. The host must be the literal
    ``127.0.0.1`` / ``localhost`` (no lookup, no proxy), the port must be one the caller allocated,
    the gateway's API and health paths are refused, and redirects are never followed.
    """
    if not isinstance(url, str) or len(url) > _MAX_URL_LEN or any(ord(c) < 33 or ord(c) == 127 for c in url):
        raise UrlRefused("A plain http URL is required.")
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError:
        raise UrlRefused("URL is malformed.") from None
    if parts.scheme.lower() != "http":
        raise UrlRefused(
            "URL scheme is not allowed here; only http URLs on a server this tool started are."
        )
    host = (parts.hostname or "").lower()
    if host not in ("127.0.0.1", "localhost") or parts.username or parts.password:
        raise UrlRefused("Only http://127.0.0.1:<port>/ on a server this tool started is allowed.")
    if port is None or not port_ok(port):
        raise UrlRefused("That port was not allocated or started by this tool.")
    reason = path_refusal(parts.path)
    if reason:
        raise UrlRefused(reason)
    target = urllib.parse.urlunsplit(("http", f"127.0.0.1:{port}", parts.path or "/", parts.query, ""))
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _GuardedRedirect(None))
    return opener.open(target, timeout=clamp_timeout(timeout))


def clamp_timeout(value: Any, default: float = 5.0, maximum: float = 30.0) -> float:
    """A positive, finite, bounded timeout. ``None`` would mean "wait forever" to urllib."""
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return default
    if seconds != seconds or seconds <= 0:  # NaN or non-positive
        return default
    return min(seconds, maximum)


# -- time budgets -----------------------------------------------------------------------------

#: Reading a long PDF or a multi-gigabyte archive is legitimately slow. The default is generous, the
#: caller may raise or lower it, and nothing is ever *truncated* to fit: a run that exceeds its
#: budget fails with a clear message instead of reporting on part of the input.
DEFAULT_READ_BUDGET_S = 300.0
MAX_BUDGET_S = 600.0


def clamp_budget(value: Any, default: float = DEFAULT_READ_BUDGET_S, maximum: float = MAX_BUDGET_S) -> float:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return default
    if seconds != seconds or seconds <= 0:
        return default
    return min(seconds, maximum)


class BudgetExceeded(Exception):
    """A read or verification ran past its time budget."""


def run_with_budget(fn: Callable[[], _T], seconds: float, what: str = "The operation") -> _T:
    """Run ``fn`` on a worker thread and give up waiting after ``seconds``.

    A thread cannot be killed, so on timeout the worker finishes in the background (it only reads, and
    its input is size-capped) and its result is discarded -- the caller gets :class:`BudgetExceeded`,
    never a partial result.
    """
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["value"] = fn()
        except BaseException as exc:  # re-raised in the caller's thread below
            box["error"] = exc

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(seconds)
    if worker.is_alive():
        raise BudgetExceeded(f"{what} did not finish within its {seconds:g}s time budget.")
    if "error" in box:
        raise box["error"]
    return box["value"]


# -- environments -----------------------------------------------------------------------------

_SECRET_NAME_PARTS = ("KEY", "SECRET", "TOKEN", "PASSWORD", "PASSWD", "CREDENTIAL", "JWT", "DATABASE_URL",
                      "REDIS_URL", "DSN")


def forbidden_env_names(env: Optional[dict[str, str]]) -> list[str]:
    """Names in a caller-supplied ``env`` that look like platform credentials (``AURA_*``, keys, tokens).

    Children get a scrubbed environment; this stops a caller (or an agent that fills ``env``) from
    simply handing the gateway's secrets back in.
    """
    bad = []
    for name in env or {}:
        upper = str(name).upper()
        if upper.startswith("AURA_") or any(part in upper for part in _SECRET_NAME_PARTS):
            bad.append(str(name))
    return bad
