"""Brief chatbot: plain-language conversation -> validated Brief.

What the LLM does: read the client's reply and report, as schema-checked JSON, only
the facts in it (with units as the client said them). Everything else is plain code:
which question comes next (the first unanswered Must item), its wording, the default
offered, unit conversion, merging into the Brief and the closing summary. One LLM call
per client turn; every prompt and response is logged.
"""

import json
import re
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from src.brief.schema import Brief, Family, Parking, Plot, Programme, Road, Site
from src.brief.llm import LLMError

FT = 0.3048
RETRIES = 3

Topic = Literal["building_use", "floors_now", "family", "programme", "site.plot", "site.north", "site.roads",
                "site.city"]


class Length(BaseModel):
    value: float = Field(gt=0)
    unit: Literal["m", "ft"]

    def metres(self):
        return round(self.value * (FT if self.unit == "ft" else 1.0), 3)


class RoadSaid(BaseModel):
    side: Literal["front", "left", "right", "rear"]
    width: Length


class Member(BaseModel):
    """One group of people the client names; the code adds them up (the LLM does not count)."""
    relation: str  # as said: "me", "wife", "parents", "kids"
    count: int = Field(1, ge=1)
    age: Literal["adult", "child", "elderly"] = "adult"
    wheelchair: bool = False


class Extraction(BaseModel):
    """What one client message says. Every field is optional: leave out what was not said."""
    building_use: Literal["single_family", "home_with_shop", "rental_floors"] | None = None
    floors_now: int | None = Field(None, ge=1, le=3)
    floors_future: int | None = Field(None, ge=1, le=3)
    persons: int | None = Field(None, ge=1)  # only a total the client states as a number ("we are five")
    members: list[Member] | None = None
    bedrooms: int | None = Field(None, ge=1)
    bathrooms_attached: int | None = Field(None, ge=0)
    bathrooms_common: int | None = Field(None, ge=0)
    kitchen: Literal["separate_dining", "with_dining", "with_store"] | None = None
    dining: bool | None = None
    puja: bool | None = None
    study: bool | None = None
    store: bool | None = None
    wc_type: Literal["western", "indian"] | None = None
    cars: int | None = Field(None, ge=0)
    two_wheelers: int | None = Field(None, ge=0)
    plot_width: Length | None = None
    plot_depth: Length | None = None
    north: Literal["N", "NE", "E", "SE", "S", "SW", "W", "NW"] | None = None
    roads: list[RoadSaid] | None = None
    city: str | None = None
    vastu: bool | None = None
    lift: bool | None = None
    unsure: list[Topic] = []  # questions the client says they cannot answer


SYSTEM = """You help collect a house-design brief in India. Read the client's latest message and
return JSON with ONLY the facts it states. Never guess or fill in values the client did not give.

Rules:
- floors_now / floors_future count storeys including the ground floor: "G+1" or "two storey" = 2,
  "G+2" = 3. floors_future is the number of storeys planned later ("may add a floor" on G+1 = 3).
- Lengths: give value and unit exactly as said ("30 by 50 feet" -> plot_width 30 ft, plot_depth 50 ft;
  the first number is the width along the road). Do not convert units.
- "2BHK" = 2 bedrooms; mention of a hall/living room needs no field. "All bathrooms attached" means
  bathrooms_common = 0.
- Household: put a total the client states as a number in "persons". List every person or group the
  client names in "members" (include "me" when the client will live there), e.g. "my parents, my wife,
  our two kids and me" -> parents x2, wife x1, kids x2 (age child), me x1; "we are 7, including my two
  aged parents" -> persons 7 and members parents x2 (elderly). Each member count is 1 or more. Do not
  add them up yourself.
  age "elderly" only when said (old, senior, in their seventies); "child" for kids/son/daughter.
- north: the direction the FRONT of the plot (the side on the road) faces, e.g. "east-facing plot" -> "E",
  "plot faces north" -> "N".
- roads: each road touching the plot; the main road is "front". A corner plot has two roads.
- If the client says they don't know or don't mind about what was asked, add that question's field to
  "unsure" (the question asked is given below).
- building_use: only when the message says who the house is for or mentions a shop or renting:
  single_family when it is for the client's own family or household ("for my family", "for 4 of us",
  "for a couple with one kid"), home_with_shop if a shop is included, rental_floors if floors will be
  rented out. Never from the word "house" alone."""

# Brief path each Extraction field writes to. While a question is open, only that question's
# path may overwrite a value the client already gave; other fields only fill empty paths.
FIELD_PATH = {"building_use": "building_use", "floors_now": "floors_now", "floors_future": "floors_now",
              "persons": "family", "members": "family", "cars": "parking", "two_wheelers": "parking",
              "plot_width": "site.plot", "plot_depth": "site.plot", "north": "site.north",
              "roads": "site.roads", "city": "site.city", "vastu": "vastu", "lift": "lift"}
FIELD_PATH |= {k: "programme" for k in ("bedrooms", "bathrooms_attached", "bathrooms_common", "kitchen",
                                        "dining", "puja", "study", "store", "wc_type")}

QUESTIONS = {
    "building_use": ("Is this house just for your family, or will it also have a shop or floors to rent out?",
                     "a home for one family"),
    "floors_now": ("How many floors do you want to build now (for example G+1, ground plus one), and might you "
                   "add a floor later?", "G+1 now, with the structure designed for one more floor later"),
    "family": ("How many people will live in the house? Are there elderly members, children, or anyone who "
               "uses a wheelchair?", "4 people"),
    "programme": ("Which rooms do you need: how many bedrooms and bathrooms, and anything extra like a puja "
                  "room, study or store?", "2 bedrooms per floor, one attached and one common bathroom per floor"),
    "site.plot": ("What is the size of your plot, width along the road and depth (feet or metres are both "
                  "fine)?", None),
    "site.north": ("Which direction does the front of your plot (the road side) face: north, east, south or "
                   "west?", "unknown; the road side is taken as the front"),
    "site.roads": ("How wide is the road in front of your plot? Is it a corner plot with a second road?",
                   "one 6 m (about 20 ft) road in front"),
    "site.city": ("Where is the plot, which city or town?", "Guwahati"),
}
PLAIN = {"building_use": "who the house is for", "floors_now": "number of floors", "family": "household size",
         "programme": "rooms", "site.plot": "plot size", "site.north": "which way the plot faces",
         "site.roads": "road width", "site.city": "location"}
YES = re.compile(r"^\s*(y|yes|yeah|yep|ok|okay|correct|confirm(ed)?|looks good|that'?s right|all good)\b", re.I)


class BriefSession:
    """One conversation. `start()` gives the opening line; `reply(text)` returns STRATA's answer."""

    def __init__(self, llm, log_path=None):
        self.llm = llm
        self.brief = Brief()
        self.accepted_defaults = set()  # Must items the client could not answer
        self.asked = None
        self.summarised = False
        self.done = False
        self.log_path = Path(log_path) if log_path else None

    def to_state(self):
        """Everything needed to resume the conversation (for stateless servers, e.g. a serverless API)."""
        return {"brief": self.brief.model_dump(mode="json"), "accepted_defaults": sorted(self.accepted_defaults),
                "asked": self.asked, "summarised": self.summarised, "done": self.done}

    @classmethod
    def from_state(cls, llm, state, log_path=None):
        s = cls(llm, log_path=log_path)
        if state:
            s.brief = Brief.model_validate(state["brief"])
            s.accepted_defaults = set(state.get("accepted_defaults", []))
            s.asked = state.get("asked")
            s.summarised = bool(state.get("summarised"))
            s.done = bool(state.get("done"))
        return s

    def start(self):
        return ("Hello! I will help you put together the brief for your house. Tell me about the house you "
                "want and your plot in your own words; I will ask about anything missing.")

    def reply(self, text):
        if self.summarised and YES.match(text):
            self.done = True
            return "Thank you, the brief is confirmed."
        extraction = self._extract(text)
        if extraction is not None:
            try:
                self._apply(extraction)
            except ValidationError:  # a value the brief itself rejects; ask again rather than crash
                extraction = None
        if extraction is None:
            return "Sorry, I could not follow that. " + (self._question() or self._summary())
        nxt = self._question()
        if nxt:
            return nxt
        self.summarised = True
        return self._summary()

    def final_brief(self):
        """The brief with matrix defaults filled in (sources say which were defaults)."""
        b = self.brief.with_defaults()
        for path in self.accepted_defaults:
            if b.sources.get(path, "").startswith("default"):
                b.sources[path] += ", client unsure"
        return b

    # ---- LLM step ----

    def _extract(self, text):
        asked = f"The question just asked was about '{self.asked}'." if self.asked else "No question asked yet."
        prompt = f"{asked}\nClient's message:\n{text}"
        error = None
        for attempt in range(RETRIES):
            full = prompt if error is None else (f"{prompt}\n\nYour previous JSON was invalid: {error}\n"
                                                 "Return corrected JSON.")
            raw, parsed, error = None, None, None
            try:
                raw = self.llm.complete_json(SYSTEM, full, Extraction)
                parsed = Extraction.model_validate(first_json_object(raw))
            except ValidationError as e:
                error = str(e).splitlines()[0] + " " + "; ".join(f"{x['loc']}: {x['msg']}" for x in e.errors()[:5])
            except json.JSONDecodeError as e:
                error = f"Invalid JSON: {e.msg} at line {e.lineno} column {e.colno}"
            except LLMError as e:
                error = str(e)
                self._log(full, raw, None, error, attempt)
                return None
            self._log(full, raw, parsed, error, attempt)
            if parsed is not None:
                return parsed
        return None

    def _log(self, prompt, raw, parsed, error, attempt):
        if not self.log_path:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "a") as f:
            f.write(json.dumps({"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "model": getattr(self.llm, "model", "?"),
                                "attempt": attempt, "system": SYSTEM, "prompt": prompt, "response": raw,
                                "parsed": parsed.model_dump(exclude_none=True) if parsed else None,
                                "error": error}) + "\n")

    # ---- deterministic merge ----

    def _set(self, path, value):
        obj = self.brief
        *parents, last = path.split(".")
        for p in parents:
            obj = getattr(obj, p)
        setattr(obj, last, value)
        self.brief.sources[path] = "client"

    def _guard(self, x):
        """Drop fields that would overwrite an earlier client answer off the open question's topic.

        With no question open (the opening message, or a correction after the summary) anything may change.
        """
        if self.asked is None:
            return x
        drop = {f: None for f, path in FIELD_PATH.items() if getattr(x, f) is not None
                and path != self.asked and self.brief.sources.get(path) == "client"}
        return x.model_copy(update=drop) if drop else x

    def _apply(self, x):
        x = self._guard(x)
        b = self.brief
        for name in ("building_use", "floors_now", "floors_future", "vastu", "lift"):
            if getattr(x, name) is not None:
                self._set(name, getattr(x, name))
        if b.floors_now and b.floors_future and b.floors_future < b.floors_now:
            self._set("floors_future", b.floors_now)
        if x.persons is not None or x.members:
            members = x.members or []
            listed = sum(m.count for m in members)
            self._set("family", Family(persons=max(x.persons or 0, listed),
                                       elderly=sum(m.count for m in members if m.age == "elderly"),
                                       children=sum(m.count for m in members if m.age == "child"),
                                       disabled=sum(m.count for m in members if m.wheelchair)))
        if x.bedrooms is not None or (b.programme and _any(x, "bathrooms_attached", "bathrooms_common", "kitchen")):
            old = b.programme.model_dump() if b.programme else {}
            floors = b.floors_now or 2
            data = {"bathrooms_attached": floors, "bathrooms_common": floors} | old  # A5 default until said
            data |= {k: v for k in ("bedrooms", "bathrooms_attached", "bathrooms_common", "kitchen", "dining", "puja",
                                    "study", "store", "wc_type") if (v := getattr(x, k)) is not None}
            if "bedrooms" in data:
                self._set("programme", Programme(**data))
        if x.cars is not None or x.two_wheelers is not None:
            self._set("parking", Parking(cars=x.cars or 0, two_wheelers=x.two_wheelers or 0))
        if x.plot_width and x.plot_depth:
            self._set("site.plot", Plot(width=x.plot_width.metres(), depth=x.plot_depth.metres()))
        if x.north:
            self._set("site.north", x.north)
        if x.roads:
            self._set("site.roads", [Road(side=r.side, width=r.width.metres()) for r in x.roads])
        if x.city:
            self._set("site.city", x.city.strip().title())
            if "guwahati" in x.city.lower():  # Guwahati Municipal Corporation area (bye-law 24(b))
                self.brief.site.in_ulb = True
                self.brief.sources["site.in_ulb"] = "derived (city)"
        for path in x.unsure:
            if QUESTIONS[path][1] is not None:  # the plot has no default
                self.accepted_defaults.add(path)

    # ---- deterministic questions and summary ----

    def _question(self):
        for path, _item in self.brief.missing_must():
            if path in self.accepted_defaults:
                continue
            question, default = QUESTIONS[path]
            self.asked = path
            hint = f" (If you are not sure, I will assume {default}.)" if default else ""
            return question + hint
        self.asked = None
        return None

    def _summary(self):
        b = self.final_brief()
        p, s = b.programme, b.site
        lines = ["Here is what I have. Please check it:"]
        lines.append(f"- Use: {b.building_use.replace('_', ' ')}; {b.floors_now} storeys now "
                     f"(G+{b.floors_now - 1}), structure designed for {b.floors_future}")
        lines.append(f"- Family: {b.family.persons} people" + (f", {b.family.elderly} elderly" if b.family.elderly else "")
                     + (f", {b.family.children} children" if b.family.children else ""))
        extras = [n for n in ("puja", "study", "store") if getattr(p, n)]
        lines.append(f"- Rooms: {p.bedrooms} bedrooms, {p.bathrooms_attached} attached + {p.bathrooms_common} common "
                     f"bathrooms, kitchen ({p.kitchen.replace('_', ' ')})" + (f", {', '.join(extras)}" if extras else ""))
        if s.plot:
            lines.append(f"- Plot: {s.plot.width:.2f} m x {s.plot.depth:.2f} m ({s.plot.area:.1f} m2), front faces "
                         f"{s.north or 'unknown'}")
        lines.append("- Roads: " + ", ".join(f"{r.side} {r.width:.1f} m" for r in s.roads))
        lines.append(f"- Location: {s.city}")
        defaults = sorted({k for k, v in b.sources.items() if v.startswith("default") and k in QUESTIONS}
                          | self.accepted_defaults)
        if defaults:
            lines.append("Assumed (you can change these): " + ", ".join(PLAIN.get(d, d) for d in defaults))
        lines.append("Is this correct? Reply 'yes' to confirm, or tell me what to change.")
        return "\n".join(lines)


def first_json_object(raw):
    """The first complete JSON object in a reply, ignoring Markdown fences or text around it.

    Gemma's JSON mode sometimes closes a valid object and then adds a stray code fence; only
    the format is cleaned here, the values still go through the pydantic checks.
    """
    if not raw:
        raise json.JSONDecodeError("empty reply", "", 0)
    start = raw.find("{")
    if start < 0:
        return json.loads(raw)  # raises the usual error, reported back to the model
    obj, _end = json.JSONDecoder().raw_decode(raw, start)
    return obj


def _any(x, *names):
    return any(getattr(x, n) is not None for n in names)
