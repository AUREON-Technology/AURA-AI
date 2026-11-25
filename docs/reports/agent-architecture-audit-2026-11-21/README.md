# Agent architecture audit, 2026-11-21

ECC `agent-architecture-audit` workflow (12-layer stack) over the backend agent path, `main` at
`032e47f`. It builds on three audits already on `main` and does not repeat their fixed items:

- `docs/reports/AGENTS_AUDIT_2026-11-16.md` (personas, F1–F13)
- `docs/reports/audit-remediation-2026-11-18.md` (F1–F58, INT-101–INT-130)
- `docs/reports/audit/full-system-audit/` (2026-10-31)

Machine-readable copy: [`findings.json`](findings.json) (`ecc.agent-architecture-audit.report.v1`).

**How it was checked.** Code was read on `main`, and every evidence line below was re-checked at
`032e47f`. The live Railway variables were read, but only a whitelist of non-secret tuning keys:
`AURA_LLM_MAX_PROMPT_TOKENS=4096`, `AURA_LLM_PROVIDERS=hosted1,hfspace,claude1`, and no
`AURA_MEMORY_*` or context overrides. No test was run and nothing was probed live. Every finding
is *reasoned from code*, at the confidence stated. Three parallel reviewers were started, for
memory, tools and transport, but all three hit the usage limit before reporting. Their layers
were covered here directly, except as listed under "Not covered".

**Correction to an earlier draft.** An earlier partial pass said the semantic answer cache is
"not keyed on the turn's retrieved documents or memory". That is no longer true. F13 (11-18)
fixed it: the semantic tier is keyed on a digest of everything except the last user message, and
invalidation rotates a per-scope token (`aura_core/llm/cache.py:196-231`, `:406-419`). It is not
a finding.

## Remediation status (2026-11-22)

All twelve findings now have a code change. Every row below is **built and tested locally, not
proven live**: CI cannot run (GitHub Actions billing block), and nothing here has been checked on
Railway or the VPS.

| Finding | Status | Where |
|---|---|---|
| A1 | Fixed | `kernel/claims.py`. An answer claiming a completed action, on a turn where no tool succeeded and nothing awaits confirmation, gets a fixed correction (a `revision` frame on the stream). The corrected text is what history keeps, and the turn is degraded with `unbacked_action_claim`. |
| A2 | Fixed | `tools/personal_os.py`: Arabic intents, `create_*` equivalents, and record-read intents (`list_*`). `planning/engine.py`: a short agreement to an offered action plans, and the planner gets the offer as its referent. |
| A3 | Fixed earlier | KAN-102 (pinned dropped-tool note, `tool_output_dropped`). |
| B1 | Fixed earlier | KAN-100. |
| B2 | Fixed earlier | KAN-101 (summary on DATA, fallback transcript marked). |
| B3 | Fixed | The kernel replaces `request.message` with the guarded text right after the input guard, in both `run` and `stream`. |
| B4 | Fixed | `memory/engine.py`: a near-duplicate in different words replaces the old record, under its own confidence and recording `replaces`, but only when the new write is at least as trusted (the person > `remember_fact` > extractors and documents). A lower-trust rewording leaves the stored fact alone. The same words still reinforce. |
| B5 | Fixed | `remember_fact` refuses a fact that doesn't restate something the person wrote this session. The kernel passes a predicate, never the text. A delegated sub-turn inherits its parent's statements. |
| C1 | Fixed | Both paths fall back on any empty answer (`empty_generation_fallback`), and an empty answer is never cached. |
| C2 | Fixed | `TurnBudget.fallbacks`, counted by the router in `complete` and `stream`, feeds `fallback_count`. |
| C3 | Fixed | The client keeps `degraded_reasons` and names the most consequential one. A correction revision no longer shows as a safety edit. |
| C4 | Partly | `AnswerEvaluator` and `AgentEvaluator` take `transport="stream"`, and the `done` frame now lists `tools`. The gates still run on `run`, because their baselines were recorded there. A stream baseline is still owed. Also fixed: `AgentEvaluator` crashed on any turn that ran a tool. |

Tests: `aura_backend/tests/unit/test_agent_audit_remaining.py` and
`aura_client/test/ux/turn_degraded_reasons_test.dart`.

## Executive verdict

**Overall: medium-high risk.** The kernel's structural defences hold: the trust channels, the
confirmation gate, the stored-plan resume, and pinned-instruction reservation. The failures sit
in three places:

1. **Tool selection is a regex gate.** A request that the English-only heuristic does not
   recognise never reaches a tool. The only thing that stops the model saying "done" is prompt
   text. (A1, A2)
2. **Conversation memory has a hole.** The prompt shows the last 4 messages, but the summary only
   starts after 20. Messages 5–20 are invisible, and when the summary does arrive, it arrives on
   the SYSTEM channel. (B1, B2)
3. **Sanitised input is not what gets stored.** The input guard's cleaned text is used for one
   turn. The raw text is written to history and replayed unguarded on every later turn. (B3)

**Most urgent fix:** code-gate action claims (A1), and give the planner a non-English intent path
(A2). This is the "agent said it did it, nothing happened" class, and it is invisible in
telemetry.

## Findings

Severity follows the skill's model:
- **critical:** can confidently produce wrong operational behaviour
- **high:** frequently degrades correctness
- **medium:** fragile or wasteful
- **low:** cosmetic

Each finding is written so it can become a tracker task as is: problem, evidence, fix, and
acceptance criteria.

### A1 — high — "No tool ran" is enforced only by prompt text

- **Layer:** 7 tool execution / 9 answer shaping.
- **Problem:** When planning is skipped or fails, nothing in code stops the answer from claiming
  that a task, event or expense was created. Three prompt instructions are the only control:
  `PLANNING_FAILED_NOTE`, `_CORE_IDENTITY`'s "Never claim you ran a tool", and the personas'
  "planning runs tools, not you". The note itself is added only when planning *raised*. When
  planning was *skipped* (A2), the model gets no signal at all.
- **User sees:** "Done, I added the task" in the chat. No `actions` frame, no approval card, and
  nothing in the task list.
- **Evidence:**
  - `aura_backend/aura_core/kernel/kernel.py:322-326`: the note is prose on the SCRATCHPAD/SYSTEM
    channel.
  - `kernel.py:551-562`: the note is added only in the `except` branch.
  - `aura_core/prompts/library.py:26-30`, `:67`.
  - `aura_core/domain/telemetry.py:187-205`: no degraded reason covers this case.
- **Fix:** a post-generation check in the kernel, for both `run` and `stream`. If the answer
  asserts a completed action (a small bilingual verb/tense classifier, or a cheap structured
  check), and no `SUCCEEDED` side-effecting tool step and no pending confirmation exist, then
  either:
  - (a) replace or append a fixed sentence and emit a `revision` frame, or
  - (b) at minimum, add the degraded reason `unbacked_action_claim` and count it.
- **Acceptance:**
  - A test turn with tools offered and planning skipped, where the provider answers "I've added
    the task", returns a `done` frame with `degraded_reasons` containing `unbacked_action_claim`.
  - With option (a), the stored history does not contain the unbacked claim.
  - A turn whose `propose_task` step succeeded is not flagged.
- **Confidence:** 0.9.

### A2 — high — The planning gate is an English-only heuristic, and the planner sees only the current message

- **Layer:** 6 tool selection.
- **Problem:** `needs_plan` decides whether any tool can run, and without a plan no tool runs
  (ADR-006). For a non-agentic persona it returns `False` in four cases:
  - any message under 40 characters, unless an English personal-OS or repository regex matches;
  - any longer message without an English multi-step marker ("and then", "first … then",
    "compare … and" …);
  - every Arabic imperative, although Arabic replies are a supported feature;
  - follow-ups such as "yes, add it" or "do the same for Friday".

  Even when planning runs, `goal_from_message` builds the goal from the current message alone, so
  "do it" has no referent.
- **User sees:**
  - "ذكّرني بموعد الطبيب غدًا" ("remind me about the doctor's appointment tomorrow") gets a prose
    answer, and no reminder is proposed.
  - "Yes, add it" after the assistant offered a task gets the A1 failure.
- **Evidence:**
  - `aura_core/planning/engine.py:43-50`: the markers, English only.
  - `planning/engine.py:160-179`: the gate.
  - `planning/engine.py:470-471`: the goal is the message only.
  - `aura_core/tools/personal_os.py:45-101`: the intent regexes, English only.
  - `kernel.py:1624-1643`: only `max_plan_iterations > 1` forces planning.
  - `prompts/library.py:30-32`: the Arabic reply path.
- **Fix:**
  - Add Arabic patterns to `_INTENT_PATTERNS` and `_MULTI_STEP_MARKERS`, or replace the regex gate
    with a cheap classifier call that is only made when tools are offered.
  - Pass the last assistant message (or the history summary) into the goal, so follow-ups resolve.
  - Fall back to planning when the previous turn ended with pending tool calls or an offered
    action.
- **Acceptance:**
  - Unit tests: `needs_plan` returns `True` for at least 5 Arabic imperatives per `propose_*`
    tool, and for "yes, add it" / "نعم أضفها" ("yes, add it") when the prior turn offered one.
  - An eval case in `agent_tasks.jsonl` for each, checking that a `propose_*` step ran.
- **Confidence:** 0.9 for the gate, 0.8 for the follow-up impact.

### A3 — high (VPS target) / low (Railway) — Tool output can be dropped by the prompt budget with no note to the model

- **Layer:** 8 tool interpretation.
- **Problem:** Tool results are ordinary, non-pinned context items (priority 30). `pack()` may
  truncate them, which is marked, or drop them entirely. `_execution_notes` names only
  unexecuted or failed steps. A `SUCCEEDED` step whose output was dropped for budget therefore
  reaches generation as nothing. That is the "an absence cannot be reasoned about" failure that
  `_execution_notes` was written to close (`kernel.py:1484-1508`).
- **Where:** the default cap is 400 tokens, and neither compose file overrides it, so this hits
  the VPS stack. `docs/reports/flame-link2-live.md:21` records exactly this in production.
  Railway runs at 4096, where it is rare.
- **Evidence:**
  - `aura_core/context/engine.py:349-364`: tool items are not pinned.
  - `aura_core/domain/context.py:299-359`: `pack` drops them.
  - `context/engine.py:422-573`: `assemble` never reports which tool items were dropped.
  - `aura_core/config/settings.py:807`: the 400 default.
- **Fix:** after `pack`, compare the tool items selected with `request.tool_results`. For each
  one dropped, add a pinned SYSTEM scratchpad line: "the output of <tool> was produced but did
  not fit; say you could not show it". Alternatively, reserve a minimum share for `TOOL_OUTPUT`
  ahead of memory and knowledge. Add a degraded reason, `tool_output_dropped`.
- **Acceptance:** a context-engine test at a 400-token cap with a 2k-token tool result shows
  either a truncated, marked result or the pinned note. It never shows neither. The stream
  `done` frame carries `tool_output_dropped`.
- **Confidence:** 0.85.

### B1 — high — History hole: messages 5–20 are neither shown nor summarised

- **Layer:** 2 session history / 4 distillation.
- **Problem:**
  - `ContextEngine` keeps only the last `prompt_history_messages` = 4 messages, which is 2
    exchanges.
  - `EpisodicMemory` keeps everything verbatim until there are 20 messages, and only then
    summarises the overflow beyond its 12-message window.
  - From the 3rd exchange to the 10th, older exchanges are therefore in neither the prompt nor
    the summary.
  - After that, the summary covers only what fell beyond 12, so messages 5–12 stay invisible for
    good.

  The only trace is `context_dropped` in telemetry.
- **User sees:** at turn 4, "what was the second option you suggested?" is answered without the
  turn it refers to. The model may invent one.
- **Evidence:**
  - `aura_core/config/settings.py:1059-1066`: the defaults 12, 20 and 4, none of them overridden
    on Railway.
  - `aura_core/context/engine.py:398-418`: `_compress_history` drops by window, and its comment
    says it does not add an omission notice.
  - `aura_core/memory/session.py:288-312`, `:358-360`.
- **Fix:** make the two windows consistent. Either:
  - summarise everything older than `prompt_history_messages`, triggered when the prompt window
    is exceeded, not at 20; or
  - pass the dropped-by-window messages into a running summary in `_compress_history`, and when
    anything was dropped without a summary, add a one-line "earlier messages omitted" marker.
- **Acceptance:** a test with 8 exchanges and the defaults shows that every exchange is
  represented either verbatim or in the summary. Where neither is possible, the prompt carries an
  explicit omission marker.
- **Confidence:** 0.9.

### B2 — medium — The history summary sits on the SYSTEM channel, and its fallback is raw, unmarked transcript

- **Layer:** 4 distillation / trust channels (ADR-006).
- **Problem:** The summary is written by a model from user *and assistant* text. The assistant
  text can quote retrieved documents and tool output, which were DATA when they arrived. The
  summary is then inserted as `"Earlier in this conversation: …"` on `TrustChannel.SYSTEM`,
  unfenced. When the summariser fails, or is not configured, the "summary" is the last 4000
  characters of the raw `role: content` transcript. The docstring says that fallback "is clearly
  marked so it is never mistaken for a model-written summary". It is not marked. Each summary
  also folds in the previous one, so errors compound: a summary of a summary.
- **User sees:** usually nothing. In the bad case, a quoted instruction from a poisoned document
  re-enters later turns with SYSTEM authority.
- **Evidence:**
  - `aura_core/context/engine.py:461-473`.
  - `aura_core/memory/session.py:378-394`: the fallback is unmarked, which contradicts its own
    docstring at line 383.
  - `session.py:406-417`.
- **Fix:**
  - Put the summary on the DATA channel inside the turn fence, or on a dedicated "own notes"
    channel that the fence rules describe.
  - Prefix the fallback with an explicit marker, for example `[unsummarised transcript excerpt]`.
  - Record in the session state whether the summary was ever produced from a tainted turn, and
    taint the turn when it was.
- **Acceptance:** a context-engine test asserts that the summary item's channel is not SYSTEM. A
  session test asserts that the fallback text starts with the marker.
- **Confidence:** 0.85.

### B3 — medium — Guarded input is used for one turn; the raw message is stored and replayed

- **Layer:** 2 session history / 1 input.
- **Problem:** `guard_input` can rewrite the message: it normalises or strips invisible and
  Unicode tag characters, and the size guard truncates. The kernel uses the cleaned `message` for
  retrieval, planning and generation, but stores `request.message`, the original, in session
  history (`_history_message`). History is never passed through context guards. From the next
  turn on, the stripped content is replayed verbatim on the USER channel. The reasoner also
  receives the unguarded `request.message` as its `question`.
- **User sees:** hidden text in a pasted block has no effect on the turn it was sent, then starts
  influencing later turns.
- **Evidence:**
  - `kernel.py:500-510`: the guarded text.
  - `kernel.py:670`, `:1023`, `:1240-1250`: the raw message is stored.
  - `kernel.py:2434`: the reasoner gets the raw message.
  - `aura_core/safety/pipeline.py:154-168`: guards rewrite the text.
- **Fix:** store and pass the guarded text: thread `message` into `_history_message`, and into
  `ReasoningRequest.question`.
- **Acceptance:** a kernel test sends a message containing Unicode tag-block characters and
  asserts that the stored history equals the guarded text.
- **Confidence:** 0.9.

### B4 — medium — A corrected fact can be swallowed as a "duplicate" of the old one

- **Layer:** 3 long-term memory.
- **Problem:** `remember()` treats any same-kind, same-scope memory at cosine ≥ 0.94 as a
  near-duplicate. In that case it keeps the *old* text and *raises* its confidence. "User's
  manager is Sara" followed by "User's manager is Omar" will often embed above 0.94. The
  correction is then discarded, and the stale fact gets stronger. There is no contradiction or
  supersede path.
- **Evidence:**
  - `aura_core/memory/engine.py:136-151`.
  - `aura_core/domain/memory.py:98`.
  - `memory/engine.py:484-510`.
- **Fix:**
  - When a near-duplicate is found and the texts differ, store the new record and mark the old
    one superseded, or run a cheap contradiction check before merging.
  - Reinforce only when the normalised texts are equal or one entails the other.
  - Recall should prefer the newer of two conflicting records.
- **Acceptance:** with the real embedder (an opt-in test), "manager is Sara" then "manager is
  Omar" results in recall returning "Omar". The old record is inactive or ranked below.
- **Confidence:** 0.6. The similarity of real pairs needs an embedding probe.

### B5 — medium — Memory writes from the model are governed by a tool description

- **Layer:** 3 long-term memory / 7 tool execution.
- **Problem:** `remember_fact` stores whatever `fact` the planner wrote. "Only for things the
  user stated about themselves … not anything you inferred" exists only in the tool's description
  string. The record's provenance is `tool:remember_fact:<correlation>`, which does not say
  whether the user's message actually contained the fact. Recall does not distinguish such a
  record from one the user saved in the UI (`v3.py:1479`). `side_effecting=True` means a
  confirmation is required only on a tainted turn.
- **Evidence:**
  - `aura_core/tools/builtin.py:271-293`.
  - `kernel.py:1156-1175`: recall does not filter on source.
- **Fix:**
  - In the handler, require lexical or semantic support of `fact` in the turn's guarded user
    message, which `ToolContext.metadata` can carry, and refuse or downgrade confidence otherwise.
  - Tag provenance `user_stated` or `model_inferred`, and rank or label the two differently at
    recall.
- **Acceptance:** when `remember_fact` is planned with a fact that does not appear in the user
  message, the record is refused, or stored with `confidence < 0.5` and `model_inferred`.
- **Confidence:** 0.75.

### C1 — medium — An empty generation without tool calls ends as a silent success

- **Layer:** 9 answer shaping / 12 persistence.
- **Problem:** The stream substitutes a fallback text only when the answer is empty *and* the
  model emitted tool calls. A plain empty or whitespace answer ends with `done`, outcome
  `SUCCESS`, no degraded reason, and an empty assistant message written to session history. `run`
  has no empty check at all.
- **Evidence:**
  - `kernel.py:958-976`: the stream path.
  - `kernel.py:1019-1027`: the empty answer is persisted.
  - `kernel.py:618-676`: the `run` path.
- **Fix:** treat `not answer.strip()` as `empty_generation` in both paths. Send the same fallback
  text and the degraded reason, and do not persist an empty assistant turn.
- **Acceptance:** with an echo provider returning `""`, both paths return non-empty text, the
  degraded reason, and history without an empty assistant message.
- **Confidence:** 0.85.

### C2 — low — Provider failover is invisible in turn telemetry

- **Layer:** 11 hidden repair/retry.
- **Problem:** The router fails over and increments `llm_fallbacks_total`, but
  `CompletionResponse` does not carry the attempts. The kernel hard-codes `fallbacks = 0`, and
  the stream never sets it. `TurnTelemetry.degraded_reasons`' `provider_fallback` can therefore
  never fire, and the client is never told that another provider answered.
- **Evidence:**
  - `kernel.py:2386-2472`: the literal `0` in every return.
  - `kernel.py:1044-1073`.
  - `aura_core/llm/router.py:901-1024`: the attempts are kept local.
  - `domain/telemetry.py:187-205`.
- **Fix:** return the attempt count, and the providers attempted, on `CompletionResponse` and on
  the last stream chunk. Set `fallback_count` and `providers_attempted`.
- **Acceptance:** a router test with a failing first provider gives a turn telemetry of
  `fallback_count == 1`, and `provider_fallback` appears in `degraded_reasons`.
- **Confidence:** 0.9.

### C3 — low — The client keeps only `degraded: bool`

- **Layer:** 10 platform rendering.
- **Problem:** The `done` frame sends `degraded_reasons`: `planning_failed`, `tool_failure`,
  `tainted_context`, `empty_generation_fallback`. `TurnMeta` stores only the boolean and
  `searched_but_empty`. The status row cannot tell "tools failed" from "context was tainted"
  (which every image or caller-context turn is).
- **Evidence:**
  - `aura_client/lib/features/chat/data/turn_meta.dart:8`, `:135-140`.
  - `features/chat/presentation/widgets/turn_status_row.dart:71-77`.
  - `kernel.py:1094-1099`.
- **Fix:** persist `degraded_reasons`. Show a specific line for `planning_failed`, `tool_failure`
  and the A1/A3 reasons, and do not badge `tainted_context` alone as degraded.
- **Acceptance:** a widget test for each reason.
- **Confidence:** 0.85.

### C4 — low — `/chat` and `/chat/stream` run different pipelines

- **Layer:** 11 hidden passes / 10 rendering.
- **Problem:** `run` uses the answer cache and the reasoner, which can run multi-sample
  strategies (several hidden generations, a confidence score). It also prepends the schema
  instruction. `stream`, the only path the app uses, uses none of these. The same question can
  therefore get different answers, and different cost, on the two routes, while eval
  (`evaluation/harness.py`, which uses `kernel.run`) measures the path users do not get.
- **Evidence:**
  - `kernel.py:2403-2458`: the `run` path.
  - `kernel.py:913-958`: the stream path.
  - `api/v3.py:735`, `:881`.
  - `evaluation/harness.py:310`.
- **Fix:** document the divergence in the stream docstring, and run the agent eval gate through
  `stream` (or both). Alternatively, apply the reasoner's direct strategy in both paths.
- **Acceptance:** the eval harness has a stream mode, and the gate runs it.
- **Confidence:** 0.8.

## Checked and sound (do not re-open)

| Area | Evidence |
|---|---|
| Semantic cache keyed on full context; per-scope invalidation (F13) | `llm/cache.py:196-231`, `:406-419` |
| Image turns never read or write the cache | `kernel.py:2400-2403` |
| Model `tool_calls` frames are informational, never dispatched | `kernel.py:917-956` |
| An approved turn runs its stored plan, never a re-plan (F-04) | `kernel.py:1847-1862`, `:1886-1897` |
| Planner fallback is a degraded turn, not "no tools needed" (CB-14) | `kernel.py:1698-1710` |
| Pinned instructions and fence rules are reserved before packing | `context/engine.py:480-499` |
| Recalled memories and caller evidence go through context guards (F35) | `kernel.py:2048-2143` |
| A streamed redaction is what gets stored (F26) | `kernel.py:995-1006` |
| Replan sees step descriptions, not tool output; ids renumbered (F12) | `planning/engine.py:411-445` |
| `response_schema` refused on the stream rather than silently ignored | `api/v3.py:871-873` |
| Session state is owner-namespaced (IDOR) | `memory/session.py:212-246` |
| The client handles every stream frame type, including `revision` and post-hoc `error` | `sse_agent_client.dart:54-74`, `chat_repository.dart:699-720` |

## Ordered fix plan (code first)

1. **A1:** code-gate unbacked action claims, and add the degraded reason. It closes the most
   damaging user-visible failure, and it is the backstop for A2 and A3. Expect "done" with no
   action to become visible and countable.
2. **A2:** a bilingual intent gate, and follow-ups carrying their referent. This removes the main
   cause of A1 for Arabic and conversational users, so tools run when users expect them.
3. **B3:** store the guarded text. A one-line change that closes a guard bypass.
4. **B1, then B2:** align the history windows, then move the summary off SYSTEM and mark the
   fallback. This stops the conversation forgetting turns 3–10, and keeps summaries from carrying
   authority.
5. **A3:** a note or reservation for dropped tool output, which needs the VPS target first. Once
   it lands, the model no longer answers around a tool result it never saw.
6. **C1, then C2, then C3:** empty-answer handling, failover telemetry, and per-reason client
   display. Each turns a degraded turn that today looks like a success into one that is
   observable.
7. **B4, B5:** supersede on correction, and provenance-checked memory writes. These stop memory
   drifting from what the user actually said.
8. **C4:** evaluate the stream path, so the eval numbers describe the product.

## Not covered

- **Client retry and idempotency, and client-versus-server timeouts.** The transport reviewer
  stopped early. Retry was addressed by F2/INT-108 on 11-18, but not re-checked here.
- **Delegation sub-turns, voice re-shaping of LLM text, and the CAG prefix's freshness after
  document edits.**
- **Runtime proof of anything above.** No test was written or run for these findings.
