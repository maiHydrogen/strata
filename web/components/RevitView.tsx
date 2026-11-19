"use client";

import { ChangeEvent, useEffect, useRef, useState } from "react";
import { load, save, useBrief } from "@/components/BriefStore";
import { useDesign } from "@/components/DesignStore";
import { Check, Cube, Download } from "@/components/Icons";
import { DemoNote, Working } from "@/components/StructureView";
import { useResult } from "@/components/ServicesView";
import { api, download, health } from "@/lib/api";

type Report = { schema: string; passed: boolean; checks: { name: string; passed: boolean; detail: string }[];
  info: Record<string, string | number> };
type RevitJob = { id: string; state: "queued" | "running" | "complete" | "failed"; stage?: string; reason?: string;
  text?: string; files?: { rvt?: string; ifc?: string }; ifc_check?: Report; elapsed?: number; seconds?: number;
  building_file?: string };

const STORE = "strata-revit-v1";

function b64(buf: ArrayBuffer) {
  const bytes = new Uint8Array(buf);
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(s);
}

function IfcReport({ report }: { report: Report }) {
  return (
    <>
      <div className="stats">
        <div className={`stat ${report.passed ? "ok" : "bad"}`}><b>{report.checks.filter((c) => c.passed).length}/{report.checks.length}</b>
          <small>IFC checks pass</small></div>
        <div className="stat"><b>{report.schema}</b><small>IFC schema</small></div>
      </div>
      <div className="table-wrap"><table className="data">
        <thead><tr><th>Check</th><th>Found</th><th></th></tr></thead>
        <tbody>{report.checks.map((c) => (
          <tr key={c.name}><td><b>{c.name}</b></td><td>{c.detail}</td>
            <td><span className={`tag ${c.passed ? "ok" : "bad"}`}>{c.passed ? "pass" : "fail"}</span></td></tr>))}</tbody>
      </table></div>
      <div className="panel-card"><h3>Also in the file</h3>
        <ul>{Object.entries(report.info).map(([k, v]) => <li key={k}><b>{k}</b>: {String(v)}</li>)}</ul></div>
    </>
  );
}

export default function RevitView() {
  const r = useResult();
  const { code } = useBrief();
  const { demo } = useDesign();
  const [live, setLive] = useState<boolean | null>(null);  // Revit reachable through the API
  const [job, setJob] = useState<RevitJob | null>(null);
  const [busy, setBusy] = useState(false);
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const headers = { "Content-Type": "application/json", ...(code ? { "X-Strata-Code": code } : {}) };

  useEffect(() => {
    void health().then((h) => setLive(!!(h.workstation && h.revit && h.revit_template)));
    const saved = load<RevitJob>(STORE);
    if (r && saved && saved.building_file === r.building_file) {
      setJob(saved);
      if (saved.state === "queued" || saved.state === "running") poll(saved.id);
    }
    return () => { if (timer.current) clearTimeout(timer.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [r?.building_file]);

  useEffect(() => { if (job) save(STORE, job); }, [job]);

  if (!r) return null;
  const file = r.copied_to ?? `C:\\Users\\Public\\Documents\\STRATA\\${r.building_file}`;

  async function poll(id: string) {
    try {
      const res = await api("/api/revit", { method: "POST", headers, body: JSON.stringify({ action: "status", job: id }) });
      if (res.status === 404) {
        setJob((j) => j && { ...j, state: "failed", reason: "The Revit job was lost (the STRATA server restarted). Build again." });
        return;
      }
      const st = (await res.json()) as RevitJob;
      setJob(st);
      if (st.state === "queued" || st.state === "running") timer.current = setTimeout(() => poll(id), 3000);
    } catch {
      timer.current = setTimeout(() => poll(id), 5000);
    }
  }

  async function buildInRevit() {
    if (!r) return;
    setError(null);
    try {
      const res = await api("/api/revit", { method: "POST", headers,
        body: JSON.stringify({ action: "start", building_file: r.building_file }) });
      const data = await res.json();
      if (!res.ok) {
        if (data.local_only) setLive(false);
        throw new Error(data.error || `Server error ${res.status}`);
      }
      const j: RevitJob = { id: data.job, state: "queued", stage: "waiting for Revit", building_file: r.building_file };
      setJob(j);
      poll(data.job);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not start the Revit build.");
    }
  }

  async function save_(name: string) {
    try { await download(name, code); } catch (e) { setError(e instanceof Error ? e.message : "Download failed."); }
  }

  async function check(e: ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (!f || !r) return;
    setBusy(true); setError(null); setReport(null);
    try {
      const res = await api("/api/ifc", { method: "POST", headers,
        body: JSON.stringify({ building: r.building, ifc_base64: b64(await f.arrayBuffer()) }) });
      const d = await res.json();
      if (!res.ok) throw new Error(d.local_only ? "The IFC check runs on the STRATA workstation (IfcOpenShell is not on this server)." : d.error || `Server error ${res.status}`);
      setReport(d);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not check the IFC.");
    } finally {
      setBusy(false);
      e.target.value = "";
    }
  }

  const running = job && (job.state === "queued" || job.state === "running");
  const canBuild = live && !demo && !running;
  const manual = (
    <>
      <div className="panel-card">
        <h3>Build it in Revit 2027 by hand</h3>
        <ol className="steps">
          <li>Start a new project from <code>Default-Multi-Discipline_Metric</code>.</li>
          <li>Insert, Load Autodesk Family: <code>M_Concrete-Rectangular-Column</code>, <code>M_Concrete-Rectangular Beam</code> and
            {" "}<code>M_Footing-Rectangular</code>.</li>
          <li>Open Dynamo, set the Code Block to <code>{file.replace(/\\/g, "/")}</code> and run the STRATA Python node once
            (or use the pyRevit button STRATA, Build From JSON).</li>
          <li>File, Export, IFC: choose IFC4 and save it next to the building JSON.</li>
          <li>Upload the IFC below to check it.</li>
        </ol>
      </div>
      <div className="panel-card">
        <h3>Check an IFC export</h3>
        <p>Counts every element class against the building JSON, checks that each door and window sits in a wall opening,
          that every element belongs to a storey, and that the services export as typed elements.</p>
        <label className="btn" style={{ marginLeft: 0, display: "inline-flex" }}>
          <Cube size={18} /> Upload .ifc
          <input type="file" accept=".ifc" onChange={check} hidden />
        </label>
      </div>
      {busy && <Working title="Checking the IFC" text="Reading it with IfcOpenShell." />}
      {report && <IfcReport report={report} />}
    </>
  );

  return (
    <div className="content scroll">
      <div className="page">
        <div className="page-head">
          <h1>Native Revit model and IFC</h1>
          <p>The Revit builder (plain code, no language model) reads the building JSON and creates native footings, columns,
            beams, walls, doors, windows, slabs, rooms, stairs, pipes and fixtures. Revit then exports IFC, which STRATA checks
            against the building JSON.</p>
        </div>
        <DemoNote />

        <div className="panel-card">
          <h3>Build in Revit on the STRATA workstation</h3>
          <p>{demo ? "This is the saved demo run, so there is nothing to build here; design your own house on the workstation."
            : live === null ? "Checking whether Revit is running on the workstation…"
            : live ? "Revit 2027 is running with the STRATA listener. One click builds the model, exports IFC and checks it."
            : "Revit is not reachable from here (the workstation, Revit, or its STRATA template is offline). Use the manual steps below, or start Revit and check the status light again."}</p>
          <div className="btn-row">
            <button type="button" className="btn" disabled={!canBuild} onClick={buildInRevit}>
              <Cube size={18} /> {job?.state === "complete" ? "Build again" : "Build in Revit"}
            </button>
            <button type="button" className="btn ghost" onClick={() => save_(r.building_file)} disabled={demo || !live}>
              <Download size={18} /> Building JSON
            </button>
          </div>
        </div>

        {running && <Working title={job.state === "queued" ? "Waiting for Revit" : "Building in Revit"}
          text={`${job.stage ?? "working"}${job.elapsed ? `, ${Math.round(job.elapsed)} s` : ""}. A two-storey house takes about a minute.`} />}
        {error && <div className="note-banner">{error}</div>}
        {job?.state === "failed" && <div className="note-banner">The Revit build failed: {job.reason}</div>}

        {job?.state === "complete" && (
          <>
            <div className="done-card">
              <Check size={22} />
              <div><h3>Native Revit model built</h3>
                <p>{job.seconds ? `${Math.round(job.seconds)} s in Revit, ` : ""}{job.ifc_check ? "IFC exported and checked." : "No IFC was written; see the report."}</p></div>
              <div className="btn-row">
                {job.files?.rvt && <button type="button" className="btn" onClick={() => save_(job.files!.rvt!)}><Download size={18} /> .rvt</button>}
                {job.files?.ifc && <button type="button" className="btn" onClick={() => save_(job.files!.ifc!)}><Download size={18} /> .ifc</button>}
              </div>
            </div>
            {job.ifc_check && <IfcReport report={job.ifc_check} />}
            {job.text && <details className="section"><summary>Revit build report</summary><pre className="json">{job.text}</pre></details>}
          </>
        )}

        <details className="section" open={!live && !demo ? true : undefined}>
          <summary>Manual steps and IFC upload <small>if the listener is offline</small></summary>
          {manual}
        </details>
      </div>
    </div>
  );
}
