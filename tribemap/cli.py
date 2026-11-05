"""The `tn` command. Every command accepts --json for machine-readable output.

Examples:
    tn import "906-5/0999_906_05_Results.docx"
    tn where 1999 --history
    tn hex "QB 1017"
    tn unscouted --near 0999 --radius 3
    tn render --out map.png --layers terrain,units,fog
"""
import argparse
import json
import sys
from pathlib import Path

from . import coords, db, importer, plans, queries
from .config import load
from .planner import parse_orders, simulate
from .terrain import TerrainTable


def emit(args, data, text):
    if args.json:
        print(json.dumps(data, indent=2, default=str))
    else:
        print(text)


def cmd_import(args, cfg, conn, terrain):
    results = []
    for path in args.files:
        latest = queries.latest_turn(conn)
        sid, rep, skipped = importer.import_report(conn, cfg, terrain, path, force=args.force)
        if rep and queries.turn_key(rep.turn) > queries.turn_key(latest):
            # a new turn's results: the plans were for the turn just played, so archive and clear them
            archived, added, upcoming = plans.start_new_turn(conn, cfg, rep.turn)
            for turn, variant, js in archived:
                print(f"Plan {turn}{'' if variant == plans.MAIN else ' (' + variant + ')'} archived to {js} "
                      "and removed", file=sys.stderr)
            print(f"Plan {rep.turn} now shows what our units did ({added} routes); plan {upcoming} is ready, empty",
                  file=sys.stderr)
        if skipped:
            results.append({"file": path, "skipped": "already imported (use --force to re-import)"})
            continue
        mapish = [u for u in rep.unparsed if u[3]]
        results.append({"file": path, "source_id": sid, "turn": rep.turn,
                        "units": [u.unit for u in rep.units], "observations": len(rep.obs),
                        "unparsed_mapish": [{"line": n, "text": t, "reason": r} for n, t, r, _ in mapish],
                        "unparsed_other": sum(1 for u in rep.unparsed if not u[3])})
    lines = []
    for r in results:
        if "skipped" in r:
            lines.append(f"{r['file']}: {r['skipped']}")
            continue
        lines.append(f"{r['file']}: turn {r['turn']}, units {', '.join(r['units'])}, "
                     f"{r['observations']} observations")
        for u in r["unparsed_mapish"]:
            lines.append(f"  UNPARSED line {u['line']}: {u['reason']}: {u['text']}")
        if r["unparsed_other"]:
            lines.append(f"  ({r['unparsed_other']} other lines not recognised; `tn unparsed --all`)")
    emit(args, results, "\n".join(lines))
    return True


def cmd_rebuild(args, cfg, conn, terrain):
    results, n = importer.rebuild(conn, cfg, terrain)
    data = {"reports": [{"source_id": s, "turn": r.turn if r else None} for s, r, _ in results],
            "corrections": n}
    emit(args, data, f"Rebuilt from {len(results)} report(s) and {n} correction(s).")
    return True


def cmd_where(args, cfg, conn, terrain):
    data = queries.where(conn, args.unit, history=args.history)
    if data is None:
        emit(args, None, f"No position known for {args.unit}.")
    elif args.history:
        emit(args, data, "\n".join(f"{r['turn']}: {r['hex']}  (from {r['previous_hex'] or '-'})"
                                   for r in data))
    else:
        emit(args, data, f"{data['unit']} ({data['kind']}{', ' + data['name'] if data['name'] else ''}) "
                         f"is at {data['hex']} as of {data['turn']}. Status: {data['status']}")


def cmd_hex(args, cfg, conn, terrain):
    h = queries.resolve_place(conn, args.hex)
    d = queries.hex_detail(conn, h, args.as_of)
    if d["terrain"] and not d["verified"]:
        state = (f"UNVERIFIED {d['terrain_how']}, from {d['terrain_provider'] or 'unknown'}, "
                 f"as of {d['terrain_as_of'] or 'unknown turn'}")
    else:
        state = d["terrain_how"] or "never seen"
    level = {"full": "FULLY SCOUTED: terrain + resources", "terrain": "TERRAIN ONLY: needs scouting",
             "reported": "needs scouting to verify", "none": "needs scouting"}[d["knowledge"]]
    lines = [f"{d['hex']}: {d['terrain'] or 'unknown'} ({state}; {level})"]
    if d["last_known"] or d["unverified"]:
        lines.append(f"  last known {d['last_known'] or 'unknown'}; ours: first seen {d['first_seen'] or '-'}, "
                     f"last seen {d['last_seen'] or '-'}, last scouted {d['last_scouted'] or 'never'}")
    for key in ("resources", "specials", "items"):
        if d[key]:
            lines.append(f"  {key}: {', '.join(d[key])}")
    if d["sightings"]:
        lines.append("  units seen: " + ", ".join(f"{s['unit']} ({s['turn']})" for s in d["sightings"]))
    if d["hexsides"]:
        lines.append("  hexsides: " + "; ".join(f"{k} {'/'.join(v)}" for k, v in d["hexsides"].items()))
    if d["units_here"]:
        lines.append("  our units here: " + ", ".join(d["units_here"]))
    if d["conflicts"]:
        lines.append("  CONFLICT: also reported as " + ", ".join(d["conflicts"]))
    if d["unverified"]:
        lines.append("  unverified (from others):")
        for u in d["unverified"]:
            lines.append(f"    {u['kind']} {u['value']} from {u['provider'] or 'unknown'}, "
                         f"as of {u['as_of'] or 'unknown turn'}, received {u['received'] or '?'}"
                         f"{' (' + u['note'] + ')' if u['note'] else ''}")
    lines.append("  history:")
    for o in d["history"]:
        who = o["unit"] or (o["provider"] and f"{o['source_kind']}:{o['provider']}") or o["source_kind"]
        conditions = ", ".join(x for x in (o["season"], o["weather"]) if x)
        lines.append(f"    {o['turn'] or '-'} {who} {o['party']}: {o['kind']} "
                     f"{o['value']} ({o['how']}, {o['confidence']}){'  [' + conditions + ']' if conditions else ''}")
    emit(args, d, "\n".join(lines))


def cmd_unscouted(args, cfg, conn, terrain):
    centre = queries.resolve_place(conn, args.near)
    rows = queries.unscouted(conn, centre, args.radius, args.stale, args.as_of)
    text = [f"{len(rows)} hex(es) within {args.radius} of {coords.label(centre)} need scouting:"]
    text += [f"  {r['hex']}  d={r['distance']}  {r['known_as']}{' ' + r['terrain'] if r['terrain'] else ''}"
             f"{'  last scouted ' + r['last_scouted'] if r['last_scouted'] else ''}" for r in rows]
    emit(args, rows, "\n".join(text))


def cmd_mover(args, cfg, conn, terrain):
    """How units usually move (units.csv): moves with no --mover of their own are simulated this way."""
    if args.unit and args.mover:
        movers = plans.set_unit_mover(cfg, args.unit, args.mover)
    elif args.unit:
        raise SystemExit("give the mover too: tn mover <unit> mounted|foot")
    else:
        movers = plans.unit_movers(cfg)
    emit(args, movers, "\n".join(f"{u} {m}" for u, m in sorted(movers.items())) or "every unit moves on foot")


def cmd_contact(args, cfg, conn, terrain):
    """The address book of other players (contacts.csv, with the game data) and where their units are."""
    import csv
    book = queries.contacts(cfg)
    if args.action == "list":
        here = {}
        for p in queries.people(conn, cfg):
            here.setdefault(p["clan"], []).append(p)
        rows = [{"clan": c, **v, "positions": [{"unit": p["unit"], "hex": coords.label((p["gx"], p["gy"])),
                                                 "turn": p["turn"], "verified": p["verified"], "approx": p["approx"]}
                                                for p in here.get(c, [])]}
                for c, v in sorted(book.items())]
        lines = []
        for r in rows:
            where = "; ".join(f"{p['unit']} {'somewhere in ' + p['hex'][:2] if p['approx'] else 'at ' + p['hex']}"
                              f" ({p['turn'] or 'turn unknown'}"
                              f"{'' if p['verified'] else ', their word'})" for p in r["positions"]) or "position unknown"
            reach = ", ".join(x for x in (r["email"], r["discord"] and "Discord " + r["discord"]) if x)
            truce = " [truce]" if r.get("truce", "").lower() in ("yes", "y", "true", "1") else ""
            lines.append(f"{r['clan']} {r['name']}{truce}{' (' + r['relation'] + ')' if r['relation'] else ''}: {where}"
                         f"{'  [' + reach + ']' if reach else ''}")
        emit(args, rows, "\n".join(lines) or "No contacts yet: tn contact add <clan> <name>")
        return False
    args.name = args.hex = args.name_or_hex
    clan = (args.clan or "").strip().zfill(4)
    if not (clan.isdigit() and len(clan) == 4):
        sys.exit("give the clan as its number, e.g. 0123")
    if args.action == "add":
        row = {"clan": clan, "name": args.name or "", "email": args.email or "", "discord": args.discord or "",
               "relation": args.relation or "", "truce": {True: "yes", False: "no", None: ""}[args.truce],
               "note": args.note or ""}
        old = book.get(clan, {})
        for k in ("name", "email", "discord", "relation", "truce", "note"):   # updating: keep what isn't given again
            row[k] = row[k] or old.get(k, "")
        book[clan] = {k: v for k, v in row.items() if k != "clan"}
        with open(cfg.contacts_csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, queries.CONTACT_FIELDS)
            w.writeheader()
            for c, v in sorted(book.items()):
                w.writerow({"clan": c, **v})
        emit(args, {"ok": True}, f"{'Updated' if old else 'Added'} {clan} {row['name']}.")
        return False
    # at: where one of their units is, in their words (or ours with --seen), recorded like any player info.
    # A sheet alone ("DO": all we know is the map sheet) goes in the sheet's middle hex, marked approximate.
    unit = args.unit or clan
    approx = len(args.hex.strip()) == 2
    if approx:
        sheet = args.hex.strip().upper()
        args.hex = coords.sheet_centre(sheet)
        args.note = args.note or f"somewhere on sheet {sheet}"
    who = args.provider or book.get(clan, {}).get("name") or f"Clan {clan}"
    if not args.turn:
        print("note: no --turn given, so how old this position is will be unknown", file=sys.stderr)
    importer.add_correction(conn, cfg, hex=args.hex, kind="unit_sighting", value=unit,
                            source_kind="manual" if args.seen else "player", provider=None if args.seen else who,
                            confidence="guess" if approx else "confirmed", turn=args.turn, note=args.note)
    emit(args, {"ok": True}, f"Recorded {unit} at {args.hex}{' (approximate: ' + args.note + ')' if approx else ''}"
                             f"{' as of ' + args.turn if args.turn else ''}"
                             f" ({'seen by us' if args.seen else 'from ' + who})." )
    return True


def cmd_fix(args, cfg, conn, terrain):
    if args.source == "player" and not args.provider:
        sys.exit("--source player needs --provider (who told us)")
    if args.source in ("player", "guess") and not args.turn:
        print("note: no --turn given, so this information's age is recorded as unknown", file=sys.stderr)
    value = args.value
    if args.kind == "terrain":
        code = terrain.code(value)
        if not code:
            sys.exit(f"unknown terrain {value!r}; add it to {cfg.terrain_csv} first")
        value = code
    importer.add_correction(conn, cfg, hex=args.hex, kind=args.kind, value=value, dir=args.dir,
                            source_kind=args.source, provider=args.provider, confidence=args.confidence,
                            turn=args.turn, note=args.note)
    emit(args, {"ok": True}, f"Recorded {args.kind} {value} at {args.hex} ({args.source}).")
    return True


def cmd_render(args, cfg, conn, terrain):
    from .render import ALL_LAYERS, render
    planned, overlap = None, ()
    if args.plan:
        planned, cov = plans.evaluate(conn, terrain, cfg, args.plan, args.turn, variant=args.variant)
        if not planned:
            sys.exit(f"no plan for {args.plan}")
        overlap = cov["scout_overlap"]
    default = tuple(l for l in ALL_LAYERS if l != "paths") if planned else ALL_LAYERS
    layers = tuple(args.layers.split(",")) if args.layers else default
    around = queries.resolve_place(conn, args.around) if args.around else None
    render(conn, terrain, args.out, as_of=args.turn, layers=layers, around=around, radius=args.radius,
           title=args.title, clan=cfg.clan, plan=planned, plan_turn=args.plan, overlap=overlap,
           focus=args.focus)
    emit(args, {"out": args.out}, f"Wrote {args.out}")


# --- planning -------------------------------------------------------------------

def route_lines(r):
    lines = []
    for s in r.steps:
        where = f"{coords.label(s.hex)} {s.terrain or '?':4}"
        if s.result == "ok":
            cost = f"{s.cost}{'*' if s.assumed else ''} MP, {s.mp_left} left"
        elif s.result == "mp_block":
            cost = f"needs {s.cost}{'*' if s.assumed else ''}, has {s.mp_left}: STOP, terrain glimpsed"
        elif s.result == "river_block":
            cost = "STOP: river, no known ford"
        else:
            cost = "STOP: impassable, terrain seen"
        lines.append(f"    {s.n:2} {s.order:4} {where} {cost}")
    if r.follow:
        lines.append(f"    -> {r.follow} from {coords.label(r.end)} with {r.mp_left} MP (not simulated)")
    if r.unused:
        lines.append(f"    orders never reached: {','.join(r.unused)}")
    return lines


def cmd_route(args, cfg, conn, terrain):
    start = queries.resolve_place(conn, getattr(args, "from"))
    hexes, edges = queries.knowledge(conn, args.as_of)
    r = simulate(start, parse_orders(args.orders), hexes, edges, terrain, args.mover, mp=args.mp,
                 mover_mp=cfg.mover_mp, assumed_cost=cfg.assumed_cost)
    text = [f"{args.mover} ({r.mp} MP) from {coords.label(start)}: ends {coords.label(r.end)}, "
            f"{r.mp_left} MP left{'; ' + r.stop if r.stop else ''}"] + route_lines(r)
    if r.assumed:
        text.append(f"    * cost assumed ({cfg.assumed_cost} MP): terrain unknown or no cost in terrain.csv")
    emit(args, r.as_dict(), "\n".join(text))


def cmd_plan_add(args, cfg, conn, terrain):
    before = plans.scout_count(conn, args.turn, args.unit, args.variant)
    party = plans.add(conn, args.turn, args.unit, args.kind, args.orders, variant=args.variant,
                      party=args.party, scouts=args.scouts,
                      horses=args.horses, mission=args.mission, start=getattr(args, "from"), mover=args.mover,
                      note=args.note)
    what = f"{args.unit} move" if args.kind == "move" else f"{args.unit} scout party {party}"
    dropped = (before - plans.scout_count(conn, args.turn, args.unit, args.variant)) if args.kind == "move" else 0
    note = f" (removed {dropped} scout route(s): they left from the old move's end)" if dropped else ""
    emit(args, {"ok": True, "party": party, "dropped_scouts": dropped},
         f"Planned {what} for {args.turn}: {args.orders}{note}")
    return True


def cmd_plan_auto(args, cfg, conn, terrain):
    result = plans.auto_scout(conn, terrain, cfg, args.turn, args.unit, parties=args.parties,
                              variant=args.variant)
    added = result["added"]
    lines = [f"Auto-planned {len(added)} scout part{'y' if len(added) == 1 else 'ies'} for {args.unit}:"]
    lines += [f"  party {a['party']}: {a['orders']}  (score {a['score']})" for a in added]
    lines += [f"  look-in moved: {f['unit']} party {f['party']} {f['old']} -> {f['new']}" for f in result["look_ins"]]
    lines.append("Scores: 1 per new hex entered, 0.6 per terrain-only/unverified/stale hex, 0.4 per new glimpse. "
                 "Unknown terrain is assumed to cost "
                 f"{cfg.assumed_cost} MP, so real routes may fall short. Edit any of them as usual.")
    emit(args, result, "\n".join(lines))
    return True


def cmd_plan_rm(args, cfg, conn, terrain):
    n = plans.remove(conn, args.turn, args.unit, args.kind, args.party, variant=args.variant)
    emit(args, {"removed": n}, f"Removed {n} route(s).")
    return True


def cmd_plan_show(args, cfg, conn, terrain):
    planned, cov = plans.evaluate(conn, terrain, cfg, args.turn, args.as_of, variant=args.variant)
    data = {"turn": args.turn, "routes": [], "coverage": {k: v for k, v in cov.items() if not k.startswith("_")}}
    name = "" if args.variant == plans.MAIN else f" ({args.variant})"
    text = [f"Plan {args.turn}{name} (map from our latest report {queries.latest_turn(conn)})"]
    # moves first, then scouting grouped by tribe (an element's parties count against its parent tribe's 8)
    per_tribe = {}
    for p in planned:
        if p.row["kind"] == "scout":
            per_tribe[p.row["unit"][:4]] = per_tribe.get(p.row["unit"][:4], 0) + 1
    group = None
    for p in planned:
        row = p.row
        g = "moves" if row["kind"] == "move" else row["unit"][:4]
        if g != group:
            group = g
            if g == "moves":
                text.append("Unit moves:")
            else:
                n = per_tribe[g]
                text.append(f"\n{g} scouting ({n}/8 parties{': TOO MANY' if n > 8 else ''}):")
        entry = {"route": p.name, "orders": row["orders"], "error": p.error,
                 "sim": p.route.as_dict() if p.route else None}
        data["routes"].append(entry)
        if p.error:
            text.append(f"  {p.name}: {row['orders']}  ERROR: {p.error}")
            continue
        r = p.route
        used = f"at least {r.known_cost}" if r.assumed else f"{r.mp_used}"
        text.append(f"  {p.name:14} {row['orders']:28} {coords.label(r.start)} -> {coords.label(r.end)} "
                    f"({used}/{r.mp} MP, {r.mover}){'  [' + r.notable_stop + ']' if r.notable_stop else ''}")
        if args.steps:
            text += route_lines(r)
    c = cov["counts"]
    text.append(f"\nCoverage: {cov['entered']} hexes entered: " + ", ".join(f"{v} {k}" for k, v in c.items())
                + f"; {len(cov['new_glimpses'])} new glimpse(s)")
    text.append("  new = never seen; terrain_only = terrain known, resources never checked; "
                "verify = only other players' word; stale = scouted 12+ turns ago; known = fully scouted")
    if cov["scout_overlap"]:
        text.append("Scout overlap: " + "; ".join(f"{h} ({', '.join(n)})" for h, n in cov["scout_overlap"].items()))
    if cov["shared_first_steps"]:
        text.append(f"({cov['shared_first_steps']} first step(s) next to a start hex are shared by several parties; "
                    "unavoidable with 8 parties, so not counted as overlap)")
    emit(args, data, "\n".join(text))


def cmd_plan_export(args, cfg, conn, terrain):
    xlsx, js, tsv = plans.export(conn, cfg, args.turn, args.out, variant=args.variant)
    text = [f"Wrote {xlsx}", f"Wrote {js}"]
    if args.tsv:
        for sheet, body in tsv.items():
            if body:
                text += [f"--- {sheet} (paste at B2) ---", body]
    emit(args, {"xlsx": str(xlsx), "json": str(js), "tsv": tsv}, "\n".join(text))


def cmd_plan_load(args, cfg, conn, terrain):
    n = plans.load(conn, args.file)
    emit(args, {"loaded": n}, f"Loaded {n} route(s) from {args.file}")
    return True


def cmd_unparsed(args, cfg, conn, terrain):
    q = ("SELECT s.turn, s.filename, u.line_no, u.text, u.reason, u.mapish FROM unparsed u"
         " JOIN source s ON s.id = u.source_id")
    if not args.all:
        q += " WHERE u.mapish = 1"
    rows = [dict(r) for r in conn.execute(q + " ORDER BY s.id, u.line_no")]
    emit(args, rows, "\n".join(f"{r['turn']} line {r['line_no']}: {r['reason']}: {r['text'][:120]}"
                               for r in rows) or "Nothing unparsed.")


def cmd_summary(args, cfg, conn, terrain):
    hexes, edges = queries.knowledge(conn)
    data = {
        "db": str(cfg.db_path), "data_dir": str(cfg.data_dir), "latest_turn": queries.latest_turn(conn),
        "sources": [dict(r) for r in conn.execute(
            "SELECT id, kind, turn, filename, provider FROM source ORDER BY id")],
        "hexes_known": len(hexes), "hexes_scouted": sum(1 for i in hexes.values() if i.scouted),
        "hexsides_known": len(edges),
        "units": {u: r["hex_text"] for u, r in queries.unit_positions(conn).items()},
        "conflicts": [i.label for i in hexes.values() if i.conflicts],
        "hexes_unverified_only": sum(1 for i in hexes.values() if not i.verified),
        "hexes_with_unverified_facts": sum(1 for i in hexes.values() if i.unverified),
        "plans": conn.execute("SELECT COUNT(*) FROM plan_route").fetchone()[0],
    }
    text = [f"DB {data['db']}", f"Latest turn {data['latest_turn']}; {data['hexes_known']} hexes known, "
            f"{data['hexes_scouted']} scouted, {data['hexsides_known']} hexsides; "
            f"{data['hexes_with_unverified_facts']} with unverified info from others "
            f"({data['hexes_unverified_only']} known only from others)",
            "Units: " + ", ".join(f"{u} @ {h}" for u, h in data["units"].items())]
    text += [f"  source {s['id']}: {s['kind']} {s['turn'] or ''} {s['filename'] or ''}" for s in data["sources"]]
    if data["conflicts"]:
        text.append("Conflicts: " + ", ".join(data["conflicts"]))
    emit(args, data, "\n".join(text))


def cmd_sql(args, cfg, conn, terrain):
    ro = db.connect(cfg.db_path, readonly=True)
    try:
        rows = [dict(r) for r in ro.execute(args.query)]
    finally:
        ro.close()
    emit(args, rows, "\n".join(" | ".join(str(v) for v in r.values()) for r in rows) or "(no rows)")


def cmd_serve(args, cfg, conn, terrain):
    import threading
    import webbrowser

    import uvicorn

    from .web import create_app
    url = f"http://{args.host}:{args.port}/"
    print(f"Tribemap at {url}  (Ctrl+C to stop)")
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, [url]).start()
    uvicorn.run(create_app(cfg), host=args.host, port=args.port, log_level="warning")


def cmd_project(args, cfg, conn, terrain):
    """Save everything as one project .zip (for the browser version, or as a backup)."""
    from . import project
    out = Path(args.out or f"tribemap-{cfg.clan}-{queries.latest_turn(conn) or 'empty'}.zip")
    out.write_bytes(project.export(cfg))
    emit(args, {"project": str(out.resolve())}, f"Saved the project to {out.resolve()}")


def cmd_backup(args, cfg, conn, terrain):
    path = db.backup(conn, cfg.backup_dir, cfg.keep_backups)
    emit(args, {"backup": str(path)}, f"Backed up to {path}")


def main(argv=None):
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="machine-readable output")
    p = argparse.ArgumentParser(prog="tn", description="TribeNet map database and planner")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help):
        s = sub.add_parser(name, parents=[common], help=help)
        s.set_defaults(fn=fn)
        return s

    s = add("import", cmd_import, "import turn report(s) (.docx or .txt)")
    s.add_argument("files", nargs="+")
    s.add_argument("--force", action="store_true", help="re-import a file already imported")
    add("rebuild", cmd_rebuild, "rebuild the DB from all reports + corrections.csv (keeps plans)")
    s = add("where", cmd_where, "where is a unit")
    s.add_argument("unit")
    s.add_argument("--history", action="store_true")
    s = add("hex", cmd_hex, "everything known about a hex (label or unit id)")
    s.add_argument("hex")
    s.add_argument("--as-of", dest="as_of")
    s = add("unscouted", cmd_unscouted, "hexes near a place that need scouting")
    s.add_argument("--near", required=True, help="hex label or unit id")
    s.add_argument("--radius", type=int, default=3)
    s.add_argument("--stale", type=int, help="also list hexes not scouted for this many turns")
    s.add_argument("--as-of", dest="as_of")
    s = add("contact", cmd_contact, "other players we know: the address book and where their units are")
    s.add_argument("action", choices=["add", "list", "at"],
                   help="add <clan> <name>: add or update a contact; list: everyone and where they are; "
                        "at <clan> <hex|sheet>: where one of their units is (a sheet alone, e.g. DO, is approximate)")
    s.add_argument("clan", nargs="?")
    s.add_argument("name_or_hex", nargs="?", metavar="name|hex")
    s.add_argument("--email")
    s.add_argument("--discord")
    s.add_argument("--relation", help="e.g. mentor, neighbour, truce offered")
    s.add_argument("--truce", action="store_true", default=None, help="with add: we have a declared truce (shown blue)")
    s.add_argument("--no-truce", dest="truce", action="store_false", help="with add: no truce (shown red)")
    s.add_argument("--unit", help="with at: which unit (default: the clan's main tribe)")
    s.add_argument("--turn", help="with at: the turn they were there")
    s.add_argument("--provider", help="with at: who told us (default: the contact)")
    s.add_argument("--seen", action="store_true", help="with at: our own sighting rather than their word")
    s.add_argument("--note")
    s = add("mover", cmd_mover, "how a unit usually moves (mounted or foot); no arguments lists them")
    s.add_argument("unit", nargs="?")
    s.add_argument("mover", nargs="?", choices=["mounted", "foot"])
    s = add("fix", cmd_fix, "record a manual fact (kept in corrections.csv)")
    s.add_argument("hex")
    s.add_argument("kind", choices=["terrain", "river", "ford", "no_ford", "resource", "special",
                                    "unit_sighting", "nothing_found"])
    s.add_argument("value")
    s.add_argument("--dir", help="hexside direction for river/ford/no_ford")
    s.add_argument("--source", default="manual", choices=["manual", "player", "gm", "guess"])
    s.add_argument("--provider", help="who it came from (for --source player)")
    s.add_argument("--confidence", default="confirmed", choices=["confirmed", "likely", "guess"])
    s.add_argument("--turn", help="the turn the information was true (for player info: when they saw it)")
    s.add_argument("--note")
    s = add("render", cmd_render, "draw the map (png/svg/pdf)")
    s.add_argument("--out", required=True)
    s.add_argument("--turn", help="map as of this turn (default: latest)")
    s.add_argument("--layers", help="comma list: terrain,icons,labels,resources,units,paths,scouting,fog,dates,sheets")
    s.add_argument("--around", help="centre on a hex label or unit id")
    s.add_argument("--radius", type=int, default=6)
    s.add_argument("--title")
    s.add_argument("--plan", help="draw this turn's plan on top (e.g. 906-06)")
    s.add_argument("--variant", "-V", default=plans.MAIN, help="with --plan: which of the turn's plans")
    s.add_argument("--focus", help="with --plan: show this tribe (and its elements) or element; fade the rest")

    s = add("route", cmd_route, "simulate one route over the known map")
    s.add_argument("--from", required=True, help="hex label or unit id")
    s.add_argument("--orders", required=True, help="e.g. N,NE,NE,NW or 'NL' or 'NW,FRR'")
    s.add_argument("--mover", default="scout", choices=["scout", "mounted", "foot"])
    s.add_argument("--mp", type=int, help="override the movement points")
    s.add_argument("--as-of", dest="as_of")

    sp = sub.add_parser("plan", help="plan a turn's moves and scouting").add_subparsers(dest="action",
                                                                                        required=True)

    def padd(name, fn, help):
        s = sp.add_parser(name, parents=[common], help=help)
        s.set_defaults(fn=fn)
        s.add_argument("--variant", "-V", default=plans.MAIN,
                       help="which of the turn's plans (default: main); e.g. B for an alternative")
        return s

    s = padd("add", cmd_plan_add, "add or replace a unit move or scout route")
    s.add_argument("turn")
    s.add_argument("unit")
    s.add_argument("kind", choices=["move", "scout"])
    s.add_argument("orders", help="e.g. N,NE,NE,NW")
    s.add_argument("--party", type=int, help="scout party 1-8 (default: next free)")
    s.add_argument("--from", help="start hex, for a unit with no known position yet")
    s.add_argument("--mover", choices=["scout", "mounted", "foot"],
                   help="default: foot for moves, scout for scouting")
    s.add_argument("--scouts", type=int, default=2)
    s.add_argument("--horses", type=int, default=2)
    s.add_argument("--mission", default="PATROL", choices=["PATROL", "LOCATE", "RAID", "SPY"])
    s.add_argument("--note")
    s = padd("auto", cmd_plan_auto, "fill a unit's free scout parties with the routes that learn the most")
    s.add_argument("turn")
    s.add_argument("unit")
    s.add_argument("--parties", type=int, help="at most this many (default: every free party)")
    s = padd("show", cmd_plan_show, "simulate the plan: routes, stops, coverage, overlap")
    s.add_argument("turn")
    s.add_argument("--steps", action="store_true", help="show every step")
    s.add_argument("--as-of", dest="as_of")
    s = padd("rm", cmd_plan_rm, "remove routes (all of a turn, or narrowed by unit/kind/party)")
    s.add_argument("turn")
    s.add_argument("unit", nargs="?")
    s.add_argument("--kind", choices=["move", "scout"])
    s.add_argument("--party", type=int)
    s = padd("export", cmd_plan_export, "write a paste-in workbook + JSON into the turn folder")
    s.add_argument("turn")
    s.add_argument("--out", help="folder (default: the turn folder, e.g. 906-6)")
    s.add_argument("--tsv", action="store_true", help="also print tab-separated rows for the clipboard")
    s = padd("load", cmd_plan_load, "restore a plan from its JSON export")
    s.add_argument("file")

    s = add("unparsed", cmd_unparsed, "report lines the parser could not place")
    s.add_argument("--all", action="store_true", help="include lines that don't look map-related")
    add("summary", cmd_summary, "what the DB holds")
    s = add("sql", cmd_sql, "read-only SQL query")
    s.add_argument("query")
    add("backup", cmd_backup, "snapshot the DB into the backups folder")
    s = add("project", cmd_project, "save everything as one project .zip (open it in the browser version)")
    s.add_argument("--out", help="the file (default tribemap-<clan>-<turn>.zip here)")
    s = add("serve", cmd_serve, "open the map in your browser (local only)")
    s.add_argument("--port", type=int, default=8507)
    s.add_argument("--host", default="127.0.0.1", help="keep 127.0.0.1 unless you mean to share it on your network")
    s.add_argument("--no-browser", action="store_true", help="don't open a browser window")

    args = p.parse_args(argv)
    cfg = load()
    terrain = TerrainTable(cfg.terrain_csv)
    conn = db.connect(cfg.db_path)
    try:
        changed = args.fn(args, cfg, conn, terrain)
        conn.commit()
        if changed:
            db.backup(conn, cfg.backup_dir, cfg.keep_backups)
    except ValueError as e:
        sys.exit(f"error: {e}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
