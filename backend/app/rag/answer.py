"""Grounded answering with citations and three layers of "I don't know" protection.

1. Retrieval gate  — if the best reranked chunk is below a calibrated score, we never call the LLM.
2. Prompt contract — the model may only use the numbered sources and must reply NO_ANSWER otherwise.
3. Citation check  — an answer that cites no valid source is discarded and replaced with "I don't know".
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from ..config import get_settings
from ..llm import LLMResult, chat
from .index import SearchResult, get_index

IDK_MESSAGE = ("That's a good question, but I don't have a reliable answer for it, and I'd rather not guess. "
               "Our customer care team can help: email support@thistleandpine.co.uk or call 0113 496 0321 "
               "(Mon–Fri 9am–6pm, Sat 10am–4pm). Is there anything else I can help you with?")

SYSTEM_PROMPT = """You are the customer support assistant for Thistle & Pine, a UK online homeware shop.
Answer the customer's question using ONLY the numbered sources provided.

Rules:
- Every sentence that states a fact must end with its source number in square brackets, e.g. "Standard delivery costs £3.95 [2]."
- Never use outside knowledge, never guess, never invent prices, dates, policies or products.
- If a source states a rule that settles the question, apply it and answer (e.g. "Klarna needs orders of £30+" answers "can I use Klarna on £20?" with "No").
- If the sources do not contain the answer, reply with exactly: NO_ANSWER
- If the sources answer only part of the question, answer that part and say clearly which part you don't know.
- Be warm, concise and plain-spoken (British English). 1-4 short sentences, or a short list if steps are needed.
- Never ask for card details or passwords."""


@dataclass
class Citation:
    n: int
    title: str
    location: str
    doc: str
    snippet: str


@dataclass
class AnswerResult:
    answer: str
    citations: list[Citation] = field(default_factory=list)
    idk: bool = False
    idk_reason: str | None = None
    retrieval: SearchResult | None = None
    llm: LLMResult | None = None
    latency_ms: float = 0.0

    def trace(self) -> dict:
        """Everything the dashboard's agent-trace panel needs to explain *why* this answer was given."""
        return {
            "idk": self.idk, "idk_reason": self.idk_reason,
            "retrieval": {
                "backend": self.retrieval.backend if self.retrieval else None,
                "fallback_reason": self.retrieval.error if self.retrieval else None,
                "top_score": round(self.retrieval.top_score, 3) if self.retrieval else None,
                "threshold": get_settings().rag_min_score,
                "timings_ms": self.retrieval.timings_ms if self.retrieval else {},
                "hits": [h.to_trace() for h in self.retrieval.hits] if self.retrieval else [],
            },
            "llm": self.llm.usage() if self.llm else None,
            "citations": [c.__dict__ for c in self.citations],
            "latency_ms": round(self.latency_ms, 1),
        }


def normalise(text: str) -> str:
    """Clean model typography: GPT-OSS cites as 【1】 and uses narrow/non-breaking spaces ("20 %", "10 am")."""
    text = text.strip()
    text = re.sub(r"【\s*(\d+)[^】]*】", r"[\1]", text)          # 【1】 / 【1†L3】 -> [1]
    text = re.sub(r"\[(\d+)\s*,\s*(\d+)\]", r"[\1][\2]", text)  # [1, 2] -> [1][2]
    text = text.replace(" ", " ").replace(" ", " ").replace("‑", "-").replace("‐", "-")
    text = re.sub(r"(\d) (%|°|mm\b|cm\b|am\b|pm\b)", r"\1\2", text)
    return text


def _format_sources(result: SearchResult) -> str:
    return "\n\n".join(f"[{i}] ({h.chunk.title} — {h.chunk.location})\n{h.chunk.text}"
                       for i, h in enumerate(result.hits, 1))


def answer_question(question: str, *, history: list[dict] | None = None,
                    examples: list[dict] | None = None, search_query: str | None = None) -> AnswerResult:
    """Answer from company docs.

    history:  previous turns [{"role": "user"|"assistant", "content": ...}] for conversational context.
    examples: human-corrected Q&A pairs from the feedback loop, injected as few-shot guidance.
    search_query: optional standalone rewrite of the question (e.g. resolving "what about Ireland?").
    """
    s = get_settings()
    t0 = time.perf_counter()
    result = get_index().search(search_query or question)

    # Layer 1: retrieval confidence gate.
    if result.top_score < s.rag_min_score:
        return AnswerResult(IDK_MESSAGE, idk=True, idk_reason="low_retrieval_score", retrieval=result,
                            latency_ms=(time.perf_counter() - t0) * 1000)

    system = SYSTEM_PROMPT
    if examples:
        system += "\n\nExamples of replies our human team approved (match their tone and accuracy):\n" + "\n".join(
            f"Customer: {e['question']}\nGood reply: {e['answer']}" for e in examples)

    messages = [{"role": "system", "content": system}]
    messages += (history or [])[-6:]
    messages.append({"role": "user", "content": f"Sources:\n{_format_sources(result)}\n\nCustomer question: {question}"})

    llm = chat(messages, temperature=0.1, max_tokens=1200)
    text = normalise(llm.content)

    # Layer 2: the model said it can't answer.
    if "NO_ANSWER" in text:
        return AnswerResult(IDK_MESSAGE, idk=True, idk_reason="model_no_answer", retrieval=result, llm=llm,
                            latency_ms=(time.perf_counter() - t0) * 1000)

    # Layer 3: keep only citations that point to real sources; no valid citation = no answer.
    used = sorted({int(n) for n in re.findall(r"\[(\d+)\]", text) if 1 <= int(n) <= len(result.hits)})
    if not used:
        return AnswerResult(IDK_MESSAGE, idk=True, idk_reason="no_valid_citations", retrieval=result, llm=llm,
                            latency_ms=(time.perf_counter() - t0) * 1000)
    text = re.sub(r"\[(\d+)\]", lambda m: m.group(0) if int(m.group(1)) in used else "", text)

    citations = [Citation(n, h.chunk.title, h.chunk.location, h.chunk.doc, h.chunk.text[:240])
                 for n, h in ((n, result.hits[n - 1]) for n in used)]
    return AnswerResult(text, citations, retrieval=result, llm=llm, latency_ms=(time.perf_counter() - t0) * 1000)
