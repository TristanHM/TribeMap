import pytest

from tribemap import coords as c


def test_label_round_trip():
    for lab in ["QB 1017", "QB 0101", "QB 3021", "AA 0101", "ZZ 1510"]:
        assert c.label(c.parse(lab)) == lab


def test_bad_labels():
    for bad in ["N/A", "## 1017", "QB 3121", "QB 1022", "QB 0017"]:
        assert c.try_parse(bad) is None
    with pytest.raises(ValueError):
        c.parse("N/A")


def test_sheets_join_vertically_and_horizontally():
    assert c.label(c.step(c.parse("QB 1021"), "S")) == "RB 1001"
    assert c.label(c.step(c.parse("QB 3010"), "NE")) == "QC 0110"  # col 30 is even: NE keeps the row


def test_odd_columns_sit_higher():
    # even column: NE stays on the same row; odd column: NE goes up a row
    assert c.label(c.step(c.parse("QB 1017"), "NE")) == "QB 1117"
    assert c.label(c.step(c.parse("QB 1117"), "NE")) == "QB 1216"
    assert c.label(c.step(c.parse("QB 1017"), "SE")) == "QB 1118"
    assert c.label(c.step(c.parse("QB 1117"), "SE")) == "QB 1217"
    assert c.label(c.step(c.parse("QB 1017"), "NW")) == "QB 0917"
    assert c.label(c.step(c.parse("QB 0917"), "NW")) == "QB 0816"


def test_sheet_borders():
    assert c.sheet(c.parse("QB 1021")) == "QB"
    # an even column on the bottom row touches the next sheet down through S, SE and SW
    even = c.sheet_borders([c.parse("QB 1021")])
    assert {c.label(h) + " " + d for h, d in even} == {"RB 1001 N", "QB 1021 SE", "RB 0901 NE"}
    # an odd column sits higher, so only its S side is on the border
    assert {d for h, d in c.sheet_borders([c.parse("QB 1121")])} == {"N"}
    # inside a sheet there are none; the right-hand edge borders the next sheet across
    assert not c.sheet_borders([c.parse("QB 1010")])
    assert c.sheet_borders([c.parse("QB 3010")])


def test_lines_and_paths():
    h = c.parse("QB 1017")
    assert c.path_dirs(h, h) == []
    assert c.path_dirs(h, c.parse("QB 1013")) == ["N"] * 4
    assert c.path_dirs(h, c.parse("QB 1316")) == ["NE"] * 3
    for target in ["QB 1512", "QB 0521", "RB 1203", "QB 0109", "QB 2017"]:
        t = c.parse(target)
        dirs = c.path_dirs(h, t)
        assert len(dirs) == c.distance(h, t) and None not in dirs
        end = h
        for d in dirs:
            end = c.step(end, d)
        assert end == t
    assert c.direction(h, c.parse("QB 1117")) == "NE" and c.direction(h, c.parse("QB 1217")) is None


def test_steps_are_reversible():
    for lab in ["QB 1017", "QB 1117"]:
        h = c.parse(lab)
        for d in c.DIRS:
            assert c.step(c.step(h, d), c.OPPOSITE[d]) == h


def test_distance():
    h = c.parse("QB 1017")
    for d in c.DIRS:
        assert c.distance(h, c.step(h, d)) == 1
    assert c.distance(h, c.parse("RB 1001")) == 5
    assert c.distance(h, c.parse("QB 1316")) == 3  # NE, NE, NE
    assert len(c.within(h, 1)) == 7
    assert len(c.within(h, 3)) == 37


def test_edge_is_canonical():
    h = c.parse("QB 1017")
    for d in c.DIRS:
        assert c.edge(h, d) == c.edge(c.step(h, d), c.OPPOSITE[d])
        assert c.edge(h, d)[1] in ("N", "NE", "SE")
