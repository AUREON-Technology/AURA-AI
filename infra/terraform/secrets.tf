# Secret Manager (Doc/V2_BLUEPRINT.md §10 — replaces committed .env files,
# the exact class of mistake that leaked credentials during this project's
# earlier hardening pass). Two categories:
#   - Terraform-generated (database URL, JWT secret): value is set here,
#     since Terraform already owns both ends of that secret.
#   - Externally-sourced (Gemini/Qdrant API keys, Firebase service account):
#     Terraform only creates the empty container + IAM binding; the actual
#     value must be added once, manually, via
#     `gcloud secrets versions add <name> --data-file=-`, and is
#     deliberately never in Terraform state or var files (see README.md).

locals {
  database_url = "postgresql+asyncpg://${google_sql_user.aura.name}:${random_password.postgres_password.result}@${google_sql_database_instance.postgres.private_ip_address}:5432/${google_sql_database.aura.name}"
}

resource "random_password" "jwt_secret" {
  length  = 48
  special = false
}

resource "google_secret_manager_secret" "database_url" {
  secret_id = "aura-database-url"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "database_url" {
  secret      = google_secret_manager_secret.database_url.id
  secret_data = local.database_url
}

resource "google_secret_manager_secret" "jwt_secret" {
  secret_id = "aura-jwt-secret"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "jwt_secret" {
  secret      = google_secret_manager_secret.jwt_secret.id
  secret_data = random_password.jwt_secret.result
}

# A managed secret holding a hosted-inference API key was removed here. The platform performs all
# inference on its own infrastructure, so such a secret has no consumer — and a provisioned key is
# an invitation to wire one back up.
#
# NOTE FOR WHOEVER APPLIES THIS: `terraform apply` will want to *destroy* the existing secret in
# any project where this was already applied. Secret Manager destruction is not instant and not
# free of consequence if something outside Terraform reads it. Check first:
#   gcloud secrets versions list aura-gemini-api-key --project <project>   # self-hosting-scan: allow
# and if it holds a real key, revoke that key at the provider before destroying the secret.
# Deleting the resource here does not revoke anything.

# Externally-sourced secrets: container + IAM only. First real value must
# be added out-of-band — see infra/terraform/README.md.
resource "google_secret_manager_secret" "qdrant_api_key" {
  secret_id = "aura-qdrant-api-key"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret" "firebase_credentials" {
  secret_id = "aura-firebase-credentials"
  replication {
    auto {}
  }
}
