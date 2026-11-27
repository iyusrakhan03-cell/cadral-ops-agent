"use client";
import { useEffect, useState } from "react";
import Shell from "@/components/Shell";
import { api, put } from "@/lib/api";

interface S { review_mode: string; review_modes: Record<string, string> }
interface OutRow { id: number; channel: string; kind: string; status: string; attempts: number; target: string | null;
  last_error: string | null; created_at: string; sent_at: string | null }
interface Ch { inbound_waiting: number; inbound_failed: number; outbox_pending: number; outbox_failed: number;
  email_bridge: boolean; email_mode?: string | null; gmail?: { address: string | null; last_error: string | null }; telegram_relay: boolean; slack: boolean; daily_report_to: boolean; recent: OutRow[] }

function Channels() {
  const [c, setC] = useState<Ch | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    const load = () => api<Ch>("/api/channels/status").then(setC).catch((e) => setErr(e.message));
    load();
    const t = setInterval(load, 10000);
    return () => clearInterval(t);
  }, []);
  const on = (b: boolean) => <span className="pill">{b ? "✓ connected" : "not set up"}</span>;
  return (
    <div className="card card-pad stack" style={{ maxWidth: 900, marginTop: 16 }}>
      <h2>Channels &amp; deliveries</h2>
      {err && <div className="error">{err}</div>}
      {c && (<>
        <div className="row" style={{ gap: 16 }}>
          <span>Email ({c.email_mode === "gmail_api" ? `Gmail API${c.gmail?.address ? ` · ${c.gmail.address}` : ""}` : "Gmail bridge"}) {on(c.email_bridge)}</span>
          <span>Telegram {on(c.telegram_relay)}</span>
          <span>Slack alerts {on(c.slack)}</span>
          <span>Daily report email {on(c.daily_report_to)}</span>
        </div>
        {c.gmail?.last_error && <div className="error">Email: {c.gmail.last_error}</div>}
        <div className="faint">
          Waiting to process: {c.inbound_waiting} · waiting to send: {c.outbox_pending} · failed: {c.outbox_failed + c.inbound_failed}.
          Nothing is dropped: messages are saved first and sends are retried (10 s → 1 h) until the channel confirms.
        </div>
        <table className="table">
          <thead><tr><th>#</th><th>Channel</th><th>What</th><th>To</th><th>Status</th><th className="num">Tries</th><th>Last error</th></tr></thead>
          <tbody>
            {c.recent.map((o) => (
              <tr key={o.id}>
                <td>{o.id}</td><td>{o.channel}</td><td>{o.kind}</td><td className="mono">{o.target || ""}</td>
                <td><span className="pill">{o.status}</span></td><td className="num">{o.attempts}</td>
                <td className="faint">{o.last_error || ""}</td>
              </tr>
            ))}
            {!c.recent.length && <tr><td colSpan={7} className="faint">Nothing sent yet.</td></tr>}
          </tbody>
        </table>
      </>)}
    </div>
  );
}

function Settings() {
  const [s, setS] = useState<S | null>(null);
  const [saved, setSaved] = useState("");
  const [error, setError] = useState("");
  useEffect(() => { api<S>("/api/settings").then(setS).catch((e) => setError(e.message)); }, []);

  async function choose(mode: string) {
    setError(""); setSaved("");
    try { await put("/api/settings", { review_mode: mode }); setS((p) => p && { ...p, review_mode: mode }); setSaved("Saved"); }
    catch (e) { setError(e instanceof Error ? e.message : "Failed"); }
  }

  return (
    <>
      <div className="page-head"><div><h1>Settings</h1><div className="muted">Decide how much the AI can send without a human checking it first.</div></div></div>
      {error && <div className="error">{error}</div>}
      {s && (
        <div className="card card-pad stack" style={{ maxWidth: 640 }}>
          <h2>Review mode</h2>
          {Object.entries(s.review_modes).map(([k, label]) => (
            <label key={k} className="row" style={{ cursor: "pointer", alignItems: "flex-start" }}>
              <input type="radio" name="mode" checked={s.review_mode === k} onChange={() => choose(k)} style={{ marginTop: 4 }} />
              <span><strong>{k}</strong><br /><span className="muted">{label}</span></span>
            </label>
          ))}
          <span className="faint">
            In “smart” and “all”, held replies show up under Live conversations → Waiting for review, and the customer sees
            “a team member is checking your answer” until someone approves, edits or rejects it.
          </span>
          {saved && <span className="faint" style={{ color: "var(--good-ink)" }}>✓ {saved}</span>}
        </div>
      )}
      <Channels />
    </>
  );
}

export default function Page() { return <Shell adminOnly><Settings /></Shell>; }
