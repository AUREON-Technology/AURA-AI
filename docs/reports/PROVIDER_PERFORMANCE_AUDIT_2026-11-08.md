# Provider performance audit — 2026-11-08

**Scope:** the LLM provider layer's performance on the Railway control plane, covering
`ProviderRouter`, the provider adapters, admission, the response cache, and the kernel's
generation calls. **Baseline:** `main` at `4287b87`, re-based on `dfcfd1c` before any edit (which
brought in ADR-033). **Method:** source trace, local tests, and the measurements already committed
under `docs/reports/`. No live endpoint was probed and no load was run.

## Verdict

**NOT READY.** No generation on the live topology has been measured: no TTFT, decode
throughput, end-to-end latency or concurrency figure exists for `hosted1` or `hfspace` on
Railway.

The only real-model generation figures for `hosted1` are n=1 and were taken in-process against
OpenRouter's free tier: TTFT 1179 ms and completion 4806 ms
(`phase-6-hosted-inference-openrouter-inprocess-20261028.txt:29-31`). The launch targets in
`docs/production/PRODUCTION_SLO.md` S3/S4/S6 (TTFT p95 < 2.5 s, answer p95 < 15 s, 50
concurrent users) have never been run. GAP-041 is still open.

## Owner decisions (2026-11-08)

| Decision | Choice |
|---|---|
| Capacity for sensitive turns | **Accept a chat-only launch.** Memory, RAG, tools, images and the Engineer stay unavailable until a sensitive-capable provider exists. |
| Launch latency bar | **Keep** S3/S4/S6 as written. |
| Where to load-test | **Production, off-peak**, approved separately at run time with stop conditions. |
| Deploy target | **Railway only.** The VPS is kept at config parity, not tuned. |
| Copy for an unavailable feature | "This needs a feature that isn't available yet. AURA can answer plain questions right now. Answers that use your memory, documents, tools or images are coming soon." No retry. |

## Findings and disposition

| ID | Pri | Finding | Disposition |
|---|---|---|---|
| PP-01 | P0 | Only `hosted1` is healthy, and its ceiling is `internal`. Every sensitive turn has no eligible provider (FSA-024). | **Accepted limitation** (chat-only launch). The user-facing half is PP-10. |
| PP-02 | P1 | No live performance evidence (above). | **Open.** Phase 3 (load test) needs separate approval. |
| PP-03 | P1 | Railway's documented settings leave admission unbounded: no queue limit, no queue timeout, no per-user cap. `hosted1` concurrency defaults to 8 per process, and the image runs 4 processes. | **Docs:** starting values and sizing rules in `docs/deployment/control-plane.md`. The owner applies them in the Railway dashboard. |
| PP-04 | P1 | Shadow mirroring was awaited inside the turn, despite its "fire-and-forget" comment. It also sent `artefact_uri` unchecked as a model name. | **Fixed:** runs in the background, at most one at a time, priority 9, no user key; non-servable tags are skipped. |
| PP-05 | P2 | `fastest`/`balanced` routing rank providers by total call duration, not by TTFT. | **Deferred:** Railway uses `quality_first`. |
| PP-06 | P2 | No bound on a turn's total generation time (worst case about 210 s on the documented settings). | **Fixed:** `AURA_LLM_TURN_GENERATION_BUDGET_S`, off by default. |
| PP-07 | P3 | Nothing bounded a stream after its first chunk. | **Fixed:** each chunk is bounded by the request timeout; `AURA_LLM_STREAM_MAX_S` caps the total. |
| PP-08 | P3 | The semantic cache tier can never hit with `hash-embed-v1` (the module's own example pair scores 0.64 against a 0.97 threshold), yet it cost an embedding and a cache read on every miss. | **Fixed:** the tier stays off while the embedder declares `is_semantic = False`. |
| PP-09 | P2 | The 400-token global prompt cap is sized for CPU Ollama. | **Docs only:** raise the global cap on Railway (sizing rule in `control-plane.md`). No per-provider cap was built; see below. |
| PP-10 | P1 | A sensitive turn reported "the model is not answering" with a Retry card that could never work. | **Fixed:** `capability_unavailable` (not retryable), with the owner's copy in the client and in the chat transcript. |

**PP-09 delivered less than approved.** The owner approved a per-provider prompt cap. Every
provider on Railway is fast, so raising the existing global cap there achieves the same thing with
no code. A per-provider cap only matters for a mixed deployment with a CPU model, which the owner
scoped out ("Railway only"). It remains available if that changes.

## Evidence for the changes

All of this is local; none of it is CI or production evidence.

| Item | What shows it |
|---|---|
| PP-10 | `tests/unit/test_llm_data_classification.py::TestCapabilityUnavailable` (5 tests; 3 fail on the previous router). `aura_client/test/chat_capability_unavailable_test.dart` (6 tests). |
| PP-04 | `tests/unit/test_shadow_off_critical_path.py` (5 tests). Against the previous kernel, `run()` times out, the stream hangs, and the URI test fails. |
| PP-06, PP-07 | `tests/unit/test_turn_deadlines.py` (16 tests); every behaviour test fails on the previous router. |
| PP-08 | `tests/unit/test_embeddings_and_cache.py::TestSemanticTierNeedsASemanticEmbedder` (4 tests). |
| Whole suites | Backend `pytest` and the legacy `unittest` tree: see the PR. Client: `flutter analyze` clean; `flutter test` 572 passed, 3 skipped (Flutter 3.44.6). |

## What is still not proven

- **Every performance figure on Railway.** Phase 3's load test is what produces them.
- **That the PP-03 starting values are right.** They are derived from the one measured
  completion and the providers' published limits, which the owner reads from the dashboard.
  Which model `hosted1` actually serves is still unknown from the repo.
- **Whether a free-tier `hosted1` can carry S6 (50 users) at all.** The sizing rule in
  `control-plane.md` suggests it cannot at 30 RPM. The load test will show that as `busy` and
  `rate_limited` responses.
- **Behaviour on a real deploy.** Merging to `main` deploys to Railway with no CI gate (CB-44), so
  a merge is a production change.
