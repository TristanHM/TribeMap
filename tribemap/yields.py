"""Production observations from report lines with explicit worker and output counts."""
import json
import re

from .queries import turn_key

N = r"([\d,]+)"
SIMPLE = (
    (rf"{N} people hunted {N} provs", "HUNTING", "PROVS", "Hunt", "PROVS"),
    (rf"{N} people fished (?:for )?{N} provs", "FISHING", "PROVS", "Fish", "PROVS"),
    (rf"{N} people cut {N} logs", "FORESTRY", "LOG", "For", "LOG"),
    (rf"{N} people mined {N} salt\b", "MINING", "SALT", "Min", "SALT"),
    (rf"{N} people made {N} sling\b \(using [\d,]+ leather\)", "LEATHERWORK", "SLING", "Ltr", "SLING"),
    (rf"{N} workers cured {N} leather \(using [\d,]+ gut, [\d,]+ skin\)", "CURING", "SKIN", "Cur", "LEATHER"),
    (rf"{N} tanners tanned {N} leather \(using [\d,]+ bark, [\d,]+ skin\)", "TANNING", "SKIN", "Tan", "LEATHER"),
    (rf"{N} woodworkers crafted {N} wagon\b", "WOODWORK", "WAGON", "Wd", "WAGON"),
)
FURRIER = re.compile(rf"{N} (?:people furried \(\s*|performed furrier for )"
                     rf"{N} provs,\s*{N} furs,\s*{N} skins", re.I)
FOREST_CLUB = re.compile(rf"{N} woodworkers crafted {N} club\b \(using\s*\)", re.I)
PATTERNS = [(re.compile(p, re.I), a, i, s, g) for p, a, i, s, g in SIMPLE]


def _count(raw):
    return int(raw.replace(",", ""))


def reported_yields(activities):
    for pattern, activity, item, skill, good in PATTERNS:
        for m in pattern.finditer(activities):
            workers, output = map(_count, m.groups())
            if workers:
                yield activity, item, "NONE", skill, workers, {good: output}
    for m in FURRIER.finditer(activities):
        workers, provs, furs, skins = map(_count, m.groups())
        if workers:
            yield "FURRIER", "FURRIER", "NONE", "Fur", workers, {"PROVS": provs, "FUR": furs, "SKIN": skins}
    for m in FOREST_CLUB.finditer(activities):
        workers, clubs = map(_count, m.groups())
        if workers:
            yield "WOODWORK", "CLUB", "FOREST", "Wd", workers, {"CLUB": clubs}


def _previous(turn):
    year, month = turn_key(turn)
    return f"{year}-{month - 1:02d}" if month > 1 else f"{year - 1}-12"


def recent_observations(conn, turns, hexes):
    """Use the latest three results before the plan and preceding reports for starting skills."""
    wanted = set(turns[-3:])
    if not wanted:
        return []
    context = wanted | {_previous(turn) for turn in wanted}
    marks = ",".join("?" for _ in context)
    rows = conn.execute(
        f"""SELECT u.turn, u.unit, u.gx, u.gy, u.skills_json, u.activities, s.season
            FROM unit_turn u JOIN unit t ON t.id = u.unit JOIN source s ON s.id = u.source_id
            WHERE u.turn IN ({marks}) AND t.kind = 'Tribe' ORDER BY u.turn, u.unit""",
        sorted(context),
    ).fetchall()
    previous = {(row["turn"], row["unit"]): row for row in rows}
    observations = []
    for row in rows:
        turn, unit = row["turn"], row["unit"]
        prior = previous.get((_previous(turn), unit))
        if turn not in wanted or prior is None or not row["activities"]:
            continue
        skills = json.loads(prior["skills_json"] or "{}")
        where = hexes.get((prior["gx"], prior["gy"]))
        for activity, item, distinction, skill, workers, output in reported_yields(row["activities"]):
            observations.append({
                "activity": activity, "item": item, "distinction": distinction, "skill": skill,
                "level": skills.get(skill, 0), "workers": workers, "goods": output,
                "turn": turn, "unit": unit, "terrain": where.terrain if where else None, "season": row["season"],
            })
    return observations
