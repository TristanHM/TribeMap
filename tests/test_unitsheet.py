"""The Planner tab's unit sheets (unitsheet.py): the made-up clan 0999 from the sample report."""
import pytest

from tribemap import coords, db, importer, plans, unitsheet


@pytest.fixture
def conn(cfg, terrain, sample_file):
    conn = db.connect(cfg.db_path)
    importer.import_report(conn, cfg, terrain, sample_file)
    return conn


def test_herders_add_up_then_round_once():
    total, shares = unitsheet.herders_needed({"Goat": 29, "Cattle": 22, "Horse": 13})
    assert total == 5 and shares == {"Goat": 1.4, "Cattle": 2.2, "Horse": 1.3}
    assert unitsheet.herders_needed({"Goat": 200, "Horse": 100})[0] == 20


def test_calendar():
    cal = unitsheet.calendar("906-08")
    assert (cal["month"], cal["winter"], cal["winter_in"]) == (8, False, 2)
    by = {e["name"]: e for e in cal["events"]}
    assert by["Tinker"]["in_turns"] == 0 and by["Fair"]["in_turns"] == 2 and by["Seeking"]["month"] == 1
    assert unitsheet.calendar("906-11")["winter"]


def test_sheet_from_report_and_plan(conn, cfg, terrain):
    plans.add(conn, "906-06", "0999", "move", "N")
    plans.add(conn, "906-06", "0999", "scout", "N,N")
    d = unitsheet.sheets(conn, terrain, cfg, "906-06")
    assert d["report_turn"] == "906-05" and [u["unit"] for u in d["units"]] == ["0999"]
    u = d["units"][0]
    assert u["where"]["hex"] == "QB 1016" and u["where"]["resources"] == ["COAL"]
    assert u["people"]["Actives"] == 1100 and u["carry"]["walking"] == 120000
    assert u["herders"]["total"] == 24 and u["herders"]["last_turn"] == {"allocated": 22, "required": 22}
    assert u["plan"]["move"]["end"] == "QB 1015" and len(u["plan"]["scouts"]) == 1
    assert u["last_turn"]["transfers"] and u["last_turn"]["notes"]
    texts = [c["text"] for c in u["checks"]]
    assert any("3,200 people: not enough to eat" in t for t in texts)   # no provs at all
    assert not any("herders" in t for t in texts)


def test_report_herding_uses_reported_requirement(conn, cfg, terrain):
    conn.execute("UPDATE unit_turn SET activities = '10 herders allocated, 12 actual herders required'")
    unit = unitsheet.sheets(conn, terrain, cfg, "906-06")["units"][0]
    assert unit["herders"]["total"] == 24
    checks = [c for c in unit["checks"] if "herders" in c["text"]]
    assert len(checks) == 1 and "assigned 10 herders; 12 were required" in checks[0]["text"]


def test_yields_use_last_three_reports_and_starting_skill(conn, cfg, terrain):
    from conftest import SAMPLE

    for month in (6, 7, 8, 9):
        report = SAMPLE.replace("Current Turn 906-05", f"Current Turn 906-{month:02}")
        report = report.replace("Adm 2, Dip 2, Sct 2,", f"Adm 2, Dip 2, Sct 2, Hunt {month},")
        report = report.replace("22 herders allocated", "20 performed furrier for 6 provs, 2 furs, 4 skins, "
                                "4 people cut 16 logs, 22 herders allocated")
        file = cfg.game_dir / f"0999_906_{month:02}_Results.txt"
        file.write_text(report, encoding="utf-8")
        importer.import_report(conn, cfg, terrain, file)
    sheet = unitsheet.sheets(conn, terrain, cfg, "906-09")
    observed = sheet["activity_observations"]
    assert [(r["turn"], r["level"]) for r in observed if r["activity"] == "HUNTING"] == [
        ("906-06", 0), ("906-07", 6), ("906-08", 7),
    ]
    assert all(r["goods"] == {"PROVS": 6, "FUR": 2, "SKIN": 4} and r["workers"] == 20
               for r in observed if r["activity"] == "FURRIER")
    assert any(r["activity"] == "FORESTRY" and r["goods"] == {"LOG": 16} and r["workers"] == 4 for r in observed)


def test_weight_and_new_unit_checks(conn, cfg, terrain):
    conn.execute("UPDATE unit_turn SET carry_json = '{\"weight\": 130000, \"walking\": 120000, \"mounted\": 0}'")
    plans.add(conn, "906-06", "0999", "move", "N")
    plans.add(conn, "906-06", "0999e1", "move", "S", start="QB 1016")
    units = {u["unit"]: u for u in unitsheet.sheets(conn, terrain, cfg, "906-06")["units"]}
    assert any(c["level"] == "warn" and "won't move" in c["text"] for c in units["0999"]["checks"])
    assert units["0999e1"]["new"] and any("creation turn" in c["text"] for c in units["0999e1"]["checks"])


def test_planner_endpoint(cfg, sample_file, terrain):
    from fastapi.testclient import TestClient
    from tribemap.web import create_app
    c = db.connect(cfg.db_path)
    importer.import_report(c, cfg, terrain, sample_file)
    c.close()
    r = TestClient(create_app(cfg), base_url="http://127.0.0.1:8507").get("/api/planner/906-06")
    assert r.status_code == 200 and r.json()["units"][0]["unit"] == "0999"
    assert r.json()["skill_info"]["Eco"]["group"] == "B"   # available in the dropdown at level zero
    assert any(row["activity"] == "MINING" and row["item"] == "SALT" for row in r.json()["activity_info"])


def test_goods_history_ends_at_the_plans_starting_report(conn, cfg, terrain, sample_file):
    original = sample_file.read_text(encoding="utf-8")
    older = cfg.game_dir / "0999_906_04_Results.txt"
    newer = cfg.game_dir / "0999_906_06_Results.txt"
    older.write_text(original.replace("Current Turn 906-05", "Current Turn 906-04").replace("370", "300"),
                     encoding="utf-8")
    newer.write_text(original.replace("Current Turn 906-05", "Current Turn 906-06").replace("370", "410"),
                     encoding="utf-8")
    for path in (newer, older):
        importer.import_report(conn, cfg, terrain, path)
    history = unitsheet.sheets(conn, terrain, cfg, "906-06")["units"][0]["goods_history"]
    assert [(point["turn"], point["goods"]["Goat"]) for point in history] == [("906-04", 300), ("906-05", 370)]
    assert all("Warriors" not in point["goods"] for point in history)


def test_transfer_locations_and_river_requirements():
    units = [
        {"unit": "0999", "where": {"hex": "QB 1016"}, "inventory": {"Structures": {"Meeting House": 1}},
         "transfer_hexes": {"BM": "QB 1016", "AM": "QB 1015"}},
        {"unit": "1999", "where": {"hex": "QB 1016"}, "inventory": {},
         "transfer_hexes": {"BM": "QB 1016", "AM": "QB 1017"}},
        {"unit": "0999e1", "where": {"hex": "QB 1017"}, "inventory": {},
         "transfer_hexes": {"BM": "QB 1017", "AM": "QB 1015"}},
        {"unit": "0999e2", "where": {"hex": "QB 1015"}, "inventory": {},
         "transfer_hexes": {"BM": "QB 1015", "AM": "QB 1014"}},
    ]
    river_side = coords.edge(coords.parse("QB 1016"), "S")
    edges = {river_side: {"river": True}}
    unitsheet.transfer_neighbours(units, edges)
    main = units[0]["transfer_neighbours"]
    assert main["BM"]["1999"]["kind"] == "Same hex"
    assert main["BM"]["0999e2"]["kind"] == "Adjacent · Meeting House"
    assert "ford or ferry" in main["BM"]["0999e1"]["requirements"][0]
    assert main["AM"]["0999e1"]["kind"] == "Same hex"
    assert "Meeting House" in main["AM"]["0999e2"]["requirements"][0]
    assert "1999" not in main["AM"]
    edges[river_side]["ford"] = True
    unitsheet.transfer_neighbours(units, edges)
    assert not units[0]["transfer_neighbours"]["BM"]["0999e1"]["requirements"]


def test_goods_tribe_is_preserved_for_the_planner(cfg, terrain, sample_file):
    contents = sample_file.read_text(encoding="utf-8")
    sample_file.write_text(contents.replace("Chief,", "Goods Tribe: 1999\nChief,", 1), encoding="utf-8")
    conn = db.connect(cfg.db_path)
    importer.import_report(conn, cfg, terrain, sample_file)
    unit = unitsheet.sheets(conn, terrain, cfg, "906-06")["units"][0]
    assert unit["goods_tribe"] == "1999"


def test_goods_weights_and_transfers():
    assert unitsheet.weight_of({"Provs": 90}) == 900 and unitsheet.weight_of({"Goat": 50, "Skin": 3}) == 6
    assert unitsheet.weight_of({"Unobtainium": 1}) is None
    t = unitsheet.transferred(["Transfer goods to 0999e1: 10 WARRIORS, 12 HORSE, 100 PROVS, "
                               "Paid to Skills Teacher: 1200 SILVER", "Receive goods from 0998: 5 SKIN"])
    assert t == {"Horse": -12, "Provs": -100, "Silver": -1200, "Skin": 5}   # people left out


def test_predict_repeats_last_turns_activities():
    prev = {"Finished Goods": {"Provs": 1000}, "Raw Materials": {"Skin": 10}}
    cur = {"Finished Goods": {"Provs": 1100}, "Raw Materials": {"Skin": 10}}
    # 100 provs were sent away, so the activities made 200 more than were eaten
    p = unitsheet.predict(prev, cur, ["Transfer goods to 0999e1: 100 PROVS"])
    assert p["change"] == {"Provs": 200} and p["weight"] == 1300 * 10 + 10 * 2
    assert unitsheet.predict(None, cur, []) is None


def test_goods_info():
    g = unitsheet.goods_info("Trap")
    assert g["weight"] == 1 and set(g["tool_for"]) == {"Furrier", "Hunting"}
    assert g["recipes"] == ["Metalwork 2: 1 Iron + 4 Coal, 1 worker -> 1 Trap (Mandate 13.1.21)"]
    leather = unitsheet.goods_info("Leather")
    assert any(u.startswith("Leatherwork: ") and "Jerkins (4)" in u for u in leather["used_in"])
    assert any("Tanning 1: 4 Skin + 10 Bark" in r for r in leather["recipes"])
    assert all(n["source"] for n in unitsheet.goods_info("Skin")["notes"])


def test_recipes_file():
    import csv
    from tribemap.config import PACKAGE_DATA
    rows = list(csv.DictReader(open(PACKAGE_DATA / "recipes.csv", encoding="utf-8")))
    names = {r["goods"] for r in csv.DictReader(open(PACKAGE_DATA / "goods.csv", encoding="utf-8"))}
    assert len(rows) > 200 and all(r["section"] for r in rows)
    for r in rows:   # every input is a good the orders workbook knows
        for part in filter(None, r["inputs"].split("; ")):
            assert part.rsplit(" ", 1)[0] in names, (r["item"], part)
    mace = [r["inputs"] for r in rows if r["item"] == "Mace"]
    assert sorted(mace) == ["COAL 20; BRONZE 6", "COAL 30; IRON 6"]   # iron or bronze, not both
