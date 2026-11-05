"""Test fixtures. All examples use a made-up clan (0999) on a made-up sheet (QB/RB),
so the repo never contains our real positions."""
import pytest

from tribemap import config
from tribemap.terrain import TerrainTable

SAMPLE = """Tribe 0999, , Current Hex = QB 1016, (Previous Hex = QB 1017)
Current Turn 906-05 (#77), Summer, FINE\tNext Turn 906-06 (#78), 18/11/2026
Received: $0, Cost: $ 0\tCredit: $ 200
Tribe Movement: Move N-PR, \\
Scout 1:Scout N-PR, \\N-BH, \\N-BH, Lcm NW
Scout 2:Scout NW-PR,Find Salt\\NW-SW, Lcm NW,River NW
Scout 3:Scout NE-GH, Lcm SE, S, River NE,Find COPPER ORE\\N-GH, River N SE NW,Ford NE,Find 3 Rope\\No Ford on River to N of HEX, Nothing of interest found
Scout 4:Scout S-PR, \\S-PR, \\Not enough M.P's to move S into SWAMP
Scout 5:Scout SE-BF, Duskholm, 0123\\Mercenary Aldo Brenn\\ Nothing of interest found
PR = Prairie
0999 Status: PRAIRIE, COAL, 0999, 0123e1

Humans
People\t3200\t
Warriors \t1000\tActives \t1100\tInactives \t1100\t
Animals
Cattle \t50\tGoat \t370\t
Ships
None
Skills:
Adm 2, Dip 2, Sct 2,
Morale : \t1
Something odd near the River
"""


@pytest.fixture
def terrain():
    return TerrainTable(config.PACKAGE_DATA / "terrain.csv")


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    game = tmp_path / "game"
    (game / "906-5").mkdir(parents=True)
    monkeypatch.setenv("TRIBEMAP_GAME_DIR", str(game))
    monkeypatch.setenv("TRIBEMAP_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TRIBEMAP_DB", str(tmp_path / "local" / "test.db"))
    return config.load()


@pytest.fixture
def sample_file(cfg):
    path = cfg.game_dir / "906-5" / "0999_906_05_Results.txt"
    path.write_text(SAMPLE, encoding="utf-8")
    return path
