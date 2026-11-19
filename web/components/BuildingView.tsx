"use client";

import Link from "next/link";
import { Check, Download } from "@/components/Icons";
import { DemoNote } from "@/components/StructureView";
import { useResult } from "@/components/ServicesView";

const LAYERS: [string, string[]][] = [
  ["Architectural", ["levels", "walls", "openings", "floors", "rooms", "stairs"]],
  ["Structural", ["footings", "columns", "beams"]],
  ["Plumbing", ["pipes", "tanks"]],
  ["Electrical", ["circuits"]],
  ["Finishing", ["finishes"]],
];

export function downloadJson(data: unknown, name: string) {
  const blob = new Blob([JSON.stringify(data, null, 1)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  URL.revokeObjectURL(a.href);
}

export default function BuildingView() {
  const r = useResult();
  if (!r) return null;
  const b = r.building as Record<string, unknown>;
  const keys = Object.keys(b).filter((k) => !["schema_version", "name"].includes(k));

  return (
    <div className="content scroll">
      <div className="page">
        <div className="page-head">
          <h1>Building JSON</h1>
          <p>The single source of truth for the design: every element of the five layers with its size, position and
            STAAD result. It is validated before it leaves STRATA, and the Revit builder turns it into native elements
            without any language model.</p>
        </div>
        <DemoNote />
        <div className="stats">
          <div className="stat ok"><b>v{String(b.schema_version)}</b><small>schema, validated</small></div>
          <div className="stat"><b>{r.counts.fixtures}</b><small>fixtures and points</small></div>
          <div className="stat"><b>{r.counts.columns + r.counts.beams + r.counts.footings}</b><small>structural members</small></div>
          <div className="stat"><b>{r.counts.walls + r.counts.openings}</b><small>walls and openings</small></div>
        </div>
        <div className="table-wrap">
          <table className="data">
            <thead><tr><th>Layer</th><th>Elements</th></tr></thead>
            <tbody>{LAYERS.map(([name, ks]) => (
              <tr key={name}><td><b>{name}</b></td>
                <td>{ks.map((k) => `${r.counts[k] ?? 0} ${k}`).join(", ")}{name === "Plumbing" || name === "Electrical" ?
                  `, ${r.services.fixtures.filter((f) => (name === "Plumbing" ? f.system === "plumbing" : f.system !== "plumbing")).length} fixtures` : ""}</td></tr>))}</tbody>
          </table>
        </div>
        <div className="panel-card">
          <h3>{r.building_file}</h3>
          <p>{r.copied_to ? <>Copied to the Revit side as <code>{r.copied_to}</code>.</> : "Download it and put it in the STRATA folder on the Windows side."}</p>
          <button type="button" className="btn" onClick={() => downloadJson(b, r.building_file)}><Download size={18} /> Download</button>
        </div>
        {keys.map((k) => {
          const v = b[k];
          const n = Array.isArray(v) ? `${v.length} items` : typeof v === "object" && v ? "object" : String(v);
          const text = JSON.stringify(v, null, 1);
          return (
            <details key={k} className="section">
              <summary>{k} <small>{n}</small></summary>
              <pre className="json">{text.length > 20000 ? `${text.slice(0, 20000)}\n... (${text.length - 20000} more characters in the download)` : text}</pre>
            </details>
          );
        })}
        <div className="done-card">
          <Check size={22} />
          <div><h3>Ready for Revit</h3><p>Next: build the native model and check its IFC export.</p></div>
          <div className="btn-row"><Link className="btn" href="/revit">Revit model</Link></div>
        </div>
      </div>
    </div>
  );
}
