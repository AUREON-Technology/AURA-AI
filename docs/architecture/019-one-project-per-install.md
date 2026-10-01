# ADR-019 — One project per install

**Status:** Proposed · **Date:** 2026-10-01 · **Track:** [AP](../roadmaps/AURA_ADAPTIVE_PLATFORM_ROADMAP.md)

## Context

Nothing in AURA sits above `user_id`: knowledge, memory and graph stores scope by owner; the tool
and agent registries are process-wide. Serving several connected systems from one install would
require a tenant boundary through every store, index, registry, queue, audit trail and metric —
the largest single change Track AP could make, and one the master roadmap's principle 8 ("no
distributed DB / microservices without evidence") argues against doing speculatively.

## Decision

1. **One AURA install serves exactly one Project** (one connected system, one active blueprint).
   A customer with two systems runs two installs.
2. Every new Track AP table carries a non-null `project_id` column (default `p0`). It is a key, not
   a tenancy model: the install resolves exactly one value at startup, no API accepts it as input,
   and no query filters on a caller-supplied value.
3. Personal AURA is **Project 0** (ADR-028).

## Alternatives rejected

- **Multi-project instance.** Requires a tenant boundary everywhere above; deferred until a load
  or ops report shows install-per-project is the bottleneck.
- **Multi-org SaaS.** Billing, quotas and org hierarchy are out of scope.

## Consequences

- Isolation between projects is process/host isolation — the strongest kind, and already how the
  prod and dev stacks are separated.
- A later move to multiple projects per install avoids a full-table rewrite because the key exists;
  it would amend this ADR.
