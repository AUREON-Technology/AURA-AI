"""Release checklist and post-deploy health verification (ADR-046, row 94).

Two parts, deliberately separated by what they may touch:

* ``release_checklist`` (pure text): from a CHANGELOG, a set of migrations and a few numbers it builds
  the go/no-go list a release manager would walk through -- is there an entry for the target version,
  does its content imply a bigger semver bump than the one proposed, which migrations destroy data or
  lack a ``downgrade``, is rolling back still safe after them, and what deployment strategy follows
  from that -- and reuses ``DeploymentReleaseManager`` for the rollback steps and the risk score.
* ``probe_health`` (network): GETs health URLs and judges the *body*, not just the status line, so a
  200 that says ``"healthy": false`` or reports the wrong ``version`` is not a pass. It goes through
  ``_ops_common.open_guarded`` only, which refuses private/internal hosts, the platform's own hosts
  and the gateway's ``/api/v3`` and ``/health`` paths, and checks every redirect hop; redirects are
  not followed here at all. The caller (``tools/release.py``) additionally pins the host to the
  operator's allowlist. This module never deploys, restarts or rolls anything back: the pipeline stays
  the operator's CI/CD.
"""

from __future__ import annotations

import ast
import json
import re
import time
import urllib.error
import urllib.request
from typing import Any

from ._ops_common import UrlRefused, open_guarded
from .deployment_release_manager import DeploymentReleaseManager

__all__ = ["parse_changelog", "release_checklist", "probe_health"]

_SEMVER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")
_VERSION_HEADING = re.compile(r"^#{1,3}\s*\[?v?(\d+\.\d+\.\d+[^\]\s]*)\]?(?:\s*[-(–]\s*([\d-]+))?", re.MULTILINE)
_BREAKING = re.compile(r"\b(breaking|incompatible|removed?|drops?|no longer|renamed?|deprecated and removed)\b", re.IGNORECASE)
_DESTRUCTIVE = re.compile(
    r"\bdrop\s+(?:table|column|index|constraint|schema)\b|\btruncate\b|op\.drop_(?:table|column|constraint)\b|"
    r"\balter\s+column\s+\S+\s+(?:type|set\s+not\s+null)\b|\brename\s+(?:table|column)\b|op\.alter_column\(|op\.rename_table\(",
    re.IGNORECASE)
_NOT_NULL_NO_DEFAULT = re.compile(
    r"add\s+column\s+\S+\s+[\w()]+\s+not\s+null(?!.*default)"
    r"|op\.add_column\((?:(?!server_default)[^)])*nullable\s*=\s*False(?:(?!server_default)[^)])*\)", re.IGNORECASE | re.DOTALL)


def parse_changelog(text: str) -> dict[str, Any]:
    """``{"releases": [{version, date, categories, breaking_hints}], "unreleased"}`` from a markdown changelog."""
    headings = list(_VERSION_HEADING.finditer(text))
    releases: list[dict[str, Any]] = []
    for index, match in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        body = text[match.end():end]
        categories: dict[str, list[str]] = {}
        current = "changes"
        for line in body.splitlines():
            head = re.match(r"^#{2,4}\s*(\w[\w ]*)$", line.strip())
            if head:
                current = head.group(1).strip().lower()
                continue
            bullet = re.match(r"^\s*[-*+]\s+(.*\S)", line)
            if bullet:
                categories.setdefault(current, []).append(bullet.group(1)[:200])
        hints = [b for items in categories.values() for b in items if _BREAKING.search(b)]
        hints += [c for c in categories if c in ("removed", "breaking", "breaking changes")]
        releases.append({"version": match.group(1), "date": match.group(2),
                         "categories": {k: v[:20] for k, v in categories.items()},
                         "entries": sum(len(v) for v in categories.values()),
                         "breaking_hints": hints[:10]})
    return {"releases": releases[:30], "unreleased": bool(re.search(r"^#{1,3}\s*\[?unreleased", text, re.IGNORECASE | re.MULTILINE))}


def _bump(old: str, new: str) -> str | None:
    a, b = _SEMVER.match(old or ""), _SEMVER.match(new or "")
    if not a or not b:
        return None
    x, y = [tuple(int(v) for v in m.groups()) for m in (a, b)]
    if y <= x:
        return "none"
    return "major" if y[0] > x[0] else "minor" if y[1] > x[1] else "patch"


def _upgrade_part(text: str) -> str:
    """The forward half of a migration: the ``upgrade()`` body of an Alembic file, or the SQL before a
    ``-- down`` marker. A ``downgrade()`` that drops what ``upgrade()`` added is not a destructive release."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return re.split(r"--\s*(?:\+migrate\s+)?down\b", text, maxsplit=1, flags=re.IGNORECASE)[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "upgrade":
            return ast.get_source_segment(text, node) or text
    return text


def _migration_findings(item: Any) -> dict[str, Any]:
    name = item if isinstance(item, str) else str(item.get("name", "?"))
    text = "" if isinstance(item, str) else str(item.get("text") or "")
    out: dict[str, Any] = {"name": name, "inspected": bool(text), "destructive": [], "has_downgrade": None, "notes": []}
    if not text:
        out["notes"].append("name only: contents not inspected")
        return out
    upgrade_text = _upgrade_part(text)
    out["destructive"] = sorted({m.group(0).lower() for m in _DESTRUCTIVE.finditer(upgrade_text)})[:8]
    if _NOT_NULL_NO_DEFAULT.search(upgrade_text):
        out["notes"].append("adds a NOT NULL column without a default: fails on a non-empty table")
    try:
        tree = ast.parse(text)
        down = next((n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "downgrade"), None)
        if down is None:
            out["has_downgrade"] = False
        else:
            only_pass = all(isinstance(s, ast.Pass) or (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))
                            for s in down.body)
            out["has_downgrade"] = not only_pass
            if only_pass:
                out["notes"].append("downgrade() is empty")
    except SyntaxError:
        if re.search(r"--\s*down|-- \+migrate Down|\bdown\b", text, re.IGNORECASE):
            out["has_downgrade"] = True
    return out


def release_checklist(
    *, service: str, target_version: str, previous_version: str | None, changelog: str | None,
    migrations: list[Any], health_checks: list[str], changed_files_count: int | None,
    test_coverage_pct: float | None, strategy: str | None,
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []

    def add(name: str, status: str, detail: str) -> None:
        items.append({"item": name, "status": status, "detail": detail})

    parsed = parse_changelog(changelog) if changelog else None
    release = None
    bump = _bump(previous_version or "", target_version)
    if previous_version and bump == "none":
        add("version increases", "fail", f"{target_version} is not newer than {previous_version}")
    elif previous_version and bump is None:
        add("version is semver", "warn", "versions are not MAJOR.MINOR.PATCH; the bump cannot be checked")
    elif previous_version:
        add("version increases", "pass", f"{previous_version} -> {target_version} is a {bump} bump")
    if parsed is not None:
        release = next((r for r in parsed["releases"] if r["version"].lstrip("v") == target_version.lstrip("v")), None)
        if release is None:
            add("changelog entry", "fail", f"no section for {target_version} in the changelog"
                + (" (there is an Unreleased section: rename it)" if parsed["unreleased"] else ""))
        elif release["entries"] == 0:
            add("changelog entry", "fail", f"the {target_version} section has no entries")
        else:
            add("changelog entry", "pass", f"{release['entries']} entries under {sorted(release['categories'])}")
            if release["breaking_hints"] and bump in ("minor", "patch"):
                add("semver matches content", "warn",
                    f"{len(release['breaking_hints'])} entry(ies) read as breaking ({release['breaking_hints'][0][:80]!r}) "
                    f"but the bump is {bump}")
            elif release["breaking_hints"]:
                add("semver matches content", "pass", "breaking-looking entries and a major bump agree")
    else:
        add("changelog entry", "skip", "no changelog supplied")

    found = [_migration_findings(m) for m in migrations]
    destructive = [f for f in found if f["destructive"]]
    missing_down = [f for f in found if f["inspected"] and f["has_downgrade"] is False]
    if not migrations:
        add("database migrations", "pass", "none in this release")
    else:
        add("database migrations", "info", f"{len(found)} migration(s): {[f['name'] for f in found][:10]}")
        if destructive:
            add("destructive migrations", "fail" if not previous_version else "warn",
                f"{[f['name'] for f in destructive]} drop/rename/retype data; after they run, rolling back the "
                "code does not bring the data back. Use expand/contract: ship the additive change first, remove later.")
        if missing_down:
            add("migrations are reversible", "fail", f"no usable downgrade in {[f['name'] for f in missing_down]}")
        elif all(f["inspected"] for f in found):
            add("migrations are reversible", "pass", "every migration has a non-empty downgrade")
        else:
            add("migrations are reversible", "warn", "some migrations were given by name only and were not inspected")
        for f in found:
            for note in f["notes"]:
                if "NOT NULL" in note:
                    add("migration safety", "warn", f"{f['name']}: {note}")
    if health_checks:
        add("health checks configured", "pass", f"{health_checks[:5]}")
    else:
        add("health checks configured", "fail", "none given; a release with nothing to verify afterwards is unobserved")
    rollback_safe = bool(previous_version) and not destructive
    add("rollback possible", "pass" if rollback_safe else "fail" if not previous_version else "warn",
        "previous version known and no destructive migration" if rollback_safe else
        "no previous version to return to" if not previous_version else
        "a destructive migration makes code rollback insufficient")

    chosen = (strategy or ("blue_green" if migrations else "rolling")).lower()
    if strategy is None and destructive:
        chosen = "blue_green"
    plan = DeploymentReleaseManager.generate_rollback_plan(
        service, target_version, previous_version or "", chosen) if previous_version else None
    risk = None
    if changed_files_count is not None and test_coverage_pct is not None:
        risk = DeploymentReleaseManager.calculate_deployment_risk(
            changed_files_count, bool(migrations), rollback_safe, float(test_coverage_pct))
        if risk.get("valid"):
            add("deployment risk", "pass" if risk["risk_level"] in ("LOW", "MEDIUM") else "warn",
                f"{risk['risk_level']} (score {risk['risk_score']}): {risk['risk_factors']}")
    blockers = [i for i in items if i["status"] == "fail"]
    return {
        "items": items, "go": not blockers, "blockers": [b["item"] for b in blockers],
        "warnings": [i["item"] for i in items if i["status"] == "warn"],
        "recommended_strategy": chosen, "semver_bump": bump,
        "migrations": found, "rollback_plan": plan, "risk": risk,
        "changelog": ({"version": release["version"], "date": release["date"], "entries": release["entries"]}
                      if release else None),
        "executed": False,
    }


# --- health verification --------------------------------------------------------------------

_HEALTHY_WORDS = {"ok", "healthy", "up", "ready", "pass", "passing", "alive", "green"}
_VERSION_KEYS = ("version", "release", "app_version", "build", "commit", "sha", "git_sha", "revision")
_BODY_CAP = 65_536


def _find(data: Any, keys: tuple[str, ...], depth: int = 0) -> Any:
    if depth > 3:
        return None
    if isinstance(data, dict):
        for key in keys:
            for k, v in data.items():
                if str(k).lower() == key and isinstance(v, (str, int, float)) and not isinstance(v, bool):
                    return v
        for v in data.values():
            found = _find(v, keys, depth + 1)
            if found is not None:
                return found
    return None


def _dotted(data: Any, path: str) -> Any:
    for part in path.split("."):
        if isinstance(data, dict) and part in data:
            data = data[part]
        else:
            return None
    return data if data is not None else None


def probe_health(
    url: str, *, timeout_s: float, expected_version: str | None = None, version_field: str | None = None,
    required_fields: list[str] | None = None,
) -> dict[str, Any]:
    """One GET. ``ok`` needs a 2xx, a body that does not say unhealthy, the expected version and fields."""
    started = time.perf_counter()
    result: dict[str, Any] = {"url": url, "ok": False}
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "AURA-Release-Verifier/1.0", "Accept": "application/json, */*"})
        with open_guarded(request, timeout=timeout_s, follow_redirects=False) as response:
            result["status_code"] = response.getcode()
            raw = response.read(_BODY_CAP + 1)
    except UrlRefused as exc:
        return {**result, "error": f"URL refused: {exc}"}
    except urllib.error.HTTPError as exc:
        return {**result, "status_code": exc.code, "error": f"HTTP {exc.code}",
                "latency_ms": round((time.perf_counter() - started) * 1000, 1)}
    except Exception as exc:  # noqa: BLE001 - every network failure is a result, not a crash
        return {**result, "error": f"{type(exc).__name__}: {str(exc)[:200]}",
                "latency_ms": round((time.perf_counter() - started) * 1000, 1)}
    result["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
    problems: list[str] = []
    code = result["status_code"]
    if not (isinstance(code, int) and 200 <= code < 300):
        problems.append(f"status {code}")
    body: Any = None
    if len(raw) > _BODY_CAP:
        problems.append("body larger than 64 KiB; not parsed")
    else:
        try:
            body = json.loads(raw.decode("utf-8", errors="replace"))
        except ValueError:
            body = None
    if isinstance(body, dict):
        flag = body.get("healthy", body.get("ok"))
        if flag is False:
            problems.append("body says healthy/ok is false")
        status = body.get("status")
        if isinstance(status, str) and status.lower() not in _HEALTHY_WORDS:
            problems.append(f"body status is {status!r}")
        found = _dotted(body, version_field) if version_field else _find(body, _VERSION_KEYS)
        result["version"] = None if found is None else str(found)[:100]
        result["fields"] = sorted(body)[:20]
        for path in required_fields or []:
            if _dotted(body, path) is None:
                problems.append(f"required field '{path}' missing")
    elif expected_version or required_fields:
        problems.append("body is not a JSON object, so version/fields cannot be checked")
    if expected_version:
        have = result.get("version")
        if have is None:
            problems.append("no version in the body")
        elif not (have == expected_version or have.lstrip("v") == expected_version.lstrip("v")
                  or (len(expected_version) >= 7 and have.startswith(expected_version))):
            problems.append(f"serving version {have!r}, expected {expected_version!r}")
    result["problems"] = problems
    result["ok"] = not problems
    return result
