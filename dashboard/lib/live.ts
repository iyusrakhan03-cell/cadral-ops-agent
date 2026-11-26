"use client";
// Live feed of every conversation (GET /api/live). One EventSource per tab, shared by all components.
import { useEffect, useRef, useState } from "react";
import { API_URL, getToken, LiveEvent } from "./api";

type Listener = (e: LiveEvent) => void;
const listeners = new Set<Listener>();
const statusListeners = new Set<(ok: boolean) => void>();
let source: EventSource | null = null;
let connected = false;

function ensureSource() {
  if (source || typeof window === "undefined") return;
  const token = getToken();
  if (!token) return;
  source = new EventSource(`${API_URL}/api/live?token=${encodeURIComponent(token)}`);
  source.onopen = () => { connected = true; statusListeners.forEach((l) => l(true)); };
  source.onerror = () => { connected = false; statusListeners.forEach((l) => l(false)); }; // EventSource retries by itself
  source.onmessage = (msg) => {
    let e: LiveEvent;
    try { e = JSON.parse(msg.data); } catch { return; }
    listeners.forEach((l) => l(e));
  };
}

export function closeLive() {
  source?.close();
  source = null;
  connected = false;
}

/** Subscribe to live events. The callback can change between renders without resubscribing. */
export function useLive(onEvent: Listener) {
  const ref = useRef(onEvent);
  ref.current = onEvent;
  useEffect(() => {
    ensureSource();
    const l: Listener = (e) => ref.current(e);
    listeners.add(l);
    return () => { listeners.delete(l); };
  }, []);
}

export function useLiveStatus(): boolean {
  const [ok, setOk] = useState(connected);
  useEffect(() => {
    ensureSource();
    statusListeners.add(setOk);
    setOk(connected);
    return () => { statusListeners.delete(setOk); };
  }, []);
  return ok;
}
