"""Pinecone backend: the knowledge base lives in a hosted vector database.

Each chunk is stored ONCE in Pinecone with two kinds of vector:
- dense  (384 numbers from bge-small)  → finds text with the same *meaning*
- sparse (BM25 keyword weights)        → finds text with the same *words*
A single "sparse-dense" query searches both at once; `hybrid_alpha` sets the balance.
The top candidates are then reranked by the cross-encoder, exactly like the local backend,
so the "I don't know" threshold stays calibrated.

Why BM25 as sparse vectors: dot(query_idf, doc_bm25_tf) equals the BM25 score, so Pinecone
computes real keyword relevance server-side — no keyword index has to live on our machine.
"""
from __future__ import annotations

import json
import math
import time
import zlib
from collections import Counter

from ..config import get_settings
from .index import Hit, SearchResult, embed_query, rerank, tokenize
from .loaders import Chunk

K1, B = 1.2, 0.75


def _token_id(tok: str) -> int:
    return zlib.crc32(tok.encode("utf-8"))  # stable uint32 index for a token


class BM25Sparse:
    """BM25 split into a document half (stored in Pinecone) and a query half (computed per search)."""

    def __init__(self, idf: dict[str, float], avgdl: float):
        self.idf, self.avgdl = idf, avgdl

    @classmethod
    def fit(cls, docs_tokens: list[list[str]]) -> "BM25Sparse":
        n = len(docs_tokens)
        df = Counter(t for toks in docs_tokens for t in set(toks))
        idf = {t: math.log((n - d + 0.5) / (d + 0.5) + 1) for t, d in df.items()}
        return cls(idf, sum(map(len, docs_tokens)) / n)

    def encode_doc(self, toks: list[str]) -> dict:
        tf, dl = Counter(toks), len(toks)
        norm = K1 * (1 - B + B * dl / self.avgdl)
        pairs = {_token_id(t): f * (K1 + 1) / (f + norm) for t, f in tf.items()}
        return {"indices": list(pairs), "values": [float(v) for v in pairs.values()]}

    def encode_query(self, toks: list[str]) -> dict:
        w = {_token_id(t): self.idf[t] for t in set(toks) if t in self.idf}
        total = sum(w.values()) or 1.0
        return {"indices": list(w), "values": [v / total for v in w.values()]}

    def save(self, path):
        path.write_text(json.dumps({"avgdl": self.avgdl, "idf": self.idf}), encoding="utf-8")

    @classmethod
    def load(cls, path) -> "BM25Sparse":
        d = json.loads(path.read_text(encoding="utf-8"))
        return cls(d["idf"], d["avgdl"])


def client():
    from pinecone import Pinecone
    key = get_settings().pinecone_api_key
    if not key:
        raise RuntimeError("PINECONE_API_KEY is not set")
    return Pinecone(api_key=key)


def ensure_index():
    """Create the serverless index on first run. dotproduct is required for sparse-dense hybrid."""
    from pinecone import ServerlessSpec
    s = get_settings()
    pc = client()
    if s.pinecone_index not in [i["name"] for i in pc.list_indexes()]:
        pc.create_index(name=s.pinecone_index, dimension=384, metric="dotproduct",
                        spec=ServerlessSpec(cloud=s.pinecone_cloud, region=s.pinecone_region))
        while not pc.describe_index(s.pinecone_index).status["ready"]:
            time.sleep(1)
    return pc.Index(s.pinecone_index)


def _metadata(c: Chunk) -> dict:
    return {"doc": c.doc, "title": c.title, "source_type": c.source_type, "location": c.location,
            "text": c.text, "internal": c.doc.startswith("internal-")}


def upsert_chunks(chunks: list[Chunk], dense) -> tuple[int, BM25Sparse]:
    """Replace the whole namespace with the given chunks. Returns (count, fitted BM25)."""
    s = get_settings()
    toks = [tokenize(c.text) for c in chunks]
    bm25 = BM25Sparse.fit(toks)
    bm25.save(s.index_dir / "bm25_params.json")   # the query side needs the same idf table

    idx = ensure_index()
    try:
        idx.delete(delete_all=True, namespace=s.pinecone_namespace)
    except Exception:  # noqa: BLE001 — namespace doesn't exist yet on first run
        pass
    vectors = [{"id": c.id, "values": dense[i].tolist(), "sparse_values": bm25.encode_doc(toks[i]),
                "metadata": _metadata(c)} for i, c in enumerate(chunks)]
    for start in range(0, len(vectors), 100):
        idx.upsert(vectors=vectors[start:start + 100], namespace=s.pinecone_namespace, show_progress=False)

    # Serverless indexes are eventually consistent: wait until every vector is countable.
    for _ in range(60):
        ns = idx.describe_index_stats().get("namespaces", {}).get(s.pinecone_namespace)
        if ns and ns["vector_count"] >= len(vectors):
            break
        time.sleep(1)
    return len(vectors), bm25


class PineconeIndex:
    def __init__(self):
        s = get_settings()
        self.idx = client().Index(s.pinecone_index)
        self.bm25 = BM25Sparse.load(s.index_dir / "bm25_params.json")

    def search(self, query: str, top_k: int | None = None, include_internal: bool = False) -> SearchResult:
        s = get_settings()
        top_k = top_k or s.rag_top_k
        a = s.hybrid_alpha
        t0 = time.perf_counter()
        dense = (embed_query(query) * a).tolist()
        sparse = self.bm25.encode_query(tokenize(query))
        sparse["values"] = [v * (1 - a) for v in sparse["values"]]
        t1 = time.perf_counter()

        kw = {"sparse_vector": sparse} if sparse["indices"] else {}
        res = self.idx.query(vector=dense, top_k=s.rag_candidates, include_metadata=True,
                             namespace=s.pinecone_namespace, timeout=8,
                             filter=None if include_internal else {"internal": {"$eq": False}}, **kw)
        t2 = time.perf_counter()

        candidates = []
        for rank, m in enumerate(res.matches, 1):
            md = m.metadata
            chunk = Chunk(id=m.id, doc=md["doc"], title=md["title"], source_type=md["source_type"],
                          location=md["location"], text=md["text"])
            candidates.append(Hit(chunk, hybrid_rank=rank, hybrid_score=float(m.score)))
        hits = rerank(query, candidates, top_k)
        t3 = time.perf_counter()
        return SearchResult(hits, {"encode": round((t1 - t0) * 1000, 1), "pinecone": round((t2 - t1) * 1000, 1),
                                   "rerank": round((t3 - t2) * 1000, 1), "total": round((t3 - t0) * 1000, 1)},
                            backend="pinecone")
