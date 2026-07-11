"""Deck B minor improvements (codes B.. from the compendium DB).

Several of these compendium entries have garbled/merged text (a PDF
column-parsing artifact in tools/parse_compendium.py: the following
card's meta+body sometimes got appended to the previous card's body).
Where that's evident (an unrelated "(Cost ...)" clause introducing a
disconnected effect mid-paragraph, or ruling text that verbatim matches
an already-registered card elsewhere in cards.py), only the sentence(s)
that match this card's own parsed cost/vp/prereq are implemented; the
trailing noise is ignored. Noted per-card below.
"""

import random

from server.agricola.cards import (
    compendium_card, CARDS, new_instance, spec, in_play, add_goods,
    goods_str, prompt_choice, parse_cost, take_bonus, space_bonus,
    round_income, schedule_on_play, harvest_food, on_play_gain,
    animal_totals_of, needs_occupations, exact_occupations,
    needs_grain_field, combine, card_fields, modified_cost, fire,
)
from server.agricola import sub_actions
from server.agricola.state import (
    ANIMAL_TYPES, TOTAL_ROUNDS, HARVEST_ROUNDS, BUILDING_RESOURCES,
    MAJOR_IMPROVEMENTS, compute_pastures, plowable_cells,
    orthogonal_neighbors,
)

UNIMPLEMENTED = {
    "B001": "requires bundling a free renovation with a follow-on free "
            "fence build (player-chosen layout) as one atomic effect; no "
            "hook combines renovation + fencing sub-actions",
    "B007": "requires major-improvement board-row ('bottom row') "
            "metadata, which the engine's MAJOR_IMPROVEMENTS table "
            "doesn't track",
    "B012": "requires card-based animal storage capacity outside "
            "house/pastures; accommodation only consults house_capacity "
            "and pasture_capacity_bonus",
    "B015": "requires restricting a pasture build to only the specific "
            "wood taken this turn and to exactly one pasture; no such "
            "gating hook exists",
    "B022": "requires placing an extra temporary family member mid-game "
            "and removing them later — the guest-token/extra-people "
            "mechanic the guide marks unsupported",
    "B023": "requires revealing next round's action-space card early as "
            "a private space only the owner may use; action-space "
            "deck/reveal manipulation is unsupported",
    "B026": "requires substituting Build Fences for one of Grain "
            "Utilization's own sub-actions; there is no pre-action hook "
            "to alter a space's legal sub-actions",
    "B029": "requires detecting 'use a Cooking improvement' as a "
            "discrete same-turn event; cook conversions are a static "
            "rate query (raw_values/cook), never a fired event",
    "B030": "requires an alternate fence-piece representation (wood "
            "palisades) with its own per-piece scoring. Now supported "
            "(engine phase 13): player['fence_tokens'] (edge -> granting "
            "card id) marks a subset of player['fences'] as wood-token "
            "edges -- fences stays the single geometric truth (no "
            "changes to validate_fence_layout's/compute_pastures' "
            "pasture logic), tokens are excluded from the 15-fence cap, "
            "priced at the card's own fence_token['cost'] instead of "
            "normal fence pricing, and restricted to border edges (state."
            "is_border_edge) via sub_actions.build_fences's new `tokens=` "
            "param; bonus points are a normal score_bonus counting the "
            "card's own tokens. See decks/GUIDE.md's 'Fence tokens' "
            "section. Not registered by this pass (temp_card-only tests "
            "exercise the mechanism); registering it as a real minor is "
            "a separate pass.",
    "B034": "requires detecting that animals taken from a space were "
            "fully accommodated (not discarded); no post-accommodation "
            "hook exists",
    "B035": "requires continuously monitoring sheep count against a "
            "once-per-game threshold; sheep can change during the "
            "unhooked breeding phase, so the threshold can't reliably "
            "be checked",
    "B038": "requires restricting which of the player's own farmyard "
            "cells are legal build/plow targets; no such legality hook "
            "exists (and this isn't a cost_mod)",
    "B042": "requires creating a new action space usable by every "
            "player; card-created shared action spaces aren't supported",
    "B059": "requires knowing which action space a minor improvement "
            "was played from; the play hook's ctx carries no space_id "
            "and engine.py can't be edited to add it",
    "B065": "the reward tier depends on which resource the player chose "
            "to pay the cost in; the engine's cost model is a single "
            "fixed dict, not a player-chosen-material-with-differing-"
            "effect cost",
    "B072": "requires letting 1-2-space pastures also act as sowable "
            "fields with reduced capacity; needs changes to sow "
            "validation and pasture capacity in the engine",
}

_WOOD_SPACES = ("forest", "grove", "copse")
_CLAY_SPACES = ("clay_pit", "hollow_3p", "hollow_4p")
_STONE_SPACES = ("western_quarry", "eastern_quarry")


def _no_occ_prereq():
    return (lambda s, p: len(p["occupations"]) == 0, "no occupations")


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


def _schedule_good(player, state, good, rounds, amount=1):
    """Write `amount` of `good` onto state["round_goods"] for each round
    in `rounds` (rounds already filtered to > current round)."""
    for r in rounds:
        slot = state["round_goods"].setdefault(str(r), {}) \
            .setdefault(str(player["index"]), {})
        add_goods(slot, {good: amount})


def _play_occupation_for_free(state, player, cid, log):
    """Instantiate an occupation directly (bypassing its printed cost),
    running its own play hook and firing occupation_played so other
    cards react normally."""
    played_spec = CARDS[cid]
    player["hand_occupations"].remove(cid)
    inst = new_instance(cid)
    player["occupations"].append(inst)
    player["occs_played"] += 1
    log.append(f"{player['name']} plays the occupation "
               f"\"{played_spec['name']}\" for free")
    play_fn = played_spec["hooks"].get("play")
    extra = {}
    if play_fn:
        sub_ctx = {"params": {}, "log": log, "actor": player["index"],
                   "extra": extra}
        play_fn(state, player, inst, sub_ctx)
    fire_ctx = {"card_id": cid, "log": log, "actor": player["index"],
                "extra": extra}
    fire(state, "occupation_played", fire_ctx)
    return extra


# ── B003 Moonshine ────────────────────────────────────────────────────
# "Randomly select an occupation in your hand. Either play it for an
# occupation cost of 2 food, or give it to the player to your left."
def _moonshine_play(state, player, inst, ctx):
    cid = random.choice(player["hand_occupations"])
    name = CARDS[cid]["name"]
    prompt_choice(state, player, inst["id"],
                  f"Moonshine selected \"{name}\": play for 2 food, or "
                  "pass it to the player on your left?",
                  [f"Play {name} for 2 food", "Give it to the left neighbor"],
                  data={"cid": cid})

def _moonshine_pass_left(state, player, cid, ctx):
    n = state["player_count"]
    if cid in player["hand_occupations"]:
        player["hand_occupations"].remove(cid)
    if n > 1:
        left = state["players"][(player["index"] + 1) % n]
        left["hand_occupations"].append(cid)
        ctx["log"].append(f"\"{CARDS[cid]['name']}\" passes to "
                          f"{left['name']}'s hand")
    else:
        ctx["log"].append(f"\"{CARDS[cid]['name']}\" is removed from play (solo)")

def _moonshine_resolve(state, player, inst, ctx):
    cid = ctx["data"]["cid"]
    if ctx["index"] == 0 and player["resources"]["food"] >= 2 \
            and cid in player["hand_occupations"]:
        player["resources"]["food"] -= 2
        extra = _play_occupation_for_free(state, player, cid, ctx["log"])
        add_goods(ctx["extra"], extra)
    else:
        _moonshine_pass_left(state, player, cid, ctx)

compendium_card("B003", cost={}, points=0,
                prereq=(lambda s, p: bool(p["hand_occupations"]),
                        "at least 1 occupation in hand"),
                hooks={"play": _moonshine_play},
                resolve_choice=_moonshine_resolve)


# ── B004 Wood Pile ────────────────────────────────────────────────────
def _wood_pile_play(state, player, inst, ctx):
    n = sum(1 for sp in state["action_spaces"]
           if sp["accumulates"] and sp["occupied_by"] == player["index"])
    if n:
        add_goods(ctx["extra"], {"wood": n})
        ctx["log"].append(f"{player['name']}'s Wood Pile grants {n} wood")

compendium_card("B004", cost={}, hooks={"play": _wood_pile_play})


# ── B005 Store of Experience ─────────────────────────────────────────
def _store_of_experience_play(state, player, inst, ctx):
    n = len(player["hand_occupations"])
    good = "wood" if n >= 7 else "clay" if n == 6 else "reed" if n == 5 else "stone"
    add_goods(ctx["extra"], {good: 1})
    ctx["log"].append(f"{player['name']}'s Store of Experience grants 1 {good}")

compendium_card("B005", cost={}, hooks={"play": _store_of_experience_play})


# ── B006 Excursion to the Quarry ─────────────────────────────────────
def _excursion_play(state, player, inst, ctx):
    n = player["people_total"]
    add_goods(ctx["extra"], {"stone": n})
    ctx["log"].append(f"{player['name']}'s Excursion to the Quarry grants {n} stone")

compendium_card("B006", cost={"food": 2}, prereq=needs_occupations(1),
                hooks={"play": _excursion_play})


# ── B009 Beating Rod ──────────────────────────────────────────────────
# (Ignoring the trailing "(Cost 3W 3F.) This card provides room for 1
# person" clause — that's the Caravan's text, bled in by the parser.)
def _beating_rod_play(state, player, inst, ctx):
    prompt_choice(state, player, inst["id"],
                  "Beating Rod: take 1 reed, or exchange 1 reed for 1 cattle?",
                  ["Take 1 reed", "Exchange 1 reed for 1 cattle"])

def _beating_rod_resolve(state, player, inst, ctx):
    if ctx["index"] == 1 and player["resources"]["reed"] >= 1:
        player["resources"]["reed"] -= 1
        add_goods(ctx["extra"], {"cattle": 1})
        ctx["log"].append(f"{player['name']}'s Beating Rod exchanges 1 reed "
                          "for 1 cattle")
    else:
        add_goods(ctx["extra"], {"reed": 1})
        ctx["log"].append(f"{player['name']}'s Beating Rod grants 1 reed")

compendium_card("B009", cost={},
                hooks={"play": _beating_rod_play},
                resolve_choice=_beating_rod_resolve)


# ── B011 Feedyard ─────────────────────────────────────────────────────
# "This card can hold 1 animal for each pasture you have, even different
# types. After the breeding phase of each harvest, you receive 1 food
# for each unused spot on this card." No "types" restriction (any mix).
def _feedyard_holds(state, player, inst):
    return {"total": len(compute_pastures(player))}

def _feedyard_breeding(state, player, inst, ctx):
    cap = len(compute_pastures(player))
    held = sum(inst.get("held", {}).values())
    unused = max(0, cap - held)
    if unused:
        add_goods(ctx["extra"], {"food": unused})
        ctx["log"].append(f"{player['name']}'s Feedyard provides {unused} "
                          "food for its unused spots")

compendium_card("B011", cost={"clay": 1, "grain": 1},
                holds_animals=_feedyard_holds,
                hooks={"breeding": _feedyard_breeding})


# ── B014 Hawktower ────────────────────────────────────────────────────
def _hawktower_round_start(state, player, inst, ctx):
    if state["round"] != 12:
        return
    if player["house_type"] != "stone":
        ctx["log"].append(f"{player['name']}'s Hawktower is discarded "
                          "(not a stone house)")
        return
    cells = _room_eligible_cells(player)
    if not cells:
        ctx["log"].append(f"{player['name']}'s Hawktower has no space to build")
        return
    player["cells"][cells[0]]["type"] = "room"
    ctx["log"].append(f"{player['name']}'s Hawktower builds a free stone room")

compendium_card("B014", cost={"clay": 2},
                prereq=(lambda s, p: s["round"] <= 7, "play in round 7 or before"),
                hooks={"round_start": _hawktower_round_start})


# ── B017 Forest Plow ──────────────────────────────────────────────────
def _forest_plow_space_used(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or ctx["space_id"] not in _WOOD_SPACES:
        return
    if player["resources"]["wood"] < 2:
        return
    cells = plowable_cells(player)
    if not cells:
        return
    options = [f"Pay 2 wood: plow cell {c}" for c in cells] + ["Skip"]
    prompt_choice(state, player, inst["id"],
                  "Forest Plow: pay 2 wood to plow a field?", options,
                  data={"cells": cells, "space_id": ctx["space_id"]})

def _forest_plow_resolve(state, player, inst, ctx):
    cells = ctx["data"]["cells"]
    if ctx["index"] >= len(cells) or player["resources"]["wood"] < 2:
        return
    cell = cells[ctx["index"]]
    if cell not in plowable_cells(player):
        return
    player["resources"]["wood"] -= 2
    space = next((s for s in state["action_spaces"]
                 if s["id"] == ctx["data"]["space_id"]), None)
    if space is not None:
        space["supply"]["wood"] = space["supply"].get("wood", 0) + 2
    player["cells"][cell]["type"] = "field"
    ctx["log"].append(f"{player['name']}'s Forest Plow plows a field")

compendium_card("B017", cost={"wood": 1},
                hooks={"space_used": _forest_plow_space_used},
                resolve_choice=_forest_plow_resolve)


# ── B018 Grassland Harrow ─────────────────────────────────────────────
# (First sentence only; the trailing "(Cost 2W. Req 1 occ.) Place 2
# field tiles..." clause is a different, merged-in card.)
def _grassland_harrow_play(state, player, inst, ctx):
    n = sum(player["resources"][r] for r in BUILDING_RESOURCES)
    target = state["round"] + max(n, 1)
    if target <= TOTAL_ROUNDS:
        inst["data"]["plow_round"] = target
        ctx["log"].append(f"{player['name']}'s Grassland Harrow schedules a "
                          f"field for round {target}")

def _grassland_harrow_round_start(state, player, inst, ctx):
    if inst["data"].get("plow_round") != state["round"]:
        return
    inst["data"]["plow_round"] = None
    cells = plowable_cells(player)
    if not cells:
        return
    options = [f"Plow cell {c}" for c in cells] + ["Skip"]
    prompt_choice(state, player, inst["id"],
                  "Grassland Harrow: plow a field?", options,
                  data={"cells": cells})

def _grassland_harrow_resolve(state, player, inst, ctx):
    cells = ctx["data"]["cells"]
    if ctx["index"] >= len(cells):
        return
    cell = cells[ctx["index"]]
    if cell in plowable_cells(player):
        player["cells"][cell]["type"] = "field"
        ctx["log"].append(f"{player['name']}'s Grassland Harrow plows a field")

compendium_card(
    "B018", cost={"wood": 2},
    prereq=combine(needs_occupations(2),
                   (lambda s, p: sum(p["resources"][r] for r in BUILDING_RESOURCES) >= 1,
                    "1 building resource in your supply")),
    hooks={"play": _grassland_harrow_play,
           "round_start": _grassland_harrow_round_start},
    resolve_choice=_grassland_harrow_resolve)


# ── B020 Chain Float ──────────────────────────────────────────────────
def _chain_float_play(state, player, inst, ctx):
    targets = [state["round"] + off for off in (7, 8, 9) if state["round"] + off <= TOTAL_ROUNDS]
    inst["data"]["plow_rounds"] = targets
    if targets:
        ctx["log"].append("Chain Float schedules fields for rounds "
                          + ", ".join(map(str, targets)))

def _chain_float_round_start(state, player, inst, ctx):
    rounds = inst["data"].get("plow_rounds") or []
    if state["round"] not in rounds:
        return
    rounds.remove(state["round"])
    cells = plowable_cells(player)
    if not cells:
        return
    options = [f"Plow cell {c}" for c in cells] + ["Skip"]
    prompt_choice(state, player, inst["id"],
                  "Chain Float: plow a field?", options, data={"cells": cells})

def _chain_float_resolve(state, player, inst, ctx):
    cells = ctx["data"]["cells"]
    if ctx["index"] >= len(cells):
        return
    cell = cells[ctx["index"]]
    if cell in plowable_cells(player):
        player["cells"][cell]["type"] = "field"
        ctx["log"].append(f"{player['name']}'s Chain Float plows a field")

compendium_card("B020", cost={"wood": 3},
                hooks={"play": _chain_float_play,
                       "round_start": _chain_float_round_start},
                resolve_choice=_chain_float_resolve)


# ── B021 Hayloft Barn ─────────────────────────────────────────────────
# "Place 4 food on this card. Each time you obtain at least 1 grain, you
# also get 1 food from this card. Once it is empty, you get a 'Family
# Growth Even without Room' action." Ruling: "Harvesting 2+ grain at
# once only counts as obtaining once" -- matches gained firing once per
# originating credit event regardless of amount. The bypass action maps
# onto sub_actions.family_growth(require_room=False), redeemed once via
# a card_action once the food pile is empty (the Educator/Scholar
# banked-credit shape, minus the "banking" -- the credit is just "pile
# is empty", checked directly).
def _hayloft_barn_gained(state, player, inst, ctx):
    if not ctx["goods"].get("grain"):
        return
    remaining = inst["data"].get("food", 4)
    if remaining <= 0:
        return
    inst["data"]["food"] = remaining - 1
    add_goods(ctx["extra"], {"food": 1})
    ctx["log"].append(f"{player['name']}'s Hayloft Barn grants 1 food "
                      f"({remaining - 1} left on the card)")

def _hayloft_barn_fg_available(state, player, inst):
    if inst["data"].get("food", 4) > 0 or inst["data"].get("used_family_growth"):
        return False
    return sub_actions.can_family_growth(state, player, require_room=False)

def _hayloft_barn_fg_apply(state, player, inst, ctx):
    sub_actions.family_growth(state, player, ctx["log"], require_room=False)
    inst["data"]["used_family_growth"] = True

compendium_card("B021", cost={"wood": 3}, prereq=needs_occupations(1),
                hooks={"gained": _hayloft_barn_gained},
                card_action={"available": _hayloft_barn_fg_available,
                             "apply": _hayloft_barn_fg_apply,
                             "description": "Family Growth even without "
                                            "room (Hayloft Barn, once empty)"})


# ── B027 Toolbox ──────────────────────────────────────────────────────
_TOOLBOX_MAJORS = ("joinery", "pottery", "basketmaker")

def _toolbox_offer(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    eligible = []
    for imp in _TOOLBOX_MAJORS:
        if imp in player["improvements"] or imp not in state["available_improvements"]:
            continue
        cost = modified_cost(state, player, "improvement", MAJOR_IMPROVEMENTS[imp]["cost"])
        if all(player["resources"][r] >= a for r, a in cost.items()):
            eligible.append(imp)
    if not eligible:
        return
    options = [f"Build {MAJOR_IMPROVEMENTS[i]['name']}" for i in eligible] + ["Skip"]
    prompt_choice(state, player, inst["id"],
                  "Toolbox: build a major improvement?", options,
                  data={"options": eligible})

def _toolbox_resolve(state, player, inst, ctx):
    options = ctx["data"]["options"]
    if ctx["index"] >= len(options):
        return
    imp = options[ctx["index"]]
    if imp not in state["available_improvements"] or imp in player["improvements"]:
        return
    cost = modified_cost(state, player, "improvement", MAJOR_IMPROVEMENTS[imp]["cost"])
    if not all(player["resources"][r] >= a for r, a in cost.items()):
        return
    for r, a in cost.items():
        player["resources"][r] -= a
    state["available_improvements"].remove(imp)
    player["improvements"].append(imp)
    ctx["log"].append(f"{player['name']}'s Toolbox builds "
                      f"{MAJOR_IMPROVEMENTS[imp]['name']}")

compendium_card("B027", cost={"wood": 1},
                hooks={"rooms_built": _toolbox_offer,
                       "stable_built": _toolbox_offer,
                       "fences_built": _toolbox_offer},
                resolve_choice=_toolbox_resolve)


# ── B028 Forestry Studies ─────────────────────────────────────────────
def _forestry_studies_space_used(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or ctx["space_id"] != "forest":
        return
    if player["resources"]["wood"] < 2:
        return
    eligible = [cid for cid in player["hand_occupations"]
               if CARDS[cid]["prereq"] is None or CARDS[cid]["prereq"][0](state, player)]
    if not eligible:
        return
    options = [f"Play {CARDS[cid]['name']} for free" for cid in eligible] + ["Skip"]
    prompt_choice(state, player, inst["id"],
                  "Forestry Studies: return 2 wood to play a free occupation?",
                  options, data={"cids": eligible})

def _forestry_studies_resolve(state, player, inst, ctx):
    cids = ctx["data"]["cids"]
    if ctx["index"] >= len(cids) or player["resources"]["wood"] < 2:
        return
    cid = cids[ctx["index"]]
    if cid not in player["hand_occupations"]:
        return
    player["resources"]["wood"] -= 2
    space = next((s for s in state["action_spaces"] if s["id"] == "forest"), None)
    if space is not None:
        space["supply"]["wood"] = space["supply"].get("wood", 0) + 2
    extra = _play_occupation_for_free(state, player, cid, ctx["log"])
    add_goods(ctx["extra"], extra)

compendium_card("B028", cost={"food": 2},
                hooks={"space_used": _forestry_studies_space_used},
                resolve_choice=_forestry_studies_resolve)


# ── B031 Pottery Yard ─────────────────────────────────────────────────
def _pottery_yard_score(state, player, inst):
    empty = {i for i, c in enumerate(player["cells"])
            if c["type"] == "empty" and not c["stable"] and not c["animal"]}
    for i in empty:
        if any(nb in empty for nb in orthogonal_neighbors(i)):
            return 2
    return 0

compendium_card("B031", cost={}, points=1,
                prereq=(lambda s, p: "pottery" in p["improvements"],
                        "pottery (or an upgrade thereof)"),
                score_bonus=_pottery_yard_score)


# ── B032 Kettle ───────────────────────────────────────────────────────
_KETTLE_TIERS = ((1, 3, 0), (3, 4, 1), (5, 5, 2))

def _kettle_available(state, player, inst):
    return player["resources"]["grain"] >= 1

def _kettle_apply(state, player, inst, ctx):
    tier = (ctx.get("params") or {}).get("tier", 0)
    if not isinstance(tier, int) or not (0 <= tier < len(_KETTLE_TIERS)):
        tier = 0
    grain, food, pts = _KETTLE_TIERS[tier]
    if player["resources"]["grain"] < grain:
        return
    player["resources"]["grain"] -= grain
    add_goods(ctx["extra"], {"food": food})
    inst["data"]["bonus_pts"] = inst["data"].get("bonus_pts", 0) + pts
    ctx["log"].append(f"{player['name']}'s Kettle exchanges {grain} grain "
                      f"for {food} food" + (f" and {pts} bonus points" if pts else ""))

compendium_card("B032", cost={"clay": 1},
                card_action={"available": _kettle_available, "apply": _kettle_apply,
                            "description": "Kettle: exchange grain for food (params.tier: 0/1/2)"},
                score_bonus=lambda s, p, i: i["data"].get("bonus_pts", 0))


# ── B037 Grange ───────────────────────────────────────────────────────
def _grange_field_count(p):
    return sum(1 for c in p["cells"] if c["type"] == "field") + len(card_fields(p))

compendium_card(
    "B037", cost={}, points=3,
    prereq=combine(
        (lambda s, p: _grange_field_count(p) >= 6, "6 field tiles"),
        (lambda s, p: all(animal_totals_of(p)[t] > 0 for t in ANIMAL_TYPES),
         "all animal types")),
    hooks={"play": on_play_gain({"food": 1})})


# ── B040 Brewery Pond ─────────────────────────────────────────────────
compendium_card("B040", cost={}, points=-1, prereq=needs_occupations(2),
                hooks=space_bonus(["fishing", "reed_bank"],
                                  {"grain": 1, "wood": 1}))


# ── B041 Hauberg ──────────────────────────────────────────────────────
def _hauberg_play(state, player, inst, ctx):
    start = (ctx.get("params") or {}).get("start")
    if start not in ("wood", "boar"):
        start = "wood"
    other = "boar" if start == "wood" else "wood"
    rnd = state["round"]
    targets = [r for r in range(rnd + 1, min(TOTAL_ROUNDS, rnd + 4) + 1)]
    for i, r in enumerate(targets):
        good = start if i % 2 == 0 else other
        amount = 2 if good == "wood" else 1
        _schedule_good(player, state, good, [r], amount)
    if targets:
        ctx["log"].append("Hauberg alternates wood/boar on rounds "
                          + ", ".join(map(str, targets)))

compendium_card("B041", cost={"food": 3}, prereq=needs_occupations(3),
                hooks={"play": _hauberg_play})


# ── B043 Chophouse ────────────────────────────────────────────────────
# (Using "3" of the ambiguous "3/2" in the parsed text — see report.)
def _chophouse_space_used(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or \
            ctx["space_id"] not in ("grain_seeds", "vegetable_seeds"):
        return
    rnd = state["round"]
    targets = [r for r in range(rnd + 1, min(TOTAL_ROUNDS, rnd + 3) + 1)]
    _schedule_good(player, state, "food", targets)
    if targets:
        ctx["log"].append(f"{player['name']}'s Chophouse places food on "
                          "future round spaces")

compendium_card("B043", cost={"wood": 2}, points=1,
                hooks={"space_used": _chophouse_space_used})


# ── B044 Chick Stable ─────────────────────────────────────────────────
# (First sentence only; "(2VP. Cost 1W. Req 2 vegetable fields.)..." is
# a different, merged-in card.)
def _chick_stable_play(state, player, inst, ctx):
    rnd = state["round"]
    targets = [rnd + off for off in (3, 4) if rnd + off <= TOTAL_ROUNDS]
    _schedule_good(player, state, "food", targets, amount=2)
    if targets:
        ctx["log"].append("Chick Stable places 2 food on rounds "
                          + ", ".join(map(str, targets)))

compendium_card("B044", cost={"wood": 1}, hooks={"play": _chick_stable_play})


# ── B046 Club House ───────────────────────────────────────────────────
# (First clause only; "(Cost 1C.) Each time you use..." is a merged-in
# card duplicating the already-registered Fish Trap-style effect.)
def _club_house_play(state, player, inst, ctx):
    rnd = state["round"]
    food_rounds = [r for r in range(rnd + 1, min(TOTAL_ROUNDS, rnd + 4) + 1)]
    _schedule_good(player, state, "food", food_rounds)
    stone_round = rnd + 5
    if stone_round <= TOTAL_ROUNDS:
        _schedule_good(player, state, "stone", [stone_round])
    ctx["log"].append("Club House schedules food and stone on future rounds")

compendium_card("B046", cost={"wood": 3}, points=1, hooks={"play": _club_house_play})


# ── B048 Forest Stone ─────────────────────────────────────────────────
def _forest_stone_play(state, player, inst, ctx):
    inst["data"]["food"] = 2

def _forest_stone_space_used(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    if ctx["space_id"] in _WOOD_SPACES and inst["data"].get("food", 0) > 0:
        inst["data"]["food"] -= 1
        player["resources"]["food"] += 1
        ctx["log"].append(f"{player['name']}'s Forest Stone releases 1 food")
    elif ctx["space_id"] in _STONE_SPACES:
        inst["data"]["food"] = inst["data"].get("food", 0) + 2
        ctx["log"].append(f"{player['name']}'s Forest Stone stores 2 food")

compendium_card("B048", cost={"wood": 2}, points=1, prereq=needs_occupations(1),
                hooks={"play": _forest_stone_play,
                       "space_used": _forest_stone_space_used})


# ── B049 Scales ───────────────────────────────────────────────────────
# (First sentence only; the ruling's trailing "In the field phase..."
# text is Butter Churn's, bled in by the parser.)
def _scales_hook(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    total_improvements = len(player["improvements"]) + len(player["minors"])
    if total_improvements == len(player["occupations"]):
        add_goods(ctx["extra"], {"food": 2})
        ctx["log"].append(f"{player['name']}'s Scales grants 2 food")

compendium_card("B049", cost={"wood": 1}, prereq=_no_occ_prereq(),
                hooks={"occupation_played": _scales_hook,
                       "minor_played": _scales_hook})


# ── B051 Digging Spate ────────────────────────────────────────────────
def _digging_spate_space_used(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or ctx["space_id"] not in _CLAY_SPACES:
        return
    n = sum(c["animal"]["count"] for c in player["cells"]
           if c.get("animal") and c["animal"]["type"] == "boar")
    if n:
        add_goods(ctx["extra"], {"food": n})
        ctx["log"].append(f"{player['name']}'s Digging Spate grants {n} food")

compendium_card("B051", cost={"wood": 1},
                prereq=(lambda s, p: s["round"] >= 7, "play in round 7 or later"),
                hooks={"space_used": _digging_spate_space_used})


# ── B052 Growing Farm ─────────────────────────────────────────────────
def _growing_farm_play(state, player, inst, ctx):
    n = state["round"]
    add_goods(ctx["extra"], {"food": n})
    ctx["log"].append(f"{player['name']}'s Growing Farm grants {n} food")

compendium_card(
    "B052", cost={"clay": 2, "reed": 1}, points=2,
    prereq=(lambda s, p: sum(len(pa) for pa in compute_pastures(p)) >= s["round"] - 1,
            "at least as many pasture spaces as completed rounds"),
    hooks={"play": _growing_farm_play})


# ── B053 Sculpture Course ─────────────────────────────────────────────
def _sculpture_course_round_start(state, player, inst, ctx):
    rnd = state["round"]
    if rnd <= 1 or (rnd - 1) in HARVEST_ROUNDS:
        return
    options = []
    if player["resources"]["wood"] >= 1:
        options.append("Exchange 1 wood for 2 food")
    if player["resources"]["stone"] >= 1:
        options.append("Exchange 1 stone for 4 food")
    if not options:
        return
    options.append("Skip")
    prompt_choice(state, player, inst["id"],
                  "Sculpture Course: exchange a resource for food?", options)

def _sculpture_course_resolve(state, player, inst, ctx):
    if ctx["option"].startswith("Exchange 1 wood") and player["resources"]["wood"] >= 1:
        player["resources"]["wood"] -= 1
        player["resources"]["food"] += 2
        ctx["log"].append(f"{player['name']}'s Sculpture Course exchanges 1 wood for 2 food")
    elif ctx["option"].startswith("Exchange 1 stone") and player["resources"]["stone"] >= 1:
        player["resources"]["stone"] -= 1
        player["resources"]["food"] += 4
        ctx["log"].append(f"{player['name']}'s Sculpture Course exchanges 1 stone for 4 food")

compendium_card("B053", cost={"grain": 1},
                hooks={"round_start": _sculpture_course_round_start},
                resolve_choice=_sculpture_course_resolve)


# ── B054 Tumbrel ──────────────────────────────────────────────────────
def _tumbrel_sow(state, player, inst, ctx):
    if not ctx["sown"]:
        return
    stables = sum(1 for c in player["cells"] if c["stable"])
    if stables:
        player["resources"]["food"] += stables
        ctx["log"].append(f"{player['name']}'s Tumbrel grants {stables} food")

compendium_card("B054", cost={"wood": 1},
                hooks={"play": on_play_gain({"food": 2}),
                       "sow": _tumbrel_sow})


# ── B055 Maintenance Premium ──────────────────────────────────────────
# (First clause only; the trailing "(Req 1 of your people on 'Fishing')
# ... (Cost 1W 1C.)..." text is other merged-in cards.)
def _maintenance_premium_play(state, player, inst, ctx):
    inst["data"]["food"] = 3

def _maintenance_premium_space_used(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or ctx["space_id"] not in _WOOD_SPACES:
        return
    if inst["data"].get("food", 0) > 0:
        inst["data"]["food"] -= 1
        player["resources"]["food"] += 1
        ctx["log"].append(f"{player['name']}'s Maintenance Premium releases 1 food")

def _maintenance_premium_renovate(state, player, inst, ctx):
    inst["data"]["food"] = 3
    ctx["log"].append(f"{player['name']}'s Maintenance Premium restocks to 3 food")

compendium_card("B055", cost={}, prereq=needs_occupations(2),
                hooks={"play": _maintenance_premium_play,
                       "space_used": _maintenance_premium_space_used,
                       "renovate": _maintenance_premium_renovate})


# ── B058 Crack Weeder ─────────────────────────────────────────────────
def _crack_weeder_harvest(state, player, inst, ctx):
    n = sum(1 for c in player["cells"]
           if c["crops"] and c["crops"]["type"] == "vegetable" and c["crops"]["count"] > 0)
    n += sum(1 for i in card_fields(player)
            if i["crops"] and i["crops"]["type"] == "vegetable" and i["crops"]["count"] > 0)
    if n:
        player["resources"]["food"] += n
        ctx["log"].append(f"{player['name']}'s Crack Weeder grants {n} food")

compendium_card("B058", cost={"wood": 1},
                hooks={"play": on_play_gain({"food": 1}),
                       "harvest_field": _crack_weeder_harvest})


# ── B060 Brewing Water ────────────────────────────────────────────────
def _brewing_water_space_used(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or ctx["space_id"] != "fishing":
        return
    if player["resources"]["grain"] < 1:
        return
    prompt_choice(state, player, inst["id"],
                  "Brewing Water: pay 1 grain for food on the next 6 round spaces?",
                  ["Pay 1 grain", "Skip"])

def _brewing_water_resolve(state, player, inst, ctx):
    if ctx["index"] != 0 or player["resources"]["grain"] < 1:
        return
    player["resources"]["grain"] -= 1
    rnd = state["round"]
    targets = [r for r in range(rnd + 1, min(TOTAL_ROUNDS, rnd + 6) + 1)]
    _schedule_good(player, state, "food", targets)
    ctx["log"].append(f"{player['name']}'s Brewing Water schedules food on "
                      "future round spaces")

compendium_card("B060", cost={},
                hooks={"space_used": _brewing_water_space_used},
                resolve_choice=_brewing_water_resolve)


# ── B063 Tasting ──────────────────────────────────────────────────────
def _tasting_space_used(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or ctx["space_id"] not in ("lessons", "lessons_b"):
        return
    if player["resources"]["grain"] < 1:
        return
    prompt_choice(state, player, inst["id"],
                  "Tasting: exchange 1 grain for 4 food?",
                  ["Exchange 1 grain for 4 food", "Skip"])

def _tasting_resolve(state, player, inst, ctx):
    if ctx["index"] == 0 and player["resources"]["grain"] >= 1:
        player["resources"]["grain"] -= 1
        player["resources"]["food"] += 4
        ctx["log"].append(f"{player['name']}'s Tasting exchanges 1 grain for 4 food")

compendium_card("B063", cost={"wood": 2},
                hooks={"space_used": _tasting_space_used},
                resolve_choice=_tasting_resolve)


# ── B064 Mill Wheel ───────────────────────────────────────────────────
def _mill_wheel_space_used(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or ctx["space_id"] != "grain_utilization":
        return
    fishing = next((s for s in state["action_spaces"] if s["id"] == "fishing"), None)
    if fishing is not None and fishing["occupied_by"] is not None:
        add_goods(ctx["extra"], {"food": 2})
        ctx["log"].append(f"{player['name']}'s Mill Wheel grants 2 food")

compendium_card("B064", cost={"wood": 2}, hooks={"space_used": _mill_wheel_space_used})


# ── B067 Hand Truck ───────────────────────────────────────────────────
# (First sentence only; the ruling's "(1VP. Req 2 occ. Cost 1F.)..." is
# a different, merged-in field card.)
def _hand_truck_bake(state, player, inst, ctx):
    n = sum(1 for sp in state["action_spaces"]
           if sp["accumulates"] and sp["occupied_by"] == player["index"])
    if n:
        player["resources"]["grain"] += n
        ctx["log"].append(f"{player['name']}'s Hand Truck grants {n} grain")

compendium_card("B067", cost={"wood": 1}, hooks={"bake": _hand_truck_bake})


# ── B069 Potters Market ───────────────────────────────────────────────
def _potters_market_available(state, player, inst):
    return player["resources"]["clay"] >= 3 and player["resources"]["food"] >= 2

def _potters_market_apply(state, player, inst, ctx):
    if player["resources"]["clay"] < 3 or player["resources"]["food"] < 2:
        return
    player["resources"]["clay"] -= 3
    player["resources"]["food"] -= 2
    rnd = state["round"]
    targets = [r for r in range(rnd + 1, min(TOTAL_ROUNDS, rnd + 2) + 1)]
    _schedule_good(player, state, "vegetable", targets)
    ctx["log"].append(f"{player['name']}'s Potters Market schedules vegetables")

compendium_card("B069", cost={"wood": 2}, points=1,
                card_action={"available": _potters_market_available,
                            "apply": _potters_market_apply,
                            "description": "Potters Market: pay 3 clay, 2 food for "
                                           "vegetables on the next 2 round spaces"})


# ── B070 New Purchase ─────────────────────────────────────────────────
def _new_purchase_round_start(state, player, inst, ctx):
    if state["round"] not in HARVEST_ROUNDS:
        return
    if player["resources"]["food"] >= 2:
        prompt_choice(state, player, inst["id"],
                      "New Purchase: buy 1 grain for 2 food?",
                      ["Buy 1 grain", "Skip"], data={"step": "grain"})

def _new_purchase_resolve(state, player, inst, ctx):
    if ctx["data"]["step"] == "grain":
        if ctx["index"] == 0 and player["resources"]["food"] >= 2:
            player["resources"]["food"] -= 2
            player["resources"]["grain"] += 1
            ctx["log"].append(f"{player['name']}'s New Purchase buys 1 grain")
        if player["resources"]["food"] >= 4:
            prompt_choice(state, player, inst["id"],
                          "New Purchase: buy 1 vegetable for 4 food?",
                          ["Buy 1 vegetable", "Skip"], data={"step": "vegetable"})
    elif ctx["index"] == 0 and player["resources"]["food"] >= 4:
        player["resources"]["food"] -= 4
        player["resources"]["vegetable"] += 1
        ctx["log"].append(f"{player['name']}'s New Purchase buys 1 vegetable")

compendium_card("B070", cost={},
                hooks={"round_start": _new_purchase_round_start},
                resolve_choice=_new_purchase_resolve)


# ── B071 Harvest House ────────────────────────────────────────────────
def _harvest_house_play(state, player, inst, ctx):
    if state["harvest_index"] == len(player["occupations"]):
        add_goods(ctx["extra"], {"food": 1, "grain": 1, "vegetable": 1})
        ctx["log"].append(f"{player['name']}'s Harvest House grants "
                          "1 food, 1 grain, and 1 vegetable")

compendium_card("B071", cost={"wood": 1, "clay": 1, "reed": 1}, points=2,
                hooks={"play": _harvest_house_play})


# ── B073 Gift Basket ──────────────────────────────────────────────────
# (First sentence only; the ruling's "(Req 5 occ in your supply.)..." is
# a different, merged-in card.)
_GIFT_BASKET_ROOMS = {2: "vegetable", 3: "food", 4: "grain", 5: "vegetable"}

def _gift_basket_play(state, player, inst, ctx):
    rooms = sum(1 for c in player["cells"] if c["type"] == "room")
    good = _GIFT_BASKET_ROOMS.get(rooms)
    if good:
        add_goods(ctx["extra"], {good: 1})
        ctx["log"].append(f"{player['name']}'s Gift Basket grants 1 {good}")

compendium_card("B073", cost={"reed": 1}, points=1, prereq=needs_occupations(3),
                hooks={"play": _gift_basket_play})


# ── B075 Wood Workshop ────────────────────────────────────────────────
# Interpreted as major improvements only (this engine's terminology
# distinguishes "build a major improvement" from "play a minor"); the
# printed "get 1 wood before paying" is implemented as an equivalent
# 1-wood cost discount, since costs (not minor costs) are the only
# improvement expense this engine can modify via cost_mod.
def _wood_workshop_mod(state, player, kind, cost, ctx):
    if kind == "improvement" and cost.get("wood"):
        cost = dict(cost)
        cost["wood"] = max(0, cost["wood"] - 1)
    return cost

compendium_card("B075", cost={"clay": 1}, prereq=needs_occupations(1),
                cost_mod=_wood_workshop_mod)


# ── B076 Ceilings ─────────────────────────────────────────────────────
# (First sentence only; "(1VP. Req 3 occ. Cost 1F.) Each time you use
# the 'Day Laborer'..." is a different, merged-in card.)
def _ceilings_play(state, player, inst, ctx):
    rnd = state["round"]
    targets = [r for r in range(rnd + 1, min(TOTAL_ROUNDS, rnd + 5) + 1)]
    _schedule_good(player, state, "wood", targets)
    inst["data"]["rounds"] = targets
    ctx["log"].append("Ceilings schedules wood on future round spaces")

def _ceilings_renovate(state, player, inst, ctx):
    rounds = inst["data"].get("rounds") or []
    removed = 0
    for r in rounds:
        if r <= state["round"]:
            continue
        slot = state["round_goods"].get(str(r), {}).get(str(player["index"]))
        if slot and slot.get("wood", 0) > 0:
            slot["wood"] -= 1
            removed += 1
    inst["data"]["rounds"] = []
    if removed:
        ctx["log"].append(f"{player['name']}'s Ceilings removes {removed} "
                          "promised wood on renovation")

compendium_card("B076", cost={"clay": 1}, prereq=needs_occupations(1),
                hooks={"play": _ceilings_play, "renovate": _ceilings_renovate})


# ── B078 Reed Belt ────────────────────────────────────────────────────
compendium_card("B078", cost={"food": 2},
                hooks=schedule_on_play("reed", fixed_rounds=(5, 8, 10, 12)))


# ── B079 Corf ─────────────────────────────────────────────────────────
# (First sentence only; "(Cost 1C.) At any time, you can exchange..." is
# a different, merged-in card.)
def _corf_hook(state, player, inst, ctx):
    if ctx["goods"].get("stone", 0) < 3:
        return
    if ctx["actor"] == player["index"]:
        add_goods(ctx["extra"], {"stone": 1})
    else:
        player["resources"]["stone"] += 1
    ctx["log"].append(f"{player['name']}'s Corf grants 1 stone")

compendium_card("B079", cost={"reed": 1}, hooks={"space_used": _corf_hook})


# ── B081 Handcart ─────────────────────────────────────────────────────
_HANDCART_THRESHOLDS = {"wood": 6, "clay": 5, "reed": 4, "stone": 4}
_HANDCART_SPACES = _WOOD_SPACES + _CLAY_SPACES + ("reed_bank",) + _STONE_SPACES

def _handcart_round_start(state, player, inst, ctx):
    options, spaces = [], []
    for sp in state["action_spaces"]:
        if sp["id"] not in _HANDCART_SPACES:
            continue
        for good, threshold in _HANDCART_THRESHOLDS.items():
            if sp["supply"].get(good, 0) >= threshold:
                options.append(f"Take 1 {good} from {sp['name']}")
                spaces.append((sp["id"], good))
    if not options:
        return
    options.append("Skip")
    prompt_choice(state, player, inst["id"],
                  "Handcart: take a resource before the work phase?", options,
                  data={"spaces": spaces})

def _handcart_resolve(state, player, inst, ctx):
    spaces = ctx["data"]["spaces"]
    if ctx["index"] >= len(spaces):
        return
    sid, good = spaces[ctx["index"]]
    sp = next((s for s in state["action_spaces"] if s["id"] == sid), None)
    if sp is None or sp["supply"].get(good, 0) < 1:
        return
    sp["supply"][good] -= 1
    player["resources"][good] += 1
    ctx["log"].append(f"{player['name']}'s Handcart takes 1 {good}")

compendium_card("B081", cost={"wood": 1},
                hooks={"round_start": _handcart_round_start},
                resolve_choice=_handcart_resolve)


# ── B082 Value Assets ─────────────────────────────────────────────────
_VALUE_ASSETS_OPTIONS = (("wood", 1), ("clay", 1), ("reed", 2), ("stone", 2))

def _value_assets_harvest(state, player, inst, ctx):
    options = [f"Buy 1 {good} for {cost} food" for good, cost in _VALUE_ASSETS_OPTIONS
              if player["resources"]["food"] >= cost]
    if not options:
        return
    options.append("Skip")
    prompt_choice(state, player, inst["id"],
                  "Value Assets: buy a building resource?", options)

def _value_assets_resolve(state, player, inst, ctx):
    if ctx["option"] == "Skip":
        return
    good = ctx["option"].split()[2]
    cost = dict(_VALUE_ASSETS_OPTIONS)[good]
    if player["resources"]["food"] >= cost:
        player["resources"]["food"] -= cost
        player["resources"][good] += 1
        ctx["log"].append(f"{player['name']}'s Value Assets buys 1 {good}")

compendium_card("B082", cost={},
                hooks={"harvest_field": _value_assets_harvest},
                resolve_choice=_value_assets_resolve)


# ── B083 Reed Belt (card storage stack) ──────────────────────────────
# (First sentence only; "(Req 3 occ. Cost 1R.) Place 1 wild boar..." is
# a different, merged-in card.)
_B083_STACK = ("boar", "food", "cattle", "food", "sheep")

def _b083_play(state, player, inst, ctx):
    inst["data"]["stack"] = list(_B083_STACK)

def _b083_available(state, player, inst):
    return bool(inst["data"].get("stack")) and player["resources"]["clay"] >= 1

def _b083_apply(state, player, inst, ctx):
    stack = inst["data"].get("stack") or []
    if not stack or player["resources"]["clay"] < 1:
        return
    player["resources"]["clay"] -= 1
    good = stack.pop()
    add_goods(ctx["extra"], {good: 1})
    ctx["log"].append(f"{player['name']}'s Reed Belt gives up 1 {good} for 1 clay")

compendium_card("B083", cost={"clay": 2},
                hooks={"play": _b083_play},
                card_action={"available": _b083_available, "apply": _b083_apply,
                            "description": "Reed Belt: pay 1 clay to take the top good"})
