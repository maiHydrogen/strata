"""Gold briefs for the Phase 9 ablation: the 25 brief test cases as complete Briefs.

Every arm of the ablation gets the same client: arms B and C get the Brief built here from the
hand-written gold facts (what the client said in the opening message plus the answers to the
follow-up questions), with the matrix defaults for whatever the client never gave, exactly as the
chatbot would hand it on. Arm A (the Text2BIM-style baseline) takes free text, so it gets the
client's own words: the opening message followed by the answers.

Using gold facts rather than the chatbot's output keeps the brief stage out of the layout and
structure comparison; the chatbot itself is scored separately (src/evaluation/brief_eval.py).
"""

from src.brief.schema import Brief, Family, Parking, Plot, Programme, Road, Site
from src.evaluation.brief_eval import load_cases

# Arms A and B take 15-30 minutes per brief on the free LLM tier, so they run on 10 briefs chosen to cover
# the variety of the set: plot sizes from 20 to 45 ft wide, G+0 to G+2, shop and rental use, a wheelchair
# user, parking, another city. Arm C runs on all 25 and is also reported on these 10.
LLM_SUBSET = ("b01_complete_feet", "b02_omit_plot", "b03_shop_omit_family", "b04_rental_corner",
              "b07_wheelchair_metres", "b10_parking", "b11_other_city", "b12_future_floor", "b21_katha_area_only",
              "b22_narrow_lane")
FAMILY = ("persons", "elderly", "children", "disabled")
PROGRAMME = ("bedrooms", "bathrooms_attached", "bathrooms_common", "kitchen", "puja", "study", "store", "wc_type")


def gold_facts(case):
    """Everything the client says in the case: the opening message's facts, then the answers."""
    facts = dict(case["stated"])
    for a in case["answers"].values():
        facts.update(a["facts"])
    return facts


def brief_text(case):
    """The client's own words for an arm that reads text: the message, then each answer given."""
    answers = [a["text"] for a in case["answers"].values() if a["facts"]]
    return " ".join([case["message"], *answers])


def gold_brief(case):
    """Complete Brief from the gold facts; missing items take the matrix defaults (sources say so)."""
    f = gold_facts(case)
    b = Brief()
    src = {}
    for k in ("building_use", "floors_now", "floors_future", "vastu"):
        if k in f:
            setattr(b, k, f[k])
            src[k] = "client"
    if "persons" in f:
        b.family = Family(**{k: f[k] for k in FAMILY if k in f})
        src["family"] = "client"
    if "bedrooms" in f:
        floors = f.get("floors_now", 2)
        p = {"bathrooms_attached": floors, "bathrooms_common": floors}  # A5 default until said
        p |= {k: f[k] for k in PROGRAMME if k in f}
        b.programme = Programme(**p)
        src["programme"] = "client"
    if "cars" in f or "two_wheelers" in f:
        b.parking = Parking(cars=f.get("cars", 0), two_wheelers=f.get("two_wheelers", 0))
        src["parking"] = "client"
    site = Site()
    if "plot_width" in f:
        site.plot = Plot(width=f["plot_width"], depth=f["plot_depth"])
        src["site.plot"] = "client"
    if "north" in f:
        site.north = f["north"]
        src["site.north"] = "client"
    if "roads" in f:
        site.roads = [Road(side=s, width=w) for s, w in f["roads"]]
        src["site.roads"] = "client"
    if "city" in f:
        site.city = f["city"]
        src["site.city"] = "client"
    b.site = site
    b.sources = src
    if b.floors_future is None and b.floors_now:
        b.floors_future = b.floors_now
        b.sources["floors_future"] = "client"
    return b.with_defaults()


def gold_cases():
    """(case, Brief) for every test case that has a plot: without one there is nothing to design on."""
    return [(c, gold_brief(c)) for c in load_cases() if "plot_width" in gold_facts(c)]
