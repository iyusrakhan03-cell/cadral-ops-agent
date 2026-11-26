"use client";
import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";
import { post, saveSession, User } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const r = await post<{ token: string; user: User }>("/api/auth/login", { email, password });
      saveSession(r.token, r.user);
      router.replace("/conversations");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="center-page">
      <form className="card card-pad login-card stack" onSubmit={submit}>
        <div>
          <h1>Thistle &amp; Pine</h1>
          <div className="muted">Staff sign-in for the AI operations dashboard</div>
        </div>
        <label className="stack" style={{ gap: 4 }}>
          <span className="faint">Email</span>
          <input className="input" type="email" autoComplete="username" value={email}
                 onChange={(e) => setEmail(e.target.value)} required />
        </label>
        <label className="stack" style={{ gap: 4 }}>
          <span className="faint">Password</span>
          <input className="input" type="password" autoComplete="current-password" value={password}
                 onChange={(e) => setPassword(e.target.value)} required />
        </label>
        {error && <div className="error">{error}</div>}
        <button className="btn primary" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
        <div className="faint">Admins also approve refunds and change settings. Agents review chats and escalations.</div>
      </form>
    </div>
  );
}
