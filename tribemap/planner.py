"""Movement and scouting simulator: where a route will get to with the map as we know it.

Rules used (rules.md; costs from terrain.csv, editable):
- Movement points: mounted scouts 15, mounted tribes 27, tribes on foot 18 (config.toml [movement] can override).
- Each hex entered costs its terrain's MP. Unknown terrain, or terrain with no cost in terrain.csv,
  uses the assumed cost and the step is flagged as assumed.
- A party that can't afford the next hex stops, but sees that hex's terrain (the "one extra hex" glimpse).
- Rivers block movement except where a ford is known. A hexside reported as "no ford" always blocks.
- Water and high mountains (IMPASSABLE) can't be entered on land.
- "To limit" orders (NL, NEL, ...) repeat the direction until the party stops.
- Follow orders (FRR, FCL, ...) aren't simulated, but follow_hint() says which way they should start, from the
  river hexsides / water / mountain hexes known next to the hex where the order begins.
"""
import math
from dataclasses import dataclass, field

from . import coords

MOVER_MP = {"scout": 15, "mounted": 27, "foot": 18}
ASSUMED_COST = 3                      # unknown terrain: assume prairie, as plan_map.py did
IMPASSABLE = {"O", "UW", "L", "HSM", "ALPS"}
FOLLOW = {"FCL", "FCR", "FLL", "FLR", "FML", "FMR", "FOL", "FOR", "FRL", "FRR"}
LIMIT = {"NL": "N", "NEL": "NE", "SEL": "SE", "SL": "S", "SWL": "SW", "NWL": "NW"}
SCOUT_ORDERS = set(coords.DIRS) | FOLLOW | set(LIMIT)
MAX_ORDERS = {"scout": 9, "move": 40}  # columns available on the orders sheet


MOUNTAINS = {"LCM", "LJM", "LSM", "LAM", "LVM", "UM", "HSM", "ALPS"}
WATER = {"O", "UW", "L"}
FOLLOW_FEATURE = {"FR": ("river", None), "FC": ("coast", WATER), "FL": ("lake", {"L"}),
                  "FO": ("ocean", {"O", "UW"}), "FM": ("mountains", MOUNTAINS)}


def follow_hint(h, order, hexes, edges):
    """Which way a follow order (FRR, FCL, ...) will start from hex h, judging by the known map.

    Keeping a feature on your right means stepping to the neighbour just counter-clockwise of the side the
    feature is on (river to the N -> head NW); on your left, just clockwise. Where the feature covers several
    sides in a row, the step is taken from the end of that run. Returns (feature name, sides, directions, note).
    """
    feature, codes = FOLLOW_FEATURE[order[:2]]
    if feature == "river":
        sides = [d for d in coords.DIRS if {"river", "ford", "no_ford"} & set(edges.get(coords.edge(h, d), {}))]
    else:
        sides = [d for d in coords.DIRS
                 if (hexes.get(coords.step(h, d)) and hexes[coords.step(h, d)].terrain in codes)]
    if not sides:
        return feature, [], [], f"no {feature} known next to {coords.label(h)}: direction unknown"
    i = {d: n for n, d in enumerate(coords.DIRS)}
    turn = -1 if order.endswith("R") else 1   # right: counter-clockwise; left: clockwise
    heads = []
    for d in sides:
        nxt = coords.DIRS[(i[d] + turn) % 6]
        if nxt not in sides and nxt not in heads:
            heads.append(nxt)
    if not heads:   # surrounded by the feature on every side
        return feature, sides, [], f"{feature} on every side of {coords.label(h)}: direction unclear"
    side_text = "/".join(sides)
    note = (f"heads {' or '.join(heads)} along the {feature} ({feature} on the {side_text} side"
            f"{'s' if len(sides) > 1 else ''}){'; more than one way possible' if len(heads) > 1 else ''}")
    return feature, sides, heads, note


def river_in_the_way(h, order, hexes, edges):
    """True for a mountain / coast / lake follow from h that a river probably blocks: the feature is only
    across a river from h, or the feature hex has a river along any of its sides (rivers run along mountain
    ranges and shores, so it likely wraps round it). Following the river itself is the better order there."""
    feature, sides, _, _ = follow_hint(h, order, hexes, edges)
    if feature == "river" or not sides:
        return False
    river = lambda e: bool({"river", "ford", "no_ford"} & set(edges.get(e, {})))
    if all(river(coords.edge(h, d)) for d in sides):
        return True
    return any(river(coords.edge(coords.step(h, d), e)) for d in sides for e in coords.DIRS)


def parse_orders(text):
    """'N, ne nw' or ['N', 'NE'] -> ['N', 'NE', 'NW'], validated."""
    if isinstance(text, str):
        text = text.replace(",", " ").split()
    orders = [o.strip().upper() for o in text if o.strip()]
    bad = [o for o in orders if o not in SCOUT_ORDERS]
    if bad:
        raise ValueError(f"unknown order(s) {', '.join(bad)}; use N NE SE S SW NW, "
                         f"to-limit ({' '.join(LIMIT)}) or follow ({' '.join(sorted(FOLLOW))})")
    return orders


@dataclass
class SimStep:
    n: int
    order: str
    dir: str
    hex: tuple
    terrain: str | None
    cost: int | None
    assumed: bool
    mp_left: int
    result: str        # ok, mp_block, river_block, impassable


@dataclass
class Route:
    start: tuple
    orders: list
    mover: str
    mp: int
    steps: list = field(default_factory=list)
    stop: str = ""          # why it stopped early ('' = ran all orders)
    follow: str = ""        # the follow order it ended on, if any
    follow_dirs: list = field(default_factory=list)   # which way that follow order is expected to start
    unused: list = field(default_factory=list)  # orders never reached

    @property
    def entered(self):
        return [s.hex for s in self.steps if s.result == "ok"]

    @property
    def end(self):
        e = self.entered
        return e[-1] if e else self.start

    @property
    def glimpse(self):
        """The hex seen but not entered when the party ran out of MP or hit impassable terrain."""
        last = self.steps[-1] if self.steps else None
        return last.hex if last and last.result in ("mp_block", "impassable") else None

    @property
    def mp_left(self):
        ok = [s for s in self.steps if s.result == "ok"]
        return ok[-1].mp_left if ok else self.mp

    @property
    def assumed(self):
        return any(s.assumed for s in self.steps if s.result == "ok")

    @property
    def mp_used(self):
        return self.mp - self.mp_left

    @property
    def known_cost(self):
        """MP the route is sure to cost: the steps through terrain whose cost we know (a minimum when some
        hexes' costs are only assumed)."""
        return sum(s.cost for s in self.steps if s.result == "ok" and not s.assumed)

    @property
    def notable_stop(self):
        """Why the route stops, when that's worth reading: running out of MP is the normal end of a scout
        route, so only rivers, impassable terrain and follow orders are reported."""
        return "" if not self.stop or self.stop.endswith("out of MP") else self.stop

    def as_dict(self):
        return {"start": coords.label(self.start), "orders": self.orders, "mover": self.mover, "mp": self.mp,
                "end": coords.label(self.end), "mp_left": self.mp_left, "stop": self.stop,
                "follow": self.follow, "follow_dirs": self.follow_dirs,
                "follow_to": [coords.label(coords.step(self.end, d)) for d in self.follow_dirs],
                "unused_orders": self.unused,
                "glimpse": coords.label(self.glimpse) if self.glimpse else None,
                "costs_assumed": self.assumed, "mp_used": self.mp_used, "known_cost": self.known_cost,
                "steps": [{"n": s.n, "order": s.order, "dir": s.dir, "hex": coords.label(s.hex),
                           "terrain": s.terrain, "cost": s.cost, "assumed": s.assumed,
                           "mp_left": s.mp_left, "result": s.result} for s in self.steps]}


def river_blocks(edges, h, d):
    kinds = edges.get(coords.edge(h, d), {})
    return "no_ford" in kinds or ("river" in kinds and "ford" not in kinds)


def simulate(start, orders, hexes, edges, terrain, mover="scout", mp=None, mover_mp=None,
             assumed_cost=ASSUMED_COST):
    """Walk a route over the known map. hexes/edges come from queries.knowledge()."""
    mp = mp if mp is not None else (mover_mp or MOVER_MP)[mover]
    route = Route(start, list(orders), mover, mp)
    cur, left = start, mp

    def attempt(order, d):
        nonlocal cur, left
        nxt = coords.step(cur, d)
        info = hexes.get(nxt)
        code = info.terrain if info else None
        n = len(route.steps) + 1
        if river_blocks(edges, cur, d):
            route.steps.append(SimStep(n, order, d, nxt, code, None, False, left, "river_block"))
            return "river"
        if code in IMPASSABLE:
            route.steps.append(SimStep(n, order, d, nxt, code, None, False, left, "impassable"))
            return "impassable"
        cost = terrain.mp(code)
        assumed = cost is None
        cost = assumed_cost if assumed else cost
        if cost > left:
            route.steps.append(SimStep(n, order, d, nxt, code, cost, assumed, left, "mp_block"))
            return "out of MP"
        left -= cost
        cur = nxt
        route.steps.append(SimStep(n, order, d, nxt, code, cost, assumed, left, "ok"))
        return ""

    for i, order in enumerate(orders):
        if order in FOLLOW:
            _, _, heads, note = follow_hint(cur, order, hexes, edges)
            route.follow, route.follow_dirs = order, heads
            route.stop = f"{order} from {coords.label(cur)}: {note}"
            route.unused = list(orders[i + 1:])
            break
        if order in LIMIT:
            for _ in range(60):
                why = attempt(order, LIMIT[order])
                if why:
                    break
            # to-limit ends the move either way; any later orders are never reached
            route.stop = f"{order}: {why}" if why else ""
            route.unused = list(orders[i + 1:])
            break
        why = attempt(order, order)
        if why:
            route.stop = why
            route.unused = list(orders[i + 1:])
            break
    return route


def follow_path(h, order, left, hexes, edges, terrain, assumed_cost=ASSUMED_COST, max_steps=10, sure=False):
    """A guess at where a follow order (FRR, FCL, ...) starting at h will take a party with `left` MP.

    Starts the way follow_hint() says; keeps the feature on its side wherever we know the feature, and
    carries straight on where we don't. Spends MP like simulate() and stops at impassable terrain.
    sure stops at the first hex where the feature isn't known any more (that hex is still included): past
    there the straight-on guess is often wrong (rivers bend round hills and mountains).
    None when the starting direction isn't clear (no known feature, or more than one way).
    """
    _, _, heads, _ = follow_hint(h, order, hexes, edges)
    if len(heads) != 1:
        return None
    d, cur, path = heads[0], h, []
    while len(path) < max_steps:
        nxt = coords.step(cur, d)
        if nxt == h or nxt in path:
            break   # the guess would double back: a follow order never retraces its steps
        info = hexes.get(nxt)
        code = info.terrain if info else None
        cost = terrain.mp(code)
        cost = assumed_cost if cost is None else cost
        if code in IMPASSABLE or cost > left:
            break
        left -= cost
        path.append(nxt)
        cur = nxt
        _, _, heads, _ = follow_hint(cur, order, hexes, edges)
        heads = [x for x in heads if coords.step(cur, x) != h and coords.step(cur, x) not in path]
        if len(heads) == 1:
            d = heads[0]
        elif sure:
            break
    return path


FOLLOW_BONUS = 1.0   # priority for routes that end with a follow order, scaled by the MP left (see plans.auto_scout)


def best_route(start, hexes, edges, terrain, value, mover="scout", mp=None, mover_mp=None,
               assumed_cost=ASSUMED_COST, max_orders=MAX_ORDERS["scout"], follow_value=None,
               require_follow=False, follow_used=(), avoid=frozenset(), shared=None, outward=False, allowed=None,
               strict=False):
    """The order list that gains the most, walking from start under the same rules as simulate().

    value(hex, how) -> score for entering a hex (how='entered') or glimpsing it at the end (how='glimpsed').
    Every route of up to max_orders steps is considered (depth-first, pruned by an upper bound), including
    one extra step that the party can't afford, since that still glimpses the next hex. A route never
    re-enters a hex it has already been through. Ties go to the route ending farthest from the start.

    follow_value(hex, order, mp_left, walked) -> score or None: when given, a route may also end with a follow
    order (FRR, FCL, ...) at any hex it reaches, scored by follow_value (walked: the hexes the route has
    already been through, start included). require_follow keeps only such
    routes; follow_used holds (hex, order) pairs already taken by other parties.
    avoid: hexes other parties already go through. A route may trail out from the start through them (several
    parties must leave through the start's six neighbours, and where only a narrow corridor is open they
    share its first hexes before fanning out), but once it has stepped off them it never enters one again,
    so routes don't cross each other. shared: the avoided hexes a route may trail through (default: all of
    them), e.g. where other parties walk but not where a follow order is only expected to go.
    strict applies avoid and allowed from the very first step too (e.g. extending a route from its end).
    allowed(hex) -> bool: after the first step, the route may only enter hexes it allows (e.g. a wedge).
    outward: a route heads away from the start: its first two steps each move a ring further out and no step
    moves back inward (sideways along a ring is fine from ring 2 on), so it can't circle the start hex.
    Returns (score, orders).
    """
    mp = mp if mp is not None else (mover_mp or MOVER_MP)[mover]
    shared = avoid if shared is None else shared
    cheapest = min([t.mp for t in terrain.by_code.values() if t.mp] + [assumed_cost])
    best_gain = 1.0   # the most one entered hex can be worth (see plans.SCOUT_VALUE); used for pruning
    best = [(-1.0, 0, 0), []]   # (score, distance, -len), orders

    def consider(score, end, orders, follow=False):
        if require_follow and not follow:
            return
        key = (round(score, 6), coords.distance(start, end), -len(orders))
        if key > best[0]:
            best[0], best[1] = key, list(orders)

    def dfs(cur, left, orders, seen, score, trail=True):
        consider(score, cur, orders)
        if len(orders) >= max_orders:
            return
        if follow_value:
            for order in sorted(FOLLOW):
                if (cur, order) in follow_used:
                    continue
                gain = follow_value(cur, order, left, seen)
                if gain is not None:
                    consider(score + gain, cur, orders + [order], follow=True)
        # bound: every remaining affordable step entering a best-value hex, plus a final glimpse
        # (a follow order can score up to the same per hex, plus its bonus)
        extra = 0.5 + (FOLLOW_BONUS + 1 if follow_value else 0)
        if score + (left // cheapest) * best_gain + extra <= best[0][0]:
            return
        for d in coords.DIRS:
            nxt = coords.step(cur, d)
            if nxt in seen or ((orders or strict) and ((nxt in avoid and not (trail and nxt in shared))
                                                      or (allowed and not allowed(nxt)))):
                continue
            if outward:
                here, there = coords.distance(start, cur), coords.distance(start, nxt)
                if there < here or (len(orders) < 2 and there == here):
                    continue
            orders.append(d)
            if river_blocks(edges, cur, d):
                consider(score, cur, orders)                          # stopped by the river, nothing seen
            else:
                info = hexes.get(nxt)
                code = info.terrain if info else None
                if code in IMPASSABLE:
                    consider(score + value(nxt, "glimpsed"), cur, orders)
                else:
                    cost = terrain.mp(code)
                    cost = assumed_cost if cost is None else cost
                    if cost > left:
                        consider(score + value(nxt, "glimpsed"), cur, orders)   # the one-extra-hex glimpse
                    else:
                        seen.add(nxt)
                        dfs(nxt, left - cost, orders, seen, score + value(nxt, "entered"), trail and nxt in shared)
                        seen.discard(nxt)
            orders.pop()

    dfs(start, mp, [], {start}, 0.0)
    return best[0][0], best[1]


def bearing(start, h):
    """Compass bearing (degrees clockwise from north) from start to h, on the drawn map."""
    (x0, y0), (x1, y1) = coords.centre(start), coords.centre(h)
    return math.degrees(math.atan2(x1 - x0, y1 - y0)) % 360


def wedge(start, i, n, offset=0.0):
    """allowed() for the i-th of n equal wedges round start (wedge 0 starts at north + offset)."""
    width = 360.0 / n
    lo = (offset + i * width) % 360

    def allowed(h):
        return h == start or (bearing(start, h) - lo) % 360 < width
    return allowed


def value_wedges(start, n, value, radius=5, min_value=0.0):
    """n wedges round start that split the ground worth scouting (value(h, 'entered') > min_value within radius)
    into equal shares, so every party gets an equally useful sector and none is pointed at known ground.
    The wedges begin in the biggest empty gap between valuable bearings. Returns n allowed() functions
    (all None when nothing nearby has value)."""
    pts = sorted((bearing(start, h), v) for h in coords.within(start, radius) if h != start
                 for v in [value(h, "entered")] if v > min_value)
    if not pts:
        return [None] * n
    # rotate the circle so it starts in the middle of the widest gap between valuable bearings
    gaps = [((pts[(i + 1) % len(pts)][0] - pts[i][0]) % 360 or 360, i) for i in range(len(pts))]
    width, i = max(gaps)
    first = (pts[i][0] + width / 2) % 360
    pts = sorted(((a - first) % 360, v) for a, v in pts)
    total, run, cuts = sum(v for _, v in pts), 0.0, [0.0]
    for k, (a, v) in enumerate(pts[:-1]):
        run += v
        while len(cuts) < n and run >= total * len(cuts) / n:
            cuts.append((a + pts[k + 1][0]) / 2)   # boundary halfway to the next valuable bearing
    while len(cuts) < n:
        cuts.append(cuts[-1])
    cuts.append(360.0)

    def make(lo, hi):
        def allowed(h):
            return h == start or lo <= (bearing(start, h) - first) % 360 < hi
        return allowed
    return [make(cuts[k], cuts[k + 1]) for k in range(n)]
