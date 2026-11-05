"""Terrain icons: a small coloured illustration per terrain type, so tiles can be told apart at a glance.

Style (after classic hex atlases and Clan Cartographer): one distinct symbol per terrain, readable on its own
as well as through the hex's background colour. Mountains have a lit and a shaded face (snow caps on snowy
ranges), hills are filled mounds, forests are small clustered trees (two-tier conifers, round broadleaf,
palms for jungle), wetlands are reeds over water lines, water is white waves.

Each terrain code maps to a list of shapes in hex-local coordinates: (0, 0) is the hex centre, hex radius 1,
y pointing DOWN (SVG convention; the PNG renderer flips it). Icons sit in a band below the centre, between the
resource notes above and the terrain code below (x within +-0.45, y within -0.2..0.5).

Shape dicts (shared by the browser and the PNG renderer):
    {"k": "line", "pts": [[x, y], ...], "stroke": S, "w": width}
    {"k": "poly", "pts": [[x, y], ...], "fill": F, "stroke": S, "w": width}
    {"k": "circle", "c": [x, y], "r": r, "fill": F, "stroke": S, "w": width}
F and S are colours ("#rrggbb"), "none", or "ink" (dark on light terrain, light on dark terrain).
"""
import math

W = 0.022            # default stroke width
GROUND = 0.36        # baseline of the icon band

# palette
GRASS, GRASS_DARK, GRASS_LIGHT = "#4f7d26", "#3d6420", "#7aa83f"
BUSH, BUSH_DARK, BUSH_LIGHT = "#6e8c2c", "#4b6420", "#9cbb4e"
PINE, PINE_DARK = "#2b5a2a", "#173a18"
LEAF, LEAF_DARK, LEAF_LIGHT = "#4a8a35", "#2f5f22", "#7cb85a"
JUNGLE, JUNGLE_DARK = "#2f7a3a", "#1b4d23"
FROND = "#7cc25a"    # palm leaves: lighter than the jungle hexes they stand on, outlined in JUNGLE_DARK
TRUNK = "#6b4a2b"
HILL, HILL_DARK, HILL_LIGHT = "#9fb35a", "#6c7d33", "#c3d27c"
SANDHILL, SANDHILL_DARK = "#c9a96a", "#8f7440"
ROCKHILL, ROCKHILL_DARK = "#a99a86", "#6d6253"
ROCK, ROCK_LIGHT, ROCK_DARK = "#8d7a66", "#bba890", "#4e4032"
SNOWROCK, SNOWROCK_LIGHT = "#8e9aa6", "#c5ced6"
SNOW, SNOW_EDGE = "#ffffff", "#9fb0bf"
WATER, WATER_LIGHT = "#3d7fb8", "#eaf4ff"
REED, CATTAIL = "#3f7a33", "#6b4a2b"
SAND, SAND_DARK = "#c49a52", "#9a7436"
CRACK = "#8a6a45"
ICE_LINE = "#7fa9c9"
ICE, ICE_SHADE = "#ffffff", "#b9d4e8"
DRY, DRY_DARK = "#b49a5e", "#8a7342"          # arid: parched earth and its cracks
DRYGRASS = "#8a8a4a"
GRAVEL, GRAVEL_DARK = "#a8a596", "#78756a"    # cold desert
LAVA, ASH = "#d0442c", "#8a8a8a"


def _p(points):
    return [[round(x, 3), round(y, 3)] for x, y in points]


def line(points, stroke, w=W):
    return {"k": "line", "pts": _p(points), "stroke": stroke, "w": w}


def poly(points, fill, stroke="none", w=W):
    return {"k": "poly", "pts": _p(points), "fill": fill, "stroke": stroke, "w": w}


def circle(x, y, r, fill, stroke="none", w=W):
    return {"k": "circle", "c": [round(x, 3), round(y, 3)], "r": round(r, 3), "fill": fill, "stroke": stroke, "w": w}


# --- primitives --------------------------------------------------------------------------------------------

def tuft(x, y, s=1.0, colour=GRASS):
    """A clump of grass blades fanning up from (x, y)."""
    blades = [(-0.06, -0.1), (-0.025, -0.14), (0.0, -0.16), (0.03, -0.13), (0.065, -0.09)]
    return [line([(x + dx * 0.25 * s, y), (x + dx * s, y + dy * s)], colour, 0.02) for dx, dy in blades]


def bush(x, y, r=0.065, flip=False):
    """A shrub: low, wide and lumpy: an irregular leafy top (five overlapping leaf-mounds drawn as one
    silhouette) on a flat base, a thin shadow along the base, a highlight and a few leaf marks.
    r is roughly the half-height; flip mirrors it (the tallest mound leans the other way)."""
    w = r * 1.6                                               # half-width
    m = -1 if flip else 1
    lobes = [(x - m * w * 0.72, y - r * 0.5, r * 0.55), (x - m * w * 0.38, y - r * 0.8, r * 0.65),
             (x + m * w * 0.12, y - r * 1.15, r * 0.9), (x + m * w * 0.6, y - r * 0.75, r * 0.6)]
    top = []
    for i in range(33):
        px = x - w + 2 * w * i / 32
        ys = [cy - math.sqrt(max(rr * rr - (px - cx) ** 2, 0)) for cx, cy, rr in lobes if abs(px - cx) <= rr]
        top.append((px, min(ys) if ys else y))
    shape = top + [(x + w, y), (x - w, y)]
    shadow = [(px, max(py, y - r * 0.25)) for px, py in top] + [(x + w, y), (x - w, y)]
    out = [poly(shape, BUSH, "none"),
           poly(shadow, BUSH_DARK, "none"),
           poly(shape, "none", BUSH_DARK, 0.012),
           circle(x - m * w * 0.38, y - r * 1.15, r * 0.2, BUSH_LIGHT),
           circle(x + m * w * 0.02, y - r * 1.7, r * 0.2, BUSH_LIGHT)]
    for lx, ly in ((x - m * w * 0.5, y - r * 0.55), (x + m * w * 0.15, y - r * 0.8), (x + m * w * 0.6, y - r * 0.5)):
        out.append(line([(lx - r * 0.16, ly - r * 0.14), (lx, ly), (lx + r * 0.16, ly - r * 0.14)], BUSH_DARK, 0.011))
    return out


def conifer(x, y, h=0.26):
    """Two-tier fir with a trunk; (x, y) is the foot of the trunk."""
    w = h * 0.36
    trunk = line([(x, y), (x, y - h * 0.18)], TRUNK, 0.025)
    low = poly([(x - w, y - h * 0.18), (x + w, y - h * 0.18), (x, y - h * 0.68)], PINE, PINE_DARK, 0.014)
    high = poly([(x - w * 0.75, y - h * 0.48), (x + w * 0.75, y - h * 0.48), (x, y - h)], PINE, PINE_DARK, 0.014)
    return [trunk, low, high]


def broadleaf(x, y, h=0.26):
    """A leafy tree: a tapering trunk with two branches into a lumpy crown of overlapping leaf clusters, outlined
    as one shape (dark clusters drawn a little larger underneath), lit from the upper left."""
    r = h * 0.19
    cx, cy = x, y - h + r * 1.9
    lobes = [(cx - r * 0.95, cy + r * 0.25, r * 0.78), (cx + r * 0.95, cy + r * 0.3, r * 0.74),
             (cx - r * 0.35, cy - r * 0.55, r * 0.85), (cx + r * 0.45, cy - r * 0.5, r * 0.8),
             (cx, cy + r * 0.45, r * 0.85), (cx + r * 0.05, cy - r * 0.05, r * 0.9)]
    out = [poly([(x - 0.018, y), (x - 0.009, y - h * 0.42), (x + 0.009, y - h * 0.42), (x + 0.018, y)], TRUNK),
           line([(x, y - h * 0.3), (x - r * 0.7, cy + r * 0.3)], TRUNK, 0.014),
           line([(x, y - h * 0.34), (x + r * 0.7, cy + r * 0.35)], TRUNK, 0.014)]
    out += [circle(lx, ly, lr + 0.012, LEAF_DARK) for lx, ly, lr in lobes]
    out += [circle(lx, ly, lr, LEAF) for lx, ly, lr in lobes]
    out += [circle(cx - r * 0.55, cy - r * 0.75, r * 0.38, LEAF_LIGHT), circle(cx - r * 1.15, cy + r * 0.05, r * 0.28, LEAF_LIGHT),
            circle(cx + r * 0.2, cy - r * 0.2, r * 0.22, LEAF_LIGHT)]
    return out


def palm(x, y, h=0.27):
    """An upright palm: a ringed trunk and a crown of drooping fronds, each outlined so it shows on dark jungle."""
    top = (x, y - h)
    out = [line([(x, y), top], TRUNK, 0.03)]
    out += [line([(x - 0.013, y - h * f), (x + 0.013, y - h * f)], "#4a321c", 0.008) for f in (0.2, 0.4, 0.6, 0.8)]
    fronds = []
    for ang, droop in ((180, 0.5), (215, 0.25), (250, 0.05), (290, 0.05), (325, 0.25), (360, 0.5)):
        a = math.radians(ang)
        mid = (top[0] + math.cos(a) * h * 0.24, top[1] + math.sin(a) * h * 0.22)
        tip = (top[0] + math.cos(a) * h * 0.46, top[1] + math.sin(a) * h * 0.2 + h * droop * 0.45)
        fronds.append([top, mid, tip])
    out += [line(f, JUNGLE_DARK, 0.042) for f in fronds]
    out += [line(f, FROND, 0.026) for f in fronds]
    return out + [circle(top[0], top[1], 0.02, JUNGLE_DARK)]


def undergrowth(x, y, s=1.0):
    """Jungle floor: broad fern fronds fanning from the ground, outlined so they show on dark green."""
    fronds = [[(x, y), (x + dx * 0.5 * s, y + dy * 0.6 * s), (x + dx * s, y + dy * s)]
              for dx, dy in ((-0.08, -0.03), (-0.05, -0.08), (0.0, -0.1), (0.05, -0.08), (0.08, -0.03))]
    return [line(f, JUNGLE_DARK, 0.034) for f in fronds] + [line(f, FROND, 0.018) for f in fronds]


def mound(x, y, w=0.16, h=0.12, fill=HILL, edge=HILL_DARK, light=HILL_LIGHT):
    """A filled hill: a smooth dome with a highlight along its left shoulder."""
    pts = [(x - w + 2 * w * t / 12, y - h * math.sin(math.pi * t / 12)) for t in range(13)]
    hl = [(x - w * 0.75 + w * 0.7 * t / 6, y - h * 0.85 * math.sin(math.pi * (0.12 + 0.38 * t / 6))) for t in range(7)]
    return [poly(pts, fill, edge, 0.018), line(hl, light, 0.022)]


def mountain(x, y, w=0.17, h=0.3, lit=ROCK_LIGHT, shade=ROCK, edge=ROCK_DARK, cap=False):
    """A peak with a lit (left) and a shaded (right) face; optional jagged snow cap."""
    peak = (x + w * 0.08, y - h)
    out = [poly([(x - w, y), peak, (x + w * 0.05, y)], lit),
           poly([peak, (x + w, y), (x + w * 0.05, y)], shade),
           poly([(x - w, y), peak, (x + w, y)], "none", edge, 0.018)]
    if cap:
        k = 0.36
        out.append(poly([peak, (peak[0] + w * k, y - h * (1 - k)), (peak[0] + w * 0.14, y - h * (1 - k * 0.7)),
                         (peak[0] - w * 0.06, y - h * (1 - k)), (peak[0] - w * 0.2, y - h * (1 - k * 0.62)),
                         (peak[0] - w * k, y - h * (1 - k))], SNOW, SNOW_EDGE, 0.012))
    return out


def waves(x, y, w=0.15, colour=WATER_LIGHT):
    pts = [(x - w + 2 * w * t / 16, y - 0.025 * math.sin(t / 16 * 4 * math.pi)) for t in range(17)]
    return [line(pts, colour, 0.026)]


def water_line(x, y, w=0.12):
    return [line([(x - w, y), (x + w, y)], WATER, 0.022)]


def reeds(x, y, s=1.0):
    out = [line([(x + dx * s, y), (x + dx * 1.4 * s, y - hgt * s)], REED, 0.02 * max(s, 0.5))
           for dx, hgt in ((-0.04, 0.15), (0.0, 0.19), (0.04, 0.14))]
    out.append(circle(x + 0.0, y - 0.19 * s, 0.016 * max(s, 0.5), CATTAIL))
    return out


def dune(x, y, w=0.13):
    pts = [(x - w + 2 * w * t / 10, y - 0.06 * math.sin(math.pi * t / 10) ** 1.5) for t in range(11)]
    return [line(pts, SAND_DARK, 0.022)]


def dot(x, y, r=0.014, colour=SAND_DARK):
    return [circle(x, y, r, colour)]


def rock(x, y, s=1.0):
    """A small boulder sitting on the ground at (x, y): its foot is set a little into the slope, so no edge
    hangs in the air; the right face is in shade."""
    y += 0.008 * s
    return [poly([(x - 0.045 * s, y), (x - 0.03 * s, y - 0.04 * s), (x + 0.005 * s, y - 0.055 * s),
                  (x + 0.04 * s, y - 0.03 * s), (x + 0.045 * s, y)], "#cfc3b1", ROCKHILL_DARK, 0.012),
            poly([(x + 0.005 * s, y - 0.055 * s), (x + 0.04 * s, y - 0.03 * s), (x + 0.045 * s, y), (x + 0.01 * s, y)],
                 "#8f8270")]


def cracks(x, y, colour=CRACK):
    return [line([(x - 0.2, y - 0.04), (x - 0.07, y - 0.1), (x + 0.04, y - 0.02), (x + 0.2, y - 0.09)], colour, 0.02),
            line([(x - 0.07, y - 0.1), (x - 0.09, y + 0.03)], colour, 0.016),
            line([(x + 0.04, y - 0.02), (x + 0.1, y + 0.05)], colour, 0.016)]


def volcano(x, y):
    w, h = 0.22, 0.3
    return [poly([(x - w, y), (x - 0.05, y - h), (x + 0.05, y - h), (x + w, y)], "#6e5348", "#3b2a24", 0.018),
            poly([(x - w, y), (x - 0.05, y - h), (x, y - h), (x - 0.05, y)], "#8a6b5c"),
            poly([(x - 0.05, y - h), (x + 0.05, y - h), (x + 0.03, y - h + 0.04), (x - 0.03, y - h + 0.04)], LAVA),
            circle(x + 0.03, y - h - 0.07, 0.04, "#d9d9d9", ASH, 0.012),
            circle(x + 0.09, y - h - 0.14, 0.05, "#e6e6e6", ASH, 0.012)]


def mudcracks(x, y, w=0.22):
    """Dried, cracked earth seen at a low angle: a flat network of cracks, wider than it is deep."""
    k = lambda pts: [(x + px * w, y + py * w * 0.5) for px, py in pts]
    runs = [[(-1.0, 0.1), (-0.6, -0.2), (-0.15, -0.05), (0.3, -0.3), (0.8, -0.1), (1.0, 0.15)],
            [(-0.85, 0.5), (-0.4, 0.35), (0.05, 0.55), (0.5, 0.35), (0.9, 0.55)],
            [(-0.6, -0.2), (-0.5, 0.35)], [(-0.15, -0.05), (-0.05, 0.5)], [(0.3, -0.3), (0.45, 0.4)],
            [(0.8, -0.1), (0.75, 0.45)], [(-0.4, 0.35), (-0.55, 0.75)], [(0.5, 0.35), (0.35, 0.8)]]
    return [line(k(r), DRY_DARK, 0.014) for r in runs]


def deadshrub(x, y, s=1.0):
    """A leafless twiggy shrub."""
    st = lambda pts: line([(x + px * s, y + py * s) for px, py in pts], DRY_DARK, 0.014)
    return [st([(0, 0), (0, -0.06)]), st([(0, -0.03), (-0.05, -0.08)]), st([(0, -0.04), (0.05, -0.1)]),
            st([(-0.03, -0.06), (-0.035, -0.11)]), st([(0, -0.06), (0.01, -0.12)]), st([(0.03, -0.07), (0.07, -0.08)])]


def drygrass(x, y, s=1.0):
    return tuft(x, y, s, DRYGRASS)


def stone(x, y, r=0.022, colour=GRAVEL, edge=GRAVEL_DARK):
    """A pebble lying flat: a squashed oval with a dark underside."""
    return [{"k": "poly", "pts": _p([(x + r * math.cos(a), y + r * 0.55 * math.sin(a)) for a in
                                       [i * math.pi / 8 for i in range(16)]]), "fill": colour, "stroke": edge, "w": 0.008}]


def drift(x, y, w=0.2, fill="#c2beaf", light="#e2dfd4"):
    """A low rounded mound of grit and gravel (cold desert), lit along its top."""
    pts = [(x - w + 2 * w * t / 14, y - 0.07 * math.sin(math.pi * t / 14) ** 0.8) for t in range(15)]
    hl = [(x - w * 0.7 + w * 0.9 * t / 6, y - 0.06 * math.sin(math.pi * (0.15 + 0.45 * t / 6)) ** 0.8) for t in range(7)]
    return [poly(pts, fill, GRAVEL_DARK, 0.014), line(hl, light, 0.016)]


def sage(x, y, s=1.0):
    """A low grey-green desert shrub: an uneven, open clump of small leafy tufts on a few twigs."""
    lobes = [(x - 0.045 * s, y - 0.018 * s, 0.022 * s), (x - 0.015 * s, y - 0.045 * s, 0.026 * s),
             (x + 0.022 * s, y - 0.035 * s, 0.024 * s), (x + 0.05 * s, y - 0.014 * s, 0.018 * s),
             (x + 0.005 * s, y - 0.012 * s, 0.02 * s)]
    twigs = [line([(x, y), (lx, ly)], "#5f6440", 0.01) for lx, ly, _ in lobes]
    return (twigs + [circle(lx, ly, lr + 0.007, "#5f6440") for lx, ly, lr in lobes]
            + [circle(lx, ly, lr, "#9aa070") for lx, ly, lr in lobes]
            + [circle(x - 0.02 * s, y - 0.055 * s, 0.009 * s, "#c3c79a")])


def dirt(x, y, w=0.06, colour="#9a8463"):
    """A bare patch of earth lying flat."""
    return [poly([(x + w * math.cos(a), y + w * 0.35 * math.sin(a) + (0.004 if i % 3 else -0.004))
                  for i, a in enumerate(i * math.pi / 7 for i in range(14))], colour)]


def floe(x, y, w, d=0.05, tilt=0.0):
    """A flat plate of ice seen from a low angle: a white top and a blue edge showing its thickness."""
    top = [(x - w, y + tilt), (x - w * 0.7, y - d), (x + w * 0.6, y - d - tilt * 0.5), (x + w, y - tilt), (x + w * 0.5, y + d * 0.6),
           (x - w * 0.6, y + d * 0.6 + tilt * 0.3)]
    edge = [(x - w, y + tilt), (x - w * 0.6, y + d * 0.6 + tilt * 0.3), (x + w * 0.5, y + d * 0.6), (x + w, y - tilt),
            (x + w, y - tilt + 0.02), (x + w * 0.5, y + d * 0.6 + 0.022), (x - w * 0.6, y + d * 0.6 + tilt * 0.3 + 0.022),
            (x - w, y + tilt + 0.018)]
    return [poly(edge, ICE_SHADE, ICE_LINE, 0.01), poly(top, ICE, ICE_LINE, 0.012)]


def mudflat(x, y, w=0.1, seed=0):
    """A patch of dried mud lying flat: paler earth broken into plates by dark cracks."""
    out = dirt(x, y, w, "#cdb47e")
    d = w * 0.35
    cracks = [[(-0.85, 0.05), (-0.4, -0.1), (0.05, 0.02), (0.5, -0.12), (0.85, 0.0)],
              [(-0.6, 0.55), (-0.15, 0.35), (0.3, 0.55)], [(-0.4, -0.1), (-0.45, 0.45)], [(0.05, 0.02), (0.15, 0.6)],
              [(0.5, -0.12), (0.6, 0.4)], [(-0.15, -0.65), (-0.1, -0.05)], [(0.3, -0.7), (0.4, -0.1)]]
    if seed:
        cracks = [[(-px, py) for px, py in c] for c in cracks]
    for c in cracks:
        out.append(line([(x + px * w * 0.95, y + py * d) for px, py in c], DRY_DARK, 0.009))
    return out


def sanddune(x, y, w=0.15, h=0.07, flip=False):
    """A dune: a long, rounded sunlit windward slope up to a sharp crest, and a short steep lee face in shade.
    Only the crest is inked; the base fades into the sand."""
    m = -1 if flip else 1
    xs = lambda t: x - m * w + m * (w * 1.3) * t   # windward foot (t=0) to crest (t=1)
    top = [(xs(t / 10), y - h * (1 - (1 - t / 10) ** 2)) for t in range(11)]
    crest = top[-1]
    lee = [crest, (x + m * w * 0.62, y), (x + m * w * 0.12, y)]
    return [poly(top + [(x + m * w * 0.3, y)], "#e3c27e"), poly(lee, "#bf964c"),
            line(top[2:], SAND_DARK, 0.014), line([crest, (x + m * w * 0.62, y)], "#a8823e", 0.01)]


def desert(dunes, n=40, seed=1):
    """Dunes with fine sand grains scattered over the open sand between and in front of them: never on a dune,
    nor just above one."""
    out, spans = [], []
    for d in dunes:
        parts = sanddune(*d)
        out += parts
        xs = [px for sh in parts[:2] for px, _ in sh["pts"]]
        spans.append((min(xs) - 0.02, max(xs) + 0.02, d[1]))   # a dune's width and the ground it stands on
    rnd = lambda k: (math.sin(k * 12.9898 + seed * 78.233) * 43758.5453) % 1.0
    for i in range(n * 3):
        if sum(1 for sh in out if sh.get("r") == 0.0045) >= n:
            break
        x, y = -0.33 + 0.66 * rnd(i), G - 0.04 + 0.14 * rnd(i + 500)
        # only on open sand: below the foot of any dune it lines up with, and no higher than the dunes' feet elsewhere
        if any(x0 <= x <= x1 and y < foot + 0.012 for x0, x1, foot in spans) or y < min(f for _, _, f in spans):
            continue
        out.append(circle(x, y, 0.0045, SAND_DARK))
    return out


def grains(x, y, w=0.3, n=14, seed=1):
    """Sand: many tiny scattered grains."""
    out = []
    rnd = lambda k: (math.sin(k * 12.9898 + seed * 78.233) * 43758.5453) % 1.0   # repeatable scatter
    for i in range(n):
        a = rnd(i) * 2 * math.pi
        r = rnd(i + 100) ** 0.6
        out.append(circle(x + math.cos(a) * r * w, y + math.sin(a) * r * w * 0.22, 0.0045, SAND_DARK))
    return out


def ice_block(x, y, s=1.0):
    """A jagged block of ice on the ice sheet: a lit face and a blue shaded face, with a crisp outline."""
    top = (x - 0.01 * s, y - 0.11 * s)
    out = [poly([(x - 0.07 * s, y), (x - 0.06 * s, y - 0.05 * s), top, (x + 0.03 * s, y - 0.08 * s), (x + 0.06 * s, y)],
                ICE, ICE_LINE, 0.012),
           poly([top, (x + 0.03 * s, y - 0.08 * s), (x + 0.06 * s, y), (x + 0.0, y)], ICE_SHADE)]
    return out + [line([top, (x, y)], ICE_LINE, 0.01)]


def ice(x, y):
    """Polar ice: one broad sheet of ice with a crack across it and blocks pushed up on it."""
    return (floe(x, y + 0.01, 0.3, 0.075, 0.0)
            + [line([(x - 0.2, y + 0.03), (x - 0.08, y + 0.005), (x + 0.03, y + 0.035), (x + 0.16, y + 0.015)], ICE_LINE, 0.01),
               line([(x + 0.03, y + 0.035), (x + 0.05, y + 0.065)], ICE_LINE, 0.008)]
            + ice_block(x + 0.1, y - 0.03, 0.9) + ice_block(x - 0.16, y - 0.015, 0.65) + ice_block(x + 0.22, y + 0.0, 0.5))


# --- the terrain set ---------------------------------------------------------------------------------------

G = GROUND


HILLS = [(0.02, G, 0.16, 0.165), (-0.13, G, 0.15, 0.12), (0.15, G + 0.02, 0.14, 0.1)]   # (x, foot, w, h), back first


def _hills(fill=HILL, edge=HILL_DARK, light=HILL_LIGHT):
    """Three hills: a smaller one behind, rising between the two in front."""
    return sum((mound(x, y, w, h, fill, edge, light) for x, y, w, h in HILLS), [])


def on_hill(i, x):
    """The height of hill i's slope at x (so things placed on it stand on the ground)."""
    hx, hy, w, h = HILLS[i]
    t = min(max((x - (hx - w)) / (2 * w), 0.0), 1.0)
    return hy - h * math.sin(math.pi * t)


def _wood():
    """Five broadleaves, back to front and uneven: three smaller ones behind, a middling one to the left, the
    biggest in front on the right."""
    return (broadleaf(0.03, G - 0.07, 0.21) + broadleaf(-0.31, G - 0.05, 0.18) + broadleaf(0.27, G - 0.04, 0.19)
            + broadleaf(-0.19, G - 0.02, 0.24) + broadleaf(0.16, G + 0.03, 0.27))


def _wooded_hills(tree, behind=0.19, on=0.15, front=0.21):
    """Two hills with three trees, placed unevenly: one rising from behind the near (left) hill's right slope, its
    foot hidden by it; one on the far hill's outer flank, off the peak; one in front at the near hill's foot."""
    return (tree(-0.06, G - 0.06, behind) + _hills()
            + tree(0.245, on_hill(2, 0.245), on) + tree(-0.27, G + 0.035, front))


ICONS = {
    # grassland
    "PR": tuft(-0.24, G, 0.9) + tuft(-0.06, G - 0.06, 1.1) + tuft(0.13, G, 1.0) + tuft(0.28, G - 0.05, 0.8),
    "PPR": tuft(-0.27, G, 0.8, GRASS_LIGHT) + tuft(-0.12, G - 0.06, 1.0, GRASS) + tuft(0.03, G, 1.1, GRASS_LIGHT)
           + tuft(0.17, G - 0.06, 0.9, GRASS) + tuft(0.3, G, 0.8, GRASS_LIGHT),
    "TU": dirt(-0.05, G + 0.02, 0.08) + dirt(0.2, G + 0.04, 0.05) + dirt(-0.25, G - 0.03, 0.045)
          + tuft(-0.17, G, 0.7, "#7f8f6a") + tuft(0.15, G - 0.03, 0.7, "#7f8f6a") + tuft(-0.31, G + 0.03, 0.55, "#7f8f6a")
          + tuft(0.02, G - 0.05, 0.5, "#7f8f6a") + tuft(0.3, G + 0.01, 0.6, "#7f8f6a")
          + stone(-0.06, G + 0.03, 0.016, "#a5a597", "#77776c") + stone(0.08, G + 0.045, 0.012, "#a5a597", "#77776c"),
    # brush
    "BF": bush(-0.04, G - 0.05, 0.07) + bush(-0.2, G, 0.06, True) + bush(0.12, G, 0.055) + tuft(0.27, G - 0.02, 0.7)
          + tuft(-0.32, G + 0.02, 0.6),
    "BR": bush(0.02, G - 0.04, 0.07) + bush(-0.15, G, 0.06, True) + bush(0.18, G, 0.05),   # the middle one behind
    # hills
    "GH": _hills() + tuft(-0.05, G - 0.08, 0.6, GRASS_DARK) + tuft(0.25, G - 0.025, 0.5, GRASS_DARK)
          + tuft(-0.27, G + 0.03, 0.75, GRASS_DARK),
    "GHP": mound(0, G, 0.27, 0.13) + tuft(-0.13, G - 0.095, 0.6, GRASS_DARK) + tuft(0.17, G - 0.06, 0.5, GRASS_DARK)
           + tuft(0.31, G + 0.03, 0.75, GRASS_DARK),
    "BH": _hills() + bush(-0.25, G + 0.02, 0.05, True) + bush(-0.13, G + 0.035, 0.032) + bush(0.25, G + 0.04, 0.038),
    "CH": _wooded_hills(conifer),
    "DH": _wooded_hills(broadleaf),
    "JH": _wooded_hills(palm, 0.17, 0.15, 0.2) + undergrowth(-0.17, G + 0.04, 0.8) + undergrowth(0.06, G + 0.035, 0.7),
    "RH": _hills(ROCKHILL, ROCKHILL_DARK, "#cfc3b1")
          # small rocks at the foot and halfway up the slopes, none on the tops
          + sum((rock(x, on_hill(i, x), sc) for i, x, sc in ((0, 0.13, 0.4), (1, -0.24, 0.45), (1, -0.035, 0.4),
                                                               (2, 0.05, 0.4), (2, 0.25, 0.38))), [])
          + rock(-0.3, G + 0.03, 0.5) + rock(-0.19, G + 0.03, 0.4) + rock(0.1, G + 0.045, 0.42) + rock(0.31, G + 0.04, 0.45),
    "AH": _hills(SANDHILL, SANDHILL_DARK, "#e3cb94") + drygrass(-0.06, on_hill(1, -0.06), 0.45)
          + drygrass(0.22, on_hill(2, 0.22), 0.4) + stone(0.09, on_hill(2, 0.09) + 0.01, 0.018, "#e3cb94", SANDHILL_DARK)
          + stone(-0.2, on_hill(1, -0.2) + 0.012, 0.016, "#e3cb94", SANDHILL_DARK) + drygrass(-0.31, G + 0.03, 0.55),
    "SH": _hills("#ffffff", SNOW_EDGE, "#dfe9f1"),
    # forest
    "D": _wood(),
    "DF": _wood(),
    "JG": palm(-0.06, G - 0.06, 0.24) + palm(0.27, G - 0.03, 0.2) + palm(-0.23, G + 0.01, 0.28) + palm(0.12, G + 0.03, 0.3)
          + undergrowth(-0.13, G + 0.045, 0.85) + undergrowth(0.03, G + 0.035, 0.55) + undergrowth(0.25, G + 0.05, 0.75),
    "UJS": broadleaf(-0.29, G - 0.04, 0.18) + broadleaf(0.1, G - 0.06, 0.2) + broadleaf(0.27, G - 0.02, 0.17)
           + broadleaf(-0.13, G - 0.01, 0.25) + reeds(0.02, G + 0.03, 0.3)
           + water_line(-0.02, G + 0.04, 0.2) + water_line(0.06, G + 0.09, 0.12),   # the swamp's water marks and a small reed
    # wetland
    "SW": reeds(-0.15, G) + reeds(0.12, G - 0.02) + water_line(-0.02, G + 0.04, 0.2) + water_line(0.06, G + 0.09, 0.12),
    # dry
    "DE": desert([(0.1, G - 0.04, 0.19, 0.09), (-0.17, G + 0.015, 0.2, 0.1), (0.23, G + 0.05, 0.12, 0.055)], 0),   # all facing one way
    "AR": mudflat(-0.12, G + 0.0, 0.13) + mudflat(0.15, G - 0.04, 0.1, 1) + mudflat(0.08, G + 0.06, 0.08)
          + drygrass(-0.3, G + 0.03, 0.55) + drygrass(0.3, G + 0.0, 0.5) + drygrass(-0.02, G - 0.06, 0.4)
          + stone(-0.27, G + 0.065, 0.016, "#c9b07a", DRY_DARK) + stone(0.24, G + 0.06, 0.014, "#c9b07a", DRY_DARK),
    "UL": drift(0.1, G - 0.03, 0.2) + drift(-0.12, G, 0.17, "#b7b3a3", "#d8d5c9")
          + stone(-0.27, G + 0.04, 0.03) + stone(-0.03, G + 0.05, 0.024) + stone(0.2, G + 0.05, 0.028)
          + stone(0.31, G + 0.02, 0.02) + tuft(0.26, G - 0.03, 0.5, "#8f9688") + tuft(-0.31, G - 0.0, 0.45, "#8f9688"),
    # mountains
    "LCM": conifer(0.2, G - 0.06, 0.15) + mountain(-0.08, G, 0.17, 0.3) + mountain(0.12, G, 0.14, 0.22)
           + conifer(-0.24, G + 0.03, 0.18),   # one peeking from behind the lower peak, one in front
    "LJM": palm(0.2, G - 0.06, 0.15) + mountain(-0.08, G, 0.17, 0.3) + mountain(0.12, G, 0.14, 0.22)
           + palm(-0.27, G + 0.03, 0.17) + palm(0.27, G + 0.03, 0.16),
    "UM": conifer(0.2, G - 0.07, 0.14) + mountain(-0.08, G, 0.17, 0.3) + mountain(0.12, G, 0.14, 0.22)
          + conifer(-0.26, G + 0.03, 0.17) + broadleaf(0.27, G + 0.03, 0.17),   # mixed: conifers and a leafy tree
    "LAM": mountain(-0.08, G, 0.17, 0.3, "#d8bf92", "#b09160", "#6e5732")
           + mountain(0.12, G, 0.14, 0.22, "#d8bf92", "#b09160", "#6e5732"),
    "LSM": mountain(-0.08, G, 0.17, 0.3, SNOWROCK_LIGHT, SNOWROCK, "#5a6672", cap=True)
           + mountain(0.12, G, 0.14, 0.23, SNOWROCK_LIGHT, SNOWROCK, "#5a6672", cap=True),
    "HSM": mountain(-0.17, G, 0.15, 0.27, SNOWROCK_LIGHT, SNOWROCK, "#5a6672", cap=True)
           + mountain(0.17, G, 0.15, 0.26, SNOWROCK_LIGHT, SNOWROCK, "#5a6672", cap=True)
           + mountain(0.0, G + 0.02, 0.19, 0.36, SNOWROCK_LIGHT, SNOWROCK, "#5a6672", cap=True),
    "ALPS": mountain(-0.2, G, 0.13, 0.3, SNOWROCK_LIGHT, SNOWROCK, "#4a5662", cap=True)
            + mountain(0.2, G, 0.13, 0.28, SNOWROCK_LIGHT, SNOWROCK, "#4a5662", cap=True)
            + mountain(0.0, G + 0.02, 0.17, 0.42, SNOWROCK_LIGHT, SNOWROCK, "#4a5662", cap=True),
    "LVM": volcano(0, G),
    # ice and water
    "PI": ice(0, G),
    "L": waves(-0.05, G - 0.06) + waves(0.06, G + 0.03),
    "O": waves(-0.06, G - 0.16) + waves(0.06, G - 0.08) + waves(-0.03, G + 0.0),   # clear of the code below
    "UW": waves(-0.06, G - 0.16) + waves(0.06, G - 0.08) + waves(-0.03, G + 0.0),
}


# Drawn at a compact size above, then scaled up about the icon band's centre so they read at normal zoom.
SCALE, CX, CY = 1.5, 0.0, 0.25


def _scaled(shape):
    out = dict(shape)
    f = lambda p: [round(CX + (p[0] - CX) * SCALE, 3), round(CY + (p[1] - CY) * SCALE, 3)]
    if "pts" in shape:
        out["pts"] = [f(p) for p in shape["pts"]]
    if "c" in shape:
        out["c"] = f(shape["c"])
        out["r"] = round(shape["r"] * SCALE, 3)
    out["w"] = round(shape.get("w", W) * 1.25, 4)
    return out


LOWEST = 0.5   # the terrain code sits just below this: no icon reaches past it


def _lifted(shapes):
    """Move an icon up just enough that its lowest point clears the terrain code."""
    low = max(p[1] + (sh.get("r", 0) if "c" in sh else 0)
              for sh in shapes for p in (sh.get("pts") or [sh["c"]]))
    dy = min(0.0, LOWEST - low)
    if not dy:
        return shapes
    out = []
    for sh in shapes:
        sh = dict(sh)
        if "pts" in sh:
            sh["pts"] = [[x, round(y + dy, 3)] for x, y in sh["pts"]]
        if "c" in sh:
            sh["c"] = [sh["c"][0], round(sh["c"][1] + dy, 3)]
        out.append(sh)
    return out


ICONS = {code: _lifted([_scaled(sh) for sh in shapes]) for code, shapes in ICONS.items()}
