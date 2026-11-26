"use client";
// Customer web-chat widget (channel #1). No staff login. The "chat as" picker stands in for a signed-in shop account.
import { useEffect, useRef, useState } from "react";
import { api, postStream } from "@/lib/api";

interface Msg { id: string | number; role: "customer" | "assistant" | "human"; content: string; streaming?: boolean; note?: string }
interface Demo { name: string; email: string }

const KEY = "tp_chat";

export default function ChatPage() {
  const [demo, setDemo] = useState<Demo[]>([]);
  const [email, setEmail] = useState("");
  const [convId, setConvId] = useState<string | null>(null);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [waiting, setWaiting] = useState(false);   // a reply is held for review, or a human has taken over
  const [error, setError] = useState("");
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api<Demo[]>("/api/demo-customers").then(setDemo).catch(() => {});
    try {
      const saved = JSON.parse(localStorage.getItem(KEY) || "{}");
      if (saved.email) setEmail(saved.email);
      if (saved.convId) setConvId(saved.convId);
    } catch { /* ignore */ }
  }, []);
  useEffect(() => { try { localStorage.setItem(KEY, JSON.stringify({ email, convId })); } catch { /* ignore */ } }, [email, convId]);
  useEffect(() => { bottom.current?.scrollIntoView({ block: "end" }); }, [msgs]);

  // Load the conversation, and keep polling while a human is involved so their replies show up.
  useEffect(() => {
    if (!convId || busy) return;
    let stop = false;
    const load = () => api<any>(`/api/chat/${convId}`).then((r) => {
      if (stop) return;
      setMsgs(r.messages.map((m: any) => ({ id: m.id, role: m.role, content: m.content })));
      setWaiting(r.waiting_for_review || r.status === "needs_human");
    }).catch(() => { if (!stop) { setConvId(null); setMsgs([]); } });
    load();
    const t = setInterval(load, 4000);
    return () => { stop = true; clearInterval(t); };
  }, [convId, busy]);

  function newChat(nextEmail = email) {
    setEmail(nextEmail); setConvId(null); setMsgs([]); setWaiting(false); setError("");
  }

  async function send() {
    const message = text.trim();
    if (!message || busy) return;
    setText(""); setBusy(true); setError("");
    const tmp = `live-${Date.now()}`;
    setMsgs((m) => [...m, { id: `c-${tmp}`, role: "customer", content: message }, { id: tmp, role: "assistant", content: "", streaming: true }]);
    const patch = (fn: (m: Msg) => Msg) => setMsgs((all) => all.map((m) => (m.id === tmp ? fn(m) : m)));
    try {
      await postStream("/api/chat", { message, conversation_id: convId, customer_email: email || null }, (e) => {
        if (e.type === "start") setConvId(e.conversation_id);
        if (e.type === "token") patch((m) => ({ ...m, content: m.content + e.text }));
        if (e.type === "done") {
          if (e.held_for_review) patch((m) => ({ ...m, content: "", note: "A member of our team is checking your answer. It will appear here shortly.", streaming: false }));
          else patch((m) => ({ ...m, content: e.reply ?? m.content, streaming: false }));
          setWaiting(!!e.held_for_review || e.status === "needs_human");
        }
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't send. Please try again.");
      patch((m) => ({ ...m, streaming: false, note: "Not sent." }));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="center-page">
      <div className="card widget">
        <div className="widget-head">
          <strong>Thistle &amp; Pine</strong>
          <div style={{ fontSize: 12, opacity: .85 }}>Customer care · usually replies in seconds</div>
        </div>
        <div className="row" style={{ padding: "8px 12px", borderBottom: "1px solid var(--border)", fontSize: 12 }}>
          <span className="faint">Chat as</span>
          <select className="select" style={{ flex: 1, padding: "4px 8px" }} value={email} onChange={(e) => newChat(e.target.value)}>
            <option value="">Guest (not signed in)</option>
            {demo.map((d) => <option key={d.email} value={d.email}>{d.name} ({d.email})</option>)}
          </select>
          <button className="btn sm" onClick={() => newChat()}>New chat</button>
        </div>
        <div className="widget-body">
          {msgs.length === 0 && <div className="empty">Hi! Ask about an order, delivery, returns or our products.</div>}
          {msgs.map((m) => (
            <div key={m.id} className={`msg ${m.role}`}>
              {m.role === "human" && <div className="msg-meta" style={{ justifyContent: "flex-start" }}>Customer care team</div>}
              {m.note && !m.content ? <div className="bubble faint">{m.note}</div>
                : <div className={`bubble ${m.streaming ? "cursor" : ""}`}>{m.content || (m.streaming ? "" : "…")}</div>}
            </div>
          ))}
          {waiting && !busy && <div className="faint" style={{ textAlign: "center" }}>A member of our team is looking at this conversation.</div>}
          {error && <div className="error">{error}</div>}
          <div ref={bottom} />
        </div>
        <div className="composer">
          <textarea className="textarea" placeholder="Type your message…" value={text} onChange={(e) => setText(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} />
          <button className="btn primary" disabled={busy || !text.trim()} onClick={send}>Send</button>
        </div>
      </div>
    </div>
  );
}
