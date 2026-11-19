"use client";

import Link from "next/link";
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useBrief } from "@/components/BriefStore";
import { useDesign } from "@/components/DesignStore";
import { Beam, Check, Restart } from "@/components/Icons";

const STOREY = ["Ground storey", "First storey", "Second storey", "Third storey", "Fourth storey"];
const LEVEL = ["Plinth", "First floor", "Second floor", "Third floor", "Fourth floor", "Fifth floor"];

export function groupLabel(g: string) {
  const [kind, n] = g.split("-");
  const k = Number(n);
  return kind === "column" ? `${STOREY[k] ?? `Storey ${k}`} columns` : `${LEVEL[k] ?? `Level ${k}`} beams`;
}

export function Working({ title, text }: { title: string; text: string }) {
  return (
    <div className="loading-card">
      <div className="grid-anim" aria-hidden>{Array.from({ length: 9 }).map((_, i) => <i key={i} style={{ animationDelay: `${i * 90}ms` }} />)}</div>
      <div><h3>{title}</h3><p>{text}</p></div>
    </div>
  );
}

export function DemoNote() {
  const { demo } = useDesign();
  return demo ? <div className="note-banner">Demo run: the 40 x 60 ft example designed on the STRATA workstation. STAAD.Pro
    is not reachable from here, so a live design needs the workstation online (see the status light).</div> : null;
}

export default function StructureView() {
  const router = useRouter();
  const { ready } = useBrief();
  const { approved, job, startDesign, showDemo, reset, error } = useDesign();

  useEffect(() => { if (ready && !approved && job.state !== "complete") router.replace("/layout"); },
    [ready, approved, job.state, router]);
  if (!approved && job.state !== "complete") return null;
  const r = job.result;

  return (
    <div className="content scroll">
      <div className="page">
        <div className="page-head">
          <h1>Structural design</h1>
          <p>STAAD.Pro analyses the RC frame for dead, imposed, wind (IS 875) and earthquake (IS 1893) loads and designs every
            member to IS 456 and IS 13920. STRATA resizes and reruns until every member and joint passes, then sizes the
            footings from the support reactions.</p>
        </div>
        <DemoNote />
        {error && <div className="note-banner">{error}</div>}

        <div className="split">
          {approved?.svg && <div className="plan small" dangerouslySetInnerHTML={{ __html: approved.svg }} />}
          <div className="panel-card">
            <h3>{approved?.name ?? r?.layout}</h3>
            {job.state === "idle" && (
              <>
                <p>The approved layout becomes a frame: columns at wall junctions, beams on the walls, slabs and the stair
                  cabin, loaded with the walls, finishes, imposed loads and the overhead tank. This takes about a minute.</p>
                <div className="btn-row" style={{ marginLeft: 0 }}>
                  <button type="button" className="btn" onClick={startDesign} disabled={!approved?.layout}><Beam size={18} /> Design structure</button>
                  <button type="button" className="btn ghost" onClick={showDemo}>Show the demo run</button>
                </div>
              </>
            )}
            {job.state === "running" && <p>Running STAAD.Pro{job.elapsed ? `, ${Math.round(job.elapsed)} s so far` : ""}. Each round appears
              below as it finishes{job.stage === "building" ? "; now building the services layers and the building JSON." : "."}</p>}
            {job.state === "complete" && r && (
              <p><span className="tag ok">pass</span> All members and joints pass after {r.attempts.at(-1)?.rounds ?? job.rounds.length} rounds;
                slab {r.design.slab_mm} mm, {r.footings.length} footings.</p>
            )}
            {(job.state === "stopped" || job.state === "failed") && (
              <>
                <p><span className="tag bad">{job.state}</span> {job.reason}</p>
                {job.advice && job.advice.length > 0 && <ul>{job.advice.map((a) => <li key={a}>{a}</li>)}</ul>}
                <button type="button" className="btn ghost" onClick={reset}><Restart size={18} /> Try again</button>
              </>
            )}
          </div>
        </div>

        {job.state === "running" && <Working title="Designing the frame" text="STAAD.Pro runs on the Windows side; rounds arrive as they finish." />}

        {job.rounds.length > 0 && job.rounds[0].sections && (
          <div className="panel-card">
            <h3>Design rounds</h3>
            <ul className="rounds">{job.rounds.map((x, i) => (
              <li key={i}><b>Round {x.number}</b>
                <span><span className={`tag ${x.passed ? "ok" : "warn"}`}>{x.passed ? "pass" : "resize"}</span>{" "}
                  {x.changes && x.changes.length > 0 ? x.changes.slice(0, 3).join("; ") : x.passed ? "every member and joint passes" : ""}</span></li>))}
            </ul>
          </div>
        )}

        {r && (
          <>
            <div className="table-wrap">
              <table className="data">
                <thead><tr><th>Member group</th><th>Section (mm)</th></tr></thead>
                <tbody>{Object.entries(r.design.sections).map(([g, s]) => (
                  <tr key={g}><td><b>{groupLabel(g)}</b> <span className="cite">{g}</span></td><td>{s}</td></tr>))}
                  <tr><td><b>Slab</b></td><td>{r.design.slab_mm} thick</td></tr></tbody>
              </table>
            </div>
            <details className="section">
              <summary>Footings <small>{r.footings.length} pads, top at -1.5 m</small></summary>
              <div className="table-wrap"><table className="data">
                <thead><tr><th>Footing</th><th>Size (m)</th><th>Bearing</th></tr></thead>
                <tbody>{r.footings.map((f) => (
                  <tr key={f.id}><td><b>{f.id}</b></td><td>{f.width} x {f.length} x {f.thickness}</td>
                    <td>{f.design?.bearing_kn_m2 !== undefined ? `${f.design.bearing_kn_m2} of ${f.design.allowed_kn_m2} kN/m2` : "-"}</td></tr>))}</tbody>
              </table></div>
            </details>
            <div className="panel-card">
              <h3>Loads and code basis</h3>
              <ul>{r.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
            </div>
            <div className="done-card">
              <Check size={22} />
              <div><h3>Structure designed</h3><p>Next: plumbing, electrical and finishes, then the building JSON for Revit.</p></div>
              <div className="btn-row"><Link className="btn" href="/services">Services</Link></div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
