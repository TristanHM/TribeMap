from tribemap import coords as c
from tribemap import db, importer, queries
from tribemap.cli import main


def setup(cfg, terrain, sample_file):
    conn = db.connect(cfg.db_path)
    sid, rep, skipped = importer.import_report(conn, cfg, terrain, sample_file)
    return conn, sid


def test_import_is_idempotent(cfg, terrain, sample_file):
    conn, sid = setup(cfg, terrain, sample_file)
    n = conn.execute("SELECT COUNT(*) FROM observation").fetchone()[0]
    sid2, _, skipped = importer.import_report(conn, cfg, terrain, sample_file)
    assert skipped and sid2 == sid
    importer.import_report(conn, cfg, terrain, sample_file, force=True)
    assert conn.execute("SELECT COUNT(*) FROM observation").fetchone()[0] == n


def test_knowledge_prefers_entered_over_glimpsed(cfg, terrain, sample_file):
    conn, _ = setup(cfg, terrain, sample_file)
    hexes, edges = queries.knowledge(conn)
    assert hexes[c.parse("QB 0916")].resources == {"SALT"}
    glimpsed = hexes[c.parse("QB 0913")]
    assert (glimpsed.terrain, glimpsed.terrain_how, glimpsed.scouted) == ("LCM", "glimpsed", False)
    assert (c.edge(c.parse("QB 0815"), "NW")) in edges


def test_where_and_unscouted(cfg, terrain, sample_file):
    conn, _ = setup(cfg, terrain, sample_file)
    w = queries.where(conn, "0999")
    assert w["hex"] == "QB 1016" and w["skills"]["Dip"] == 2
    rows = queries.unscouted(conn, c.parse("QB 1016"), 1)
    labels = {r["hex"] for r in rows}
    assert "QB 1016" not in labels and "QB 1015" not in labels   # status hex and scout1's first step
    assert len(rows) < 7


def test_fix_survives_rebuild_and_plans_are_kept(cfg, terrain, sample_file):
    conn, _ = setup(cfg, terrain, sample_file)
    importer.add_correction(conn, cfg, hex="QB 0913", kind="terrain", value="LSM", note="test")
    conn.execute("INSERT INTO plan_route (turn, unit, kind, orders) VALUES ('906-06','0999','move','N')")
    conn.commit()
    importer.rebuild(conn, cfg, terrain)
    hexes, _ = queries.knowledge(conn)
    assert (hexes[c.parse("QB 0913")].terrain, hexes[c.parse("QB 0913")].terrain_how) == ("LSM", "manual")
    assert conn.execute("SELECT COUNT(*) FROM plan_route").fetchone()[0] == 1


def test_as_of_hides_later_turns(cfg, terrain, sample_file):
    conn, _ = setup(cfg, terrain, sample_file)
    hexes, _ = queries.knowledge(conn, as_of="906-04")
    assert not hexes


def test_cli_end_to_end(cfg, sample_file, tmp_path, capsys):
    main(["import", str(sample_file), "--json"])
    out = capsys.readouterr().out
    assert '"turn": "906-05"' in out
    assert list(cfg.backup_dir.glob("tribemap-*.db"))
    main(["hex", "QB 0916"])
    assert "SALT" in capsys.readouterr().out
    png = tmp_path / "map.png"
    main(["render", "--out", str(png)])
    assert png.stat().st_size > 10_000
    main(["sql", "SELECT COUNT(*) AS n FROM unit"])
    assert capsys.readouterr().out.strip().endswith("1")


def test_player_info_is_unverified_and_never_overrides_ours(cfg, terrain, sample_file):
    conn, _ = setup(cfg, terrain, sample_file)
    # contradicts our own scouting of QB 0916 (PR + salt), and is newer
    importer.add_correction(conn, cfg, hex="QB 0916", kind="terrain", value="SW", source_kind="player",
                            provider="Clan 0123", turn="906-07")
    importer.add_correction(conn, cfg, hex="QB 0916", kind="resource", value="IRON", source_kind="player",
                            provider="Clan 0123", turn="906-07")
    # a hex we know nothing about, age unknown
    importer.add_correction(conn, cfg, hex="QB 2005", kind="terrain", value="GH", source_kind="player",
                            provider="Clan 0123")
    importer.add_correction(conn, cfg, hex="QB 2005", kind="river", value="river", dir="SW",
                            source_kind="player", provider="Clan 0123")
    hexes, edges = queries.knowledge(conn)

    ours = hexes[c.parse("QB 0916")]
    assert (ours.terrain, ours.verified, ours.resources) == ("PR", True, {"SALT"})
    assert ours.last_seen == "906-05" and ours.last_known == "906-07"
    assert {(u["kind"], u["value"], u["provider"], u["as_of"]) for u in ours.unverified} == {
        ("terrain", "SW", "Clan 0123", "906-07"), ("resource", "IRON", "Clan 0123", "906-07")}
    assert all(u["received"] for u in ours.unverified)

    theirs = hexes[c.parse("QB 2005")]
    assert (theirs.terrain, theirs.terrain_how, theirs.verified, theirs.scouted) == ("GH", "reported", False, False)
    assert theirs.age_unknown and theirs.last_known is None
    assert edges[c.edge(c.parse("QB 2005"), "SW")] == {"river": False}
    near = {r["hex"]: r for r in queries.unscouted(conn, c.parse("QB 2005"), 0)}
    assert near["QB 2005"]["known_as"] == "reported"   # still needs our own scouting


def test_our_scouting_verifies_a_reported_hex(cfg, terrain, sample_file):
    conn, _ = setup(cfg, terrain, sample_file)
    importer.add_correction(conn, cfg, hex="QB 0913", kind="terrain", value="LSM", source_kind="player",
                            provider="Wren", turn="906-03")
    info = queries.knowledge(conn)[0][c.parse("QB 0913")]
    assert (info.terrain, info.terrain_how) == ("LCM", "glimpsed")   # our glimpse beats their report
    assert info.verified and info.unverified


def test_cli_player_fix_needs_provider(cfg, sample_file):
    import pytest
    main(["import", str(sample_file)])
    with pytest.raises(SystemExit):
        main(["fix", "QB 2005", "terrain", "GH", "--source", "player"])


def test_old_database_is_migrated(cfg):
    import sqlite3
    old = sqlite3.connect(cfg.db_path)
    old.executescript(db.SCHEMA.replace(",\n    received_at TEXT                 -- when we got the information (turn = when it was true)", ""))
    old.close()
    conn = db.connect(cfg.db_path)
    assert "received_at" in {r[1] for r in conn.execute("PRAGMA table_info(observation)")}


def test_knowledge_levels(cfg, terrain, sample_file):
    conn, _ = setup(cfg, terrain, sample_file)
    importer.add_correction(conn, cfg, hex="QB 2005", kind="terrain", value="GH", source_kind="player",
                            provider="X", turn="906-05")
    importer.add_correction(conn, cfg, hex="QB 2006", kind="terrain", value="PR")   # our fix: terrain only
    hexes, _ = queries.knowledge(conn)
    level = lambda label: hexes[c.parse(label)].knowledge
    assert level("QB 0916") == "full"       # scouts entered (and found salt)
    assert level("QB 1016") == "full"       # our unit's status line
    assert level("QB 0913") == "terrain"    # glimpsed from next door
    assert level("QB 2006") == "terrain"    # manual terrain fix: resources never checked
    assert level("QB 2005") == "reported"


def test_tribe_movement_gives_terrain_only(cfg, terrain):
    path = cfg.game_dir / "906-5" / "move.txt"
    path.write_text("Tribe 0999, , Current Hex = QB 1014, (Previous Hex = QB 1016)\n"
                    "Current Turn 906-05 (#77), Summer, FINE\n"
                    r"Tribe Movement: Move N-PR, \N-GH, \ " "\n"
                    "0999 Status: GRASSY HILLS, COAL\n", encoding="utf-8")
    conn = db.connect(cfg.db_path)
    importer.import_report(conn, cfg, terrain, path)
    hexes, _ = queries.knowledge(conn)
    passed, end = hexes[c.parse("QB 1015")], hexes[c.parse("QB 1014")]
    assert (passed.terrain, passed.terrain_how, passed.knowledge) == ("PR", "passed", "terrain")
    assert (end.knowledge, end.resources) == ("full", {"COAL"})
    assert "QB 1015" in {r["hex"] for r in queries.unscouted(conn, c.parse("QB 1014"), 1)}
