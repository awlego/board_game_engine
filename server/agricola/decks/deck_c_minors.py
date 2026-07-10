"""Deck C minor improvements (codes C001-C084 from the compendium DB).

Data-quality note: the PDF-derived compendium text for several of these
cards contains bleed-through from an adjacent card's meta+text (a column-
boundary parsing artifact in tools/parse_compendium.py) -- e.g. a card
costing "1F" whose text contains a second, unrelated "(Cost 2W. Req 2
occ.)" clause partway through, sometimes tagged "(FotM)" in the rulings
of an entirely different (Farmers of the Moor) card. Where the DB's own
top-level cost/vp/prereq fields don't match a clause embedded mid-text,
that clause is treated as noise from a different card and ignored; the
registration below implements only the card's own (leading) clause. This
is noted per-card below where it applies.
"""

from server.agricola.cards import (
    compendium_card, prompt_choice, add_goods, animal_totals_of,
    needs_occupations, needs_grain_field, card_fields,
)
from server.agricola.state import (
    ANIMAL_TYPES, HARVEST_ROUNDS, TOTAL_ROUNDS,
    compute_pastures, plowable_cells,
)

UNIMPLEMENTED = {
    "C001": "razes and rebuilds existing fences with new pieces -- "
            "moving/removing built fences is unsupported",
    "C010": "conditional room-gated capacity (once you have 4 rooms, hold "
            "5 people) and per-species card storage (1 sheep/1 boar/1 "
            "cattle) aren't expressible via the flat extra_rooms/"
            "house_capacity queries",
    "C012": "per-pasture-count animal storage (grows with pastures built) "
            "has no supported query -- house_capacity only takes a flat "
            "int or 'per_room'",
    "C013": "renovating directly from wood to stone, skipping the clay "
            "stage, requires overriding the fixed RENOVATION_TARGET "
            "sequence -- not exposed to cost_mod",
    "C015": "grants an extra 'Build Fences' action tied to using a "
            "specific action space -- only bake_on_spaces exists for that "
            "trigger shape, and fences need a whole layout choice besides",
    "C021": "reacts to an action-space card being revealed -- no reveal "
            "hook exists (only round_start/space_used)",
    "C022": "moves the first-placed person to this card and grants an "
            "extra person placement this turn -- same unsupported "
            "mechanic as guest tokens/extra placements",
    "C023": "lets one person use two action spaces in sequence outside "
            "animal markets -- lasso is hardcoded to the animal-market "
            "double-placement case only",
    "C024": "grants a free, scheduled Family Growth action (no person "
            "placed) at the next harvest -- no hook fires at 'start of "
            "harvest', and replicating family-growth bookkeeping "
            "(people_total/newborns/family_growth event) from a deck "
            "module isn't supported",
    "C028": "grants an extra 'play an occupation' action tied to using "
            "another action space -- playing occupations is "
            "engine-internal (_play_occupation), not exposed to hooks",
    "C031": "bonus equals the sum of the player's other negative score "
            "categories; computing that from within score_bonus risks "
            "recursing back into score_bonuses/score_player",
    "C039": "in 1-3 player games this card becomes an entirely new "
            "personal action space (a 'Traveling Players' proxy) -- "
            "adding action spaces dynamically isn't supported",
    "C040": "cost is a player choice between two alternative payments "
            "(1 grain or 1 reed) with different resulting gains -- the "
            "engine's cost model is one fixed dict, no alternative-"
            "payment mechanism",
    "C043": "no hook fires when a major improvement is built (only "
            "rooms_built/stable_built/fences_built/renovate exist for "
            "structures)",
    "C048": "detecting 'the first unused farmyard space used this turn' "
            "needs a turn-boundary hook; the existing per-structure "
            "hooks can co-occur within one turn (e.g. Farm Expansion), "
            "risking double-counting with no turn-level dedupe available",
    "C049": "conversion count depends on the number of empty, unfenced "
            "stables (dynamic per-instance state); conversions' "
            "per_harvest cap is a static integer fixed at registration",
    "C053": "reacts to a feeding-phase conversion of 2 goods at once -- "
            "the feeding action's conversion loop never fires a hook "
            "event",
    "C054": "cost and effect both spend structural components (a stable, "
            "a fence, from supply) rather than resource goods -- the "
            "pay/cost system only handles the resources dict",
    "C060": "requires returning (removing) an already-built major "
            "improvement -- no removal mechanism for built majors exists",
    "C062": "doubles each owned cooking improvement's rate once per "
            "harvest -- needs per-major usage tracking and a dynamic "
            "rate override beyond the conversions/cook query shapes",
    "C063": "cost draws from two different pools at once (1 grain from "
            "general supply plus 1 grain from a specific field) -- "
            "conversions' 'give' only debits the fungible resource pool, "
            "not field-stored crops",
    "C069": "converts 3 of the grain in a field into 1 vegetable in that "
            "same field -- a field cell only stores a single "
            "{type,count} crop, so a partial-type conversion within one "
            "field isn't representable. Reassessed for engine phase 13's "
            "multi-stack card fields: unaffected -- 'stacks' gives a "
            "CARD instance several independent single-crop slots, but "
            "does not change a farmyard CELL (or any one slot) to hold "
            "two crop types at once, which is exactly what this "
            "exchange needs. Still gated.",
    "C071": "reacts to the breeding phase (no hook exists) and grants a "
            "free Sow action outside the normal action-space flow (not "
            "exposed)",
    "C072": "invokes an out-of-sequence harvest field phase and grants a "
            "free Major/Minor Improvement action -- neither is exposed "
            "to card hooks",
    "C073": "the 'sow' hook doesn't report which action space triggered "
            "it, so 'an unconditional Sow action' (vs. one bundled with "
            "plow or bake) can't be verified",
    "C075": "restocks 1 wood each round in the 'returning home' "
            "(end-of-round) phase -- no end-of-round hook exists (only "
            "round_start, at the following preparation phase)",
}

_COOKING_MAJORS = ("fireplace_2", "fireplace_3",
                    "cooking_hearth_4", "cooking_hearth_5")


def _needs_pasture(n=1):
    return (lambda s, p: len(compute_pastures(p)) >= n, f"{n} pasture(s)")


def _schedule_goods(state, player, good, rounds, amount=1):
    """Place `amount` of `good` on each of `rounds` (future round numbers
    only); granted automatically at the start of those rounds, Well-style
    (mirrors cards.schedule_on_play's internal bookkeeping)."""
    targets = [r for r in rounds if state["round"] < r <= TOTAL_ROUNDS]
    for r in targets:
        slot = state["round_goods"].setdefault(str(r), {}) \
            .setdefault(str(player["index"]), {})
        add_goods(slot, {good: amount})
    return targets


def _remove_one_animal(player, species):
    """Remove a single animal of `species` from cells then pets (mirrors
    engine._remove_animals for a count of 1)."""
    for cell in player["cells"]:
        a = cell.get("animal")
        if a and a["type"] == species and a["count"] > 0:
            a["count"] -= 1
            if a["count"] <= 0:
                cell["animal"] = None
            return True
    pets = player.get("pets", {})
    if pets.get(species):
        pets[species] -= 1
        if pets[species] <= 0:
            del pets[species]
        return True
    return False


# ── On-play gains (count-dependent) ──────────────────────────────────
def _c004_play(state, player, inst, ctx):
    n = len(player["occupations"])
    if n:
        add_goods(ctx["extra"], {"wood": n})
        ctx["log"].append(f"{player['name']}'s Writing Boards grants {n} wood")

compendium_card("C004", hooks={"play": _c004_play})
# C004 Writing Boards: "You immediately get 1 wood for each occupation
# you have in front of you."


def _c005_play(state, player, inst, ctx):
    clay_rooms = sum(1 for c in player["cells"] if c["type"] == "room") \
        if player["house_type"] == "clay" else 0
    n = clay_rooms + len(player["improvements"])
    if n:
        add_goods(ctx["extra"], {"clay": n})
        ctx["log"].append(f"{player['name']}'s Remodeling grants {n} clay")

compendium_card("C005", hooks={"play": _c005_play})
# C005 Remodeling: "You immediately get 1 clay for each clay room and
# for each major improvement you have." (The DB text continues with a
# "(Cost 1F.) Immediately place 1 stone on each of your empty fields..."
# clause whose ruling is tagged "(FotM)" -- Farmers of the Moor bleed;
# not implemented, per GUIDE's FotM exclusion.)


def _c083_play(state, player, inst, ctx):
    add_goods(ctx["extra"], {"cattle": 2})
    ctx["log"].append(f"{player['name']}'s Early Cattle grants 2 cattle")

compendium_card("C083", prereq=_needs_pasture(1), hooks={"play": _c083_play})
# C083 Early Cattle (-3VP, req 1 pasture): "When you play this card, you
# immediately get 2 cattle." (Note: uses ctx["extra"] rather than the
# on_play_gain factory, since on_play_gain writes straight into
# player["resources"] and would skip the accommodation prompt for an
# animal good.)


def _c038_play(state, player, inst, ctx):
    for p2 in state["players"]:
        if p2["index"] != player["index"]:
            p2["resources"]["food"] += 1
    ctx["log"].append(f"{player['name']}'s Christianity grants 1 food to "
                      "all other players")

compendium_card(
    "C038",
    prereq=(lambda s, p: animal_totals_of(p)["sheep"] == 1,
            "exactly 1 sheep"),
    hooks={"play": _c038_play})
# C038 Christianity (2VP, req exactly 1 sheep): "When you play this card,
# all other players get 1 food each."


def _c032_prereq(state, player):
    return all(len(pl["occupations"]) + len(pl["minors"]) < 5
               for pl in state["players"])

compendium_card(
    "C032",
    prereq=(_c032_prereq, "no player has 5+ cards in front of them"))
# C032 Abort Oriel (3VP): "You can no longer play this card when any
# player (including you) has 5 or more cards in front of them." (Purely
# a playability gate -- printed points only, no effect hook. Ruling
# clarifies it may be played as one's own fifth card, matching an "at
# most 4 before playing" check.)


# ── Choice-driven on-play/harvest/space effects ──────────────────────
def _c007_play(state, player, inst, ctx):
    sheep = animal_totals_of(player)["sheep"]
    prompt_choice(state, player, inst["id"], "Blade Shears: choose your food",
                  ["3 food", f"{sheep} food (1 per sheep)"],
                  data={"sheep": sheep})

def _c007_choice(state, player, inst, ctx):
    amount = 3 if ctx["index"] == 0 else ctx["data"]["sheep"]
    if amount:
        add_goods(ctx["extra"], {"food": amount})
    ctx["log"].append(f"{player['name']}'s Blade Shears grants {amount} food")

compendium_card(
    "C007", prereq=_needs_pasture(1),
    hooks={"play": _c007_play}, resolve_choice=_c007_choice)
# C007 Blade Shears (req 1 pasture): "You immediately get your choice of
# 3 food or 1 food for each sheep you have. (Keep the sheep.)"


def _c057_play(state, player, inst, ctx):
    if player["resources"]["food"] >= 3:
        prompt_choice(state, player, inst["id"],
                      "Crudite: buy 1 vegetable for 3 food?", ["Yes", "No"])

def _c057_play_choice(state, player, inst, ctx):
    if ctx["index"] != 0:
        return
    player["resources"]["food"] -= 3
    add_goods(ctx["extra"], {"vegetable": 1})
    ctx["log"].append(f"{player['name']}'s Crudite buys 1 vegetable")

def _c057_has_veg_field(player):
    if any(c["crops"] and c["crops"]["type"] == "vegetable"
           for c in player["cells"]):
        return True
    return any(i["crops"] and i["crops"]["type"] == "vegetable"
               for i in card_fields(player))

def _c057_available(state, player, inst):
    return player["resources"]["vegetable"] >= 1 and \
        _c057_has_veg_field(player)

def _c057_apply(state, player, inst, ctx):
    player["resources"]["vegetable"] -= 1
    player["resources"]["food"] += 4
    ctx["log"].append(f"{player['name']}'s Crudite discards 1 vegetable "
                      "for 4 food")

compendium_card(
    "C057",
    hooks={"play": _c057_play}, resolve_choice=_c057_play_choice,
    card_action={"available": _c057_available, "apply": _c057_apply,
                 "description": "Discard 1 vegetable onto a vegetable "
                 "field for 4 food"})
# C057 Crudite: "When you play this card, you can immediately buy exactly
# 1 vegetable for 3 food. At any time, you can discard 1 vegetable on top
# of another vegetable in a field to get 4 food."


# ── Harvest-triggered effects ────────────────────────────────────────
def _c029_harvest(state, player, inst, ctx):
    if player["resources"]["grain"] >= 1:
        prompt_choice(state, player, inst["id"],
                      "Beer Table: pay 1 grain for 2 bonus points? "
                      "(other players get 1 food each)", ["Yes", "No"])

def _c029_choice(state, player, inst, ctx):
    if ctx["index"] != 0:
        return
    player["resources"]["grain"] -= 1
    inst["data"]["bp"] = inst["data"].get("bp", 0) + 2
    for p2 in state["players"]:
        if p2["index"] != player["index"]:
            p2["resources"]["food"] += 1
    ctx["log"].append(f"{player['name']}'s Beer Table converts 1 grain "
                      "into 2 bonus points; other players get 1 food each")

compendium_card(
    "C029",
    prereq=(lambda s, p: p["resources"]["grain"] == 0,
            "no grain in your supply"),
    hooks={"harvest_field": _c029_harvest}, resolve_choice=_c029_choice,
    score_bonus=lambda s, p, i: i["data"].get("bp", 0))
# C029 Beer Table (req no grain in supply): "At the end of the field
# phase of each harvest, you can pay 1 grain from your supply to get 2
# bonus points. If you do, all other players get 1 food each." (DB text
# continues with an unrelated "(Cost 1W 1C 2S 1R.) ... stone room" bonus
# clause -- bleed, not implemented.)


def _c034_harvest(state, player, inst, ctx):
    if player["resources"]["reed"] >= 1:
        prompt_choice(state, player, inst["id"],
                      "Elephantgrass Plant: exchange 1 reed for 1 bonus "
                      "point?", ["Yes", "No"])

def _c034_choice(state, player, inst, ctx):
    if ctx["index"] != 0:
        return
    player["resources"]["reed"] -= 1
    inst["data"]["bp"] = inst["data"].get("bp", 0) + 1
    ctx["log"].append(f"{player['name']}'s Elephantgrass Plant grants 1 "
                      "bonus point")

compendium_card(
    "C034", prereq=needs_occupations(2),
    hooks={"harvest_field": _c034_harvest}, resolve_choice=_c034_choice,
    score_bonus=lambda s, p, i: i["data"].get("bp", 0))
# C034 Elephantgrass Plant (req 2 occ): "Immediately after each harvest,
# you can use this card to exchange exactly 1 reed for 1 bonus point."
# (A ruling tagged "(FotM)" caps this at 5 -- not applied, matching the
# base-game text. The ruling also contains a wholly unrelated second
# card's meta+text bleeding in; ignored.)


def _c066_harvest(state, player, inst, ctx):
    grain = player["resources"]["grain"]
    if grain >= 3:
        player["resources"]["grain"] += 1
        ctx["log"].append(f"{player['name']}'s Eternal Rye Cultivation "
                          "grants 1 grain")
    elif grain == 2:
        player["resources"]["food"] += 1
        ctx["log"].append(f"{player['name']}'s Eternal Rye Cultivation "
                          "grants 1 food")

compendium_card("C066", prereq=needs_grain_field(1),
                hooks={"harvest_field": _c066_harvest})
# C066 Eternal Rye Cultivation (req 1 grain field): "After each harvest
# in which you have 2 or 3+ grain in your supply, you get 1 food and 1
# additional grain, respectively." Per the card's own errata, the final
# "and" is "or" (mutually exclusive tiers), as implemented.


# ── Round-start effects ──────────────────────────────────────────────
def _c067_has_pastured_sheep(player):
    pasture_cells = {i for pa in compute_pastures(player) for i in pa}
    return any(player["cells"][i].get("animal")
               and player["cells"][i]["animal"]["type"] == "sheep"
               for i in pasture_cells)

def _c067_round_start(state, player, inst, ctx):
    if state["round"] in HARVEST_ROUNDS:
        return
    if _c067_has_pastured_sheep(player):
        player["resources"]["grain"] += 1
        ctx["log"].append(f"{player['name']}'s Mineral Feeder grants 1 grain")

compendium_card("C067", hooks={"round_start": _c067_round_start})
# C067 Mineral Feeder: "At the start of each round that does not end
# with a harvest, if you have at least 1 sheep in a pasture, you get 1
# grain." (DB text continues with an unrelated "(Cost 2W. Req 1 occ.)
# ... 1 vegetable" clause -- bleed, not implemented.)


# ── Space-use triggers ───────────────────────────────────────────────
def _c052_hook(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    gained = ctx["goods"].get("boar", 0) + ctx["extra"].get("boar", 0)
    if gained > 0:
        add_goods(ctx["extra"], {"food": gained})
        ctx["log"].append(f"{player['name']}'s Huntsman's Hat grants "
                          f"{gained} food")

compendium_card(
    "C052",
    prereq=(lambda s, p: any(m in p["improvements"]
                             for m in _COOKING_MAJORS),
            "a cooking improvement"),
    hooks={"space_used": _c052_hook})
# C052 Huntsman's Hat (req cooking imp): "For each new wild boar you get
# from the effect of an action space, you also get 1 food."


def _c058_hook(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or not ctx["goods"].get("wood"):
        return
    projected = player["resources"]["wood"] + ctx["extra"].get("wood", 0)
    if projected <= 5:
        add_goods(ctx["extra"], {"food": 1})
        ctx["log"].append(f"{player['name']}'s Woodcraft grants 1 food")

compendium_card("C058", prereq=needs_occupations(1),
                hooks={"space_used": _c058_hook})
# C058 Woodcraft (req 1 occ): "Each time you use a wood accumulation
# space, if immediately afterward you have at most 5 wood in your
# supply, you get 1 food." (DB text continues with an unrelated
# "(2VP. Cost 1S 1 vegetable.) ... vegetables" clause -- bleed, not
# implemented.)


def _c076_hook(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or not ctx["goods"].get("wood"):
        return
    add_goods(ctx["extra"], {"wood": 2})
    ctx["log"].append(f"{player['name']}'s Wood Cart adds 2 wood")

compendium_card("C076", prereq=needs_occupations(3),
                hooks={"space_used": _c076_hook})
# C076 Wood Cart (req 3 occ): "Whenever you use a person to take Wood
# that is on an Action space, you receive 2 additional Wood."


def _c082_hook(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or ctx["space_id"] != "day_laborer":
        return
    if player["resources"]["food"] >= 2:
        prompt_choice(state, player, inst["id"],
                      "Hardware Store: pay 2 food for 1 wood, 1 clay, "
                      "1 reed, and 1 stone?", ["Yes", "No"])

def _c082_choice(state, player, inst, ctx):
    if ctx["index"] != 0:
        return
    player["resources"]["food"] -= 2
    add_goods(player["resources"], {"wood": 1, "clay": 1, "reed": 1,
                                    "stone": 1})
    ctx["log"].append(f"{player['name']}'s Hardware Store buys 1 each of "
                      "wood/clay/reed/stone")

compendium_card(
    "C082",
    prereq=(lambda s, p: p["resources"]["reed"] >= 1
            and p["resources"]["stone"] >= 1,
            "1 reed and 1 stone in your supply"),
    hooks={"space_used": _c082_hook}, resolve_choice=_c082_choice)
# C082 Hardware Store (req 1 reed 1 stone in supply): "Each time after
# you use the Day Laborer action space, you can pay 2 food total to buy
# 1 wood, 1 clay, 1 reed, and 1 stone."


def _c036_hook(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or \
            ctx["space_id"] not in ("clay_pit", "hollow_3p", "hollow_4p"):
        return
    if player["resources"]["clay"] >= 1:
        prompt_choice(state, player, inst["id"],
                      "Clay Deposit: exchange 1 clay for 1 bonus point?",
                      ["Yes", "No"], data={"space_id": ctx["space_id"]})

def _c036_choice(state, player, inst, ctx):
    if ctx["index"] != 0:
        return
    player["resources"]["clay"] -= 1
    inst["data"]["bp"] = inst["data"].get("bp", 0) + 1
    space = next((sp for sp in state["action_spaces"]
                  if sp["id"] == ctx["data"]["space_id"]), None)
    if space is not None:
        space["supply"]["clay"] = space["supply"].get("clay", 0) + 1
    ctx["log"].append(f"{player['name']}'s Clay Deposit grants 1 bonus "
                      "point")

compendium_card(
    "C036", prereq=needs_occupations(1),
    hooks={"space_used": _c036_hook}, resolve_choice=_c036_choice,
    score_bonus=lambda s, p, i: i["data"].get("bp", 0))
# C036 Clay Deposit (req 1 occ; distinct from the base deck's
# minor_clay_deposit): "Immediately after each time you use a clay
# accumulation space, you can exchange 1 clay for 1 bonus point. If you
# do, place the clay on the accumulation space." (DB text continues
# with an unrelated "(3VP. Cost 1F. Play in round 3 or before.) ...
# field tile" clause -- bleed, not implemented.)


def _c081_play(state, player, inst, ctx):
    inst["data"] = {"wood": 2, "clay": 2, "reed": 2, "stone": 2}
    ctx["log"].append(f"{player['name']}'s Material Hub stocks 2 of each "
                      "building resource")

_C081_THRESHOLDS = {"wood": 5, "clay": 4, "reed": 3, "stone": 3}

def _c081_hook(state, player, inst, ctx):
    for good, threshold in _C081_THRESHOLDS.items():
        if ctx["goods"].get(good, 0) >= threshold \
                and inst["data"].get(good, 0) > 0:
            inst["data"][good] -= 1
            player["resources"][good] += 1
            ctx["log"].append(f"{player['name']}'s Material Hub grants "
                              f"1 {good}")

compendium_card("C081", hooks={"play": _c081_play, "space_used": _c081_hook})
# C081 Material Hub: "Immediately place 2 of each building resource on
# this card. Each time any player (including you) takes at least 5
# wood, 4 clay, 3 reed, or 3 stone, you get 1 of that building resource
# from this card." (Fires for any player's space use, always crediting
# the card's OWNER -- not gated on ctx["actor"].)


# ── Plow / bake / sow hooks ──────────────────────────────────────────
def _c080_plow(state, player, inst, ctx):
    if player["resources"]["food"] >= 1:
        prompt_choice(state, player, inst["id"],
                      "Rocky Terrain: buy 1 stone for 1 food?",
                      ["Yes", "No"])

def _c080_choice(state, player, inst, ctx):
    if ctx["index"] != 0:
        return
    player["resources"]["food"] -= 1
    player["resources"]["stone"] += 1
    ctx["log"].append(f"{player['name']}'s Rocky Terrain buys 1 stone")

compendium_card("C080", hooks={"plow": _c080_plow}, resolve_choice=_c080_choice)
# C080 Rocky Terrain: "Each time you plow a field (tile or card), you
# can also buy 1 stone for 1 food." Implemented for tile plows (the
# "plow" event); playing a field-card minor doesn't fire "plow" in this
# engine, so that half of "(tile or card)" isn't covered.


def _c061_bake(state, player, inst, ctx):
    if ctx["grain"] > 0 and player["resources"]["grain"] >= 1:
        prompt_choice(state, player, inst["id"],
                      "Beer Stein: turn 1 grain into 2 food and 1 bonus "
                      "point?", ["Yes", "No"])

def _c061_choice(state, player, inst, ctx):
    if ctx["index"] != 0:
        return
    player["resources"]["grain"] -= 1
    player["resources"]["food"] += 2
    inst["data"]["bp"] = inst["data"].get("bp", 0) + 1
    ctx["log"].append(f"{player['name']}'s Beer Stein grants 2 food and "
                      "1 bonus point")

compendium_card(
    "C061", hooks={"bake": _c061_bake}, resolve_choice=_c061_choice,
    score_bonus=lambda s, p, i: i["data"].get("bp", 0))
# C061 Beer Stein: "Each time you take a Bake Bread action, you can use
# this card once to turn 1 grain into 2 food and 1 bonus point."


# ── Plowing extras ───────────────────────────────────────────────────
def _c017_plowable(player):
    """Like state.plowable_cells but without the adjacent-to-an-existing-
    field requirement (this card's text explicitly waives it)."""
    pasture_cells = {i for pa in compute_pastures(player) for i in pa}
    return [i for i, c in enumerate(player["cells"])
            if c["type"] == "empty" and not c["stable"]
            and i not in pasture_cells]

def _c017_play(state, player, inst, ctx):
    cell = (ctx.get("params") or {}).get("cell")
    options = _c017_plowable(player)
    if not isinstance(cell, int) or cell not in options:
        raise ValueError("Newly Plowed Field: choose a space to plow "
                         "(params.cell)")
    player["cells"][cell]["type"] = "field"
    ctx["log"].append(f"{player['name']} plows a field (Newly Plowed Field)")

compendium_card(
    "C017",
    prereq=(lambda s, p: sum(1 for c in p["cells"]
                             if c["type"] == "field") == 3,
            "exactly 3 field tiles"),
    hooks={"play": _c017_play})
# C017 Newly Plowed Field (req exactly 3 field tiles): "When you play
# this card, you can immediately plow 1 field, which needs not be
# adjacent to another field."


def _c018_available(state, player, inst):
    planted = [i for i, c in enumerate(player["cells"])
               if c["type"] == "field" and c["crops"]]
    return len(planted) >= 3

def _c018_apply(state, player, inst, ctx):
    params = ctx.get("params") or {}
    field_cell, plow_cell = params.get("field_cell"), params.get("plow_cell")
    planted = [i for i, c in enumerate(player["cells"])
               if c["type"] == "field" and c["crops"]]
    if field_cell not in planted:
        raise ValueError("Roll-Over Plow: choose a planted field to clear "
                         "(params.field_cell)")
    if plow_cell not in plowable_cells(player):
        raise ValueError("Roll-Over Plow: choose a valid cell to plow "
                         "(params.plow_cell)")
    player["cells"][field_cell]["crops"] = None
    player["cells"][plow_cell]["type"] = "field"
    ctx["log"].append(f"{player['name']}'s Roll-Over Plow discards a "
                      "field's goods to plow another")

compendium_card(
    "C018",
    card_action={"available": _c018_available, "apply": _c018_apply,
                 "description": "Discard a planted field's goods to plow "
                 "1 new field"})
# C018 Roll-Over Plow: "At any time, if you have at least 3 planted
# fields, you can discard all goods from one of those fields to plow 1
# field." (DB text continues with an unrelated "(Cost 3W. Req 3 occ.)
# Place 4 field tiles on this card..." clause -- bleed, not implemented.)


def _c020_hook(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or \
            ctx["space_id"] not in ("farmland", "cultivation"):
        return
    options = plowable_cells(player)
    if not options:
        return
    prompt_choice(state, player, inst["id"],
                  "Mole Plow: plow an additional field?",
                  [f"Plow cell {c}" for c in options] + ["Skip"],
                  data={"cells": options})

def _c020_choice(state, player, inst, ctx):
    cells = ctx["data"]["cells"]
    if ctx["index"] >= len(cells):
        return
    cell = cells[ctx["index"]]
    if cell in plowable_cells(player):
        player["cells"][cell]["type"] = "field"
        ctx["log"].append(f"{player['name']}'s Mole Plow plows an "
                          "additional field")

compendium_card(
    "C020",
    prereq=(lambda s, p: s["round"] >= 9, "play in round 9 or later"),
    hooks={"space_used": _c020_hook}, resolve_choice=_c020_choice)
# C020 Mole Plow (req play in round 9+): "Each time you use the 'Plow 1
# Field' or 'Plow 1 Field and/or Sow' action space, you can plow 1
# additional field." ('farmland' = Plow 1 Field, 'cultivation' = Plow 1
# Field and/or Sow, per the space_bonus factory's own naming in cards.py.)


# ── Card actions: purchases and conversions ──────────────────────────
def _c008_targets(player):
    tile = [i for i, c in enumerate(player["cells"])
            if c["crops"] and c["crops"]["count"] == 1]
    card = [i["id"] for i in card_fields(player)
            if i["crops"] and i["crops"]["count"] == 1]
    return tile, card

def _c008_available(state, player, inst):
    tile, card = _c008_targets(player)
    return bool(tile or card)

def _c008_apply(state, player, inst, ctx):
    params = ctx.get("params") or {}
    tile, card = _c008_targets(player)
    if "cell" in params and params["cell"] in tile:
        player["cells"][params["cell"]]["crops"]["count"] += 1
    elif "card" in params and params["card"] in card:
        target = next(i for i in card_fields(player)
                      if i["id"] == params["card"])
        target["crops"]["count"] += 1
    else:
        raise ValueError("Plant Fertilizer: choose a field with exactly "
                         "1 good (params.cell or params.card)")
    ctx["log"].append(f"{player['name']}'s Plant Fertilizer adds 1 good "
                      "to a field")

compendium_card(
    "C008",
    card_action={"available": _c008_available, "apply": _c008_apply,
                 "description": "Add 1 more good to a field that has "
                 "exactly 1"})
# C008 Plant Fertilizer: "In each field with exactly 1 good, you can
# immediately place 1 additional good of the same type."


_C009_COST = {"sheep": 0, "boar": 1, "cattle": 2}

def _c009_available(state, player, inst):
    return any(player["resources"]["food"] >= cost
               for cost in _C009_COST.values())

def _c009_apply(state, player, inst, ctx):
    species = (ctx.get("params") or {}).get("species")
    if species not in _C009_COST:
        raise ValueError("Automatic Water Trough: choose species "
                         "(params.species: sheep/boar/cattle)")
    cost = _C009_COST[species]
    if player["resources"]["food"] < cost:
        raise ValueError("Not enough food")
    player["resources"]["food"] -= cost
    add_goods(ctx["extra"], {species: 1})
    ctx["log"].append(f"{player['name']}'s Automatic Water Trough buys "
                      f"1 {species}")

compendium_card(
    "C009",
    card_action={"available": _c009_available, "apply": _c009_apply,
                 "description": "Buy 1 sheep/wild boar/cattle for "
                 "0/1/2 food"})
# C009 Automatic Water Trough: "If you can accommodate the animal, you
# can immediately buy 1 sheep/wild boar/cattle for 0/1/2 food."


def _c050_play(state, player, inst, ctx):
    remaining = TOTAL_ROUNDS - state["round"]
    if remaining > 0:
        add_goods(ctx["extra"], {"food": remaining})
        ctx["log"].append(f"{player['name']}'s Stable Yard grants "
                          f"{remaining} food")

def _c050_available(state, player, inst):
    totals = animal_totals_of(player)
    return totals["sheep"] >= 1 and totals["boar"] >= 1

def _c050_apply(state, player, inst, ctx):
    totals = animal_totals_of(player)
    if totals["sheep"] < 1 or totals["boar"] < 1:
        raise ValueError("Not enough sheep and wild boar")
    _remove_one_animal(player, "sheep")
    _remove_one_animal(player, "boar")
    add_goods(ctx["extra"], {"cattle": 1})
    ctx["log"].append(f"{player['name']}'s Stable Yard exchanges 1 sheep "
                      "+ 1 wild boar for 1 cattle")

compendium_card(
    "C050",
    prereq=(lambda s, p: sum(1 for c in p["cells"] if c["stable"]) >= 3
            and len(compute_pastures(p)) >= 3,
            "3 stables and 3 pastures"),
    hooks={"play": _c050_play},
    card_action={"available": _c050_available, "apply": _c050_apply,
                 "description": "Exchange 1 sheep + 1 wild boar for "
                 "1 cattle"})
# C050 Stable Yard (req 3 stables and 3 pastures): "When you play this
# card, you immediately get 1 food for each complete round left to
# play. At any time, you can exchange 1 sheep plus 1 wild boar for 1
# cattle." (DB text continues with an unrelated "(1VP. Cost 1R.) ...
# Fishing" clause -- bleed, not implemented.)


def _c084_owned_types(player):
    totals = animal_totals_of(player)
    return [t for t in ANIMAL_TYPES if totals[t] > 0]

def _c084_available(state, player, inst):
    return (state["round"] not in HARVEST_ROUNDS
            and player["resources"]["grain"] >= 1
            and bool(_c084_owned_types(player)))

def _c084_apply(state, player, inst, ctx):
    species = (ctx.get("params") or {}).get("species")
    if species not in _c084_owned_types(player):
        raise ValueError("Perennial Rye: choose an animal type you own "
                         "(params.species)")
    player["resources"]["grain"] -= 1
    add_goods(ctx["extra"], {species: 1})
    ctx["log"].append(f"{player['name']}'s Perennial Rye breeds 1 {species}")

compendium_card(
    "C084", prereq=needs_occupations(2),
    card_action={"available": _c084_available, "apply": _c084_apply,
                 "description": "Pay 1 grain to breed 1 animal of a type "
                 "you own (non-harvest rounds)"})
# C084 Perennial Rye (req 2 occ): "Each round that does not end with a
# harvest, you can pay 1 grain to breed exactly 1 type of animal. (This
# is not considered a breeding phase.)"


def _c046_available(state, player, inst):
    return player["resources"]["vegetable"] >= 1 \
        and inst["data"].get("last_round") != state["round"]

def _c046_apply(state, player, inst, ctx):
    player["resources"]["vegetable"] -= 1
    inst["data"]["last_round"] = state["round"]
    inst["data"]["bp"] = inst["data"].get("bp", 0) + 1
    rnd = state["round"]
    targets = _schedule_goods(state, player, "food", range(rnd + 1, rnd + 3))
    msg = f"{player['name']}'s Mandoline: 1 vegetable -> 1 bonus point"
    if targets:
        msg += f", food scheduled on round(s) {', '.join(map(str, targets))}"
    ctx["log"].append(msg)

compendium_card(
    "C046",
    card_action={"available": _c046_available, "apply": _c046_apply,
                 "description": "Once per round, pay 1 vegetable for 1 "
                 "bonus point (and scheduled food)"},
    score_bonus=lambda s, p, i: i["data"].get("bp", 0))
# C046 Mandoline: "Once per round, you can pay 1 vegetable to get 1
# bonus point. If you do, place 1 food on each of the next 2 round
# spaces. At the start of these rounds, you get the food."


def _c064_available(state, player, inst):
    return player["resources"]["grain"] >= 1 \
        and inst["data"].get("last_round") != state["round"]

def _c064_apply(state, player, inst, ctx):
    player["resources"]["grain"] -= 1
    inst["data"]["last_round"] = state["round"]
    rnd = state["round"]
    targets = _schedule_goods(state, player, "food", range(rnd + 1, rnd + 5))
    if targets:
        ctx["log"].append(f"{player['name']}'s Corn Schnapps Distillery "
                          f"schedules food on round(s) "
                          f"{', '.join(map(str, targets))}")

compendium_card(
    "C064",
    card_action={"available": _c064_available, "apply": _c064_apply,
                 "description": "Once per round, pay 1 grain to schedule "
                 "food on the next 4 rounds"})
# C064 Corn Schnapps Distillery: "Once per round, you can pay 1 grain to
# place 1 food on each of the next 4 round spaces. At the start of these
# rounds, you get the food." (DB text continues with an unrelated
# "(1VP. Cost 3W/3C.) ... grain" clause -- bleed, not implemented.)


# ── Schedulers (round-space goods) ───────────────────────────────────
def _c077_play(state, player, inst, ctx):
    rnd = state["round"]
    targets = _schedule_goods(state, player, "clay", range(rnd + 1, rnd + 4))
    if targets:
        ctx["log"].append(f"{player['name']}'s Clay Supply places clay on "
                          f"round(s) {', '.join(map(str, targets))}")

compendium_card("C077", hooks={"play": _c077_play})
# C077 Clay Supply: "Place 1 clay on each of the next 3 round spaces. At
# the start of these rounds, you get the clay."


def _c078_play(state, player, inst, ctx):
    rnd = state["round"]
    targets = _schedule_goods(state, player, "reed",
                              [rnd + 5, rnd + 7, rnd + 9, rnd + 11, rnd + 13])
    if targets:
        ctx["log"].append(f"{player['name']}'s Toad places reed on "
                          f"round(s) {', '.join(map(str, targets))}")

compendium_card("C078", hooks={"play": _c078_play})
# C078 Toad: "Add 5, 7, 9, 11 and 13 to the current round and place 1
# reed on each corresponding round space. At the start of these rounds,
# you receive the reed." (DB text continues with an unrelated
# "(Cost 2W. Req 2 occ.) ... stone" clause -- bleed, not implemented.)


def _c045_hook(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or ctx["space_id"] != "day_laborer":
        return
    rnd = state["round"]
    targets = _schedule_goods(state, player, "food", range(rnd + 1, rnd + 5))
    if targets:
        ctx["log"].append(f"{player['name']}'s Stew places food on "
                          f"round(s) {', '.join(map(str, targets))}")

compendium_card("C045", hooks={"space_used": _c045_hook})
# C045 Stew: "Each time you use the Day Laborer action space, also place
# 1 food on each of the next 4 round spaces. At the start of these
# rounds, you get the food."


def _c047_play(state, player, inst, ctx):
    fields = sum(1 for c in player["cells"] if c["crops"])
    fields += sum(1 for i in card_fields(player) if i["crops"])
    cap = fields * 3
    if cap <= 0:
        return
    rnd = state["round"]
    targets = _schedule_goods(state, player, "food",
                              list(range(rnd + 1, TOTAL_ROUNDS + 1))[:cap])
    if targets:
        ctx["log"].append(f"{player['name']}'s Garden Claw places food on "
                          f"{len(targets)} round space(s)")

compendium_card("C047", hooks={"play": _c047_play})
# C047 Garden Claw: "Place 1 food on each remaining round space, up to
# three times the number of planted fields you have. At the start of
# these rounds, you get the food."


# ── Scoring-only minors ───────────────────────────────────────────────
def _c033_unplanted(player):
    n = sum(1 for c in player["cells"]
            if c["type"] == "field" and not c["crops"])
    n += sum(1 for i in card_fields(player) if not i["crops"])
    return n

def _c033_score(state, player, inst):
    n = _c033_unplanted(player)
    for minimum, pts in ((6, 5), (5, 3), (4, 2), (2, 1)):
        if n >= minimum:
            return pts
    return 0

compendium_card("C033", score_bonus=_c033_score)
# C033 Greening Plan: "During scoring, if you then have at least
# 2/4/5/6 unplanted fields, you get 1/2/3/5 bonus points."


# ── Conversions ───────────────────────────────────────────────────────
compendium_card("C055", conversions=[
    {"give": {"wood": 1}, "get": {"food": 2}},
    {"give": {"clay": 1}, "get": {"food": 2}},
    {"give": {"stone": 1}, "get": {"food": 3}},
])
# C055 Studio: "In the feeding phase of each harvest, you can use this
# card to turn exactly 1 wood/clay/stone into 2/2/3 food." (DB text
# continues with an unrelated "(Cost 1W.) ... stable" clause about
# building stables for clay -- bleed, not implemented.)
