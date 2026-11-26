"use client";
import { useEffect, useState } from "react";
import Shell from "@/components/Shell";
import { api, put } from "@/lib/api";

interface S { review_mode: string; review_modes: Record<string, string> }

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
    </>
  );
}

export default function Page() { return <Shell adminOnly><Settings /></Shell>; }
