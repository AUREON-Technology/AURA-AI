"""In-memory :class:`~aura_core.ports.advisories.AdvisoryStore` -- the real substrate for tests and a
single-process run, with the same merge semantics as the Postgres one."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..domain.advisories import EcosystemStatus, StoredAdvisory

__all__ = ["InMemoryAdvisoryStore"]


class InMemoryAdvisoryStore:
    def __init__(self) -> None:
        self._rows: dict[tuple[str, str, str], StoredAdvisory] = {}
        self._status: dict[str, EcosystemStatus] = {}

    async def upsert(self, items: Sequence[StoredAdvisory]) -> int:
        for item in items:
            self._rows[(item.ecosystem, item.package, item.advisory_id)] = item
        return len(items)

    async def remove(self, ecosystem: str, advisory_ids: Sequence[str]) -> int:
        ids = set(advisory_ids)
        gone = [k for k in self._rows if k[0] == ecosystem and k[2] in ids]
        for key in gone:
            del self._rows[key]
        return len(gone)

    async def lookup(self, ecosystem: str, packages: Sequence[str]) -> list[Mapping[str, Any]]:
        wanted = set(packages)
        merged: dict[str, dict[str, Any]] = {}
        for (eco, package, adv_id), row in self._rows.items():
            if eco != ecosystem or package not in wanted:
                continue
            if adv_id in merged:
                merged[adv_id]["affected"] = merged[adv_id]["affected"] + list(row.payload["affected"])
            else:
                merged[adv_id] = {**row.payload, "affected": list(row.payload["affected"])}
        return list(merged.values())

    async def status(self) -> list[EcosystemStatus]:
        counts: dict[str, set[str]] = {}
        for eco, _package, adv_id in self._rows:
            counts.setdefault(eco, set()).add(adv_id)
        out = []
        for eco in sorted(set(self._status) | set(counts)):
            recorded = self._status.get(eco, EcosystemStatus(eco))
            out.append(EcosystemStatus(
                eco, len(counts.get(eco, ())), recorded.newest_modified, recorded.synced_at,
                recorded.last_error, recorded.last_attempt_at))
        return out

    async def record_sync(self, status: EcosystemStatus) -> None:
        self._status[status.ecosystem] = status
