"""TribePlanner, first version: each of our units as it starts a planned turn (see docs/planner-design.md).

A sheet says where the unit is, what it has (people, herds, goods, skills, weight against what it can carry),
what the plan has it doing (its move and scout parties) and what it did last turn, plus checks on the plain
facts: food, weight, herders, growth, winter and the Fair. Every check names where its rule comes from; none
of them guesses at yields (that needs the rules file, which Tristan approves first).
"""
import json
import math
import re

from . import coords, plans, worksheet, yields
from .planner import WATER
from .queries import knowledge, turn_key, unit_positions

# The GM's calendar (Nov 2025; Working Files/knowledge/rules.md): month -> events
EVENTS = [("Fair", (4, 10)), ("Tinker", (2, 8)), ("New-clan Auction", (5, 11)),
          ("Auction", (1, 3, 6, 7, 9, 12)), ("Seeking", (1,)), ("Triball League", (12,))]
WINTER = (10, 11, 12)
CALENDAR_SOURCE = "GM's calendar, Nov 2025"

# What each event is and what to get ready, for the calendar's pop-up. From rules.md, Tribe News (906-06) and
# Discord; each says where it comes from.
EVENT_INFO = {
    "Fair": {
        "what": "The big trading event. Sell goods for silver, and trade your clan's two Desired Commodities for "
                "warriors or inactives. Triball (up to 800 warriors; horses and clubs add to it) and the four "
                "cultural events (up to 500 people each) earn silver even with nothing to sell.",
        "prepare": ["To trade there a tribe needs Economics 5, Economics 4 with a Trading Post in its hex (anyone's "
                    "post), or Diplomacy 7.",
                    "Goods to sell must be held at the start of the Fair turn: make them the turn before.",
                    "Your clan's two Desired Commodities are at the top of your turn report."],
        "source": "rules.md (Fair); Scott, 28 Oct 2026"},
    "Tinker": {
        "what": "A small Fair: the Tinker sells a few goods for silver.",
        "prepare": ["Keep silver on hand.",
                    "Tea is worth buying at the maximum: clans that desire Tea pay about 10 times as much.",
                    "Transfers to the Tinker use unit code 2263."],
        "source": "rules.md (Events); Tribe News 906-06"},
    "New-clan Auction": {
        "what": "An auction only for clans that started in the previous 18 game months. Only about 10% of those "
                "eligible bid, so the odds are good.",
        "prepare": ["Follow the auction notice for where to send bids.",
                    "Goods you bid go to 'usage' and are gone: don't use them in Activities too.",
                    "You aren't told whether you won: check the transfers on your report. Discord #auctions lists "
                    "the winners."],
        "source": "Tribe News 906-06; Discord (Quokkas, Jan 2026; Mike J)"},
    "Auction": {
        "what": "A regular auction. Items are posted on Discord's #auctions channel; since Oct 2026 they're "
                "reserved for newer clans (started 904-11 or later).",
        "prepare": ["Follow the auction notice for where to send bids.",
                    "Goods you bid go to 'usage' and are gone: don't use them in Activities too.",
                    "You aren't told whether you won: check the transfers on your report."],
        "source": "Tribe News 906-06; Discord (Quokkas, Oct 2026)"},
    "Seeking": {
        "what": "Once a year: warriors (and horses) go out seeking items, and can bring back goods and actives you "
                "can't get otherwise. No random factor: warriors, horses and Scouting decide the results.",
        "prepare": ["Use that year's Seeking spreadsheet.",
                    "About 50 warriors per item, plus horses; take them off other activities that turn.",
                    "One transfer per item, from 4263 to the seeking unit.",
                    "Rich Seeking hexes need the unit in the hex at the start of the turn."],
        "source": "Discord (Jeff Perkins, Raven, Aug 2026; Jeff Perkins, Sep 2025)"},
    "Triball League": {
        "what": "A Triball league run by Jeff Perkins: free to enter, no people committed, about 1,600 silver just "
                "for entering.",
        "prepare": ["Send your tribe number and Triball skill using the league notice."],
        "source": "rules.md (Events)"},
    "Winter": {
        "what": "Months 10 to 12.",
        "prepare": ["Hunting is very poor. Fishing doesn't change with the season (it needs a river, lake or ocean "
                    "hexside); Furrier gives about 1:1:1 provs:furs:skins.",
                    "Only goats breed in winter.",
                    "Mining deaths are worst in winter.",
                    "Travel is slower."],
        "source": "GM's calendar, Nov 2025; rules.md"},
}

# Herders needed (rules.md, from the Mandate): 1 per 20 goats, 1 per 10 cattle, horses or dogs, 1 per 5 elephants
HERD_SIZE = {"Goat": 20, "Cattle": 10, "Horse": 10, "Dog": 10, "Elephant": 5}
PEOPLE = ("Warriors", "Actives", "Inactives")
NEAR_FULL = 0.9   # carrying more than this share of capacity: a big hunt could stop it moving


def months_until(month, months):
    """Turns from `month` until the next of `months` (0 if it's this month)."""
    return min((m - month) % 12 for m in months)


def calendar(turn):
    """The planned turn's month, whether it's winter, and how far off each event is."""
    _, month = turn_key(turn)
    events = []
    for name, months in EVENTS:
        n = months_until(month, months)
        events.append({"name": name, "in_turns": n, "month": (month + n - 1) % 12 + 1, "months": months,
                       **EVENT_INFO[name]})
    events.sort(key=lambda e: e["in_turns"])
    return {"turn": turn, "month": month, "winter": month in WINTER,
            "winter_in": months_until(month, WINTER), "winter_info": {"months": WINTER, **EVENT_INFO["Winter"]},
            "events": events, "source": CALENDAR_SOURCE}


def herders_needed(animals):
    """Total herders and each animal's share. Add the shares and round up once."""
    shares = {a: n / HERD_SIZE[a] for a, n in animals.items() if a in HERD_SIZE and n}
    return math.ceil(round(sum(shares.values()), 6)), {a: round(v, 1) for a, v in shares.items()}


RE_HERDERS = re.compile(r"([\d,]+) herders allocated, ([\d,]+) actual herders required")


# --- weight: the orders workbook's Valid Goods weights (data/goods.csv) give the report's Weight exactly -------------

_GOODS = None


def goods():
    """{NAME: (short name, weight, table)} by both the long name (as transfers write it: 'PROVS') and the short one
    (as the inventory does: 'Provs'), upper-cased."""
    global _GOODS
    if _GOODS is None:
        import csv
        from .config import PACKAGE_DATA
        _GOODS = {}
        with open(PACKAGE_DATA / "goods.csv", newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                entry = (r["short"], float(r["weight"] or 0), r["table"])
                _GOODS[r["goods"].upper()] = _GOODS[r["short"].upper()] = entry
    return _GOODS


def transfer_goods_codes():
    """Map report aliases to the official Transfers sheet spelling."""
    import csv
    from .config import PACKAGE_DATA
    with open(PACKAGE_DATA / "goods.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return {alias: row["goods"] for row in rows for alias in (row["short"].upper(), row["goods"].upper())}


_USES = None


def recipe_text(r):
    """'Tanning 1: 4 Skin + 10 Bark, 1 worker -> 4 Leather (Mandate 13.1.33)'."""
    ins = " + ".join(f"{q} {g.title()}" for g, q in (p.rsplit(" ", 1) for p in r["inputs"].split("; ") if p))
    who = f", {r['people']} worker{'s' * (r['people'] != '1')}" if r["people"] else ""
    where = f", needs {r['also_needs']}" if r["also_needs"] else ""
    return (f"{r['activity']}{' ' + r['level'] if r['level'] else ''}: {ins or 'no materials'}{who}{where}"
            f" -> {r['makes']} {r['item']} (Mandate {r['section']})")


def goods_info(short):
    """What a good is for: its weight; the Mandate's recipes that make it and that use it (data/recipes.csv, built
    by tools/build_recipes.py); the activities it's a tool for, and others the orders workbook links it to
    (data/goods_uses.csv); and notes on other uses (data/goods_notes.csv, each with its source)."""
    global _USES
    import csv
    from .config import PACKAGE_DATA
    if _USES is None:
        _USES = {"uses": {}, "notes": {}, "long": {}, "made": {}, "used": {}}
        with open(PACKAGE_DATA / "recipes.csv", newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r["good"]:
                    _USES["made"].setdefault(r["good"], []).append(r)
                for part in r["inputs"].split("; "):
                    if part:
                        _USES["used"].setdefault(part.rsplit(" ", 1)[0], []).append((r, part.rsplit(" ", 1)[1]))
        with open(PACKAGE_DATA / "goods.csv", newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                _USES["long"].setdefault(r["short"].upper(), r["goods"].upper())
        with open(PACKAGE_DATA / "goods_uses.csv", newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                _USES["uses"].setdefault(r["goods"], []).append(r)
        with open(PACKAGE_DATA / "goods_notes.csv", newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                _USES["notes"].setdefault(r["goods"], []).append({"note": r["note"], "source": r["source"]})
    name = _USES["long"].get(short.upper(), short.upper())
    known = goods().get(name)
    out = {"name": name.title(), "weight": known[1] if known else None, "table": known[2].title() if known else "",
           "notes": _USES["notes"].get(name, [])}
    for rel in ("made_by", "processed_by", "material_for", "tool_for"):
        acts = {}   # activity -> items (for material_for: what it can be made into)
        for r in _USES["uses"].get(name, []):
            if r["relation"] == rel:
                items = acts.setdefault(r["activity"].title(), [])
                if rel == "material_for" and r["item"] and r["item"].title() not in items:
                    items.append(r["item"].title())
        out[rel] = [a + (": " + ", ".join(i[:6]) + (", …" if len(i) > 6 else "") if i else "")
                    for a, i in acts.items()]
    out["recipes"] = [recipe_text(r) for r in _USES["made"].get(name, [])]
    used = {}   # activity -> ['Jerkins (4)', ...]: what it goes into, and how much
    for r, q in _USES["used"].get(name, []):
        x = f"{r['item']} ({q})"
        if x not in used.setdefault(r["activity"], []):
            used[r["activity"]].append(x)
    out["used_in"] = [f"{a}: " + ", ".join(i[:8]) + (", …" if len(i) > 8 else "") for a, i in used.items()]
    if out["recipes"]:
        out["made_by"] = []        # the Mandate's recipes say it better
    if out["used_in"]:
        out["material_for"] = []
    return out


_SKILLS = None


def skill_list():
    """{short name: {skill, group, limited, description, source}} from data/skills.csv (the orders workbook's
    Valid_Skills, with the Mandate's worker limits and descriptions)."""
    global _SKILLS
    if _SKILLS is None:
        import csv
        from .config import PACKAGE_DATA
        with open(PACKAGE_DATA / "skills.csv", newline="", encoding="utf-8") as f:
            _SKILLS = {r["short"]: {"skill": r["skill"], "group": r["group"], "limited": r["limited"] == "yes",
                                    "description": r["description"], "source": r["source"]}
                       for r in csv.DictReader(f)}
    return _SKILLS


_ACTIVITIES = None


def activity_list():
    """Activity, item, distinction and outcome label from the v1.13 orders workbook."""
    global _ACTIVITIES
    if _ACTIVITIES is None:
        import csv
        from .config import PACKAGE_DATA
        with open(PACKAGE_DATA / "activities.csv", newline="", encoding="utf-8") as f:
            _ACTIVITIES = list(csv.DictReader(f))
    return _ACTIVITIES


def flat(inv):
    """An inventory's goods (not its people) as {short name: n}."""
    return {k: n for sec, items in inv.items() if sec != "Humans" for k, n in items.items()}


def weight_of(items):
    """The weight of {short name: n}; None if any good's weight isn't known."""
    g = goods()
    if any(k.upper() not in g for k in items):
        return None
    return round(sum(g[k.upper()][1] * n for k, n in items.items()))


RE_QTY = re.compile(r"(\d[\d,]*)\s+([A-Z][A-Z' -]*[A-Z])")


def transferred(lines):
    """{short name: net amount received} from the report's transfer lines (sent counts negative; people skipped)."""
    g, out = goods(), {}
    for line in lines:
        sign = 1 if line.startswith("Receive") else -1
        for qty, name in RE_QTY.findall(line.split(":", 1)[-1]):
            short, _, table = g.get(name.strip().upper(), (None, 0, ""))
            if short and table != "HUMANS":
                out[short] = out.get(short, 0) + sign * int(qty.replace(",", ""))
    return out


def predict(prev, cur, transfers):
    """If the unit repeats last turn's activities: its goods next turn and their weight. Last turn's change in each
    good (between the two reports) less what was transferred is what its activities made and used; apply it again.
    None without an earlier report, or if a good's weight isn't known."""
    if prev is None:
        return None
    before, now, moved = flat(prev), flat(cur), transferred(transfers)
    change = {k: now.get(k, 0) - before.get(k, 0) - moved.get(k, 0) for k in set(before) | set(now) | set(moved)}
    nxt = {k: max(0, now.get(k, 0) + d) for k, d in change.items()}
    w = weight_of(nxt)
    if w is None:
        return None
    return {"weight": w, "basis": "if it repeats last turn's activities (the change between its last two reports,"
                                   " transfers left out)",
            "change": {k: d for k, d in sorted(change.items()) if d}}


def _check(level, text, source):
    return {"level": level, "text": text, "source": source}


def checks(sheet, cal):
    """Warnings ('warn') and notes ('info') on what the report and the plan already tell us."""
    out = []
    people, inv = sheet["people"], sheet["inventory"]
    eaters = people.get("People", 0)
    provs = inv.get("Finished Goods", {}).get("Provs", 0)
    if eaters:
        if provs < eaters:
            out.append(_check("warn", f"{provs:,} provs for {eaters:,} people: not enough to eat this turn"
                              " unless it hunts, fishes or is sent provs",
                              "1 prov per person per turn (rules.md)"))
        elif provs < 3 * eaters:
            out.append(_check("info", f"Food for {provs // eaters} turns ({provs:,} provs, {eaters:,} eat)",
                              "1 prov per person per turn (rules.md)"))
    carry, moving = sheet["carry"], sheet["plan"]["move"]
    cap_key = "mounted" if sheet["mover"] == "mounted" else "walking"
    cap, weight = carry.get(cap_key), carry.get("weight")
    if cap is not None and weight is not None:
        how = "mounted" if cap_key == "mounted" else "walking"
        if weight > cap:
            out.append(_check("warn" if moving else "info",
                              f"Weight {weight:,} is over its {how} capacity {cap:,}"
                              + (": it won't move" if moving else ""),
                              "the report's Weight and CC lines"))
        elif moving and cap and weight > NEAR_FULL * cap:
            out.append(_check("info", f"Weight {weight:,} is {weight / cap:.0%} of its {how} capacity: a big hunt or"
                              " transfer this turn could stop it moving", "the report's Weight and CC lines"))
    pred = sheet.get("predicted")
    if pred and cap and moving and weight is not None and weight <= cap < pred["weight"]:
        out.append(_check("info", f"Repeating last turn's activities would take it to about {pred['weight']:,}, over"
                          f" its {how} capacity {cap:,}: it couldn't move the turn after",
                          "goods weights from the orders workbook (Valid Goods); last two reports"))
    herding = sheet["herders"].get("last_turn")
    if herding and herding["allocated"] < herding["required"]:
        out.append(_check("warn", f"Last turn assigned {herding['allocated']:,} herders; {herding['required']:,} were required.",
                          "Reported herder allocation"))
    if eaters and any(people.get(k, 0) == 0 for k in PEOPLE):
        missing = [k.lower() for k in PEOPLE if people.get(k, 0) == 0]
        out.append(_check("info", f"No {' or '.join(missing)}: it won't grow",
                          "growth comes from the smallest of warriors, actives and inactives (rules.md)"))
    hunted = "hunted" in (sheet["last_turn"]["activities"] or "")
    if cal["winter"] and hunted:
        out.append(_check("warn", f"Month {cal['month']} is winter: hunting is very poor. Fishing doesn't change "
                          "with the season; Furrier gives about 1:1:1 provs:furs:skins in winter", CALENDAR_SOURCE))
    elif hunted and cal["winter_in"] == 1:
        out.append(_check("info", "Winter starts next turn: plan food now (hunting is very poor in months 10–12)",
                          CALENDAR_SOURCE))
    if sheet["new"]:
        out.append(_check("info", "Created this turn: a new unit does no activities on its creation turn",
                          "design notes; check with the Mandate"))
    return out


def clan_checks(units, cal):
    """Notes for the whole clan: the Fair, judged by the tribe best placed to trade there."""
    out = []
    fair = next(e for e in cal["events"] if e["name"] == "Fair")
    tribes = [u for u in units if u["kind"] == "Tribe" and not u["new"]]
    if tribes and fair["in_turns"] <= 3:
        best = max(tribes, key=lambda u: (u["skills"].get("Eco", 0) >= 5 or u["skills"].get("Dip", 0) >= 7,
                                         u["skills"].get("Eco", 0)))
        eco, dip = best["skills"].get("Eco", 0), best["skills"].get("Dip", 0)
        when = "this turn" if fair["in_turns"] == 0 else f"in {fair['in_turns']} turn{'s' * (fair['in_turns'] > 1)}"
        access = ("can trade there" if eco >= 5 or dip >= 7 else
                  "needs a Trading Post too (it has Eco 4)" if eco == 4 else
                  f"can't trade there yet (Eco {eco}; it needs Eco 5, Eco 4 and a Trading Post, or Dip 7)")
        out.append(_check("info", f"Fair {when} (month {fair['month']}): {best['unit']} {access}. Goods to sell must"
                          " be held at the start of the Fair turn", "Fair access and timing (rules.md)"))
    return out


def _where(h, hexes, edges, positions, unit):
    """The start hex: terrain, river/ford hexsides, water next door, resources, who else is there."""
    info = hexes.get(h)
    sides, water = {}, []
    for d in coords.DIRS:
        kinds = edges.get(coords.edge(h, d))
        if kinds:
            sides[d] = sorted(kinds)
        n = hexes.get(coords.step(h, d))
        if n and n.terrain in WATER:
            water.append(d)
    return {"hex": coords.label(h), "terrain": info.terrain if info else None,
            "resources": sorted(info.resources) if info else [],
            "specials": sorted(info.specials) if info else [],
            "hexsides": sides, "water": water,
            "river": any("river" in k for k in sides.values()),
            "with": sorted(u for u, r in positions.items() if (r["gx"], r["gy"]) == h and u != unit)}


def transfer_neighbours(units, edges):
    """Transfer options at each planned phase. Mandate sections 8.5 and 8.10."""
    houses = set()
    for u in units:
        where = u["where"]
        if not where:
            continue
        structures = {name.casefold(): quantity for section in ("Structures", "Buildings")
                      for name, quantity in u["inventory"].get(section, {}).items()}
        if any(structures.get(name, 0) for name in ("meeting house", "meetinghouse", "mh")):
            houses.add(where["hex"])

    for u in units:
        u["transfer_neighbours"] = {phase: {} for phase in ("BM", "AM")}
        for phase, options in u["transfer_neighbours"].items():
            h = u["transfer_hexes"][phase]
            if h is None:
                continue
            a = coords.parse(h)
            for other in units:
                destination = other["transfer_hexes"][phase]
                if u is other or destination is None:
                    continue
                b = coords.parse(destination)
                requirements = []
                if a == b:
                    kind = "Same hex"
                else:
                    side = coords.direction(a, b)
                    if side is None:
                        continue
                    kind = "Adjacent"
                    if h in houses or destination in houses:
                        kind += " · Meeting House"
                    else:
                        requirements.append("your clan’s Meeting House in either hex")
                    edge = edges.get(coords.edge(a, side), {})
                    if "river" in edge and not edge.get("ford"):
                        requirements.append("a working ford or ferry on this hexside")
                options[other["unit"]] = {"hex": destination, "kind": kind, "requirements": requirements}


def sheets(conn, terrain, cfg, turn, variant=plans.MAIN):
    """{turn, variant, report_turn, calendar, units: [sheet]} for every unit in the latest report before `turn`,
    plus any unit the plan creates (a move with a start hex and no report yet)."""
    turn, variant = plans.check_turn(turn), plans.check_variant(variant)
    saved_drafts = worksheet.drafts(conn, turn, variant)
    creations = plans.creations(conn, turn, variant)
    reports = sorted({r[0] for r in conn.execute("SELECT turn FROM source WHERE kind = 'own_report'")},
                     key=turn_key)
    before = [t for t in reports if turn_key(t) < turn_key(turn)]
    report_turn = before[-1] if before else (reports[-1] if reports else None)
    cal = calendar(turn)
    hexes, edges = knowledge(conn, report_turn)
    positions = unit_positions(conn, report_turn)
    planned, _ = plans.evaluate(conn, terrain, cfg, turn, report_turn, variant=variant)
    movers = plans.unit_movers(cfg)
    rows = {r["unit"]: r for r in conn.execute("SELECT * FROM unit_turn WHERE turn = ?", (report_turn,))}
    history = {}
    for r in conn.execute("SELECT unit, turn, inventory_json FROM unit_turn"):
        if report_turn and turn_key(r["turn"]) <= turn_key(report_turn):
            history.setdefault(r["unit"], []).append(r)
    units = {r["id"]: r for r in conn.execute("SELECT * FROM unit")}
    ids = sorted(set(rows) | set(creations) | {p.row["unit"] for p in planned}, key=lambda u: (u[1:4], u[0] != "0", u))

    out = []
    for u in ids:
        row, meta = rows.get(u), units.get(u)
        creation = creations.get(u)
        mine = [p for p in planned if p.row["unit"] == u]
        move = next((p for p in mine if p.row["kind"] == "move"), None)
        start = (row["gx"], row["gy"]) if row and row["gx"] is not None else \
            (move.route.start if move and move.route else None)
        if start is None and creation and (source := positions.get(creation["from"])):
            start = (source["gx"], source["gy"])
        inv = json.loads(row["inventory_json"] or "{}") if row else {}
        sheet = {
            "unit": u, "name": (meta["name"] if meta else "") or (creation.get("name", "") if creation else ""),
            "kind": (meta["kind"] if meta else None) or ("Element" if "e" in u else "Tribe"),
            "parent": meta["parent"] if meta else None,
            "goods_tribe": row["goods_tribe"] if row else None,
            "new": row is None, "report_turn": report_turn if row else None, "creation": creation,
            "mover": (move.row["mover"] if move and move.row["mover"] else None) or movers.get(u, "foot"),
            "where": _where(start, hexes, edges, positions, u) if start else None,
            "people": inv.get("Humans", {}),
            "inventory": {k: v for k, v in inv.items() if k != "Humans" and v},
            "skills": json.loads(row["skills_json"] or "{}") if row else {},
            "morale": row["morale"] if row else "",
            "carry": json.loads(row["carry_json"] or "{}") if row and row["carry_json"] else {},
            "herders": dict(zip(("total", "by_animal"), herders_needed(inv.get("Animals", {})))),
            "last_turn": {"activities": row["activities"] if row else "",
                          "final": row["final"] if row else "",
                          "transfers": json.loads(row["transfers_json"] or "[]") if row and row["transfers_json"] else [],
                          "notes": json.loads(row["notes_json"] or "[]") if row and row["notes_json"] else []},
            "plan": {"move": None, "scouts": []},
        }
        sheet["transfer_hexes"] = {
            "BM": coords.label(start) if start else None,
            "AM": coords.label(move.route.end) if move and move.route else
                  coords.label(start) if start and not move else None,
        }
        snapshots = [(r["turn"], json.loads(r["inventory_json"] or "{}"))
                     for r in sorted(history.get(u, []), key=lambda r: turn_key(r["turn"]))]
        sheet["goods_history"] = [{"turn": t, "goods": flat(stock)} for t, stock in snapshots]
        previous = [stock for t, stock in snapshots if t != report_turn]
        sheet["predicted"] = predict(previous[-1], inv, sheet["last_turn"]["transfers"]) if row and previous else None
        for p in mine:
            r = p.route
            d = {"orders": p.row["orders"], "error": p.error,
                 "end": coords.label(r.end) if r else None, "mp_used": r.mp_used if r else None,
                 "mp": r.mp if r else None, "steps": len(r.entered) if r else 0,
                 "stopped": (r.steps[-1].result if r and r.steps and r.steps[-1].result != "ok" else None)}
            if p.row["kind"] == "move":
                sheet["plan"]["move"] = d
            else:
                sheet["plan"]["scouts"].append({**d, "party": p.row["party"], "scouts": p.row["scouts"],
                                                "horses": p.row["horses"], "mission": p.row["mission"]})
        if m := RE_HERDERS.search(sheet["last_turn"]["activities"] or ""):
            allocated, required = (int(value.replace(",", "")) for value in m.groups())
            sheet["herders"]["last_turn"] = {"allocated": allocated, "required": required}
        sheet["checks"] = checks(sheet, cal)
        out.append(sheet)
    known = dict(skill_list())
    transfer_neighbours(out, edges)
    for short in {k for u in out for k in u["skills"]}:
        known.setdefault(short, {"skill": short, "group": "?", "limited": False, "description": "", "source": ""})
    stock = {k for u in out for k in flat(u["inventory"])}
    return {"turn": turn, "variant": variant, "report_turn": report_turn, "calendar": cal,
            "worksheet": saved_drafts, "held_scouts": plans.held_scouts(conn, turn, variant),
            "activity_observations": yields.recent_observations(conn, before, hexes),
            "previous_export": worksheet.previous_export(conn, turn, variant),
            "goods_catalog": {code: {"short": meta[0], "weight": meta[1], "category": meta[2]}
                              for code, meta in goods().items()},
            "checks": clan_checks(out, cal), "units": out,
            "skill_info": known, "activity_info": activity_list(),
            "goods_info": {k: goods_info(k) for k in sorted(stock)}}
