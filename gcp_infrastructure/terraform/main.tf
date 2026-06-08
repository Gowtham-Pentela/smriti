provider "google" {
  project = var.project_id
  region  = var.region
}

# 1. VPC Network & Private Subnet
resource "google_compute_network" "vpc" {
  name                    = "knowledge-guardian-vpc"
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "private_subnet" {
  name          = "private-subnet"
  ip_cidr_range = "10.0.1.0/24"
  network       = google_compute_network.vpc.id
  region        = var.region

  # Enables private Google access so nodes can reach Google APIs (like GCS)
  private_ip_google_access = true
}

# 2. Cloud Router & NAT (Allows private nodes internet access to pull Ollama models securely)
resource "google_compute_router" "router" {
  name    = "nat-router"
  network = google_compute_network.vpc.id
  region  = var.region
}

resource "google_compute_router_nat" "nat" {
  name                               = "nat-config"
  router                             = google_compute_router.router.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"
}

# 3. Private IP Allocation for Cloud SQL Database
resource "google_compute_global_address" "private_ip_address" {
  name          = "private-ip-address"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = google_compute_network.vpc.id
}

resource "google_service_networking_connection" "private_vpc_connection" {
  network                 = google_compute_network.vpc.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_ip_address.name]
}

# 4. Cloud SQL PostgreSQL Database (pgvector target)
resource "google_sql_database_instance" "postgres" {
  name             = "knowledge-guardian-db"
  database_version = "POSTGRES_15"
  region           = var.region

  depends_on = [google_service_networking_connection.private_vpc_connection]

  settings {
    tier = "db-custom-1-3840" # Cost-optimized: 1 vCPU, 3.75GB RAM (~$25/month)
    ip_configuration {
      ipv4_enabled    = false # No public IP
      private_network = google_compute_network.vpc.id
    }
    database_flags {
      name  = "cloudsql.enable_pgvector"
      value = "on"
    }
    # ── Automatic daily backups (14-day retention) ───────────────────────────
    backup_configuration {
      enabled                        = true
      start_time                     = "03:00" # 3am UTC — low traffic window
      point_in_time_recovery_enabled = true
      transaction_log_retention_days = 7
      backup_retention_settings {
        retained_backups = 14
        retention_unit   = "COUNT"
      }
    }
  }
}

# 5. GKE Cluster (Google Kubernetes Engine)
resource "google_container_cluster" "primary" {
  name     = "knowledge-guardian-cluster"
  location = var.zone

  network    = google_compute_network.vpc.id
  subnetwork = google_compute_subnetwork.private_subnet.id

  # We will delete the default node pool and use custom node pools
  remove_default_node_pool = true
  initial_node_count       = 1

  private_cluster_config {
    enable_private_nodes    = true
    enable_private_endpoint = false # Keep control plane endpoint public for developer ease, but nodes remain private
    master_ipv4_cidr_block  = "172.16.0.0/28"
  }

  ip_allocation_policy {
    cluster_ipv4_cidr_block  = "/21"
    services_ipv4_cidr_block = "/21"
  }
}

# 6. CPU Node Pool (Hosts API backend, Celery workers, and web frontend UI)
resource "google_container_node_pool" "cpu_nodes" {
  name       = "cpu-node-pool"
  location   = var.zone
  cluster    = google_container_cluster.primary.name
  node_count = 2

  node_config {
    preemptible  = true # Set to true (Spot instances) for 60-70% cost reduction
    machine_type = "e2-medium" # 2 vCPU, 4GB RAM (~$25/month each)

    oauth_scopes = [
      "https://www.googleapis.com/auth/cloud-platform"
    ]
  }
}

# 7. GPU Node Pool (Dedicated to running vLLM or Ollama for local model inference)
resource "google_container_node_pool" "gpu_nodes" {
  name     = "gpu-node-pool"
  location = var.zone
  cluster  = google_container_cluster.primary.name

  # ── Autoscaling: scales to 0 at night, up to 1 during active hours ───────────
  # Saves ~70% GPU cost vs always-on. Cold-start adds ~90s for first query after idle.
  autoscaling {
    min_node_count = 0
    max_node_count = 1
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }

  node_config {
    preemptible  = true
    machine_type = "g2-standard-4" # 4 vCPUs, 16GB RAM, 1x NVIDIA L4 GPU (24GB VRAM)

    guest_accelerator {
      type  = "nvidia-l4"
      count = 1
    }

    oauth_scopes = [
      "https://www.googleapis.com/auth/cloud-platform"
    ]

    taint {
      key    = "nvidia.com/gpu"
      value  = "present"
      effect = "NO_SCHEDULE"
    }
  }
}

# ── 8. Cloud Monitoring: Uptime check + alert on backend /status endpoint ────────

# Notification channel: send alert to a Slack webhook
resource "google_monitoring_notification_channel" "slack_ops" {
  display_name = "KGF Ops Slack Webhook"
  type         = "webhook_tokenauth"
  labels = {
    url = var.monitoring_slack_webhook
  }
}

# Uptime check: ping /status every 60 seconds from multiple GCP regions
resource "google_monitoring_uptime_check_config" "kgf_api_health" {
  display_name = "KGF Backend /status"
  timeout      = "10s"
  period       = "60s"

  http_check {
    path         = "/status"
    port         = "80"
    use_ssl      = false
    validate_ssl = false
  }

  monitored_resource {
    type   = "uptime_url"
    labels = {
      host      = "REPLACE_WITH_INGRESS_IP" # deploy.sh patches this post-apply
      project_id = var.project_id
    }
  }
}

# Alert policy: fire if uptime check fails for 2 consecutive minutes
resource "google_monitoring_alert_policy" "kgf_backend_down" {
  display_name = "KGF Backend Down"
  combiner     = "OR"

  conditions {
    display_name = "Uptime check failed"
    condition_threshold {
      filter          = "resource.type = \"uptime_url\" AND metric.type = \"monitoring.googleapis.com/uptime_check/check_passed\""
      comparison      = "COMPARISON_LT"
      threshold_value = 1
      duration        = "120s"
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_NEXT_OLDER"
        cross_series_reducer = "REDUCE_COUNT_FALSE"
        group_by_fields    = ["resource.label.host"]
      }
    }
  }

  notification_channels = [google_monitoring_notification_channel.slack_ops.name]

  alert_strategy {
    auto_close = "1800s" # Auto-resolve after 30min if service recovers
  }
}
