# 05 Implementation plan

Not approved and not started. Sizes are estimates until the files are opened for editing.

## Blockers (P1)

**WP-1: classify 402 and 429 in the adapter** (F1, FR-001, SEC-002). First, others depend on it.
- Files: `aura_backend/aura_core/adapters/hf_space.py`, `aura_backend/tests/unit/test_control_space.py`.
- Add `hf_payment_required` and `hf_rate_limited` reasons beside the existing ones; keep vendor text out.
- Done when: unit tests per status pass; the full `pytest` and legacy `unittest` still pass.

**WP-2: show the reason in the console, en and ar** (F1, F5, FR-002). After WP-1.
- Files: `aura_admin/src/lib/i18n/errors.ts`, `en.ts`, `ar.ts`, `tests/e2e/mock-gateway.mjs`,
  `tests/e2e/control-center.spec.ts`.
- Done when: both languages render the new line in a Playwright test; `npm run lint` clean.

**WP-3: make the deploy workflow respect GPU-off** (F2, FR-005). Independent. Needs O4.
- Files: `.github/workflows/inference-plane-deploy.yml`, `tests/deployment/test_inference_contract.py`,
  `docs/deployment/inference-plane.md`.
- Owner step: set the chosen repository variable.

## Before general availability (P2)

**WP-4: audit refused attempts** (F4). Read `api/admin.py` first; the fix may already exist.
**WP-5: D2 rollout** (F6, SEC-001, DATA-001). **Gated on O1, O2 and an ADR amendment.**
  Owner steps, in order: answer O1/O2; approve the ADR; set `AURA_PROVIDER_HOSTED1_MAX_DATA_CLASSIFICATION=sensitive`
  on Railway (not in code); confirm the boot warning; run one sensitive turn and read its audit row;
  confirm an image turn still refuses.
**WP-6: reconcile docs** (F3, DOC-001): `inference-plane.md` §3/§9, `BACKEND_STATUS.md`, the verifier's header note.
**WP-7: readiness noise** (F9, O3): probe passive or remove `hfspace`.

## After launch (P3)

**WP-8:** price hints in the hardware picker (F7). **WP-9:** capture the real 402 body once and the HF
token's scope, as evidence for F8.

## Sequencing

WP-1 -> WP-2. WP-3 and WP-6 can run beside them. WP-5 waits for sign-off. WP-4 is a read first.

## Verification plan (nothing run yet)

`pytest tests/unit/test_control_space.py`, `pytest tests/deployment/test_inference_contract.py`, full
`pytest`, legacy `unittest`, `npm run lint`, `npm run test:e2e`. A live Hub call is the only thing that
moves anything from "tested locally" to "proven", and needs billing enabled or a deliberate 402 capture.

## Owner steps, now

Nothing is needed to "fix" the 402: leave the Space paused (D1). If a GPU is wanted later: enable
billing on the Hugging Face account that owns the Space, then Resume.
