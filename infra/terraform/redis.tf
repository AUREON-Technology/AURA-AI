# Memorystore Redis — backs the sync/attestation nonce store, rate
# limiter, and refresh-token store (infrastructure/redis_client.py), all
# of which already have in-process fallbacks if this is ever unreachable.

resource "google_redis_instance" "cache" {
  name           = "aura-redis-${var.environment}"
  tier           = "BASIC" # single-node; upgrade to STANDARD_HA for automatic failover once traffic justifies it
  memory_size_gb = var.redis_memory_size_gb
  region         = var.gcp_region

  authorized_network = google_compute_network.vpc.id
  connect_mode        = "PRIVATE_SERVICE_ACCESS"

  depends_on = [google_service_networking_connection.private_vpc_connection]
}
