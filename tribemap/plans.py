"""Turn plans: planned unit moves and scout routes, their simulation, coverage and export to the orders sheet.

A plan is a set of plan_route rows for one turn and variant. Planning is always about the next turn, so a
turn can have several variants (alternative plans): 'main' plus any named ones ('B', 'C', ...).
Each variant holds:
    kind 'move'   one per unit: the unit's movement orders (Tribe_Movement sheet)
    kind 'scout'  up to 8 per tribe (party 1-8): Scout_Movement sheet

Movement happens before scouting, so scouts start where their unit's planned move ends.
"""
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

from . import coords
from .planner import (FOLLOW, FOLLOW_BONUS, LIMIT, MAX_ORDERS, best_route, follow_hint, follow_path,
                      parse_orders, river_in_the_way, simulate, value_wedges)
from .queries import knowledge, latest_turn, turn_key, turns_between, unit_positions

MAX_PARTIES = 8
MAIN = "main"
FIELDS = ["turn", "variant", "unit", "kind", "party", "orders", "scouts", "horses", "mission", "start_hex", "mover",
          "colour", "note"]


def turn_folder(turn):
    """'906-06' -> '906-6' (the turn folders' naming)."""
    y, m = turn_key(turn)
    return f"{y}-{m}"


def check_turn(turn):
    y, m = turn_key(turn)
    if not 1 <= m <= 12:
        raise ValueError(f"bad turn {turn!r}; use e.g. 906-06")
    return f"{y}-{m:02d}"


def check_variant(variant):
    v = (variant or MAIN).strip()
    if not re.match(r"^[A-Za-z0-9 _-]{1,24}$", v):
        raise ValueError("plan names may use letters, digits, spaces, - and _ (up to 24)")
    return v


def routes(conn, turn, variant=MAIN):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM plan_route WHERE turn = ? AND variant = ? ORDER BY unit, kind = 'scout', party",
        (check_turn(turn), check_variant(variant)))]


def register(conn, turn, variant=MAIN, note=None):
    """Make sure a plan exists, even with no routes yet."""
    conn.execute("INSERT OR IGNORE INTO plan (turn, variant, note) VALUES (?,?,?)",
                 (check_turn(turn), check_variant(variant), note))


def variants(conn):
    """[(turn, variant), ...] of every saved plan, oldest turn first, 'main' first within a turn."""
    rows = conn.execute("SELECT turn, variant FROM plan UNION SELECT turn, variant FROM plan_route").fetchall()
    return sorted(((r[0], r[1]) for r in rows), key=lambda tv: (turn_key(tv[0]), tv[1] != MAIN, tv[1]))


def copy_plan(conn, turn, src, dst):
    """Copy every route of one variant into another (replacing it). Returns the number copied."""
    turn, src, dst = check_turn(turn), check_variant(src), check_variant(dst)
    if src == dst:
        raise ValueError("copy a plan to a different name")
    conn.execute("DELETE FROM plan_route WHERE turn = ? AND variant = ?", (turn, dst))
    register(conn, turn, dst)
    cols = "unit, kind, party, orders, scouts, horses, mission, colour, note, updated_at, start_hex, mover"
    n = conn.execute(f"INSERT INTO plan_route (turn, variant, {cols}) SELECT turn, ?, {cols} FROM plan_route"
                     " WHERE turn = ? AND variant = ?", (dst, turn, src)).rowcount
    conn.commit()
    return n


def add(conn, turn, unit, kind, orders, party=None, scouts=2, horses=2, mission="PATROL", start=None,
        mover=None, note=None, variant=MAIN):
    """Add or replace a route. Scouts without a party number take the next free one (1-8)."""
    turn, variant = check_turn(turn), check_variant(variant)
    orders = parse_orders(orders)
    if kind not in ("move", "scout"):
        raise ValueError("kind must be move or scout")
    if not orders:
        raise ValueError("no orders given")
    if len(orders) > MAX_ORDERS[kind]:
        raise ValueError(f"{kind} orders are limited to {MAX_ORDERS[kind]} on the orders sheet")
    if start:
        start = coords.label(coords.parse(start))
    register(conn, turn, variant)
    if kind == "move":
        party = None
        if move_changes(conn, turn, unit, ",".join(orders), start, mover, variant):
            drop_scouts(conn, turn, unit, variant)   # they left from the old move's end
        conn.execute("DELETE FROM plan_route WHERE turn = ? AND variant = ? AND unit = ? AND kind = 'move'",
                     (turn, variant, unit))
    else:
        used = {r[0] for r in conn.execute(
            "SELECT party FROM plan_route WHERE turn = ? AND variant = ? AND unit = ? AND kind = 'scout'",
            (turn, variant, unit))}
        if party is None:
            free = [p for p in range(1, MAX_PARTIES + 1) if p not in used]
            if not free:
                raise ValueError(f"{unit} already has {MAX_PARTIES} scout parties planned for {turn}")
            party = free[0]
        elif not 1 <= party <= MAX_PARTIES:
            raise ValueError(f"party must be 1-{MAX_PARTIES}")
        conn.execute("DELETE FROM plan_route WHERE turn = ? AND variant = ? AND unit = ? AND kind = 'scout'"
                     " AND party = ?", (turn, variant, unit, party))
    conn.execute(
        "INSERT INTO plan_route (turn, variant, unit, kind, party, orders, scouts, horses, mission, start_hex,"
        " mover, note, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (turn, variant, unit, kind, party, ",".join(orders), scouts if kind == "scout" else None,
         horses if kind == "scout" else None, mission.upper() if kind == "scout" else None,
         start, mover, note, time.strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    return party


def move_changes(conn, turn, unit, orders, start=None, mover=None, variant=MAIN):
    """Would saving this move change where the unit ends up (so its scout routes no longer fit)?"""
    old = conn.execute("SELECT orders, start_hex, mover FROM plan_route WHERE turn = ? AND variant = ? AND unit = ?"
                       " AND kind = 'move'", (check_turn(turn), check_variant(variant), unit)).fetchone()
    return old is None or (old[0], old[1] or None, old[2] or None) != (orders, start or None, mover or None)


def scout_count(conn, turn, unit, variant=MAIN):
    return conn.execute("SELECT COUNT(*) FROM plan_route WHERE turn = ? AND variant = ? AND unit = ?"
                        " AND kind = 'scout'", (check_turn(turn), check_variant(variant), unit)).fetchone()[0]


def drop_scouts(conn, turn, unit, variant=MAIN):
    """Remove a unit's scout routes (when its move changes, they start from the wrong hex)."""
    return conn.execute("DELETE FROM plan_route WHERE turn = ? AND variant = ? AND unit = ? AND kind = 'scout'",
                        (check_turn(turn), check_variant(variant), unit)).rowcount


def remove(conn, turn, unit=None, kind=None, party=None, variant=MAIN):
    """Remove routes of one plan variant: all of them, or narrowed by unit/kind/party."""
    if kind == "move" and unit:
        drop_scouts(conn, turn, unit, variant)   # deleting a move moves the scouts' start too
    q, args = "DELETE FROM plan_route WHERE turn = ? AND variant = ?", [check_turn(turn), check_variant(variant)]
    for col, val in (("unit", unit), ("kind", kind), ("party", party)):
        if val is not None:
            q += f" AND {col} = ?"
            args.append(val)
    n = conn.execute(q, args).rowcount
    if unit is None and kind is None and party is None:   # the whole plan, not just some of its routes
        conn.execute("DELETE FROM plan WHERE turn = ? AND variant = ?", (check_turn(turn), check_variant(variant)))
    conn.commit()
    return n


# --- evaluation ---------------------------------------------------------------

@dataclass
class Planned:
    row: dict
    route: object = None     # planner.Route
    error: str = ""
    draft: bool = False      # the unsaved route being edited in the browser

    @property
    def name(self):
        r = self.row
        return f"{r['unit']} move" if r["kind"] == "move" else f"{r['unit']} scout {r['party']}"


RE_UNIT = re.compile(r"^\d{4}(?:[a-z]\d+)?$")
MOVERS = ("scout", "mounted", "foot")


def _key(row):
    return row["unit"], row["kind"], row["party"]


def draft_row(conn, turn, d, replace=None, variant=MAIN):
    """A route from the browser editor, validated like add() but not saved.

    d: {unit, kind, orders (list or 'N,NE'), party?, start?, mover?, scouts?, horses?, mission?}
    replace: the (unit, kind, party) of a saved route this draft is editing, if any.
    """
    turn = check_turn(turn)
    unit, kind = str(d.get("unit", "")).strip(), d.get("kind")
    if not RE_UNIT.match(unit):
        raise ValueError(f"unit should look like 0999, 1999 or 0999e1, not {unit!r}")
    if kind not in ("move", "scout"):
        raise ValueError("kind must be move or scout")
    orders = parse_orders(d.get("orders") or [])
    if len(orders) > MAX_ORDERS[kind]:
        raise ValueError(f"{kind} orders are limited to {MAX_ORDERS[kind]} on the orders sheet")
    party = d.get("party")
    if kind == "move":
        party = None
    elif party in (None, ""):
        used = {r[0] for r in conn.execute(
            "SELECT party FROM plan_route WHERE turn = ? AND variant = ? AND unit = ? AND kind = 'scout'",
            (turn, check_variant(variant), unit))}
        if replace and replace[0] == unit and replace[1] == "scout":
            used.discard(replace[2])
        free = [p for p in range(1, MAX_PARTIES + 1) if p not in used]
        if not free:
            raise ValueError(f"{unit} already has {MAX_PARTIES} scout parties planned for {turn}")
        party = free[0]
    else:
        party = int(party)
        if not 1 <= party <= MAX_PARTIES:
            raise ValueError(f"party must be 1-{MAX_PARTIES}")
    start = d.get("start") or None
    if start:
        start = coords.label(coords.parse(start))
    mover = d.get("mover") or None
    if mover and mover not in MOVERS:
        raise ValueError(f"mover must be one of {', '.join(MOVERS)}")
    return {"turn": turn, "variant": check_variant(variant), "unit": unit, "kind": kind, "party": party,
            "orders": ",".join(orders),
            "scouts": int(d.get("scouts") or 2) if kind == "scout" else None,
            "horses": int(d.get("horses") or 2) if kind == "scout" else None,
            "mission": (d.get("mission") or "PATROL").upper() if kind == "scout" else None,
            "start_hex": start, "mover": mover, "colour": None, "note": d.get("note") or None}


def unit_movers(cfg):
    """{unit: mover} from units.csv (kept with the game data): how a unit usually moves, e.g. a tribe or element
    with a horse for everyone and no wagons is mounted. A move with no mover of its own uses this, else foot."""
    import csv
    if not cfg.units_csv.exists():
        return {}
    with open(cfg.units_csv, newline="", encoding="utf-8") as f:
        return {r["unit"].strip(): r["mover"].strip() for r in csv.DictReader(f)
                if (r.get("unit") or "").strip() and (r.get("mover") or "").strip() in MOVERS}


def set_unit_mover(cfg, unit, mover):
    """Save how a unit usually moves (mounted or foot; None or foot clears it back to the default)."""
    import csv
    unit = unit.strip()
    if not unit:
        raise ValueError("which unit?")
    if mover not in (None, "", "foot", "mounted"):
        raise ValueError("a unit moves mounted or foot")
    movers = unit_movers(cfg)
    if mover in (None, "", "foot"):
        movers.pop(unit, None)
    else:
        movers[unit] = mover
    cfg.units_csv.parent.mkdir(parents=True, exist_ok=True)
    with open(cfg.units_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["unit", "mover"])
        w.writerows(sorted(movers.items()))
    return movers


def evaluate(conn, terrain, cfg, turn, as_of=None, draft=None, replace=None, variant=MAIN):
    """Simulate every route in the plan. Returns (planned list, coverage dict).

    draft: an unsaved route (from draft_row) evaluated in place of any saved route with the same
    unit/kind/party, and in place of `replace` (the saved route being edited).
    """
    turn = check_turn(turn)
    hexes, edges = knowledge(conn, as_of)
    positions = unit_positions(conn, as_of)
    rows = routes(conn, turn, variant)
    if draft:
        drop = {_key(draft)} | ({tuple(replace)} if replace else set())
        rows = [r for r in rows if _key(r) not in drop] + [draft]
    out, move_end = [], {}
    usual = unit_movers(cfg)

    def start_of(row, fallback=None):
        if row["start_hex"]:
            return coords.parse(row["start_hex"])
        if fallback:
            return fallback
        pos = positions.get(row["unit"])
        return (pos["gx"], pos["gy"]) if pos else None

    for row in rows:
        if row["kind"] != "move":
            continue
        p = Planned(row)
        start = start_of(row)
        if start is None:
            p.error = f"no known position for {row['unit']}: give it a start hex (--from)"
        else:
            p.route = simulate(start, _orders(row), hexes, edges, terrain,
                               row["mover"] or usual.get(row["unit"], "foot"), mover_mp=cfg.mover_mp,
                               assumed_cost=cfg.assumed_cost)
            move_end[row["unit"]] = p.route.end
        out.append(p)
    for row in rows:
        if row["kind"] != "scout":
            continue
        p = Planned(row)
        start = start_of(row, move_end.get(row["unit"]))
        if start is None:
            p.error = f"no known position for {row['unit']}: give it a start hex (--from) or plan its move"
        else:
            p.route = simulate(start, _orders(row), hexes, edges, terrain,
                               row["mover"] or "scout", mover_mp=cfg.mover_mp, assumed_cost=cfg.assumed_cost)
        out.append(p)
    if draft:
        for p in out:
            p.draft = _key(p.row) == _key(draft)
    return out, coverage(out, hexes, as_of or latest_turn(conn))


# Scout route colours: one per party slot in a tribe (party 1 blue, 2 red, ...). Only one tribe is shown at
# full strength at a time (the browser fades the others), so a tribe's 8 parties never share a colour.
ROUTE_COLOURS = ["#1f77b4", "#d62728", "#2ca02c", "#ff7f0e", "#9467bd", "#17becf", "#8c564b", "#e377c2"]
MOVE_COLOUR = "#111111"
DRAFT_COLOUR = "#e0115f"


def assign_colours(planned):
    """{route name: colour}. Within each tribe's group (the tribe's parties, then its elements' parties)
    the n-th party gets the n-th colour; moves are black and the draft being edited is pink."""
    out, groups = {}, {}
    for p in planned:
        if p.draft:
            out[p.name] = DRAFT_COLOUR
        elif p.row["kind"] == "move":
            out[p.name] = MOVE_COLOUR
        else:
            groups.setdefault(p.row["unit"][:4], []).append(p)
    for tribe, ps in groups.items():
        ps.sort(key=lambda p: (p.row["unit"] != tribe, p.row["unit"], p.row["party"] or 0))
        for i, p in enumerate(ps):
            out[p.name] = ROUTE_COLOURS[i % len(ROUTE_COLOURS)]
    return out


def _orders(row):
    return [o for o in row["orders"].split(",") if o]


STALE = 12   # turns (months) since our last scouting after which a hex is worth scouting again


def hex_status(info, now, stale=STALE):
    """new, verify, terrain_only, stale or known: how much scouting the hex would add."""
    if not info or not info.terrain:
        return "new"
    if not info.verified:
        return "verify"           # only other players' word so far
    if not info.scouted:
        return "terrain_only"     # glimpsed or passed through: resources never checked
    if now and info.last_scouted and turns_between(info.last_scouted, now) >= stale:
        return "stale"
    return "known"


def coverage(planned, hexes, now, stale=STALE):
    """What the plan would add to our map, and where scout parties overlap."""
    entered, glimpsed, starts = {}, set(), {}
    for p in planned:
        if not p.route:
            continue
        starts[p.name] = p.route.start
        for h in p.route.entered:
            entered.setdefault(h, []).append(p.name)
        if p.route.glimpse:
            glimpsed.add(p.route.glimpse)

    def first_step(h, names):
        # 8 parties leave a unit through only 6 neighbouring hexes, so sharing a first step is unavoidable:
        # not flagged when the hex is next to the start of every party involved.
        return all(coords.distance(starts[n], h) == 1 for n in names)

    def status(h):
        return hex_status(hexes.get(h), now, stale)

    by_status = {}
    for h in entered:
        by_status.setdefault(status(h), []).append(coords.label(h))
    shared = {h: [n for n in names if "scout" in n] for h, names in entered.items()}
    shared = {h: names for h, names in shared.items() if len(names) > 1}
    scout_overlap = {coords.label(h): names for h, names in shared.items() if not first_step(h, names)}
    new_glimpses = sorted(coords.label(h) for h in glimpsed - set(entered) if status(h) == "new")
    return {
        "entered": len(entered),
        "by_status": {k: sorted(v) for k, v in sorted(by_status.items())},
        "counts": {k: len(v) for k, v in sorted(by_status.items())},
        "new_glimpses": new_glimpses,
        "scout_overlap": scout_overlap,
        "shared_first_steps": len(shared) - len(scout_overlap),
        "_entered_hexes": entered,
    }


# --- export / load -------------------------------------------------------------

def export(conn, cfg, turn, outdir=None, variant=MAIN):
    """Write the plan as a paste-in workbook (+ JSON copy) into the turn folder. Returns the paths.
    A named variant gets its name in the file names (0999_906_5_B_PlanPaste.xlsx)."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    turn, variant = check_turn(turn), check_variant(variant)
    rows = routes(conn, turn, variant)
    if not rows:
        raise ValueError(f"no plan for {turn}" + ("" if variant == MAIN else f" ({variant})"))
    outdir = Path(outdir) if outdir else cfg.game_dir / turn_folder(turn)
    outdir.mkdir(parents=True, exist_ok=True)
    stem = f"{cfg.clan}_{turn_folder(turn).replace('-', '_')}"
    if variant != MAIN:
        stem += "_" + re.sub(r"[^A-Za-z0-9-]+", "_", variant)

    moves = [[None, r["unit"], None, None, None] + r["orders"].split(",")
             + ["Empty"] * (MAX_ORDERS["move"] - len(r["orders"].split(",")))
             for r in rows if r["kind"] == "move"]
    scouts = [[None, r["unit"], r["scouts"], r["horses"], r["mission"]] + r["orders"].split(",")
              + ["EMPTY"] * (MAX_ORDERS["scout"] - len(r["orders"].split(",")))
              for r in rows if r["kind"] == "scout"]

    wb = Workbook()
    idx = wb.active
    idx.title = "READ ME"
    bold, head = Font(bold=True), PatternFill("solid", fgColor="D9E1F2")
    blocks = []
    for title, headers, data in [
        ("Tribe_Movement", ["UnitName", "TRIBE", "FOLLOW_TRIBE", "MovementType", "Hex"]
         + [f"MOVEMENT_{i}" for i in range(1, MAX_ORDERS["move"] + 1)], moves),
        ("Scout_Movement", ["UnitName", "TRIBE", "No_of_Scouts", "No_of_Horses", "Mission"]
         + [f"Movement{i}" for i in range(1, MAX_ORDERS["scout"] + 1)], scouts),
    ]:
        if not data:
            continue
        ws = wb.create_sheet(title)
        for c, h in enumerate(headers, 1):
            cell = ws.cell(1, c, h)
            cell.font, cell.fill = bold, head
        for r, row in enumerate(data, 2):
            for c, v in enumerate(row, 1):
                if v is not None:
                    ws.cell(r, c, v)
        ws.freeze_panes = "A2"
        blocks.append((title, f"B2:{get_column_letter(len(headers))}{len(data) + 1}"))
    which = "" if variant == MAIN else f", plan {variant}"
    notes = [f"Paste-in orders for Clan {cfg.clan}, turn {turn}{which} (generated by tn plan export)", "",
             "Each tab matches the order sheet of the same name, column for column.",
             "Copy the range below and paste it at B2 of the same sheet in the orders file,",
             "using Paste Values (Ctrl+Shift+V) so the dropdowns, formulas and formatting stay.",
             "Column A is a formula: leave it alone.", ""]
    for r, line in enumerate(notes, 1):
        idx.cell(r, 1, line)
    idx["A1"].font = Font(bold=True, size=14)
    r = len(notes) + 1
    for c, h in enumerate(["Tab", "Copy this range", "Paste at"], 1):
        idx.cell(r, c, h).font = bold
    for title, rng in blocks:
        r += 1
        idx.cell(r, 1, title)
        idx.cell(r, 2, rng)
        idx.cell(r, 3, "B2")
    idx.column_dimensions["A"].width = 22
    idx.column_dimensions["B"].width = 18

    xlsx = outdir / f"{stem}_PlanPaste.xlsx"
    wb.save(xlsx)
    js = outdir / f"{stem}_plan.json"
    js.write_text(json.dumps([{k: r.get(k) for k in FIELDS} for r in rows], indent=1), encoding="utf-8")
    def tab(rows):
        return "\n".join("\t".join("" if v is None else str(v) for v in row[1:]) for row in rows)

    tsv = {"Tribe_Movement": tab(moves), "Scout_Movement": tab(scouts)}
    return xlsx, js, tsv


def load(conn, path):
    """Restore a plan from a JSON export (replaces that plan variant)."""
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    for turn, variant in {(r["turn"], r.get("variant") or MAIN) for r in rows}:
        remove(conn, turn, variant=variant)
    for r in rows:
        add(conn, r["turn"], r["unit"], r["kind"], r["orders"], party=r["party"], scouts=r["scouts"] or 2,
            horses=r["horses"] or 2, mission=r["mission"] or "PATROL", start=r["start_hex"], mover=r["mover"],
            note=r["note"], variant=r.get("variant") or MAIN)
    return len(rows)


# What a scout party gains from a hex, for auto-planning: entering a never-seen hex is worth most; entering a
# hex we only know the terrain of (or only from others, or long ago) still adds its resources; glimpsing a
# never-seen hex at the end of the route adds its terrain.
SCOUT_VALUE = {("new", "entered"): 1.0, ("terrain_only", "entered"): 0.6, ("verify", "entered"): 0.6,
               ("new", "glimpsed"): 0.4}
# Ores (everything but salt) occur in hills and mountains, so entering one we only know the terrain of is worth
# more than other terrain-only ground: a follow order along a mountain range doesn't reveal its resources.
ORE_TERRAIN_VALUE = {**{c: 0.9 for c in ("LCM", "LJM", "LSM", "LAM", "LVM", "UM")},
                     **{c: 0.75 for c in ("BH", "CH", "DH", "GH", "GHP", "JH", "RH", "AH", "SH")}}
# Ground we've already scouted is the lowest tier: never avoided, worth a little for refreshing its
# "last seen", a little more the longer it's been, but always below anything not fully known.
REFRESH_MIN, REFRESH_PER_TURN, REFRESH_MAX = 0.1, 0.02, 0.3


def scout_value(info, now, how):
    """What a scout party learns from a hex: entering ('entered') or looking into it ('glimpsed').
    new 1.0 > terrain-only mountain 0.9 > terrain-only hills 0.75 > other terrain only / unverified 0.6 >
    new look-in 0.4 > our own scouted ground 0.1-0.3 (by age)."""
    status = hex_status(info, now)
    if status == "terrain_only" and how == "entered" and info and info.terrain in ORE_TERRAIN_VALUE:
        return ORE_TERRAIN_VALUE[info.terrain]
    if status in ("known", "stale"):
        if how != "entered":
            return 0.0
        age = turns_between(info.last_scouted, now) if (info and info.last_scouted and now) else 0
        return min(REFRESH_MAX, REFRESH_MIN + REFRESH_PER_TURN * max(0, age))
    return SCOUT_VALUE.get((status, how), 0.0)


MAX_FOLLOW_PARTIES = 4   # at most this many parties go to follow orders; the rest scout by plain moves
FOLLOW_DISCOUNT = 0.8     # a follow path is a projection, so its hexes count for a little less
RIVER_FOLLOW_BONUS = 0.5  # extra priority for following a known river over other features
MIN_FOLLOW_HEXES = 2      # a follow order must have MP left to travel at least this far along the feature
SPIRAL = 0.25             # how much less each ring further from the start is worth (0 = straight rays)
FOLLOWED_VALUE = 0.4      # hexes a follow order may cover are worth this share to other parties
GAP_FILL = 0.1            # extra value per neighbour already scouted/covered (up to 3): sweep next to other
                          # parties instead of leaving unscouted strips between them


TO_LIMIT = {d: o for o, d in LIMIT.items()}   # N -> NL, ...
# A straight run followed by a look-in step another way (NW,NW,NW,N) also becomes a to-limit order, giving up the
# aimed look-in for one straight ahead (decided 29 Oct: to-limit wins for now; set False to keep the look-in).
TO_LIMIT_OVER_LOOK_IN = True


def with_to_limit(orders, start, hexes, edges, terrain, sim, others=frozenset()):
    """A route ending in a straight run of 2+ steps the same way, with that run as one to-limit order instead
    (S,SW,S,S,S -> S,SW,SL): the party keeps going that way until it runs out of MP or is stopped, and it frees
    order slots. With TO_LIMIT_OVER_LOOK_IN, a run followed by a final look-in step in another direction counts
    too (the look-in is dropped). Kept as it was if the to-limit order wouldn't cover at least the same hexes, or
    would walk on into hexes in `others` (ground other parties already cover)."""
    old_r = simulate(start, orders, hexes, edges, terrain, **sim)
    body = list(orders)
    if (TO_LIMIT_OVER_LOOK_IN and len(body) >= 3 and old_r.glimpse and old_r.steps
            and old_r.steps[-1].result == "mp_block" and body[-1] != body[-2]):
        body = body[:-1]   # the look-in step: a to-limit order looks in straight ahead instead
    if len(body) < 2 or body[-1] not in TO_LIMIT:
        return orders
    k = len(body)
    while k > 0 and body[k - 1] == body[-1]:
        k -= 1
    if len(body) - k < 2:
        return orders
    new = body[:k] + [TO_LIMIT[body[-1]]]
    new_r = simulate(start, new, hexes, edges, terrain, **sim)
    extra = [h for h in new_r.entered if h not in old_r.entered]
    if new_r.entered[:len(old_r.entered)] != old_r.entered or any(h in others for h in extra):
        return orders
    return new


def auto_scout(conn, terrain, cfg, turn, unit, parties=None, as_of=None, variant=MAIN):
    """Fill the unit's free scout parties with the routes that gain the most, one party at a time.

    Follow orders come first: for each river / coast / lake / ocean / mountain side the parties can reach,
    a route ending with that follow order is planned, as long as its projected path (planner.follow_path)
    still reaches ground we don't know (up to MAX_FOLLOW_PARTIES). The remaining parties take the
    best plain routes. Hexes already covered by the plan's other scout routes (any unit, including the
    user's own, and the projected follow paths) are worth nothing, so parties fan out instead of
    overlapping. Free parties respect the tribe's shared 8.
    Saves the routes, then re-aims any auto-planned look-in that has become pointless (fix_look_ins).
    Returns {"added": [{party, orders, score, follow}], "look_ins": [{unit, party, old, new}]}.
    """
    turn = check_turn(turn)
    planned, _ = evaluate(conn, terrain, cfg, turn, as_of, variant=variant)
    hexes, edges = knowledge(conn, as_of)
    now = as_of or latest_turn(conn)

    start = None
    for p in planned:
        if p.row["unit"] == unit and p.row["kind"] == "move" and p.route:
            start = p.route.end
    if start is None:
        pos = unit_positions(conn, as_of).get(unit)
        start = (pos["gx"], pos["gy"]) if pos else None
    if start is None:
        raise ValueError(f"no known position for {unit}: plan its move (with a start hex) first")

    tribe = unit[:4]
    group = [p for p in planned if p.row["kind"] == "scout" and p.row["unit"][:4] == tribe]
    used = {p.row["party"] for p in group if p.row["unit"] == unit}
    free = [n for n in range(1, MAX_PARTIES + 1) if n not in used][:max(0, MAX_PARTIES - len(group))]
    if parties is not None:
        free = free[:parties]
    if not free:
        raise ValueError(f"{tribe} already has {MAX_PARTIES} scout parties planned for {turn}")

    covered = set()   # hexes other parties enter (or a follow order is expected to pass through): avoided
    looked = set()    # hexes other parties only look into at the end of a route: still unscouted, so as
                      # worth entering as ever, but not worth looking into again
    followed = set()  # hexes a follow order may pass through (a projection, so not certain): worth less,
                      # but not avoided
    for p in planned:
        if p.row["kind"] == "scout" and p.route:
            covered.update(p.route.entered)
            if p.route.glimpse:
                looked.add(p.route.glimpse)
            if p.route.follow:
                followed.update(follow_path(p.route.end, p.route.follow, p.route.mp_left, hexes, edges, terrain,
                                            cfg.assumed_cost, sure=True) or [])

    def value(h, how):
        if h in looked and how == "glimpsed":
            return 0.0   # another party already looks into it
        if h in covered:
            # another party already gets this hex: walking through it again is a small waste, so a route
            # around it wins when it gains as much
            return -0.1 if how == "entered" else 0.0
        # a hex another party only looks into is still unscouted (a look-in gives terrain, not resources),
        # so entering it is worth as much as any new hex; only looking into it again is pointless (above)
        base = scout_value(hexes.get(h), now, how)
        if h in followed:
            base *= FOLLOWED_VALUE   # a follow order will probably get it: worth less, still allowed
        if base and how == "entered" and hex_status(hexes.get(h), now) not in ("known", "stale"):
            done = sum(1 for n in coords.neighbours(h)
                       if n in covered or n == start or (hexes.get(n) and hexes[n].scouted))
            base += GAP_FILL * min(done, 3)
        # spiral out: nearer rings are worth more, so routes sweep round the inner rings before reaching
        # far, and the parties together cover the area without gaps
        return base / (1 + SPIRAL * max(0, coords.distance(start, h) - 1))

    def follow_value(h, order, left, walked=frozenset()):
        if river_in_the_way(h, order, hexes, edges):
            return None
        heads = follow_hint(h, order, hexes, edges)[2]
        if heads and (h, heads[0]) in follow_heads:
            return None   # another follow already leaves this hex the same way: same corridor, different name
        # the same follow could have started earlier, at a hex the party already walked through: start it there
        # (more MP left for the feature) rather than walking beside it first
        if any(h in (follow_path(w, order, cfg.mover_mp["scout"], hexes, edges, terrain, cfg.assumed_cost) or [])
               for w in walked if w != h):
            return None
        path = follow_path(h, order, left, hexes, edges, terrain, cfg.assumed_cost)
        if not path or path[0] in walked:
            return None   # no known way, or it would head straight back down the party's own route
        path = [x for x in path if x not in walked]   # ground the party already crossed teaches nothing new
        if len(path) < MIN_FOLLOW_HEXES:
            return None   # a token follow tacked onto a spent route isn't worth a priority slot
        # only the stretch no other follow order already runs along counts: two follows down the same
        # corridor (a river and the mountains beside it) learn nothing extra
        gain = FOLLOW_DISCOUNT * sum(value(x, "entered") for x in path if x not in followed)
        if gain <= 0.5:
            return None   # only worth it if it reaches new ground
        # a known river is the surest feature to follow: it's there, and it carries on. A follow is worth most
        # started early, with most of the MP still to spend along the feature, not after a long walk to reach it.
        bonus = (FOLLOW_BONUS + (RIVER_FOLLOW_BONUS if order[:2] == "FR" else 0)) * left / cfg.mover_mp["scout"]
        return gain + bonus

    sim = dict(mover_mp=cfg.mover_mp, assumed_cost=cfg.assumed_cost)
    added, follow_used, follow_heads = [], set(), set()   # follow_heads: (hex, first step) taken

    def mark(orders, follow):
        """Record what a chosen route covers, so later parties plan around it."""
        route = simulate(start, orders, hexes, edges, terrain, **sim)
        covered.update(route.entered)
        if route.glimpse:
            looked.add(route.glimpse)
        if follow:
            # the follow path is only a projection: other parties may still go there, it's just worth less
            followed.update(follow_path(route.end, orders[-1], route.mp_left, hexes, edges, terrain,
                                        cfg.assumed_cost, sure=True) or [])
            follow_used.add((route.end, orders[-1]))
            heads = follow_hint(route.end, orders[-1], hexes, edges)[2]
            if heads:
                follow_heads.add((route.end, heads[0]))

    def take(party, orders, score, follow):
        add(conn, turn, unit, "scout", orders, party=party, note="auto-planned", variant=variant)
        mark(orders, follow)
        added.append({"party": party, "orders": ",".join(orders), "score": round(score, 2), "follow": follow})

    def best_follow():
        # a follow party doesn't walk down another follow's projected path to reach its own feature: that
        # stretch is already expected to be covered (plain routes may still use it, at reduced value)
        return best_route(start, hexes, edges, terrain, value, follow_value=follow_value, require_follow=True,
                          follow_used=follow_used, avoid=frozenset(covered | followed), shared=frozenset(covered),
                          outward=True, **sim)

    # 1. follow orders, while a reachable feature still leads somewhere new
    base = (set(covered), set(looked), set(followed), set(follow_used), set(follow_heads))
    follows = []   # [(score, orders)]
    while len(follows) < min(MAX_FOLLOW_PARTIES, len(free)):
        score, orders = best_follow()
        if not orders or score <= 0:
            break
        follows.append((score, orders))
        mark(orders, True)

    # A follow chosen early didn't know where the later ones would go (two follows can run down the same
    # corridor, e.g. a river and the mountains beside it). Re-plan each with all the others in place; a party
    # whose follow is no longer worth it goes back to plain scouting.
    def reset(keep):
        for live, saved in zip((covered, looked, followed, follow_used, follow_heads), base):
            live.clear()
            live.update(saved)
        for _, orders in keep:
            mark(orders, True)

    for k in range(len(follows)):
        others = [f for j, f in enumerate(follows) if j != k and f is not None]
        reset(others)
        score, orders = best_follow()
        follows[k] = (score, orders) if orders and score > 0 else None
    follows = [f for f in follows if f]
    reset([])
    queue = list(free)
    for score, orders in follows:
        take(queue.pop(0), orders, score, True)

    # 2. the remaining parties split the ground still worth scouting into wedges of equal value, and each
    # sweeps its own, outwards: routes can't cross, and no open direction is left out.
    # (only ground not fully known, and not on a follow's projected path, shapes the wedges)
    wedges = value_wedges(start, len(queue), lambda h, how: 0.0 if h in followed else value(h, how),
                          min_value=REFRESH_MAX)
    # passes, in order: own wedge, anywhere without crossing (each only if it learns more than a refresh of old
    # ground would), then refreshing old ground without crossing, and crossing only as a last resort
    # Plain parties keep out of follow orders' projected paths too, unless that's all that's left.
    def best_plain(wedge):
        clear = frozenset(covered | followed)   # as it stands now, with the parties planned so far
        passes = ((True, clear, REFRESH_MAX), (False, clear, REFRESH_MAX), (False, clear, 0.0),
                  (False, frozenset(covered), 0.0), (False, frozenset(), 0.0))
        score, orders = 0, None
        for in_wedge, avoid, floor in passes:
            score, orders = best_route(start, hexes, edges, terrain, value, avoid=avoid, shared=frozenset(covered),
                                       outward=True, allowed=wedge if in_wedge else None, **sim)
            if orders and score > floor:
                break
        return score, orders

    def entered(orders):
        return simulate(start, orders, hexes, edges, terrain, **sim).entered

    def worth(orders):
        """What a route gains with everything else currently planned in place."""
        r = simulate(start, orders, hexes, edges, terrain, **sim)
        return sum(value(h, "entered") for h in r.entered) + (value(r.glimpse, "glimpsed") if r.glimpse else 0)

    before_plain = (set(covered), set(looked), set(followed))
    plain = []   # [[party, score, orders]]
    for i, party in enumerate(queue):
        score, orders = best_plain(wedges[i])
        if not orders or score <= 0:
            continue   # nothing worth scouting for this party
        plain.append([party, score, orders])
        mark(orders, False)

    # A party planned early can end up adding nothing: a later one walks through all its hexes and carries on.
    # Re-plan such a party with all the others in place (it may now trail them out and branch off).
    def place(skip):
        for live, saved in zip((covered, looked, followed), before_plain):
            live.clear()
            live.update(saved)
        for k, (_, _, orders) in enumerate(plain):
            if k != skip:
                mark(orders, False)

    # Likewise a party that stops with MP to spare and nothing to look into (boxed in by its wedge, or by routes
    # planned after it): with everyone else in place it may find a longer route.
    def stops_short(orders):
        r = simulate(start, orders, hexes, edges, terrain, **sim)
        return not r.glimpse and not r.follow and r.mp_left >= cfg.assumed_cost

    for k in range(len(plain)):
        others = before_plain[0].union(*(entered(o) for j, (_, _, o) in enumerate(plain) if j != k))
        if not set(entered(plain[k][2])) <= others and not stops_short(plain[k][2]):
            continue
        place(k)
        score, orders = best_plain(None)
        if orders and worth(orders) > worth(plain[k][2]):
            plain[k][1:] = [score, orders]
    # A party still boxed in (every way on is another party's route) may cross one to reach new ground beyond:
    # better one shared hex than MP left unused. It never turns back over its own route, and the extension must be
    # worth at least about one new hex this far out (the spiral weighting discounts distant hexes).
    for k in range(len(plain)):
        orders = plain[k][2]
        if not stops_short(orders) or len(orders) >= MAX_ORDERS["scout"]:
            continue
        place(k)
        r = simulate(start, orders, hexes, edges, terrain, **sim)
        ext_score, ext = best_route(r.end, hexes, edges, terrain, value, mp=r.mp_left,
                                    max_orders=MAX_ORDERS["scout"] - len(orders),
                                    avoid=frozenset(r.entered) | {start}, shared=frozenset(), strict=True, **sim)
        if ext and ext_score >= 0.35:
            plain[k][1:] = [plain[k][1] + ext_score, orders + ext]
    # An extension can cross a party planned earlier, when that ground was still free: re-plan any party that now
    # shares hexes with another (beyond the start's six neighbours) with everyone else in place, keeping the new
    # route only if it gains more.
    ring1 = set(coords.neighbours(start))
    for k in range(len(plain)):
        mine = set(entered(plain[k][2])) - ring1
        if not any(mine & set(entered(o)) for j, (_, _, o) in enumerate(plain) if j != k):
            continue
        place(k)
        score, orders = best_plain(None)
        if orders and worth(orders) > worth(plain[k][2]):
            plain[k][1:] = [score, orders]
    place(None)
    for party, score, orders in plain:
        # a straight run to the end becomes one to-limit order (the map still draws the projected line)
        others = set(covered) - set(simulate(start, orders, hexes, edges, terrain, **sim).entered)
        orders = with_to_limit(orders, start, hexes, edges, terrain, sim, frozenset(others))
        add(conn, turn, unit, "scout", orders, party=party, note="auto-planned", variant=variant)
        added.append({"party": party, "orders": ",".join(orders), "score": round(score, 2), "follow": False})
    return {"added": added, "look_ins": fix_look_ins(conn, terrain, cfg, turn, as_of, variant)}


def fix_look_ins(conn, terrain, cfg, turn, as_of=None, variant=MAIN):
    """Re-aim wasted look-ins on auto-planned routes.

    A route that runs out of MP glimpses one more hex (its "look-in"). When another party now enters that
    hex, or also looks into it, the look-in tells us nothing new: the route's last order is changed to look
    into a different never-seen hex that no other party enters or looks into, or dropped if there's none.
    Routes the user drew by hand are left alone. Returns [{unit, party, old, new}].
    """
    planned, _ = evaluate(conn, terrain, cfg, turn, as_of, variant=variant)
    hexes, edges = knowledge(conn, as_of)
    scouts = [p for p in planned if p.row["kind"] == "scout" and p.route]
    sim = dict(mover_mp=cfg.mover_mp, assumed_cost=cfg.assumed_cost)
    changed = []
    for p in scouts:
        r = p.route
        if p.row.get("note") != "auto-planned" or not r.glimpse or r.follow or _orders(p.row)[-1] in LIMIT:
            continue
        others = [q.route for q in scouts if q is not p]
        entered = {h for o in others for h in o.entered}
        looked = {o.glimpse for o in others if o.glimpse}
        if r.glimpse not in entered and r.glimpse not in looked:
            continue
        base = _orders(p.row)[:-1]
        best = None
        for d in ("N", "NE", "SE", "S", "SW", "NW"):
            alt = simulate(r.start, base + [d], hexes, edges, terrain, **sim)
            g = alt.glimpse
            if (g and g not in entered and g not in looked and g not in alt.entered
                    and hex_status(hexes.get(g), None) == "new"):
                best = base + [d]
                break
        new = best or base
        if new and new != _orders(p.row):
            add(conn, turn, p.row["unit"], "scout", new, party=p.row["party"], scouts=p.row["scouts"] or 2,
                horses=p.row["horses"] or 2, mission=p.row["mission"] or "PATROL", start=p.row["start_hex"],
                mover=p.row["mover"], note="auto-planned", variant=variant)
            changed.append({"unit": p.row["unit"], "party": p.row["party"], "old": p.row["orders"],
                            "new": ",".join(new)})
    return changed


def archive_and_clear(conn, cfg):
    """Called when a report for a new turn is imported: the plans were for the turn just played, so they're
    archived as JSON (plan-archive/ in the data folder, restorable with `tn plan load`) and removed.
    Returns [(turn, variant, json path), ...]."""
    archive = cfg.data_dir / "plan-archive"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = []
    for turn, variant in variants(conn):
        rows = routes(conn, turn, variant)
        archive.mkdir(parents=True, exist_ok=True)
        path = archive / f"{turn}_{re.sub(r'[^A-Za-z0-9-]+', '_', variant)}_{stamp}.json"
        path.write_text(json.dumps([{k: r.get(k) for k in FIELDS} for r in rows], indent=1), encoding="utf-8")
        out.append((turn, variant, path))
    conn.execute("DELETE FROM plan_route")
    conn.execute("DELETE FROM plan")
    conn.commit()
    return out


def next_turn(turn):
    """'906-05' -> '906-06'; '906-12' -> '907-01'."""
    y, m = turn_key(turn)
    return f"{y + 1}-01" if m == 12 else f"{y}-{m + 1:02d}"


def from_report(conn, turn, variant=MAIN):
    """Turn what our units actually did in a report into a plan for that turn: each unit's move, and every
    scout party's route (with the step it couldn't afford, so the look-in shows too). Returns the routes added."""
    turn = check_turn(turn)
    register(conn, turn, variant, note=f"what happened, from the {turn} report")
    rows = conn.execute("SELECT p.unit, p.party, p.dir, t.prev_gx, t.prev_gy FROM unit_path p"
                        " JOIN unit_turn t ON t.unit = p.unit AND t.turn = p.turn"
                        " WHERE p.turn = ? ORDER BY p.unit, p.party = 'move' DESC, p.party, p.step",
                        (turn,)).fetchall()
    paths = {}
    for unit, party, d, px, py in rows:
        paths.setdefault((unit, party), {"dirs": [], "prev": (px, py) if px is not None else None})["dirs"].append(d)
    n, note = 0, f"from the {turn} report"
    for (unit, party), p in paths.items():   # moves come first: adding a move drops the unit's scouts
        try:
            if party == "move":
                add(conn, turn, unit, "move", p["dirs"], start=coords.label(p["prev"]) if p["prev"] else None,
                    note=note, variant=variant)
            elif party.startswith("scout"):
                add(conn, turn, unit, "scout", p["dirs"], party=int(party[5:]), note=note, variant=variant)
            else:
                continue
            n += 1
        except ValueError:
            continue   # e.g. more steps than the orders sheet allows: leave that one out
    return n


def start_new_turn(conn, cfg, turn):
    """A report for a new turn arrived: archive and clear the old plans, turn the report's movements into a
    plan for that turn, and open an empty plan for the next one. Returns (archived, routes added, next turn)."""
    archived = archive_and_clear(conn, cfg)
    added = from_report(conn, turn)
    upcoming = next_turn(turn)
    register(conn, upcoming)
    conn.commit()
    return archived, added, upcoming
