"""Read-side queries: what we know about each hex (as of a turn), where units are, what is unscouted."""
import json
from dataclasses import dataclass, field

from . import coords

# How much a terrain report is worth. Our own information (reports, our corrections, the GM) is verified
# and always outranks what other players tell us ('reported') or guesses.
HOW_RANK = {"manual": 5, "entered": 4, "moved": 4, "status": 4, "glimpsed": 3, "mapped": 3, "reported": 2, "guess": 1}
CONF_RANK = {"confirmed": 3, "likely": 2, "guess": 1}
# Full knowledge (terrain + resources) comes only from scouts entering a hex or a unit's Status line there.
# A tribe moving through a hex ('moved') reports its terrain only, like a glimpse.
SCOUTED = {"entered", "status"}
UNVERIFIED = {"reported", "guess"}
# mapped: from a .wxx counted as our own (browser-only version); it has no turn, so any report of ours outranks it
HOW_TO_TERRAIN_HOW = {"manual": "manual", "glimpsed": "glimpsed", "mapped": "mapped", "moved": "passed",
                      "reported": "reported",
                      "guess": "guess", "entered": "scouted", "status": "scouted"}


def turn_key(turn):
    """'906-04' -> (906, 4); None/'' sorts first."""
    if not turn:
        return (0, 0)
    y, m = turn.split("-")
    return int(y), int(m)


def turns_between(a, b):
    (ya, ma), (yb, mb) = turn_key(a), turn_key(b)
    return (yb - ya) * 12 + (mb - ma)


# Fog: how grey a hex we scouted long ago is drawn. A turn is a month, so nothing for the first year, then
# deepening to full strength at three years.
FOG_START, FOG_FULL, FOG_MIN, FOG_MAX = 12, 36, 0.12, 0.55


def fog_alpha(age):
    """Grey overlay opacity for ground last scouted `age` turns ago (0 = none)."""
    if age is None or age < FOG_START:
        return 0.0
    return round(FOG_MIN + (FOG_MAX - FOG_MIN) * min(1.0, (age - FOG_START) / (FOG_FULL - FOG_START)), 3)


@dataclass
class HexInfo:
    hex: tuple
    terrain: str | None = None
    terrain_how: str = ""            # scouted, glimpsed, passed, manual (ours); reported, guess (unverified)
    terrain_as_of: str | None = None   # the turn the chosen terrain was true
    terrain_provider: str | None = None
    first_seen: str | None = None    # first_seen / last_seen / last_scouted: our own information only
    last_seen: str | None = None
    last_scouted: str | None = None
    last_known: str | None = None    # the latest turn anything is known for, from anyone
    resources: set = field(default_factory=set)
    items: list = field(default_factory=list)
    specials: set = field(default_factory=set)
    sightings: list = field(default_factory=list)   # (unit id, turn)
    nothing_found: bool = False
    conflicts: list = field(default_factory=list)    # other terrain values our own visits reported
    unverified: list = field(default_factory=list)   # facts from other players / guesses, with provenance
    _best: tuple = (-1,)
    _scouted_values: set = field(default_factory=set)

    @property
    def label(self):
        return coords.label(self.hex)

    @property
    def verified(self):
        return self.terrain_how not in UNVERIFIED

    @property
    def scouted(self):
        """Fully known: our scouts entered it or our unit stood in it, so resources were checked too."""
        return self.last_scouted is not None

    @property
    def knowledge(self):
        """full (terrain + resources), terrain (terrain only: needs scouting), reported (others' word only), none."""
        if self.scouted:
            return "full"
        if self.terrain and self.verified:
            return "terrain"
        if self.terrain:
            return "reported"
        return "none"

    @property
    def age_unknown(self):
        return any(u["as_of"] is None for u in self.unverified)

    def as_dict(self):
        return {"hex": self.label, "terrain": self.terrain, "terrain_how": self.terrain_how,
                "verified": self.verified, "terrain_as_of": self.terrain_as_of,
                "terrain_provider": self.terrain_provider, "scouted": self.scouted, "knowledge": self.knowledge,
                "first_seen": self.first_seen, "last_seen": self.last_seen,
                "last_scouted": self.last_scouted, "last_known": self.last_known,
                "resources": sorted(self.resources), "items": self.items, "specials": sorted(self.specials),
                "sightings": [{"unit": u, "turn": t} for u, t in self.sightings],
                "nothing_found": self.nothing_found, "conflicts": self.conflicts,
                "unverified": self.unverified}


def observations(conn, as_of=None):
    rows = conn.execute("SELECT o.*, s.kind AS source_kind, s.provider FROM observation o "
                        "JOIN source s ON s.id = o.source_id ORDER BY o.id").fetchall()
    if as_of:
        limit = turn_key(as_of)
        rows = [r for r in rows if not r["turn"] or turn_key(r["turn"]) <= limit]
    return rows


def _later(a, b):
    """The later of two turns (either may be None)."""
    if a is None:
        return b
    if b is None:
        return a
    return a if turn_key(a) >= turn_key(b) else b


def knowledge(conn, as_of=None):
    """Returns (hexes: {(gx,gy): HexInfo}, edges: {((gx,gy),dir): {kind: verified}}).

    Edge kinds are river, ford, no_ford; verified is True if any of our own observations reported it.
    """
    hexes, edges = {}, {}
    for o in observations(conn, as_of):
        h = (o["gx"], o["gy"])
        own = o["how"] not in UNVERIFIED
        if o["dir"]:
            kinds = edges.setdefault((h, o["dir"]), {})
            kinds[o["kind"]] = kinds.get(o["kind"], False) or own
            continue
        info = hexes.setdefault(h, HexInfo(h))
        t = o["turn"]
        info.last_known = _later(info.last_known, t)
        if t and own:
            if info.first_seen is None or turn_key(t) < turn_key(info.first_seen):
                info.first_seen = t
            info.last_seen = _later(info.last_seen, t)
            if o["how"] in SCOUTED:
                info.last_scouted = _later(info.last_scouted, t)
        k = o["kind"]
        if k == "terrain":
            rank = (HOW_RANK.get(o["how"], 0), CONF_RANK.get(o["confidence"], 0), turn_key(t), o["id"])
            if o["how"] in SCOUTED or o["how"] == "moved":   # our own first-hand terrain reports
                info._scouted_values.add(o["value"])
            if rank > info._best:
                info._best = rank
                info.terrain = o["value"]
                info.terrain_how = HOW_TO_TERRAIN_HOW.get(o["how"], "scouted")
                info.terrain_as_of = t
                info.terrain_provider = None if own else o["provider"]
        if not own:
            info.unverified.append({"kind": k, "value": o["value"], "provider": o["provider"],
                                    "as_of": t, "received": o["received_at"], "how": o["how"],
                                    "note": o["note"]})
            continue
        if k == "resource":
            info.resources.add(o["value"])
        elif k == "item":
            info.items.append(o["value"])
        elif k == "special":
            info.specials.add(o["value"])
        elif k == "unit_sighting":
            info.sightings.append((o["value"], t))
        elif k == "nothing_found":
            info.nothing_found = True
    for info in hexes.values():
        # two of our own visits disagreeing about terrain is worth a look (a manual fix settles it)
        if info.terrain_how != "manual":
            info.conflicts = sorted(info._scouted_values - {info.terrain})
    return hexes, edges


def latest_turn(conn):
    turns = [r["turn"] for r in conn.execute("SELECT DISTINCT turn FROM source WHERE turn IS NOT NULL")]
    return max(turns, key=turn_key) if turns else None


def unit_history(conn, unit):
    rows = conn.execute("SELECT * FROM unit_turn WHERE unit = ?", (unit,)).fetchall()
    return sorted(rows, key=lambda r: turn_key(r["turn"]))


def unit_positions(conn, as_of=None):
    """{unit: row} for each unit's latest known position (up to as_of)."""
    out = {}
    for r in conn.execute("SELECT * FROM unit_turn WHERE gx IS NOT NULL").fetchall():
        if as_of and turn_key(r["turn"]) > turn_key(as_of):
            continue
        if r["unit"] not in out or turn_key(r["turn"]) > turn_key(out[r["unit"]]["turn"]):
            out[r["unit"]] = r
    return out


def where(conn, unit, history=False):
    rows = unit_history(conn, unit)
    if not rows:
        return None
    def fmt(r):
        return {"unit": r["unit"], "turn": r["turn"], "hex": r["hex_text"],
                "previous_hex": coords.label((r["prev_gx"], r["prev_gy"])) if r["prev_gx"] else None,
                "status": r["status"]}
    if history:
        return [fmt(r) for r in rows]
    out = fmt(rows[-1])
    u = conn.execute("SELECT * FROM unit WHERE id = ?", (unit,)).fetchone()
    out.update(kind=u["kind"], name=u["name"], parent=u["parent"],
               skills=json.loads(rows[-1]["skills_json"] or "{}"),
               inventory=json.loads(rows[-1]["inventory_json"] or "{}"))
    return out


def resolve_place(conn, text):
    """A hex label or a unit id -> (gx, gy)."""
    h = coords.try_parse(text)
    if h:
        return h
    pos = unit_positions(conn).get(text)
    if pos:
        return pos["gx"], pos["gy"]
    raise ValueError(f"{text!r} is neither a hex label nor a unit with a known position")


def hex_detail(conn, h, as_of=None):
    hexes, edges = knowledge(conn, as_of)
    info = hexes.get(h, HexInfo(h)).as_dict()
    sides = {}
    for d in coords.DIRS:
        kinds = edges.get(coords.edge(h, d))
        if kinds:
            sides[d] = sorted(k if ok else f"{k} (unverified)" for k, ok in kinds.items())
    info["hexsides"] = sides
    info["units_here"] = [u for u, r in unit_positions(conn, as_of).items() if (r["gx"], r["gy"]) == h]
    rows = conn.execute(
        "SELECT o.turn, o.unit, o.party, o.kind, o.value, o.dir, o.how, o.confidence, o.raw, o.note,"
        " o.received_at, s.kind AS source_kind, s.provider, s.filename, s.season, s.weather"
        " FROM observation o JOIN source s ON s.id = o.source_id"
        " WHERE (o.gx = ? AND o.gy = ? AND o.dir IS NULL) ORDER BY o.id", h).fetchall()
    info["history"] = [dict(r) for r in rows]
    return info


def unscouted(conn, centre, radius, stale=None, as_of=None):
    """Hexes within radius of centre that were never scouted, or (with stale=N) not scouted for N+ turns."""
    hexes, _ = knowledge(conn, as_of)
    now = as_of or latest_turn(conn)
    out = []
    for h in sorted(coords.within(centre, radius), key=lambda h: (coords.distance(centre, h), h)):
        info = hexes.get(h)
        if info and info.scouted:
            if stale is None or info.terrain_how == "manual" or \
                    turns_between(info.last_scouted, now) < stale:
                continue
        out.append({"hex": coords.label(h), "distance": coords.distance(centre, h),
                    "terrain": info.terrain if info else None,
                    "known_as": info.terrain_how if info else "unknown",
                    "knowledge": info.knowledge if info else "none",
                    "last_scouted": info.last_scouted if info else None,
                    "last_known": info.last_known if info else None})
    return out


# --- people we know: other clans' units, where they were last known to be ---------------------------------

CONTACT_FIELDS = ["clan", "name", "email", "discord", "relation", "truce", "note"]   # truce: "yes" once declared


def clan_of(unit):
    """'1123' or '0123e1' -> '0123' (a clan's tribes share the last three digits; the clan is the 0xxx one)."""
    return "0" + unit[1:4] if len(unit) >= 4 and unit[:4].isdigit() else unit


def contacts(cfg):
    """{clan: {name, email, discord, relation, note}} from contacts.csv (kept with the game data)."""
    import csv
    if not cfg.contacts_csv.exists():
        return {}
    with open(cfg.contacts_csv, newline="", encoding="utf-8") as f:
        return {r["clan"].strip().zfill(4): {k: (r.get(k) or "").strip() for k in CONTACT_FIELDS if k != "clan"}
                for r in csv.DictReader(f) if (r.get("clan") or "").strip()}


def people(conn, cfg, as_of=None):
    """Every other clan's unit we have a position for, at its latest known hex: from our own reports' sightings
    (verified) or from what a player told us (unverified; our own sighting wins a tie). Named from contacts.csv."""
    rows = conn.execute(
        "SELECT o.value AS unit, o.gx, o.gy, o.turn, o.note, o.confidence, s.kind AS source, s.provider"
        " FROM observation o"
        " JOIN source s ON s.id = o.source_id WHERE o.kind = 'unit_sighting'").fetchall()
    book = contacts(cfg)
    latest = {}
    for r in rows:
        if as_of and r["turn"] and turn_key(r["turn"]) > turn_key(as_of):
            continue
        own = r["source"] in ("own_report", "manual", "gm")
        key = (turn_key(r["turn"]), own)
        if r["unit"] not in latest or key > latest[r["unit"]][0]:
            latest[r["unit"]] = (key, r, own)
    out = []
    for unit, (_, r, own) in sorted(latest.items()):
        clan = clan_of(unit)
        c = book.get(clan, {})
        out.append({"unit": unit, "clan": clan, "name": c.get("name", ""), "relation": c.get("relation", ""),
                    "truce": c.get("truce", "").lower() in ("yes", "y", "true", "1"),
                    "gx": r["gx"], "gy": r["gy"], "turn": r["turn"], "verified": own,
                    "provider": None if own else r["provider"], "note": r["note"],
                    "approx": r["confidence"] == "guess"})   # only the map sheet is known (tn contact at <clan> DO)
    return out


# --- history: where everyone has been, turn by turn ----------------------------------------------------------

def history(conn, cfg=None, first=None, last=None):
    """Each unit's track over the turns first..last (inclusive; None = no limit), oldest first.

    Ours: per turn, the hexes its move went through (from the hex it left) to where it ended. Other clans' units
    (when cfg is given, for contacts' names): each turn's sighting, ours or a player's word (ours wins a tie).
    Returns [{unit, own, clan, name, turns: [{turn, hex: (gx, gy), path: [(gx, gy), ...], verified}]}].
    """
    def inside(turn):
        k = turn_key(turn)
        return (not first or k >= turn_key(first)) and (not last or k <= turn_key(last))

    steps = {}
    for r in conn.execute("SELECT unit, turn, gx, gy FROM unit_path WHERE party = 'move' AND result = 'ok'"
                          " ORDER BY unit, turn, step"):
        steps.setdefault((r["unit"], r["turn"]), []).append((r["gx"], r["gy"]))
    tracks = {}
    for r in conn.execute("SELECT * FROM unit_turn WHERE gx IS NOT NULL"):
        if not inside(r["turn"]):
            continue
        end = (r["gx"], r["gy"])
        path = [(r["prev_gx"], r["prev_gy"])] if r["prev_gx"] is not None else []
        path += [p for p in steps.get((r["unit"], r["turn"]), []) if not path or p != path[-1]]
        if not path or path[-1] != end:
            path.append(end)
        tracks.setdefault(r["unit"], []).append({"turn": r["turn"], "hex": end, "path": path, "verified": True})

    book = contacts(cfg) if cfg is not None else {}
    seen = {}
    for r in conn.execute("SELECT o.value AS unit, o.gx, o.gy, o.turn, s.kind AS source FROM observation o"
                          " JOIN source s ON s.id = o.source_id WHERE o.kind = 'unit_sighting' AND o.turn IS NOT NULL"
                          " AND o.confidence IS NOT 'guess'"):   # a sheet-only guess isn't a place to draw a track
        if not inside(r["turn"]) or r["unit"] in tracks:
            continue
        own = r["source"] in ("own_report", "manual", "gm")
        key = (r["unit"], r["turn"])
        if key not in seen or (own and not seen[key]["verified"]):
            seen[key] = {"turn": r["turn"], "hex": (r["gx"], r["gy"]), "path": [(r["gx"], r["gy"])], "verified": own}
    others = {}
    for (unit, _), t in seen.items():
        others.setdefault(unit, []).append(t)

    out = []
    for own, group in ((True, tracks), (False, others)):
        for unit, turns in sorted(group.items()):
            turns.sort(key=lambda t: turn_key(t["turn"]))
            clan = clan_of(unit)
            out.append({"unit": unit, "own": own, "clan": clan, "name": book.get(clan, {}).get("name", ""),
                        "turns": turns})
    return out
