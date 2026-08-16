resource "azurerm_resource_group" "this" {
  name     = "rg-${var.cluster_name}"
  location = var.location

  tags = {
    project = var.cluster_name
  }
}

# CPU-only cluster. This project serves DistilBERT (~250 MB), so there is no GPU
# anywhere in it — the GPU serving work lives in the vllm-serving-eks and
# llm-argo-canary projects. Paying for a GPU to prove a CI/CD pipeline buys nothing.
#
# Sized against a hard ceiling of 10 Total Regional vCPUs (see variables.tf).
# 6 vCPU used, 4 left for upgrade surge.
resource "azurerm_kubernetes_cluster" "this" {
  name                = var.cluster_name
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  dns_prefix          = var.cluster_name
  kubernetes_version  = var.kubernetes_version
  sku_tier            = "Free" # control plane costs $0 (no uptime SLA — fine here)

  identity {
    type = "SystemAssigned"
  }

  # Platform pool: kube-system plus the long-lived services (Argo CD, Argo Rollouts,
  # Prometheus, Grafana, MLflow, Postgres).
  #
  # Deliberately NOT tainted. `only_critical_addons_enabled` would keep Argo CD and
  # Prometheus off this node, which is the opposite of what's wanted, and a hard taint on
  # the other pool risks leaving platform pods Pending if 8 GB proves tight. Placement is
  # steered from the workload side instead: training and serving pods carry a
  # `nodeSelector` for pool=workload, so they always land on the 4-vCPU node. Platform
  # pods stay schedulable anywhere, which is the safer failure mode.
  default_node_pool {
    name       = "system"
    vm_size    = var.system_vm_size
    node_count = 1

    # 64 GB is plenty for these images and keeps the managed-disk bill down; the default
    # 128 GB doubles it for no benefit. Dsv6 has no local temp disk, so this is a managed
    # OS disk either way.
    os_disk_size_gb = 64

    node_labels = {
      pool = "system"
    }
  }

  tags = {
    project = var.cluster_name
  }
}

# Application pool. Separate from `system` because a 2 vCPU node cannot run a Job that
# requests 2 vCPU — after kubelet and system reservations only ~1.5 is allocatable, so the
# Job would sit Pending forever. Training needs 4 vCPU; the platform does not.
resource "azurerm_kubernetes_cluster_node_pool" "workload" {
  name                  = "workload"
  kubernetes_cluster_id = azurerm_kubernetes_cluster.this.id
  vm_size               = var.workload_vm_size
  node_count            = 1
  os_disk_size_gb       = 64

  node_labels = {
    pool = "workload"
  }

  tags = {
    project = var.cluster_name
  }
}
