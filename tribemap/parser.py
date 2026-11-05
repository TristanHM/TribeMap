"""Turn a GM turn report into observations: facts about hexes, each tied to the report line it came from.

The parser never edits the map. It produces a Report (units, paths, observations) and a list of
lines it could not place ("unparsed"). The importer writes those into the database.

Report formats handled (see tests/test_parser.py for one example of each):
    Tribe 0999, , Current Hex = QB 1017, (Previous Hex = N/A)
    Current Turn 906-04 (#76), Summer, FINE<tab>Next Turn 906-05 (#77), 18/10/2026
    Scout 2:Scout NW-PR,Find Salt\\NW-SW, Lcm NW,River NW
    Tribe Movement: Move NE-PR, \\NE-PR, \\NE-GH
    0999 Status: PRAIRIE, 0999

Scout and movement text is split into steps by '\\'. A step starting 'DIR-CODE' moves into that hex;
the rest of the step is what was seen there:
    Lcm SE, S                   tall terrain / water seen in neighbouring hexes ('S' continues the previous item)
    River N SE NW / Ford NE     river (or ford) on those hexsides
    Find Salt / Find 3 Rope     a deposit / a one-off item
    Not enough M.P's to move (to) N into PRAIRIE   stopped; the next hex's terrain is glimpsed
    No Ford on River to N of HEX              stopped by a river
    Nothing of interest found
Anything else in a step (a special-hex name, an encounter) is kept as 'special' and also listed
as unparsed so it gets reviewed.
"""
import re
from dataclasses import dataclass, field

from . import coords

DIR = r"(?:NE|NW|SE|SW|N|S)"
DIRS_ONLY = re.compile(rf"^{DIR}(?:\s+{DIR})*$")

RE_HEADER = re.compile(
    r"^(?P<kind>[A-Za-z]+)\s+(?P<unit>\d{4}(?:[a-z]\d+)?)\s*,\s*(?P<name>[^,]*?)\s*,\s*"
    r"Current Hex\s*=\s*(?P<hex>[^,]+?)\s*,\s*\(Previous Hex\s*=\s*(?P<prev>[^)]*?)\s*\)")
RE_TURN = re.compile(
    r"^Current Turn\s+(?P<turn>\d+-\d+)\s+\(#(?P<no>\d+)\)\s*,\s*(?P<season>\w+)\s*,\s*(?P<weather>\w+)"
    r"(?:\s+Next Turn\s+(?P<next>\d+-\d+)\s+\(#\d+\)\s*,\s*(?P<due>\S+))?")
RE_SCOUT = re.compile(r"^Scout\s+(?P<party>\d+)\s*:\s*(?:Scout\b)?\s*(?P<text>.*)$")
RE_MOVE = re.compile(r"^(?P<who>[A-Za-z]+) Movement\s*:\s*(?:Move\b)?\s*(?P<text>.*)$")
RE_STATUS = re.compile(r"^(?P<unit>\S+) Status\s*:\s*(?P<text>.*)$")
RE_STEP = re.compile(rf"^(?P<dir>{DIR})-(?P<code>[A-Za-z]+)$")
RE_MP_BLOCK = re.compile(rf"^Not enough M\.?P'?s? to move (?:to )?(?P<dir>{DIR}) into (?P<name>.+)$", re.I)
RE_NO_FORD = re.compile(rf"^No Ford on\W*River to (?P<dir>{DIR}) of HEX$", re.I)
RE_FIND = re.compile(r"^Find\s+(?P<what>.+)$", re.I)
RE_EDGE = re.compile(rf"^(?P<kind>River|Ford)\s+(?P<dirs>{DIR}(?:\s+{DIR})*)$", re.I)
RE_SEEN = re.compile(rf"^(?P<code>[A-Za-z]+)\s+(?P<dirs>{DIR}(?:\s+{DIR})*)$")
RE_UNIT_ID = re.compile(r"^\d{4}(?:[a-z]\d+)?$")
RE_NOTHING = re.compile(r"^Nothing of interest found$", re.I)
RE_SKILL = re.compile(r"^([A-Za-z&]+)\s+(\d+)$")

INVENTORY_SECTIONS = {"Humans", "Animals", "Minerals", "War Equipment", "Finished Goods",
                      "Raw Materials", "Ships"}
# Lines we recognise and deliberately ignore (prefix match).
IGNORED = ("Received:", "Goods Tribe:", "Desired Commodities:", "Turn Timing", "Movement Weight:")
# Deposits a status line may list in mixed case ('PRAIRIE, Salt, 0999'); anything ending in 'ore' is one too.
RESOURCES = {"SALT", "COAL", "STONE", "JADE", "DIAMONDS", "GEMS", "FRANKINCENSE"}
# Words that make an unrecognised line worth a look.
MAPISH = re.compile(rf"(\b{DIR}-[A-Za-z]+\b|Hex|Scout|Movement|River|Ford|Find\b|Status)")


@dataclass
class Obs:
    kind: str           # terrain, river, ford, no_ford, resource, item, special, unit_sighting, nothing_found
    hex: tuple
    value: str = ""
    dir: str | None = None   # hexside, for river/ford/no_ford
    how: str = "entered"     # entered, glimpsed, status, moved
    confidence: str = "confirmed"
    unit: str = ""
    party: str = ""          # 'scout3', 'move', 'status'
    step: int = 0
    line_no: int = 0
    raw: str = ""


@dataclass
class Step:
    n: int
    dir: str
    hex: tuple
    result: str  # ok, mp_block, no_ford


@dataclass
class UnitReport:
    unit: str
    kind: str
    name: str
    hex: tuple | None
    prev_hex: tuple | None
    hex_text: str
    status: str = ""
    inventory: dict = field(default_factory=dict)
    skills: dict = field(default_factory=dict)
    morale: str = ""
    paths: dict = field(default_factory=dict)  # party -> [Step]


@dataclass
class Report:
    clan: str = ""   # whose report: the clan given, or the first unit's
    turn: str = ""
    turn_no: int = 0
    season: str = ""
    weather: str = ""
    next_turn: str = ""
    next_due: str = ""
    units: list = field(default_factory=list)
    obs: list = field(default_factory=list)
    unparsed: list = field(default_factory=list)  # (line_no, text, reason, mapish)


class Parser:
    def __init__(self, terrain, clan=None):
        """clan: the player's clan; None takes it from the report's first unit (Tribe 1999 -> 0999)."""
        self.terrain = terrain
        self.clan = clan

    def parse(self, lines):
        r = Report()
        unit = None
        section = None
        expect_skills = False

        def unparsed(n, text, reason):
            r.unparsed.append((n, text, reason, bool(MAPISH.search(text))))

        for n, raw in enumerate(lines, 1):
            line = raw.strip()
            if not line:
                continue
            if m := RE_HEADER.match(line):
                if not self.clan:
                    self.clan = "0" + m["unit"][1:4]
                unit = UnitReport(m["unit"], m["kind"], m["name"], coords.try_parse(m["hex"]),
                                  coords.try_parse(m["prev"]), m["hex"])
                r.units.append(unit)
                section, expect_skills = None, False
                if unit.hex is None:
                    unparsed(n, line, f"unit {unit.unit}: current hex not understood")
                continue
            if m := RE_TURN.match(line):
                r.turn, r.turn_no = m["turn"], int(m["no"])
                r.season, r.weather = m["season"], m["weather"]
                r.next_turn, r.next_due = m["next"] or "", m["due"] or ""
                continue
            if unit is None:
                unparsed(n, line, "before any unit header")
                continue
            if m := RE_SCOUT.match(line):
                self._path(r, unit, f"scout{m['party']}", unit.hex, m["text"], n, line, unparsed)
                continue
            if m := RE_MOVE.match(line):
                self._path(r, unit, "move", unit.prev_hex or unit.hex, m["text"], n, line, unparsed,
                           moving=True)
                continue
            if m := RE_STATUS.match(line):
                self._status(r, unit, m["text"], n, line, unparsed)
                continue
            if line in INVENTORY_SECTIONS:
                section = line
                continue
            if line.startswith("Skills:"):
                expect_skills = True
                rest = line[len("Skills:"):].strip()
                if rest:
                    self._skills(unit, rest)
                    expect_skills = False
                continue
            if expect_skills:
                self._skills(unit, line)
                expect_skills = False
                continue
            if line.startswith("Morale"):
                unit.morale = line.split(":", 1)[-1].strip()
                continue
            if section and "\t" in raw:
                cells = [c.strip() for c in raw.split("\t") if c.strip()]
                for name, qty in zip(cells[::2], cells[1::2]):
                    if qty.replace(",", "").isdigit():
                        unit.inventory.setdefault(section, {})[name] = int(qty.replace(",", ""))
                continue
            if section and line == "None":
                continue
            if line.startswith(IGNORED) or " = " in line:  # ' = ' lines are the GM's legend
                continue
            unparsed(n, line, "not recognised")
        r.clan = self.clan or ""
        return r

    # --- helpers -----------------------------------------------------------

    def _skills(self, unit, text):
        for part in text.split(","):
            if m := RE_SKILL.match(part.strip()):
                unit.skills[m.group(1)] = int(m.group(2))

    def _own(self, unit_id):
        return unit_id[1:4] == self.clan[1:4]

    def _path(self, r, unit, party, start, text, n, line, unparsed, moving=False):
        if start is None:
            unparsed(n, line, f"{party}: start hex unknown")
            return
        how_in = "moved" if moving else "entered"
        cur, steps, stopped = start, [], False
        base = dict(unit=unit.unit, party=party, line_no=n, raw=line)
        for seg in text.split("\\"):
            tokens = [t.strip() for t in seg.split(",") if t.strip()]
            if not tokens:
                continue
            first = tokens[0]
            if not stopped and (m := RE_STEP.match(first)):
                code = self.terrain.code(m["code"])
                cur = coords.step(cur, m["dir"])
                steps.append(Step(len(steps) + 1, m["dir"], cur, "ok"))
                r.obs.append(Obs("terrain", cur, code or m["code"].upper(), how=how_in,
                                 confidence="confirmed" if code else "likely", step=len(steps), **base))
                if not code:
                    unparsed(n, line, f"unknown terrain code {m['code']!r}")
                tokens = tokens[1:]
            last = None  # the previous dir-list item, for bare 'S' continuations
            for tok in tokens:
                last, stop = self._token(r, unit, cur, tok, last, len(steps), base, n, line, unparsed, steps)
                stopped = stopped or stop
        unit.paths[party] = steps
        if moving and unit.hex and steps and cur != unit.hex:
            unparsed(n, line, f"movement ends at {coords.label(cur)} but header says {unit.hex_text}")

    def _token(self, r, unit, cur, tok, last, step_no, base, n, line, unparsed, steps):
        """Handle one comma-separated item seen from hex cur. Returns (dir-list item for continuations, stopped)."""
        add = lambda *a, **k: r.obs.append(Obs(*a, step=step_no, **k, **base))
        if m := RE_MP_BLOCK.match(tok):
            code = self.terrain.code(m["name"])
            target = coords.step(cur, m["dir"])
            steps.append(Step(step_no + 1, m["dir"], target, "mp_block"))
            add("terrain", target, code or m["name"].upper(), how="glimpsed",
                confidence="confirmed" if code else "likely")
            if not code:
                unparsed(n, line, f"unknown terrain name {m['name']!r}")
            return None, True
        if m := RE_NO_FORD.match(tok):
            steps.append(Step(step_no + 1, m["dir"], coords.step(cur, m["dir"]), "no_ford"))
            add("river", cur, "river", dir=m["dir"])
            add("no_ford", cur, "no ford", dir=m["dir"])
            return None, True
        if RE_NOTHING.match(tok):
            add("nothing_found", cur, "nothing of interest")
            return None, False
        if m := RE_FIND.match(tok):
            what = m["what"].strip()
            kind = "item" if what[0].isdigit() else "resource"
            add(kind, cur, what.upper() if kind == "resource" else what)
            return None, False
        if m := RE_EDGE.match(tok):
            item = ("edge", m["kind"].lower())
            self._edge(add, cur, m["kind"].lower(), m["dirs"].split())
            return item, False
        if DIRS_ONLY.match(tok) and last:
            if last[0] == "edge":
                self._edge(add, cur, last[1], tok.split())
            else:
                for d in tok.split():
                    add("terrain", coords.step(cur, d), last[1], how="glimpsed")
            return last, False
        if (m := RE_SEEN.match(tok)) and (code := self.terrain.code(m["code"])):
            for d in m["dirs"].split():
                add("terrain", coords.step(cur, d), code, how="glimpsed")
            return ("seen", code), False
        if RE_UNIT_ID.match(tok):
            if not self._own(tok):
                add("unit_sighting", cur, tok)
            return None, False
        add("special", cur, tok, confidence="likely")
        unparsed(n, line, f"kept as special: {tok!r}")
        return None, False

    def _edge(self, add, cur, kind, dirs):
        for d in dirs:
            add("river", cur, "river", dir=d)
            if kind == "ford":
                add("ford", cur, "ford", dir=d)

    def _status(self, r, unit, text, n, line, unparsed):
        unit.status = text
        if unit.hex is None:
            unparsed(n, line, "status for a unit with no known hex")
            return
        items = [t.strip() for t in text.split(",") if t.strip()]
        base = dict(unit=unit.unit, party="status", line_no=n, raw=line, how="status")
        if not items:
            return
        code = self.terrain.code(items[0])
        if code:
            r.obs.append(Obs("terrain", unit.hex, code, **base))
        else:
            unparsed(n, line, f"unknown terrain name {items[0]!r}")
        seen = None
        for it in items[1:]:
            if RE_UNIT_ID.match(it):
                if not self._own(it):
                    r.obs.append(Obs("unit_sighting", unit.hex, it, **base))
            elif (m := RE_SEEN.match(it)) and (code := self.terrain.code(m["code"])):
                # 'Lcm N': tall terrain / water seen in a neighbouring hex
                seen = code
                for d in m["dirs"].split():
                    r.obs.append(Obs("terrain", coords.step(unit.hex, d), code, **{**base, "how": "glimpsed"}))
                continue
            elif DIRS_ONLY.match(it) and seen:
                for d in it.split():
                    r.obs.append(Obs("terrain", coords.step(unit.hex, d), seen, **{**base, "how": "glimpsed"}))
                continue
            elif it.isupper() or it.upper() in RESOURCES or it.upper().endswith(" ORE"):
                r.obs.append(Obs("resource", unit.hex, it.upper(), **base))
            else:
                r.obs.append(Obs("special", unit.hex, it, confidence="likely", **base))
                unparsed(n, line, f"kept as special: {it!r}")
