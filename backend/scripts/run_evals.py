"""ONE command for all evals:  python -m scripts.run_evals

1. RAG suite    — 40 questions (30 answerable + 10 must-say-"I don't know") against the knowledge base
2. Agent suite  — 62 end-to-end cases (routing, tools, DB effects, escalations, security, multi-turn)

Prints one combined score plus cost/latency, and saves evals/results/summary_latest.json.
Use --agents-only / --rag-only to run one suite.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def run_rag() -> dict:
    """Runs scripts.eval_rag in a subprocess (it uses the normal DB/index) and parses its score lines."""
    out = subprocess.run([sys.executable, "-m", "scripts.eval_rag"], cwd=REPO / "backend", capture_output=True,
                         text=True, encoding="utf-8", errors="replace", env={**__import__("os").environ,
                                                                             "PYTHONIOENCODING": "utf-8"}).stdout
    m = re.search(r"RAG answer score: (\d+)/(\d+).*?median latency (\d+) ms.*?total cost \$([\d.]+)", out)
    hit = re.search(r"hit@\d+: (\d+)/(\d+)", out)
    if not m:
        print(out[-2000:])
        raise SystemExit("RAG eval failed")
    for line in out.splitlines():
        if line.startswith("FAIL"):
            print("  " + line)
    return {"passed": int(m[1]), "cases": int(m[2]), "score": int(m[1]) / int(m[2]),
            "retrieval_hit_at_5": f"{hit[1]}/{hit[2]}" if hit else None,
            "latency_ms_p50": int(m[3]), "total_cost_usd": float(m[4])}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rag-only", action="store_true")
    ap.add_argument("--agents-only", action="store_true")
    a = ap.parse_args()
    results = {}

    if not a.agents_only:
        print("== RAG suite (40 questions) ==")
        results["rag"] = run_rag()
        r = results["rag"]
        print(f"RAG: {r['passed']}/{r['cases']} = {r['score']:.1%}  (retrieval hit@5 {r['retrieval_hit_at_5']})\n")

    if not a.rag_only:
        print("== Agent suite (62 cases) ==")
        from scripts import eval_agents
        sys.argv = [sys.argv[0], "--quiet"]
        s = eval_agents.main()
        results["agents"] = {k: s[k] for k in ("passed", "cases", "score", "routing_accuracy", "total_cost_usd",
                                               "avg_cost_per_case_usd", "latency_ms_p50", "latency_ms_p95",
                                               "by_category")}

    passed = sum(v["passed"] for v in results.values())
    cases = sum(v["cases"] for v in results.values())
    results["overall"] = {"passed": passed, "cases": cases, "score": round(passed / cases, 4),
                          "total_cost_usd": round(sum(v["total_cost_usd"] for v in results.values()), 5)}
    print("\n" + "#" * 64)
    print(f"#  OVERALL EVAL SCORE: {passed}/{cases} = {passed / cases:.1%}   "
          f"(total cost ${results['overall']['total_cost_usd']})")
    print("#" * 64)
    (REPO / "evals" / "results").mkdir(parents=True, exist_ok=True)
    (REPO / "evals" / "results" / "summary_latest.json").write_text(json.dumps(results, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
