# Rollback

Independent rollback per plane — a failed deploy on one side never requires touching the other,
per the task's own §21 requirement. Neither plane's rollback is built or scripted new here;
both use each platform's native mechanism, since inventing a bespoke rollback tool would be
scope creep for a deployment-only migration.

## Control Plane (Railway)

Railway keeps prior deployments; rolling back is selecting the previous known-good deployment
for the `gateway` service (and, independently, for `worker-realtime`/`worker-ingestion`/`beat`
if any of them shipped a bad config) in the Railway dashboard, or `railway rollback` via the
CLI. This reverts code + environment-variable snapshot together, which matters: a bad env var
change and a bad code change roll back atomically.

**A failed Inference Plane deployment never requires a Control Plane rollback**, unless the API
contract itself changed (e.g., a new required request field vLLM's OpenAI-compatible server
doesn't support at the old version) — see `architecture.md` §7 for the versioning this protects
against. If only the model or GPU config changed, `AURA_PROVIDER_HFSPACE_*` config on Railway is
untouched and the Control Plane keeps running against whatever the Space serves once it
recovers.

## Inference Plane (Hugging Face Space)

A Space's git history holds every prior revision. Roll back with:

```bash
hf upload <user>/<space> deploy/spaces/inference-vllm . --repo-type space --revision <prior-good-commit>
```

or, more simply, `git revert` the change in `deploy/spaces/inference-vllm/` in this repo and
re-run the same `hf upload` the CI workflow uses — the Space rebuilds from whatever was last
pushed to it, same as any other container image rollback.

**A failed Control Plane deployment never requires rebuilding the inference image.** The Space
has no dependency on Railway's deploy state; it only serves whatever `VLLM_MODEL`/config it was
last given.

## Model-config rollback (a narrower case than a full Space rollback)

If only `VLLM_MODEL`/`VLLM_MAX_MODEL_LEN`/`VLLM_GPU_MEMORY_UTILIZATION` need reverting (not the
Dockerfile/entrypoint), reset those Space variables (Settings → Variables and secrets) to the
prior values and restart the Space — no image rebuild needed, since the entrypoint reads them at
container start.

## Rolling the whole migration back

To fully revert to the pre-migration VPS-only topology (per `architecture.md` §5, still
running and untouched): point DNS back at the VPS if it was ever moved, and on the VPS restore
`AURA_LLM_PROVIDERS=local` (or `local,hfspace` for the failover variant) in `deploy/.env`. This
is the same rollback `docs/guides/split-inference-deployment.md` §"Rolling back" already
documents for the failover topology — this migration doesn't add a new one, since dropping back
to a single-provider `local` config was already a supported, tested path.
