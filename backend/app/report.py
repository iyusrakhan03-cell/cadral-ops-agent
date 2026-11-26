"""Daily management report: yesterday's numbers + what needs attention now, by email (and a Slack summary).

Sent automatically once a day at REPORT_HOUR in the shop's time zone by the outbox worker (the email itself goes
out through the Gmail bridge). If the server was asleep at that hour, it goes out as soon as it wakes. Admins can
preview it and send it on demand from the dashboard (Reports page).
"""
from __future__ import annotations

import html
import logging
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select

from .analytics import solved_by_ai
from .config import get_settings
from .db import SessionLocal
from .models import Conversation, Escalation, Feedback, InboundEvent, Message, Outbox, Refund, Setting, Trace

log = logging.getLogger("report")

SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
LABEL_NAMES = {"order_tracking": "Where's my order", "order_action": "Refunds, returns & changes",
               "product_question": "Product questions", "policy_question": "Policy questions",
               "escalation": "Complaints & escalations", "chit_chat": "Greetings & other"}


def _tz() -> ZoneInfo:
    return ZoneInfo(get_settings().report_timezone)


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _pct(part: int, whole: int) -> float | None:
    return round(100 * part / whole, 1) if whole else None


def build(day: date | None = None) -> dict:
    """Numbers for one shop-local day (default: yesterday) plus the live 'needs attention' lists."""
    tz = _tz()
    day = day or (datetime.now(tz).date() - timedelta(days=1))
    start = datetime.combine(day, time.min, tzinfo=tz).astimezone(timezone.utc)
    end = start + timedelta(days=1)

    def in_day(dt) -> bool:
        return dt is not None and start <= _utc(dt) < end

    with SessionLocal() as s:
        escalated = {e for (e,) in s.execute(select(Escalation.conversation_id)).all()}
        convs = [c for c in s.scalars(select(Conversation)).all() if in_day(c.created_at)]
        traces = [t for t in s.scalars(select(Trace)).all() if in_day(t.created_at)]
        new_escs = [e for e in s.scalars(select(Escalation)).all() if in_day(e.created_at)]
        refunds = [r for r in s.scalars(select(Refund)).all() if in_day(r.created_at)]
        fb = [f for f in s.scalars(select(Feedback)).all() if in_day(f.created_at)]
        failed_inbound = [i for i in s.scalars(select(InboundEvent).where(InboundEvent.status == "failed")).all()]

        verdicts = [solved_by_ai(c, escalated) for c in convs]
        solved, needed = verdicts.count(True), verdicts.count(False)
        lat = sorted(t.latency_ms for t in traces if t.latency_ms and not t.error)
        cost = sum(t.cost_usd or 0 for t in traces)
        issued = [r for r in refunds if r.status == "issued"]

        # ---- needs attention right now (not limited to yesterday)
        open_escs = sorted(s.scalars(select(Escalation).where(Escalation.status == "open")).all(),
                           key=lambda e: (SEV_ORDER.get(e.severity, 9), e.created_at))
        pending_refunds = s.scalars(select(Refund).where(Refund.status == "pending_approval")).all()
        data = {
            "date": day.isoformat(),
            "date_label": day.strftime("%A %d %B %Y"),
            "conversations": len(convs),
            "by_channel": dict(Counter(c.channel for c in convs)),
            "ai_replies": len(traces),
            "ai_solved": solved, "needed_human": needed, "ai_solved_pct": _pct(solved, solved + needed),
            "avg_reply_s": round(sum(lat) / len(lat) / 1000, 1) if lat else None,
            "p95_reply_s": round(lat[min(len(lat) - 1, int(0.95 * (len(lat) - 1) + 0.5))] / 1000, 1) if lat else None,
            "ai_cost_usd": round(cost, 4),
            "errors": sum(1 for t in traces if t.error),
            "topics": dict(Counter(LABEL_NAMES.get(t.route_label, t.route_label or "unknown")
                                   for t in traces if t.route_label).most_common()),
            "new_escalations": len(new_escs),
            "new_escalations_by_severity": dict(Counter(e.severity for e in new_escs)),
            "refunds_issued": len(issued),
            "refunds_issued_gbp": round(sum(float(r.amount) for r in issued), 2),
            "refunds_auto_by_ai": sum(1 for r in issued if (r.created_by or "").startswith("ai") and not r.decided_by),
            "refunds_sent_for_approval": sum(1 for r in refunds if r.decided_by or r.status == "pending_approval"),
            "refunds_declined": sum(1 for r in refunds if r.status in ("declined", "rejected")),
            "reviews": dict(Counter(f.action for f in fb)),
            "attention": {
                "open_escalations": [{"id": e.id, "severity": e.severity, "category": e.category,
                                      "summary": e.summary[:300], "conversation_id": e.conversation_id}
                                     for e in open_escs[:8]],
                "open_escalations_total": len(open_escs),
                "pending_refunds": [{"id": r.id, "order_id": r.order_id, "amount_gbp": float(r.amount),
                                     "reason": (r.reason or "")[:200]} for r in pending_refunds[:8]],
                "pending_refunds_total": len(pending_refunds),
                "pending_refunds_gbp": round(sum(float(r.amount) for r in pending_refunds), 2),
                "replies_awaiting_review": s.query(Message).filter_by(status="pending_review").count(),
                "conversations_needing_human": s.query(Conversation).filter_by(status="needs_human").count(),
                "failed_deliveries": s.query(Outbox).filter_by(status="failed").count() + len(failed_inbound),
            },
        }
    return data


# ---------------------------------------------------------------------------------------------- rendering
def _fmt(v, suffix="") -> str:
    return "—" if v is None else f"{v}{suffix}"


def to_text(d: dict) -> str:
    a = d["attention"]
    lines = [f"Thistle & Pine — daily operations report for {d['date_label']}", "",
             f"Conversations: {d['conversations']} "
             f"({', '.join(f'{k} {v}' for k, v in d['by_channel'].items()) or 'none'})",
             f"Solved by AI alone: {_fmt(d['ai_solved_pct'], '%')} ({d['ai_solved']} of {d['ai_solved'] + d['needed_human']})",
             f"AI reply time: average {_fmt(d['avg_reply_s'], ' s')}, slowest 5% {_fmt(d['p95_reply_s'], ' s')}",
             f"Refunds issued: {d['refunds_issued']} (£{d['refunds_issued_gbp']:.2f}); "
             f"{d['refunds_auto_by_ai']} automatically within policy",
             f"New escalations: {d['new_escalations']}  |  AI errors: {d['errors']}  |  AI cost: ${d['ai_cost_usd']:.4f}",
             "", "NEEDS ATTENTION NOW",
             f"- Open escalations: {a['open_escalations_total']}",
             *[f"    #{e['id']} [{e['severity']}] {e['category']}: {e['summary'][:140]}" for e in a["open_escalations"]],
             f"- Refunds waiting for approval: {a['pending_refunds_total']} (£{a['pending_refunds_gbp']:.2f})",
             f"- AI replies waiting for review: {a['replies_awaiting_review']}",
             f"- Conversations waiting for a human: {a['conversations_needing_human']}",
             f"- Failed deliveries: {a['failed_deliveries']}"]
    link = get_settings().dashboard_url
    if link:
        lines += ["", f"Dashboard: {link}"]
    return "\n".join(lines)


def to_html(d: dict) -> str:
    e = html.escape
    a = d["attention"]
    dash = get_settings().dashboard_url.rstrip("/")

    def tile(label, value, note=""):
        return (f'<td style="padding:12px 14px;background:#f6f4ef;border-radius:8px;vertical-align:top">'
                f'<div style="font-size:12px;color:#6b6b6b">{e(label)}</div>'
                f'<div style="font-size:22px;font-weight:600;color:#1f2a24;margin-top:2px">{e(str(value))}</div>'
                f'<div style="font-size:12px;color:#6b6b6b">{e(note)}</div></td>')

    def row(cells):
        return "<tr>" + "".join(f'<td style="padding:6px 8px;border-top:1px solid #e6e1d6">{c}</td>' for c in cells) + "</tr>"

    escs = "".join(row([f"#{x['id']}", f"<b>{e(x['severity'])}</b>", e(x["category"].replace("_", " ")),
                        e(x["summary"][:180])]) for x in a["open_escalations"]) or row(["Nothing open"])
    refs = "".join(row([e(x["order_id"]), f"£{x['amount_gbp']:.2f}", e(x["reason"][:160])])
                   for x in a["pending_refunds"]) or row(["Nothing waiting"])
    topics = "".join(row([e(k), str(v)]) for k, v in d["topics"].items()) or row(["No conversations"])
    channels = ", ".join(f"{k} {v}" for k, v in d["by_channel"].items()) or "none"
    reviews = d["reviews"]
    btn = (f'<p style="margin:24px 0"><a href="{e(dash)}" style="background:#2f5d46;color:#fff;padding:10px 16px;'
           f'border-radius:6px;text-decoration:none">Open the dashboard</a></p>') if dash else ""
    return f"""<div style="font-family:Arial,Helvetica,sans-serif;max-width:680px;color:#1f2a24">
<h2 style="margin:0 0 4px">Thistle &amp; Pine — daily operations report</h2>
<div style="color:#6b6b6b;margin-bottom:16px">{e(d['date_label'])}</div>
<table cellspacing="8" style="width:100%;margin-left:-8px"><tr>
{tile('Conversations', d['conversations'], channels)}
{tile('Solved by AI alone', _fmt(d['ai_solved_pct'], '%'), f"{d['ai_solved']} of {d['ai_solved'] + d['needed_human']}")}
{tile('AI reply time', _fmt(d['avg_reply_s'], ' s'), f"slowest 5%: {_fmt(d['p95_reply_s'], ' s')}")}
</tr><tr>
{tile('Refunds issued', f"£{d['refunds_issued_gbp']:.2f}", f"{d['refunds_issued']} refunds, {d['refunds_auto_by_ai']} automatic")}
{tile('New escalations', d['new_escalations'], ', '.join(f'{k} {v}' for k, v in d['new_escalations_by_severity'].items()))}
{tile('AI cost', f"${d['ai_cost_usd']:.4f}", f"{d['errors']} errors · free tier")}
</tr></table>
<h3 style="margin:24px 0 6px">Needs attention now</h3>
<p style="margin:0 0 8px">{a['open_escalations_total']} open escalations · {a['pending_refunds_total']} refunds waiting
(£{a['pending_refunds_gbp']:.2f}) · {a['replies_awaiting_review']} AI replies to review ·
{a['conversations_needing_human']} conversations waiting for a human · {a['failed_deliveries']} failed deliveries</p>
<table style="width:100%;border-collapse:collapse;font-size:14px">{escs}</table>
<h3 style="margin:24px 0 6px">Refunds waiting for approval</h3>
<table style="width:100%;border-collapse:collapse;font-size:14px">{refs}</table>
<h3 style="margin:24px 0 6px">What customers asked about</h3>
<table style="width:100%;border-collapse:collapse;font-size:14px">{topics}</table>
<p style="color:#6b6b6b;font-size:13px;margin-top:16px">Human review of AI replies: {reviews.get('approve', 0)} approved,
{reviews.get('edit', 0)} edited, {reviews.get('reject', 0)} rejected.</p>
{btn}
<p style="color:#9a9a9a;font-size:12px">Sent automatically by the Thistle &amp; Pine operations agent.</p></div>"""


def slack_summary(d: dict) -> tuple[str, list]:
    a = d["attention"]
    text = (f"*Daily report — {d['date_label']}*\n"
            f"• {d['conversations']} conversations · {_fmt(d['ai_solved_pct'], '%')} solved by AI alone\n"
            f"• Refunds issued: {d['refunds_issued']} (£{d['refunds_issued_gbp']:.2f}) · "
            f"new escalations: {d['new_escalations']} · AI errors: {d['errors']}\n"
            f"• *Needs attention:* {a['open_escalations_total']} open escalations, "
            f"{a['pending_refunds_total']} refunds to approve (£{a['pending_refunds_gbp']:.2f}), "
            f"{a['replies_awaiting_review']} replies to review")
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": f":bar_chart: {text}"}}]
    return text.replace("*", ""), blocks


# ---------------------------------------------------------------------------------------------- sending
def send(day: date | None = None, *, to: str | None = None) -> dict:
    """Queue the report email (sent by the Gmail bridge) and post the Slack summary."""
    from . import notify
    from .channels import enqueue
    d = build(day)
    to = to or get_settings().manager_email
    if to:
        enqueue("email", "report", {"to": to, "subject": f"Thistle & Pine daily report — {d['date_label']}",
                                    "text": to_text(d), "html": to_html(d)}, target=to)
    text, blocks = slack_summary(d)
    notify.slack(text, blocks)
    log.info("daily report for %s queued (email to %s)", d["date"], to or "nobody — MANAGER_EMAIL not set")
    return {"date": d["date"], "emailed_to": to or None, "slack": bool(get_settings().slack_webhook_url)}


def maybe_send_daily() -> None:
    """Called every few seconds by the outbox worker; sends at most once per shop-local day."""
    st = get_settings()
    if not (st.manager_email or st.slack_webhook_url):
        return
    now = datetime.now(_tz())
    if now.hour < st.report_hour:
        return
    today = now.date().isoformat()
    with SessionLocal() as s:
        row = s.get(Setting, "last_daily_report")
        if row and row.value >= today:
            return
        if row is None:
            row = Setting(key="last_daily_report", value=today)
            s.add(row)
        row.value = today
        s.commit()          # claim first, so a crash mid-send can't send twice
    send()
