"""End-to-end evaluation of the multi-agent system on data/eval_agents.jsonl (62 cases).

Each case runs on a FRESH copy of a seeded demo database, then checks:
  routing (which agent / label / source), tools called, real database effects (refund rows, order status,
  address), escalations raised, conversation status, "I don't know" + citations, and what the reply
  must / must not say (e.g. internal thresholds, other customers' data).

Run:  python -m scripts.eval_agents                 # all cases
      python -m scripts.eval_agents --only act_     # cases whose id starts with act_
Writes evals/results/agents_latest.json and appends a line to evals/history.jsonl (accuracy over time).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EVAL_DB = REPO / "backend" / "data" / "eval.db"
os.environ["DATABASE_URL"] = f"sqlite:///{EVAL_DB.as_posix()}"   # must be set before importing app.*
logging.disable(logging.WARNING)

from app.agents.orchestrator import handle_turn  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.models import Conversation, Escalation, Order, Refund  # noqa: E402

SEVERITY = ["low", "medium", "high", "critical"]


def norm(s: str) -> str:
    s = s.replace("’", "'").replace(" ", " ").replace(" ", " ").replace("‑", "-")
    return re.sub(r"\s+", " ", s).lower()


def seed_snapshot() -> bytes:
    from scripts import seed_db
    import io, contextlib
    with contextlib.redirect_stdout(io.StringIO()):
        seed_db.main()
    engine.dispose()
    return EVAL_DB.read_bytes()


def restore(snapshot: bytes):
    engine.dispose()
    EVAL_DB.write_bytes(snapshot)


def check_case(case: dict, results: list, conv_id: str, before_refund_ids: set, before_esc_ids: set,
               before_addresses: dict) -> list[tuple[str, bool, str]]:
    exp, last = case["expect"], results[-1]
    checks: list[tuple[str, bool, str]] = []
    tool_steps = [st for r in results for st in r.steps if st["type"] == "tool"]
    tools_called = [st["tool"] for st in tool_steps]
    all_replies = " ".join(norm(r.reply) for r in results)
    reply = norm(last.reply)

    def add(name, ok, detail=""):
        checks.append((name, bool(ok), detail))

    if "agent" in exp:
        add("agent", last.agent == exp["agent"], f"got {last.agent}")
    if "route_label" in exp:
        add("route_label", last.route.get("label") == exp["route_label"], f"got {last.route.get('label')}")
    if "route_source" in exp:
        add("route_source", last.route.get("source") == exp["route_source"], f"got {last.route.get('source')}")
    if "idk" in exp:
        ret = [st for st in last.steps if st["type"] == "retrieval"]
        got = ret[-1]["idk"] if ret else None
        add("idk", got == exp["idk"], f"got {got}")
    if exp.get("citations"):
        add("citations", len(last.citations) > 0, f"{len(last.citations)} citations")
    if "tools_any" in exp:
        add("tools", any(t in tools_called for t in exp["tools_any"]), f"called {tools_called}")
    if exp.get("tools_none"):
        add("tools_none", not tools_called, f"called {tools_called}")
    for s in exp.get("reply_contains", []):
        add("reply_contains", norm(s) in reply, f"missing {s!r}")
    if "reply_contains_any" in exp:
        add("reply_contains_any", any(norm(s) in reply for s in exp["reply_contains_any"]),
            f"none of {exp['reply_contains_any'][:3]}…")
    for s in exp.get("reply_not_contains", []):
        add("reply_not_contains", norm(s) not in all_replies, f"leaked {s!r}")

    with SessionLocal() as s:
        new_refunds = [r for r in s.query(Refund).all() if r.id not in before_refund_ids]
        new_escs = [e for e in s.query(Escalation).all() if e.id not in before_esc_ids]
        if "refund" in exp:
            want = exp["refund"]
            hit = [r for r in new_refunds if r.order_id == want["order"] and r.status == want["status"]]
            add("db_refund", hit, f"new refunds: {[(r.order_id, r.status, float(r.amount)) for r in new_refunds]}")
        if exp.get("no_new_refunds"):
            issued = [r for r in new_refunds if r.status == "issued"]
            add("db_no_money_moved", not issued, f"issued: {[(r.order_id, float(r.amount)) for r in issued]}")
        if "refund_not_issued" in exp:
            bad = [r for r in new_refunds if r.order_id == exp["refund_not_issued"] and r.status == "issued"]
            add("db_refund_not_issued", not bad, f"issued: {[(r.order_id, float(r.amount)) for r in bad]}")
        for oid, st in exp.get("order_status", {}).items():
            o = s.get(Order, oid)
            add("db_order_status", o and o.status == st, f"{oid} is {o.status if o else None}")
        for oid, sub in exp.get("address_contains", {}).items():
            o = s.get(Order, oid)
            add("db_address", o and sub.lower() in o.shipping_address.lower(), f"{oid}: {o.shipping_address if o else None}")
        if "address_unchanged" in exp:
            oid = exp["address_unchanged"]
            add("db_address_unchanged", s.get(Order, oid).shipping_address == before_addresses[oid], "address changed")
        if "escalation" in exp:
            want = exp["escalation"]
            ok = [e for e in new_escs
                  if ("category_in" not in want or e.category in want["category_in"])
                  and ("severity_at_least" not in want
                       or SEVERITY.index(e.severity) >= SEVERITY.index(want["severity_at_least"]))]
            add("escalation", ok, f"got {[(e.category, e.severity) for e in new_escs]}")
        if "conversation_status" in exp:
            conv = s.get(Conversation, conv_id)
            add("conversation_status", conv.status == exp["conversation_status"], f"got {conv.status}")
    if any(r.error for r in results):
        add("no_errors", False, "; ".join(r.error for r in results if r.error))
    return checks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="run cases whose id starts with this prefix")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    cases = [json.loads(l) for l in (REPO / "data" / "eval_agents.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    if args.only:
        cases = [c for c in cases if any(c["id"].startswith(p) for p in args.only.split(","))]
    snapshot = seed_snapshot()
    print(f"Running {len(cases)} cases (fresh demo DB per case)…\n")

    rows, t_start = [], time.time()
    for i, case in enumerate(cases, 1):
        restore(snapshot)
        with SessionLocal() as s:
            before_refunds = {r.id for r in s.query(Refund.id)}
            before_escs = {e.id for e in s.query(Escalation.id)}
            before_addr = {o.id: o.shipping_address for o in s.query(Order).all()}
        results, conv_id = [], None
        for turn in case["turns"]:
            r = handle_turn(turn, conversation_id=conv_id, customer_email=case.get("email"))
            conv_id = r.conversation_id
            results.append(r)
        checks = check_case(case, results, conv_id, before_refunds, before_escs, before_addr)
        passed = all(ok for _, ok, _ in checks)
        cost = sum(r.usage.get("cost_usd", 0) for r in results)
        lat = [r.usage.get("latency_ms", 0) for r in results]
        tokens = sum(r.usage.get("prompt_tokens", 0) + r.usage.get("completion_tokens", 0) for r in results)
        rows.append({"id": case["id"], "category": case["category"], "passed": passed, "cost_usd": cost,
                     "latency_ms": lat, "tokens": tokens, "agent": results[-1].agent,
                     "route": results[-1].route, "reply": results[-1].reply,
                     "checks": [{"check": n, "ok": ok, "detail": d} for n, ok, d in checks]})
        mark = "PASS" if passed else "FAIL"
        print(f"[{i:2d}/{len(cases)}] {mark} {case['id']:8s} {results[-1].agent:10s} "
              f"{statistics.mean(lat):6.0f} ms  ${cost:.5f}")
        if not passed and not args.quiet:
            for n, ok, d in checks:
                if not ok:
                    print(f"        ✗ {n}: {d}")
            print(f"        reply: {results[-1].reply[:200]!r}")
        sys.stdout.flush()

    # ---------------------------------------------------------------- summary
    n = len(rows)
    passed = sum(r["passed"] for r in rows)
    all_checks = [c for r in rows for c in r["checks"]]
    by_check: dict[str, list[bool]] = {}
    for c in all_checks:
        by_check.setdefault(c["check"], []).append(c["ok"])
    by_cat: dict[str, list[bool]] = {}
    for r in rows:
        by_cat.setdefault(r["category"], []).append(r["passed"])
    lats = [l for r in rows for l in r["latency_ms"]]
    routing = [c["ok"] for c in all_checks if c["check"] == "agent"]
    summary = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cases": n, "passed": passed, "score": round(passed / n, 4) if n else 0,
        "check_pass_rate": round(sum(c["ok"] for c in all_checks) / len(all_checks), 4) if all_checks else 0,
        "routing_accuracy": round(sum(routing) / len(routing), 4) if routing else None,
        "by_category": {k: f"{sum(v)}/{len(v)}" for k, v in sorted(by_cat.items())},
        "by_check": {k: f"{sum(v)}/{len(v)}" for k, v in sorted(by_check.items())},
        "total_cost_usd": round(sum(r["cost_usd"] for r in rows), 5),
        "avg_cost_per_case_usd": round(sum(r["cost_usd"] for r in rows) / n, 6) if n else 0,
        "latency_ms_p50": round(statistics.median(lats)) if lats else 0,
        "latency_ms_p95": round(sorted(lats)[max(0, int(len(lats) * 0.95) - 1)]) if lats else 0,
        "avg_tokens_per_case": round(sum(r["tokens"] for r in rows) / n) if n else 0,
        "wall_clock_s": round(time.time() - t_start),
    }
    print("\n" + "=" * 64)
    print(f"AGENT EVAL SCORE: {passed}/{n} cases = {summary['score']:.1%}   "
          f"(checks {summary['check_pass_rate']:.1%}, routing {summary['routing_accuracy']:.1%})")
    print(f"by category: {summary['by_category']}")
    print(f"by check:    {summary['by_check']}")
    print(f"cost ${summary['total_cost_usd']} total, ${summary['avg_cost_per_case_usd']}/case · "
          f"latency p50 {summary['latency_ms_p50']} ms, p95 {summary['latency_ms_p95']} ms · "
          f"{summary['avg_tokens_per_case']} tokens/case · {summary['wall_clock_s']} s")

    out = REPO / "evals"
    (out / "results").mkdir(parents=True, exist_ok=True)
    if not args.only:
        (out / "results" / "agents_latest.json").write_text(json.dumps({"summary": summary, "cases": rows}, indent=1,
                                                                       default=str), encoding="utf-8")
        with (out / "history.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"suite": "agents", **{k: summary[k] for k in (
                "timestamp", "cases", "passed", "score", "routing_accuracy", "total_cost_usd", "latency_ms_p50")}}) + "\n")
    return summary


if __name__ == "__main__":
    main()
