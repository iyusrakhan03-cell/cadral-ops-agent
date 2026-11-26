"use client";
import { useCallback, useEffect, useState } from "react";
import Shell from "@/components/Shell";
import { api, post, Refund } from "@/lib/api";
import { gbp, timeAgo } from "@/lib/format";
import { useLive } from "@/lib/live";

function Refunds() {
  const [status, setStatus] = useState("pending_approval");
  const [rows, setRows] = useState<Refund[]>([]);
  const [notes, setNotes] = useState<Record<number, string>>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<number | null>(null);
  const load = useCallback(() => {
    api<Refund[]>(`/api/refunds?status=${status}`).then(setRows).catch((e) => setError(e.message));
  }, [status]);
  useEffect(load, [load]);
  useLive((e) => { if (e.type === "done" || e.type === "refund_decision") load(); });

  async function decide(id: number, decision: "approve" | "decline") {
    setBusy(id);
    setError("");
    try { await post(`/api/refunds/${id}/decide`, { decision, note: notes[id] || null }); load(); }
    catch (e) { setError(e instanceof Error ? e.message : "Failed"); }
    finally { setBusy(null); }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Refund approvals</h1>
          <div className="muted">The policy engine sends big or unusual refunds here. The AI never decides these. The customer is told in their chat.</div>
        </div>
        <div className="tabs">
          {[["pending_approval", "Waiting"], ["issued", "Issued"], ["rejected", "Declined by staff"], ["declined", "Declined by policy"]].map(([v, l]) => (
            <button key={v} className={`tab ${status === v ? "active" : ""}`} onClick={() => setStatus(v)}>{l}</button>
          ))}
        </div>
      </div>
      {error && <div className="error" style={{ marginBottom: 10 }}>{error}</div>}
      <div className="stack">
        {rows.length === 0 && <div className="card empty">Nothing here.</div>}
        {rows.map((r) => (
          <div key={r.id} className="card card-pad">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <div className="row">
                <strong style={{ fontSize: 18 }}>{gbp(r.amount)}</strong>
                <span className="pill">{r.order_id}</span>
                <span>{r.customer?.name}</span>
                <span className="faint">{timeAgo(r.created_at)} · requested by {r.created_by}</span>
              </div>
              {r.decided_by && <span className="faint">decided by {r.decided_by}</span>}
            </div>
            <div style={{ marginTop: 6 }}>{r.reason}</div>
            <div className="faint" style={{ marginTop: 4 }}>
              Order total {gbp(r.order_total)} · {r.order_status} · {r.items.map((i) => `${i.qty}× ${i.name}`).join(", ")}
            </div>
            {r.status === "pending_approval" && (
              <div className="row" style={{ marginTop: 10 }}>
                <input className="input" style={{ flex: 1, minWidth: 200 }} placeholder="Note for the audit log (optional)"
                       value={notes[r.id] || ""} onChange={(e) => setNotes({ ...notes, [r.id]: e.target.value })} />
                <button className="btn bad" disabled={busy === r.id} onClick={() => decide(r.id, "decline")}>Decline</button>
                <button className="btn primary" disabled={busy === r.id} onClick={() => decide(r.id, "approve")}>Approve {gbp(r.amount)}</button>
              </div>
            )}
          </div>
        ))}
      </div>
    </>
  );
}

export default function Page() { return <Shell adminOnly><Refunds /></Shell>; }
