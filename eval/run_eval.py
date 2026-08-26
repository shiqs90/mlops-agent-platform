"""Evaluation runner — replays a question set against Nova and scores four metrics.

This is the logic the Evaluation Hub will wrap. Running it as a script first is
deliberate: the CRD + controller is packaging, and packaging logic you haven't
validated is how you end up debugging Kubernetes when the problem is a scoring bug.

SIX METRICS, THREE ARITHMETIC AND THREE JUDGED — but only ONE judge call per case:

  tool_correctness      code   did it call the tools we expected?
  argument_correctness  code   with the right arguments?
  faithfulness          judge  is every claim it made supported by the tool result?
  answer_relevance      judge  does it answer the question that was asked?
  answer_correctness    judge  did it cover what the reference says it must?  (rare)
  cost_per_request      code   tokens x model rate
  (plus latency, aggregated at run level — reported and budgeted, not scored per case)

THE NAMES ARE THE FIELD'S NAMES ON PURPOSE. Renamed 2026-08-25, computation unchanged:

  groundedness       -> faithfulness           (RAGAS / LangChain)
  tool_selection     -> tool_correctness       (DeepEval ToolCorrectnessMetric)
  parameter_accuracy -> argument_correctness   (DeepEval ArgumentCorrectnessMetric)

Note DeepEval judges argument correctness with an LLM and no reference. This does it as
a deterministic key-value comparison, which is strictly better HERE because the golden
set already declares the expected arguments — there is no reason to pay a model to
decide whether "2026-07-01" equals "2026-07-01".

THE THREE JUDGED METRICS SHARE ONE API CALL. This is not a cosmetic saving. Three
separate calls would triple judge spend on the one metric family that costs money,
and would let the judge contradict itself across calls on the same answer. One call,
one reading of the answer, three independent scores out.

WHAT answer_correctness IS FOR, AND WHAT IT IS NOT FOR
------------------------------------------------------
It is tempting to think faithfulness + answer_relevance already cover everything, and
for most cases in this suite THEY DO. That was checked case by case on 2026-08-25 and
the references were cut from 6 cases to 3 as a result:

  fabricated balance on a missing account  -> faithfulness catches it (unsupported claim)
  invented card on an account with none    -> faithfulness catches it
  exchange rate quoted with no tool call   -> faithfulness catches it (no context at all)
  inventing an account nobody named        -> tool_correctness catches it (expected [])
  wrong verdict from correct figures       -> faithfulness catches it too: "the account
                                              has exceeded its limit" cannot be INFERRED
                                              from balance +185k / limit 7126, and RAGAS
                                              faithfulness scores inference, not quoting

THE ONE GAP FAITHFULNESS CANNOT CLOSE IS OMISSION. Faithfulness scores the claims that
ARE in the answer. It has no opinion about claims that SHOULD have been there and are
not — an answer listing 2 of a customer's 5 accounts is 100% faithful and 100% wrong.
No judge prompt fixes this, because the missing content is not in the answer to judge.
RAGAS defines answer_correctness as coverage + relevance against ground truth for
exactly this reason: coverage is the half that needs a reference.

So the surviving references are the cases where the risk is INCOMPLETENESS, not
falsehood: a per-item fan-out (gs-016), a category breakdown (gs-004), and a two-part
question where answering one half is the likely failure (gs-008).

WHY FAITHFULNESS NEEDS NO REFERENCE: it compares the answer to what the tool returned
*in this run*, not to a stored string. That is what keeps it valid against changing
data, and what makes E2 drift monitoring possible on live traffic, where no reference
exists by definition. `answer_correctness` is the opposite — pinned to seed
ING-20260801-42, wrong the moment the data changes. Every reference is maintenance, so
each one has to earn its place against the question "would faithfulness catch this?"

ONE BLENDED SCORE WOULD TELL YOU LESS. Six scores localise a regression:
  tool_correctness down          -> routing broke (prompt or tool descriptions)
  argument_correctness down      -> argument extraction broke, routing fine
  faithfulness down, rest ok     -> right data fetched, model fabricated on top of it
  relevance down, rest ok        -> model is answering something else, correctly
  correctness down, rest ok      -> answer is true but incomplete
  cost up, quality flat          -> looping or over-calling tools
"""

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone

import httpx
import yaml

NOVA_URL = os.environ.get("NOVA_URL", "http://localhost:8000")
JUDGE_MODEL = os.environ.get("NOVA_JUDGE_MODEL", "claude-haiku-4-5")

# USD per million tokens. Used for cost_per_request; update when models change.
RATES = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-5":  (3.00, 15.00),
    "claude-opus-5":    (5.00, 25.00),
}

# Per-metric pass thresholds. NOT one number, and the split is the point:
#
#   The code metrics are exact comparisons. There is no such thing as a near-miss —
#   either the expected tool was called or it wasn't — so anything below 1.0 is a real
#   defect and the threshold is 1.0.
#
#   The judged metrics are opinions. A judge that returns 0.9 for a good answer is
#   behaving normally, not signalling a problem. Holding them to 1.0 would make the
#   suite fail on judge noise instead of on agent regressions, and a gate that cries
#   wolf gets switched off within a week. Relevance sits lowest because it is the most
#   subjective of the three.
#
# These are the numbers the promotion gate reads. Changing one is a policy change, so
# it belongs in git next to the metric it governs — not in a dashboard someone edits.
THRESHOLDS = {
    "tool_correctness": 1.0,
    "argument_correctness": 1.0,
    "faithfulness": 0.9,
    "answer_relevance": 0.8,
    "answer_correctness": 0.9,
}

# Run-level budgets. 0 disables, which is the default so a bare local run still just
# scores quality. The Job sets these; see status.md Phase 5.
# Cost is per request, NOT per run — a run total moves when you add cases, and a gate
# that trips because the suite grew is measuring the wrong thing.
COST_BUDGET_USD = float(os.environ.get("NOVA_EVAL_COST_BUDGET_USD", "0") or 0)
LATENCY_BUDGET_MS = float(os.environ.get("NOVA_EVAL_LATENCY_BUDGET_MS", "0") or 0)

JUDGE_PROMPT = """You are grading a banking assistant's answer on three INDEPENDENT
criteria. Score each on its own terms. A good answer can fail one and pass the others,
and keeping them separate is the entire point — do not average them in your head.

FAITHFULNESS — is every factual claim in the answer supported by the tool results?
You are NOT judging whether the answer is helpful, well-written, or complete. Mark a
claim unsupported if it states a number, date, or fact that does not appear in the
tool results. If the tools returned an error or no data and the answer says so, that
is fully faithful — reporting missing data is correct behaviour. If the tools returned
nothing and the answer states a figure anyway, that is fabrication and scores 0.

ANSWER_RELEVANCE — does the answer address the question that was actually asked?
This is independent of truth. An answer that cites every number correctly but responds
to a different question scores high on faithfulness and low here. Penalise evasion,
padding, and answering a narrower or broader question than the one asked. Do NOT
penalise an answer for being short if the question was narrow.

When a question CANNOT be answered as asked — it is out of scope, the data does not
exist, or it is missing something required like an account number — then declining, or
asking for the missing detail, IS the maximally relevant response. Score it 1.0. Do not
mark it down for failing to supply an answer that would have had to be invented.

ANSWER_CORRECTNESS — does the answer COVER everything the reference says it must?
This metric exists for omission, so weight coverage heavily: score down every required
item the reference names that the answer leaves out, even if everything present is
true. Wording, ordering, formatting, and extra detail do not matter. A reference of
"(none provided)" means this case has no reference — return null for this field, and
do not let its absence affect the other two scores.

QUESTION:
{question}

REFERENCE ANSWER:
{reference}

TOOL RESULTS:
{tool_results}

ANSWER:
{answer}

Reply with JSON only:
{{"faithfulness": <0.0 to 1.0>,
  "answer_relevance": <0.0 to 1.0>,
  "answer_correctness": <0.0 to 1.0, or null>,
  "unsupported": ["claim", ...]}}"""


def ask(client: httpx.Client, session_id: str, message: str) -> dict:
    r = client.post(f"{NOVA_URL}/chat", json={"session_id": session_id, "message": message},
                    timeout=90.0)
    r.raise_for_status()
    return r.json()


def score_tool_correctness(actual: list[str], expected: list[str]) -> float:
    """Set comparison, not sequence.

    Order is genuinely not asserted: for a two-tool question, either order produces a
    correct answer, and penalising one would make the metric measure style rather than
    correctness. An empty expectation (the out-of-scope case) scores 1.0 only if the
    agent called nothing.
    """
    if not expected:
        return 1.0 if not actual else 0.0
    if not actual:
        return 0.0
    hit = len(set(expected) & set(actual))
    # Penalise extra tools too — calling three tools when one was needed is a real
    # cost and latency regression, not a harmless overshoot.
    return hit / max(len(set(expected)), len(set(actual)))


def score_argument_correctness(calls: list[dict], expected: dict) -> float:
    """Fraction of expected arguments that appear correctly in ANY call this turn.

    Any call, not a specific one, because a two-tool turn legitimately splits
    arguments across calls. Dates are compared as strings — the tools require
    absolute YYYY-MM-DD, so normalising here would hide the exact failure this metric
    exists to catch.
    """
    if not expected:
        return 1.0
    supplied = {}
    for c in calls:
        supplied.update(c.get("args", {}))
    hits = sum(1 for k, v in expected.items() if str(supplied.get(k, "")).strip() == str(v).strip())
    return hits / len(expected)


def score_judge(anthropic_client, question: str, answer: str, tool_results: str,
                reference: str | None) -> dict:
    """The only scorer that needs a model, and the only one that costs money.

    Returns all three judged metrics from ONE call. `answer_correctness` is None when
    the case carries no `expect_answer` — and that is enforced HERE rather than
    trusted from the judge, because a judge told "return null" will occasionally
    return 1.0 anyway, and a metric that is silently 1.0 for two thirds of the suite
    would drag the aggregate upward and hide real failures in the third that has one.
    """
    if not answer.strip():
        return {"faithfulness": 0.0, "answer_relevance": 0.0,
                "answer_correctness": 0.0 if reference else None,
                "unsupported": ["empty answer"]}

    msg = anthropic_client.messages.create(
        model=JUDGE_MODEL,
        max_tokens=768,
        messages=[{"role": "user", "content": JUDGE_PROMPT.format(
            question=question,
            reference=reference or "(none provided)",
            tool_results=tool_results or "(no tools were called)",
            answer=answer)}],
    )
    raw = "".join(b.text for b in msg.content if b.type == "text").strip()
    try:
        # Judges wrap JSON in prose or fences often enough that this is worth doing.
        start, end = raw.find("{"), raw.rfind("}")
        parsed = json.loads(raw[start:end + 1])
    except Exception:
        # Score 0, don't crash. An unparseable judge is a failing case, not a failing
        # run — one bad response must not lose the other seventeen results.
        return {"faithfulness": 0.0, "answer_relevance": 0.0,
                "answer_correctness": 0.0 if reference else None,
                "unsupported": [f"judge returned unparseable output: {raw[:120]}"]}

    corr = parsed.get("answer_correctness")
    return {
        "faithfulness": float(parsed.get("faithfulness", 0.0)),
        "answer_relevance": float(parsed.get("answer_relevance", 0.0)),
        "answer_correctness": (float(corr) if reference and corr is not None else None),
        "unsupported": parsed.get("unsupported", []),
    }


def push_to_gateway(url: str, run: dict) -> None:
    """Publish run-level scores so Prometheus can scrape them after this pod exits.

    An eval run is a Job: it starts, scores, and dies in about two minutes. Prometheus
    PULLS on a ~30s interval, so it may never scrape while the pod exists — and even
    if it does, the series disappears with the pod. Pushgateway is the standard fix
    and the one case the Prometheus docs endorse it for: a permanent target the batch
    job posts to on its way out.

    Know the caveat before an interviewer asks: this pattern is WRONG for services.
    A pushed metric outlives its source, so "is it up?" becomes unanswerable — the
    gateway keeps serving the last value long after the pusher died. Correct for a
    job whose whole purpose is to leave a result behind; wrong for anything you need
    liveness from.

    Pushing REPLACES the group each run, deliberately. The gate wants the latest
    verdict, and history lives in Prometheus' own TSDB, not in the gateway.
    """
    lines = [f'nova_eval_score{{metric="{k}"}} {v}' for k, v in run["aggregate"].items()
             if v is not None]
    lines += [
        f'nova_eval_cost_usd_per_request {run["cost_per_request"]}',
        f'nova_eval_latency_p95_ms {run["latency_p95_ms"]}',
        f'nova_eval_cases_total {run["cases"]}',
        f'nova_eval_cases_failed {run["failed"]}',
        f'nova_eval_timestamp_seconds {time.time()}',
    ]
    # Trailing newline is REQUIRED — the text exposition format rejects the body
    # without it, and the error looks like a generic 400.
    body = "\n".join(lines) + "\n"
    r = httpx.post(f"{url.rstrip('/')}/metrics/job/nova_eval", content=body, timeout=10.0)
    r.raise_for_status()


def cost_usd(usage: dict, model: str) -> float:
    inp, out = RATES.get(model, (0.0, 0.0))
    return (usage.get("input_tokens", 0) / 1e6) * inp + (usage.get("output_tokens", 0) / 1e6) * out


def failed_metrics(r: dict) -> list[str]:
    """Which metrics on this case fell below their threshold. Empty list = pass.

    `is not None` matters: a case without a reference answer has answer_correctness
    None, and None must mean "not applicable", never "scored zero".
    """
    return [m for m, floor in THRESHOLDS.items()
            if r.get(m) is not None and r[m] < floor]


def percentile(values: list[float], pct: float) -> float:
    """p95 without pulling in numpy for one line. Nearest-rank, which on a suite of
    ~18 cases is the honest method anyway — interpolating between two samples implies
    a resolution this sample size does not have."""
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round(pct / 100 * len(ordered) + 0.5)) - 1)
    return ordered[idx]


def main() -> None:
    global NOVA_URL   # must precede any use of NOVA_URL in this function

    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="eval/golden/questions.yaml")
    ap.add_argument("--target", default=NOVA_URL)
    ap.add_argument("--limit", type=int, default=0, help="run only the first N cases")
    args = ap.parse_args()

    NOVA_URL = args.target

    cases = yaml.safe_load(open(args.set))
    if args.limit:
        cases = cases[:args.limit]

    import anthropic
    judge = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    run_id = f"EVAL-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}"
    results, started = [], time.monotonic()

    print(f"run_id={run_id}  target={NOVA_URL}  judge={JUDGE_MODEL}  cases={len(cases)}\n")
    print(f"{'id':<8} {'shape':<7} {'tool':>5} {'param':>6} {'faith':>6} "
          f"{'relev':>6} {'corr':>6} {'cost$':>8}  tools")
    print("-" * 92)

    with httpx.Client() as client:
        for case in cases:
            # Fresh session per case so cases can't contaminate each other — except
            # `setup`, which deliberately shares one to test memory.
            session_id = f"{run_id}-{case['id']}"
            if case.get("setup"):
                ask(client, session_id, case["setup"])

            try:
                resp = ask(client, session_id, case["question"])
            except Exception as exc:
                print(f"{case['id']:<8} {case.get('shape',''):<7}  REQUEST FAILED: {exc}")
                continue

            if resp.get("error"):
                print(f"{case['id']:<8} {case.get('shape',''):<7}  AGENT ERROR: {resp.get('detail','')[:50]}")
                continue

            calls = resp.get("tool_calls", [])
            names = [c["tool"] for c in calls]

            ts = score_tool_correctness(names, case.get("expect_tools", []))
            ps = score_argument_correctness(calls, case.get("expect_params", {}))
            # Score against what the tools RETURNED, not what was called. Passing
            # tool_calls here scored a perfect agent at 0.17 — the judge was shown
            # {"tool": "check_balance", "args": {...}} and asked whether
            # "the balance is 185,254.95" was supported by it.
            evidence = resp.get("tool_results", [])
            judged = score_judge(
                judge, case["question"], resp.get("answer", ""),
                json.dumps(evidence, indent=2) if evidence else "",
                case.get("expect_answer"))
            cost = cost_usd(resp.get("usage", {}), resp.get("model", ""))

            row = {
                "run_id": run_id, "question_id": case["id"], "shape": case.get("shape"),
                # Carried so a result file reads on its own, without opening the fixture.
                "question": case["question"], "answer": resp.get("answer", ""),
                "tool_correctness": ts, "argument_correctness": ps,
                "faithfulness": judged["faithfulness"],
                "answer_relevance": judged["answer_relevance"],
                "answer_correctness": judged["answer_correctness"],
                "cost_per_request": cost, "unsupported": judged["unsupported"],
                "trace_id": resp.get("trace_id"), "tools_called": names,
                "latency_ms": resp.get("latency_ms"),
            }
            row["failed_metrics"] = failed_metrics(row)
            results.append(row)

            corr = row["answer_correctness"]
            flag = "" if not row["failed_metrics"] else \
                "  <-- FAIL " + ",".join(row["failed_metrics"])
            print(f"{case['id']:<8} {case.get('shape',''):<7} {ts:>5.2f} {ps:>6.2f} "
                  f"{judged['faithfulness']:>6.2f} {judged['answer_relevance']:>6.2f} "
                  f"{'   -  ' if corr is None else format(corr, '>6.2f')} "
                  f"{cost:>8.5f}  {','.join(names) or '-'}{flag}")

    if not results:
        print("\nNo results — is Nova reachable?")
        sys.exit(1)

    n = len(results)
    # Each metric averages over the cases that HAVE it. answer_correctness exists on a
    # subset, so dividing it by n would understate it in proportion to how many cases
    # lack a reference — an aggregate that moves when you add an unrelated case.
    agg = {}
    for k in ("tool_correctness", "argument_correctness", "faithfulness",
              "answer_relevance", "answer_correctness"):
        vals = [r[k] for r in results if r.get(k) is not None]
        agg[k] = (sum(vals) / len(vals)) if vals else None

    cost_per_request = sum(r["cost_per_request"] for r in results) / n
    latency_p95 = percentile([r["latency_ms"] or 0 for r in results], 95)

    print("-" * 92)
    print(f"{'AGG':<8} {'':<7} {agg['tool_correctness']:>5.2f} {agg['argument_correctness']:>6.2f} "
          f"{agg['faithfulness']:>6.2f} {agg['answer_relevance']:>6.2f} "
          f"{'   -  ' if agg['answer_correctness'] is None else format(agg['answer_correctness'], '>6.2f')} "
          f"{cost_per_request:>8.5f}  ({n} cases, {time.monotonic()-started:.0f}s, "
          f"p95 {latency_p95:.0f}ms)")

    failures = [r for r in results if r["failed_metrics"]]

    # Budget breaches fail the run without failing any single case: cost and latency
    # are properties of the run, not of one question. A prompt change that keeps every
    # quality score flat while doubling tokens per request is exactly the regression
    # this catches, and it is invisible in the per-case table above.
    budget_breaches = []
    if COST_BUDGET_USD and cost_per_request > COST_BUDGET_USD:
        budget_breaches.append(
            f"cost_per_request ${cost_per_request:.5f} > budget ${COST_BUDGET_USD:.5f}")
    if LATENCY_BUDGET_MS and latency_p95 > LATENCY_BUDGET_MS:
        budget_breaches.append(
            f"latency_p95 {latency_p95:.0f}ms > budget {LATENCY_BUDGET_MS:.0f}ms")

    if failures:
        print(f"\n{len(failures)} failing case(s):")
        for r in failures:
            print(f"  {r['question_id']}  failed={','.join(r['failed_metrics'])}  "
                  f"tools={r['tools_called']}  trace_id={r['trace_id']}")
            print(f'      q: "{r["question"]}"')
            print(f'      a: "{r["answer"][:160]}"')
            if r["unsupported"]:
                print(f"      unsupported: {r['unsupported']}")
    for b in budget_breaches:
        print(f"\nBUDGET: {b}")

    summary = {
        "run_id": run_id, "target": NOVA_URL, "judge_model": JUDGE_MODEL,
        "thresholds": THRESHOLDS, "aggregate": agg,
        "cost_per_request": cost_per_request, "latency_p95_ms": latency_p95,
        "cases": n, "failed": len(failures), "budget_breaches": budget_breaches,
    }

    out = f"eval-results-{run_id}.json"
    with open(out, "w") as f:
        json.dump({**summary, "results": results}, f, indent=2)
    print(f"\nwrote {out}")

    # Publish for Prometheus when running as a Job. Unset locally, so a laptop run is
    # unchanged. Deliberately AFTER the results file is written and BEFORE the exit
    # code: a failing run's scores are the ones the drift alert most needs to see, so
    # pushing only on success would blind the gate at the exact moment it matters.
    gateway = os.environ.get("PUSHGATEWAY_URL")
    if gateway:
        try:
            push_to_gateway(gateway, summary)
            print(f"pushed scores to {gateway}")
        except Exception as exc:
            # Never fail the run on a telemetry failure. The verdict is the exit code;
            # the push is how someone else finds out about it later.
            print(f"WARNING: pushgateway push failed: {exc}")

    # Exit non-zero on failure so this can gate a pipeline unchanged — the same
    # property the promotion gate relies on.
    sys.exit(1 if (failures or budget_breaches) else 0)


if __name__ == "__main__":
    main()
