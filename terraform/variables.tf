variable "location" {
  description = <<-EOT
    Azure region. eastus, westeurope and northeurope are identical on this subscription:
    Total Regional vCPUs = 10, every D family capped by that same ceiling. westeurope chosen;
    the decision is price and preference, not capability. Verified with:
      az vm list-usage --location <r> -o tsv | grep -E "Total Regional|Standard D"
  EOT
  type        = string
  default     = "westeurope"
}

variable "cluster_name" {
  description = "AKS cluster name (also used for the resource group: rg-<name>)."
  type        = string
  default     = "mlops-model-lifecycle"
}

variable "kubernetes_version" {
  description = "AKS Kubernetes version. null = let AKS pick its default (`az aks get-versions --location <loc> -o table`)."
  type        = string
  default     = null
}

# --- Node pools -----------------------------------------------------------
# THE BINDING CONSTRAINT: Total Regional vCPUs = 10 on this subscription.
# Not per-family — every D family reports limit 10 because they all share that ceiling.
#
# Two pools of different sizes, totalling 6 vCPU, for two reasons:
#
#   1. A training Job requesting 2 vCPU cannot schedule on a 2-vCPU node (the kubelet and
#      system daemons take ~0.5). It needs a 4-vCPU node. So the sizes must differ.
#   2. Surge headroom. AKS upgrades a pool by adding a surge node. A single 2-node D4 pool
#      (8 vCPU) would need 12 to roll — over the cap, permanently un-upgradeable. This is
#      llm-argo-canary war story #6, which cost a pool replacement. Split this way, rolling
#      `system` needs 8 and rolling `workload` needs 10. Both fit.
#
# v6 rather than v3: newer silicon at similar cost, and the training Job is CPU-bound so
# faster cores shorten every pipeline run. There is no v5 family offered on this
# subscription at all — v6 and v7 are, v7 skipped as too new to assume AKS support.

variable "system_vm_size" {
  description = "Platform pool. Standard_D2s_v6 = 2 vCPU / 8 GB. Runs kube-system, Argo CD, Argo Rollouts, Prometheus, Grafana, MLflow, Postgres (~4 GB of ~5.5 GB allocatable — tight but fits)."
  type        = string
  default     = "Standard_D2s_v6"
}

variable "workload_vm_size" {
  description = "Application pool. Standard_D4s_v6 = 4 vCPU / 16 GB. Runs training Jobs (2 vCPU / ~5 GB) and the serving pods, including both versions during a blue-green switch."
  type        = string
  default     = "Standard_D4s_v6"
}

# --- Helm chart pins -------------------------------------------------------
# Confirm each of these exists before the first apply, and bump the default if not:
#   helm repo add argo https://argoproj.github.io/argo-helm
#   helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
#   helm repo update
#   helm search repo argo/argo-rollouts --versions | head -5
#   helm search repo argo/argo-cd --versions | head -5
#   helm search repo prometheus-community/kube-prometheus-stack --versions | head -5

variable "argo_rollouts_chart_version" {
  description = "argo/argo-rollouts chart version. 2.41.0 is the version proven in the llm-argo-canary project."
  type        = string
  default     = "2.41.0"
}

variable "argo_cd_chart_version" {
  description = "argo/argo-cd chart version. 10.2.1 = Argo CD v3.4.5, verified present in the repo."
  type        = string
  default     = "10.2.1"
}

variable "kube_prometheus_stack_chart_version" {
  description = "prometheus-community/kube-prometheus-stack chart version. VERIFY before applying."
  type        = string
  default     = "69.7.4"
}

variable "grafana_admin_password" {
  description = "Grafana admin password. Demo only — a real deployment reads this from Key Vault."
  type        = string
  default     = "mlops-demo"
  sensitive   = true
}
