"use client";
// "Talk to the AI": staff chat with the agents as a customer would, inside the dashboard, and watch what happens
// behind each reply: the router's decision, every tool call as it runs, the reply word by word, then the full trace
// (cost, time, sources) with the usual approve / edit / reject buttons.
import { useCallback, useEffect, useRef, useState } from "react";
import MessageCard from "@/components/MessageCard";
import Shell from "@/components/Shell";
import { api, ConversationDetail, LiveEvent, postStream } from "@/lib/api";
import { AGENT_LABEL, SOURCE_LABEL } from "@/lib/format";
import { useLive } from "@/lib/live";

interface Demo { name: string; email: string }
interface LiveTurn { customer: string; route?: any; tools: any[]; text: string; agent?: string; draft?: string }

const KEY = "tp_playground";

const EXAMPLES: { label: string; agent: string; text: string; as?: string }[] = [
  { label: "Delivery question", agent: "Support agent", text: "Do you deliver to Northern Ireland, and how much is it?" },
  { label: "Product question", agent: "Support agent", text: "Is the oak bedside table solid wood?" },
  { label: "Where is my order", agent: "Orders agent", text: "Where is my order TP-100482?", as: "emma.clarke@example.com" },
  { label: "Damaged → refund", agent: "Orders agent", text: "My order TP-100482 arrived damaged. Can I get a refund?", as: "emma.clarke@example.com" },
  { label: "Angry, wants a manager", agent: "Escalation agent", text: "This is the third time I'm asking. I want to speak to a manager now." },
  { label: "Not in the documents", agent: "Says “I don’t know”", text: "Do you sell garden sheds?" },
];

function Playground() {
  const [demo, setDemo] = useState<Demo[]>([]);
  const [email, setEmail] = useState("");
  const [convId, setConvId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ConversationDetail | null>(null);
  const [live, setLive] = useState<LiveTurn | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const convRef = useRef<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api<Demo[]>("/api/demo-customers").then(setDemo).catch(() => {});
    try {
      const saved = JSON.parse(localStorage.getItem(KEY) || "{}");
      if (saved.email) setEmail(saved.email);
      if (saved.convId) setConvId(saved.convId);
    } catch { /* ignore */ }
  }, []);
  useEffect(() => {
    convRef.current = convId;
    try { localStorage.setItem(KEY, JSON.stringify({ email, convId })); } catch { /* ignore */ }
  }, [email, convId]);

  const load = useCallback((id: string | null = convRef.current) => {
    if (!id) { setDetail(null); return; }
    api<ConversationDetail>(`/api/conversations/${id}`).then(setDetail)
      .catch(() => { setConvId(null); setDetail(null); });
  }, []);
  useEffect(() => { load(convId); }, [convId, load]);
  useEffect(() => { bottom.current?.scrollIntoView({ block: "end" }); }, [detail?.messages.length, live?.text, live?.tools.length, live?.route]);

  // Behind-the-scenes events (routing, tool calls, drafts) for this chat come from the staff live feed.
  useLive((e: LiveEvent) => {
    if (!convRef.current || e.conversation_id !== convRef.current) return;
    if (e.type === "route") setLive((t) => t && { ...t, route: e });
    else if (e.type === "tool_call") setLive((t) => t && { ...t, tools: [...t.tools, e] });
    else if (e.type === "draft") setLive((t) => t && { ...t, draft: e.text, agent: e.agent });
    else if (["feedback", "status", "human_reply", "refund_decision", "escalation_update"].includes(e.type)) load();
  });

  function newChat(nextEmail = email) {
    convRef.current = null;
    setEmail(nextEmail); setConvId(null); setDetail(null); setLive(null); setError("");
  }

  async function send(message = text.trim(), asEmail = email) {
    if (!message || busy) return;
    setText(""); setBusy(true); setError("");
    setLive({ customer: message, tools: [], text: "" });
    let id = convRef.current;
    try {
      await postStream("/api/chat", { message, conversation_id: id, customer_email: asEmail || null }, (e) => {
        if (e.type === "start") { id = e.conversation_id; convRef.current = id; setConvId(id); }
        if (e.type === "token") setLive((t) => t && { ...t, text: t.text + e.text, agent: e.agent ?? t.agent });
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't send");
    } finally {
      setBusy(false);
      setLive(null);
      load(id);
    }
  }

  function tryExample(ex: (typeof EXAMPLES)[number]) {
    const as = ex.as ?? "";
    if (as !== email) newChat(as);       // a different customer means a new conversation
    send(ex.text, as);
  }

  const lastSaved = detail?.messages[detail.messages.length - 1];
  const showLiveCustomer = live && !(lastSaved?.role === "customer" && lastSaved.content === live.customer);
  const agentsUsed = Array.from(new Set((detail?.messages || []).map((m) => m.agent).filter(Boolean))) as string[];

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Talk to the AI</h1>
          <div className="muted">Chat with the agents yourself and see which one answers, why, and every tool it uses.
            These chats also appear in Live conversations.</div>
        </div>
      </div>

      <div className="split">
        <div className="card card-pad stack">
          <div>
            <h2 style={{ marginBottom: 4 }}>Chat as</h2>
            <select className="select" value={email} onChange={(e) => newChat(e.target.value)}>
              <option value="">Guest (not signed in)</option>
              {demo.map((d) => <option key={d.email} value={d.email}>{d.name} ({d.email})</option>)}
            </select>
            <div className="faint" style={{ marginTop: 4 }}>A signed-in customer lets the Orders agent look up their orders.</div>
          </div>
          <button className="btn" onClick={() => newChat()}>Start a new chat</button>

          <div>
            <h2 style={{ marginBottom: 4 }}>Try one</h2>
            <div className="stack" style={{ gap: 6 }}>
              {EXAMPLES.map((ex) => (
                <button key={ex.label} className="btn sm" style={{ display: "flex", width: "100%", gap: 8, justifyContent: "space-between", textAlign: "left" }}
                        disabled={busy} onClick={() => tryExample(ex)}>
                  <span>{ex.label}</span><span className="faint">{ex.agent}</span>
                </button>
              ))}
            </div>
          </div>

          <div>
            <h2 style={{ marginBottom: 4 }}>The agents</h2>
            <div className="faint" style={{ lineHeight: 1.6 }}>
              <div><strong>Router</strong>: the fine-tuned model reads each message and picks an agent.</div>
              <div><strong>Support agent</strong>: answers from the shop’s documents, with sources.</div>
              <div><strong>Orders agent</strong>: looks up, cancels, changes and refunds orders (refunds follow the policy rules).</div>
              <div><strong>Escalation agent</strong>: hands the chat to a human with a summary.</div>
            </div>
          </div>
          {detail && (
            <div className="faint">This chat: #{detail.id} · {detail.messages.length} messages
              {agentsUsed.length > 0 && <> · agents used: {agentsUsed.map((a) => AGENT_LABEL[a] || a).join(", ")}</>}</div>
          )}
        </div>

        <div className="card">
          <div className="thread" style={{ minHeight: 420 }}>
            {!detail?.messages.length && !live && (
              <div className="empty">Type a message below, or pick one on the left.</div>
            )}
            {detail?.messages.map((m) => (
              <MessageCard key={m.id} m={m} trace={m.trace_id ? detail.traces[String(m.trace_id)] : undefined}
                           feedback={detail.feedback[String(m.id)]} onChanged={() => load()} />
            ))}
            {live && (
              <>
                {showLiveCustomer && (
                  <div className="msg customer"><div className="msg-meta"><strong>You (as customer)</strong> now</div>
                    <div className="bubble">{live.customer}</div></div>
                )}
                <div className="msg assistant">
                  <div className="live-steps">
                    {!live.route && <span><span className="live-dot pulse" /> Router is reading the message…</span>}
                    {live.route && (
                      <span>↪ Routed to <strong>{AGENT_LABEL[live.route.agent] || live.route.agent}</strong> by{" "}
                        {SOURCE_LABEL[live.route.source] || live.route.source}
                        {live.route.label ? ` (${String(live.route.label).replace(/_/g, " ")})` : ""}</span>
                    )}
                    {live.tools.map((t, i) => (
                      <span key={i}>🔧 {t.tool}{t.why ? ` — “${t.why}”` : ""}{t.result?.decision ? ` → ${t.result.decision}` : ""}</span>
                    ))}
                  </div>
                  {(live.text || live.draft) && (
                    <>
                      <div className="msg-meta"><strong style={{ color: "var(--text-2)" }}>{AGENT_LABEL[live.agent || ""] || "AI"}</strong>
                        {live.draft ? <span className="pill">⏳ held for review</span> : <span>typing…</span>}</div>
                      <div className={`bubble ${live.draft ? "" : "cursor"}`}>{live.draft || live.text}</div>
                    </>
                  )}
                </div>
              </>
            )}
            {error && <div className="error">{error}</div>}
            <div ref={bottom} />
          </div>
          <div className="composer">
            <textarea className="textarea" placeholder="Write as the customer… (Enter to send, Shift+Enter for a new line)"
                      value={text} onChange={(e) => setText(e.target.value)}
                      onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} />
            <button className="btn primary" disabled={busy || !text.trim()} onClick={() => send()}>Send</button>
          </div>
        </div>
      </div>
    </>
  );
}

export default function PlaygroundPage() {
  return <Shell><Playground /></Shell>;
}
