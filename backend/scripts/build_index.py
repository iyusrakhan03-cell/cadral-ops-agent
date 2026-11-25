"""Load every document, chunk it, embed it and save the search index.
Run:  python -m scripts.build_index
"""
from collections import Counter

from app.rag.index import HybridIndex


def main():
    idx = HybridIndex.build()
    docs = Counter(c.doc for c in idx.chunks)
    types = Counter(c.source_type for c in idx.chunks)
    print(f"Indexed {len(docs)} documents -> {len(idx.chunks)} chunks  {dict(types)}")
    for d, n in sorted(docs.items()):
        print(f"  {n:4d}  {d}")


if __name__ == "__main__":
    main()
