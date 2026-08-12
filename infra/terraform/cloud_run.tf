# Cloud Run — replaces V1's Hugging Face Spaces deployment (a demo-hosting
# platform with no real autoscaling/secrets-manager/zero-downtime deploys,
# per Doc/V2_BLUEPRINT.md §10). Actual deploys happen via
# .github/workflows/deploy-backend.yml's `gcloud run deploy --source`,
# which builds and deploys in one step; this resource exists for the
# initial `terraform apply` bootstrap and for anyone managing the service
# directly with Terraform instead of the CI/CD path.

resource "google_service_account" "backend_runtime" {
  account_id   = "aura-backend-runtime"
  display_name = "AURA backend Cloud Run runtime identity"
}

resource "google_project_iam_member" "backend_secret_access" {
  for_each = toset([
    google_secret_manager_secret.database_url.secret_id,
    google_secret_manager_secret.jwt_secret.secret_id,
    google_secret_manager_secret.qdrant_api_key.secret_id,
    google_secret_manager_secret.firebase_credentials.secret_id,
  ])
  project = var.gcp_project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${google_service_account.backend_runtime.email}"
}

resource "google_cloud_run_v2_service" "backend" {
  name     = "aura-backend"
  location = var.gcp_region

  template {
    service_account = google_service_account.backend_runtime.email

    vpc_access {
      connector = google_vpc_access_connector.connector.id
      egress    = "PRIVATE_RANGES_ONLY" # only route Redis/Postgres traffic through the VPC — Gemini/Qdrant Cloud calls still go direct
    }

    scaling {
      min_instance_count = 0 # scale-to-zero — matches the cost profile V1 had on HF Spaces' free tier
      max_instance_count = 10
    }

    containers {
      image = var.backend_image

      env {
        name  = "REDIS_HOST"
        value = google_redis_instance.cache.host
      }
      env {
        name  = "QDRANT_HOST"
        value = "" # Qdrant Cloud, not self-hosted — set to the real cluster URL post-apply
      }
      env {
        name  = "LOG_FORMAT"
        value = "json"
      }

      env {
        name = "DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.database_url.secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "JWT_SECRET"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.jwt_secret.secret_id
            version = "latest"
          }
        }
      }
      # An env block for a hosted-inference API key sat here, sourced from Secret Manager.
      # Removed: no code reads it, and injecting such a credential into a runtime that is
      # forbidden from using one is the kind of dead wiring that quietly becomes live wiring.
      env {
        name = "QDRANT_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.qdrant_api_key.secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "FIREBASE_CREDENTIALS_JSON"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.firebase_credentials.secret_id
            version = "latest"
          }
        }
      }
    }
  }

  # gcloud run deploy --source (used by deploy-backend.yml) manages the
  # image after the first apply — don't fight CI/CD over this field.
  lifecycle {
    ignore_changes = [template[0].containers[0].image]
  }
}

resource "google_cloud_run_v2_service_iam_member" "public_invoker" {
  name     = google_cloud_run_v2_service.backend.name
  location = google_cloud_run_v2_service.backend.location
  role     = "roles/run.invoker"
  member   = "allUsers" # matches --allow-unauthenticated in deploy-backend.yml — the API does its own auth (services/security.py, services/jwt_auth.py), not IAM-level gating
}
