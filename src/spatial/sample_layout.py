"""A hand-drawn G+1 layout that passes every check: the checker's reference case.

Brief: 3 bedrooms (2 attached + 1 common bathroom), separate kitchen and dining, store,
40 x 60 ft plot in Guwahati on a 6 m road. Footprint 8.0 x 11.0 m set at the front-left
corner of the buildable envelope (setbacks: front 3.6 m, sides and rear 1.8 m).

Plan in footprint coordinates (x across the frontage, y into the plot):

    y=11.0 +--------------+---+----------------+
           | common bath  |   |    kitchen     |
       9.2 +--------------+ p +----------------+ 8.4
           |    store     | a |     dining     |
       7.6 +--------------+ s +----------------+ 5.6
           |    stair     | s |  bath 1 (att.) |
       4.0 +--------------+---+----------------+ 4.0
           |   living (main door)  | bedroom 1 |
       0.0 +-----------------------+-----------+   <- road side
           x=0          3.6  4.8              8.0

First floor: bedroom 2 over the living room, a balcony and bath 2 over bedroom 1, bedroom 3
over bath 1, with the same stair and passage; the rear part is a terrace (not built).
"""

from src.brief.schema import Brief, Family, Plot, Programme, Road, Site
from src.spatial.layout import Column, Door, Floor, Layout, Materials, Room, Stair, Window, derive_walls

FT = 0.3048
ORIGIN = (1.8, 3.6)  # front-left corner of the buildable envelope


def sample_brief():
    return Brief(building_use="single_family", floors_now=2, floors_future=2, family=Family(persons=5),
                 programme=Programme(bedrooms=3, bathrooms_attached=2, bathrooms_common=1, store=True),
                 site=Site(plot=Plot(width=40 * FT, depth=60 * FT), north="E", roads=[Road(side="front", width=6.0)],
                           city="Guwahati", in_ulb=True),
                 sources={k: "client" for k in ("building_use", "floors_now", "floors_future", "family", "programme",
                                                "site.plot", "site.north", "site.roads", "site.city")})


def _p(x, y):
    return (round(ORIGIN[0] + x, 4), round(ORIGIN[1] + y, 4))


def _rect(rid, rtype, x0, y0, x1, y1, **kw):
    return Room(id=rid, type=rtype, polygon=[_p(x0, y0), _p(x1, y0), _p(x1, y1), _p(x0, y1)], **kw)


STAIR = Stair(flight_width=1.0, riser=0.1667, tread=0.25, risers_per_flight=9, headroom=2.2)


def _ground():
    rooms = [
        _rect("living", "living", 0, 0, 4.8, 4.0),
        _rect("bed1", "bedroom", 4.8, 0, 8.0, 4.0),
        _rect("stair", "stair", 0, 4.0, 3.6, 7.6, stair=STAIR),
        _rect("passage", "corridor", 3.6, 4.0, 4.8, 11.0),
        _rect("bath1", "bath_wc", 4.8, 4.0, 8.0, 5.6, name="attached to bed1"),
        _rect("dining", "dining", 4.8, 5.6, 8.0, 8.4),
        _rect("kitchen", "kitchen", 4.8, 8.4, 8.0, 11.0),
        _rect("store", "store", 0, 7.6, 3.6, 9.2),
        _rect("bath_c", "bath_wc", 0, 9.2, 3.6, 11.0, name="common"),
    ]
    doors = [
        Door(id="D0", between=("living", "outside"), at=_p(1.5, 0), width=1.0, height=2.1, main=True),
        Door(id="D1", between=("living", "bed1"), at=_p(4.8, 3.0), width=0.9),
        Door(id="D2", between=("living", "passage"), at=_p(4.2, 4.0), width=0.9),
        Door(id="D3", between=("living", "stair"), at=_p(1.8, 4.0), width=0.9),
        Door(id="D4", between=("bed1", "bath1"), at=_p(6.4, 4.0), width=0.75),
        Door(id="D5", between=("passage", "dining"), at=_p(4.8, 7.0), width=0.9),
        Door(id="D6", between=("dining", "kitchen"), at=_p(6.4, 8.4), width=0.9),
        Door(id="D7", between=("passage", "store"), at=_p(3.6, 8.4), width=0.75),
        Door(id="D8", between=("passage", "bath_c"), at=_p(3.6, 10.1), width=0.75),
    ]
    windows = [
        Window(id="W1", room="living", at=_p(3.3, 0), width=1.5, height=1.2),
        Window(id="W2", room="living", at=_p(0, 2.0), width=1.5, height=1.2),
        Window(id="W3", room="bed1", at=_p(6.4, 0), width=1.8, height=1.2),
        Window(id="V1", room="bath1", at=_p(8.0, 4.8), width=0.6, height=0.6, sill=1.8, kind="ventilator"),
        Window(id="W4", room="dining", at=_p(8.0, 7.0), width=1.2, height=1.2),
        Window(id="W5", room="kitchen", at=_p(8.0, 9.7), width=1.2, height=1.2, sill=1.05),
        Window(id="V2", room="bath_c", at=_p(1.8, 11.0), width=0.6, height=0.6, sill=1.8, kind="ventilator"),
    ]
    columns = [Column(id=f"C{i}{j}", at=_p(x, y)) for i, x in enumerate((0, 3.6, 4.8, 8.0))
               for j, y in enumerate((0, 4.0, 7.6, 11.0))]
    f = Floor(level=0, name="Ground floor", rooms=rooms, doors=doors, windows=windows, columns=columns)
    f.walls = derive_walls(f)
    return f


def _first():
    rooms = [
        _rect("bed2", "bedroom", 0, 0, 4.8, 4.0),
        _rect("balcony", "balcony", 4.8, 0, 8.0, 2.4),
        _rect("bath2", "bath_wc", 4.8, 2.4, 8.0, 4.0, name="attached to bed3"),
        _rect("stair", "stair", 0, 4.0, 3.6, 7.6, stair=STAIR),
        _rect("passage", "corridor", 3.6, 4.0, 4.8, 7.6),
        _rect("bed3", "bedroom", 4.8, 4.0, 8.0, 7.6),
    ]
    doors = [
        Door(id="D1", between=("stair", "passage"), at=_p(3.6, 5.8), width=0.9),
        Door(id="D2", between=("passage", "bed2"), at=_p(4.2, 4.0), width=0.9),
        Door(id="D3", between=("passage", "bed3"), at=_p(4.8, 6.0), width=0.9),
        Door(id="D4", between=("bed3", "bath2"), at=_p(6.4, 4.0), width=0.75),
        Door(id="D5", between=("bed2", "balcony"), at=_p(4.8, 1.2), width=0.9),
    ]
    windows = [
        Window(id="W1", room="bed2", at=_p(1.2, 0), width=1.5, height=1.2),
        Window(id="W2", room="bed2", at=_p(3.4, 0), width=1.5, height=1.2),
        Window(id="V1", room="bath2", at=_p(8.0, 3.2), width=0.6, height=0.6, sill=1.8, kind="ventilator"),
        Window(id="W3", room="bed3", at=_p(8.0, 5.8), width=1.5, height=1.2),
    ]
    columns = [Column(id=f"C{i}{j}", at=_p(x, y)) for i, x in enumerate((0, 3.6, 4.8, 8.0))
               for j, y in enumerate((0, 4.0, 7.6))]
    f = Floor(level=1, name="First floor", rooms=rooms, doors=doors, windows=windows, columns=columns)
    f.walls = derive_walls(f)
    return f


def sample_layout():
    return Layout(name="g1_40x60_sample", source={"kind": "hand-drawn reference"},
                  plot={"width": round(40 * FT, 4), "depth": round(60 * FT, 4)},
                  floors=[_ground(), _first()], plinth_height=0.45, parapet_height=1.0,
                  materials=Materials(concrete_grade=25, steel_proof_stress=500, steel_elongation=14.5,
                                      masonry="burnt clay brick"))
