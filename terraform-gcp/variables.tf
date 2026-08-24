variable "project_id" {
  description = <<-EOT
    GCP project ID (NOT the display name, NOT the number). Dedicated project so that
    `gcloud projects delete` is a complete teardown — the strongest guardrail against a
    forgotten GKE cluster billing for a month.
      gcloud projects describe mlops-lifecycle-p7-gke
  EOT
  type        = string
  default     = "mlops-lifecycle-p7-gke"
}

variable "region" {
  description = "GCP region. us-west1 (Oregon) — already the gcloud default on this machine, and among the cheaper US regions."
  type        = string
  default     = "us-west1"
}

variable "zone" {
  description = <<-EOT
    Zone for the ZONAL GKE cluster. Zonal, not regional, on purpose:

      - regional = control plane replicated across 3 zones, and 3x the node count by default
      - zonal    = one control-plane zone, and it is the shape the GKE free tier covers

    A regional control plane is the right production answer (it survives a zone outage);
    for a lab it triples cost to demonstrate nothing this project is about. Know the
    tradeoff, pick zonal here.
  EOT
  type        = string
  default     = "us-west1-b"
}

variable "cluster_name" {
  description = "Name for the GKE cluster and the prefix for network resources."
  type        = string
  default     = "mlops-lifecycle"
}

# --- IP address planning ---------------------------------------------------
# This is a real design task on GKE, not boilerplate. A VPC-native cluster gives every pod
# a REAL, ROUTABLE VPC address out of a secondary range on the subnet — not an overlay.
# Two consequences:
#
#   1. The load balancer can target pod IPs directly (container-native LB via NEGs),
#      skipping the nodeport -> kube-proxy -> other-node double hop.
#   2. You can run out of addresses. GKE reserves a /24 per node (default 110 max pods per
#      node rounds up to 256 addresses), so the pod range caps cluster size permanently:
#
#         pod range /16  ->  2^(24-16) = 256 nodes
#         pod range /18  ->  2^(24-18) =  64 nodes
#         pod range /20  ->  2^(24-20) =  16 nodes
#
#      The secondary range CANNOT be resized in place. Undersize it and the fix is a new
#      cluster. This is the GKE capacity-planning question an interviewer actually asks.
#
# Sized generously here because address space is free — the cost of being wrong is
# asymmetric.

variable "subnet_cidr" {
  description = "Primary range — NODE addresses. /20 = 4094 usable, far more nodes than this project will run."
  type        = string
  default     = "10.10.0.0/20"
}

variable "pods_cidr" {
  description = "Secondary range — POD addresses. /16 supports 256 nodes at a /24 per node. Cannot be resized later."
  type        = string
  default     = "10.20.0.0/16"
}

variable "services_cidr" {
  description = "Secondary range — ClusterIP SERVICE addresses. /20 = 4096 services."
  type        = string
  default     = "10.30.0.0/20"
}

# --- Cluster ---------------------------------------------------------------

variable "authorized_cidr" {
  description = <<-EOT
    Public IP allowed to reach the Kubernetes API. The control plane endpoint is public
    (so kubectl works without a bastion) but reachable only from here.

    This is a residential IP and WILL rotate. When kubectl starts hanging with a timeout
    rather than a clear error, that is this. Fix:
      curl -s -4 ifconfig.me                       # new address
      update the ip addess below
      terraform apply
  EOT
  type        = string
  default     = "86.98.166.216/32"   # rotated 2026-08-24
}

variable "machine_type" {
  description = <<-EOT
    e2-standard-4 = 4 vCPU / 16 GB, ~$0.134/hr.

    One 4-vCPU node satisfies the training Job (2 vCPU / ~5 GB) with room for serving pods.
    A 2-vCPU node cannot: after kubelet and system reservations only ~1.5 vCPU is
    allocatable, so the Job sits Pending forever. That trap cost a pool redesign on the
    Azure track.
  EOT
  type        = string
  default     = "e2-standard-4"
}

variable "node_count" {
  description = <<-EOT
    Two nodes, not one larger one: a training Job on a separate kubelet cannot starve the
    serving pods. Scale to 0 between sessions rather than destroying the cluster.
  EOT
  type        = number
  default     = 2
}

variable "node_disk_size_gb" {
  description = "50 GB pd-balanced per node. The 100 GB default is ~2x the disk cost for space this never uses."
  type        = number
  default     = 50
}

variable "master_ipv4_cidr" {
  description = "Private range for the GKE-managed control plane. /28 is required, and it must not overlap the VPC or its secondary ranges."
  type        = string
  default     = "172.16.0.0/28"
}
