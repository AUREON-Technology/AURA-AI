# 04 Gap and risk register

Severity: P0 critical, P1 high, P2 medium, P3 low. "Verified" means read in code or observed live.

## P1

**F1: Hub 402 and 429 are opaque** (FR-001, FR-002). Verified.
- Evidence: `hf_space.py:83-86` maps every other 4xx to "refused the request (HTTP N)"; the only
  adapter test is the 401 case (`test_control_space.py:281`).
- Impact: the operator saw a dead end (the screenshot) and cannot tell billing from a bad request.
- Fix: distinct reasons for 402 and 429, no vendor text, tests per status.
- Verify: unit tests, then e2e with a mock refusal.

**F2: The deploy workflow contradicts D1** (FR-005). Verified in the workflow file; trigger behaviour read, not run.
- Evidence: `inference-plane-deploy.yml:149-162` calls `request_space_hardware(..., l4x1, sleep_time=-1)`
  on every push to `main` that touches `deploy/spaces/inference-vllm/**`.
- Impact: today that call would fail on billing; once billing exists it silently starts about $584/month.
- Fix: gate on a variable, update `test_inference_contract.py`. Needs O4.
- Verify: contract test; a dry run on a PR.

## P2

**F3: Four sources disagree on the intended Space state** (DOC-001). Verified (table in 01).
Fix: one policy in the docs per D1, ADR amendment.

**F4: Refused attempts are not journaled** (FR-003). Partly verified.
- Evidence: `space.py:224-229` journals after success only; the dialog text says it writes an audit
  event. Whether `api/admin.py` or `ControlService.check_write_cap` records a failure is **not read**.
- Verify: read the admin layer, then a unit test of a refused restart.

**F5: Arabic shows English vendor text** (FR-002). Verified.
- Evidence: `errors.ts:40-45` maps non-English errors by error *kind* and appends the raw English
  message in parentheses; there is no reason-keyed message.

**F6: D2 is broad and install-wide** (SEC-001, DATA-001). Verified.
- Evidence: ADR-030 §8 known limits; `settings.py:699-703`; scope in 02.
- Risk: every sensitive generation goes to a third party; images are still refused.
- Mitigations: audit stays on, boot warning, O1/O2 answered, ADR amendment, privacy text.

**F9: `hfspace` reads as a permanent red provider** (OPS-001). Verified live (`HTTP 503`). Cosmetic for
gateway readiness (`healthy: true`) but noisy. Fix: R3.

## P3

**F7: Hardware picker is a flat list of 17 flavours** (FR-004). Verified (`domain/control.py:63-67`):
includes `a100x8`, with one generic cost warning.

**F8: UNVERIFIABLE items.** The stored HF token's scope (write needed for pause, restart, hardware);
the real 402 body; whether `SLEEPING` (a Hub stage) is handled sensibly (it falls to PENDING in
`space_health_of`); Pause, variables, secrets and logs against the live Hub.

## Not investigated

Dockerfile and vLLM tuning; Railway, Keys, Vector and Monitoring tabs; `claude1`'s ceiling; the
knowledge-index mismatch seen in `/health/ready`; `api/admin.py`.
