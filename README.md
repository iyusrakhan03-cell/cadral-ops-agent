# Thistle & Pine — Autonomous AI Operations Agent

Multi-agent customer-operations system for a (fictional) UK homeware e-commerce shop.
Built for the Cadral AI "Autonomous AI Operations Agent" assessment.

> Full README, architecture diagram and write-up land on Day 5. Current status: **Day 1 — data, RAG, fine-tuning prep.**

## Quick start (local)

```bash
cd backend
python -m venv .venv && .venv/Scripts/activate      # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example ../.env                           # add GROQ_API_KEY

python -m scripts.render_pdfs      # policy Markdown -> real PDFs
python -m scripts.seed_db          # shop database (SQLite locally, Postgres via DATABASE_URL)
python -m scripts.build_index      # chunk + embed 22 documents
python -m scripts.ask "how much is express delivery?"
python -m scripts.eval_rag         # retrieval + answer evaluation
```

## Repo layout

| Path | What |
|---|---|
| `data/FACTS.md` | Single source of truth for the fictional company |
| `data/docs/` | Knowledge base: 13 web pages, 8 PDFs, products CSV (60 SKUs) |
| `data/eval_rag_qa.jsonl` | 40 RAG eval questions (30 answerable, 10 must-say-I-don't-know) |
| `backend/app/rag/` | Loaders → hybrid index (BM25 + vectors + RRF + cross-encoder rerank) → grounded answers |
| `backend/app/llm.py` | Groq client with retries, model fallback, token/cost/latency tracking |
| `backend/scripts/` | Seed, index, ask, eval |
| `finetune/` | Intent dataset, hand-written test set, Colab notebook (Qwen2.5-1.5B LoRA) |
