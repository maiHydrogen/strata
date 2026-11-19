"use client";

/** The full-tab app frame: the STRATA pipeline menu, the page in the middle, the brief panel on the right.
 * Navigation is by URL (/brief, /layout), so back, forward, refresh and bookmarks work. */

import { useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Beam, Book, Braces, Chat, Cube, Logo, Pipe, Plan, Restart, Spark } from "@/components/Icons";
import { type Item, useBrief } from "@/components/BriefStore";
import { useDesign } from "@/components/DesignStore";
import StatusLight, { modelName } from "@/components/StatusLight";
import type { Health } from "@/lib/api";

const STAGES = [
  { name: "Brief", icon: Chat, href: "/brief", after: "" },
  { name: "Standards", icon: Book, href: "/standards", after: "after brief" },
  { name: "Layout", icon: Plan, href: "/layout", after: "after brief" },
  { name: "Structure", icon: Beam, href: "/structure", after: "approve a layout" },
  { name: "Services", icon: Pipe, href: "/services", after: "after structure" },
  { name: "Building JSON", icon: Braces, href: "/building", after: "after structure" },
  { name: "Revit model", icon: Cube, href: "/revit", after: "after structure" },
];

export default function Shell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const router = useRouter();
  const { done, brief, busy, panel, restart } = useBrief();
  const { approved, job } = useDesign();
  const [h, setH] = useState<Health | null>(null);
  const briefDone = done && brief !== null;
  const open = (href?: string) => href === "/brief"
    || ((href === "/standards" || href === "/layout") && briefDone)
    || (href === "/structure" && (!!approved || job.state === "complete"))
    || ((href === "/services" || href === "/building" || href === "/revit") && !!job.result);
  const current = STAGES.find((s) => s.href && path.startsWith(s.href));
  const needed = (panel?.items ?? []).filter((i) => (i.group ?? "needed") === "needed");
  const also = (panel?.items ?? []).filter((i) => i.group === "also");
  const pct = panel && needed.length ? Math.round((100 * needed.filter((i) => i.status !== "missing").length) / needed.length) : 0;

  return (
    <div className="app">
      <aside className="menu" aria-label="STRATA pipeline">
        <header className="menu-head">
          <Link href="/brief" className="menu-brand" aria-label="STRATA home"><Logo size={28} /><h2>Strata</h2></Link>
        </header>
        <ul>
          {STAGES.map(({ name, icon: Icon, href, after }) => {
            const on = href !== undefined && href === current?.href;
            const body = (
              <>
                <Icon size={20} />
                <span>{name}</span>
                {!open(href) && after && <em>{after}</em>}
              </>
            );
            return (
              <li key={name} className={on ? "on" : href ? (open(href) ? "open" : "wait") : "off"}>
                {open(href) && href
                  ? <Link href={href} aria-current={on ? "page" : undefined}>{body}</Link>
                  : <span className="disabled" aria-disabled>{body}</span>}
              </li>
            );
          })}
        </ul>
        <div className="menu-foot">
          <div className="wave" aria-hidden>
            {Array.from({ length: 28 }).map((_, i) => (
              <i key={i} style={{ height: `${18 + 26 * Math.abs(Math.sin(i * 0.7))}%`, animationDelay: `${i * 60}ms` }}
                 className={busy ? "live" : ""} />
            ))}
          </div>
          <StatusLight onHealth={setH} />
          <div className="model">
            <span className={`led${busy ? " busy" : ""}`} />
            <div>
              <b>{modelName(h)}</b>
              <small>{busy ? "reading your reply" : "ready"}</small>
            </div>
          </div>
        </div>
      </aside>

      <main className="main">
        <div className="topbar">
          <div className="pill"><Spark size={16} /> {current?.name === "Brief" || !current ? "Brief chat" : current.name}</div>
        </div>
        {children}
      </main>

      <aside className="inspector" aria-label="Your brief">
        <header className="inspector-head">
          <h2>Your brief</h2>
          <button type="button" className="icon-btn" onClick={() => { restart(); router.push("/brief"); }}
                  title="Start over" aria-label="Start over">
            <Restart size={20} />
          </button>
        </header>
        <div className="ring">
          <svg viewBox="0 0 120 120" aria-hidden>
            <defs>
              <linearGradient id="rg" x1="0" y1="0" x2="1" y2="1">
                <stop offset="0" stopColor="#f7cbfd" /><stop offset=".5" stopColor="#5b7fd6" /><stop offset="1" stopColor="#01b7c5" />
              </linearGradient>
            </defs>
            {[0, 1, 2].map((k) => (
              <path key={k} d="M60 8 105 34v52L60 112 15 86V34Z" className="ghost"
                    transform={`rotate(${k * 20 - 20} 60 60)`} />
            ))}
            <path d="M60 8 105 34v52L60 112 15 86V34Z" className="track" />
            <path d="M60 8 105 34v52L60 112 15 86V34Z" className="fill" pathLength={100}
                  style={{ strokeDasharray: `${pct} 100`, opacity: pct ? 1 : 0 }} />
          </svg>
          <div className="ring-label"><b>{pct}%</b><small>known</small></div>
        </div>
        <p className="ring-note">
          {panel ? `${panel.given} of ${panel.total} needed answers from you` : "Waiting for your first message"}
        </p>
        <div className="items">
          {needed.length > 0 && <h3 className="items-head">Needed</h3>}
          {needed.map((it) => <BriefItem key={it.path} it={it} />)}
          {also.length > 0 && (
            <>
              <h3 className="items-head">Also used <small>{panel?.also_given ?? 0} from you, the rest assumed; say any of these in the chat to change them</small></h3>
              {also.map((it) => <BriefItem key={it.path} it={it} />)}
            </>
          )}
        </div>
      </aside>
    </div>
  );
}

function BriefItem({ it }: { it: Item }) {
  return (
    <div className={`item ${it.status}${it.asking ? " asking" : ""}`}>
      <div className="item-top">
        <span>{it.label}{it.item && <small className="item-ref"> {it.item}</small>}</span>
        <em>{it.asking ? "asking now" : it.status === "given" ? "from you" : it.status === "assumed" ? "assumed" : "missing"}</em>
      </div>
      <p>{it.value ?? "Not given yet"}</p>
    </div>
  );
}
