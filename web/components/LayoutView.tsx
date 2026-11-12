"use client";

import { useEffect, useState } from "react";
import { Check, Download, Restart, Spark } from "@/components/Icons";

type Room = { floor: string; name: string; carpet: number };
type Option = {
  name: string;
  svg: string;
  pass: boolean;
  summary: { checks: number; failures: number; practice_warnings: number; skipped: number };
  areas: { plot_area?: number; built_up_total: number; carpet_total: number; far?: number };
  floors: { floor: string; built_up: number; carpet: number }[];
  failures: string[];
  rooms: Room[];
  layout: unknown;
};
type Suggestion = { change: string; fits: boolean; brief: unknown };
type Result = {
  envelope?: { width: number; depth: number; setbacks: Record<string, number> } | null;
  options: Option[];
  suggestions?: Suggestion[];
};

const STORE = "strata-layout-v1";

export default function LayoutView({ brief, code, onNeedCode, onApproved }: {
  brief: unknown; code: string; onNeedCode: () => void; onApproved: (layout: unknown) => void;
}) {
  const [result, setResult] = useState<Result | null>(null);
  const [current, setCurrent] = useState<unknown>(brief);
  const [pick, setPick] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [seed, setSeed] = useState(1);
  const [approved, setApproved] = useState<string | null>(null);

  useEffect(() => {
    try {
      const saved = window.localStorage.getItem(STORE);
      if (saved) {
        const s = JSON.parse(saved);
        if (JSON.stringify(s.brief) === JSON.stringify(brief)) {
          setResult(s.result); setPick(s.pick ?? 0); setApproved(s.approved ?? null); setSeed(s.seed ?? 1);
          return;
        }
      }
    } catch { /* storage unavailable: design afresh */ }
    void design(brief, 1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!result) return;
    try {
      window.localStorage.setItem(STORE, JSON.stringify({ brief, result, pick, approved, seed }));
    } catch { /* too large or blocked: fine, it is only a convenience */ }
  }, [brief, result, pick, approved, seed]);

  async function design(b: unknown, s: number) {
    setBusy(true); setError(null); setApproved(null);
    try {
      const res = await fetch("/api/layout", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...(code ? { "X-Strata-Code": code } : {}) },
        body: JSON.stringify({ brief: b, seed: s }),
      });
      if (res.status === 401) { onNeedCode(); return; }
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `Server error ${res.status}`);
      setResult(data); setPick(0); setCurrent(b); setSeed(s);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not design layouts. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  function approve(o: Option) {
    const blob = new Blob([JSON.stringify(o.layout, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `strata.${o.name.toLowerCase().replace(/\s+/g, "_")}.layout.json`;
    a.click();
    URL.revokeObjectURL(a.href);
    setApproved(o.name);
    onApproved(o.layout);
  }

  const options = result?.options ?? [];
  const o = options[pick];
  const fits = options.some((x) => x.pass);
  const env = result?.envelope;

  return (
    <>
      <section className="banner">
        <h1>Layouts for<br />your plot</h1>
        <p>
          {env
            ? `Buildable area after setbacks: ${env.width.toFixed(2)} m by ${env.depth.toFixed(2)} m. Every option is checked room by room against NBC 2016 and the Guwahati bye-laws.`
            : "STRATA draws plans under the building rules, then checks every room against NBC 2016 and the bye-laws."}
        </p>
      </section>

      <div className="layout-body">
        {busy && (
          <div className="loading-card" role="status">
            <div className="grid-anim" aria-hidden>{Array.from({ length: 12 }).map((_, i) => <i key={i} style={{ animationDelay: `${i * 90}ms` }} />)}</div>
            <div>
              <h3>Designing and checking layouts</h3>
              <p>The genetic algorithm tries thousands of plans. Allow up to half a minute, longer if the plot is tight.</p>
            </div>
          </div>
        )}
        {error && <p className="error" role="alert">{error}</p>}

        {!busy && result && (
          <>
            <div className="tabs" role="tablist">
              {options.map((x, i) => (
                <button key={x.name} role="tab" aria-selected={i === pick} className={i === pick ? "on" : ""} onClick={() => setPick(i)}>
                  {x.name}
                  <em className={x.pass ? "ok" : "bad"}>{x.pass ? "passes" : "does not fit"}</em>
                </button>
              ))}
              <button type="button" className="icon-btn again" onClick={() => design(current, seed + 1)} title="Generate other layouts" aria-label="Generate other layouts">
                <Restart size={18} />
              </button>
            </div>

            {o && (
              <>
                <div className="plan" dangerouslySetInnerHTML={{ __html: o.svg }} />
                <div className="stats">
                  <div className={`stat ${o.pass ? "ok" : "bad"}`}>
                    <b>{o.summary.checks - o.summary.failures}/{o.summary.checks}</b><small>rule checks passed</small>
                  </div>
                  <div className="stat"><b>{o.areas.carpet_total.toFixed(1)} m2</b><small>carpet area</small></div>
                  <div className="stat"><b>{o.areas.built_up_total.toFixed(1)} m2</b><small>built up</small></div>
                  {o.areas.far != null && <div className="stat"><b>{o.areas.far.toFixed(2)}</b><small>FAR used</small></div>}
                </div>

                {o.failures.length > 0 && (
                  <div className="panel-card bad">
                    <h3>Rules this option breaks</h3>
                    <ul>{o.failures.map((f) => <li key={f}>{f}</li>)}</ul>
                  </div>
                )}

                {!fits && result.suggestions && (
                  <div className="panel-card">
                    <h3>Your brief does not fit this plot as asked</h3>
                    <p>These changes were tried with the same rules. Pick one to design again:</p>
                    <ul className="suggest">
                      {result.suggestions.map((s) => (
                        <li key={s.change} className={s.fits ? "ok" : ""}>
                          <span>{s.change.charAt(0).toUpperCase() + s.change.slice(1)}</span>
                          {s.fits ? (
                            <button type="button" className="chip" onClick={() => design(s.brief, seed)}><Spark size={14} /> Try this</button>
                          ) : <em>still does not fit</em>}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                <div className="panel-card">
                  <h3>Rooms</h3>
                  <div className="rooms">
                    {o.floors.map((f) => (
                      <div key={f.floor}>
                        <h4>{f.floor} <small>{f.carpet.toFixed(1)} m2 carpet</small></h4>
                        <ul>
                          {o.rooms.filter((r) => r.floor === f.floor).map((r, i) => (
                            <li key={i}><span>{r.name}</span><b>{r.carpet.toFixed(1)} m2</b></li>
                          ))}
                        </ul>
                      </div>
                    ))}
                  </div>
                </div>

                {o.pass && (
                  <div className="done-card">
                    {approved === o.name ? <Check size={22} /> : <Spark size={22} />}
                    <div>
                      <h3>{approved === o.name ? `${o.name} approved` : `Approve ${o.name}?`}</h3>
                      <p>{approved === o.name
                        ? "Saved as layout.json. Next comes the structural design in STAAD.Pro."
                        : "This plan goes on to structural design. You can still generate others first."}</p>
                    </div>
                    <button type="button" className="btn" onClick={() => approve(o)}>
                      <Download size={18} /> {approved === o.name ? "Download again" : "Approve"}
                    </button>
                  </div>
                )}
              </>
            )}
          </>
        )}
      </div>
    </>
  );
}
