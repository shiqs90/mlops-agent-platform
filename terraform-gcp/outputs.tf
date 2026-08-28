output "project_id" {
  description = "Project everything is built in. Teardown of last resort: gcloud projects delete <this>."
  value       = var.project_id
}

output "vpc_name" {
  value = google_compute_network.vpc.name
}

output "subnet_name" {
  value = google_compute_subnetwork.subnet.name
}

output "secondary_ranges" {
  description = "Pod and service ranges — the thing that makes the cluster VPC-native."
  value = {
    for r in google_compute_subnetwork.subnet.secondary_ip_range :
    r.range_name => r.ip_cidr_range
  }
}

output "cluster_name" {
  value = google_container_cluster.primary.name
}

output "get_credentials" {
  description = "Run this to point kubectl at the cluster."
  value       = "gcloud container clusters get-credentials ${google_container_cluster.primary.name} --zone ${var.zone} --project ${var.project_id}"
}

output "artifact_registry" {
  description = "Docker registry host path. Tag images as <this>/<image>:<tag>."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}

output "workload_identity_pool" {
  description = "Bind a Kubernetes SA to a Google SA against this pool."
  value       = google_container_cluster.primary.workload_identity_config[0].workload_pool
}

output "node_service_account" {
  description = "Least-privilege node SA — deliberately NOT the Compute Engine default (which holds project Editor)."
  value       = google_service_account.node.email
}

output "cicd_service_account" {
  description = "GitHub Actions build identity. Mint a key against this and store it as the repo secret GCP_SA_KEY — see cicd.tf for why the key is not created by Terraform."
  value       = google_service_account.cicd.email
}
