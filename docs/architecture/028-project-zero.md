# ADR-028 — Project 0: personal AURA on the universal engine, and the zero-knowledge boundary

**Status:** Proposed · **Date:** 2026-10-01 · **Track:** [AP](../roadmaps/AURA_ADAPTIVE_PLATFORM_ROADMAP.md)

> **Superseded in part by [ADR-032](032-personal-os-single-store.md) (Accepted 2026-10-31).** The
> synced tables are no longer zero-knowledge. The server holds each user's data key, and the
> agent's personal-OS tools read and write those tables directly. Decision 3 below therefore
> rests on a property that no longer exists. Whether those tables may be discovered or indexed
> is now a policy question, not an encryption boundary, and has to be decided before this ADR is
> accepted.

## Context

Personal AURA (tasks, calendar, finance, health, personas, Engineer) is the live product. Keeping
it as a separate hardcoded path forever would mean two code paths. Two stores are involved: the
server-readable `PersonalOsRecord` store (`aura_core/domain/personal_os.py`, kind plus free-form
fields) and the legacy `tasks` / `calendar_events` / `finance_records` / `health_records` tables,
which are **zero-knowledge encrypted** — the server is contractually forbidden to read them.

## Decision

1. Personal AURA becomes **Project 0**: a hand-written `blueprints/project0.yaml` running on the
   universal engine.
2. The migration is non-breaking and reversible:
   1. A parity test: the blueprint-built registry equals `build_default_agent_registry()` (ids,
      grants, ceilings, scopes, budgets).
   2. Activation with the hardcoded registry as fallback, plus a shadow evaluation for one release.
   3. A "native store" connector maps `PersonalOsRecord` kinds to AUSM.
   4. The Life Hub reads the manifest behind a flag.
   5. Hardcoded coupling is retired only after the parity and shadow evidence exists.
3. **The zero-knowledge boundary is permanent.** The client-encrypted legacy tables are never
   discovered, profiled, labeled or indexed. Their tools stay hand-written and appear in the
   blueprint only as opaque `native` tools.

## Alternatives rejected

- **Keep personal AURA as a built-in product.** Two code paths forever.
- **Discover the encrypted tables server-side.** Inverts the encryption guarantee.

## Consequences

- Project 0 is the first proof that the engine can carry a real product, using a system whose
  behaviour is already well tested.
- The Engineer persona remains core (not blueprint-generated) because it is capability-gated
  infrastructure, not a domain.
