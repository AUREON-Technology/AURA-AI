variable "gcp_project_id" {
  description = "Target GCP project. Must already exist with billing enabled."
  type        = string
}

variable "gcp_region" {
  description = "Region for all resources (Cloud Run, Cloud SQL, Memorystore, VPC connector)."
  type        = string
  default     = "us-central1"
}

variable "environment" {
  description = "Deployment environment name, used as a resource-name suffix."
  type        = string
  default     = "production"
}

variable "postgres_tier" {
  description = "Cloud SQL machine tier. db-f1-micro is the smallest/cheapest — fine for early traffic, not for real load."
  type        = string
  default     = "db-f1-micro"
}

variable "redis_memory_size_gb" {
  description = "Memorystore Redis instance size in GB."
  type        = number
  default     = 1
}

variable "backend_image" {
  description = "Container image for the Cloud Run service. Built by .github/workflows/backend-ci.yml; deployed by deploy-backend.yml via `gcloud run deploy --source`, which builds in-place rather than requiring a pre-pushed image — this variable exists for a manual `terraform apply` path instead of the CI/CD path."
  type        = string
  default     = "gcr.io/cloudrun/hello" # placeholder until the first real image is pushed
}

variable "github_repository" {
  description = "GitHub \"owner/repo\" allowed to assume the deploy service account via Workload Identity Federation."
  type        = string
}
