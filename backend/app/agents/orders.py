"""Orders agent: an LLM tool-use loop over real database functions (app/agents/tools.py)."""
from __future__ import annotations

import json
import re
import time
from datetime import date

from ..llm import chat
from .base import AgentResult
from .memory import customer_profile
from .tools import REGISTRY, TOOL_SPECS, ToolContext

MAX_STEPS = 6

SYSTEM = """You are the Orders specialist for Thistle & Pine, a UK online homeware shop. Today is {today}.
You can look up orders and take real actions with your tools. Tools change the real database.

{profile}

How to work:
- General questions about shop rules or facts (how long to report damage, what happens when tracking stops, delivery days, returns rules, product care) - even when worded about "my parcel" or "my order" - call search_help_centre and answer from it, keeping its [n] source markers. Never state a policy, timescale, date or price from memory. If it returns found=false, say we don't have that information and give our contact details: support@thistleandpine.co.uk, 0113 496 0321.
- Answer the general part straight away, even for customers who are NOT VERIFIED. Then, if they want something done on a specific order, ask for the email address on the order and the order number.
- If the customer is NOT VERIFIED, ask for the email address on the order and the order number, then call verify_customer. Never discuss or change a specific order before verification.
- If the profile above says "(verified)", the customer is ALREADY verified (signed in): never ask for their email or to confirm identity — look up their orders with list_my_orders / get_order straight away.
- If the customer clearly asks for an action (refund, cancel, address change) and you have the order number and reason, do it now with the tool — don't ask "are you sure?". If something essential is missing (which order? which item? new address?), ask one short question.
- If the customer has exactly one order that fits what they describe, or says "my latest/last order", use that order from the profile without asking for the number.
- If the order has only one item, or the customer means the whole order, call issue_refund without sku — don't ask which item.
- For refunds, call issue_refund: the shop's policy engine decides the outcome and amount. Tell the customer what `tell_customer` says, in your own warm words. NEVER reveal internal_reasons, thresholds or policy rules behind a decision.
- For a parcel whose tracking is stuck or which seems lost, call open_courier_investigation.
- For compensation/goodwill requests or anything you cannot do, call handoff_to_human and tell the customer a colleague will follow up within one working day.
- Only state facts that come from tool results or the profile above. Never invent dates, amounts, tracking numbers or policies - look rules up with search_help_centre.
- Never ask for or repeat card details.
- Reply in British English, 1-4 short sentences, friendly and specific (order number, amount, next step)."""


def _tool_call_dict(tc) -> dict:
    return {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}


def run(text: str, history: list[dict], ctx: ToolContext, on_event=None) -> AgentResult:
    profile = customer_profile(ctx.session, ctx.conversation.customer_id)
    messages = [{"role": "system", "content": SYSTEM.format(today=date.today().isoformat(), profile=profile)},
                *history, {"role": "user", "content": text}]
    steps, calls = [], []

    for _ in range(MAX_STEPS):
        r = chat(messages, tools=TOOL_SPECS, max_tokens=1500, temperature=0.1)
        calls.append(r)
        if not r.tool_calls:
            reply = r.content.strip()
            break
        messages.append({"role": "assistant", "content": r.content or "",
                         "tool_calls": [_tool_call_dict(tc) for tc in r.tool_calls]})
        for tc in r.tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            why = args.pop("why", "")
            t0 = time.perf_counter()
            fn = REGISTRY.get(name)
            try:
                result = fn(ctx, **args) if fn else {"error": f"unknown tool {name}"}
            except TypeError as e:  # model passed wrong/missing arguments
                result = {"error": f"bad arguments: {e}"}
            ctx.session.flush()
            step = {"type": "tool", "tool": name, "args": args, "why": why, "result": result,
                    "ms": round((time.perf_counter() - t0) * 1000, 1)}
            steps.append(step)
            if on_event:
                on_event({"type": "tool_call", "tool": name, "args": args, "why": why, "result": result})
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result, default=str)})
    else:
        reply = "Sorry, I couldn't finish that just now — I've passed it to a colleague who'll get back to you."
        ctx.handoffs.append({"category": "agent_handoff", "severity": "medium", "reason": "orders agent hit step limit"})

    if not reply:
        reply = "Sorry, something went wrong on my side — a colleague will follow up shortly."
    errors = [s for s in steps if isinstance(s["result"], dict) and s["result"].get("error")]
    confidence = 0.95 if steps and not errors else (0.7 if not steps else 0.5)
    used = {int(n) for n in re.findall(r"\[(\d+)\]", reply)}
    citations = [c for c in ctx.citations if c["n"] in used]
    return AgentResult("orders", reply, citations=citations, steps=steps, llm_calls=calls, confidence=confidence,
                       handoffs=list(ctx.handoffs))
