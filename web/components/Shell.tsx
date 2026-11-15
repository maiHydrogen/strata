"use client";

/** The full-tab app frame: icon rail, pipeline menu, the page in the middle, the brief panel on the right.
 * Navigation is by URL (/brief, /layout), so back, forward, refresh and bookmarks work. */

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Beam, Book, Braces, Chat, Cube, Logo, Pipe, Plan, Restart, Spark } from "@/components/Icons";
import { useBrief } from "@/components/BriefStore";

const STAGES = [
  { name: "Brief", icon: Chat, href: "/brief" },
  { name: "Standards", icon: Book },
  { name: "Layout", icon: Plan, href: "/layout" },
  { name: "Structure", icon: Beam },
  { name: "Services", icon: Pipe },
  { name: "Building JSON", icon: Braces },
  { name: "Revit model", icon: Cube },
];

export default function Shell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const router = useRouter();
  const { done, brief, busy, panel, restart } = useBrief();
  const open = (href?: string) => href === "/brief" || (href === "/layout" && done && brief !== null);
  const current = STAGES.find((s) => s.href && path.startsWith(s.href));
  const pct = panel ? Math.round((100 * panel.items.filter((i) => i.status !== "missing").length) / panel.total) : 0;

  return (
    <div className="app">
      <nav className="rail" aria-label="STRATA">
        <Link href="/brief" className="rail-logo" aria-label="STRATA home"><Logo /></Link>
        <div className="rail-icons">
          {STAGES.slice(0, 4).map(({ name, icon: Icon, href }) => {
            const on = href !== undefined && href === current?.href;
            return open(href) && href ? (
              <Link key={name} href={href} className={`rail-btn${on ? " on" : ""}`} title={name} aria-label={name}
                    aria-current={on ? "page" : undefined}>
                <Icon size={20} />
              </Link>
            ) : (
              <span key={name} className="rail-btn off" title={`${name} (not yet)`} aria-disabled><Icon size={20} /></span>
            );
          })}
        </div>
      </nav>

      <aside className="menu">
        <header className="menu-head"><h2>Pipeline</h2></header>
        <ul>
          {STAGES.map(({ name, icon: Icon, href }) => {
            const on = href !== undefined && href === current?.href;
            const body = (
              <>
                <Icon size={20} />
                <span>{name}</span>
                {!href && <em>soon</em>}
                {href === "/layout" && !open(href) && <em>after brief</em>}
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
          <div className="model">
            <span className={`led${busy ? " busy" : ""}`} />
            <div>
              <b>Gemma 4 26B</b>
              <small>{busy ? "reading your reply" : "ready"}</small>
            </div>
          </div>
        </div>
      </aside>

      <main className="main">
        <div className="topbar">
          <div className="pill"><Spark size={16} /> {current?.name === "Layout" ? "Layout" : "Brief chat"}</div>
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
          {panel ? `${panel.given} of ${panel.total} answered by you` : "Waiting for your first message"}
        </p>
        <div className="items">
          {(panel?.items ?? []).map((it) => (
            <div key={it.path} className={`item ${it.status}${it.asking ? " asking" : ""}`}>
              <div className="item-top">
                <span>{it.label}</span>
                <em>{it.asking ? "asking now" : it.status === "given" ? "from you" : it.status === "assumed" ? "assumed" : "missing"}</em>
              </div>
              <p>{it.value ?? "Not given yet"}</p>
            </div>
          ))}
        </div>
      </aside>
    </div>
  );
}
