output "cloud_run_url" {
  value       = google_cloud_run_v2_service.backend.uri
  description = "Public URL of the deployed backend — set as BACKEND_URL in the Flutter client's --dart-define at build time."
}

output "postgres_private_ip" {
  value       = google_sql_database_instance.postgres.private_ip_address
  description = "Cloud SQL private IP (VPC-internal only, matches the private_network config)."
}

output "redis_host" {
  value       = google_redis_instance.cache.host
  description = "Memorystore Redis host (VPC-internal only)."
}

output "github_workload_identity_provider" {
  value       = google_iam_workload_identity_pool_provider.github.name
  description = "Set as the GCP_WORKLOAD_IDENTITY_PROVIDER repository variable for deploy-backend.yml."
}

output "github_deployer_service_account" {
  value       = google_service_account.github_deployer.email
  description = "Set as the GCP_SERVICE_ACCOUNT repository variable for deploy-backend.yml."
}
