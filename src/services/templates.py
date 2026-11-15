"""Room templates for plumbing, electrical and finishing (Phase 6), each value with its source.

Deterministic data, no LLM. Where NBC 2016 states a rule it is cited by clause and page (Vol 2,
pages as printed); where the code leaves the number to the designer (points per room, mounting
heights, finish materials) the value is a STRATA default marked "assumed", so the report shows
it as an assumption rather than a requirement.
"""

NBC = "National Building Code of India 2016 (SP 7:2016)"


def _src(clause, page, status="transcribed", note=""):
    s = {"standard": NBC, "clause": clause, "page": page, "status": status}
    if note:
        s["note"] = note
    return s


def _default(note):
    return {"standard": "STRATA default", "clause": "-", "page": "-", "status": "assumed", "note": note}


SOURCES = {
    "fitments": _src("Vol 2, Part 9 Section 2, cl. 4.2.4.1", "16",
                     note="Dwelling with individual convenience: a bath room with a tap and floor trap, a water "
                          "closet with flushing apparatus and ablution tap, a tap with floor trap or a sink in the "
                          "kitchen or wash place."),
    "amenities": _src("Vol 2, Part 9 Section 1, cl. 3.1.5", "8",
                      note="At least one water closet, one lavatory, one kitchen sink and one bathing place."),
    "population": _src("Vol 2, Part 9 Section 1, cl. 4.1 (a)", "10",
                       note="Residential population: 1 bedroom 4, 2 bedroom 5, 3 bedroom 6, 4 bedroom and above 7."),
    "overhead_tank": _src("Vol 2, Part 9 Section 1, cl. 4.4.10, NOTE (a)", "14-15",
                          note="Only an OHT provided: 33.33 to 50 percent of one day's requirement; the upper "
                               "bound is used."),
    "subcircuit": _src("Vol 2, Part 8 Section 2, cl. 5.4.4.3", "39-40",
                       note="Light/fan/6 A socket sub-circuit: at most ten points and 800 W; power sub-circuit: at "
                            "most two 16 A outlets."),
    "lighting_density": _src("Vol 2, Part 8 Section 2, cl. 5.9.2", "45",
                             note="Lighting demand at most 13 W/m2 of built-up area; power at least 55 W/m2 with "
                                  "diversity not over 50 percent."),
    "two_pipe": _src("Vol 2, Part 9 Section 2 (drainage and sanitation)", "-", status="assumed",
                     note="Two-pipe system: soil stack for water closets, waste stack for sinks and washing; "
                          "stacks on the external wall face. Pipe routing is LOD 300 and not modelled."),
    "points": _default("Points per room: no NBC count; typical Indian residential practice."),
    "heights": _default("Mounting heights above finished floor: switch boards 1.2 m, 6 A sockets 0.3 m "
                        "(kitchen 1.1 m), AC 16 A 2.1 m, geyser 16 A 1.8 m, washing machine 16 A 1.0 m."),
    "loads": _default("Connected load per point: LED light 20 W, fan 60 W, 6 A socket 100 W, 16 A outlet 1000 W."),
    "finishes": _default("Finish schedule by room type: typical Guwahati residential specification."),
}

# NBC Part 9 Section 1 cl. 4.1 (a): persons per dwelling by bedrooms
POPULATION_BY_BEDROOMS = {1: 4, 2: 5, 3: 6}
POPULATION_4_PLUS = 7
OHT_FRACTION = 0.5  # cl. 4.4.10 NOTE (a), upper bound
# Standard moulded overhead tanks (litres) -> (diameter, height) in m
TANK_SIZES = {500: (0.86, 1.0), 750: (0.98, 1.15), 1000: (1.1, 1.2), 1500: (1.3, 1.3), 2000: (1.42, 1.45),
              3000: (1.62, 1.68)}
TANK_SELF_WEIGHT = 0.5  # kN, empty tank, assumed

# Loads per point (W) and limits (cl. 5.4.4.3, 5.9.2)
POINT_LOAD = {"light": 20, "fan": 60, "socket_6a": 100, "socket_16a": 1000, "switchboard": 0,
              "distribution_board": 0}
LIGHT_CIRCUIT_POINTS, LIGHT_CIRCUIT_W, POWER_CIRCUIT_OUTLETS = 10, 800, 2
LIGHTING_W_PER_M2_MAX, POWER_W_PER_M2_MIN = 13.0, 55.0

# Fixture geometry for LOD 200 boxes: (dx, dy, dz) before turning to face the wall, base height z (m),
# and the IFC class the Revit builder asks the exporter for
FIXTURE = {
    "wc": ((0.38, 0.68, 0.40), 0.0, "IfcSanitaryTerminal", "WCSEAT"),
    "washbasin": ((0.50, 0.40, 0.18), 0.68, "IfcSanitaryTerminal", "WASHHANDBASIN"),
    "shower": ((0.90, 0.90, 0.05), 0.0, "IfcSanitaryTerminal", "SHOWER"),
    "kitchen_sink": ((0.60, 0.45, 0.20), 0.65, "IfcSanitaryTerminal", "SINK"),
    "washing_machine": ((0.60, 0.60, 0.85), 0.0, "IfcSanitaryTerminal", "USERDEFINED"),
    "light": ((0.30, 0.30, 0.05), None, "IfcLightFixture", "POINTSOURCE"),
    "fan": ((1.20, 1.20, 0.10), None, "IfcFlowTerminal", "USERDEFINED"),
    "socket_6a": ((0.08, 0.03, 0.08), 0.3, "IfcOutlet", "POWEROUTLET"),
    "socket_16a": ((0.10, 0.03, 0.10), 0.3, "IfcOutlet", "POWEROUTLET"),
    "switchboard": ((0.20, 0.03, 0.12), 1.2, "IfcSwitchingDevice", "TOGGLESWITCH"),
    "distribution_board": ((0.40, 0.12, 0.50), 1.5, "IfcElectricDistributionBoard", "DISTRIBUTIONBOARD"),
}
SYSTEM = {"wc": "plumbing", "washbasin": "plumbing", "shower": "plumbing", "kitchen_sink": "plumbing",
          "washing_machine": "plumbing", "light": "lighting", "fan": "electrical", "socket_6a": "electrical",
          "socket_16a": "electrical", "switchboard": "electrical", "distribution_board": "electrical"}
CEILING_CLEARANCE = {"light": 0.05, "fan": 0.45}  # below the slab soffit (fan on a 0.3 m downrod)

WET = {"bathroom", "bath_wc", "wc", "kitchen", "utility"}
HABITABLE = {"living", "bedroom", "dining", "study", "foyer", "puja"}

# Plumbing fixtures per room type (cl. 4.2.4.1 and 3.1.5)
PLUMBING = {
    "bathroom": ["wc", "washbasin", "shower"],
    "bath_wc": ["wc", "washbasin", "shower"],
    "wc": ["wc", "washbasin"],
    "kitchen": ["kitchen_sink"],
    "utility": ["washing_machine"],
}

# Electrical per room type: 6 A sockets, 16 A outlets as (use, mounting height), fans, lights per m2
ELECTRICAL = {
    "living": {"socket_6a": 4, "power": [], "fans": 1, "m2_per_light": 8},
    "bedroom": {"socket_6a": 3, "power": [("ac", 2.1)], "fans": 1, "m2_per_light": 9},
    "dining": {"socket_6a": 2, "power": [], "fans": 1, "m2_per_light": 9},
    "study": {"socket_6a": 3, "power": [], "fans": 1, "m2_per_light": 8},
    "puja": {"socket_6a": 1, "power": [], "fans": 0, "m2_per_light": 9},
    "foyer": {"socket_6a": 1, "power": [], "fans": 0, "m2_per_light": 9},
    "kitchen": {"socket_6a": 2, "power": [("appliance", 1.1), ("appliance", 1.1)], "fans": 0, "m2_per_light": 8,
                "socket_height": 1.1},
    "bathroom": {"socket_6a": 0, "power": [("geyser", 1.8)], "fans": 0, "m2_per_light": 9},
    "bath_wc": {"socket_6a": 0, "power": [("geyser", 1.8)], "fans": 0, "m2_per_light": 9},
    "wc": {"socket_6a": 0, "power": [], "fans": 0, "m2_per_light": 9},
    "utility": {"socket_6a": 1, "power": [("washing machine", 1.0)], "fans": 0, "m2_per_light": 9},
    "store": {"socket_6a": 0, "power": [], "fans": 0, "m2_per_light": 12},
    "corridor": {"socket_6a": 1, "power": [], "fans": 0, "m2_per_light": 9},
    "stair": {"socket_6a": 0, "power": [], "fans": 0, "m2_per_light": 12},
    "balcony": {"socket_6a": 0, "power": [], "fans": 0, "m2_per_light": 12},
    "parking": {"socket_6a": 1, "power": [], "fans": 0, "m2_per_light": 15},
}
BIG_ROOM_SECOND_FAN = 16.0  # m2

# Finish schedule by room type: floor (material, thickness m), wall, dado (material, height above floor), skirting
_TILE = ("Vitrified tile 600x600 on 40 mm cement mortar bed", 0.05)
_ANTISKID = ("Anti-skid ceramic tile on 40 mm bed over waterproofing", 0.05)
_PAINT = "12 mm cement plaster, acrylic emulsion paint"
FINISHES = {
    "living": (_TILE, _PAINT, None, "100 mm vitrified tile skirting"),
    "bedroom": (_TILE, _PAINT, None, "100 mm vitrified tile skirting"),
    "dining": (_TILE, _PAINT, None, "100 mm vitrified tile skirting"),
    "study": (_TILE, _PAINT, None, "100 mm vitrified tile skirting"),
    "foyer": (_TILE, _PAINT, None, "100 mm vitrified tile skirting"),
    "puja": (("Marble 18 mm on 32 mm bed", 0.05), _PAINT, None, "100 mm marble skirting"),
    "corridor": (_TILE, _PAINT, None, "100 mm vitrified tile skirting"),
    "kitchen": (_ANTISKID, _PAINT, ("Glazed ceramic tile dado over the counter", 0.6),
                "100 mm ceramic tile skirting"),
    "bathroom": (_ANTISKID, _PAINT, ("Glazed ceramic wall tile", 2.1), None),
    "bath_wc": (_ANTISKID, _PAINT, ("Glazed ceramic wall tile", 2.1), None),
    "wc": (_ANTISKID, _PAINT, ("Glazed ceramic wall tile", 2.1), None),
    "utility": (_ANTISKID, _PAINT, ("Glazed ceramic wall tile", 1.2), None),
    "store": (("Cement concrete IPS flooring with neat cement finish", 0.04), _PAINT, None, None),
    "stair": (("Kota stone 25 mm on treads, risers and landings", 0.05), _PAINT, None, "100 mm Kota stone skirting"),
    "balcony": (_ANTISKID, "Exterior grade acrylic paint on plaster", None, "100 mm ceramic tile skirting"),
    "parking": (("Cement concrete IPS flooring, trowelled", 0.04), _PAINT, None, None),
}
CEILING = "Plaster on RCC slab, acrylic emulsion paint"
