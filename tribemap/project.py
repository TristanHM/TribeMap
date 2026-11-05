"""A project file: everything one player's Tribemap holds, in one .zip, to move it to another copy or keep as a backup.

Inside: tribemap.db (a consistent snapshot), the data files that live beside it (contacts.csv, units.csv,
corrections.csv, terrain.csv, the plan archive) and project.json (the clan, when, the latest turn). Not the reports
themselves (the DB already holds what they say) and not the DB backups.
"""
import io
import json
import sqlite3
import tempfile
import time
import zipfile
from pathlib import Path

from . import db, queries

FORMAT = 1
DATA_FILES = ("contacts.csv", "units.csv", "corrections.csv", "terrain.csv")
ARCHIVE = "plan-archive"
MAX_SIZE = 200 * 1024 * 1024   # unpacked: far beyond any real project, small enough to refuse a zip bomb


def export(cfg):
    """The project as .zip bytes."""
    buf = io.BytesIO()
    with tempfile.TemporaryDirectory() as tmp:
        snap = Path(tmp) / "tribemap.db"
        src, dest = db.connect(cfg.db_path, readonly=True), sqlite3.connect(snap)
        try:
            src.backup(dest)
            latest = queries.latest_turn(src)
        finally:
            dest.close()
            src.close()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("project.json", json.dumps({"format": FORMAT, "clan": cfg.clan, "latest_turn": latest,
                                                   "saved": time.strftime("%Y-%m-%d %H:%M:%S")}, indent=1))
            z.write(snap, "tribemap.db")
            for name in DATA_FILES:
                if (cfg.data_dir / name).exists():
                    z.write(cfg.data_dir / name, f"data/{name}")
            for p in sorted((cfg.data_dir / ARCHIVE).glob("*.json")):
                z.write(p, f"data/{ARCHIVE}/{p.name}")
    return buf.getvalue()


def read(data):
    """Check a project file without changing anything. Returns (info, {name: bytes}) or raises ValueError."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        infos = z.infolist()
    except zipfile.BadZipFile:
        raise ValueError("that isn't a TribeMap data file")
    if sum(i.file_size for i in infos) > MAX_SIZE:
        raise ValueError("that data file is too big")
    allowed = {"project.json", "tribemap.db"} | {f"data/{n}" for n in DATA_FILES}
    files = {}
    for i in infos:
        name = i.filename
        archived = name.startswith(f"data/{ARCHIVE}/") and name.count("/") == 2 and name.endswith(".json") \
            and ".." not in name
        if name in allowed or archived:   # anything else (other paths, ../ tricks) is ignored
            files[name] = z.read(i)
    if "project.json" not in files or "tribemap.db" not in files:
        raise ValueError("that isn't a TribeMap data file (no project.json or tribemap.db)")
    try:
        info = json.loads(files["project.json"])
    except ValueError:
        raise ValueError("the data file is damaged (project.json)")
    if info.get("format") != FORMAT:
        raise ValueError("that data file is from a different version of TribeMap")
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "check.db"
        p.write_bytes(files["tribemap.db"])
        c = sqlite3.connect(p)
        try:
            ok = c.execute("PRAGMA integrity_check").fetchone()[0] == "ok" and \
                c.execute("SELECT count(*) FROM sqlite_master WHERE name IN ('source', 'observation', 'plan_route')"
                          ).fetchone()[0] == 3
        except sqlite3.DatabaseError:
            ok = False
        finally:
            c.close()   # before the folder goes: Windows won't delete an open file
    if not ok:
        raise ValueError("the data file's database is damaged")
    return info, files


def load(cfg, data):
    """Replace this copy's data with a project file (the current DB is backed up first). Returns its project.json.

    Plans, reports' facts, corrections, contacts, mounted units: all become the project's. The clan too.
    """
    info, files = read(data)
    if cfg.db_path.exists():
        c = db.connect(cfg.db_path, readonly=True)
        try:
            db.backup(c, cfg.backup_dir, cfg.keep_backups)
        finally:
            c.close()
    tmp = cfg.db_path.with_suffix(".loading")
    tmp.write_bytes(files["tribemap.db"])
    tmp.replace(cfg.db_path)
    db.connect(cfg.db_path).close()   # migrate it, if it's from an older schema
    for name in DATA_FILES:
        if f"data/{name}" in files:
            (cfg.data_dir / name).write_bytes(files[f"data/{name}"])
        elif name != "terrain.csv":
            (cfg.data_dir / name).unlink(missing_ok=True)   # not in the project: it had none
    for name, body in files.items():
        if name.startswith(f"data/{ARCHIVE}/"):
            (cfg.data_dir / ARCHIVE).mkdir(parents=True, exist_ok=True)
            (cfg.data_dir / ARCHIVE / name.rsplit("/", 1)[1]).write_bytes(body)
    return info
