# Private networking so Cloud SQL and Memorystore are never exposed to the
# public internet — Cloud Run reaches them only via the VPC connector.
# Doc/V2_BLUEPRINT.md §10.

resource "google_compute_network" "vpc" {
  name                    = "aura-vpc-${var.environment}"
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "subnet" {
  name          = "aura-subnet-${var.environment}"
  ip_cidr_range = "10.8.0.0/28"
  region        = var.gcp_region
  network       = google_compute_network.vpc.id
}

# Private services access, required for Cloud SQL/Memorystore to live on
# the VPC rather than getting a public IP.
resource "google_compute_global_address" "private_ip_range" {
  name          = "aura-private-ip-range-${var.environment}"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = google_compute_network.vpc.id
}

resource "google_service_networking_connection" "private_vpc_connection" {
  network                 = google_compute_network.vpc.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_ip_range.name]
}

resource "google_vpc_access_connector" "connector" {
  # GCP hard-limits VPC connector names to 25 characters — kept short and
  # fixed (not environment-suffixed like other resources here) so it never
  # breaks if `environment` is set to something longer than "production".
  name          = "aura-conn"
  region        = var.gcp_region
  ip_cidr_range = "10.9.0.0/28"
  network       = google_compute_network.vpc.name
}
