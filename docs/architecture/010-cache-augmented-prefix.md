# ADR-010 — Cache-Augmented Generation as a reserved prompt prefix

**Status:** Accepted · **Date:** 2026-08-29

## Context

RAG retrieves a few passages per turn. That is the right default for large, per-user, mutable
corpora. It is the wrong shape for a small, stable handbook (leave policy, product facts) that
every turn needs: the retriever can miss it, and every hit still pays embedding + search latency.

Cache-Augmented Generation (CAG) puts that handbook in the prompt once and reuses it. True CAG
keeps the model’s KV prefix across turns. This deployment runs CPU Ollama with a hard
400-token prefill cap (`AURA_LLM_MAX_PROMPT_TOKENS`), so a full-corpus KV cache is not viable
today.

## Decision

Ship **CAG-lite** now; leave seams for true KV reuse later.

1. **Reserved slot.** After system instructions, before memory/RAG/history, the context engine
   packs a CAG section of at most 80 tokens (40 global + 40 user by default). The 400-token
   prefill cap does **not** rise.
2. **Pins, not auto-ingest.** Operators pin up to two global `INTERNAL`/`VERIFIED` documents;
   users pin up to three of their own. Pinning is a flag on the active document version.
3. **Byte-stable prefix.** Identity = hash of `(owner, budgets, sorted doc ids/versions/hashes)`.
   A `prefix_nonce` is generated once when the prefix is built and reused until invalidation.
   The per-turn fence nonce applies only to variable DATA (memory, RAG, tools).
4. **RAG stays.** Query-time hybrid retrieval still runs. Chunks from a pinned document are
   dropped from the RAG section so the clip budget goes to something new.
5. **KV seam only.** `ProviderCapabilities.supports_prefix_cache` and optional
   `CompletionRequest.prefix_tokens` / `cache_control` exist for a later GPU/vLLM adapter.
   Ollama on this VPS does not get a prefix-cache integration in this phase.

## Alternatives rejected

| Alternative | Why rejected |
| --- | --- |
| Stuff the whole knowledge base into context | Exceeds the 400-token CPU prefill budget; destroys TTFT. |
| Raise `max_prompt_tokens` to “make room for CAG” | Undoes the latency fix that made chat usable on this box. |
| Replace RAG with CAG | User corpora grow past any window; CAG cannot scale that way. |
| Derive the fence nonce from document text | A writer could close the fence; nonce must be an unguessable secret. |
| Put the turn fence nonce in instructions | Would make the prompt head unique every turn and poison a later KV cache of instructions+CAG. |

## Consequences

**Positive** — a tiny handbook is always present; prefix bytes are stable and measurable;
invalidation ties to pin/ingest/delete and clears the LLM answer cache namespace.

**Negative** — 80 tokens of the 400-token budget are reserved when pins exist; operators must
curate the handbook deliberately.

**Impact:** answer grounding for pinned facts improves without retrieval luck; latency is
unchanged beyond packing a few dozen tokens. True KV-prefix savings require a different
serving stack and are out of scope here.
