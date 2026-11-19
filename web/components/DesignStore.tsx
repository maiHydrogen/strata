"use client";

/** After the brief: the approved layout and the structure job (STAAD rounds, then the building JSON).
 * Kept in localStorage so a refresh or a page change keeps them; a running job is polled again after a
 * refresh. Where STAAD cannot be reached (a hosted deployment) the saved demo run is shown instead. */

import { createContext, useContext, useEffect, useRef, useState } from "react";
import { load, save, useBrief } from "@/components/BriefStore";
import { api } from "@/lib/api";

export type Approved = { name: string; svg: string; layout: unknown };
export type Round = { layout: string; number: number; sections: Record<string, string> | null; passed: boolean | null;
  changes?: string[] };
export type Check = { check: string; pass: boolean; detail: string; source?: { standard?: string; clause?: string; page?: string } };
export type Fixture = { id: string; kind: string; system: string; room: string; level: string; z: number; use?: string;
  circuit?: string | null; load_w: number };
export type Result = {
  layout: string;
  design: { sections: Record<string, string>; slab_mm: number; rounds?: unknown[]; notes?: string[] };
  notes: string[];
  attempts: { layout: string; passed: boolean; rounds: number; reason: string | null }[];
  footings: { id: string; width: number; length: number; thickness: number; design?: Record<string, unknown> }[];
  services: {
    checks: Check[]; notes: string[]; tank: Record<string, number | string>;
    circuits: { id: string; level: string; kind: string; board: string | null; points: string[]; load_w: number }[];
    pipes: { id: string; kind: string; z0: number; z1: number; diameter: number; serves: string[] }[];
    finishes: { room: string; level: string; floor: string; wall: string; dado: string | null; dado_height: number;
      skirting: string | null; ceiling: string }[];
    fixtures: Fixture[];
    rooms: { id: string; name: string; level: string }[];
  };
  counts: Record<string, number>;
  building: Record<string, unknown>;
  copied_to: string | null;
  building_file: string;
};
export type Job = {
  id?: string; state: "idle" | "running" | "complete" | "stopped" | "failed"; stage?: string; rounds: Round[];
  result?: Result; reason?: string; advice?: string[]; elapsed?: number; demo?: boolean;
};

const STORE = "strata-design-v1";
const IDLE: Job = { state: "idle", rounds: [] };

type Store = {
  approved: Approved | null; approve: (a: Approved) => void; job: Job; demo: boolean;
  startDesign: () => void; showDemo: () => void; reset: () => void; error: string | null;
};
const Ctx = createContext<Store | null>(null);

export function useDesign() {
  const s = useContext(Ctx);
  if (!s) throw new Error("useDesign outside DesignProvider");
  return s;
}

export function DesignProvider({ children }: { children: React.ReactNode }) {
  const { brief, code, done } = useBrief();
  const [approved, setApproved] = useState<Approved | null>(null);
  const [job, setJob] = useState<Job>(IDLE);
  const [error, setError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const headers = { "Content-Type": "application/json", ...(code ? { "X-Strata-Code": code } : {}) };

  useEffect(() => {
    const s = load<{ approved: Approved | null; job: Job }>(STORE);
    if (s) { setApproved(s.approved); setJob(s.job ?? IDLE); if (s.job?.state === "running" && s.job.id) poll(s.job.id); }
    setReady(true);
    return () => { if (timer.current) clearTimeout(timer.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => { if (ready) save(STORE, { approved, job }); }, [ready, approved, job]);

  useEffect(() => {  // a new brief (Start over) clears the design
    if (ready && !done) { setApproved(null); setJob(IDLE); }
  }, [ready, done]);

  async function poll(id: string) {
    try {
      const res = await api("/api/structure", { method: "POST", headers, body: JSON.stringify({ action: "status", job: id }) });
      if (res.status === 404) { setJob({ ...IDLE, state: "failed", reason: "The design job was lost (the API restarted). Run it again." }); return; }
      const st = await res.json();
      setJob({ id, state: st.state, stage: st.stage, rounds: st.rounds ?? [], result: st.result, reason: st.reason,
               advice: st.advice, elapsed: st.elapsed });
      if (st.state === "running") timer.current = setTimeout(() => poll(id), 2000);
    } catch {
      timer.current = setTimeout(() => poll(id), 4000);  // the API may be busy with STAAD; try again
    }
  }

  async function showDemo() {
    const res = await fetch("/demo/run.json");
    const d = await res.json();
    setApproved({ name: `${d.layout.name} (demo)`, svg: d.layout.svg, layout: null });
    setJob({ state: "complete", rounds: d.rounds ?? [], result: d.result, demo: true });
  }

  async function startDesign() {
    if (!approved?.layout || !brief) return;
    setError(null);
    setJob({ state: "running", rounds: [], stage: "structure" });
    try {
      const res = await api("/api/structure", { method: "POST", headers,
        body: JSON.stringify({ action: "start", brief, layout: approved.layout }) });
      const data = await res.json();
      if (res.status === 503 && data.local_only) {
        setJob(IDLE);
        setError("STAAD.Pro runs on the STRATA workstation, which this server cannot reach. Showing the saved demo run.");
        await showDemo();
        return;
      }
      if (!res.ok) throw new Error(data.error || `Server error ${res.status}`);
      setJob({ id: data.job, state: "running", rounds: [], stage: "structure" });
      poll(data.job);
    } catch (e) {
      setJob(IDLE);
      setError(e instanceof Error ? e.message : "Could not start the design.");
    }
  }

  function approve(a: Approved) {
    setApproved(a);
    setJob(IDLE);
  }

  function reset() { setJob(IDLE); setError(null); }

  return (
    <Ctx.Provider value={{ approved, approve, job, demo: !!job.demo, startDesign, showDemo, reset, error }}>
      {children}
    </Ctx.Provider>
  );
}
