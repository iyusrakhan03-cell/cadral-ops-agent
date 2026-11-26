"""The fine-tuned Qwen2.5-1.5B router model, run locally on CPU via llama.cpp (~0.2 s/message, $0).

Trained in finetune/router_finetune.ipynb (46.7% -> 95.0% accuracy on 120 hand-written test messages),
published at https://huggingface.co/iyusrakhan/tp-router-qwen2.5-1.5b-gguf.

Loading order: local file (ROUTER_GGUF_PATH) -> download from ROUTER_GGUF_REPO -> unavailable.
If it is unavailable (no llama-cpp-python, no file, no network) the router uses GPT-OSS-20B instead.
"""
from __future__ import annotations

import logging
import os
import time
from functools import lru_cache
from pathlib import Path

from ..config import get_settings

log = logging.getLogger(__name__)
_last_ms: float = 0.0


@lru_cache
def _model():
    s = get_settings()
    if s.router_model == "llm":
        return None
    try:
        from llama_cpp import Llama
    except ImportError:
        log.warning("llama-cpp-python not installed — router uses the LLM classifier")
        return None
    kw = {"n_ctx": 512, "n_threads": max(1, (os.cpu_count() or 2) - 1), "verbose": False}
    try:
        if s.router_gguf_path and Path(s.router_gguf_path).exists():
            return Llama(model_path=s.router_gguf_path, **kw)
        if s.router_gguf_repo:
            from huggingface_hub import hf_hub_download
            path = hf_hub_download(s.router_gguf_repo, "tp-router-qwen2.5-1.5b-q4_k_m.gguf",
                                   local_dir=str(Path(s.router_gguf_path).parent))
            return Llama(model_path=path, **kw)
    except Exception as e:  # noqa: BLE001 — never let the optional model break routing
        log.warning("Fine-tuned router unavailable: %r", e)
    return None


def available() -> bool:
    return _model() is not None


def classify(text: str) -> str | None:
    global _last_ms
    from .router import CLASSIFIER_PROMPT, parse_label  # exact prompt the model was trained with
    m = _model()
    if m is None:
        return None
    t0 = time.perf_counter()
    out = m.create_chat_completion(messages=[{"role": "system", "content": CLASSIFIER_PROMPT},
                                             {"role": "user", "content": text}], max_tokens=8, temperature=0)
    _last_ms = (time.perf_counter() - t0) * 1000
    return parse_label(out["choices"][0]["message"]["content"])


def last_latency_ms() -> float:
    return _last_ms
