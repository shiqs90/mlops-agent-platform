# Nova — Status

Living to-do tracker. Update the checkbox and date when a step lands. Design rationale lives in
`IMPLEMENTATION-PLAN.md`; the interview-facing summary is `README.md`. This file is just
"what's done, what's next."

Last updated: 2026-08-16

**Rescoped 2026-08-15/16** — from a DistilBERT/banking77 classifier pipeline to an MLOps
platform for a tool-calling agent, where evaluation is the deployment gate. GitOps delivery was
moved out of the core build into enhancements: a deployment gate is only meaningful once there
is something worth gating.

## Carried forward — still valid

- [x] Platform decision: GCP / GKE, no GPU, standalone
- [x] `terraform-gcp/` — VPC, subnet, secondary ranges, Cloud Router, IAP-only SSH ($0, applied)
- [x] `terraform-gcp/gke.tf` + `artifact-registry.tf` written, **not yet applied**
- [x] GCS infra in place
- [x] GitOps + blue-green rollout strategy and its rationale
- [x] Gate layering (CI contract → CI smoke → pre-promotion → post-promotion)
- [x] `IMPLEMENTATION-PLAN.md` rewritten for the new scope
- [x] `README.md` written — coverage table, architecture, gates, metrics, time estimate
- [x] Repo renamed to `mlops-agent-platform`

## Superseded

- [x] ~~DistilBERT + banking77 as the product~~ — returns in **E4** as the cheap router, with a
      cost justification instead of being the deliverable
- [x] ~~MLflow as the central registry from day one~~ — deferred to **E4**, where a trained
      artifact exists
- [x] ~~Seeded retail/ops dataset~~ — replaced by banking, which makes PII masking and human
      approval genuinely required rather than decorative
- [ ] Azure/AKS track (`terraform/`) — parked, kept as a JD-B asset, not deleted
- [ ] `docs/architecture.md`, `docs/mlops-lifecycle.md` — still describe the classifier design,
      revise after Phase 2

## Housekeeping

- [ ] `git init` — there's a `.github/` and `.gitignore` but no repo yet
- [ ] Check `authorized_cidr` (`variables.tf:90`) against current public IP before applying —
      `curl -s -4 ifconfig.me`. Stale value = `kubectl` hangs with no clear error.

---

# CORE BUILD (~39h)

End state: a working agent with its safety controls in place (PII masking, approval on writes),
plus an automated evaluation service that scores it on demand and on a schedule, and alerts when
quality drifts.

## Phase 0 — Infrastructure (~2h) — DONE 2026-08-16

- [x] GKE zonal cluster `mlops-lifecycle` applied — `RUNNING`, us-west1-b
- [x] Node pool `primary` — `e2-standard-4`, 50 GB, GKE 1.35.6
- [x] Artifact Registry `mlops-lifecycle` — DOCKER, us-west1, terraform-provisioned
- [x] kubectl context configured via `get-credentials`
- [x] Nodes scaled to 0 between sessions (cluster + pool retained)

## Phase 1 — Data and MCP connectors (~10h)

*Scope grew by ~2h: secrets management was added rather than using `kubectl create secret`,
because an imperative Secret lives nowhere in git and breaks the reconstruct-from-repo property
E1 depends on.*

- [x] **Scale nodes up** — 2 × `e2-standard-4` Ready (2026-08-16). Nothing schedules at 0:
      ```bash
      gcloud container clusters resize mlops-lifecycle --node-pool=primary --num-nodes=2 \
        --zone=us-west1-b --project=mlops-lifecycle-p7-gke --quiet
      ```

### 1a — Secrets (Secret Manager → ESO → cluster)

- [x] `terraform-gcp/secrets.tf` written and **applied** (2026-08-16) — Secret Manager secret
      `nova-postgres-password`, ESO service account, scoped `secretAccessor`, Workload Identity binding
- [ ] Helm install External Secrets Operator into `external-secrets` ns, with the
      `iam.gke.io/gcp-service-account` annotation from `terraform output external_secrets_sa_email`
- [ ] **Check CRD API version** — `kubectl api-resources | grep external-secrets`. If `v1` rather
      than `v1beta1`, edit both apiVersion lines in `k8s/external-secrets.yaml` first
- [ ] Apply `k8s/external-secrets.yaml` (SecretStore + ExternalSecret)
- [ ] verify: `kubectl get externalsecret -n nova` reaches `SecretSynced`

### 1b — Postgres

- [x] `k8s/postgres.yaml` written — StatefulSet, headless Service, 10Gi PVC
- [x] `db/schema.sql` written — 6 tables incl. `ingest_runs` lineage
- [ ] Apply `k8s/postgres.yaml` (pod waits in `CreateContainerConfigError` until 1a completes —
      expected, not a failure)
- [ ] Load schema: `kubectl exec -i -n nova postgres-0 -- psql -U nova -d nova < db/schema.sql`
- [ ] verify: `\dt` lists accounts, cards, customers, ingest_runs, loans, transactions

### 1c — Seed data

- [ ] Seed script — ~2k customers, 3k accounts, 200k transactions, cards, loans
- [ ] Run as a K8s Job; writes an `ingest_runs` row with the source commit
- [ ] verify: row counts match; every data row has a non-null `ingest_run_id`

### 1d — MCP connectors

- [ ] `mcp-accounts` — balance, account details, overdraft limits
- [ ] `mcp-transactions` — history, search, categorisation
- [ ] `mcp-products` — cards, loans, rates
- [ ] verify: each server responds to tool-list; a SQL query and its tool call agree

## Phase 2 — Nova agent (~8h)

- [ ] FastAPI + LangChain `create_agent`
- [ ] MCP adapter binding the three servers as tools
- [ ] Redis StatefulSet + LangGraph checkpointer (session memory)
- [ ] `trace_id` issued on request entry, returned in response header
- [ ] verify: single-tool question correct; "and last month?" resolves from memory

## Phase 2.5 — PII masking + human approval middleware (~2h)

LangChain prebuilt middleware. Banking makes both **required, not decorative**.

- [ ] PII masking — mask names, account numbers, balances before the prompt leaves the boundary
- [ ] Human-in-the-loop approval — pause before consequential tool calls
      (**not optional once `initiate_transfer` exists**)
- [ ] verify: an account number never appears in the outbound prompt (check the Langfuse trace);
      a transfer request halts and waits rather than executing

## Phase 3 — Observability (~5h)

- [ ] **Decide: Langfuse self-hosted vs cloud free tier**
- [ ] Langfuse + LangChain callback handler
- [ ] kube-prometheus-stack
- [ ] Nova metrics: rate, errors, p50/p95/p99, tool counts + latency by tool, tokens/request
- [ ] verify: one question → one trace with nested LLM/tool/LLM spans; header `trace_id` matches

## Phase 4 — Evaluation Hub (~10h)

- [ ] Generate ~30 candidate questions from tool schemas; curate to ~18
- [ ] Tag `expect_tools` + `expect_params`; SQL-computed answers where a literal is wanted
- [ ] Empty regression set scaffolded
- [ ] `EvaluationRun` CRD + RBAC
- [ ] kopf controller — watch CRs → spawn Job → patch status
- [ ] Runner image — replay, score **four** metrics, write Postgres
- [ ] `/metrics` endpoint scraped by Prometheus
- [ ] verify: `kubectl apply` an EvaluationRun → Job runs → CR status shows four scores → same in Prometheus

## Phase 4.5 — Drift Tier 1, scheduled replay (~2h)

- [ ] CronJob creating a scheduled `EvaluationRun` against production
- [ ] PrometheusRule alerting on score thresholds
- [ ] verify: a scheduled run appears with no human action; degrade a threshold, alert fires

---

# ENHANCEMENTS (~26h)

## E1 — GitOps delivery (~9h)

**The phase that makes evaluation an actual deployment gate.** Until this lands, the Hub
produces a verdict but nothing consumes it.

- [ ] Argo CD Applications (app-of-apps)
- [ ] Argo Rollouts blue-green; **set `scaleDownDelaySeconds` to several minutes**
- [ ] `prePromotionAnalysis` — quality **and** cost via Prometheus provider
- [ ] `postPromotionAnalysis` — live metrics
- [ ] GitHub Actions: contract checks → commit manifest → smoke test
- [ ] verify: prompt change ships with no `kubectl`; bad prompt blocked + auto-rolled-back;
      token-wasteful prompt blocked by the **cost** gate while passing quality

## E2 — Drift Tier 2, live-traffic scoring (~4h)

- [ ] Sample production traces, score `groundedness` (reference-free)
- [ ] Track score distribution over time
- [ ] Promote failures into the regression set
- [ ] verify: a question shape absent from the golden set appears in sampled scores and lands
      in the regression set

## E3 — Cheap router: train a classifier to replace the LLM for tool selection (~8h)

*Model distillation — a small "student" model trained on the LLM "teacher's" past decisions.*

- [ ] Export eval-passing traces → `(question, tool_chosen)` training set
- [ ] In-cluster training Job (PyTorch / HF Transformers)
- [ ] MLflow + Postgres backend + GCS artifacts (registry + experiment tracking)
- [ ] Nova routes via classifier above a confidence threshold, falls back to Claude
- [ ] verify: promotes only if `tool_selection` ≥ Claude baseline; record cost-per-request delta

## E4 — Hardening (~5h)

- [ ] Break it at all five stages, document each diagnosis path
- [ ] Verify teardown commands
- [ ] Final README pass

## Notes

- **Models:** `claude-haiku-4-5` for the agent, `claude-sonnet-5` for the LLM judge. Pin the
  judge — an upgrade changes scores with no change to the agent.
- **Secrets — accepted tradeoff.** Terraform generates the Postgres password with
  `random_password`, so it sits in HCP state in plaintext (twice: `result` and `secret_data`).
  Chosen for apply-completeness — `terraform apply` alone leaves a working system. Fine on a
  personal workspace; on a shared one, "anyone who can read state" is a much wider set than
  "anyone who should know the DB password". Two tiers above this, worth being able to name:
  inject the value out-of-band so state never holds it, or have no static password at all
  (Cloud SQL IAM auth / Vault dynamic credentials — ruled out by the in-cluster Postgres
  cost decision).
- **Rotation:** add a new Secret Manager version (console or CLI), then
  `kubectl rollout restart statefulset/postgres -n nova`. ESO refreshes hourly, but Postgres
  reads the password once at startup — a rotation nothing restarts is a silent no-op.
- **Between sessions:** scale the node pool to zero, don't destroy.
  ```bash
  gcloud container clusters resize mlops-lifecycle --node-pool=primary --num-nodes=0 \
    --zone=us-west1-b --project=mlops-lifecycle-p7-gke --quiet
  ```
- **Budget:** no GPU. ~$0.29–0.38/hr node time + ~$0.17/eval run. $10 ceiling.
- **GCP project stays `mlops-lifecycle-p7-gke`** — project IDs are immutable; the repo rename
  doesn't touch it. Same for the HCP workspace `mlops-model-lifecycle-gcp`.
- **Total:** ~63h work, ~79h elapsed with the command round-trip workflow. Core build alone is
  ~35h and is a demonstrable milestone on its own.
