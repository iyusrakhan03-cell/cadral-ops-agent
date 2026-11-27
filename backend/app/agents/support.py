"""Support agent: answers from company documents (RAG) with citations, or says it doesn't know.

For follow-up questions ("and what about Ireland?") it first rewrites the message into a standalone
search query using the conversation history, so retrieval doesn't lose the context.
"""
from __future__ import annotations

import math

from ..llm import chat
from ..rag.answer import answer_question
from .base import AgentResult

SMALL_TALK_PROMPT = """You are the friendly customer support assistant for Thistle & Pine, a UK online homeware shop.
The customer sent a greeting, thanks, or an off-topic message. Reply in 1-2 short sentences (British English).
- Greetings: greet back and ask how you can help with their order or our products.
- Thanks / goodbye: respond warmly.
- Off-topic requests (homework, news, jokes, other companies): politely say you can only help with Thistle & Pine orders, products and policies.
- If asked whether you are a bot: say you are Thistle & Pine's AI assistant and a human colleague can step in any time.
- If the message asks a question about Thistle & Pine itself (the company, its people, jobs, apps, shops, partners, plans, ethics, anything a customer might want to know about us), reply with exactly: COMPANY_QUESTION
Never state prices, policies or facts here."""

REWRITE_PROMPT = """Rewrite the customer's latest message as ONE standalone search query for a homeware shop's help
centre, resolving references like "it", "that", "what about X" from the conversation. Output only the query."""


def _rewrite(text: str, history: list[dict]):
    convo = "\n".join(f"{m['role']}: {m['content'][:300]}" for m in history[-6:])
    r = chat([{"role": "system", "content": REWRITE_PROMPT},
              {"role": "user", "content": f"Conversation:\n{convo}\n\nLatest message: {text}"}],
             model="openai/gpt-oss-20b", max_tokens=300, temperature=0)
    q = r.content.strip().strip('"')
    return (q if 3 < len(q) < 300 else text), r


def run(text: str, label: str, history: list[dict], examples: list[dict] | None = None) -> AgentResult:
    if label == "chit_chat":
        r = chat([{"role": "system", "content": SMALL_TALK_PROMPT}, *history[-4:],
                  {"role": "user", "content": text}], max_tokens=500, temperature=0.3)
        if "COMPANY_QUESTION" not in r.content:
            return AgentResult("support", r.content.strip(), llm_calls=[r], confidence=0.9,
                               steps=[{"type": "small_talk", "why": "greeting / thanks / off-topic — no documents needed"}])
        # A question about the shop itself: look it up like any other question (or say we don't know).
        res = run(text, "policy_question", history, examples)
        res.llm_calls.insert(0, r)
        res.steps.insert(0, {"type": "small_talk", "why": "about Thistle & Pine itself - checking the documents"})
        return res

    steps, calls = [], []
    query = text
    if any(m["role"] == "assistant" for m in history):
        query, rw = _rewrite(text, history)
        calls.append(rw)
        steps.append({"type": "query_rewrite", "why": "follow-up question: made it standalone for search",
                      "query": query})

    res = answer_question(text, history=history, examples=examples, search_query=query)
    if res.llm:
        calls.append(res.llm)
    steps.append({"type": "retrieval", "why": "answer must come from company documents", **res.trace()["retrieval"],
                  "idk": res.idk, "idk_reason": res.idk_reason})
    # Confidence from the reranker's top score (logit -> 0..1); "I don't know" is low by definition.
    conf = 0.3 if res.idk else round(1 / (1 + math.exp(-res.retrieval.top_score / 2)), 3)
    return AgentResult("support", res.answer, citations=[c.__dict__ for c in res.citations], steps=steps,
                       llm_calls=calls, confidence=conf)
