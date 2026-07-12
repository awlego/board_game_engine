"""Deck FR (France) minor improvements (codes FR001-FR060 from the
compendium DB).

Most FR entries parse cleanly (unlike deck B's column-bleed artifacts).
One exception: FR016 Cornrick's cost/prereq fields disagree with its own
`meta` string ("(Cost 1F. Req 1 field.)" vs a parsed cost of "" and a
prereq of "1 field; )"), a parser glitch on the trailing parenthesis;
see _TEXT_FIXES below, following the pattern deck_b_occupations.py uses
for its own DB corruption fixes.

A few cards have printed alternative ("1W or 1S"-style) costs; per
GUIDE.md ground rule 1 these are registered as cost=[{...}, {...}] and
noted in a comment. FR020 is the one exception still collapsed to a
single fixed option -- see its own comment for why.
"""

from server.agricola.cards import (
    compendium_card, CARDS, new_instance, add_goods, prompt_choice,
    schedule_on_play, harvest_food, space_bonus, animal_totals_of,
    needs_occupations, exact_occupations, combine, card_fields,
    modified_cost, fire, fire_player, bake_bonus,
)
from server.agricola import cards, sub_actions
from server.agricola.state import (
    ANIMAL_TYPES, TOTAL_ROUNDS, HARVEST_ROUNDS, MAJOR_IMPROVEMENTS,
    FIREPLACES, COOKING_HEARTHS, NUM_CELLS, compute_pastures, plowable_cells,
    orthogonal_neighbors, SPACE_POSITIONS, ROUND_SLOTS,
)

UNIMPLEMENTED = {
    "FR005": "requires a per-round 'returning home phase' harvest hook; "
             "harvest_field only fires during the 6 official harvests, "
             "not every round",
    "FR009": "requires detecting 'converted a grain/vegetable into food' "
             "as a discrete event; raw crop-to-food conversion is a "
             "static rate query (raw_values), never a fired event (same "
             "gap as B029)",
    "FR014": "requires a food stash usable only to pay Occupation costs; "
             "the occupation-cost payment path has no hook for an "
             "alternate resource source (same gap as B155)",
    "FR025": "lets a played minor be discarded to fully pay for a major "
             "improvement, and treats Clay/Stone Ovens as minors for "
             "other cards' prerequisites; neither an alternate-payment-"
             "via-discard channel nor a 'counts as' prerequisite override "
             "exists",
    "FR029": "lets a placement ignore that an action space is already "
             "occupied; placing on occupied spaces is explicitly "
             "unsupported",
    "FR034": "would need to intercept the Bake Bread action before it "
             "resolves to offer 'store food instead'; the only baking "
             "hook fires after a bake completes (with the grain count), "
             "not before/instead of one",
    "FR047": "requires reducing the number of people placed in a "
             "specific future round (no such placement-count modifier "
             "exists) and a round-end hook to grant free occupations "
             "afterward (only round_start fires; there is no round-end "
             "event)",
    "FR051": "restricts a resource stash to paying only Minor Improvement "
             "costs; occupation/room/renovation/improvement costs all "
             "draw from the same undifferentiated resource pool with no "
             "hook to gate payment by source, so the card's core "
             "restriction (its only balancing feature) can't be enforced",
    "FR058": "conditionally keeps or passes the card based on an in-hook "
             "choice; _play_minor decides whether to send a card to the "
             "left neighbor using the static `traveling` spec flag "
             "immediately after the play hook returns, before any later "
             "resolve_choice could run, so the outcome can't be made "
             "conditional on a choice made inside that hook",
    "FR059": "extends or shrinks the farmyard by 2 spaces; the 15-cell "
             "grid (ROWS*COLS) is a fixed engine constant with no per-"
             "player extension/removal mechanism. Reassessed for engine "
             "phase 13 (the last item of the 19-item engine-gap program) "
             "and still gated -- no clean seam found: `player['cells']` "
             "is a fixed-length list every geometry query (orthogonal_"
             "neighbors' row/col math, compute_regions'/compute_pastures' "
             "flood fill, plowable_cells) and every scoring table "
             "('fields'/'pastures'/'unused_spaces') assumes has exactly "
             "NUM_CELLS entries with a fixed 3x5 (row, col) shape; a "
             "per-player variable-length or non-rectangular farmyard "
             "would need cell_rc/cell_index/cell_edges/edge_cells (the "
             "coordinate system fence edges are keyed on) to become "
             "per-player rather than global constants -- a materially "
             "bigger change than the multi-stack/fence-token mechanisms "
             "above, not a bounded addition. See CARDS.md item 19 and "
             "decks/GUIDE.md's 'Field/fence/grid extensions' section for "
             "the full writeup; this is the one remaining gated geometry "
             "gap after phase 13.",
}

_MIDDLE_CELLS = (6, 7, 8)  # the 3 farmyard cells with all 8 neighbors
                           # present, on this engine's 3-row x 5-col grid


def _total_grain(player):
    total = player["resources"]["grain"]
    for c in player["cells"]:
        if c["crops"] and c["crops"]["type"] == "grain":
            total += c["crops"]["count"]
    for inst in card_fields(player):
        if inst["crops"] and inst["crops"]["type"] == "grain":
            total += inst["crops"]["count"]
    return total


def _total_vegetable(player):
    total = player["resources"]["vegetable"]
    for c in player["cells"]:
        if c["crops"] and c["crops"]["type"] == "vegetable":
            total += c["crops"]["count"]
    for inst in card_fields(player):
        if inst["crops"] and inst["crops"]["type"] == "vegetable":
            total += inst["crops"]["count"]
    return total


def _planted_field_count(player, crop=None):
    """Currently-sown fields (farmyard cells + card fields), optionally
    filtered to one crop type."""
    n = 0
    for c in player["cells"]:
        if c["type"] == "field" and c["crops"] and \
                (crop is None or c["crops"]["type"] == crop):
            n += 1
    for inst in card_fields(player):
        if inst["crops"] and (crop is None or inst["crops"]["type"] == crop):
            n += 1
    return n


def _field_count(player):
    """All field-tile farmyard cells, planted or not."""
    return sum(1 for c in player["cells"] if c["type"] == "field")


def _has_oven(player):
    return bool({"clay_oven", "stone_oven"} & set(player["improvements"]))


def _remove_animal(player, animal_type, count=1):
    """Remove `count` animals of a type from cells/pets. Returns True if
    the full amount was removed."""
    remaining = count
    for c in player["cells"]:
        a = c.get("animal")
        if a and a["type"] == animal_type and remaining > 0:
            take = min(a["count"], remaining)
            a["count"] -= take
            remaining -= take
            if a["count"] <= 0:
                c["animal"] = None
    if remaining > 0 and player["pets"].get(animal_type):
        take = min(player["pets"][animal_type], remaining)
        player["pets"][animal_type] -= take
        if player["pets"][animal_type] <= 0:
            del player["pets"][animal_type]
        remaining -= take
    return remaining == 0


def _schedule_good(player, state, good, rounds, amount=1):
    for r in rounds:
        slot = state["round_goods"].setdefault(str(r), {}) \
            .setdefault(str(player["index"]), {})
        add_goods(slot, {good: amount})


def _room_eligible_cells(player):
    """Empty, non-stable, non-pasture cells orthogonally adjacent to an
    existing room (mirrors engine._buildable_room_cells)."""
    rooms = {i for i, c in enumerate(player["cells"]) if c["type"] == "room"}
    pasture_cells = {i for pa in compute_pastures(player) for i in pa}
    out = []
    for i, c in enumerate(player["cells"]):
        if i in rooms or c["type"] != "empty" or c["stable"] or i in pasture_cells:
            continue
        if any(nb in rooms for nb in orthogonal_neighbors(i)):
            out.append(i)
    return out


def _play_occupation_bypassing_cost(state, player, cid, log):
    """Instantiate an occupation directly (its own printed food cost is
    waived/handled by the calling card instead), running its own play
    hook and firing occupation_played so other cards react normally."""
    played_spec = CARDS[cid]
    player["hand_occupations"].remove(cid)
    inst = new_instance(cid)
    player["occupations"].append(inst)
    player["occs_played"] += 1
    log.append(f"{player['name']} plays the occupation "
               f"\"{played_spec['name']}\"")
    extra = {}
    play_fn = played_spec["hooks"].get("play")
    if play_fn:
        sub_ctx = {"params": {}, "log": log, "actor": player["index"],
                   "extra": extra}
        play_fn(state, player, inst, sub_ctx)
    fire(state, "occupation_played",
        {"card_id": cid, "log": log, "actor": player["index"], "extra": extra})
    return extra


# ── FR001 Abandoned Willow ─────────────────────────────────────────────
# "Immediately remove 1 empty field from your farmyard and receive 4
# Wood. (That space now counts as unused)." Req 1 empty field. No new
# engine plumbing needed -- a play hook flips cell['type'] back to
# 'empty' via the params channel (Shifting Cultivation's on-play plow,
# run in reverse); see decks/GUIDE.md's "FR001 recipe" section and
# test_fr001_style_remove_empty_field_recipe in tests/test_agricola.py
# for the full regression (plow target, pasture validity, and scoring
# all verified to behave normally on the reverted cell).
#
# Ruling "You may not remove a field which causes other fields to be
# isolated" is NOT modeled: this engine has no adjacency/connectivity
# requirement for fields anywhere -- a field cell works identically
# whether or not it touches another field cell (no scoring category,
# card effect, or validation anywhere keys off field-to-field
# adjacency), so there is no "isolated" state to detect or forbid. The
# GUIDE.md recipe this card follows was explicitly verified to introduce
# no such hidden invariant.
def _abandoned_willow_play(state, player, inst, ctx):
    cell = (ctx.get("params") or {}).get("cell")
    c = player["cells"][cell] if isinstance(cell, int) \
        and 0 <= cell < NUM_CELLS else None
    if c is None or c["type"] != "field" or c["crops"]:
        raise ValueError("Abandoned Willow: choose an empty field to "
                         "remove (params.cell)")
    c["type"] = "empty"
    player["resources"]["wood"] += 4
    ctx["log"].append(f"{player['name']} removes an empty field "
                      "(Abandoned Willow), gets 4 wood")

def _has_empty_field(s, p):
    return any(c["type"] == "field" and not c["crops"] for c in p["cells"])

compendium_card(
    "FR001", prereq=(_has_empty_field, "1 empty field"),
    hooks={"play": _abandoned_willow_play},
)


# ── FR002 Absinthe ────────────────────────────────────────────────────
def _absinthe_play(state, player, inst, ctx):
    rnd = state["round"]
    targets = list(range(rnd + 1, min(TOTAL_ROUNDS, rnd + 5) + 1))
    inst["data"]["rounds"] = targets
    if targets:
        ctx["log"].append(f"{player['name']}'s Absinthe marks rounds "
                          + ", ".join(map(str, targets))
                          + " for a food placement")

def _absinthe_available(state, player, inst):
    return state["round"] in (inst["data"].get("rounds") or [])

def _absinthe_apply(state, player, inst, ctx):
    rounds = inst["data"].get("rounds") or []
    if state["round"] not in rounds:
        return
    sid = (ctx.get("params") or {}).get("space_id")
    space = next((s for s in state["action_spaces"] if s["id"] == sid), None)
    if space is None or not space["accumulates"]:
        raise ValueError("Absinthe: choose a revealed accumulation space "
                         "(params.space_id)")
    rounds.remove(state["round"])
    space["supply"]["food"] = space["supply"].get("food", 0) + 1
    ctx["log"].append(f"{player['name']}'s Absinthe places 1 food on "
                      f"{space['name']}")

compendium_card(
    "FR002", points=1, prereq=needs_occupations(3),
    hooks={"play": _absinthe_play},
    card_action={"available": _absinthe_available, "apply": _absinthe_apply,
                "description": "Absinthe: place this round's food on a "
                               "revealed accumulation space (params.space_id)"})


# ── FR003 Amusement Park ──────────────────────────────────────────────
_AMUSEMENT_PARK_TIERS = {1: 2, 2: 3, 3: 4}

def _amusement_park_play(state, player, inst, ctx):
    n = len(compute_pastures(player))
    rounds_ahead = _AMUSEMENT_PARK_TIERS.get(n, 5 if n >= 4 else 0)
    if rounds_ahead <= 0:
        return
    rnd = state["round"]
    targets = list(range(rnd + 1, min(TOTAL_ROUNDS, rnd + rounds_ahead) + 1))
    _schedule_good(player, state, "food", targets)
    if targets:
        ctx["log"].append(f"{player['name']}'s Amusement Park schedules "
                          "food on rounds " + ", ".join(map(str, targets)))

compendium_card(
    "FR003", points=1,
    prereq=(lambda s, p: len(compute_pastures(p)) >= 1, "1 pasture"),
    hooks={"play": _amusement_park_play})


# ── FR004 Apple Garden ────────────────────────────────────────────────
def _apple_garden_score(state, player, inst):
    if _total_grain(player) == 0 or _total_vegetable(player) == 0:
        return 2
    return 0

compendium_card("FR004", score_bonus=_apple_garden_score)


# ── FR006 Badger ──────────────────────────────────────────────────────
# "Immediately place a marker on an Action space of your choice. At
# the start of each round, you must move it to an orthogonally
# adjacent revealed Action space. Any player that uses that space also
# receives 1 Food." Rulings: it may not be moved onto an "additional"
# (occupation/improvement) action space -- automatically true here,
# since a card_space has no board position/adjacency at all; taking
# the Badger's food doesn't require using the marked space first or
# last, just at some point (the space_used hook below doesn't care
# about order); and playing this card does not itself pay out, even if
# played via the space the marker ends up on.

def _badger_play(state, player, inst, ctx):
    space_id = (ctx.get("params") or {}).get("space")
    valid = isinstance(space_id, str) and not space_id.startswith("card:") \
        and any(s["id"] == space_id for s in state["action_spaces"])
    if not valid:
        raise ValueError("Badger: choose an existing action space "
                         "(params.space)")
    inst["data"]["marker"] = space_id
    ctx["log"].append(f"{player['name']} places the Badger marker on "
                      f"{space_id}")


def _badger_move(state, player, inst, marker, log):
    options = cards.adjacent_spaces(state, marker)
    if not options:
        log.append(f"{player['name']}'s Badger has no adjacent revealed "
                   "action space to move to and stays put")
        return
    if len(options) == 1:
        inst["data"]["marker"] = options[0]
        log.append(f"{player['name']} moves the Badger to {options[0]}")
        return
    prompt_choice(state, player, inst["id"],
                  "Move the Badger to which adjacent action space?", options)


def _badger_round_start(state, player, inst, ctx):
    marker = inst["data"].get("marker")
    if marker is not None:
        _badger_move(state, player, inst, marker, ctx["log"])


def _badger_choice(state, player, inst, ctx):
    inst["data"]["marker"] = ctx["option"]
    ctx["log"].append(f"{player['name']} moves the Badger to {ctx['option']}")


def _badger_space_used(state, player, inst, ctx):
    if ctx["space_id"] == inst["data"].get("marker"):
        add_goods(ctx["extra"], {"food": 1})
        actor_name = state["players"][ctx["actor"]]["name"]
        ctx["log"].append(f"The Badger grants {actor_name} 1 food")


compendium_card(
    "FR006",
    hooks={"play": _badger_play, "round_start": _badger_round_start,
          "space_used": _badger_space_used},
    resolve_choice=_badger_choice)


# ── FR007 Baguette ────────────────────────────────────────────────────
def _baguette_available(state, player, inst):
    if state["phase"] != "feeding":
        return False
    if inst["data"].get("used_harvest") == state.get("harvest_index"):
        return False
    if player["resources"]["wood"] < 1 or player["resources"]["grain"] < 1:
        return False
    return any(MAJOR_IMPROVEMENTS[i].get("bake") for i in player["improvements"])

def _baguette_apply(state, player, inst, ctx):
    imp = next((i for i in player["improvements"]
               if MAJOR_IMPROVEMENTS[i].get("bake")), None)
    if imp is None or player["resources"]["wood"] < 1:
        return
    limit, value = MAJOR_IMPROVEMENTS[imp]["bake"]
    count = (ctx.get("params") or {}).get("grain", 1)
    if not isinstance(count, int) or count < 1:
        count = 1
    if limit is not None:
        count = min(count, limit)
    count = min(count, player["resources"]["grain"])
    if count < 1:
        return
    player["resources"]["wood"] -= 1
    player["resources"]["grain"] -= count
    food = count * value + bake_bonus(player, count)
    player["resources"]["food"] += food
    inst["data"]["used_harvest"] = state.get("harvest_index")
    ctx["log"].append(f"{player['name']}'s Baguette bakes {count} grain "
                      f"into {food} food for 1 wood")
    fire_player(state, player, "bake", {"grain": count, "log": ctx["log"],
                                       "actor": ctx["actor"], "extra": {}})

compendium_card(
    "FR007",
    card_action={"available": _baguette_available, "apply": _baguette_apply,
                "description": "Baguette: pay 1 wood to bake bread during "
                               "the feeding phase (params.grain)"})


# ── FR008 Barber shop ─────────────────────────────────────────────────
def _barber_shop_play(state, player, inst, ctx):
    remaining = sum(1 for r in HARVEST_ROUNDS if r > state["round"])
    inst["data"]["bonus"] = remaining
    if remaining:
        ctx["log"].append(f"{player['name']}'s Barber shop banks "
                          f"{remaining} bonus point(s)")

compendium_card("FR008", hooks={"play": _barber_shop_play},
                score_bonus=lambda s, p, i: i["data"].get("bonus", 0))


# ── FR010 Breakfast Outdoors ──────────────────────────────────────────
# DB cost "1 vegetable or 2 grains" is a printed alternative (GUIDE.md
# ground rule 1, note the alternatives are NOT symmetric quantities) --
# cost=[{...}, {...}]; the effect doesn't depend on which was paid.
compendium_card("FR010", cost=[{"vegetable": 1}, {"grain": 2}], points=1,
                hooks=harvest_food(lambda s, p: 1))


# ── FR011 Brickyard ───────────────────────────────────────────────────
def _brickyard_round_start(state, player, inst, ctx):
    if player["resources"]["clay"] == 0:
        inst["data"]["stash"] = inst["data"].get("stash", 0) + 1
        ctx["log"].append(f"{player['name']}'s Brickyard stores 1 clay")

def _brickyard_available(state, player, inst):
    return inst["data"].get("stash", 0) >= 4

def _brickyard_apply(state, player, inst, ctx):
    n = inst["data"].get("stash", 0)
    if n < 4:
        return
    player["resources"]["clay"] += n
    inst["data"]["stash"] = 0
    ctx["log"].append(f"{player['name']}'s Brickyard releases {n} clay")

compendium_card(
    "FR011", prereq=needs_occupations(2),
    hooks={"round_start": _brickyard_round_start},
    card_action={"available": _brickyard_available, "apply": _brickyard_apply,
                "description": "Brickyard: move all stashed clay to your "
                               "supply"})


# ── FR012 Camembert ───────────────────────────────────────────────────
def _camembert_play(state, player, inst, ctx):
    ids = (ctx.get("params") or {}).get("spaces")
    if not isinstance(ids, list) or len(set(ids)) != 3:
        raise ValueError("Camembert: choose 3 distinct revealed action "
                         "spaces (params.spaces)")
    spaces = []
    for sid in ids:
        sp = next((s for s in state["action_spaces"] if s["id"] == sid), None)
        if sp is None or not sp["accumulates"]:
            raise ValueError(f"Camembert: {sid} is not a revealed "
                             "accumulation space")
        spaces.append(sp)
    for sp in spaces:
        sp["supply"]["food"] = sp["supply"].get("food", 0) + 1
    ctx["log"].append(f"{player['name']}'s Camembert places 1 food on 3 "
                      "action spaces")

compendium_card("FR012", points=1, prereq=needs_occupations(2),
                hooks={"play": _camembert_play})


# ── FR013 Chameleon ────────────────────────────────────────────────────
# "When you play this card, you receive 1 Wild boar. You may keep 1
# Wild boar in each of your pastures that hold Sheep. (Pastures can
# still only hold the normal amount of animals)." Cost 1 sheep -- an
# animal cost, not a resource cost, so it's paid inside the play hook
# (same shape as this file's own FR028 Hammock). This is the GUIDE.md
# worked example for pasture_secondary_types (a second animal type
# sharing a pasture, still counting against that pasture's own total
# capacity) -- not a capacity increase, despite this card's stale
# UNIMPLEMENTED note (written before pasture_secondary_types existed as
# its own distinct mechanism) describing it as one.
def _chameleon_play(state, player, inst, ctx):
    if not _remove_animal(player, "sheep", 1):
        raise ValueError("Chameleon: you need 1 sheep to play this card")
    add_goods(ctx["extra"], {"boar": 1})
    ctx["log"].append(f"{player['name']}'s Chameleon exchanges 1 sheep for "
                      "1 wild boar")

def _chameleon_secondary(state, player, inst, info):
    return {"boar": 1} if info["animal_type"] == "sheep" else {}

compendium_card(
    "FR013", cost={},
    prereq=(lambda s, p: animal_totals_of(p)["sheep"] >= 1, "1 sheep"),
    hooks={"play": _chameleon_play},
    pasture_secondary_types=_chameleon_secondary)


# ── FR015 Coffee Break ────────────────────────────────────────────────
def _coffee_break_play(state, player, inst, ctx):
    rnd = state["round"]
    targets = list(range(rnd + 1, min(TOTAL_ROUNDS, rnd + 5) + 1))
    _schedule_good(player, state, "food", targets)
    inst["data"]["rounds"] = targets
    if targets:
        ctx["log"].append(f"{player['name']}'s Coffee Break schedules food "
                          "on rounds " + ", ".join(map(str, targets)))

def _coffee_break_occ(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    rounds = inst["data"].get("rounds") or []
    removed = 0
    for r in rounds:
        if r <= state["round"]:
            continue
        slot = state["round_goods"].get(str(r), {}).get(str(player["index"]))
        if slot and slot.get("food", 0) > 0:
            slot["food"] -= 1
            removed += 1
    inst["data"]["rounds"] = []
    if removed:
        ctx["log"].append(f"{player['name']}'s Coffee Break cancels "
                          f"{removed} promised food")

compendium_card(
    "FR015", cost={"clay": 1}, points=1,
    hooks={"play": _coffee_break_play, "occupation_played": _coffee_break_occ})


# ── FR016 Cornrick ────────────────────────────────────────────────────
# DB parse glitch: the parsed cost ("") and prereq ("1 field; )") don't
# match this card's own meta string "(Cost 1F. Req 1 field.)" (a stray
# trailing parenthesis in the source). Using the meta clause instead.
compendium_card(
    "FR016", cost={"food": 1},
    prereq=(lambda s, p: _field_count(p) >= 1, "1 field"),
    hooks=schedule_on_play("grain", fixed_rounds=(7, 9)))


# ── FR017 Diary ───────────────────────────────────────────────────────
def _diary_occ(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    add_goods(ctx["extra"], {"wood": 1})
    ctx["log"].append(f"{player['name']}'s Diary grants 1 wood")

compendium_card("FR017", prereq=needs_occupations(2),
                hooks={"occupation_played": _diary_occ})


# ── FR018 Encircling Wall ────────────────────────────────────────────
# "When you play this card, you may immediately fence 1 space in your
# farmyard. (You do not need to pay Wood for the fences.)" Fence-edge
# layout is open-ended, so it rides the play action's own params (the
# Shifting Cultivation shape); "1 space" is enforced by checking the
# farmyard cell count newly enclosed after the build, not just trusting
# the edge count (an enclosure can take 1-3 edges depending on
# adjacency to existing pastures).
def _encircling_wall_play(state, player, inst, ctx):
    fences = (ctx.get("params") or {}).get("fences")
    if not fences:
        return
    before = {i for pa in compute_pastures(player) for i in pa}
    sub_actions.build_fences(state, player, fences, ctx["log"], cost_override="free")
    after = {i for pa in compute_pastures(player) for i in pa}
    if len(after - before) != 1:
        raise ValueError("Encircling Wall: must enclose exactly 1 new farmyard space")

compendium_card("FR018", hooks={"play": _encircling_wall_play})


# ── FR019 Evening Prayer ──────────────────────────────────────────────
def _evening_prayer_play(state, player, inst, ctx):
    cids = (ctx.get("params") or {}).get("cids") or []
    if not cids:
        return
    n = min(_field_count(player), 2)
    if len(cids) > n:
        raise ValueError(f"Evening Prayer: you may only play {n} "
                         "occupation(s)")
    if len(set(cids)) != len(cids) or \
            any(c not in player["hand_occupations"] for c in cids):
        raise ValueError("Evening Prayer: invalid occupation selection")
    if player["resources"]["food"] < len(cids):
        raise ValueError("Not enough food")
    player["resources"]["food"] -= len(cids)
    for cid in cids:
        extra = _play_occupation_bypassing_cost(state, player, cid, ctx["log"])
        add_goods(ctx["extra"], extra)

compendium_card("FR019", prereq=exact_occupations(0),
                hooks={"play": _evening_prayer_play})


# ── FR020 Five Rings ──────────────────────────────────────────────────
# DB cost "1W or 1S": "pay 1 Wood/Stone less if you chose Wood/Stone as
# the cost of this card" -- unlike the plain alternative-cost cards
# elsewhere in this codebase, here WHICH alternative is paid determines
# which resource the standing discount applies to. The engine's cost
# model does support a printed alternative now (cost=[{...}, {...}],
# GUIDE.md ground rule 1), but play_minor's play-hook ctx never learns
# which alternative/cost_option was actually resolved, and cost_mod
# fires later with no per-instance memory of it either -- there's no
# channel to record the choice made at play time. Registering
# cost=[{wood:1}, {stone:1}] here would let a player pay stone yet
# still only ever receive the wood discount, which is less faithful
# than the status quo. So this stays collapsed to the Wood option (cost
# always Wood, discount always Wood) pending that channel -- same gap
# as B065/deck_b_minors.py and C040/deck_c_minors.py.
def _five_rings_mod(state, player, kind, cost, ctx):
    if kind == "improvement" and cost.get("wood"):
        cost = dict(cost)
        cost["wood"] = max(0, cost["wood"] - 1)
    return cost

compendium_card("FR020", cost={"wood": 1}, cost_mod=_five_rings_mod)


# ── FR021 Flat Hill ───────────────────────────────────────────────────
def _flat_hill_play(state, player, inst, ctx):
    if player["house_type"] != "wood":
        ctx["log"].append(f"{player['name']}'s Flat Hill has no effect "
                          "(not a wooden hut)")
        return
    player["house_type"] = "clay"
    ctx["log"].append(f"{player['name']}'s Flat Hill renovates to a clay "
                      "hut for free")
    fire_player(state, player, "renovate",
               {"free_stable_cell": None, "log": ctx["log"],
                "actor": ctx["actor"], "extra": ctx["extra"]})

compendium_card(
    "FR021", prereq=(lambda s, p: _planted_field_count(p) >= 4,
                     "4 planted fields"),
    hooks={"play": _flat_hill_play})


# ── FR022 Full-bottomed Wig ───────────────────────────────────────────
def _wig_mod(state, player, kind, cost, ctx):
    if kind == "renovation" and cost.get("reed"):
        cost = dict(cost)
        cost["reed"] = 0
    return cost

compendium_card(
    "FR022", points=1,
    prereq=(lambda s, p: animal_totals_of(p)["sheep"] >= 3, "3 sheep"),
    cost_mod=_wig_mod)


# ── FR023 Goblet ──────────────────────────────────────────────────────
def _goblet_improvement(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or ctx["improvement"] != "well":
        return
    rnd = state["round"]
    _schedule_good(player, state, "food",
                  range(rnd + 1, min(TOTAL_ROUNDS, rnd + 5) + 1))
    ctx["log"].append(f"{player['name']}'s Goblet doubles the Well's food "
                      "to 2 per round")

compendium_card("FR023", cost={"wood": 1},
                hooks={"improvement_built": _goblet_improvement})


# ── FR024 Golden Rose ─────────────────────────────────────────────────
# "Whenever you pay food to play an Occupation or a Minor Improvement,
# you may pay up to 2 food less." Ruling: any additional food costs the
# card itself specifies still apply -- that's automatic here since the
# discount folds over whatever base food cost `play_occupation`/
# `play_minor` already computed, including a card's own extra food
# clause baked into its `cost` dict.
def _golden_rose_mod(state, player, kind, cost, ctx):
    if kind not in ("occupation", "minor") or not cost.get("food"):
        return cost
    cost = dict(cost)
    cost["food"] = max(0, cost["food"] - 2)
    return cost

compendium_card(
    "FR024",
    prereq=(lambda s, p: _planted_field_count(p) >= 1, "1 planted field"),
    cost_mod=_golden_rose_mod)


# ── FR026 Grotto ──────────────────────────────────────────────────────
# The final clause ("once empty, provides room for 1 family member") is
# dropped: extra_rooms is a static per-card value read off the spec, not
# something that can be conditioned on this instance's stash reaching
# empty (no per-instance-aware query exists for it).
def _grotto_play(state, player, inst, ctx):
    inst["data"]["clay"] = 3
    inst["data"]["stone"] = 2
    ctx["log"].append(f"{player['name']}'s Grotto stores 3 clay and 2 stone")

def _grotto_available(state, player, inst):
    stash = {g: inst["data"].get(g, 0) for g in ("clay", "stone")}
    return player["resources"]["food"] >= 2 and any(v > 0 for v in stash.values())

def _grotto_apply(state, player, inst, ctx):
    good = (ctx.get("params") or {}).get("good")
    if good not in ("clay", "stone") or inst["data"].get(good, 0) < 1:
        raise ValueError("Grotto: choose clay or stone (params.good)")
    if player["resources"]["food"] < 2:
        return
    player["resources"]["food"] -= 2
    inst["data"][good] -= 1
    player["resources"][good] += 1
    ctx["log"].append(f"{player['name']}'s Grotto trades 2 food for 1 {good}")

compendium_card(
    "FR026", prereq=needs_occupations(2),
    hooks={"play": _grotto_play},
    card_action={"available": _grotto_available, "apply": _grotto_apply,
                "description": "Grotto: pay 2 food for 1 stashed clay/stone "
                               "(params.good)"})


# ── FR027 Ground Pickaxe Plow ────────────────────────────────────────
# "Once during the game, when you use either the 'Plow 1 field' or
# 'Plow 1 field and/or Sow' Action space, you can place 1 Wood from
# your supply on 1/2 orthogonally adjacent (revealed or unrevealed)
# Action spaces (to the used plow space) to Plow 1/2 additional
# fields." In this engine those two named spaces are "farmland" and
# "cultivation" (state.PERMANENT_SPACES / STAGE_CARDS's "Farmland"/
# "Cultivation"). Interpreted as: the FIRST time you use either space,
# you may spend up to min(2, <adjacent action-space count>) wood, one
# at a time, to plow that many additional fields, each field chosen
# individually -- "Once during the game" gates the opportunity itself
# (offered exactly once, whether or not any wood ends up spent), not
# each individual field plowed (the ruling that this "can be combined
# with other plows on the same action" only clarifies it stacks with
# the space's own plow, not that the once-per-game trigger repeats).
# Ruling combined with A091 Shifting Cultivator's precedent for a
# pay-and-pick-a-cell prompt chain.
#
# Both farmland and cultivation always have at least 2 orthogonal
# board neighbors in this engine's geometry (see decks/GUIDE.md's
# board diagrams), so the adjacency count never actually reduces the
# cap below 2 in practice -- it's still computed for fidelity to the
# card's explicit "revealed or unrevealed" clause, which
# cards.adjacent_spaces (existence-filtered, "revealed" only) can't
# express: state.SPACE_POSITIONS/ROUND_SLOTS are read directly instead
# (every round 1-14 has a fixed grid position whether or not it has
# been revealed yet).

_PLOW_SPACES = ("farmland", "cultivation")


def _pickaxe_neighbor_cap(state, space_id):
    rect = cards.space_rect(state, space_id)
    if rect is None:
        return 0
    all_rects = list(SPACE_POSITIONS.get(state["player_count"], {}).values())
    all_rects += list(ROUND_SLOTS.values())
    return min(2, sum(1 for r in all_rects
                      if r != rect and cards.rects_adjacent(rect, r)))


def _pickaxe_offer(state, player, inst):
    remaining = inst["data"]["cap"] - inst["data"]["used_count"]
    if remaining <= 0 or player["resources"]["wood"] < 1:
        return
    cells = plowable_cells(player)
    if not cells:
        return
    options = ["Decline"] + [f"Plow cell {c} (1 wood)" for c in cells]
    prompt_choice(state, player, inst["id"],
                  "Ground Pickaxe Plow: pay 1 wood to plow an additional "
                  "field?", options, data={"cells": cells})


def _pickaxe_space_used(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or inst["data"].get("used"):
        return
    if ctx["space_id"] not in _PLOW_SPACES:
        return
    cap = _pickaxe_neighbor_cap(state, ctx["space_id"])
    inst["data"]["used"] = True
    if cap <= 0:
        return
    inst["data"]["cap"] = cap
    inst["data"]["used_count"] = 0
    _pickaxe_offer(state, player, inst)


def _pickaxe_choice(state, player, inst, ctx):
    if ctx["index"] == 0:
        return
    cell = ctx["data"]["cells"][ctx["index"] - 1]
    if player["resources"]["wood"] < 1 or cell not in plowable_cells(player):
        return
    player["resources"]["wood"] -= 1
    player["cells"][cell]["type"] = "field"
    inst["data"]["used_count"] += 1
    ctx["log"].append(f"{player['name']}'s Ground Pickaxe Plow plows a field")
    _pickaxe_offer(state, player, inst)


compendium_card(
    "FR027",
    hooks={"space_used": _pickaxe_space_used},
    resolve_choice=_pickaxe_choice)


# ── FR028 Hammock ─────────────────────────────────────────────────────
def _hammock_play(state, player, inst, ctx):
    if not _remove_animal(player, "sheep", 1):
        raise ValueError("Hammock: you need 1 sheep to play this card")

compendium_card(
    "FR028", cost={"wood": 2}, points=1,
    prereq=combine(
        (lambda s, p: sum(1 for c in p["cells"] if c["type"] == "room") >= 4,
         "4 rooms"),
        (lambda s, p: animal_totals_of(p)["sheep"] >= 1, "1 sheep")),
    hooks={"play": _hammock_play}, extra_rooms=1)


# ── FR030 Haystack ────────────────────────────────────────────────────
compendium_card(
    "FR030", cost={"wood": 1},
    prereq=(lambda s, p: sum(animal_totals_of(p).values()) >= 6, "6 animals"),
    hooks=schedule_on_play("food", rounds_ahead=TOTAL_ROUNDS, amount=3))


# ── FR031 Heatwave ────────────────────────────────────────────────────
def _heatwave_play(state, player, inst, ctx):
    options, data = [], []
    if not any(i in FIREPLACES for i in player["improvements"]):
        avail = next((i for i in FIREPLACES
                     if i in state["available_improvements"]), None)
        if avail:
            options.append(f"Build {MAJOR_IMPROVEMENTS[avail]['name']}")
            data.append(("build", avail))
    else:
        avail = next((i for i in COOKING_HEARTHS
                     if i in state["available_improvements"]), None)
        if avail:
            options.append(f"Upgrade to {MAJOR_IMPROVEMENTS[avail]['name']}")
            data.append(("upgrade", avail))
    if not options:
        ctx["log"].append(f"{player['name']}'s Heatwave has no eligible "
                          "improvement available")
        return
    options.append("Skip")
    prompt_choice(state, player, inst["id"],
                 "Heatwave: build or upgrade a hearth for free?", options,
                 data={"choices": data})

def _heatwave_resolve(state, player, inst, ctx):
    choices = ctx["data"]["choices"]
    if ctx["index"] >= len(choices):
        return
    kind, imp = choices[ctx["index"]]
    if imp not in state["available_improvements"]:
        return
    if kind == "build":
        state["available_improvements"].remove(imp)
        player["improvements"].append(imp)
        ctx["log"].append(f"{player['name']}'s Heatwave builds "
                          f"{MAJOR_IMPROVEMENTS[imp]['name']} for free")
    else:
        fireplace = next((i for i in player["improvements"]
                         if i in FIREPLACES), None)
        if fireplace is None:
            return
        player["improvements"].remove(fireplace)
        state["available_improvements"].append(fireplace)
        state["available_improvements"].sort()
        state["available_improvements"].remove(imp)
        player["improvements"].append(imp)
        ctx["log"].append(f"{player['name']}'s Heatwave upgrades to "
                          f"{MAJOR_IMPROVEMENTS[imp]['name']} for free")
    fire(state, "improvement_built",
        {"improvement": imp, "log": ctx["log"], "actor": ctx["actor"],
         "extra": ctx["extra"]})

compendium_card(
    "FR031",
    prereq=(lambda s, p: p["house_type"] in ("clay", "stone"),
           "clay hut or stone house"),
    hooks={"play": _heatwave_play}, resolve_choice=_heatwave_resolve)


# ── FR032 Homework ────────────────────────────────────────────────────
def _homework_play(state, player, inst, ctx):
    cids = (ctx.get("params") or {}).get("cids") or []
    if len(cids) > 2:
        raise ValueError("Homework: choose at most 2 occupations")
    if len(set(cids)) != len(cids) or \
            any(c not in player["hand_occupations"] for c in cids):
        raise ValueError("Homework: invalid occupation selection")
    for cid in cids:
        extra = _play_occupation_bypassing_cost(state, player, cid, ctx["log"])
        add_goods(ctx["extra"], extra)

compendium_card("FR032", points=1, hooks={"play": _homework_play})


# ── FR033 Kid's Corner ────────────────────────────────────────────────
# "More than 1 type of animal in the home" already has nothing stopping
# it in this engine (house pets are an unrestricted {type: count} dict),
# so only the +1 capacity needs registering.
compendium_card("FR033", points=1, house_capacity=1)


# ── FR035 Lighthouse ──────────────────────────────────────────────────
compendium_card(
    "FR035", points=2,
    prereq=(lambda s, p: p["house_type"] == "stone"
           and sum(1 for c in p["cells"] if c["type"] == "room") == 2,
           "exactly 2 stone rooms"),
    extra_rooms=1)


# ── FR036 March ───────────────────────────────────────────────────────
def _march_play(state, player, inst, ctx):
    params = ctx.get("params") or {}
    animals = params.get("animals")
    cells = params.get("cells")
    if not animals and not cells:
        return
    if not (isinstance(animals, list) and len(animals) == 2
           and len(set(animals)) == 2
           and all(a in ANIMAL_TYPES for a in animals)):
        raise ValueError("March: choose 2 different animal types "
                         "(params.animals)")
    if not (isinstance(cells, list) and len(cells) == 2):
        raise ValueError("March: choose 2 fields to plow (params.cells)")
    totals = animal_totals_of(player)
    if any(totals.get(a, 0) < 1 for a in animals):
        raise ValueError("March: you don't have one of those animals")
    first_cell, second_cell = cells
    if first_cell not in plowable_cells(player):
        raise ValueError("March: invalid first field")
    for a in animals:
        _remove_animal(player, a, 1)
    player["cells"][first_cell]["type"] = "field"
    if second_cell not in plowable_cells(player):
        raise ValueError("March: invalid second field")
    player["cells"][second_cell]["type"] = "field"
    ctx["log"].append(f"{player['name']}'s March returns 2 animals to plow "
                      "2 fields")

compendium_card("FR036", hooks={"play": _march_play})


# ── FR037 Necklace ────────────────────────────────────────────────────
# "Whenever at the end of a Work phase, you have at least 2 Family
# members occupying 2 orthogonally adjacent Action spaces, you receive
# 1 Food." (Ruling: "Action spaces do not need to be the same
# dimensions" -- no special handling needed, adjacency here is grid-
# based regardless of a space's real printed size.) returning_home
# fires once per player at the end of the work phase, before
# occupied_by/extra_occupants resets, with ctx["spaces"] already the
# exact list this card's text asks for.

def _necklace_returning_home(state, player, inst, ctx):
    spaces = ctx["spaces"]
    for i in range(len(spaces)):
        for j in range(i + 1, len(spaces)):
            if cards.spaces_adjacent(state, spaces[i], spaces[j]):
                add_goods(ctx["extra"], {"food": 1})
                ctx["log"].append(f"{player['name']}'s Necklace adds 1 food")
                return

compendium_card("FR037", prereq=needs_occupations(1),
                hooks={"returning_home": _necklace_returning_home})


# ── FR038 Orchard ─────────────────────────────────────────────────────
def _orchard_play(state, player, inst, ctx):
    n = _planted_field_count(player)
    if n <= 0:
        return
    rnd = state["round"]
    targets = list(range(rnd + 1, min(TOTAL_ROUNDS, rnd + n) + 1))
    _schedule_good(player, state, "food", targets)
    if targets:
        ctx["log"].append(f"{player['name']}'s Orchard schedules food on "
                          "rounds " + ", ".join(map(str, targets)))

compendium_card(
    "FR038", points=1,
    prereq=(lambda s, p: _planted_field_count(p) >= 1, "1 planted field"),
    hooks={"play": _orchard_play})


# ── FR039 Par Force Hunting ───────────────────────────────────────────
def _par_force_play(state, player, inst, ctx):
    rnd = state["round"]
    targets = [r for r in (rnd + 4, rnd + 7) if r <= TOTAL_ROUNDS]
    inst["data"]["rounds"] = targets
    if targets:
        ctx["log"].append("Par Force Hunting marks wild boar on rounds "
                          + ", ".join(map(str, targets)))

def _par_force_available(state, player, inst):
    return state["round"] in (inst["data"].get("rounds") or []) \
        and player["resources"]["food"] >= 1

def _par_force_apply(state, player, inst, ctx):
    rounds = inst["data"].get("rounds") or []
    if state["round"] not in rounds or player["resources"]["food"] < 1:
        return
    player["resources"]["food"] -= 1
    rounds.remove(state["round"])
    add_goods(ctx["extra"], {"boar": 1})
    ctx["log"].append(f"{player['name']}'s Par Force Hunting takes 1 wild "
                      "boar for 1 food")

compendium_card(
    "FR039", cost={"wood": 2}, points=1,
    hooks={"play": _par_force_play},
    card_action={"available": _par_force_available, "apply": _par_force_apply,
                "description": "Par Force Hunting: pay 1 food for 1 wild "
                               "boar (only during the marked rounds)"})


# ── FR040 Park Cemetery ───────────────────────────────────────────────
def _park_cemetery_play(state, player, inst, ctx):
    space = next((s for s in state["action_spaces"] if s["id"] == "farmland"),
                None)
    if space is not None:
        space["supply"]["stone"] = space["supply"].get("stone", 0) + 3
        ctx["log"].append(f"{player['name']}'s Park Cemetery places 3 stone "
                          "on Farmland")

def _park_cemetery_space(state, player, inst, ctx):
    if ctx["space_id"] != "farmland":
        return
    space = next((s for s in state["action_spaces"] if s["id"] == "farmland"),
                None)
    if space is not None and space["supply"].get("stone", 0) > 0:
        space["supply"]["stone"] -= 1
        add_goods(ctx["extra"], {"stone": 1})
        ctx["log"].append(f"{player['name']} takes 1 stone (Park Cemetery)")

def _park_cemetery_available(state, player, inst):
    space = next((s for s in state["action_spaces"] if s["id"] == "farmland"),
                None)
    stone_left = space["supply"].get("stone", 0) if space else 0
    return stone_left <= 0 and bool(plowable_cells(player))

def _park_cemetery_apply(state, player, inst, ctx):
    cell = (ctx.get("params") or {}).get("cell")
    if not isinstance(cell, int) or cell not in plowable_cells(player):
        raise ValueError("Park Cemetery: choose a field to plow (params.cell)")
    if inst in player["minors"]:
        player["minors"].remove(inst)
    player["cells"][cell]["type"] = "field"
    ctx["log"].append(f"{player['name']} discards Park Cemetery to plow a "
                      "field")

compendium_card(
    "FR040",
    hooks={"play": _park_cemetery_play, "space_used": _park_cemetery_space},
    card_action={"available": _park_cemetery_available,
                "apply": _park_cemetery_apply,
                "description": "Park Cemetery: once its stone is gone, "
                               "discard it to plow 1 field (params.cell)"})


# ── FR041 Peasants Boutique ───────────────────────────────────────────
_PEASANTS_BOUTIQUE_TIERS = ((4, 3), (3, 2), (1, 1))

def _peasants_boutique_score(state, player, inst):
    # scoring.py already awards Basketmaker's own reed-tier bonus
    # whenever "basketmaker" is in player["improvements"]; subtract it so
    # the two don't stack, matching "you do not receive additional bonus
    # points from Basket Maker's Workshop."
    reed = player["resources"]["reed"]
    card_val = next((pts for minimum, pts in _PEASANTS_BOUTIQUE_TIERS
                     if reed >= minimum), 0)
    bm_val = 0
    if "basketmaker" in player["improvements"]:
        _, bm_tiers = MAJOR_IMPROVEMENTS["basketmaker"]["scoring_bonus"]
        bm_val = next((pts for minimum, pts in bm_tiers if reed >= minimum), 0)
    return card_val - bm_val

compendium_card(
    "FR041", points=3,
    prereq=(lambda s, p: "basketmaker" in p["improvements"],
           "Basketmaker's Workshop"),
    conversions=[{"give": {"reed": 1}, "get": {"food": 4}, "per_harvest": 1}],
    score_bonus=_peasants_boutique_score)


# ── FR042 Rock Pyramid ────────────────────────────────────────────────
# Simplified to a flat "plow up to 1 field" on every stone room build:
# distinguishing "paid at least 1 stone" from the room-cost fold after
# the fact isn't available (cost_mod folds are not individually
# inspectable), so the doubled ("2 fields") tier is dropped.
def _rock_pyramid_rooms(state, player, inst, ctx):
    if player["house_type"] != "stone":
        return
    cells = plowable_cells(player)
    if not cells:
        return
    options = [f"Plow cell {c}" for c in cells] + ["Skip"]
    prompt_choice(state, player, inst["id"], "Rock Pyramid: plow a field?",
                 options, data={"cells": cells})

def _rock_pyramid_resolve(state, player, inst, ctx):
    cells = ctx["data"]["cells"]
    if ctx["index"] >= len(cells):
        return
    cell = cells[ctx["index"]]
    if cell in plowable_cells(player):
        player["cells"][cell]["type"] = "field"
        ctx["log"].append(f"{player['name']}'s Rock Pyramid plows a field")

compendium_card("FR042", hooks={"rooms_built": _rock_pyramid_rooms},
                resolve_choice=_rock_pyramid_resolve)


# ── FR043 Sofa ────────────────────────────────────────────────────────
def _sofa_score(state, player, inst):
    rooms = sum(1 for c in player["cells"] if c["type"] == "room")
    if rooms <= 2:
        return 4
    if rooms == 3:
        return 2
    return 0

compendium_card("FR043", cost={"reed": 1}, score_bonus=_sofa_score)


# ── FR044 Star Classification Meal ───────────────────────────────────
compendium_card(
    "FR044", points=1,
    prereq=(lambda s, p: p["house_type"] == "stone", "stone house"),
    hooks=schedule_on_play("food", rounds_ahead=TOTAL_ROUNDS))


# ── FR045 Stone House Reconstruction ─────────────────────────────────
def _stone_house_recon_cost(player):
    rooms = sum(1 for c in player["cells"] if c["type"] == "room")
    return {"stone": rooms, "reed": 1}

def _stone_house_recon_available(state, player, inst):
    if player["house_type"] != "clay":
        return False
    cost = modified_cost(state, player, "renovation",
                         _stone_house_recon_cost(player))
    return all(player["resources"].get(r, 0) >= a for r, a in cost.items())

def _stone_house_recon_apply(state, player, inst, ctx):
    if player["house_type"] != "clay":
        return
    cost = modified_cost(state, player, "renovation",
                         _stone_house_recon_cost(player))
    if not all(player["resources"].get(r, 0) >= a for r, a in cost.items()):
        return
    for r, a in cost.items():
        player["resources"][r] -= a
    player["house_type"] = "stone"
    ctx["log"].append(f"{player['name']}'s Stone House Reconstruction "
                      "renovates to stone")
    fire_player(state, player, "renovate",
               {"free_stable_cell": None, "log": ctx["log"],
                "actor": ctx["actor"], "extra": ctx["extra"]})

compendium_card(
    "FR045", points=1,
    card_action={"available": _stone_house_recon_available,
                "apply": _stone_house_recon_apply,
                "description": "Stone House Reconstruction: renovate to "
                               "stone at any time (still pays the normal "
                               "cost)"})


# ── FR046 Straw-Thatched Hut ──────────────────────────────────────────
def _straw_thatched_hut_mod(state, player, kind, cost, ctx):
    if kind == "room" and player["house_type"] == "clay":
        n = ctx.get("count", 1)
        return {"clay": 2 * n, "grain": 1 * n, "food": 1 * n}
    return cost

compendium_card(
    "FR046",
    prereq=(lambda s, p: _planted_field_count(p) >= 2, "2 planted fields"),
    cost_mod=_straw_thatched_hut_mod)


# ── FR048 Swimming Studio ─────────────────────────────────────────────
def _swimming_studio_space(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or ctx["space_id"] != "fishing":
        return
    if player["resources"]["wood"] < 1:
        return
    prompt_choice(state, player, inst["id"],
                 "Swimming Studio: convert 1 wood into 3 food?",
                 ["Convert 1 wood for 3 food", "Skip"])

def _swimming_studio_resolve(state, player, inst, ctx):
    if ctx["index"] == 0 and player["resources"]["wood"] >= 1:
        player["resources"]["wood"] -= 1
        player["resources"]["food"] += 3
        ctx["log"].append(f"{player['name']}'s Swimming Studio converts 1 "
                          "wood for 3 food")

compendium_card(
    "FR048", points=1,
    hooks={"space_used": _swimming_studio_space},
    resolve_choice=_swimming_studio_resolve)


# ── FR049 The Port Le Havre ───────────────────────────────────────────
def _port_le_havre_bake(state, player, inst, ctx):
    options = []
    if player["resources"]["clay"] >= 1:
        options.append("Convert 1 clay to 1 stone")
    if player["resources"]["clay"] >= 2:
        options.append("Convert 2 clay to 2 stone")
    if not options:
        return
    options.append("Skip")
    prompt_choice(state, player, inst["id"],
                 "The Port Le Havre: convert clay to stone?", options)

def _port_le_havre_resolve(state, player, inst, ctx):
    if ctx["option"].startswith("Convert 1"):
        n = 1
    elif ctx["option"].startswith("Convert 2"):
        n = 2
    else:
        return
    if player["resources"]["clay"] < n:
        return
    player["resources"]["clay"] -= n
    player["resources"]["stone"] += n
    ctx["log"].append(f"{player['name']}'s The Port Le Havre converts {n} "
                      f"clay to {n} stone")

compendium_card(
    "FR049", points=1,
    prereq=(lambda s, p: _has_oven(p), "1 oven"),
    hooks={"bake": _port_le_havre_bake},
    resolve_choice=_port_le_havre_resolve)


# ── FR050 Threshing Machine Plow ─────────────────────────────────────
# The Farmland space always requires a normal plow cell in the action
# itself, so this grants the 3 middle spaces IN ADDITION TO (not
# "instead of") the player's own chosen field — a documented deviation
# from "instead of", since the engine has no way to let the plow action
# itself be skipped. Eligibility only checks that the 3 middle cells are
# themselves free (empty, no stable, no pasture) per the ruling "all
# three spaces are able to be plowed" — not plowable_cells()'s usual
# "adjacent to an existing field" rule, which models normal farm growth
# rather than a hard farmyard constraint, and would otherwise make this
# card's own designated cells nearly always ineligible.
def _threshing_machine_space(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or ctx["space_id"] != "farmland" \
            or inst["data"].get("used"):
        return
    pasture_cells = {i for pa in compute_pastures(player) for i in pa}
    eligible = all(
        player["cells"][c]["type"] == "empty" and not player["cells"][c]["stable"]
        and c not in pasture_cells
        for c in _MIDDLE_CELLS)
    if not eligible:
        return
    inst["data"]["used"] = True
    for c in _MIDDLE_CELLS:
        player["cells"][c]["type"] = "field"
    ctx["log"].append(f"{player['name']}'s Threshing Machine Plow plows "
                      "all 3 middle spaces")

compendium_card("FR050", hooks={"space_used": _threshing_machine_space})


# ── FR052 Trees for the Citizens ──────────────────────────────────────
def _trees_for_citizens_play(state, player, inst, ctx):
    if "joinery" in player["improvements"]:
        player["resources"]["wood"] += 3
        ctx["log"].append(f"{player['name']}'s Joinery refunds the wood "
                          "cost of Trees for the Citizens")

def _trees_for_citizens_score(state, player, inst):
    n = sum(1 for i in player["improvements"]
           if MAJOR_IMPROVEMENTS[i]["cost"].get("wood", 0) > 0)
    n += sum(1 for i in player["minors"]
            if CARDS[i["id"]]["cost"].get("wood", 0) > 0)
    return n // 2

compendium_card(
    "FR052", points=1, prereq=needs_occupations(3),
    hooks={"play": _trees_for_citizens_play},
    score_bonus=_trees_for_citizens_score)


# ── FR053 Trip to the Lake ────────────────────────────────────────────
compendium_card("FR053", prereq=needs_occupations(2),
                hooks=space_bonus(["fishing"], {"food": 1, "wood": 1}))


# ── FR054 Tuileries Garden ────────────────────────────────────────────
def _tuileries_plow(state, player, inst, ctx):
    inst["data"]["last_cell"] = ctx["cell"]

def _tuileries_space(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or ctx["space_id"] != "farmland":
        return
    cell = inst["data"].get("last_cell")
    if cell is None:
        return
    c = player["cells"][cell]
    if c["type"] != "field" or c["crops"]:
        return
    prompt_choice(state, player, inst["id"],
                 "Tuileries Garden: sow the field with grain or vegetable?",
                 ["Sow grain", "Sow vegetable"], data={"cell": cell})

def _tuileries_resolve(state, player, inst, ctx):
    cell = ctx["data"]["cell"]
    c = player["cells"][cell]
    if c["type"] != "field" or c["crops"]:
        return
    crop = "grain" if ctx["index"] == 0 else "vegetable"
    c["crops"] = {"type": crop, "count": 3 if crop == "grain" else 2}
    ctx["log"].append(f"{player['name']}'s Tuileries Garden sows 1 {crop} "
                      "from the supply")

compendium_card(
    "FR054", points=1, prereq=needs_occupations(4),
    hooks={"plow": _tuileries_plow, "space_used": _tuileries_space},
    resolve_choice=_tuileries_resolve)


# ── FR055 Vegetable Harvest ───────────────────────────────────────────
# "If you have an Oven" is an ongoing condition; since ovens can't be
# un-built in this engine, requiring one already owned at play time
# (like Peasants Boutique's Basketmaker prereq) is an equivalent proxy.
compendium_card("FR055", prereq=(lambda s, p: _has_oven(p), "1 oven"),
                raw_values={"vegetable": 4})


# ── FR056 Watering Can ────────────────────────────────────────────────
def _watering_can_play(state, player, inst, ctx):
    n = 0
    for c in player["cells"]:
        if c["crops"]:
            c["crops"]["count"] += 1
            n += 1
    for i in card_fields(player):
        if i["crops"]:
            i["crops"]["count"] += 1
            n += 1
    if n:
        ctx["log"].append(f"{player['name']}'s Watering Can adds crops to "
                          f"{n} planted field(s)")

compendium_card("FR056", hooks={"play": _watering_can_play})


# ── FR057 Wild Game ───────────────────────────────────────────────────
def _wild_game_play(state, player, inst, ctx):
    if not _remove_animal(player, "boar", 1):
        raise ValueError("Wild Game: you need 1 wild boar to play this card")
    add_goods(ctx["extra"], {"food": 5})
    ctx["log"].append(f"{player['name']}'s Wild Game grants 5 food")

compendium_card(
    "FR057", cost={},
    prereq=(lambda s, p: animal_totals_of(p)["boar"] >= 1, "1 wild boar"),
    hooks={"play": _wild_game_play})


# ── FR060 Wood Saw ────────────────────────────────────────────────────
def _wood_saw_available(state, player, inst):
    others = [p for p in state["players"] if p["index"] != player["index"]]
    if not others or not all(p["people_total"] > player["people_total"]
                             for p in others):
        return False
    if not _room_eligible_cells(player):
        return False
    cost = modified_cost(state, player, "room",
                         {player["house_type"]: 5, "reed": 2})
    return all(player["resources"].get(r, 0) >= a for r, a in cost.items())

def _wood_saw_apply(state, player, inst, ctx):
    cells = (ctx.get("params") or {}).get("cells")
    if not isinstance(cells, list) or not cells:
        raise ValueError("Wood Saw: choose room cells (params.cells)")
    built = []
    for cell in cells:
        if cell not in _room_eligible_cells(player):
            raise ValueError("Wood Saw: invalid room cell")
        cost = modified_cost(state, player, "room",
                             {player["house_type"]: 5, "reed": 2})
        if not all(player["resources"].get(r, 0) >= a for r, a in cost.items()):
            raise ValueError("Wood Saw: not enough resources")
        for r, a in cost.items():
            player["resources"][r] -= a
        player["cells"][cell]["type"] = "room"
        built.append(cell)
    ctx["log"].append(f"{player['name']}'s Wood Saw builds {len(built)} "
                      "room(s)")
    fire_player(state, player, "rooms_built",
               {"cells": built, "log": ctx["log"], "actor": ctx["actor"],
                "extra": ctx["extra"]})

compendium_card(
    "FR060",
    card_action={"available": _wood_saw_available, "apply": _wood_saw_apply,
                "description": "Wood Saw: if every other player has more "
                               "people than you, build room(s) "
                               "(params.cells)"})
