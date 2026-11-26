// Thin client for the FastAPI backend (backend/app/main.py).
export const API_URL = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/\/$/, "");
const TOKEN_KEY = "tp_token";
const USER_KEY = "tp_user";

export type Role = "admin" | "agent";
export interface User { id: number; email: string; name: string; role: Role }

export function getToken(): string | null {
  try { return localStorage.getItem(TOKEN_KEY); } catch { return null; }
}
export function getStoredUser(): User | null {
  try { const raw = localStorage.getItem(USER_KEY); return raw ? JSON.parse(raw) : null; } catch { return null; }
}
export function saveSession(token: string, user: User) {
  try { localStorage.setItem(TOKEN_KEY, token); localStorage.setItem(USER_KEY, JSON.stringify(user)); } catch { /* private mode */ }
}
export function clearSession() {
  try { localStorage.removeItem(TOKEN_KEY); localStorage.removeItem(USER_KEY); } catch { /* ignore */ }
}

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

export async function api<T = unknown>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = { ...(init.headers as Record<string, string>) };
  if (init.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
  if (token) headers.Authorization = `Bearer ${token}`;
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, { ...init, headers });
  } catch {
    throw new ApiError(0, `Can't reach the server at ${API_URL}. Is the backend running?`);
  }
  if (res.status === 401 && !path.startsWith("/api/auth/login")) {
    clearSession();
    if (typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) window.location.href = "/login";
  }
  if (!res.ok) {
    let msg = res.statusText;
    try { const j = await res.json(); msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch { /* not json */ }
    throw new ApiError(res.status, msg);
  }
  const type = res.headers.get("content-type") || "";
  return (type.includes("application/json") ? res.json() : res.text()) as Promise<T>;
}

export const post = <T = unknown>(path: string, body: unknown) => api<T>(path, { method: "POST", body: JSON.stringify(body) });
export const put = <T = unknown>(path: string, body: unknown) => api<T>(path, { method: "PUT", body: JSON.stringify(body) });

/** POST that answers with Server-Sent Events (the customer chat). Calls onEvent for every `data:` line. */
export async function postStream(path: string, body: unknown, onEvent: (e: LiveEvent) => void): Promise<void> {
  const res = await fetch(`${API_URL}${path}`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (!res.ok || !res.body) throw new ApiError(res.status, `Chat failed (${res.status})`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      for (const line of chunk.split("\n")) {
        if (line.startsWith("data: ")) {
          try { onEvent(JSON.parse(line.slice(6))); } catch { /* ignore malformed */ }
        }
      }
    }
  }
}

// ---------------------------------------------------------------- shared types
export interface LiveEvent { type: string; conversation_id: string; [k: string]: any }

export interface Customer { id: number; name: string; email: string }
export interface ConversationSummary {
  id: string; channel: string; status: "open" | "needs_human" | "resolved"; subject: string | null;
  customer: Customer | null; last_agent: string | null; agents: string[]; created_at: string; updated_at: string;
  message_count: number; pending_review: number; escalated: boolean; ai_solved: boolean | null;
  last_message: { role: string; content: string; created_at: string } | null;
}
export interface Citation { n: number; title: string; location: string; [k: string]: any }
export interface Message {
  id: number; role: "customer" | "assistant" | "human"; agent: string | null; content: string;
  original_content: string | null; status: string; citations: Citation[]; confidence: number | null;
  trace_id: number | null; created_at: string;
}
export interface Step { type: string; why?: string; tool?: string; args?: any; result?: any; ms?: number; [k: string]: any }
export interface Trace {
  id: number; route_label: string | null; route_source: string | null; route_reason: string | null; agent: string | null;
  steps: Step[]; prompt_tokens: number; completion_tokens: number; cost_usd: number; latency_ms: number;
  llm_calls: number; error: string | null; created_at: string;
}
export interface FeedbackInfo { action: string; reason: string | null; by: string; final_reply: string | null; times_used: number }
export interface Escalation {
  id: number; conversation_id: string; severity: "low" | "medium" | "high" | "critical"; category: string;
  summary: string; customer_wants: string | null; suggested_action: string | null; status: string;
  created_at: string; customer: Customer | null;
}
export interface ConversationDetail extends ConversationSummary {
  messages: Message[]; traces: Record<string, Trace>; feedback: Record<string, FeedbackInfo>; escalations: Escalation[];
}
export interface Refund {
  id: number; order_id: string; amount: number; reason: string; status: string; created_by: string;
  decided_by: string | null; created_at: string; order_total: number; order_status: string; customer: Customer | null;
  items: { name: string; qty: number; unit_price: number }[];
}
export interface Counts { pending_review: number; needs_human: number; open_escalations: number; pending_refunds: number }
