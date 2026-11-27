"use client";
// Daily management report: the same email the manager gets every morning, previewed here, plus "send now".
import { useCallback, useEffect, useState } from "react";
import Shell, { useUser } from "@/components/Shell";
import { api, post } from "@/lib/api";

interface Report { html: string; text: string; manager_email_set: boolean; data: { date: string; date_label: string } }

// The shop is in the UK, so "today" is the UK date (the backend's REPORT_TIMEZONE), not the viewer's.
const SHOP_TZ = "Europe/London";
function isoDay(offset: number) {
  const ukToday = new Intl.DateTimeFormat("en-CA", { timeZone: SHOP_TZ }).format(new Date()); // YYYY-MM-DD
  const d = new Date(`${ukToday}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + offset);
  return d.toISOString().slice(0, 10);
}

function Reports() {
  const user = useUser();
  // Default to today so far (UK time), so a manual "Send now" always shows current numbers.
  // The automatic 8am report still covers yesterday, the last complete day.
  const [day, setDay] = useState(isoDay(0));
  const [r, setR] = useState<Report | null>(null);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    setError("");
    api<Report>(`/api/reports/daily?date=${day}`).then(setR).catch((e) => setError(e.message));
  }, [day]);
  useEffect(load, [load]);

  async function sendNow() {
    setBusy(true); setNote(""); setError("");
    try {
      const res = await post<{ emailed_to: string | null; slack: boolean }>("/api/reports/daily/send", { date: day });
      setNote(`Queued${res.emailed_to ? ` for ${res.emailed_to}` : ""}${res.slack ? " and posted to Slack" : ""}. ` +
        "The email arrives within a minute.");
    } catch (e) { setError(e instanceof Error ? e.message : "Failed"); }
    setBusy(false);
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Daily report</h1>
          <div className="muted">Emailed to the manager every morning at 8am UK time (and summarised in Slack): yesterday's numbers and what needs attention now. Shown here: today so far (UK date). Pick a date to see another day.</div>
        </div>
        <div className="row">
          <input className="input" type="date" value={day} max={isoDay(0)} onChange={(e) => setDay(e.target.value)} />
          {user?.role === "admin" && <button className="btn primary" disabled={busy} onClick={sendNow}>Send now</button>}
        </div>
      </div>
      {error && <div className="error">{error}</div>}
      {note && <div className="faint" style={{ color: "var(--good-ink)", marginBottom: 10 }}>✓ {note}</div>}
      {r && !r.manager_email_set && <div className="faint" style={{ marginBottom: 10 }}>MANAGER_EMAIL isn't set on the server, so the report only goes to Slack.</div>}
      {r && (
        <div className="card" style={{ background: "#fff", padding: 20, maxWidth: 760 }}>
          <div dangerouslySetInnerHTML={{ __html: r.html }} />
        </div>
      )}
    </>
  );
}

export default function Page() { return <Shell><Reports /></Shell>; }
