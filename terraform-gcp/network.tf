# Network for a VPC-native GKE cluster.
#
# Everything in this file is FREE. Cloud NAT — the one billable network resource — lives
# with the cluster, because it only earns its cost once there are nodes needing egress.

# Custom-mode VPC, not auto-mode.
#
# The `default` network GCP auto-created when compute.googleapis.com was enabled is
# auto-mode: one subnet per region, chosen for you, with no secondary ranges. Auto-mode
# cannot express the pod/service ranges a VPC-native cluster needs, so we build our own.
# Deleting the default network is standard landing-zone practice; the org-policy
# constraint that prevents it being created at all is compute.skipDefaultNetworkCreation.
resource "google_compute_network" "vpc" {
  name                    = "${var.cluster_name}-vpc"
  auto_create_subnetworks = false
  routing_mode            = "REGIONAL"

  description = "VPC-native network for the P7 GKE cluster"
}

# One subnet, three ranges: nodes (primary) + pods + services (secondary).
#
# private_ip_google_access is what lets nodes with NO external IP reach Google APIs —
# Artifact Registry, Cloud SQL, Cloud Logging — over Google's internal network instead of
# the public internet. Forget it on a private cluster and image pulls fail with what looks
# like a network timeout. It is free, and there is no reason not to set it.
resource "google_compute_subnetwork" "subnet" {
  name          = "${var.cluster_name}-subnet"
  network       = google_compute_network.vpc.id
  region        = var.region
  ip_cidr_range = var.subnet_cidr

  private_ip_google_access = true

  secondary_ip_range {
    range_name    = "pods"
    ip_cidr_range = var.pods_cidr
  }

  secondary_ip_range {
    range_name    = "services"
    ip_cidr_range = var.services_cidr
  }
}

# Cloud Router — free on its own; it exists to host the Cloud NAT config added with the
# cluster. Created here so the network stack is complete and the cluster step adds only
# the one billable resource.
resource "google_compute_router" "router" {
  name    = "${var.cluster_name}-router"
  network = google_compute_network.vpc.id
  region  = var.region
}

# --- Firewall --------------------------------------------------------------
# GCP firewall rules are VPC-level and stateful, targeted by network TAG or by SERVICE
# ACCOUNT — not attached to an instance like an AWS security group. There is no NACL
# equivalent; rule priority does that job.
#
# GKE creates the rules it needs for node/pod traffic itself. This one rule exists so that
# `gcloud compute ssh` style debugging via IAP works without opening 22 to the internet.
# IAP's forwarding range is a fixed, documented Google-owned block.
resource "google_compute_firewall" "iap_ssh" {
  name    = "${var.cluster_name}-allow-iap-ssh"
  network = google_compute_network.vpc.name

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }

  # Identity-Aware Proxy TCP forwarding range. Traffic arrives from here, already
  # authenticated by IAP, so this is not equivalent to 0.0.0.0/0 on port 22.
  source_ranges = ["35.235.240.0/20"]
  target_tags   = ["${var.cluster_name}-node"]

  description = "SSH via IAP only — no public 22"
}
