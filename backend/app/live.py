"""In-process live event bus for the dashboard.

Every customer turn publishes events (customer message, routing decision, each tool call, the reply word by
word, done). Subscribers are the customer's own chat widget (one conversation) and every signed-in staff
member watching the dashboard (all conversations, channel "*").

Agents run in worker threads, so `publish` is thread-safe: it hops onto the server's event loop.
Single-process only; at scale this becomes Redis pub/sub (noted in the write-up).
"""
from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from datetime import datetime, timezone

ALL = "*"
_subs: dict[str, set[asyncio.Queue]] = defaultdict(set)
_loop: asyncio.AbstractEventLoop | None = None


def bind_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _loop
    _loop = loop


def subscribe(channel: str) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=2000)
    _subs[channel].add(q)
    return q


def unsubscribe(channel: str, q: asyncio.Queue) -> None:
    _subs[channel].discard(q)
    if not _subs[channel]:
        _subs.pop(channel, None)


def _deliver(event: dict) -> None:
    for ch in (event["conversation_id"], ALL):
        for q in list(_subs.get(ch, ())):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:   # a stalled browser tab must never block the agents
                pass


def publish(conversation_id: str, event: dict) -> None:
    event = {**event, "conversation_id": conversation_id, "ts": datetime.now(timezone.utc).isoformat()}
    if _loop is None:
        return
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is _loop:
        _deliver(event)
    else:
        _loop.call_soon_threadsafe(_deliver, event)


def sse(event: dict) -> str:
    """One Server-Sent Event. The event type travels inside the JSON, so a plain EventSource.onmessage sees all."""
    return f"data: {json.dumps(event, default=str)}\n\n"
