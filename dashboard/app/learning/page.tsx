"use client";
// The self-improving loop, made visible: every human judgement and whether the AI is reusing it.
import Link from "next/link";
import { useEffect, useState } from "react";
import Shell, { useUser } from "@/components/Shell";
import { api, API_URL, getToken } from "@/lib/api";
import { timeAgo } from "@/lib/format";

interface Fb {
  id: number; conversation_id: string; agent: string | null; action: string; question: string; ai_reply: string;
  final_reply: string | null; reason: string | null; by: string; times_used: number; used_as_example: boolean; created_at: string;
}

function Learning() {
  const user = useUser();
  const [rows, setRows] = useState<Fb[]>([]);
  const [error, setError] = useState("");
  useEffect(() => { api<Fb[]>("/api/feedback").then(setRows).catch((e) => setError(e.message)); }, []);

  async function download() {
    const res = await fetch(`${API_URL}/api/feedback/export`, { headers: { Authorization: `Bearer ${getToken()}` } });
    if (!res.ok) { setError(`Export failed (${res.status})`); return; }
    const url = URL.createObjectURL(await res.blob());
    const a = document.createElement("a");
    a.href = url; a.download = "feedback-dataset.jsonl"; a.click();
    URL.revokeObjectURL(url);
  }

  const corrections = rows.filter((r) => r.action !== "approve");
  return (
    <>
      <div className="page-head">
        <div>
          <h1>What the AI learned</h1>
          <div className="muted">
            When you edit or reject a reply (and write the right answer), the Support agent sees your version as an example the next time a customer asks something similar.
          </div>
        </div>
        {user?.role === "admin" && <button className="btn" onClick={download}>Download re-training dataset (.jsonl)</button>}
      </div>
      {error && <div className="error">{error}</div>}
      <div className="kpis">
        <div className="card card-pad kpi"><div className="label">Replies reviewed</div><div className="value">{rows.length}</div></div>
        <div className="card card-pad kpi"><div className="label">Corrections saved</div><div className="value">{corrections.length}</div></div>
        <div className="card card-pad kpi"><div className="label">Times corrections were reused</div>
          <div className="value">{rows.reduce((n, r) => n + r.times_used, 0)}</div></div>
      </div>
      <div className="stack">
        {rows.length === 0 && <div className="card empty">No reviews yet. Approve, edit or reject an AI reply in Live conversations.</div>}
        {rows.map((r) => (
          <div key={r.id} className="card card-pad">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <div className="row">
                <span className="pill">{r.action === "approve" ? "✓ approved" : r.action === "edit" ? "✏️ edited" : "✕ rejected"}</span>
                <span className="pill">{r.agent} agent</span>
                {r.used_as_example && <span className="pill" style={{ color: "var(--good-ink)" }}>used for learning · reused {r.times_used}×</span>}
                <span className="faint">{r.by.replace("human:", "")} · {timeAgo(r.created_at)}</span>
              </div>
              <Link className="btn sm" href={`/conversations?id=${r.conversation_id}`}>Open chat</Link>
            </div>
            <div style={{ marginTop: 8 }}><span className="faint">Customer asked:</span> {r.question}</div>
            {r.action !== "approve" && (
              <div className="row" style={{ alignItems: "stretch", marginTop: 8 }}>
                <div className="bubble" style={{ flex: 1, minWidth: 220, background: "var(--surface-2)", textDecoration: "line-through", opacity: .7 }}>{r.ai_reply}</div>
                {r.final_reply && <div className="bubble" style={{ flex: 1, minWidth: 220, background: "var(--human-bubble)" }}>{r.final_reply}</div>}
              </div>
            )}
            {r.reason && <div className="faint" style={{ marginTop: 6 }}>Reason: {r.reason}</div>}
            {r.action !== "approve" && !r.used_as_example && (
              <div className="faint" style={{ marginTop: 4 }}>Saved to the re-training dataset (live examples are used for the Support agent only).</div>
            )}
          </div>
        ))}
      </div>
    </>
  );
}

export default function Page() { return <Shell><Learning /></Shell>; }
