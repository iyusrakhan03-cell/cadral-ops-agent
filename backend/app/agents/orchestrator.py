"""Orchestrator: runs one customer turn end-to-end.

  message in → save it → Router → specialist agent (support | orders | escalation)
             → escalation tickets for any handoffs → save reply + trace → result out

Every turn writes a Trace row (route decision, every tool call with the agent's stated reason,
tokens, $ cost, latency) — the dashboard's agent-trace view reads it directly.
`on_event` lets the API stream progress (routing decided, tool called...) to the dashboard live.
"""
from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field

from .. import notify
from ..db import SessionLocal
from ..llm import LLMResult, LLMUnavailable
from ..models import Conversation, Customer, Message, Trace
from . import escalation, orders, support
from .memory import history
from .router import route
from .tools import ToolContext

log = logging.getLogger(__name__)

HOLDING_REPLY = ("Thanks for your message — it's been added to your case, and the colleague handling it "
                 "will reply personally.")
ERROR_REPLY = ("Sorry, I'm having a technical problem right now. Your message is saved and a member of our team "
               "will get back to you shortly.")


@dataclass
class TurnResult:
    conversation_id: str
    reply: str
    agent: str
    route: dict
    citations: list = field(default_factory=list)
    steps: list = field(default_factory=list)
    escalations: list = field(default_factory=list)
    actions: list = field(default_factory=list)
    status: str = "open"
    message_status: str = "sent"
    trace_id: int | None = None
    usage: dict = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _usage(calls: list[LLMResult], latency_ms: float) -> dict:
    return {"llm_calls": len(calls), "prompt_tokens": sum(c.prompt_tokens for c in calls),
            "completion_tokens": sum(c.completion_tokens for c in calls),
            "cost_usd": round(sum(c.cost_usd for c in calls), 6), "latency_ms": round(latency_ms, 1),
            "models": sorted({c.model for c in calls}), "fallback_used": any(c.fallback_used for c in calls)}


def get_or_create_conversation(s, conversation_id: str | None, channel: str, customer_email: str | None,
                               external_ref: str | None, subject: str | None) -> Conversation:
    conv = s.get(Conversation, conversation_id) if conversation_id else None
    if conv is None and external_ref:
        conv = s.query(Conversation).filter_by(channel=channel, external_ref=external_ref).first()
    if conv is None:
        conv = Conversation(channel=channel, external_ref=external_ref, subject=subject)
        if conversation_id:
            conv.id = conversation_id
        s.add(conv)
    if conv.customer_id is None and customer_email:
        # Channel identity: a signed-in web user or the From: address of an email.
        cust = s.query(Customer).filter(Customer.email.ilike(customer_email.strip())).first()
        if cust:
            conv.customer_id = cust.id
    s.flush()
    return conv


def handle_turn(text: str, *, channel: str = "web", conversation_id: str | None = None,
                customer_email: str | None = None, external_ref: str | None = None, subject: str | None = None,
                on_event=None, review_mode: str = "auto") -> TurnResult:
    t0 = time.perf_counter()
    emit = on_event or (lambda e: None)

    with SessionLocal() as s:
        conv = get_or_create_conversation(s, conversation_id, channel, customer_email, external_ref, subject)
        hist = history(conv)
        s.add(Message(conversation_id=conv.id, role="customer", content=text, status="received"))
        s.commit()   # the customer's message is saved before any AI call — nothing is lost if the AI fails
        conv_id = conv.id

    with SessionLocal() as s:
        conv = s.get(Conversation, conv_id)
        calls: list[LLMResult] = []
        steps: list[dict] = []
        escalations: list[dict] = []
        ctx = ToolContext(session=s, conversation=conv)
        try:
            # ---- already with a human: don't let the AI act, just acknowledge (once per hour is plenty)
            if conv.status == "needs_human":
                r = route(text, hist, conv.last_agent)  # still classify, for analytics
                result_agent, reply, conf = "escalation", HOLDING_REPLY, 1.0
                rt = {"label": r.label, "agent": "escalation", "source": "status",
                      "reason": "conversation already handed to a human", "triggers": r.triggers}
                if r.llm:
                    calls.append(r.llm)
                res = None
            else:
                r = route(text, hist, conv.last_agent)
                if r.llm:
                    calls.append(r.llm)
                rt = {"label": r.label, "agent": r.agent, "source": r.source, "reason": r.reason, "triggers": r.triggers}
                emit({"type": "route", **rt})

                if r.agent == "orders":
                    res = orders.run(text, hist, ctx, on_event=emit)
                elif r.agent == "escalation":
                    res = escalation.takeover(s, conv, text, hist, r.triggers)
                else:
                    from ..feedback import examples_for  # learned corrections (self-improving loop)
                    res = support.run(text, r.label, hist, examples=examples_for(s, text))
                result_agent, reply, conf = res.agent, res.reply, res.confidence
                calls += res.llm_calls
                steps += res.steps

                # ---- tickets for handoffs raised by the orders agent (refund approval, lost parcel...)
                if res.agent == "escalation":
                    escalations += res.handoffs
                for h in (res.handoffs if res.agent == "orders" else []):
                    esc = escalation.record(s, conv, h)
                    escalations.append({"escalation_id": esc.id, "severity": esc.severity,
                                        "category": esc.category, "summary": esc.summary})
                    steps.append({"type": "handoff", "why": h["reason"], "escalation_id": esc.id})

            latency = (time.perf_counter() - t0) * 1000
            usage = _usage(calls, latency)
            trace = Trace(conversation_id=conv.id, route_label=rt["label"], route_source=rt["source"],
                          route_reason=rt["reason"], agent=result_agent, steps=steps,
                          prompt_tokens=usage["prompt_tokens"], completion_tokens=usage["completion_tokens"],
                          cost_usd=usage["cost_usd"], latency_ms=latency, llm_calls=len(calls))
            s.add(trace)
            s.flush()
            msg_status = "pending_review" if review_mode == "all" or (review_mode == "smart" and conf < 0.5) else "sent"
            s.add(Message(conversation_id=conv.id, role="assistant", agent=result_agent, content=reply,
                          status=msg_status, confidence=conf, trace_id=trace.id,
                          citations=res.citations if res else None))
            conv.last_agent = result_agent if result_agent != "escalation" else conv.last_agent
            s.commit()
        except LLMUnavailable as e:
            s.rollback()
            return _fail(conv_id, f"LLM unavailable: {e}", t0, calls)
        except Exception as e:  # noqa: BLE001
            s.rollback()
            log.exception("turn failed")
            return _fail(conv_id, f"{type(e).__name__}: {e}", t0, calls)

        for e in escalations:
            notify.escalation(e["escalation_id"], e["severity"], e["category"], e["summary"], conv.id)

        result = TurnResult(conversation_id=conv.id, reply=reply, agent=result_agent, route=rt,
                            citations=res.citations if res else [], steps=steps, escalations=escalations,
                            actions=ctx.actions, status=conv.status, message_status=msg_status,
                            trace_id=trace.id, usage=usage)
        emit({"type": "final", **result.to_dict()})
        return result


def _fail(conv_id: str, error: str, t0: float, calls: list[LLMResult]) -> TurnResult:
    """Record the failure (trace + holding reply + alert). The customer message is already saved."""
    usage = _usage(calls, (time.perf_counter() - t0) * 1000)
    with SessionLocal() as s:
        trace = Trace(conversation_id=conv_id, route_label=None, route_source=None, agent=None, steps=[],
                      error=error[:2000], latency_ms=usage["latency_ms"], cost_usd=usage["cost_usd"],
                      llm_calls=len(calls))
        s.add(trace)
        s.flush()
        s.add(Message(conversation_id=conv_id, role="assistant", agent="system", content=ERROR_REPLY,
                      status="sent", trace_id=trace.id))
        s.commit()
        trace_id = trace.id
    notify.system_error("orchestrator.handle_turn", error)
    return TurnResult(conversation_id=conv_id, reply=ERROR_REPLY, agent="system", route={}, trace_id=trace_id,
                      usage=usage, error=error, message_status="sent")
