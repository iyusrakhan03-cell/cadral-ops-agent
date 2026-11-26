"""Point the shop's Telegram bot at the dashboard's relay (run once, from your computer).

    backend/.venv/Scripts/python deploy/set_telegram_webhook.py          # Windows
    backend/.venv/bin/python deploy/set_telegram_webhook.py              # Mac/Linux

Needs in .env: TELEGRAM_BOT_TOKEN (from @BotFather) and DASHBOARD_URL (e.g. https://thistle-pine-dashboard.vercel.app).
Creates TELEGRAM_WEBHOOK_SECRET if missing (saved to .env) — put the same value in Vercel's environment variables.
"""
from __future__ import annotations

import secrets
import sys
from pathlib import Path

import httpx
from dotenv import dotenv_values

REPO = Path(__file__).resolve().parent.parent
ENV_FILE = REPO / ".env"
env = {k: v for k, v in dotenv_values(ENV_FILE).items() if v}
token, dash = env.get("TELEGRAM_BOT_TOKEN"), (env.get("DASHBOARD_URL") or "").rstrip("/")
if not token or not dash:
    sys.exit("Add TELEGRAM_BOT_TOKEN and DASHBOARD_URL to .env first.")

secret = env.get("TELEGRAM_WEBHOOK_SECRET")
if not secret:
    secret = secrets.token_urlsafe(32)
    with ENV_FILE.open("a", encoding="utf-8") as f:
        f.write(f"TELEGRAM_WEBHOOK_SECRET={secret}\n")
    print("generated TELEGRAM_WEBHOOK_SECRET (saved to .env) — add it to Vercel too")

api = f"https://api.telegram.org/bot{token}"
me = httpx.get(f"{api}/getMe", timeout=20).json()
if not me.get("ok"):
    sys.exit(f"Telegram rejected the token: {me}")
r = httpx.post(f"{api}/setWebhook", timeout=20, json={
    "url": f"{dash}/api/telegram/webhook", "secret_token": secret,
    "allowed_updates": ["message", "edited_message"], "drop_pending_updates": False}).json()
print("setWebhook:", r)
httpx.post(f"{api}/setMyDescription", timeout=20, json={
    "description": "Thistle & Pine customer care. Ask about products, delivery, returns or your order."})
httpx.post(f"{api}/setMyCommands", timeout=20, json={"commands": [{"command": "start", "description": "Say hello"}]})
print(f"Bot @{me['result']['username']} -> {dash}/api/telegram/webhook")
print(httpx.get(f"{api}/getWebhookInfo", timeout=20).json())
