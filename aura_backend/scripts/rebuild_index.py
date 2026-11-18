#!/usr/bin/env python3
"""Rebuild the knowledge index for the configured embedder, gate it, and promote it (ADR-007).

The same sequence as ``POST /api/v3/admin/index/rebuild``, without an HTTP request that a proxy
may time out on a large corpus. It is what an embedding-model change needs: an index's identity
includes the embedding model (and, for E5 models, the document prefix), so after
``AURA_EMBEDDING_MODEL`` changes the alias still points at the old model's index and reads
refuse it (``IndexManager.resolve_for_read``) until a new one is built and promoted. The document
store is the source of truth, so the build reads every active chunk from there; the previous
index stays in place for rollback.

Switching to the multilingual embedder (docs/plans/arabic-language_plan.md, stage 4)::

    # 1. set AURA_EMBEDDING_MODEL=intfloat/multilingual-e5-small (dimension stays 384), restart
    # 2. inside the gateway container:
    docker compose -p aura -f deploy/docker-compose.prod.yml exec gateway \\
        python scripts/rebuild_index.py
    # Railway: railway ssh, then: python scripts/rebuild_index.py

``--dry-run`` prints the target identity and the active one and changes nothing.
``--force`` promotes even if the promotion gate fails (after reading why it failed).

Exit status: 0 promoted (or dry run), 1 built but not promoted (gate failed), 2 build failed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def run(*, dry_run: bool, force: bool) -> int:
    from aura_core.config.settings import load_settings
    from aura_core.container import Container

    container = Container(load_settings(refresh=True, validate=False))
    await container.startup()
    try:
        manager = container.index_manager
        target = manager.identity_for_current_embedder()
        active = await manager.active_identity()
        print(json.dumps({
            "target": target.physical_name,
            "active": active.physical_name if active else None,
            "embedding_model": target.embedding_model_id,
            "dimension": target.dimension,
        }, indent=2))
        if dry_run:
            return 0
        try:
            report = await manager.build(identity=target)
        except Exception as exc:  # noqa: BLE001 - report and exit, never half-promote
            print(f"build failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 2
        gate = await manager.evaluate_promotion(target, report=report)
        print(json.dumps({
            "documents": report.documents, "chunks": report.chunks, "failures": report.failures,
            "gate_passed": gate.passed, "gate_failures": list(gate.failures),
            "measurements": dict(gate.measurements),
        }, indent=2, default=str))
        if not gate.passed and not force:
            print("not promoted: the gate failed (rerun with --force to promote anyway)",
                  file=sys.stderr)
            return 1
        await manager.promote(target, gate=gate, reason="cli_rebuild" if gate.passed else "cli_forced")
        print(f"promoted {target.physical_name}")
        return 0
    finally:
        await container.shutdown()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="show identities, change nothing")
    parser.add_argument("--force", action="store_true", help="promote even if the gate fails")
    args = parser.parse_args(argv)
    return asyncio.run(run(dry_run=args.dry_run, force=args.force))


if __name__ == "__main__":
    raise SystemExit(main())
