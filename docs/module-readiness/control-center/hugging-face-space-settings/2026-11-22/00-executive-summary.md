# Hugging Face Space settings: executive summary

Module: Control center (ADR-037) / Sub-module: GPU Space tab / Feature: Hugging Face Space
configuration (`/settings/space` in `aura_admin`, `/api/v3/admin/control/space/*`, `HfSpaceHost`).
Mode: DISCOVER (read-only; no application code, config or test was changed or run).
Date: 2026-11-22. Branch inspected: `anas/knowledge-wp4-fetchers`. Live gateway release: `93635069680e`.

## Outcome

**Not production-ready. Decision: NO-SHIP for the Space tab as an operator workflow until the P1
findings below are fixed.** Pause, variables, secrets and logs are UNVERIFIABLE live: no Hub call
had been made from the gateway before this one (`docs/status/BACKEND_STATUS.md:289`).

## What the screenshot is

Clicking **Resume** on `aura-ai-backend` returned "Hugging Face refused the request (HTTP 402)".

- 402 is "payment required". Resume calls `restart_space` on a PAUSED Space whose requested
  hardware is **`a10g-small`** (public Hub record: `stage PAUSED`, `hardware.current null`,
  `requested a10g-small`, `gcTimeout 1800`). A paid GPU cannot start on an account with no billing.
- **It is not a code defect.** The cause is **inferred, not confirmed**: the adapter discards the
  Hub's response body (`hf_space.py:83-86`), so "no payment method or credits" is the most likely
  reading of a 402, not something we observed.
- **The defect is that the console cannot say so.** Every 4xx other than 401/403/404 becomes
  "refused the request (HTTP N)": no cause, no next step, English only on the Arabic UI.

## Decisions taken (user-confirmed, see 02)

- **D1: No GPU for now.** The Space stays paused. Groq `hosted1` (and `claude1`) are the live providers.
- **D2: Groq may read sensitive content.** Reverses ADR-030 §8's rule that this content stays on
  self-hosted compute. Conditional on the sign-offs in 02. Images stay refused either way.

## Headline findings (full register in 04)

| ID | Sev | Finding |
|---|---|---|
| F1 | P1 | 402/429 are opaque: no cause, no next step |
| F2 | P1 | `inference-plane-deploy.yml` forces `l4x1` + never-sleep on any push to `deploy/spaces/inference-vllm/**`; contradicts D1 and would silently start a ~$584/month GPU once billing exists |
| F3 | P2 | Four documents/states disagree on what the Space should be (see 01) |
| F4 | P2 | Refused attempts are not journaled, although the dialog says "This writes an audit event" (to verify) |
| F5 | P2 | Arabic UI shows the English vendor text; no reason-keyed message |
| F6 | P2 | D2 as an operator setting is install-wide, has no per-user opt-in, and needs a privacy sign-off |
| F7 | P3 | Hardware picker offers 17 flavours (up to 8 GPUs) with no price or billing check |
| F8 | P3 | UNVERIFIABLE: stored HF token's permissions, the real 402 body, Space wake behaviour |

## Reports in this folder

`01-as-is-system-map.md` · `02-business-interview-and-decisions.md` ·
`03-requirements-and-acceptance-criteria.md` · `04-gap-and-risk-register.md` ·
`05-implementation-plan.md`

Not run this session: pytest, Playwright e2e, any live Hub call. Nothing here is proven.
