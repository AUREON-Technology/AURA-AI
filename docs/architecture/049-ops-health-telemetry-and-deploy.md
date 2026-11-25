# ADR-049: Operations tools — platform health, live telemetry, trace analysis, workflow deploys

Status: Proposed (built; checked against the live gateway and Railway, not against a live Loki/Tempo or a real
workflow dispatch). Extends ADR-046.

## Problem

The deployment tools only planned (`plan_rollout`) and verified a caller-chosen URL (`verify_release_health`,
which refuses the platform's own hosts and `/health` on purpose); log analysis took pasted text; there was no
trace analysis; nothing could deploy, restart or roll back.

## Decisions (`tools/ops.py`, `tools/ops_deploy.py`, `tools/open/trace_ext.py`, `OpsSettings`)

Every tool registers only when the operator configured what it needs (`AURA_OPS_*`), so a default deployment
gains nothing.

1. **`check_platform_health`** probes `/health/live`, `/health/version`, `/health/ready` on the operator's own
   origins. It takes **no URL**: the origin must be a configured one and the paths are fixed in code, so a
   poisoned prompt has nothing to redirect (this is why it can exist beside `open_guarded`'s refusal). https
   only, redirects not followed, 64 KiB cap. It reports readiness, the serving commit (vs `expected_git_sha`),
   component and provider health, and whether the schema revision is the code's (the CB-43 failure).
2. **`query_telemetry`** reads Loki (LogQL), Prometheus (PromQL), Tempo (TraceQL / one trace, analysed) and
   **Railway** (recent deployments + their logs, the production target has no Loki) over their HTTP APIs, with
   bounded windows (≤24 h), limits, a 4 MiB response cap, and the existing `analyze_logs` analyzers over the
   result. URLs/ids are operator config; tokens are read from environment variables named by the operator
   (`AURA_OPS_TELEMETRY_TOKEN_ENV`, `AURA_OPS_RAILWAY_TOKEN_ENV`), never stored in settings.
3. **Trace analysis** (`trace_ext.analyze_trace`): critical path, slowest spans by self time (overlapping
   children counted once), per-service time, errors, orphans; reads Tempo/OTLP, Jaeger and flat span lists.
4. **`deploy_release` / `rollback_release` / `list_workflow_runs`** start the operator's *own* CI workflow with
   `workflow_dispatch` (the button a person presses), so every gate in that workflow still applies. Fences:
   workflow-file allowlist (`AURA_OPS_DEPLOY_WORKFLOWS=file.yml[:sha_input]`), the deployment's default
   repository only, refs from `AURA_OPS_DEPLOY_REFS` (default `main`), `side_effecting` + `CONFIDENTIAL` (forced
   confirmation) + `engineer_agent` capability + a written reason for the audit record. Rollback is a redeploy
   of an older commit, only for a workflow that declared a commit input; otherwise the answer is a revert PR.
   A restart is a redeploy of the same ref. No tool holds a Railway deploy token or an SSH key.

## Limits

* The agent cannot deploy anything the CI workflow cannot: today `control-plane-deploy.yml` has no inputs (no
  rollback by commit) and `Deploy VPS` is blocked by the host-key change (owner's decision).
* GitHub's token must be allowed to dispatch workflows (`actions: write` / `repo`).
* No Tempo in the compose stacks: `source=trace(s)` needs the operator to run one and set
  `OTEL_EXPORTER_OTLP_ENDPOINT` + `AURA_OPS_TEMPO_URL`.
* Findings from the first live run: Railway's API (Cloudflare) answers urllib's default User-Agent with error
  1010, so requests carry an explicit one; the platform's API host is `api.aureon-ai.tech` (the apex answers
  "Application not found" from Railway's router).

## Evidence

`tests/unit/test_ops_tools.py` (local servers that answer like the gateway, Loki, Prometheus, Tempo and
Railway's GraphQL; registry-level confirmation/capability; the real GitHub adapter over `MockTransport`).
Live, 2026-11-25: `check_platform_health` against `https://api.aureon-ai.tech` (healthy, schema 0033,
`hfspace` and `hosted3` down) and `query_telemetry source=railway` against the production service (five
deployments with commits and reasons).


## Update: rollback by commit on the control plane (ADR-060 session)

`control-plane-deploy.yml` now takes an optional `sha` input on `workflow_dispatch`. A named commit must
be 40 lowercase hex characters and an ancestor of `main`; the workflow checks this itself right after
checkout (the ops tool checks it before dispatching, this enforces it where the deploy runs). Set
`AURA_OPS_DEPLOY_WORKFLOWS=control-plane-deploy.yml:sha` to let `rollback_release` use it. The tested
commit on `workflow_run` still always wins. Not yet run against real GitHub Actions.
