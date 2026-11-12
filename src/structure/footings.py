"""Isolated footings from STAAD's support reactions (LOD 200: plan size and thickness).

Pads that would overlap (columns either side of a 1.2 m passage) become one combined footing.

Plan size: the service reaction plus 10% for the footing and the soil over it, on the
safe bearing capacity; for DL + 0.8 LL + 0.8 EL the bearing pressure may be 50% higher
(IS 1893 Part 1:2016 cl. 6.3.5.2, Table 1, medium soil, isolated footings). Thickness: the
factored load against punching shear 0.25 sqrt(fck) (IS 456 cl. 31.6.3.1) and one-way
shear at d from the column face (IS 456 cl. 34.2.4.1, Table 19). Square footings, no
moments: the plinth beams tie the footings together and take the column base moments
(practice; the reinforcement is not designed at LOD 200).
"""

import math
from dataclasses import dataclass

from src.structure.std_writer import SERVICE_START

SELF_WEIGHT = 1.10  # footing and backfill, 10% of the column load (practice)
SEISMIC_BEARING = 1.5  # IS 1893 Part 1 cl. 6.3.5.2
LOAD_FACTOR = 1.5
TAU_C = 0.36  # MPa, IS 456 Table 19, M25 at 0.25% steel (nominal for one-way shear)
COVER = 0.06  # m, 50 mm cover plus half a bar (IS 456 cl. 26.4.2.2)
MIN_SIZE, MIN_THICKNESS, STEP = 1.0, 0.30, 0.05


@dataclass
class Footing:
    joints: tuple  # base joints standing on it (two or more: a combined footing)
    at: tuple  # plan centre (x, y)
    width: float  # m, along x
    length: float  # m, along y
    thickness: float
    service_kn: float  # DL + LL on it
    governing: str
    pressure: float  # kN/m2 under the governing case
    allowed: float

    @property
    def joint(self):
        return self.joints[0]

    @property
    def size(self):
        return max(self.width, self.length)


GAP = 0.1  # m: pads closer than this to each other are combined into one footing (practice)


def _round_up(v, step=STEP):
    return round(math.ceil(v / step - 1e-9) * step, 3)


def thickness_for(pu_kn, size, column, fck):
    """Smallest thickness (m) that passes punching and one-way shear."""
    qu = pu_kn / size ** 2  # kN/m2 net upward
    tau_p = 0.25 * math.sqrt(fck) * 1000  # kN/m2
    d = 0.15
    while d < 1.5:
        a = column + d
        punching = qu * (size ** 2 - a ** 2) <= tau_p * 4 * a * d
        overhang = (size - column) / 2 - d
        one_way = overhang <= 0 or qu * size * overhang <= TAU_C * 1000 * size * d
        if punching and one_way:
            break
        d += 0.025
    return max(MIN_THICKNESS, _round_up(d + COVER))


def _pad(joint, x, z, cases, sbc, column, fck):
    """Square pad for one column: (Footing, area needed)."""
    service = {SERVICE_START: "DL+LL"}
    for k, name in enumerate(("DL+0.8LL+0.8EQX", "DL+0.8LL-0.8EQX", "DL+0.8LL+0.8EQZ", "DL+0.8LL-0.8EQZ"), start=1):
        service[SERVICE_START + k] = name
    need, governing, load = 0.0, "", 0.0
    for case, name in service.items():
        if case not in cases:
            continue
        fy = cases[case][1]
        area = fy * SELF_WEIGHT / (sbc * (1.0 if case == SERVICE_START else SEISMIC_BEARING))
        if area > need:
            need, governing, load = area, name, fy
    size = max(MIN_SIZE, _round_up(math.sqrt(need)))
    factor = 1.0 if governing == "DL+LL" else SEISMIC_BEARING
    gravity = cases[SERVICE_START][1]
    pu = LOAD_FACTOR * max(gravity, load / factor)
    ft = Footing((joint,), (x, z), size, size, thickness_for(pu, size, column, fck), round(gravity, 1),
                 governing, round(load * SELF_WEIGHT / size ** 2, 1), sbc * factor)
    return ft, need


def _overlap(a, b):
    return (abs(a.at[0] - b.at[0]) < (a.width + b.width) / 2 + GAP
            and abs(a.at[1] - b.at[1]) < (a.length + b.length) / 2 + GAP)


def _combine(group):
    """One rectangular footing for pads that would overlap: it covers every pad's extent, is centred on
    the columns' load, and is at least as large in area as the pads together."""
    pads = [p for p, _ in group]
    total = sum(p.service_kn for p in pads)
    cx = sum(p.at[0] * p.service_kn for p in pads) / total
    cy = sum(p.at[1] * p.service_kn for p in pads) / total
    half_x = max(abs(p.at[0] - cx) + p.width / 2 for p in pads)
    half_y = max(abs(p.at[1] - cy) + p.length / 2 for p in pads)
    width, length = _round_up(2 * half_x), _round_up(2 * half_y)
    need = sum(a for _, a in group)
    if width * length < need:  # grow across the line of columns
        if width >= length:
            length = _round_up(need / width)
        else:
            width = _round_up(need / length)
    worst = max(pads, key=lambda p: p.pressure / p.allowed)
    pressure = round(sum(p.pressure * p.width * p.length for p in pads) / (width * length), 1)
    return Footing(tuple(j for p in pads for j in p.joints), (round(cx, 4), round(cy, 4)), width, length,
                   max(p.thickness for p in pads), round(total, 1), worst.governing, pressure, worst.allowed)


def design_footings(frame, reactions, sbc, column_size):
    """Footings under the base joints of `frame`. reactions: joint -> {case: (FX, FY, ...)} from STAAD.

    Each column gets a square pad; pads that would overlap or nearly touch are combined into
    one rectangular footing (thickness: the thickest of its pads, a simplification at LOD 200)."""
    pads = []
    for joint, (x, y, z) in sorted(frame.nodes.items()):
        if abs(y - frame.base_y) > 1e-6:
            continue
        cases = reactions.get(joint)
        if not cases or SERVICE_START not in cases:
            raise ValueError(f"no support reaction for joint {joint}")
        pads.append(_pad(joint, x, z, cases, sbc, column_size, frame.fck))
    groups = [[p] for p in pads]
    merged = True
    while merged:
        merged = False
        for i, j in ((i, j) for i in range(len(groups)) for j in range(i + 1, len(groups))):
            if _overlap(_footing(groups[i]), _footing(groups[j])):
                groups[i] += groups.pop(j)
                merged = True
                break
    return [_footing(g) for g in groups]


def _footing(group):
    return _combine(group) if len(group) > 1 else group[0][0]


def basis(sbc, sbc_source):
    return (f"Isolated footings, combined where pads would overlap, on {sbc:g} kN/m2 safe bearing capacity ({sbc_source}), +50% under "
            f"earthquake combinations (IS 1893 Part 1 cl. 6.3.5.2); thickness from punching and one-way shear "
            f"(IS 456 cl. 31.6.3.1, 34.2.4.1); reinforcement not designed at LOD 200.")
