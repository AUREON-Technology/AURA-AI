"""Regenerate docs/guides/api-reachability.md: which HTTP routes have a client caller.

CLAUDE.md warns that "a backend endpoint shipping does not mean the client can reach it" -- this
codebase has repeatedly shipped a v3 route and left it with zero callers. This script makes that
checkable instead of anecdotal: it enumerates every route the production gateway mounts (the same
enumeration ``tests/security/test_route_auth.py`` uses) and searches the Flutter client
(``aura_client/lib``) and the admin console (``aura_admin``) for a string that builds that path.

The match is a heuristic, not a call graph. A route counts as called when some source line contains
its path after ``/api/vN``, with each ``{param}`` allowed to be a literal segment or a Dart/TS
interpolation (``$id``, ``${x.id}``), followed by a quote, ``?`` or end of string -- so ``/chat``
is not credited to a ``/chat/stream`` caller. A path assembled from several constants will be
missed; the doc says so, and a human confirms any "none" before acting on it.

Run from aura_backend/:  python scripts/api_reachability.py
"""

from __future__ import annotations

import os
import re
import sys
from collections import Counter
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
OUT = ROOT / "docs" / "guides" / "api-reachability.md"
CLIENT_DIRS = {"flutter": (ROOT / "aura_client" / "lib", (".dart",)),
               "admin": (ROOT / "aura_admin", (".ts", ".tsx")),
               "hostd": (ROOT / "aura_hostd", (".dart",))}
# Test code is not a caller (audit FSA-083): a Playwright spec or a daemon test that spells an API
# path proves nothing about the shipped app.
SKIP_PARTS = {"node_modules", ".next", "build", ".dart_tool", "test", "tests", "e2e", "integration_test"}

#: What the heuristic cannot tell, stated in the document rather than guessed at (audit FSA-083).
METHOD_BLIND_NOTE = (
    "The match is by path only: a caller of one HTTP method on a path credits every method the "
    "gateway serves on it, so a `called` row for a DELETE or PATCH may rest on a GET caller alone."
)

# Why a route with no caller has none -- decided by a person, kept here so a re-run keeps it.
# The first matching pattern wins. Anything uncalled and unlisted reads "untriaged", which is
# the signal to come and decide.
# A pattern ending in `$` explains exactly that route; one ending in `/` explains everything under
# it; any other explains the route and its own sub-resources (`/x`, `/x/...`), never a sibling that
# merely shares the spelling (`/xy`). See triage_note().
TRIAGE = (
    ("/api/v3/integrations/github/callback", "server-only: OAuth redirect target, reached by the browser"),
    ("/api/v3/billing/fawry/notify", "server-only: Fawry's server notification (SaaS P1-D1)"),
    ("/api/v3/billing/apple/notify", "server-only: App Store Server Notifications V2 (SaaS P1-D2)"),
    ("/api/v3/billing/google/notify", "server-only: Play RTDN via Pub/Sub push (SaaS P1-D2)"),
    ("/api/v3/chat$", "server-to-server: the non-streaming turn is for integrating systems (ADR-029 service keys, AR-6 structured output); the app streams via /chat/stream"),
    ("/api/v3/chat/plan-feedback", "to wire: free-text feedback on a plan held by plan_mode review (ADR-047, row 51); the client's review screen only approves or walks away so far"),
    ("/api/v3/auth/desktop/", "to wire: desktop pairing flow (ADR-011), no caller in client or hostd yet"),
    ("/api/v3/admin/model-monitor", "to wire: the production-inference monitor's report and re-baseline (ADR-052) are an operator API; the console has no page for them yet"),
    ("/api/v3/engineering/policy", "to wire: the review-profile setting (ADR-041) has no client view yet; backend and API only so far"),

    ("/api/v3/agents/runs", "to wire: agent-run history (wave 2) has no client or admin view yet; search and timeline (ADR-043) are backend-only so far"),
    ("/api/v3/agents/tasks", "to wire: the agent task gateway (ADR-043, roadmap rows 84/87/91) is backend-only; the client submits, polls, approves and cancels in its next step, with a client-generated idempotency key per submission"),
    ("/api/v3/usage", "to wire: usage dashboard API (ADR-043, roadmap row 72); the client's usage view is not built"),
    ("/api/v3/voice/task", "to wire: voice-to-task extraction (ADR-043, roadmap row 88); the client confirms the structured task, then POSTs /agents/tasks"),
    ("/api/v3/engineering/workspaces/", "to wire: workspace start/stop/restart and stats (ADR-043, roadmap row 89); no client view yet"),
    ("/api/v3/host/status", "to wire: paired-desktop status (ADR-043, roadmap row 89); read-only, no client view yet"),
    ("/api/v3/agents/{agent_id}", "to wire: creating, editing, cloning and deleting custom agents (api/agents.py) has no app screen yet; the app's per-agent instruction table (AgentConfigurations) is local and unused"),
    ("/api/v3/knowledge/graph", "to wire: knowledge-graph read API (wave 2) has no view yet"),
    ("/api/v3/admin/connectors", "to wire: Console Project Factory is Track AP phase AP9"),
    ("/api/v3/admin/twin", "to wire: Console twin browser and diff are Track AP phase AP9"),
    ("/api/v3/admin/blueprints", "to wire: Console blueprint review and deploy are Track AP phase AP9"),
    ("/api/v3/admin/", "to wire: admin console page missing for this route"),
    ("/api/v3/learning/", "to wire or remove: learning surface with no caller (GAP-013)"),
    ("/api/v3/notifications", "to wire: the server inbox (approvals waiting, failed runs, billing, plan limits); the app's notification center is built locally from telemetry and keeps its read mark on the device (api/notifications.py)"),
    ("/api/v3/me/notification-settings", "to wire: which notification kinds are on; the app has no such setting (api/notifications.py)"),
    ("/api/v3/me/usage", "to wire: this month's messages, storage, tokens and subscription, and the history; the app shows a team's plan through /workspaces/{id}/plan but nothing for the personal workspace's tokens or history (api/usage.py)"),
    ("/api/v3/plans", "to wire: the plan catalogue (what each plan allows) for an upgrade screen; the app has no such list today (api/usage.py)"),
    ("/api/v3/me/model-settings", "to wire: the app sends no routing_policy, require_local, temperature or max_output_tokens today, so a person cannot choose how chats are answered (api/models.py)"),
    ("/api/v3/models", "to wire: the model catalog (capabilities and privacy of each model) is not shown anywhere; the app offers no model or policy choice (api/models.py)"),
    ("/api/v3/chats/{chat_id}/messages", "to wire: reading, editing and deleting one message through the API; the app keeps its messages in its own database and syncs them (api/chats.py)"),
    ("/api/v3/feedback", "to wire: the app only posts a rating; listing one's own ratings has no screen (GET /feedback)"),
    ("/api/v3/me/export", "to wire: a \"download my data\" button; the bundle is the server's personal scope in plain text, the app has no such screen (api/export.py)"),
    ("/api/v3/sessions/{session_id}/export", "to wire: an export action on a chat; the server's copy is partial (summary + unsummarised turns) because synced chats are encrypted, so the app should export from its own database and use this only as a fallback (api/export.py)"),
    ("/api/v3/me", "to wire: profile read/update and password change have no app screen yet; register and forgot/reset password stay in the client's Firebase SDK (api/me.py)"),
    ("/api/v3/turns/", "to wire: usage by turn id (read it later); the app already gets the same numbers inline on /chat and the stream's done frame"),
    ("/api/v3/chats", "to wire: the app lists, pins, archives and renames chats locally (Drift) today; no screen calls the server list or the pin/archive/title routes yet (api/chats.py)"),
    ("/api/v3/runtime", "to wire: whether the paired computer is connected and which device; the app learns it today from its own daemon's /status, and the phone and web only from /host/folders (aura_core/api/v3.py)"),
    ("/api/v3/approvals", "to wire: the app approves held tool calls through /chat (approval_ids) and keeps the pending ones only on the device that received them; no screen lists them, shows the history, or refuses one on the server yet (api/approvals.py)"),
    ("/api/v3/tools", "to wire: tool catalogue is not shown anywhere"),
    ("/api/v3/memory/lessons", "to wire: pending Engineer lessons (row 60, ADR-040) have no approve/reject view yet; backend scope"),
    ("/api/v3/sessions/{session_id}/checkpoint", "to wire: session checkpoint (row 26, ADR-040) is backend-only until the client shows a resume card; backend scope"),
    ("/api/v3/workspaces", "client: team workspaces, plan, billing and shared chats are called from aura_client/lib/features/workspaces/data/workspaces_client.dart through AuraApiClient's string-built paths (SaaS P1-F2)"),
)

# Served to infrastructure or browsers rather than an app client; never "to wire".
INFRA = re.compile(r"^/(health|metrics|docs|redoc|openapi\.json)")


def routes() -> list[tuple[str, str]]:
    sys.path.insert(0, str(BACKEND))
    from fastapi import routing
    from fastapi.routing import APIWebSocketRoute

    # The gateway module reads TESTING at import. Set it only for that import and restore it:
    # tests/architecture/test_api_reachability.py calls this inside the pytest process, and a
    # leaked TESTING=True disables production-only validators for every test that runs after it.
    previous = os.environ.get("TESTING")
    os.environ["TESTING"] = "True"
    try:
        from aura_service import app
    finally:
        if previous is None:
            os.environ.pop("TESTING", None)
        else:
            os.environ["TESTING"] = previous

    found: set[tuple[str, str]] = set()
    for ctx in routing.iter_route_contexts(app.routes):
        original = ctx.original_route
        path = ctx.path or original.path
        if isinstance(original, APIWebSocketRoute):
            found.add(("WS", path))
            continue
        for method in getattr(original, "methods", None) or ():
            if method != "HEAD":
                found.add((method, path))
    return sorted(found, key=lambda r: (r[1], r[0]))


# A wrapper call left open at the end of a line (`await api(` -- the formatter put the argument on
# the next line) is carried onto that next line, so the matcher sees `api(\`/admin/...\`` as one call.
_OPEN_WRAPPER = re.compile(r"\b(?:api|stepUp\.write)(?:<.*>)?\(\s*$")


def scan_tree(base: Path, exts: tuple[str, ...], *, root: Path = ROOT) -> list[tuple[str, int, str]]:
    """`(repo-relative file, line number, text)` for every line that could hold a path."""
    out: list[tuple[str, int, str]] = []
    if not base.is_dir():
        return out
    # rglob order is filesystem-dependent; sorted keeps the committed matrix identical on every OS.
    for path in sorted(base.rglob("*")):
        # Parts below the scanned tree only: a checkout living under `/…/tests/` is not test code.
        if path.suffix not in exts or SKIP_PARTS & set(path.relative_to(base).parts):
            continue
        rel = path.relative_to(root).as_posix()
        carry = ""
        for i, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            text = carry + line.lstrip() if carry else line
            carry = "api(" if _OPEN_WRAPPER.search(line) else ""
            if "/" in text:
                out.append((rel, i, text))
    return out


def triage_note(path: str) -> str:
    """Why an uncalled route has no caller, from TRIAGE, or `untriaged`."""
    for pattern, why in TRIAGE:
        if pattern.endswith("$"):
            matched = path == pattern[:-1]
        elif pattern.endswith("/"):
            matched = path.startswith(pattern)
        else:
            matched = path == pattern or path.startswith(pattern + "/")
        if matched:
            return why
    return "untriaged"


def sources() -> dict[str, list[tuple[str, int, str]]]:
    return {name: scan_tree(base, exts) for name, (base, exts) in CLIENT_DIRS.items()}


# A path ends at a quote, a literal query string, end of line, or an interpolated query suffix
# (the admin console's `/admin/audit${query}`).
_END = r"(?=['\"`?]|$|\$\{?\w*[qQ]uery)"
_INTERP = r"(?:\$\{[^}]+\}|\$[A-Za-z_]\w*)"
# A whole path counts only on an API base: the literal `/api/vN`; an interpolated base such as
# `$_baseUrl` / `${backend}` that the file resolves elsewhere; or the admin console's `api(...)`
# wrapper (`aura_admin/src/lib/api.ts` prepends `/api/v3` to the path it is given), and its
# control-center `stepUp.write(...)` (`aura_admin/src/lib/control.ts`), which hands the path to
# `api(...)` with the step-up header. A bare
# `'/chat'` is a UI route (go_router, Next.js) and used to credit `/api/v3/chat` (audit FSA-083).
_WRAPPER = r"\b(?:api|stepUp\.write)(?:<.*>)?\(\s*[\"'`]"
_BASE = rf"(?:/api/v\d+|{_INTERP}|{_WRAPPER})"


def _segments(path: str) -> list[str]:
    tail = re.sub(r"^/api/v\d+", "", path)
    return [s for s in tail.strip("/").split("/") if s]


def _join(segs: list[str], *, literal_params: bool) -> str:
    param = rf"(?:{_INTERP}|[^/'\"\s?]+)" if literal_params else _INTERP
    return "/" + "/".join(param if s.startswith("{") else re.escape(s) for s in segs)


def matchers(path: str) -> list[tuple[re.Pattern[str], re.Pattern[str] | None]]:
    """(whole-path) or (base-prefix, relative-suffix) pairs that must both appear in one file.

    Clients build URLs two ways: the whole path in one string, or a ``_baseUrl`` ending at some
    prefix plus a relative suffix elsewhere in the same file. In the split form a parameter in
    the suffix must be an interpolation, or ``'/'+anything`` would credit every route.
    """
    segs = _segments(path)
    if not segs:
        return []
    out = [(re.compile(_BASE + _join(segs, literal_params=True) + _END), None)]
    for k in range(1, len(segs)):
        prefix = segs[:k]
        if any(s.startswith("{") for s in prefix):
            break
        out.append((re.compile(_join(prefix, literal_params=False) + _END),
                    re.compile(_join(segs[k:], literal_params=False) + _END)))
    return out


def render() -> tuple[str, Counter]:
    src = sources()
    rows, counts = [], Counter()
    for method, path in routes():
        pats = matchers(path)
        callers = {}
        for name, lines in src.items():
            by_file: dict[str, list[tuple[int, str]]] = {}
            for f, n, text in lines:
                by_file.setdefault(f, []).append((n, text))
            hits = []
            for f, flines in by_file.items():
                for whole_or_base, suffix in pats:
                    base_hits = [n for n, t in flines if whole_or_base.search(t)]
                    if not base_hits:
                        continue
                    if suffix is None:
                        hits += [f"`{f}:{n}`" for n in base_hits]
                        break
                    suffix_hits = [n for n, t in flines if suffix.search(t)]
                    if suffix_hits:
                        hits += [f"`{f}:{n}`" for n in suffix_hits]
                        break
            callers[name] = ", ".join(hits[:2]) + (f" (+{len(hits) - 2})" if len(hits) > 2 else "")
        if INFRA.match(path):
            status = "infra"
        elif any(callers.values()):
            status = "called"
        else:
            status = "none"
        counts[status] += 1
        note = triage_note(path) if status == "none" else ""
        rows.append(
            f"| {method} | `{path}` | {callers['flutter'] or '—'} | {callers['admin'] or '—'} "
            f"| {callers['hostd'] or '—'} | {status} | {note} |"
        )

    text = (
        "# API reachability\n\n"
        "<!-- generated by aura_backend/scripts/api_reachability.py; re-run it, don't hand-edit -->\n\n"
        "Every route the production gateway (`aura_service.app`) mounts, and where the Flutter client "
        "(`aura_client/lib`), the admin console (`aura_admin`) or the desktop daemon (`aura_hostd`) "
        "builds its path. Generated by a "
        "string heuristic, not a call graph: a path assembled from several constants reads as "
        "`none`, so confirm a `none` by hand before wiring or removing it. `infra` routes serve "
        "probes, Prometheus or API docs, not an app client. " + METHOD_BLIND_NOTE + "\n\n"
        f"**Totals:** {sum(counts.values())} routes — {counts['called']} called, "
        f"{counts['none']} with no caller found, {counts['infra']} infrastructure.\n\n"
        "| Method | Path | Flutter caller | Admin caller | hostd caller | Status | Triage |\n"
        "| --- | --- | --- | --- | --- | --- | --- |\n" + "\n".join(rows) + "\n"
    )
    return text, counts


def main() -> int:
    text, counts = render()
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}: {dict(counts)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
