"""Made-up turn reports for the guide's pictures: clan 0999 on sheet ZZ, which no real game has.

    python webapp/guide/make_demo.py <out_dir>      writes 0999_901_0N_Results.txt for three turns

The land is fixed (the same hex always has the same terrain), so the reports agree with each other.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tribemap import coords  # noqa: E402

NAMES = {"PR": "PRAIRIE", "GH": "GRASSY HILLS", "BF": "BRUSH FLAT", "CH": "CONIFER HILLS", "D": "DECIDUOUS",
         "SW": "SWAMP", "BH": "BRUSH HILLS", "LCM": "LOW CONIFER MOUNTAINS", "L": "LAKE", "DH": "DECIDUOUS HILLS"}
LAND = {   # hand-drawn patches around ZZ 1010 (column, row): the rest is prairie
    "GH": ["ZZ 0908", "ZZ 0909", "ZZ 1008", "ZZ 1108"], "CH": ["ZZ 1106", "ZZ 1206", "ZZ 1107", "ZZ 1207"],
    "LCM": ["ZZ 1005", "ZZ 1105", "ZZ 1204", "ZZ 1305"], "BF": ["ZZ 0811", "ZZ 0812", "ZZ 0911", "ZZ 0712"],
    "D": ["ZZ 1211", "ZZ 1212", "ZZ 1311", "ZZ 1310"], "SW": ["ZZ 0913", "ZZ 1013", "ZZ 1014"],
    "BH": ["ZZ 0706", "ZZ 0707", "ZZ 0807"], "L": ["ZZ 1409", "ZZ 1408"], "DH": ["ZZ 1309", "ZZ 1308"],
}
CODE = {coords.parse(h): c for c, hs in LAND.items() for h in hs}
RIVER = {(coords.parse("ZZ 1009"), "NE"), (coords.parse("ZZ 1109"), "N"), (coords.parse("ZZ 1108"), "NE")}
FINDS = {coords.parse("ZZ 1107"): "Find COPPER ORE", coords.parse("ZZ 1211"): "Find Salt"}
SPECIAL = {coords.parse("ZZ 0812"): "Duskholm"}
OTHERS = {coords.parse("ZZ 1212"): "0123"}   # another clan's unit, seen by a scout


def terrain(h):
    return CODE.get(h, "PR")


def river(h, d):
    return (h, d) in RIVER or (coords.step(h, d), coords.OPPOSITE[d]) in RIVER


def walk(start, dirs, kind):
    """One party's report text: each step's terrain, what it saw next door and found there."""
    h, parts = start, []
    for d in dirs:
        h = coords.step(h, d)
        bits = [f"{d}-{terrain(h)}"]
        tall = {}
        for nd in coords.DIRS:
            if terrain(coords.step(h, nd)) in ("LCM", "L"):
                tall.setdefault(terrain(coords.step(h, nd)).title(), []).append(nd)
        bits += [f"{code} {' '.join(ds)}" for code, ds in tall.items()]
        rivers = [nd for nd in coords.DIRS if river(h, nd)]
        if rivers:
            bits.append("River " + " ".join(rivers))
        if kind == "scout":
            bits += [x for x in (SPECIAL.get(h), OTHERS.get(h), FINDS.get(h)) if x]
        parts.append(", ".join(bits))
    if kind == "scout" and not any(x in parts[-1] for x in ("Find", "Duskholm")):
        parts.append("Nothing of interest found")
    return h, "\\".join(parts)


TURNS = [   # (turn, {unit: (start, move, [scout routes])})
    ("901-01", {"0999": ("ZZ 1011", ["N"], [["N", "N", "NE"], ["NW", "NW", "N"], ["SE", "SE", "NE"]]),
                "0999e1": ("ZZ 1011", ["SW"], [["SW", "S", "S"]])}),
    ("901-02", {"0999": (None, ["N"], [["NE", "N", "N"], ["N", "NW", "NW"], ["SE", "S", "SE"]]),
                "0999e1": (None, ["SW", "S"], [["NW", "N", "NW"]])}),
    ("901-03", {"0999": (None, ["NE"], [["N", "N", "N"], ["NE", "SE", "SE"], ["NW", "SW", "SW"]]),
                "0999e1": (None, ["S"], [["SE", "SE", "S"]])}),
]


def reports():
    where = {}
    out = []
    for i, (turn, units) in enumerate(TURNS):
        y, m = turn.split("-")
        nxt = f"{y}-{int(m) + 1:02d}"
        lines = []
        for unit, (start, move, scouts) in units.items():
            here = coords.parse(start) if start else where[unit]
            end, mtext = walk(here, move, "move")
            kind, name = ("Tribe", "Main Tribe") if unit == "0999" else ("Element", "Outriders")
            lines.append(f"{kind} {unit}, {name}, Current Hex = {coords.label(end)}, (Previous Hex = {coords.label(here)})")
            if unit == "0999":
                lines.append(f"Current Turn {turn} (#{i + 1}), Spring, FINE\tNext Turn {nxt} (#{i + 2}), 01/0{i + 2}/2027")
                lines.append("Received: $0, Cost: $ 0\tCredit: $ 100")
            lines.append(f"{kind} Movement: Move {mtext}")
            for n, route in enumerate(scouts, 1):
                lines.append(f"Scout {n}:Scout {walk(end, route, 'scout')[1]}")
            others = [u for u in units if u != unit and where.get(u, None) == end]
            lines.append(f"{unit} Status: {NAMES[terrain(end)]}, {', '.join([unit] + others)}")
            lines.append("")
            where[unit] = end
        lines += ["Humans", "People\t900\t", "Skills:", "Sct 2,", "Morale : \t1", ""]
        out.append((f"0999_{y}_{m}_Results.txt", "\n".join(lines)))
    return out


if __name__ == "__main__":
    d = Path(sys.argv[1])
    d.mkdir(parents=True, exist_ok=True)
    for name, text in reports():
        (d / name).write_text(text, encoding="utf-8")
        print(d / name)
