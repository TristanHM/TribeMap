"""Project files: everything in one .zip, saved from one copy and opened in another (the browser-only version)."""
import base64
import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from tribemap import config, db, plans, project
from tribemap.web import create_app
from test_web import client  # noqa: F401  (the web app over the sample report)


def other_copy(tmp_path, monkeypatch):
    """A second, empty Tribemap (another folder and DB), like a new browser."""
    monkeypatch.setenv("TRIBEMAP_GAME_DIR", str(tmp_path / "b" / "game"))
    monkeypatch.setenv("TRIBEMAP_DATA_DIR", str(tmp_path / "b" / "data"))
    monkeypatch.setenv("TRIBEMAP_DB", str(tmp_path / "b" / "b.db"))
    return config.load()


def test_save_and_open_a_project(client, cfg, tmp_path, monkeypatch):
    plans.set_unit_mover(cfg, "0999", "mounted")
    (cfg.data_dir / "contacts.csv").write_text("clan,name\n0123,Duskholm\n", encoding="utf-8")
    r = client.get("/api/project")
    assert r.status_code == 200 and r.headers["content-disposition"] == f'attachment; filename="tribemap-{cfg.clan}-906-05.zip"'
    names = set(zipfile.ZipFile(io.BytesIO(r.content)).namelist())
    assert {"project.json", "tribemap.db", "data/units.csv", "data/contacts.csv", "data/corrections.csv"} <= names

    b = other_copy(tmp_path, monkeypatch)
    opened = []
    web = TestClient(create_app(b, project_open=opened.append), base_url="http://127.0.0.1:8507")
    assert web.get("/api/meta").json()["latest_turn"] is None
    res = web.post("/api/project", json={"data": base64.b64encode(r.content).decode()})
    assert res.status_code == 200 and res.json()["clan"] == cfg.clan and opened
    meta = web.get("/api/meta").json()
    assert meta["latest_turn"] == "906-05" and meta["unit_movers"] == {"0999": "mounted"}
    routes = web.get("/api/plan/906-06").json()["routes"]
    assert sorted((x["kind"], x["orders"]) for x in routes) == [("move", "N"), ("scout", "N"), ("scout", "N,N,N")]
    assert (b.data_dir / "contacts.csv").read_text(encoding="utf-8").startswith("clan,name")


def test_open_project_is_refused_on_a_desktop_copy(client):
    # there it would replace the live DB: only the browser-only version (which passes project_open) allows it
    data = base64.b64encode(client.get("/api/project").content).decode()
    r = client.post("/api/project", json={"data": data})
    assert r.status_code == 403 and client.get("/api/meta").json()["project_open"] is False


def test_bad_project_files(cfg):
    with pytest.raises(ValueError, match="isn't a TribeMap data file"):
        project.read(b"not a zip")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("project.json", '{"format": 1, "clan": "0999"}')
        z.writestr("tribemap.db", b"rubbish")
        z.writestr("../evil.txt", "x")
    with pytest.raises(ValueError, match="damaged"):
        project.read(buf.getvalue())
    # a path outside the allowed names is never unpacked
    db.connect(cfg.db_path).close()
    good = zipfile.ZipFile(io.BytesIO(project.export(cfg)))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n in good.namelist():
            z.writestr(n, good.read(n))
        z.writestr("data/../../evil.txt", "x")
        z.writestr("data/plan-archive/../x.json", "x")
    _, files = project.read(buf.getvalue())
    assert not any("evil" in n or ".." in n for n in files)
