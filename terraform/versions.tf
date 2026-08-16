terraform {
  required_version = "~> 1.15"

  # HCP Terraform workspace with LOCAL execution — state lives in HCP, applies run
  # from this machine using the local `az login` session for Azure credentials.
  # Set the workspace's Execution Mode to "Local" before the first apply.
  cloud {
    organization = "Shikha_Projects"

    workspaces {
      name = "mlops-model-lifecycle"
    }
  }

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
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
