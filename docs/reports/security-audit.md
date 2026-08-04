# Security Audit

Post-remediation status for every finding in
[`docs/analysis/06-security-report.md`](../analysis/06-security-report.md), plus the controls
added beyond them and an honest account of what remains open.

**Scope.** This audits `aura_core` and the deployment configuration under `deploy/`. It is a
code and configuration review with test-backed verification — not a penetration test and not a
third-party assessment. §5 says what that leaves unverified.

---

## 1. Status of the Phase 1 findings

| # | Finding | Severity | Status | Verified by |
| --- | --- | --- | --- | --- |
| SEC-01 | Unauthenticated model-management proxy | Critical | **Closed** | Routes removed from `main.py`; lifecycle is admin-only and allowlisted. `tests/unit/test_ollama_provider.py::TestModelLifecycle` |
| SEC-02 | Indirect prompt injection through retrieved content | Critical | **Closed** | Four independent controls, §2. `tests/security/test_prompt_injection.py` |
| SEC-03 | Destructive collection recreation on a normal failure path | Critical | **Closed** | Identity-scoped indexes, gated promotion, O(1) rollback. `tests/unit/test_in_memory_adapters.py::TestVectorStore` |
| SEC-04 | Authorisation from a mutable file on the container filesystem | High | **Closed** | Roles come from verified token claims; capabilities fail closed. `tests/unit/test_api_surface.py::TestAuthorisation` |
| SEC-05 | Debug mode disables authentication and attestation | High | **Closed** | Settings validation refuses the combination in production; `aura_insecure_mode` metric + alert. `tests/unit/test_settings_validation.py::TestProductionGate` |
| SEC-06 | No output validation or PII controls | High | **Closed** | Output guard stage: secret detection, PII, system-prompt leakage, groundedness. `tests/security/test_prompt_injection.py` |
| SEC-07 | Tool execution without authorisation, timeout, or validation | High | **Closed** | Capability grants, JSON-schema validation, timeouts, concurrency caps, audit on every attempt. `tests/unit/test_ml_specs_and_schema.py::TestArgumentValidation`, `tests/unit/test_builtin_tools.py` |
| SEC-08 | Secrets handling | Medium | **Closed** | Docker secrets as files; keys referenced by variable *name* and never stored on the settings object. `tests/unit/test_settings_validation.py::TestIntrospection` |
| SEC-09 | Rate limiting is per-IP only | Medium | **Closed** | Cost-weighted, layered per user then IP; `X-Forwarded-For` parsed from the right against a configured proxy count. `tests/unit/test_providers_and_platform.py::TestClientIpResolution` |
| SEC-10 | No audit log | Medium | **Closed** | Every tool invocation (including refusals), approval decision, model transition, and quarantine. Arguments stored as a digest. |
| SEC-11 | Attestation weaknesses | Medium | **Partially closed** | Nonce replay and clock-skew handling fixed; hardware attestation remains a client-side signal — see §4. |
| SEC-12 | Transport and network exposure | Medium | **Closed** | Nginx is the only published port; internal networks; TLS 1.2/1.3 with HSTS; `/metrics` refused publicly. |

**11 of 12 fully closed; 1 partially closed with the residual risk documented.**

## 2. SEC-02 in detail — the one worth reading

Indirect prompt injection is the finding that most shapes the architecture, so its remediation is
worth stating in full. The legacy code concatenates retrieved chunks and stored memories into the
**system instruction** (`services/agent.py:214-221`). Any document the user ingests — or that the
nightly refresh fetches — can therefore issue instructions with system authority.

There is no filter that reliably detects instruction-shaped text. The fix is structural, and it
is layered because each layer alone is defeasible:

**1. Channel separation.** Every `Message` and `ContextItem` carries a `TrustChannel`:
`SYSTEM`, `USER`, or `DATA`. Retrieved content and tool results are `DATA` by construction, not by
convention — `Message.from_tool_result` sets it, and the context engine cannot promote it.

**2. Nonce-delimited fencing.** Untrusted content is wrapped in a per-turn random fence.
Fence-like sequences inside the content are neutralised with a *visible* replacement
(`(fence)`), not zero-width padding — `EncodingGuard` strips zero-width characters, so that
escape would be undone by our own sanitiser. The nonce is per turn, so a document cannot contain
a closing fence it did not observe.

**3. Ingestion-time quarantine.** `PromptInjectionGuard` runs at `GuardStage.CONTEXT` and
`GuardStage.TOOL_RESULT`, where it returns `QUARANTINE`; at `GuardStage.INPUT` the same detection
returns only `FLAG`, because a user legitimately asking "how do prompt injections work?" must not
be blocked.

**4. Tool taint.** A turn whose context contains quarantined content marks its `ToolContext`
tainted, and a **side-effecting tool refuses to run in a tainted turn without explicit
confirmation** (`tools/policy.py`). This is the control that matters most: it breaks the chain
between "the model was influenced" and "something happened in the world."

Each layer is asserted independently in `tests/security/test_prompt_injection.py`, including the
end-to-end case — ingest a poisoned document, ask a question that retrieves it, and assert the
side-effecting tool does not fire.

## 3. Controls added beyond the findings

| Control | What it prevents |
| --- | --- |
| SSRF guard on the `fetch_url` tool | An agent with an HTTP tool becoming a proxy into the VPS's own network. HTTPS only, host allowlist, private/link-local/reserved ranges refused, redirects not followed. |
| SSRF guard on the scheduled fetcher | The same, unattended. The 02:00 job reads only from the approved-source registry; there is no path by which it fetches an arbitrary URL. |
| Calculator AST allowlist | `eval` on a model-supplied string is remote code execution, and the string can be influenced by any retrieved document. Node-type allowlist, fixed function table, exponent and factorial bounds. |
| Workspace containment after symlink resolution | Reading outside the sandbox via a symlink placed inside it — the standard bypass for a pre-resolution check. |
| Session ownership recheck on load | A guessed session id serving another user's conversation. A session id is not a capability. |
| Owner scoping enforced *before* vector scoring | A cross-tenant document displacing a legitimate one out of the top-k, silently shortening a result set. |
| Model pull allowlist, failing closed | Fetching and executing arbitrary third-party weights. An empty allowlist disables pulls rather than permitting everything. |
| Approval binding (person + time + artefact digest) | An approval granted for one dataset being reused to promote another. Approvals expire after 14 days. |
| Content-free telemetry | Prompts, documents, and answers reaching logs, metrics, or traces. Lengths, hashes, counts, and scores only. |
| `aura_insecure_mode` gauge | A security bypass being active and *invisible*, which is half of why SEC-05 survived. |

## 4. Residual risk

**SEC-11 — hardware attestation is a client-side signal.** Play Integrity and App Attest tell you
what a client claims about itself. A rooted device or a modified client can defeat them; the
protocol is not the weak part. AURA treats attestation as one input to a risk score, never as
authorisation on its own, and every authorisation decision is made server-side from verified
token claims. *This is a bound on what attestation can achieve, not a defect to fix.*

**Prompt injection is mitigated, not solved.** The four controls in §2 raise the cost
substantially and break the exploitation chain at its most consequential point. They do not make
it impossible for a model to be influenced by text it reads. The design accepts this and places
the enforcement boundary at the *action*, not at the model's reasoning — which is the only place
it can be enforced reliably.

**The audit log is append-only by convention.** Records go to Postgres with no `UPDATE` or
`DELETE` path in the application, but a database superuser can modify them. Tamper-evidence
(hash chaining, or shipping to write-once storage) is the next increment and is not implemented.

**Groundedness is a heuristic.** It uses token overlap between the answer and its cited sources.
It fails open with a metric rather than blocking, deliberately: treating a noisy proxy as an
enforcement signal produces exactly the confidently-wrong behaviour it is meant to measure.

**Denial of service is bounded, not prevented.** Rate limits, concurrency caps, timeouts, and
budgets bound what one authenticated caller can consume. A distributed attack against the public
endpoint needs an upstream defence (a CDN or a WAF); the deployment is not sized to absorb one.

## 5. What this audit does not cover

Stating this plainly matters more than the findings above, because an audit that overstates its
scope is worse than no audit.

- **No penetration test.** No attempt was made to exploit the running system.
- **No dependency CVE scan.** `pip-audit` in CI is a recommended next step and is not wired up.
- **No review of the legacy `services/` tree beyond Phase 1.** The v1/v2 surface still runs during
  the migration; its findings are documented but only SEC-01 was fixed there directly (by
  removing the routes). The migration plan is ADR-002.
- **No review of the Flutter client.** Token storage, certificate pinning, and local encryption on
  the device are out of scope for this backend audit.
- **No formal threat model document.** The findings are organised by observed defect rather than
  by a STRIDE or attack-tree decomposition.
- **No load or chaos testing.** The concurrency numbers in the
  [Benchmark Report](benchmark-report.md) are throughput measurements, not resilience tests.

## 6. Verifying a deployment

Three checks, in the order they are worth running:

```bash
# 1. No security bypass is active. Every series must read 0.
curl -s http://localhost:8000/metrics | grep 'aura_insecure_mode' | grep -v ' 0$'

# 2. Only 80/443 are exposed; the data tier is not reachable.
ss -tlnp | grep -E ':(5432|6379|6333|11434)' && echo "FAIL: data tier exposed" || echo "ok"

# 3. The removed proxy routes are gone (SEC-01).
curl -s -o /dev/null -w '%{http_code}\n' https://$AURA_DOMAIN/api/pull   # expect 404
```

Check 1 is also a Prometheus alert (`AuraInsecureModeActive`), because SEC-05 survived partly
because nothing reported it.

Ongoing signals, in `deploy/monitoring/`:

| Signal | Query | Means |
| --- | --- | --- |
| Injection attempts | `rate(aura_guard_verdicts_total{guard="prompt_injection",stage="context"}[15m])` | A rising quarantine rate means a document in the knowledge base contains instruction-shaped content — review recent ingestions. |
| Secret leakage | `rate(aura_guard_verdicts_total{guard="secret",action="block"}[15m])` | Either an attack or a real credential leaking into answers. Investigate before tuning the threshold. |
| Tool refusals | `rate(aura_tool_calls_total{outcome="refused"}[15m])` | A spike is either a capability misconfiguration or an agent being steered. |
| Taint rate | `rate(aura_turns_tainted_total[1h])` | Turns proceeding with quarantined context in scope. |

## 7. Recommended next steps

In priority order, with what each would actually buy:

1. **`pip-audit` in CI.** The single highest-value addition here — a known-vulnerable transitive
   dependency is a more likely compromise route than anything in §4.
2. **Hash-chained audit records.** Turns the audit log from a record into evidence.
3. **Per-user capability grants.** Grants are role-derived today; per-user grants would let a
   deployment restrict a single account without inventing a role.
4. **A WAF or CDN in front of Nginx.** The gap named in §4 under denial of service.
5. **An external penetration test.** The gap named in §5. Nothing in this document substitutes
   for one.

---

Related: [Security Report (Phase 1)](../analysis/06-security-report.md) ·
[Administrator Guide → Security posture](../guides/administrator-guide.md#security-posture) ·
[ADR-006 Safety pipeline](../architecture/006-safety-pipeline.md)
