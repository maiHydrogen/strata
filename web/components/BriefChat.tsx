"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Check, Download, Lock, Logo, Plan, Send } from "@/components/Icons";
import { useBrief } from "@/components/BriefStore";

const EXAMPLES = [
  "A G+1 house for 4 of us, 3BHK, on a 30 by 50 feet east facing plot in Guwahati, 20 feet road in front",
  "Ground plus two for my joint family of 7 including my aged parents, 5 bedrooms, 40x60 ft plot",
  "Small home with a shop at the front, 2BHK above, plot 9 m by 15 m, road 7.5 m",
];

export default function BriefChat() {
  const { messages, summarised, done, busy, error, needCode, code, setCode, send, submitCode, downloadBrief } = useBrief();
  const [draft, setDraft] = useState("");
  const thread = useRef<HTMLDivElement>(null);
  const started = messages.some((m) => m.role === "you");

  useEffect(() => {
    thread.current?.scrollTo({ top: thread.current.scrollHeight, behavior: "smooth" });
  }, [messages, busy, done]);

  function say(text: string) {
    if (!text.trim() || busy) return;
    send(text);
    setDraft("");
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    say(draft);
  }

  function onCode(e: FormEvent) {
    e.preventDefault();
    submitCode();
  }

  return (
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
              <button key={x} type="button" onClick={() => say(x)}>{x}</button>
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
              <Link className="btn" href="/layout"><Plan size={18} /> Design layouts</Link>
            </div>
          </div>
        )}
      </div>

      {error && <p className="error" role="alert">{error}</p>}

      {needCode ? (
        <form className="composer" onSubmit={onCode}>
          <Lock size={20} className="muted" />
          <input type="password" value={code} onChange={(e) => setCode(e.target.value)}
                 placeholder="Access code for this STRATA demo" aria-label="Access code" autoFocus />
          <button className="send" type="submit" disabled={!code || busy}>Unlock</button>
        </form>
      ) : (
        <form className="composer" onSubmit={onSubmit}>
          {summarised && !done && (
            <button type="button" className="chip" onClick={() => say("yes")} disabled={busy}>
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
  );
}
