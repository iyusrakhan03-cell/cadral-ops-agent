// Telegram → our AI. Telegram calls this URL for every message to the shop's bot (set up with
// deploy/set_telegram_webhook.py). We forward it to the backend, which saves it first and answers via the agents.
//
// Why here and not on the backend? Free Hugging Face Spaces can't reach api.telegram.org, so Vercel is the bridge
// in both directions (see ../send/route.ts). If the backend is down we return 503 and Telegram retries later.
import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const BACKEND = (process.env.BACKEND_URL || process.env.NEXT_PUBLIC_API_URL || "").replace(/\/$/, "");

async function tg(method: string, body: object) {
  const token = process.env.TELEGRAM_BOT_TOKEN;
  if (!token) return;
  await fetch(`https://api.telegram.org/bot${token}/${method}`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  }).catch(() => undefined);
}

export async function POST(req: NextRequest) {
  const secret = process.env.TELEGRAM_WEBHOOK_SECRET;
  if (!secret || req.headers.get("x-telegram-bot-api-secret-token") !== secret) {
    return NextResponse.json({ error: "forbidden" }, { status: 403 });
  }
  const update = await req.json().catch(() => null);
  const msg = update?.message || update?.edited_message;
  if (!update || !msg?.chat?.id) return NextResponse.json({ ok: true });   // not a customer message: ignore

  const text: string | undefined = msg.text || msg.caption;
  if (!text) {
    await tg("sendMessage", { chat_id: msg.chat.id,
      text: "Sorry, I can only read text messages at the moment. Please type your question." });
    return NextResponse.json({ ok: true });
  }

  await tg("sendChatAction", { chat_id: msg.chat.id, action: "typing" });
  const r = await fetch(`${BACKEND}/api/channels/telegram/inbound`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Bridge-Key": process.env.BRIDGE_KEY || "" },
    body: JSON.stringify({ update_id: update.update_id, chat_id: msg.chat.id, text: text.slice(0, 4096),
      first_name: msg.from?.first_name, last_name: msg.from?.last_name, username: msg.from?.username }),
  }).catch(() => null);
  if (!r || !r.ok) {
    return NextResponse.json({ error: "backend unavailable" }, { status: 503 });   // Telegram will retry
  }
  return NextResponse.json({ ok: true });
}
