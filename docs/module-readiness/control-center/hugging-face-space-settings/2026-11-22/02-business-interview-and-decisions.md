# 02 Business interview and decisions

## Decision log

### D1: No GPU for now (USER-CONFIRMED, 2026-11-22)
- Question: which GPU policy (L4 always on / A10G sleeping / none)?
- Answer: **none**. The Space stays paused; `hosted1` (Groq) and `claude1` are the live providers.
- Supersedes the 2026-11-17 and 2026-11-18 owner decisions in `inference-plane.md` §3 and §9.
- Affects: BR-001, FR-005, DOC-001, the verifier (`verify_gpu_source.py` checks 2-3 and 7 cannot pass).
- Not a decision to delete the Space or the HF credential.

### D2: Groq may receive sensitive content (USER-CONFIRMED, 2026-11-22, conditional)
- Question: how should memory, document, tool, Engineer and image turns behave with no GPU?
- Answer: "Let Groq read them", the option that was labelled as needing explicit privacy sign-off.
- **This reverses ADR-030 §8 for this install.** Findings that bound it (all verified in code/docs):
  - Mechanism is an operator setting, not code: `AURA_PROVIDER_HOSTED1_MAX_DATA_CLASSIFICATION=sensitive`.
    Boot logs a warning every time, and **refuses to boot** if the slot also has
    `ALLOW_PROMPT_TRAINING` (`settings.py:699-703`).
  - It is **install-wide, with no per-user opt-in** (ADR-030 §8 "Known limits").
  - "Sensitive" includes anything that touched memory, a retrieved document, a tool result, an
    Engineer turn, and every background caller without a scope (summariser, extraction, insights).
  - **Images are not unlocked**: `router.py:727` never sends a user's photos to a third party, and
    gpt-oss-120b has no vision. Image turns still fail until a GPU exists.
  - Audit stays on: `llm.third_party_generation` records provider, model, classification, counts, never content.
- Conditions before it is switched on (see WP-5): O1, O2 below, an ADR-030 amendment, and updating
  every statement that sensitive content stays on self-hosted models.
- Status: **decided, not implemented.**

## Recommendations (not decisions)

- R1: Map 402 to a distinct reason that names Hugging Face billing (FR-001). Low risk, no business choice.
- R2: Gate the deploy workflow's GPU step so it cannot request paid hardware while GPU policy is off (FR-005).
- R3: Set `AURA_PROVIDER_HFSPACE_HEALTH_PROBE=passive` (or drop `hfspace` from `AURA_LLM_PROVIDERS`) so a
  deliberately paused Space does not read as a permanent red provider.

## Open decisions

- O1: Does this install hold anyone's data other than the owner's (customer workspaces, ADR-034)? D2 sends it to Groq.
- O2: Groq's retention and training terms for the API key in use. Owner to check; not a call the agent makes.
- O3: R3, probe passive vs remove the slot.
- O4: Mechanism for R2 (a repository variable such as `HF_SPACE_GPU=off`, or removing the step).

## ADR offer

D1 and D2 are hard to reverse in effect, surprising without context and real trade-offs. An ADR
amendment for ADR-030 §8 (and a note on §9) in `docs/decisions/` or `docs/architecture/` is
recommended. **Not created**; it needs the owner's confirmation.
