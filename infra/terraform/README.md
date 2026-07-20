# AURA AI — Terraform (GCP)

Provisions the V2 backend's cloud infrastructure (Doc/V2_BLUEPRINT.md §10):
Cloud Run, Cloud SQL (Postgres 16), Memorystore (Redis), Secret Manager,
private VPC networking, and the Workload Identity Federation setup
`.github/workflows/deploy-backend.yml` needs to deploy without a
downloaded service-account key.

**Status: written, never applied.** This config was produced in a sandbox
with no `terraform` CLI and no GCP project/billing account — it has been
reviewed for internal consistency (resource references, GCP naming
constraints like the 25-character VPC connector limit) but **not run
through `terraform plan`/`apply` against a real project**. Treat the first
real `terraform plan` as this configuration's actual test, the same way
every other untestable piece of Phase 4/5 is flagged in the blueprint.

## Prerequisites (one-time, manual, outside this config)

1. A GCP project with billing enabled.
2. Enable the required APIs: `run.googleapis.com`, `sqladmin.googleapis.com`,
   `redis.googleapis.com`, `secretmanager.googleapis.com`,
   `vpcaccess.googleapis.com`, `servicenetworking.googleapis.com`,
   `iam.googleapis.com`.
3. A GCS bucket for Terraform remote state, then uncomment the `backend
   "gcs"` block in `versions.tf`.
4. Authenticate locally: `gcloud auth application-default login`.

## Apply

```bash
terraform init
terraform plan \
  -var="gcp_project_id=<your-project>" \
  -var="github_repository=<org>/<repo>"
terraform apply \
  -var="gcp_project_id=<your-project>" \
  -var="github_repository=<org>/<repo>"
```

## After the first apply

1. Add the real values for the three externally-sourced secrets Terraform
   only created empty containers for (see `secrets.tf`'s doc comment):
   ```bash
   echo -n "<gemini key>" | gcloud secrets versions add aura-gemini-api-key --data-file=-
   echo -n "<qdrant key>" | gcloud secrets versions add aura-qdrant-api-key --data-file=-
   gcloud secrets versions add aura-firebase-credentials --data-file=credentials.json
   ```
2. Set the Cloud Run service's `QDRANT_HOST` env var to the real Qdrant
   Cloud cluster URL (left blank in `cloud_run.tf` since it's an external,
   not-Terraform-managed resource).
3. Copy `terraform output github_workload_identity_provider` and
   `terraform output github_deployer_service_account` into the GitHub
   repo's Actions variables (`GCP_WORKLOAD_IDENTITY_PROVIDER`,
   `GCP_SERVICE_ACCOUNT`) so `deploy-backend.yml` can authenticate.
4. Run the Alembic migration against the new Cloud SQL instance
   (`alembic upgrade head`, e.g. via `gcloud sql connect` or a one-off
   Cloud Run Job) — this config provisions the database, not its schema.

## What's deliberately not here

- DNS / custom domain mapping — depends on a domain the project doesn't
  currently own outside HF Spaces' assigned subdomain.
- Qdrant — stays on Qdrant Cloud (already external, already working per
  `services/rag.py`'s existing fallback logic); no self-hosted Qdrant
  Terraform resource.
- Staging environment — `var.environment` supports it (`-var="environment=staging"`),
  but a second full `terraform apply` with separate state/variables is an
  operational decision for whoever runs this for real, not something to
  half-wire here.
