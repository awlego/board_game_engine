"""Deck I minor improvements (codes I63-I104, I337 from the compendium DB).

Deck I is an ORIGINAL-edition deck (per-card `edition: "Original"`); card
texts reference the original board's action spaces. Mapping used below:
"Plough 1 Field" -> farmland, "Plough Field and Sow" -> cultivation,
"Take 1 Grain" -> grain_seeds, "3 Wood" -> forest (the only 3-wood
accumulation space this engine models).
"""

from server.agricola.cards import (
    compendium_card, add_goods, goods_str, prompt_choice,
    take_bonus, space_bonus, schedule_on_play, harvest_food, on_play_gain,
    animal_totals_of, needs_occupations, needs_grain_field, card_fields,
    card_space_owner, grant_goods,
)
from server.agricola.state import (
    ANIMAL_TYPES, TOTAL_ROUNDS, HARVEST_ROUNDS, MAJOR_IMPROVEMENTS,
    table_score, orthogonal_neighbors, compute_pastures, plowable_cells,
)

UNIMPLEMENTED = {
    "I68": "grants a 'plough 2 fields instead of 1' option to OTHER "
           "players (who must pay the owner 2 food to use it); the "
           "choice dispatch (_apply_choice) requires the card instance "
           "to be in the resolving player's OWN in_play list, so a "
           "non-owner can never be prompted to invoke someone else's "
           "card -- there is no channel for 'another player may "
           "optionally activate your card for a fee'.",
    "I70": "requires reacting to another player's plough action (via "
           "the Harrow or a plough card), but the 'plow' event only "
           "fires to the acting player's own cards (fire_player, "
           "to_all=False) -- no card can observe another player's plow "
           "(same gap as the B159 precedent).",
    "I71": "forces the owner to skip all people placement in round 14 "
           "-- a turn-structure control the engine has no hook for; "
           "_advance_work/_placement_actions have no per-player "
           "'skip the work phase' key to consult.",
    "I73": "guest tokens / extra people placements are explicitly "
           "unsupported.",
    "I78": "requires sowing wood (not grain/vegetable) onto a "
           "card-held field with its own stacking rule; _do_sow raises "
           "for any crop type other than grain/vegetable, and the "
           "field crop-count formula (3 grain / 2 vegetable) has no "
           "analog for wood stacks.",
    "I82": "the round restriction (5-7, 10-11) identifies a specific "
           "'Take 1 Stone' card by the fixed round it's revealed on the "
           "original board; this engine shuffles stage-card order "
           "within each stage, so there is no stable mapping from "
           "those literal round numbers to western_quarry/"
           "eastern_quarry that would let the condition be tested "
           "faithfully.",
    "I97": "reacts to another player converting an animal to food "
           "during feeding, and moves the owner to the end of the "
           "feeding turn order; _apply_feed applies conversions "
           "directly with no fired event for any card to observe, and "
           "there is no mechanism to reorder the feeding sequence.",
    "I103": "offers every player (not just the owner) an optional "
            "feeding-phase conversion, taxed to the owner; "
            "card_action/conversions are only ever queried against the "
            "acting player's own in_play cards, and harvest_field only "
            "fires to the acting player's own cards (fire_player), so "
            "this card can't even react to other players' harvests.",
}

_OVEN_IDS = ("clay_oven", "stone_oven")
_HEARTH_IDS = ("cooking_hearth_4", "cooking_hearth_5")


def _no_occ_prereq():
    return (lambda s, p: len(p["occupations"]) == 0, "no occupations")


def _needs_veg_field(n):
    def ok(s, p):
        fields = sum(1 for c in p["cells"]
                     if c["crops"] and c["crops"]["type"] == "vegetable")
        fields += sum(1 for i in card_fields(p)
                      if i["crops"] and i["crops"]["type"] == "vegetable")
        return fields >= n
    return (ok, f"{n} vegetable field(s)")


def _needs_planted_fields(n):
    def ok(s, p):
        count = sum(1 for c in p["cells"] if c["crops"])
        count += sum(1 for i in card_fields(p) if i["crops"])
        return count >= n
    return (ok, f"{n} planted field(s)")


def _return_major(state, player, candidates, log, label):
    """Give up one of the player's own qualifying major improvements,
    returning it to the board (mirrors the engine's own Cooking Hearth
    upgrade, which does the same thing for the Fireplace)."""
    owned = [i for i in candidates if i in player["improvements"]]
    if not owned:
        return
    imp = owned[0]
    player["improvements"].remove(imp)
    state["available_improvements"].append(imp)
    state["available_improvements"].sort()
    log.append(f"{player['name']}'s {label} returns their "
               f"{MAJOR_IMPROVEMENTS[imp]['name']} to the improvements board")


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


# ── I63 Moldboard Plough ──────────────────────────────────────────────
def _moldboard_play(state, player, inst, ctx):
    inst["data"]["uses_left"] = 2


def _moldboard_space_used(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or ctx["space_id"] != "farmland":
        return
    if inst["data"].get("uses_left", 0) <= 0:
        return
    cells = plowable_cells(player)
    if not cells:
        return
    options = ["Decline"] + [f"Plough field {c}" for c in cells]
    prompt_choice(state, player, inst["id"],
                 "Moldboard Plough: plough an additional field?", options,
                 data={"cells": cells})


def _moldboard_resolve(state, player, inst, ctx):
    if ctx["index"] == 0:
        return
    cells = ctx["data"]["cells"]
    cell = cells[ctx["index"] - 1]
    if cell not in plowable_cells(player):
        return
    player["cells"][cell]["type"] = "field"
    inst["data"]["uses_left"] = inst["data"].get("uses_left", 0) - 1
    ctx["log"].append(f"{player['name']}'s Moldboard Plough ploughs an "
                      "additional field")


compendium_card("I63", prereq=needs_occupations(1),
                hooks={"play": _moldboard_play,
                       "space_used": _moldboard_space_used},
                resolve_choice=_moldboard_resolve)


# ── I64 Alms ───────────────────────────────────────────────────────────
def _alms_play(state, player, inst, ctx):
    n = state["round"] - 1  # completed rounds only, not the current one
    if n > 0:
        add_goods(ctx["extra"], {"food": n})
        ctx["log"].append(f"{player['name']}'s Alms grants {n} food")


compendium_card("I64", prereq=_no_occ_prereq(), hooks={"play": _alms_play})


# ── I65 Baker's Kitchen ────────────────────────────────────────────────
# On-play "you may also bake bread immediately" is implemented as an
# immediate conversion using this card's own rate only (params.bake_grain,
# 1 or 2); using OTHER ovens simultaneously at play time isn't modeled --
# minors have no generic multi-improvement bake sub-action (only majors
# get one, via _do_improvement's action.get("bake")).
def _bakers_kitchen_play(state, player, inst, ctx):
    _return_major(state, player, _OVEN_IDS, ctx["log"], "Baker's Kitchen")
    grain = (ctx.get("params") or {}).get("bake_grain")
    if isinstance(grain, int) and 1 <= grain <= 2 \
            and player["resources"]["grain"] >= grain:
        player["resources"]["grain"] -= grain
        player["resources"]["food"] += grain * 5
        ctx["log"].append(f"{player['name']}'s Baker's Kitchen immediately "
                          f"bakes {grain} grain for {grain * 5} food")


compendium_card(
    "I65",
    prereq=(lambda s, p: any(i in p["improvements"] for i in _OVEN_IDS),
            "an oven built (Clay Oven or Stone Oven)"),
    hooks={"play": _bakers_kitchen_play},
    bake=(2, 5))


# ── I66 Village Well ──────────────────────────────────────────────────
def _village_well_play(state, player, inst, ctx):
    _return_major(state, player, ("well",), ctx["log"], "Village Well")
    rnd = state["round"]
    targets = list(range(rnd + 1, min(TOTAL_ROUNDS, rnd + 3) + 1))
    for r in targets:
        slot = state["round_goods"].setdefault(str(r), {}) \
            .setdefault(str(player["index"]), {})
        add_goods(slot, {"food": 1})
    if targets:
        ctx["log"].append("Village Well schedules food on rounds "
                          + ", ".join(map(str, targets)))


compendium_card(
    "I66", prereq=(lambda s, p: "well" in p["improvements"], "the Well built"),
    hooks={"play": _village_well_play})


# ── I67 Threshing Board ───────────────────────────────────────────────
compendium_card("I67", prereq=needs_occupations(2),
                bake_on_spaces=("farmland", "cultivation"))


# ── I69 Strawberry Patch ──────────────────────────────────────────────
compendium_card("I69", prereq=_needs_veg_field(2),
                hooks=schedule_on_play("food", rounds_ahead=3))


# ── I72 Goose Pond ────────────────────────────────────────────────────
compendium_card("I72", prereq=needs_occupations(3),
                hooks=schedule_on_play("food", rounds_ahead=4))


# ── I74 Grain Cart ────────────────────────────────────────────────────
compendium_card("I74", prereq=needs_occupations(2),
                hooks=space_bonus(["grain_seeds"], {"grain": 2}))


# ── I75 Hand Mill ─────────────────────────────────────────────────────
compendium_card("I75", raw_values={"grain": 2})


# ── I76 Rake ──────────────────────────────────────────────────────────
# "a plough" is interpreted as any minor granting extra-field ploughs;
# I68 (Harrow) and I70 (Punner) are UNIMPLEMENTED so they never appear
# in play, but the id set is kept generic (future decks may add Yoke).
_PLOW_CARD_IDS = ("I63", "I68", "I70", "K124")


def _rake_score(state, player, inst):
    fields = sum(1 for c in player["cells"] if c["type"] == "field")
    threshold = 6 if any(i["id"] in _PLOW_CARD_IDS for i in player["minors"]) \
        else 5
    return 2 if fields >= threshold else 0


compendium_card("I76", score_bonus=_rake_score)


# ── I77 Shepherd's Crook (duplicate of the base card under a new id) ──
def _shepherds_crook_i77(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    if any(len(p) >= 4 for p in ctx["new_pastures"]):
        add_goods(ctx["extra"], {"sheep": 2})
        ctx["log"].append(f"{player['name']}'s Shepherd's Crook grants 2 sheep")


compendium_card("I77", hooks={"fences_built": _shepherds_crook_i77})


# ── I79 Wood Cart ─────────────────────────────────────────────────────
compendium_card("I79", prereq=needs_occupations(3),
                hooks=take_bonus(["wood"], {"wood": 2}))


# ── I80 Spinney ───────────────────────────────────────────────────────
def _spinney_space_used(state, player, inst, ctx):
    if ctx["actor"] == player["index"] or ctx["space_id"] != "forest":
        return
    if not ctx["goods"].get("wood"):
        return
    actor = state["players"][ctx["actor"]]
    if actor["resources"]["wood"] < 1:
        return
    actor["resources"]["wood"] -= 1
    player["resources"]["wood"] += 1
    ctx["log"].append(f"{player['name']}'s Spinney takes 1 wood from "
                      f"{actor['name']}")


compendium_card("I80", prereq=needs_occupations(3),
                hooks={"space_used": _spinney_space_used})


# ── I81 Wooden Hut Extension ──────────────────────────────────────────
def _wooden_hut_ext_play(state, player, inst, ctx):
    if player["house_type"] != "wood":
        ctx["log"].append(f"{player['name']}'s Wooden Hut Extension has no "
                          "effect (not a wood house)")
        return
    cells = _room_eligible_cells(player)
    if not cells:
        ctx["log"].append(f"{player['name']}'s Wooden Hut Extension has no "
                          "space to build")
        return
    player["cells"][cells[0]]["type"] = "room"
    ctx["log"].append(f"{player['name']}'s Wooden Hut Extension builds a "
                      "free room")


compendium_card("I81", hooks={"play": _wooden_hut_ext_play})


# ── I83 / I89 / I94 Streets (Wooden Path / Clay Path / Paved Road) ────
# Only one player can ever own a given code, so "the most valuable
# street" reduces to: whichever of these three unique cards present in
# the game has the highest tier scores the 2 bonus points.
_STREET_TIERS = {"I83": 1, "I89": 2, "I94": 3}


def _street_max_tier(state):
    best = 0
    for p in state["players"]:
        for i in p["minors"]:
            t = _STREET_TIERS.get(i["id"])
            if t and t > best:
                best = t
    return best


def _street_score(state, player, inst):
    my_tier = _STREET_TIERS.get(inst["id"], 0)
    return 2 if my_tier and my_tier == _street_max_tier(state) else 0


compendium_card("I83", score_bonus=_street_score)


# ── I84 Chicken Coop ──────────────────────────────────────────────────
# Cost "2W or 2C, 1R" (ruling: either 2 wood & 1 reed, or 2 clay & 1
# reed) -- the OR-alternative payment isn't representable by a plain
# cost dict (see deck_a_minors A004 / deck_d_minors D080); the wood
# variant is used.
compendium_card("I84", cost={"wood": 2, "reed": 1},
                hooks=schedule_on_play("food", rounds_ahead=8))


# ── I85 Cooking Corner ────────────────────────────────────────────────
compendium_card(
    "I85",
    prereq=(lambda s, p: any(i in p["improvements"] for i in _HEARTH_IDS),
            "a Cooking Hearth built"),
    hooks={"play": lambda s, p, i, ctx: _return_major(
        s, p, _HEARTH_IDS, ctx["log"], "Cooking Corner")},
    cook={"sheep": 2, "boar": 3, "cattle": 4, "vegetable": 4},
    bake=(None, 3))


# ── I86 Corn Storehouse ───────────────────────────────────────────────
# Cost "2W or 2C, 2R" (ruling: either 2 wood & 2 reed, or 2 clay & 2
# reed); the wood variant is used (see I84 above).
def _corn_storehouse_harvest(state, player, inst, ctx):
    eligible = [i for i, c in enumerate(player["cells"])
               if c["type"] == "field" and not c["crops"]]
    if not eligible:
        return
    inst["data"]["queue"] = eligible
    _corn_storehouse_next(state, player, inst)


def _corn_storehouse_next(state, player, inst):
    queue = inst["data"].get("queue") or []
    if not queue:
        return
    cell = queue[0]
    prompt_choice(state, player, inst["id"],
                 f"Corn Storehouse: sow 2 grain on empty field {cell}?",
                 ["Sow 2 grain", "Skip"], data={"cell": cell})


def _corn_storehouse_resolve(state, player, inst, ctx):
    queue = inst["data"].get("queue") or []
    if queue:
        queue.pop(0)
        inst["data"]["queue"] = queue
    cell = ctx["data"]["cell"]
    if ctx["index"] == 0:
        c = player["cells"][cell]
        if c["type"] == "field" and not c["crops"]:
            c["crops"] = {"type": "grain", "count": 2}
            ctx["log"].append(f"{player['name']}'s Corn Storehouse sows 2 "
                              f"grain on field {cell}")
    _corn_storehouse_next(state, player, inst)


compendium_card("I86", cost={"wood": 2, "reed": 2},
                hooks={"harvest_field": _corn_storehouse_harvest},
                resolve_choice=_corn_storehouse_resolve)


# ── I87 Flagon ────────────────────────────────────────────────────────
# Cross-player food grant on a broadcast event (Milk Jug / deck C
# precedent). The double-count edge case around the Village Well
# upgrade (I66) sharing a physical Well token is not modeled; every
# "well" improvement_built event (initial build or rebuild after being
# returned) distributes food once.
def _flagon_distribute(state, player, log):
    for pl in state["players"]:
        if pl["index"] == player["index"]:
            pl["resources"]["food"] += 4
        else:
            pl["resources"]["food"] += 1
    log.append(f"{player['name']}'s Flagon distributes food for the Well")


def _flagon_play(state, player, inst, ctx):
    if any("well" in p["improvements"] for p in state["players"]):
        _flagon_distribute(state, player, ctx["log"])


def _flagon_improvement_built(state, player, inst, ctx):
    if ctx.get("improvement") == "well":
        _flagon_distribute(state, player, ctx["log"])


compendium_card("I87", hooks={"play": _flagon_play,
                              "improvement_built": _flagon_improvement_built})


# ── I88 Lasso (duplicate of the base card under a new id) ────────────
compendium_card("I88", lasso=True)


# ── I89 Clay Path ─────────────────────────────────────────────────────
compendium_card("I89", cost={"clay": 3}, score_bonus=_street_score)


# ── I90 Planter Box ───────────────────────────────────────────────────
def _planter_box_sow(state, player, inst, ctx):
    rooms = {i for i, c in enumerate(player["cells"]) if c["type"] == "room"}
    gained = {}
    for target, crop in ctx["sown"]:
        if not isinstance(target, int):
            continue  # card fields are never adjacent to a room
        if any(nb in rooms for nb in orthogonal_neighbors(target)):
            bonus = 2 if crop == "grain" else 1
            player["cells"][target]["crops"]["count"] += bonus
            gained[crop] = gained.get(crop, 0) + bonus
    if gained:
        ctx["log"].append(f"{player['name']}'s Planter Box adds "
                          + goods_str(gained))


compendium_card("I90", prereq=needs_occupations(2),
                hooks={"sow": _planter_box_sow})


# ── I91 Ladder ─────────────────────────────────────────────────────────
# Only the room/renovation reed discount is implemented; the printed
# text also discounts the reed cost of playing specific OTHER minor
# cards (Water Mill, Chicken Coop, ...) -- kind="minor" cost_mod calls
# now carry ctx["card"], so that half is no longer blocked in principle,
# but singling out those specific (currently unimplemented) cards is a
# separate per-card pass, not this plumbing change.
def _ladder_mod(state, player, kind, cost, ctx):
    if kind in ("room", "renovation") and cost.get("reed"):
        cost = dict(cost)
        n = ctx.get("count", 1) if kind == "room" else 1
        cost["reed"] = max(0, cost["reed"] - n)
    return cost


compendium_card("I91", cost_mod=_ladder_mod)


# ── I92 Manure ────────────────────────────────────────────────────────
# Auto-applied from round_start (per GUIDE.md: don't prompt there); the
# effect is a pure gain with no real downside to declining, so forcing
# it isn't a meaningful approximation.
def _manure_round_start(state, player, inst, ctx):
    prev = ctx["round"] - 1
    if prev < 1 or prev in HARVEST_ROUNDS:
        return
    got = {"grain": 0, "vegetable": 0}
    for cell in player["cells"]:
        crops = cell.get("crops")
        if crops:
            got[crops["type"]] += 1
            crops["count"] -= 1
            if crops["count"] <= 0:
                cell["crops"] = None
    for finst in card_fields(player):
        crops = finst.get("crops")
        if crops:
            got[crops["type"]] += 1
            crops["count"] -= 1
            if crops["count"] <= 0:
                finst["crops"] = None
    if got["grain"] or got["vegetable"]:
        add_goods(player["resources"], got)
        ctx["log"].append(f"{player['name']}'s Manure harvests "
                          + goods_str(got))


compendium_card(
    "I92",
    prereq=(lambda s, p: sum(animal_totals_of(p).values()) >= 2, "2 animals"),
    hooks={"round_start": _manure_round_start})


# ── I93 Milking Shed ─────────────────────────────────────────────────
def _milking_shed_food(state, player):
    total_sheep = sum(animal_totals_of(p)["sheep"] for p in state["players"])
    total_cattle = sum(animal_totals_of(p)["cattle"] for p in state["players"])
    return total_sheep // 5 + total_cattle // 3


compendium_card("I93", hooks=harvest_food(_milking_shed_food))


# ── I94 Paved Road ────────────────────────────────────────────────────
compendium_card("I94", score_bonus=_street_score)


# ── I95 Fish Trap ─────────────────────────────────────────────────────
def _fish_trap_space_used(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    if ctx["space_id"] == "fishing" or ctx["goods"].get("reed"):
        add_goods(ctx["extra"], {"food": 1})
        ctx["log"].append(f"{player['name']}'s Fish Trap adds 1 food")


compendium_card("I95", hooks={"space_used": _fish_trap_space_used})


# ── I96 Reed Exchange ─────────────────────────────────────────────────
# Cost "2W or 2C" (OR-alternative payment isn't representable, see I84
# above); the wood variant is used.
compendium_card("I96", cost={"wood": 2}, hooks={"play": on_play_gain({"reed": 2})})


# ── I98 Schnaps Distillery ────────────────────────────────────────────
def _total_vegetable(player):
    total = player["resources"]["vegetable"]
    for c in player["cells"]:
        if c["crops"] and c["crops"]["type"] == "vegetable":
            total += c["crops"]["count"]
    for inst in card_fields(player):
        if inst["crops"] and inst["crops"]["type"] == "vegetable":
            total += inst["crops"]["count"]
    return total


def _schnaps_score(state, player, inst):
    total = _total_vegetable(player)
    return (1 if total >= 5 else 0) + (1 if total >= 6 else 0)


compendium_card(
    "I98", cost={"vegetable": 1, "stone": 2},
    conversions=[{"give": {"vegetable": 1}, "get": {"food": 4},
                 "per_harvest": 1}],
    score_bonus=_schnaps_score)


# ── I99 Straw-thatched Roof ───────────────────────────────────────────
def _straw_roof_mod(state, player, kind, cost, ctx):
    if kind in ("room", "renovation") and cost.get("reed"):
        cost = dict(cost)
        cost["reed"] = 0
    return cost


compendium_card("I99", prereq=needs_grain_field(3), cost_mod=_straw_roof_mod)


# ── I100 Tavern ──────────────────────────────────────────────────────
# An action space for all (card_space), no toll: "If another player uses
# the Tavern, you yourself do not receive anything from it." A non-owner
# placer simply gets 3 food. The owner's own placement is a choice
# between 3 food or 2 bonus points (scored normally, since it's the
# owner's own card and own scoring pass -- unlike A039 Chapel, this
# choice is never offered to a non-owner placer, so there's no
# cross-player-scoring gap to worry about).
def _tavern_resolve(state, player, inst, action, log):
    owner = card_space_owner(state, inst)
    if player is not owner:
        log.append(f"{player['name']} takes 3 food from the Tavern")
        return {"food": 3}
    prompt_choice(state, player, inst["id"],
                 "Take 3 food or 2 bonus points? (Tavern)",
                 ["3 food", "2 bonus points"])
    return {}


def _tavern_choice(state, player, inst, ctx):
    if ctx["index"] == 0:
        add_goods(ctx["extra"], {"food": 3})
        ctx["log"].append(f"{player['name']} takes 3 food (Tavern)")
    else:
        inst["data"]["bonus"] = inst["data"].get("bonus", 0) + 2
        ctx["log"].append(f"{player['name']} takes 2 bonus points (Tavern)")


compendium_card(
    "I100",
    card_space={"resolve": _tavern_resolve},
    resolve_choice=_tavern_choice,
    score_bonus=lambda s, p, i: i["data"].get("bonus", 0),
)


# ── I101 Animal Feed ──────────────────────────────────────────────────
def _animal_feed_score(state, player, inst):
    mine = animal_totals_of(player)
    total = 0
    for t in ANIMAL_TYPES:
        if mine[t] > 0:
            total += table_score(t, mine[t] + 1) - table_score(t, mine[t])
    return total


compendium_card("I101", prereq=_needs_planted_fields(4),
                score_bonus=_animal_feed_score)


# ── I102 Wildlife Reserve ────────────────────────────────────────────
# "This card can hold up to 1 sheep, 1 wild boar and 1 cattle." Req 2
# occ. (Rulings say Shepherd's Pipe/Drinking Trough also increase this
# card's capacity -- that cross-card interaction isn't modeled here,
# same documented-gap treatment as E58/E29/K120's own noted interaction
# gaps.)
def _wildlife_reserve_holds(state, player, inst):
    return {"types": {"sheep": 1, "boar": 1, "cattle": 1}}

compendium_card("I102", cost={"wood": 2}, points=1, prereq=needs_occupations(2),
                holds_animals=_wildlife_reserve_holds)


# ── I104 Weekly Market ────────────────────────────────────────────────
compendium_card("I104", cost={"grain": 3},
                hooks={"play": on_play_gain({"vegetable": 2})})


# ── I337 Clay Deposit ───────────────────────────────────────────────────
# An action space for all (card_space). A non-owner placer must pay the
# owner 1 food and receives 5 clay ("you do not need to have or to pay
# any food" for the owner's own use). The owner's own placement is
# instead a choice between the 5 clay or 2 bonus points, no toll.
def _clay_deposit_resolve(state, player, inst, action, log):
    owner = card_space_owner(state, inst)
    if player is not owner:
        if player["resources"]["food"] < 1:
            raise ValueError("You must pay 1 food to use the Clay Deposit")
        player["resources"]["food"] -= 1
        grant_goods(state, owner, {"food": 1}, log)
        log.append(f"{player['name']} pays {owner['name']} 1 food")
        log.append(f"{player['name']} takes 5 clay")
        return {"clay": 5}
    prompt_choice(state, player, inst["id"],
                 "Take 5 clay or 2 bonus points? (Clay Deposit)",
                 ["5 clay", "2 bonus points"])
    return {}


def _clay_deposit_choice(state, player, inst, ctx):
    if ctx["index"] == 0:
        add_goods(ctx["extra"], {"clay": 5})
        ctx["log"].append(f"{player['name']} takes 5 clay (Clay Deposit)")
    else:
        inst["data"]["bonus"] = inst["data"].get("bonus", 0) + 2
        ctx["log"].append(f"{player['name']} takes 2 bonus points (Clay Deposit)")


compendium_card(
    "I337",
    prereq=needs_occupations(3),
    card_space={"resolve": _clay_deposit_resolve},
    resolve_choice=_clay_deposit_choice,
    score_bonus=lambda s, p, i: i["data"].get("bonus", 0),
)
