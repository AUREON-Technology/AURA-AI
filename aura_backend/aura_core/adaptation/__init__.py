"""The Adaptive Intelligence Layer (Track AP, ADR-018).

Turns a connected system into a Super-Admin-approved Project Blueprint the unchanged core
executes. This package only ever produces *data* -- descriptors, twins, blueprints, specs -- and
never imports kernel internals, edits tool policy or loads code. It is an engine package: it may
use domain types and ports, never adapters (``tests/architecture/test_dependency_rules.py``).

Built so far: the connector registry (AP2), discovery (AP3) and the Project Twin -- normalisation,
merge proposals, process mining v0, optional labeling, versions and diffs (AP4), capability
planning (AP5), the factories and blueprint compiler (AP6), the blueprint runtime -- agent
snapshot, generated read tools, deploy and rollback (AP7) -- and the generated evaluation gate that
every version passes before it can be approved (AP8).
See ``docs/roadmaps/AURA_ADAPTIVE_PLATFORM_ROADMAP.md``.
"""
