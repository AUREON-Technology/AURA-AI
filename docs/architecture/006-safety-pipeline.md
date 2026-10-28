# ADR-006 — Retrieved content is data, never instruction; two-sided guard pipeline

**Status:** Accepted · **Date:** 2026-08-02

## Context

SEC-02: `services/agent.py:214-221` concatenates retrieved document chunks and stored memories
directly into the **system instruction**. Anyone who can get text into the knowledge base — by
uploading a PDF, by having the research agent ingest a Drive file, or by stating a "fact" that memory
extraction saves — writes into the model's instruction channel, while Google Drive search and
ingestion tools are live with the caller's OAuth token.

There are no input guards and no output guards of any kind.

## Decision

### 1. Channel separation

The prompt has three channels with different trust levels, and they are never concatenated
ambiguously:

| Channel | Trust | Content |
| --- | --- | --- |
| **System** | trusted | operator-authored, versioned prompt templates only |
| **Context** | **untrusted data** | retrieved chunks, memories, tool results — each in a fenced block tagged with its provenance |
| **User** | semi-trusted | the user's message |

The system channel states explicitly that content inside context fences is data to be *used*, never
instructions to be *followed*, and that any instruction found inside a fence must be reported rather
than obeyed. Fences use a per-request random nonce so injected text cannot close the fence and escape
into the system channel.

Channel separation is a mitigation, not a proof. It is layered with the guards below.

### 2. Input guard pipeline (`safety/pipeline.py`)

Runs on the user message *and separately* on every retrieved chunk and every tool result:

| Guard | Detects | Default action |
| --- | --- | --- |
| `SizeGuard` | oversized/pathological input | reject |
| `EncodingGuard` | zero-width chars, bidi overrides, homoglyph obfuscation, base64 instruction payloads | sanitise + flag |
| `PromptInjectionGuard` | instruction-shaped content in a data channel ("ignore previous", "you are now", "system:", tool-invocation phrasing) | quarantine the chunk, continue without it |
| `JailbreakGuard` | known jailbreak framings targeting the system prompt | flag, escalate |
| `PIIGuard` | emails, phones, national ids, cards, IBANs, keys | redact or tag by policy |
| `SecretGuard` | API keys, bearer tokens, private-key headers | reject and audit |

Each guard returns a `GuardVerdict(action, confidence, reason, spans)`. The pipeline's aggregate
action is the strictest verdict. Every non-`ALLOW` verdict emits a metric and an audit event.

### 3. Output guard pipeline

| Guard | Detects |
| --- | --- |
| `SystemPromptLeakGuard` | verbatim spans of the system prompt in the output |
| `SecretGuard` | credentials that entered via context leaving via output |
| `PIIGuard` | PII in output not present in the user's own input |
| `SchemaGuard` | structured outputs that don't validate against the declared schema |
| `GroundednessGuard` | claims unsupported by any retrieved citation (advisory: emits `aura_ungrounded_claims`, does not block) |

### 4. Tool authorisation interacts with safety

A turn whose context contains a **quarantined** chunk marks its `ExecutionContext` as
`tainted`. Tools declared `side_effecting=True` refuse to run in a tainted context without explicit
user confirmation (`tools/policy.py`). This directly closes the SEC-02 attack: a poisoned document
cannot cause a Drive ingestion.

A confirmation is bound to the exact call the user saw (audit F-04, CB-04;
`execution/confirmations.py`). When a turn stops on a held call, the server stores that turn -- its
plan as executed so far, its taint, owner, session and message -- and issues one opaque
`approval_id` per held call, recorded with a digest of the tool name and its resolved arguments.
Approving sends those ids back (`approval_ids`); the server consumes the stored turn once, executes
**that plan** rather than re-planning, and dispatches a call as confirmed only if its tool and
arguments match the approved digest, once. Tool names and positional call ids confirm nothing, an
id from another user, session or turn is refused (409 `confirmation_invalid`), and steps that
already ran are not run again.

### 5. Fail-closed by category

Guards that detect **exfiltration** (secrets, system-prompt leakage) fail closed — the response is
blocked. Guards that detect **quality** issues (groundedness) fail open with a metric. This split is
deliberate: a false positive that blocks an answer is cheap; a false negative that leaks a
credential is not.

## Alternatives rejected

| Alternative | Why rejected |
| --- | --- |
| Ask the LLM to police itself in the prompt | The attacker also controls prompt-visible text. Instruction-only defence is not a control. |
| A single "moderation API" call | Vendor moderation targets harmful content, not indirect prompt injection or exfiltration, and adds a network hop to every turn. Used as an optional adapter, not the mechanism. |
| Block all retrieved content containing imperative language | Unusable — documentation is full of imperatives. Hence quarantine-and-continue plus tool-taint rather than hard rejection. |
| Classifier-only detection | Needs training data the project doesn't have yet. Rules ship now; `safety/classifiers.py` provides the seam to add a trained classifier, and the guard results are logged specifically to build that dataset. |

## Consequences

**Positive** — closes the critical injection path; makes exfiltration attempts visible and auditable;
gives per-guard metrics that turn safety into something measurable rather than asserted.

**Negative** — false positives will quarantine legitimate chunks (measured by
`aura_guard_verdicts_total{guard,action}`, tuned per deployment via thresholds), and guards add
~3–10 ms per turn.

**Impact:** security is the point; maintainability is fine because each guard is ~40 lines and
independently tested; cost is negligible; the groundedness guard doubles as the hallucination metric
required by Phase 10.
