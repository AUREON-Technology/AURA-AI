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
SKIP_PARTS = {"node_modules", ".next", "build", ".dart_tool"}

# Why a route with no caller has none -- decided by a person, kept here so a re-run keeps it.
# Matched by prefix on the path; the first match wins. Anything uncalled and unlisted reads
# "untriaged", which is the signal to come and decide.
TRIAGE = (
    ("/api/v3/integrations/github/callback", "server-only: OAuth redirect target, reached by the browser"),
    ("/api/v3/auth/desktop/", "to wire: desktop pairing flow (ADR-011), no caller in client or hostd yet"),
    ("/api/v3/agents/runs", "to wire: agent-run history (wave 2) has no client or admin view yet"),
    ("/api/v3/knowledge/graph", "to wire: knowledge-graph read API (wave 2) has no view yet"),
    ("/api/v3/admin/connectors", "to wire: Console Project Factory is Track AP phase AP9"),
    ("/api/v3/admin/twin", "to wire: Console twin browser and diff are Track AP phase AP9"),
    ("/api/v3/admin/blueprints", "to wire: Console blueprint review and deploy are Track AP phase AP9"),
    ("/api/v3/admin/", "to wire: admin console page missing for this route"),
    ("/api/v3/learning/", "to wire or remove: learning surface with no caller (GAP-013)"),
    ("/api/v3/tools", "to wire: tool catalogue is not shown anywhere"),
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


def sources() -> dict[str, list[tuple[str, int, str]]]:
    lines: dict[str, list[tuple[str, int, str]]] = {}
    for name, (base, exts) in CLIENT_DIRS.items():
        out = lines.setdefault(name, [])
        if not base.is_dir():
            continue
        # rglob order is filesystem-dependent; sorted keeps the committed matrix identical on every OS.
        for path in sorted(base.rglob("*")):
            if path.suffix not in exts or SKIP_PARTS & set(path.parts):
                continue
            rel = path.relative_to(ROOT).as_posix()
            for i, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                if "/" in line:
                    out.append((rel, i, line))
    return lines


# A path ends at a quote, a literal query string, end of line, or an interpolated query suffix
# (the admin console's `/admin/audit${query}`).
_END = r"(?=['\"`?]|$|\$\{?\w*[qQ]uery)"
_INTERP = r"(?:\$\{[^}]+\}|\$[A-Za-z_]\w*)"


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
    out = [(re.compile(_join(segs, literal_params=True) + _END), None)]
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
        note = next((why for prefix, why in TRIAGE if path.startswith(prefix)), "untriaged") \
            if status == "none" else ""
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
        "probes, Prometheus or API docs, not an app client.\n\n"
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
