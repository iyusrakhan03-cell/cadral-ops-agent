"""Central settings. Everything secret comes from environment variables (.env locally)."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_DIR / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- LLM ---
    groq_api_key: str = ""
    llm_model: str = "openai/gpt-oss-120b"
    llm_fallback_model: str = "openai/gpt-oss-20b"

    # --- Database (blank = local SQLite file) ---
    database_url: str = ""

    # --- RAG ---
    docs_dir: Path = REPO_DIR / "data" / "docs"
    index_dir: Path = BACKEND_DIR / "index"
    embed_model: str = "BAAI/bge-small-en-v1.5"
    rerank_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"
    rag_candidates: int = 20          # how many hits each retriever returns before fusion
    rag_top_k: int = 5                # how many chunks go to the LLM
    # Cross-encoder logit below which we refuse to answer ("I don't know").
    # Calibrated on data/eval_rag_qa.jsonl with scripts/eval_rag.py.
    rag_min_score: float = -5.0

    # --- Vector database (Pinecone serverless, free Starter plan) ---
    pinecone_api_key: str = ""
    pinecone_index: str = "thistle-pine-kb"
    pinecone_namespace: str = "kb"
    pinecone_cloud: str = "aws"
    pinecone_region: str = "us-east-1"   # the only region on the free Starter plan
    vector_store: str = "auto"           # auto = Pinecone if a key is set, else local | pinecone | local
    hybrid_alpha: float = 0.5            # 1.0 = meaning only, 0.0 = keywords only

    # --- Fine-tuned router model (Qwen2.5-1.5B LoRA, GGUF q4_k_m, runs on CPU via llama.cpp) ---
    # Uses backend/models/<file> if present; else downloads from ROUTER_GGUF_REPO; else falls back to the LLM.
    router_gguf_path: str = str(BACKEND_DIR / "models" / "tp-router-qwen2.5-1.5b-q4_k_m.gguf")
    router_gguf_repo: str = "iyusrakhan/tp-router-qwen2.5-1.5b-gguf"
    router_model: str = "auto"           # auto = fine-tuned if available | llm = always GPT-OSS-20B

    # --- Dashboard / API ---
    jwt_secret: str = ""                 # blank = random per process (everyone is signed out on restart)
    jwt_hours: int = 12
    cors_origins: str = "http://localhost:3000"   # comma-separated dashboard URLs
    # First-run staff accounts (created only if the users table is empty). CHANGE THESE in production.
    admin_email: str = "admin@thistleandpine.co.uk"
    admin_password: str = "admin-demo-123"
    agent_email: str = "agent@thistleandpine.co.uk"
    agent_password: str = "agent-demo-123"
    warmup_models: bool = True           # load search + router models at startup so the first chat is fast

    # --- Channels & automation (Day 4) ---
    # Shared secret for our own bridges (Gmail Apps Script, Vercel Telegram relay). Blank = channel endpoints off.
    bridge_key: str = ""
    # The dashboard's URL on Vercel; Telegram messages go out through its /api/telegram/send relay, because
    # Hugging Face Spaces can't reach api.telegram.org directly.
    relay_url: str = ""
    slack_webhook_url: str = ""          # Slack "Incoming Webhook" URL: escalations, system errors, daily report
    manager_email: str = ""              # who receives the daily management report
    report_hour: int = 8                 # daily report goes out at/after this hour...
    report_timezone: str = "Europe/London"   # ...in the shop's time zone
    dashboard_url: str = ""              # links in Slack alerts / the report point here
    workers: bool = True                 # background delivery + inbound + report workers (off in evals/tests)

    # --- Database backup to a private Hugging Face dataset (free hosting has no permanent disk) ---
    hf_token: str = ""
    db_backup_repo: str = ""             # e.g. <hf-user>/thistle-pine-data (private dataset). Blank = off.
    db_backup_every_s: int = 300

    @property
    def use_pinecone(self) -> bool:
        return self.vector_store == "pinecone" or (self.vector_store == "auto" and bool(self.pinecone_api_key))

    @property
    def db_url(self) -> str:
        if self.database_url:
            return self.database_url
        (BACKEND_DIR / "data").mkdir(exist_ok=True)
        return f"sqlite:///{(BACKEND_DIR / 'data' / 'local.db').as_posix()}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
