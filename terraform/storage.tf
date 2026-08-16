# MLflow artifact store: model weights, signatures, pip envs, plots.
# Blob rather than a PVC because artifacts must outlive the cluster — destroying
# and rebuilding the cluster should not lose the registry's contents.

resource "random_string" "storage_suffix" {
  length  = 8
  special = false
  upper   = false
}

resource "azurerm_storage_account" "mlflow" {
  name                     = "mlflow${random_string.storage_suffix.result}" # globally unique, <=24 chars, lowercase alnum
  resource_group_name       = azurerm_resource_group.this.name
  location                  = azurerm_resource_group.this.location
  account_tier              = "Standard"
  account_replication_type  = "LRS" # cheapest; this is a demo artifact store
  min_tls_version           = "TLS1_2"
  https_traffic_only_enabled = true

  tags = {
    project = var.cluster_name
  }
}

resource "azurerm_storage_container" "artifacts" {
  name                  = "mlflow-artifacts"
  storage_account_id    = azurerm_storage_account.mlflow.id
  container_access_type = "private"
}

resource "kubernetes_namespace" "mlflow" {
  metadata {
    name = "mlflow"
  }

  depends_on = [azurerm_kubernetes_cluster.this]
}

# Only the tracking server gets the storage key. The MLflow server runs with
# --serve-artifacts, so training Jobs and serving pods upload and download through
# it and never see a storage credential themselves. One secret, one holder.
#
# Production upgrade (documented, not built): AKS workload identity + Key Vault,
# so there is no long-lived key at all.
resource "kubernetes_secret" "mlflow_blob" {
  metadata {
    name      = "mlflow-blob"
    namespace = kubernetes_namespace.mlflow.metadata[0].name
  }

  data = {
    AZURE_STORAGE_CONNECTION_STRING = azurerm_storage_account.mlflow.primary_connection_string
  }

  type = "Opaque"
}
