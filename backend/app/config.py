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
    llm_model: str = "llama-3.3-70b-versatile"
    llm_fallback_model: str = "llama-3.1-8b-instant"

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
    rag_min_score: float = -3.0

    @property
    def db_url(self) -> str:
        if self.database_url:
            return self.database_url
        (BACKEND_DIR / "data").mkdir(exist_ok=True)
        return f"sqlite:///{(BACKEND_DIR / 'data' / 'local.db').as_posix()}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
