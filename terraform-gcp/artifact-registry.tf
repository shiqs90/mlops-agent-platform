# Artifact Registry — the ECR / ACR equivalent, and the successor to Container Registry
# (gcr.io), which is deprecated.
#
# Regional, in the same region as the cluster: an image pull crossing regions is slower and
# incurs egress charges. Storage is ~$0.10/GB/month, so a handful of training and serving
# images is cents.
resource "google_artifact_registry_repository" "images" {
  location      = var.region
  repository_id = var.cluster_name
  format        = "DOCKER"
  description   = "Training and serving images for the P7 pipeline"

  # Keep the registry from growing without limit — every pipeline run pushes a new tag.
  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 10
    }
  }
}

# Nodes pull images using their own service account, granted artifactregistry.reader in
# gke.tf at project level. Nothing further is needed for pulls.
#
# Pushes come from GitHub Actions, which will authenticate by Workload Identity Federation
# rather than a JSON key — wired up in the CI stage.
