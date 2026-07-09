#!/usr/bin/env python3
"""Export the Agricola card registry to a JSON catalog for the client.

Usage: python tools/export_agricola_cards.py
Writes client/games/agricola_cards.json. Run after changing cards.py
(tests/test_agricola_catalog.py fails if the file is stale).
"""

import json
import os
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
                if isinstance(value, tuple):
                    value = list(value)
                if key == "field":
                    value = {"crops": list(value["crops"])}
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
        catalog[code] = {
            "name": db["name"], "type": db["type"], "deck": db["deck"],
            "text": db["text"], "implemented": False,
            "reason": reason or "not yet implemented",
        }
    return catalog


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
