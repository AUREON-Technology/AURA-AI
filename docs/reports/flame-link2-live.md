# Flame ↔ AURA live proof (AR-7)

Date: 2026-10-07. Deployment: AURA on Railway (`aura-ai-production-03a4`), Flame on Railway (`flame-api-production-e948`).

## Revisions
- AURA: `main` @ `7affe0cc` plus branch `fix/leak-guard-instructions-only` (deployed from the working tree; deployment `f07d23ef`).
- Flame (NASA): `65f6532` plus branch `fix/bigint-file-size` (`aae2617`, ingest fixes applied by hand to the running container).

## Objects
- Connector `flame-api` — `read_only_attested`, active, digest `sha256:ae6400a5…` (prefix only recorded).
- Twin v1 — 81 entities, digest `sha256:4228a016…`.
- Blueprint v2 — 8 tools (`bp2_api_*`), agent `bp_project`, digest `sha256:7fbfd1a0…`, approved and deployed.

## Gate
- `tool_contract`: 3/3 probed tools passed; the other 5 need an identifier and are `not_run`.
- Coverage 0.375 against a **waived** minimum: `AURA_ADAPTATION_GATE_MIN_COVERAGE=0.35` (default 0.8). This is an explicit operator decision, not a pass at the default.
- The Super Admin approved their own request (allowed in this deployment).

## Required deployment settings found during the run
- `AURA_LLM_MAX_PROMPT_TOKENS=4096` (default 400 dropped tool output from the prompt on the 8k-context hfspace provider).
- `AURA_PROVIDER_HFSPACE_TIMEOUT_S=120`, `AURA_LLM_TIMEOUT_S=150` (the 45 s / 60 s defaults cut off planning calls on the GPU Space).
- Code fixes merged from this work: output leak guard compares against the INSTRUCTIONS section only (#158); rejected tool arguments are logged and an out-of-set optional enum filter is dropped on read-only tools, planner signatures show enum values (#159); the execution note leaves composition steps to the answer once tool steps succeed (#160) and says to answer in plain text (#161).

## Root causes found on the way (all fixed)
1. Blueprint digest mismatch: OpenAPI int bounds decoded as floats.
2. Tool output dropped from the prompt (400-token default).
3. Leak guard blocked answers that quoted a tool result.
4. Planner copied "evidence" into Flame search's optional `type` filter (allowed: document|experiment); validation rejected it on every replan.
5. Execution note said the model had "NOT seen" results of non-tool plan steps even after the search succeeded, so the model refused to answer.

## Live answers (browser session of the Super Admin, `/console/api/v3/chat`, agent `bp_project`, planning on; after #161 was deployed)
1. "How many experiments and documents does Flame have?" -> "840 experiments and 24 documents", tool `bp2_api_get_dashboard_counts`. Matches Flame's database.
2. "Find heptane in the Flame data and tell me the evidence_id of a matching result." -> `6PHB5MGPMMB6FN02EGDXZGPHW9`. Flame's own `GET /api/search?q=heptane` returns this ID as rank 1 ("PSI-69 Presentations Cool Flames in Space, a Hot Prospect on Earth!").
3. "Search Flame evidence for heptane and quote one result with its evidence_id." -> quoted evidence `4A02J2MN7VJ4VB8E7WHSV0045D`, "PSI-69 Science Documents FLEX Test Plan". The ID is real (rank 1 of Flame's search for "Flame"), but the planner searched for "Flame" rather than "heptane", and the model said honestly that the item does not mention heptane. Grounded, not fabricated, but not the best answer.

## Caveats
- Prompt 3 shows the limit of the Qwen2.5-7B planner on loosely worded requests (it picks its own search query). Not a plumbing defect; a larger model or a query-rewriting step would address it.
- The `tool_contract` gate passed 3/3 probed tools; 5 tools need an identifier and are `not_run`. Coverage 0.375 passed only because `AURA_ADAPTATION_GATE_MIN_COVERAGE` was lowered to 0.35 (default 0.8). This is a waiver, not a pass at the default.
- The three answers above were produced by running a script in the operator's own browser (the embedded browser could not complete the Google sign-in popup); the outputs were pasted back, not captured by tooling.
- `docs/production/gap_register.yaml` status is unchanged by this report.
- Dataset files are not in the Flame image; DB rows persist, source files do not survive a redeploy.
- Secrets that appeared in session output (Flame read token, `svc-flame` key, HF Space key, a pasted GitHub token) still need rotating/revoking.