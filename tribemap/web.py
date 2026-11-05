"""The browser UI's server: a small read-only JSON API over the same queries the CLI uses, plus the page.

Run with `tn serve`; it listens on 127.0.0.1 only. Geometry is computed here (hex centres, river segments,
route points in SVG coordinates: x right, y down, hex radius 1) so the page only draws.

Endpoints:
    GET    /api/meta                 clan, turns with reports, turns with plans, terrain table
    GET    /api/map?as_of=T&plan=P   hexes, hexsides, our units, reported paths (plan widens the drawn area)
    GET    /api/hex/{label}?as_of=   everything known about one hex (as `tn hex --json`)
    GET    /api/plan/{turn}?as_of=   simulated routes + coverage (as `tn plan show --json`)
    POST   /api/plan/{turn}/preview  simulate with the editor's unsaved route (nothing is saved)
    POST   /api/plan/{turn}/route    save a route (like `tn plan add`); body may name the route it replaces
    DELETE /api/plan/{turn}/route    remove a route: ?unit=&kind=&party=
    POST   /api/plan/{turn}/autoscout  fill a unit's free scout parties with the best routes: {unit, parties?}
    POST   /api/plan/{turn}/export   write the paste-in workbook + JSON into the turn folder
    POST   /api/import               a turn report from the page: {filename, data (base64), dry_run?, force?}
    POST   /api/plan/{turn}/copy     new plan variant, optionally a copy of another: {to, from?}
    DELETE /api/plan/{turn}          delete a whole plan variant
    Every plan endpoint takes ?variant=NAME (default 'main') to choose which of the turn's plans.

Writes are only accepted as JSON from this page (same origin, local host), so other websites open in the
browser can't change the plan.
"""
import time
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import coords, db, importer, plans, project, queries, wxx
from .icons import ICONS
from .planner import FOLLOW, IMPASSABLE, simulate
from .render import side
from .terrain import TerrainTable

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

BACKUP_EVERY = 600  # seconds: browser edits come in bursts, so snapshot at most every 10 minutes

STATIC = Path(__file__).resolve().parent / "static"


def svg(pt):
    """Math coordinates (y up) -> SVG coordinates (y down), rounded."""
    return [round(pt[0], 3), round(-pt[1], 3)]


def centre(h):
    return svg(coords.centre(h))


def map_payload(conn, terrain, as_of=None, extra=(), pad=1, cfg=None):
    """extra: more hexes to include in the drawn area (e.g. everything a plan's routes touch).
    pad: how many rings of hexes to draw around everything (more while editing, to leave room to click)."""
    now = as_of or queries.latest_turn(conn)
    hexes, edges = queries.knowledge(conn, as_of)
    positions = queries.unit_positions(conn, as_of)
    # a hex known only from a player telling us their units are there isn't map ground (the People layer shows
    # them); it would only stretch the drawn area and Fit
    hexes = {h: i for h, i in hexes.items()
             if i.terrain or i.last_seen or any(u["kind"] != "unit_sighting" for u in i.unverified)}
    core = set(hexes) | {(r["gx"], r["gy"]) for r in positions.values()} | set(extra)
    view = set(core)
    for _ in range(max(0, min(pad, 8))):
        view |= {n for h in view for n in coords.neighbours(h) if n[0] >= 1 and n[1] >= 1}

    out = []
    for h in sorted(view):
        info = hexes.get(h)
        x, y = centre(h)
        d = {"id": coords.label(h), "x": x, "y": y, "terrain": None, "knowledge": "none"}
        if info:
            d.update(
                terrain=info.terrain, how=info.terrain_how, verified=info.verified, scouted=info.scouted,
                knowledge=info.knowledge,
                last_known=info.last_known, last_seen=info.last_seen, last_scouted=info.last_scouted,
                age=queries.turns_between(info.last_scouted, now) if info.last_scouted and now else None,
                fog=queries.fog_alpha(queries.turns_between(info.last_scouted, now))
                if info.last_scouted and now and info.terrain_how != "manual" else 0,
                notes=sorted(info.resources) + sorted(info.specials) + [u for u, _ in info.sightings],
                unverified_notes=[u["value"] for u in info.unverified
                                  if u["kind"] in ("resource", "special", "unit_sighting")],
                has_unverified=bool(info.unverified), conflicts=info.conflicts,
                specials=sorted(info.specials),
                unverified_specials=sorted({u["value"] for u in info.unverified if u["kind"] == "special"}),
                resources=sorted(info.resources),
                unverified_resources=sorted({u["value"] for u in info.unverified if u["kind"] == "resource"}))
        out.append(d)

    sides = []
    for (h, d), kinds in edges.items():
        a, b = side(h, d)
        sides.append({"hex": coords.label(h), "dir": d, "kinds": sorted(kinds),
                      "verified": any(kinds.values()), "a": svg(a), "b": svg(b)})

    borders = []
    for h, d in coords.sheet_borders(view):
        a, b = side(h, d)
        borders.append({"a": svg(a), "b": svg(b)})
    by_sheet = {}
    for h in view:
        by_sheet.setdefault(coords.sheet(h), []).append(centre(h))
    sheets = [{"name": name, "x": round(sum(p[0] for p in pts) / len(pts), 3),
               "y": round(sum(p[1] for p in pts) / len(pts), 3), "hexes": len(pts)}
              for name, pts in sorted(by_sheet.items())]

    units = [{"unit": u, "hex": r["hex_text"], "turn": r["turn"], "x": centre((r["gx"], r["gy"]))[0],
              "y": centre((r["gx"], r["gy"]))[1]} for u, r in sorted(positions.items())]

    paths = []
    if now:
        rows = conn.execute("SELECT * FROM unit_path WHERE turn = ? ORDER BY unit, party, step", (now,)).fetchall()
        groups = {}
        for r in rows:
            groups.setdefault((r["unit"], r["party"]), []).append(r)
        for (unit, party), steps in groups.items():
            pos = positions.get(unit)
            if not pos:
                continue
            first = (pos["prev_gx"], pos["prev_gy"]) if party == "move" else (pos["gx"], pos["gy"])
            if first[0] is None:
                continue
            ok = [s for s in steps if s["result"] == "ok"]
            stopped = [s for s in steps if s["result"] != "ok"]
            paths.append({"unit": unit, "party": party,
                          "points": [centre(first)] + [centre((s["gx"], s["gy"])) for s in ok],
                          "stop": centre((stopped[0]["gx"], stopped[0]["gy"])) if stopped else None})
    people = []
    if cfg is not None:
        for p in queries.people(conn, cfg, as_of):
            x, y = centre((p["gx"], p["gy"]))
            age = queries.turns_between(p["turn"], now) if p["turn"] and now else None   # None: turn unknown
            people.append({**{k: v for k, v in p.items() if k not in ("gx", "gy")}, "hex": coords.label((p["gx"], p["gy"])),
                           "x": x, "y": y, "age": age})
    return {"as_of": as_of, "now": now, "hexes": out, "sides": sides, "units": units, "paths": paths,
            "borders": borders, "sheets": sheets, "people": people}


def plan_payload(conn, terrain, cfg, turn, as_of=None, draft=None, replace=None, variant=plans.MAIN):
    planned, cov = plans.evaluate(conn, terrain, cfg, turn, as_of, draft=draft, replace=replace, variant=variant)
    colours = plans.assign_colours(planned)
    routes = []
    for p in planned:
        row, r = p.row, p.route
        d = {"name": p.name, "colour": colours[p.name],
             "unit": row["unit"], "kind": row["kind"], "party": row["party"],
             "orders": row["orders"], "error": p.error, "draft": p.draft, "start_hex": row["start_hex"],
             "mover_set": row["mover"], "scouts": row["scouts"], "horses": row["horses"],
             "mission": row["mission"]}
        if r:
            last = r.steps[-1] if r.steps else None
            stopped = last if last and last.result != "ok" else None
            d.update(start=centre(r.start), points=[centre(h) for h in [r.start] + r.entered],
                     stop_point=centre(stopped.hex) if stopped else None,
                     stop_kind=stopped.result if stopped else None, end=coords.label(r.end),
                     mover=r.mover, mp=r.mp, mp_left=r.mp_left, stop=r.stop, follow=r.follow,
                     notable_stop=r.notable_stop, mp_used=r.mp_used, known_cost=r.known_cost,
                     follow_points=[centre(coords.step(r.end, d)) for d in r.follow_dirs],
                     assumed=r.assumed, sim=r.as_dict())
        routes.append(d)
    overlap = [{"id": label, "x": centre(coords.parse(label))[0], "y": centre(coords.parse(label))[1],
                "routes": names} for label, names in cov["scout_overlap"].items()]
    return {"turn": plans.check_turn(turn), "variant": plans.check_variant(variant), "routes": routes,
            "overlap": overlap,
            "coverage": {k: v for k, v in cov.items() if not k.startswith("_")}}, planned


def preview_payload(conn, terrain, cfg, turn, body, as_of=None, variant=plans.MAIN):
    """Simulate the plan with the editor's unsaved route in it.

    body: {draft: {...route fields...}, replace: [unit, kind, party] | null, extend_to: 'QB 1013' | null}
    extend_to appends the directions of a straight hex line from the route's end to that hex.
    Also returns, for the draft's current end, what each of the six next steps would cost.
    """
    replace = tuple(body["replace"]) if body.get("replace") else None
    draft = plans.draft_row(conn, turn, body.get("draft") or {}, replace, variant)
    payload, planned = plan_payload(conn, terrain, cfg, turn, as_of, draft, replace, variant)
    mine = next(p for p in planned if p.draft)

    if body.get("extend_to") and mine.route:
        orders = [o for o in draft["orders"].split(",") if o]
        if mine.route.follow or (orders and orders[-1] in FOLLOW):
            raise ValueError("the route ends with a follow order; remove it before adding steps")
        base = mine.route.start
        for o in orders:
            base = coords.step(base, o) if o in coords.DIRS else mine.route.end
        target = coords.parse(body["extend_to"])
        orders += coords.path_dirs(base, target)
        if len(orders) > plans.MAX_ORDERS[draft["kind"]]:
            raise ValueError(f"that would be {len(orders)} orders; the sheet allows "
                             f"{plans.MAX_ORDERS[draft['kind']]} for a {draft['kind']}")
        draft["orders"] = ",".join(orders)
        payload, planned = plan_payload(conn, terrain, cfg, turn, as_of, draft, replace, variant)
        mine = next(p for p in planned if p.draft)

    nxt = []
    r = mine.route
    if r and not r.stop and not r.follow:
        hexes, edges = queries.knowledge(conn, as_of)
        orders = [o for o in draft["orders"].split(",") if o]
        for d in coords.DIRS:
            sim = simulate(r.start, orders + [d], hexes, edges, terrain, r.mover, mover_mp=cfg.mover_mp,
                           assumed_cost=cfg.assumed_cost)
            s = sim.steps[-1]
            x, y = centre(s.hex)
            nxt.append({"dir": d, "hex": coords.label(s.hex), "x": x, "y": y, "terrain": s.terrain,
                        "cost": s.cost, "assumed": s.assumed, "result": s.result, "mp_left": s.mp_left})
    drops = 0
    if draft["kind"] == "move" and draft["orders"] and plans.move_changes(
            conn, turn, draft["unit"], draft["orders"], draft["start_hex"], draft["mover"], variant):
        drops = plans.scout_count(conn, turn, draft["unit"], variant)   # saving would remove these
    payload["draft"] = {"name": mine.name, "orders": draft["orders"], "party": draft["party"],
                        "error": mine.error, "next": nxt, "drops_scouts": drops}
    return payload


# Climate band of each map-sheet row (the sheet's first letter), from tribenet.wiki/mapping/climate (Squip's
# revision). Players say it may be slightly out at the edges. Rows W-Z have no band there.
CLIMATE = {"A": "Cold", "B": "Cool", "C": "Cool", "D": "Temp", "E": "Temp", "F": "Temp", "G": "SubTrop",
           "H": "Tropical", "I": "Tropical", "J": "SubTrop", "K": "SubTrop",
           **{r: "Temp" for r in "LMNOPQR"}, **{r: "Cool" for r in "STUV"}}


def world_payload(cfg):
    """Every map sheet in the configured world grid, as drawing rectangles, for the zoomed-out view."""
    sheets = []
    for r in coords.letters(cfg.grid_rows):
        for c in coords.letters(cfg.grid_cols):
            x0, y0, x1, y1 = coords.sheet_box(r + c)
            sheets.append({"name": r + c, "x0": round(x0, 3), "y0": round(y0, 3), "x1": round(x1, 3), "y1": round(y1, 3),
                           "climate": CLIMATE.get(r)})
    xs = [v for s in sheets for v in (s["x0"], s["x1"])]
    ys = [v for s in sheets for v in (s["y0"], s["y1"])]
    return {"rows": cfg.grid_rows, "cols": cfg.grid_cols, "sheets": sheets,
            "box": [min(xs), min(ys), max(xs), max(ys)]}


def wxx_export(conn, as_of, layers):
    """A Clan Cartographer .wxx of the map as shown: plain hex information only. The known hexes (others' too only
    with the Others' info layer), rivers and fords (Rivers layer), resources and special sites (Resources layer).
    Nothing of ours on top: no scouting status, sources, units, plans, people or notes. Returns (bytes, as-of turn)."""
    layers = set(layers or ())
    others = "others" in layers
    hexes, edges = queries.knowledge(conn, as_of)
    cells, notes = {}, {}
    for h, info in hexes.items():
        if not info.terrain or not (info.verified or others):
            continue
        cells[h] = info.terrain
        if "resources" in layers:
            texts = sorted(info.resources) + sorted(info.specials)
            if others:
                texts += sorted({u["value"] for u in info.unverified if u["kind"] in ("resource", "special")} - set(texts))
            if texts:
                notes[h] = [t.title() if t.isupper() else t for t in texts]
    rivers, fords = [], []
    if "rivers" in layers:
        for (h, d), kinds in edges.items():
            if not ({"river", "ford"} & set(kinds)) or not (any(kinds.values()) or others):
                continue
            rivers.append((h, d))
            if "ford" in kinds:
                fords.append((h, d))
    return wxx.export(cells, rivers, fords, notes), as_of or queries.latest_turn(conn)


def page_snapshot(base_url, body):
    """A PNG of the map exactly as the page shows it, taken by opening the page in a hidden Edge window with the
    same settings (the browser's saved layers, plan, selection; the same view and size) and screenshotting the map.
    The page's buttons and pop-ups are hidden for it (?snapshot=1). Taken at twice the size, for a crisp picture."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise ValueError("saving the map needs Playwright: pip install playwright")
    import json
    from urllib.parse import urlencode
    size = lambda v, lo, hi: max(lo, min(hi, int(float(v or 0))))
    width, height = size(body.get("width"), 300, 4000), size(body.get("height"), 300, 4000)
    vb = [float(v) for v in (body.get("vb") or [])]
    if len(vb) != 4 or vb[2] <= 0 or vb[3] <= 0:
        raise ValueError("no map view to save")
    storage = {k: v for k, v in (body.get("storage") or {}).items()
               if isinstance(k, str) and k.startswith("tribemap.") and isinstance(v, str)}
    params = {k: v for k, v in (body.get("params") or {}).items() if k in ("plan", "variant", "asof") and v}
    params.update(snapshot="1", vb=",".join(f"{v:.4f}" for v in vb))
    url = f"{base_url.rstrip('/')}/?{urlencode(params)}"
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        try:
            ctx = browser.new_context(viewport={"width": width, "height": height}, device_scale_factor=2)
            ctx.add_init_script(f"for (const [k, v] of Object.entries({json.dumps(storage)})) localStorage.setItem(k, v);")
            page = ctx.new_page()
            page.goto(url)
            page.wait_for_function("window.tribemapReady === true", timeout=30000)
            return page.locator("#mapWrap").screenshot()
        finally:
            browser.close()


def create_app(cfg, project_open=None):
    """project_open: how to switch to a loaded project file (the browser-only version passes one; it reloads its
    settings). Without it, Open project is refused: on a desktop copy it would replace the live DB."""
    terrain = TerrainTable(cfg.terrain_csv)
    db.connect(cfg.db_path).close()   # make sure the DB exists and is migrated
    app = FastAPI(title="Tribemap", docs_url="/api/docs")

    def conn():
        return db.connect(cfg.db_path, readonly=True)

    def guard(fn):
        try:
            return fn()
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.get("/api/meta")
    def meta():
        c = conn()
        try:
            turns = sorted({r[0] for r in c.execute("SELECT turn FROM source WHERE turn IS NOT NULL")},
                           key=queries.turn_key)
            saved = plans.variants(c)
            plan_turns = sorted({t for t, _ in saved}, key=queries.turn_key)
            return {"clan": cfg.clan, "latest_turn": queries.latest_turn(c), "turns": turns,
                    "plan_turns": plan_turns, "plans": [{"turn": t, "variant": v} for t, v in saved],
                    "terrain": [{"code": t.code, "name": t.name, "colour": t.colour, "mp": t.mp,
                                 "mp_source": t.mp_source, "impassable": t.code in IMPASSABLE}
                                for t in terrain.by_code.values()],
                    "icons": ICONS, "world": world_payload(cfg),
                    "unit_names": {r[0]: r[1] for r in c.execute("SELECT id, name FROM unit WHERE name > ''")},
                    "unit_movers": plans.unit_movers(cfg), "project_open": project_open is not None}
        finally:
            c.close()

    @app.get("/api/history")
    def history(first: str | None = None, last: str | None = None):
        """Where each unit has been over the turns first..last (queries.history), in map coordinates."""
        c = conn()
        try:
            tracks = queries.history(c, cfg, first or None, last or None)
        finally:
            c.close()
        for t in tracks:
            for turn in t["turns"]:
                turn["path"] = [centre(h) for h in turn["path"]]
                turn["hex"] = coords.label(turn["hex"])
        return {"first": first, "last": last, "tracks": tracks}

    @app.get("/api/map")
    def map_(as_of: str | None = None, plan: str | None = None, pad: int = 1, extra: str | None = None,
             variant: str = plans.MAIN):
        """extra: comma-separated hex labels to include (e.g. an unsaved route's hexes)."""
        c = conn()
        try:
            def build():
                more = {coords.parse(x) for x in (extra or "").split(",") if x.strip()}
                if plan:
                    planned, _ = plans.evaluate(c, terrain, cfg, plan, as_of or None, variant=variant)
                    for p in planned:
                        if p.route:
                            more.add(p.route.start)
                            more.update(s.hex for s in p.route.steps)
                return map_payload(c, terrain, as_of or None, more, pad, cfg)
            return guard(build)
        finally:
            c.close()

    @app.get("/api/hex/{label}")
    def hex_(label: str, as_of: str | None = None):
        c = conn()
        try:
            return guard(lambda: queries.hex_detail(c, queries.resolve_place(c, label), as_of or None))
        finally:
            c.close()

    @app.get("/api/plan/{turn}")
    def plan(turn: str, as_of: str | None = None, variant: str = plans.MAIN):
        c = conn()
        try:
            return guard(lambda: plan_payload(c, terrain, cfg, turn, as_of or None, variant=variant)[0])
        finally:
            c.close()

    # --- writes (plan editing) ----------------------------------------------------

    last_backup = [0.0]

    def write_conn():
        return db.connect(cfg.db_path)

    def after_write(c):
        if time.time() - last_backup[0] > BACKUP_EVERY:
            db.backup(c, cfg.backup_dir, cfg.keep_backups)
            last_backup[0] = time.time()

    @app.post("/api/plan/{turn}/preview")
    def preview(turn: str, body: dict = Body(...), as_of: str | None = None, variant: str = plans.MAIN):
        c = conn()
        try:
            return guard(lambda: preview_payload(c, terrain, cfg, turn, body, as_of or None, variant))
        finally:
            c.close()

    @app.post("/api/plan/{turn}/route")
    def save_route(turn: str, body: dict = Body(...), variant: str = plans.MAIN):
        c = write_conn()
        try:
            def run():
                replace = tuple(body["replace"]) if body.get("replace") else None
                row = plans.draft_row(c, turn, body.get("draft") or {}, replace, variant)
                if not row["orders"]:
                    raise ValueError("the route has no orders yet")
                before = plans.scout_count(c, turn, row["unit"], variant)
                if replace and tuple(replace) != (row["unit"], row["kind"], row["party"]):
                    plans.remove(c, turn, replace[0], replace[1], replace[2], variant=variant)
                party = plans.add(c, turn, row["unit"], row["kind"], row["orders"], party=row["party"],
                                  scouts=row["scouts"] or 2, horses=row["horses"] or 2,
                                  mission=row["mission"] or "PATROL", start=row["start_hex"],
                                  mover=row["mover"], note=row["note"], variant=variant)
                after_write(c)
                dropped = (before - plans.scout_count(c, turn, row["unit"], variant)) if row["kind"] == "move" else 0
                return {"ok": True, "unit": row["unit"], "kind": row["kind"], "party": party,
                        "dropped_scouts": dropped}
            return guard(run)
        finally:
            c.close()

    @app.delete("/api/plan/{turn}/route")
    def delete_route(turn: str, unit: str, kind: str, party: int | None = None, variant: str = plans.MAIN):
        c = write_conn()
        try:
            def run():
                before = plans.scout_count(c, turn, unit, variant)
                n = plans.remove(c, turn, unit, kind, party if kind == "scout" else None, variant=variant)
                after_write(c)
                dropped = (before - plans.scout_count(c, turn, unit, variant)) if kind == "move" else 0
                return {"removed": n, "dropped_scouts": dropped}
            return guard(run)
        finally:
            c.close()

    @app.post("/api/unit-mover")
    def unit_mover(body: dict = Body(...)):
        """How a unit usually moves: {unit, mover: 'mounted' | 'foot'}. Saved in units.csv; used by every plan."""
        return {"unit_movers": guard(lambda: plans.set_unit_mover(cfg, str(body.get("unit", "")), body.get("mover")))}

    @app.post("/api/plan/{turn}/autoscout")
    def autoscout(turn: str, body: dict = Body(...), variant: str = plans.MAIN):
        c = write_conn()
        try:
            def run():
                result = plans.auto_scout(c, terrain, cfg, turn, str(body.get("unit", "")).strip(),
                                          parties=body.get("parties"), variant=variant)
                after_write(c)
                return result
            return guard(run)
        finally:
            c.close()

    @app.get("/api/project")
    def project_save():
        """The whole project (DB, contacts, mounted units, corrections, plan archive) as one .zip."""
        c = conn()
        try:
            turn = queries.latest_turn(c) or "empty"
        finally:
            c.close()
        return Response(project.export(cfg), media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="tribemap-{cfg.clan}-{turn}.zip"'})

    @app.post("/api/project")
    def project_load(body: dict = Body(...)):
        """Replace everything with a project file: {data (base64)}. Browser-only version."""
        if project_open is None:
            raise HTTPException(403, "Open data is only for the browser version; on this computer use tn rebuild")

        def run():
            import base64
            try:
                data = base64.b64decode(body.get("data") or "", validate=True)
            except ValueError:
                raise ValueError("the file didn't arrive intact; try again")
            info = project.load(cfg, data)
            project_open(info)
            return info
        return guard(run)

    @app.post("/api/export-wxx")
    def export_wxx(body: dict = Body(...)):
        """The map as shown as a Clan Cartographer / Worldographer .wxx: {as_of, layers: [...]}."""
        c = conn()
        try:
            data, turn = guard(lambda: wxx_export(c, body.get("as_of") or None, body.get("layers")))
        finally:
            c.close()
        return Response(data, media_type="application/octet-stream",
                        headers={"Content-Disposition": f'attachment; filename="clan-{cfg.clan}-map-{turn}.wxx"'})

    @app.post("/api/snapshot")
    def snapshot(request: Request, body: dict = Body(...)):
        """The map as shown, as a PNG: {storage: {tribemap.*: value}, params: {plan, variant, asof}, vb: [x, y, w, h],
        width, height} (the page's own saved settings, current view and map size)."""
        return Response(guard(lambda: page_snapshot(str(request.base_url), body)), media_type="image/png")

    @app.post("/api/import")
    def import_report(body: dict = Body(...)):
        """A turn report or .wxx map from the browser: {filename, data (base64), dry_run?, force?}."""
        c = write_conn()
        try:
            def run():
                import base64
                try:
                    data = base64.b64decode(body.get("data") or "", validate=True)
                except ValueError:
                    raise ValueError("the file didn't arrive intact; try again")
                info = importer.import_upload(c, cfg, terrain, body.get("filename"), data,
                                              dry_run=bool(body.get("dry_run")), force=bool(body.get("force")),
                                              own_maps=project_open is not None)   # browser-only: maps are ours
                if not body.get("dry_run"):
                    db.backup(c, cfg.backup_dir, cfg.keep_backups)
                return info
            return guard(run)
        finally:
            c.close()

    @app.post("/api/plan/{turn}/export")
    def export(turn: str, variant: str = plans.MAIN):
        c = conn()
        try:
            def run():
                xlsx, js, _ = plans.export(c, cfg, turn, variant=variant)
                return {"xlsx": str(xlsx), "json": str(js)}
            return guard(run)
        finally:
            c.close()

    @app.get("/api/plan/{turn}/paste-in")
    def paste_in(turn: str, variant: str = plans.MAIN):
        """The paste-in workbook as a download, written nowhere (the browser-only version has no turn folders)."""
        c = conn()
        try:
            def run():
                import tempfile
                with tempfile.TemporaryDirectory() as tmp:
                    xlsx, _, _ = plans.export(c, cfg, turn, outdir=tmp, variant=variant)
                    return Response(xlsx.read_bytes(), media_type=XLSX,
                                    headers={"Content-Disposition": f'attachment; filename="{xlsx.name}"'})
            return guard(run)
        finally:
            c.close()

    @app.post("/api/plan/{turn}/copy")
    def copy(turn: str, body: dict = Body(...)):
        """A new variant of the turn, optionally starting as a copy of another: {to, from?}."""
        c = write_conn()
        try:
            def run():
                to = plans.check_variant(body.get("to"))
                if any(t == plans.check_turn(turn) and v == to for t, v in plans.variants(c)):
                    raise ValueError(f"plan {to} already exists for {turn}")
                n = plans.copy_plan(c, turn, body["from"], to) if body.get("from") else 0
                after_write(c)
                return {"turn": plans.check_turn(turn), "variant": to, "copied": n}
            return guard(run)
        finally:
            c.close()

    @app.delete("/api/plan/{turn}")
    def delete_plan(turn: str, variant: str = plans.MAIN):
        c = write_conn()
        try:
            def run():
                n = plans.remove(c, turn, variant=variant)
                after_write(c)
                return {"removed": n}
            return guard(run)
        finally:
            c.close()

    @app.middleware("http")
    async def fresh_page_files(request: Request, call_next):
        # the page's own files change with every update: make the browser check for a new version each time,
        # so a normal refresh never runs an old script
        response = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    @app.middleware("http")
    async def same_origin_writes(request: Request, call_next):
        # Only this page may change data: other sites open in the browser can still send requests
        # to 127.0.0.1, so writes must be JSON (forcing a CORS preflight we never approve) and must
        # come from our own origin. The Host check also blocks DNS-rebinding tricks.
        host = (request.headers.get("host") or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost"):
            return JSONResponse({"detail": "forbidden host"}, status_code=403)
        if request.method not in ("GET", "HEAD"):
            origin = request.headers.get("origin")
            ours = {f"http://127.0.0.1:{request.url.port}", f"http://localhost:{request.url.port}"}
            if origin and origin not in ours:
                return JSONResponse({"detail": "forbidden origin"}, status_code=403)
            if request.method != "DELETE" and \
                    not (request.headers.get("content-type") or "").startswith("application/json"):
                return JSONResponse({"detail": "JSON only"}, status_code=415)
            if request.method == "DELETE" and request.headers.get("x-tribemap") != "1":
                return JSONResponse({"detail": "missing header"}, status_code=403)
        return await call_next(request)

    @app.get("/")
    def index():
        # Version the page's script and stylesheet by their modification time, so after an update the browser
        # fetches the new files instead of running a cached copy (a normal refresh doesn't always re-check them).
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        for name in ("app.js", "style.css"):
            version = int((STATIC / name).stat().st_mtime)
            html = html.replace(f"/static/{name}", f"/static/{name}?v={version}")
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
