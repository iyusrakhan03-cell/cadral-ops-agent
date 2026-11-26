"""Outbound alerts. Day 4 wires these to Slack; until then they log, so callers don't change later."""
from __future__ import annotations

import logging

log = logging.getLogger("notify")


def escalation(esc_id: int, severity: str, category: str, summary: str, conversation_id: str):
    log.warning("ESCALATION #%s [%s/%s] conv=%s: %s", esc_id, severity, category, conversation_id, summary[:200])


def system_error(where: str, error: str):
    log.error("SYSTEM ERROR in %s: %s", where, error[:500])
