"""Shared memory: every agent sees the same conversation history and customer profile.

- Short-term memory: the last N messages of this conversation (from the messages table).
- Long-term memory: the customer's profile — orders, refunds, loyalty, and `notes` that agents
  write back (e.g. an escalation summary), so the next conversation starts with that context.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ..models import Conversation, Customer

HISTORY_TURNS = 12


def history(conv: Conversation, limit: int = HISTORY_TURNS) -> list[dict]:
    """Chat history in LLM format. Rejected drafts are excluded; human replies count as assistant turns."""
    msgs = [m for m in conv.messages if m.status != "rejected" and m.status != "pending_review"][-limit:]
    return [{"role": "user" if m.role == "customer" else "assistant", "content": m.content} for m in msgs]


def customer_profile(session: Session, customer_id: int | None) -> str:
    if customer_id is None:
        return "Customer: NOT VERIFIED yet (unknown identity)."
    c = session.get(Customer, customer_id)
    now = datetime.now(timezone.utc)
    orders = sorted(c.orders, key=lambda o: o.created_at, reverse=True)
    recent_refunds = sum(1 for o in orders for r in o.refunds if r.status == "issued"
                         and (r.created_at if r.created_at.tzinfo else r.created_at.replace(tzinfo=timezone.utc))
                         >= now - timedelta(days=90))
    lines = [f"Customer: {c.name} <{c.email}> (verified), city {c.city}, customer since {c.created_at.date()}",
             f"Pine Points: {c.pine_points}. Orders: {len(orders)}. Refunds in last 90 days: {recent_refunds}."]
    for o in orders[:6]:
        items = ", ".join(f"{it.qty}× {it.name}" for it in o.items)
        lines.append(f"- {o.id} · {o.status} · placed {o.created_at.date()} · £{float(o.total):.2f} · {items}")
    if c.notes:
        lines.append(f"Notes from previous conversations: {c.notes[-800:]}")
    return "\n".join(lines)


def add_note(session: Session, customer_id: int | None, note: str):
    if customer_id is None:
        return
    c = session.get(Customer, customer_id)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    c.notes = ((c.notes + "\n") if c.notes else "") + f"[{stamp}] {note}"
