# Standalone observability. This project shares nothing with the
# gpu-inference-observability project — clone this repo, apply, get Prometheus.
#
# What it's for here: the Argo Rollouts analysis gate queries Prometheus to decide
# whether a canary is promoted. That is a stronger gate than the curl Job used in
# llm-argo-canary, because a model can return HTTP 200 while predicting garbage.

resource "helm_release" "kube_prometheus_stack" {
  name             = "monitoring"
  repository       = "https://prometheus-community.github.io/helm-charts"
  chart            = "kube-prometheus-stack"
  version          = var.kube_prometheus_stack_chart_version
  namespace        = "monitoring"
  create_namespace = true
  timeout          = 900 # CRD install is slow; the 300s default sometimes loses the race

  values = [yamlencode({
    # Off: no paging in a demo, and it wants its own storage.
    alertmanager = { enabled = false }

    prometheus = {
      prometheusSpec = {
        # 6h retention keeps memory small. Long-term storage is a different problem
        # (Thanos/Mimir) and out of scope.
        retention = "6h"

        # THE gotcha. Left at its default, the operator only picks up ServiceMonitors
        # created by this Helm release, and the ones for our own FastAPI serving pods
        # are silently ignored. Symptom: correct-looking config, no targets, empty gate.
        serviceMonitorSelectorNilUsesHelmValues = false
        podMonitorSelectorNilUsesHelmValues     = false
        ruleSelectorNilUsesHelmValues           = false

        resources = {
          requests = { cpu = "200m", memory = "700Mi" }
          limits   = { memory = "1500Mi" }
        }
      }
    }

    grafana = {
      adminPassword = var.grafana_admin_password
      service       = { type = "ClusterIP" } # port-forward only
      resources = {
        requests = { cpu = "50m", memory = "128Mi" }
        limits   = { memory = "256Mi" }
      }
    }

    # Node-level metrics are cheap and answer "was the training Job starved or OOMed?"
    nodeExporter    = { enabled = true }
    kubeStateMetrics = { enabled = true }
  })]

  depends_on = [azurerm_kubernetes_cluster.this]
}
