#!/usr/bin/env python3
"""Export the Agricola card registry to a JSON catalog for the client.

Usage: python tools/export_agricola_cards.py
Writes client/games/agricola_cards.json. Run after changing cards.py
(tests/test_agricola_catalog.py fails if the file is stale).
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from server.agricola.cards import CARDS, compendium, load_decks  # noqa: E402

CLIENT_KEYS = (
    "name", "type", "text", "deck", "min_players", "cost", "points",
    "traveling", "pasture_capacity_bonus", "house_capacity", "raw_values",
    "bake", "bake_on_spaces", "bake_bonus_per_grain", "bake_bonus_flat",
    "cook", "lasso", "field", "extra_rooms", "occ_cost_delta", "conversions",
)


def build_catalog():
    unimplemented = load_decks()
    catalog = {}
    for cid, spec in sorted(CARDS.items()):
        entry = {}
        for key in CLIENT_KEYS:
            if key in spec and spec[key] not in (None, {}, (), [], 0, False):
                value = spec[key]
                if callable(value):
                    # A computed extra_rooms/house_capacity (fn(state,
                    # player, inst) -> int) has no static value to show
                    # the client; skip it (the card's own text still
                    # describes the effect).
                    continue
                if isinstance(value, tuple):
                    value = list(value)
                if key == "cost" and isinstance(value, list):
                    # Alternative printed cost ("3 wood or 3 clay"): the
                    # client renders `cost` as one dict, so emit the
                    # first alternative there plus the full list for a
                    # future cost-picker UI.
                    entry["cost_alternatives"] = value
                    entry["cost_text"] = " or ".join(
                        " + ".join(f"{n} {g}" for g, n in alt.items())
                        for alt in value)
                    value = value[0]
                if key == "field":
                    field_value = {"crops": list(value["crops"])}
                    if value.get("stacks", 1) != 1:
                        field_value["stacks"] = value["stacks"]
                    value = field_value
                if key == "conversions":
                    value = [
                        {k: v for k, v in conv.items()
                         if k in ("give", "get", "per_harvest")}
                        for conv in value]
                entry[key] = value
        if spec.get("prereq"):
            entry["prereq_text"] = spec["prereq"][1]
        if spec.get("card_action"):
            entry["has_card_action"] = True
        catalog[cid] = entry
    # Compendium cards not implemented: minimal browsable entries.
    for code, db in compendium().items():
        if code in catalog:
            continue
        reason = unimplemented.get(code)
        if reason is None and db["type"] == "major":
            # Deck A majors are the 10 built-in major improvements,
            # listed under both original and revised numbering.
            reason = "available as the built-in major improvement"
        entry = {
            "name": db["name"], "type": db["type"], "deck": db["deck"],
            "text": db["text"], "implemented": False,
            "reason": reason or "not yet implemented",
        }
        if db.get("vp"):
            entry["points"] = db["vp"]
        if db.get("prereq"):
            entry["prereq_text"] = db["prereq"]
        players = re.match(r"(\d)", db.get("players") or "")
        if players and int(players.group(1)) > 1:
            entry["min_players"] = int(players.group(1))
        cost = parse_cost(db.get("cost") or "")
        if cost:
            entry["cost"] = cost
        elif db.get("cost"):
            entry["cost_text"] = db["cost"]
        catalog[code] = entry
    return catalog


# Compendium costs are strings like "2W 1C". Parse the regular ones
# into {resource: count}; anything else (e.g. "1W or 1C", "1 Grain")
# stays a cost_text string for the client to show verbatim.
COST_LETTERS = {"W": "wood", "C": "clay", "S": "stone", "R": "reed", "F": "food"}


def parse_cost(text):
    parts = text.split()
    if not parts:
        return None
    cost = {}
    for part in parts:
        m = re.fullmatch(r"(\d+)([WCSRF])", part)
        if not m:
            return None
        good = COST_LETTERS[m.group(2)]
        cost[good] = cost.get(good, 0) + int(m.group(1))
    return cost


def main():
    out = os.path.join(os.path.dirname(__file__), "..",
                       "client", "games", "agricola_cards.json")
    catalog = build_catalog()
    with open(out, "w") as f:
        json.dump(catalog, f, indent=1, sort_keys=True)
        f.write("\n")
    print(f"Wrote {len(catalog)} cards to {os.path.normpath(out)}")


if __name__ == "__main__":
    main()
