---
name: system-audit
description: Perform an evidence-based, read-only audit of an entire codebase or selected module across UI/UX, user journeys, business logic, architecture, security, performance, data correctness, reliability, tests, maintainability, and algorithm choices. Produce ranked findings and a dependency-aware remediation and upgrade roadmap. Use before implementing upgrades or when asked to find system issues, broken flows, bugs, design problems, or improvement opportunities.
argument-hint: "<project-paths> [module=<name>] [objective=<goal>] [mode=full|module|delta]"
disable-model-invocation: true
effort: high
---

# System Audit

## Mission

Audit the system that actually exists—not the system documentation claims exists. Find and explain defects, broken or incomplete flows, user-experience friction, design inconsistencies, architectural risks, weak algorithms, performance bottlenecks, missing verification, and evidence-backed opportunities to improve it. Deliver a useful report and a staged remediation roadmap.

This is an **audit and planning workflow, not an implementation workflow**. Do not edit, refactor, delete, install, migrate, deploy, or otherwise modify product code. The only permitted writes are audit artifacts explicitly described below. Never claim that every possible issue has been found; explain coverage and remaining uncertainty honestly.

## Invocation and arguments

Read `$ARGUMENTS` and resolve:

- `project-paths`: one or more repository, workspace, or module paths. If omitted, use the current working directory.
- `module=<name-or-path>`: focus the audit on a module, feature, screen, service, or subsystem. Follow its upstream and downstream dependencies and callers; do not inspect it in isolation.
- `objective=<goal>`: the intended behavior, user outcome, business rule, or upgrade target. Treat this as a review lens, not proof that the current implementation is correct.
- `mode=full|module|delta`: default to `full`; `module` narrows the primary scope but still traces dependencies; `delta` emphasizes recent changes while checking adjacent regression surfaces.
- Optional `baseline=<path>`: prior audit, roadmap, acceptance criteria, or specification to compare with the live code.

If a parameter is omitted, make the safest useful assumption and state it in the report. Do not stop for clarification unless the paths or objective are genuinely impossible to interpret.

Examples:

```text
/system-audit "E:\Projects\AURA" objective="Find UI/UX, logic, and workflow defects and produce an upgrade roadmap" mode=full
/system-audit ./aura_client module="chat and memory UI" objective="Find broken states, navigation problems, sync defects, and UX improvements" mode=module
/system-audit ./backend ./client module=authentication mode=module baseline=docs/audits/latest.md
/system-audit . mode=delta
```

## Non-negotiable rules

1. **Inspect before judging.** Read the project instructions, architecture decisions, current tests, contracts, schemas, and relevant prior audit reports before proposing changes.
2. **Current code is the implementation evidence.** Treat older reports, plans, comments, and README statements as claims to verify. Mark stale or contradictory documentation; do not silently inherit its assumptions.
3. **Preserve intent and architecture.** Follow `AGENTS.md`, local rules, ADRs, product requirements, and documented security boundaries unless concrete evidence shows a blocker. Recommend the smallest safe change that solves the demonstrated problem. Do not prescribe rewrites or new infrastructure by default.
4. **Read-only product audit.** Do not edit source code, tests, configuration, manifests, lockfiles, databases, generated assets, or deployment state. Do not install packages, perform migrations, change secrets, call production-mutating endpoints, or deploy. Running a check must be safe for the environment.
5. **Evidence before findings.** Every defect must point to exact file paths and line ranges or to captured test/runtime evidence. Never invent line numbers, test results, metrics, reproductions, screenshots, or framework capabilities.
6. **Separate defects from opportunities.** A bug violates intended behavior or a technical invariant. A recommendation improves a trade-off, maintainability, usability, or future readiness. Do not present preference as a defect.
7. **Trace end-to-end behavior.** Follow UI events through state management, domain logic, APIs, persistence, asynchronous work, and the result shown to the user. Check both success and failure paths.
8. **Verify, then challenge your own conclusions.** Try to falsify high-impact findings, inspect callers and tests, check for an existing guard or a deliberate trade-off, and deduplicate symptoms that share one root cause.
9. **Never equate unavailable checks with passing checks.** `NOT RUN`, `BLOCKED`, `PARTIALLY VERIFIED`, and `UNKNOWN` must remain explicit. Do not run a dangerous or poorly understood test just to complete a checklist.
10. **Recommendations need a reason and validation method.** Only recommend algorithms, libraries, patterns, or infrastructure when tied to a demonstrated requirement, bottleneck, risk, or maintainability problem.
11. **Protect sensitive information.** Do not print secrets, tokens, private user data, or environment values into reports. Name the exposure and location with sensitive values redacted. Do not upload source code or private data to third-party services.
12. **Do not fix findings during the audit.** The user must explicitly request a separate implementation task after reviewing the report.

## Phase 0 — Establish a trustworthy baseline

1. Parse the arguments and confirm that each target path exists. Identify whether the workspace is a repository, monorepo, multi-repository setup, or partial checkout.
2. Read applicable `AGENTS.md`, `.Codex/rules/`, package/project manifests, README files, architecture documents, ADRs, API schemas, data models, design tokens, test configuration, CI workflows, and relevant existing audit/roadmap documents.
3. Capture a safe baseline where available: repository root, branch, commit, working-tree status, detected stacks, entry points, run/build/test commands, and required external services. Do not expose sensitive environment values.
4. Identify user journeys and product invariants from specifications, domain code, tests, API contracts, UI copy, and documented rules. Record conflicts instead of guessing which source is authoritative; prioritize explicit user requirements and current enforced contracts, then document unresolved conflicts.
5. State audit scope, exclusions, baseline revision, execution limitations, and the approach used for static, test, and runtime verification.

Do not mistake a file count, grep match, TODO, large class, or static-analysis warning for a confirmed defect. Inventory findings are leads to investigate.

## Phase 1 — Build the actual system map

Map, as applicable:

- applications, packages, services, workers, jobs, agents, and deployment units;
- UI entry points, screens, navigation routes, reusable components, state owners, and event handlers;
- API endpoints, request/response schemas, authentication/authorization checks, and client callers;
- domain services, business rules, persistence, caches, queues, event buses, synchronization, and external integrations;
- model/provider routing, tool execution, retrieval, agents, evaluation, and safety boundaries in AI systems;
- configuration ownership, environment-dependent behavior, failure boundaries, logging, metrics, and tests.

Draw a concise Mermaid or text diagram where it helps. Distinguish observed paths from documentation-only paths. For a module-specific run, trace its callers, shared state, dependencies, public contracts, and user-visible effects.

## Phase 2 — Run the audit lenses

Use the lenses in `references/audit-checklist.md`. Adapt the checklist to the detected technology and actual product. Do not force irrelevant categories onto a project.

When supported by the current Codex environment, use independent read-only subagents for separate lenses, such as (1) UI/UX and accessibility, (2) user flows and business logic, (3) architecture and data/API contracts, (4) security and privacy, and (5) performance, reliability, and tests. Give each worker a bounded scope and the same evidence/finding rules. If subagents are unavailable, perform the work sequentially. The main auditor must merge, cross-check, deduplicate, and verify worker findings; copied agent output is not automatically accepted as fact.

For large repositories, partition by actual module boundaries and maintain an explicit progress matrix. Resume incomplete partitions without re-auditing clean unchanged areas blindly. Perform a final cross-module pass for duplicate findings, contradictory recommendations, broken contracts, shared-state interactions, and dependency ordering.

### UI/UX verification rule

Review source-level UI structure, state handling, routes, styles, semantics, and responsive constraints. If a safe running application and browser/test tooling already exist, inspect representative screens and journeys at relevant breakpoints and states. Do not install browser tooling or alter project setup merely for this audit. If no live rendering, screenshots, or assistive-technology checks were performed, label visual, responsive, and accessibility claims as code-inferred or unverified rather than runtime-confirmed.

### Testing and runtime rule

First discover the established project commands and their side effects. Run only relevant checks that are available and safe. Record the exact command, scope, exit status, and meaningful result. Prefer narrow relevant checks before broader suites. Do not assume a test passed because it exists, a CI file defines it, or a previous report says it passed. Do not run production integrations, destructive tests, real emails/payments, migrations, or commands with unclear side effects.

## Phase 3 — Validate and classify each finding

Before keeping a finding:

1. Re-open the precise evidence and record accurate file/line references.
2. Trace the affected path from trigger to outcome, including error and edge paths.
3. Inspect relevant tests, guards, contracts, feature flags, and callers to determine whether the behavior is intentionally handled elsewhere.
4. Try to falsify the finding. Check whether it is a duplicate, stale, a documented trade-off, or only a possible risk.
5. If safely reproducible, record the reproduction command or steps and the actual result. Otherwise say that it was inferred from code and remains unverified.
6. State the likely root cause separately from the visible symptom. Do not overstate causality where it has not been proven.
7. Recommend a minimal corrective approach and define how its success would be demonstrated.

Use these evidence states consistently:

- **CONFIRMED** — directly reproduced or demonstrated by an executed check with evidence.
- **CODE-VERIFIED** — the defect follows clearly from the inspected implementation and contract, but was not reproduced at runtime.
- **PARTIALLY VERIFIED** — part of the claim is evidenced; another material part remains unknown.
- **UNVERIFIED** — a plausible hypothesis or risk that lacks sufficient evidence. Keep it out of the confirmed-defect count.
- **NOT RUN** — a relevant check was not executed; give the reason.
- **CLEARED** — an audited hypothesis was investigated and evidence supports that it is not a defect. Normally summarize cleared hypotheses in coverage notes instead of listing them as findings.

Every finding must include: unique ID, title, category, severity, evidence state, affected path/module, exact evidence, triggering conditions, actual versus expected behavior, user/system impact, root cause or explicit uncertainty, recommended fix, regression-test strategy, dependencies, and confidence. Keep each finding to one coherent root cause; link related symptoms instead of creating inflated counts.

## Phase 4 — Rank severity and recommendations

Use the severity definitions in `references/audit-checklist.md`. Rank by user/business impact, security/data exposure, reach, likelihood, frequency, reversibility, and dependency centrality. Explain the rationale. Avoid fake precision or unsupported claims such as a percentage improvement without measurement.

For every algorithm or technique recommendation, include:

- demonstrated problem and why the current approach may be insufficient;
- current approach and its observed costs/constraints;
- candidate alternative(s), plus why they fit the actual requirements;
- correctness implications, expected time/space complexity where applicable, operational cost, and migration risk;
- conditions under which the alternative would *not* be better;
- a benchmark, test, evaluation corpus, or measurable acceptance criterion to validate it.

Do not introduce microservices, distributed systems, caching, queues, a framework rewrite, AI/ML models, or sophisticated algorithms without a requirement or measured need. Prefer reuse of existing project capabilities where appropriate.

## Phase 5 — Create the remediation and upgrade roadmap

Build the roadmap from verified findings, then add clearly labeled strategic opportunities. Group work by dependency and risk—not simply by folder or by how exciting the upgrade sounds.

For each roadmap item provide:

- roadmap ID and linked finding IDs;
- objective and user/system outcome;
- specific modules/files/contracts likely to change (mark as provisional if not yet fully traced);
- priority and relative effort (`S`, `M`, or `L`, not a time promise);
- prerequisites, dependencies, and migration/rollback considerations;
- implementation approach and alternatives rejected where important;
- acceptance criteria that are observable and testable;
- tests, instrumentation, baselines, or evaluations required before declaring success.

Separate at least: (1) immediate safety/data-loss/blocking risks, (2) correctness and broken user journeys, (3) architecture and reliability work, (4) performance/algorithm improvements backed by measurement, (5) UX/accessibility and product-quality upgrades, and (6) longer-term strategic opportunities. Reorder these when actual dependencies or risk justify a different sequence. Identify quick wins without letting them displace critical fixes.

## Phase 6 — Final cross-check and deliverables

Before finalizing:

- verify each reported path and line reference still exists in the inspected baseline;
- cross-reference findings across UI, API, domain logic, persistence, background work, and tests;
- deduplicate symptoms and distinguish correlated issues from proven causality;
- check that every high-priority finding appears in the roadmap and every roadmap claim is supported or labeled as an opportunity;
- check that current architecture constraints and deliberately accepted trade-offs are preserved;
- summarize what could not be inspected or tested and the resulting residual risk;
- do not call the system “production-ready,” “fully secure,” or “bug-free” without explicit, scoped evidence supporting that conclusion.

Read `references/report-template.md` before writing the final report.

Save reports, if the workspace is writable, under `.Codex/audits/<run-id>/` as:

- `audit-report.md` — executive summary, system map, coverage, detailed findings, verification evidence, limitations, and recommendations;
- `remediation-roadmap.md` — sequenced fix/upgrade phases with acceptance criteria and dependencies;
- `audit-manifest.md` — baseline revision, scope, coverage matrix, commands run/not run, and files/modules reviewed.

These are the only permitted writes. Do not modify product files. If creating report files is not possible or the user asks for chat-only output, provide the same structured report in the response. Use a stable, non-sensitive run identifier; avoid adding personal information or secrets to filenames.

## Completion criteria

The audit is complete only when it has a defined scope and baseline, an implementation-derived system map, a coverage matrix, validated and severity-ranked findings, an actionable dependency-aware roadmap, an explicit test/runtime verification record, and a residual uncertainty section. If the repository is too large for one pass, finish a tracked partial audit with completed/pending areas clearly marked rather than implying whole-codebase coverage.

## Additional resources

- [Audit lenses and severity rubric](references/audit-checklist.md)
- [Report and roadmap structure](references/report-template.md)
