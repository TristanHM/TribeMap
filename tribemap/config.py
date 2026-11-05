"""Where things live. Everything can be overridden in config.toml or by environment variables.

Defaults:
    game_dir   the TribeNet folder (the parent of this repo)
    data_dir   <game_dir>/Working Files/tribemap   config.toml, terrain.csv, corrections.csv, backups/
    db_path    %LOCALAPPDATA%/TribeMap/tribemap.db (outside synced folders: sync can corrupt a live SQLite file)

Environment overrides: TRIBEMAP_GAME_DIR, TRIBEMAP_DATA_DIR, TRIBEMAP_DB (used by the tests).
"""
import os
import shutil
import tomllib
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PACKAGE_DATA = Path(__file__).resolve().parent / "data"


@dataclass
class Config:
    clan: str
    game_dir: Path
    data_dir: Path
    db_path: Path
    report_glob: str
    keep_backups: int
    grid_rows: str        # [grid] rows = "A-M": the world's map-sheet rows (first-last letter)
    grid_cols: str        # [grid] cols = "A-M": its map-sheet columns
    mover_mp: dict        # [movement] scout / mounted / foot
    assumed_cost: int     # [movement] assumed_cost: MP for terrain with no known cost

    @property
    def backup_dir(self):
        return self.data_dir / "backups"

    @property
    def terrain_csv(self):
        return self.data_dir / "terrain.csv"

    @property
    def corrections_csv(self):
        return self.data_dir / "corrections.csv"

    @property
    def contacts_csv(self):
        return self.data_dir / "contacts.csv"

    @property
    def units_csv(self):
        return self.data_dir / "units.csv"


def learn_clan(cfg, clan):
    """No clan set yet: take the one from the first report imported, and keep it in config.toml."""
    if cfg.clan or not clan:
        return
    path = cfg.data_dir / "config.toml"
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    path.write_text(f'clan = "{clan}"\n' + old, encoding="utf-8")   # first: a top-level key goes before any [table]
    cfg.clan = clan


def load():
    game_dir = Path(os.environ.get("TRIBEMAP_GAME_DIR", REPO.parent))
    data_dir = Path(os.environ.get("TRIBEMAP_DATA_DIR", game_dir / "Working Files" / "tribemap"))
    settings = {}
    if (data_dir / "config.toml").exists():
        settings = tomllib.loads((data_dir / "config.toml").read_text(encoding="utf-8"))
    local = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "TribeMap"
    db = os.environ.get("TRIBEMAP_DB") or settings.get("db_path") or local / "tribemap.db"
    from .planner import ASSUMED_COST, MOVER_MP
    movement = settings.get("movement", {})
    cfg = Config(
        clan=str(settings["clan"]) if settings.get("clan") else None,   # None until the first report (learn_clan)
        game_dir=Path(settings.get("game_dir", game_dir)),
        data_dir=data_dir,
        db_path=Path(db),
        report_glob=settings.get("report_glob", "**/*Results*.docx"),
        keep_backups=int(settings.get("keep_backups", 20)),
        # the world's extent: sheet rows A-V and columns A-P, as on tribenet.wiki/mapping/climate (rows W-Z are
        # greyed out there, with no climate)
        grid_rows=str(settings.get("grid", {}).get("rows", "A-V")),
        grid_cols=str(settings.get("grid", {}).get("cols", "A-P")),
        mover_mp={k: int(movement.get(k, v)) for k, v in MOVER_MP.items()},
        assumed_cost=int(movement.get("assumed_cost", ASSUMED_COST)),
    )
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    cfg.db_path.parent.mkdir(parents=True, exist_ok=True)
    if not cfg.terrain_csv.exists():
        shutil.copy(PACKAGE_DATA / "terrain.csv", cfg.terrain_csv)
    return cfg
