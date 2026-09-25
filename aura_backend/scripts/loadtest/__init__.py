"""Concurrent-user load harness for the AURA v3 gateway (roadmap Phase 3).

A measuring instrument, not a capacity claim. It drives a *deployed* gateway over its real
HTTP/SSE surface (``/api/v3/chat/stream``, ``/api/v3/voice/*``, ``/api/v3/knowledge/*``,
``/api/v3/memory``) with a matrix of closed-loop virtual users, and writes a JSON results file
plus a Markdown report that records the run conditions next to every number. See
``docs/guides/load-testing.md`` for the runbook and the rule that capacity is accepted only after
this measurement on the target hardware.

Deliberately independent of ``aura_core``: the load generator should run from a host that has
nothing but ``httpx`` (and ``PyJWT`` for minted test identities), so its modules import nothing
from the application. The one exception is :mod:`.inprocess`, which builds the real FastAPI app on
the in-memory container for the harness's own self-test, and imports ``aura_core`` lazily.

    cd aura_backend
    python -m scripts.loadtest run --base-url https://staging.example --auth mint ...
    python -m scripts.loadtest render results.json      # re-render a report from its JSON
"""

from __future__ import annotations

HARNESS_VERSION = "1.0"

__all__ = ["HARNESS_VERSION"]
