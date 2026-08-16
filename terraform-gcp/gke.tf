# Dedicated node service account.
#
# GKE defaults nodes to the Compute Engine default service account, which holds the
# project-wide Editor role. Every pod that can reach the node metadata server then inherits
# it. This SA gets only what a node genuinely needs, which is the standard finding in any
# GKE security review.
resource "google_service_account" "node" {
  account_id   = "${var.cluster_name}-node"
  display_name = "GKE node pool service account"
}

# The documented minimum for a functioning node: ship logs and metrics, pull images.
resource "google_project_iam_member" "node" {
  for_each = toset([
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/monitoring.viewer",
    "roles/stackdriver.resourceMetadata.writer",
    "roles/artifactregistry.reader",
  ])

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.node.email}"
}

resource "google_container_cluster" "primary" {
  name = var.cluster_name

  # A zone here (not a region) is what makes this a ZONAL cluster — one control plane
  # instead of three. See the `zone` variable for the tradeoff.
  location = var.zone

  # TRAP: the in-line default node pool cannot be fully managed by Terraform — changing it
  # later forces cluster replacement. The idiom is to create it, throw it away, and manage
  # a real google_container_node_pool separately. initial_node_count is required even
  # though the pool is immediately removed.
  remove_default_node_pool = true
  initial_node_count       = 1

  # TRAP: defaults to true in Google provider 6.x, and `terraform destroy` then fails
  # outright with no way to override at destroy time. False for a lab; a production
  # cluster should leave it on.
  deletion_protection = false

  network    = google_compute_network.vpc.id
  subnetwork = google_compute_subnetwork.subnet.id

  # VPC-native: pods get real, routable VPC addresses from the subnet's secondary ranges
  # rather than an overlay. This is what allows container-native load balancing — the
  # Google front end targets pod IPs directly through NEGs, skipping the
  # nodeport -> kube-proxy -> other-node hop that a routes-based cluster requires.
  networking_mode = "VPC_NATIVE"
  ip_allocation_policy {
    cluster_secondary_range_name  = "pods"
    services_secondary_range_name = "services"
  }

  private_cluster_config {
    # Nodes get no external IPs. Egress to the internet goes through Cloud NAT; egress to
    # Google APIs goes through Private Google Access, set on the subnet. Both are needed —
    # missing either shows up as image pulls that fail like a network timeout.
    enable_private_nodes = true

    # Control plane endpoint stays PUBLIC so kubectl works without a bastion, but is
    # reachable only from master_authorized_networks_config below.
    #
    # The stricter production shape is enable_private_endpoint = true, reached via IAP TCP
    # forwarding, Cloud VPN, or the GKE Connect Gateway — that is what CIS GKE Benchmark
    # asks for. Not used here because the bastion-plus-tunnel friction on every kubectl
    # buys nothing this project is testing.
    enable_private_endpoint = false

    master_ipv4_cidr_block = var.master_ipv4_cidr
  }

  master_authorized_networks_config {
    cidr_blocks {
      cidr_block   = var.authorized_cidr
      display_name = "laptop"
    }
  }

  # Workload Identity — the IRSA equivalent. A Kubernetes ServiceAccount is bound to a
  # Google service account, and the GKE metadata server hands the pod short-lived tokens.
  # No static service-account key ever exists, which is the whole point.
  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }

  # REGULAR: patched within weeks of release, not on release day (RAPID) and not months
  # behind (STABLE). Auto-upgrade is on by default in a channel.
  release_channel {
    channel = "REGULAR"
  }

  # No maintenance policy set here because nothing runs overnight. In production this is
  # where a peak-trading exclusion window goes, so Google cannot upgrade the control plane
  # during a sale.

  addons_config {
    http_load_balancing {
      disabled = false
    }
    horizontal_pod_autoscaling {
      disabled = false
    }
  }

  lifecycle {
    ignore_changes = [
      # GKE rewrites this as nodes are added and removed; it is not a real diff.
      node_config,
    ]
  }
}

resource "google_container_node_pool" "primary" {
  name     = "primary"
  cluster  = google_container_cluster.primary.id
  location = var.zone

  node_count = var.node_count

  node_config {
    machine_type = var.machine_type
    disk_size_gb = var.node_disk_size_gb
    disk_type    = "pd-balanced"

    service_account = google_service_account.node.email

    # cloud-platform scope with a least-privilege service account is the current guidance:
    # the SA's IAM roles are the real boundary, and narrow scopes break more than they fix.
    oauth_scopes = ["https://www.googleapis.com/auth/cloud-platform"]

    # GKE_METADATA runs the metadata server that makes Workload Identity work, and blocks
    # pods from reading the legacy node metadata endpoint. Without this, workload_identity
    # on the cluster does nothing at the pool level.
    workload_metadata_config {
      mode = "GKE_METADATA"
    }

    # Matches the target_tags on the IAP SSH firewall rule in network.tf.
    tags = ["${var.cluster_name}-node"]

    shielded_instance_config {
      enable_secure_boot          = true
      enable_integrity_monitoring = true
    }

    labels = {
      pool = "primary"
    }
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }

  # Surge upgrade: add one node, then drain one. max_unavailable = 0 keeps capacity flat
  # through a roll. Pair with PodDisruptionBudgets so a drain cannot take a service below
  # its minimum.
  upgrade_settings {
    max_surge       = 1
    max_unavailable = 0
  }
}
