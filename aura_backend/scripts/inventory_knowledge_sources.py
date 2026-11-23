#!/usr/bin/env python3
"""Read-only inventory of the global knowledge corpus: which sources exist, what each one put in.

Answers the questions the knowledge readiness work could not answer from the repository alone,
because the registry lives in the production database:

* **KB-004** -- which global sources have no approved licence yet still have live documents. Those
  were ingested before the licence gate (migration 0032) and are retrievable by every user.
* **KB-010** -- which enabled sources have a type no fetcher handles any more, so the nightly refresh
  fails them with "no fetcher".
* Documents in the global corpus that no registered source accounts for.

It writes nothing: it lists sources, pages through the global documents and prints JSON. Run it where
the database is reachable, the same way as ``scripts/rebuild_index.py``::

    # Railway: railway ssh, then: python scripts/inventory_knowledge_sources.py
    # VPS:     docker compose -p aura -f deploy/docker-compose.prod.yml exec gateway \\
    #              python scripts/inventory_knowledge_sources.py

Exit status: 0 when the inventory ran; 2 when it could not read the registry or the corpus.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def run() -> int:
    from aura_core.config.settings import load_settings
    from aura_core.container import Container
    from aura_core.knowledge.source_retirement import documents_of_source

    container = Container(load_settings(refresh=True, validate=False))
    await container.startup()
    try:
        try:
            registry = container.sources
            listing = getattr(registry, "list_all", None)
            sources = list(await listing() if listing else await registry.list_enabled())
            allowed = container.settings.knowledge.allowed_licenses
            global_documents = []
            offset = 0
            while True:
                page = await container.documents.list_active(owner_id=None, limit=100, offset=offset)
                if not page:
                    break
                global_documents.extend(page)
                if len(page) < 100:
                    break
                offset += 100
        except Exception as exc:  # noqa: BLE001 - report and exit rather than print a partial picture
            print(f"could not read the registry or the corpus: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 2

        accounted: set[str] = set()
        rows = []
        for source in sources:
            owned = await documents_of_source(container.documents, source.id, owner_id=source.owner_id)
            if source.owner_id is None:
                accounted.update(d.id.value for d in owned)
            rows.append({
                "id": source.id,
                "type": str(source.type),
                "scope": "global" if source.owner_id is None else "owned",
                "enabled": source.enabled,
                "license_spdx": source.license_spdx,
                "license_status": str(source.license_status),
                "ingestible": source.permits_global_corpus(allowed),
                # ``supports`` arrives with the WP4 fetcher framework; before it, every type fell
                # through to the single-page fetcher, so there is nothing to report (null).
                "fetcher_exists": (
                    container.source_fetcher.supports(source.type)
                    if hasattr(container.source_fetcher, "supports") else None
                ),
                "live_documents": len(owned),
                "last_success_at": source.last_success_at.isoformat() if source.last_success_at else None,
            })

        unlicensed_with_documents = [
            r["id"] for r in rows
            if r["scope"] == "global" and r["live_documents"] and not r["ingestible"]
        ]
        unfetchable_enabled = [r["id"] for r in rows if r["enabled"] and r["fetcher_exists"] is False]
        unaccounted = [d.id.value for d in global_documents if d.id.value not in accounted]

        print(json.dumps({
            "summary": {
                "sources": len(rows),
                "global_documents": len(global_documents),
                "kb004_unlicensed_sources_with_live_documents": unlicensed_with_documents,
                "kb010_enabled_sources_without_a_fetcher": unfetchable_enabled,
                "global_documents_no_source_accounts_for": len(unaccounted),
            },
            "sources": rows,
            "unaccounted_document_ids": unaccounted[:200],
        }, indent=2))
        return 0
    finally:
        await container.shutdown()


def main() -> int:
    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
