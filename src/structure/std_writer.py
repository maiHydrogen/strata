"""Generate STAAD.Pro input (.std) files for regular RCC frames.

Deterministic: the same frame always produces the same text. Units are
metre / kN throughout. STAAD's global Y axis is vertical.

Codes applied:
- Loads: IS 875 Part 1 (dead) and Part 2 (imposed)
- Seismic: IS 1893 (Part 1) equivalent static method
- Combinations: IS 456 Table 18 / IS 1893 clause 6.3
- Concrete design: IS 456, optionally IS 13920 ductile design
"""

from dataclasses import dataclass, field

DEFAULT_COLUMN_BAR = 12  # mm, IS 456 cl. 26.5.3.1(c): column bars at least 12 mm
BAR_SIZES = (12, 16, 20, 25)  # mm, main bar catalogue


@dataclass
class Section:
    depth: float  # local y (YD), m
    width: float  # local z (ZD), m


@dataclass
class Member:
    start: int
    end: int
    kind: str  # "column" or "beam"
    level: int  # storey index: columns of storey i are level i, beams at the top of storey i are level i


@dataclass
class SeismicParams:
    zone_factor: float = 0.36  # Zone V (Guwahati)
    response_reduction: float = 5.0  # SMRF
    importance: float = 1.0
    soil_type: int = 2  # medium soil
    structure_type: int = 1  # RC frame
    damping: float = 0.05


@dataclass
class Loads:
    slab_thickness: float = 0.125  # m
    concrete_density: float = 25.0  # kN/m3, IS 875 Part 1
    floor_finish: float = 1.0  # kN/m2
    floor_live: float = 2.0  # kN/m2, IS 875 Part 2, residential rooms
    roof_live: float = 1.5  # kN/m2, accessible flat roof
    wall_thickness: float = 0.23  # m
    masonry_density: float = 20.0  # kN/m3
    parapet_height: float = 1.0  # m

    def floor_dead(self) -> float:
        return self.slab_thickness * self.concrete_density + self.floor_finish


@dataclass
class Frame:
    nodes: dict = field(default_factory=dict)  # id -> (x, y, z)
    members: dict = field(default_factory=dict)  # id -> Member
    sections: dict = field(default_factory=dict)  # group name ("column-0", "beam-1", ...) -> Section
    column_bars: dict = field(default_factory=dict)  # column group -> minimum main bar diameter, mm (STAAD MINMAIN)
    storey_height: float = 3.0
    storeys: int = 2
    loads: Loads = field(default_factory=Loads)
    seismic: SeismicParams = field(default_factory=SeismicParams)
    fck: float = 25.0  # MPa
    fy: float = 500.0  # MPa
    ductile: bool = True  # IS 13920 design

    def group_of(self, member_id: int) -> str:
        """Members are sized in groups: columns per storey, beams per floor level."""
        m = self.members[member_id]
        return f"{m.kind}-{m.level}"

    def section(self, member_id: int) -> Section:
        return self.sections[self.group_of(member_id)]

    def min_bar(self, group: str) -> int:
        return self.column_bars.get(group, DEFAULT_COLUMN_BAR)

    def beam_bar_cap(self, level: int) -> int:
        """Largest beam bar at floor `level`: IS 13920 cl. 7.1.1(a) needs every column
        meeting there (the storeys below and above) at least 20 x the beam bar diameter."""
        below = self.sections[f"column-{level - 1}"]
        above = self.sections.get(f"column-{level}", below)
        least_mm = min(below.depth, below.width, above.depth, above.width) * 1000
        fitting = [d for d in BAR_SIZES if 20 * d <= least_mm + 1e-6]
        return fitting[-1] if fitting else BAR_SIZES[0]


def make_rc_frame(bays_x, bays_z, storeys=2, storey_height=3.0,
                  column=Section(0.30, 0.30), beam=Section(0.45, 0.23)) -> Frame:
    """Regular grid frame. bays_x / bays_z are lists of bay lengths in metres."""
    xs = [0.0]
    for b in bays_x:
        xs.append(xs[-1] + b)
    zs = [0.0]
    for b in bays_z:
        zs.append(zs[-1] + b)

    sections = {f"column-{lvl}": column for lvl in range(storeys)}
    sections.update({f"beam-{lvl}": beam for lvl in range(1, storeys + 1)})
    frame = Frame(storey_height=storey_height, storeys=storeys, sections=sections)
    ids = {}
    nid = 1
    for level in range(storeys + 1):
        for z in zs:
            for x in xs:
                frame.nodes[nid] = (x, level * storey_height, z)
                ids[(x, level, z)] = nid
                nid += 1

    mid = 1
    for level in range(storeys):
        for z in zs:
            for x in xs:
                frame.members[mid] = Member(ids[(x, level, z)], ids[(x, level + 1, z)], "column", level)
                mid += 1
    for level in range(1, storeys + 1):
        for z in zs:
            for x0, x1 in zip(xs, xs[1:]):
                frame.members[mid] = Member(ids[(x0, level, z)], ids[(x1, level, z)], "beam", level)
                mid += 1
        for x in xs:
            for z0, z1 in zip(zs, zs[1:]):
                frame.members[mid] = Member(ids[(x, level, z0)], ids[(x, level, z1)], "beam", level)
                mid += 1
    return frame


def _num(v):
    return f"{v:g}"


def _id_list(ids):
    """STAAD member lists: '1 TO 4 7 9 TO 10'."""
    ids = sorted(ids)
    parts, i = [], 0
    while i < len(ids):
        j = i
        while j + 1 < len(ids) and ids[j + 1] == ids[j] + 1:
            j += 1
        parts.append(f"{ids[i]} TO {ids[j]}" if j > i else str(ids[i]))
        i = j + 1
    return " ".join(parts)


def _yrange(y):
    return f"YRANGE {_num(y - 0.1)} {_num(y + 0.1)}"


def _grouped(values: dict) -> list:
    """{member: value} -> [(value, [members])] sorted by value, for compact STAAD lists."""
    by_value = {}
    for i, v in values.items():
        by_value.setdefault(v, []).append(i)
    return sorted(by_value.items())


def wall_line_loads(frame: Frame) -> dict:
    """Masonry line load (kN/m) carried by each beam.

    A floor beam carries the wall of the storey above, up to the soffit of the
    beam above it; a roof beam carries the parapet.
    """
    L = frame.loads
    loads = {}
    for i, m in frame.members.items():
        if m.kind != "beam":
            continue
        if m.level == frame.storeys:
            height = L.parapet_height
        else:
            height = frame.storey_height - frame.sections[f"beam-{m.level + 1}"].depth
        loads[i] = round(L.wall_thickness * L.masonry_density * height, 3)
    return loads


def _slab_share(span, across):
    """Equivalent UDL factor (m) for a two-way panel on a beam of length `span`.

    Triangular load when the beam is the short side (w * lx / 3), trapezoidal
    when it is the long side (w * lx / 2 * (1 - lx^2 / (3 ly^2))).
    """
    lx, ly = min(span, across), max(span, across)
    if span <= across:
        return lx / 3.0
    return lx / 2.0 * (1 - lx ** 2 / (3 * ly ** 2))


def beam_gravity_udl(frame: Frame) -> dict:
    """1.2(DL + LL) equivalent UDL per beam in kN/m, for IS 13920 cl. 6.3.3 shear design."""
    L = frame.loads
    xs = sorted({x for x, _, _ in frame.nodes.values()})
    zs = sorted({z for _, _, z in frame.nodes.values()})
    walls = wall_line_loads(frame)
    udl = {}
    for i, m in frame.members.items():
        if m.kind != "beam":
            continue
        (x0, _, z0), (x1, _, z1) = frame.nodes[m.start], frame.nodes[m.end]
        roof = m.level == frame.storeys
        if z0 == z1:  # beam along X; panels on either side in Z
            span, line, across_lines = abs(x1 - x0), z0, zs
        else:
            span, line, across_lines = abs(z1 - z0), x0, xs
        k = across_lines.index(line)
        widths = [across_lines[k] - across_lines[k - 1]] if k > 0 else []
        widths += [across_lines[k + 1] - across_lines[k]] if k + 1 < len(across_lines) else []
        w_slab = L.floor_dead() + (L.roof_live if roof else L.floor_live)
        slab = sum(w_slab * _slab_share(span, c) for c in widths)
        sec = frame.section(i)
        self_wt = sec.depth * sec.width * L.concrete_density
        udl[i] = 1.2 * (self_wt + walls[i] + slab)
    return udl


def write_std(frame: Frame, title="STRATA frame") -> str:
    L = frame.loads
    columns = [i for i, m in frame.members.items() if m.kind == "column"]
    beams = [i for i, m in frame.members.items() if m.kind == "beam"]
    base_nodes = [i for i, (_, y, _) in frame.nodes.items() if y == 0.0]
    floor_levels = [lvl * frame.storey_height for lvl in range(1, frame.storeys + 1)]
    roof_y = floor_levels[-1]
    walls = _grouped(wall_line_loads(frame))
    e_conc = 5000 * frame.fck ** 0.5 * 1000  # IS 456 cl. 6.2.3.1, kN/m2
    s = frame.seismic

    out = [
        "STAAD SPACE",
        "START JOB INFORMATION",
        f"JOB NAME {title}",
        "END JOB INFORMATION",
        "INPUT WIDTH 79",
        "UNIT METER KN",
        "JOINT COORDINATES",
    ]
    out += [f"{i} {_num(x)} {_num(y)} {_num(z)};" for i, (x, y, z) in frame.nodes.items()]
    out.append("MEMBER INCIDENCES")
    out += [f"{i} {m.start} {m.end};" for i, m in frame.members.items()]
    out += [
        "DEFINE MATERIAL START",
        "ISOTROPIC CONCRETE",
        f"E {e_conc:.6g}",
        "POISSON 0.17",
        f"DENSITY {_num(L.concrete_density)}",
        "ALPHA 1e-05",
        "DAMP 0.05",
        "TYPE CONCRETE",
        f"STRENGTH FCU {_num(frame.fck * 1000)}",
        "END DEFINE MATERIAL",
        "MEMBER PROPERTY INDIAN",
    ]
    by_section = {}
    for i in columns + beams:
        sec = frame.section(i)
        by_section.setdefault((sec.depth, sec.width), []).append(i)
    for (depth, width), ids in by_section.items():
        out.append(f"{_id_list(ids)} PRISMATIC YD {_num(depth)} ZD {_num(width)}")
    out += [
        "CONSTANTS",
        "MATERIAL CONCRETE ALL",
        "SUPPORTS",
        f"{_id_list(base_nodes)} FIXED",
        # IS 1893 seismic weight: full DL + 25% of imposed load <= 3 kN/m2 (cl. 7.3.1)
        "DEFINE 1893 LOAD",
        f"ZONE {_num(s.zone_factor)} RF {_num(s.response_reduction)} I {_num(s.importance)} "
        f"SS {s.soil_type} ST {s.structure_type} DM {_num(s.damping)}",
        "SELFWEIGHT 1",
        "MEMBER WEIGHT",
    ]
    out += [f"{_id_list(ids)} UNI {_num(w)}" for w, ids in walls]
    out += [
        "FLOOR WEIGHT",
    ]
    for y in floor_levels:
        live = 0.0 if y == roof_y else L.floor_live  # roof imposed load excluded (IS 1893 cl. 7.3.2)
        out.append(f"{_yrange(y)} FLOAD {_num(round(L.floor_dead() + 0.25 * live, 3))}")

    out += [
        "LOAD 1 LOADTYPE SEISMIC TITLE EQ X",
        "1893 LOAD X 1",
        "LOAD 2 LOADTYPE SEISMIC TITLE EQ Z",
        "1893 LOAD Z 1",
        "LOAD 3 LOADTYPE DEAD TITLE DL",
        "SELFWEIGHT Y -1",
        "MEMBER LOAD",
    ]
    out += [f"{_id_list(ids)} UNI GY {_num(-w)}" for w, ids in walls]
    out += [
        "FLOOR LOAD",
    ]
    out += [f"{_yrange(y)} FLOAD {_num(-round(L.floor_dead(), 3))} GY" for y in floor_levels]
    out += ["LOAD 4 LOADTYPE LIVE TITLE LL", "FLOOR LOAD"]
    out += [
        f"{_yrange(y)} FLOAD {_num(-(L.roof_live if y == roof_y else L.floor_live))} GY"
        for y in floor_levels
    ]

    # IS 456 Table 18 / IS 1893 cl. 6.3.1.2
    combos = [("1.5(DL+LL)", {3: 1.5, 4: 1.5})]
    for name, eq in (("EQX", 1), ("EQZ", 2)):
        for sign, sym in ((1, "+"), (-1, "-")):
            combos += [
                (f"1.2(DL+LL{sym}{name})", {3: 1.2, 4: 1.2, eq: 1.2 * sign}),
                (f"1.5(DL{sym}{name})", {3: 1.5, eq: 1.5 * sign}),
                (f"0.9DL{sym}1.5{name}", {3: 0.9, eq: 1.5 * sign}),
            ]
    for n, (name, factors) in enumerate(combos, start=101):
        out.append(f"LOAD COMB {n} {name}")
        out.append(" ".join(f"{case} {_num(f)}" for case, f in sorted(factors.items())))

    out += [
        "PERFORM ANALYSIS PRINT STATICS CHECK",
        f"LOAD LIST {_id_list(range(101, 101 + len(combos)))}",
        "START CONCRETE DESIGN",
        "CODE IS13920" if frame.ductile else "CODE INDIAN",
        "UNIT MMS NEWTON",
        f"FC {_num(frame.fck)} ALL",
        f"FYMAIN {_num(frame.fy)} ALL",
        f"FYSEC {_num(frame.fy)} ALL",
        "TRACK 2 ALL",
    ]
    # Bigger column bars raise the column's moment capacity (IS 13920 cl. 7.2) and its steel ratio
    for group, bar in sorted(frame.column_bars.items()):
        out.append(f"MINMAIN {bar} MEMB {_id_list(i for i in columns if frame.group_of(i) == group)}")
    if frame.ductile:  # cap beam bars so the columns at each joint satisfy IS 13920 cl. 7.1.1(a)
        for level in range(1, frame.storeys + 1):
            ids = [i for i in beams if frame.members[i].level == level]
            out.append(f"MAXMAIN {frame.beam_bar_cap(level)} MEMB {_id_list(ids)}")
    # IS 13920 shear design needs the gravity UDL on each beam; STAAD's EUDL is in current units (N/mm = kN/m)
    eudl = {i: round(w, 2) for i, w in beam_gravity_udl(frame).items()}
    out += [f"EUDL {_num(w)} MEMB {_id_list(ids)}" for w, ids in _grouped(eudl)]
    out += [
        f"DESIGN BEAM {_id_list(beams)}",
        f"DESIGN COLUMN {_id_list(columns)}",
        "END CONCRETE DESIGN",
        "FINISH",
    ]
    return "\n".join(out) + "\n"
