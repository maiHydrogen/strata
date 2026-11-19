"use client";

import Link from "next/link";
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useBrief } from "@/components/BriefStore";
import { useDesign } from "@/components/DesignStore";
import { Check } from "@/components/Icons";
import { DemoNote } from "@/components/StructureView";

const KIND: Record<string, string> = {
  wc: "WC", washbasin: "Washbasin", shower: "Shower", kitchen_sink: "Kitchen sink", washing_machine: "Washing machine",
  light: "Light", fan: "Fan", socket_6a: "6 A socket", socket_16a: "16 A outlet", switchboard: "Switch board",
  distribution_board: "Distribution board",
};

export function useResult() {
  const router = useRouter();
  const { ready } = useBrief();
  const { job } = useDesign();
  useEffect(() => { if (ready && !job.result) router.replace("/structure"); }, [ready, job.result, router]);
  return job.result;
}

export default function ServicesView() {
  const r = useResult();
  if (!r) return null;
  const s = r.services;
  const roomName = Object.fromEntries(s.rooms.map((x) => [x.id, `${x.name} (${x.level})`]));
  const byRoom: Record<string, Record<string, number>> = {};
  for (const f of s.fixtures) {
    const k = f.use ? `${KIND[f.kind] ?? f.kind} (${f.use})` : KIND[f.kind] ?? f.kind;
    byRoom[f.room] = byRoom[f.room] ?? {};
    byRoom[f.room][k] = (byRoom[f.room][k] ?? 0) + 1;
  }
  const passed = s.checks.filter((c) => c.pass).length;
  const t = s.tank;

  return (
    <div className="content scroll">
      <div className="page">
        <div className="page-head">
          <h1>Plumbing, electrical and finishes</h1>
          <p>Room templates place the fixtures and points; NBC 2016 Parts 8 and 9 set the minimum fitments, the circuit limits
            and the overhead tank. Your finish and appliance choices come from the brief; anything you did not say is a
            stated default.</p>
        </div>
        <DemoNote />
        <div className="stats">
          <div className={`stat ${passed === s.checks.length ? "ok" : "bad"}`}><b>{passed}/{s.checks.length}</b><small>code checks pass</small></div>
          <div className="stat"><b>{s.fixtures.length}</b><small>fixtures and points</small></div>
          <div className="stat"><b>{s.circuits.length}</b><small>sub-circuits</small></div>
          <div className="stat"><b>{t.capacity_l} l</b><small>overhead tank</small></div>
        </div>

        <div className="table-wrap">
          <table className="data">
            <thead><tr><th>Check</th><th>Found</th><th>Source</th><th></th></tr></thead>
            <tbody>{s.checks.map((c, i) => (
              <tr key={i}><td><b>{c.check}</b></td><td>{c.detail}</td>
                <td className="cite">{c.source?.standard?.split(" (")[0]}{c.source?.clause && c.source.clause !== "-" ? `, ${c.source.clause}` : ""}
                  {c.source?.page && c.source.page !== "-" ? `, p. ${c.source.page}` : ""}</td>
                <td><span className={`tag ${c.pass ? "ok" : "bad"}`}>{c.pass ? "pass" : "fail"}</span></td></tr>))}</tbody>
          </table>
        </div>

        <div className="panel-card">
          <h3>Overhead tank and stacks</h3>
          <p>{t.capacity_l} litres ({t.diameter} m across, {t.height} m tall) on the stair cabin roof for {t.persons} persons:
            {" "}{t.persons} x {t.demand_lphd} l/day x half a day = {t.required_l} l. Full, it weighs {t.weight_kn} kN, which the
            structure carries.</p>
          <ul>{s.pipes.map((p) => <li key={p.id}>{p.kind} stack {p.id}, {Math.round(p.diameter * 1000)} mm, ground to {p.z1} m, serving{" "}
            {p.serves.map((x) => roomName[x] ?? x).join(", ")}</li>)}</ul>
        </div>

        <details className="section" open>
          <summary>Fixtures and points by room <small>{Object.keys(byRoom).length} rooms</small></summary>
          <div className="table-wrap"><table className="data">
            <thead><tr><th>Room</th><th>Fixtures and points</th></tr></thead>
            <tbody>{Object.entries(byRoom).map(([room, kinds]) => (
              <tr key={room}><td><b>{roomName[room] ?? room}</b></td>
                <td>{Object.entries(kinds).map(([k, n]) => `${n} x ${k}`).join(", ")}</td></tr>))}</tbody>
          </table></div>
        </details>

        <details className="section">
          <summary>Sub-circuits <small>NBC Part 8 Sec 2 cl. 5.4.4.3</small></summary>
          <div className="table-wrap"><table className="data">
            <thead><tr><th>Circuit</th><th>Kind</th><th>Points</th><th>Load</th><th>Board</th></tr></thead>
            <tbody>{s.circuits.map((c) => (
              <tr key={c.id}><td><b>{c.id}</b></td><td>{c.kind}</td><td>{c.points.length}</td><td>{c.load_w} W</td>
                <td className="cite">{c.board}</td></tr>))}</tbody>
          </table></div>
        </details>

        <details className="section">
          <summary>Finish schedule <small>{s.finishes.length} rooms</small></summary>
          <div className="table-wrap"><table className="data">
            <thead><tr><th>Room</th><th>Floor</th><th>Walls</th><th>Skirting</th></tr></thead>
            <tbody>{s.finishes.map((f) => (
              <tr key={f.room}><td><b>{roomName[f.room] ?? f.room}</b></td><td>{f.floor}</td>
                <td>{f.wall}{f.dado ? `; ${f.dado} to ${f.dado_height} m` : ""}</td><td>{f.skirting ?? "-"}</td></tr>))}</tbody>
          </table></div>
        </details>

        <div className="panel-card"><h3>Notes</h3><ul>{s.notes.map((n, i) => <li key={i}>{n}</li>)}</ul></div>
        <div className="done-card">
          <Check size={22} />
          <div><h3>All five layers designed</h3><p>Next: the building JSON that the Revit builder reads.</p></div>
          <div className="btn-row"><Link className="btn" href="/building">Building JSON</Link></div>
        </div>
      </div>
    </div>
  );
}
