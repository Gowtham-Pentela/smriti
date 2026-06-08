# ── Outputs used by deploy.sh ─────────────────────────────────────────────────

output "gke_cluster_name" {
  description = "GKE cluster name for kubectl credential configuration"
  value       = google_container_cluster.primary.name
}

output "gke_cluster_zone" {
  description = "Zone where the GKE cluster is deployed"
  value       = var.zone
}

output "db_private_ip" {
  description = "Private IP of the Cloud SQL Postgres instance"
  value       = google_sql_database_instance.postgres.private_ip_address
  sensitive   = true
}

output "db_connection_name" {
  description = "Cloud SQL connection name (for Cloud SQL Auth Proxy)"
  value       = google_sql_database_instance.postgres.connection_name
}
