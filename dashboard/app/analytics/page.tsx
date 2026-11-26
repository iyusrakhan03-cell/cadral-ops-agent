"use client";
// Analytics: how many chats the AI solved alone, how fast, how accurately, and what it cost.
import { useCallback, useEffect, useState } from "react";
import {
  Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import Shell from "@/components/Shell";
import { api } from "@/lib/api";
import { pct, usd } from "@/lib/format";
import { useLive } from "@/lib/live";

interface Day {
  date: string; ai_solved: number; needed_human: number; conversations: number; ai_solved_pct: number | null;
  avg_latency_s: number | null; p95_latency_s: number | null; cost_usd: number; reviewed: number; approved_pct: number | null;
}
interface Analytics {
  days: number; definitions: Record<string, string>;
  totals: Record<string, number | null>;
  series: Day[]; by_agent: Record<string, number>; by_route_source: Record<string, number>;
  eval_history: { timestamp: string; suite: string; score: number; cases: number; passed: number }[];
}

// Read chart colours from the CSS tokens so light/dark mode stay in sync.
function useColors() {
  const read = () => {
    if (typeof window === "undefined") return null;
    const cs = getComputedStyle(document.documentElement);
    const v = (n: string) => cs.getPropertyValue(n).trim();
    return { s1: v("--series-1"), s2: v("--series-2"), grid: v("--grid"), text: v("--text-2"), muted: v("--text-3"), surface: v("--surface") };
  };
  const [c, setC] = useState(read);
  useEffect(() => {
    setC(read());
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const on = () => setC(read());
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return c;
}

const shortDate = (d: string) => new Date(d + "T00:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "short" });

function Tip({ active, payload, label, fmt }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div className="tooltip">
      <div className="t">{label}</div>
      {payload.map((p: any) => (
        <div key={p.dataKey}><span className="sw" style={{ background: p.color, width: 8, height: 8, display: "inline-block", borderRadius: 2, marginRight: 6 }} />
          {p.name}: <strong>{p.value == null ? "–" : fmt ? fmt(p.value) : p.value}</strong></div>
      ))}
    </div>
  );
}

function Legend({ items }: { items: [string, string][] }) {
  return <div className="legend">{items.map(([label, color]) => <span key={label}><span className="sw" style={{ background: color }} />{label}</span>)}</div>;
}

function Kpi({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return <div className="card card-pad kpi"><div className="label">{label}</div><div className="value">{value}</div>{sub && <div className="sub">{sub}</div>}</div>;
}

function AnalyticsView() {
  const [days, setDays] = useState(14);
  const [data, setData] = useState<Analytics | null>(null);
  const [error, setError] = useState("");
  const [asTable, setAsTable] = useState(false);
  const c = useColors();
  const load = useCallback(() => {
    api<Analytics>(`/api/analytics?days=${days}`).then((d) => { setData(d); setError(""); }).catch((e) => setError(e.message));
  }, [days]);
  useEffect(load, [load]);
  useLive((e) => { if (e.type === "done" || e.type === "feedback") load(); });

  if (error) return <div className="error">{error}</div>;
  if (!data || !c) return <div className="muted">Loading…</div>;
  const t = data.totals;
  const series = data.series.map((d) => ({ ...d, label: shortDate(d.date) }));
  const lastEval = data.eval_history[data.eval_history.length - 1];
  const evals = data.eval_history.map((e) => ({
    label: `${new Date(e.timestamp).toLocaleDateString("en-GB", { day: "numeric", month: "short" })} · ${e.suite}`,
    score: Math.round(e.score * 1000) / 10,
  }));
  const agents = Object.entries(data.by_agent).map(([agent, n]) => ({ agent, n })).sort((a, b) => b.n - a.n);
  const axis = { stroke: c.grid, tick: { fill: c.muted, fontSize: 11 }, tickLine: false };
  const xPad = { left: 14, right: 14 };   // keeps a point on the first/last day from being clipped
  const grid = <CartesianGrid stroke={c.grid} vertical={false} />;

  return (
    <>
      <div className="page-head">
        <div><h1>Analytics</h1><div className="muted">Updates live as chats come in.</div></div>
        <div className="row">
          <div className="tabs">
            {[7, 14, 30].map((d) => <button key={d} className={`tab ${days === d ? "active" : ""}`} onClick={() => setDays(d)}>Last {d} days</button>)}
          </div>
          <button className="btn sm" onClick={() => setAsTable((v) => !v)}>{asTable ? "Show charts" : "Show as table"}</button>
        </div>
      </div>

      <div className="kpis">
        <Kpi label="Solved by AI alone" value={pct(t.ai_solved_pct)} sub={`${t.ai_solved} of ${(t.ai_solved || 0) + (t.needed_human || 0)} conversations · ${t.needed_human} needed a human`} />
        <Kpi label="Typical reply time" value={t.p50_latency_s == null ? "–" : `${t.p50_latency_s}s`} sub={`median · slowest 5% take ${t.p95_latency_s ?? "–"}s+`} />
        <Kpi label="Accuracy" value={lastEval ? pct(Math.round(lastEval.score * 1000) / 10) : "–"}
             sub={`latest test suite${lastEval ? ` (${lastEval.passed}/${lastEval.cases})` : ""} · humans approved ${pct(t.approved_pct)} of ${t.reviewed} reviewed`} />
        <Kpi label="Cost per conversation" value={usd(t.cost_per_conversation_usd as number | null, 5)}
             sub={`${usd(t.total_cost_usd as number | null, 4)} total · ${t.tokens?.toLocaleString()} tokens · free tier = $0 actual`} />
      </div>

      {asTable ? (
        <div className="card" style={{ overflow: "auto" }}>
          <table className="table">
            <thead><tr><th>Day</th><th className="num">Chats</th><th className="num">Solved by AI</th><th className="num">Needed human</th>
              <th className="num">% AI</th><th className="num">Avg reply</th><th className="num">p95 reply</th><th className="num">Reviewed</th>
              <th className="num">Approved</th><th className="num">Cost</th></tr></thead>
            <tbody>{series.map((d) => (
              <tr key={d.date}><td>{d.label}</td><td className="num">{d.conversations}</td><td className="num">{d.ai_solved}</td>
                <td className="num">{d.needed_human}</td><td className="num">{pct(d.ai_solved_pct)}</td>
                <td className="num">{d.avg_latency_s ?? "–"}s</td><td className="num">{d.p95_latency_s ?? "–"}s</td>
                <td className="num">{d.reviewed}</td><td className="num">{pct(d.approved_pct)}</td><td className="num">{usd(d.cost_usd, 5)}</td></tr>
            ))}</tbody>
          </table>
        </div>
      ) : (
        <div className="charts">
          <div className="card card-pad chart-card">
            <h2>Chats solved by the AI alone</h2>
            <span className="faint">{data.definitions.ai_solved}</span>
            <Legend items={[["Solved by AI alone", c.s1], ["Needed a human", c.s2]]} />
            <ResponsiveContainer width="100%" height={240}>
              <BarChart data={series} margin={{ top: 4, right: 8, left: -18, bottom: 0 }}>
                {grid}
                <XAxis dataKey="label" {...axis} padding={xPad} />
                <YAxis allowDecimals={false} {...axis} axisLine={false} />
                <Tooltip content={<Tip />} cursor={{ fill: c.grid, opacity: .4 }} />
                <Bar dataKey="ai_solved" name="Solved by AI alone" stackId="a" fill={c.s1} stroke={c.surface} strokeWidth={2} maxBarSize={28} />
                <Bar dataKey="needed_human" name="Needed a human" stackId="a" fill={c.s2} stroke={c.surface} strokeWidth={2} radius={[4, 4, 0, 0]} maxBarSize={28} />
              </BarChart>
            </ResponsiveContainer>
          </div>

          <div className="card card-pad chart-card">
            <h2>Speed: time to reply (seconds)</h2>
            <span className="faint">{data.definitions.speed}</span>
            <Legend items={[["Average", c.s1], ["Slowest 5% (p95)", c.s2]]} />
            <ResponsiveContainer width="100%" height={240}>
              <LineChart data={series} margin={{ top: 4, right: 8, left: -18, bottom: 0 }}>
                {grid}
                <XAxis dataKey="label" {...axis} padding={xPad} />
                <YAxis {...axis} axisLine={false} />
                <Tooltip content={<Tip fmt={(v: number) => `${v}s`} />} cursor={{ stroke: c.muted, strokeDasharray: "3 3" }} />
                <Line dataKey="avg_latency_s" name="Average" stroke={c.s1} strokeWidth={2} isAnimationActive={false} dot={{ r: 4, fill: c.s1, stroke: c.surface, strokeWidth: 2 }} connectNulls />
                <Line dataKey="p95_latency_s" name="Slowest 5% (p95)" stroke={c.s2} strokeWidth={2} isAnimationActive={false} dot={{ r: 4, fill: c.s2, stroke: c.surface, strokeWidth: 2 }} connectNulls />
              </LineChart>
            </ResponsiveContainer>
          </div>

          <div className="card card-pad chart-card">
            <h2>Accuracy: test-suite score per run (%)</h2>
            <span className="faint">{data.definitions.accuracy_tests}</span>
            {evals.length === 0 ? <div className="empty">No test runs yet. Run <span className="mono">python -m scripts.run_evals</span>.</div> : (
              <ResponsiveContainer width="100%" height={240}>
                <LineChart data={evals} margin={{ top: 4, right: 8, left: -18, bottom: 0 }}>
                  {grid}
                  <XAxis dataKey="label" {...axis} padding={xPad} />
                  <YAxis domain={[0, 100]} {...axis} axisLine={false} />
                  <Tooltip content={<Tip fmt={(v: number) => `${v}%`} />} cursor={{ stroke: c.muted, strokeDasharray: "3 3" }} />
                  <Line dataKey="score" name="Score" stroke={c.s1} strokeWidth={2} isAnimationActive={false} dot={{ r: 4, fill: c.s1, stroke: c.surface, strokeWidth: 2 }} />
                </LineChart>
              </ResponsiveContainer>
            )}
          </div>

          <div className="card card-pad chart-card">
            <h2>Accuracy: replies humans approved (%)</h2>
            <span className="faint">{data.definitions.accuracy_review}</span>
            {t.reviewed ? (
              <ResponsiveContainer width="100%" height={240}>
                <LineChart data={series} margin={{ top: 4, right: 8, left: -18, bottom: 0 }}>
                  {grid}
                  <XAxis dataKey="label" {...axis} padding={xPad} />
                  <YAxis domain={[0, 100]} {...axis} axisLine={false} />
                  <Tooltip content={<Tip fmt={(v: number) => `${v}%`} />} cursor={{ stroke: c.muted, strokeDasharray: "3 3" }} />
                  <Line dataKey="approved_pct" name="Approved" stroke={c.s1} strokeWidth={2} isAnimationActive={false} dot={{ r: 4, fill: c.s1, stroke: c.surface, strokeWidth: 2 }} connectNulls />
                </LineChart>
              </ResponsiveContainer>
            ) : <div className="empty">No reviews yet. Approve, edit or reject replies in Live conversations.</div>}
          </div>

          <div className="card card-pad chart-card">
            <h2>Cost per day (USD)</h2>
            <span className="faint">{data.definitions.cost}</span>
            <ResponsiveContainer width="100%" height={240}>
              <BarChart data={series} margin={{ top: 4, right: 8, left: -6, bottom: 0 }}>
                {grid}
                <XAxis dataKey="label" {...axis} padding={xPad} />
                <YAxis {...axis} axisLine={false} tickFormatter={(v) => `$${v}`} />
                <Tooltip content={<Tip fmt={(v: number) => usd(v, 5)} />} cursor={{ fill: c.grid, opacity: .4 }} />
                <Bar dataKey="cost_usd" name="Cost" fill={c.s1} radius={[4, 4, 0, 0]} maxBarSize={28} />
              </BarChart>
            </ResponsiveContainer>
          </div>

          <div className="card card-pad chart-card">
            <h2>Which agent handled the replies</h2>
            <span className="faint">AI replies per agent in this period.</span>
            <ResponsiveContainer width="100%" height={240}>
              <BarChart data={agents} layout="vertical" margin={{ top: 4, right: 24, left: 10, bottom: 0 }}>
                <CartesianGrid stroke={c.grid} horizontal={false} />
                <XAxis type="number" allowDecimals={false} {...axis} />
                <YAxis type="category" dataKey="agent" {...axis} axisLine={false} width={80} />
                <Tooltip content={<Tip />} cursor={{ fill: c.grid, opacity: .4 }} />
                <Bar dataKey="n" name="Replies" fill={c.s1} radius={[0, 4, 4, 0]} maxBarSize={22} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}
    </>
  );
}

export default function Page() { return <Shell><AnalyticsView /></Shell>; }
