"""Outbound alerts to staff on Slack (an "Incoming Webhook" — free, no bot needed).

Alerts are queued in the Outbox and sent by the delivery worker, so a Slack outage or a restart doesn't lose
them (they retry with back-off). Everything is also written to the server log. With no SLACK_WEBHOOK_URL set,
alerts are only logged.
"""
from __future__ import annotations

import logging
import time

from .config import get_settings

log = logging.getLogger("notify")

SEVERITY_EMOJI = {"critical": ":rotating_light:", "high": ":red_circle:", "medium": ":large_orange_circle:",
                  "low": ":white_circle:"}
_last_error_alert: dict[str, float] = {}
ERROR_ALERT_EVERY_S = 600       # at most one Slack alert per error source every 10 minutes


def _link(path: str) -> str | None:
    base = get_settings().dashboard_url.rstrip("/")
    return f"{base}{path}" if base else None


def slack(text: str, blocks: list | None = None) -> None:
    """Queue a Slack message (no-op without a webhook URL)."""
    if not get_settings().slack_webhook_url:
        return
    from .channels import enqueue
    payload = {"text": text}
    if blocks:
        payload["blocks"] = blocks
    try:
        enqueue("slack", "alert", payload)
    except Exception:  # noqa: BLE001 — alerting must never break a customer turn
        log.exception("could not queue Slack alert")


def escalation(esc_id: int, severity: str, category: str, summary: str, conversation_id: str):
    log.warning("ESCALATION #%s [%s/%s] conv=%s: %s", esc_id, severity, category, conversation_id, summary[:200])
    emoji = SEVERITY_EMOJI.get(severity, ":white_circle:")
    title = f"{emoji} {severity.upper()} · {category.replace('_', ' ')} · ticket #{esc_id}"
    link = _link(f"/conversations?id={conversation_id}")
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": f"*{title}*\n{summary[:2500]}"}}]
    if link:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"<{link}|Open the conversation>"}]})
    slack(f"{title}: {summary[:200]}", blocks)


def system_error(where: str, error: str):
    log.error("SYSTEM ERROR in %s: %s", where, error[:500])
    now = time.time()
    if now - _last_error_alert.get(where, 0) < ERROR_ALERT_EVERY_S:
        return
    _last_error_alert[where] = now
    slack(f":warning: System problem in {where}: {error[:500]}",
          [{"type": "section", "text": {"type": "mrkdwn", "text": f":warning: *System problem* in `{where}`\n```{error[:1500]}```"}}])
