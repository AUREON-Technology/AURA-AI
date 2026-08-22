# Troubleshooting Guide

Symptoms, causes, and fixes. Ordered by how often they occur in practice.

Start every investigation with the correlation id. It flows through logs, metrics exemplars,
traces, and audit records for one turn:

```bash
docker compose -f deploy/docker-compose.prod.yml logs gateway \
  | jq -R 'fromjson? // empty | select(.correlation_id == "<id>")'
```

Every API response carries it in `X-Correlation-Id`.

---

## The gateway will not start

**`ConfigurationError` naming several problems.** This is settings validation refusing an
unsafe production configuration, and the log lists every violation at once. Common ones:

| Message | Meaning |
| --- | --- |
| `debug must be false in staging/production` | `AURA_DEBUG=true` — this would disable authentication entirely (SEC-05) |
| `infrastructure.vector_backend must not be 'memory'` | In-memory stores lose everything on restart; they are not a system of record |
| `the 'echo' test provider must not be enabled` | `AURA_LLM_PROVIDERS` still contains the deterministic test provider |
| `provider 'x' references GEMINI_API_KEY which is unset` | The named variable is empty |

Fix the configuration. Do not work around validation — every check corresponds to a finding in
the security report.

**Exits with no message.** Almost always a missing secret file. Check `deploy/secrets/` has all
five files with mode 600, and re-run `./scripts/bootstrap-secrets.sh` (it keeps existing files).

---

## Every login fails with 403 "Device attestation validation failed"

`POST /api/v3/auth/token` is the only place a session is minted (`services/security.py`'s
`verify_token`); a device that fails attestation cannot do anything else in the app, including
sync or chat, so this reads as "the whole app is broken," not a narrow auth bug. The response
body's parenthetical names the reason, matching `attestation_failure_reason` in the gateway logs
(search by the request's `X-Correlation-Id`):

| Reason | Meaning | Fix |
| --- | --- | --- |
| `play_integrity_unavailable` | The gateway could not build a Google Play Integrity API client at all — no usable service-account credentials were loadable. | Confirm `deploy/secrets/firebase_credentials.json` exists, is a valid service-account key, and that key's Google Cloud project has the Play Integrity API enabled (see [play-store-signing.md](play-store-signing.md)). |
| `play_integrity_decode` | Credentials loaded, but the `decodeIntegrityToken` API call itself failed. | Usually a project mismatch: the Android app must be registered in Play Console under the *same* Cloud project as the service account (`IntegrityAttestationPlugin.kt`'s `CLOUD_PROJECT_NUMBER` must match `google-services.json`'s `project_number`, and both must match the linked Play Console project). |
| `package_mismatch` | The token's package name doesn't match `AURA_ANDROID_PACKAGE_NAME`. | Confirm the setting matches the APK's actual `applicationId`. |
| `device_verdict` | Play Integrity's own device-recognition check failed (rooted/emulated/tampered device, or a legitimate device Google hasn't attested before). | Expected behavior for an untrusted device — not a bug. Verify on a normal, unrooted device. |
| `nonce_replay` / `nonce_mismatch` | The client didn't bind the server-issued nonce from `POST /api/v3/auth/nonce` into its attestation request, sent a stale one, or reused one already consumed (H1's single-use replay protection). | Confirm the client build includes the nonce-fetch fix — `device_validation_service.dart`'s `_serverNonce()` — and that `/api/v3/auth/nonce` is reachable from the device. An older client without this fix has nothing to send and relies on the check being skipped (no `expected_nonce` header), so this specific reason implies a nonce *was* sent but didn't match. |
| `missing_token` | No `X-Device-Attestation` header at all. | Client-side bug, or a non-mobile release build (see [platform-support.md](platform-support.md) — expected there, not fixable server-side). |
| `ios_attest` | App Attest verification failed — see the gateway log line just above this one for the specific reason (`services/attestation/app_attest.py` logs before returning). | Depends on the underlying reason; common causes are an untrusted certificate chain or an App ID mismatch. |

Debug builds bypass this entirely (`aura_dev_attestation`); this table only applies to release
builds hitting a production-mode backend (`allow_mock_attestation=false`, which is always the
case in staging/production — see `aura_core/config/settings.py`'s `is_production_like` check).

---

## `/health/ready` returns 503

Readiness reflects dependencies; liveness does not. A 503 here is *correct* while something is
still coming up.

```bash
curl -s https://$AURA_DOMAIN/health/ready | jq
```

| Field | Meaning |
| --- | --- |
| `providers.*.healthy: false` | That provider failed its probe |
| `providers.*.detail: "model 'x' not installed"` | Ollama is up but the model was never pulled |
| `index: null` | No index published — the container's startup has not completed |

```bash
docker compose -f deploy/docker-compose.prod.yml exec ollama ollama list
docker compose -f deploy/docker-compose.prod.yml exec ollama ollama pull qwen2.5:7b-instruct-q4_K_M
```

A first pull takes several minutes. `docker compose logs -f ollama` shows progress.

---

## Chat returns 503 `all_providers_failed`

Every candidate provider failed or is circuit-open. **This is the honest failure mode** — the
system refuses to fabricate an answer, which the previous implementation did by falling through
to hard-coded strings indistinguishable from a real response.

```bash
curl -H "$AUTH" $AURA/api/v3/admin/status | jq '.providers'
```

- `state: "open"` — the breaker tripped after consecutive failures. It half-opens after 30 s
  and admits one probe. Fix the cause, then
  `POST /api/v3/admin/providers/<name>/reset-circuit`.
- `failure_rate` high but `state: "closed"` — intermittent upstream. Check `docker compose logs
  ollama` for OOM kills; a 7B Q4 model needs ~6 GB and will be killed on a smaller box.
- All providers excluded with `context_window_too_small` — the prompt exceeds every configured
  model. Reduce `AURA_MEMORY_TOKEN_BUDGET` or `AURA_KNOWLEDGE_TOP_K`, or configure a
  larger-context provider.

---

## Answers ignore documents that are clearly in the knowledge base

Check what retrieval actually returned:

```bash
curl -X POST -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"query":"<the user question>"}' $AURA/api/v3/knowledge/search | jq
```

**`results: []` with `searched_but_empty: true`** — retrieval searched and found nothing above
the score floor. That is the designed behaviour, not a bug: below the floor the system returns
nothing rather than top-k noise labelled "relevant context". Causes, in order of likelihood:

1. The corpus genuinely lacks the answer.
2. `AURA_KNOWLEDGE_SCORE_THRESHOLD` (default 0.35) is too high for your embedding model.
   Lower it in steps of 0.05 and re-measure.
3. The document was **quarantined at ingestion** — check the ingest response's
   `quarantined_chunks`, and `aura_guard_verdicts_total{stage="context"}`.
4. Ownership mismatch: documents ingested under a different user are invisible by design.

**`results` non-empty but the answer ignores them** — check `groundedness` in the Quality
dashboard. Persistently low means the model is not using the context it was given; try a
higher-tier provider for that route.

---

## Retrieval got worse after a change

```bash
curl -H "$AUTH" $AURA/api/v3/admin/status | jq '.health.index'
```

If the index name changed, the embedding model changed — the identity encodes it (ADR-007).
Either restore the previous `AURA_EMBEDDING_MODEL` (the old index is retained for the retention
window and the alias flips back in O(1)), or accept the new one and verify the promotion gates
passed.

If the name did not change, check whether the nightly refresh promoted a regression:

```promql
aura_knowledge_generation
sum(rate(aura_refresh_gate_failures_total[24h])) by (gate)
```

---

## The nightly refresh is not updating anything

```promql
time() - aura_refresh_last_success_timestamp_seconds
```

Above 172800 fires `AuraKnowledgeStale`. The live index is intact either way — a failed run
never touches it.

Read the last `RefreshReport`. Frequent causes:

| `gate_failures` entry | Meaning |
| --- | --- |
| `chunk count changed by N%` | A source is returning error pages or was emptied upstream |
| `error rate N%` | The embedding backend is degrading mid-build |
| `recall dropped N` | The rebuild retrieves worse than what it replaces |

`documents_skipped` high with `ingested: 0` is **normal**: ingestion is idempotent on content
hash, so unchanged documents are skipped rather than re-versioned every night.

---

## Streaming arrives all at once

Nginx is buffering. The `/api/v3/chat/stream` location must set `proxy_buffering off`,
`gzip off`, and a long `proxy_read_timeout`. With defaults the stream still "works" and is
entirely useless.

If you run your own proxy, the gateway also emits `X-Accel-Buffering: no`.

---

## Rate limiting behaves oddly

**Everyone shares one limit.** `AURA_TRUSTED_PROXY_COUNT` does not match your topology, so
every request appears to come from the proxy. With the bundled Nginx it is 1; add one per
additional hop (a CDN in front makes it 2).

**A client evades the limit.** The opposite mistake — reading the leftmost `X-Forwarded-For`
entry, which is client-controlled. The gateway counts from the right; verify your proxy *sets*
rather than appends the header (see `deploy/nginx/conf.d/proxy_headers.inc`).

**Legitimate users hit limits quickly.** Limits are cost-weighted: a chat turn costs 10 and an
agent turn 20 against a health check's 0. Raise `AURA_RATE_LIMIT_USER_PER_MIN` rather than
lowering weights — the weights reflect real consumption.

---

## Tool calls are refused

The response text names the reason.

| Reason | Fix |
| --- | --- |
| `missing_capability:<name>` | The caller lacks the grant. Capability checks fail closed by design. |
| `side_effect_in_tainted_context` | The turn's context contained quarantined content, so a side-effecting tool needs explicit confirmation. **This is the SEC-02 defence working.** Investigate what was quarantined before confirming. |
| `tool_requires_confirmation` | Re-send with the tool in `confirmed_tools`. |
| `tool_not_found` | Not registered, or filtered out for this caller — tools the caller cannot use are deliberately not advertised. |

---

## Memory is not recalled

- **Quota exceeded** (`memory quota exceeded for this user`) — 20,000 records by default. Run
  consolidation or raise the limit. This raises rather than silently evicting, because a user
  losing memories without being told is worse than a visible error.
- **Score below threshold** — recall blends similarity, recency, and reinforcement with
  per-kind weights. A semantic fact stated once and never referenced ranks low. Lower
  `AURA_MEMORY_RECALL_MIN_SCORE` to check.
- **Superseded** — consolidation marks a contradicted fact `superseded_by` rather than deleting
  it, and superseded records are excluded from recall by default.

---

## Costs are higher than expected

```promql
sum(rate(aura_llm_cost_usd_total[1h])) by (provider, model) * 86400   # projected daily
sum(rate(aura_cache_lookups_total{result="hit"}[1h]))
  / sum(rate(aura_cache_lookups_total[1h]))                            # hit rate
sum(rate(aura_reasoning_strategy_total[1h])) by (strategy)
```

Three levers, in order of effect:

1. **Cache hit rate below 5 %** — only deterministic, tool-free turns are cacheable. If clients
   send `temperature > 0` by default, that alone disables caching.
2. **Reasoning escalation** — a high share of `tree_of_thoughts` or `self_consistency`
   multiplies generations per turn. Set `AURA_REASONING_AUTO_SELECT=false` to force `direct`.
3. **Routing policy** — `local_first` keeps most traffic on hardware you already pay for.

---

## Memory usage grows over time

Every cache in this codebase is a bounded TTL-LRU, so the classic slow leak should not occur.
If it does:

```promql
aura_cache_entries
process_resident_memory_bytes
```

The usual real cause is the embedding model: `sentence-transformers` loads ~90-400 MB **per
worker**. Four workers is 1.6 GB before anything else. Either reduce `AURA_WORKERS` or move
embeddings to a shared endpoint (`AURA_EMBEDDING_PROVIDER` pointing at a provider name).

---

## Escalating

Collect before asking for help:

```bash
curl -s https://$AURA_DOMAIN/health/ready | jq                        # dependency state
curl -s -H "$AUTH" $AURA/api/v3/admin/status | jq '.providers,.health'
docker compose -f deploy/docker-compose.prod.yml ps
docker compose -f deploy/docker-compose.prod.yml logs --tail=200 gateway | jq -R 'fromjson? // .'
curl -s http://localhost:8000/metrics | grep -E 'aura_(insecure_mode|provider_healthy|turn_degraded)'
```

Include the correlation id of a failing request. Logs contain no prompt or document content by
design, so they are safe to share.
