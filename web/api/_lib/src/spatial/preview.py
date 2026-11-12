"""2D plan preview: a layout as one SVG (floors side by side), for approval and change requests.

Rooms are filled by type and labelled with their carpet area; walls, door openings,
windows, columns and the plot's setback envelope are drawn to scale. Rooms that failed a
rule-checker check are outlined in red when a checker result is passed in.
"""

from html import escape

from shapely.geometry import Point
from shapely.ops import unary_union

FILL = {"living": "#5b7fd6", "dining": "#6f8fe0", "bedroom": "#9775dc", "study": "#a685e3", "kitchen": "#1bb1d0",
        "bath_wc": "#01b7c5", "bathroom": "#01b7c5", "wc": "#01b7c5", "store": "#4a4e78", "stair": "#f0a6e6",
        "corridor": "#3a3e66", "utility": "#33365a", "balcony": "#2a6f86", "foyer": "#3a3e66"}
NAME = {"living": "Living", "dining": "Dining", "bedroom": "Bedroom", "study": "Study", "kitchen": "Kitchen",
        "bath_wc": "Bath", "store": "Store", "stair": "Stair", "corridor": "Passage", "utility": "Utility",
        "balcony": "Balcony"}


def render_svg(layout, check=None, scale=42, gap=60, margin=1.5):
    """`margin` (m) around the building; the view is cropped to the building so the plan stays legible."""
    failed = set()
    if check:
        for r in check["results"]:
            if r["pass"] is False and r["basis"] != "practice":
                failed.add(r["subject"].split(" (")[0])
    carpets = {}
    if check:
        for f in check["areas"]["floors"]:
            for rid, a in f["rooms"].items():
                carpets[(f["floor"], rid)] = a
    plot = layout.plot or {}
    xs = [x for f in layout.floors for r in f.rooms for x, _ in r.polygon]
    ys = [y for f in layout.floors for r in f.rooms for _, y in r.polygon]
    pw = plot.get("width") or max(xs)
    pd = plot.get("depth") or max(ys)
    vx0, vx1 = max(0.0, min(xs) - margin), min(pw, max(xs) + margin)
    vy0, vy1 = max(0.0, min(ys) - margin), min(pd, max(ys) + margin)
    fw, fh = (vx1 - vx0) * scale, (vy1 - vy0) * scale
    W = len(layout.floors) * (fw + gap) + gap
    H = fh + 2 * gap + 30
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W:.0f} {H:.0f}" width="{W:.0f}" height="{H:.0f}" '
           f'font-family="Comfortaa, system-ui, sans-serif">',
           f'<rect width="{W:.0f}" height="{H:.0f}" fill="#1d203e"/>']
    for i, floor in enumerate(layout.floors):
        ox = gap + i * (fw + gap)
        oy = gap + 10

        def X(x):
            return ox + (x - vx0) * scale

        def Y(y):  # y grows into the plot: draw the road side at the bottom
            return oy + (vy1 - y) * scale

        tag = floor.name or f"L{floor.level}"
        out.append(f'<text x="{ox:.1f}" y="{oy - 22:.1f}" fill="#fff" font-size="16" font-weight="700" '
                   f'font-family="Unbounded, system-ui, sans-serif">{escape(tag)}</text>')
        out.append(f'<clipPath id="v{i}"><rect x="{ox:.1f}" y="{oy:.1f}" width="{fw:.1f}" height="{fh:.1f}"/></clipPath>')
        out.append(f'<rect x="{X(0):.1f}" y="{Y(pd):.1f}" width="{pw * scale:.1f}" height="{pd * scale:.1f}" fill="none" '
                   f'stroke="rgba(255,255,255,0.25)" stroke-dasharray="6 5" clip-path="url(#v{i})"/>')
        road = "road" if vy0 <= 0.01 else "road side"
        out.append(f'<text x="{ox + fw / 2:.1f}" y="{oy + fh + 20:.1f}" fill="rgba(255,255,255,0.55)" font-size="12" '
                   f'text-anchor="middle">{road}</text>')
        for r in floor.rooms:
            pts = " ".join(f"{X(x):.1f},{Y(y):.1f}" for x, y in r.polygon)
            bad = f"{tag} {r.id}" in failed
            out.append(f'<polygon points="{pts}" fill="{FILL.get(r.type, "#444")}" fill-opacity="0.55" '
                       f'stroke="{"#ff5f7a" if bad else "rgba(255,255,255,0.15)"}" stroke-width="{3 if bad else 1}"/>')
        for w in floor.walls or []:
            out.append(f'<line x1="{X(w.start[0]):.1f}" y1="{Y(w.start[1]):.1f}" x2="{X(w.end[0]):.1f}" '
                       f'y2="{Y(w.end[1]):.1f}" stroke="#e9e6ff" stroke-width="{max(1.5, w.thickness * scale):.1f}" '
                       f'stroke-linecap="square"/>')
        for d in floor.doors + floor.windows:  # openings cut through the wall
            w = next((w for w in floor.walls or [] if w.line.distance(Point(d.at)) <= w.thickness / 2 + 0.02), None)
            if w is None:
                continue
            ux, uy = (w.end[0] - w.start[0]) / w.line.length, (w.end[1] - w.start[1]) / w.line.length
            h = d.width / 2
            a, b = (d.at[0] - ux * h, d.at[1] - uy * h), (d.at[0] + ux * h, d.at[1] + uy * h)
            is_door = hasattr(d, "between")
            colour = "#1d203e" if is_door else "#7fe9f1"
            out.append(f'<line x1="{X(a[0]):.1f}" y1="{Y(a[1]):.1f}" x2="{X(b[0]):.1f}" y2="{Y(b[1]):.1f}" '
                       f'stroke="{colour}" stroke-width="{max(2.0, w.thickness * scale + 1):.1f}"/>')
            if is_door and getattr(d, "main", False):
                out.append(f'<circle cx="{X(d.at[0]):.1f}" cy="{Y(d.at[1]):.1f}" r="5" fill="#f7cbfd"/>')
        for c in floor.columns:
            s = c.width * scale
            out.append(f'<rect x="{X(c.at[0]) - s / 2:.1f}" y="{Y(c.at[1]) - s / 2:.1f}" width="{s:.1f}" '
                       f'height="{s:.1f}" fill="#f7cbfd"/>')
        for r in floor.rooms:
            c = r.shape.representative_point()
            area = carpets.get((tag, r.id), r.shape.area)
            label = NAME.get(r.type, r.type)
            if r.type in ("bedroom", "study") and r.name:
                label = r.name
            out.append(f'<text x="{X(c.x):.1f}" y="{Y(c.y) - 2:.1f}" fill="#fff" font-size="12" font-weight="700" '
                       f'text-anchor="middle">{escape(label)}</text>')
            if r.type not in ("corridor",):
                out.append(f'<text x="{X(c.x):.1f}" y="{Y(c.y) + 13:.1f}" fill="rgba(255,255,255,0.7)" font-size="11" '
                           f'text-anchor="middle">{area:.1f} m2</text>')
    if check:
        verdict = "passes every rule check" if check["pass"] else f"{check['summary']['failures']} rule failures"
        a = check["areas"]
        far = f", FAR {a['far']:.2f}" if a.get("far") is not None else ""
        out.append(f'<text x="{gap}" y="{H - 16:.0f}" fill="rgba(255,255,255,0.75)" font-size="13">'
                   f'{escape(layout.name)}: {verdict}; carpet {a["carpet_total"]:.1f} m2, built up '
                   f'{a["built_up_total"]:.1f} m2{far}</text>')
    out.append("</svg>")
    return "\n".join(out)


__all__ = ["render_svg", "unary_union"]
