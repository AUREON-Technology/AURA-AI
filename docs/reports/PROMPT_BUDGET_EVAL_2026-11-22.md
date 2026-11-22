# Prompt budget evaluation: how much retrieved evidence reaches the model

**Date:** 2026-11-22. **Branch:** `anas/knowledge-wp2-prompt-budget`.
**Plan:** WP2 / finding KM-11 in `docs/module-readiness/knowledge/source-registry/2026-11-22/`.
**Harness:** `aura_backend/evaluation/prompt_budget_eval.py` (pinned by `tests/unit/test_prompt_budget_eval.py`).

**Evidence level: TESTED offline. In-memory container, hash embedder, heuristic reranker, development
CPU, a scripted provider. Not measured with a real model, a semantic embedder or on a deploy target, and
no latency figure here comes from production.**

## What was measured

The existing gate scores retrieval by document id (recall@k). It cannot see what sits between a retrieved
chunk and the model, so I ran the real kernel turn over a grid of the three budgets that do:

| Setting | Env var | Today's default |
|---|---|---|
| Whole assembled prompt, per generation | `AURA_LLM_MAX_PROMPT_TOKENS` | **400** |
| Chunks retrieved and offered | `AURA_KNOWLEDGE_TOP_K` | 2 |
| Characters kept of each chunk | `AURA_KNOWLEDGE_PROMPT_CHUNK_CHARS` | 280 |

For each of the 20 answerable questions in `docs_corpus` (the repository's ADRs), a provider recorded the
fenced knowledge text it was shown. Measures:

- `evidence_in_prompt`: share of questions whose `expected_substrings` appear in that text.
- `retrieved_hit`: share whose expected document was retrieved at all.
- Prompt tokens: the turn-wide total over every generation (planner plus answer), as the turn's telemetry
  reports it. Divide by about two for one generation.

## Results (27 configurations; full grid in `E:\aura_audit_tmp\budget-grid.json`)

Evidence in the prompt (retrieved_hit in the last column):

| Cap | top_k | 280 chars | 600 chars | 1,200 chars | retrieved_hit |
|---|---|---|---|---|---|
| **400 (today)** | 2 | **0.00** | 0.00 | 0.00 | 0.60 |
| 400 | 4 | 0.00 | 0.00 | 0.00 | 0.65 |
| 400 | 6 | 0.00 | 0.00 | 0.00 | 0.70 |
| 1,000 | 2 | 0.35 | 0.40 | 0.50 | 0.60 |
| 1,000 | 4 | 0.40 | 0.45 | 0.55 | 0.65 |
| 1,000 | 6 | 0.40 | 0.45 | 0.55 | 0.70 |
| 3,000 | 2 | 0.35 | 0.45 | 0.65 | 0.60 |
| 3,000 | 4 | 0.40 | 0.50 | **0.70** | 0.65 |
| 3,000 | 6 | 0.40 | 0.50 | 0.75 | 0.70 |

Where the cap starts to let knowledge in (top_k 2, 600 chars): cap 500 gives 0.05, 600 gives 0.20, 800 gives
0.40, 1,000 gives 0.40, 1,500 gives 0.45.

Cost of the better settings, prompt tokens summed over the turn's generations: mean 0.52k at today's
settings, 1.15k at cap 3,000 / top_k 4 / 600 chars, 1.30k at 1,200 chars (p95 about 4.0k).

## What it shows

1. **At a 400-token cap, retrieved knowledge never reaches the model.** Raising `top_k` or the chunk length
   changes nothing there (0.00 in all nine cells), so the cap is the binding limit, not the clip. The likely
   reason is that the fixed parts of the prompt (instructions, fence rules) use the budget first and the
   knowledge section is what is left out; I did not inspect which items were dropped.
2. **Once the cap is about 800 or more, chunk length decides.** At 280 characters the model sees 35 to 40%
   of the evidence; at 1,200 characters (close to a whole stored chunk, which is about 320 tokens) it sees
   65 to 75%.
3. **`top_k` matters little here.** Going from 4 to 6 adds one question out of 20, which is within noise.
4. **Some evidence is incidental.** At 1,200 characters `evidence_in_prompt` can exceed `retrieved_hit`
   because an expected phrase also occurs in a chunk from another document. Treat the evidence figure as an
   upper bound on grounded evidence, and read the trend rather than a single cell.

## Recommendation (to be set by the owner; nothing in this change sets it)

| Target | Setting | Why |
|---|---|---|
| **Railway (hosted providers, 8k+ context)** | `AURA_LLM_MAX_PROMPT_TOKENS=3000`, `AURA_KNOWLEDGE_TOP_K=4`, `AURA_KNOWLEDGE_PROMPT_CHUNK_CHARS=1200` | 0.70 evidence against 0.00 today, for about 1.3k tokens a turn. `docs/deployment/control-plane.md` already named 3000 as a first value. Check `TPM` first: a turn sends roughly the prompt twice |
| **VPS CPU stack (qwen2.5:3b)** | leave at 400, knowing it means no knowledge in the prompt | 400 was chosen because CPU prefill cannot evaluate more before the stream timeout. Raising it trades latency for knowledge, and that trade is **not measured**. This is an owner decision |

The live Railway value of `AURA_LLM_MAX_PROMPT_TOKENS` is unknown: it is set in the dashboard, and
`/health/ready` does not report it. If it is unset, production is at 400 and gets no retrieved knowledge even
after the index is repaired (WP0).

## What is not settled

- **Answer quality with a real model.** This measures whether the evidence is in the prompt, not whether a
  model uses it. A real-model run on the same questions is still needed.
- **A semantic embedder.** The hash embedder caps `retrieved_hit` at 0.60 to 0.70. A semantic one moves that
  number; it should not move the gap between it and `evidence_in_prompt`.
- **Latency.** The extra prompt tokens cost time on CPU, and a hosted provider's per-minute token limit
  bounds how many turns fit.
- **Small suite.** 20 questions; one question is 0.05.
- **Arabic.** Not run. Arabic text uses more tokens per character, so the same character budget carries
  fewer words.
