"""Editable unit orders and their paste-in rows."""
import hashlib
import json
import math
import re
from datetime import datetime, timezone


def drafts(conn, turn, variant):
    return {row["unit"]: {"revision": row["revision"], "orders": json.loads(row["data"])}
            for row in conn.execute("SELECT unit, revision, data FROM plan_worksheet WHERE turn=? AND variant=?",
                                    (turn, variant))}


def unit_id(value):
    return isinstance(value, str) and re.fullmatch(r"\d{4}(?:[a-z]\d+)?", value)


def quantity(n):
    # Excel preserves up to 15 significant digits in numeric cells.
    return type(n) is int and 0 <= n <= 999_999_999_999_999


def valid_estimate(value):
    return type(value) in (int, float) and 0 <= value <= 999_999_999_999_999 and math.isfinite(value)


def validate(orders):
    if not isinstance(orders, dict):
        raise ValueError("Orders must be an object")
    creation = orders.get("creation")
    if creation is not None and (not isinstance(creation, dict) or not unit_id(creation.get("from")) or
            not isinstance(creation.get("name", ""), str)):
        raise ValueError("Invalid planned unit")
    for field in ("rows", "transfers", "skills", "gm", "comments", "research"):
        if not isinstance(orders.get(field, []), list):
            raise ValueError(f"{field} must be a list")
    for row in orders.get("rows", []):
        if (not isinstance(row, list) or len(row) not in (3, 4) or not isinstance(row[0], str)
                or not quantity(row[1]) or (len(row) == 4 and not isinstance(row[3], dict))):
            raise ValueError("Invalid activity row or quantity (maximum 15 digits)")
        rate = row[2]
        if rate is not None and not valid_estimate(rate):
            raise ValueError("Invalid provision estimate")
        if len(row) == 4:
            fields = row[3]
            if any(not isinstance(fields.get(key, ""), str) for key in ("activity", "item", "distinction")):
                raise ValueError("Activity, item and distinction must be text")
            for field in ("implements", "materials"):
                pairs = fields.get(field, [])
                if not isinstance(pairs, list) or len(pairs) > 20 or any(
                    not isinstance(pair, list) or len(pair) != 2 or not isinstance(pair[0], str)
                    or not quantity(pair[1]) for pair in pairs
                ):
                    raise ValueError(f"Invalid {field} (up to 20 per activity)")
            outputs = fields.get("outputs", [])
            if not isinstance(outputs, list) or len(outputs) > 20 or any(
                not isinstance(out, list) or len(out) != 3 or not isinstance(out[0], str)
                or not valid_estimate(out[1]) or out[2] not in ("total", "per worker") for out in outputs
            ):
                raise ValueError("Invalid output estimate")
    if orders.get("cap") is not None and not quantity(orders["cap"]):
        raise ValueError("Invalid provision cap (maximum 15 digits)")
    for row in orders.get("transfers", []):
        if (not isinstance(row, dict) or not quantity(row.get("qty")) or row.get("phase") not in ("BM", "AM")
                or row.get("external") not in (None, "send", "receive")
                or any(not isinstance(row.get(key, ""), str) for key in ("to", "good", "notes"))):
            raise ValueError("Invalid transfer row or quantity (maximum 15 digits)")
    if len(orders.get("skills", [])) > 3:
        raise ValueError("At most three skill attempts per unit")
    for field in ("skills", "gm", "comments", "research"):
        if any(not isinstance(value, str) or len(value) > 32767 for value in orders.get(field, [])):
            raise ValueError(f"Invalid text in {field}")


class ChangedDraft(ValueError):
    pass


def save(conn, turn, variant, unit, orders, expected):
    if not unit_id(unit) or type(expected) is not int or expected < 0:
        raise ValueError("Invalid unit or draft version")
    validate(orders)
    data = json.dumps(orders, allow_nan=False)
    if len(data) > 250_000:
        raise ValueError("Unit orders are too large")
    with conn:
        conn.execute("INSERT OR IGNORE INTO plan (turn, variant) VALUES (?, ?)", (turn, variant))
        current = conn.execute("SELECT revision, data FROM plan_worksheet WHERE turn=? AND variant=? AND unit=?",
                               (turn, variant, unit)).fetchone()
        revision = current["revision"] if current else 0
        if current and json.loads(current["data"]) == orders:
            return revision
        if revision != expected:
            raise ChangedDraft("This draft changed in another tab. Reload it before saving again.")
        conn.execute(
            """INSERT INTO plan_worksheet (turn, variant, unit, data, revision) VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(turn, variant, unit) DO UPDATE SET data=excluded.data, revision=excluded.revision""",
            (turn, variant, unit, data, revision + 1),
        )
    return revision + 1


def _activity_details(fields, catalogue):
    by_key = {(r["activity"], r["item"], r["distinction"]): r for r in catalogue}
    aliases = {
        "HUNTING": ("HUNTING", "PROVS", "NONE"), "FISHING": ("FISHING", "PROVS", "NONE"),
        "FURRIER": ("FURRIER", "FURRIER", "NONE"), "HERDING": ("HERDING", "ANIMALS", "NONE"),
        "WOODWORK · CLUBS": ("WOODWORK", "CLUB", "FOREST"), "WOODWORK · WAGONS": ("WOODWORK", "WAGON", "NONE"),
        "FORESTRY": ("FORESTRY", "LOG", "NONE"), "CURING": ("CURING", "SKIN", "NONE"),
        "TANNING": ("TANNING", "SKIN", "NONE"),
    }
    for activity, item, distinction in by_key:
        label = " · ".join(v for i, v in enumerate((activity, item, distinction))
                           if v and v != "NONE" and (i == 0 or v != activity))
        aliases.setdefault(label, (activity, item, distinction))
    for row in fields:
        label, workers, _ = row[:3]
        extra = row[3] if len(row) > 3 else {}
        codes = tuple(extra.get(key, "").strip().upper() for key in ("activity", "item", "distinction"))
        codes = (codes[0], codes[1], codes[2] or "NONE") if any(codes) else aliases.get(label.strip().upper())
        yield label, workers, codes, extra.get("implements", [])


def blocks(saved, routes, catalogue, goods_codes, skills):
    """The editable worksheet ranges and any incomplete orders."""
    activities, transfers, attempts, gm, comments, research, creations, issues = [], [], [], [], [], [], [], []
    scout_counts = {}
    for r in routes:
        if r["kind"] == "scout":
            scout_counts[r["unit"]] = scout_counts.get(r["unit"], 0) + r["scouts"]
    for unit in sorted(set(saved) | (set(scout_counts) if saved else set())):
        orders = saved.get(unit, {}).get("orders", {})
        if creation := orders.get("creation"):
            creations.append([None, creation["from"], "Create Unit", "BM", unit, "Current"])
        for i, (label, workers, codes, implements) in enumerate(_activity_details(orders.get("rows", []), catalogue), 1):
            if not codes or not all(codes) or codes[0] == "SCOUTING" or not workers:
                issues.append({"unit": unit, "section": "Activities", "row": i,
                               "message": f"{label}: enter activity and item codes and a positive quantity."})
                continue
            row = [None, unit, *codes, workers, *([None] * 5)]
            if any(not code.strip() or not qty for code, qty in implements):
                issues.append({"unit": unit, "section": "Activities", "row": i,
                               "message": f"{label}: give each implement a code and a positive quantity."})
            for code, qty in implements:
                row.extend((goods_codes.get(code.strip().upper(), code.strip().upper()), qty))
            activities.append(row)
        if scout_counts.get(unit):
            activities.append([None, unit, "SCOUTING", "SCOUTING", "NONE", scout_counts[unit]])
        for i, r in enumerate(orders.get("transfers", []), 1):
            if not unit_id(r.get("to")) or not r.get("good", "").strip() or not r["qty"]:
                issues.append({"unit": unit, "section": "Transfers", "row": i,
                               "message": "Choose a recipient, good and positive quantity."})
                continue
            good = r["good"].strip().upper()
            sender, recipient = (r["to"], unit) if r.get("external") == "receive" else (unit, r["to"])
            transfers.append([sender, recipient, goods_codes.get(good, good), r["qty"], r["phase"], r.get("notes", "") or None])
        if orders.get("cap") is not None:
            transfers.append([unit, unit, "PROVS", orders["cap"], "BM", None])
        for i, topic in enumerate(orders.get("skills", []), 1):
            if topic.strip():
                name = skills.get(topic, {}).get("skill", topic)
                attempts.append([unit, i, name.strip().upper()])
        for field, output in (("gm", gm), ("comments", comments), ("research", research)):
            output.extend([unit, value.strip()] for value in orders.get(field, []) if value.strip())
    result = [
        ("Tribes_Activities", ["UnitName", "TRIBE", "ACTIVITY", "ITEM", "DISTINCTION", "PEOPLE", "SLAVES",
                               "SPECIALISTS", "JOINT", "OWNING_TRIBE", "Building"] +
         [label for i in range(1, 21) for label in (f"Implement_{i}", f"Imp_{i}_Number")], activities, 2),
        ("Transfers", ["From", "To", "Item", "Quantity", "Transfer_Timing", "Notes"], transfers, 1),
        ("Skill_Attempts", ["TRIBE", "ORDER", "TOPIC"], attempts, 1),
        ("GM Actions", ["Unit", "What does the GM need to do?"], gm, 1),
        ("Comments", ["Unit", "Message/Notes"], comments, 1),
        ("Research_Attempts", ["TRIBE", "TOPIC"], research, 1),
        ("Auto-GM", ["", "From_Unit", "Action", "Timing", "To_Unit", "NewUnitDirection"], creations, 2),
    ]
    # Paste only through the last populated column; formula columns are excluded.
    return [(name, headers[:max(len(r) for r in data)], data, col)
            for name, headers, data, col in result if data], issues


def record_export(conn, turn, variant, snapshot):
    encoded = json.dumps(snapshot, sort_keys=True, ensure_ascii=False)
    digest = fingerprint(snapshot["plan"])
    conn.execute("INSERT INTO plan_export VALUES (?, ?, ?, ?, ?) ON CONFLICT(turn, variant, digest)"
                 " DO UPDATE SET exported_at=excluded.exported_at",
                 (turn, variant, digest, datetime.now(timezone.utc).isoformat(), encoded))
    conn.commit()
    return digest


def fingerprint(snapshot):
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def last_export(conn, turn, variant):
    row = conn.execute("SELECT digest, exported_at, snapshot FROM plan_export WHERE turn=? AND variant=?"
                       " ORDER BY exported_at DESC, rowid DESC LIMIT 1", (turn, variant)).fetchone()
    return dict(row) if row else None


def previous_export(conn, turn, variant):
    row = conn.execute("SELECT turn, variant, snapshot FROM plan_export WHERE turn < ? "
                       "ORDER BY turn DESC, variant = ? DESC, exported_at DESC LIMIT 1", (turn, variant)).fetchone()
    if not row:
        return None
    snapshot = json.loads(row["snapshot"])
    plan = snapshot["plan"]
    return {"turn": row["turn"], "variant": row["variant"], "worksheet": plan.get("worksheet", {}) if isinstance(plan, dict) else {},
            "estimates": snapshot.get("estimates", {})}
