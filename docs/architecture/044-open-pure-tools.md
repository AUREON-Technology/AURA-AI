# ADR-044 — Fourteen more `tools/open` classes become pure agent tools

**Status:** Proposed (built, not proven) · **Date:** 2026-11-23 · **Related:** ADR-036 (open
analysis tools, group A), ADR-042 (lint, search, media, document tools), ADR-006 (trust channels),
ADR-014 (`workspace_local`) · **Inventory rows:** 7, 40, 41, 45, 47, 48, 49, 50, 51, 69, 73, 80, 81, 105

## Context

The tools inventory marked fourteen capabilities `Open Source` but "class only": the class existed
under `aura_core/tools/open/` and nothing registered it, so the agent could not call it. Each also
had a gap that made the bare class a weak tool:

| Row | Class | Gap the review found |
|---|---|---|
| 7 | `ArchitecturalConsistencyChecker` | the caller had to build the import map; no conventions |
| 40 | `AmbiguityAndClarificationDetector` | fixed keyword list: "faster", "improve", "UI" missed; questions not tied to a finding |
| 41 | `AcceptanceCriteriaVerifier` | nothing fed it; the kernel never sets `Goal.success_criteria` |
| 45 | `ScopeAndDiffGuard` | compared with caller-supplied paths, never with the task text |
| 47 | `SourceContentExplainer` | study guide was a template |
| 48 | `AutomatedQuizGenerator` | every question "How does X contribute to Y?" with the same options |
| 49 | `ArtifactGenerator` | `task` key rendered as "Unnamed task" |
| 50 | `VerificationEvidenceCollector` | no way to record a test log or a screenshot; state shape unclear |
| 51 | `ArtifactFeedbackIngestor` | only "remove step N" and "add ..." |
| 69 | `MobileArtifactSummarizer` | pieces (diff, tests) but no combined summary or risk flags |
| 73 | `FileTypeDetectorRouter` | read a path; a DOCX renamed `.dat` was a zip; named classes the agent cannot call |
| 80 | `StructuredDataParser` | key/type contract only, no JSON Schema, no line for a schema error |
| 81 | `MultiLanguageAstAnalyzer` | non-Python was regex-level: no methods, lines, calls |
| 105 | `WorkflowDependencyValidator` | superseded (ADR-036), reads `depends_on` as nothing, no deadlines or recovery |

## Decision

One module of fourteen tools, `aura_core/tools/open_pure.py`, with the computation the classes lack
in `aura_core/tools/open_pure_logic.py`. Same risk profile as ADR-036 group A: every tool takes
text or JSON the caller supplies and returns JSON; none reads a path, spawns a process, makes a
network call or keeps state in the process; none needs a capability or a confirmation; none is
tagged `untrusted_content`. Inputs are length- and count-capped (the cap, not the timeout, is the
CPU bound, because `asyncio.to_thread` cannot be cancelled); lists are shortened whole, never cut
mid-JSON. On by default (`AURA_OPEN_PURE_TOOLS_ENABLED`, default `true`).

| Tool | Row | What it adds over the class |
|---|---|---|
| `check_architecture` | 7 | reads Python, JS/TS and Go/Rust/Java/C#/Kotlin/Dart imports from the files you pass; cycles on resolved modules (`from . import x` included); naming, wildcard import, bare except, mutable default, long line, print findings |
| `detect_ambiguity` | 40 | per-sentence findings (unmeasured comparative, subjective quality, unquantified quantity, hedge, open list, placeholder, undefined reference, universal quantifier; English + Arabic) each with its own question; polarity and numeric-bound conflicts; multiple-choice questions for missing aspects; a number in the sentence clears a comparative |
| `verify_acceptance_criteria` | 41 | takes criteria or a Markdown spec, evidence as an object, free-text lines (`PASS:`, `[x]`), pytest output or a summary; names each unverified and contradicted criterion and the next action |
| `check_diff_scope` | 45 | task text vs diff: files the task does not account for, reformat-only noise, lock/generated files, one file far larger than the rest; plus forbidden paths, secrets, allowed paths, size |
| `explain_source` | 47 | symbols with signature, summary, in-file calls, dependency-ordered reading list, review questions; Go/Rust/TS by declaration scan; Markdown by headings |
| `generate_quiz` | 48 | questions built from facts (parameter lists, return types, docstring-to-function, which class defines a method, inheritance, imports, branch counts, cloze and true/false from prose); deterministic; separate `answer_key`; grading; coding challenge derived from a function |
| `generate_artifact` | 49 | one entry point; `task`/`title`/`description` accepted as the name; nothing written (use `write_file`) |
| `collect_verification_evidence` | 50 | collect, add a command, add a test log or an artifact (a screenshot by `content_base64`: hash, size, MIME, dimensions), verify, certificate |
| `apply_plan_feedback` | 51 | remove several, move, swap, change, insert after/before; numbers refer to the plan as sent; `needs_replan` and a `replan_instruction` for rejections and unparseable feedback |
| `summarize_for_mobile` | 69 | one summary from plan, diff, tests and last command output, plus a push line, a card and risk flags (sensitive files, migrations, deletions, failing tests) |
| `detect_file_type` | 73 | bytes in, not a path; zip internals (docx/xlsx/pptx/epub/jar/apk/wheel), magic numbers, JSON/XML/CSV/TOML/YAML/SQL/log sniffing, extension mismatch; names the *agent tool* that reads the format |
| `parse_structured_data` | 80 | JSON-Schema subset with nested types, `$ref`, `oneOf`/`anyOf`/`allOf`/`not`, pattern, format; each error carries a path and, for JSON/YAML, a line; a keyword the tool does not check is reported, not ignored |
| `analyze_source_structure` | 81 | classes, methods with parent, line range, signature, exported flag, calls, imports with lines, call graph; Python by AST, eight other languages by declaration scan with brace matching |
| `validate_workflow` | 105 | order, tiers, cycles with the path, critical path against per-task and overall deadlines, failure impact, recovery tasks that exist and can run, failure simulation |

### State

`collect_verification_evidence` and `apply_plan_feedback` look stateful and are not. The dossier
(with its digest) and the plan (a list of strings) are returned to the caller and passed back. There
is no process-global state, so nothing one user stores can be read by another;
`tests/unit/test_open_pure_tools.py` pins that with an AST check for module-level mutable state and
an IDOR-style two-user test. The digest is an unkeyed SHA-256: it detects accidental change, not
forgery.

### Grants

`engineer`: all fourteen. `coding_agent`: all but `summarize_for_mobile` (it holds no
`untrusted_content` tool, and these are not). `task_planner`: `detect_ambiguity`,
`validate_workflow`, `generate_artifact`, `apply_plan_feedback`, `verify_acceptance_criteria`,
`summarize_for_mobile`. `research_agent`: `detect_file_type`, `parse_structured_data`,
`explain_source`, `generate_quiz`, `detect_ambiguity`. Finance, health and decision personas get
none.

### What was deliberately not done

* `workflow_dependency_validator` is on the superseded list
  (`tests/architecture/test_superseded_open_tools_unused.py`) and stays unimported: the new
  `validate_workflow` is its own implementation, iterative, with deadlines and recovery.
* No path-reading variants. A file is read with `read_file`/`parse_document` and its text passed.
* No kernel change. `Goal.success_criteria` is still never populated by the kernel; the criteria
  verifier is a tool the agent calls, not a stage the kernel runs.

## Consequences

* Fourteen more tool descriptions in the planner prompt for the Engineer, which on the CPU-hosted
  model is a real cost. The tools are consolidated (one per class, `operation` enums) for that
  reason; an operator who needs the prompt smaller sets `AURA_OPEN_PURE_TOOLS_ENABLED=false`.
* Several results are heuristics and say so (`heuristic: true`): ambiguity, task relevance of a
  diff, non-Python structure. A clean result means no pattern matched.

## Not proven

Built and covered by `tests/unit/test_open_pure_tools.py` (every tool called through
`Tool.execute`, and the Engineer path through a real `Container`). Not proven: how a real model
uses fourteen more tools under a CPU-hosted planner, and how often the heuristics are right on real
tasks. No Tree-sitter is installed, so rows 81's "Tree-sitter" part is not built.
