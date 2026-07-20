terraform {
  required_version = ">= 1.5.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Remote state is required for a real team (local state can't be shared
  # safely). Uncomment and point at a pre-created GCS bucket before the
  # first `terraform init` — left commented because provisioning that
  # bucket is itself a one-time manual step outside this config.
  # backend "gcs" {
  #   bucket = "aura-ai-terraform-state"
  #   prefix = "prod"
  # }
}

provider "google" {
  project = var.gcp_project_id
  region  = var.gcp_region
}
