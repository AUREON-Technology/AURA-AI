"""Built-in tools.

Each is self-contained, needs no vendor account beyond what it declares, and is written to be
safe when handed hostile arguments -- because a tool's arguments come from a model that may
itself have been influenced by attacker-controlled text.

The security-relevant ones are worth reading closely:

* :func:`calculator` evaluates arithmetic by walking an AST with a node allowlist, never
  ``eval``. The naive version of this tool is a remote code execution vulnerability.
* :func:`build_file_tools` confines every path to one workspace root, resolving symlinks before
  the containment check -- a symlink out of the sandbox is the standard bypass.
* :func:`build_http_tool` requires a host allowlist and refuses private address ranges, which is
  what stops a tool-calling agent from becoming an SSRF proxy into the VPS's own network.
"""

from __future__ import annotations

import ast
import asyncio
import ipaddress
import math
import operator
import re
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Sequence
from urllib.parse import urlparse

from ..domain.errors import ToolExecutionError
from ..domain.memory import SHARED_MEMORY_SCOPE, MemoryKind, MemoryQuery, readable_scopes
from ..domain.network import is_internal_address
from ..domain.tools import ToolCategory, ToolSensitivity
from ..ports.runtime import Tool, ToolContext
from .base import param, tool
from .coding import build_github_tools, build_sandbox_tools
from .personal_os import personal_os_tools
from .planning import find_calendar_slots, prioritize_tasks
from .records import build_personal_os_crud_tools

__all__ = [
    "calculator",
    "current_datetime",
    "build_knowledge_tools",
    "build_memory_tools",
    "build_file_tools",
    "build_http_tool",
    "build_weather_tool",
    "default_tools",
]


# --------------------------------------------------------------------------------------
# Pure utility tools
# --------------------------------------------------------------------------------------

_BINARY_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCTIONS: dict[str, Callable[..., float]] = {
    "abs": abs, "round": round, "min": min, "max": max, "sum": lambda *a: sum(a),
    "sqrt": math.sqrt, "log": math.log, "log10": math.log10, "log2": math.log2,
    "exp": math.exp, "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "asin": math.asin, "acos": math.acos, "atan": math.atan, "atan2": math.atan2,
    "floor": math.floor, "ceil": math.ceil, "trunc": math.trunc,
    "degrees": math.degrees, "radians": math.radians, "hypot": math.hypot,
    "factorial": math.factorial, "gcd": math.gcd,
}
_CONSTANTS = {"pi": math.pi, "e": math.e, "tau": math.tau, "inf": math.inf}

# Bounds on exponentiation: 2**10**9 is a trivially-reachable memory exhaustion.
_MAX_EXPONENT = 1024
_MAX_FACTORIAL = 170


def _eval_node(node: ast.AST) -> Any:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ToolExecutionError("only numeric literals are allowed", tool="calculator")

    if isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in _BINARY_OPS:
            raise ToolExecutionError(f"operator {op_type.__name__} is not allowed", tool="calculator")
        left, right = _eval_node(node.left), _eval_node(node.right)
        if op_type is ast.Pow and (abs(right) > _MAX_EXPONENT or abs(left) > 1e12):
            raise ToolExecutionError(
                f"exponent is too large (limit {_MAX_EXPONENT})", tool="calculator"
            )
        return _BINARY_OPS[op_type](left, right)

    if isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type not in _UNARY_OPS:
            raise ToolExecutionError("unary operator is not allowed", tool="calculator")
        return _UNARY_OPS[op_type](_eval_node(node.operand))

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCTIONS:
            raise ToolExecutionError(
                f"only these functions are allowed: {sorted(_FUNCTIONS)}", tool="calculator"
            )
        if node.keywords:
            raise ToolExecutionError("keyword arguments are not allowed", tool="calculator")
        args = [_eval_node(a) for a in node.args]
        if node.func.id == "factorial" and (args and args[0] > _MAX_FACTORIAL):
            raise ToolExecutionError(f"factorial argument exceeds {_MAX_FACTORIAL}", tool="calculator")
        return _FUNCTIONS[node.func.id](*args)

    if isinstance(node, ast.Name):
        if node.id in _CONSTANTS:
            return _CONSTANTS[node.id]
        raise ToolExecutionError(f"unknown name {node.id!r}", tool="calculator")

    if isinstance(node, ast.Tuple):
        return tuple(_eval_node(e) for e in node.elts)

    raise ToolExecutionError(
        f"expression element {type(node).__name__} is not allowed", tool="calculator"
    )


@tool(
    "calculator",
    "Evaluate an arithmetic expression. Supports + - * / // % **, parentheses, and the "
    "functions abs round min max sum sqrt log log10 log2 exp sin cos tan asin acos atan atan2 "
    "floor ceil trunc degrees radians hypot factorial gcd, plus the constants pi, e, tau. "
    "Use this for any calculation instead of computing mentally.",
    param("expression", "string", "The arithmetic expression, e.g. '(1250 * 0.15) + 30'",
          max_length=500),
    category=ToolCategory.UTILITY,
    timeout_s=2.0,
)
async def calculator(expression: str) -> str:
    """Arithmetic via a node-allowlisted AST walk.

    ``eval`` on a model-supplied string is remote code execution: the model's input can be
    influenced by any document the retrieval stage pulled in. Parsing in ``eval`` mode and
    permitting only literals, arithmetic operators, and a fixed function table closes that,
    and the exponent/factorial bounds close the resource-exhaustion variant.
    """
    try:
        parsed = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ToolExecutionError(f"could not parse expression: {exc.msg}", tool="calculator") from exc

    result = _eval_node(parsed.body)
    if isinstance(result, float):
        if math.isnan(result) or math.isinf(result):
            return f"{expression} = {result}"
        if result == int(result) and abs(result) < 1e15:
            return f"{expression} = {int(result)}"
        return f"{expression} = {result:.10g}"
    return f"{expression} = {result}"


@tool(
    "current_datetime",
    "Get the current date and time. Call this before any reasoning about 'today', 'tomorrow', "
    "'this week', or relative deadlines -- do not assume the date.",
    param("timezone_offset_hours", "number", "UTC offset in hours, e.g. 2 for UTC+2. Defaults to UTC.",
          required=False, minimum=-12, maximum=14),
    category=ToolCategory.UTILITY,
    timeout_s=1.0,
)
async def current_datetime(timezone_offset_hours: float | None = None) -> str:
    offset = timedelta(hours=timezone_offset_hours or 0.0)
    now = datetime.now(timezone.utc) + offset
    label = "UTC" if not timezone_offset_hours else f"UTC{timezone_offset_hours:+g}"
    return (
        f"{now.strftime('%A, %d %B %Y, %H:%M')} {label} "
        f"(ISO: {now.replace(tzinfo=timezone(offset)).isoformat()})"
    )


# --------------------------------------------------------------------------------------
# Knowledge and memory
# --------------------------------------------------------------------------------------


def build_knowledge_tools(knowledge_engine: Any) -> list[Tool]:
    """Retrieval exposed as a tool, so an agent can search deliberately.

    Distinct from the automatic retrieval stage: that runs once per turn on the user's message,
    while this lets a plan step search for something the user did not literally ask about
    ("find the policy that governs this").
    """

    @tool(
        "search_knowledge",
        "Search the user's document knowledge base and return the most relevant passages with "
        "their sources. Use this for anything that might be in the user's own documents. "
        "Returns nothing when the knowledge base has no relevant material -- treat that as a "
        "genuine 'not found', not a reason to guess.",
        param("query", "string", "What to search for. Include distinctive terms and identifiers.",
              max_length=500),
        param("limit", "integer", "Maximum passages to return (1-10).", required=False,
              minimum=1, maximum=10, default=5),
        category=ToolCategory.KNOWLEDGE,
        sensitivity=ToolSensitivity.PERSONAL,
        timeout_s=20.0,
    )
    async def search_knowledge(query: str, context: ToolContext, limit: int = 5) -> str:
        result = await knowledge_engine.retrieve(
            query, owner_id=context.user_id, limit=limit
        )
        if not result.chunks:
            return (
                f"No passages above the relevance threshold for {query!r} "
                f"({result.candidates_considered} candidate(s) examined, "
                f"{len(result.below_threshold)} below threshold)."
            )
        lines = []
        for index, retrieved in enumerate(result.chunks, start=1):
            lines.append(
                f"[{index}] {retrieved.citation()} (score {retrieved.score:.2f})\n"
                f"{retrieved.chunk.content.strip()}"
            )
        return "\n\n".join(lines)

    return [search_knowledge]


def build_memory_tools(memory_engine: Any) -> list[Tool]:
    """Explicit memory access.

    ``remember_fact`` is marked side-effecting: it writes durable state about the user, and the
    legacy system's equivalent writes whatever an extractor produced with no confirmation and no
    provenance.
    """

    @tool(
        "recall_memory",
        "Search what AURA remembers about this user -- stated facts, preferences, and past "
        "conclusions. Use before asking the user something they may already have told you.",
        param("query", "string", "What you want to recall, e.g. 'dietary preferences'.",
              max_length=300),
        param("limit", "integer", "Maximum memories to return (1-10).", required=False,
              minimum=1, maximum=10, default=5),
        category=ToolCategory.MEMORY,
        sensitivity=ToolSensitivity.PERSONAL,
        timeout_s=10.0,
    )
    async def recall_memory(query: str, context: ToolContext, limit: int = 5) -> str:
        hits = await memory_engine.recall(
            MemoryQuery(
                owner_id=context.user_id, text=query, limit=limit,
                scopes=readable_scopes(getattr(context, "memory_scope", None) or SHARED_MEMORY_SCOPE),
            )
        )
        if not hits:
            return f"Nothing recorded about {query!r}."
        return "\n".join(
            f"- {hit.record.content} (confidence {hit.record.confidence:.2f}, "
            f"{hit.record.kind})"
            for hit in hits
        )

    @tool(
        "remember_fact",
        "Store a durable fact about the user. Only for things the user stated about themselves "
        "that will still be true next month. Do not store passing context, questions, or "
        "anything you inferred.",
        param("fact", "string", "The fact, written in the third person.", max_length=500),
        param("subject", "string", "A 1-3 word topic label, e.g. 'diet' or 'work location'.",
              required=False, max_length=64),
        category=ToolCategory.MEMORY,
        sensitivity=ToolSensitivity.PERSONAL,
        side_effecting=True,
        timeout_s=10.0,
    )
    async def remember_fact(fact: str, context: ToolContext, subject: str | None = None) -> str:
        record = await memory_engine.remember(
            owner_id=context.user_id,
            content=fact,
            kind=MemoryKind.SEMANTIC,
            subject=subject,
            source=f"tool:remember_fact:{context.correlation_id}",
            scope=getattr(context, "memory_scope", None) or SHARED_MEMORY_SCOPE,
        )
        return f"Recorded: {record.content}"

    return [recall_memory, remember_fact]


# --------------------------------------------------------------------------------------
# Filesystem (sandboxed)
# --------------------------------------------------------------------------------------


def build_file_tools(workspace: str | Path, *, max_bytes: int = 512_000) -> list[Tool]:
    """Read-only file access confined to one directory.

    Containment is checked after ``resolve()``, which follows symlinks -- checking before
    resolution is the classic bypass, since a symlink inside the workspace can point anywhere.
    Writing is deliberately not offered: a tool that writes files under model control is a much
    larger surface than this platform needs, and nothing in the product requires it.
    """
    root = Path(workspace).resolve()
    root.mkdir(parents=True, exist_ok=True)

    def resolve_within(relative: str) -> Path:
        if "\x00" in relative:
            raise ToolExecutionError("path contains a null byte", tool="read_file")
        candidate = (root / relative.lstrip("/")).resolve()
        if candidate != root and root not in candidate.parents:
            raise ToolExecutionError(
                "path escapes the workspace directory", tool="read_file",
                details={"requested": relative},
            )
        return candidate

    @tool(
        "list_files",
        "List files in the user's workspace directory.",
        param("subdirectory", "string", "Subdirectory to list. Omit for the workspace root.",
              required=False, max_length=200),
        category=ToolCategory.DATA,
        sensitivity=ToolSensitivity.PERSONAL,
        timeout_s=5.0,
    )
    async def list_files(subdirectory: str | None = None) -> str:
        target = resolve_within(subdirectory or ".")
        if not target.is_dir():
            raise ToolExecutionError(f"{subdirectory or '.'} is not a directory", tool="list_files")

        def scan() -> list[str]:
            entries = []
            for path in sorted(target.iterdir()):
                if path.name.startswith("."):
                    continue
                relative = path.relative_to(root)
                if path.is_dir():
                    entries.append(f"{relative}/")
                else:
                    entries.append(f"{relative} ({path.stat().st_size:,} bytes)")
            return entries

        entries = await asyncio.to_thread(scan)
        return "\n".join(entries) if entries else "(empty)"

    @tool(
        "read_file",
        "Read a UTF-8 text file from the user's workspace directory.",
        param("path", "string", "Path relative to the workspace root.", max_length=300),
        category=ToolCategory.DATA,
        sensitivity=ToolSensitivity.PERSONAL,
        timeout_s=10.0,
        max_result_chars=40_000,
    )
    async def read_file(path: str) -> str:
        target = resolve_within(path)
        if not target.is_file():
            raise ToolExecutionError(f"{path} is not a file", tool="read_file")
        size = target.stat().st_size
        if size > max_bytes:
            raise ToolExecutionError(
                f"{path} is {size:,} bytes, exceeding the {max_bytes:,} byte limit",
                tool="read_file",
            )
        try:
            return await asyncio.to_thread(target.read_text, encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ToolExecutionError(f"{path} is not valid UTF-8 text", tool="read_file") from exc

    return [list_files, read_file]


# --------------------------------------------------------------------------------------
# Network
# --------------------------------------------------------------------------------------

_PRIVATE_HOSTS = {"localhost", "metadata.google.internal", "metadata"}


def _assert_public_url(url: str, allowed_hosts: frozenset[str], tool_name: str) -> str:
    """Reject anything that could reach the deployment's own network.

    An agent with an unrestricted HTTP tool is an SSRF proxy: it will happily fetch
    ``http://169.254.169.254/`` (cloud instance metadata, including credentials) or an internal
    admin endpoint, on behalf of whoever influenced the model's arguments. The allowlist is the
    primary control; the private-range check is defence in depth for an allowlisted name that
    resolves somewhere unexpected.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ToolExecutionError("only https URLs are permitted", tool=tool_name)
    host = (parsed.hostname or "").lower()
    if not host or host in _PRIVATE_HOSTS:
        raise ToolExecutionError("host is not permitted", tool=tool_name)
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and is_internal_address(address):
        raise ToolExecutionError("private and link-local addresses are not permitted", tool=tool_name)
    if not any(host == allowed or host.endswith(f".{allowed}") for allowed in allowed_hosts):
        raise ToolExecutionError(
            f"host {host!r} is not in the configured allowlist", tool=tool_name,
            details={"allowlist": sorted(allowed_hosts)},
        )
    return url


async def _assert_resolves_publicly(host: str, tool_name: str) -> None:
    """Reject a hostname whose DNS resolves to a private or internal address.

    ``_assert_public_url`` only inspects the literal URL string: an IP-literal URL is checked
    directly, but an allowlisted *hostname* is only checked against the allowlist, never against
    what it actually resolves to. That gap is a DNS-rebinding SSRF -- an allowlisted domain whose
    record points at ``169.254.169.254`` or ``127.0.0.1`` (now, or once the operator no longer
    controls its DNS) would otherwise be fetched without complaint, string allowlist satisfied.
    Resolving immediately before the request narrows, though does not eliminate, the TOCTOU window
    a full IP-pinning fetch would close -- DNS could still change in the moment between this check
    and the connection ``httpx`` makes a line later.
    """
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, None)
    except socket.gaierror as exc:
        raise ToolExecutionError(f"could not resolve host {host!r}: {exc}", tool=tool_name) from exc
    for _family, _kind, _proto, _canon, sockaddr in infos:
        # getaddrinfo reports scoped IPv6 as "fe80::1%eth0"; the scope id is not part of the
        # address and would make ip_address() raise instead of refusing.
        address = ipaddress.ip_address(str(sockaddr[0]).split("%", 1)[0])
        if is_internal_address(address):
            raise ToolExecutionError(
                f"host {host!r} resolves to a private address ({address}); refusing to fetch",
                tool=tool_name,
            )


def build_http_tool(*, allowed_hosts: Sequence[str], timeout_s: float = 15.0) -> list[Tool]:
    """Fetch a page from an allowlisted host.

    Returns an empty list when no hosts are configured: an HTTP tool with an empty allowlist
    would be advertised to the model and then refuse every call, which wastes a generation and
    teaches the model a capability it does not have.
    """
    allowlist = frozenset(h.lower().lstrip(".") for h in allowed_hosts if h.strip())
    if not allowlist:
        return []

    @tool(
        "fetch_url",
        "Fetch the text content of a web page. Only these hosts are permitted: "
        + ", ".join(sorted(allowlist)),
        param("url", "string", "The https URL to fetch.", max_length=2000),
        category=ToolCategory.KNOWLEDGE,
        sensitivity=ToolSensitivity.PUBLIC,
        timeout_s=timeout_s,
        max_result_chars=20_000,
        # Page content is attacker-influenceable and never scanned by
        # deps.safety.guard_context_items (that only covers RAG retrieval) --
        # registry.execute() taints the ToolContext on this tag so a
        # prompt-injection payload here can't get a later PERSONAL-sensitivity
        # propose_* call auto-approved (M4).
        tags=("untrusted_content",),
    )
    async def fetch_url(url: str) -> str:
        _assert_public_url(url, allowlist, "fetch_url")
        await _assert_resolves_publicly(urlparse(url).hostname or "", "fetch_url")
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover
            raise ToolExecutionError("httpx is not installed", tool="fetch_url") from exc

        async with httpx.AsyncClient(
            timeout=timeout_s, follow_redirects=False, headers={"User-Agent": "AURA/3.0"}
        ) as client:
            # Redirects are not followed: a redirect is a trivial allowlist bypass, since the
            # allowed host can redirect to an internal address.
            response = await client.get(url)
            if response.is_redirect:
                raise ToolExecutionError(
                    "the URL redirected; redirects are not followed", tool="fetch_url"
                )
            if response.status_code >= 400:
                raise ToolExecutionError(
                    f"HTTP {response.status_code}", tool="fetch_url"
                )
            content_type = response.headers.get("content-type", "")
            if not any(t in content_type for t in ("text/", "json", "xml")):
                raise ToolExecutionError(
                    f"unsupported content type {content_type!r}", tool="fetch_url"
                )
            text = response.text
        # Crude tag strip: this content is fenced as untrusted data downstream, so the goal is
        # readability, not sanitisation.
        text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", text, flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    return [fetch_url]


def build_weather_tool(*, timeout_s: float = 10.0) -> list[Tool]:
    """Current weather via Open-Meteo.

    Chosen because it needs no API key, which keeps a working demo tool out of the credential
    management path entirely.
    """

    @tool(
        "get_weather",
        "Get the current weather for a location by name (e.g. 'Cairo' or 'Berlin, Germany').",
        param("location", "string", "City name, optionally with country.", max_length=120),
        category=ToolCategory.UTILITY,
        sensitivity=ToolSensitivity.PUBLIC,
        timeout_s=timeout_s,
    )
    async def get_weather(location: str) -> str:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover
            raise ToolExecutionError("httpx is not installed", tool="get_weather") from exc

        async with httpx.AsyncClient(timeout=timeout_s) as client:
            geo = await client.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": location, "count": 1, "format": "json"},
            )
            if geo.status_code >= 400:
                raise ToolExecutionError(f"geocoding failed (HTTP {geo.status_code})", tool="get_weather")
            results = (geo.json() or {}).get("results") or []
            if not results:
                raise ToolExecutionError(f"no location found matching {location!r}", tool="get_weather")
            place = results[0]

            forecast = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": place["latitude"],
                    "longitude": place["longitude"],
                    "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code",
                    "timezone": "auto",
                },
            )
            if forecast.status_code >= 400:
                raise ToolExecutionError(
                    f"forecast failed (HTTP {forecast.status_code})", tool="get_weather"
                )
            current = (forecast.json() or {}).get("current") or {}

        name = ", ".join(p for p in (place.get("name"), place.get("country")) if p)
        return (
            f"{name}: {current.get('temperature_2m')}degC, "
            f"humidity {current.get('relative_humidity_2m')}%, "
            f"wind {current.get('wind_speed_10m')} km/h "
            f"(observed {current.get('time')})"
        )

    return [get_weather]


# --------------------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------------------


def default_tools(
    *,
    knowledge_engine: Any = None,
    memory_engine: Any = None,
    personal_os_store: Any = None,
    workspace: str | Path | None = None,
    allowed_http_hosts: Sequence[str] = (),
    enable_weather: bool = False,
    sandbox_runner: Any = None,
    github_client_resolver: Any = None,
    github_default_repo: str = "",
    workspace_manager: Any = None,
    host_git: Any = None,
    github_token_resolver: Any = None,
    host_agent_registry: Any = None,
) -> list[Tool]:
    """Assemble the tool set for a deployment.

    Everything that touches the network or the filesystem is opt-in and requires explicit
    configuration. A default install has arithmetic, the clock, and personal-OS proposals
    (client-applied JSON; no server writes): useful, and incapable of reaching anything.
    ``personal_os_store`` additionally wires the create/list CRUD tools (Phase C2) -- real
    server-side writes an agent's grant permits without a client-side approval card, against
    aura_core's own table, never the client's zero-knowledge encrypted one.

    ``sandbox_runner`` and ``github_client_resolver`` wire the engineer agent's tools
    (``tools/coding.py``) the same opt-in way -- absent, none of those tools are registered at
    all. ``github_client_resolver`` is a callable ``(UserId) -> Awaitable[GitHubClient | None]``
    (``Container._build_github_client_resolver``), not a single client: whether every caller
    shares one admin-configured client or each resolves their own OAuth-connected one is a
    container-wiring decision the tools themselves don't need to know about. MCP-bridged tools
    are *not* assembled here: discovering them is an async I/O call (``build_mcp_bridge_tools``),
    so ``Container.startup()`` registers them separately once the event loop is running.
    """
    tools: list[Tool] = [
        calculator, current_datetime, prioritize_tasks, find_calendar_slots,
        *personal_os_tools(),
    ]
    if knowledge_engine is not None:
        tools += build_knowledge_tools(knowledge_engine)
    if memory_engine is not None:
        tools += build_memory_tools(memory_engine)
    if personal_os_store is not None:
        tools += build_personal_os_crud_tools(personal_os_store)
    if workspace is not None:
        tools += build_file_tools(workspace)
    if allowed_http_hosts:
        tools += build_http_tool(allowed_hosts=allowed_http_hosts)
    if enable_weather:
        tools += build_weather_tool()
    if sandbox_runner is not None:
        # The resolver is passed to both builders on purpose: `clone_repository` lives with the
        # sandbox tools (it writes into their per-turn workspace) but reaches GitHub through the
        # same per-user client the `github_*` tools use. Absent a resolver, the sandbox tools
        # register without it and cloning is simply not offered.
        tools += build_sandbox_tools(
            sandbox_runner,
            code_host_resolver=github_client_resolver,
            workspace_manager=workspace_manager,
            host_git=host_git,
            token_resolver=github_token_resolver,
            host_agent_registry=host_agent_registry,
        )
    if github_client_resolver is not None:
        tools += build_github_tools(github_client_resolver, default_repo=github_default_repo)
    return tools
