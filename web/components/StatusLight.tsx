"use client";

/** The workstation status in the menu: is the design server reachable, and are STAAD and Revit running.
 * Click to check again (after starting Revit or the tunnel). */

import { useEffect, useState } from "react";
import { type Health, health } from "@/lib/api";

const MODEL: Record<string, string> = { "gemma-4-26b-a4b-it": "Gemma 4 26B", "openai/gpt-oss-120b": "GPT-OSS 120B (Groq)",
  "openai/gpt-oss-20b": "GPT-OSS 20B (Groq)", "qwen/qwen3.8-27b": "Qwen3.8 27B (Groq)" };

export function modelName(h: Health | null) {
  return h?.llm_model ? MODEL[h.llm_model] ?? h.llm_model : "Gemma 4 26B";
}

export default function StatusLight({ onHealth }: { onHealth?: (h: Health) => void }) {
  const [h, setH] = useState<Health | null>(null);
  const [checking, setChecking] = useState(false);

  async function check(retry: boolean) {
    setChecking(true);
    const r = await health(retry);
    setH(r); onHealth?.(r); setChecking(false);
  }

  useEffect(() => {
    void check(false);
    const t = setInterval(() => void check(false), 60_000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const rows: [string, boolean | undefined, string][] = h?.workstation
    ? [["STAAD.Pro", h.staad, h.staad ? "ready" : "not found"],
       ["Revit", h.revit && h.revit_template, h.revit ? (h.revit_template ? (h.revit_busy ? "building" : "ready") : "no template") : "offline"]]
    : [];
  return (
    <button type="button" className="status-light" onClick={() => void check(true)} title="Check the workstation again"
            aria-label="Workstation status; click to check again">
      <span className="status-row">
        <span className={`led${h?.workstation ? "" : " off"}${checking ? " busy" : ""}`} />
        <b>{h === null ? "Checking…" : h.workstation ? "Workstation online" : "Workstation offline"}</b>
      </span>
      {h && !h.workstation && <small>Live STAAD and Revit need the STRATA workstation; later pages show the demo run.</small>}
      {rows.map(([name, ok, text]) => (
        <span key={name} className="status-row sub"><span>{name}</span><span className={`tag ${ok ? "ok" : "bad"}`}>{text}</span></span>
      ))}
    </button>
  );
}
