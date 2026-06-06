variable "project_id" {
  type        = string
  description = "The GCP project ID to deploy resources in"
}

variable "region" {
  type        = string
  default     = "us-central1"
  description = "The GCP region for the subnets and database"
}

variable "zone" {
  type        = string
  default     = "us-central1-a"
  description = "The GCP zone for the GKE cluster and node pools"
}
