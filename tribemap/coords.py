"""Hex coordinates for the TribeNet world map.

Map sheets are named by two letters: the first is the sheet's row (A at the top),
the second its column. Each sheet is SHEET_COLS x SHEET_ROWS hexes, numbered from 1.
A hex label like "QB 1017" is sheet QB, column 10, row 17.

Internally a hex is a world-grid tuple (gx, gy):
    gx = sheet_col_index * SHEET_COLS + col
    gy = sheet_row_index * SHEET_ROWS + row
Hexes are flat-topped and odd columns sit half a hex higher (checked against the
GM's map). SHEET_COLS is even, so column parity is the same in sheet and world.
"""
import re

SHEET_COLS = 30  # assumed, not yet confirmed
SHEET_ROWS = 21  # confirmed: LC row 21 is followed by MC row 01

DIRS = ("N", "NE", "SE", "S", "SW", "NW")
OPPOSITE = {"N": "S", "NE": "SW", "SE": "NW", "S": "N", "SW": "NE", "NW": "SE"}

_LABEL = re.compile(r"^\s*([A-Z]{2})\s*(\d{2})(\d{2})\s*$")


def parse(label):
    """'QB 1017' -> (gx, gy). Raises ValueError for anything else (e.g. 'N/A', '## 1017')."""
    m = _LABEL.match(label.upper())
    if not m:
        raise ValueError(f"not a hex label: {label!r}")
    sheet, col, row = m.group(1), int(m.group(2)), int(m.group(3))
    if not (1 <= col <= SHEET_COLS and 1 <= row <= SHEET_ROWS):
        raise ValueError(f"column/row out of range: {label!r}")
    srow, scol = ord(sheet[0]) - ord("A"), ord(sheet[1]) - ord("A")
    return scol * SHEET_COLS + col, srow * SHEET_ROWS + row


def try_parse(label):
    try:
        return parse(label)
    except ValueError:
        return None


def sheet_centre(sheet):
    """'DO' -> 'DO 1511': the middle hex of a map sheet, for a position known only to the sheet."""
    sheet = sheet.strip().upper()
    if not (len(sheet) == 2 and sheet.isalpha()):
        raise ValueError(f"bad map sheet {sheet!r}; use two letters, e.g. DO")
    return f"{sheet} {(SHEET_COLS + 1) // 2:02d}{(SHEET_ROWS + 1) // 2:02d}"


def label(h):
    """(gx, gy) -> 'QB 1017'."""
    gx, gy = h
    scol, col = divmod(gx - 1, SHEET_COLS)
    srow, row = divmod(gy - 1, SHEET_ROWS)
    return f"{chr(ord('A') + srow)}{chr(ord('A') + scol)} {col + 1:02d}{row + 1:02d}"


def sheet(h):
    """The map sheet a hex is on: (gx, gy) -> 'LC'."""
    return label(h)[:2]


def sheet_borders(hexes):
    """Canonical hexsides (hex, dir) where a hex in `hexes` meets a hex on another map sheet."""
    out = set()
    for h in hexes:
        for d in DIRS:
            n = step(h, d)
            if n[0] >= 1 and n[1] >= 1 and sheet(n) != sheet(h):
                out.add(edge(h, d))
    return out


def step(h, d):
    """The neighbouring hex in direction d."""
    x, y = h
    odd = x % 2 == 1
    return {
        "N": (x, y - 1), "S": (x, y + 1),
        "NE": (x + 1, y - 1) if odd else (x + 1, y),
        "SE": (x + 1, y) if odd else (x + 1, y + 1),
        "NW": (x - 1, y - 1) if odd else (x - 1, y),
        "SW": (x - 1, y) if odd else (x - 1, y + 1),
    }[d]


def neighbours(h):
    return [step(h, d) for d in DIRS]


def _axial(h):
    x, y = h
    return x, y - (x + (x & 1)) // 2


def distance(a, b):
    aq, ar = _axial(a)
    bq, br = _axial(b)
    dq, dr = bq - aq, br - ar
    return (abs(dq) + abs(dr) + abs(dq + dr)) // 2


def _from_axial(q, r):
    return q, r + (q + (q & 1)) // 2


def line(a, b):
    """Hexes on the straight line from a to b, both included (cube-coordinate interpolation)."""
    n = distance(a, b)
    if n == 0:
        return [a]
    (aq, ar), (bq, br) = _axial(a), _axial(b)
    out = []
    for i in range(n + 1):
        t = i / n
        # nudge so lines along hex edges always round the same way
        q, r = aq + (bq - aq) * t + 1e-6, ar + (br - ar) * t + 2e-6
        s = -q - r
        rq, rr, rs = round(q), round(r), round(s)
        dq, dr, ds = abs(rq - q), abs(rr - r), abs(rs - s)
        if dq > dr and dq > ds:
            rq = -rr - rs
        elif dr > ds:
            rr = -rq - rs
        out.append(_from_axial(rq, rr))
    return out


def direction(a, b):
    """The direction from a to its neighbour b (None if they aren't neighbours)."""
    for d in DIRS:
        if step(a, d) == b:
            return d
    return None


def path_dirs(a, b):
    """Directions that walk the straight line from a to b."""
    pts = line(a, b)
    return [direction(p, q) for p, q in zip(pts, pts[1:])]


def within(h, radius):
    """All hexes within radius of h (including h)."""
    out = {h}
    frontier = {h}
    for _ in range(radius):
        frontier = {n for f in frontier for n in neighbours(f)} - out
        out |= frontier
    return out


def edge(h, d):
    """Canonical key for the hexside of h in direction d: stored on the N, NE or SE side."""
    if d in ("N", "NE", "SE"):
        return h, d
    return step(h, d), OPPOSITE[d]


def centre(h):
    """Drawing position of a hex centre (hex radius 1, y up)."""
    x, y = h
    return x * 1.5, -(y * 3 ** 0.5 - (3 ** 0.5 / 2 if x % 2 == 1 else 0))


def letters(span):
    """'A-M' -> ['A', ..., 'M']."""
    a, b = span.replace(" ", "").upper().split("-")
    return [chr(c) for c in range(ord(a), ord(b) + 1)]


def sheet_box(name):
    """Drawing rectangle (x0, y0, x1, y1) of a map sheet in SVG coordinates (y down, hex radius 1).
    Neighbouring sheets' boxes touch, so together they tile the world."""
    srow, scol = ord(name[0]) - ord("A"), ord(name[1]) - ord("A")
    r3 = 3 ** 0.5
    return ((scol * SHEET_COLS + 0.5) * 1.5, (srow * SHEET_ROWS + 0.25) * r3,
            (scol * SHEET_COLS + SHEET_COLS + 0.5) * 1.5, (srow * SHEET_ROWS + SHEET_ROWS + 0.25) * r3)
