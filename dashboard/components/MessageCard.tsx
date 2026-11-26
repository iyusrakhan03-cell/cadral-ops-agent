"use client";
// One message in the staff view. AI replies get the trace plus Approve / Edit / Reject.
import { useState } from "react";
import { FeedbackInfo, Message, post, Trace } from "@/lib/api";
import { AGENT_LABEL, clock } from "@/lib/format";
import TracePanel from "./TracePanel";

const STATUS_PILL: Record<string, string> = {
  pending_review: "⏳ waiting for review", edited: "✏️ edited by a human", rejected: "✕ rejected",
};

export default function MessageCard({ m, trace, feedback, onChanged }: {
  m: Message; trace?: Trace; feedback?: FeedbackInfo; onChanged: () => void;
}) {
  const [mode, setMode] = useState<"" | "edit" | "reject">("");
  const [text, setText] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [showOriginal, setShowOriginal] = useState(false);

  async function send(action: "approve" | "edit" | "reject") {
    setBusy(true);
    setError("");
    try {
      await post(`/api/messages/${m.id}/feedback`, {
        action, final_reply: action === "approve" ? null : text || null, reason: reason || null,
      });
      setMode("");
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed");
    } finally {
      setBusy(false);
    }
  }

  const who = m.role === "customer" ? "Customer" : m.role === "human" ? "Human agent"
    : AGENT_LABEL[m.agent || ""] || "AI";

  return (
    <div className={`msg ${m.role} ${m.status === "rejected" ? "rejected" : ""}`}>
      <div className="msg-meta">
        <strong style={{ color: "var(--text-2)" }}>{who}</strong>
        <span>{clock(m.created_at)}</span>
        {STATUS_PILL[m.status] && <span className="pill">{STATUS_PILL[m.status]}</span>}
        {m.role === "assistant" && m.confidence != null && <span className="pill">confidence {Math.round(m.confidence * 100)}%</span>}
        {feedback?.action === "approve" && <span className="pill" style={{ color: "var(--good-ink)" }}>✓ approved</span>}
      </div>
      <div className="bubble">{m.content}</div>

      {m.original_content && (
        <div className="faint" style={{ textAlign: "right", marginTop: 4 }}>
          <button className="btn sm" onClick={() => setShowOriginal((v) => !v)}>
            {showOriginal ? "Hide" : "Show"} the AI’s original draft
          </button>
          {showOriginal && <div className="bubble" style={{ marginTop: 6, textAlign: "left", opacity: .7 }}>{m.original_content}</div>}
        </div>
      )}
      {m.citations?.length > 0 && (
        <div className="faint" style={{ textAlign: "right", marginTop: 4 }}>
          Sources: {m.citations.map((c) => `[${c.n}] ${c.title}${c.location ? ` (${c.location})` : ""}`).join(" · ")}
        </div>
      )}
      {feedback && feedback.action !== "approve" && (
        <div className="faint" style={{ textAlign: "right", marginTop: 4 }}>
          {feedback.action === "edit" ? "Corrected" : "Rejected"} by {feedback.by.replace("human:", "")}
          {feedback.reason ? ` — “${feedback.reason}”` : ""}
          {feedback.times_used ? ` · reused ${feedback.times_used}× to answer similar questions` : ""}
        </div>
      )}

      {m.role === "assistant" && m.agent !== "system" && m.status !== "rejected" && (
        <>
          {mode === "" && (
            <div className="msg-actions">
              <button className="btn sm good" disabled={busy} onClick={() => send("approve")}>
                ✓ {m.status === "pending_review" ? "Approve & send" : "Approve"}
              </button>
              <button className="btn sm" disabled={busy} onClick={() => { setText(m.content); setReason(""); setMode("edit"); }}>✏️ Edit</button>
              <button className="btn sm bad" disabled={busy} onClick={() => { setText(""); setReason(""); setMode("reject"); }}>✕ Reject</button>
            </div>
          )}
          {mode !== "" && (
            <div className="card card-pad stack" style={{ marginTop: 8 }}>
              <strong>{mode === "edit" ? "Correct this reply" : "Reject this reply"}</strong>
              <span className="faint">
                {mode === "edit"
                  ? "Your version replaces the AI’s reply, and the AI uses it as an example for similar questions in future."
                  : "The AI reply is withdrawn. If you write what it should have said, the customer gets that instead, and the AI learns from it."}
              </span>
              <textarea className="textarea" placeholder={mode === "edit" ? "Corrected reply" : "What it should have said (optional)"}
                        value={text} onChange={(e) => setText(e.target.value)} />
              <input className="input" placeholder="Why? e.g. wrong price, too formal (optional)" value={reason}
                     onChange={(e) => setReason(e.target.value)} />
              {error && <div className="error">{error}</div>}
              <div className="row" style={{ justifyContent: "flex-end" }}>
                <button className="btn sm" onClick={() => setMode("")}>Cancel</button>
                <button className={`btn sm ${mode === "edit" ? "primary" : "bad"}`}
                        disabled={busy || (mode === "edit" && !text.trim())} onClick={() => send(mode)}>
                  {mode === "edit" ? "Save correction" : "Reject"}
                </button>
              </div>
            </div>
          )}
          {error && mode === "" && <div className="error" style={{ textAlign: "right" }}>{error}</div>}
        </>
      )}
      {trace && <TracePanel trace={trace} />}
    </div>
  );
}
