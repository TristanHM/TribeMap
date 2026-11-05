"""The terrain table: codes, names, colours and movement costs (terrain.csv, editable)."""
import csv
from dataclasses import dataclass
from pathlib import Path

DEFAULTS = Path(__file__).resolve().parent / "data" / "terrain.csv"   # the table shipped with TribeMap


@dataclass
class Terrain:
    code: str
    name: str
    status_name: str
    colour: str
    mp: int | None
    mp_source: str
    notes: str


class TerrainTable:
    def __init__(self, path):
        self.by_code = {}
        self.by_status = {}
        self._read(path)
        if Path(path).resolve() != DEFAULTS:
            # a copy made from an older TribeMap: costs it lacks (and new codes) come from the shipped table;
            # a cost set in the copy always stands
            for code, t in TerrainTable(DEFAULTS).by_code.items():
                mine = self.by_code.get(code)
                if mine is None:
                    self._add(t)
                elif mine.mp is None and t.mp is not None:
                    mine.mp, mine.mp_source = t.mp, t.mp_source

    def _read(self, path):
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                self._add(Terrain(row["code"].strip().upper(), row["name"], row["status_name"].strip().upper(),
                                  row["colour"] or "#dddddd", int(row["mp"]) if row["mp"] else None,
                                  row["mp_source"], row["notes"]))

    def _add(self, t):
        self.by_code.setdefault(t.code, t)
        if t.status_name:
            self.by_status.setdefault(t.status_name, t)

    def code(self, text):
        """Terrain code for a short code ('Lcm', 'PR') or a status name ('GRASSY HILLS'); None if unknown."""
        key = text.strip().upper()
        if key in self.by_code:
            return key
        if key in self.by_status:
            return self.by_status[key].code
        return None

    def colour(self, code):
        t = self.by_code.get(code or "")
        return t.colour if t else "#dddddd"

    def mp(self, code):
        t = self.by_code.get(code or "")
        return t.mp if t else None
