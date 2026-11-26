"""Load every document, chunk it, embed it, save the local fallback index and upload to Pinecone.
Run:  python -m scripts.build_index            (local + Pinecone if PINECONE_API_KEY is set)
      python -m scripts.build_index --local    (local only)
"""
import argparse
import time
from collections import Counter

from app.config import get_settings
from app.rag.index import HybridIndex


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", action="store_true", help="skip the Pinecone upload")
    args = ap.parse_args()

    idx = HybridIndex.build()
    docs = Counter(c.doc for c in idx.chunks)
    types = Counter(c.source_type for c in idx.chunks)
    print(f"Indexed {len(docs)} documents -> {len(idx.chunks)} chunks  {dict(types)}")
    for d, n in sorted(docs.items()):
        print(f"  {n:4d}  {d}")
    print(f"Local fallback index saved to {get_settings().index_dir}")

    s = get_settings()
    if args.local or not s.pinecone_api_key:
        print("(Pinecone upload skipped)")
        return
    from app.rag.pinecone_store import upsert_chunks
    t0 = time.time()
    n, bm25 = upsert_chunks(idx.chunks, idx.embeddings)
    print(f"Uploaded {n} vectors (dense 384-d + BM25 sparse, vocab {len(bm25.idf)}) to Pinecone "
          f"index '{s.pinecone_index}' / namespace '{s.pinecone_namespace}' in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
