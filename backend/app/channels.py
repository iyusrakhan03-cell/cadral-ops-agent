"""Outside channels: email and Telegram in, replies / Slack alerts / the daily report out — and nothing lost.

How a message travels
---------------------
  Gmail  --(Apps Script bridge, every minute)-->  POST /api/channels/email/inbound     ┐
  Telegram --(webhook on the Vercel dashboard)--> POST /api/channels/telegram/inbound  ┘ -> InboundEvent row (saved first)
        -> inbound worker -> orchestrator.handle_turn (same agents as web chat) -> reply Message
        -> outbox sync: every customer-visible reply on email/Telegram gets an Outbox row
        -> email:    the Apps Script pulls GET /api/channels/email/outbox, sends from Gmail, then acks
           telegram: delivery worker -> Vercel relay -> api.telegram.org
           slack:    delivery worker -> Slack incoming webhook

Why the bridges? Free Hugging Face Spaces only allow outbound web traffic (ports 80/443) and block
api.telegram.org, so the server can't speak SMTP/IMAP or Telegram directly. Each bridge is ~100 lines and free.

Reliability rules
-----------------
- Inbound is idempotent: (channel, external message id) is unique, so a bridge re-sending is harmless.
- Inbound is saved before processing; anything not finished (crash, restart) is processed again.
- Outbound rows stay "pending" until the channel confirms; failures retry with back-off
  (10 s → 1 h, 8 attempts), then the row is marked failed and staff get a Slack alert.
- Human-approved, edited and human-written replies flow out the same way, so the dashboard's
  approve / edit / reply buttons work for every channel.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from . import live
from .config import get_settings
from .db import SessionLocal
from .models import Conversation, Customer, InboundEvent, Message, Outbox, Setting, new_id

log = logging.getLogger("channels")

CHANNELS = ("email", "telegram")
MAX_ATTEMPTS = 8
BACKOFF_S = [10, 30, 60, 120, 300, 600, 1800, 3600]
EMAIL_LEASE_S = 300          # an email handed to the bridge isn't handed out again for 5 min unless acked
CONVERSATION_IDLE = timedelta(hours=24)   # Telegram: a new conversation after a day of silence

_wake_inbound = threading.Event()
_wake_outbox = threading.Event()
_started = False


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utc(dt: datetime | None) -> datetime | None:
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _backoff(attempts: int) -> timedelta:
    return timedelta(seconds=BACKOFF_S[min(attempts, len(BACKOFF_S)) - 1])


def wake() -> None:
    _wake_inbound.set()
    _wake_outbox.set()


# ====================================================================== inbound
def accept_inbound(channel: str, external_id: str, payload: dict) -> tuple[int, bool]:
    """Save an incoming message. Returns (event id, is_new). A duplicate (bridge retry) is not processed twice."""
    key = f"{channel}:{external_id}"
    with SessionLocal() as s:
        existing = s.scalar(select(InboundEvent).where(InboundEvent.external_id == key))
        if existing:
            return existing.id, False
        ev = InboundEvent(channel=channel, external_id=key, payload=payload)
        s.add(ev)
        try:
            s.commit()
        except IntegrityError:            # two deliveries raced — the other one won, that's fine
            s.rollback()
            ev = s.scalar(select(InboundEvent).where(InboundEvent.external_id == key))
            return ev.id, False
        ev_id = ev.id
    _wake_inbound.set()
    return ev_id, True


_QUOTE_START = re.compile(r"^(On .{0,200}wrote:|-----\s*Original Message|From: .+|Sent from my \w+)", re.I | re.M)


def clean_email_body(body: str) -> str:
    """Keep only what the customer just wrote: drop quoted history, '>' lines and signatures."""
    body = (body or "").replace("\r\n", "\n")
    m = _QUOTE_START.search(body)
    if m and m.start() > 0:
        body = body[:m.start()]
    lines = []
    for line in body.split("\n"):
        if line.strip() == "--" or line.startswith("-- "):
            break
        if line.lstrip().startswith(">"):
            continue
        lines.append(line)
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return text[:4000]


def _conversation_for(s, channel: str, ref: str) -> str:
    """Reuse the customer's current conversation on this channel, or start a new one."""
    conv = s.scalars(select(Conversation).where(Conversation.channel == channel, Conversation.external_ref == ref)
                     .order_by(Conversation.updated_at.desc()).limit(1)).first()
    if conv is None:
        return new_id()
    if channel == "telegram" and (conv.status == "resolved" or utcnow() - _utc(conv.updated_at) > CONVERSATION_IDLE):
        return new_id()
    return conv.id


def _turn_args(s, ev: InboundEvent) -> dict:
    p = ev.payload
    if ev.channel == "email":
        ref = f"thread:{p['thread_id']}"
        subject = (p.get("subject") or "").strip() or "(no subject)"
        text = clean_email_body(p.get("body", ""))
        if not text:
            text = subject
        elif re.sub(r"^(re|fwd?):\s*", "", subject, flags=re.I).lower() not in text.lower():
            text = f"[Email subject: {subject}]\n{text}"
        return {"text": text, "channel": "email", "external_ref": ref, "subject": subject[:300],
                "customer_email": (p.get("from") or "").strip().lower() or None,
                "conversation_id": _conversation_for(s, "email", ref)}
    # telegram
    chat_id = str(p["chat_id"])
    text = (p.get("text") or "").strip()
    if text.startswith("/start"):
        text = "Hello!"
    linked = s.scalar(select(Customer).where(Customer.telegram_chat_id == chat_id))
    name = " ".join(x for x in (p.get("first_name"), p.get("last_name")) if x)
    return {"text": text[:4000], "channel": "telegram", "external_ref": chat_id,
            "subject": f"Telegram chat with {name or p.get('username') or chat_id}",
            "customer_email": linked.email if linked else None,
            "conversation_id": _conversation_for(s, "telegram", chat_id)}


def _already_answered(s, conv_id: str, text: str, since: datetime) -> bool:
    """After a crash mid-turn the event is retried; don't answer the same message twice."""
    msgs = s.scalars(select(Message).where(Message.conversation_id == conv_id).order_by(Message.id)).all()
    for i, m in enumerate(msgs):
        if m.role == "customer" and m.content == text and _utc(m.created_at) >= since - timedelta(seconds=5):
            return any(x.role == "assistant" for x in msgs[i + 1:])
    return False


def process_event(ev_id: int) -> None:
    from .agents.orchestrator import handle_turn
    with SessionLocal() as s:
        ev = s.get(InboundEvent, ev_id)
        if ev is None or ev.status != "received":
            return
        args = _turn_args(s, ev)
        ev.attempts += 1
        ev.conversation_id = args["conversation_id"]
        s.commit()
        if ev.attempts > 1 and _already_answered(s, args["conversation_id"], args["text"], _utc(ev.created_at)):
            ev.status, ev.processed_at = "done", utcnow()
            s.commit()
            return
        review_mode = (s.get(Setting, "review_mode").value if s.get(Setting, "review_mode") else "auto")

    conv_id = args.pop("conversation_id")
    text = args.pop("text")
    live.publish(conv_id, {"type": "customer_message", "text": text, "channel": args["channel"],
                           "customer_email": args.get("customer_email")})
    live.publish(conv_id, {"type": "thinking"})

    def on_event(e: dict) -> None:
        if e.get("type") != "final":
            live.publish(conv_id, e)

    try:
        res = handle_turn(text, conversation_id=conv_id, on_event=on_event, review_mode=review_mode, **args)
    except Exception as e:  # noqa: BLE001 — retry later; handle_turn normally catches its own failures
        log.exception("inbound event %s failed", ev_id)
        with SessionLocal() as s:
            ev = s.get(InboundEvent, ev_id)
            ev.error = f"{type(e).__name__}: {e}"[:2000]
            if ev.attempts >= 5:
                ev.status = "failed"
                from . import notify
                notify.system_error("inbound", f"{ev.channel} message {ev.external_id} failed 5 times: {ev.error}")
            s.commit()
        return

    with SessionLocal() as s:
        ev = s.get(InboundEvent, ev_id)
        ev.status, ev.processed_at, ev.error = "done", utcnow(), res.error
        if args["channel"] == "telegram":   # the customer verified in this chat → remember the chat for next time
            conv = s.get(Conversation, res.conversation_id)
            if conv and conv.customer_id:
                cust = s.get(Customer, conv.customer_id)
                if cust and cust.telegram_chat_id != args["external_ref"]:
                    cust.telegram_chat_id = args["external_ref"]
        s.commit()
    with SessionLocal() as s:
        msg = s.scalar(select(Message).where(Message.trace_id == res.trace_id)) if res.trace_id else None
    live.publish(res.conversation_id, {"type": "done", "message_id": msg.id if msg else None, "reply": res.reply,
                                       "agent": res.agent, "route": res.route, "steps": res.steps,
                                       "usage": res.usage, "status": res.status,
                                       "message_status": res.message_status, "trace_id": res.trace_id,
                                       "escalations": res.escalations, "error": res.error,
                                       "channel": args["channel"]})
    _wake_outbox.set()


def _inbound_loop() -> None:
    while True:
        ids: list[int] = []
        try:
            with SessionLocal() as s:
                ids = s.scalars(select(InboundEvent.id).where(InboundEvent.status == "received")
                                .order_by(InboundEvent.id).limit(20)).all()
            for ev_id in ids:          # one at a time: keeps us inside Groq's per-minute token limit
                process_event(ev_id)
        except Exception:  # noqa: BLE001 — the loop must never die
            log.exception("inbound loop")
        _wake_inbound.wait(timeout=30 if not ids else 1)
        _wake_inbound.clear()


# ====================================================================== outbox
def _with_sources(m: Message) -> str:
    """Email/Telegram can't show the dashboard's clickable [n] markers, so list the sources under the reply."""
    cites = [c for c in (m.citations or []) if f"[{c.get('n')}]" in m.content]
    if m.role != "assistant" or m.status == "edited" or not cites:
        return m.content
    lines = [f"[{c['n']}] {c.get('title', '')}" + (f" ({c['location']})" if c.get("location") else "") for c in cites]
    return m.content + "\n\nSources:\n" + "\n".join(lines)


def _email_payload(s, conv: Conversation, m: Message) -> tuple[str | None, dict]:
    last_in = s.scalars(select(InboundEvent).where(InboundEvent.conversation_id == conv.id,
                                                   InboundEvent.channel == "email")
                        .order_by(InboundEvent.id.desc()).limit(1)).first()
    p = last_in.payload if last_in else {}
    subject = conv.subject or "Your message to Thistle & Pine"
    return p.get("from"), {"reply_to_message_id": p.get("message_id"), "thread_id": p.get("thread_id"),
                           "to": p.get("from"), "subject": subject if subject.lower().startswith("re:")
                           else f"Re: {subject}", "text": _with_sources(m)}


def sync_outbox() -> int:
    """Give every customer-visible email/Telegram reply an Outbox row (catches AI replies, approvals, edits,
    human replies and refund decisions alike — one place, so no path can forget to deliver)."""
    created = 0
    with SessionLocal() as s:
        rows = s.execute(
            select(Message, Conversation)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .outerjoin(Outbox, Outbox.message_id == Message.id)
            .where(Conversation.channel.in_(CHANNELS), Message.role.in_(("assistant", "human")),
                   Message.status.in_(("sent", "edited")), Outbox.id.is_(None))
            .order_by(Message.id)).all()
        for m, conv in rows:
            if conv.channel == "email":
                target, payload = _email_payload(s, conv, m)
            else:
                target, payload = conv.external_ref, {"chat_id": conv.external_ref, "text": _with_sources(m)}
            s.add(Outbox(channel=conv.channel, kind="reply", message_id=m.id, conversation_id=conv.id,
                         target=target, payload=payload))
            created += 1
        if created:
            s.commit()
    return created


def enqueue(channel: str, kind: str, payload: dict, target: str | None = None) -> None:
    with SessionLocal() as s:
        s.add(Outbox(channel=channel, kind=kind, payload=payload, target=target))
        s.commit()
    _wake_outbox.set()


def _mark(s, row: Outbox, ok: bool, error: str | None = None) -> None:
    if ok:
        row.status, row.sent_at, row.last_error = "sent", utcnow(), None
        if row.conversation_id:
            live.publish(row.conversation_id, {"type": "delivered", "channel": row.channel, "message_id": row.message_id})
        return
    row.last_error = (error or "unknown error")[:2000]
    if row.attempts >= MAX_ATTEMPTS:
        row.status = "failed"
        if row.channel != "slack":     # don't alert about the alert channel failing (it would loop)
            from . import notify
            notify.system_error("delivery", f"{row.channel} {row.kind} #{row.id} gave up after "
                                            f"{row.attempts} attempts: {row.last_error}")
    else:
        row.next_attempt_at = utcnow() + _backoff(row.attempts)


def _send_telegram(payload: dict) -> None:
    st = get_settings()
    if not (st.relay_url and st.bridge_key):
        raise RuntimeError("RELAY_URL / BRIDGE_KEY not set")
    text = payload["text"]
    for i in range(0, len(text), 4000):      # Telegram's limit is 4096 characters per message
        r = httpx.post(f"{st.relay_url.rstrip('/')}/api/telegram/send", timeout=20,
                       headers={"X-Bridge-Key": st.bridge_key},
                       json={"chat_id": payload["chat_id"], "text": text[i:i + 4000]})
        if r.status_code >= 300:
            raise RuntimeError(f"relay {r.status_code}: {r.text[:300]}")


def _send_slack(payload: dict) -> None:
    url = get_settings().slack_webhook_url
    if not url:
        raise RuntimeError("SLACK_WEBHOOK_URL not set")
    r = httpx.post(url, json=payload, timeout=15)
    if r.status_code >= 300:
        raise RuntimeError(f"slack {r.status_code}: {r.text[:300]}")


SENDERS = {"telegram": _send_telegram, "slack": _send_slack}


def deliver_due() -> int:
    """Push-style channels (Telegram, Slack). Email is pulled by the Gmail bridge instead."""
    n = 0
    with SessionLocal() as s:
        rows = s.scalars(select(Outbox).where(Outbox.status == "pending", Outbox.channel.in_(tuple(SENDERS)),
                                              Outbox.next_attempt_at <= utcnow())
                         .order_by(Outbox.id).limit(20)).all()
        for row in rows:
            row.attempts += 1
            try:
                SENDERS[row.channel](row.payload)
                _mark(s, row, True)
                n += 1
            except Exception as e:  # noqa: BLE001
                log.warning("deliver %s #%s failed: %s", row.channel, row.id, e)
                _mark(s, row, False, f"{type(e).__name__}: {e}")
            s.commit()
    return n


def claim_emails(limit: int = 10) -> list[dict]:
    """Hand the Gmail bridge the emails it should send now. Each is leased for 5 min until acked."""
    sync_outbox()
    out = []
    with SessionLocal() as s:
        rows = s.scalars(select(Outbox).where(Outbox.status == "pending", Outbox.channel == "email",
                                              Outbox.next_attempt_at <= utcnow())
                         .order_by(Outbox.id).limit(limit)).all()
        for row in rows:
            row.attempts += 1
            row.next_attempt_at = utcnow() + timedelta(seconds=EMAIL_LEASE_S)
            out.append({"id": row.id, "kind": row.kind, **row.payload})
        s.commit()
    return out


def ack_email(outbox_id: int, ok: bool, error: str | None = None) -> bool:
    with SessionLocal() as s:
        row = s.get(Outbox, outbox_id)
        if row is None or row.channel != "email":
            return False
        if row.status == "pending":
            _mark(s, row, ok, error)
            s.commit()
        return True


def _outbox_loop() -> None:
    from . import report
    while True:
        try:
            sync_outbox()
            deliver_due()
            report.maybe_send_daily()
        except Exception:  # noqa: BLE001
            log.exception("outbox loop")
        _wake_outbox.wait(timeout=5)
        _wake_outbox.clear()


def status() -> dict:
    """For /api/health and the dashboard: is anything stuck?"""
    from . import gmail
    with SessionLocal() as s:
        def count(model, **kw):
            q = select(model)
            for k, v in kw.items():
                q = q.where(getattr(model, k) == v)
            return len(s.scalars(q).all())
        st = get_settings()
        return {"inbound_waiting": count(InboundEvent, status="received"),
                "inbound_failed": count(InboundEvent, status="failed"),
                "outbox_pending": count(Outbox, status="pending"),
                "outbox_failed": count(Outbox, status="failed"),
                "email_bridge": bool(st.bridge_key) or gmail.configured(),
                "email_mode": "gmail_api" if gmail.configured() else ("apps_script" if st.bridge_key else None),
                "gmail": gmail.status(), "telegram_relay": bool(st.relay_url and st.bridge_key),
                "slack": bool(st.slack_webhook_url), "daily_report_to": bool(st.manager_email)}


def start_workers() -> None:
    global _started
    if _started or not get_settings().workers:
        return
    _started = True
    threading.Thread(target=_inbound_loop, name="inbound-worker", daemon=True).start()
    threading.Thread(target=_outbox_loop, name="outbox-worker", daemon=True).start()
    log.info("channel workers started")
