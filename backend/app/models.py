"""Shop database tables. Statuses are plain strings so the schema is identical on SQLite and Postgres."""
from datetime import datetime, timezone

import uuid

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(40))
    city: Mapped[str | None] = mapped_column(String(80))
    telegram_chat_id: Mapped[str | None] = mapped_column(String(40), index=True)
    pine_points: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str | None] = mapped_column(Text)  # long-term memory written by agents
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    orders: Mapped[list["Order"]] = relationship(back_populates="customer")


class Product(Base):
    __tablename__ = "products"
    sku: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(60))
    price: Mapped[float] = mapped_column(Numeric(10, 2))
    stock_qty: Mapped[int] = mapped_column(Integer, default=0)
    delivery_type: Mapped[str] = mapped_column(String(20), default="standard")
    final_sale: Mapped[bool] = mapped_column(Boolean, default=False)


ORDER_STATUSES = ("processing", "dispatched", "delivered", "cancelled", "returned")


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[str] = mapped_column(String(20), primary_key=True)  # e.g. TP-100482
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    shipping_method: Mapped[str] = mapped_column(String(30))
    shipping_cost: Mapped[float] = mapped_column(Numeric(10, 2))
    subtotal: Mapped[float] = mapped_column(Numeric(10, 2))
    total: Mapped[float] = mapped_column(Numeric(10, 2))
    payment_method: Mapped[str] = mapped_column(String(30))
    shipping_address: Mapped[str] = mapped_column(Text)
    courier: Mapped[str | None] = mapped_column(String(30))
    tracking_number: Mapped[str | None] = mapped_column(String(40))
    tracking_status: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    customer: Mapped[Customer] = relationship(back_populates="orders")
    items: Mapped[list["OrderItem"]] = relationship(back_populates="order", cascade="all, delete-orphan")
    refunds: Mapped[list["Refund"]] = relationship(back_populates="order", cascade="all, delete-orphan")
    events: Mapped[list["OrderEvent"]] = relationship(back_populates="order", cascade="all, delete-orphan")


class OrderItem(Base):
    __tablename__ = "order_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    sku: Mapped[str] = mapped_column(ForeignKey("products.sku"))
    name: Mapped[str] = mapped_column(String(200))
    qty: Mapped[int] = mapped_column(Integer)
    unit_price: Mapped[float] = mapped_column(Numeric(10, 2))
    refunded_qty: Mapped[int] = mapped_column(Integer, default=0)

    order: Mapped[Order] = relationship(back_populates="items")


REFUND_STATUSES = ("issued", "pending_approval", "rejected", "declined")


class Refund(Base):
    __tablename__ = "refunds"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    amount: Mapped[float] = mapped_column(Numeric(10, 2))
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), index=True)
    created_by: Mapped[str] = mapped_column(String(60))  # "ai:orders_agent" | "human:<user>"
    decided_by: Mapped[str | None] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    order: Mapped[Order] = relationship(back_populates="refunds")


class OrderEvent(Base):
    """Audit log: every change an agent or human makes to an order."""
    __tablename__ = "order_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40))  # created, dispatched, address_changed, refund_issued...
    detail: Mapped[str] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    order: Mapped[Order] = relationship(back_populates="events")


# ====================================================================== conversations (shared memory)
def new_id() -> str:
    return uuid.uuid4().hex[:12]


CONVERSATION_STATUSES = ("open", "needs_human", "resolved")


class Conversation(Base):
    """One thread with a customer on one channel. Every agent reads the same history from here."""
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=new_id)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), index=True)
    channel: Mapped[str] = mapped_column(String(20), default="web")      # web | email | telegram
    external_ref: Mapped[str | None] = mapped_column(String(200), index=True)  # email thread id / telegram chat id
    subject: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    last_agent: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    customer: Mapped[Customer | None] = relationship()
    messages: Mapped[list["Message"]] = relationship(back_populates="conversation", order_by="Message.id",
                                                     cascade="all, delete-orphan")


MESSAGE_STATUSES = ("sent", "pending_review", "edited", "rejected", "received")


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))          # customer | assistant | human
    agent: Mapped[str | None] = mapped_column(String(20))  # support | orders | escalation (assistant msgs)
    content: Mapped[str] = mapped_column(Text)
    original_content: Mapped[str | None] = mapped_column(Text)  # AI draft before a human edited it
    status: Mapped[str] = mapped_column(String(20), default="sent", index=True)
    citations: Mapped[list | None] = mapped_column(JSON)
    confidence: Mapped[float | None] = mapped_column(Float)
    trace_id: Mapped[int | None] = mapped_column(ForeignKey("traces.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class Trace(Base):
    """Agent trace for one customer turn: route decision, every tool call (with the agent's 'why'), cost, latency."""
    __tablename__ = "traces"
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    route_label: Mapped[str | None] = mapped_column(String(30))
    route_source: Mapped[str | None] = mapped_column(String(20))   # rules | finetuned | llm | sticky
    route_reason: Mapped[str | None] = mapped_column(Text)
    agent: Mapped[str | None] = mapped_column(String(20))
    steps: Mapped[list] = mapped_column(JSON, default=list)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    llm_calls: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Escalation(Base):
    __tablename__ = "escalations"
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"))
    severity: Mapped[str] = mapped_column(String(10))       # low | medium | high | critical
    category: Mapped[str] = mapped_column(String(40))       # safety | legal | chargeback | angry | refund_approval ...
    summary: Mapped[str] = mapped_column(Text)
    customer_wants: Mapped[str | None] = mapped_column(Text)
    suggested_action: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Feedback(Base):
    """A human approved, edited or rejected an AI reply. Edited replies become few-shot examples
    for similar future questions and rows in the re-training dataset (self-improving loop)."""
    __tablename__ = "feedback"
    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id"), index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    agent: Mapped[str | None] = mapped_column(String(20))
    route_label: Mapped[str | None] = mapped_column(String(30))
    corrected_label: Mapped[str | None] = mapped_column(String(30))   # human says the router was wrong
    question: Mapped[str] = mapped_column(Text)
    ai_reply: Mapped[str] = mapped_column(Text)
    final_reply: Mapped[str | None] = mapped_column(Text)
    action: Mapped[str] = mapped_column(String(10))   # approve | edit | reject
    reason: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(60))
    times_used: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
