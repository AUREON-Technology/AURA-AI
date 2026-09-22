# AURA AI — Execution State Matrix

**Generated:** 2026-09-22
**Phase:** 0 → 1 transition complete

## Legend
- **STATE**: NOT_IMPLEMENTED, SCAFFOLDED, IMPLEMENTED, IMPLEMENTED_AND_TESTED, INTEGRATED, END_TO_END_VERIFIED, PRODUCTION_READY
- **SEV**: P0 (blocker), P1 (critical), P2 (important), P3 (nice-to-have)
- **✓** = verified working; **≡** = exists but not verified; **✗** = broken/missing

---

## PHASE 0 — Current-State Baseline

### P0 Status: ARCHITECTURE FITNESS TESTS
- `test_no_external_inference.py` — PASS ✓ (enforces self-hosted inference)
- `test_v3_only_surface.py` — PASS ✓ (enforces /api/v3 only)
- `tests/architecture/test_backends_are_resolvable.py` — PASS ✓
- `tests/architecture/test_dependency_rules.py` — PASS ✓
- `tests/architecture/test_dockerfile_and_observability.py` — PASS ✓
- `tests/architecture/test_untrusted_content_tags.py` — PASS ✓
- `tests/architecture/test_workspace_local_tools.py` — PASS ✓

### P1/P2 Status: CORE UNIT TESTS (selected)
- `test_knowledge.py` — PASS ✓
- `test_evaluation.py` — PASS ✓
- `test_in_memory_adapters.py` — PASS ✓
- `test_rag_strategies.py` — PASS ✓
- `test_llm_routing.py` — PASS ✓

### P2 Status: SETTINGS VALIDATION
- `test_settings_validation.py` — 63 unit tests, mostly PASS ✓
- Core checks: debug+production gate, provider kind validation, model requirements
- Gap: production insecure combos still need runtime enforcement

### P2 Status: KERNEL PIPELINE
- `AIKernel._run_turn()` — IMPLEMENTED ✓ (all 10 stages: guards → memory/retrieval → context → generation → output guards → persist → telemetry)
- `AIKernel.stream()` — IMPLEMENTED ✓ (honest pre-first-token failover only)
- Agent planning/execution path — IMPLEMENTED ✓
- Safety pipeline (input/output guards) — IMPLEMENTED ✓
- Taint tracking — IMPLEMENTED ✓

### P1 Status: PROVIDER ROUTER
- `ProviderRouter` — IMPLEMENTED ✓ (5 policies: LOCAL_FIRST, CHEAPEST, FASTEST, QUALITY_FIRST, BALANCED)
- Circuit breakers with failover-before-first-token — IMPLEMENTED ✓
- Stream failover only before first chunk — IMPLEMENTED ✓
- Post-first-token error surfacing — IMPLEMENTED ✓

### P1 Status: TOOLS & POLICY
- `ToolRegistry` with `ToolPolicy` — IMPLEMENTED ✓
- Capability grants, denylist/allowlist — IMPLEMENTED ✓
- Taint checks in `ToolContext` — IMPLEMENTED ✓
- Confirmation policy for side-effects — IMPLEMENTED ✓

### P1 Status: MEMORY ENGINE
- `MemoryEngine` — IMPLEMENTED ✓ (decay, consolidation, provenance)
- Memory recall with scoring — IMPLEMENTED ✓

### P2 Status: KNOWLEDGE ENGINE
- Hybrid RAG (BM25+dense, RRF, reranking, MMR) — IMPLEMENTED ✓
- Knowledge ingestion with chunking and graph extraction — IMPLEMENTED ✓
- Score threshold, quarantine — IMPLEMENTED ✓

### P2 Status: SAFETY PIPELINE
- Input/context/output guards — IMPLEMENTED ✓
- PII redaction — IMPLEMENTED ✓
- Secret leak blocking — IMPLEMENTED ✓
- Quarantine of untrusted content — IMPLEMENTED ✓

### P1 Status: CONTAINER / INFRASTRUCTURE
- `Container` composition root — IMPLEMENTED ✓ (lazy connections, eager config validation)
- In-memory adapters as substrate — IMPLEMENTED ✓
- Postgres backend switches — SCAFFOLDED (code exists but no real Postgres)
- Graph store, goals port, github identities — SCAFFOLDED (gated on document_backend=="postgres")

### P2 Status: LEARNING / MLOps
- `ScriptedTrainer` — IMPLEMENTED ✓ (placeholder; no real weight training)
- `FineTuneService` — IMPLEMENTED ✓ (orchestrator only)
- `ModelRegistry` — IMPLEMENTED ✓ (in-memory)
- `ExperimentTracker` — IMPLEMENTED ✓ (in-memory)
- `KnowledgeRefreshPipeline` — IMPLEMENTED ✓ (infrastructure-gated)
- **Gap**: Real SFT/LoRA/QLoRA training with actual weight modification — NOT_IMPLEMENTED

### P2 Status: MULTIMODAL
- Tesseract OCR engine — SCAFFOLDED (binary-gated: `tesseract_available()`)
- Faster-Whisper STT — SCAFFOLDED (package-gated: `faster_whisper_available()`)
- Piper TTS — SCAFFOLDED (model file-gated: `piper_available()`)
- espeak-ng fallback — IMPLEMENTED ✓ (already on PATH in CI)
- **Gap**: Full multimodal pipeline (OCR→chunk→embed→index→retrieve) — NOT_IMPLEMENTED

### P2 Status: AGENT / EXECUTION
- `ExecutionEngine` — IMPLEMENTED ✓ (bounded step execution, retry policies)
- `AgenticExecutor` — IMPLEMENTED ✓ (bounded replan loop)
- Workspace management — IMPLEMENTED ✓ (InMemoryWorkspaceStore + Docker-gated)
- Host Git — IMPLEMENTED ✓ (when token+repo configured)
- **Gap**: Agent runtime with persistent state, recovery, measurable completion — PARTIALLY_COMPLETE

### P1 Status: EVALUATION
- `evaluation/runner.py` — IMPLEMENTED ✓
- `evaluation/suites/agent_tasks.jsonl` — EXISTS ✓ (empty benchmark suite)
- Unit tests for evaluation — PASS ✓
- **Gap**: Continuous evaluation with regression gates — NOT_IMPLEMENTED

### P1 Status: PERSONAL OS
- Tasks, Goals, Projects — IMPLEMENTED ✓ (InMemoryPersonalOsStore)
- Calendar, Finance — EXIST but minimal
- Sync across backend/client — NOT_SYNCED
- **Gap**: Full Personal OS with backend API → client UI → database persistence

### P1 Status: SECURITY / AUTH
- JWT authenticator in `aura_service.py:_build_authenticator` — IMPLEMENTED ✓
- RBAC with engineer_agent_roles — IMPLEMENTED ✓
- Rate limiting — IMPLEMENTED ✓
- Settings validate gate — IMPLEMENTED ✓
- **Gap**: End-to-end JWT verification in production path

### P1 Status: DATABASE / DISTRIBUTED
- In-memory adapters as substrate — IMPLEMENTED ✓
- Postgres backend code — SCAFFOLDED (imports exist, no running instance)
- **Gap**: Real Postgres-backed deployment path verified

---

## GAP REGISTER (sorted by SEV then PHASE)

| ID | Priority | Gap | Dependency | Files | Exit Criteria |
|----|----------|-----|------------|-------|---------------|
| **G1** | P0 | `AuraSettings.validate()` refuses insecure env combos at startup | Settings `validate()` method needs completeness check | `aura_core/config/settings.py` | `pytest tests/unit/test_settings_validation.py` all pass; no P0/P1 defects on `validate()` |
| **G2** | P1 | End-to-end JWT authenticator verification in `aura_service.py` | Auth flow from request → Principal | `aura_service.py:_build_authenticator`, `services/jwt_auth.py` | Integration test: real JWT token → Principal with correct user_id/role/capabilities |
| **G3** | P1 | Real Postgres-backed deployment path verification | Document/memory/vector backends → Postgres | `container.py` postgres builders, `aura_service.py` | Container builds successfully with Postgres settings; schema check works |
| **G4** | P2 | Full multimodal pipeline (OCR→chunk→embed→index→retrieve) | Tesseract binary, OCR engine integration | `aura_core/adapters/tesseract_ocr.py`, `knowledge/engine.py` | KnowledgeEngine ingest with OCR'd document returns chunks with citations |
| **G5** | P2 | Real SFT/LoRA/QLoRA training with actual weight modification | Learning pipeline, finetune trainer, model registry | `aura_core/learning/`, `aura_core/mlops/` | Trainer runs, weights actually change (verified by diffing model state before/after) |
| **G6** | P2 | Continuous evaluation with regression gates | Evaluation runner, canary gates, model registry | `aura_backend/evaluation/`, `aura_core/learning/` | Evaluation run blocks bad releases; canary → production pipeline works |
| **G7** | P2 | Personal OS backend API → client UI sync | Personal OS store, sync protocol, Drift SQLite | `aura_core/personal_os/`, `aura_client/` | Client can create/read/update personal OS records; backend API serves them |
| **G8** | P1 | Engineer agent with persistent state, recovery, measurable completion | AgenticExecutor, workspace management, engineer runs | `aura_core/execution/`, `aura_core/agents/registry.py` | Agent completes bounded multi-step task; outcomes recorded; recovery from failure works |

---

## EXECUTION PRIORITY ORDER

**Immediate (P0/P1, blocking further progress):**
1. G1: Settings validate() completeness + production insecure combo gate
2. G2: End-to-end JWT authenticator verification

**Short-term (P1/P2, enable core capabilities):**
3. G3: Postgres deployment path
4. G5: Real training pipeline (SFT/LoRA/QLoRA)
5. G8: Engineer agent with persistence

**Medium-term (P2, platform completeness):**
6. G4: Multimodal pipeline
7. G6: Continuous evaluation
8. G7: Personal OS sync

**Long-term (P3, production hardening):**
9. Scale & hardening (security, reliability, infrastructure)
10. Continuous intelligence loop

---

## NEXT IMMEDIATE ACTION

**Phase 1: Critical Reliability & Security** — Begin with G1 (settings.validate()) and G2 (JWT authenticator E2E verification).

**Rationale:** These are P0/P1 defects that if unaddressed, block all downstream work. The architecture and core pipelines are already in place; we need the security gates to be complete before adding features.

**Exit criteria for Phase 1:**
- `pytest tests/unit/test_settings_validation.py` — ALL PASS
- End-to-end JWT test: valid token → Principal with correct user_id/role; invalid token → AuthenticationError
- No new test failures introduced