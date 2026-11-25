"""Ask the support knowledge base a question from the terminal.

  python -m scripts.ask "how much is express delivery?"
  python -m scripts.ask --search-only "is the cast iron pan dishwasher safe"
"""
import argparse
import json

from app.config import get_settings
from app.rag.index import get_index


def main():
    p = argparse.ArgumentParser()
    p.add_argument("question")
    p.add_argument("--search-only", action="store_true", help="show retrieval only (no LLM key needed)")
    a = p.parse_args()

    if a.search_only or not get_settings().groq_api_key:
        r = get_index().search(a.question)
        print(f"timings {r.timings_ms}  threshold {get_settings().rag_min_score}")
        for h in r.hits:
            print(f"{h.score:7.2f}  bm25#{h.bm25_rank}  vec#{h.vector_rank}  {h.chunk.citation}")
            print(f"         {h.chunk.text[:160]}")
        return

    from app.rag.answer import answer_question
    res = answer_question(a.question)
    print("\n" + res.answer + "\n")
    for c in res.citations:
        print(f"  [{c.n}] {c.title} — {c.location}  ({c.doc})")
    print("\ntrace:", json.dumps({k: v for k, v in res.trace().items() if k != "citations"}, indent=1)[:1500])


if __name__ == "__main__":
    main()
