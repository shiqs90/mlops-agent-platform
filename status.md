# Nova — Status

Living to-do tracker. Update the checkbox and date when a step lands. Design rationale lives in
`IMPLEMENTATION-PLAN.md`; the interview-facing summary is `README.md`. This file is just
"what's done, what's next."

Last updated: 2026-08-19

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

- [x] `git init` — repo live, 7 commits through 2026-08-18
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

### 1a — Secrets (Secret Manager → ESO → cluster) — DONE 2026-08-16

- [x] `terraform-gcp/secrets.tf` applied — Secret Manager secret `nova-postgres-password`,
      ESO service account, scoped `secretAccessor`, Workload Identity binding
- [x] External Secrets Operator installed via Helm with the `iam.gke.io/gcp-service-account`
      annotation
- [x] CRDs registered as **`external-secrets.io/v1`**, not `v1beta1` — both apiVersion lines in
      `k8s/external-secrets.yaml` were edited to match. Re-check after any chart upgrade; the
      CRDs decide, not the docs.
- [x] `k8s/external-secrets.yaml` applied — SecretStore + ExternalSecret reached `SecretSynced`

### 1b — Postgres — DONE 2026-08-16

- [x] `k8s/postgres.yaml` — StatefulSet, headless Service, 10Gi PVC, `postgres-0` Running
- [x] `db/schema.sql` loaded — 6 tables incl. `ingest_runs` lineage

### 1c — Seed data — DONE 2026-08-16

- [x] `db/seed.py` + `k8s/seed-job.yaml` (ConfigMap-mounted script, no image build)
- [x] Loaded: `ingest_run_id=ING-20260801-42` — 2,000 customers / 3,000 accounts /
      ~200,397 transactions / 2,488 cards / 589 loans
- [x] verify: 0 balance mismatches (balance == transaction net)
- [x] **Bug found and fixed:** 397 accounts (13%) held balances their type doesn't permit —
      savings and fixed deposits overdrawn against a 0 limit, worst −163k. Cause: transactions
      assigned to random accounts regardless of account type. Fixed by emitting an
      `opening_balance` credit for any account below its floor, **not** by clamping the balance
      — clamping would break `balance == sum(transactions)` and let Nova contradict itself
      across two questions in one conversation. Current accounts legitimately overdrawn within
      their limit were left alone; they're the interesting cases for overdraft questions.

### 1d — MCP connectors — DONE 2026-08-16

- [x] One image, three entrypoints: `mcp-servers/{common,accounts,transactions,products}.py`
- [x] Built via **Cloud Build** (`gcloud builds submit`) — local Docker daemon wasn't running,
      and Cloud Build also builds natively on amd64, sidestepping the arm64 `exec format error`
      that bites when building on Apple silicon for GKE nodes
- [x] All three Deployments Running, serving StreamableHTTP on :8080
- [x] verify: `tools/list` returns the expected tools per server — 9 tools across the three
      servers, confirmed via `/healthz`
- [x] verify: a SQL query and the equivalent tool call agree — `check_balance(ACC-00004)`
      returns 185,254.95 AED, matching the DB exactly

## Phase 2 — Nova agent (~8h) — mostly done 2026-08-17

- [x] `nova/app.py` — FastAPI + LangChain `create_agent`, four runaway limits
      (recursion, max_tokens, request timeout, client timeout), agent/judge models as
      separate env vars so the judge can move to Sonnet without a code change
- [x] `nova/Dockerfile`, `nova/requirements.txt` — **`mcp` pinned explicitly**, not left
      to transitive resolution (see war story #10)
- [x] `k8s/redis.yaml` — **`redis/redis-stack-server`**, not `redis:7-alpine`; the
      checkpointer needs RediSearch (war story #11)
- [x] `k8s/nova.yaml` — Deployment + Service, plain Deployment on purpose (becomes an
      Argo Rollout in E1, not before)
- [x] Built via Cloud Build, deployed, **9 tools loaded** across all three MCP servers
- [x] Redis checkpointer connects — `session memory: redis at ...`, no fallback
- [x] **verify: single-tool question returns a correct balance** — `185,254.95 AED`,
      matches the DB exactly, correct tool and args
- [x] **verify: follow-up resolves account + date from memory** — "that account" → `ACC-00004`,
      "July 2026" → `2026-07-01`/`2026-07-31`, `category: salary`
- [x] **Reporting bug fixed** (war story #17) — `result["messages"]` is the whole thread, so
      `tool_calls` and `usage` were accumulating across turns. Would have failed every
      multi-turn Phase 4 eval case against a correctly-behaving agent. Now sliced per turn;
      `turn_messages` / `history_messages` added so the cost gate can tell an inefficient
      agent from a long conversation.
- [x] **Stale-answer bug fixed** (war story #18) — the agent sometimes answered repeat
      questions from a previous turn's tool result instead of re-querying, non-deterministically.
      System prompt now requires a tool call for any balance/transaction/card/loan question.
      Verified: Haiku calls the tool 100% of the time after the fix, which also answers
      "is Haiku not smart enough?" — it was an unwritten rule, not a capability limit.
- [x] Lockfiles — `nova/requirements.lock`, `mcp-servers/requirements.lock` via
      `uv pip compile --python-platform linux`; both Dockerfiles install from the lock.
      Three outages in this build came from dependency resolution, not code.

**Live tags:** `nova:20260817-1245-nocache`, `mcp-servers:20260817-1148-lock`
**Rollback:** `nova:20260817-1115`, `mcp-servers:20260816-1924`
- [x] `docs/DEBUGGING.md` written — command playbook by symptom
- [x] War stories #7–14 recorded in `PROJECT7-SUMMARY.md`

### Deferred from Phase 2 — cleared 2026-08-19

This list was stale; every item had in fact landed. Retained rather than deleted because one
of them shipped differently than planned.

- [x] FastAPI + LangChain `create_agent`
- [x] MCP adapter binding the three servers as tools
- [x] Redis StatefulSet + LangGraph checkpointer (session memory)
- [x] `trace_id` issued on request entry — **returned in the response BODY, not a header.**
      The plan said header; the code never did that. The body is the right place here: the
      eval runner already parses the JSON for `tool_calls` and `usage`, so a header would be
      a second thing to read for no benefit. Fixed the doc, not the code.
- [x] verify: single-tool question correct; follow-up resolves from memory

## Phase 2.5 — PII masking + human approval middleware (~2h)

LangChain prebuilt middleware. Banking makes both **required, not decorative**.

- [ ] PII masking — mask names, account numbers, balances before the prompt leaves the boundary
- [ ] Human-in-the-loop approval — pause before consequential tool calls
      (**not optional once `initiate_transfer` exists**)
- [ ] verify: an account number never appears in the outbound prompt (check the Langfuse trace);
      a transfer request halts and waits rather than executing

## Phase 3 — Observability (~5h) — DONE 2026-08-19

- [x] **Decided: Langfuse cloud free tier.** Self-hosting Langfuse means running its own
      Postgres and ClickHouse — more billable cluster than the thing being observed.
- [x] Langfuse + LangChain callback handler — one handler instruments the whole agent.
      Deliberately optional: missing keys leave Nova running untraced rather than refusing to
      start (`optional: true` on the Secret refs). Observability must not take down what it
      observes.
- [x] kube-prometheus-stack installed via Helm. The two `...SelectorNilUsesHelmValues=false`
      flags are load-bearing — without them the operator only picks up monitors carrying its
      own release label, and the ServiceMonitor is ignored silently, with no error and an
      empty graph.
- [x] Nova metrics at `/metrics`: `nova_requests_total{status}`, `nova_request_duration_seconds`,
      `nova_tool_calls_total{tool}`, `nova_tokens_total{direction}`, `nova_cost_usd_total`,
      `nova_tools_per_turn`, plus `nova_memory_persistent` / `nova_tracing_enabled` gauges
- [x] `charts/nova/templates/monitoring.yaml` applied — ServiceMonitor + 6 alert rules, target `up=1`
- [x] verify: one question → one Langfuse trace; metadata `trace_id` matches the response body
- [x] verify: counters move correctly — 3 requests → `requests_total{ok}=3`,
      `check_balance=2`, `get_cards=1`, cost $0.0185

### What the first real numbers showed

**93% of spend is INPUT tokens** (17,209 in / 264 out across 3 turns, ~$0.0062/request). The
answers are short; what costs money is what gets sent *to* the model every turn — system
prompt, nine tool schemas, and the whole history resent each call. The cost levers are
therefore tool-schema verbosity, history truncation, and prompt caching. Shortening answers
would move nothing.

Consequence for the cost gate: **cost-per-request is only comparable at equal conversation
depth.** A longer conversation costs more per turn with identical behaviour. The golden set is
mostly single-turn so the Phase 4 baseline is clean, but E2's live-traffic scoring will see
real multi-turn sessions and the distribution will look alarming until it is normalised by
`history_messages`.

### Counters are not durable state

An in-process Prometheus counter resets to zero on every pod restart — by design, since
`rate()` and `increase()` detect the reset. So `sum(nova_requests_total)` lies after a restart;
`sum(increase(nova_requests_total[24h]))` does not. The alert rules all use `rate`, so they are
correct as written. Related trap seen live: a *labelled* counter does not exist at all until
its first `.inc()`, so `nova_requests_total` is absent from `/metrics` on a fresh process while
the unlabelled `nova_cost_usd_total` already reads 0.0. An absent series and a zero series mean
different things.

## Phase 4 — Evaluation Hub (~10h) — NEXT

The scoring logic already exists and has been run: `eval/run_eval.py` (247 lines) plus
`eval/golden/questions.yaml`, with two result files at the repo root as evidence. What remains
is **packaging it as a CRD + controller**, not writing it. Validating the logic as a script
first was deliberate — packaging logic you have not validated is how you end up debugging
Kubernetes when the problem is a scoring bug.

**Two golden-set fixes found during Phase 3 verification, do these before generating any
baseline:**

- [ ] `gs-002` points at `ACC-00004`, which has **zero cards**. The agent handled it correctly
      (said "no cards" rather than inventing any), but as a coverage case it is weak: an empty
      result makes `groundedness` trivially passable and never tests whether the agent can read
      card fields. Repoint it at an account that has cards; move the empty case to the `refuse`
      shape, where it belongs.
- [ ] The `memory` shape tests **reference resolution** ("that account" → ACC-00004) but not
      **staleness** — re-asking a question whose answer is already in context, where a lazy
      agent skips the tool and answers from history (war story #18). These are opposite failure
      modes: one wants the agent to use context, the other forbids it. Needs its own case, where
      `expect_tools` being non-empty is the entire assertion. Note the failure looks *better*
      than the pass — faster, cheaper, right number — until the underlying data changes.

- [ ] **Decide: swap `NOVA_JUDGE_MODEL` to `claude-sonnet-5` before generating any baseline.**
      Currently Haiku in `k8s/nova.yaml`, while README and the notes below both say Sonnet.
      One env change plus a rollout, no rebuild. It matters because the judge model is part of
      what an eval run is versioned by — swapping mid-Phase-4 shifts every groundedness score
      with no change to Nova.

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

### E1a — Argo CD sync — DONE 2026-08-24

Pulled ahead of Phase 2.5/4 deliberately: the phases that remain are the most manifest-heavy
in the project (CRD, RBAC, controller Deployment, runner Job, CronJob, PrometheusRule), which
is the worst possible time to still be applying by hand.

- [x] Argo CD installed via Helm, **chart pinned 10.4.0 → v3.5.1**. dex + notifications
      disabled; `applicationSet.enabled: false` does NOT exist in 10.x and was silently
      ignored (war story #20) — left at the chart default of 1 replica.
- [x] `k8s/` manifests packaged as `charts/nova` — a Helm chart, NOT Kustomize, because Helm
      was already in the stack (ESO, kube-prometheus-stack, Argo CD) and Kustomize would have
      been a fourth way to render YAML. Deliberately thin: only the 4 image refs templated.
- [x] `charts/nova/values.yaml` holds the image tags — the file CI writes in E1b. `git log
      --follow` on it is the deploy record.
- [x] `k8s/seed-job.yaml` deliberately EXCLUDED from the chart. `ttlSecondsAfterFinished: 3600`
      makes it self-delete hourly; Argo CD with selfHeal reads that as drift and recreates it,
      and the job opens with TRUNCATE. Self-deleting resources and auto-sync are incompatible.
- [x] `gitops/bootstrap/root-app.yaml` (app-of-apps) + `gitops/apps/nova.yaml`. No
      `resources-finalizer` on nova on purpose — it would make `delete application` cascade
      into deleting the Postgres StatefulSet.
- [x] verify: both Applications `Synced` / `Healthy`; adoption of the kubectl-created
      resources was clean, no OutOfSync
- [x] verify: **selfHeal** — `kubectl scale deploy/nova --replicas=3` reverted in **1 second**
- [x] verify: **git drives the cluster** — `replicas: 2` committed, deployed with no kubectl,
      ~1 minute after the push

**Two clocks, and they are not the same** (this was measured, not assumed):

| Drift | Detected by | Latency |
|---|---|---|
| Cluster edited by hand | Kubernetes **watch** on managed resources | ~1s |
| Git commit | **Poll**, `timeout.reconciliation` 180s | 0–3 min, ~1 min typical |

selfHeal never re-reads git — the controller already holds the rendered desired state, so a
watch event is enough. The 180s interval only governs noticing git changed. Webhooks (E1b)
fix the second clock, not the first.

**Nova at 2 replicas stays correct only because of the Redis checkpointer** — session memory
lives outside the pod. On the in-process fallback, turn 2 would miss its history whenever it
landed on the other replica. `nova_memory_persistent` is the gauge standing between the
platform and that.

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
