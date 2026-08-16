# The MLOps Lifecycle — reference, with banking77 as the worked example

The eight stages of a model's life, what each one produces, and how each one fails. The second half
walks the same eight stages against this project's actual model and dataset, so the abstractions
have something concrete attached to them.

Reference for anyone picking this repo up, and the basis for the design decisions in
[../IMPLEMENTATION-PLAN.md](../IMPLEMENTATION-PLAN.md).

---

## The core idea

Regular software is a **machine**. You build it and it does the same thing forever.

A ML model is a **photograph of the world at a moment**. Machines don't rot. Photographs go out of
date on their own, while you sleep.

That one difference generates everything MLOps adds to DevOps:

> **MLOps = DevOps + two more things to version (data, model) + one new failure mode
> (silent degradation).**

*Silent* is the important word. A broken service pages you. A model that has quietly become wrong
returns HTTP 200 with a confident, incorrect answer, forever, until somebody checks.

## The eight stages

It's a loop, not a line.

| # | Stage | What happens | Fails like |
|---|---|---|---|
| 1 | **Frame** | What decision does this make? Which metric counts? | Building the wrong thing accurately |
| 2 | **Data** | Collect, label, validate, version. Engineer features. | Garbage in. Silently changed schema. |
| 3 | **Train** | Try approaches, record every run: params, metrics, code version | "Which run was the good one?" Nobody knows. |
| 4 | **Evaluate** | Score on data the model never saw | Looks great in training, useless in reality |
| 5 | **Register** | Promote the chosen artifact with its full lineage | A model file on someone's laptop |
| 6 | **Deploy** | Package as a service, roll out safely, staging before prod | Model that works, service that doesn't |
| 7 | **Monitor** | Operational (latency, errors) *and* model (drift, quality decay) | Green dashboards, wrong predictions |
| 8 | **Retrain** | New data arrives — back to stage 2 | Retraining on bad data ships something worse |

Stage 8 closing back to stage 2 is what makes this "Ops" rather than a project. A model is never
finished.

## Where this project sits

```
1 Frame   2 Data   3 Train   4 Evaluate   5 Register │ 6 Deploy   7 Monitor   8 Retrain
──────────────────────────────────────────────────── │ ─────────────────────────────────
              P7 — this project                      │  P1, P2, P3, P5, P8 (serving)
                                                     │  P4 (operational monitoring)
```

The serving projects own stage 6 and the operational half of stage 7 — vLLM on GPU, multi-model
routing, GPU sharing, progressive delivery, autoscaling, DCGM/Prometheus observability.

P7 owns the left half: train → evaluate → register → deploy, wired so a single commit walks the
whole path.

Remaining coverage in the roadmap: feature store completes stage 2, drift monitoring completes the
model half of stage 7.

---

# The same eight stages, against banking77

## The two nouns

**banking77** — about 13,000 real customer-service messages from a banking app, each labelled with
one of **77 intents**: `card_arrival`, `declined_card_payment`, `lost_or_stolen_card`, and so on.
Roughly 10,000 train / 3,000 test. A standard benchmark.

**DistilBERT** — a compressed BERT. 6 layers instead of 12, ~66M parameters, about 60% faster,
keeping ~97% of the quality. It already understands English.

We are **fine-tuning**: bolting a 77-way classifier onto a model that already has language
knowledge, and nudging its weights. That's why minutes on a CPU is enough. Training language
understanding from scratch is weeks of GPU time. Know which one you're describing — the cost
differs by orders of magnitude.

## Stage 1 — Frame

The decision: *a customer types a message; which of 77 intents is it?* That routes them to the
right help article or agent queue.

The part that gets skipped: **choosing the metric is an engineering decision, not paperwork.**

- Plain **accuracy** misleads here. 77 unevenly-sized classes, so a model can look fine while
  failing every rare intent.
- **Macro-F1** averages per class, so rare intents count as much as common ones. Better fit.
- And the business question: is a wrong answer *expensive*? Here, no. A misrouted help article is
  annoying.

That last answer is why P7 uses blue-green instead of canary. Cheap failures mean a brief 100%
exposure is an acceptable trade for instant revert. Fraud scoring would answer differently and the
deployment strategy would change with it. **Stage 1 decided stage 6.**

*Artifact: a written metric and threshold. No code.*

## Stage 2 — Data

Load banking77, pin the version, then **validate before spending any compute**:

- exactly 77 unique labels — a silently-76 label set is a classic
- no nulls, text lengths in a sane range
- class distribution close to a stored baseline
- no duplicate texts across train and test. Leakage inflates the score and hides itself.

**Data versioning** here means: pin the dataset name and revision, hash the contents, log the hash
with the run. That hash is what makes "reproducible" a true claim later.

*P7: `src/validate_data.py`, the first CI step. The gate that fails cheapest.*

## Stage 3 — Train

Tokenize, then fine-tune. Around 2 epochs, batch 16, learning rate 2e-5, **fixed seed**.

Everything goes to MLflow: parameters, per-epoch loss and eval accuracy, the model, the tokenizer,
the label map, plus tags for git SHA and dataset hash.

That logging *is* experiment tracking. Without it, three weeks later you have five models and no
idea which run produced the good one or with what settings.

**Runtime note:** on 2 CPU cores, 10k samples over 2 epochs takes 15–30 minutes. The training set
is subsetted to keep pipeline runs tolerable.

*P7: a Kubernetes Job running `src/train.py`.*

## Stage 4 — Evaluate

Score on the 3,000 test examples the model never saw. Accuracy, macro-F1, per-class report.

A **confusion matrix** is worth producing, because it shows *which* intents get mixed up.
`card_arrival` confused with `card_delivery_estimate` is understandable. `card_arrival` confused
with `lost_or_stolen_card` means something is wrong.

Two things come out of this stage:

- **Gate 1's threshold** — set *after* there's a baseline number. Inventing a threshold before
  training once is guessing.
- **The golden set** for gate 3a: ~50 hand-checked examples covering the intents that matter most.

## Stage 5 — Register

`mlflow.register_model` creates **version N** of `banking77-intent`.

This table is the answer to *"what's in the registry besides weights?"*

| Item | Why it matters |
|---|---|
| **Signature** | input is a string, output is 77 probabilities. Callers can't guess wrong. |
| **Input example** | a real request, so anyone can try it |
| **Pip environment** | exact library versions — reproducibility |
| **Params + metrics** | how it was trained, how well it scored |
| **Source run id** | links back to the training run |
| **Git SHA + dataset hash** | links back to the code and the data |
| **Label map** | integer `43` means nothing without it |
| **Alias** | `candidate`, later `champion` |

**Artifact store versus registry:** the store holds the file. The registry holds the version
number, the lineage, the stage, and who approved it. Confusing the two is the most common mistake
here.

## Stage 6 — Deploy

FastAPI loads `models:/banking77-intent@champion` **once at startup**, then serves `/predict`.

Deploying means making it a *service*: readiness and liveness probes, resource limits, `/metrics`,
graceful shutdown, and the model version reported in every response. Argo CD syncs it, the
blue-green Rollout switches it, gates 3a and 3b decide whether the switch sticks.

**A model artifact is not a service.** Stage 5 produces a file. Stage 6 is everything that makes
it answerable over HTTP at 3am.

## Stage 7 — Monitor

Two categories. Conflating them is the mistake.

| Operational | Model |
|---|---|
| request rate, p99 latency | mean prediction confidence |
| error rate, pod restarts | predicted-class distribution |
| CPU, memory | % of predictions below a confidence floor |

The constraint that shapes all production model monitoring: **there is no ground truth.** Nobody tells you
the correct intent. You cannot compute accuracy on live traffic.

So production model monitoring is **proxies plus delayed feedback**. If the predicted-intent
distribution suddenly shifts — 40% of messages classified `lost_or_stolen_card` — either the world
changed or the model broke, and *the metric alone cannot tell you which*. Real feedback arrives
late and indirectly: did the customer re-ask? did an agent re-route the ticket?

This is the honest limitation of ML monitoring. Saying it out loud signals you've operated a model
rather than read about one.

## Stage 8 — Retrain

Triggers: a schedule, a drift alarm, or enough new labelled data. The same pipeline runs again,
produces a new version, faces the same four gates.

**The gates are what make retraining safe.** Automated retraining without gates is an automated way
to ship a worse model — and it will deploy itself with total confidence.

---

## Five traps

1. **Training is one stage of eight, and the one MLOps cares least about.** Infra people
   over-index on it because it's the visible ML part. Stages 2, 5 and 7 are where production
   breaks.
2. **Test accuracy is not production accuracy.** The test set came from the same distribution as
   training. Production didn't.
3. **No ground truth in production.** See stage 7. This is the one that changes how you think.
4. **The registry is metadata, not storage.**
5. **Fine-tuning is not training from scratch.**

## Questions this design is accountable for

The ones that get asked when the system misbehaves:

1. Production accuracy is reported to have dropped from 92% to 71%. Given that stage 7 has no
   ground truth, **how would that even be detected**, and what gets checked first?
2. "The pipeline is reproducible, it's all in git." What's missing from that claim?
3. Why does the cheapest-failing gate run first, and which of the four gates is it?
