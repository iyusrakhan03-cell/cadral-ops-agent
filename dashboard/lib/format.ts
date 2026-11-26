export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}

export function clock(iso: string | null | undefined): string {
  if (!iso) return "";
  return new Date(iso).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
}

export const usd = (n: number | null | undefined, digits = 4) =>
  n == null ? "–" : `$${n.toFixed(digits)}`;
export const gbp = (n: number) => `£${n.toFixed(2)}`;
export const secs = (ms: number | null | undefined) => (ms == null ? "–" : `${(ms / 1000).toFixed(1)}s`);
export const pct = (n: number | null | undefined) => (n == null ? "–" : `${n.toFixed(n % 1 ? 1 : 0)}%`);

export const AGENT_LABEL: Record<string, string> = {
  support: "Support agent", orders: "Orders agent", escalation: "Escalation agent", system: "System",
};
export const SOURCE_LABEL: Record<string, string> = {
  rules: "safety rules", finetuned: "fine-tuned router", llm: "backup AI router", sticky: "follow-up (same agent)",
  status: "already with a human",
};
export const STATUS_LABEL: Record<string, string> = {
  open: "Open", needs_human: "Needs a human", resolved: "Resolved",
};
