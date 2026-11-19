"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useBrief } from "@/components/BriefStore";
import { api } from "@/lib/api";

type Row = { what: string; rule: string; source: { standard: string; clause: string | null; page: string | null; status: string };
  note: string | null; assumptions: string[] };
type View = {
  jurisdiction: string; context: Record<string, { value: unknown; from: string }>;
  rooms: { name: string; rows: Row[] }[]; groups: { key: string; title: string; rows: Row[] }[];
  checks: { check: string; actual: unknown; pass: boolean }[]; unresolved: { element: string; quantity: string; reason: string }[];
  deferred: { element: string; quantity: string; reason: string }[]; notes: string[]; defaults: string[];
  counts: { requirements: number; cited: number };
};

function Rows({ rows }: { rows: Row[] }) {
  return (
    <div className="table-wrap">
      <table className="data">
        <thead><tr><th>Requirement</th><th>Rule</th><th>Source</th></tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              <td><b>{r.what}</b>{r.note && <div className="cite">{r.note}</div>}
                {r.assumptions.map((a) => <div key={a} className="cite">assumes {a}</div>)}</td>
              <td>{r.rule}</td>
              <td>{r.source.standard}<div className="cite">{r.source.clause}{r.source.page ? `, p. ${r.source.page}` : ""}
                {r.source.status !== "transcribed" && <> <span className="tag warn">{r.source.status}</span></>}</div></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function show(v: unknown): string {
  if (v && typeof v === "object") {
    const o = v as Record<string, unknown>;
    if ("width" in o && "depth" in o) return `${o.width} x ${o.depth} m`;
    return JSON.stringify(v);
  }
  return String(v);
}

export default function StandardsView() {
  const router = useRouter();
  const { ready, done, brief, code } = useBrief();
  const [view, setView] = useState<View | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { if (ready && !(done && brief)) router.replace("/brief"); }, [ready, done, brief, router]);

  useEffect(() => {
    if (!done || !brief) return;
    api("/api/spec", { method: "POST", headers: { "Content-Type": "application/json", ...(code ? { "X-Strata-Code": code } : {}) },
                         body: JSON.stringify({ brief }) })
      .then(async (r) => { const d = await r.json(); if (!r.ok) throw new Error(d.error || `Server error ${r.status}`); setView(d); })
      .catch((e) => setError(e instanceof Error ? e.message : "Could not load the standards."));
  }, [done, brief, code]);

  if (!done || !brief) return null;
  return (
    <div className="content scroll">
      <div className="page">
        <div className="page-head">
          <h1>Standards for your house</h1>
          <p>Every value STRATA designs to, looked up in the knowledge graph of NBC 2016, the IS codes and the Guwahati
            bye-laws, with the clause and page it comes from. No language model is involved here.</p>
        </div>
        {error && <p className="error" role="alert">{error}</p>}
        {!view && !error && <div className="loading-card"><div className="grid-anim" aria-hidden>{Array.from({ length: 9 }).map((_, i) => <i key={i} style={{ animationDelay: `${i * 90}ms` }} />)}</div>
          <div><h3>Looking up the codes</h3><p>Querying the knowledge graph for your brief.</p></div></div>}
        {view && (
          <>
            <div className="stats">
              <div className="stat ok"><b>{view.counts.requirements}</b><small>requirements</small></div>
              <div className="stat ok"><b>{view.counts.cited}</b><small>with a clause cited</small></div>
              <div className={`stat ${view.checks.every((c) => c.pass) ? "ok" : "bad"}`}>
                <b>{view.checks.filter((c) => c.pass).length}/{view.checks.length}</b><small>plot checks pass</small></div>
              <div className="stat"><b>{view.jurisdiction}</b><small>jurisdiction</small></div>
            </div>
            <div className="panel-card">
              <h3>Worked out from your brief</h3>
              <ul>{Object.entries(view.context).filter(([, c]) => c.value !== null).map(([k, c]) => (
                <li key={k}><b>{k.replace(/_/g, " ")}</b>: {show(c.value)} <span className="cite">({c.from})</span></li>))}</ul>
            </div>
            <div className="table-wrap">
              <table className="data">
                <thead><tr><th>Plot check</th><th>Found</th><th></th></tr></thead>
                <tbody>{view.checks.map((c, i) => (
                  <tr key={i}><td><b>{c.check}</b></td><td>{show(c.actual)}</td>
                    <td><span className={`tag ${c.pass ? "ok" : "bad"}`}>{c.pass ? "pass" : "fail"}</span></td></tr>))}</tbody>
              </table>
            </div>
            {view.groups.map((g) => (
              <details key={g.key} className="section" open={g.key === "plot"}>
                <summary>{g.title} <small>{g.rows.length} values</small></summary>
                <Rows rows={g.rows} />
              </details>
            ))}
            {view.rooms.map((r) => (
              <details key={r.name} className="section">
                <summary>{r.name} <small>{r.rows.length} values</small></summary>
                <Rows rows={r.rows} />
              </details>
            ))}
            {(view.defaults.length > 0 || view.deferred.length > 0 || view.unresolved.length > 0) && (
              <div className="panel-card">
                <h3>Assumed, deferred or open</h3>
                <ul>
                  {view.defaults.length > 0 && <li>Defaults used for: {view.defaults.map((d) => d.replace(/_/g, " ")).join(", ")}</li>}
                  {view.deferred.map((d, i) => <li key={`d${i}`}>{d.element} {d.quantity}: {d.reason}</li>)}
                  {view.unresolved.map((u, i) => <li key={`u${i}`}>{u.element} {u.quantity}: {u.reason}</li>)}
                  {view.notes.map((n, i) => <li key={`n${i}`}>{n}</li>)}
                </ul>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
