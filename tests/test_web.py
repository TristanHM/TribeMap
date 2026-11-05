import pytest
from fastapi.testclient import TestClient

from tribemap import db, importer, plans
from tribemap.web import create_app


@pytest.fixture
def client(cfg, terrain, sample_file):
    conn = db.connect(cfg.db_path)
    importer.import_report(conn, cfg, terrain, sample_file)
    importer.add_correction(conn, cfg, hex="QB 2005", kind="terrain", value="GH", source_kind="player",
                            provider="Wren", turn="906-04")
    plans.add(conn, "906-06", "0999", "move", "N")
    plans.add(conn, "906-06", "0999", "scout", "N,N,N")
    plans.add(conn, "906-06", "0999", "scout", "N")
    conn.close()
    return TestClient(create_app(cfg), base_url="http://127.0.0.1:8507")


def test_page_and_static(client):
    r = client.get("/")
    assert r.status_code == 200 and "TribeMap" in r.text
    assert client.get("/static/app.js").status_code == 200


def test_meta(client):
    m = client.get("/api/meta").json()
    assert m["latest_turn"] == "906-05" and m["turns"] == ["906-05"] and m["plan_turns"] == ["906-06"]
    assert any(t["code"] == "PR" and t["colour"] for t in m["terrain"])
    # movement cost for the hover text: where it comes from, and what can't be entered
    t = {x["code"]: x for x in m["terrain"]}
    assert t["PR"]["mp"] == 3 and t["PR"]["mp_source"] == "mandate 10.5" and not t["PR"]["impassable"]
    assert t["O"]["impassable"]
    assert m["unit_names"] == {}   # the sample tribe has no name; named units are listed {id: name}
    # the world grid: sheet rows A-V x columns A-P, each with its climate band
    w = {sh["name"]: sh for sh in m["world"]["sheets"]}
    assert len(w) == 22 * 16 and "VP" in w and "WA" not in w
    assert (w["AA"]["climate"], w["HC"]["climate"], w["LC"]["climate"], w["VA"]["climate"]) ==         ("Cold", "Tropical", "Temp", "Cool")


def test_map(client):
    m = client.get("/api/map").json()
    by = {h["id"]: h for h in m["hexes"]}
    assert by["QB 0916"]["notes"] == ["SALT"] and by["QB 0916"]["verified"]
    assert by["QB 0913"]["how"] == "glimpsed"
    assert by["QB 2005"]["verified"] is False and by["QB 2005"]["terrain"] == "GH"
    assert [u["unit"] for u in m["units"]] == ["0999"]
    assert any(s["kinds"] == ["ford", "river"] for s in m["sides"])
    assert {p["party"] for p in m["paths"]} >= {"move", "scout1"}
    assert [s["name"] for s in m["sheets"]] == ["QB"] and m["borders"] == []   # sample stays inside one sheet
    h = by["QB 1016"]
    assert isinstance(h["x"], float) and isinstance(h["y"], float)
    # as of 906-04 only Wren's 906-04 report exists: one hex plus its unknown ring, and no units yet
    early = client.get("/api/map?as_of=906-04").json()
    assert {x["terrain"] for x in early["hexes"]} == {"GH", None} and len(early["hexes"]) == 7
    assert early["units"] == []


def test_hex_and_plan(client):
    d = client.get("/api/hex/QB 0916").json()
    assert d["resources"] == ["SALT"] and d["history"]
    assert client.get("/api/hex/0999").json()["hex"] == "QB 1016"
    assert client.get("/api/hex/nowhere").status_code == 400
    p = client.get("/api/plan/906-06").json()
    names = [r["name"] for r in p["routes"]]
    assert names == ["0999 move", "0999 scout 1", "0999 scout 2"]
    assert p["routes"][1]["points"][0] == p["routes"][0]["points"][-1]   # scouts leave from the move's end
    # both parties' first step is QB 1014: shared first steps aren't flagged as overlap
    assert p["overlap"] == [] and p["coverage"]["shared_first_steps"] == 1


# --- phase 4: editing plans from the browser ------------------------------------

def preview(client, **body):
    return client.post("/api/plan/906-06/preview", json=body)


def test_preview_draft_does_not_save(client):
    r = preview(client, draft={"unit": "0999", "kind": "scout", "orders": ["S", "S"]})
    assert r.status_code == 200
    p = r.json()
    draft = [x for x in p["routes"] if x["draft"]]
    assert len(draft) == 1 and draft[0]["name"] == "0999 scout 3"     # next free party
    assert p["draft"]["party"] == 3 and len(p["draft"]["next"]) == 6
    assert len(client.get("/api/plan/906-06").json()["routes"]) == 3   # still only the saved ones


def test_preview_extend_to_and_next_costs(client):
    # scouts start after the move (QB 1015); clicking QB 1012 adds a straight line N,N,N
    p = preview(client, draft={"unit": "0999", "kind": "scout", "orders": []}, extend_to="QB 1012").json()
    assert p["draft"]["orders"] == "N,N,N"
    nxt = {n["dir"]: n for n in p["draft"]["next"]}   # route used 6+6+3 = 15: out of MP
    assert p["draft"]["next"] == [] or all(n["result"] == "mp_block" for n in nxt.values())
    p = preview(client, draft={"unit": "0999", "kind": "scout", "orders": ["N"]}).json()
    nxt = {n["dir"]: n for n in p["draft"]["next"]}
    assert nxt["N"]["hex"] == "QB 1013" and nxt["N"]["cost"] == 6 and nxt["N"]["mp_left"] == 3
    assert nxt["S"]["result"] == "ok"


def test_preview_errors(client):
    assert preview(client, draft={"unit": "nope", "kind": "scout"}).status_code == 400
    assert preview(client, draft={"unit": "0999", "kind": "scout", "orders": ["UP"]}).status_code == 400
    r = preview(client, draft={"unit": "0999", "kind": "scout", "orders": ["NW", "FRR"]}, extend_to="QB 1010")
    assert r.status_code == 400 and "follow" in r.json()["detail"]
    r = preview(client, draft={"unit": "0999", "kind": "scout", "orders": []}, extend_to="QB 1001")
    assert r.status_code == 400   # 14 steps: more than the 9 the sheet allows


def test_save_edit_replace_and_delete(client):
    r = client.post("/api/plan/906-06/route", json={"draft": {"unit": "0999", "kind": "scout", "orders": ["S"]}})
    assert r.status_code == 200 and r.json()["party"] == 3
    # editing party 3 and moving it to party 5 replaces it rather than duplicating it
    r = client.post("/api/plan/906-06/route", json={
        "draft": {"unit": "0999", "kind": "scout", "party": 5, "orders": ["S", "S"], "mission": "locate"},
        "replace": ["0999", "scout", 3]})
    assert r.status_code == 200
    routes = {x["name"]: x for x in client.get("/api/plan/906-06").json()["routes"]}
    assert "0999 scout 3" not in routes and routes["0999 scout 5"]["orders"] == "S,S"
    assert routes["0999 scout 5"]["mission"] == "LOCATE"
    r = client.delete("/api/plan/906-06/route?unit=0999&kind=scout&party=5", headers={"X-Tribemap": "1"})
    assert r.json() == {"removed": 1, "dropped_scouts": 0}
    assert client.post("/api/plan/906-06/route", json={"draft": {"unit": "0999", "kind": "move"}}).status_code == 400


def test_new_unit_in_a_new_plan(client):
    r = client.post("/api/plan/906-07/route", json={"draft": {"unit": "1999", "kind": "move", "orders": ["NE"],
                                                               "start": "qb 1016", "mover": "mounted"}})
    assert r.status_code == 200
    p = client.get("/api/plan/906-07").json()
    assert p["routes"][0]["end"] == "QB 1116" and p["routes"][0]["mp"] == 27
    assert "906-07" in client.get("/api/meta").json()["plan_turns"]


def test_map_padding_and_extra(client):
    base = len(client.get("/api/map").json()["hexes"])
    wide = client.get("/api/map?pad=3&extra=QB 2510").json()["hexes"]
    assert len(wide) > base and "QB 2510" in {h["id"] for h in wide}


def test_export_from_browser(client, cfg):
    r = client.post("/api/plan/906-06/export", json={})
    assert r.status_code == 200 and r.json()["xlsx"].endswith("0999_906_6_PlanPaste.xlsx")


def test_writes_only_from_this_page(client):
    body = {"draft": {"unit": "0999", "kind": "scout", "orders": ["S"]}}
    assert client.post("/api/plan/906-06/route", json=body,
                       headers={"Origin": "http://evil.example"}).status_code == 403
    assert client.post("/api/plan/906-06/route", content='{"draft":{}}',
                       headers={"Content-Type": "text/plain"}).status_code == 415
    assert client.delete("/api/plan/906-06/route?unit=0999&kind=move").status_code == 403
    assert client.get("/api/meta", headers={"Host": "attacker.example"}).status_code == 403
    assert client.post("/api/plan/906-06/route", json=body,
                       headers={"Origin": "http://127.0.0.1:8507"}).status_code == 200


def test_terrain_icons(client):
    from tribemap.icons import ICONS
    from tribemap.terrain import TerrainTable
    from tribemap import config
    codes = set(TerrainTable(config.PACKAGE_DATA / "terrain.csv").by_code)
    assert codes <= set(ICONS), f"terrain codes without an icon: {codes - set(ICONS)}"
    icons = client.get("/api/meta").json()["icons"]
    for code, shapes in icons.items():
        for sh in shapes:
            pts = sh.get("pts") or [sh["c"]]
            # every icon stays inside its hex (with a margin) and clear of the terrain code at the bottom
            assert all(abs(x) < 0.9 - abs(y) / 3 ** 0.5 and -0.3 < y < 0.55 for x, y in pts), code


def test_autoscout_and_move_change_from_browser(client):
    r = client.post("/api/plan/906-06/autoscout", json={"unit": "0999"})
    assert r.status_code == 200 and len(r.json()["added"]) == 6   # 2 parties already planned
    # previewing a changed move warns how many scout routes saving would remove
    p = client.post("/api/plan/906-06/preview", json={"draft": {"unit": "0999", "kind": "move", "orders": ["S"]},
                                                      "replace": ["0999", "move", None]}).json()
    assert p["draft"]["drops_scouts"] == 8
    r = client.post("/api/plan/906-06/route", json={"draft": {"unit": "0999", "kind": "move", "orders": ["S"]},
                                                    "replace": ["0999", "move", None]})
    assert r.json()["dropped_scouts"] == 8
    assert [x["kind"] for x in client.get("/api/plan/906-06").json()["routes"]] == ["move"]


def test_import_report_from_the_page(client, cfg):
    import base64
    from conftest import SAMPLE
    newer = SAMPLE.replace("906-05 (#77)", "906-06 (#78)").replace("906-06 (#78), 18/11", "906-07 (#79), 18/12")
    body = {"filename": "0999_906_06_Results.txt", "data": base64.b64encode(newer.encode()).decode()}
    # the check reads it without changing anything
    d = client.post("/api/import", json={**body, "dry_run": True}).json()
    assert d["turn"] == "906-06" and d["new_turn"] and d["units"] == ["0999"]
    from pathlib import PureWindowsPath
    assert d["plans_to_archive"] == ["906-06 main"]
    assert PureWindowsPath(d["saved_to"]).as_posix() == "906-6/0999_906_06_Results.txt"
    assert not (cfg.game_dir / "906-6" / "0999_906_06_Results.txt").exists()
    # importing saves it in the turn folder, loads it, and archives + clears the plans
    d = client.post("/api/import", json=body).json()
    assert d["observations"] > 0 and [a["plan"] for a in d["archived"]] == ["906-06 main"]
    assert (cfg.game_dir / "906-6" / "0999_906_06_Results.txt").exists()
    assert client.get("/api/meta").json()["latest_turn"] == "906-06"
    # what happened in 906-06 becomes that turn's plan, and 906-07 starts empty
    assert client.get("/api/meta").json()["plans"] == [{"turn": "906-06", "variant": "main"},
                                                       {"turn": "906-07", "variant": "main"}]
    assert d["next_plan"] == "906-07"
    assert any("Duskholm" in u["text"] for u in d["unparsed"])     # unplaced lines come back for review
    # junk is refused
    bad = {"filename": "notes.txt", "data": base64.b64encode(b"hello").decode()}
    assert client.post("/api/import", json=bad).status_code == 400
    assert client.post("/api/import", json={"filename": "x.exe", "data": ""}).status_code == 400


def test_special_hexes_are_flagged(client):
    by = {h["id"]: h for h in client.get("/api/map").json()["hexes"]}
    # the sample's scout 5 found "Duskholm" and "Mercenary Aldo Brenn" in QB 1117
    assert by["QB 1117"]["specials"] == ["Duskholm", "Mercenary Aldo Brenn"]
    assert by["QB 1016"]["specials"] == [] and by["QB 1016"]["resources"] == ["COAL"]
    assert by["QB 0916"]["resources"] == ["SALT"]


def test_people_i_know(client, cfg):
    from tribemap.cli import main
    main(["contact", "add", "0123", "Aldric", "--email", "m@example.com", "--relation", "truce"])
    main(["contact", "at", "0123", "QB 2710", "--unit", "1123", "--turn", "906-05"])   # their word, far away
    people = {p["unit"]: p for p in client.get("/api/map").json()["people"]}
    # our report's sightings (verified) and what the contact told us (unverified), named from contacts.csv
    assert people["0123"]["verified"] and people["0123"]["name"] == "Aldric" and people["0123"]["clan"] == "0123"
    assert people["0123e1"]["name"] == "Aldric"
    assert people["1123"]["verified"] is False and people["1123"]["provider"] == "Aldric"
    assert people["1123"]["hex"] == "QB 2710" and people["1123"]["turn"] == "906-05"
    # a hex known only from their word isn't drawn as map ground (it would only stretch the view)
    assert "QB 2710" not in {h["id"] for h in client.get("/api/map").json()["hexes"]}
    # updating a contact keeps what isn't given again
    main(["contact", "add", "0123", "--discord", "aldric_tn"])
    from tribemap import queries
    assert queries.contacts(cfg)["0123"] == {"name": "Aldric", "email": "m@example.com", "discord": "aldric_tn",
                                             "relation": "truce", "truce": "", "note": ""}
    # a declared truce shows them blue on the map
    assert not people["1123"]["truce"]
    main(["contact", "add", "0123", "--truce"])
    assert all(p["truce"] for p in client.get("/api/map").json()["people"] if p["clan"] == "0123")


def test_people_positions_move_with_new_information(client, cfg, sample_file):
    from tribemap.cli import main
    where = lambda: {p["unit"]: p for p in client.get("/api/map").json()["people"]}["0123"]
    first = where()
    assert first["verified"] and first["turn"] == "906-05" and first["age"] == 0
    # an older word from them doesn't move them; a newer one does
    main(["contact", "at", "0123", "QB 1520", "--turn", "906-04"])
    assert where()["hex"] == first["hex"]
    main(["contact", "at", "0123", "QB 1520", "--turn", "906-06"])
    assert where()["hex"] == "QB 1520" and not where()["verified"]
    # a newer report of ours that sights them somewhere else moves them again (and counts as seen)
    newer = sample_file.with_name("0999_906_07_Results.txt")
    newer.write_text(sample_file.read_text(encoding="utf-8").replace("906-06 (#78), 18/11", "906-08 (#80), 18/01")
                     .replace("906-05 (#77)", "906-07 (#79)").replace("Scout 5:Scout SE-BF", "Scout 5:Scout S-BF"),
                     encoding="utf-8")
    main(["import", str(newer)])
    now = where()
    assert now["turn"] == "906-07" and now["verified"] and now["hex"] not in ("QB 1520", first["hex"])


def test_position_known_only_to_the_sheet(client):
    from tribemap.cli import main
    main(["contact", "add", "0123", "Aldric"])
    main(["contact", "at", "0123", "rb", "--unit", "2123", "--turn", "906-05"])
    p = {q["unit"]: q for q in client.get("/api/map").json()["people"]}["2123"]
    assert p["hex"] == "RB 1511" and p["approx"] and p["note"] == "somewhere on sheet RB"
    assert not {q["unit"]: q for q in client.get("/api/map").json()["people"]}["0123"]["approx"]


def test_history_tracks(client, cfg, sample_file):
    from tribemap.cli import main
    newer = sample_file.with_name("0999_906_06_Results.txt")
    newer.write_text(sample_file.read_text(encoding="utf-8").replace("906-06 (#78), 18/11", "906-07 (#79), 18/12")
                     .replace("906-05 (#77)", "906-06 (#78)").replace("QB 1016, (Previous Hex = QB 1017)",
                                                                      "QB 1015, (Previous Hex = QB 1016)"),
                     encoding="utf-8")
    main(["import", str(newer)])
    main(["contact", "at", "0123", "QB 1520", "--turn", "906-07"])   # their word, after we last saw them
    tracks = {t["unit"]: t for t in client.get("/api/history").json()["tracks"]}
    ours = tracks["0999"]
    assert ours["own"] and [(x["turn"], x["hex"]) for x in ours["turns"]] == [("906-05", "QB 1016"), ("906-06", "QB 1015")]
    assert len(ours["turns"][1]["path"]) == 2   # from the hex it left to where it ended
    theirs = tracks["0123"]
    assert not theirs["own"] and [(x["turn"], x["verified"]) for x in theirs["turns"]] ==         [("906-05", True), ("906-06", True), ("906-07", False)]
    # only the turns asked for
    tracks = {t["unit"]: t for t in client.get("/api/history?first=906-06&last=906-06").json()["tracks"]}
    assert [x["turn"] for x in tracks["0999"]["turns"]] == ["906-06"]
    assert [x["turn"] for x in tracks["0123"]["turns"]] == ["906-06"]


def test_export_as_a_download(client, cfg):
    # the browser-only version has no turn folders: the workbook comes back as a file instead
    r = client.get("/api/plan/906-06/paste-in")
    assert r.status_code == 200 and r.content[:2] == b"PK"
    assert r.headers["content-disposition"] == 'attachment; filename="0999_906_6_PlanPaste.xlsx"'
    assert not (cfg.game_dir / "906-6" / "0999_906_6_PlanPaste.xlsx").exists()
