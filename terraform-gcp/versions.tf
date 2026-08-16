terraform {
  required_version = "~> 1.15"

  # HCP Terraform workspace with LOCAL execution — state lives in HCP, applies run from
  # this machine using the local Application Default Credentials for GCP.
  # Set the workspace's Execution Mode to "Local" before the first apply, otherwise HCP
  # tries to run remotely and has no GCP credentials.
  #
  # Separate workspace from the AKS build: the two are different clouds with different
  # state. `terraform/` (Azure) stays as it is.
  cloud {
    organization = "Shikha_Projects"

    workspaces {
      name = "mlops-model-lifecycle-gcp"
    }
  }

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
    # google-beta is a separate provider, not a flag. Some GKE features surface in beta
    # first; declared now so adding one later doesn't need a re-init.
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 6.0"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.35"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 2.17"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
  zone    = var.zone
}

provider "google-beta" {
  project = var.project_id
  region  = var.region
  zone    = var.zone
}
