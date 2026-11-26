"use client";
// Live conversations: list on the left, the chosen chat on the right. While the AI works on a message you see
// the routing decision, each tool call as it happens, and the reply appearing word by word.
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import MessageCard from "@/components/MessageCard";
import Shell from "@/components/Shell";
import { api, ConversationDetail, ConversationSummary, LiveEvent, post } from "@/lib/api";
import { AGENT_LABEL, SOURCE_LABEL, STATUS_LABEL, timeAgo } from "@/lib/format";
import { useLive } from "@/lib/live";

interface LiveTurn { customer?: string; route?: any; tools: any[]; text: string; agent?: string; draft?: string }

const FILTERS: [string, string][] = [
  ["", "All"], ["pending_review", "Waiting for review"], ["needs_human", "Needs a human"], ["open", "Open"], ["resolved", "Resolved"],
];

function StatusPill({ status }: { status: string }) {
  const color = status === "needs_human" ? "var(--critical)" : status === "resolved" ? "var(--good)" : "var(--series-1)";
  return <span className="pill"><span className="dot" style={{ background: color }} />{STATUS_LABEL[status] || status}</span>;
}

function useDebounced(fn: () => void, ms: number) {
  const t = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  return useCallback(() => { clearTimeout(t.current); t.current = setTimeout(fn, ms); }, [fn, ms]);
}

function Conversations() {
  const router = useRouter();
  const params = useSearchParams();
  const selected = params.get("id");
  const [filter, setFilter] = useState("");
  const [q, setQ] = useState("");
  const [list, setList] = useState<ConversationSummary[]>([]);
  const [detail, setDetail] = useState<ConversationDetail | null>(null);
  const [liveTurns, setLiveTurns] = useState<Record<string, LiveTurn>>({});
  const [error, setError] = useState("");
  const [reply, setReply] = useState("");
  const [busy, setBusy] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);

  const loadList = useCallback(() => {
    const qs = new URLSearchParams();
    if (filter) qs.set("status", filter);
    if (q) qs.set("q", q);
    api<ConversationSummary[]>(`/api/conversations?${qs}`).then((r) => { setList(r); setError(""); })
      .catch((e) => setError(e.message));
  }, [filter, q]);
  const loadDetail = useCallback(() => {
    if (!selected) { setDetail(null); return; }
    api<ConversationDetail>(`/api/conversations/${selected}`).then(setDetail).catch((e) => setError(e.message));
  }, [selected]);
  const refreshList = useDebounced(loadList, 300);
  const refreshDetail = useDebounced(loadDetail, 300);

  useEffect(() => { loadList(); }, [loadList]);
  useEffect(() => { loadDetail(); }, [loadDetail]);

  useLive((e: LiveEvent) => {
    const id = e.conversation_id;
    if (!id) return;
    setLiveTurns((prev) => {
      const cur = prev[id] || { tools: [], text: "" };
      switch (e.type) {
        case "customer_message": return { ...prev, [id]: { customer: e.text, tools: [], text: "" } };
        case "route": return { ...prev, [id]: { ...cur, route: e } };
        case "tool_call": return { ...prev, [id]: { ...cur, tools: [...cur.tools, e] } };
        case "token": return { ...prev, [id]: { ...cur, text: cur.text + e.text, agent: e.agent } };
        case "draft": return { ...prev, [id]: { ...cur, draft: e.text, agent: e.agent } };
        case "done": { const n = { ...prev }; delete n[id]; return n; }
        default: return prev;
      }
    });
    if (["customer_message", "done", "feedback", "status", "human_reply", "refund_decision", "escalation_update"].includes(e.type)) {
      refreshList();
      if (id === selected) refreshDetail();
    }
  });

  const live = selected ? liveTurns[selected] : undefined;
  useEffect(() => { bottom.current?.scrollIntoView({ block: "end" }); }, [detail?.messages.length, live?.text, live?.tools.length]);

  async function sendReply() {
    if (!selected || !reply.trim()) return;
    setBusy(true);
    try { await post(`/api/conversations/${selected}/reply`, { content: reply }); setReply(""); loadDetail(); }
    catch (e) { setError(e instanceof Error ? e.message : "Failed"); }
    finally { setBusy(false); }
  }
  async function setStatus(status: string) {
    if (!selected) return;
    await post(`/api/conversations/${selected}/status`, { status }).catch((e) => setError(e.message));
    loadDetail(); loadList();
  }

  // Avoid showing the customer's message twice once the saved copy has loaded.
  const lastSaved = detail?.messages[detail.messages.length - 1];
  const showLiveCustomer = live?.customer && !(lastSaved?.role === "customer" && lastSaved.content === live.customer);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Live conversations</h1>
          <div className="muted">Every chat, as it happens. Click one to see which agent handled it and which tools it used.</div>
        </div>
        <input className="input" style={{ maxWidth: 260 }} placeholder="Search name, email, text…" value={q}
               onChange={(e) => setQ(e.target.value)} />
      </div>
      <div className="tabs" style={{ marginBottom: 12 }}>
        {FILTERS.map(([v, label]) => (
          <button key={v} className={`tab ${filter === v ? "active" : ""}`} onClick={() => setFilter(v)}>{label}</button>
        ))}
      </div>
      {error && <div className="error" style={{ marginBottom: 10 }}>{error}</div>}

      <div className="split">
        <div className="card conv-list">
          {list.length === 0 && <div className="empty">No conversations here.</div>}
          {list.map((c) => {
            const typing = !!liveTurns[c.id];
            return (
              <button key={c.id} className={`conv-item ${c.id === selected ? "active" : ""}`}
                      onClick={() => router.push(`/conversations?id=${c.id}`)}>
                <div className="top">
                  <strong>{c.customer?.name || "Unknown visitor"}</strong>
                  <span className="faint">{typing ? <><span className="live-dot pulse" /> live</> : timeAgo(c.updated_at)}</span>
                </div>
                <div className="preview">{c.last_message?.role === "customer" ? "" : "↳ "}{c.last_message?.content}</div>
                <div className="row" style={{ marginTop: 6, gap: 5 }}>
                  <StatusPill status={c.status} />
                  {c.agents.map((a) => <span key={a} className="pill">{a}</span>)}
                  {c.pending_review > 0 && <span className="pill" style={{ color: "var(--critical-ink)" }}>⏳ {c.pending_review} to review</span>}
                  <span className="pill">{c.channel}</span>
                </div>
              </button>
            );
          })}
        </div>

        <div className="card">
          {!selected && <div className="empty">Pick a conversation on the left.</div>}
          {selected && detail && (
            <>
              <div className="card-pad" style={{ borderBottom: "1px solid var(--border)" }}>
                <div className="row" style={{ justifyContent: "space-between" }}>
                  <div>
                    <h2 style={{ marginBottom: 2 }}>{detail.customer?.name || "Unknown visitor"}</h2>
                    <div className="faint">{detail.customer?.email || "not signed in"} · {detail.channel} · #{detail.id}</div>
                  </div>
                  <div className="row">
                    <StatusPill status={detail.status} />
                    {detail.ai_solved === true && <span className="pill" style={{ color: "var(--good-ink)" }}>solved by AI alone</span>}
                    {detail.status !== "needs_human" && <button className="btn sm" onClick={() => setStatus("needs_human")}>Take over</button>}
                    {detail.status === "needs_human" && <button className="btn sm" onClick={() => setStatus("open")}>Hand back to AI</button>}
                    {detail.status !== "resolved" && <button className="btn sm" onClick={() => setStatus("resolved")}>Mark resolved</button>}
                  </div>
                </div>
                {detail.escalations.map((e) => (
                  <div key={e.id} className="faint" style={{ marginTop: 8 }}>
                    🚨 Escalation #{e.id} · <strong>{e.severity}</strong> · {e.category} · {e.status} — {e.summary}
                  </div>
                ))}
              </div>
              <div className="thread">
                {detail.messages.map((m) => (
                  <MessageCard key={m.id} m={m} trace={m.trace_id ? detail.traces[String(m.trace_id)] : undefined}
                               feedback={detail.feedback[String(m.id)]} onChanged={() => { loadDetail(); loadList(); }} />
                ))}
                {live && (
                  <>
                    {showLiveCustomer && (
                      <div className="msg customer"><div className="msg-meta"><strong>Customer</strong> now</div>
                        <div className="bubble">{live.customer}</div></div>
                    )}
                    <div className="msg assistant">
                      <div className="live-steps">
                        {!live.route && <span><span className="live-dot pulse" /> Router is reading the message…</span>}
                        {live.route && (
                          <span>↪ Routed to <strong>{AGENT_LABEL[live.route.agent] || live.route.agent}</strong> by {SOURCE_LABEL[live.route.source] || live.route.source} ({live.route.label?.replace("_", " ")})</span>
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
                <div ref={bottom} />
              </div>
              <div className="composer">
                <textarea className="textarea" placeholder="Reply to the customer as a human…" value={reply}
                          onChange={(e) => setReply(e.target.value)}
                          onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) sendReply(); }} />
                <button className="btn primary" disabled={busy || !reply.trim()} onClick={sendReply}>Send</button>
              </div>
            </>
          )}
          {selected && !detail && !error && <div className="empty">Loading…</div>}
        </div>
      </div>
    </>
  );
}

export default function ConversationsPage() {
  return <Shell><Suspense fallback={<div className="muted">Loading…</div>}><Conversations /></Suspense></Shell>;
}
