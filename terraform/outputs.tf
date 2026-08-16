output "cluster_name" {
  description = "AKS cluster name"
  value       = azurerm_kubernetes_cluster.this.name
}

output "resource_group" {
  description = "Resource group holding all project resources"
  value       = azurerm_resource_group.this.name
}

output "configure_kubectl" {
  description = "Run this to point kubectl at the cluster"
  value       = "az aks get-credentials --resource-group ${azurerm_resource_group.this.name} --name ${azurerm_kubernetes_cluster.this.name}"
}

output "mlflow_artifact_root" {
  description = "Pass to the MLflow server as --artifacts-destination"
  value       = "wasbs://${azurerm_storage_container.artifacts.name}@${azurerm_storage_account.mlflow.name}.blob.core.windows.net/"
}

output "storage_account_name" {
  description = "MLflow artifact storage account"
  value       = azurerm_storage_account.mlflow.name
}

output "argocd_initial_password" {
  description = "Get the Argo CD admin password (it is generated into a Secret, not by Terraform)"
  value       = "kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d"
}

output "port_forwards" {
  description = "Reach each UI locally — nothing in this project is internet-facing"
  value = join("\n", [
    "kubectl -n argocd     port-forward svc/argo-cd-argocd-server 8080:443   # Argo CD",
    "kubectl -n monitoring port-forward svc/monitoring-grafana 3000:80       # Grafana (admin / see var.grafana_admin_password)",
    "kubectl -n mlflow     port-forward svc/mlflow 5000:5000                 # MLflow (after step 3)",
  ])
}
