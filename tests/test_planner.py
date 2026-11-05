import math
import pytest

from tribemap import coords as c
from tribemap import db, importer, plans
from tribemap.cli import main
from tribemap.planner import FOLLOW, parse_orders, simulate
from tribemap.queries import HexInfo

H = c.parse
START = H("QB 1016")


def world(terrain_by_label, rivers=(), fords=(), no_fords=()):
    hexes = {H(l): HexInfo(H(l), terrain=t, terrain_how="scouted") for l, t in terrain_by_label.items()}
    edges = {}
    for kind, items in (("river", rivers), ("ford", fords), ("no_ford", no_fords)):
        for label, d in items:
            edges.setdefault(c.edge(H(label), d), {})[kind] = True
    return hexes, edges


def column(*codes):
    """Terrain for the hexes straight north of START: QB 1015, 1014, ..."""
    return {f"QB 10{15 - i:02d}": t for i, t in enumerate(codes)}


def test_parse_orders():
    assert parse_orders("n, ne  NW,frr") == ["N", "NE", "NW", "FRR"]
    with pytest.raises(ValueError):
        parse_orders("N,UP")


def test_mp_spent_and_glimpse(terrain):
    hexes, edges = world(column("PR", "PR", "PR", "PR", "PR", "BH"))
    r = simulate(START, list("NNNNNN"), hexes, edges, terrain)
    assert [s.result for s in r.steps] == ["ok"] * 5 + ["mp_block"]
    assert r.mp_left == 0 and c.label(r.end) == "QB 1011" and c.label(r.glimpse) == "QB 1010"
    assert r.stop == "out of MP" and not r.unused


def test_expensive_terrain_stops_early(terrain):
    hexes, edges = world(column("BH", "BH", "BH"))
    r = simulate(START, ["N", "N", "N", "NE"], hexes, edges, terrain)
    assert [s.result for s in r.steps] == ["ok", "ok", "mp_block"]
    assert r.mp_left == 3 and r.unused == ["NE"]


def test_unknown_terrain_uses_assumed_cost(terrain):
    hexes, edges = world(column("PR"))
    r = simulate(START, ["N", "N"], hexes, edges, terrain, assumed_cost=4)
    assert [(s.cost, s.assumed) for s in r.steps] == [(3, False), (4, True)] and r.assumed


def test_rivers_block_except_at_fords(terrain):
    hexes, edges = world(column("PR", "PR"), rivers=[("QB 1015", "N")])
    r = simulate(START, ["N", "N"], hexes, edges, terrain)
    assert [s.result for s in r.steps] == ["ok", "river_block"] and r.glimpse is None
    hexes, edges = world(column("PR", "PR"), rivers=[("QB 1015", "N")], fords=[("QB 1014", "S")])
    assert len(simulate(START, ["N", "N"], hexes, edges, terrain).entered) == 2
    hexes, edges = world(column("PR", "PR"), fords=[("QB 1015", "N")], no_fords=[("QB 1015", "N")])
    assert simulate(START, ["N", "N"], hexes, edges, terrain).steps[-1].result == "river_block"


def test_impassable_water(terrain):
    hexes, edges = world(column("PR", "O"))
    r = simulate(START, ["N", "N"], hexes, edges, terrain)
    assert r.steps[-1].result == "impassable" and c.label(r.glimpse) == "QB 1014"


def test_to_limit_and_follow(terrain):
    hexes, edges = world(column("PR", "PR", "BH", "PR"))
    r = simulate(START, ["NL", "S"], hexes, edges, terrain)
    assert [s.result for s in r.steps] == ["ok"] * 4 + ["mp_block"]   # 3+3+6+3 = 15, then out of MP
    assert r.stop == "NL: out of MP" and r.unused == ["S"]
    r = simulate(START, ["NW", "FRR", "N"], hexes, edges, terrain)
    assert len(r.entered) == 1 and r.follow == "FRR" and r.unused == ["N"]


def test_mounted_and_foot_mp(terrain):
    hexes, edges = world({})
    assert simulate(START, ["N"] * 12, hexes, edges, terrain, "mounted").mp == 27
    assert len(simulate(START, ["N"] * 12, hexes, edges, terrain, "foot").entered) == 6


# --- plans (with the sample report imported) ---------------------------------

@pytest.fixture
def conn(cfg, terrain, sample_file):
    conn = db.connect(cfg.db_path)
    importer.import_report(conn, cfg, terrain, sample_file)
    return conn


def test_plan_parties_and_scouts_start_after_move(conn, cfg, terrain):
    plans.add(conn, "906-6", "0999", "move", "N")
    assert plans.add(conn, "906-06", "0999", "scout", "N,N") == 1
    assert plans.add(conn, "906-06", "0999", "scout", "S") == 2
    assert plans.add(conn, "906-06", "0999", "scout", "SE", party=2) == 2   # replaces party 2
    planned, cov = plans.evaluate(conn, terrain, cfg, "906-06")
    by = {p.name: p for p in planned}
    assert set(by) == {"0999 move", "0999 scout 1", "0999 scout 2"}
    assert c.label(by["0999 move"].route.end) == "QB 1015"
    assert c.label(by["0999 scout 1"].route.start) == "QB 1015"
    assert by["0999 scout 1"].route.mover == "scout" and by["0999 move"].route.mover == "foot"


def test_plan_limits(conn):
    for _ in range(8):
        plans.add(conn, "906-06", "0999", "scout", "N")
    with pytest.raises(ValueError):
        plans.add(conn, "906-06", "0999", "scout", "N")
    with pytest.raises(ValueError):
        plans.add(conn, "906-06", "0999", "scout", ",".join(["N"] * 10))


def test_new_unit_needs_start(conn, cfg, terrain):
    plans.add(conn, "906-06", "1999", "scout", "N")
    planned, _ = plans.evaluate(conn, terrain, cfg, "906-06")
    assert "no known position" in planned[0].error
    plans.add(conn, "906-06", "1999", "move", "NE", start="QB 1016", mover="mounted")
    planned, _ = plans.evaluate(conn, terrain, cfg, "906-06")
    assert all(not p.error for p in planned)


def test_coverage_status_and_overlap(conn, cfg, terrain):
    # SE x3 from QB 1016: QB 1117 (our BF), QB 1217 (only a player's word), QB 1318 (never seen)
    importer.add_correction(conn, cfg, hex="QB 1217", kind="terrain", value="PR", source_kind="player",
                            provider="X", turn="906-05")
    plans.add(conn, "906-06", "0999", "scout", "SE,SE,SE")
    plans.add(conn, "906-06", "0999", "scout", "SE")
    # S x4: QB 1017, 1018 (known), QB 1019 (glimpsed SW, 8 MP) then out of MP glimpsing QB 1020
    plans.add(conn, "906-06", "0999", "scout", "S,S,S,S")
    _, cov = plans.evaluate(conn, terrain, cfg, "906-06")
    assert cov["by_status"] == {"terrain_only": ["QB 1019"], "known": ["QB 1017", "QB 1018", "QB 1117"],
                                "new": ["QB 1318"], "verify": ["QB 1217"]}
    assert cov["new_glimpses"] == ["QB 1020"]
    # both SE parties share their first step (QB 1117): unavoidable, so not flagged as overlap
    assert cov["scout_overlap"] == {} and cov["shared_first_steps"] == 1


def test_export_and_load(conn, cfg, terrain, tmp_path):
    from openpyxl import load_workbook
    plans.add(conn, "906-06", "0999", "move", "NW")
    plans.add(conn, "906-06", "0999", "scout", "N,NE,FRR")
    xlsx, js, tsv = plans.export(conn, cfg, "906-06")
    assert xlsx.parent.name == "906-6" and xlsx.name == "0999_906_6_PlanPaste.xlsx"
    wb = load_workbook(xlsx)
    tm = [c.value for c in wb["Tribe_Movement"][2]]
    assert tm[1:7] == ["0999", None, None, None, "NW", "Empty"] and len(tm) == 45
    sm = [c.value for c in wb["Scout_Movement"][2]]
    assert sm[1:9] == ["0999", 2, 2, "PATROL", "N", "NE", "FRR", "EMPTY"] and len(sm) == 14
    assert tsv["Scout_Movement"].startswith("0999\t2\t2\tPATROL\tN\tNE\tFRR\tEMPTY")
    assert tsv["Tribe_Movement"].startswith("0999\t\t\t\tNW\tEmpty")
    plans.remove(conn, "906-06")
    assert plans.load(conn, js) == 2 and len(plans.routes(conn, "906-06")) == 2


def test_cli_plan_and_render(cfg, sample_file, tmp_path, capsys):
    main(["import", str(sample_file)])
    main(["plan", "add", "906-06", "0999", "scout", "N,N,N,N,N,N"])
    main(["plan", "show", "906-06", "--steps"])
    out = capsys.readouterr().out
    assert "0999 scout 1" in out and "Coverage:" in out
    main(["route", "--from", "0999", "--orders", "NL", "--json"])
    assert '"stop": "NL: out of MP"' in capsys.readouterr().out
    png = tmp_path / "plan.png"
    main(["render", "--plan", "906-06", "--out", str(png)])
    assert png.stat().st_size > 10_000


def test_follow_hint_direction(terrain):
    # river on the NW side of QB 0816: with it on the right a scout heads SW, on the left it heads N
    hexes, edges = world({"QB 0816": "SW"}, rivers=[("QB 0816", "NW")])
    r = simulate(H("QB 0917"), ["NW", "FRR"], hexes, edges, terrain)
    assert r.follow == "FRR" and r.follow_dirs == ["SW"] and "heads SW along the river" in r.stop
    r = simulate(H("QB 0917"), ["NW", "FRL"], hexes, edges, terrain)
    assert r.follow_dirs == ["N"] and r.as_dict()["follow_to"] == ["QB 0815"]
    # a run of river sides (N and NE): right-hand follow leaves from the N end, left-hand from the NE end
    hexes, edges = world({}, rivers=[("QB 1016", "N"), ("QB 1016", "NE")])
    assert simulate(START, ["FRR"], hexes, edges, terrain).follow_dirs == ["NW"]
    assert simulate(START, ["FRL"], hexes, edges, terrain).follow_dirs == ["SE"]
    # no river known: say so rather than guess
    r = simulate(START, ["FRR"], *world({}), terrain)
    assert r.follow_dirs == [] and "direction unknown" in r.stop
    # coast and mountains use neighbouring hexes: ocean to the N, keep it on the left -> NE
    hexes, edges = world({"QB 1015": "O"})
    assert simulate(START, ["FCL"], hexes, edges, terrain).follow_dirs == ["NE"]
    assert simulate(START, ["FMR"], hexes, edges, terrain).follow_dirs == []


def test_shared_first_steps_are_not_overlap(conn, cfg, terrain):
    for orders in ("N,N,N", "N,NE", "SE,SE,SE", "S,SE,NE"):
        plans.add(conn, "906-06", "0999", "scout", orders)
    _, cov = plans.evaluate(conn, terrain, cfg, "906-06")
    # QB 1015 (N) is a first step shared by two parties: unavoidable, not flagged;
    # QB 1217 is entered by two parties further out (SE,SE and S,SE,NE): flagged
    assert set(cov["scout_overlap"]) == {"QB 1217"} and cov["shared_first_steps"] == 1   # QB 1015


def test_route_lanes():
    from tribemap.render import LANE_GAP, lanes
    a, b, c_, d = (0, 0), (1.5, 0.866), (3, 0), (1.5, -0.866)
    alone, shared1, shared2 = lanes([[a, d], [a, b, c_], [a, b]])
    assert alone == [a, d]                         # a lone route runs centre to centre
    assert shared1[0] == a and shared1[3] == b     # shared segment: leaves and rejoins the centres...
    assert shared1[-1] == c_ and len(shared1) == 5 # ...and the unshared next segment goes straight
    gap = math.hypot(shared1[1][0] - shared2[1][0], shared1[1][1] - shared2[1][1])
    assert abs(gap - LANE_GAP) < 1e-9              # the two lanes are just one small gap apart


def test_route_colours_per_tribe(terrain):
    from tribemap.plans import DRAFT_COLOUR, MOVE_COLOUR, ROUTE_COLOURS, Planned, assign_colours
    hexes, edges = world({})

    def route(unit, party, kind="scout", draft=False):
        p = Planned({"unit": unit, "kind": kind, "party": party}, simulate(START, ["N"], hexes, edges, terrain))
        p.draft = draft
        return p

    plan = [route(t, n) for t in ("0999", "1999") for n in range(1, 7)]
    plan += [route("0999e1", 1), route("0999e1", 2), route("0999", None, kind="move"),
             route("1999", 7, draft=True)]
    c = assign_colours(plan)
    assert c["0999 move"] == MOVE_COLOUR and c["1999 scout 7"] == DRAFT_COLOUR
    # party n gets colour n in every tribe; an element's parties continue its tribe's sequence
    assert [c[f"0999 scout {n}"] for n in range(1, 7)] == ROUTE_COLOURS[:6]
    assert [c[f"1999 scout {n}"] for n in range(1, 7)] == ROUTE_COLOURS[:6]
    assert [c["0999e1 scout 1"], c["0999e1 scout 2"]] == ROUTE_COLOURS[6:8]
    assert len(ROUTE_COLOURS) == 8


def test_best_route_takes_the_most_new_ground(terrain):
    from tribemap.planner import best_route
    hexes, edges = world(column("PR"))   # QB 1015 known, everything else unknown (assumed 3 MP)
    known = {H("QB 1015"), START}
    value = lambda h, how: 0.0 if h in known else (1.0 if how == "entered" else 0.4)
    score, orders = best_route(START, hexes, edges, terrain, value)
    # 15 MP / 3 = 5 new hexes, plus one more order that can't be afforded: a free glimpse
    assert score == 5.4 and len(orders) == 6
    r = simulate(START, orders, hexes, edges, terrain)
    assert len(r.entered) == 5 and r.glimpse and H("QB 1015") not in r.entered


def test_best_route_respects_rivers(terrain):
    from tribemap.planner import best_route
    # river on every side but N: the only way out is north
    hexes, edges = world({}, rivers=[("QB 1016", d) for d in ("NE", "SE", "S", "SW", "NW")])
    score, orders = best_route(START, hexes, edges, terrain, lambda h, how: 1.0 if how == "entered" else 0.4)
    assert orders[0] == "N" and score > 4


def test_changing_a_move_drops_that_units_scouts(conn, cfg, terrain):
    plans.add(conn, "906-06", "0999", "move", "N")
    plans.add(conn, "906-06", "0999", "scout", "N,N")
    plans.add(conn, "906-06", "0999", "scout", "S")
    plans.add(conn, "906-06", "1999", "scout", "S", start="QB 1016")
    plans.add(conn, "906-06", "0999", "move", "N")          # unchanged: scouts kept
    assert plans.scout_count(conn, "906-06", "0999") == 2
    plans.add(conn, "906-06", "0999", "move", "N,N")        # changed: scouts dropped
    assert plans.scout_count(conn, "906-06", "0999") == 0
    assert plans.scout_count(conn, "906-06", "1999") == 1   # other units untouched
    plans.add(conn, "906-06", "0999", "scout", "S")
    plans.remove(conn, "906-06", "0999", "move")             # deleting the move drops them too
    assert plans.scout_count(conn, "906-06", "0999") == 0


def test_auto_scout(conn, cfg, terrain):
    plans.add(conn, "906-06", "0999", "scout", "S", party=3)   # a manual party is kept and avoided
    added = plans.auto_scout(conn, terrain, cfg, "906-06", "0999")["added"]
    assert [a["party"] for a in added] == [1, 2, 4, 5, 6, 7, 8]
    planned, cov = plans.evaluate(conn, terrain, cfg, "906-06")
    scouts = [p for p in planned if p.row["kind"] == "scout"]
    assert len(scouts) == 8 and all(p.route and not p.error for p in scouts)
    # follow orders come first where they can travel along a known feature; the rest are plain routes
    follows = [a for a in added if a["follow"]]
    assert follows and len(follows) <= plans.MAX_FOLLOW_PARTIES
    assert all(a["orders"].split(",")[-1] in FOLLOW for a in follows)
    assert all(a["orders"].split(",")[-1] not in FOLLOW for a in added if not a["follow"])
    # (only hexes walked into count here, not where the follow orders go after, so follows lower it)
    assert cov["counts"].get("new", 0) >= 12
    assert len(cov["scout_overlap"]) <= 1   # a follow's approach may cross a plain route: follows have priority
    with pytest.raises(ValueError):
        plans.auto_scout(conn, terrain, cfg, "906-06", "0999")   # no free parties left
    with pytest.raises(ValueError):
        plans.auto_scout(conn, terrain, cfg, "906-06", "1999")   # no position known


def test_a_units_usual_mover(conn, cfg, terrain):
    plans.add(conn, "906-06", "0999", "move", "N")
    mp = lambda: next(p for p in plans.evaluate(conn, terrain, cfg, "906-06")[0] if p.row["kind"] == "move").route.mp
    assert mp() == 18                                        # on foot unless told otherwise
    assert plans.set_unit_mover(cfg, "0999", "mounted") == {"0999": "mounted"}
    assert plans.unit_movers(cfg) == {"0999": "mounted"} and mp() == 27
    plans.add(conn, "906-06", "0999", "move", "N", mover="foot")   # a move's own mover still wins
    assert mp() == 18
    plans.set_unit_mover(cfg, "0999", "foot")
    assert plans.unit_movers(cfg) == {}
    with pytest.raises(ValueError):
        plans.set_unit_mover(cfg, "0999", "scout")


def test_plan_variants_are_separate(conn, cfg, terrain):
    plans.add(conn, "906-06", "0999", "move", "N")
    plans.add(conn, "906-06", "0999", "scout", "N")
    assert plans.copy_plan(conn, "906-06", "main", "B") == 2
    plans.add(conn, "906-06", "0999", "move", "S", variant="B")          # changes B only
    assert plans.variants(conn) == [("906-06", "main"), ("906-06", "B")]
    assert [r["orders"] for r in plans.routes(conn, "906-06")] == ["N", "N"]
    assert [r["orders"] for r in plans.routes(conn, "906-06", "B")] == ["S"]   # B's scout was dropped
    xlsx, js, _ = plans.export(conn, cfg, "906-06", variant="B")
    assert xlsx.name == "0999_906_6_B_PlanPaste.xlsx"
    plans.remove(conn, "906-06", variant="B")
    assert plans.variants(conn) == [("906-06", "main")]
    assert plans.load(conn, js) == 1 and plans.variants(conn) == [("906-06", "main"), ("906-06", "B")]
    with pytest.raises(ValueError):
        plans.add(conn, "906-06", "0999", "move", "N", variant="bad/name")


def test_new_turn_import_archives_and_clears_plans(cfg, terrain, sample_file):
    import json
    from tribemap.cli import main
    main(["import", str(sample_file)])
    main(["plan", "add", "906-06", "0999", "move", "N"])
    main(["plan", "add", "906-06", "0999", "move", "S", "--variant", "B"])
    # re-importing the same turn keeps the plans (plus 906-05: what our units did, from the first import)
    main(["import", str(sample_file), "--force"])
    conn = db.connect(cfg.db_path)
    assert plans.variants(conn) == [("906-05", "main"), ("906-06", "main"), ("906-06", "B")]
    conn.close()
    # a report for a new turn (906-06) clears them, after archiving each one
    newer = sample_file.with_name("0999_906_06_Results.txt")
    newer.write_text(sample_file.read_text(encoding="utf-8").replace("906-05 (#77)", "906-06 (#78)")
                     .replace("906-06 (#78), 18/11", "906-07 (#79), 18/12"), encoding="utf-8")
    main(["import", str(newer)])
    conn = db.connect(cfg.db_path)
    # left: 906-06 as it actually went (from the report), and an empty plan for 906-07
    assert plans.variants(conn) == [("906-06", "main"), ("906-07", "main")]
    actual = plans.routes(conn, "906-06")
    assert actual and all(r["note"] == "from the 906-06 report" for r in actual)
    assert {r["kind"] for r in actual} == {"move", "scout"}
    assert plans.routes(conn, "906-07") == []
    archived = sorted((cfg.data_dir / "plan-archive").glob("*.json"))
    assert [p.name.split("_")[:2] for p in archived] == [["906-05", "main"], ["906-06", "B"], ["906-06", "main"]]
    assert json.loads(archived[2].read_text())[0]["orders"] == "N"


def test_follow_path_projection(terrain):
    from tribemap.planner import follow_path
    # a river along the N sides of a row of hexes: FRL (river on the left) heads east along it
    row = ["QB 1016", "QB 1116", "QB 1216", "QB 1316"]
    hexes, edges = world({}, rivers=[(h, "N") for h in row])
    path = follow_path(H("QB 1016"), "FRL", 15, hexes, edges, terrain)
    assert path and path[0] == coords_step("QB 1016", "NE") and len(path) == 5
    assert follow_path(H("QB 1016"), "FCL", 15, hexes, edges, terrain) is None   # no coast known
    # sure: only as far as the river is known, plus one step; past that the straight-on guess isn't trusted
    sure = follow_path(H("QB 1016"), "FRL", 15, hexes, edges, terrain, sure=True)
    assert sure and len(sure) < len(path) and path[:len(sure)] == sure


def test_no_follow_where_a_river_is_in_the_way(terrain):
    from tribemap.planner import river_in_the_way as blocked
    # mountains to the N of the start, with a river between: FMR would only walk the riverbank
    hexes, edges = world({"QB 1015": "LCM"}, rivers=[("QB 1016", "N")])
    assert blocked(START, "FMR", hexes, edges) and blocked(START, "FML", hexes, edges)
    assert not blocked(START, "FRR", hexes, edges)                # the river itself is fine to follow
    # a river along the far side of the mountain: it probably wraps round the range
    hexes, edges = world({"QB 1015": "LCM"}, rivers=[("QB 1015", "N")])
    assert blocked(START, "FMR", hexes, edges)
    hexes, edges = world({"QB 1015": "LCM"})                     # no river known: follow the mountains
    assert not blocked(START, "FMR", hexes, edges)


def coords_step(label, d):
    return c.step(H(label), d)


def test_outward_routes_never_circle_the_start(terrain):
    from tribemap.planner import best_route
    hexes, edges = world({})
    # nearer rings worth far more: without the rule the best route would wind round the start
    value = lambda h, how: (3.0 / c.distance(START, h)) if how == "entered" else 0.0
    _, orders = best_route(START, hexes, edges, terrain, value, outward=True)
    cur, rings = START, []
    for d in orders:
        cur = c.step(cur, d)
        rings.append(c.distance(START, cur))
    assert rings[:2] == [1, 2] and all(b >= a for a, b in zip(rings, rings[1:]))


def test_value_wedges_split_the_open_side(terrain):
    from tribemap.planner import bearing, value_wedges
    # only hexes to the east are worth scouting: every wedge should point east
    value = lambda h, how: 1.0 if 30 <= bearing(START, h) <= 150 else 0.0
    wedges = value_wedges(START, 4, value)
    for w in wedges:
        inside = [h for h in c.within(START, 4) if h != START and w(h)]
        assert inside and all(20 <= bearing(START, h) <= 160 for h in inside if value(h, "entered"))
    # the four wedges don't overlap
    ring = [h for h in c.within(START, 4) if h != START]
    assert all(sum(w(h) for w in wedges) == 1 for h in ring)
    assert value_wedges(START, 3, lambda h, how: 0.0) == [None, None, None]


def test_wasted_look_ins_are_re_aimed(conn, cfg, terrain):
    # party 1 runs out of MP after QB 1013 and looks into QB 1012 (N); another unit's party then enters it
    plans.add(conn, "906-06", "0999", "scout", "N,N,N,N", note="auto-planned")
    plans.add(conn, "906-06", "1999", "scout", "S", start="QB 1011", note="auto-planned")
    planned, _ = plans.evaluate(conn, terrain, cfg, "906-06")
    first = next(p for p in planned if p.name == "0999 scout 1")
    assert c.label(first.route.glimpse) == "QB 1012"
    changed = plans.fix_look_ins(conn, terrain, cfg, "906-06")
    assert [(f["unit"], f["party"], f["old"]) for f in changed] == [("0999", 1, "N,N,N,N")]
    planned, _ = plans.evaluate(conn, terrain, cfg, "906-06")
    first = next(p for p in planned if p.name == "0999 scout 1")
    assert first.route.entered[-1] == H("QB 1013")                    # same route...
    assert first.route.glimpse is None or c.label(first.route.glimpse) != "QB 1012"   # ...new look-in
    # a hand-drawn route keeps its look-in
    plans.add(conn, "906-06", "0999", "scout", "N,N,N,N", party=2)
    assert not [f for f in plans.fix_look_ins(conn, terrain, cfg, "906-06") if f["party"] == 2]


def test_scout_value_tiers():
    from tribemap.plans import REFRESH_MAX, scout_value
    from tribemap.queries import HexInfo
    def info(terrain=None, how="scouted", last=None, verified=True):
        i = HexInfo(H("QB 1016"), terrain=terrain, terrain_how=how if verified else "reported", last_scouted=last)
        return i
    new = scout_value(None, "906-10", "entered")
    terrain_only = scout_value(info("PR", "glimpsed"), "906-10", "entered")
    fresh = scout_value(info("PR", last="906-10"), "906-10", "entered")
    old = scout_value(info("PR", last="906-01"), "906-10", "entered")
    ancient = scout_value(info("PR", last="900-01"), "906-10", "entered")
    assert new > terrain_only > old > fresh > 0          # old ground: lowest, but never zero
    assert ancient == REFRESH_MAX < terrain_only           # capped below anything not fully known
    assert scout_value(info("PR", last="906-01"), "906-10", "glimpsed") == 0   # looking in adds nothing


def test_unexplored_mountains_and_hills_are_worth_more():
    from tribemap.plans import scout_value
    from tribemap.queries import HexInfo
    glimpsed = lambda code: HexInfo(H("QB 1016"), terrain=code, terrain_how="glimpsed")
    new = scout_value(None, "906-10", "entered")
    mountain, hills, plain = (scout_value(glimpsed(c), "906-10", "entered") for c in ("LCM", "BH", "PR"))
    assert new > mountain > hills > plain > 0


def test_follow_value_is_told_where_the_route_has_been(terrain):
    from tribemap.planner import best_route
    hexes, edges = world({})
    seen = []

    def follow_value(h, order, left, walked):
        seen.append((h, set(walked)))
        return None
    best_route(START, hexes, edges, terrain, lambda h, how: 1.0, follow_value=follow_value, max_orders=2)
    # at every hex the follow is offered, the hexes walked so far (start included) come with it
    assert all(h in walked and START in walked for h, walked in seen)
    assert any(len(walked) == 2 for _, walked in seen)   # one step out (a follow takes the second order)


def test_fog_starts_after_a_year():
    from tribemap.queries import FOG_MAX, fog_alpha
    assert fog_alpha(None) == fog_alpha(0) == fog_alpha(11) == 0   # nothing for the first year (12 turns)
    assert 0 < fog_alpha(12) < fog_alpha(24) < fog_alpha(36) == FOG_MAX == fog_alpha(60)


def test_straight_run_becomes_to_limit(terrain):
    from tribemap.plans import with_to_limit
    hexes, edges = world(column(*["PR"] * 8))
    sim = dict(mover_mp={"scout": 15, "mounted": 27, "foot": 18}, assumed_cost=3)
    # N x5 then the step it can't afford: one NL order goes exactly as far
    assert with_to_limit(list("NNNNNN"), START, hexes, edges, terrain, sim) == ["NL"]
    assert with_to_limit(["NE", "N", "N"], START, hexes, edges, terrain, sim) == ["NE", "NL"]
    assert with_to_limit(["N", "NE"], START, hexes, edges, terrain, sim) == ["N", "NE"]      # no straight run
    assert with_to_limit(["NE", "N"], START, hexes, edges, terrain, sim) == ["NE", "N"]      # a run of one
    # a run then a look-in step another way: the to-limit order wins (it looks in straight ahead instead)
    assert with_to_limit(["N", "N", "N", "N", "N", "NE"], START, hexes, edges, terrain, sim) == ["NL"]
    # it would walk on into ground another party covers: keep the plain steps
    further = simulate(START, ["NL"], hexes, edges, terrain, mover_mp=sim["mover_mp"])
    assert with_to_limit(["N", "N"], START, hexes, edges, terrain, sim,
                         frozenset(further.entered[2:3])) == ["N", "N"]


def test_an_older_terrain_table_gets_the_shipped_costs(tmp_path):
    from tribemap.terrain import DEFAULTS, TerrainTable
    old = tmp_path / "terrain.csv"
    lines = DEFAULTS.read_text(encoding="utf-8").splitlines()
    keep = [l for l in lines if not l.startswith(("CH,", "PI,"))]          # PI missing altogether
    keep = [l.replace("GH,Grassy Hills,GRASSY HILLS,#a9c96a,5,mandate 10.5", "GH,Grassy Hills,GRASSY HILLS,#a9c96a,,")
            .replace("SW,Swamp,SWAMP,#9ad9ad,8,mandate 10.5", "SW,Swamp,SWAMP,#9ad9ad,9,my guess") for l in keep]
    old.write_text("\n".join(keep) + "\n", encoding="utf-8")
    t = TerrainTable(old)
    assert t.mp("GH") == 5 and t.mp("CH") == 6 and t.mp("PI") == 7   # filled in from the shipped table
    assert t.mp("SW") == 9                                           # a cost set in the copy stands
