# CI identity for GitHub Actions.
#
# ACCEPTED TRADEOFF — chosen 2026-08-25, know how to defend it AND how to attack it.
# This grants GitHub a LONG-LIVED service account key. The modern answer is Workload
# Identity Federation: GitHub's OIDC token is exchanged for a credential that lives
# minutes, and no secret is stored in the repo at all. WIF was rejected here for speed,
# not because it is wrong. What that costs, stated plainly:
#
#   - the key never expires; rotation is a manual act nobody is reminded to perform
#   - anyone with repo admin can read it out of Actions secrets
#   - a leaked key is valid until someone notices and revokes it, with no OIDC
#     `repository` claim narrowing who could have used it
#
# Migrating later is ~40 lines here plus swapping `credentials_json` for
# `workload_identity_provider` in the workflow. Nothing else changes.
#
# The KEY ITSELF IS NOT CREATED HERE, deliberately. `google_service_account_key` writes
# the private key into Terraform state in plaintext, and HCP state is readable by
# anyone on the workspace — the same trap already documented for the Postgres password,
# but worse, because this one grants registry write. Mint it out-of-band instead:
#
#   gcloud iam service-accounts keys create /tmp/gha-key.json \
#     --iam-account=$(terraform -chdir=terraform-gcp output -raw cicd_service_account) \
#     --project=mlops-lifecycle-p7-gke
#
# then paste the file contents into the repo secret GCP_SA_KEY and delete it locally.

resource "google_service_account" "cicd" {
  account_id   = "gha-cicd"
  display_name = "GitHub Actions — build images, push to Artifact Registry"
  project      = var.project_id
}

# Least privilege that still works. Each role is here for one reason:
locals {
  cicd_roles = [
    # submit builds to Cloud Build. Cloud Build rather than docker/buildx in the runner
    # because it builds natively on amd64 — building for GKE nodes from Apple silicon
    # produced `exec format error` and cost a debugging session (war story #8).
    "roles/cloudbuild.builds.editor",

    # push the resulting image. Writer, not admin: CI publishes, it never deletes tags.
    "roles/artifactregistry.writer",

    # Cloud Build runs AS its own service account. Submitting a build means acting as
    # that identity, which is a separate permission from being allowed to build.
    "roles/iam.serviceAccountUser",

    # `serviceusage.services.use` — required to consume project quota when calling the
    # Cloud Build API. Consumer, not Admin: this identity needs to USE enabled services,
    # never to enable or disable them.
    "roles/serviceusage.serviceUsageConsumer",

    # Read Cloud Logging, so `gcloud builds submit` can STREAM build output.
    #
    # Without it the build runs, succeeds, and pushes the image — and gcloud still
    # exits 1, because it could not tail the logs:
    #
    #   ERROR: (gcloud.builds.submit)
    #   The build is running, and logs are being written to the default logs bucket.
    #   This tool can only stream logs if you are Viewer/Owner of the project...
    #
    # A green build reported as a red job, which in this pipeline means the image is
    # published but the tag is never committed, so nothing deploys. `--suppress-logs`
    # would also fix the exit code, at the cost of having no build output in CI the
    # next time something genuinely breaks. Grant the read instead.
    "roles/logging.viewer",
  ]
}

# The Cloud Build staging bucket, scoped to that ONE bucket rather than granted
# project-wide.
#
# WHY NOT roles/storage.objectAdmin (which this replaces): object roles grant CRUD on
# objects but nothing on the bucket itself. `gcloud builds submit` calls
# storage.buckets.get on <project>_cloudbuild BEFORE it uploads the source tarball, so
# the run dies at the bucket check with:
#
#   ERROR: (gcloud.builds.submit) The user is forbidden from accessing the bucket
#   [<project>_cloudbuild]. Please check your organization's policy or if the user has
#   the "serviceusage.services.use" permission.
#
# That message points at serviceusage and is misleading — the missing permission is on
# storage. Worth remembering as a class of bug: GCP surfaces several distinct denials
# through one error string, so read the RESOURCE named, not the permission suggested.
#
# Bucket-scoped rather than roles/storage.admin at project level, which would also hand
# CI full control of the Terraform state bucket and anything else in the project. This
# bucket is created by Google on the first build in a project; if it does not exist yet,
# run one build as yourself first, or this binding has nothing to attach to.
resource "google_storage_bucket_iam_member" "cicd_cloudbuild_staging" {
  bucket = "${var.project_id}_cloudbuild"
  role   = "roles/storage.admin"
  member = "serviceAccount:${google_service_account.cicd.email}"
}

resource "google_project_iam_member" "cicd" {
  for_each = toset(local.cicd_roles)
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.cicd.email}"
}
