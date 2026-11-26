// Our AI → Telegram. The backend's delivery worker calls this (with the shared BRIDGE_KEY) to send a reply,
// because free Hugging Face Spaces can't reach api.telegram.org. A non-2xx answer makes the backend retry.
import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  const key = process.env.BRIDGE_KEY;
  if (!key || req.headers.get("x-bridge-key") !== key) {
    return NextResponse.json({ error: "forbidden" }, { status: 403 });
  }
  const token = process.env.TELEGRAM_BOT_TOKEN;
  if (!token) return NextResponse.json({ error: "TELEGRAM_BOT_TOKEN not set on Vercel" }, { status: 500 });

  const { chat_id, text } = await req.json().catch(() => ({}));
  if (!chat_id || !text) return NextResponse.json({ error: "chat_id and text required" }, { status: 422 });

  const r = await fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id, text: String(text).slice(0, 4096) }),
  });
  const body = await r.json().catch(() => ({}));
  return NextResponse.json(body, { status: r.ok ? 200 : 502 });
}
