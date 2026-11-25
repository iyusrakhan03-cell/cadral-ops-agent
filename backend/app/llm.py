"""Thin wrapper around the Groq chat API.

- Retries transient failures (rate limits, timeouts, 5xx) with exponential backoff.
- Falls back to a smaller model if the main one keeps failing, so a customer still gets an answer.
- Returns token usage, latency and $ cost for every call (Groq free tier costs $0; we report the
  pay-as-you-go list price so cost-per-conversation is meaningful for planning).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from groq import APIConnectionError, APIStatusError, Groq, RateLimitError
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from .config import get_settings

# USD per 1M tokens (input, output) — Groq list prices.
PRICES = {
    "llama-3.3-70b-versatile": (0.59, 0.79),
    "llama-3.1-8b-instant": (0.05, 0.08),
}


class LLMUnavailable(RuntimeError):
    """Raised when every model failed — callers queue the message for retry instead of losing it."""


@dataclass
class LLMResult:
    content: str
    model: str
    tool_calls: list = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0
    fallback_used: bool = False

    @property
    def cost_usd(self) -> float:
        pin, pout = PRICES.get(self.model, (0.0, 0.0))
        return (self.prompt_tokens * pin + self.completion_tokens * pout) / 1_000_000

    def usage(self) -> dict:
        return {"model": self.model, "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens, "latency_ms": round(self.latency_ms, 1),
                "cost_usd": round(self.cost_usd, 6), "fallback_used": self.fallback_used}


def _transient(e: BaseException) -> bool:
    if isinstance(e, (RateLimitError, APIConnectionError)):
        return True
    return isinstance(e, APIStatusError) and e.status_code >= 500


_client: Groq | None = None


def client() -> Groq:
    global _client
    if _client is None:
        key = get_settings().groq_api_key
        if not key:
            raise LLMUnavailable("GROQ_API_KEY is not set")
        _client = Groq(api_key=key, timeout=30, max_retries=0)  # we do our own retries
    return _client


@retry(retry=retry_if_exception(_transient), stop=stop_after_attempt(4),
       wait=wait_exponential(multiplier=0.5, max=8), reraise=True)
def _call(model: str, messages: list[dict], **kw):
    return client().chat.completions.create(model=model, messages=messages, **kw)


def chat(messages: list[dict], *, temperature: float = 0.1, max_tokens: int = 700,
         tools: list | None = None, json_mode: bool = False, model: str | None = None) -> LLMResult:
    s = get_settings()
    kw: dict = {"temperature": temperature, "max_tokens": max_tokens}
    if tools:
        kw["tools"] = tools
    if json_mode:
        kw["response_format"] = {"type": "json_object"}

    models = [model or s.llm_model]
    if s.llm_fallback_model not in models:
        models.append(s.llm_fallback_model)

    last_err: Exception | None = None
    for i, m in enumerate(models):
        t0 = time.perf_counter()
        try:
            r = _call(m, messages, **kw)
        except Exception as e:  # noqa: BLE001 — try the next model, then give up loudly
            last_err = e
            continue
        msg = r.choices[0].message
        return LLMResult(
            content=msg.content or "", model=m, tool_calls=list(msg.tool_calls or []),
            prompt_tokens=r.usage.prompt_tokens if r.usage else 0,
            completion_tokens=r.usage.completion_tokens if r.usage else 0,
            latency_ms=(time.perf_counter() - t0) * 1000, fallback_used=i > 0,
        )
    raise LLMUnavailable(f"All models failed: {last_err!r}")
