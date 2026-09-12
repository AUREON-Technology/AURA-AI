# Workload Identity Federation for GitHub Actions (Doc/V2_BLUEPRINT.md
# §10) — deploy-backend.yml authenticates as this service account without
# a downloaded JSON key ever existing, keyless by design.

resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "github-actions-pool"
  display_name              = "GitHub Actions"
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id         = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-provider"
  display_name                       = "GitHub OIDC"

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
  }

  # Restricts token exchange to this specific repo, not any GitHub repo
  # under the org — least-privilege by default.
  attribute_condition = "assertion.repository == \"${var.github_repository}\""

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account" "github_deployer" {
  account_id   = "aura-github-deployer"
  display_name = "GitHub Actions deploy identity (Workload Identity Federation)"
}

resource "google_service_account_iam_member" "github_wif_binding" {
  service_account_id = google_service_account.github_deployer.name
  role                = "roles/iam.workloadIdentityUser"
  member              = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repository}"
}

resource "google_project_iam_member" "github_deployer_run_admin" {
  project = var.gcp_project_id
  role    = "roles/run.admin"
  member  = "serviceAccount:${google_service_account.github_deployer.email}"
}

resource "google_project_iam_member" "github_deployer_sa_user" {
  project = var.gcp_project_id
  role    = "roles/iam.serviceAccountUser" # needed to deploy Cloud Run revisions running as backend_runtime
  member  = "serviceAccount:${google_service_account.github_deployer.email}"
}
