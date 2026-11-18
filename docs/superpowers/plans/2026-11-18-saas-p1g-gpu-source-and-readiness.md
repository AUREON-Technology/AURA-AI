# SaaS P1-G: Always-On GPU Source and Live Readiness Evidence, Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the self-hosted GPU source (`hfspace`) always on, on the GPU the owner chose. Build the one tool that collects the readiness-floor evidence this plan can reach on the live target.

**Architecture:** The Space's hardware and sleep policy become code. The inference-plane deploy workflow asks Hugging Face for them on every deploy, and a contract test pins the values. A new stdlib-only verifier, `deploy/scripts/verify_gpu_source.py` (the pattern of `verify_vision.py`), runs from anywhere against the live gateway, the Space and the Hugging Face API. It writes `docs/reports/readiness-gpu-<UTC>.txt`. A unit test runs the whole verifier in-process against a local fake of all three, so a later rename cannot break the one run that produces evidence.

**Tech Stack:** GitHub Actions with `huggingface_hub`; Python 3.11 stdlib (`urllib`, `concurrent.futures`, `http.server` in the test).

**Spec:** `docs/superpowers/specs/2026-11-16-saas-phase1-design.md`: §5.3 "The self-hosted GPU source", §6.1 items 2, 3, 6 and 9, and §8 decisions 2 and 3.

## Owner decisions (2026-11-18)

- **GPU:** one **NVIDIA L4**, 24 GB, Hugging Face flavor `l4x1`, at $0.80/h, **always on**: about $584 a month at 730 h. It replaces the `a10g-small` that slept when idle.
  - **Model:** unchanged, `Qwen/Qwen2.5-VL-7B-Instruct-AWQ` at the pinned revision `536a35794df8831aa814970ee8f89eff577e7718`. It is Apache-2.0, and its AWQ kernels run on Ada.
  - **Trade-off:** the L4 has about half the A10G's memory bandwidth, so expect lower tokens per second. The capacity check below measures this rather than assuming it.
- **Launch capacity target:** **10 concurrent users** (spec §8 decision 3).

## Global Constraints

- **No ADR-030 rule changes.** `hfspace` stays rented compute running our own weights: not third-party, with a ceiling of `sensitive`.
- **The deploy may not sleep the Space.** The workflow sets hardware `vars.HF_SPACE_HARDWARE` (default `l4x1`) and sleep time **-1 (never)**, and fails if Hugging Face refuses either. It never touches Railway.
- **The gateway's own probing.** Since the Space no longer sleeps, `AURA_PROVIDER_HFSPACE_HEALTH_PROBE` goes back to `active`. Readiness then calls the Space, and a dead Space shows as unhealthy within one probe. This is an owner step on Railway; the docs say so.
- **The verifier spends little.**
  - It sends one document turn and `AURA_VERIFY_CONCURRENCY` (default 10) short turns.
  - It needs an operator or Pro account token, because a Free account's monthly limit would refuse the turns.
  - It deletes the document it created.
  - It never prints a token or the Space key.
- **Evidence rules.**
  - Exit code = the number of failed checks.
  - A check that cannot run because a credential was not given is **SKIP** and counts as not proven, never as a pass.
  - Until a report with zero FAIL and zero SKIP is committed, readiness items 2, 3, 6 and 9 are built, not proven.
- **The default `pytest` stays offline.** The verifier test uses a loopback `http.server` only.

## Review Focus

1. **The Space answers while unauthenticated.** That is free GPU for anyone, so check 1 must FAIL, not just warn. Test: the fake Space accepts a request with no key, and check 1 fails.
2. **`/health/ready` reports `hfspace` from the circuit breaker (`"probe": "passive"`) while the Space is down.** Healthy-by-default must not pass check 3. Test: the fake says `healthy: true, probe: passive`, and check 3 fails with "passive".
3. **The document turn is answered by another provider.** If `hosted1` answered, a sensitive document reached a third party, or the answer did not come from the GPU. Test: the fake gateway answers with `provider: hosted1`, and check 6 fails.
4. **The capacity run partly fails.** Some 429s or 5xx under 10 concurrent turns mean capacity is not met. Test: the fake refuses 2 of 10 with 503, and check 7 fails and names the count.
5. **The document is left behind after a failed check 6.** Test: the fake answers the wrong code, and the DELETE for that document was still sent.

---

### Task 1: Space hardware and sleep as code, docs for the L4

**Files:**
- Modify:
  - `.github/workflows/inference-plane-deploy.yml`: in the deploy job, after `upload_folder`, call `HfApi().request_space_hardware(repo_id, hardware)` with `hardware = vars.HF_SPACE_HARDWARE or "l4x1"`, then `HfApi().set_space_sleep_time(repo_id, -1)`. An exception fails the job.
  - `deploy/spaces/inference-vllm/README.md`: the role changes from "failover" to the always-on sensitive-capable source, on an L4.
  - `docs/deployment/inference-plane.md`: §8 records the 2026-11-18 decision. The sleeping-Space bullet list moves under "Before 2026-11-18". The health probe is `active`, persistent storage `HF_HOME=/data/hf` stays recommended, and the owner steps are listed in order.
- Test: `aura_backend/tests/deployment/test_inference_contract.py`, with a new test `test_deploy_keeps_the_space_on_the_owner_gpu_and_awake`. It parses the workflow YAML and asserts:
  - the deploy job's script contains `request_space_hardware` and `set_space_sleep_time(` with `-1`;
  - the default hardware string is `l4x1`.

- [ ] Write the test and run it: it fails.
- [ ] Implement the workflow change and the docs.
- [ ] Run `pytest tests/deployment/test_inference_contract.py -q`: it passes.
- [ ] Commit.

### Task 2: The readiness verifier

**Files:**
- Create: `deploy/scripts/verify_gpu_source.py`
- Test: `aura_backend/tests/unit/test_gpu_source_verifier.py`

**Configuration** (environment variables, read in `main()`, not at import, so the test can set them):

| Variable | Meaning |
|---|---|
| `AURA_VERIFY_GATEWAY` | e.g. `https://api.aureon-ai.tech`. Required |
| `AURA_VERIFY_TOKEN` | a user access token (operator or Pro). Required for checks 6–7 |
| `AURA_VERIFY_EXTRA_TOKENS` | optional, comma-separated. Spreads check 7 over accounts so the 10/min chat limit is not the thing measured |
| `AURA_VERIFY_ADMIN_TOKEN` | optional, SuperAdmin/Admin; check 5 |
| `AURA_VERIFY_SPACE_URL`, `AURA_VERIFY_SPACE_KEY` | checks 1 |
| `AURA_VERIFY_HF_TOKEN`, `AURA_VERIFY_SPACE_ID`, `AURA_VERIFY_HF_API` (default `https://huggingface.co`) | check 2 |
| `AURA_VERIFY_EXPECT_HARDWARE` | default `l4x1` |
| `AURA_VERIFY_CONCURRENCY` | default `10` |
| `AURA_VERIFY_P95_S` | default `60` |
| `AURA_VERIFY_REPORT_DIR` | default `docs/reports` |

**Interfaces:**
- `main() -> int` returns the number of failed checks, capped at 125.
- The report is `readiness-gpu-<YYYYMMDDTHHMMSSZ>.txt`. Each line is `PASS|FAIL|SKIP  N. name`, plus details, latencies and the configuration (with no secrets).

**Checks:**
1. **The Space is authenticated and serves the pinned model.**
   - `GET {space}/v1/models` with no key must be 401 or 403.
   - With the key it must be 200 and list a model, which is recorded.
2. **The Space runs on the chosen GPU and never sleeps** (Spec §6.1 #9, "always on").
   - `GET {hf_api}/api/spaces/{id}/runtime` with the token.
   - `hardware.current == expected` and `stage == "RUNNING"`.
   - `gcTimeout` is null or ≤ 0.
3. **The gateway reports `hfspace` healthy from a real probe.** In `GET /health/ready`, `providers.hfspace.healthy` is true, the probe is not `passive`, `healthy` is true, and `checks.schema.healthy` is true.
4. **Operator surfaces are not public** (§6.1 #3). `/metrics`, `/docs`, `/redoc` and `/openapi.json`, called without credentials, are each 401, 403 or 404.
5. **No training-permitted model can serve customers** (§6.1 #2). `GET /api/v3/admin/status` with the admin token: every provider has `may_train_on_prompts` false.
6. **A document turn is served end to end by the GPU** (§6.1 #9).
   - Ingest a document with a random 6-digit code.
   - `POST /api/v3/chat` with `use_knowledge: true` and the question.
   - The answer contains the code, and `provider == "hfspace"`.
   - DELETE the document in a `finally`.
7. **Capacity at the launch target** (§6.1 #6). Send N concurrent `/chat` turns, each with `use_knowledge: false` and `max_output_tokens: 120`, through a `ThreadPoolExecutor`.
   - It passes when every turn is 200 and the p95 latency is at most `AURA_VERIFY_P95_S`.
   - It records p50, p95 and max, and the providers that answered.
   - With a single token it waits 61 s first, so the chat limit's window is clear.

**Steps:**
- [ ] **Write the test.** A `FakeWorld` is a `ThreadingHTTPServer` on 127.0.0.1 serving the gateway, the Space and the HF API paths from a mutable dict of behaviours. It loads the driver with `importlib` (as `test_phase6_verifier.py` does) and sets the environment with `monkeypatch`. The 61 s wait is patched out by giving extra tokens. Tests:
  - `test_all_checks_pass_against_a_healthy_world`: exit 0, and the report says `PASS` × 7.
  - `test_an_open_space_fails_check_1` (Review Focus 1).
  - `test_a_passive_probe_is_not_proof_of_health` (Review Focus 2).
  - `test_a_document_answered_by_another_provider_fails` (Review Focus 3).
  - `test_partial_failures_under_load_fail_capacity` (Review Focus 4).
  - `test_the_document_is_deleted_even_when_the_check_fails` (Review Focus 5).
  - `test_missing_credentials_are_skip_not_pass`: no admin token and no HF token make checks 2 and 5 SKIP, and the exit is 0 failures, but the report's summary line says `NOT PROVEN`.
- [ ] Run it: it fails, because there is no driver yet.
- [ ] Implement.
- [ ] Run the test, then the full `pytest`.
- [ ] Commit.

### Task 3: Runbook, status and the reachability matrix

- [ ] **`docs/deployment/inference-plane.md`: a new §9, "Always-on L4 (owner steps)".** The steps, in order:
  1. Space → Settings → Hardware `L4`, Sleep time `never`. The workflow also enforces this from the next deploy.
  2. Persistent storage plus `HF_HOME=/data/hf`.
  3. Wait for `/v1/models`.
  4. On Railway, set `AURA_PROVIDER_HFSPACE_HEALTH_PROBE=active` and redeploy.
  5. Run the verifier and commit the report.
- [ ] **Status files.** `docs/status/BACKEND_STATUS.md` gets a GPU-source row, and readiness items 2, 3, 6 and 9 read "verifier built; not proven until a report is committed". Record the live facts observed on 2026-11-18:
  - `hfspace` was HTTP 503 and `hosted1` healthy;
  - `/metrics`, `/docs` and `/openapi.json` returned 404 on `api.aureon-ai.tech`;
  - the schema revision was `0023`.
- [ ] **Commit.**
