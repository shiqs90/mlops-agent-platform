"""Evaluation runner — replays a question set against Nova and scores four metrics.

This is the logic the Evaluation Hub will wrap. Running it as a script first is
deliberate: the CRD + controller is packaging, and packaging logic you haven't
validated is how you end up debugging Kubernetes when the problem is a scoring bug.

FOUR METRICS, and only one of them costs money:

  tool_selection      deterministic  did it call the tools we expected?
  parameter_accuracy  deterministic  with the right arguments?
  groundedness        LLM judge      is every claim supported by the tool result?
  cost_per_request    deterministic  tokens x model rate

The split matters more than the metrics. Three of four are arithmetic, so they are
free, instant, and can't drift. The judge is reserved for the one question
arithmetic cannot answer — whether free text is supported by a JSON blob.

WHY groundedness needs no expected answer: it compares the answer to what the tool
returned *in this run*, not to a stored string. That is what makes the suite valid
against changing data, and it is what will make Phase 5.5 drift monitoring possible
on live traffic, where no expected answer exists by definition.

ONE BLENDED SCORE WOULD TELL YOU LESS. Four scores localise a regression:
  tool_selection down          -> routing broke (prompt or tool descriptions)
  parameters down, routing ok  -> argument extraction broke
  grounding down, rest ok      -> right data fetched, model misread it
  cost up, quality flat        -> looping or over-calling tools
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

JUDGE_PROMPT = """You are grading a banking assistant's answer for GROUNDEDNESS.

Groundedness asks one question only: is every factual claim in the answer supported
by the tool results provided? You are NOT judging whether the answer is helpful,
well-written, or complete.

Mark a claim unsupported if it states a number, date, or fact that does not appear in
the tool results. If the tools returned an error or no data and the answer says so,
that is fully grounded — reporting missing data is correct behaviour. If the tools
returned nothing and the answer states a figure anyway, that is a fabrication and
scores 0.

TOOL RESULTS:
{tool_results}

ANSWER:
{answer}

Reply with JSON only:
{{"score": <0.0 to 1.0>, "unsupported": ["claim", ...]}}"""


def ask(client: httpx.Client, session_id: str, message: str) -> dict:
    r = client.post(f"{NOVA_URL}/chat", json={"session_id": session_id, "message": message},
                    timeout=90.0)
    r.raise_for_status()
    return r.json()


def score_tool_selection(actual: list[str], expected: list[str]) -> float:
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


def score_parameters(calls: list[dict], expected: dict) -> float:
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


def score_groundedness(anthropic_client, answer: str, tool_results: str) -> tuple[float, list]:
    """LLM judge. The one metric that needs a model, and the only one that costs."""
    if not answer.strip():
        return 0.0, ["empty answer"]
    msg = anthropic_client.messages.create(
        model=JUDGE_MODEL,
        max_tokens=512,
        messages=[{"role": "user", "content": JUDGE_PROMPT.format(
            tool_results=tool_results or "(no tools were called)", answer=answer)}],
    )
    raw = "".join(b.text for b in msg.content if b.type == "text").strip()
    try:
        # Judges wrap JSON in prose or fences often enough that this is worth doing.
        start, end = raw.find("{"), raw.rfind("}")
        parsed = json.loads(raw[start:end + 1])
        return float(parsed.get("score", 0.0)), parsed.get("unsupported", [])
    except Exception:
        return 0.0, [f"judge returned unparseable output: {raw[:120]}"]


def cost_usd(usage: dict, model: str) -> float:
    inp, out = RATES.get(model, (0.0, 0.0))
    return (usage.get("input_tokens", 0) / 1e6) * inp + (usage.get("output_tokens", 0) / 1e6) * out


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
    print(f"{'id':<8} {'shape':<7} {'tool':>5} {'param':>6} {'ground':>7} {'cost$':>8}  tools")
    print("-" * 78)

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

            ts = score_tool_selection(names, case.get("expect_tools", []))
            ps = score_parameters(calls, case.get("expect_params", {}))
            # Score against what the tools RETURNED, not what was called. Passing
            # tool_calls here scored a perfect agent at 0.17 — the judge was shown
            # {"tool": "check_balance", "args": {...}} and asked whether
            # "the balance is 185,254.95" was supported by it.
            evidence = resp.get("tool_results", [])
            gs, unsupported = score_groundedness(
                judge, resp.get("answer", ""),
                json.dumps(evidence, indent=2) if evidence else "")
            cost = cost_usd(resp.get("usage", {}), resp.get("model", ""))

            results.append({
                "run_id": run_id, "question_id": case["id"], "shape": case.get("shape"),
                "tool_selection": ts, "parameter_accuracy": ps, "groundedness": gs,
                "cost_per_request": cost, "unsupported": unsupported,
                "trace_id": resp.get("trace_id"), "tools_called": names,
                "latency_ms": resp.get("latency_ms"),
            })

            flag = "" if min(ts, ps, gs) >= 0.99 else "  <-- FAIL"
            print(f"{case['id']:<8} {case.get('shape',''):<7} {ts:>5.2f} {ps:>6.2f} "
                  f"{gs:>7.2f} {cost:>8.5f}  {','.join(names) or '-'}{flag}")

    if not results:
        print("\nNo results — is Nova reachable?")
        sys.exit(1)

    n = len(results)
    agg = {k: sum(r[k] for r in results) / n
           for k in ("tool_selection", "parameter_accuracy", "groundedness")}
    total_cost = sum(r["cost_per_request"] for r in results)

    print("-" * 78)
    print(f"{'AGG':<8} {'':<7} {agg['tool_selection']:>5.2f} {agg['parameter_accuracy']:>6.2f} "
          f"{agg['groundedness']:>7.2f} {total_cost:>8.5f}  ({n} cases, "
          f"{time.monotonic()-started:.0f}s)")

    failures = [r for r in results
                if min(r["tool_selection"], r["parameter_accuracy"], r["groundedness"]) < 0.99]
    if failures:
        print(f"\n{len(failures)} failing case(s):")
        for r in failures:
            print(f"  {r['question_id']}  tools={r['tools_called']}  "
                  f"trace_id={r['trace_id']}")
            if r["unsupported"]:
                print(f"      unsupported: {r['unsupported']}")

    out = f"eval-results-{run_id}.json"
    with open(out, "w") as f:
        json.dump({"run_id": run_id, "target": NOVA_URL, "judge_model": JUDGE_MODEL,
                   "aggregate": agg, "total_cost_usd": total_cost, "results": results},
                  f, indent=2)
    print(f"\nwrote {out}")

    # Exit non-zero on failure so this can gate a pipeline unchanged — the same
    # property the Hub's promotion gate will rely on.
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
