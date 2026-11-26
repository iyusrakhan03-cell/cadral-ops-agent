"""Numbers for the dashboard's analytics page, computed straight from the tables the agents already write.

Definitions (also shown in the dashboard so nobody has to guess):
- Solved by AI alone: the conversation got at least one AI reply, and no human was needed: no escalation
  ticket, not marked "needs a human", no human-written reply, and no AI reply edited or rejected.
- Speed: time from the customer's message arriving to the AI's reply being ready (Trace.latency_ms).
- Accuracy (human review): approved / all AI replies a human judged (approve + edit + reject).
- Accuracy (test suite): the score of each run of scripts.run_evals (evals/history.jsonl).
- Cost: Groq list price of the tokens used (we run on the free tier, so the real bill is $0).
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from .config import REPO_DIR
from .models import Conversation, Escalation, Feedback, Message, Trace


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    v = sorted(values)
    return round(v[min(len(v) - 1, int(round(p * (len(v) - 1))))], 1)


def _eval_history() -> list[dict]:
    path = REPO_DIR / "evals" / "history.jsonl"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        rows.append({"timestamp": r.get("timestamp"), "suite": r.get("suite"), "score": r.get("score"),
                     "cases": r.get("cases"), "passed": r.get("passed")})
    return rows


def solved_by_ai(conv: Conversation, escalated: set[str]) -> bool | None:
    """True/False, or None if the AI hasn't replied yet (not counted either way)."""
    msgs = conv.messages
    if not any(m.role == "assistant" and m.agent != "system" for m in msgs):
        return None
    if conv.id in escalated or conv.status == "needs_human":
        return False
    if any(m.role == "human" or m.status in ("edited", "rejected") for m in msgs):
        return False
    return True


def compute(session: Session, days: int = 14) -> dict:
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=days - 1)
    since = datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc)
    day_keys = [(start + timedelta(days=i)).isoformat() for i in range(days)]

    convs = [c for c in session.query(Conversation).all() if _utc(c.created_at) >= since]
    escalated = {e.conversation_id for e in session.query(Escalation.conversation_id).all()}
    traces = [t for t in session.query(Trace).all() if _utc(t.created_at) >= since]
    feedback = [f for f in session.query(Feedback).all() if _utc(f.created_at) >= since]

    # ---- solved by AI alone
    per_day = {k: {"date": k, "ai_solved": 0, "needed_human": 0, "conversations": 0} for k in day_keys}
    solved = needed = 0
    for c in convs:
        k = _utc(c.created_at).date().isoformat()
        if k not in per_day:
            continue
        per_day[k]["conversations"] += 1
        verdict = solved_by_ai(c, escalated)
        if verdict is True:
            solved += 1
            per_day[k]["ai_solved"] += 1
        elif verdict is False:
            needed += 1
            per_day[k]["needed_human"] += 1

    # ---- speed and cost, per day
    lat_by_day: dict[str, list[float]] = defaultdict(list)
    cost_by_day: dict[str, float] = defaultdict(float)
    for t in traces:
        k = _utc(t.created_at).date().isoformat()
        if t.latency_ms:
            lat_by_day[k].append(t.latency_ms)
        cost_by_day[k] += t.cost_usd or 0.0
    all_lat = [t.latency_ms for t in traces if t.latency_ms]
    total_cost = sum(t.cost_usd or 0.0 for t in traces)
    convs_with_ai = len({t.conversation_id for t in traces})

    # ---- accuracy from human review, per day
    fb_by_day: dict[str, Counter] = defaultdict(Counter)
    for f in feedback:
        fb_by_day[_utc(f.created_at).date().isoformat()][f.action] += 1
    fb_total = Counter(f.action for f in feedback)
    judged = sum(fb_total.values())

    series = []
    for k in day_keys:
        lat = lat_by_day.get(k, [])
        fb = fb_by_day.get(k, Counter())
        n_fb = sum(fb.values())
        d = per_day[k]
        series.append({
            **d,
            "ai_solved_pct": round(100 * d["ai_solved"] / (d["ai_solved"] + d["needed_human"]), 1)
            if d["ai_solved"] + d["needed_human"] else None,
            "avg_latency_s": round(sum(lat) / len(lat) / 1000, 2) if lat else None,
            "p95_latency_s": round(_pct(lat, 0.95) / 1000, 2) if lat else None,
            "cost_usd": round(cost_by_day.get(k, 0.0), 5),
            "reviewed": n_fb,
            "approved_pct": round(100 * fb["approve"] / n_fb, 1) if n_fb else None,
        })

    return {
        "days": days,
        "definitions": {
            "ai_solved": "Got an AI reply and never needed a human: no escalation, not handed over, "
                         "no human reply, no AI reply edited or rejected.",
            "speed": "Customer message arrives → AI reply ready.",
            "accuracy_review": "Approved ÷ all AI replies a human reviewed.",
            "accuracy_tests": "Score of each full test-suite run (scripts.run_evals).",
            "cost": "Groq list price of tokens used. We run on the free tier, so the actual bill is $0.",
        },
        "totals": {
            "conversations": len(convs),
            "ai_solved": solved,
            "needed_human": needed,
            "ai_solved_pct": round(100 * solved / (solved + needed), 1) if solved + needed else None,
            "ai_replies": len(traces),
            "errors": sum(1 for t in traces if t.error),
            "avg_latency_s": round(sum(all_lat) / len(all_lat) / 1000, 2) if all_lat else None,
            "p50_latency_s": round(_pct(all_lat, 0.5) / 1000, 2) if all_lat else None,
            "p95_latency_s": round(_pct(all_lat, 0.95) / 1000, 2) if all_lat else None,
            "total_cost_usd": round(total_cost, 5),
            "cost_per_conversation_usd": round(total_cost / convs_with_ai, 6) if convs_with_ai else None,
            "tokens": sum((t.prompt_tokens or 0) + (t.completion_tokens or 0) for t in traces),
            "reviewed": judged,
            "approved": fb_total["approve"],
            "edited": fb_total["edit"],
            "rejected": fb_total["reject"],
            "approved_pct": round(100 * fb_total["approve"] / judged, 1) if judged else None,
        },
        "series": series,
        "by_agent": dict(Counter(t.agent or "error" for t in traces)),
        "by_route_source": dict(Counter(t.route_source or "error" for t in traces)),
        "eval_history": _eval_history(),
    }

