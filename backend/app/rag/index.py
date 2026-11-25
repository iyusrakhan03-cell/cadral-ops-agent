"""Hybrid retrieval: BM25 (keyword) + dense vectors (meaning) → Reciprocal Rank Fusion → cross-encoder rerank.

Why each stage:
- BM25 catches exact tokens embeddings miss: "£3.95", "TP-CKW-001", "Klarna".
- Vectors catch paraphrases BM25 misses: "my pan arrived cracked" ≈ "damaged in transit".
- RRF merges the two ranked lists without needing their scores to be comparable.
- The cross-encoder reads (question, chunk) together and scores true relevance; its top score
  is also our confidence signal for saying "I don't know".
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np
from rank_bm25 import BM25Okapi

from ..config import get_settings
from .loaders import Chunk, load_all

_STOP = set("""a an the and or but if of to in on at for from by with about as is are was were be been being
do does did i me my we our you your it its this that these those can could would should will shall may might
how what when where which who why there here have has had not no so than then too very just any some""".split())


def tokenize(text: str) -> list[str]:
    toks = re.findall(r"£?\d+(?:\.\d+)?|[a-z]+(?:-[a-z0-9]+)*", text.lower())
    out = []
    for t in toks:
        if t in _STOP:
            continue
        if len(t) > 4 and t.endswith("s") and not t.endswith("ss"):
            t = t[:-1]  # crude plural folding: "returns" -> "return"
        out.append(t)
    return out


@dataclass
class Hit:
    chunk: Chunk
    score: float                      # cross-encoder relevance (logit)
    bm25_rank: int | None = None
    vector_rank: int | None = None
    rrf: float = 0.0

    def to_trace(self) -> dict:
        return {"id": self.chunk.id, "citation": self.chunk.citation, "score": round(self.score, 3),
                "bm25_rank": self.bm25_rank, "vector_rank": self.vector_rank, "rrf": round(self.rrf, 4)}


@dataclass
class SearchResult:
    hits: list[Hit]
    timings_ms: dict = field(default_factory=dict)

    @property
    def top_score(self) -> float:
        return self.hits[0].score if self.hits else float("-inf")


class HybridIndex:
    def __init__(self, chunks: list[Chunk], embeddings: np.ndarray):
        self.chunks = chunks
        self.embeddings = embeddings  # (n, d), L2-normalised
        self.bm25 = BM25Okapi([tokenize(c.text) for c in chunks])
        s = get_settings()
        cache = str(s.index_dir.parent / ".fastembed_cache")
        from fastembed import TextEmbedding
        from fastembed.rerank.cross_encoder import TextCrossEncoder
        self._embedder = TextEmbedding(s.embed_model, cache_dir=cache)
        self._reranker = TextCrossEncoder(s.rerank_model, cache_dir=cache)

    # ------------------------------------------------------------ build / load
    @classmethod
    def build(cls) -> "HybridIndex":
        s = get_settings()
        from fastembed import TextEmbedding
        chunks = load_all(s.docs_dir)
        if not chunks:
            raise RuntimeError(f"No documents found in {s.docs_dir}")
        embedder = TextEmbedding(s.embed_model, cache_dir=str(s.index_dir.parent / ".fastembed_cache"))
        emb = np.array(list(embedder.passage_embed([c.text for c in chunks])), dtype=np.float32)
        emb /= np.linalg.norm(emb, axis=1, keepdims=True)
        s.index_dir.mkdir(parents=True, exist_ok=True)
        np.save(s.index_dir / "embeddings.npy", emb)
        (s.index_dir / "chunks.json").write_text(
            json.dumps([c.to_dict() for c in chunks], ensure_ascii=False, indent=0), encoding="utf-8")
        return cls(chunks, emb)

    @classmethod
    def load(cls) -> "HybridIndex":
        s = get_settings()
        chunks = [Chunk(**d) for d in json.loads((s.index_dir / "chunks.json").read_text(encoding="utf-8"))]
        return cls(chunks, np.load(s.index_dir / "embeddings.npy"))

    # ------------------------------------------------------------ search
    def search(self, query: str, top_k: int | None = None) -> SearchResult:
        s = get_settings()
        top_k = top_k or s.rag_top_k
        n = s.rag_candidates
        t0 = time.perf_counter()

        bm25_scores = self.bm25.get_scores(tokenize(query))
        bm25_order = [i for i in np.argsort(-bm25_scores)[:n] if bm25_scores[i] > 0]
        t1 = time.perf_counter()

        q = np.array(next(iter(self._embedder.query_embed(query))), dtype=np.float32)
        q /= np.linalg.norm(q)
        vec_order = list(np.argsort(-(self.embeddings @ q))[:n])
        t2 = time.perf_counter()

        # Reciprocal Rank Fusion (k=60 is the standard constant from the RRF paper).
        fused: dict[int, Hit] = {}
        for rank, i in enumerate(bm25_order, 1):
            h = fused.setdefault(i, Hit(self.chunks[i], 0.0))
            h.bm25_rank, h.rrf = rank, h.rrf + 1 / (60 + rank)
        for rank, i in enumerate(vec_order, 1):
            h = fused.setdefault(i, Hit(self.chunks[i], 0.0))
            h.vector_rank, h.rrf = rank, h.rrf + 1 / (60 + rank)
        candidates = sorted(fused.values(), key=lambda h: -h.rrf)[:n]

        scores = list(self._reranker.rerank(query, [h.chunk.text for h in candidates]))
        for h, sc in zip(candidates, scores):
            h.score = float(sc)
        candidates.sort(key=lambda h: -h.score)
        t3 = time.perf_counter()

        return SearchResult(candidates[:top_k], {
            "bm25": round((t1 - t0) * 1000, 1), "vector": round((t2 - t1) * 1000, 1),
            "rerank": round((t3 - t2) * 1000, 1), "total": round((t3 - t0) * 1000, 1)})


@lru_cache
def get_index() -> HybridIndex:
    s = get_settings()
    if (s.index_dir / "chunks.json").exists():
        return HybridIndex.load()
    return HybridIndex.build()
