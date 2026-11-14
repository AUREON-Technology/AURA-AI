# Audit Lenses and Severity Rubric

Adapt this checklist to the observed system. A checklist item is a question to investigate, not a presumption that a defect exists. Record coverage as `REVIEWED`, `PARTIAL`, `NOT RUN`, or `NOT APPLICABLE`, with a brief evidence note.

## 1. Product purpose and requirements

- What are the primary user roles, workflows, business goals, and invariants?
- Do implemented behavior, UI copy, API contracts, docs, and tests agree?
- Which capabilities are implemented, partial, stubbed, mocked, unreachable, or only documented?
- Are permissions, limits, business policies, and deliberate trade-offs explicit?
- Are old audit findings still true at the current revision, fixed, regressed, or obsolete?

## 2. UI/UX and visual quality

- Information hierarchy, readability, typography, spacing, alignment, contrast, density, and consistency.
- Design tokens versus scattered hard-coded values; repeated patterns versus inconsistent component variants.
- Responsive layouts, overflow, clipping, safe areas, orientation, scaling, desktop/window resizing, and supported breakpoints.
- Navigation clarity, back behavior, route guards, deep links, modal/drawer dismissal, focus order, and prevention of accidental data loss.
- Forms: labels, validation timing, inline errors, input type/format, keyboard behavior, disabled states, submission progress, duplicate-submit prevention, and recovery after errors.
- Every relevant state: initial/loading, loaded, empty, partial data, error, offline, slow connection, retry, permission denied, stale data, and success feedback.
- Accessibility: semantic roles/names, keyboard access, visible focus, contrast, text scaling, screen-reader order, reduced motion, target size, and non-color status cues.
- UX copy: clarity, consistency, understandable errors, next steps, destructive-action confirmation, and user confidence.
- Visual issues require rendered evidence when claimed as visually confirmed. Static code may reveal likely problems but cannot prove actual appearance.

## 3. User journeys and interaction flow

Trace end-to-end scenarios rather than inspecting screens independently:

- Entry point → navigation → user input → state transition → request/business operation → persistence → visible result.
- Happy paths, invalid input, cancellations, retries, duplicate events, repeated taps, timeouts, interrupted operations, partial success, and app/process restarts.
- Loading indicator start/stop, stale requests, response ordering, race conditions, optimistic updates, rollback, and stale caches.
- Authentication expiry, unauthenticated state, authorization changes, onboarding, permissions, account changes, and recovery.
- Back/forward, deep links, restoration after process death, and transitions between online/offline states.
- Are users trapped, forced into unnecessary steps, asked for redundant information, or shown success when the underlying operation failed?
- Are events handled exactly once where required? Are retries idempotent? Can operations be safely resumed?

## 4. Business logic and domain correctness

- Explicit domain invariants and validation enforced at the correct trust boundary.
- Boundary values, null/missing fields, empty collections, locale, date/time zones, daylight saving, currency, rounding, precision, units, and type conversions where relevant.
- Role and ownership rules, workflow/state-machine transitions, lifecycle rules, quotas, and derived values.
- Duplicated business rules across client/server or across modules; inconsistent defaults and conflicting sources of truth.
- Off-by-one errors, inverted conditions, unreachable branches, accidental fallbacks, incorrect sorting/filtering, and improper error-to-success conversion.
- Transaction boundaries, atomic updates, idempotency keys, duplicate processing, optimistic concurrency, and integrity constraints.
- AI/ML features: prompt/response contracts, grounding, citations, evaluation datasets, confidence handling, model/provider mismatch, tool authorization, approval gates, and safe fallback behavior.

## 5. Architecture and maintainability

- Actual dependency directions and module boundaries; circular dependencies, hidden coupling, excessive reach-through, duplicate abstractions, and unclear ownership.
- Separation of UI, state, domain/business logic, persistence, transport, and infrastructure responsibilities appropriate to the stack.
- SOLID/design patterns as diagnostic lenses, not mandates; identify concrete change friction or correctness risk before recommending patterns.
- Single source of truth, lifecycle/resource ownership, dependency injection, configuration centralization, and testability.
- Dead/unreachable code, unused APIs, duplicate or divergent implementations, stubs/mocks accidentally used in production, and misleading documentation.
- API compatibility, schema evolution, versioning, migrations, backward compatibility, feature flags, and rollback.
- Whether the architecture is fit for current measured requirements; do not optimize for speculative scale.

## 6. API, contracts, and integration

- Client callers versus actual server routes; unreachable, deprecated, shadowed, or incorrectly mounted routes.
- Request/response schemas, nullability, validation, error semantics, status codes, pagination, filtering, sorting, timeouts, and cancellation.
- Authentication/authorization on every sensitive path; role/tenant/owner scoping; mass assignment; schema and serialization boundaries.
- Retry policy, idempotency, rate limits, exponential backoff with jitter where suitable, circuit breaking, bulkheads, and bounded work.
- Streaming lifecycle, disconnect handling, partial frames, backpressure, correlation IDs, and cleanup.
- Compatibility between client, server, background worker, model provider, and schema versions.

## 7. State, data, persistence, and synchronization

- Correct source of truth and well-defined ownership for durable versus ephemeral state.
- Schema constraints, indexes, query shape, N+1 queries, pagination, migrations, rollback, data retention, and backups where relevant.
- Cache invalidation, stale data, local/remote conflict resolution, eventual consistency, offline write queues, and ordering guarantees.
- Transactions, locking, concurrent initialization, shared mutable state, deadlocks, race windows, resource leaks, and connection-pool limits.
- Data integrity during retries, crashes, app restarts, partial failures, and recovery.
- Whether logs, backups, indexes, analytics, and caches leak data that the primary store protects.

## 8. Security, privacy, and safety

- Secrets in source, logs, client bundles, build artifacts, tests, history, defaults, or error messages. Report locations only; redact values.
- Authentication, authorization, object-level access, tenant isolation, session expiry, token storage, CSRF/CORS as applicable, and insecure direct object references.
- Injection, unsafe parsing/deserialization, command execution, path traversal, SSRF, XSS, SQL/NoSQL injection, upload handling, and unsafe file operations as relevant.
- Input validation, output encoding, rate limiting, abuse controls, secure headers, TLS assumptions, dependency risks, and least-privilege permissions.
- Sensitive data minimization, retention/deletion, export, encryption in transit/at rest, auditability, and consent where relevant.
- AI/tool risks: prompt injection, data taint, tool permission checks, human confirmation, untrusted retrieved content, sandboxing, and model-generated actions bypassing policy.
- Fail-open versus fail-closed behavior at security boundaries; default configurations safe for their deployment environment.

## 9. Performance and scalability

- Measure or identify the actual critical path: rendering, application CPU, network, DB, vector search, queue wait, model inference, filesystem, serialization, or external service.
- Big-O behavior with realistic cardinality; nested scans, repeated work, excess copying, large payloads, over-fetching, unbounded collections/queues, and blocking work on async/event-loop paths.
- Caching only when reuse and invalidation are clear; batching only when it preserves latency and correctness; concurrency only when resources and ordering permit it.
- Database query plans/indexes, pool sizing, rate limits, queue depth, backpressure, model batching, inference parallelism, and memory/VRAM constraints where applicable.
- Client startup/build size, list virtualization, unnecessary rebuilds/renders, image/resource loading, and UI jank where relevant.
- Benchmarks include environment, workload/data size, concurrency, warm/cold state, model/provider, metrics, sample count, and baseline. Never compare unlike conditions as if comparable.

## 10. Reliability and operability

- Error handling preserves causal information without leaking secrets; no silent swallowed exception or false success.
- Timeouts, cancellation propagation, retries, idempotency, circuit recovery, resource cleanup, graceful shutdown, health/readiness, and restart behavior.
- Background-job retries, dead-letter behavior, scheduling/time zones, duplicate execution, durable event delivery, and replay safety.
- Structured logs, useful metrics, traces/correlation IDs, actionable alerts, redaction, SLO/SLI definitions, and overload behavior.
- Backup and restore evidence, disaster recovery, migration verification, deployment safety, rollback, and configuration drift.

## 11. Tests, evaluation, and CI/CD

- Unit, integration, contract, UI/widget, end-to-end, accessibility, visual regression, performance/load, security, and failure-injection coverage appropriate to the project.
- Tests target invariants and user journeys, not only implementation details or mocks.
- Assertions meaningful enough to fail for the real bug; negative/error path coverage; deterministic fixtures and time/network isolation.
- Flaky tests, skipped tests, ignored failures, non-gating workflows, tests never called by CI, and tests that use mocks where production adapters differ materially.
- Build/lint/type checks, dependency scanning, secret scanning, migrations, artifact reproducibility, staging smoke tests, and rollback checks.
- AI/ML: labeled evaluation datasets, retrieval metrics, groundedness/citation correctness, task success, safety tests, latency/cost, and regression gates with stated measurement conditions.

## 12. Algorithms, libraries, and technical upgrades

For each proposed replacement, compare the current approach with the candidate using:

- correctness and domain fit;
- time/space complexity, realistic data sizes, and bottleneck evidence;
- operational burden, dependencies, resource needs, observability, and failure modes;
- implementation/migration complexity and compatibility;
- measurable test or benchmark that could disprove the recommendation.

Examples of candidate areas to evaluate only when relevant: better indexing/query plans, hash/map/set-based lookup, streaming instead of materialization, pagination, state machines, idempotent commands, work queues, bounded concurrency, cache policy, better retrieval/ranking, structured evaluation, or simpler domain abstractions. These are examples, not defaults. Do not recommend a technique merely because it is more advanced.

## Severity levels

- **P0 — Critical:** active or readily exploitable security/data-loss risk, systemic corruption, or complete loss of a critical capability. State the specific evidence and immediate containment recommendation.
- **P1 — High:** a critical user journey is blocked or materially incorrect; privilege/privacy boundary fails; high-likelihood production failure or serious data-integrity issue.
- **P2 — Medium:** meaningful feature/flow defect, recurring user friction, important reliability/performance problem, or maintainability issue that increases near-term change risk.
- **P3 — Low:** limited-impact defect, consistency/polish issue, non-urgent cleanup, or low-risk opportunity.

Use `INFO / OPPORTUNITY` for worthwhile improvements that are not defects. Never inflate severity to make the roadmap seem important. Severity measures impact and urgency; confidence/evidence state is a separate dimension.

## Suggested coverage statuses

`REVIEWED` = relevant sources inspected; `PARTIAL` = material surfaces could not be inspected; `NOT RUN` = a runtime/check step was not performed; `BLOCKED` = a concrete access/tool/environment blocker; `N/A` = not applicable with a reason.
