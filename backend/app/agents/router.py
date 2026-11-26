"""Router: decides which specialist handles a customer message.

Three layers, cheapest and safest first:
1. Safety net (rules)   — escalation triggers from the refund policy (injury, fire, legal, chargeback,
                          "speak to a manager"...). Always wins: a missed angry/at-risk customer is the
                          most expensive routing error, so we don't leave it to a model.
2. Classifier           — the fine-tuned Qwen2.5-1.5B (when its GGUF is available) or, failing that,
                          GPT-OSS-20B with the same label prompt.
3. Conversation context — short follow-ups ("yes please", an email address, an order number) stay with
                          the agent already handling the conversation ("sticky" routing).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..llm import LLMResult, chat
from .policy import escalation_triggers

LABELS = ["order_tracking", "order_action", "product_question", "policy_question", "escalation", "chit_chat"]
AGENT_FOR = {"order_tracking": "orders", "order_action": "orders", "product_question": "support",
             "policy_question": "support", "chit_chat": "support", "escalation": "escalation"}

# Same prompt the model was fine-tuned with (finetune/build_notebook.py) — keep in sync.
CLASSIFIER_PROMPT = """Classify the customer message for a UK homeware shop's support system into exactly one label:
order_tracking: asking where their order is or about its delivery status
order_action: wants a refund, return, cancellation, replacement or change to their own order
product_question: asks about a product (features, care, size, stock, price)
policy_question: asks about general shop policies (delivery costs, returns rules, payments, discounts, contact)
escalation: angry, threatens legal action or chargeback, reports injury or a safety hazard, or demands a human
chit_chat: greetings, thanks or off-topic
Reply with the label only."""

# Extra guidance for the zero-shot LLM classifier only (the fine-tuned model learned these from data).
LLM_HINTS = """
Notes: order_action / order_tracking only when the customer refers to THEIR OWN specific order or delivery.
General "can I return X?", "how do refunds work?" questions without a specific order are policy_question.
Questions about the company itself (jobs, owners, apps, partnerships, programmes) are policy_question, not chit_chat."""

_FOLLOWUP = re.compile(r"^\s*(yes|yeah|yep|ok(ay)?|sure|please( do)?|go ahead|do it|that'?s right|correct|"
                       r"no|nope|thanks?( you)?|cheers)\b[\s\w,.!']{0,40}$", re.I)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_ORDER = re.compile(r"\b(TP-?)?1\d{5}\b", re.I)


@dataclass
class Route:
    label: str
    agent: str
    source: str          # rules | finetuned | llm | sticky
    reason: str
    triggers: list[str]
    llm: LLMResult | None = None


def parse_label(out: str) -> str | None:
    o = out.lower().strip().replace(" ", "_").replace("-", "_")
    return next((l for l in LABELS if l in o), None)


def _classify_llm(text: str, prev_assistant: str | None) -> tuple[str, LLMResult]:
    user = text if not prev_assistant else f"(Our previous reply: {prev_assistant[:300]})\nCustomer message: {text}"
    r = chat([{"role": "system", "content": CLASSIFIER_PROMPT + LLM_HINTS}, {"role": "user", "content": user}],
             model="openai/gpt-oss-20b", max_tokens=300, temperature=0)
    return parse_label(r.content) or "policy_question", r


def route(text: str, history: list[dict], last_agent: str | None) -> Route:
    triggers = escalation_triggers(text)
    if triggers:
        return Route("escalation", "escalation", "rules", f"safety net matched: {', '.join(triggers)}", triggers)

    prev_assistant = next((m["content"] for m in reversed(history) if m["role"] == "assistant"), None)

    # Sticky routing: the customer is answering the orders agent's question (email, order no., "yes").
    if last_agent == "orders" and (_FOLLOWUP.match(text) or (_EMAIL.search(text) and len(text) < 120)
                                   or (_ORDER.search(text) and len(text) < 60)):
        return Route("order_action", "orders", "sticky", "short follow-up to the orders agent's question", [])

    from . import finetuned  # the fine-tuned Qwen2.5-1.5B, local CPU, ~0.2 s, $0
    label, llm, source, reason = None, None, "llm", ""
    if finetuned.available():
        label = finetuned.classify(text)
        source = "finetuned"
        reason = f"fine-tuned Qwen2.5-1.5B → {label} ({finetuned.last_latency_ms():.0f} ms, $0)"
    if label is None:
        label, llm = _classify_llm(text, prev_assistant)
        source = "llm"
        reason = f"GPT-OSS-20B classifier → {label} ({llm.latency_ms:.0f} ms)"

    # A bare order number with no other words is an order question.
    if label == "chit_chat" and _ORDER.search(text):
        label, reason = "order_tracking", reason + "; order number present → order_tracking"
    return Route(label, AGENT_FOR[label], source, reason, [], llm)
