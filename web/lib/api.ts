/** Where the web app's API calls go.
 *
 * Locally (`next dev`) and on a plain Vercel build, `/api/*` on the same site: the dev proxy or Vercel's
 * Python functions (brief, standards, layout; the later stages then show the saved demo run).
 * When the build sets NEXT_PUBLIC_STRATA_WORKER_URL (the workstation's ngrok address), the browser calls
 * the workstation directly, so STAAD and Revit run live and there is no Vercel time or size limit. If the
 * workstation does not answer its health check, the app falls back to the same-site API. */

const WORKER = (process.env.NEXT_PUBLIC_STRATA_WORKER_URL || "").replace(/\/+$/, "");
// ngrok's free plan shows a warning page to browsers unless this header is sent
const SKIP = { "ngrok-skip-browser-warning": "1" };

export type Health = {
  ok: boolean; workstation: boolean; staad?: boolean; revit?: boolean; revit_template?: boolean;
  revit_busy?: boolean; revit_error?: string | null; llm?: string; llm_model?: string; via?: "workstation" | "site";
};

let base: Promise<string> | null = null;
let last: Health | null = null;

async function probe(url: string, headers: Record<string, string> = {}): Promise<Health | null> {
  try {
    const res = await fetch(`${url}/api/health`, { headers, cache: "no-store", signal: AbortSignal.timeout(6000) });
    return res.ok ? ((await res.json()) as Health) : null;
  } catch {
    return null;
  }
}

/** The API origin: the workstation if it is configured and answering, else this site (""). */
export function apiBase(): Promise<string> {
  if (!base) {
    base = (async () => {
      if (WORKER) {
        const h = await probe(WORKER, SKIP);
        if (h?.workstation) { last = { ...h, via: "workstation" }; return WORKER; }
      }
      return "";
    })();
  }
  return base;
}

/** fetch() for an API path ("/api/brief"), sent to wherever apiBase() points. */
export async function api(path: string, init: RequestInit = {}): Promise<Response> {
  const b = await apiBase();
  const headers = new Headers(init.headers);
  if (b) for (const [k, v] of Object.entries(SKIP)) headers.set(k, v);
  return fetch(`${b}${path}`, { ...init, headers });
}

/** Fresh status of the workstation (STAAD, Revit) for the status light; `retry` re-checks the workstation. */
export async function health(retry = false): Promise<Health> {
  if (retry) base = null;
  const b = await apiBase();
  const h = await probe(b, b ? SKIP : {});
  last = h ? { ...h, via: b ? "workstation" : "site" } : { ok: false, workstation: false, via: b ? "workstation" : "site" };
  return last;
}

export function lastHealth() { return last; }

/** Save a workstation file (.rvt, .ifc, .building.json) through the API, with the access code. */
export async function download(name: string, code: string) {
  const res = await api(`/api/files?name=${encodeURIComponent(name)}`, { headers: code ? { "X-Strata-Code": code } : {} });
  if (!res.ok) throw new Error(res.status === 404 ? `${name} is not on the workstation.` : `Download failed (${res.status}).`);
  const blob = await res.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 10_000);
}
