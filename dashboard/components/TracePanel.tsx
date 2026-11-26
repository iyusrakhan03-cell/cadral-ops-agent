"use client";
// The agent trace for one AI reply: who handled it, why, every tool call (with the agent's stated reason), cost, time.
import { Step, Trace } from "@/lib/api";
import { AGENT_LABEL, SOURCE_LABEL, secs, usd } from "@/lib/format";

const TOOL_LABEL: Record<string, string> = {
  verify_customer: "Verify customer", list_my_orders: "List customer's orders", get_order: "Look up order",
  issue_refund: "Request refund (policy engine decides)", cancel_order: "Cancel order",
  update_shipping_address: "Change delivery address", open_courier_investigation: "Open courier investigation",
  handoff_to_human: "Hand off to a human",
};

function Json({ value }: { value: unknown }) {
  return <pre className="mono">{JSON.stringify(value, null, 2)}</pre>;
}

function StepView({ s }: { s: Step }) {
  if (s.type === "tool") {
    const decision = s.result?.decision;
    return (
      <div className="step tool">
        <div className="row">
          <strong>🔧 {TOOL_LABEL[s.tool!] || s.tool}</strong>
          {decision && <span className="pill">policy: {String(decision).replace("_", " ")}</span>}
          {s.result?.error && <span className="pill" style={{ color: "var(--critical-ink)" }}>error</span>}
          {s.ms != null && <span className="faint">{s.ms} ms</span>}
        </div>
        {s.why && <div className="muted">Why: “{s.why}”</div>}
        <details><summary className="faint">Arguments and result</summary>
          <Json value={{ arguments: s.args, result: s.result }} />
        </details>
      </div>
    );
  }
  if (s.type === "retrieval") {
    const hits: any[] = s.hits || [];
    return (
      <div className="step">
        <div className="row">
          <strong>📚 Searched company documents</strong>
          <span className="pill">{s.backend || "search"}</span>
          {s.idk ? <span className="pill">said “I don’t know”</span> : null}
          {s.timings_ms?.total != null && <span className="faint">{Math.round(s.timings_ms.total)} ms</span>}
        </div>
        {s.why && <div className="muted">Why: “{s.why}”</div>}
        {s.idk_reason && <div className="muted">I-don’t-know reason: {s.idk_reason}</div>}
        {hits.length > 0 && (
          <ol style={{ margin: "4px 0 0", paddingLeft: 20 }}>
            {hits.slice(0, 5).map((h) => (
              <li key={h.id} className="faint">{h.citation} <span className="mono">(score {h.score})</span></li>
            ))}
          </ol>
        )}
      </div>
    );
  }
  if (s.type === "escalation") {
    return (
      <div className="step escalation">
        <div className="row"><strong>🚨 Handed to a human</strong>
          <span className="pill">{s.severity}</span><span className="pill">{s.category}</span></div>
        {s.why && <div className="muted">Why: {s.why}</div>}
        {s.summary && <div>{s.summary}</div>}
        {s.suggested_action && <div className="faint">Suggested next step: {s.suggested_action}</div>}
      </div>
    );
  }
  if (s.type === "handoff") {
    return <div className="step escalation"><strong>🎫 Ticket opened for the team</strong><div className="muted">{s.why}</div></div>;
  }
  if (s.type === "query_rewrite") {
    return <div className="step"><strong>✏️ Rewrote follow-up for search</strong><div className="muted">“{s.query}”</div></div>;
  }
  if (s.type === "small_talk") {
    return <div className="step"><strong>💬 Small talk</strong><div className="muted">{s.why}</div></div>;
  }
  return <div className="step"><strong>{s.type}</strong><Json value={s} /></div>;
}

export default function TracePanel({ trace, open = false }: { trace: Trace; open?: boolean }) {
  const tools = trace.steps.filter((s) => s.type === "tool").map((s) => s.tool);
  return (
    <details className="trace" open={open}>
      <summary>
        Agent trace · {AGENT_LABEL[trace.agent || ""] || trace.agent || "error"}
        {tools.length ? ` · ${tools.length} tool${tools.length > 1 ? "s" : ""}` : ""} · {secs(trace.latency_ms)} · {usd(trace.cost_usd, 5)}
      </summary>
      {trace.error && <div className="error" style={{ marginTop: 8 }}>Failed: {trace.error}</div>}
      <div className="trace-grid">
        <div className="kv"><div className="k">Handled by</div><div className="v">{AGENT_LABEL[trace.agent || ""] || "–"}</div></div>
        <div className="kv"><div className="k">Message type</div><div className="v">{trace.route_label?.replace("_", " ") || "–"}</div></div>
        <div className="kv"><div className="k">Routed by</div><div className="v">{SOURCE_LABEL[trace.route_source || ""] || trace.route_source || "–"}</div></div>
        <div className="kv"><div className="k">Reply time</div><div className="v">{secs(trace.latency_ms)}</div></div>
        <div className="kv"><div className="k">Cost</div><div className="v">{usd(trace.cost_usd, 5)}</div></div>
        <div className="kv"><div className="k">Tokens · AI calls</div><div className="v">{trace.prompt_tokens + trace.completion_tokens} · {trace.llm_calls}</div></div>
      </div>
      {trace.route_reason && <div className="faint">Routing reason: {trace.route_reason}</div>}
      {trace.steps.length > 0 ? trace.steps.map((s, i) => <StepView key={i} s={s} />)
        : <div className="faint" style={{ marginTop: 6 }}>No tools used.</div>}
    </details>
  );
}
