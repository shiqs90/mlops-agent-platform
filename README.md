# Nova — MLOps Banking Agent Platform

**An MLOps/LLMOps platform: CI/CD for an AI agent, where automated evaluation is the
deployment gate.**

Nova is a banking assistant that answers customer questions by calling tools. This repo is the
platform around it — connectors, memory, tracing, evaluation, and GitOps delivery — built so
that **no change to Nova reaches production without proving it didn't make the answers worse.**

For a normal web service the deploy gate is "does it return HTTP 200?" An LLM returns 200 all
day while quietly being wrong: a prompt tweak makes it pick the wrong tool, a model swap makes
it invent numbers the tool never returned. Nothing in a health check, a status code, or a
latency graph catches that. So the evaluation *is* the health check.

---

## What it does

```
Customer: "Did my salary land this month, and am I over my overdraft limit?"

Nova:  → query_transactions(account_id=ACC-2291, month=2026-03)
       → check_balance(account_id=ACC-2291)
       → "Your salary of AED 18,400 credited on 25 March. Your balance is
          AED -1,240 against an overdraft limit of AED 5,000, so you're within it."
```

```
Customer: "And last month?"

Nova:  → resolves "last month" from session memory, re-queries, answers
```

```
Customer: "Transfer AED 2,000 to my savings."

Nova:  → pauses for human approval before executing the write
```

## The loop this project exists to build

```
1. You change something          prompt, model, tool description, connector
2. Commit to git
3. Argo CD deploys it            to a preview endpoint — no real users
4. Evaluation Hub runs           replays golden + regression sets against preview
5. Four scores come back         right tool?  right arguments?  answer grounded
                                 in the tool result?  cost per request?
6. Pass → promote to production
   Fail → auto-rollback, nothing ships
```

Plus the part the gates can't see: **drift monitoring** catches degradation when *nothing*
changed — a provider retuning the model under a pinned ID, customers asking question shapes
the golden set never covered, a connector quietly changing its response format.

---

## What it covers

| Capability | How it's covered here |
|---|---|
| **Agentic systems** | LangChain `create_agent`, multi-step tool loop, fallback routing |
| **Tool calling** | Four tools across three connectors, incl. one consequential write |
| **Enterprise connectors / MCP** | Three real MCP servers, consumed via LangChain's MCP adapter |
| **Agent memory** | LangGraph checkpointer, Redis-backed session state |
| **Evaluation** | Evaluation Hub — a Kubernetes controller running scored replays |
| **LLM-as-judge** | `claude-sonnet-5` scoring groundedness, reference-free |
| **Golden + regression sets** | Curated coverage set; append-only never-again set |
| **Drift monitoring** | Scheduled replay + sampled live-traffic scoring |
| **Cost engineering** | Cost-per-request as a promotion gate; router distillation |
| **Kubernetes** | CRD + custom controller, StatefulSets, Jobs, CronJobs, RBAC, scoped ServiceAccounts |
| **GitOps** | Argo CD pull-based reconciliation — CI never holds cluster credentials |
| **Progressive delivery** | Argo Rollouts blue-green with pre- and post-promotion analysis |
| **CI/CD** | GitHub Actions: contract checks → manifest commit → smoke test |
| **Observability** | Langfuse traces + Prometheus metrics, joined by one `trace_id` |
| **Model registry / training** | MLflow + in-cluster fine-tuning Job for router distillation |
| **Security** | PII masking before the prompt leaves the boundary; HITL approval on writes; least-privilege node SA; Workload Identity |
| **IaC** | Terraform on GKE via HCP Terraform |

## Architecture

![Nova on GKE](docs/diagrams/nova-gke.png)

One customer question, numbered 1–9: in through the GKE control plane, context from Redis, the
model decides a tool, the MCP connectors query Postgres, the answer goes back, the trace goes out.
Prometheus scrapes `/metrics`; secrets reach the pods through Workload Identity with no static
credential on the path.

Generated from `terraform-gcp/` and `k8s/` by
[`docs/diagrams/nova-gke.py`](docs/diagrams/nova-gke.py). Every edge, the trust boundaries, and
the tradeoffs behind each choice: [docs/architecture-gke.md](docs/architecture-gke.md).

## The five gates

| Gate | Runs in | Against | Catches |
|---|---|---|---|
| 1. Contract | CI | tool schemas | A tool that doesn't exist, args that don't validate |
| 2. Smoke | CI | staging | Agent loads but the API is broken |
| 3. Quality replay | Cluster | **preview** Service | Routing, argument, or grounding regression — before any user is exposed |
| 4. **Cost budget** | Cluster | **preview** Service | A change that holds quality but doubles tokens |
| 5. Live metrics | Cluster | **active** Service | Real input distribution, real concurrency |

Quality and cost fail independently — a change can keep answering correctly while burning
twice the tokens through extra tool-loop iterations. Most portfolio projects gate only on
quality.

## The four metrics, and why they're separate

| Metric | Computed by | Needs ground truth? |
|---|---|---|
| `tool_selection` | Deterministic string compare | one-word annotation |
| `parameter_accuracy` | Deterministic, after normalising dates/accounts | one-line annotation |
| `groundedness` | LLM judge — is every claim supported by the tool result? | **no — reference-free** |
| `cost_per_request` | Tokens from the trace × model rate | no |

One blended score tells you *something* broke. Four tell you *where*:

| tool_selection | parameter_accuracy | groundedness | cost | Diagnosis |
|---|---|---|---|---|
| **↓** | ok | ↓ | ok | Routing regressed — prompt or tool descriptions |
| ok | **↓** | ↓ | ok | Routing fine, argument extraction broke |
| ok | ok | **↓** | ok | Right data fetched, model misread it |
| ok | ok | ok | **↑** | Quality held, agent is looping or over-calling |

`groundedness` being reference-free is what makes drift monitoring on live traffic possible —
you can score a real customer question with no expected answer to compare against.

## Golden set vs regression set

| | Golden set | Regression set |
|---|---|---|
| Purpose | Coverage | Never-again |
| Size at start | ~18 | 0 |
| Grows | Deliberately, with new capability | Automatically, from every bug found |
| Source | Generated from tool schemas, curated by hand | Failed drift runs and incident postmortems |

Neither is hand-authored as question/answer pairs. Where a literal answer is needed it is
**computed by SQL against the same seed data** — machine-generated and provably correct.

## Cost

No GPU at any point.

| | |
|---|---|
| GKE node pool | ~$0.29–0.38/hr — **scale to zero between sessions** |
| GKE control plane | Free tier (zonal cluster) |
| Claude API per ~18-question eval run | ~$0.17 (`haiku-4-5` agent + `sonnet-5` judge) |
| Drift monitoring | ~$5/month nightly, ~$1.20/month weekly |

```bash
# Between sessions
gcloud container clusters resize mlops-lifecycle --node-pool=primary --num-nodes=0 \
  --zone=us-west1-b --project=mlops-lifecycle-p7-gke --quiet
```
