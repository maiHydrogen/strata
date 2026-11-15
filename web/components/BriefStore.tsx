"use client";

/** The brief conversation, shared by every page so moving between /brief and /layout keeps it.
 * Saved to localStorage (per browser) so a refresh keeps it too. */

import { createContext, useContext, useEffect, useState } from "react";
import { demoBrief } from "@/lib/demoBrief";

export type Status = "given" | "assumed" | "missing";
export type Item = { path: string; label: string; status: Status; value: string | null; asking: boolean };
export type Panel = { items: Item[]; given: number; total: number };
export type Msg = { role: "bot" | "you"; text: string };
type Saved = { messages: Msg[]; state: unknown; panel: Panel | null; summarised: boolean; done: boolean; brief: unknown };

const STORE = "strata-brief-v1";
const CODE = "strata-access-code";
export const LAYOUT_STORE = "strata-layout-v1";

export function load<T>(key: string): T | null {
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : null;
  } catch {
    return null;
  }
}

export function save(key: string, value: unknown) {
  try {
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, typeof value === "string" ? value : JSON.stringify(value));
  } catch {
    /* private window or blocked storage: the chat still works, it just is not remembered */
  }
}

type Store = {
  ready: boolean; messages: Msg[]; panel: Panel | null; summarised: boolean; done: boolean; brief: unknown;
  busy: boolean; error: string | null; needCode: boolean; code: string;
  setCode: (c: string) => void; requireCode: () => void;
  send: (text: string) => void; submitCode: () => void; restart: () => void; downloadBrief: () => void;
};

const Ctx = createContext<Store | null>(null);

export function useBrief() {
  const s = useContext(Ctx);
  if (!s) throw new Error("useBrief outside BriefProvider");
  return s;
}

export function BriefProvider({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = useState(false);
  const [messages, setMessages] = useState<Msg[]>([]);
  const [state, setState] = useState<unknown>(null);
  const [panel, setPanel] = useState<Panel | null>(null);
  const [summarised, setSummarised] = useState(false);
  const [done, setDone] = useState(false);
  const [brief, setBrief] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [needCode, setNeedCode] = useState(false);
  const [code, setCode] = useState("");
  const [pending, setPending] = useState<string | null>(null);

  useEffect(() => {
    if (process.env.NODE_ENV === "development" && new URLSearchParams(window.location.search).get("demo") === "layout") {
      setMessages([{ role: "bot", text: "Demo: the 40 x 60 ft example brief." }]);
      setDone(true); setBrief(demoBrief); setReady(true);
      return;
    }
    const saved = load<Saved>(STORE);
    const stored = (() => { try { return window.localStorage.getItem(CODE) ?? ""; } catch { return ""; } })();
    setCode(stored);
    if (saved && saved.messages?.length) {
      setMessages(saved.messages); setState(saved.state); setPanel(saved.panel);
      setSummarised(saved.summarised); setDone(saved.done); setBrief(saved.brief);
    } else {
      void call(null, null, stored);
    }
    setReady(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (messages.length) save(STORE, { messages, state, panel, summarised, done, brief });
  }, [messages, state, panel, summarised, done, brief]);

  async function call(message: string | null, current: unknown, accessCode = code) {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/brief", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...(accessCode ? { "X-Strata-Code": accessCode } : {}) },
        body: JSON.stringify(message === null && current === null ? {} : { state: current, message }),
      });
      if (res.status === 401) {
        setNeedCode(true);
        setPending(message);
        return;
      }
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `Server error ${res.status}`);
      setNeedCode(false);
      setMessages((m) => [...m, { role: "bot", text: data.reply }]);
      setState(data.state); setPanel(data.panel); setSummarised(data.summarised); setDone(data.done);
      if (data.brief) setBrief(data.brief);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  function send(text: string) {
    const t = text.trim();
    if (!t || busy) return;
    setMessages((m) => [...m, { role: "you", text: t }]);
    void call(t, state);
  }

  function submitCode() {
    save(CODE, code);
    void call(pending, pending === null ? null : state, code);
  }

  function restart() {
    save(STORE, null);
    save(LAYOUT_STORE, null);
    setMessages([]); setState(null); setPanel(null); setSummarised(false); setDone(false); setBrief(null);
    void call(null, null);
  }

  function downloadBrief() {
    const blob = new Blob([JSON.stringify(brief, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "strata.brief.json";
    a.click();
    URL.revokeObjectURL(a.href);
  }

  return (
    <Ctx.Provider value={{
      ready, messages, panel, summarised, done, brief, busy, error, needCode, code,
      setCode, requireCode: () => setNeedCode(true), send, submitCode, restart, downloadBrief,
    }}>
      {children}
    </Ctx.Provider>
  );
}
