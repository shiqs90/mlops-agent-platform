# Secrets: Terraform generates the value, stores it in GCP Secret Manager, and
# External Secrets Operator pulls it into the cluster using Workload Identity.
#
# Why not `kubectl create secret`: this is a GitOps project. An imperatively-created
# Secret lives nowhere in git, so the cluster can't be reconstructed from the repo —
# the property E1 depends on. Here the reference is committed and the value is not.
#
# ACCEPTED TRADEOFF — the password IS in Terraform state, in plaintext, twice
# (random_password.result and secret_data). State lives in HCP Terraform, which
# encrypts at rest and gates access by workspace permission, so on a personal
# workspace the reader set is one person. On a shared workspace it would not be:
# "anyone who can read state" is far wider than "anyone who should know the database
# password", and state is exactly what gets pulled, backed up, and passed around
# while debugging. Chosen here for apply-completeness — `terraform apply` alone
# leaves a working system, with no second command standing between it and Postgres
# starting.
#
# The two tiers above this, worth being able to name:
#   1. Value injected out-of-band (`gcloud secrets versions add`) so state never
#      holds it — Terraform provisions only the container and IAM.
#   2. No static password at all — Cloud SQL IAM database auth or Vault dynamic
#      credentials mint a short-lived credential per pod. Ruled out here because
#      Postgres runs in-cluster as a cost decision.

resource "random_password" "postgres" {
  length = 32
  # No special characters: this value ends up in a libpq connection string and in
  # shell one-liners while debugging. Escaping bugs there cost more than the few
  # bits of entropy — 32 alphanumeric characters is still ~190 bits.
  special = false
}

resource "google_secret_manager_secret" "postgres_password" {
  secret_id = "nova-postgres-password"

  replication {
    auto {}
  }

  lifecycle {
    # Metadata added in the console isn't stripped on the next apply.
    ignore_changes = [labels, annotations]
  }
}

resource "google_secret_manager_secret_version" "postgres_password" {
  secret      = google_secret_manager_secret.postgres_password.id
  secret_data = random_password.postgres.result

  lifecycle {
    # Rotate freely in the console or by CLI without Terraform arguing.
    #
    # Strictly this is belt-and-braces: Secret Manager versions are immutable and
    # append-only, so a new version created outside Terraform becomes version 2
    # while Terraform continues to own version 1 — there is no drift to detect.
    # ESO reads `latest`, so the rotated value is what reaches the cluster.
    ignore_changes = [secret_data]
  }
}

# Anthropic API key. Container only — the value is yours, not generated, so Terraform
# never sees it. Add it after apply:
#
#   printf '%s' "$ANTHROPIC_API_KEY" \
#     | gcloud secrets versions add nova-anthropic-api-key --data-file=- \
#         --project=mlops-lifecycle-p7-gke
#
# printf rather than echo: echo appends a newline, which becomes part of the key and
# produces a 401 that looks like a wrong key rather than a formatting bug.
resource "google_secret_manager_secret" "anthropic_api_key" {
  secret_id = "nova-anthropic-api-key"

  replication {
    auto {}
  }

  lifecycle {
    ignore_changes = [labels, annotations]
  }
}

# Langfuse keys. Container only — values come from cloud.langfuse.com after apply:
#
#   printf '%s' "pk-lf-..." | gcloud secrets versions add nova-langfuse-public-key  --data-file=- --project=mlops-lifecycle-p7-gke
#   printf '%s' "sk-lf-..." | gcloud secrets versions add nova-langfuse-secret-key  --data-file=- --project=mlops-lifecycle-p7-gke
resource "google_secret_manager_secret" "langfuse_public_key" {
  secret_id = "nova-langfuse-public-key"
  replication {
    auto {}
  }

  lifecycle {
    ignore_changes = [labels, annotations]
  }
}

resource "google_secret_manager_secret" "langfuse_secret_key" {
  secret_id = "nova-langfuse-secret-key"
  replication {
    auto {}
  }

  lifecycle {
    ignore_changes = [labels, annotations]
  }
}

# ---------------------------------------------------------------------------
# Identity for External Secrets Operator
#
# Workload Identity rather than a downloaded JSON key: the pod proves who it is to
# Google with a projected token, so there is no static credential to rotate, leak,
# or accidentally commit.
# ---------------------------------------------------------------------------

resource "google_service_account" "external_secrets" {
  account_id   = "external-secrets"
  display_name = "External Secrets Operator — reads Secret Manager on behalf of the cluster"
}

# Scoped to this one secret, not project-wide secretAccessor. A second secret later
# means a second binding — deliberate friction, so the blast radius of the operator's
# identity stays legible.
resource "google_secret_manager_secret_iam_member" "eso_postgres" {
  secret_id = google_secret_manager_secret.postgres_password.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.external_secrets.email}"
}

resource "google_secret_manager_secret_iam_member" "eso_anthropic" {
  secret_id = google_secret_manager_secret.anthropic_api_key.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.external_secrets.email}"
}

resource "google_secret_manager_secret_iam_member" "eso_langfuse_public" {
  secret_id = google_secret_manager_secret.langfuse_public_key.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.external_secrets.email}"
}

resource "google_secret_manager_secret_iam_member" "eso_langfuse_secret" {
  secret_id = google_secret_manager_secret.langfuse_secret_key.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.external_secrets.email}"
}

# The Workload Identity binding. The member string is the load-bearing part:
#   serviceAccount:<PROJECT>.svc.id.goog[<K8S_NAMESPACE>/<K8S_SERVICE_ACCOUNT>]
# Those two names must match where the operator actually runs — the Helm chart's
# defaults are namespace `external-secrets`, service account `external-secrets`.
# A typo here fails at runtime with a 403 from Secret Manager, not at apply time.
resource "google_service_account_iam_member" "external_secrets_wi" {
  service_account_id = google_service_account.external_secrets.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[external-secrets/external-secrets]"
}

output "external_secrets_sa_email" {
  description = "Annotate the external-secrets Kubernetes ServiceAccount with this to complete the Workload Identity link."
  value       = google_service_account.external_secrets.email
}
