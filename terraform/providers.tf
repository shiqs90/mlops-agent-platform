provider "azurerm" {
  features {}
  # Subscription + tenant come from the local `az login` session (or ARM_* env vars).

  # Don't auto-register every Azure resource provider — a fresh subscription
  # 409-throttles the burst. Register only what's needed, explicitly, via CLI:
  #   az provider register --namespace Microsoft.Compute
  #   az provider register --namespace Microsoft.Network
  #   az provider register --namespace Microsoft.ContainerService
  #   az provider register --namespace Microsoft.Storage
  #   az provider register --namespace Microsoft.ManagedIdentity
  resource_provider_registrations = "none"
}

# kubernetes and helm authenticate with the admin kube_config AKS exports as a
# resource attribute (certificate-based) — no exec/token plugin needed, unlike EKS.
provider "kubernetes" {
  host                   = azurerm_kubernetes_cluster.this.kube_config[0].host
  client_certificate     = base64decode(azurerm_kubernetes_cluster.this.kube_config[0].client_certificate)
  client_key             = base64decode(azurerm_kubernetes_cluster.this.kube_config[0].client_key)
  cluster_ca_certificate = base64decode(azurerm_kubernetes_cluster.this.kube_config[0].cluster_ca_certificate)
}

provider "helm" {
  kubernetes {
    host                   = azurerm_kubernetes_cluster.this.kube_config[0].host
    client_certificate     = base64decode(azurerm_kubernetes_cluster.this.kube_config[0].client_certificate)
    client_key             = base64decode(azurerm_kubernetes_cluster.this.kube_config[0].client_key)
    cluster_ca_certificate = base64decode(azurerm_kubernetes_cluster.this.kube_config[0].cluster_ca_certificate)
  }
}
