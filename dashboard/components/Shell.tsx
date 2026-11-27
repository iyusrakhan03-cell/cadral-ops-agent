"use client";
// Signed-in layout: sidebar with badges, role-aware navigation, and the auth guard.
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api, clearSession, Counts, getStoredUser, getToken, User } from "@/lib/api";
import { closeLive, useLive, useLiveStatus } from "@/lib/live";

const UserCtx = createContext<User | null>(null);
export const useUser = () => useContext(UserCtx);

const NAV: { href: string; label: string; badge?: keyof Counts; admin?: boolean }[] = [
  { href: "/conversations", label: "Live conversations", badge: "pending_review" },
  { href: "/playground", label: "Talk to the AI" },
  { href: "/escalations", label: "Escalations", badge: "open_escalations" },
  { href: "/refunds", label: "Refund approvals", badge: "pending_refunds", admin: true },
  { href: "/analytics", label: "Analytics" },
  { href: "/learning", label: "What the AI learned" },
  { href: "/reports", label: "Daily report" },
  { href: "/settings", label: "Settings", admin: true },
];

export default function Shell({ children, adminOnly = false }: { children: React.ReactNode; adminOnly?: boolean }) {
  const router = useRouter();
  const path = usePathname();
  const [user, setUser] = useState<User | null>(null);
  const [counts, setCounts] = useState<Counts | null>(null);
  const liveOk = useLiveStatus();

  useEffect(() => {
    if (!getToken()) { router.replace("/login"); return; }
    setUser(getStoredUser());
    api<User>("/api/auth/me").then(setUser).catch(() => { /* api() redirects on 401 */ });
  }, [router]);

  const loadCounts = useCallback(() => { api<Counts>("/api/counts").then(setCounts).catch(() => {}); }, []);
  useEffect(() => { if (getToken()) loadCounts(); }, [loadCounts, path]);
  useLive((e) => {
    if (["done", "feedback", "status", "refund_decision", "escalation_update", "human_reply"].includes(e.type)) loadCounts();
  });

  const logout = () => { closeLive(); clearSession(); router.replace("/login"); };

  if (!user) return <div className="center-page muted">Loading…</div>;

  return (
    <UserCtx.Provider value={user}>
      <div className="shell">
        <aside className="sidebar">
          <div className="brand">Thistle &amp; Pine<small>AI operations</small></div>
          {NAV.filter((n) => !n.admin || user.role === "admin").map((n) => {
            const c = n.badge && counts ? counts[n.badge] : 0;
            return (
              <Link key={n.href} href={n.href} className={`nav-link ${path.startsWith(n.href) ? "active" : ""}`}>
                <span>{n.label}</span>
                {c ? <span className={`badge-count ${n.badge === "pending_review" || n.badge === "pending_refunds" ? "hot" : ""}`}>{c}</span> : null}
              </Link>
            );
          })}
          <Link href="/chat" target="_blank" className="nav-link"><span>Customer chat ↗</span></Link>
          <div className="sidebar-foot">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <span><span className={`live-dot ${liveOk ? "" : "off"}`} /> {liveOk ? "Live" : "Reconnecting…"}</span>
              <span className="pill">{user.role}</span>
            </div>
            <div style={{ margin: "8px 0 6px" }} className="muted">{user.email}</div>
            <button className="btn sm" onClick={logout}>Sign out</button>
          </div>
        </aside>
        <main className="main">
          {adminOnly && user.role !== "admin"
            ? <div className="card card-pad empty">This page is for admins only.</div>
            : children}
        </main>
      </div>
    </UserCtx.Provider>
  );
}
