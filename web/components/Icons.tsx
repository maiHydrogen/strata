// Line icons drawn for STRATA (24 px grid, currentColor stroke).
type P = { size?: number; className?: string };

const base = (size = 24) => ({
  width: size, height: size, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor",
  strokeWidth: 1.7, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true,
});

export const Logo = ({ size = 40 }: P) => (
  <svg width={size} height={size} viewBox="0 0 40 40" aria-hidden>
    <defs>
      <linearGradient id="lg" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stopColor="#f7cbfd" />
        <stop offset="0.5" stopColor="#9775dc" />
        <stop offset="1" stopColor="#1bb1d0" />
      </linearGradient>
    </defs>
    <path d="M20 2.5 35.2 11.25v17.5L20 37.5 4.8 28.75v-17.5Z" fill="url(#lg)" />
    <path d="M11 17.5 20 13l9 4.5-9 4.5Z" fill="#fff" fillOpacity=".95" />
    <path d="M11 21.5l9 4.5 9-4.5" stroke="#fff" strokeOpacity=".8" strokeWidth="1.6" fill="none" strokeLinejoin="round" />
    <path d="M11 25.5l9 4.5 9-4.5" stroke="#fff" strokeOpacity=".55" strokeWidth="1.6" fill="none" strokeLinejoin="round" />
  </svg>
);

export const Chat = ({ size, className }: P) => (
  <svg {...base(size)} className={className}><path d="M4 5h16v11H9l-5 4Z" /><path d="M8 9.5h8M8 12.5h5" /></svg>
);
export const Book = ({ size, className }: P) => (
  <svg {...base(size)} className={className}><path d="M5 4h10a3 3 0 0 1 3 3v13H8a3 3 0 0 1-3-3Z" /><path d="M5 17a3 3 0 0 1 3-3h10" /></svg>
);
export const Plan = ({ size, className }: P) => (
  <svg {...base(size)} className={className}><rect x="4" y="4" width="16" height="16" rx="1.5" /><path d="M4 12h7v8M11 4v5M15 12h5" /></svg>
);
export const Beam = ({ size, className }: P) => (
  <svg {...base(size)} className={className}><path d="M4 7h16M4 17h16M7 7v10M17 7v10" /><path d="m7 7 10 10" /></svg>
);
export const Pipe = ({ size, className }: P) => (
  <svg {...base(size)} className={className}><path d="M5 4v7a3 3 0 0 0 3 3h11" /><path d="M16 11v6M9 4v4" /></svg>
);
export const Braces = ({ size, className }: P) => (
  <svg {...base(size)} className={className}><path d="M9 4c-2 0-2.5 1-2.5 3s-.5 4-2.5 5c2 1 2.5 3 2.5 5s.5 3 2.5 3M15 4c2 0 2.5 1 2.5 3s.5 4 2.5 5c-2 1-2.5 3-2.5 5s-.5 3-2.5 3" /></svg>
);
export const Cube = ({ size, className }: P) => (
  <svg {...base(size)} className={className}><path d="m12 3 8 4.5v9L12 21l-8-4.5v-9Z" /><path d="m4 7.5 8 4.5 8-4.5M12 12v9" /></svg>
);
export const Send = ({ size, className }: P) => (
  <svg {...base(size)} className={className}><path d="M4 12 20 4l-5 16-3.5-6.5Z" /><path d="m11.5 13.5 3-3" /></svg>
);
export const Download = ({ size, className }: P) => (
  <svg {...base(size)} className={className}><path d="M12 4v11M7 10.5l5 5 5-5M5 20h14" /></svg>
);
export const Restart = ({ size, className }: P) => (
  <svg {...base(size)} className={className}><path d="M4 12a8 8 0 1 0 2.4-5.7" /><path d="M4 4v4.5h4.5" /></svg>
);
export const Check = ({ size, className }: P) => (
  <svg {...base(size)} className={className}><path d="m5 12.5 4.5 4.5L19 7.5" /></svg>
);
export const Lock = ({ size, className }: P) => (
  <svg {...base(size)} className={className}><rect x="5" y="10.5" width="14" height="9.5" rx="2" /><path d="M8 10.5V8a4 4 0 0 1 8 0v2.5" /></svg>
);
export const Spark = ({ size, className }: P) => (
  <svg {...base(size)} className={className}><path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6" /></svg>
);
