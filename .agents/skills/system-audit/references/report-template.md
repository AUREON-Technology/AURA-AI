# Audit Report and Roadmap Template

Replace bracketed instructions with evidence. Do not leave headings that imply a check passed when it was not performed.

## `audit-report.md`

# System Audit Report

- **Project / repository:** [name/path]
- **Audit run:** [run identifier]
- **Baseline:** [commit/revision or `unknown`]
- **Audit mode and scope:** [full/module/delta, paths, exclusions]
- **Objective:** [user goal]
- **Date:** [date if available]
- **Method:** [static review, tests, runtime/browser review, subagent lenses if used]

## 1. Executive summary

Summarize the actual system, its current risk profile, the most consequential findings, strong areas worth preserving, and the next decision. Avoid unsupported global claims such as “production-ready.”

## 2. System map

[Diagram or layered/component description. Label observed, inferred, and documentation-only components.]

## 3. Coverage and baseline

| Area/module | Coverage | Evidence examined | Limitation / next check |
|---|---|---|---|
| [area] | REVIEWED / PARTIAL / NOT RUN / BLOCKED / N/A | [files, tests, route, UI flows] | [reason] |

## 4. Finding summary

| ID | Severity | Evidence state | Category | Finding | Impact | Roadmap item |
|---|---|---|---|---|---|---|
| SA-001 | P1 | CONFIRMED | [category] | [title] | [impact] | R-01 |

Keep unverified hypotheses in a separate subsection and do not count them as confirmed defects.

## 5. Detailed findings

Repeat this template per finding:

### SA-001 — [concise title]

- **Severity:** P0 / P1 / P2 / P3 / INFO-OPPORTUNITY
- **Evidence state:** CONFIRMED / CODE-VERIFIED / PARTIALLY VERIFIED / UNVERIFIED
- **Category and affected journey:** [category, users, module]
- **Evidence:** [`path/to/file.ext:line-line`; actual command/output; route/screen]
- **Trigger / preconditions:** [specific conditions]
- **Actual behavior:** [what current code does, with evidence]
- **Expected behavior:** [contract, requirement, invariant, or explicit assumption]
- **Impact:** [user, data, security, business, reliability, maintenance]
- **Root cause:** [demonstrated cause or clearly labeled hypothesis]
- **Counter-check performed:** [guard/test/caller/trade-off examined; why finding remains valid]
- **Recommended correction:** [smallest safe approach; do not implement]
- **Regression verification:** [specific automated/manual check and acceptance condition]
- **Dependencies / risks:** [linked findings, migration, rollback, uncertainty]

## 6. Upgrade and algorithm recommendations

| ID | Problem/evidence | Current method | Candidate | Benefits and trade-offs | How to validate | Confidence |
|---|---|---|---|---|---|---|
| U-01 | [evidence] | [current] | [candidate] | [expected and risks] | [test/benchmark] | [level] |

Explicitly state when the current method should be kept.

## 7. Verification record

| Command / scenario | Why it was selected | Result | Exit status / evidence | Limitations |
|---|---|---|---|---|
| `[exact command]` | [reason] | PASS / FAIL / PARTIAL / NOT RUN / BLOCKED | [actual output summary] | [scope] |

Separate static inspection from tests, runtime verification, visual rendering, and accessibility testing.

## 8. Residual risks and unknowns

[Important paths not inspected, runtime dependencies unavailable, assumptions that need a human decision, and what evidence would resolve them.]

## 9. Strengths and constraints to preserve

[Working architectural seams, correct safety controls, tests, useful patterns, accepted trade-offs, and project-specific rules verified in the implementation.]

---

## `remediation-roadmap.md`

# Remediation and Upgrade Roadmap

- **Baseline:** [revision]
- **Derived from:** [report path/run]
- **Planning rule:** dependencies and risk take precedence over convenience. No task is complete without its acceptance evidence.

## Prioritized task list

| ID | Priority | Finding IDs | Objective | Effort | Dependencies | Status |
|---|---|---|---|---|---|---|
| R-01 | P1 | SA-001 | [outcome] | S/M/L | [R-xx or none] | PROPOSED |

## Phase N — [objective]

### R-01 — [task title]

- **Problem / linked findings:** [IDs and evidence]
- **User/system outcome:** [observable improvement]
- **Scope:** [modules, APIs, schema, UI, worker]
- **Implementation approach:** [ordered steps]
- **Alternatives / trade-offs:** [if material]
- **Prerequisites and rollback:** [dependencies, migration order, compatibility]
- **Acceptance criteria:**
  - [ ] [testable criterion]
  - [ ] [negative or failure-path criterion]
  - [ ] [compatibility/security criterion]
  - [ ] [measured result or baseline, when relevant]
- **Verification plan:** [commands, tests, user journey, metrics]
- **Completion evidence required:** [test result, screenshot, benchmark, log, CI gate]

## Dependencies and sequencing

[Describe critical dependencies, parallelizable tasks, rollout/rollback order, and tasks intentionally deferred.]

## Deferred opportunities

[Good ideas not justified as immediate work, with evidence needed before reconsidering them.]

---

## `audit-manifest.md`

Record the exact baseline revision, mode, target paths, objective, project instructions/ADRs reviewed, modules covered, modules excluded, commands run and not run, runtime tools available/unavailable, audit state per area, report locations, and remaining tasks. Do not include secrets or sensitive user data.
