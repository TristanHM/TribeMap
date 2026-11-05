"""Clan Cartographer .wxx files: what we write reads back, and the format matches Clan Cartographer's own."""
import gzip
import re

from tribemap import coords as c
from tribemap import wxx
from test_web import client  # noqa: F401  (the web app over the sample report, for the export test)

H = c.parse


def test_terrain_index_is_clan_cartographers_list():
    assert wxx.INDEX["ALPS"] == 1 and wxx.INDEX["PR"] == 23 and wxx.INDEX["LCM"] == 17 and wxx.INDEX["UW"] == 32
    names = wxx.TERRAINMAP.split("\t")[0::2]
    assert names[wxx.INDEX["PR"]] == "Flat Grazing Land" and names[wxx.INDEX["GH"]] == "Hills Grassland"


def test_round_trip():
    cells = {H("QB 1016"): "PR", H("QB 1015"): "GH", H("QB 1116"): "LCM", H("QB 1117"): "SW", H("QB 0917"): "BR"}
    rivers = [c.edge(H("QB 1016"), "N"), c.edge(H("QB 1116"), "SE")]
    out = wxx.export(cells, rivers, fords=[rivers[0]], notes={H("QB 1016"): ["Salt"]}, unscouted={H("QB 1015")})
    text = gzip.decompress(out).decode("utf-16")
    assert text.startswith("<?xml version='1.0' encoding='utf-16'?>") and 'version="1.74"' in text
    assert "TribeNet Coords" in text and ">QB 1016</label>" in text and 'type="Symbol X"' in text
    back = wxx.read(out)
    assert back["cells"] == {**cells, H("QB 0917"): "BF"}   # BR isn't in Clan Cartographer's list: the nearest
    assert back["rivers"] == sorted(rivers)
    assert back["notes"] == {H("QB 1016"): ["Salt"]}


def test_round_trip_on_an_even_column():
    # the grid must start on an odd world column whatever the hexes are, or every other column shifts
    cells = {H("QB 1216"): "PR", H("QB 1316"): "GH", H("QB 1215"): "CH"}
    assert wxx.read(wxx.export(cells))["cells"] == cells


def test_the_legend_column_is_not_ground():
    # Clan Cartographer adds a legend: sample tiles in one column under a "Legend" label
    out = gzip.decompress(wxx.export({H("QB 1016"): "PR", H("QB 1416"): "GH", H("QB 1417"): "CH"})).decode("utf-16")
    col = H("QB 1416")[0] - (H("QB 1016")[0] - 1)   # the grid starts one hex (the pad) before QB 1016
    x, _ = wxx.centre(col, 0)
    out = out.replace("</labels>", f'<label  mapLayer="Labels" style="null"><location viewLevel="WORLD" x="{x}" '
                                   f'y="0.0" scale="18.0" />Legend</label>\n</labels>')
    cells = wxx.read(gzip.compress(out.encode("utf-16")))["cells"]
    assert H("QB 1016") in cells and H("QB 1416") not in cells and H("QB 1417") not in cells


def test_export_follows_the_layers(client):
    def get(layers):
        r = client.post("/api/export-wxx", json={"layers": layers})
        assert r.status_code == 200 and r.headers["content-disposition"].endswith('.wxx"')
        return wxx.read(r.content)
    every = get(["terrain", "rivers", "resources", "others"])
    assert H("QB 2005") in every["cells"]                        # Wren's hex: others' info is on
    assert H("QB 2005") not in get(["terrain", "rivers", "resources"])["cells"]
    assert every["rivers"] and not get(["terrain", "resources", "others"])["rivers"]
    assert every["notes"] and not get(["terrain", "rivers", "others"])["notes"]
    assert all(t == t.title() for ts in every["notes"].values() for t in ts if t.isalpha())   # Salt, not SALT
    # plain hex information: none of our own marks (no scouting-status X)
    r = client.post("/api/export-wxx", json={"layers": ["terrain", "rivers", "resources", "others"]})
    assert 'type="Symbol X"' not in gzip.decompress(r.content).decode("utf-16")


def test_importing_a_map(client, cfg, tmp_path, monkeypatch):
    import base64
    from fastapi.testclient import TestClient
    from tribemap.web import create_app
    from test_project import other_copy
    data = client.post("/api/export-wxx", json={"layers": ["terrain", "rivers", "resources"]}).content
    up = {"filename": "allies.wxx", "data": base64.b64encode(data).decode()}

    # the browser-only version: a map is the player's own information, terrain known (resources not checked)
    b = other_copy(tmp_path, monkeypatch)
    web = TestClient(create_app(b, project_open=lambda info: None), base_url="http://127.0.0.1:8507")
    check = web.post("/api/import", json={**up, "dry_run": True}).json()
    assert check["map"] and check["hexes"] > 5 and check["own"] and not check["already_imported"]
    assert web.post("/api/import", json=up).json()["observations"] > check["hexes"]
    hexes = {h["id"]: h for h in web.get("/api/map").json()["hexes"]}
    assert hexes["QB 1016"]["terrain"] == "PR" and hexes["QB 1016"]["verified"]
    d = web.get("/api/hex/QB 1016").json()
    assert d["knowledge"] == "terrain" and d["terrain_how"] == "mapped"
    assert web.post("/api/import", json=up).json()["skipped"]   # the same file twice: nothing changes

    # a desktop copy: someone else's map, unverified and credited to the file; our own report still wins
    r = client.post("/api/import", json=up).json()
    assert r["map"] and not r["own"]
    d = client.get("/api/hex/QB 1016").json()
    assert d["knowledge"] == "full" and any(u["provider"] == "allies" for u in d["unverified"])
    assert (cfg.data_dir / "maps" / "allies.wxx").exists()   # kept for tn rebuild
