# Thistle & Pine — Autonomous AI Operations Agent

Multi-agent customer-operations system for a (fictional) UK homeware e-commerce shop.
Built for the Cadral AI "Autonomous AI Operations Agent" assessment.

> Full README, architecture diagram and write-up land on Day 5. Current status: **Day 1 — data, RAG, fine-tuning prep.**

## Quick start (local)

```bash
cd backend
python -m venv .venv && .venv/Scripts/activate      # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example ../.env                           # add GROQ_API_KEY (+ PINECONE_API_KEY, optional)

# The demo database (backend/data/local.db) and search index (backend/index/) are committed,
# so you can ask questions straight away. Embedding/reranker models (~150 MB) download on first run.
python -m scripts.ask "how much is express delivery?"
python -m scripts.eval_rag         # retrieval + answer evaluation

# Only needed after you change the documents or want fresh demo data:
python -m scripts.render_pdfs      # policy Markdown -> real PDFs
python -m scripts.build_index      # re-chunk + re-embed the documents, upload to Pinecone
python -m scripts.seed_db          # recreate the shop database (dates are relative to today)
```

## Repo layout

| Path | What |
|---|---|
| `data/FACTS.md` | Single source of truth for the fictional company |
| `data/docs/` | Knowledge base: 13 web pages, 8 PDFs, products CSV (60 SKUs) |
| `data/eval_rag_qa.jsonl` | 40 RAG eval questions (30 answerable, 10 must-say-I-don't-know) |
| `backend/app/rag/` | Loaders → hybrid search → cross-encoder rerank → grounded answers with citations |
| `backend/app/rag/pinecone_store.py` | **Pinecone** serverless index: one sparse-dense query (BM25 keywords + bge-small meaning); falls back to the local index in `backend/index/` if Pinecone is unreachable |
| `backend/app/llm.py` | Groq client with retries, model fallback, token/cost/latency tracking |
| `backend/scripts/` | Seed, index, ask, eval |
| `finetune/` | Intent dataset, hand-written test set, Colab notebook (Qwen2.5-1.5B LoRA) |
