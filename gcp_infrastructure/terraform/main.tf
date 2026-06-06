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
  name       = "gpu-node-pool"
  location   = var.zone
  cluster    = google_container_cluster.primary.name
  node_count = 1

  node_config {
    preemptible  = true # Spot/Preemptible L4 GPU cuts cost to ~$0.22/hour!
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
