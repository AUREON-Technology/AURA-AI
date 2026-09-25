# Phase 3 load harness - self-validation (echo provider, in-memory adapters; NOT a capacity result)

Run 2026-09-24 22:32:27 UTC against target `11abb50b3ef0017f3887dede9231a1aa976af783 (in-process: the harness's own checkout)` (harness `11abb50b3ef0`, v1.0), gateway `http://aura-inprocess`, model `echo/echo-1`, hardware: load-generator host itself (in-process; 4 vCPU sandbox container). Produced by `aura_backend/scripts/loadtest` (runbook: [`docs/guides/load-testing.md`](../guides/load-testing.md)).

## Status

- **ECHO PROVIDER / IN-PROCESS RUN: these numbers measure the harness and the pipeline around a model that answers instantly. They are NOT a capacity measurement.**
- In-process ASGI transport buffers each response, so TTFT here is ~ the full response time; real TTFT needs a real network stream.

- **A - normal chat (short prompt + RAG)**: every evaluated threshold passed up to **50** users; no saturation knee inside the matrix.
- **non-LLM API (list/search/memory/agents)**: every evaluated threshold passed up to **50** users; no saturation knee inside the matrix.
- **F - mixed workload**: every evaluated threshold passed up to **50** users; saturation knee at **30** users (30 -> 40 users: goodput 54.2 -> 59.5/s (+10%), p95 latency 519 -> 662 ms (+27%)).

> Capacity is accepted only from a run of this harness against the target hardware, with the target model, recorded with its run conditions. A number from any other host, model or provider -- including every echo-provider or in-process run -- is a harness check, not a capacity figure.

## Run conditions

| | |
| --- | --- |
| Date (UTC) | 2026-09-24 22:32:27 UTC -> 2026-09-24 22:35:57 UTC |
| Target git SHA | `11abb50b3ef0017f3887dede9231a1aa976af783 (in-process: the harness's own checkout)` |
| Harness git SHA | `11abb50b3ef0017f3887dede9231a1aa976af783` |
| Gateway URL (redacted) | `http://aura-inprocess` |
| Metrics URL | `/metrics` |
| Target hardware | load-generator host itself (in-process; 4 vCPU sandbox container) |
| Model | `echo/echo-1` (probe: echo/echo-1) |
| Environment | local |
| In-process (ASGI) | yes |
| Identities | mint (HS256, role=User, users loadtest-vu-NNN, ttl 3600s, one identity per VU) |
| Load generator | Linux-6.18.44-fc-v37-x86_64-with-glibc2.39, Python 3.11.15, 4 CPUs |
| Matrix | 1, 2, 5, 10, 20, 30, 40, 50 |
| Step shape | ramp 2.0 s, steady 6.0 s, think 0.2-0.5 s, cooldown 1.0 s |
| Output tokens / timeouts | max_output_tokens 256; turn timeout 300.0 s, API timeout 30.0 s; retries 0 |
| Voice audio | - |
| `provider` | echo (in-memory container) |
| `rate_limiter` | not wired in-process |
| `harness_tree` | uncommitted Phase 3 harness on top of 11abb50 |

Preflight: `/health/ready` 200, providers {'echo': True}, voice {'tts': None, 'stt': 'faster-whisper-tiny.en', 'live': False}, workspaces False. Setup: {'identities': 50, 'failed': 0, 'seconds': 2.98, 'first_error': None}.

## The measured numbers

Per step: RPS/RPM count HTTP requests *started* in the steady window; goodput counts successful scenario iterations; TTFT is time to the first `token` frame; E2E is the whole scenario iteration (all of its requests, retries included); tok/s is `done.usage.completion_tokens` over the post-first-token span, where the stream reports it.

### A - normal chat (short prompt + RAG)

| Users | Req | RPS | Req/min | Goodput/s | TTFT p50 | TTFT p95 | TTFT p99 | E2E p50 | E2E p95 | E2E p99 | tok/s p50 | Err % | 5xx-class % | Retries |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 16 | 2.67 | 160 | 2.67 | 12 ms | 15 ms | 15 ms | 12 ms | 15 ms | 16 ms | - | 0.0% | 0.0% | 0 |
| 2 | 34 | 5.67 | 340 | 5.67 | 9 ms | 12 ms | 15 ms | 9 ms | 13 ms | 15 ms | - | 0.0% | 0.0% | 0 |
| 5 | 83 | 13.83 | 830 | 13.83 | 5 ms | 12 ms | 18 ms | 5 ms | 12 ms | 18 ms | - | 0.0% | 0.0% | 0 |
| 10 | 171 | 28.50 | 1710 | 28.50 | 5 ms | 12 ms | 16 ms | 5 ms | 13 ms | 16 ms | - | 0.0% | 0.0% | 0 |
| 20 | 333 | 55.50 | 3330 | 55.50 | 5 ms | 13 ms | 19 ms | 5 ms | 14 ms | 19 ms | - | 0.0% | 0.0% | 0 |
| 30 | 508 | 84.67 | 5080 | 84.67 | 5 ms | 19 ms | 43 ms | 5 ms | 20 ms | 46 ms | - | 0.0% | 0.0% | 0 |
| 40 | 669 | 111.50 | 6690 | 111.50 | 5 ms | 14 ms | 24 ms | 5 ms | 16 ms | 29 ms | - | 0.0% | 0.0% | 0 |
| 50 | 804 | 134.00 | 8040 | 134.00 | 6 ms | 73 ms | 180 ms | 6 ms | 75 ms | 185 ms | - | 0.0% | 0.0% | 0 |

Errors by class (steady-state requests):

| Users | 4xx | 5xx | overload_429 | unavailable_503 | timeout | connection | blocked | Top details |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | - |
| 2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | - |
| 5 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | - |
| 10 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | - |
| 20 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | - |
| 30 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | - |
| 40 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | - |
| 50 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | - |

### non-LLM API (list/search/memory/agents)

| Users | Req | RPS | Req/min | Goodput/s | TTFT p50 | TTFT p95 | TTFT p99 | E2E p50 | E2E p95 | E2E p99 | tok/s p50 | Err % | 5xx-class % | Retries |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 18 | 3.00 | 180 | 3.00 | - | - | - | 2 ms | 9 ms | 9 ms | - | 0.0% | 0.0% | 0 |
| 2 | 33 | 5.50 | 330 | 5.50 | - | - | - | 2 ms | 6 ms | 8 ms | - | 0.0% | 0.0% | 0 |
| 5 | 83 | 13.83 | 830 | 13.83 | - | - | - | 2 ms | 7 ms | 9 ms | - | 0.0% | 0.0% | 0 |
| 10 | 176 | 29.33 | 1760 | 29.33 | - | - | - | 2 ms | 3 ms | 9 ms | - | 0.0% | 0.0% | 0 |
| 20 | 335 | 55.83 | 3350 | 55.83 | - | - | - | 1 ms | 3 ms | 8 ms | - | 0.0% | 0.0% | 0 |
| 30 | 508 | 84.67 | 5080 | 84.67 | - | - | - | 1 ms | 3 ms | 4 ms | - | 0.0% | 0.0% | 0 |
| 40 | 684 | 114.00 | 6840 | 114.00 | - | - | - | 1 ms | 3 ms | 4 ms | - | 0.0% | 0.0% | 0 |
| 50 | 859 | 143.17 | 8590 | 143.17 | - | - | - | 1 ms | 3 ms | 6 ms | - | 0.0% | 0.0% | 0 |

Errors by class (steady-state requests):

| Users | 4xx | 5xx | overload_429 | unavailable_503 | timeout | connection | blocked | Top details |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | - |
| 2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | - |
| 5 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | - |
| 10 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | - |
| 20 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | - |
| 30 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | - |
| 40 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | - |
| 50 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | - |

Per request type (p95 request latency / p95 TTFT / count):

| Users | `api/list_agents` | `api/list_documents` | `api/list_memory` | `api/search` |
| --- | --- | --- | --- | --- |
| 1 | 2 ms / n=8 | 2 ms / n=2 | 2 ms / n=5 | 9 ms / n=3 |
| 2 | 2 ms / n=9 | 2 ms / n=7 | 2 ms / n=9 | 8 ms / n=8 |
| 5 | 2 ms / n=23 | 3 ms / n=20 | 2 ms / n=22 | 9 ms / n=18 |
| 10 | 2 ms / n=45 | 2 ms / n=39 | 2 ms / n=39 | 9 ms / n=53 |
| 20 | 2 ms / n=84 | 2 ms / n=86 | 2 ms / n=87 | 8 ms / n=78 |
| 30 | 2 ms / n=134 | 2 ms / n=144 | 2 ms / n=104 | 4 ms / n=126 |
| 40 | 2 ms / n=161 | 2 ms / n=184 | 2 ms / n=172 | 4 ms / n=167 |
| 50 | 2 ms / n=194 | 2 ms / n=207 | 2 ms / n=231 | 5 ms / n=227 |

### F - mixed workload

Traffic mix (weights, normalised over available scenarios): {'chat': 50.0, 'long_context': 10.0, 'agent': 15.0, 'api': 20.0}.
Dropped from the mix: voice (weight 5): target has no TTS engine configured.

| Users | Req | RPS | Req/min | Goodput/s | TTFT p50 | TTFT p95 | TTFT p99 | E2E p50 | E2E p95 | E2E p99 | tok/s p50 | Err % | 5xx-class % | Retries |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 18 | 3.00 | 180 | 3.00 | 16 ms | 161 ms | 163 ms | 14 ms | 163 ms | 166 ms | - | 0.0% | 0.0% | 0 |
| 2 | 31 | 5.17 | 310 | 5.17 | 10 ms | 75 ms | 94 ms | 10 ms | 60 ms | 97 ms | - | 0.0% | 0.0% | 0 |
| 5 | 79 | 13.17 | 790 | 13.17 | 9 ms | 102 ms | 127 ms | 8 ms | 105 ms | 129 ms | - | 0.0% | 0.0% | 0 |
| 10 | 157 | 26.17 | 1570 | 26.17 | 7 ms | 127 ms | 181 ms | 6 ms | 113 ms | 180 ms | - | 0.0% | 0.0% | 0 |
| 20 | 244 | 40.67 | 2440 | 40.67 | 89 ms | 356 ms | 470 ms | 53 ms | 380 ms | 473 ms | 113.8 | 0.0% | 0.0% | 0 |
| 30 | 325 | 54.17 | 3250 | 54.17 | 116 ms | 496 ms | 553 ms | 78 ms | 519 ms | 575 ms | 199.1 | 0.0% | 0.0% | 0 |
| 40 | 357 | 59.50 | 3570 | 59.50 | 265 ms | 613 ms | 748 ms | 191 ms | 662 ms | 762 ms | 181.1 | 0.0% | 0.0% | 0 |
| 50 | 501 | 83.50 | 5010 | 83.50 | 248 ms | 566 ms | 667 ms | 208 ms | 546 ms | 704 ms | 164.9 | 0.0% | 0.0% | 0 |

Errors by class (steady-state requests):

| Users | 4xx | 5xx | overload_429 | unavailable_503 | timeout | connection | blocked | Top details |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | - |
| 2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | - |
| 5 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | - |
| 10 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | - |
| 20 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | - |
| 30 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | - |
| 40 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | - |
| 50 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | - |

Per request type (p95 request latency / p95 TTFT / count):

| Users | `agent/chat_stream` | `api/list_agents` | `api/list_documents` | `api/list_memory` | `api/search` | `chat/chat_stream` | `long_context/chat_stream` |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 20 ms / 20 ms / n=3 | 2 ms / n=2 | 4 ms / n=1 | - | 8 ms / n=1 | 18 ms / 18 ms / n=9 | 166 ms / 163 ms / n=2 |
| 2 | 22 ms / 22 ms / n=8 | 2 ms / n=3 | 2 ms / n=1 | - | 2 ms / n=1 | 13 ms / 13 ms / n=16 | 97 ms / 94 ms / n=2 |
| 5 | 36 ms / 35 ms / n=15 | 3 ms / n=4 | 2 ms / n=1 | 2 ms / n=2 | 4 ms / n=3 | 43 ms / 42 ms / n=48 | 148 ms / 141 ms / n=6 |
| 10 | 43 ms / 43 ms / n=14 | 38 ms / n=11 | 74 ms / n=16 | 3 ms / n=7 | 12 ms / n=13 | 85 ms / 81 ms / n=83 | 223 ms / 216 ms / n=13 |
| 20 | 365 ms / 356 ms / n=36 | 31 ms / n=11 | 79 ms / n=17 | 18 ms / n=12 | 103 ms / n=14 | 367 ms / 311 ms / n=121 | 471 ms / 458 ms / n=33 |
| 30 | 546 ms / 515 ms / n=50 | 203 ms / n=18 | 220 ms / n=17 | 134 ms / n=18 | 152 ms / n=21 | 456 ms / 432 ms / n=168 | 587 ms / 566 ms / n=33 |
| 40 | 707 ms / 668 ms / n=53 | 190 ms / n=18 | 110 ms / n=20 | 117 ms / n=13 | 201 ms / n=12 | 612 ms / 596 ms / n=199 | 784 ms / 751 ms / n=42 |
| 50 | 658 ms / 627 ms / n=89 | 144 ms / n=32 | 158 ms / n=22 | 118 ms / n=31 | 241 ms / n=29 | 530 ms / 448 ms / n=256 | 788 ms / 744 ms / n=42 |

Scenario counters: 1 users {'agent.step_frames': 0, 'agent.turns_without_steps': 3}; 2 users {'agent.step_frames': 0, 'agent.turns_without_steps': 11}; 5 users {'agent.step_frames': 0, 'agent.turns_without_steps': 20}; 10 users {'agent.step_frames': 0, 'agent.turns_without_steps': 17}; 20 users {'agent.step_frames': 0, 'agent.turns_without_steps': 43}; 30 users {'agent.step_frames': 0, 'agent.turns_without_steps': 59}; 40 users {'agent.step_frames': 0, 'agent.turns_without_steps': 68}; 50 users {'agent.step_frames': 0, 'agent.turns_without_steps': 107}.

## Launch thresholds

Proposed launch thresholds: non-LLM API p95 < 500.0 ms / p99 < 1000.0 ms; TTFT p95 < 2500.0 ms and ordinary response p95 < 15000.0 ms (scenario A only); 5xx-class < 1.0% (5xx, 503, timeouts, dropped connections, mid-stream server errors -- not 429s); bounded LLM queue. `NO DATA` = no samples of that population in the step; `NOT EVALUATED` = the signal was not available (e.g. /metrics).

### A - normal chat (short prompt + RAG)

| Users | Non-LLM API p95 | Non-LLM API p99 | TTFT p95 | Ordinary response p95 | 5xx rate | Bounded queue |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | NO DATA (no samples) | NO DATA (no samples) | **PASS** (15 ms) | **PASS** (15 ms) | **PASS** (0.00% (0/16)) | NOT EVALUATED (aura_llm_queue_depth is declared but has no series (never set by the gateway)) |
| 2 | NO DATA (no samples) | NO DATA (no samples) | **PASS** (12 ms) | **PASS** (13 ms) | **PASS** (0.00% (0/34)) | NOT EVALUATED (aura_llm_queue_depth is declared but has no series (never set by the gateway)) |
| 5 | NO DATA (no samples) | NO DATA (no samples) | **PASS** (12 ms) | **PASS** (12 ms) | **PASS** (0.00% (0/83)) | NOT EVALUATED (aura_llm_queue_depth is declared but has no series (never set by the gateway)) |
| 10 | NO DATA (no samples) | NO DATA (no samples) | **PASS** (12 ms) | **PASS** (13 ms) | **PASS** (0.00% (0/171)) | NOT EVALUATED (aura_llm_queue_depth is declared but has no series (never set by the gateway)) |
| 20 | NO DATA (no samples) | NO DATA (no samples) | **PASS** (13 ms) | **PASS** (14 ms) | **PASS** (0.00% (0/333)) | NOT EVALUATED (aura_llm_queue_depth is declared but has no series (never set by the gateway)) |
| 30 | NO DATA (no samples) | NO DATA (no samples) | **PASS** (19 ms) | **PASS** (20 ms) | **PASS** (0.00% (0/508)) | NOT EVALUATED (aura_llm_queue_depth is declared but has no series (never set by the gateway)) |
| 40 | NO DATA (no samples) | NO DATA (no samples) | **PASS** (14 ms) | **PASS** (16 ms) | **PASS** (0.00% (0/669)) | NOT EVALUATED (aura_llm_queue_depth is declared but has no series (never set by the gateway)) |
| 50 | NO DATA (no samples) | NO DATA (no samples) | **PASS** (73 ms) | **PASS** (75 ms) | **PASS** (0.00% (0/804)) | NOT EVALUATED (aura_llm_queue_depth is declared but has no series (never set by the gateway)) |

### non-LLM API (list/search/memory/agents)

| Users | Non-LLM API p95 | Non-LLM API p99 | TTFT p95 | Ordinary response p95 | 5xx rate | Bounded queue |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | **PASS** (9 ms) | **PASS** (9 ms) | NO DATA (no samples) | NO DATA (no samples) | **PASS** (0.00% (0/18)) | NOT EVALUATED (aura_llm_queue_depth is declared but has no series (never set by the gateway)) |
| 2 | **PASS** (6 ms) | **PASS** (8 ms) | NO DATA (no samples) | NO DATA (no samples) | **PASS** (0.00% (0/33)) | NOT EVALUATED (aura_llm_queue_depth is declared but has no series (never set by the gateway)) |
| 5 | **PASS** (7 ms) | **PASS** (9 ms) | NO DATA (no samples) | NO DATA (no samples) | **PASS** (0.00% (0/83)) | NOT EVALUATED (aura_llm_queue_depth is declared but has no series (never set by the gateway)) |
| 10 | **PASS** (3 ms) | **PASS** (9 ms) | NO DATA (no samples) | NO DATA (no samples) | **PASS** (0.00% (0/176)) | NOT EVALUATED (aura_llm_queue_depth is declared but has no series (never set by the gateway)) |
| 20 | **PASS** (3 ms) | **PASS** (8 ms) | NO DATA (no samples) | NO DATA (no samples) | **PASS** (0.00% (0/335)) | NOT EVALUATED (aura_llm_queue_depth is declared but has no series (never set by the gateway)) |
| 30 | **PASS** (3 ms) | **PASS** (4 ms) | NO DATA (no samples) | NO DATA (no samples) | **PASS** (0.00% (0/508)) | NOT EVALUATED (aura_llm_queue_depth is declared but has no series (never set by the gateway)) |
| 40 | **PASS** (3 ms) | **PASS** (4 ms) | NO DATA (no samples) | NO DATA (no samples) | **PASS** (0.00% (0/684)) | NOT EVALUATED (aura_llm_queue_depth is declared but has no series (never set by the gateway)) |
| 50 | **PASS** (3 ms) | **PASS** (6 ms) | NO DATA (no samples) | NO DATA (no samples) | **PASS** (0.00% (0/859)) | NOT EVALUATED (aura_llm_queue_depth is declared but has no series (never set by the gateway)) |

### F - mixed workload

| Users | Non-LLM API p95 | Non-LLM API p99 | TTFT p95 | Ordinary response p95 | 5xx rate | Bounded queue |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | **PASS** (7 ms) | **PASS** (8 ms) | **PASS** (18 ms) | **PASS** (18 ms) | **PASS** (0.00% (0/18)) | NOT EVALUATED (aura_llm_queue_depth is declared but has no series (never set by the gateway)) |
| 2 | **PASS** (2 ms) | **PASS** (2 ms) | **PASS** (13 ms) | **PASS** (13 ms) | **PASS** (0.00% (0/31)) | NOT EVALUATED (aura_llm_queue_depth is declared but has no series (never set by the gateway)) |
| 5 | **PASS** (4 ms) | **PASS** (4 ms) | **PASS** (42 ms) | **PASS** (43 ms) | **PASS** (0.00% (0/79)) | NOT EVALUATED (aura_llm_queue_depth is declared but has no series (never set by the gateway)) |
| 10 | **PASS** (53 ms) | **PASS** (88 ms) | **PASS** (81 ms) | **PASS** (85 ms) | **PASS** (0.00% (0/157)) | NOT EVALUATED (aura_llm_queue_depth is declared but has no series (never set by the gateway)) |
| 20 | **PASS** (87 ms) | **PASS** (106 ms) | **PASS** (311 ms) | **PASS** (367 ms) | **PASS** (0.00% (0/244)) | NOT EVALUATED (aura_llm_queue_depth is declared but has no series (never set by the gateway)) |
| 30 | **PASS** (206 ms) | **PASS** (237 ms) | **PASS** (432 ms) | **PASS** (456 ms) | **PASS** (0.00% (0/325)) | NOT EVALUATED (aura_llm_queue_depth is declared but has no series (never set by the gateway)) |
| 40 | **PASS** (183 ms) | **PASS** (227 ms) | **PASS** (596 ms) | **PASS** (612 ms) | **PASS** (0.00% (0/357)) | NOT EVALUATED (aura_llm_queue_depth is declared but has no series (never set by the gateway)) |
| 50 | **PASS** (192 ms) | **PASS** (249 ms) | **PASS** (448 ms) | **PASS** (530 ms) | **PASS** (0.00% (0/501)) | NOT EVALUATED (aura_llm_queue_depth is declared but has no series (never set by the gateway)) |

## Saturation

Knee = the last step before throughput (goodput/s) stopped growing by at least 10% while E2E p95 rose by at least 10% -- beyond it, extra users only queue.

- **A - normal chat (short prompt + RAG)**: throughput kept scaling (or latency did not rise) across the whole matrix -- no knee found; extend the matrix to find it.
- **non-LLM API (list/search/memory/agents)**: throughput kept scaling (or latency did not rise) across the whole matrix -- no knee found; extend the matrix to find it.
- **F - mixed workload**: knee at **30** users, saturated at 40 -- 30 -> 40 users: goodput 54.2 -> 59.5/s (+10%), p95 latency 519 -> 662 ms (+27%).

## Server-side metrics

| Run | Users | Queue depth max / mean | Turns in flight max | LLM in-flight max | Server TTFT mean | Server turn mean | LLM calls by outcome | Rate-limit rejections | Fallbacks | Circuit opens |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| chat | 1 | - / - | 0 | 0 | 0 ms | - | {'success': 18.0} | - | - | - |
| chat | 2 | - / - | 0 | 0 | 0 ms | - | {'success': 49.0} | - | - | - |
| chat | 5 | - / - | 0 | 1 | 0 ms | - | {'success': 115.0} | - | - | - |
| chat | 10 | - / - | 0 | 1 | 0 ms | - | {'success': 232.0} | - | - | - |
| chat | 20 | - / - | 0 | 0 | 0 ms | - | {'success': 452.0} | - | - | - |
| chat | 30 | - / - | 0 | 1 | 1 ms | - | {'success': 678.0} | - | - | - |
| chat | 40 | - / - | 0 | 0 | 0 ms | - | {'success': 900.0} | - | - | - |
| chat | 50 | - / - | 0 | 8 | 2 ms | - | {'success': 1088.0} | - | - | - |
| api | 1 | - / - | 0 | 0 | - | - | - | - | - | - |
| api | 2 | - / - | 0 | 0 | - | - | - | - | - | - |
| api | 5 | - / - | 0 | 0 | - | - | - | - | - | - |
| api | 10 | - / - | 0 | 0 | - | - | - | - | - | - |
| api | 20 | - / - | 0 | 0 | - | - | - | - | - | - |
| api | 30 | - / - | 0 | 0 | - | - | - | - | - | - |
| api | 40 | - / - | 0 | 0 | - | - | - | - | - | - |
| api | 50 | - / - | 0 | 0 | - | - | - | - | - | - |
| mixed | 1 | - / - | 0 | 0 | 0 ms | - | {'success': 20.0} | - | - | - |
| mixed | 2 | - / - | 0 | 0 | 0 ms | - | {'success': 51.0} | - | - | - |
| mixed | 5 | - / - | 0 | 1 | 1 ms | - | {'success': 120.0} | - | - | - |
| mixed | 10 | - / - | 0 | 0 | 3 ms | - | {'success': 179.0} | - | - | - |
| mixed | 20 | - / - | 0 | 9 | 20 ms | - | {'success': 330.0} | - | - | - |
| mixed | 30 | - / - | 0 | 9 | 29 ms | - | {'success': 434.0} | - | - | - |
| mixed | 40 | - / - | 0 | 13 | 53 ms | - | {'success': 489.0} | - | - | - |
| mixed | 50 | - / - | 0 | 8 | 42 ms | - | {'success': 666.0} | - | - | - |

| Signal | Exported by the gateway |
| --- | --- |
| LLM queue wait (aura_llm_queue_depth) | declared, never set |
| LLM in-flight generations (aura_llm_inflight) | yes |
| Turns in flight (aura_turns_in_flight) | yes |
| Server-side TTFT (aura_llm_ttft_seconds) | yes |
| DB connection pool | no |
| Celery queue depth | no |

`declared, never set` means the family has a `# TYPE` line but no series: it looks available on a dashboard and measures nothing. `no` means the gateway does not export it at all. Neither is measured by this run. Note also that `aura_turns_in_flight` is maintained only by `AIKernel.run` (`POST /chat`), not by `AIKernel.stream` (`/chat/stream`, what every scenario here uses), so it reads ~0 under streamed load; `aura_llm_inflight` is the in-flight signal to read.

## What this does and does not show

- It shows how *this* deployment -- the hardware, model, provider settings and limits in the run-conditions table -- behaved under the recorded matrix. Change any of those and the numbers no longer apply.
- Closed-loop virtual users with think time: offered load is bounded by the user count, so a saturated system shows rising latency and flat throughput rather than an unbounded backlog. The knee and the threshold table are where to read capacity from.
- 429s are the gateway's (or nginx's) rate limiting and are reported apart from server failures; a step dominated by them measured the limiter, not capacity.
- Capacity is accepted only from a run of this harness against the target hardware, with the target model, recorded with its run conditions. A number from any other host, model or provider -- including every echo-provider or in-process run -- is a harness check, not a capacity figure.
