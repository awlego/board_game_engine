"""Deck M — Farmers of the Moor.

Every M-deck card depends on expansion systems this engine does not
model (fuel/heating, horses, forest and moor tiles, special actions);
see CARDS.md "Known remaining gaps". The whole deck stays unimplemented
until those systems land, so the codes are gated wholesale from the DB
rather than listed by hand.
"""

from .. import cards

UNIMPLEMENTED = {
    code: "requires Farmers of the Moor systems (fuel/heating, horses, "
          "forest/moor tiles)"
    for code, db in cards.compendium().items()
    if db["deck"] == "M"
}
