"""Write parsed reports and manual corrections into the database."""
import csv
import hashlib
import json
import time
from pathlib import Path

from . import config, coords
from .docx import read_report
from .parser import Parser

CORRECTION_FIELDS = ["added_at", "hex", "kind", "value", "dir", "source_kind", "provider",
                     "confidence", "turn", "note"]

# Tables rebuilt from reports + corrections. plan_route and share_log are kept.
DERIVED = ["unparsed", "unit_path", "unit_turn", "unit", "observation", "source"]


def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def parent_of(unit):
    """Best guess at the creating tribe: 0999e1 -> 0999, 1999 -> 0999."""
    if len(unit) > 4:
        return unit[:4]
    if unit[0] != "0":
        return "0" + unit[1:]
    return None


def _store_obs(conn, source_id, turn, o, received):
    gx, gy, d = o.hex[0], o.hex[1], None
    if o.dir:
        (gx, gy), d = coords.edge(o.hex, o.dir)
    conn.execute(
        "INSERT INTO observation (source_id, turn, unit, party, step, gx, gy, dir, kind, value, how,"
        " confidence, raw, line_no, received_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (source_id, turn, o.unit, o.party, o.step, gx, gy, d, o.kind, o.value, o.how,
         o.confidence, o.raw, o.line_no, received))


def import_report(conn, cfg, terrain, path, force=False):
    """Parse one report file into the DB. Returns (source_id, report, skipped)."""
    path = Path(path)
    digest = sha256(path)
    old = conn.execute("SELECT id FROM source WHERE sha256 = ?", (digest,)).fetchone()
    if old and not force:
        return old["id"], None, True
    if old:
        conn.execute("DELETE FROM source WHERE id = ?", (old["id"],))

    rep = Parser(terrain, cfg.clan).parse(read_report(path))
    config.learn_clan(cfg, rep.clan)   # a first report tells us whose map this is
    try:
        filename = str(path.resolve().relative_to(cfg.game_dir.resolve()))
    except ValueError:
        filename = str(path)
    cur = conn.execute(
        "INSERT INTO source (kind, turn, turn_no, filename, sha256, season, weather, next_turn, next_due,"
        " imported_at) VALUES ('own_report',?,?,?,?,?,?,?,?,?)",
        (rep.turn, rep.turn_no, filename, digest, rep.season, rep.weather, rep.next_turn,
         rep.next_due, now()))
    sid = cur.lastrowid
    received = now()
    for o in rep.obs:
        _store_obs(conn, sid, rep.turn, o, received)
    for u in rep.units:
        conn.execute("INSERT OR IGNORE INTO unit (id, kind, name, parent, first_turn) VALUES (?,?,?,?,?)",
                     (u.unit, u.kind, u.name, parent_of(u.unit), rep.turn))
        if u.name:
            conn.execute("UPDATE unit SET name = ? WHERE id = ?", (u.name, u.unit))
        h, p = u.hex or (None, None), u.prev_hex or (None, None)
        conn.execute(
            "INSERT OR REPLACE INTO unit_turn VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (u.unit, rep.turn, sid, h[0], h[1], p[0], p[1], u.hex_text, u.status,
             json.dumps(u.inventory), json.dumps(u.skills), u.morale))
        for party, steps in u.paths.items():
            for s in steps:
                conn.execute("INSERT INTO unit_path VALUES (?,?,?,?,?,?,?,?,?)",
                             (sid, u.unit, rep.turn, party, s.n, s.dir, s.hex[0], s.hex[1], s.result))
    for line_no, text, reason, mapish in rep.unparsed:
        conn.execute("INSERT INTO unparsed VALUES (?,?,?,?,?)", (sid, line_no, text, reason, int(mapish)))
    conn.commit()
    return sid, rep, False


# --- corrections.csv: manual facts and other players' information ------------

def _source_for(conn, kind, provider):
    row = conn.execute("SELECT id FROM source WHERE kind = ? AND IFNULL(provider,'') = ? AND sha256 IS NULL",
                       (kind, provider or "")).fetchone()
    if row:
        return row["id"]
    return conn.execute("INSERT INTO source (kind, provider, filename, imported_at) VALUES (?,?,?,?)",
                        (kind, provider or None, "corrections.csv", now())).lastrowid


# Our own corrections and the GM's word are verified; other players' information and guesses are not.
HOW_FOR_SOURCE = {"manual": "manual", "gm": "manual", "player": "reported", "guess": "guess"}


def _apply_correction(conn, row):
    h = coords.parse(row["hex"])
    gx, gy, d = h[0], h[1], None
    if row.get("dir"):
        (gx, gy), d = coords.edge(h, row["dir"].upper())
    kind = row.get("source_kind") or "manual"
    sid = _source_for(conn, kind, row.get("provider"))
    conn.execute(
        "INSERT INTO observation (source_id, turn, unit, party, step, gx, gy, dir, kind, value, how,"
        " confidence, raw, note, received_at) VALUES (?,?,'',?,0,?,?,?,?,?,?,?,?,?,?)",
        (sid, row.get("turn") or None, kind, gx, gy, d, row["kind"], row["value"], HOW_FOR_SOURCE[kind],
         row.get("confidence") or "confirmed", "corrections.csv", row.get("note"),
         row.get("added_at") or None))


def add_correction(conn, cfg, **row):
    """Record a manual fact: appended to corrections.csv (survives rebuilds) and inserted into the DB."""
    row = {k: (row.get(k) or "") for k in CORRECTION_FIELDS}
    row["added_at"] = now()
    coords.parse(row["hex"])  # validate before writing anything
    new = not cfg.corrections_csv.exists()
    with open(cfg.corrections_csv, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, CORRECTION_FIELDS)
        if new:
            w.writeheader()
        w.writerow(row)
    _apply_correction(conn, row)
    conn.commit()


def load_corrections(conn, cfg):
    if not cfg.corrections_csv.exists():
        return 0
    n = 0
    with open(cfg.corrections_csv, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            _apply_correction(conn, row)
            n += 1
    conn.commit()
    return n


def find_reports(cfg):
    repo = Path(__file__).resolve().parent.parent
    return sorted(p for p in cfg.game_dir.glob(cfg.report_glob)
                  if repo not in p.resolve().parents and not p.name.startswith("~$"))


def rebuild(conn, cfg, terrain):
    """Empty the derived tables and re-import every report plus corrections.csv."""
    for t in DERIVED:
        conn.execute(f"DELETE FROM {t}")
    conn.commit()
    results = [import_report(conn, cfg, terrain, p) for p in find_reports(cfg)]
    for p in sorted((cfg.data_dir / MAPS).glob("*.wxx")):   # maps given to us: other players' information
        import_map(conn, cfg, p.name, p.read_bytes())
    return results, load_corrections(conn, cfg)


# --- reports uploaded from the browser ------------------------------------------

UPLOAD_TYPES = (".docx", ".txt", ".wxx")
MAX_UPLOAD = 20 * 1024 * 1024


# --- maps: Clan Cartographer / Worldographer .wxx files ---------------------------------------------------

MAPS = "maps"   # in the data folder: every .wxx imported, so tn rebuild can bring them back


def import_map(conn, cfg, filename, data, own=False, dry_run=False):
    """A .wxx map: each hex's terrain, its rivers and the resources labelled on it.

    own: the map counts as our own information (the browser-only version, which keeps no other sources), shown as
    terrain known; our reports still win where they disagree, and a map can't say whether a hex's resources were
    checked. Otherwise it's another player's information (unverified), credited to the file's name.
    """
    from . import wxx
    name = Path(filename or "").name
    try:
        m = wxx.read(data)
    except Exception as e:   # not gzip, not UTF-16, no coordinate labels...
        raise ValueError(f"couldn't read {name} as a map: {e}")
    digest = hashlib.sha256(data).hexdigest()
    already = conn.execute("SELECT 1 FROM source WHERE sha256 = ?", (digest,)).fetchone() is not None
    info = {"file": name, "map": True, "turn": None, "own": own, "hexes": len(m["cells"]),
            "rivers": len(m["rivers"]), "already_imported": already, "skipped": already}
    if dry_run or already:
        return info
    kind, how, provider = ("map", "mapped", None) if own else ("player", "reported", Path(name).stem)
    sid = conn.execute("INSERT INTO source (kind, filename, sha256, provider, imported_at) VALUES (?,?,?,?,?)",
                       (kind, name, digest, provider, now())).lastrowid
    rows = [((gx, gy), None, "terrain", code) for (gx, gy), code in m["cells"].items()]
    rows += [(h, d, "river", "") for h, d in m["rivers"]]
    rows += [(h, None, "resource", text) for h, texts in m["notes"].items() for text in texts]
    conn.executemany(
        "INSERT INTO observation (source_id, turn, unit, party, step, gx, gy, dir, kind, value, how, confidence, raw,"
        " received_at) VALUES (?,NULL,'','map',0,?,?,?,?,?,?,'confirmed',?,?)",
        [(sid, h[0], h[1], d, k, v, how, name, now()) for h, d, k, v in rows])
    (cfg.data_dir / MAPS).mkdir(parents=True, exist_ok=True)
    (cfg.data_dir / MAPS / name).write_bytes(data)
    conn.commit()
    info["observations"] = len(rows)
    return info


def import_upload(conn, cfg, terrain, filename, data, dry_run=False, force=False, own_maps=False):
    """A report file (or a .wxx map: import_map) sent from the browser: check it, save it into its turn folder
    and import it.

    dry_run only reads it and says what importing would do (turn, units, new turn or not, plans that would be
    archived), so the page can ask before anything changes. A new turn's report archives and clears the
    plans, as `tn import` does.
    """
    from . import plans, queries
    name = Path(filename or "").name
    if not name or Path(name).suffix.lower() not in UPLOAD_TYPES:
        raise ValueError("choose a turn report (.docx, or a .txt copy) or a .wxx map")
    if len(data) > MAX_UPLOAD:
        raise ValueError("that file is too big to be a turn report or a map")
    if Path(name).suffix.lower() == ".wxx":
        return import_map(conn, cfg, name, data, own=own_maps, dry_run=dry_run)

    incoming = cfg.data_dir / "incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    tmp = incoming / name
    tmp.write_bytes(data)
    try:
        rep = Parser(terrain, cfg.clan).parse(read_report(tmp))
    except Exception as e:   # a damaged or non-Word file
        tmp.unlink(missing_ok=True)
        raise ValueError(f"couldn't read {name}: {e}")
    if not rep.turn:
        tmp.unlink(missing_ok=True)
        raise ValueError(f"{name} doesn't look like a turn report (no 'Current Turn' line)")

    latest = queries.latest_turn(conn)
    new_turn = queries.turn_key(rep.turn) > queries.turn_key(latest)
    digest = hashlib.sha256(data).hexdigest()
    already = conn.execute("SELECT 1 FROM source WHERE sha256 = ?", (digest,)).fetchone() is not None
    dest = cfg.game_dir / plans.turn_folder(rep.turn) / name
    clash = dest.exists() and hashlib.sha256(dest.read_bytes()).hexdigest() != digest
    info = {"file": name, "turn": rep.turn, "latest_turn": latest, "new_turn": new_turn,
            "units": [u.unit for u in rep.units], "already_imported": already,
            "plans_to_archive": [f"{t} {v}" for t, v in plans.variants(conn)] if new_turn else [],
            "saved_to": str(dest.relative_to(cfg.game_dir)), "replaces_different_file": clash}
    if dry_run:
        tmp.unlink(missing_ok=True)
        return info
    if clash and not force:
        tmp.unlink(missing_ok=True)
        raise ValueError(f"{info['saved_to']} already exists with different contents; tick 'replace' to overwrite it")

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp.replace(dest)
    sid, rep, skipped = import_report(conn, cfg, terrain, dest, force=force)
    archived, upcoming = [], None
    if rep and new_turn:
        done, _, upcoming = plans.start_new_turn(conn, cfg, rep.turn)
        archived = [{"plan": f"{t} {v}", "json": str(p)} for t, v, p in done]
    info.update(skipped=skipped, archived=archived, next_plan=upcoming)
    if rep:
        info.update(observations=len(rep.obs),
                    unparsed=[{"line": n, "text": t, "reason": r} for n, t, r, m in rep.unparsed if m],
                    unparsed_other=sum(1 for u in rep.unparsed if not u[3]))
    return info
