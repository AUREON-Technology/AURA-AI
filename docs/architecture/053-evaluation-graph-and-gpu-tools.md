# ADR-053 / 054 / 055: evaluation scoring, knowledge-graph reading and GPU status as agent tools

Status: Proposed (built and tested; no GPU exists on any machine used, so ADR-055's probe was exercised against a real
executable that prints nvidia-smi's documented output, not against a device).

## ADR-053 Evaluation scoring (`tools/eval_tools.py`)
`score_retrieval` (recall/precision/MRR/nDCG, the harness's own functions), `score_answer` (token-F1, required-term
coverage, lexical grounding overlap), `compute_wer` (word error rate, Arabic-safe) and `compare_to_baseline` (the
regression gate's tolerance rule over supplied numbers). Pure and bounded: no I/O, no model call. They are *screening
aids*: `score_answer` is lexical and says so. `LLMJudge` and `AgentEvaluator` stay out of the tool surface (they cost
money and drive the live kernel); running a suite stays an operator/CI action.

## ADR-054 Knowledge-graph reading (`tools/graph_tools.py`)
`graph_find_entities`, `graph_neighbours` (<= 2 hops), `graph_path` (<= 4 hops, <= 200 visited entities). The owner comes
from the turn's context (`knowledge_owner` or the user), never from an argument, so another user's graph is unreachable
(tested with two owners on the real in-memory store). Read-only on purpose: the graph is written by the ingestion pipeline,
which attaches provenance and calibrates confidence (ADR-005); an agent asserting facts through a tool would bypass that,
so agent-proposed facts are a separate confirmation-gated decision. Results are `untrusted_content` (document-derived).
Granted to Engineer and Research, not the coding agent.

## ADR-055 GPU status (`observability/gpu.py`, `adapters/gpu_probe.py`, tool `gpu_status`)
A pure nvidia-smi parser (unknown fields are unknown, not zero; non-CSV output is an error, never "no GPUs"), a
scrape-time probe that sets the already-declared `gpu_memory_used_bytes` / `gpu_utilisation_ratio` gauges (cached 15 s,
bounded, absent binary = unavailable and gauges stay unset), and `gpu_status`, which runs the same query where the sandbox
runs (the paired desktop or the server). **It reports; it cannot reserve or allocate a GPU.** What "allocation" could
honestly mean here is an advisory reservation ledger or `CUDA_VISIBLE_DEVICES` for a job the platform itself launches;
neither is built, and `gpu_resource_scheduler` stays unregistered (ADR-036).

## Evidence
`tests/unit/test_eval_tools.py`, `test_graph_tools.py`, `test_gpu_status.py`.
