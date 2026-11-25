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
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Sequence
from urllib.parse import urlparse

from ..domain.errors import ToolExecutionError
from ..domain.memory import SHARED_MEMORY_SCOPE, MemoryKind, MemoryQuery, readable_scopes
from ..domain.network import is_internal_address
from ..domain.tools import NETWORK_TAG, ToolCategory, ToolSensitivity
from ..ports.runtime import Tool, ToolContext
from .fact_grounding import check_fact_support
from ..domain.safety import GuardAction, GuardStage
from ..safety.guards import SecretGuard
from .analysis import build_analysis_tools
from .open_pure import build_open_pure_tools
from .base import param, tool
from .coding import build_github_tools, build_sandbox_tools
from .confinement import resolve_within as _resolve_within
from .open._input import SourceRead, SourceReadError, is_pdf_bytes, read_source_file, read_source_bytes
from .media import build_media_tools
from .open_exec import build_open_exec_tools
from .open_workspace import FileReadLedger, build_open_workspace_tools, note_file_read as _note_read
from .personal_os import personal_os_tools
from .planning import find_calendar_slots, prioritize_tasks
from .records import build_personal_os_crud_tools
from .websearch import build_web_search_tool

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
# ``round(5, -10**7)`` builds 10**10**7 inside CPython (seconds of CPU, gigabytes at 10**8), and the
# calculator runs on the event loop, so the digits argument is bounded like the exponent.
_MAX_ROUND_DIGITS = 400
# Python refuses to print an int of more than 4300 digits (ValueError); 14,000 bits is ~4,214.
_MAX_RESULT_BITS = 14_000


def _number(value: Any, what: str) -> Any:
    """``value`` if it is a real number. Tuples exist only as function arguments (``max((1, 2))``):
    as an operand ``(1,) * 10**8`` is a 800 MB allocation, and ``True``/complex are not what a
    calculator user means by a number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ToolExecutionError(f"{what} needs a number, not {type(value).__name__}", tool="calculator")
    return value


def _eval_node(node: ast.AST) -> Any:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return node.value
        raise ToolExecutionError("only numeric literals are allowed", tool="calculator")

    if isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in _BINARY_OPS:
            raise ToolExecutionError(f"operator {op_type.__name__} is not allowed", tool="calculator")
        left = _number(_eval_node(node.left), "an arithmetic operator")
        right = _number(_eval_node(node.right), "an arithmetic operator")
        if op_type is ast.Pow and (abs(right) > _MAX_EXPONENT or abs(left) > 1e12):
            raise ToolExecutionError(
                f"exponent is too large (limit {_MAX_EXPONENT})", tool="calculator"
            )
        try:
            result = _BINARY_OPS[op_type](left, right)
        except ZeroDivisionError:
            raise ToolExecutionError("division by zero", tool="calculator") from None
        except OverflowError:
            raise ToolExecutionError("the result is too large to represent", tool="calculator") from None
        if isinstance(result, complex):
            # (-8) ** (1/3): Python answers with a complex number, which is not an answer to a
            # question asked of a calculator.
            raise ToolExecutionError("the result is not a real number", tool="calculator")
        return result

    if isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type not in _UNARY_OPS:
            raise ToolExecutionError("unary operator is not allowed", tool="calculator")
        return _UNARY_OPS[op_type](_number(_eval_node(node.operand), "a sign"))

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCTIONS:
            raise ToolExecutionError(
                f"only these functions are allowed: {sorted(_FUNCTIONS)}", tool="calculator"
            )
        if node.keywords:
            raise ToolExecutionError("keyword arguments are not allowed", tool="calculator")
        name = node.func.id
        args = [_eval_node(a) for a in node.args]
        if name in ("factorial", "gcd"):
            # 5.0 is a whole number; math.factorial/gcd refuse it only because it is a float.
            args = [int(a) if isinstance(a, float) and a.is_integer() else a for a in args]
        if name == "factorial" and (args and isinstance(args[0], int) and args[0] > _MAX_FACTORIAL):
            raise ToolExecutionError(f"factorial argument exceeds {_MAX_FACTORIAL}", tool="calculator")
        if name == "round" and len(args) == 2 and isinstance(args[1], (int, float)) \
                and abs(args[1]) > _MAX_ROUND_DIGITS:
            raise ToolExecutionError(
                f"round() digits must be between -{_MAX_ROUND_DIGITS} and {_MAX_ROUND_DIGITS}",
                tool="calculator",
            )
        try:
            return _FUNCTIONS[name](*args)
        except OverflowError:
            raise ToolExecutionError(f"{name}() result is too large to represent", tool="calculator") from None

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
    except (ValueError, RecursionError, MemoryError) as exc:  # NUL byte, absurd nesting
        raise ToolExecutionError("could not parse expression", tool="calculator") from exc

    result = _number(_eval_node(parsed.body), "the expression's result")
    if isinstance(result, float):
        if math.isnan(result) or math.isinf(result):
            return f"{expression} = {result}"
        if result == int(result) and abs(result) < 1e15:
            return f"{expression} = {int(result)}"
        # 15 significant digits is what a double carries; 10 silently dropped the cents of a
        # nine-digit amount (1234567890.12 came back as 1234567890).
        return f"{expression} = {result:.15g}"
    if result.bit_length() > _MAX_RESULT_BITS:
        raise ToolExecutionError("the result has too many digits to display", tool="calculator")
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
    hours = timezone_offset_hours or 0.0
    if not math.isfinite(hours):
        raise ToolExecutionError("timezone_offset_hours must be a finite number", tool="current_datetime")
    # Whole minutes: a sub-minute offset (1e-07 h) made an ISO string no parser accepts.
    minutes = round(hours * 60)
    now = datetime.now(timezone.utc).astimezone(timezone(timedelta(minutes=minutes)))
    label = "UTC" if not minutes else f"UTC{minutes / 60:+g}"
    return f"{now.strftime('%A, %d %B %Y, %H:%M')} {label} (ISO: {now.isoformat()})"


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
            query, owner_id=context.knowledge_owner or context.user_id, limit=limit
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
        "in this message that will still be true next month. Do not store passing context, "
        "questions, or anything you inferred. The fact is checked against the user's own words: "
        "numbers must match exactly and most of its words must appear in what they said, so "
        "write it with their wording (\"The user lives in Cairo\" for \"I live in Cairo\"), not "
        "a paraphrase, and ask when unsure.",
        param("fact", "string", "The fact, written in the third person but in the user's own "
              "words.", max_length=500),
        param("subject", "string", "A 1-3 word topic label, e.g. 'diet' or 'work location'.",
              required=False, max_length=64),
        category=ToolCategory.MEMORY,
        sensitivity=ToolSensitivity.PERSONAL,
        side_effecting=True,
        timeout_s=10.0,
    )
    async def remember_fact(fact: str, context: ToolContext, subject: str | None = None) -> str:
        # A NUL byte is legal Python and illegal in a Postgres text column; stripping it here keeps
        # one odd character from turning into a storage error.
        fact = fact.replace("\x00", "").strip()
        subject = subject.replace("\x00", "").strip() if subject else None
        if not fact:
            raise ToolExecutionError("fact must not be empty", tool="remember_fact")
        # Durable memory is recalled into later prompts, which may go to a hosted model; a password
        # or API key the user pasted must not become a permanent "fact" about them.
        secret = SecretGuard().evaluate(f"{fact}\n{subject or ''}", stage=GuardStage.OUTPUT)
        if secret.action is GuardAction.BLOCK:
            raise ToolExecutionError(
                "the fact appears to contain a credential (API key, token or private key); "
                "it was not stored. Remember what the credential is for, never its value.",
                tool="remember_fact",
            )
        # What the user said this turn (set by the kernel, never retrieved text or a delegating
        # agent's task). Absent means the call cannot be checked -- a context built outside a turn.
        said = (getattr(context, "metadata", None) or {}).get("user_message")
        if isinstance(said, str) and said.strip():
            support = check_fact_support(fact, said)
            if not support.supported:
                raise ToolExecutionError(
                    f"the fact was not stored: {support.explain()}", tool="remember_fact"
                )
        record = await memory_engine.remember(
            owner_id=context.user_id,
            content=fact,
            kind=MemoryKind.SEMANTIC,
            subject=subject or None,
            source=f"tool:remember_fact:{context.correlation_id}",
            scope=getattr(context, "memory_scope", None) or SHARED_MEMORY_SCOPE,
        )
        return f"Recorded: {record.content}"

    return [recall_memory, remember_fact]


# --------------------------------------------------------------------------------------
# Filesystem (sandboxed)
# --------------------------------------------------------------------------------------


#: Characters of a file returned per call. Sized under ``read_file``'s ``max_result_chars`` (40,000)
#: with room for the coverage header, so the registry's blunt cut-off never trims a result.
_WINDOW_CHARS = 36_000
#: A PDF's text is cached for the next window of the same file: re-extracting every page for each
#: 36,000 characters would make reading a long document quadratic.
_CACHE_ENTRIES = 3
#: Entries ``list_files`` will look at in one directory.
_MAX_LISTED = 1_000


def _page_ranges(pages: Sequence[int]) -> str:
    """``[3, 4, 5, 9]`` -> ``'3-5, 9'``. A 5,000-page scan must not list 5,000 numbers."""
    out: list[str] = []
    ordered = sorted(set(pages))
    i = 0
    while i < len(ordered):
        j = i
        while j + 1 < len(ordered) and ordered[j + 1] == ordered[j] + 1:
            j += 1
        out.append(str(ordered[i]) if i == j else f"{ordered[i]}-{ordered[j]}")
        i = j + 1
    return ", ".join(out)


def _pdf_notes(name: str, read: SourceRead) -> list[str]:
    """What the model must know about how much of a PDF it was actually given."""
    total = read.pages or 0
    unread = list(read.unreadable_pages)
    notes = [
        f"PDF {name!r}: {total} page(s); {total - len(unread)} read"
        + (" in full -- every page." if not unread else ".")
    ]
    if unread:
        why = read.ocr_note or "OCR is required and is not available"
        notes.append(
            f"NOT READ: page(s) {_page_ranges(unread)} are scanned images with no text layer ({why}). "
            "Their content is missing from the text below; do not assume anything about it."
        )
    if read.ocr_pages:
        notes.append(
            f"Page(s) {_page_ranges(read.ocr_pages)} were read by OCR; that text is approximate and "
            "may contain recognition errors."
        )
    if read.blank_pages:
        notes.append(f"Page(s) {_page_ranges(read.blank_pages)} are blank.")
    return notes


def _office_notes(name: str, read: SourceRead) -> list[str]:
    """What the model must know about a Word / PowerPoint / Excel file it was given."""
    label = {"docx": "Word document", "pptx": "PowerPoint deck", "xlsx": "Excel workbook"}.get(read.kind, read.kind)
    notes = [f"{label} {name!r}: read in full."] if read.complete and not read.notes else [f"{label} {name!r}:"]
    notes.extend(read.warnings())
    return notes


def _present(text: str, notes: Sequence[str], offset: int, window: int, tool_name: str, tool_label: str) -> str:
    """The part of ``text`` from ``offset`` on that fits one result, with every caveat spelled out.

    Nothing is cut silently: a result that is not the whole of the text says which characters it
    holds and the ``offset`` to continue from. A complete, unannotated text comes back as it is.
    """
    total = len(text)
    if offset > total or (offset == total and total):
        raise ToolExecutionError(
            f"offset {offset:,} is at or past the end of the text ({total:,} characters)", tool=tool_name
        )
    end = min(total, offset + window)
    if end < total:
        # Break at a line so the next window starts on a clean boundary.
        newline = text.rfind("\n", offset + window // 2, end)
        if newline != -1:
            end = newline + 1
    lines = [f"[{note}]" for note in notes]
    partial = offset > 0 or end < total
    if partial:
        if end < total:
            lines.append(
                f"[Characters {offset:,}-{end:,} of {total:,}. This is NOT the whole text: call "
                f"{tool_label} again with offset={end} to continue.]"
            )
        else:
            lines.append(f"[Characters {offset:,}-{end:,} of {total:,}: the end of the text.]")
    body = text[offset:end]
    if not lines:
        return body
    if not body and not total:
        body = "(no text could be extracted)"
    return "\n".join(lines) + "\n\n" + body


def build_file_tools(workspace: str | Path, *, max_bytes: int = 512_000, read_ledger: Any = None) -> list[Tool]:
    """Read-only file access confined to one directory.

    Containment is checked after ``resolve()``, which follows symlinks -- checking before
    resolution is the classic bypass, since a symlink inside the workspace can point anywhere.
    Writing is deliberately not offered: a tool that writes files under model control is a much
    larger surface than this platform needs, and nothing in the product requires it.

    ``read_file`` reads a PDF **in full** (every page) through the shared ``open/_input`` reader and
    says which pages, if any, could not be read; text and PDF alike come back in windows the model
    pages through with ``offset``, so nothing is ever dropped without being reported.
    """
    root = Path(workspace).resolve()
    root.mkdir(parents=True, exist_ok=True)
    cache: "OrderedDict[tuple[str, int, int], tuple[str, list[str]]]" = OrderedDict()

    def resolve_within(relative: str, tool_name: str = "read_file") -> Path:
        # One definition shared with the ADR-040 readers (tools/confinement.py).
        return _resolve_within(root, relative, tool_name)

    @tool(
        "list_files",
        "List files in the user's workspace directory. PDFs are marked, so you know to read_file them "
        "for their full text.",
        param("subdirectory", "string", "Subdirectory to list. Omit for the workspace root.",
              required=False, max_length=200),
        category=ToolCategory.DATA,
        sensitivity=ToolSensitivity.PERSONAL,
        timeout_s=5.0,
    )
    async def list_files(subdirectory: str | None = None) -> str:
        target = resolve_within(subdirectory or ".", "list_files")
        if not target.is_dir():
            raise ToolExecutionError(f"{subdirectory or '.'} is not a directory", tool="list_files")

        def scan() -> list[str]:
            found: list[Path] = []
            more = False
            for path in target.iterdir():
                if len(found) >= _MAX_LISTED:
                    more = True
                    break
                if not path.name.startswith("."):
                    found.append(path)
            entries = []
            for path in sorted(found):
                # A newline in a file name would forge a second listing line.
                shown = "".join(c if c.isprintable() else "?" for c in str(path.relative_to(root)))
                try:
                    if path.is_symlink() and root not in path.resolve().parents:
                        entries.append(f"{shown} (symlink leaving the workspace; not readable)")
                    elif path.is_dir():
                        entries.append(f"{shown}/")
                    else:
                        kind = ", PDF" if path.suffix.lower() == ".pdf" else ""
                        entries.append(f"{shown} ({path.stat().st_size:,} bytes{kind})")
                except (OSError, RuntimeError):  # a broken link, a file removed mid-listing
                    entries.append(f"{shown} (unreadable)")
            if more:
                entries.append(f"[listing stopped after {_MAX_LISTED:,} entries; the directory has more]")
            return entries

        entries = await asyncio.to_thread(scan)
        return "\n".join(entries) if entries else "(empty)"

    def load(target: Path, shown: str) -> tuple[str, list[str]]:
        """The text of ``target`` and the notes about how much of it was read. Runs in a thread."""
        try:
            info = target.stat()
        except OSError as exc:
            raise ToolExecutionError(f"{shown} cannot be read: {exc.strerror or type(exc).__name__}",
                                     tool="read_file") from exc
        key = (str(target), info.st_mtime_ns, info.st_size)
        hit = cache.get(key)
        if hit is not None:
            cache.move_to_end(key)
            return hit
        try:
            with target.open("rb") as handle:
                head = handle.read(1024)
                if is_pdf_bytes(head) or target.suffix.lower() == ".pdf" or head[:4] == b"PK\x03\x04":
                    # A PDF or an Office package (.docx/.pptx/.xlsx): read in full by the shared reader.
                    # A zip that is none of these is refused there with a clear message.
                    read = None
                else:
                    if info.st_size > max_bytes:
                        raise ToolExecutionError(
                            f"{shown} is {info.st_size:,} bytes, exceeding the {max_bytes:,} byte limit",
                            tool="read_file",
                        )
                    handle.seek(0)
                    data = handle.read(max_bytes + 1)  # bounded even if the file grows meanwhile
                    read = False
        except OSError as exc:
            raise ToolExecutionError(f"{shown} cannot be read: {exc.strerror or type(exc).__name__}",
                                     tool="read_file") from exc

        if read is None:
            try:
                # base_dir re-checks containment on the path the reader itself resolves.
                pdf = read_source_file(target, base_dir=root, ocr_time_budget_s=60.0)
            except SourceReadError as exc:
                raise ToolExecutionError(str(exc).replace(str(target), shown), tool="read_file") from exc
            result = (pdf.text, _pdf_notes(shown, pdf) if pdf.kind == "pdf" else _office_notes(shown, pdf))
        else:
            if len(data) > max_bytes:
                raise ToolExecutionError(
                    f"{shown} is over the {max_bytes:,} byte limit", tool="read_file"
                )
            if b"\x00" in data:
                raise ToolExecutionError(
                    f"{shown} is a binary file, not UTF-8 text and not a PDF", tool="read_file"
                )
            try:
                result = (data.decode("utf-8-sig"), [])
            except UnicodeDecodeError as exc:
                raise ToolExecutionError(f"{shown} is not valid UTF-8 text", tool="read_file") from exc
        cache[key] = result
        while len(cache) > _CACHE_ENTRIES:
            cache.popitem(last=False)
        return result

    @tool(
        "read_file",
        "Read a UTF-8 text file, a PDF or a Word/PowerPoint/Excel file (.docx/.pptx/.xlsx) from the user's "
        "workspace directory. A PDF is read in FULL, every page; the result names any scanned page that "
        "could not be read. Long files come back "
        "in parts: the result says which characters it holds and the offset to pass to read the next.",
        param("path", "string", "Path relative to the workspace root.", max_length=300),
        param("offset", "integer", "Character offset to continue from when a result says it is not the "
              "whole text. Omit to start at the beginning.", required=False, minimum=0, default=0),
        category=ToolCategory.DATA,
        sensitivity=ToolSensitivity.PERSONAL,
        # A long PDF takes a while to extract (and OCR, when configured, longer); text is instant.
        timeout_s=120.0,
        max_result_chars=40_000,
        # A PDF or note in the workspace may have come from anyone; its text is data, not
        # instructions, and a turn that read it must not auto-approve a later side effect (ADR-006).
        tags=("untrusted_content",),
    )
    async def read_file(path: str, offset: int = 0, context: ToolContext | None = None) -> str:
        target = resolve_within(path)
        try:
            is_file = target.is_file()
        except OSError as exc:
            raise ToolExecutionError(f"{path} cannot be read: {exc.strerror or type(exc).__name__}",
                                     tool="read_file") from exc
        if not is_file:
            raise ToolExecutionError(f"{path} is not a file", tool="read_file")
        text, notes = await asyncio.to_thread(load, target, path)
        if read_ledger is not None and context is not None:
            # ADR-043: remember what the agent saw, so check_file_staleness can say it changed.
            await asyncio.to_thread(_note_read, read_ledger, context, path, target)
        return _present(text, notes, offset or 0, _WINDOW_CHARS, "read_file", "read_file")

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
    # Whitespace, control characters and backslashes are where two URL parsers disagree about which
    # host a URL names (``https://evil.test\\@allowed.com/``): this check and the HTTP client must
    # read the same one, so anything ambiguous is refused outright.
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127 or c == "\\" for c in url):
        raise ToolExecutionError("the URL contains characters that are not permitted", tool=tool_name)
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError as exc:
        raise ToolExecutionError("the URL is not valid", tool=tool_name) from exc
    if parsed.scheme != "https":
        raise ToolExecutionError("only https URLs are permitted", tool=tool_name)
    if "@" in parsed.netloc:
        raise ToolExecutionError("URLs with embedded credentials are not permitted", tool=tool_name)
    if port not in (None, 443):
        # The allowlist names hosts, never ports, so a port is not something it can have approved.
        raise ToolExecutionError("only the default https port is permitted", tool=tool_name)
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


_MARKUP_BLOCK_OPEN = re.compile(r"<(?:script|style)\b", re.I)
_MARKUP_BLOCK_CLOSE = re.compile(r"</(?:script|style)\b[^>]*>", re.I)
_MARKUP_TAG = re.compile(r"<[^<>]*>")


def _strip_markup(text: str) -> str:
    """Page text without tags, scripts and styles, in one linear pass.

    The page is attacker-influenceable and up to a megabyte. ``<script.*?</script>`` with no closing
    tag re-scans to the end for every ``<script`` (quadratic: a megabyte of them is hours of CPU on
    the event loop), and so does ``<[^>]+>`` on a run of ``<``. Here every search resumes where the
    previous one ended, and an unterminated script drops the rest of the page, as a browser would.
    """
    parts: list[str] = []
    pos = 0
    while True:
        opened = _MARKUP_BLOCK_OPEN.search(text, pos)
        if opened is None:
            parts.append(text[pos:])
            break
        parts.append(text[pos:opened.start()])
        parts.append(" ")
        closed = _MARKUP_BLOCK_CLOSE.search(text, opened.end())
        if closed is None:
            break
        pos = closed.end()
    return re.sub(r"\s+", " ", _MARKUP_TAG.sub(" ", "".join(parts))).strip()


def build_http_tool(
    *, allowed_hosts: Sequence[str], timeout_s: float = 15.0, max_body_bytes: int = 1_000_000,
    max_pdf_bytes: int = 25_000_000,
) -> list[Tool]:
    """Fetch a page from an allowlisted host.

    Returns an empty list when no hosts are configured: an HTTP tool with an empty allowlist
    would be advertised to the model and then refuse every call, which wastes a generation and
    teaches the model a capability it does not have.

    ``max_body_bytes`` is the most of a response body that is ever read (audit FSA-011). The body
    is streamed and reading stops at the budget, so an allowlisted host serving a huge or endless
    response costs at most this much memory; a ``Content-Length`` over the budget is refused
    before a byte is read. The budget is deliberately larger than ``max_result_chars``: tags are
    stripped after reading, so a page's text is a fraction of its bytes. A page cut at the budget
    says so in its result.

    A PDF is read in **full** or refused, never cut: ``max_pdf_bytes`` bounds it, and a PDF over it
    is an error, because the text of the first N pages passed off as the document would be worse.
    """
    allowlist = frozenset(h.lower().lstrip(".") for h in allowed_hosts if h.strip())
    if not allowlist:
        return []

    @tool(
        "fetch_url",
        "Fetch the text content of a web page, or of a PDF (read in full, every page). Long content "
        "comes back in parts: the result says which characters it holds and the offset to pass to "
        "read the next. Only these hosts are permitted: "
        + ", ".join(sorted(allowlist)),
        param("url", "string", "The https URL to fetch.", max_length=2000),
        param("offset", "integer", "Character offset to continue from when a result says it is not "
              "the whole text. Omit to start at the beginning.", required=False, minimum=0, default=0),
        category=ToolCategory.KNOWLEDGE,
        sensitivity=ToolSensitivity.PUBLIC,
        # The fetch itself is bounded by httpx's per-operation timeout; extracting a long PDF is not.
        timeout_s=timeout_s + 45.0,
        max_result_chars=20_000,
        # Page content is attacker-influenceable and never scanned by
        # deps.safety.guard_context_items (that only covers RAG retrieval) --
        # registry.execute() taints the ToolContext on this tag so a
        # prompt-injection payload here can't get a later PERSONAL-sensitivity
        # propose_* call auto-approved (M4).
        tags=("untrusted_content", NETWORK_TAG),
    )
    async def fetch_url(url: str, offset: int = 0) -> str:
        _assert_public_url(url, allowlist, "fetch_url")
        await _assert_resolves_publicly(urlparse(url).hostname or "", "fetch_url")
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover
            raise ToolExecutionError("httpx is not installed", tool="fetch_url") from exc

        notes: list[str] = []
        async with httpx.AsyncClient(
            timeout=timeout_s, follow_redirects=False, headers={"User-Agent": "AURA/3.0"}
        ) as client:
            # Redirects are not followed: a redirect is a trivial allowlist bypass, since the
            # allowed host can redirect to an internal address.
            async with client.stream("GET", url) as response:
                if response.is_redirect:
                    raise ToolExecutionError(
                        "the URL redirected; redirects are not followed", tool="fetch_url"
                    )
                if response.status_code >= 400:
                    raise ToolExecutionError(
                        f"HTTP {response.status_code}", tool="fetch_url"
                    )
                content_type = response.headers.get("content-type", "").lower()
                is_pdf = "application/pdf" in content_type
                if not is_pdf and not any(t in content_type for t in ("text/", "json", "xml")):
                    raise ToolExecutionError(
                        f"unsupported content type {content_type!r}", tool="fetch_url"
                    )
                budget = max_pdf_bytes if is_pdf else max_body_bytes
                declared = response.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > budget:
                    raise ToolExecutionError(
                        f"the response is {int(declared):,} bytes, over the {budget:,}-byte "
                        "budget; not read",
                        tool="fetch_url",
                    )
                # Streamed, and stopped at the budget: the server's size claim (if any) is not
                # what bounds memory here, the count of bytes actually received is.
                received = bytearray()
                cut = False
                async for chunk in response.aiter_bytes():
                    if len(received) >= budget:
                        cut = True  # more data exists beyond the budget
                        break
                    room = budget - len(received)
                    received += chunk[:room]
                    if len(chunk) > room:
                        cut = True
                        break
                encoding = response.encoding or "utf-8"
        if is_pdf:
            if cut:
                raise ToolExecutionError(
                    f"the PDF is larger than the {budget:,}-byte budget; not read", tool="fetch_url"
                )
            name = (urlparse(url).path.rsplit("/", 1)[-1] or "document.pdf")[:80]
            try:
                pdf = await asyncio.to_thread(
                    read_source_bytes, bytes(received), name=name, ocr_time_budget_s=60.0
                )
            except SourceReadError as exc:
                raise ToolExecutionError(str(exc), tool="fetch_url") from exc
            return _present(pdf.text, _pdf_notes(name, pdf), offset or 0, 18_000, "fetch_url", "fetch_url")
        if cut:
            notes.append(
                f"The page is larger than the {budget:,}-byte read budget; only its first part was read."
            )
        try:
            decoded = bytes(received).decode(encoding, errors="replace")
        except LookupError:  # a server-supplied charset name Python does not know
            decoded = bytes(received).decode("utf-8", errors="replace")
        # Crude tag strip: this content is fenced as untrusted data downstream, so the goal is
        # readability, not sanitisation.
        return _present(_strip_markup(decoded), notes, offset or 0, 18_000, "fetch_url", "fetch_url")

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
        tags=(NETWORK_TAG,),
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

        if current.get("temperature_2m") is None:
            # "Berlin: Nonedegc" reads as a reading; it is the absence of one.
            raise ToolExecutionError(
                f"no current weather is available for {location!r} right now", tool="get_weather"
            )
        name = ", ".join(str(p)[:80] for p in (place.get("name"), place.get("country")) if p)
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
    enable_analysis_tools: bool = False,
    enable_open_exec_tools: bool = False,
    enable_desktop_ops_tools: bool = False,
    enable_open_pure_tools: bool = False,
    episodic_memory: Any = None,
    web_search_url: str = "",
    web_search_timeout_s: float = 10.0,
    web_search_engines: str = "",
    web_search_language: str = "",
    ocr_engine: Any = None,
    vision_engine: Any = None,
    image_normalizer: Any = None,
    stt_engine: Any = None,
    engineer_runs: Any = None,
    agent_runs: Any = None,
    advisory_store: Any = None,
    advisory_max_age_days: int = 3,
) -> list[Tool]:
    """Assemble the tool set for a deployment.

    Everything that touches the network or the filesystem is opt-in and requires explicit
    configuration. A default install has arithmetic, the clock, and personal-OS proposals
    (client-applied JSON; no server writes): useful, and incapable of reaching anything.
    ``personal_os_store`` additionally wires the create/list/update/delete CRUD tools (Phase C2)
    -- real server-side writes an agent's grant permits without a client-side approval card. In
    production that store is the app's own synced tables, encrypted with the user's data key
    (ADR-032).

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
    if episodic_memory is not None:
        # Row 26: the checkpoint capability had no caller. Owner-scoped inside EpisodicMemory.
        from .session_tools import build_session_tools

        tools += build_session_tools(episodic_memory)
    if personal_os_store is not None:
        tools += build_personal_os_crud_tools(personal_os_store)
    # ADR-043: one ledger of what the agent last read or wrote, shared by read_file, the Engineer's
    # write_file/edit_file and check_file_staleness (in memory; a restart only loses baselines).
    read_ledger = FileReadLedger()
    if workspace is not None:
        tools += build_file_tools(workspace, read_ledger=read_ledger)
        # ADR-040: structured readers over the same confined directory. Each is registered only if what
        # it needs is present (the OCR / vision / speech engines are injected, never imported here).
        tools += build_media_tools(
            workspace, ocr_engine=ocr_engine, vision_engine=vision_engine,
            image_normalizer=image_normalizer, stt_engine=stt_engine,
        )
    # ADR-043: the tools/open classes that read the workspace, plus project_status over the run stores.
    tools += build_open_workspace_tools(
        workspace, ledger=read_ledger, engineer_runs=engineer_runs, agent_runs=agent_runs)
    if allowed_http_hosts:
        tools += build_http_tool(allowed_hosts=allowed_http_hosts)
    if enable_weather:
        tools += build_weather_tool()
    if web_search_url:
        # ADR-040: search through the operator's own SearXNG, or not at all (no hosted search API).
        tools += build_web_search_tool(
            base_url=web_search_url, timeout_s=web_search_timeout_s,
            engines=web_search_engines, language=web_search_language,
        )
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
            desktop_ops=enable_desktop_ops_tools,
            read_ledger=read_ledger,
        )
    if github_client_resolver is not None:
        tools += build_github_tools(github_client_resolver, default_repo=github_default_repo)
    if enable_analysis_tools:
        # Pure text-in/JSON-out analysis (ADR-036 group A): no I/O, no capability, so unlike the
        # groups above it needs no resource to be present -- only the operator not turning it off.
        tools += build_analysis_tools(
            workspace=workspace, advisory_store=advisory_store, advisory_max_age_days=advisory_max_age_days)
    if enable_open_pure_tools:
        # ADR-042: fourteen more pure tools (architecture, ambiguity, acceptance criteria, diff scope,
        # explain, quiz, artifacts, evidence, plan feedback, mobile summary, file type, structured
        # data, source structure, workflow validation). Same risk profile as group A.
        tools += build_open_pure_tools()
    if enable_analysis_tools and allowed_http_hosts:
        # ADR-044: post-release health verification, only against the operator's allowed hosts.
        from .release import build_release_tools

        tools += build_release_tools(allowed_http_hosts)
    if enable_open_exec_tools:
        # ADR-036 group B: runs project code in a child of *this* process. Operator opt-in only,
        # and each tool still needs `engineer_agent` plus a per-call confirmation.
        tools += build_open_exec_tools()
    return tools
