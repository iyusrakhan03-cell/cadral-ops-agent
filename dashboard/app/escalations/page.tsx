"use client";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import Shell from "@/components/Shell";
import { api, Escalation, post } from "@/lib/api";
import { timeAgo } from "@/lib/format";
import { useLive } from "@/lib/live";

const SEV: Record<string, { color: string; icon: string }> = {
  critical: { color: "var(--critical)", icon: "⛔" }, high: { color: "var(--serious)", icon: "▲" },
  medium: { color: "var(--warning)", icon: "●" }, low: { color: "var(--text-3)", icon: "○" },
};

function Escalations() {
  const [status, setStatus] = useState("open");
  const [rows, setRows] = useState<Escalation[]>([]);
  const [error, setError] = useState("");
  const load = useCallback(() => {
    api<Escalation[]>(`/api/escalations?status=${status}`).then(setRows).catch((e) => setError(e.message));
  }, [status]);
  useEffect(load, [load]);
  useLive((e) => { if (e.type === "done" || e.type === "escalation_update" || e.type === "refund_decision") load(); });

  async function update(id: number, s: string) {
    await post(`/api/escalations/${id}`, { status: s }).catch((e) => setError(e.message));
    load();
  }

  return (
    <>
      <div className="page-head">
        <div><h1>Escalations</h1><div className="muted">Chats the AI handed to a person, most urgent first.</div></div>
        <div className="tabs">
          {["open", "resolved"].map((s) => (
            <button key={s} className={`tab ${status === s ? "active" : ""}`} onClick={() => setStatus(s)}>{s}</button>
          ))}
        </div>
      </div>
      {error && <div className="error">{error}</div>}
      <div className="stack">
        {rows.length === 0 && <div className="card empty">Nothing {status}.</div>}
        {rows.map((e) => (
          <div key={e.id} className="card card-pad">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <div className="row">
                <span className="pill"><span style={{ color: SEV[e.severity]?.color }}>{SEV[e.severity]?.icon}</span>{e.severity}</span>
                <span className="pill">{e.category.replace("_", " ")}</span>
                <strong>{e.customer?.name || "Unknown customer"}</strong>
                <span className="faint">{timeAgo(e.created_at)}</span>
              </div>
              <div className="row">
                <Link className="btn sm" href={`/conversations?id=${e.conversation_id}`}>Open chat</Link>
                {e.status === "open"
                  ? <button className="btn sm primary" onClick={() => update(e.id, "resolved")}>Mark resolved</button>
                  : <button className="btn sm" onClick={() => update(e.id, "open")}>Reopen</button>}
              </div>
            </div>
            <div style={{ marginTop: 8 }}>{e.summary}</div>
            {e.customer_wants && <div className="faint" style={{ marginTop: 4 }}>Customer wants: {e.customer_wants}</div>}
            {e.suggested_action && <div className="faint">Suggested next step: {e.suggested_action}</div>}
          </div>
        ))}
      </div>
    </>
  );
}

export default function Page() { return <Shell><Escalations /></Shell>; }
