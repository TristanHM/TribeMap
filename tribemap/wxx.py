"""Worldographer .wxx files as Clan Cartographer writes them: the format players swap maps in.

A .wxx is gzip-compressed UTF-16 XML. The layout here copies Clan Cartographer's own output (Worldographer 1.74, its
"TribeNet ..." map layers), so a file we write imports the way one of its own does. Details in
the notes kept with the game data (wxx-format.md).

- Tiles: one <tilerow> per column, one line per hex: "<terrain index>\t0\t0\t0\t0\tZ" (0 = blank). The terrain
  index is the position of the TribeNet code in Clan Cartographer's list (TERRAINS).
- Pixels: tile (c, r) has its centre at x = 225c + 150, y = 300r + 150 (+150 more in odd tile columns). Tile column 0
  is always an odd world column, so odd tile columns are TribeNet's lower (even) columns.
- Each known hex carries its coordinate as a text label ("TribeNet Coords" and "TribeNet Large Coords" layers):
  that's what ties the grid to TribeNet hexes, and how read() finds where a file sits.
- Rivers are two-point paths along a hexside; resources a "Resource Mines" feature with a label; special hexes a
  point-of-interest with a label; hexes we only know the terrain of get Clan Cartographer's yellow X.
"""
import gzip
import re
import uuid
from xml.sax.saxutils import escape

from . import coords

# Clan Cartographer's terrain list: index 1..32 in its <terrainmap> (Worldographer names in TERRAINMAP below)
TERRAINS = ["ALPS", "AH", "AR", "BF", "BH", "CH", "D", "DH", "DE", "GH", "GHP", "HSM", "JG", "JH", "L", "LAM", "LCM",
            "LJM", "LSM", "LVM", "O", "PI", "PR", "PPR", "RH", "SH", "SW", "TU", "UJS", "UL", "UM", "UW"]
INDEX = {code: i + 1 for i, code in enumerate(TERRAINS)}
# codes Clan Cartographer doesn't have: the nearest it does
NEAREST = {"BR": "BF", "DF": "D", "C": "CH"}

TERRAINMAP = ("Blank\t0\tMountains Snowcapped\t1\tHills\t2\tFlat Mud\t3\tFlat Shrubland\t4\tHills Shrubland\t5\t"
              "Hills Forest Evergreen\t6\tFlat Forest Deciduous Heavy\t7\tHills Forest Deciduous\t8\tFlat Desert Sandy\t9\t"
              "Hills Grassland\t10\tHills Grassy\t11\tMountain Snowcapped\t12\tFlat Forest Jungle Heavy\t13\t"
              "Hills Forest Jungle\t14\tWater Sea\t15\tMountains\t16\tMountains Forest Evergreen\t17\t"
              "Mountains Forest Jungle\t18\tMountains Snowcapped\t19\tMountain Volcano Dormant\t20\tWater Sea\t21\t"
              "Water Reefs\t22\tFlat Grazing Land\t23\tFlat Grassland\t24\tUnderdark Broken Lands\t25\tHills\t26\t"
              "Flat Swamp\t27\tFlat Snowfields\t28\tFlat Swamp\t29\tFlat Desert Cold\t30\tMountain Forest Jungle\t31\t"
              "Water Sea\t32")

HEADER = """<?xml version='1.0' encoding='utf-16'?>
<map type="WORLD" version="1.74" lastViewLevel="WORLD" continentFactor="0" kingdomFactor="0" provinceFactor="0" \
worldToContinentHOffset="0.0" continentToKingdomHOffset="0.0" kingdomToProvinceHOffset="0.0" \
worldToContinentVOffset="0.0" continentToKingdomVOffset="0.0" kingdomToProvinceVOffset="0.0"
hexWidth="146.85240000000002" hexHeight="127.2" hexOrientation="COLUMNS" mapProjection="FLAT" showNotes="true" \
showGMOnly="false" showGMOnlyGlow="false" showFeatureLabels="true" showGrid="true" showGridNumbers="false" \
showShadows="false"  triangleSize="12">
<gridandnumbering color0="0x808080ff" color1="0x808080ff" color2="0x808080ff" color3="0x808080ff" \
color4="0x808080ff" width0="0.5" width1="0.5" width2="0.5" width3="0.5" width4="0.5" \
gridOffsetContinentKingdomX="0.0" gridOffsetContinentKingdomY="0.0" gridOffsetWorldContinentX="0.0" \
gridOffsetWorldContinentY="0.0" gridOffsetWorldKingdomX="0.0" gridOffsetWorldKingdomY="0.0" gridSquare="0" \
gridSquareHeight="-1.0" gridSquareWidth="-1.0" gridOffsetX="0.0" gridOffsetY="0.0" numberFont="Arial" \
numberColor="0x000000ff" numberSize="20" numberStyle="PLAIN" numberFirstCol="0" numberFirstRow="0" \
numberOrder="COL_ROW" numberPosition="BOTTOM" numberPrePad="DOUBLE_ZERO" numberSeparator="." />
"""
LAYERS = ["TribeNet Units", "TribeNet Clan Units", "TribeNet Encounters", "TribeNet Resources",
          "TribeNet Settlements", "TribeNet MP Cost", "TribeNet Visited", "TribeNet Never Visited",
          "TribeNet Never Scouted", "TribeNet Large Coords", "TribeNet Coords", "Labels", "Grid", "Features",
          "Above Terrain", "Terrain Land", "Above Water", "Terrain Water", "Below All"]
HIDDEN = {"TribeNet MP Cost", "TribeNet Never Visited", "TribeNet Never Scouted", "TribeNet Large Coords"}
MAPKEY = """<mapkey positionx="0.0" positiony="0.0" viewlevel="WORLD" height="-1" \
backgroundcolor="0.9803921580314636,0.9215686321258545,0.843137264251709,1.0" backgroundopacity="50" \
titleText="Map Key" titleFontFace="Arial"  titleFontColor="0.0,0.0,0.0,1.0" titleFontBold="true" \
titleFontItalic="false" titleScale="80" scaleText="1 Hex = ? units" scaleFontFace="Arial"  \
scaleFontColor="0.0,0.0,0.0,1.0" scaleFontBold="true" scaleFontItalic="false" scaleScale="65" entryFontFace="Arial"  \
entryFontColor="0.0,0.0,0.0,1.0" entryFontBold="true" entryFontItalic="false" entryScale="55"  >
</mapkey>
"""
FOOTER = """<notes>
</notes>
<informations></informations>
<configuration>
  <terrain-config></terrain-config>
  <feature-config></feature-config>
  <texture-config></texture-config>
  <text-config></text-config>
  <shape-config></shape-config>
</configuration>
</map>
"""
ALL_LEVELS = 'isWorld="true" isContinent="true" isKingdom="true" isProvince="true"'

# hex corners from the centre (pixels) and which two bound each side
CORNERS = {"E": (150, 0), "NE": (75, -150), "NW": (-75, -150), "W": (-150, 0), "SW": (-75, 150), "SE": (75, 150)}
SIDE_CORNERS = {"N": ("NE", "NW"), "NE": ("E", "NE"), "NW": ("NW", "W"), "SW": ("W", "SW"), "S": ("SW", "SE"),
                "SE": ("SE", "E")}


def centre(c, r):
    return 225 * c + 150, 300 * r + 150 + (150 if c % 2 else 0)


def _label(layer, x, y, scale, text, colour="0.0,0.0,0.0,1.0", background=None):
    bg = f'backgroundColor="{background}" ' if background else ""
    return (f'<label  mapLayer="{layer}" style="null" fontFace="null" {bg}color="{colour}" outlineColor="1.0,1.0,1.0,1.0" '
            f'outlineSize="0.0" rotate="0.0" isBold="false" isItalic="false" {ALL_LEVELS} isGMOnly="false" tags="">'
            f'<location viewLevel="WORLD" x="{x:.1f}" y="{y:.1f}" scale="{scale}" />{escape(text)}</label>\n')


def _feature(kind, layer, x, y, scale, colour="null"):
    return (f'<feature type="{kind}" rotate="0.0" uuid="{uuid.uuid4()}" mapLayer="{layer}" isFlipHorizontal="false" '
            f'isFlipVertical="false" scale="{scale}" scaleHt="-1.0" tags="" color="{colour}" ringcolor="null" '
            f'isGMOnly="false" isPlaceFreely="false" labelPosition="6:00" labelDistance="0" {ALL_LEVELS} '
            f'isFillHexBottom="false" isHideTerrainIcon="false"><location viewLevel="WORLD" x="{x:.1f}" y="{y:.1f}" />'
            f'<label  mapLayer="{layer}" style="City" fontFace="Academy Engraved LET" color="0.0,0.0,0.0,1.0" '
            f'outlineColor="0.0,0.0,0.0,1.0" outlineSize="2.0" rotate="0.0" isBold="false" isItalic="false" {ALL_LEVELS} '
            f'isGMOnly="false" tags=""><location viewLevel="WORLD" x="0.0" y="0.0" scale="25.0" /></label></feature>\n')


def _river(a, b):
    return (f'<shape  type="Path" isCurve="false" isGMOnly="false" isSnapVertices="true" isMatchTileBorders="false" '
            f'tags="river" creationType="BASIC" isDropShadow="false" isInnerShadow="false" isBoxBlur="false" {ALL_LEVELS} '
            f'dsSpread="0.2" dsRadius="50.0" dsOffsetX="0.0" dsOffsetY="0.0" insChoke="0.2" insRadius="50.0" '
            f'insOffsetX="0.0" insOffsetY="0.0" bbWidth="10.0" bbHeight="10.0" bbIterations="3" mapLayer="Above Terrain" '
            f'fillTexture="" strokeTexture="" strokeType="SIMPLE" highestViewLevel="WORLD" currentShapeViewLevel="WORLD" '
            f'lineCap="ROUND" lineJoin="ROUND" opacity="1.0" fillRule="NON_ZERO" '
            f'strokeColor="0.5490196078431373,0.6980392156862745,0.8470588235294118,1.0" strokeWidth="0.1" '
            f'dsColor="1.0,0.8941176533699036,0.7686274647712708,1.0" insColor="1.0,0.8941176533699036,0.7686274647712708,1.0">\n'
            f' <p type="m" x="{a[0]}" y="{a[1]}"/>\n <p x="{b[0]}" y="{b[1]}"/></shape>\n')


def export(cells, rivers=(), fords=(), notes=None, unscouted=(), pad=1):
    """A .wxx (gzip bytes) of the given hexes.

    cells: {(gx, gy): terrain code}. rivers / fords: [((gx, gy), dir)] hexsides. notes: {(gx, gy): [text]} for
    resources and special sites. unscouted: hexes we only know the terrain of (marked with Clan Cartographer's X).
    """
    if not cells:
        raise ValueError("no known hexes to export")
    notes = notes or {}
    gxs, gys = [h[0] for h in cells], [h[1] for h in cells]
    gx0, gy0 = min(gxs) - pad, min(gys) - pad
    if gx0 % 2 == 0:
        gx0 -= 1                           # tile column 0 must be an odd world column
    gx0, gy0 = max(gx0, 1), max(gy0, 1)
    wide, high = max(gxs) - gx0 + 1 + pad, max(gys) - gy0 + 1 + pad
    tile = lambda h: (h[0] - gx0, h[1] - gy0)

    rows = []
    for c in range(wide):
        lines = []
        for r in range(high):
            code = cells.get((gx0 + c, gy0 + r))
            code = NEAREST.get(code, code)
            lines.append(f"{INDEX.get(code, 0)}\t0\t0\t0\t0\tZ")
        rows.append("<tilerow>\n" + "\n".join(lines) + "\n</tilerow>\n")

    features, labels, shapes = [], [], []
    for h in sorted(cells):
        x, y = centre(*tile(h))
        name = coords.label(h)
        labels.append(_label("TribeNet Coords", x, y - 115, "10.0", name))
        labels.append(_label("TribeNet Large Coords", x, y + 145, "12.5", name))
        if h in unscouted:
            features.append(_feature("Symbol X", "Features", x, y, "30.0", "1.0,1.0,0.0,1.0"))
    for h, texts in sorted(notes.items()):
        if h not in cells:
            continue
        x, y = centre(*tile(h))
        features.append(_feature("Resource Mines", "TribeNet Resources", x - 60, y + 40, "20.0"))
        labels.append(_label("TribeNet Resources", x + 20, y + 60, "12.0", ", ".join(texts)))
    for (h, d) in sorted(set(rivers)):
        cx, cy = centre(*tile(h))
        a, b = (CORNERS[k] for k in SIDE_CORNERS[d])
        shapes.append(_river((cx + a[0], cy + a[1]), (cx + b[0], cy + b[1])))
    for (h, d) in sorted(set(fords)):
        cx, cy = centre(*tile(h))
        a, b = (CORNERS[k] for k in SIDE_CORNERS[d])
        labels.append(_label("Labels", cx + (a[0] + b[0]) / 2, cy + (a[1] + b[1]) / 2, "10.0", "Ford"))

    xml = (HEADER + f"<terrainmap>{TERRAINMAP}</terrainmap>\n"
           + "".join(f'<maplayer name="{n}" isVisible="{"false" if n in HIDDEN else "true"}"/>\n' for n in LAYERS)
           + f'<tiles viewLevel="WORLD" tilesWide="{wide}" tilesHigh="{high}">\n' + "".join(rows) + "</tiles>\n"
           + MAPKEY + "<features>\n" + "".join(features) + "</features>\n<labels>\n" + "".join(labels) + "</labels>\n"
           + "<shapes>\n" + "".join(shapes) + "</shapes>\n" + FOOTER)
    return gzip.compress(xml.encode("utf-16"))


def read(data):
    """A .wxx (gzip bytes) -> {"cells": {(gx, gy): code}, "rivers": [((gx, gy), dir)], "notes": {(gx, gy): [text]}}.

    Where the grid sits is found from its TribeNet coordinate labels. Only Clan Cartographer's terrain list is
    understood; other terrain names are left out.
    """
    text = gzip.decompress(data).decode("utf-16")
    tmap = re.search(r"<terrainmap>(.*?)</terrainmap>", text, re.S).group(1).split("\t")
    names = {int(tmap[i + 1]): tmap[i].replace("Classic/", "") for i in range(0, len(tmap) - 1, 2)}
    our_names = TERRAINMAP.split("\t")[0::2]   # position = index; 0 is Blank
    first = {}   # Clan Cartographer's names repeat (three "Water Sea"s): a name alone means its first index
    for i, name in enumerate(our_names):
        first.setdefault(name, i)
    code_of = {}
    for i, name in names.items():
        # the same list as ours (a Clan Cartographer file): trust the index; otherwise go by the name
        idx = i if i < len(our_names) and our_names[i] == name else first.get(name)
        if idx:
            code_of[i] = TERRAINS[idx - 1]

    m = re.search(r'mapLayer="TribeNet Coords"[^>]*><location viewLevel="WORLD" x="([\d.]+)" y="([\d.]+)"[^>]*/>'
                  r'\s*([A-Z]{2} \d{4})</label>', text)
    if not m:
        raise ValueError("no TribeNet coordinate labels: can't tell where this map sits")
    x, y = float(m.group(1)), float(m.group(2))
    c = round((x - 150) / 225)
    r = round((y + 115 - 150 - (150 if c % 2 else 0)) / 300)
    gx, gy = coords.parse(m.group(3))
    gx0, gy0 = gx - c, gy - r

    # Clan Cartographer draws a legend as a column of sample tiles under a "Legend" label: not real ground
    key = re.search(r'<location viewLevel="WORLD" x="([\d.]+)" y="([\d.]+)"[^>]*/>Legend</label>', text)
    key_col, key_top = (round((float(key.group(1)) - 150) / 225), float(key.group(2))) if key else (None, None)

    cells = {}
    for c, row in enumerate(re.findall(r"<tilerow[^>]*>(.*?)</tilerow>", text, re.S)):
        for r, line in enumerate(row.strip().splitlines()):
            i = int(line.split("\t")[0] or 0)
            if c == key_col and centre(c, r)[1] > key_top:
                continue
            if i and i in code_of:
                cells[(gx0 + c, gy0 + r)] = code_of[i]

    # hexsides by their midpoint, for the rivers
    mids = {}
    for (hx, hy) in cells:
        cx, cy = centre(hx - gx0, hy - gy0)
        for d, (k1, k2) in SIDE_CORNERS.items():
            a, b = CORNERS[k1], CORNERS[k2]
            mids[(round(cx + (a[0] + b[0]) / 2), round(cy + (a[1] + b[1]) / 2))] = ((hx, hy), d)
    rivers = []
    for shape in re.findall(r'<shape [^>]*tags="river"[^>]*>(.*?)</shape>', text, re.S):
        pts = [(float(a), float(b)) for a, b in re.findall(r'<p [^>]*x="([\d.]+)" y="([\d.]+)"/>', shape)]
        for p, q in zip(pts, pts[1:]):
            hit = mids.get((round((p[0] + q[0]) / 2), round((p[1] + q[1]) / 2)))
            if hit:
                rivers.append(coords.edge(*hit))
    notes = {}
    for lx, ly, txt in re.findall(r'mapLayer="TribeNet Resources"[^>]*><location viewLevel="WORLD" x="([\d.]+)" '
                                  r'y="([\d.]+)"[^>]*/>([^<]+)</label>', text):
        lx, ly = float(lx), float(ly)
        best = min(cells, key=lambda h: (centre(h[0] - gx0, h[1] - gy0)[0] - lx) ** 2
                   + (centre(h[0] - gx0, h[1] - gy0)[1] - ly) ** 2, default=None)
        if best:
            notes.setdefault(best, []).append(txt.strip())
    return {"cells": cells, "rivers": sorted(set(rivers)), "notes": notes}
