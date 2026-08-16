# Cloud NAT — the one billable resource in the network stack (~$0.05/hr + data processed),
# which is why it lives here rather than in network.tf.
#
# Private nodes have no external IP, so without this they cannot reach the public internet:
# no pip install, no Hugging Face model download, no Docker Hub base image. Google APIs are
# a separate path entirely — those go over Private Google Access, set on the subnet.
#
# Missing NAT and missing Private Google Access produce the same-looking symptom (a hang,
# then a timeout) for different destinations. Knowing which of the two is at fault is a
# question of *what* failed to resolve, not of the error text.
resource "google_compute_router_nat" "nat" {
  name   = "${var.cluster_name}-nat"
  router = google_compute_router.router.name
  region = var.region

  # AUTO_ONLY lets Google allocate and scale the external IPs. A production setup often
  # pins static IPs instead, so that partners can allowlist a known egress address.
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"

  # Errors only. Logging every successful translation is a large, and largely useless,
  # Cloud Logging bill.
  log_config {
    enable = true
    filter = "ERRORS_ONLY"
  }
}
