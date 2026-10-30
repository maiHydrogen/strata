"""The G+1 test house: architecture on the same grid as its structural frame.

8 m x 7 m on a 2 x 2 grid (4 m x 3.5 m bays). Every wall sits on a grid
line, so upper-floor walls stand on beams. Walls stop at the soffit of the
beam above. Openings keep clear of the 350 mm columns at grid points.
Stairs are not modelled yet.
"""

from src.execution.frame_to_building import level_id
from src.structure.std_writer import Frame, make_rc_frame

BAYS_X = [4.0, 4.0]
BAYS_Y = [3.5, 3.5]
STOREYS = 2
STOREY_HEIGHT = 3.0

WALL_TYPES = [
    {"id": "EXT230", "name": "STRATA External 230 Brick + Plaster", "layers": [
        {"material": "Plaster", "thickness": 0.015, "function": "Finish1"},
        {"material": "Brick", "thickness": 0.23, "function": "Structure"},
        {"material": "Plaster", "thickness": 0.012, "function": "Finish2"}]},
    {"id": "INT115", "name": "STRATA Internal 115 Brick + Plaster", "layers": [
        {"material": "Plaster", "thickness": 0.012, "function": "Finish2"},
        {"material": "Brick", "thickness": 0.115, "function": "Structure"},
        {"material": "Plaster", "thickness": 0.012, "function": "Finish2"}]},
]

# Per storey: internal walls, openings, rooms. Wall ids get the storey prefix.
PERIMETER = [("S", (0, 0), (8, 0)), ("E", (8, 0), (8, 7)), ("N", (8, 7), (0, 7)), ("W", (0, 7), (0, 0))]
STOREY_LAYOUT = {
    0: {
        "internal": [("X", (0, 3.5), (8, 3.5)), ("Y", (4, 3.5), (4, 7))],
        "openings": [
            ("door", "S", 1.5, 1.0, 2.1, 0.0, "Main Door"),
            ("door", "X", 1.5, 0.9, 2.1, 0.0, "Kitchen Door"),
            ("door", "X", 5.5, 0.9, 2.1, 0.0, "Bedroom Door"),
            ("window", "S", 5.5, 1.5, 1.2, 0.9, "Living Window"),
            ("window", "W", 1.2, 1.2, 1.2, 0.9, "Kitchen Window"),
            ("window", "E", 4.6, 1.2, 1.2, 0.9, "Bedroom Window"),
        ],
        "rooms": [("Living Room", (4.0, 1.75)), ("Kitchen", (2.0, 5.25)), ("Bedroom", (6.0, 5.25))],
    },
    1: {
        "internal": [("X", (0, 3.5), (8, 3.5)), ("Y", (4, 0), (4, 3.5))],
        "openings": [
            ("door", "X", 1.5, 0.9, 2.1, 0.0, "Bedroom 1 Door"),
            ("door", "X", 5.5, 0.9, 2.1, 0.0, "Bedroom 2 Door"),
            ("window", "S", 1.5, 1.2, 1.2, 0.9, "Bedroom 1 Window"),
            ("window", "S", 5.5, 1.2, 1.2, 0.9, "Bedroom 2 Window"),
            ("window", "N", 2.0, 1.5, 1.2, 0.9, "Family Room Window East"),
            ("window", "N", 5.0, 1.5, 1.2, 0.9, "Family Room Window West"),
        ],
        "rooms": [("Bedroom 1", (2.0, 1.75)), ("Bedroom 2", (6.0, 1.75)), ("Family Room", (4.0, 5.25))],
    },
}


def house_frame() -> Frame:
    return make_rc_frame(BAYS_X, BAYS_Y, storeys=STOREYS, storey_height=STOREY_HEIGHT)


def architecture(frame: Frame) -> dict:
    """Walls, openings and rooms; wall heights stop at the soffit of the beam above."""
    walls, openings, rooms = [], [], []
    for storey, layout in STOREY_LAYOUT.items():
        lvl = level_id(storey)
        height = round(frame.storey_height - frame.sections[f"beam-{storey + 1}"].depth, 6)
        for tag, start, end in PERIMETER:
            walls.append({"id": f"{lvl}-{tag}", "level": lvl, "type": "EXT230", "start": start, "end": end,
                          "height": height, "external": True})
        for tag, start, end in layout["internal"]:
            walls.append({"id": f"{lvl}-{tag}", "level": lvl, "type": "INT115", "start": start, "end": end,
                          "height": height})
        for n, (kind, wall, offset, width, h, sill, _name) in enumerate(layout["openings"], start=1):
            openings.append({"id": f"{lvl}-{'D' if kind == 'door' else 'WIN'}{n}", "kind": kind,
                             "wall": f"{lvl}-{wall}", "offset": offset, "width": width, "height": h, "sill": sill})
        for n, (name, point) in enumerate(layout["rooms"], start=1):
            rooms.append({"id": f"{lvl}-R{n}", "level": lvl, "name": name, "point": point})
    return {"wall_types": WALL_TYPES, "walls": walls, "openings": openings, "rooms": rooms}
