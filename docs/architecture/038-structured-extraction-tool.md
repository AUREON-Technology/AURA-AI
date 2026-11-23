# ADR-038 — `extract_structured`: typed field extraction through Cloudflare's `clairic`, behind a narrow closed-model exception

**Status:** Proposed (built, not proven) · **Date:** 2026-11-22 · **Related:** ADR-006 (trust
channels), ADR-030 (hosted inference and data classification), ADR-031 (images), ADR-033 (the one
closed-model exception this one is modelled on), ADR-036 (`tools/open` exposure) · **Gap:**
GAP-068

## Context

The owner asked for Cloudflare's `@cf/cloudflare/clairic` to be used in the system. It is not a
chat model. It reads a body of text (and, in its own API, images and PDFs) and fills in typed
fields the caller declares -- `text`, `number`, `date`, `boolean`, `enum`, lists, objects -- and
returns each answer with a confidence and, where the value is printed in the input, a character
span. It is called at `POST /client/v4/accounts/<id>/ai/run/@cf/cloudflare/clairic`, not at the
OpenAI-compatible `/ai/v1` path, and wraps its answer in `{"result": …}`. So it cannot be a
`ProviderConfig` slot and does not belong in `ProviderRouter`.

What Cloudflare's own pages say about it (read 2026-11-22), and what they do not:

- It is labelled a **preview** from the Workers AI team, provided "AS IS" and "AS AVAILABLE", and
  Cloudflare may stop providing it.
- **Whether its weights are published is not stated.** No open-source licence is named and there
  is no model-card link. `is_open_weight_model("@cf/cloudflare/clairic")` is therefore `False`:
  the ID matches none of `OPEN_WEIGHT_MODEL_PATTERNS`. ADR-030's rule 2 is "open-weight only,
  enforced", and its module says adding a family takes a decision, not an edit.
- Workers AI's data-usage page (dated 2026-10-16) says customer content is not used to train
  models and is not retained after the request. That page is about Workers AI generally. It does
  not say it covers Cloudflare's own model, and the model page only tags it "Zero data retention".
- Context window 65,536 tokens; up to 4 images and 4 PDFs. **Language support is not stated and
  Arabic is never mentioned.**
- Price: $0.10 per M input tokens until 2026-12-01, $0.24 after; output is not charged. Free-plan
  eligibility and per-model rate limits are not stated.

A probe on 2026-11-22 (one request, five fields) returned HTTP 200. Four fields were right.
`due_date` was wrong (`2026-10-12` for an issue date of 2026-09-02 and net 30, which is
`2026-10-02`) and carried a confidence of 0.096. One sample proves nothing about accuracy, but it
shows why a value must never be used without its confidence.

**Owner decision D-2 (2026-11-22):** treat `clairic` as a closed model under a narrow exception,
the way ADR-033 treats Claude, rather than widen ADR-030's open-weight list on no evidence. The
owner chose a user-callable tool over replacing the knowledge-graph extractor or the connector
normaliser.

## Decision

### 1. One path, one module, one host

- **A new flag, `AURA_ALLOW_EXTRACTION_INFERENCE`.** The tool is not registered and the adapter is
  not built unless it is `true`. It covers this capability only. `AURA_ALLOW_HOSTED_INFERENCE` and
  `AURA_ALLOW_CLAUDE_INFERENCE` do not imply it, and it implies neither.
- **The endpoint cannot be redirected.** There is no `base_url` setting. The adapter builds the
  URL itself from `AURA_EXTRACT_CLOUDFLARE_ACCOUNT_ID`, and the model is fixed to
  `@cf/cloudflare/clairic`.
- **The key comes only from a named variable.** `AURA_EXTRACT_API_KEY_ENV` names the environment
  variable that holds the token (the same convention as `AURA_PROVIDER_<NAME>_API_KEY_ENV`). An
  empty key refuses to boot with the flag on. The key never appears in a log line, an error
  message, a tool result or the audit record.
- **Exactly one module may call `/ai/run`.** A fitness test pins the adapter as the sole file that
  names that path, the way `SANCTIONED_SDK_IMPORTERS` pins the Claude SDK, so widening the
  exception shows up as a diff to a named test.
- **`OPEN_WEIGHT_MODEL_PATTERNS` is not changed.** The OpenAI-compatible Workers AI slot
  (`hosted3`) stays open-weight-only. This ADR does not admit `clairic` to ADR-030's category; it
  makes a separate, smaller exception beside it.

### 2. A tool, not a provider

- `extract_structured` is a read-only tool in `aura_core/tools/`. It takes `text`, a `fields`
  map and nothing else, and returns JSON. It runs nothing, reads no path and writes nothing.
- It depends on a `StructuredExtractor` port (domain and ports only). The Cloudflare adapter and a
  deterministic in-memory adapter both implement it, so the default `pytest` run needs no network.
- The router, its pools and its failover are untouched.

### 3. The data ceiling holds

Document text leaves our infrastructure when this runs, so ADR-030's classification applies.

- The tool reads the current data scope (`llm/sensitivity.py`) and **refuses unless it is
  `internal` or lower**. An unscoped call counts as `sensitive`, as every unclassified generation
  does, so a background caller that did not opt in is refused.
- The refusal is a clean tool error (`data_ceiling`), never a silent local fallback: there is no
  local equivalent to fall back to.
- Every call writes an audit record (`extraction.third_party_call`) with the user id, the field
  count, the input size in characters, the outcome and how many answers came back in each status.
  It never stores the text or the answers. (It records the user, not a tenant: `ToolContext`
  carries none.)
- v1 sends **text only**. Images and PDFs are out of scope until ADR-031's image handling is
  applied to this path.

### 4. A value is only as good as its confidence

- A returned value whose confidence is below `AURA_EXTRACT_MIN_CONFIDENCE` (default `0.5`) is
  returned as `null`, with the raw guess and `low_confidence: true` beside it. An answer the
  service marks `valid: false`, or a response marked `truncated`, is `null` with the reason.
- The result keeps the span when one exists, so a caller can check a value against the source.
- The output is tagged `untrusted_content`: it is derived from text an attacker may have
  written, so the turn is tainted the same way `fetch_url` taints it (ADR-006).

### 5. Bounded, because the model is a preview on a shared budget

- Inputs are capped (`AURA_EXTRACT_MAX_CHARS`, default 20,000; at most 20 fields; supported field
  types only: `text`, `number`, `date`, `boolean`, `enum`, and lists of `text`). `estimate`,
  `forecast` and `compute` are not exposed.
- The request schema alone cost 1,809 of 2,541 input tokens in the probe, so field specs are
  validated and trimmed before sending, and a long `instructions` string is refused.
- A per-process daily call cap (`AURA_EXTRACT_DAILY_CALL_CAP`) protects the Workers AI daily
  allowance that the `hosted3` chat slot also draws on. It is per process, so the effective cap
  is multiplied by the number of workers; that is recorded as a known limit, not hidden.
- A 429 or 5xx opens a cooldown; a timeout is bounded (`AURA_EXTRACT_TIMEOUT_S`). Every failure
  is a tool error the planner can see, never an exception that fails the turn.
- Because the model can be withdrawn, the tool is **off by default** and nothing else depends on
  it. Removing it later removes one tool.

### 6. Out of scope for v1

Replacing `LLMEntityExtractor` (the nightly refresh would spend the shared daily allowance in
minutes), connector record normalisation, images and PDFs, any Arabic claim, and any use of
`estimate` or `forecast`.

## Consequences

- **Text leaves our infrastructure when this is enabled.** Only text the user supplied in the turn
  can be sent, because any tool output, retrieved document or workspace content already raises
  the scope above `internal`. A plan that ran another tool first cannot call this one. That is
  the intended ceiling, and it limits usefulness: extraction from stored documents needs a
  separate decision.
- **The self-hosting fitness tests now encode a second exception**, narrower than ADR-033's. They
  still catch any other vendor, any second call site and any redirect.
- **A preview dependency.** It can change or disappear without notice. The tool fails closed and
  the flag is the rollback.
- **The shared allowance can be starved.** The cap bounds this per process only.
- **Accuracy is unmeasured.** One sample, one wrong date. No accuracy or Arabic claim is made.

## Built

- `domain/extraction.py` and `ports/extraction.py`: the field types, the request and result, the
  confidence rule (`classify_answer`) and the `StructuredExtractor` port; two error classes
  (`ExtractionError`, `ExtractionRefusedError`).
- `adapters/cloudflare_clairic.py`, the one module that names `/ai/run`, and
  `adapters/memory_extractor.py`, a deterministic reader that applies the same rule.
- `config/external_inference.py`: the fixed host and model, and the account-id check. The check uses
  `fullmatch`; a first version used `^...$` and let `<id>\n` through into the URL path.
- `config/settings.py::ExtractionSettings`, validated in `AuraSettings.validate()`.
- `tools/extract.py`, registered by the container only when the adapter exists. `/health/ready`
  reports an informational `extraction` block (enabled, model, cooling down) that never gates
  readiness and carries no secret.

## Proof

Tested with no network (2026-11-23, on Python 3.14, not the locked 3.11): 263 unit tests and 6
architecture pins. The Cloudflare adapter runs over an in-process transport replaying the real
response from the 2026-11-22 probe; the tool runs over the in-memory extractor; the container
wiring runs through the real tool registry, data scope and audit sink; and tests assert that no
error, audit record, health payload, settings dump or repr contains the credential. An opt-in live
check (`tests/load/test_extraction_live.py`, `-m load`) measures per-field accuracy in English and
Arabic and asserts that no English answer is confidently wrong.

**Not proven, and what proves it:** one `extract_structured` call from a deployed gateway with
the audit record present; one refusal at `sensitive` scope; a measured field-accuracy report on a
small labelled set in English and in Arabic (`docs/reports/`); and a recorded answer to the open
items below.

## Open items

- **O-1.** The Cloudflare token used for the 2026-11-22 probe was pasted into a chat transcript.
  Rotate it (the console's Keys tab can create a replacement) and use a dedicated token for this
  capability.
- **O-2.** Whether `clairic`'s weights are published. If Cloudflare states they are, ADR-030's
  list could admit the family instead, and this exception could be retired.
- **O-3.** Whether the free plan may call it, and its per-model rate limits.
- **O-4.** Whether the Workers AI no-training and no-retention terms are confirmed to cover
  Cloudflare's own model.
- **O-5.** Not plumbed on the VPS compose stack. It needs a new docker secret and parity across
  `docker-compose.prod.yml`, `docker-compose.dev.yml` and `bootstrap-secrets.sh`. It is left until
  the owner wants it there: the Railway target takes plain variables, and without the plumbing a
  flag set in a VPS `.env` is not forwarded, so extraction stays off.
- **O-6.** The minimum Cloudflare token permissions for `/ai/run` are unverified. The token used for
  the 2026-11-22 probe had broad access; a dedicated, narrower one should be created and checked with
  the live test.
