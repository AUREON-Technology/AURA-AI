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
- Output leak guard now compares against the INSTRUCTIONS section only (`AssembledContext.instructions_text`); previously quoting a tool result tripped `system_prompt_leak`.

## Live answers (via `/api/v3/chat`, agent `bp_project`, planning on)
1. "How many experiments and documents does Flame have?" → "840 experiments and 24 documents", tool `bp2_api_get_dashboard_counts`, `degraded: false`. Matches Flame's database.
2. "Use the search tool with query heptane…" → title "PSI-69 Presentations Cool Flames in Space, a Hot Prospect on Earth!", `evidence_id` `6PHB5MGPMMB6FN02EGDXZGPHW9`, tool `bp2_api_search`. Both values were confirmed present in Flame's own `GET /api/search?q=heptane` response.

## Caveats
- A differently worded heptane prompt ("Search Flame evidence … quote one result") failed twice with 503 `all_providers_failed` at the planning step (~45 s); the provider itself was healthy. Not yet root-caused.
- Coverage of the gate was waived; `docs/production/gap_register.yaml` status is unchanged by this report.
- Dataset files are not in the Flame image; DB rows persist, source files do not survive a redeploy.
