"""Draw the map to PNG / SVG / PDF (format from the file extension).

Layers:
    terrain    hex colours and terrain codes; rivers and fords on hexsides
    icons      small terrain symbols in each hex (tribemap/icons.py, same as the browser)
    labels     hex labels (QB 1017)
    resources  deposits, specials, other clans' units seen
    units      our units' positions
    paths      scout and movement paths reported for the turn
    scouting   hatches hexes we know the terrain of but never scouted (glimpsed or passed through)
    fog        knowledge age: older scouting is greyed
    dates      the turn each hex was last known (from anyone)
    sheets     map sheet borders (dashed) and faint sheet names

Information from other players is always drawn as unverified: dashed orange outline, faded fill, '?'.
"""
import math

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon

from . import coords
from .icons import ICONS
from .queries import fog_alpha, knowledge, latest_turn, turns_between, unit_positions

ALL_LAYERS = ("terrain", "icons", "labels", "resources", "units", "paths", "scouting", "fog", "dates", "sheets")
UNKNOWN = "#ededed"
UNVERIFIED = "#e67e22"
RIVER = "#3b7dd8"
PATH_COLOURS = ["#1f3a93", "#c0392b", "#27ae60", "#8e44ad", "#d35400", "#16a085", "#2c3e50", "#b7950b"]
DARK = {"LCM", "LJM", "JG", "JH", "O", "UW", "LVM", "UM", "CH", "DH"}


def corners(h, r=1.0):
    cx, cy = coords.centre(h)
    return [(cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a))) for a in range(0, 360, 60)]


def side(h, d, r=1.0):
    """End points of the hexside of h facing d (flat-topped hex)."""
    angle = {"NE": 30, "N": 90, "NW": 150, "SW": 210, "S": 270, "SE": 330}[d]
    cx, cy = coords.centre(h)
    pts = []
    for a in (angle - 30, angle + 30):
        pts.append((cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a))))
    return pts


LANE_GAP = 0.075


def lanes(point_lists, gap=LANE_GAP):
    """Polylines through hex centres; where routes share a hex-to-hex segment, each bows out into its own
    lane (a small gap apart) mid-segment and returns to the centre at both ends. Same rule as the browser."""
    users = {}
    for ri, ps in enumerate(point_lists):
        for a, b in zip(ps, ps[1:]):
            key = tuple(sorted((a, b)))
            users.setdefault(key, [])
            if ri not in users[key]:
                users[key].append(ri)
    out = []
    for ri, ps in enumerate(point_lists):
        line = list(ps[:1])
        for a, b in zip(ps, ps[1:]):
            key = tuple(sorted((a, b)))
            n = len(users[key])
            off = (users[key].index(ri) - (n - 1) / 2) * gap
            if off:
                p, q = key   # canonical direction, so routes going opposite ways agree on lanes
                length = math.hypot(q[0] - p[0], q[1] - p[1]) or 1
                nx, ny = -(q[1] - p[1]) / length, (q[0] - p[0]) / length
                for t in (0.3, 0.7):
                    line.append((a[0] + (b[0] - a[0]) * t + nx * off, a[1] + (b[1] - a[1]) * t + ny * off))
            line.append(b)
        out.append(line)
    return out


def draw_icon(ax, code, cx, cy, ink, alpha, pt_per_unit=20):
    """Draw a terrain icon (hex-local shapes, y down) centred on the hex at (cx, cy) (y up).
    Shapes carry their own colours; "ink" is dark on light terrain and light on dark."""
    from matplotlib.patches import Circle
    paint = lambda c: ink if c == "ink" else "white" if c == "snow" else (c or "none")
    xy = lambda p: (cx + p[0], cy - p[1])
    for sh in ICONS[code]:
        stroke, lw = paint(sh.get("stroke", "ink")), sh.get("w", 0.022) * pt_per_unit
        if sh["k"] == "line":
            xs, ys = zip(*(xy(p) for p in sh["pts"]))
            ax.plot(xs, ys, color=stroke, lw=lw, alpha=alpha, solid_capstyle="round", solid_joinstyle="round",
                    zorder=2)
        elif sh["k"] == "poly":
            ax.add_patch(Polygon([xy(p) for p in sh["pts"]], closed=True, fc=paint(sh.get("fill")),
                                 ec=stroke, lw=lw if stroke != "none" else 0, alpha=alpha, zorder=2))
        else:
            ax.add_patch(Circle(xy(sh["c"]), sh["r"], fc=paint(sh.get("fill")), ec=stroke,
                                lw=lw if stroke != "none" else 0, alpha=alpha, zorder=2))


def plan_hexes(plan):
    out = set()
    for p in plan or []:
        if p.route:
            out.add(p.route.start)
            out.update(s.hex for s in p.route.steps)
    return out


def render(conn, terrain, out, as_of=None, layers=ALL_LAYERS, around=None, radius=None, title=None,
           clan=None, plan=None, plan_turn=None, overlap=(), focus=None):
    """plan: the Planned list from plans.evaluate(), drawn on top when given; overlap: its scout_overlap."""
    # With no as_of, show everything (including others' info dated after our latest report);
    # 'now' is only the reference point for knowledge age.
    now = as_of or latest_turn(conn)
    hexes, edges = knowledge(conn, as_of)
    positions = unit_positions(conn, as_of)

    if around is not None:
        view = coords.within(around, radius or 6)
    else:
        view = set(hexes) | {(r["gx"], r["gy"]) for r in positions.values()} | plan_hexes(plan)
        for h in list(view):
            view.update(coords.neighbours(h))
    if not view:
        raise ValueError("nothing to draw yet: import a report first")

    xs = [coords.centre(h)[0] for h in view]
    ys = [coords.centre(h)[1] for h in view]
    w, hgt = max(xs) - min(xs) + 2, max(ys) - min(ys) + 2
    scale = min(1.0, 22 / max(w, hgt))
    fig, ax = plt.subplots(figsize=(max(6, w * scale * 0.75), max(6, hgt * scale * 0.75)))

    for h in view:
        info = hexes.get(h)
        t = info.terrain if info else None
        fill = terrain.colour(t) if (t and "terrain" in layers) else UNKNOWN
        hatch = None
        if "scouting" in layers and info and info.knowledge == "terrain":
            hatch = "///"   # terrain known, resources never checked
        unverified = bool(info and t and not info.verified)
        ax.add_patch(Polygon(corners(h), closed=True, fc=fill, ec="#999999", lw=0.6, hatch=hatch,
                             alpha=0.5 if unverified else 1.0))
        if unverified:
            ax.add_patch(Polygon(corners(h, 0.93), closed=True, fc="none", ec=UNVERIFIED, lw=1.4, ls="--"))
        cx, cy = coords.centre(h)
        if "fog" in layers and info and info.last_scouted and info.terrain_how != "manual":
            alpha = fog_alpha(turns_between(info.last_scouted, now))
            if alpha:
                ax.add_patch(Polygon(corners(h), closed=True, fc="#808080", ec="none", alpha=alpha))
        if "labels" in layers:
            ax.text(cx, cy + 0.62, coords.label(h), ha="center", va="center", fontsize=5.5, color="#444")
        icon = "terrain" in layers and "icons" in layers and t in ICONS
        if icon:
            draw_icon(ax, t, cx, cy, "white" if t in DARK else "#2b2b2b", 0.55 if unverified else 1)
        if "terrain" in layers and t:
            ax.text(cx, cy - (0.66 if icon else 0.5), t + ("?" if unverified else ""), ha="center", va="center",
                    fontsize=5.5 if icon else 6.5,
                    color=UNVERIFIED if unverified else ("white" if t in DARK else "#333"),
                    weight="bold" if unverified else "normal")
        if "dates" in layers and info and (info.last_known or info.unverified):
            when = info.last_known or "date?"
            if info.unverified and info.last_known != info.last_seen:
                when += "?"   # the latest news is someone else's, not ours
            ax.text(cx, cy - 0.74, when, ha="center", va="center", fontsize=5,
                    color=UNVERIFIED if when.endswith("?") else "#555")
        theirs = [u for u in (info.unverified if info else []) if u["kind"] in ("special", "resource")]
        if "resources" in layers and info and (info.specials or info.resources or theirs):
            # special hexes (★ towns, encounters) and resource hexes (⚒ deposits): gold double border;
            # dashed if only others reported it
            ls = "-" if (info.specials or info.resources) else (0, (4, 2))
            special = info.specials or any(u["kind"] == "special" for u in theirs)
            ax.add_patch(Polygon(corners(h, 0.94), closed=True, fc="none", ec="#c08a00", lw=2.2, ls=ls, zorder=3))
            ax.add_patch(Polygon(corners(h, 0.8), closed=True, fc="none", ec="#c08a00", lw=0.9, ls=ls, zorder=3))
            ax.text(cx + 0.5, cy + 0.36, "★" if special else "⚒", ha="center", va="center", fontsize=8,
                    color="#c08a00", zorder=6)
        if "resources" in layers and info:
            notes = sorted(info.resources) + sorted(info.specials) + [u for u, _ in info.sightings]
            notes += [f"{u['value']}?" for u in info.unverified
                      if u["kind"] in ("resource", "special", "unit_sighting")]
            if notes:
                ax.text(cx, cy + 0.28, "\n".join(n.title() if n.isupper() else n for n in notes[:3]),
                        ha="center", va="center", fontsize=6, weight="bold", color="#5a2d0c")

    if "terrain" in layers:
        for (h, d), kinds in edges.items():
            if h not in view and coords.step(h, d) not in view:
                continue
            if "river" in kinds or "ford" in kinds or "no_ford" in kinds:
                (x1, y1), (x2, y2) = side(h, d)
                verified = any(kinds.values())
                ax.plot([x1, x2], [y1, y2], color=RIVER, lw=3.5 if verified else 2.5,
                        ls="-" if verified else "--", solid_capstyle="round")
                if "ford" in kinds:
                    ax.plot([(x1 + x2) / 2], [(y1 + y2) / 2], "s", color="#c49a5a", ms=5, mec="#333", mew=0.5)

    if "sheets" in layers:
        for h, d in coords.sheet_borders(view):
            (x1, y1), (x2, y2) = side(h, d)
            ax.plot([x1, x2], [y1, y2], color="#222", lw=1.6, ls=(0, (4, 2.5)), alpha=0.75, zorder=3)
        by_sheet = {}
        for h in view:
            by_sheet.setdefault(coords.sheet(h), []).append(coords.centre(h))
        for name, pts in by_sheet.items() if len(by_sheet) > 1 else []:
            size = min(60, 14 * len(pts) ** 0.5)
            ax.text(sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts), name, fontsize=size,
                    weight="heavy", color="black", alpha=0.08, ha="center", va="center", zorder=1)

    if "paths" in layers and now:
        rows = conn.execute("SELECT * FROM unit_path WHERE turn = ? ORDER BY unit, party, step",
                            (now,)).fetchall()
        starts = {u: r for u, r in positions.items()}
        groups = {}
        for r in rows:
            groups.setdefault((r["unit"], r["party"]), []).append(r)
        drawn = []
        for (unit, party), steps in sorted(groups.items()):
            pos = starts.get(unit)
            if pos is None:
                continue
            if party == "move":
                first = (pos["prev_gx"], pos["prev_gy"]) if pos["prev_gx"] else None
            else:
                first = (pos["gx"], pos["gy"])
            if first is None:
                continue
            ok = [s for s in steps if s["result"] == "ok"]
            drawn.append((party, steps, [coords.centre(first)] + [coords.centre((s["gx"], s["gy"])) for s in ok]))
        for i, ((party, steps, _), line) in enumerate(zip(drawn, lanes([d[2] for d in drawn]))):
            col = PATH_COLOURS[i % len(PATH_COLOURS)]
            px, py = zip(*line)
            ax.plot(px, py, color=col, lw=3 if party == "move" else 1.4, alpha=0.85)
            ax.plot(px[-1], py[-1], "o", color=col, ms=3)
            for s in steps:
                if s["result"] != "ok":
                    bx, by = coords.centre((s["gx"], s["gy"]))
                    ax.plot([px[-1], bx], [py[-1], by], color=col, lw=1, ls=":")

    if plan:
        # hexes the plan's scouts will enter that aren't fully known yet (or stale: plans.STALE turns or more)
        from .plans import STALE
        for p in plan:
            if not p.route or p.row["kind"] != "scout":
                continue
            for h in p.route.entered:
                info = hexes.get(h)
                stale = info and info.last_scouted and now and turns_between(info.last_scouted, now) >= STALE
                if not info or not info.scouted or stale:
                    ax.add_patch(Polygon(corners(h), closed=True, fc="#7a7a7a", ec="none", alpha=0.28, zorder=2))
        draw_plan(ax, plan, view, overlap, focus)

    if "units" in layers:
        here = {}
        for u, r in positions.items():
            here.setdefault((r["gx"], r["gy"]), []).append(u)
        for h, units in here.items():
            if h not in view:
                continue
            cx, cy = coords.centre(h)
            ax.text(cx, cy - 0.1, " + ".join(sorted(units)), ha="center", va="center", fontsize=7.5,
                    weight="bold", color="white", bbox=dict(fc="black", ec="none", pad=1.5))

    default = f"as of {as_of}" if as_of else f"our latest report {now}"
    if plan_turn:
        default = f"plan for {plan_turn}, map from {default}"
    ax.set_title(title or f"Clan {clan} — known map ({default})", fontsize=13, weight="bold")
    note = "Grey = unknown. "
    if plan:
        note += ("Thick arrows = unit moves; thin lines = scouts, dotted to where they stop "
                 "(glimpsed hex, or x = river with no known ford); purple outline = scout overlap.\n")
    if "scouting" in layers:
        note += "Hatched = terrain only, needs scouting (glimpsed or passed through). "
    if "fog" in layers:
        note += "Grey = scouted over a year ago (darker = longer). "
    note += "Orange dashed / ? = unverified, from other players."
    ax.text(0.5, -0.01, note, transform=ax.transAxes, ha="center", fontsize=8, color="#555")
    ax.set_aspect("equal")
    ax.autoscale_view()
    ax.axis("off")
    fig.savefig(out, dpi=140, bbox_inches="tight")
    plt.close(fig)
    return out


def draw_plan(ax, plan, view, overlap=(), focus=None):
    """Planned unit moves (thick arrows) and scout routes (thin lines) with stops, overlaps and labels."""
    from matplotlib.patches import FancyArrowPatch

    for label in overlap or ():   # plans.coverage()'s scout_overlap (shared first steps aren't counted)
        ax.add_patch(Polygon(corners(coords.parse(label), 0.86), closed=True, fc="none", ec="#8e44ad", lw=2))

    from .plans import assign_colours
    colours = assign_colours(plan)   # same colours as the browser
    scouts = [p for p in plan if p.route and p.row["kind"] == "scout"]
    lane_of = dict(zip(map(id, scouts),
                       lanes([[coords.centre(h) for h in [p.route.start] + p.route.entered] for p in scouts])))
    def shown(unit):   # --focus: a tribe (with its elements) or one element; the rest are faded
        return not focus or (unit[:4] == focus if len(focus) == 4 else unit == focus)

    for i, p in enumerate(plan):
        r = p.route
        if not r:
            continue
        faded = not shown(p.row["unit"])
        if p.row["kind"] == "move":
            col = "#111111"
            pts = [coords.centre(h) for h in [r.start] + r.entered]
            if len(pts) > 1:   # one line, with a single arrowhead on the last step
                alpha = 0.3 if faded else 1
                xs, ys = zip(*pts)
                ax.plot(xs[:-1], ys[:-1], color=col, lw=3.2, zorder=5, alpha=alpha, solid_capstyle="round")
                ax.add_patch(FancyArrowPatch(pts[-2], pts[-1], arrowstyle="-|>", mutation_scale=18,
                                             color=col, lw=3.2, zorder=5, alpha=alpha))
            continue
        col = colours[p.name]
        px, py = zip(*lane_of[id(p)])
        if faded:
            ax.plot(px, py, color=col, lw=1.2, alpha=0.3, zorder=3)
            continue
        ax.plot(px, py, color=col, lw=1.6, alpha=0.9, zorder=4)
        ax.plot(px[-1], py[-1], "o", color=col, ms=3.5, zorder=4)
        last = r.steps[-1] if r.steps else None
        if last and last.result != "ok":
            bx, by = coords.centre(last.hex)
            if last.result == "river_block":
                bx, by = (px[-1] + bx) / 2, (py[-1] + by) / 2
                ax.plot(bx, by, "x", color=col, ms=8, mew=2.2, zorder=6)
            ax.plot([px[-1], bx], [py[-1], by], color=col, lw=1.2, ls=":", zorder=4)
        for d in r.follow_dirs:   # which way the follow order should start
            tx, ty = coords.centre(coords.step(r.end, d))
            ax.add_patch(FancyArrowPatch((px[-1], py[-1]), (px[-1] + (tx - px[-1]) * 0.75, py[-1] + (ty - py[-1]) * 0.75),
                                         arrowstyle="-|>", mutation_scale=12, color=col, lw=1.4, ls="--", zorder=6))
        tag = f"{p.row['unit']}/{p.row['party']}" + (f" {r.follow}" if r.follow else "")
        ax.text(px[-1], py[-1] + 0.22, tag, fontsize=6, color=col, weight="bold", ha="center", zorder=7,
                bbox=dict(fc="white", ec=col, lw=0.5, pad=0.8, alpha=0.85))
