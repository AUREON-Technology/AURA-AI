# AURA AI — Production Readiness Score

Assessed 2026-08-12, at commit `e7c7896` (PR #8 merged to `main`).

The brief asked for this to be brutally honest and uninflated. Two things make that easier to
comply with than it sounds. First, most of the interesting findings in this work were defects in
code I had just written and declared finished — so a high score would contradict the record above
it. Second, "brutally honest" mostly means separating three things that get conflated: what is
**built**, what is **tested**, and what is **proven in production**. Almost everything here is the
first two. Almost nothing is the third.

## Scoring scale

| Score | Meaning |
| --- | --- |
| 9–10 | Proven under production load, with operational history |
| 7–8 | Complete and well tested; no production evidence |
| 5–6 | Complete but with a material gap, or untested in a way that matters |
| 3–4 | Partial — the shape exists, key capability does not |
| 1–2 | Declared, not delivered |

**No dimension can score above 8**, because this system has never served a real user. That ceiling
is the single most important fact in this document.

---

## Scores

| Dimension | Score | The reason, stated plainly |
| --- | --- | --- |
| Architecture | **8** | Hexagonal, stdlib-only domain, one-directional strangler boundary — all enforced by AST fitness tests rather than convention. The design has survived several substantial changes (three storage adapters, ten retrieval strategies, a reranker) without a port changing shape, which is the only real evidence a boundary is in the right place. |
| AI / ML | **5** | Ten retrieval strategies, six memory kinds, evaluation harness, ML architectures declared as data. But nothing is trained, no model has been fine-tuned, and the ML platform has never run a real job. The pipeline is real; the ML is aspirational. |
| LLM integration | **7** | Provider abstraction, five routing policies, circuit breaker, pre-first-token-only failover, two-tier response cache. Deducted because it has only ever run against Ollama and an echo provider — vLLM and llama.cpp are supported by protocol, not by having been tried. |
| RAG | **7** | All ten strategies implemented as composable objects with 90–100% coverage, the adaptive router selects per query, and page-aware extraction means `multimodal` now filters real content rather than an empty set. Deducted, and not raised, because retrieval quality is still unmeasured on any real corpus — and because two of the ten (`graph`, then `multimodal`) were structurally incapable of working at the moment I first reported them complete. Both are fixed; the pattern is the reason this is not an 8. |
| Memory | **7** | Six kinds with per-kind decay, supersede-not-delete, provenance-scoped forgetting, per-owner quotas. Well tested, entirely unexercised by real usage patterns. |
| Security | **6** | The four-layer prompt-injection remediation is genuine and mechanically enforced; external inference is impossible without defeating seven independent checks (three of which exist because the first four missed real violations in YAML, HCL and `pubspec.yaml`). Deducted hard for what has *not* happened: no penetration test, no CVE scan in CI, no formal threat model, and the Firebase auth path is inherited rather than reviewed. |
| Performance | **5** | Pipeline overhead measured at p95 4.56 ms against a 60 ms budget — but that was measured with in-memory adapters and an echo provider, which is to say it measured the parts that are not the bottleneck. No measurement exists with Postgres, Qdrant, Redis or a real model in the path. The reranker's latency figure is inferred, not measured, and labelled as such. |
| Scalability | **4** | The stateless-gateway design is sound and the binding constraint is correctly identified as generation concurrency. Nothing has been load tested. A concurrency figure of 478 turns/s appears in the benchmark report and describes an echo provider; it says nothing about real capacity. |
| Reliability | **5** | Failure handling is the most carefully considered part of this work — circuit breakers, honest empty results, degradation paths at every layer that needs one, and a rule that nothing fabricates an answer when a subsystem fails. But no chaos testing, no recovery drill, and the restore procedure has never been executed. |
| Observability | **7** | Structured content-free logging, a full metric catalogue, three provisioned dashboards, alert rules including one for active security bypasses. Deducted because dashboards that have never been watched during an incident are a hypothesis about what you will want to see. |
| Testing | **7** | 1284 backend tests plus 63 legacy, 90% coverage, no infrastructure required, ~7 seconds. Fitness tests enforce architecture, self-hosting and backend resolvability. Deducted — and this is the deduction I would defend hardest — because **fakes concealed real defects twice in this work**, including a strategy that could not function at all while five tests passed. Coverage measures execution, not correctness. The extraction work is the corrected method: real PDFs through the real library, which caught two defects a double could not have. |
| MLOps | **6** | Approval binding to person + time + artefact digest, expiry, canary by stable hash, rollback needing no approval, gated nightly index promotion with O(1) alias rollback. The knowledge loop is complete and testable; the model loop has never promoted anything. |
| Deployment | **6** | The production stack now boots, which it could not before this work — three adapter modules the compose file referenced had never been written. Docker build, health split, secrets as files, backup with restore verification. Deducted because it has been deployed exactly zero times, and the running VPS is a hand-assembled four-container stack that is not this compose file. |

### Overall: **6.2 / 10**

Unweighted mean, 6.15, rounded. I am not going to weight it, because weighting is where an honest
score becomes a flattering one — any weighting that raises this number does so by deciding that the
untested dimensions matter less.

**What 6.2 means:** a well-architected system with unusually good failure handling and unusually
honest empty-result semantics, which has never met a user, a real corpus, or production load. The
engineering is largely sound. The evidence that it works is largely absent.

---

## The three things that would move this most

1. **Deploy it and serve real traffic** (+1.5 across Performance, Scalability, Reliability,
   Deployment). Every ceiling above is the same ceiling. Nothing else in this list competes.
2. **Measure retrieval quality on a real corpus** (+1 on RAG and AI/ML). The evaluation harness
   exists and the metrics are implemented; there has never been a labelled suite to run it against.
   Without that, "ten strategies" is a count, not a capability.
3. **Load test with the real data tier** (+1 on Performance and Scalability). The 4.56 ms figure
   measured in-memory adapters. The interesting number is what happens with Qdrant, Postgres and a
   7B model actually in the path.

Cheap and worth doing regardless: add `pip-audit` to CI (Security), and run one restore drill
against a staging copy (Reliability). Both are hours, not weeks.

---

## What I would not claim

- **That the RAG strategies improve answers.** They are correctly implemented and tested. Whether
  `hyde` beats `simple` on this corpus is unmeasured, and on some corpora it will be worse — HyDE is
  known to hurt on entity-specific questions, which is why the router selects it rather than
  defaulting to it.
- **That `multimodal` handles images.** It handles *page structure*: a PDF ingested through
  `knowledge/documents.py` yields `pdf_page`, `table` and `image` chunks with page numbers, and the
  filter selects them correctly. But there is no OCR and no vision model, so a scanned page is
  indexed as a placeholder stating it could not be read. That placeholder is deliberate and useful —
  it is retrievable, and it names the fixable reason — and it is not text recognition. Audio and
  video have no extraction at all, and a detected table is tagged, not parsed into cells.
- **That the security posture is verified.** It is *designed*, and the design is enforced by tests.
  Design plus tests is not a penetration test.
- **That the benchmark numbers describe production.** They describe a pipeline with in-memory
  adapters and an echo provider, which is stated in the benchmark report and worth restating here,
  because a p95 of 4.56 ms is exactly the kind of number that gets quoted without its conditions.
- **That coverage means correctness.** 90% is real and useful. It also sat at 90% while `GraphRAG`
  was incapable of traversing a graph.

---

## What I am confident about

- **External inference is genuinely impossible** without deliberately defeating four independent
  checks — SDK imports, endpoint strings, declared dependencies, and runtime `base_url` validation,
  all reading one shared registry. This is the strongest claim in the document.
- **No inference happens on the mobile device, and no model weights ship in the APK.** Verified by
  `flutter analyze`, a green release build, and the removal of a 986 MB LFS-tracked asset.
- **The production stack boots.** Verified by constructing the real container against the real
  compose file's backend selection, plus a fitness test that will fail if that ever stops being true.
- **Failure paths degrade rather than fabricate.** This got the most design attention of anything
  here, and several of the defects found along the way were violations of it that I had shipped.

---

Related: [Final Implementation Report](AURA_AI_FINAL_IMPLEMENTATION_REPORT.md) ·
[Current Architecture Audit](AURA_AI_CURRENT_ARCHITECTURE.md) ·
[Security Audit](docs/reports/security-audit.md) ·
[Performance Report](docs/reports/performance-report.md)
