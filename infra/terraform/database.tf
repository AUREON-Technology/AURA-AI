# Postgres 16, matching docker-compose.yml's local postgres:16-alpine —
# Doc/V2_BLUEPRINT.md §6, the relational source of truth for tasks/
# calendar/finance/health/memories.

resource "google_sql_database_instance" "postgres" {
  name             = "aura-postgres-${var.environment}"
  database_version = "POSTGRES_16"
  region           = var.gcp_region

  depends_on = [google_service_networking_connection.private_vpc_connection]

  settings {
    tier = var.postgres_tier

    ip_configuration {
      ipv4_enabled    = false
      private_network = google_compute_network.vpc.id
    }

    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
    }
  }

  # Prevent `terraform destroy` from silently deleting production user
  # data — this must be flipped to false deliberately before a real teardown.
  deletion_protection = true
}

resource "google_sql_database" "aura" {
  name     = "aura"
  instance = google_sql_database_instance.postgres.name
}

resource "random_password" "postgres_password" {
  length  = 32
  special = false # avoid characters that need extra escaping in a connection URL
}

resource "google_sql_user" "aura" {
  name     = "aura"
  instance = google_sql_database_instance.postgres.name
  password = random_password.postgres_password.result
}
