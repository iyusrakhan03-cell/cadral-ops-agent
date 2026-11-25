"""Evaluate the RAG pipeline on data/eval_rag_qa.jsonl.

Retrieval metrics (no API key needed):
  - hit@k: is the expected source document among the top-k reranked chunks?
  - MRR:   how high the first correct chunk ranks
  - threshold sweep: best cross-encoder score cut-off for refusing unanswerable questions

Answer metrics (needs GROQ_API_KEY, or pass --retrieval-only):
  - answerable: answer is not "I don't know", contains every must_contain phrase, has >=1 citation
  - unanswerable: system says "I don't know"

Run:  python -m scripts.eval_rag [--retrieval-only]
"""
import argparse
import json
import re
import statistics

from app.config import REPO_DIR, get_settings
from app.rag.index import get_index


NUMBER_WORDS = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
                "eight": "8", "nine": "9", "ten": "10", "twelve": "12", "fourteen": "14", "thirty": "30"}


def norm(s: str) -> str:
    """Compare meaning, not typography: 'five working days' == '5 working days', 'hand-wash' == 'hand wash'."""
    s = s.lower().replace("’", "'").replace("-", " ").replace("‑", " ")
    s = re.sub(r"\b(" + "|".join(NUMBER_WORDS) + r")\b", lambda m: NUMBER_WORDS[m.group(1)], s)
    return re.sub(r"\s+", " ", s).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--retrieval-only", action="store_true")
    args = ap.parse_args()

    rows = [json.loads(l) for l in (REPO_DIR / "data" / "eval_rag_qa.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    idx = get_index()
    s = get_settings()

    hits, rr, pos_scores, neg_scores, misses = 0, [], [], [], []
    answerable = [r for r in rows if r["answerable"]]
    for r in rows:
        res = idx.search(r["question"])
        if not r["answerable"]:
            neg_scores.append(res.top_score)
            continue
        pos_scores.append(res.top_score)
        docs = [h.chunk.doc.rsplit(".", 1)[0] for h in res.hits]
        target = r["source"].rsplit(".", 1)[0]
        if target in docs:
            hits += 1
            rr.append(1 / (docs.index(target) + 1))
        else:
            rr.append(0)
            misses.append((r["id"], r["question"], r["source"], [h.chunk.doc for h in res.hits[:3]]))

    print(f"\n== Retrieval ({len(answerable)} answerable, {len(neg_scores)} unanswerable)")
    print(f"hit@{s.rag_top_k}: {hits}/{len(answerable)} = {hits / len(answerable):.1%}   MRR: {statistics.mean(rr):.3f}")
    for m in misses:
        print("  MISS", m)
    print(f"top score answerable   min {min(pos_scores):.2f}  median {statistics.median(pos_scores):.2f}")
    print(f"top score unanswerable max {max(neg_scores):.2f}  median {statistics.median(neg_scores):.2f}")
    print("threshold sweep (refuse if top score < t): t -> answerable kept / unanswerable refused")
    for t in [x / 2 for x in range(-12, 7)]:
        kept = sum(p >= t for p in pos_scores)
        refused = sum(n < t for n in neg_scores)
        mark = "  <- current" if abs(t - s.rag_min_score) < 1e-9 else ""
        print(f"  {t:5.1f}  {kept}/{len(pos_scores)}  {refused}/{len(neg_scores)}{mark}")

    if args.retrieval_only or not s.groq_api_key:
        if not s.groq_api_key:
            print("\n(no GROQ_API_KEY — skipped answer evaluation)")
        return

    from app.rag.answer import answer_question
    print("\n== Answers")
    ok, total_cost, lat = 0, 0.0, []
    for r in rows:
        res = answer_question(r["question"])
        lat.append(res.latency_ms)
        total_cost += res.llm.cost_usd if res.llm else 0
        if r["answerable"]:
            text = norm(res.answer)
            missing = [m for m in r["must_contain"] if norm(m) not in text]
            passed = not res.idk and not missing and res.citations
            why = "said IDK" if res.idk else (f"missing {missing}" if missing else ("no citation" if not res.citations else ""))
        else:
            passed, why = res.idk, "answered instead of IDK"
        ok += bool(passed)
        print(f"{'PASS' if passed else 'FAIL'}  {r['id']}  {r['question'][:60]!r}" + ("" if passed else f"  -> {why}: {res.answer[:120]!r}"))
    print(f"\nRAG answer score: {ok}/{len(rows)} = {ok / len(rows):.1%}   "
          f"median latency {statistics.median(lat):.0f} ms   total cost ${total_cost:.4f}")


if __name__ == "__main__":
    main()
