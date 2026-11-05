"""SQLite storage. The database is a rebuildable cache: reports + corrections.csv are the source of truth.

Hexes are stored as world-grid integers (gx, gy); see coords.py. Hexsides are stored canonically
(hex, dir) with dir in N/NE/SE.
"""
import sqlite3
import time
from pathlib import Path

SCHEMA_VERSION = 4

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS source (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,              -- own_report, player, gm, manual, guess
    turn TEXT,                       -- '906-04'
    turn_no INTEGER,                 -- the GM's running turn number (#76)
    filename TEXT,
    sha256 TEXT UNIQUE,
    provider TEXT,                   -- who it came from (for player sources)
    season TEXT, weather TEXT, next_turn TEXT, next_due TEXT,
    imported_at TEXT NOT NULL
);

-- Append-only fact log. Never edited: corrections are new 'manual' rows.
CREATE TABLE IF NOT EXISTS observation (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES source(id) ON DELETE CASCADE,
    turn TEXT,
    unit TEXT,                       -- observing unit, '' for manual
    party TEXT,                      -- scout3, move, status, manual
    step INTEGER,
    gx INTEGER NOT NULL, gy INTEGER NOT NULL,
    dir TEXT,                        -- hexside (canonical N/NE/SE) for river/ford/no_ford
    kind TEXT NOT NULL,              -- terrain, river, ford, no_ford, resource, item, special, unit_sighting, nothing_found
    value TEXT,
    how TEXT,                        -- entered, glimpsed, status, moved, manual
    confidence TEXT,                 -- confirmed, likely, guess
    raw TEXT, line_no INTEGER, note TEXT,
    received_at TEXT                 -- when we got the information (turn = when it was true)
);
CREATE INDEX IF NOT EXISTS obs_hex ON observation(gx, gy);
CREATE INDEX IF NOT EXISTS obs_kind ON observation(kind);

CREATE TABLE IF NOT EXISTS unit (
    id TEXT PRIMARY KEY, kind TEXT, name TEXT, parent TEXT, first_turn TEXT
);
CREATE TABLE IF NOT EXISTS unit_turn (
    unit TEXT NOT NULL, turn TEXT NOT NULL, source_id INTEGER REFERENCES source(id) ON DELETE CASCADE,
    gx INTEGER, gy INTEGER, prev_gx INTEGER, prev_gy INTEGER, hex_text TEXT,
    status TEXT, inventory_json TEXT, skills_json TEXT, morale TEXT,
    PRIMARY KEY (unit, turn)
);
CREATE TABLE IF NOT EXISTS unit_path (
    source_id INTEGER REFERENCES source(id) ON DELETE CASCADE,
    unit TEXT, turn TEXT, party TEXT, step INTEGER, dir TEXT, gx INTEGER, gy INTEGER, result TEXT
);

-- Lines the parser could not place; mapish = looks map-related, review these first.
CREATE TABLE IF NOT EXISTS unparsed (
    source_id INTEGER REFERENCES source(id) ON DELETE CASCADE,
    line_no INTEGER, text TEXT, reason TEXT, mapish INTEGER
);

-- Planning (phase 2+): planned unit moves and scout routes.
CREATE TABLE IF NOT EXISTS plan_route (
    id INTEGER PRIMARY KEY,
    turn TEXT NOT NULL, unit TEXT NOT NULL,
    kind TEXT NOT NULL,              -- move, scout
    party INTEGER,                   -- scout party 1-8
    orders TEXT NOT NULL,            -- comma-separated: N,NE,FRR
    scouts INTEGER, horses INTEGER, mission TEXT,
    colour TEXT, note TEXT, updated_at TEXT,
    start_hex TEXT,                  -- override the start (e.g. a tribe created this turn)
    mover TEXT,                      -- scout, mounted, foot
    variant TEXT NOT NULL DEFAULT 'main'   -- alternative plans for the same turn: main, B, C, ...
);

-- Every plan (turn + variant), so one can exist before it has any routes (e.g. a new turn's empty plan).
CREATE TABLE IF NOT EXISTS plan (
    turn TEXT NOT NULL, variant TEXT NOT NULL DEFAULT 'main', note TEXT,
    PRIMARY KEY (turn, variant)
);

-- Sharing (phase 5): what was given to whom.
CREATE TABLE IF NOT EXISTS share_log (
    id INTEGER PRIMARY KEY, recipient TEXT, given_at TEXT, region TEXT, turn_cutoff TEXT,
    layers TEXT, filename TEXT, sha256 TEXT, watermark TEXT, note TEXT
);
"""


def connect(path, readonly=False):
    path = Path(path)
    if readonly:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(path)
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
        conn.commit()
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")  # must run outside a transaction, or it's ignored
    return conn


def _migrate(conn):
    """Bring an older database up to the current schema (columns added since version 1)."""
    added = {"observation": ["received_at TEXT"], "plan_route": ["start_hex TEXT", "mover TEXT", "variant TEXT NOT NULL DEFAULT 'main'"]}
    for table, columns in added.items():
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for col in columns:
            if col.split()[0] not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col}")


def backup(conn, backup_dir, keep=20):
    """Snapshot the live DB into backup_dir as a finished file, keeping the newest `keep`."""
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / time.strftime("tribemap-%Y%m%d-%H%M%S.db")
    tmp = target.with_suffix(".tmp")
    dest = sqlite3.connect(tmp)
    with dest:
        conn.backup(dest)
    dest.close()
    tmp.replace(target)
    for old in sorted(backup_dir.glob("tribemap-*.db"))[:-keep]:
        old.unlink()
    return target
