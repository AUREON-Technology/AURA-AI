"""Vulnerability advisories (OSV) kept locally, so a dependency scan needs no network.

``scan_code_security`` matches the versions a lockfile pins against advisories. It makes no network
call, so the advisories have to already be on this side. A scheduled job (``learning/osv_sync.py``)
copies them from the public OSV feed into a store (``ports/advisories.py``); the scan only reads.

This module is the stdlib-only vocabulary both sides share: the one host the feed may come from, how a
package name is normalised, and how an OSV document is cut down to what version matching needs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

__all__ = [
    "FEED_HOST", "FEED_BASE_URL", "FEED_ECOSYSTEMS", "StoredAdvisory", "EcosystemStatus",
    "normalize_package", "slim_advisory", "is_withdrawn", "age_days", "parse_osv_time",
]

#: The only host the sync may contact -- the OSV project's public bucket. The operator opts the
#: *job* in (``AURA_OSV_SYNC_ENABLED``); this constant is what keeps that opt-in from being a general
#: "download from anywhere" switch.
FEED_HOST = "storage.googleapis.com"
FEED_BASE_URL = f"https://{FEED_HOST}/osv-vulnerabilities"

#: Ecosystems the dependency scan can match (see ``workspace_deps``): our key -> OSV's directory name.
FEED_ECOSYSTEMS: Mapping[str, str] = {"pypi": "PyPI", "npm": "npm"}

#: A version list longer than this is not kept: the advisory is matched by its ranges instead.
_MAX_VERSIONS = 4000


def normalize_package(name: str) -> str:
    """PEP 503 normalisation, which is also right for npm's already-lower-case names."""
    return re.sub(r"[-_.]+", "-", str(name)).strip().lower()


def parse_osv_time(value: Any) -> Optional[datetime]:
    """An OSV RFC 3339 timestamp (nanoseconds allowed) as an aware UTC datetime, or ``None``."""
    if not isinstance(value, str) or not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    match = re.match(r"^(.*?\.\d{6})\d*(.*)$", text)
    if match:
        text = match.group(1) + match.group(2)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class StoredAdvisory:
    """One advisory as it affects one package: the unit the store keeps and looks up."""

    ecosystem: str          # "pypi" | "npm"
    package: str            # normalised
    advisory_id: str
    modified: datetime
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class EcosystemStatus:
    """How current the local copy of one ecosystem's advisories is."""

    ecosystem: str
    advisories: int = 0
    newest_modified: Optional[datetime] = None
    synced_at: Optional[datetime] = None
    last_error: Optional[str] = None
    last_attempt_at: Optional[datetime] = None


def age_days(status: EcosystemStatus, now: datetime) -> Optional[float]:
    """Days since ``status`` was last refreshed, or ``None`` if it never was."""
    if status.synced_at is None:
        return None
    return max(0.0, (now - status.synced_at).total_seconds() / 86400.0)


def is_withdrawn(advisory: Mapping[str, Any]) -> bool:
    return bool(advisory.get("withdrawn"))


def slim_advisory(advisory: Mapping[str, Any], ecosystem: str) -> list[StoredAdvisory]:
    """One :class:`StoredAdvisory` per package ``advisory`` affects in ``ecosystem`` (our key).

    Only what ``workspace_deps.match_advisories`` and the report use is kept: id, aliases, a short
    summary, severity, and per package its ranges and explicit versions. Free text and references,
    which are most of an OSV document, are dropped.
    """
    adv_id = advisory.get("id")
    modified = parse_osv_time(advisory.get("modified"))
    if not isinstance(adv_id, str) or not adv_id or modified is None:
        return []
    osv_name = FEED_ECOSYSTEMS[ecosystem].lower()
    base: dict[str, Any] = {
        "id": adv_id,
        "aliases": [str(a) for a in (advisory.get("aliases") or [])][:10],
        "summary": str(advisory.get("summary") or advisory.get("details") or "")[:300],
        "modified": advisory.get("modified"),
    }
    severity = (advisory.get("database_specific") or {}).get("severity")
    if severity:
        base["database_specific"] = {"severity": str(severity)[:40]}
    if isinstance(advisory.get("severity"), list):
        base["severity"] = [
            {"type": str(s.get("type", ""))[:20], "score": str(s.get("score", ""))[:80]}
            for s in advisory["severity"][:3] if isinstance(s, Mapping)
        ]
    out: dict[str, StoredAdvisory] = {}
    for entry in advisory.get("affected") or []:
        package = entry.get("package") if isinstance(entry, Mapping) else None
        if not isinstance(package, Mapping) or str(package.get("ecosystem", "")).split(":")[0].lower() != osv_name:
            continue
        name = package.get("name")
        if not isinstance(name, str) or not name:
            continue
        key = normalize_package(name)
        ranges = [
            {"type": r.get("type"), "events": [e for e in r.get("events") or [] if isinstance(e, Mapping)]}
            for r in entry.get("ranges") or [] if isinstance(r, Mapping)
        ]
        versions = [str(v) for v in entry.get("versions") or []]
        affected = {"package": {"name": name, "ecosystem": FEED_ECOSYSTEMS[ecosystem]}, "ranges": ranges}
        if len(versions) <= _MAX_VERSIONS:
            affected["versions"] = versions
        merged = out.get(key)
        slim = dict(base)
        slim["affected"] = (list(merged.payload["affected"]) if merged else []) + [affected]
        out[key] = StoredAdvisory(ecosystem, key, adv_id, modified, slim)
    return list(out.values())
