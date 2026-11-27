"""Email channel over the Gmail API (OAuth) — the backend reads and sends the shop's Gmail itself.

This replaces the Apps Script bridge (channels/gmail-bridge/) now that the backend runs on a normal machine
instead of a Hugging Face Space. It does exactly what the bridge did, through the same inbox/outbox:

  every GMAIL_POLL_S seconds
    1. unread inbox emails -> channels.accept_inbound("email", ...)  -> then marked read (only after it's saved)
    2. channels.claim_emails() -> sent from the shop's Gmail (a reply in the customer's thread) -> ack_email()

One-time setup: put GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET in .env, then run
    python -m scripts.gmail_auth
which opens Google's consent screen and saves GMAIL_REFRESH_TOKEN to .env. Restart the backend.

Scope: gmail.modify (read, mark as read, send). Nothing is deleted.
If both this and the Apps Script run, nothing is sent twice: claim_emails() leases each email to one sender.
"""
from __future__ import annotations

import base64
import html as html_lib
import logging
import re
import threading
import time
from email.message import EmailMessage
from email.utils import formataddr, parseaddr

import httpx

from .config import get_settings

log = logging.getLogger("gmail")

API = "https://gmail.googleapis.com/gmail/v1/users/me"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/gmail.modify"
SENDER_NAME = "Thistle & Pine Customer Care"
MAX_PER_RUN = 15
INBOX_QUERY = "in:inbox is:unread newer_than:7d"

_token: dict = {"access": "", "exp": 0.0}
_me: str = ""
_started = False
_last_error = ""


class GmailAuthError(RuntimeError):
    """The saved Google login no longer works (revoked, expired, wrong client) — needs scripts.gmail_auth again."""


def configured() -> bool:
    st = get_settings()
    return bool(st.google_client_id and st.google_client_secret and st.gmail_refresh_token)


# ---------------------------------------------------------------------- auth + HTTP
def _access_token() -> str:
    if _token["access"] and time.time() < _token["exp"] - 60:
        return _token["access"]
    st = get_settings()
    r = httpx.post(TOKEN_URL, timeout=20, data={
        "client_id": st.google_client_id, "client_secret": st.google_client_secret,
        "refresh_token": st.gmail_refresh_token, "grant_type": "refresh_token"})
    if r.status_code in (400, 401):
        raise GmailAuthError(f"Google refused the saved login ({r.json().get('error', r.status_code)}). "
                             "Run: python -m scripts.gmail_auth")
    r.raise_for_status()
    j = r.json()
    _token.update(access=j["access_token"], exp=time.time() + int(j.get("expires_in", 3600)))
    return _token["access"]


def _api(method: str, path: str, **kw) -> dict:
    for attempt in (1, 2):
        r = httpx.request(method, API + path, timeout=30,
                          headers={"Authorization": f"Bearer {_access_token()}"}, **kw)
        if r.status_code == 401 and attempt == 1:      # token expired early: refresh once
            _token["access"] = ""
            continue
        if r.status_code >= 300:
            raise RuntimeError(f"Gmail API {method} {path.split('?')[0]} -> {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else {}
    return {}


def my_address() -> str:
    global _me
    if not _me:
        _me = _api("GET", "/profile")["emailAddress"].lower()
    return _me


# ---------------------------------------------------------------------- reading
def _b64(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")


def _headers(msg: dict) -> dict[str, str]:
    return {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}


def _html_to_text(h: str) -> str:
    h = re.sub(r"(?is)<(script|style).*?</\1>", "", h)
    h = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</tr>", "\n", h)
    return html_lib.unescape(re.sub(r"<[^>]+>", "", h))


def plain_body(payload: dict) -> str:
    """The text/plain part of a Gmail message (falls back to the HTML part, stripped of tags)."""
    plain, html_part = [], []

    def walk(p: dict) -> None:
        mime = p.get("mimeType", "")
        data = p.get("body", {}).get("data")
        if p.get("filename"):          # attachment
            return
        if mime == "text/plain" and data:
            plain.append(_b64(data))
        elif mime == "text/html" and data:
            html_part.append(_b64(data))
        for sub in p.get("parts", []) or []:
            walk(sub)

    walk(payload)
    return "\n".join(plain) if plain else _html_to_text("\n".join(html_part))


def is_automated(headers: dict[str, str], sender: str) -> bool:
    if re.search(r"(no-?reply|mailer-daemon|postmaster|notifications?@)", sender, re.I):
        return True
    auto = headers.get("auto-submitted", "").lower()
    prec = headers.get("precedence", "").lower()
    return bool(auto and auto != "no") or prec in ("bulk", "list", "junk")


def pull_inbound() -> int:
    from .channels import accept_inbound
    me = my_address()
    listing = _api("GET", "/messages", params={"q": INBOX_QUERY, "maxResults": MAX_PER_RUN})
    n = 0
    for ref in listing.get("messages", []) or []:
        msg = _api("GET", f"/messages/{ref['id']}", params={"format": "full"})
        h = _headers(msg)
        name, addr = parseaddr(h.get("from", ""))
        addr = addr.strip().lower()
        if addr and addr != me and not is_automated(h, addr):
            accept_inbound("email", msg["id"], {
                "message_id": msg["id"], "thread_id": msg["threadId"], "from": addr, "from_name": name,
                "subject": h.get("subject", ""), "body": plain_body(msg.get("payload", {}))[:50000]})
            n += 1
        # mark read only after the backend saved it (accept_inbound commits before returning)
        _api("POST", f"/messages/{msg['id']}/modify", json={"removeLabelIds": ["UNREAD"]})
    return n


# ---------------------------------------------------------------------- sending
def build_mime(e: dict, sender: str, original: dict[str, str] | None = None) -> EmailMessage:
    m = EmailMessage()
    m["From"] = formataddr((SENDER_NAME, sender))
    if original:                                   # reply inside the customer's own thread
        to = original.get("reply-to") or original.get("from") or e.get("to")
        subj = original.get("subject", "") or e.get("subject", "")
        m["To"] = to
        m["Subject"] = subj if subj.lower().startswith("re:") else f"Re: {subj}"
        if original.get("message-id"):
            m["In-Reply-To"] = original["message-id"]
            m["References"] = (original.get("references", "") + " " + original["message-id"]).strip()
    else:
        m["To"] = e["to"]
        m["Subject"] = e.get("subject") or "Thistle & Pine"
    m.set_content(e.get("text", ""))
    if e.get("html"):
        m.add_alternative(e["html"], subtype="html")
    return m


def send_one(e: dict) -> None:
    original, thread_id = None, None
    if e.get("reply_to_message_id"):
        meta = _api("GET", f"/messages/{e['reply_to_message_id']}", params=[
            ("format", "metadata"), *[("metadataHeaders", x) for x in
                                      ("From", "Reply-To", "Subject", "Message-ID", "References")]])
        original, thread_id = _headers(meta), meta.get("threadId")
    raw = base64.urlsafe_b64encode(build_mime(e, my_address(), original).as_bytes()).decode()
    body = {"raw": raw}
    if thread_id:
        body["threadId"] = thread_id
    _api("POST", "/messages/send", json=body)


def push_outbox() -> int:
    from .channels import ack_email, claim_emails
    sent = 0
    for e in claim_emails(MAX_PER_RUN):
        try:
            send_one(e)
            ack_email(e["id"], True)
            sent += 1
        except GmailAuthError:
            ack_email(e["id"], False, "Gmail login expired")
            raise
        except Exception as ex:  # noqa: BLE001 — retried with back-off by the outbox
            log.warning("email #%s failed: %s", e["id"], ex)
            ack_email(e["id"], False, f"{type(ex).__name__}: {ex}"[:1000])
    return sent


# ---------------------------------------------------------------------- worker
def _loop() -> None:
    global _last_error
    poll = max(10, get_settings().gmail_poll_s)
    while True:
        wait = poll
        try:
            pull_inbound()
            push_outbox()
            _last_error = ""
        except GmailAuthError as e:
            _last_error = str(e)
            from . import notify
            notify.system_error("gmail", str(e))
            wait = 600
        except Exception as e:  # noqa: BLE001 — offline, Google hiccup… try again next round
            _last_error = f"{type(e).__name__}: {e}"[:500]
            log.warning("gmail round failed: %s", _last_error)
            wait = max(poll, 60)
        time.sleep(wait)


def status() -> dict:
    return {"configured": configured(), "address": _me or None, "last_error": _last_error or None}


def start() -> None:
    global _started
    if _started or not configured() or not get_settings().workers:
        return
    _started = True
    threading.Thread(target=_loop, name="gmail-worker", daemon=True).start()
    log.info("Gmail API email channel started (polling every %ss)", get_settings().gmail_poll_s)
