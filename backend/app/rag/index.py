"""Hybrid retrieval: keyword (BM25) + meaning (dense vectors) → cross-encoder rerank.

Two interchangeable backends share one interface (`.search(query) -> SearchResult`):
- PineconeIndex (production, app/rag/pinecone_store.py): one sparse-dense hybrid query to Pinecone.
- HybridIndex (local fallback, this file): BM25 + numpy vectors fused with Reciprocal Rank Fusion.

`get_index()` returns a ResilientIndex that uses Pinecone and silently falls back to the
local copy if Pinecone is unreachable — so a vector-DB outage never stops customer answers.

Why each stage:
- BM25 catches exact tokens embeddings miss: "£3.95", "TP-CKW-001", "Klarna".
- Vectors catch paraphrases BM25 misses: "my pan arrived cracked" ≈ "damaged in transit".
- The cross-encoder reads (question, chunk) together and scores true relevance; its top score
  is also our confidence signal for saying "I don't know".
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np
from rank_bm25 import BM25Okapi

from ..config import get_settings
from .loaders import Chunk, load_all

log = logging.getLogger(__name__)

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


# ------------------------------------------------------------------ shared models (loaded once)
def _cache_dir() -> str:
    return str(get_settings().index_dir.parent / ".fastembed_cache")


@lru_cache
def get_embedder():
    from fastembed import TextEmbedding
    return TextEmbedding(get_settings().embed_model, cache_dir=_cache_dir())


@lru_cache
def get_reranker():
    from fastembed.rerank.cross_encoder import TextCrossEncoder
    return TextCrossEncoder(get_settings().rerank_model, cache_dir=_cache_dir())


def embed_query(text: str) -> np.ndarray:
    q = np.array(next(iter(get_embedder().query_embed(text))), dtype=np.float32)
    return q / np.linalg.norm(q)


def embed_passages(texts: list[str]) -> np.ndarray:
    emb = np.array(list(get_embedder().passage_embed(texts)), dtype=np.float32)
    return emb / np.linalg.norm(emb, axis=1, keepdims=True)


# ------------------------------------------------------------------ result types
@dataclass
class Hit:
    chunk: Chunk
    score: float = 0.0                 # cross-encoder relevance (logit)
    bm25_rank: int | None = None       # local backend only
    vector_rank: int | None = None     # local backend only
    rrf: float = 0.0                   # local backend only
    hybrid_rank: int | None = None     # pinecone backend: rank in the sparse-dense query
    hybrid_score: float | None = None

    def to_trace(self) -> dict:
        t = {"id": self.chunk.id, "citation": self.chunk.citation, "score": round(self.score, 3)}
        if self.hybrid_rank is not None:
            t |= {"hybrid_rank": self.hybrid_rank, "hybrid_score": round(self.hybrid_score or 0, 4)}
        else:
            t |= {"bm25_rank": self.bm25_rank, "vector_rank": self.vector_rank, "rrf": round(self.rrf, 4)}
        return t


@dataclass
class SearchResult:
    hits: list[Hit]
    timings_ms: dict = field(default_factory=dict)
    backend: str = "local"             # "pinecone" | "local" | "local-fallback"
    error: str | None = None           # why we fell back, if we did

    @property
    def top_score(self) -> float:
        return self.hits[0].score if self.hits else float("-inf")


def rerank(query: str, candidates: list[Hit], top_k: int) -> list[Hit]:
    scores = list(get_reranker().rerank(query, [h.chunk.text for h in candidates]))
    for h, sc in zip(candidates, scores):
        h.score = float(sc)
    return sorted(candidates, key=lambda h: -h.score)[:top_k]


# ------------------------------------------------------------------ local backend
class HybridIndex:
    """In-process index: the offline fallback, and what build() writes to backend/index/."""

    def __init__(self, chunks: list[Chunk], embeddings: np.ndarray):
        self.chunks = chunks
        self.embeddings = embeddings  # (n, d), L2-normalised
        self.bm25 = BM25Okapi([tokenize(c.text) for c in chunks])
        # Staff-only documents (file name starts "internal-") are never shown to customers.
        self.internal = np.array([c.doc.startswith("internal-") for c in chunks])

    @classmethod
    def build(cls) -> "HybridIndex":
        s = get_settings()
        chunks = load_all(s.docs_dir)
        if not chunks:
            raise RuntimeError(f"No documents found in {s.docs_dir}")
        emb = embed_passages([c.text for c in chunks])
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

    def search(self, query: str, top_k: int | None = None, include_internal: bool = False) -> SearchResult:
        s = get_settings()
        top_k = top_k or s.rag_top_k
        n = s.rag_candidates
        t0 = time.perf_counter()

        bm25_scores = self.bm25.get_scores(tokenize(query))
        if not include_internal:
            bm25_scores[self.internal] = 0
        bm25_order = [i for i in np.argsort(-bm25_scores)[:n] if bm25_scores[i] > 0]
        t1 = time.perf_counter()

        sims = self.embeddings @ embed_query(query)
        if not include_internal:
            sims[self.internal] = -np.inf
        vec_order = list(np.argsort(-sims)[:n])
        t2 = time.perf_counter()

        # Reciprocal Rank Fusion (k=60 is the standard constant from the RRF paper).
        fused: dict[int, Hit] = {}
        for rank, i in enumerate(bm25_order, 1):
            h = fused.setdefault(i, Hit(self.chunks[i]))
            h.bm25_rank, h.rrf = rank, h.rrf + 1 / (60 + rank)
        for rank, i in enumerate(vec_order, 1):
            h = fused.setdefault(i, Hit(self.chunks[i]))
            h.vector_rank, h.rrf = rank, h.rrf + 1 / (60 + rank)
        candidates = sorted(fused.values(), key=lambda h: -h.rrf)[:n]
        hits = rerank(query, candidates, top_k)
        t3 = time.perf_counter()

        return SearchResult(hits, {"bm25": round((t1 - t0) * 1000, 1), "vector": round((t2 - t1) * 1000, 1),
                                   "rerank": round((t3 - t2) * 1000, 1), "total": round((t3 - t0) * 1000, 1)})


# ------------------------------------------------------------------ resilient wrapper
class ResilientIndex:
    """Pinecone first; on any Pinecone error, answer from the local copy and record why."""

    def __init__(self, primary, fallback_factory, startup_error: str | None = None):
        self.primary = primary
        self._fallback_factory = fallback_factory
        self._fallback = None
        self.startup_error = startup_error  # Pinecone was wanted but could not be reached at startup

    @property
    def fallback(self) -> HybridIndex:
        if self._fallback is None:
            self._fallback = self._fallback_factory()
        return self._fallback

    def search(self, query: str, top_k: int | None = None, include_internal: bool = False) -> SearchResult:
        if self.primary is not None:
            try:
                return self.primary.search(query, top_k, include_internal)
            except Exception as e:  # noqa: BLE001 — any vector-DB failure must not stop answers
                log.warning("Pinecone search failed, using local fallback: %r", e)
                res = self.fallback.search(query, top_k, include_internal)
                res.backend, res.error = "local-fallback", repr(e)[:300]
                return res
        res = self.fallback.search(query, top_k, include_internal)
        if self.startup_error:
            res.backend, res.error = "local-fallback", self.startup_error
        return res


@lru_cache
def get_index() -> ResilientIndex:
    s = get_settings()

    def local() -> HybridIndex:
        return HybridIndex.load() if (s.index_dir / "chunks.json").exists() else HybridIndex.build()

    primary, startup_error = None, None
    if s.use_pinecone:
        from .pinecone_store import PineconeIndex
        try:
            primary = PineconeIndex()
        except Exception as e:  # noqa: BLE001
            startup_error = repr(e)[:300]
            log.warning("Pinecone unavailable at startup, using local index: %s", startup_error)
    return ResilientIndex(primary, local, startup_error)
