"""Parse STAAD.Pro output (.anl) into per-member design results.

Reads what STRATA needs for the resize loop and for Revit parameters:
member size, steel required/provided, column interaction ratio, and the
code-check warnings STAAD prints (e.g. IS 13920 strong-column/weak-beam).
The per-joint IS 13920 table comes from STAAD's <name>_13920.txt report,
which the runners append to the .anl text (TRACK 4 also prints it in the .anl).

One check is STRATA's own: IS 456 minimum column steel on the gross area.
"""

import re
from dataclasses import dataclass, field

_BEAM_HEAD = re.compile(r"B E A M\s+N O\.\s+(\d+)\s+D E S I G N")
_COL_HEAD = re.compile(r"C O L U M N\s+N O\.\s+(\d+)\s+D E S I G N")
_BEAM_SIZE = re.compile(r"SIZE:\s+([\d.]+) mm X\s+([\d.]+) mm")
_COL_SIZE = re.compile(r"CROSS SECTION:\s+([\d.]+) mm X\s+([\d.]+) mm")
_BEAM_ROW = re.compile(
    r"^\s*([\d.]+) \|\s*([\d.]+)/\s*([\d.]+)\(\s*(\d+)-(\d+)d \)\|"
    r"\s*([\d.]+)/\s*([\d.]+)\(\s*(\d+)-(\d+)d \)\|\s*(\d+)d\s+@\s+(\d+) mm",
    re.M,
)
_PU = re.compile(r"DESIGN AXIAL FORCE \(Pu\)\s+:\s+([\d.]+)")
_REQ_STEEL = re.compile(r"REQD\. STEEL AREA\s+:\s+([\d.]+)")
_MAIN_BARS = re.compile(r"MAIN REINFORCEMENT : Provide\s+(\d+) - (\d+) dia\.\s+\(\s*([\d.]+)%,\s+([\d.]+) Sq\.mm")
_IR_PROVIDED = re.compile(r"REINFORCEMENT PROVIDED.*?IR:\s+([\d.]+)", re.S)
_BASE_SHEAR = re.compile(r"TIME PERIOD FOR ([XZ]) 1893 LOADING =\s+([\d.]+).*?VB PER 1893=\s+([\d.]+) X\s+([\d.]+)=\s+([\d.]+)", re.S)
_VERSION = re.compile(r"Version\s+([\d.]+)")
_WARNING = re.compile(r"\*\*\* ?WARNING ?:(.*?)(?=\n\s*\n)", re.S)
# STAAD prints errors both as "*** ERROR ..." and as a bare "ERROR: ..." with continuation lines
_ERROR = re.compile(r"^\s*(?:\*+\s*)?ERROR\s*:?(.*?)(?=\n\s*\n|\n\s*(?:\*+\s*)?ERROR|\Z)", re.S | re.M)
_ERROR_MEMBER = re.compile(r"\b(BEAM|COLUMN|MEMBER)\s+(\d+)")
_DESIGN_CMD = re.compile(r"^\s*\d+\.\s+DESIGN (BEAM|COLUMN) ([\d TO]+)$", re.M)
_FAIL = re.compile(r"FAIL", re.I)
_JOINT_TABLE = "RELATIVE STRENGTHS OF BEAMS AND COLUMNS AT A JOINT"
_STATUS = ("P", "F", "-")

# IS 456 cl. 26.5.3.1(a): column steel >= 0.8% of the gross area. STAAD applies
# the 0.8% to the concrete area it needs, so a column bigger than needed falls short.
MIN_COLUMN_STEEL_PCT = 0.8


@dataclass
class BeamResult:
    member: int
    width_mm: float
    depth_mm: float
    max_top_required: float = 0.0
    max_top_provided: float = 0.0
    max_bottom_required: float = 0.0
    max_bottom_provided: float = 0.0
    min_stirrup_spacing_mm: int = 0
    failed: bool = False
    errors: list = field(default_factory=list)


@dataclass
class ColumnResult:
    member: int
    width_mm: float
    depth_mm: float
    axial_kn: float = 0.0
    steel_required: float = 0.0
    bars: str = ""
    steel_ratio_pct: float = 0.0
    interaction_ratio: float = 0.0  # with steel provided; must be <= 1.0
    failed: bool = False
    errors: list = field(default_factory=list)
    below_min_steel: bool = False  # STRATA check, IS 456 cl. 26.5.3.1(a)


@dataclass
class JointCheck:
    """One row of STAAD's IS 13920 cl. 7.2 table: a column end and the members meeting there."""
    column: int
    joint: int
    other_column: int = 0  # column on the far side of the joint; 0 when there is none (roof, base)
    beams_x: tuple = ()
    beams_z: tuple = ()
    status_x: str = "-"  # "P" pass, "F" fail, "-" not checked
    status_z: str = "-"

    @property
    def failed(self) -> bool:
        return "F" in (self.status_x, self.status_z)

    @property
    def failed_directions(self) -> str:
        return "".join(d for d, st in (("X", self.status_x), ("Z", self.status_z)) if st == "F")

    @property
    def failed_beams(self) -> tuple:
        return (self.beams_x if self.status_x == "F" else ()) + (self.beams_z if self.status_z == "F" else ())


@dataclass
class StaadResults:
    version: str = ""
    beams: dict = field(default_factory=dict)
    columns: dict = field(default_factory=dict)
    base_shear: dict = field(default_factory=dict)  # direction -> {"period", "coeff", "weight", "vb"}
    warnings: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    general_errors: list = field(default_factory=list)  # errors not tied to any member: resizing cannot fix them
    joint_strength_failed: bool = False  # IS 13920 cl. 7.2 strong-column/weak-beam
    joints: list = field(default_factory=list)  # JointCheck rows; empty when STAAD's joint report is missing

    @property
    def failed_joints(self) -> list:
        return [j for j in self.joints if j.failed]

    @property
    def all_passed(self) -> bool:
        return (not self.errors and not self.joint_strength_failed
                and not any(b.failed for b in self.beams.values())
                and not any(c.failed or c.below_min_steel for c in self.columns.values()))


def _blocks(text, pattern):
    """Yield (member id, text up to the next member header or end of that design section)."""
    heads = list(pattern.finditer(text))
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else text.find("END OF", m.end())
        yield int(m.group(1)), text[m.start():end if end != -1 else len(text)]


def _expand(id_list):
    """'1 TO 4 7' -> [1, 2, 3, 4, 7]"""
    tokens, ids, i = id_list.split(), [], 0
    while i < len(tokens):
        if i + 2 < len(tokens) and tokens[i + 1] == "TO":
            ids += range(int(tokens[i]), int(tokens[i + 2]) + 1)
            i += 3
        else:
            ids.append(int(tokens[i]))
            i += 1
    return ids


def _member(token):
    return 0 if token == "-" else int(token)


def parse_joint_table(text: str) -> list:
    """JointCheck rows of the IS 13920 relative-strength table.

    A row starting with a column number is that column's first joint; the
    next row without one is its other joint. TRACK 4 output in the .anl lists
    only the failing rows.
    """
    start = text.find(_JOINT_TABLE)
    if start == -1:
        return []
    rows, column = [], 0
    for line in text[start:].splitlines()[1:]:
        tokens = line.split()
        is_row = (len(tokens) in (8, 9) and tokens[-1] in _STATUS and tokens[-2] in _STATUS
                  and all(t == "-" or t.isdigit() for t in tokens[:-2]))
        if not is_row:
            if (rows and tokens) or "FINISH" in line:
                break
            continue
        if len(tokens) == 9:
            column, tokens = int(tokens[0]), tokens[1:]
        joint, other, bx1, bx2, bz1, bz2, sx, sz = tokens
        rows.append(JointCheck(column, int(joint), _member(other),
                               tuple(_member(b) for b in (bx1, bx2) if b != "-"),
                               tuple(_member(b) for b in (bz1, bz2) if b != "-"), sx, sz))
    return rows


def parse_anl(text: str) -> StaadResults:
    res = StaadResults()
    if m := _VERSION.search(text):
        res.version = m.group(1)
    for d, period, coeff, weight, vb in _BASE_SHEAR.findall(text):
        res.base_shear[d] = {"period": float(period), "coeff": float(coeff),
                             "weight": float(weight), "vb": float(vb)}

    for member, block in _blocks(text, _BEAM_HEAD):
        size = _BEAM_SIZE.search(block)
        beam = BeamResult(member, float(size.group(1)), float(size.group(2)))
        rows = _BEAM_ROW.findall(block)
        if rows:
            beam.max_top_required = max(float(r[1]) for r in rows)
            beam.max_top_provided = max(float(r[2]) for r in rows)
            beam.max_bottom_required = max(float(r[5]) for r in rows)
            beam.max_bottom_provided = max(float(r[6]) for r in rows)
            beam.min_stirrup_spacing_mm = min(int(r[10]) for r in rows)
        beam.errors = [" ".join(e.split()) for e in _ERROR.findall(block)]
        beam.failed = not rows or bool(_FAIL.search(block)) or bool(beam.errors)
        res.beams[member] = beam

    for member, block in _blocks(text, _COL_HEAD):
        size = _COL_SIZE.search(block)
        col = ColumnResult(member, float(size.group(1)), float(size.group(2)))
        if m := _PU.search(block):
            col.axial_kn = float(m.group(1))
        if m := _REQ_STEEL.search(block):
            col.steel_required = float(m.group(1))
        if m := _MAIN_BARS.search(block):
            col.bars = f"{m.group(1)}-{m.group(2)}d"
            col.steel_ratio_pct = float(m.group(3))
        if m := _IR_PROVIDED.search(block):
            col.interaction_ratio = float(m.group(1))
        col.errors = [" ".join(e.split()) for e in _ERROR.findall(block)]
        col.failed = (not _MAIN_BARS.search(block) or col.interaction_ratio > 1.0
                      or bool(_FAIL.search(block)) or bool(col.errors))
        col.below_min_steel = bool(col.bars) and col.steel_ratio_pct < MIN_COLUMN_STEEL_PCT
        res.columns[member] = col

    res.warnings = sorted({" ".join(w.split()) for w in _WARNING.findall(text)})
    res.errors = [" ".join(e.split()) for e in _ERROR.findall(text)]

    # Members STAAD was asked to design but reported no result for count as failed
    for kind, ids in _DESIGN_CMD.findall(text):
        table, cls = (res.beams, BeamResult) if kind == "BEAM" else (res.columns, ColumnResult)
        for member in _expand(ids):
            if member not in table:
                table[member] = cls(member, 0.0, 0.0, failed=True)
    # Attach member-specific errors (e.g. IS 13920 cl. 7.1.1 column size) to that member
    for err in res.errors:
        if m := _ERROR_MEMBER.search(err):
            table = res.columns if m.group(1) == "COLUMN" else res.beams
            if (member := int(m.group(2))) in table and err not in table[member].errors:
                table[member].failed = True
                table[member].errors.append(err)
    attached = {e for t in (res.beams, res.columns) for r in t.values() for e in r.errors}
    res.general_errors = [e for e in res.errors if e not in attached]
    res.joints = parse_joint_table(text)
    res.joint_strength_failed = "Relative strength checks have failed" in text or bool(res.failed_joints)
    return res


def read_anl(path) -> StaadResults:
    with open(path, encoding="latin-1") as f:
        return parse_anl(f.read())
