"""Large-API-model baseline for the router comparison: GPT-OSS-120B (Groq) on the same 120 hand-written
test messages, same prompt and label parsing as the Colab notebook. Merges into finetune_results.json.

Run (from repo root):  backend/.venv/Scripts/python finetune/compare_api.py
"""
import json
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "backend"))
from app.agents.router import CLASSIFIER_PROMPT, LABELS, parse_label  # noqa: E402  (same prompt as training)
from app.llm import PRICES, client  # noqa: E402

MODEL = "openai/gpt-oss-120b"


def main():
    test = [json.loads(l) for l in (HERE / "data" / "test.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    preds, lats, tin, tout = [], [], 0, 0
    for i, r in enumerate(test, 1):
        for attempt in range(6):
            try:
                t0 = time.perf_counter()
                resp = client().chat.completions.create(
                    model=MODEL, temperature=0, max_tokens=400, extra_body={"reasoning_effort": "low"},
                    messages=[{"role": "system", "content": CLASSIFIER_PROMPT}, {"role": "user", "content": r["text"]}])
                lats.append((time.perf_counter() - t0) * 1000)
                break
            except Exception as e:  # rate limit on the free tier -> wait and retry
                time.sleep(min(5 * (attempt + 1), 30))
        else:
            raise SystemExit(f"failed on message {i}")
        preds.append(parse_label(resp.choices[0].message.content or "") or "unknown")
        tin += resp.usage.prompt_tokens
        tout += resp.usage.completion_tokens
        print(f"{i:3d}/{len(test)} {preds[-1]:17s} gold {r['label']}", flush=True)

    gold = [r["label"] for r in test]
    acc = sum(p == g for p, g in zip(preds, gold)) / len(gold)
    f1s = []
    for l in LABELS:
        tp = sum(p == l and g == l for p, g in zip(preds, gold))
        fp = sum(p == l and g != l for p, g in zip(preds, gold))
        fn = sum(p != l and g == l for p, g in zip(preds, gold))
        prec = tp / (tp + fp) if tp + fp else 0
        rec = tp / (tp + fn) if tp + fn else 0
        f1s.append(2 * prec * rec / (prec + rec) if prec + rec else 0)
    pin, pout = PRICES[MODEL]
    res = {"model": "GPT-OSS-120B via Groq API (zero-shot)", "accuracy": round(acc, 4),
           "macro_f1": round(sum(f1s) / len(f1s), 4), "median_latency_ms": round(statistics.median(lats), 1),
           "p95_latency_ms": round(sorted(lats)[int(len(lats) * .95) - 1], 1),
           "cost_per_1k_msgs_usd": round((tin * pin + tout * pout) / 1e6 / len(test) * 1000, 4),
           "avg_tokens_per_msg": round((tin + tout) / len(test)),
           "confusion": [[sum(p == pl and g == gl for p, g in zip(preds, gold)) for pl in LABELS] for gl in LABELS],
           "wrong": [(t["text"], g, p) for t, g, p in zip(test, gold, preds) if g != p]}
    path = HERE / "finetune_results.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["results"]["large_api"] = res
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")
    print(f"\nGPT-OSS-120B: accuracy {acc:.1%}, macro-F1 {res['macro_f1']}, median {res['median_latency_ms']} ms, "
          f"${res['cost_per_1k_msgs_usd']}/1k msgs -> saved to finetune_results.json")


if __name__ == "__main__":
    main()
