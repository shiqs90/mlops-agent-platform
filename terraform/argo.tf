# Two different Argo products, two different jobs. Worth keeping straight:
#
#   Argo Rollouts — progressive delivery. Replaces Deployment with Rollout and runs
#                   the canary steps + analysis gates.
#   Argo CD       — GitOps delivery. Watches a git repo and makes the cluster match it.
#
# Together: CI commits a model-version bump to git, Argo CD syncs the Rollout,
# Argo Rollouts executes the canary. CI itself never holds cluster credentials.

resource "helm_release" "argo_rollouts" {
  name             = "argo-rollouts"
  repository       = "https://argoproj.github.io/argo-helm"
  chart            = "argo-rollouts"
  version          = var.argo_rollouts_chart_version
  namespace        = "argo-rollouts"
  create_namespace = true

  # Expose the controller's metrics to the kube-prometheus-stack Prometheus so
  # rollout state (phase, canary weight, analysis result) is visible on a dashboard.
  set {
    name  = "controller.metrics.enabled"
    value = "true"
  }

  set {
    name  = "controller.metrics.serviceMonitor.enabled"
    value = "true"
  }

  depends_on = [helm_release.kube_prometheus_stack]
}

resource "helm_release" "argo_cd" {
  name             = "argo-cd"
  repository       = "https://argoproj.github.io/argo-helm"
  chart            = "argo-cd"
  version          = var.argo_cd_chart_version # 10.2.1 -> Argo CD v3.4.5
  namespace        = "argocd"
  create_namespace = true

  # No values overrides needed. The chart already defaults to what this cluster wants:
  # redis-ha off, one replica of each component, and server.service.type = ClusterIP.
  # Reach the UI with `kubectl port-forward` — nothing here is internet-facing.
  #
  # Setting them explicitly would only add keys that can be renamed between chart majors,
  # for no behaviour change.

  depends_on = [azurerm_kubernetes_cluster.this]
}
