"""Run the billing cycle once (SaaS P1-D1): renewals, past due -> grace -> expired, reconciliation.

Meant for a Railway cron service (hourly) with the same variables as the gateway:

    python scripts/run_billing_cycle.py

Prints the cycle's counts as JSON; exits 1 if any payment or renewal could not be processed
(the next run retries them).
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def _run() -> int:
    from aura_core.config import load_settings
    from aura_core.container import Container

    container = Container(load_settings())
    if not container.settings.billing.enabled:
        print(json.dumps({"skipped": "billing is not enabled"}))
        return 0
    try:
        counts = await container.billing.run_cycle()
    finally:
        close = getattr(container, "shutdown", None)
        if close is not None:
            await close()
    print(json.dumps(counts))
    return 1 if counts.get("errors") else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
