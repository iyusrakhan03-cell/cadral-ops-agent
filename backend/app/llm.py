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

# USD per 1M tokens (input, output) — Groq pay-as-you-go list prices (we run on the free tier).
PRICES = {
    "openai/gpt-oss-120b": (0.15, 0.60),
    "openai/gpt-oss-20b": (0.075, 0.30),
    "qwen/qwen3.8-27b": (0.29, 0.59),
}
# GPT-OSS models "think" before answering; low effort keeps support replies fast and cheap.
REASONING_MODELS = {"openai/gpt-oss-120b", "openai/gpt-oss-20b"}


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


MAX_RETRY_WAIT_S = 30.0
LAST_RESORT_MODEL = "qwen/qwen3.8-27b"   # separate free-tier quota pool, used only if both GPT-OSS models are out
_cooldown_until: dict[str, float] = {}   # circuit breaker: model -> time it may be used again


def _retry_after(e: Exception) -> float | None:
    try:
        return float(e.response.headers.get("retry-after"))
    except (TypeError, ValueError, AttributeError):
        return None


def _transient(e: BaseException) -> bool:
    if isinstance(e, RateLimitError):
        # Per-minute limits clear in seconds — worth waiting. A daily-quota 429 says "try in 13 min":
        # don't block the customer; give up on this model now and let chat() fall back.
        ra = _retry_after(e)
        return ra is None or ra <= MAX_RETRY_WAIT_S
    if isinstance(e, APIConnectionError):
        return True
    return isinstance(e, APIStatusError) and e.status_code >= 500


def model_status() -> dict:
    """For the dashboard/health check: which models are currently cooling down and for how long."""
    now = time.time()
    return {m: round(t - now) for m, t in _cooldown_until.items() if t > now}


_client: Groq | None = None


def client() -> Groq:
    global _client
    if _client is None:
        key = get_settings().groq_api_key
        if not key:
            raise LLMUnavailable("GROQ_API_KEY is not set")
        _client = Groq(api_key=key, timeout=30, max_retries=0)  # we do our own retries
    return _client


_backoff = wait_exponential(multiplier=0.5, max=8)


def _wait(retry_state) -> float:
    """Honour Groq's Retry-After on 429 (free tier: 8k tokens/min resets within ~60s); else exponential backoff."""
    e = retry_state.outcome.exception()
    if isinstance(e, RateLimitError):
        try:
            return min(float(e.response.headers.get("retry-after", 5)) + 0.5, 30.0)
        except (TypeError, ValueError, AttributeError):
            return 10.0
    return _backoff(retry_state)


@retry(retry=retry_if_exception(_transient), stop=stop_after_attempt(5), wait=_wait, reraise=True)
def _call(model: str, messages: list[dict], **kw):
    return client().chat.completions.create(model=model, messages=messages, **kw)


def chat(messages: list[dict], *, temperature: float = 0.1, max_tokens: int = 1500,
         tools: list | None = None, json_mode: bool = False, model: str | None = None,
         reasoning_effort: str = "low") -> LLMResult:
    s = get_settings()
    kw: dict = {"temperature": temperature, "max_tokens": max_tokens}
    if tools:
        kw["tools"] = tools
    if json_mode:
        kw["response_format"] = {"type": "json_object"}

    models = [model or s.llm_model]
    for m in (s.llm_fallback_model, LAST_RESORT_MODEL):
        if m not in models:
            models.append(m)

    last_err: Exception | None = None
    now = time.time()
    for i, m in enumerate(models):
        if _cooldown_until.get(m, 0) > now:   # circuit open: this model's quota is exhausted, skip instantly
            last_err = last_err or RuntimeError(f"{m} cooling down")
            continue
        t0 = time.perf_counter()
        if m in REASONING_MODELS:
            extra = {"extra_body": {"reasoning_effort": reasoning_effort}}
        else:
            extra = {"extra_body": {"reasoning_format": "hidden"}}  # Qwen: don't return <think> text
        try:
            r = _call(m, messages, **kw, **extra)
        except Exception as e:  # noqa: BLE001 — try the next model, then give up loudly
            last_err = e
            ra = _retry_after(e) if isinstance(e, RateLimitError) else None
            if ra and ra > MAX_RETRY_WAIT_S:
                _cooldown_until[m] = time.time() + ra
                from . import notify
                notify.system_error("llm", f"{m} quota exhausted for {ra / 60:.0f} min — falling back")
            continue
        msg = r.choices[0].message
        return LLMResult(
            content=msg.content or "", model=m, tool_calls=list(msg.tool_calls or []),
            prompt_tokens=r.usage.prompt_tokens if r.usage else 0,
            completion_tokens=r.usage.completion_tokens if r.usage else 0,
            latency_ms=(time.perf_counter() - t0) * 1000, fallback_used=i > 0,
        )
    raise LLMUnavailable(f"All models failed: {last_err!r}")
