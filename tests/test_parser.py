from conftest import SAMPLE

from tribemap import coords as c
from tribemap.parser import Parser

H = c.parse


def parse(terrain, text=SAMPLE):
    return Parser(terrain, clan="0999").parse(text.splitlines())


def obs(rep, kind, label=None, party=None):
    return [o for o in rep.obs if o.kind == kind
            and (label is None or c.label(o.hex) == label) and (party is None or o.party == party)]


def test_header_and_turn(terrain):
    rep = parse(terrain)
    u = rep.units[0]
    assert (u.unit, u.kind, u.hex, u.prev_hex) == ("0999", "Tribe", H("QB 1016"), H("QB 1017"))
    assert (rep.turn, rep.turn_no, rep.season, rep.weather) == ("906-05", 77, "Summer", "FINE")
    assert (rep.next_turn, rep.next_due) == ("906-06", "18/11/2026")


def test_tribe_movement_starts_at_previous_hex(terrain):
    rep = parse(terrain)
    assert [(s.dir, c.label(s.hex)) for s in rep.units[0].paths["move"]] == [("N", "QB 1016")]
    assert not any("movement ends" in u[2] for u in rep.unparsed)


def test_scout_steps_and_glimpses(terrain):
    rep = parse(terrain)
    path = rep.units[0].paths["scout1"]
    assert [c.label(s.hex) for s in path] == ["QB 1015", "QB 1014", "QB 1013"]
    t = obs(rep, "terrain", party="scout1")
    assert [(c.label(o.hex), o.value, o.how) for o in t] == [
        ("QB 1015", "PR", "entered"), ("QB 1014", "BH", "entered"), ("QB 1013", "BH", "entered"),
        ("QB 0913", "LCM", "glimpsed")]


def test_find_salt_river_and_seen_terrain(terrain):
    rep = parse(terrain)
    assert [o.value for o in obs(rep, "resource", "QB 0916")] == ["SALT"]
    rivers = obs(rep, "river", party="scout2")
    assert [(c.label(o.hex), o.dir) for o in rivers] == [("QB 0815", "NW")]


def test_continuation_rivers_fords_items_no_ford(terrain):
    rep = parse(terrain)
    first = "QB 1116"   # NE of QB 1016 (even column: same row)
    glimpsed = {(c.label(o.hex), o.value) for o in obs(rep, "terrain", party="scout3") if o.how == "glimpsed"}
    assert glimpsed == {(c.label(c.step(H(first), "SE")), "LCM"), (c.label(c.step(H(first), "S")), "LCM")}
    assert [o.value for o in obs(rep, "resource", first)] == ["COPPER ORE"]
    second = c.label(c.step(H(first), "N"))
    assert sorted(o.dir for o in obs(rep, "river", second)) == ["N", "N", "NE", "NW", "SE"]
    assert [o.dir for o in obs(rep, "ford", second)] == ["NE"]
    assert [o.value for o in obs(rep, "item", second)] == ["3 Rope"]
    assert [o.dir for o in obs(rep, "no_ford", second)] == ["N"]
    assert rep.units[0].paths["scout3"][-1].result == "no_ford"
    assert obs(rep, "nothing_found", second)


def test_mp_block_glimpses_next_hex(terrain):
    rep = parse(terrain)
    path = rep.units[0].paths["scout4"]
    assert [s.result for s in path] == ["ok", "ok", "mp_block"]
    t = obs(rep, "terrain", c.label(path[-1].hex))
    assert [(o.value, o.how) for o in t] == [("SW", "glimpsed")]


def test_specials_sightings_and_unparsed(terrain):
    rep = parse(terrain)
    first = c.label(c.step(H("QB 1016"), "SE"))
    assert {o.value for o in obs(rep, "special", first)} == {"Duskholm", "Mercenary Aldo Brenn"}
    assert [o.value for o in obs(rep, "unit_sighting", first)] == ["0123"]
    assert obs(rep, "nothing_found", first)
    reasons = [u[2] for u in rep.unparsed]
    assert "kept as special: 'Duskholm'" in reasons
    odd = [u for u in rep.unparsed if u[1] == "Something odd near the River"]
    assert odd and odd[0][3]  # flagged as map-related


def test_status_line(terrain):
    rep = parse(terrain)
    st = [o for o in rep.obs if o.party == "status"]
    assert {(o.kind, o.value) for o in st} == {("terrain", "PR"), ("resource", "COAL"),
                                               ("unit_sighting", "0123e1")}  # own 0999 ignored


def test_inventory_skills_morale(terrain):
    u = parse(terrain).units[0]
    assert u.inventory["Humans"] == {"People": 3200, "Warriors": 1000, "Actives": 1100, "Inactives": 1100}
    assert u.inventory["Animals"] == {"Cattle": 50, "Goat": 370}
    assert u.skills == {"Adm": 2, "Dip": 2, "Sct": 2}
    assert u.morale == "1"


def test_legend_and_admin_lines_are_ignored(terrain):
    texts = [u[1] for u in parse(terrain).unparsed]
    assert not any(t.startswith(("PR =", "Received:")) for t in texts)


def test_unknown_terrain_is_flagged(terrain):
    rep = parse(terrain, "Tribe 0999, , Current Hex = QB 1016, (Previous Hex = N/A)\nScout 1:Scout N-XQ")
    assert obs(rep, "terrain")[0].confidence == "likely"
    assert any("unknown terrain code" in u[2] for u in rep.unparsed)


def test_newer_report_wording(terrain):
    # 906-05 wording: "move to SE", a trailing empty step, a cargo line, a mixed-case deposit on the status line
    text = ("Tribe 0999, , Current Hex = QB 1016, (Previous Hex = N/A)\n"
            "Movement Weight: 1,200 Walking Capacity: 32,700 Mounted Capacity: 12,000\n"
            "Scout 1:Scout S-PR, \SW-PR, \,Not enough M.P's to move to SE into PRAIRIE,\n"
            "0999 Status: PRAIRIE, Salt, 0999")
    rep = parse(terrain, text)
    path = rep.units[0].paths["scout1"]
    assert [s.result for s in path] == ["ok", "ok", "mp_block"] and path[-1].dir == "SE"
    assert [(o.value, o.how) for o in obs(rep, "terrain", c.label(path[-1].hex))] == [("PR", "glimpsed")]
    assert not obs(rep, "special") and not rep.unparsed
    assert [o.value for o in obs(rep, "resource")] == ["SALT"]


def test_status_line_neighbour_terrain(terrain):
    # 906-06 wording: 'Lcm N' on a status line = mountain seen in the next hex, not a special hex
    text = ("Tribe 0999, , Current Hex = QB 1016, (Previous Hex = N/A)\n"
            "0999 Status: CONIFER HILLS, Lcm N, NE, 0999")
    rep = parse(terrain, text)
    assert not obs(rep, "special") and not rep.unparsed
    seen = {(o.hex, o.value, o.how) for o in obs(rep, "terrain") if o.how == "glimpsed"}
    assert seen == {(c.step(c.parse("QB 1016"), d), "LCM", "glimpsed") for d in ("N", "NE")}


def test_the_clan_comes_from_the_first_report(cfg, terrain, sample_file):
    from tribemap import config, db, importer
    assert cfg.clan is None   # a new copy: nobody's map yet
    conn = db.connect(cfg.db_path)
    importer.import_report(conn, cfg, terrain, sample_file)
    conn.close()
    assert cfg.clan == "0999" and config.load().clan == "0999"   # kept in config.toml
