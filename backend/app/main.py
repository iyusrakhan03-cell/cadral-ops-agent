"""FastAPI server: the customer web-chat channel and the staff dashboard API.

Run locally:   cd backend && uvicorn app.main:app --reload --port 8000

Customer side (no login; the widget is channel #1):
  POST /api/chat                       one message in -> Server-Sent Events out (reply arrives word by word)
  GET  /api/chat/{conversation_id}     what the customer can see (sent replies, human replies)

Staff side (login; roles admin | agent):
  POST /api/auth/login, GET /api/auth/me
  GET  /api/live?token=...             live feed of every conversation (routing, tool calls, reply words)
  GET  /api/conversations, GET /api/conversations/{id}   (messages + full agent trace)
  POST /api/messages/{id}/feedback     approve | edit | reject an AI reply  -> self-improving loop
  POST /api/conversations/{id}/reply   reply as a human;  POST /api/conversations/{id}/status
  GET  /api/escalations, POST /api/escalations/{id}
  GET  /api/refunds, POST /api/refunds/{id}/decide        (admin: money decisions)
  GET  /api/analytics, GET /api/counts, GET /api/feedback
  GET/PUT /api/settings, GET /api/feedback/export          (admin)
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import re
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from . import analytics, live
from .auth import (current_user, current_user_query, make_token, require_admin, seed_staff, verify_password)
from .config import get_settings
from .db import Base, SessionLocal, engine, session_scope
from .models import (Conversation, Customer, Escalation, Feedback, Message, Order, OrderEvent, Refund, Setting,
                     Trace, User, new_id)

log = logging.getLogger("api")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

REVIEW_MODES = ("auto", "smart", "all")
CONV_ID_RE = re.compile(r"^[0-9a-f]{12}$")
WORD_DELAY_S = 0.025          # pace of the word-by-word reply


# ====================================================================== startup
def _warmup() -> None:
    """Load the search models and the fine-tuned router once, so the first customer isn't kept waiting."""
    try:
        from .rag.index import get_embedder, get_index, get_reranker
        get_embedder(), get_reranker(), get_index()
        if get_settings().router_model != "llm":
            from .agents import finetuned
            finetuned.available()
        log.info("warmup complete: search + router models loaded")
    except Exception as e:  # noqa: BLE001 — warmup is an optimisation, never fatal
        log.warning("warmup skipped: %s", e)


@asynccontextmanager
async def lifespan(_: FastAPI):
    live.bind_loop(asyncio.get_running_loop())
    Base.metadata.create_all(engine)       # adds the users/settings tables to an existing database
    seed_staff()
    if get_settings().warmup_models:
        threading.Thread(target=_warmup, daemon=True).start()
    from . import backup, channels
    channels.start_workers()     # email/Telegram inbound, outbox delivery + retries, daily report
    backup.start()               # free hosting: snapshot the database to a private HF dataset
    yield
    backup.stop()


app = FastAPI(title="Thistle & Pine Operations API", version="0.3.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in get_settings().cors_origins.split(",") if o.strip()],
                   allow_methods=["*"], allow_headers=["*"])


def get_setting(key: str, default: str) -> str:
    with SessionLocal() as s:
        row = s.get(Setting, key)
        return row.value if row else default


# ====================================================================== serialisers
def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat()


def message_out(m: Message) -> dict:
    return {"id": m.id, "role": m.role, "agent": m.agent, "content": m.content,
            "original_content": m.original_content, "status": m.status, "citations": m.citations or [],
            "confidence": m.confidence, "trace_id": m.trace_id, "created_at": _iso(m.created_at)}


def trace_out(t: Trace) -> dict:
    return {"id": t.id, "route_label": t.route_label, "route_source": t.route_source, "route_reason": t.route_reason,
            "agent": t.agent, "steps": t.steps or [], "prompt_tokens": t.prompt_tokens,
            "completion_tokens": t.completion_tokens, "cost_usd": t.cost_usd, "latency_ms": round(t.latency_ms or 0, 1),
            "llm_calls": t.llm_calls, "error": t.error, "created_at": _iso(t.created_at)}


def customer_out(c: Customer | None) -> dict | None:
    return {"id": c.id, "name": c.name, "email": c.email} if c else None


def conv_summary(c: Conversation, escalated: set[str]) -> dict:
    msgs = c.messages
    last = msgs[-1] if msgs else None
    return {"id": c.id, "channel": c.channel, "status": c.status, "subject": c.subject,
            "customer": customer_out(c.customer), "last_agent": c.last_agent,
            "agents": sorted({m.agent for m in msgs if m.role == "assistant" and m.agent}),
            "created_at": _iso(c.created_at), "updated_at": _iso(c.updated_at),
            "message_count": len(msgs), "pending_review": sum(1 for m in msgs if m.status == "pending_review"),
            "escalated": c.id in escalated, "ai_solved": analytics.solved_by_ai(c, escalated),
            "last_message": {"role": last.role, "content": last.content[:160], "created_at": _iso(last.created_at)}
            if last else None}


# ====================================================================== customer web chat (channel #1)
class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = None
    customer_email: str | None = Field(default=None, max_length=200)  # demo sign-in: who the web visitor is


_running: set[asyncio.Task] = set()
CUSTOMER_EVENTS = {"start", "thinking", "token", "done"}


def _for_customer(ev: dict) -> dict | None:
    """The widget only ever sees the reply itself — never routing, tool results or internal policy reasons."""
    if ev["type"] not in CUSTOMER_EVENTS:
        return None
    if ev["type"] == "done":
        held = ev.get("message_status") == "pending_review"
        return {"type": "done", "conversation_id": ev["conversation_id"], "message_id": ev.get("message_id"),
                "reply": None if held else ev.get("reply"), "held_for_review": held,
                "status": ev.get("status")}
    return ev


async def _run_turn(conv_id: str, text: str, customer_email: str | None, channel: str = "web") -> None:
    live.publish(conv_id, {"type": "customer_message", "text": text, "channel": channel,
                           "customer_email": customer_email})
    live.publish(conv_id, {"type": "thinking"})
    review_mode = get_setting("review_mode", "auto")

    def on_event(e: dict) -> None:
        if e.get("type") != "final":      # the final result is streamed word by word below instead
            live.publish(conv_id, e)

    from .agents.orchestrator import handle_turn
    try:
        res = await run_in_threadpool(handle_turn, text, channel=channel, conversation_id=conv_id,
                                      customer_email=customer_email, on_event=on_event, review_mode=review_mode)
    except Exception as e:  # noqa: BLE001 — handle_turn already saves failures; this is belt and braces
        log.exception("turn crashed")
        live.publish(conv_id, {"type": "done", "reply": None, "error": str(e), "message_status": "sent"})
        return

    with SessionLocal() as s:
        msg = s.query(Message).filter_by(trace_id=res.trace_id).first() if res.trace_id else None
    msg_id = msg.id if msg else None

    if res.message_status == "pending_review":
        live.publish(conv_id, {"type": "draft", "message_id": msg_id, "agent": res.agent, "text": res.reply})
    else:
        for chunk in re.findall(r"\S+\s*", res.reply):
            live.publish(conv_id, {"type": "token", "text": chunk, "agent": res.agent, "message_id": msg_id})
            await asyncio.sleep(WORD_DELAY_S)

    live.publish(conv_id, {"type": "done", "message_id": msg_id, "reply": res.reply, "agent": res.agent,
                           "route": res.route, "steps": res.steps, "usage": res.usage, "status": res.status,
                           "message_status": res.message_status, "trace_id": res.trace_id,
                           "escalations": res.escalations, "error": res.error})


@app.post("/api/chat")
async def chat(body: ChatIn):
    conv_id = body.conversation_id or new_id()
    if not CONV_ID_RE.match(conv_id):
        raise HTTPException(422, "bad conversation id")
    q = live.subscribe(conv_id)                      # subscribe before the turn starts, so no event is missed
    task = asyncio.create_task(_run_turn(conv_id, body.message.strip(), body.customer_email))
    _running.add(task)                                # the turn finishes even if the browser disconnects
    task.add_done_callback(_running.discard)

    async def stream():
        try:
            yield live.sse({"type": "start", "conversation_id": conv_id})
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                out = _for_customer(ev)
                if out:
                    yield live.sse(out)
                if ev["type"] == "done":
                    break
        finally:
            live.unsubscribe(conv_id, q)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/chat/{conv_id}")
def chat_history(conv_id: str):
    """What the customer sees: their messages, sent/edited AI replies and human replies. Drafts stay hidden."""
    if not CONV_ID_RE.match(conv_id):
        raise HTTPException(422, "bad conversation id")
    with SessionLocal() as s:
        c = s.get(Conversation, conv_id)
        if c is None:
            raise HTTPException(404, "conversation not found")
        visible = [m for m in c.messages if m.role == "customer" or m.status in ("sent", "edited")]
        return {"conversation_id": c.id, "status": c.status,
                "waiting_for_review": any(m.status == "pending_review" for m in c.messages),
                "messages": [{"id": m.id, "role": m.role, "content": m.content, "created_at": _iso(m.created_at)}
                             for m in visible]}


@app.get("/api/demo-customers")
def demo_customers():
    """The eight demo customers (HANDOVER.md §3), for the widget's 'chat as' picker."""
    emails = ["emma.clarke", "daniel.hughes", "olivia.bennett", "mohammed.ali", "grace.thompson", "james.patel",
              "sophie.walker", "liam.oconnor"]
    with SessionLocal() as s:
        rows = s.query(Customer).filter(Customer.email.in_([f"{e}@example.com" for e in emails])).all()
        by_email = {c.email: c for c in rows}
        return [{"name": by_email[f"{e}@example.com"].name, "email": f"{e}@example.com"}
                for e in emails if f"{e}@example.com" in by_email]


# ====================================================================== staff auth
class LoginIn(BaseModel):
    email: str
    password: str


def user_out(u: User) -> dict:
    return {"id": u.id, "email": u.email, "name": u.name, "role": u.role}


@app.post("/api/auth/login")
def login(body: LoginIn):
    with SessionLocal() as s:
        u = s.query(User).filter(User.email == body.email.strip().lower()).first()
    if u is None or not u.active or not verify_password(body.password, u.password_hash):
        raise HTTPException(401, "Wrong email or password")
    return {"token": make_token(u), "user": user_out(u)}


@app.get("/api/auth/me")
def me(user: User = Depends(current_user)):
    return user_out(user)


# ====================================================================== live feed for staff
@app.get("/api/live")
async def live_feed(user: User = Depends(current_user_query)):
    q = live.subscribe(live.ALL)

    async def stream():
        try:
            yield live.sse({"type": "hello", "conversation_id": "", "user": user.email})
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                yield live.sse(ev)
        finally:
            live.unsubscribe(live.ALL, q)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ====================================================================== conversations + trace
@app.get("/api/conversations")
def conversations(status: str | None = None, q: str | None = None, limit: int = Query(100, le=500),
                  _: User = Depends(current_user)):
    with SessionLocal() as s:
        query = s.query(Conversation)
        if status == "pending_review":
            query = query.filter(Conversation.messages.any(Message.status == "pending_review"))
        elif status:
            query = query.filter(Conversation.status == status)
        rows = query.order_by(Conversation.updated_at.desc()).limit(limit).all()
        escalated = {e.conversation_id for e in s.query(Escalation.conversation_id).all()}
        out = [conv_summary(c, escalated) for c in rows]
    if q:
        needle = q.lower()
        out = [c for c in out if needle in json.dumps(c).lower()]
    return out


@app.get("/api/conversations/{conv_id}")
def conversation(conv_id: str, _: User = Depends(current_user)):
    with SessionLocal() as s:
        c = s.get(Conversation, conv_id)
        if c is None:
            raise HTTPException(404, "conversation not found")
        escalated = {e.conversation_id for e in s.query(Escalation.conversation_id).all()}
        traces = s.query(Trace).filter_by(conversation_id=conv_id).all()
        feedback = s.query(Feedback).filter_by(conversation_id=conv_id).all()
        escs = s.query(Escalation).filter_by(conversation_id=conv_id).all()
        return {**conv_summary(c, escalated),
                "messages": [message_out(m) for m in c.messages],
                "traces": {t.id: trace_out(t) for t in traces},
                "feedback": {f.message_id: {"action": f.action, "reason": f.reason, "by": f.created_by,
                                            "final_reply": f.final_reply, "times_used": f.times_used}
                             for f in feedback},
                "escalations": [escalation_out(e, None) for e in escs]}


class FeedbackIn(BaseModel):
    action: Literal["approve", "edit", "reject"]
    final_reply: str | None = Field(default=None, max_length=4000)   # edit: the corrected reply; reject: optional
    reason: str | None = Field(default=None, max_length=1000)
    corrected_label: str | None = None                                 # "the router should have said ..."


@app.post("/api/messages/{message_id}/feedback")
def message_feedback(message_id: int, body: FeedbackIn, user: User = Depends(current_user)):
    final = (body.final_reply or "").strip() or None
    if body.action == "edit" and not final:
        raise HTTPException(422, "An edit needs the corrected reply")
    with session_scope() as s:
        m = s.get(Message, message_id)
        if m is None or m.role != "assistant":
            raise HTTPException(404, "AI reply not found")
        conv = s.get(Conversation, m.conversation_id)
        question = next((x.content for x in reversed(conv.messages) if x.role == "customer" and x.id < m.id), "")
        trace = s.get(Trace, m.trace_id) if m.trace_id else None
        ai_reply = m.original_content or m.content

        if body.action == "approve":
            if m.status == "pending_review":
                m.status = "sent"
        elif body.action == "edit":
            m.original_content = ai_reply
            m.content = final
            m.status = "edited"
        else:  # reject: the draft is withdrawn; if the human wrote the right answer, that goes to the customer
            m.status = "rejected"
            if final:
                s.add(Message(conversation_id=conv.id, role="human", content=final, status="sent"))

        fb = s.query(Feedback).filter_by(message_id=m.id).first() or Feedback(message_id=m.id, conversation_id=conv.id)
        fb.agent, fb.route_label = m.agent, trace.route_label if trace else None
        fb.corrected_label, fb.question, fb.ai_reply = body.corrected_label, question, ai_reply
        fb.final_reply = final if body.action != "approve" else None
        fb.action, fb.reason, fb.created_by = body.action, body.reason, f"human:{user.email}"
        fb.created_at = datetime.now(timezone.utc)
        s.add(fb)
        conv.updated_at = datetime.now(timezone.utc)
        s.flush()
        out = message_out(m)
        conv_id = conv.id
    live.publish(conv_id, {"type": "feedback", "message_id": message_id, "action": body.action, "by": user.email})
    return out


class ReplyIn(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


@app.post("/api/conversations/{conv_id}/reply")
def human_reply(conv_id: str, body: ReplyIn, user: User = Depends(current_user)):
    with session_scope() as s:
        c = s.get(Conversation, conv_id)
        if c is None:
            raise HTTPException(404, "conversation not found")
        m = Message(conversation_id=c.id, role="human", agent=None, content=body.content.strip(), status="sent")
        s.add(m)
        c.updated_at = datetime.now(timezone.utc)
        s.flush()
        out = message_out(m)
    live.publish(conv_id, {"type": "human_reply", "message_id": out["id"], "text": out["content"], "by": user.email})
    return out


class StatusIn(BaseModel):
    status: Literal["open", "needs_human", "resolved"]


@app.post("/api/conversations/{conv_id}/status")
def set_conversation_status(conv_id: str, body: StatusIn, user: User = Depends(current_user)):
    with session_scope() as s:
        c = s.get(Conversation, conv_id)
        if c is None:
            raise HTTPException(404, "conversation not found")
        c.status = body.status
        c.updated_at = datetime.now(timezone.utc)
    live.publish(conv_id, {"type": "status", "status": body.status, "by": user.email})
    return {"id": conv_id, "status": body.status}


# ====================================================================== escalations
def escalation_out(e: Escalation, customer: Customer | None) -> dict:
    return {"id": e.id, "conversation_id": e.conversation_id, "severity": e.severity, "category": e.category,
            "summary": e.summary, "customer_wants": e.customer_wants, "suggested_action": e.suggested_action,
            "status": e.status, "created_at": _iso(e.created_at), "customer": customer_out(customer)}


SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


@app.get("/api/escalations")
def escalations(status: str | None = "open", _: User = Depends(current_user)):
    with SessionLocal() as s:
        query = s.query(Escalation)
        if status:
            query = query.filter(Escalation.status == status)
        rows = query.all()
        out = [escalation_out(e, s.get(Customer, e.customer_id) if e.customer_id else None) for e in rows]
    return sorted(out, key=lambda e: (SEVERITY_ORDER.get(e["severity"], 9), e["created_at"] or ""))


class EscalationIn(BaseModel):
    status: Literal["open", "resolved"]


@app.post("/api/escalations/{esc_id}")
def update_escalation(esc_id: int, body: EscalationIn, user: User = Depends(current_user)):
    with session_scope() as s:
        e = s.get(Escalation, esc_id)
        if e is None:
            raise HTTPException(404, "escalation not found")
        e.status = body.status
        conv_id = e.conversation_id
    live.publish(conv_id, {"type": "escalation_update", "escalation_id": esc_id, "status": body.status,
                           "by": user.email})
    return {"id": esc_id, "status": body.status}


# ====================================================================== refunds waiting for a human (admin)
def refund_out(r: Refund) -> dict:
    o = r.order
    return {"id": r.id, "order_id": r.order_id, "amount": float(r.amount), "reason": r.reason, "status": r.status,
            "created_by": r.created_by, "decided_by": r.decided_by, "created_at": _iso(r.created_at),
            "order_total": float(o.total), "order_status": o.status, "customer": customer_out(o.customer),
            "items": [{"name": i.name, "qty": i.qty, "unit_price": float(i.unit_price)} for i in o.items]}


@app.get("/api/refunds")
def refunds(status: str | None = "pending_approval", _: User = Depends(current_user)):
    with SessionLocal() as s:
        query = s.query(Refund)
        if status:
            query = query.filter(Refund.status == status)
        return [refund_out(r) for r in query.order_by(Refund.created_at.desc()).limit(200).all()]


class RefundDecisionIn(BaseModel):
    decision: Literal["approve", "decline"]
    note: str | None = Field(default=None, max_length=1000)


@app.post("/api/refunds/{refund_id}/decide")
def decide_refund(refund_id: int, body: RefundDecisionIn, user: User = Depends(require_admin)):
    """A human makes the money decision the policy engine passed up. The customer is told in their conversation."""
    with session_scope() as s:
        r = s.get(Refund, refund_id)
        if r is None:
            raise HTTPException(404, "refund not found")
        if r.status != "pending_approval":
            raise HTTPException(409, f"Refund is already {r.status}")
        order: Order = r.order
        actor = f"human:{user.email}"
        r.decided_by = actor
        if body.decision == "approve":
            r.status = "issued"
            detail = f"Refund £{float(r.amount):.2f} approved by {user.email}"
            customer_msg = (f"Good news: your refund of £{float(r.amount):.2f} for order {order.id} has been approved. "
                            "It will reach your original payment method within 5 working days.")
        else:
            r.status = "rejected"
            detail = f"Refund £{float(r.amount):.2f} declined by {user.email}"
            customer_msg = (f"We've reviewed your refund request for order {order.id}. We're unable to approve it this "
                            "time; a member of our team will be in touch to explain the options.")
        if body.note:
            detail += f": {body.note}"
        s.add(OrderEvent(order_id=order.id, kind="refund_issued" if r.status == "issued" else "refund_rejected",
                         detail=detail, actor=actor))
        # Close the matching approval ticket and tell the customer in the conversation it came from.
        conv_id = None
        for e in s.query(Escalation).filter(Escalation.category == "refund_approval", Escalation.status == "open").all():
            if order.id in e.summary:
                e.status = "resolved"
                conv_id = conv_id or e.conversation_id
        if conv_id:
            s.add(Message(conversation_id=conv_id, role="human", content=customer_msg, status="sent"))
        s.flush()
        out = refund_out(r)
    if conv_id:
        live.publish(conv_id, {"type": "refund_decision", "refund_id": refund_id, "decision": body.decision,
                               "by": user.email})
    return out


# ====================================================================== analytics, counts, learning loop
@app.get("/api/analytics")
def get_analytics(days: int = Query(14, ge=1, le=90), _: User = Depends(current_user)):
    with SessionLocal() as s:
        return analytics.compute(s, days)


@app.get("/api/counts")
def counts(_: User = Depends(current_user)):
    """Badges for the sidebar."""
    with SessionLocal() as s:
        return {"pending_review": s.query(Message).filter_by(status="pending_review").count(),
                "needs_human": s.query(Conversation).filter_by(status="needs_human").count(),
                "open_escalations": s.query(Escalation).filter_by(status="open").count(),
                "pending_refunds": s.query(Refund).filter_by(status="pending_approval").count()}


@app.get("/api/feedback")
def feedback_list(_: User = Depends(current_user)):
    """What the AI has learned: every human judgement, with how often each correction has been reused."""
    with SessionLocal() as s:
        rows = s.query(Feedback).order_by(Feedback.created_at.desc()).limit(200).all()
        return [{"id": f.id, "message_id": f.message_id, "conversation_id": f.conversation_id, "agent": f.agent,
                 "action": f.action, "question": f.question, "ai_reply": f.ai_reply, "final_reply": f.final_reply,
                 "reason": f.reason, "by": f.created_by, "times_used": f.times_used,
                 "used_as_example": f.agent == "support" and bool(f.final_reply) and f.action in ("edit", "reject"),
                 "created_at": _iso(f.created_at)} for f in rows]


@app.get("/api/feedback/export", response_class=PlainTextResponse)
def feedback_export(_: User = Depends(require_admin)):
    from .feedback import export_dataset
    with SessionLocal() as s:
        rows = export_dataset(s)
    return PlainTextResponse("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                             media_type="application/x-ndjson",
                             headers={"Content-Disposition": "attachment; filename=feedback-dataset.jsonl"})


# ====================================================================== settings (admin)
class SettingsIn(BaseModel):
    review_mode: Literal["auto", "smart", "all"]


@app.get("/api/settings")
def get_settings_api(_: User = Depends(current_user)):
    return {"review_mode": get_setting("review_mode", "auto"),
            "review_modes": {"auto": "AI replies go straight to the customer",
                             "smart": "Low-confidence AI replies wait for a human to approve them",
                             "all": "Every AI reply waits for a human to approve it"}}


@app.put("/api/settings")
def put_settings(body: SettingsIn, _: User = Depends(require_admin)):
    with session_scope() as s:
        row = s.get(Setting, "review_mode") or Setting(key="review_mode", value="auto")
        row.value = body.review_mode
        s.add(row)
    return {"review_mode": body.review_mode}


@app.get("/api/health")
def health():
    from .llm import model_status
    with SessionLocal() as s:
        s.query(Conversation.id).limit(1).all()
    from .channels import status as channel_status
    return {"ok": True, "models_cooling_down": model_status(), "groq_key_set": bool(get_settings().groq_api_key),
            "channels": channel_status()}


# ====================================================================== channels: email + Telegram (Day 4)
def bridge_auth(x_bridge_key: str = Header(default="")) -> None:
    """Our own bridges (Gmail Apps Script, Vercel Telegram relay) prove themselves with a shared secret."""
    key = get_settings().bridge_key
    if not key:
        raise HTTPException(503, "channels are not configured (BRIDGE_KEY)")
    if not hmac.compare_digest(x_bridge_key.encode(), key.encode()):
        raise HTTPException(401, "bad bridge key")


class EmailIn(BaseModel):
    message_id: str = Field(min_length=1, max_length=200)
    thread_id: str = Field(min_length=1, max_length=200)
    from_: str = Field(alias="from", min_length=3, max_length=200)
    from_name: str | None = Field(default=None, max_length=200)
    subject: str | None = Field(default=None, max_length=500)
    body: str = Field(default="", max_length=50_000)


@app.post("/api/channels/email/inbound", dependencies=[Depends(bridge_auth)])
def email_inbound(body: EmailIn):
    from .channels import accept_inbound
    payload = body.model_dump(by_alias=True)
    payload["from"] = payload["from"].strip().lower()
    ev_id, new = accept_inbound("email", body.message_id, payload)
    return {"accepted": True, "event_id": ev_id, "duplicate": not new}


@app.get("/api/channels/email/outbox", dependencies=[Depends(bridge_auth)])
def email_outbox(limit: int = Query(10, ge=1, le=50)):
    from .channels import claim_emails
    return {"emails": claim_emails(limit)}


class AckIn(BaseModel):
    ok: bool
    error: str | None = Field(default=None, max_length=2000)


@app.post("/api/channels/email/outbox/{outbox_id}/ack", dependencies=[Depends(bridge_auth)])
def email_ack(outbox_id: int, body: AckIn):
    from .channels import ack_email
    if not ack_email(outbox_id, body.ok, body.error):
        raise HTTPException(404, "outbox item not found")
    return {"ok": True}


class TelegramIn(BaseModel):
    update_id: int
    chat_id: int
    text: str = Field(min_length=1, max_length=4096)
    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None


@app.post("/api/channels/telegram/inbound", dependencies=[Depends(bridge_auth)])
def telegram_inbound(body: TelegramIn):
    from .channels import accept_inbound
    ev_id, new = accept_inbound("telegram", str(body.update_id), body.model_dump())
    return {"accepted": True, "event_id": ev_id, "duplicate": not new}


@app.get("/api/channels/ping", dependencies=[Depends(bridge_auth)])
def bridge_ping():
    """The Gmail bridge calls this every minute: proves the key works and keeps the free Space awake."""
    return {"ok": True}


@app.get("/api/channels/status")
def channels_status(_: User = Depends(current_user)):
    from .channels import status as channel_status
    from .models import Outbox
    with SessionLocal() as s:
        recent = s.query(Outbox).order_by(Outbox.id.desc()).limit(25).all()
        items = [{"id": o.id, "channel": o.channel, "kind": o.kind, "status": o.status, "attempts": o.attempts,
                  "target": o.target if o.channel != "slack" else "slack", "last_error": o.last_error,
                  "conversation_id": o.conversation_id, "created_at": _iso(o.created_at), "sent_at": _iso(o.sent_at)}
                 for o in recent]
    return {**channel_status(), "recent": items}


# ====================================================================== daily management report
def _parse_day(day: str | None):
    if not day:
        return None
    try:
        return datetime.strptime(day, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(422, "date must be YYYY-MM-DD")


@app.get("/api/reports/daily")
def daily_report(date: str | None = None, _: User = Depends(current_user)):
    from . import report
    d = report.build(_parse_day(date))
    return {"data": d, "html": report.to_html(d), "text": report.to_text(d),
            "manager_email_set": bool(get_settings().manager_email)}


class ReportSendIn(BaseModel):
    date: str | None = None
    to: str | None = Field(default=None, max_length=200)


@app.post("/api/reports/daily/send")
def send_daily_report(body: ReportSendIn, _: User = Depends(require_admin)):
    from . import report
    if not (body.to or get_settings().manager_email or get_settings().slack_webhook_url):
        raise HTTPException(409, "Set MANAGER_EMAIL or SLACK_WEBHOOK_URL on the server first")
    return report.send(_parse_day(body.date), to=body.to)
