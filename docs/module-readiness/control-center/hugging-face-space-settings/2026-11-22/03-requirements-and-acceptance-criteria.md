# 03 Requirements and acceptance criteria

Sources: D1, D2 (user), or R# (recommendation, awaiting approval). Status as of 2026-11-22.

| ID | Requirement | Acceptance criteria | Source | Status |
|---|---|---|---|---|
| BR-001 | A paused Space MUST be a valid, intentional state, not shown as a failure. | Console shows PAUSED without an error banner; no alert or readiness text calls it broken. | D1 | PARTIAL: stage badge is neutral; `/health/ready` still lists `hfspace` unhealthy |
| FR-001 | A Hub 402 MUST yield a distinct reason (`hf_payment_required`) whose message says billing or credits are the cause and where to fix it. 429 MUST say rate-limited, retry later. Vendor text MUST NOT pass through. | Unit tests: 402, 429, other 4xx, 5xx, network each give the expected `reason`; no token or Hub text in `str(error)`. | R1 | GAP |
| FR-002 | The console MUST show that message with the next step, in English and Arabic. | e2e: a mock refusal with each reason renders the localized line in both languages. | R1 | GAP |
| FR-003 | A refused or failed Space write MUST leave an audit record, because the dialog promises one. | A failed Resume produces a journal or audit row with action and reason; no secret in it. | R1 | UNVERIFIABLE (admin layer not read) |
| FR-004 | Hardware choices SHOULD show an estimated price and a billing caveat. | Picker shows a cost hint for each flavour or the warning names billing explicitly. | R1 | PARTIAL: warning exists, no price |
| FR-005 | The deploy workflow MUST NOT request paid hardware while GPU policy is off. | With the gating variable set to off, the step is skipped and `test_inference_contract.py` pins both states. | D1, R2 | GAP |
| SEC-001 | D2, once implemented, MUST use only the documented ceiling setting; the slot MUST NOT allow prompt training; `llm.third_party_generation` MUST keep recording; image turns MUST still be refused. | A sensitive test turn is answered by `hosted1` with an audit row classified `sensitive`; an image turn is refused; boot warning present. | D2 | NOT IMPLEMENTED |
| SEC-002 | The vendor response MUST NOT be echoed (existing rule). | Existing test pattern at `test_control_space.py:281-294` extended to 402/429. | existing | PASS for 401 only |
| OPS-001 | A deliberately paused Space SHOULD NOT hold readiness red or noisy. | Probe mode passive or slot removed; `/health/ready` shows no spurious unhealthy provider. | R3 | GAP |
| DOC-001 | Docs MUST state one current GPU policy. | `inference-plane.md` §3/§9, status docs and the verifier note agree with D1. | D1 | GAP |
| DATA-001 | D2 MUST be recorded as an ADR-030 amendment and reflected in every "stays self-hosted" claim. | ADR amendment exists; grep finds no stale claim. | D2 | GAP |

## Scenarios

1. **Operator clicks Resume, no billing:** dialog shows "Hugging Face needs billing before this GPU can
   start", names the hardware requested, the Space stays PAUSED, an audit row records the refusal.
2. **Operator leaves the Space paused:** nothing alarms; text turns use `hosted1`; image turns say a GPU
   is needed.
3. **Someone pushes to `deploy/spaces/inference-vllm/`:** the workflow does not start paid hardware while
   the policy is off.
4. **Sensitive text turn after D2 is implemented:** answered by `hosted1`, audited as sensitive.
