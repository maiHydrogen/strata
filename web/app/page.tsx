"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import {
  Beam, Book, Braces, Chat, Check, Cube, Download, Lock, Logo, Pipe, Plan, Restart, Send, Spark,
} from "@/components/Icons";
import LayoutView from "@/components/LayoutView";
import { demoBrief } from "@/lib/demoBrief";

type Status = "given" | "assumed" | "missing";
type Item = { path: string; label: string; status: Status; value: string | null; asking: boolean };
type Panel = { items: Item[]; given: number; total: number };
type Msg = { role: "bot" | "you"; text: string };
type Saved = { messages: Msg[]; state: unknown; panel: Panel | null; summarised: boolean; done: boolean; brief: unknown };

const STORE = "strata-brief-v1";
const CODE = "strata-access-code";

const STAGES = [
  { name: "Brief", icon: Chat, view: "brief" },
  { name: "Standards", icon: Book },
  { name: "Layout", icon: Plan, view: "layout" },
  { name: "Structure", icon: Beam },
  { name: "Services", icon: Pipe },
  { name: "Building JSON", icon: Braces },
  { name: "Revit model", icon: Cube },
];

const EXAMPLES = [
  "A G+1 house for 4 of us, 3BHK, on a 30 by 50 feet east facing plot in Guwahati, 20 feet road in front",
  "Ground plus two for my joint family of 7 including my aged parents, 5 bedrooms, 40x60 ft plot",
  "Small home with a shop at the front, 2BHK above, plot 9 m by 15 m, road 7.5 m",
];

function load<T>(key: string): T | null {
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : null;
  } catch {
    return null;
  }
}

function save(key: string, value: unknown) {
  try {
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, typeof value === "string" ? value : JSON.stringify(value));
  } catch {
    /* private window or blocked storage: the chat still works, it just is not remembered */
  }
}

export default function BriefPage() {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [state, setState] = useState<unknown>(null);
  const [panel, setPanel] = useState<Panel | null>(null);
  const [summarised, setSummarised] = useState(false);
  const [done, setDone] = useState(false);
  const [brief, setBrief] = useState<unknown>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [needCode, setNeedCode] = useState(false);
  const [view, setView] = useState<"brief" | "layout">("brief");
  const canOpen = (v?: string) => v === "brief" || (v === "layout" && done && brief !== null);
  const [code, setCode] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const thread = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (process.env.NODE_ENV === "development" && new URLSearchParams(window.location.search).get("demo") === "layout") {
      setMessages([{ role: "bot", text: "Demo: the 40 x 60 ft example brief." }]);
      setDone(true); setBrief(demoBrief); setView("layout");
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (messages.length) save(STORE, { messages, state, panel, summarised, done, brief });
    thread.current?.scrollTo({ top: thread.current.scrollHeight, behavior: "smooth" });
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
    setDraft("");
    void call(t, state);
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    send(draft);
  }

  function submitCode(e: FormEvent) {
    e.preventDefault();
    save(CODE, code);
    void call(pending, pending === null ? null : state, code);
  }

  function restart() {
    save(STORE, null);
    save("strata-layout-v1", null);
    setView("brief");
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

  const started = messages.some((m) => m.role === "you");
  const pct = panel ? Math.round((100 * panel.items.filter((i) => i.status !== "missing").length) / panel.total) : 0;

  return (
    <div className="stage">
      <div className="window">
        <nav className="rail" aria-label="STRATA">
          <div className="dots" aria-hidden><i /><i /><i /></div>
          <div className="rail-logo"><Logo /></div>
          <div className="rail-icons">
            {STAGES.slice(0, 4).map(({ name, icon: Icon, view: v }) => (
              <button key={name} type="button" className={`rail-btn${v === view ? " on" : ""}`} title={name}
                      aria-label={name} disabled={!canOpen(v)} onClick={() => v && setView(v as "brief" | "layout")}>
                <Icon size={20} />
              </button>
            ))}
          </div>
        </nav>

        <aside className="menu">
          <header className="menu-head"><h2>Pipeline</h2></header>
          <ul>
            {STAGES.map(({ name, icon: Icon, view: v }) => (
              <li key={name} className={v === view ? "on" : v ? (canOpen(v) ? "open" : "wait") : "off"}
                  aria-current={v === view ? "page" : undefined}>
                <button type="button" disabled={!canOpen(v)} onClick={() => v && setView(v as "brief" | "layout")}>
                  <Icon size={20} />
                  <span>{name}</span>
                  {!v && <em>soon</em>}
                  {v === "layout" && !canOpen(v) && <em>after brief</em>}
                </button>
              </li>
            ))}
          </ul>
          <div className="menu-foot">
            <div className="wave" aria-hidden>
              {Array.from({ length: 28 }).map((_, i) => (
                <i key={i} style={{ height: `${18 + 26 * Math.abs(Math.sin(i * 0.7))}%`, animationDelay: `${i * 60}ms` }}
                   className={busy ? "live" : ""} />
              ))}
            </div>
            <div className="model">
              <span className={`led${busy ? " busy" : ""}`} />
              <div>
                <b>Gemma 4 26B</b>
                <small>{busy ? "reading your reply" : "ready"}</small>
              </div>
            </div>
          </div>
        </aside>

        <main className="main">
          <div className="topbar">
            <div className="pill"><Spark size={16} /> {view === "layout" ? "Layout" : "Brief chat"}</div>
          </div>
          {view === "layout" && brief !== null ? (
          <div className="content scroll">
            <LayoutView brief={brief} code={code}
                        onNeedCode={() => { setNeedCode(true); setView("brief"); }}
                        onApproved={() => undefined} />
          </div>
          ) : (
          <div className="content">
            <section className="banner">
              <h1>Describe the house<br />you want</h1>
              <p>Plain words are fine, in feet or metres. STRATA asks only for what is missing and keeps every assumption visible.</p>
            </section>

            <div className="thread" ref={thread} aria-live="polite">
              {messages.map((m, i) => (
                <div key={i} className={`msg ${m.role}`}>
                  {m.role === "bot" && <span className="avatar"><Logo size={30} /></span>}
                  <div className="bubble">{m.text}</div>
                </div>
              ))}
              {busy && (
                <div className="msg bot">
                  <span className="avatar"><Logo size={30} /></span>
                  <div className="bubble typing" aria-label="STRATA is typing"><i /><i /><i /></div>
                </div>
              )}
              {!started && !busy && messages.length > 0 && (
                <div className="examples">
                  <p>Try one of these</p>
                  {EXAMPLES.map((x) => (
                    <button key={x} type="button" onClick={() => send(x)}>{x}</button>
                  ))}
                </div>
              )}
              {done && (
                <div className="done-card">
                  <Check size={22} />
                  <div>
                    <h3>Brief confirmed</h3>
                    <p>Next, STRATA works out every code requirement and draws layouts that meet them.</p>
                  </div>
                  <div className="btn-row">
                    <button type="button" className="btn ghost" onClick={downloadBrief}><Download size={18} /> brief.json</button>
                    <button type="button" className="btn" onClick={() => setView("layout")}><Plan size={18} /> Design layouts</button>
                  </div>
                </div>
              )}
            </div>

            {error && <p className="error" role="alert">{error}</p>}

            {needCode ? (
              <form className="composer" onSubmit={submitCode}>
                <Lock size={20} className="muted" />
                <input type="password" value={code} onChange={(e) => setCode(e.target.value)}
                       placeholder="Access code for this STRATA demo" aria-label="Access code" autoFocus />
                <button className="send" type="submit" disabled={!code || busy}>Unlock</button>
              </form>
            ) : (
              <form className="composer" onSubmit={onSubmit}>
                {summarised && !done && (
                  <button type="button" className="chip" onClick={() => send("yes")} disabled={busy}>
                    <Check size={16} /> Yes, confirm
                  </button>
                )}
                <input value={draft} onChange={(e) => setDraft(e.target.value)} disabled={busy || done}
                       placeholder={done ? "Brief confirmed" : summarised ? "Or tell me what to change" : "Type your answer"}
                       aria-label="Your message" maxLength={2000} />
                <button className="send" type="submit" disabled={busy || done || !draft.trim()} aria-label="Send">
                  <Send size={20} />
                </button>
              </form>
            )}
          </div>
          )}
        </main>

        <aside className="inspector" aria-label="Your brief">
          <header className="inspector-head">
            <h2>Your brief</h2>
            <button type="button" className="icon-btn" onClick={restart} title="Start over" aria-label="Start over">
              <Restart size={20} />
            </button>
          </header>
          <div className="ring">
            <svg viewBox="0 0 120 120" aria-hidden>
              <defs>
                <linearGradient id="rg" x1="0" y1="0" x2="1" y2="1">
                  <stop offset="0" stopColor="#f7cbfd" /><stop offset=".5" stopColor="#5b7fd6" /><stop offset="1" stopColor="#01b7c5" />
                </linearGradient>
              </defs>
              {[0, 1, 2].map((k) => (
                <path key={k} d="M60 8 105 34v52L60 112 15 86V34Z" className="ghost"
                      transform={`rotate(${k * 20 - 20} 60 60)`} />
              ))}
              <path d="M60 8 105 34v52L60 112 15 86V34Z" className="track" />
              <path d="M60 8 105 34v52L60 112 15 86V34Z" className="fill" pathLength={100}
                    style={{ strokeDasharray: `${pct} 100`, opacity: pct ? 1 : 0 }} />
            </svg>
            <div className="ring-label"><b>{pct}%</b><small>known</small></div>
          </div>
          <p className="ring-note">
            {panel ? `${panel.given} of ${panel.total} answered by you` : "Waiting for your first message"}
          </p>
          <div className="items">
            {(panel?.items ?? []).map((it) => (
              <div key={it.path} className={`item ${it.status}${it.asking ? " asking" : ""}`}>
                <div className="item-top">
                  <span>{it.label}</span>
                  <em>{it.asking ? "asking now" : it.status === "given" ? "from you" : it.status === "assumed" ? "assumed" : "missing"}</em>
                </div>
                <p>{it.value ?? "Not given yet"}</p>
              </div>
            ))}
          </div>
        </aside>
      </div>
    </div>
  );
}
