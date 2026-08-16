# COMMANDS — P7 on GCP/GKE

Running log of every command executed for this build, with what it does and why.
Working agreement (2026-08-11): Claude runs commands, I approve each, and every one lands
here for later reference. Interactive/browser flows stay mine to run.

**Read this before an interview** — the GKE, IAM, and networking commands in here are the
ones Landmark will actually probe.

Legend: 🔵 Claude ran it · 🟢 I ran it (interactive) · ⏳ pending

---

## Phase 0 — Discovery

### 🔵 Check tooling is installed

```bash
which gcloud terraform
```

Confirms both CLIs are on PATH before anything else. Result: both present via Homebrew
(`/opt/homebrew/bin/`).

### 🔵 Inspect current gcloud configuration

```bash
gcloud config list
gcloud auth list
```

`gcloud config list` shows the active *configuration* — which project, region, zone and
account subsequent commands will default to. `gcloud auth list` shows which identities have
stored credentials and which is active.

**Why it matters:** almost every confusing gcloud error traces back to being pointed at the
wrong project or account. Check this first, always.

**Result (2026-08-11):** account `shsi@zoop.com`, project
`pc-api-5040442987508696703-427`, region `us-west1`. Both stale — Zoop account was deleted
after leaving on 2026-08-04.

### 🔵 Attempt to list projects and billing

```bash
gcloud projects list
gcloud billing accounts list
```

Failed with `invalid_grant: Account has been deleted` — the stored refresh token belongs to
a deleted identity. This is the expected failure mode when a corporate Google account is
deprovisioned; the local credential cache doesn't know until it tries to refresh.

---

## Phase 1 — Authenticate and establish the project

### 🟢 Log in (interactive — browser flow, must be run by me)

```bash
gcloud auth login
```

Opens a browser for the OAuth consent flow and stores a refresh token locally. Replaces the
dead Zoop credential with the personal account.

### 🔵 Discover what the new account has

```bash
gcloud projects list
gcloud billing accounts list
gcloud billing projects list --billing-account=014573-E85184-ECF0F8
```

- `projects list` — every project this identity can see. GCP projects are the blast-radius
  and billing boundary, roughly "an AWS account you create casually."
- `billing accounts list` — the payment instruments available. `OPEN: True` means usable.
- `billing projects list` — which projects are already attached to that billing account.
  **A project with no billing account attached cannot create most resources**, which
  produces a confusing permission-shaped error rather than a billing-shaped one.

**Result:** 3 pre-existing projects (2 auto-created by AI Studio, 1 "My First Project");
billing account `014573-E85184-ECF0F8` open, with only `heroic-light-470407-s7` linked.

### 🔵 Remove the dead Zoop credential

```bash
gcloud auth revoke shsi@zoop.com
```

Deletes the locally stored refresh token. The account was already deleted server-side, so
nothing was lost — this just stops `gcloud auth list` showing a credential that can never
work again.

---

## Phase 2 — Project setup

> **Account switch, 2026-08-11.** Built first under `shiqs90@gmail.com` / project
> `mlops-model-lifecycle-p7`, then moved: the **free trial credits live on the
> `shikha2531@gmail.com` billing account**, not the other one. Credits attach to the
> *billing account*, not to a project — so any project linked to that billing account is
> covered.
>
> Project IDs are **globally unique across all of GCP** and a released ID is held for ~30
> days, so the rebuild needed a new ID: **`mlops-lifecycle-p7-gke`**.
> `mlops-model-lifecycle-p7` is orphaned under `shiqs90` (empty apart from an auto-created
> default VPC, $0) — delete later.

**Final values for this build:**

| | |
|---|---|
| Account | `shikha2531@gmail.com` |
| Project ID | `mlops-lifecycle-p7-gke` |
| Project number | `166750278291` |
| Billing account | `01C1F2-F8BF41-416ADD` (has trial credits) |
| Region / zone | `us-west1` / `us-west1-b` |

### 🔵 Create a dedicated project, link billing, make it the default

```bash
gcloud config set account shikha2531@gmail.com
gcloud projects create mlops-lifecycle-p7-gke --name="P7 MLOps Model Lifecycle"
gcloud billing projects link mlops-lifecycle-p7-gke --billing-account=01C1F2-F8BF41-416ADD
gcloud config set project mlops-lifecycle-p7-gke
```

**Why a dedicated project rather than reusing "My First Project":** a GCP project is the
billing *and* blast-radius boundary. `gcloud projects delete` therefore removes every
resource inside it in one command — the strongest possible teardown guarantee against an
accidental month of GKE charges. It's also the professionally correct answer, and it's the
GCP resource-hierarchy concept an interviewer probes (Org → Folder → Project → Resource,
with IAM inheriting downward).

**The three identifiers — don't confuse them:**

| | Value | Used by |
|---|---|---|
| Project **name** | `P7 MLOps Model Lifecycle` | Humans, Console display |
| Project **ID** | `mlops-lifecycle-p7-gke` | **Terraform, gcloud, APIs** ← the one you want |
| Project **number** | `166750278291` | IAM principals, service identities |

```bash
gcloud config get-value project                     # what gcloud acts on right now
gcloud projects describe mlops-lifecycle-p7-gke   # all three identifiers
```

### 🔵 Enable the APIs this build needs

```bash
gcloud services enable \
  compute.googleapis.com \
  container.googleapis.com \
  iam.googleapis.com \
  iamcredentials.googleapis.com \
  cloudresourcemanager.googleapis.com \
  artifactregistry.googleapis.com \
  sqladmin.googleapis.com \
  servicenetworking.googleapis.com \
  secretmanager.googleapis.com \
  --project=mlops-lifecycle-p7-gke
```

| API | Why |
|---|---|
| `compute` | VPC, subnets, firewall — and GKE nodes are Compute instances underneath |
| `container` | GKE itself |
| `iam` + `iamcredentials` | Service accounts, and token *impersonation* — `iamcredentials` is the one Workload Identity needs, and it's easy to miss |
| `cloudresourcemanager` | Terraform reading/setting project-level IAM |
| `artifactregistry` | Container images (the ECR equivalent) |
| `sqladmin` | Cloud SQL for the MLflow backend store |
| `servicenetworking` | Private IP for Cloud SQL — VPC peering to Google's service producer network |
| `secretmanager` | MLflow DB credentials |

**Enabling an API is free.** GCP bills for *resources*, not for APIs being switched on.
Nothing here costs anything until a cluster or database is created.

**Verify:**
```bash
gcloud services list --enabled --project=mlops-lifecycle-p7-gke --format='value(config.name)' | sort
```
The list comes back longer than what you asked for — GCP auto-enables dependencies, and
new projects have BigQuery/logging/monitoring on by default.

### 🔵 Side effect to know about: the default VPC

```bash
gcloud compute networks list --project=mlops-lifecycle-p7-gke
```

Enabling `compute.googleapis.com` **auto-creates a `default` VPC in auto-subnet mode** —
one subnet in every region, with permissive default firewall rules. It's free, but it isn't
something you asked for.

We build our own VPC instead, because auto-mode can't do the **secondary IP ranges** a
VPC-native GKE cluster needs for pods and services. Deleting the default network is common
practice in real landing zones (and enforceable via the `compute.skipDefaultNetworkCreation`
Org Policy constraint).

### 🟢 Application Default Credentials (interactive — must be run by me)

```bash
gcloud auth application-default login
```

**The trip-up worth remembering:** `gcloud auth login` and `gcloud auth application-default
login` write to two *different* credential stores.

- `gcloud auth login` → authenticates the **gcloud CLI itself**
- `gcloud auth application-default login` → writes
  `~/.config/gcloud/application_default_credentials.json`, which is what **client libraries
  and Terraform's Google provider** read

So gcloud can be working perfectly while `terraform plan` fails with *"could not find
default credentials."* Two stores, two logins.

**Verify:**
```bash
gcloud auth application-default print-access-token >/dev/null && echo OK
```

---

## Phase 3 — GKE cluster via Terraform

*(next — VPC with secondary ranges, then the cluster)*

---

## Phase 3 — GKE cluster via Terraform

*(to be filled as we go)*

---

## Phase 4 — MLflow, Cloud SQL, GCS

*(to be filled as we go)*

---

## Phase 5 — Argo CD + Argo Rollouts

*(to be filled as we go)*

---

## Phase 6 — GitHub Actions pipeline

*(to be filled as we go)*

---

## Teardown

*(to be filled — every billable resource, with the command to destroy it)*
