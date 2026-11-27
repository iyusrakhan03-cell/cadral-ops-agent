"""Orders-agent tools: real function calls that read and write the shop database.

Safety rules enforced here (not in the prompt):
- Ownership: a conversation can only see/change orders belonging to its verified customer.
- Money: refund decisions and amounts come from policy.evaluate_refund, never from the model.
- State: cancel / address change only before dispatch.
- Audit: every write adds an OrderEvent with the actor, so humans can see what the AI did.
Every tool takes a `why` argument: the model must state its reason, which is shown in the agent trace.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models import Conversation, Customer, Order, OrderEvent, Product, Refund
from . import policy

ACTOR = "ai:orders_agent"


@dataclass
class ToolContext:
    session: Session
    conversation: Conversation
    handoffs: list[dict] = field(default_factory=list)   # requests for the Escalation agent
    actions: list[str] = field(default_factory=list)     # human-readable list of DB changes this turn
    citations: list[dict] = field(default_factory=list)  # help-centre sources the Orders agent quoted


def _why(desc: str = "One short sentence: why you are calling this tool now.") -> dict:
    return {"type": "string", "description": desc}


def _fn(name: str, description: str, props: dict, required: list[str]) -> dict:
    props = {**props, "why": _why()}
    return {"type": "function", "function": {"name": name, "description": description, "parameters": {
        "type": "object", "properties": props, "required": [*required, "why"], "additionalProperties": False}}}


ORDER_ID = {"type": "string", "description": "Order number, e.g. TP-100482"}

TOOL_SPECS = [
    _fn("verify_customer", "Link this conversation to a customer by checking that the email address matches "
        "the order number. Required before discussing any order when the customer is not yet verified.",
        {"email": {"type": "string"}, "order_id": ORDER_ID}, ["email", "order_id"]),
    _fn("list_my_orders", "List the verified customer's recent orders with status and totals.", {}, []),
    _fn("get_order", "Full details of one order: items, status, courier, tracking, dates, refunds.",
        {"order_id": ORDER_ID}, ["order_id"]),
    _fn("issue_refund", "Request a refund for a whole order or one item. The shop's policy engine decides the "
        "outcome (approved / sent for human approval / declined) and the amount — report its decision faithfully.",
        {"order_id": ORDER_ID,
         "reason_category": {"type": "string", "enum": list(policy.REASON_CATEGORIES)},
         "reason": {"type": "string", "description": "The customer's reason in their own words"},
         "sku": {"type": "string", "description": "Only for a single-item refund; omit for the whole order"}},
        ["order_id", "reason_category", "reason"]),
    _fn("cancel_order", "Cancel an order that has not been dispatched yet (full refund).",
        {"order_id": ORDER_ID}, ["order_id"]),
    _fn("update_shipping_address", "Change the delivery address of an order that has not been dispatched yet.",
        {"order_id": ORDER_ID, "new_address": {"type": "string", "description": "Full new UK address incl. postcode"}},
        ["order_id", "new_address"]),
    _fn("open_courier_investigation", "Open a lost-parcel investigation with the courier when tracking has "
        "stopped updating. Use for dispatched orders that seem stuck or lost.", {"order_id": ORDER_ID}, ["order_id"]),
    _fn("search_help_centre", "Look up shop rules and facts in the company's help documents: delivery times and "
        "costs, returns/refund rules, damage and fault reporting windows, warranties, product care, discounts. Use it "
        "for ANY general question, even when phrased about 'my parcel' or 'my order'. Returns a sourced answer, or "
        "found=false when the documents don't cover it.",
        {"question": {"type": "string", "description": "The general question, written as a standalone question"}},
        ["question"]),
    _fn("handoff_to_human", "Hand the conversation to a human colleague (compensation/goodwill requests, "
        "anything you cannot resolve, or the customer insists on a person).",
        {"reason": {"type": "string"}}, ["reason"]),
]


# ---------------------------------------------------------------------------------------- helpers
def _norm_order_id(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    return f"TP-{digits}" if digits else (raw or "").strip().upper()


def _event(ctx: ToolContext, order: Order, kind: str, detail: str):
    ctx.session.add(OrderEvent(order_id=order.id, kind=kind, detail=detail, actor=ACTOR))
    ctx.actions.append(f"{order.id}: {detail}")


def _owned_order(ctx: ToolContext, order_id: str) -> tuple[Order | None, dict | None]:
    if ctx.conversation.customer_id is None:
        return None, {"error": "customer_not_verified",
                      "hint": "Ask for the email address on the order and the order number, then call verify_customer."}
    order = ctx.session.get(Order, _norm_order_id(order_id))
    if order is None or order.customer_id != ctx.conversation.customer_id:
        # Same answer for "doesn't exist" and "belongs to someone else" — never leak other customers' orders.
        return None, {"error": "order_not_found", "order_id": _norm_order_id(order_id),
                      "hint": "Ask the customer to double-check the order number."}
    return order, None


def _iso(dt: datetime | None) -> str | None:
    return dt.date().isoformat() if dt else None


def order_summary(o: Order) -> dict:
    return {"order_id": o.id, "status": o.status, "placed": _iso(o.created_at), "total_gbp": float(o.total),
            "items": ", ".join(f"{it.qty}× {it.name}" for it in o.items)}


def order_detail(o: Order) -> dict:
    return {
        **order_summary(o),
        "items": [{"sku": it.sku, "name": it.name, "qty": it.qty, "unit_price_gbp": float(it.unit_price),
                   "refunded_qty": it.refunded_qty} for it in o.items],
        "shipping_method": o.shipping_method, "shipping_cost_gbp": float(o.shipping_cost),
        "payment_method": o.payment_method, "shipping_address": o.shipping_address,
        "courier": o.courier, "tracking_number": o.tracking_number, "tracking_status": o.tracking_status,
        "dispatched": _iso(o.dispatched_at), "delivered": _iso(o.delivered_at),
        "refunds": [{"amount_gbp": float(r.amount), "status": r.status, "date": _iso(r.created_at)} for r in o.refunds],
    }


# ---------------------------------------------------------------------------------------- tools
def verify_customer(ctx: ToolContext, email: str, order_id: str, **_) -> dict:
    order = ctx.session.get(Order, _norm_order_id(order_id))
    cust = order.customer if order else None
    if not cust or cust.email.lower() != (email or "").strip().lower():
        return {"verified": False, "message": "That email and order number don't match our records."}
    ctx.conversation.customer_id = cust.id
    ctx.actions.append(f"conversation linked to customer {cust.email}")
    return {"verified": True, "customer_name": cust.name}


def list_my_orders(ctx: ToolContext, **_) -> dict:
    if ctx.conversation.customer_id is None:
        return {"error": "customer_not_verified"}
    cust = ctx.session.get(Customer, ctx.conversation.customer_id)
    orders = sorted(cust.orders, key=lambda o: o.created_at, reverse=True)[:10]
    return {"orders": [order_summary(o) for o in orders]}


def get_order(ctx: ToolContext, order_id: str, **_) -> dict:
    order, err = _owned_order(ctx, order_id)
    return err or order_detail(order)


def issue_refund(ctx: ToolContext, order_id: str, reason_category: str, reason: str, sku: str | None = None,
                 **_) -> dict:
    order, err = _owned_order(ctx, order_id)
    if err:
        return err
    if reason_category not in policy.REASON_CATEGORIES:
        reason_category = "other"
    final_sale = {p.sku for p in ctx.session.query(Product).filter(Product.final_sale.is_(True))}
    d = policy.evaluate_refund(order, reason_category=reason_category, sku=sku or None,
                               recent_refund_count=policy.recent_refunds(order), final_sale_skus=final_sale)

    if d.outcome == "approve":
        ctx.session.add(Refund(order_id=order.id, amount=d.amount, reason=f"[{reason_category}] {reason}",
                               status="issued", created_by=ACTOR, decided_by="policy:auto"))
        refunded = {i["sku"] for i in d.items}
        for it in order.items:
            if it.sku in refunded:
                it.refunded_qty = it.qty
        if all(it.refunded_qty >= it.qty for it in order.items):
            order.status = "returned"
        _event(ctx, order, "refund_issued", f"Refund £{d.amount:.2f} issued automatically ({reason_category})"
               + ("; no return needed" if d.no_return_needed else ""))
    elif d.outcome == "needs_approval":
        ctx.session.add(Refund(order_id=order.id, amount=d.amount, reason=f"[{reason_category}] {reason}",
                               status="pending_approval", created_by=ACTOR))
        _event(ctx, order, "refund_requested", f"Refund £{d.amount:.2f} sent for human approval: {', '.join(d.reasons)}")
        ctx.handoffs.append({"category": "refund_approval", "severity": "medium",
                             "reason": f"Refund £{d.amount:.2f} on {order.id} needs approval ({', '.join(d.reasons)})"})
    else:
        _event(ctx, order, "refund_declined", f"Refund declined by policy: {', '.join(d.reasons)}")

    return {"decision": d.outcome, "amount_gbp": d.amount, "includes_delivery_charge": d.include_delivery,
            "no_return_needed": d.no_return_needed, "tell_customer": d.customer_message,
            "internal_reasons_do_not_share": d.reasons}


def cancel_order(ctx: ToolContext, order_id: str, **_) -> dict:
    order, err = _owned_order(ctx, order_id)
    if err:
        return err
    if order.status != "processing":
        return {"cancelled": False, "status": order.status,
                "message": "Already dispatched — it can't be cancelled now, but it can be returned (or refused on delivery)."
                if order.status in ("dispatched", "delivered") else f"Order is already {order.status}."}
    order.status = "cancelled"
    ctx.session.add(Refund(order_id=order.id, amount=float(order.total), reason="Cancelled before dispatch",
                           status="issued", created_by=ACTOR, decided_by="policy:auto"))
    for it in order.items:
        it.refunded_qty = it.qty
    _event(ctx, order, "cancelled", f"Cancelled before dispatch; full refund £{float(order.total):.2f}")
    return {"cancelled": True, "refund_gbp": float(order.total),
            "tell_customer": f"Order {order.id} is cancelled and £{float(order.total):.2f} will be refunded to your "
                             f"{order.payment_method} within 5 working days."}


def update_shipping_address(ctx: ToolContext, order_id: str, new_address: str, **_) -> dict:
    order, err = _owned_order(ctx, order_id)
    if err:
        return err
    if order.status != "processing":
        return {"updated": False, "status": order.status,
                "message": "This order has already been dispatched, so the address can't be changed."}
    if not re.search(r"\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b", new_address.upper()):
        return {"updated": False, "message": "That doesn't look like a full UK address with a postcode — ask for it."}
    old = order.shipping_address
    order.shipping_address = new_address.strip()
    _event(ctx, order, "address_changed", f"Address changed from '{old}' to '{order.shipping_address}'")
    return {"updated": True, "new_address": order.shipping_address}


def open_courier_investigation(ctx: ToolContext, order_id: str, **_) -> dict:
    order, err = _owned_order(ctx, order_id)
    if err:
        return err
    if order.status != "dispatched":
        return {"opened": False, "status": order.status, "message": f"Order is {order.status}, not in transit."}
    dispatched = order.dispatched_at if order.dispatched_at.tzinfo else order.dispatched_at.replace(tzinfo=timezone.utc)
    days = (datetime.now(timezone.utc) - dispatched).days
    if days < 7:  # ~5 working days
        return {"opened": False, "days_since_dispatch": days,
                "message": "It's within the normal delivery time — please allow 5 working days without tracking updates."}
    _event(ctx, order, "courier_investigation", f"Lost-parcel investigation opened with {order.courier} "
                                                f"({days} days since dispatch, tracking: {order.tracking_status})")
    ctx.handoffs.append({"category": "lost_parcel", "severity": "low",
                         "reason": f"Courier investigation opened for {order.id}; refund/resend needs human decision"})
    return {"opened": True, "courier": order.courier, "tell_customer":
            f"I've opened an investigation with {order.courier}. It can take up to 10 working days; once it's "
            "complete our team will either resend your order or refund you, and they'll email you either way."}


def search_help_centre(ctx: ToolContext, question: str, **_) -> dict:
    """The same document search + answer the Support agent uses (citations, "I don't know" gate)."""
    from ..rag.answer import answer_question
    res = answer_question(question)
    if res.idk:
        return {"found": False, "tell_customer": "We don't have that information in our help centre."}
    offset = len(ctx.citations)
    text = re.sub(r"\[(\d+)\]", lambda m: f"[{int(m.group(1)) + offset}]", res.answer)
    for c in res.citations:
        ctx.citations.append({**c.__dict__, "n": c.n + offset})
    return {"found": True, "answer": text, "sources": [f"[{c.n + offset}] {c.title}" for c in res.citations]}


def handoff_to_human(ctx: ToolContext, reason: str, **_) -> dict:
    ctx.handoffs.append({"category": "agent_handoff", "severity": "medium", "reason": reason})
    return {"handed_off": True}


REGISTRY = {f.__name__: f for f in (verify_customer, list_my_orders, get_order, issue_refund, cancel_order,
                                     update_shipping_address, open_courier_investigation, search_help_centre,
                                     handoff_to_human)}
