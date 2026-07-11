"""Deck A occupations (codes A085-A168 from the compendium DB).

Several DB entries in this deck have `text`/`rulings` fields that are
visibly the concatenation of two or more adjacent cards (a
tools/parse_compendium.py artifact: an embedded "(N-M players)" marker
appears mid-string, restarting an unrelated card's text). Where that
happens, only the first clause (matching the card's own name/number) is
implemented; the trailing bleed is not this card's effect.
"""

from server.agricola.cards import (
    compendium_card, add_goods, prompt_choice, space_bonus, on_play_gain,
    animal_totals_of, fire, extra_rooms, card_fields,
    new_instance, spec, pasture_capacity as card_pasture_capacity,
)
from server.agricola.state import (
    ANIMAL_TYPES, TOTAL_ROUNDS, NUM_CELLS, MAX_PEOPLE, MAJOR_IMPROVEMENTS,
    FIREPLACES, compute_pastures, plowable_cells,
)
from server.agricola import sub_actions

UNIMPLEMENTED = {
    "A085": "extra room capacity depends on live farmyard adjacency (field "
            "+ pasture); extra_rooms is a flat per-card int, not a dynamic "
            "geometry-based predicate.",
    "A094": "requires placing a person on an occupied action space "
            "(guest-token style); not supported.",
    "A095": "grants a full Major/Minor Improvement action outside the "
            "normal action space; building improvements is engine-internal "
            "transaction logic not exposed to card hooks.",
    "A096": "needs a 'stage card revealed in the preparation phase' hook "
            "(none exists) and grants a Minor Improvement action "
            "(engine-internal, not exposed).",
    "A097": "requires intercepting/replacing the Bake Bread action with "
            "playing an occupation; no hook fires before Grain "
            "Utilization/Farmland/Cultivation resolves a bake.",
    "A100": "requires a 'returning home phase' hook and tracking which "
            "spaces were accumulation-type per player this round; "
            "occupied_by/placement counts are reset before any card hook "
            "fires.",
    "A109": "minor_played doesn't carry which action space triggered it, "
            "so 'via the Major/Minor Improvement action' (vs. Meeting "
            "Place) can't be distinguished; the DB text for this code is "
            "also visibly concatenated with several unrelated cards.",
    "A113": "requires a field to simultaneously hold two crop types "
            "(grain stacked with vegetable); the field model stores a "
            "single crop type per cell/card.",
    "A128": "grants a discounted room build reactively to another "
            "player's Reed Bank use; building a room (cell adjacency, "
            "house-type cost) is engine-internal logic not exposed to "
            "card hooks.",
    "A129": "requires placing on an already-occupied action space "
            "(guest-token style); not supported.",
    "A130": "requires placing a later person on an action space your own "
            "earlier person already occupies; guest-token/occupied-space "
            "placement is not supported.",
    "A132": "'each time another player sows' can't be observed: the sow "
            "hook fires only for the sower's own cards (fire_player), not "
            "broadcast.",
    "A141": "checks whether two spaces are occupied 'immediately before "
            "the returning home phase'; occupied_by is reset to None "
            "before any round-boundary hook fires, so that state can't be "
            "observed.",
    "A149": "grants a full discounted Build Rooms sub-action reactively; "
            "room building (adjacency, house-type cost) is engine-internal "
            "and not exposed to card hooks.",
    "A150": "grants a full Build Fences/Stables/Rooms sub-action "
            "reactively to another player's trigger; none of those build "
            "transactions are exposed to card hooks.",
    "A151": "needs a 'returning home phase' hook to check which round "
            "1-4 spaces are unoccupied; occupied_by is reset before any "
            "card hook fires at that point, and it would also grant a "
            "full alternate space-use action.",
    "A152": "needs a 'returning home phase' hook to detect whether any "
            "player used an Occupation space that round; occupied_by is "
            "reset beforehand and no per-round usage log is kept.",
    "A157": "needs a 'returning home phase' hook to check whether an "
            "Occupation space is unoccupied; occupied_by is reset before "
            "any round-boundary hook fires.",
    "A162": "'the gap between two occupied spaces' is a phantom action "
            "space that must consume a person placement; the engine has "
            "no mechanism for a dynamic extra placement slot, and a free "
            "card_action would misrepresent the worker cost.",
}

# ── Space-id groupings (mirrors the pattern used throughout cards.py) ──
WOOD_SPACES = ("forest", "grove", "copse")
CLAY_SPACES = ("clay_pit", "hollow_3p", "hollow_4p")
STONE_SPACES = ("western_quarry", "eastern_quarry")
FOOD_ACC_SPACES = ("fishing", "traveling_players")
BUILDING_RESOURCE_SPACES = WOOD_SPACES + CLAY_SPACES + ("reed_bank",) + STONE_SPACES
OCCUPATION_SPACES = ("lessons", "lessons_b")


def _space(state, sid):
    return next((s for s in state["action_spaces"] if s["id"] == sid), None)


def _remove_animal(player, animal_type):
    """Remove 1 animal of `animal_type` from wherever it lives (pasture/
    stable cell first, then house pets). Returns True if one was removed."""
    for c in player["cells"]:
        a = c.get("animal")
        if a and a["type"] == animal_type and a["count"] > 0:
            a["count"] -= 1
            if a["count"] == 0:
                c["animal"] = None
            return True
    if player["pets"].get(animal_type, 0) > 0:
        player["pets"][animal_type] -= 1
        if player["pets"][animal_type] == 0:
            del player["pets"][animal_type]
        return True
    return False


# ── A089 Stable Planner ──────────────────────────────────────────────
def _stable_planner_available(state, player, inst):
    rnd = state["round"]
    done = inst["data"].get("built", [])
    if not [m for m in (3, 6, 9) if m <= rnd and m not in done]:
        return False
    if sum(1 for c in player["cells"] if c["stable"]) >= 4:
        return False
    return any(c["type"] == "empty" and not c["stable"] for c in player["cells"])


def _stable_planner_apply(state, player, inst, ctx):
    rnd = state["round"]
    done = inst["data"].setdefault("built", [])
    milestones = [m for m in (3, 6, 9) if m <= rnd and m not in done]
    if not milestones:
        raise ValueError("Stable Planner: no free stable available")
    cell = (ctx.get("params") or {}).get("cell")
    if not isinstance(cell, int) or not (0 <= cell < NUM_CELLS):
        raise ValueError("Stable Planner: choose a cell (params.cell)")
    c = player["cells"][cell]
    if c["type"] != "empty" or c["stable"]:
        raise ValueError("That space cannot hold a stable")
    if sum(1 for x in player["cells"] if x["stable"]) >= 4:
        raise ValueError("You already have 4 stables")
    c["stable"] = True
    done.append(milestones[0])
    ctx["log"].append(f"{player['name']}'s Stable Planner builds a free stable")

compendium_card("A089", card_action={
    "available": _stable_planner_available, "apply": _stable_planner_apply,
    "description": "Build a free stable (Stable Planner, rounds 3/6/9)"})


# ── A091 Shifting Cultivator ─────────────────────────────────────────
def _shifting_cultivator_space_used(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or ctx["space_id"] not in WOOD_SPACES:
        return
    if player["resources"]["food"] < 3:
        return
    cells = plowable_cells(player)
    if not cells:
        return
    options = ["Decline"] + [f"Plow cell {c}" for c in cells]
    prompt_choice(state, player, inst["id"],
                  "Shifting Cultivator: pay 3 food to plow a field?",
                  options, data={"cells": cells})


def _shifting_cultivator_choice(state, player, inst, ctx):
    if ctx["index"] == 0:
        return
    cell = ctx["data"]["cells"][ctx["index"] - 1]
    if player["resources"]["food"] < 3 or cell not in plowable_cells(player):
        return
    player["resources"]["food"] -= 3
    player["cells"][cell]["type"] = "field"
    ctx["log"].append(f"{player['name']}'s Shifting Cultivator plows a field")

compendium_card("A091", hooks={"space_used": _shifting_cultivator_space_used},
                resolve_choice=_shifting_cultivator_choice)


# ── A093 Bed Maker ───────────────────────────────────────────────────
def _bed_maker_rooms_built(state, player, inst, ctx):
    if player["resources"]["wood"] < 1 or player["resources"]["grain"] < 1:
        return
    if player["people_total"] >= MAX_PEOPLE:
        return
    rooms = sum(1 for c in player["cells"] if c["type"] == "room")
    if rooms + extra_rooms(state, player) <= player["people_total"]:
        return
    prompt_choice(state, player, inst["id"],
                  "Bed Maker: pay 1 wood + 1 grain for Family Growth?",
                  ["Decline", "Pay 1 wood, 1 grain for Family Growth"])


def _bed_maker_choice(state, player, inst, ctx):
    if ctx["index"] == 0:
        return
    if player["resources"]["wood"] < 1 or player["resources"]["grain"] < 1:
        return
    if player["people_total"] >= MAX_PEOPLE:
        return
    player["resources"]["wood"] -= 1
    player["resources"]["grain"] -= 1
    player["people_total"] += 1
    player["newborns"] += 1
    ctx["log"].append(f"{player['name']}'s Bed Maker grants Family Growth")
    fg_ctx = {"log": ctx["log"], "actor": player["index"], "extra": {}}
    fire(state, "family_growth", fg_ctx)
    add_goods(ctx["extra"], fg_ctx["extra"])

compendium_card("A093", hooks={"rooms_built": _bed_maker_rooms_built},
                resolve_choice=_bed_maker_choice)


# ── A099 Fellow Grazer ───────────────────────────────────────────────
def _fellow_grazer_score(state, player, inst):
    return sum(2 for pasture in compute_pastures(player) if len(pasture) >= 3)

compendium_card("A099", score_bonus=_fellow_grazer_score)


# ── A101 Cookery Outfitter ───────────────────────────────────────────
def _cookery_outfitter_score(state, player, inst):
    return sum(1 for imp in player["improvements"]
               if MAJOR_IMPROVEMENTS[imp].get("cook"))

compendium_card("A101", score_bonus=_cookery_outfitter_score)


# ── A103 Portmonger ──────────────────────────────────────────────────
def _portmonger_space_used(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or ctx["space_id"] not in FOOD_ACC_SPACES:
        return
    amount = ctx["goods"].get("food", 0)
    if amount <= 0:
        return
    good = "vegetable" if amount == 1 else "grain" if amount == 2 else "reed"
    add_goods(ctx["extra"], {good: 1})
    ctx["log"].append(f"{player['name']}'s Portmonger adds 1 {good}")

compendium_card("A103", hooks={"space_used": _portmonger_space_used})


# ── A104 Wood Harvester ──────────────────────────────────────────────
def _wood_harvester_harvest(state, player, inst, ctx):
    wood_gain = food_gain = 0
    for sid in WOOD_SPACES:
        sp = _space(state, sid)
        if not sp:
            continue
        amt = sp["supply"].get("wood", 0)
        if amt == 2:
            wood_gain += 1
        elif amt >= 3:
            food_gain += 1
    if wood_gain:
        player["resources"]["wood"] += wood_gain
    if food_gain:
        player["resources"]["food"] += food_gain
    if wood_gain or food_gain:
        ctx["log"].append(f"{player['name']}'s Wood Harvester grants "
                          f"{wood_gain} wood, {food_gain} food")

compendium_card("A104", hooks={"harvest_field": _wood_harvester_harvest})


# ── A105 Barrow Pusher ───────────────────────────────────────────────
def _barrow_pusher_plow(state, player, inst, ctx):
    add_goods(player["resources"], {"clay": 1, "food": 1})
    ctx["log"].append(f"{player['name']}'s Barrow Pusher grants 1 clay, 1 food")

compendium_card("A105", hooks={"plow": _barrow_pusher_plow})


# ── A106 Slurry Spreader ─────────────────────────────────────────────
def _field_snapshot(player):
    snap = {}
    for i, c in enumerate(player["cells"]):
        snap[("cell", i)] = (
            (c["crops"]["type"], c["crops"]["count"]) if c["crops"] else None)
    for i, inst in enumerate(card_fields(player)):
        snap[("card", i)] = (
            (inst["crops"]["type"], inst["crops"]["count"])
            if inst["crops"] else None)
    return snap


def _slurry_spreader_round_start(state, player, inst, ctx):
    inst["data"]["snapshot"] = _field_snapshot(player)


def _slurry_spreader_harvest(state, player, inst, ctx):
    before = inst["data"].get("snapshot", {})
    after = _field_snapshot(player)
    grain = veg = 0
    for key, prev in before.items():
        if prev is None:
            continue
        crop_type, count = prev
        if count == 1 and after.get(key) is None:
            if crop_type == "grain":
                grain += 1
            elif crop_type == "vegetable":
                veg += 1
    gain = grain * 2 + veg
    if gain:
        player["resources"]["food"] += gain
        ctx["log"].append(f"{player['name']}'s Slurry Spreader grants {gain} food")

compendium_card("A106", hooks={"round_start": _slurry_spreader_round_start,
                               "harvest_field": _slurry_spreader_harvest})


# ── A107 Catcher ─────────────────────────────────────────────────────
CATCHER_TARGETS = {1: 5, 2: 4, 3: 3}

def _catcher_space_used(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or \
            ctx["space_id"] not in BUILDING_RESOURCE_SPACES:
        return
    target = CATCHER_TARGETS.get(player["people_placed"])
    if target is None:
        return
    amount = sum(ctx["goods"].get(g, 0) for g in ("wood", "clay", "reed", "stone"))
    if amount == target:
        add_goods(ctx["extra"], {"food": 1})
        ctx["log"].append(f"{player['name']}'s Catcher grants 1 food")

compendium_card("A107", hooks={"space_used": _catcher_space_used})


# ── A115 Chief Forester ──────────────────────────────────────────────
SOW_YIELD = {"grain": 3, "vegetable": 2}

def _chief_forester_space_used(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or ctx["space_id"] not in WOOD_SPACES:
        return
    options = []
    for i, c in enumerate(player["cells"]):
        if c["type"] == "field" and not c["crops"]:
            for crop in ("grain", "vegetable"):
                if player["resources"][crop] > 0:
                    options.append((i, crop))
    if not options:
        return
    labels = ["Decline"] + [f"Sow {crop} on cell {i}" for i, crop in options]
    prompt_choice(state, player, inst["id"], "Chief Forester: sow a field?",
                  labels, data={"options": options})


def _chief_forester_choice(state, player, inst, ctx):
    if ctx["index"] == 0:
        return
    cell, crop = ctx["data"]["options"][ctx["index"] - 1]
    c = player["cells"][cell]
    if c["type"] != "field" or c["crops"] or player["resources"][crop] <= 0:
        return
    player["resources"][crop] -= 1
    c["crops"] = {"type": crop, "count": SOW_YIELD[crop]}
    ctx["log"].append(f"{player['name']}'s Chief Forester sows {crop}")

compendium_card("A115", hooks={"space_used": _chief_forester_space_used},
                resolve_choice=_chief_forester_choice)


# ── A117 Wood Carrier ────────────────────────────────────────────────
def _wood_carrier_play(state, player, inst, ctx):
    n = len(player["improvements"]) + len(player["minors"])
    if n:
        player["resources"]["wood"] += n
        ctx["log"].append(f"{player['name']}'s Wood Carrier grants {n} wood")

compendium_card("A117", hooks={"play": _wood_carrier_play})


# ── A118 Treegardener ────────────────────────────────────────────────
def _treegardener_harvest(state, player, inst, ctx):
    player["resources"]["wood"] += 1
    inst["data"]["buys_left"] = 2
    ctx["log"].append(f"{player['name']}'s Treegardener grants 1 wood")


def _treegardener_available(state, player, inst):
    return (inst["data"].get("buys_left", 0) > 0
            and state["phase"] == "feeding"
            and player["resources"]["food"] >= 1)


def _treegardener_apply(state, player, inst, ctx):
    if inst["data"].get("buys_left", 0) <= 0 or player["resources"]["food"] < 1:
        raise ValueError("Treegardener: no purchase available")
    player["resources"]["food"] -= 1
    player["resources"]["wood"] += 1
    inst["data"]["buys_left"] -= 1
    ctx["log"].append(f"{player['name']} buys 1 wood (Treegardener)")

compendium_card("A118", hooks={"harvest_field": _treegardener_harvest},
    card_action={"available": _treegardener_available,
                 "apply": _treegardener_apply,
                 "description": "Buy 1 wood for 1 food (Treegardener, up to 2/harvest)"})


# ── A121 Clay Puncher ────────────────────────────────────────────────
def _clay_puncher_space_used(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    if ctx["space_id"] not in OCCUPATION_SPACES and ctx["space_id"] != "clay_pit":
        return
    add_goods(ctx["extra"], {"clay": 1})
    ctx["log"].append(f"{player['name']}'s Clay Puncher adds 1 clay")

compendium_card("A121", hooks={"play": on_play_gain({"clay": 1}),
                               "space_used": _clay_puncher_space_used})


# ── A122 Pan Baker ───────────────────────────────────────────────────
compendium_card("A122", hooks=space_bonus(["grain_utilization"],
                                          {"clay": 2, "wood": 1}))


# ── A124 Knapper ─────────────────────────────────────────────────────
def _knapper_space_used(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    if ctx["space_id"] in state.get("revealed", [])[4:7]:
        add_goods(ctx["extra"], {"stone": 1})
        ctx["log"].append(f"{player['name']}'s Knapper adds 1 stone")

compendium_card("A124", hooks={"space_used": _knapper_space_used})


# ── A126 Master Workman ──────────────────────────────────────────────
MASTER_WORKMAN_GOODS = {0: "wood", 1: "clay", 2: "reed", 3: "stone"}

def _master_workman_space_used(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    revealed = state.get("revealed", [])
    for idx, good in MASTER_WORKMAN_GOODS.items():
        if idx < len(revealed) and revealed[idx] == ctx["space_id"]:
            add_goods(ctx["extra"], {good: 1})
            ctx["log"].append(f"{player['name']}'s Master Workman adds 1 {good}")
            return

compendium_card("A126", hooks={"space_used": _master_workman_space_used})


# ── A127 Lodger ──────────────────────────────────────────────────────
# "This card provides room for 1 person, but only until the returning
# home phase of round 9. If you have not moved the person elsewhere by
# then, remove it from play." extra_rooms is now a callable that can
# read state["round"] (engine phase 8), so the expiry is a computed 0
# after round 9 -- round 9's own returning-home phase runs while
# state["round"] is still 9 (it only advances to 10 afterward), so
# `<= 9` covers exactly "through round 9's returning home". The card
# itself is never literally removed from play (no such mechanism
# exists) -- functionally equivalent here since Lodger has no other
# ability that cares whether it's "in play"; extra_rooms is the only
# thing any caller consults it for, and it correctly reads 0 from round
# 10 on. This does not retroactively evict a person already grown into
# the room, matching the physical rule (losing the room doesn't remove
# family members, only blocks further growth relying on it).
def _lodger_extra_rooms(state, player, inst):
    return 1 if state["round"] <= 9 else 0

compendium_card("A127", extra_rooms=_lodger_extra_rooms)


# ── A131 Craft Teacher ───────────────────────────────────────────────
CRAFT_TEACHER_MAJORS = ("joinery", "pottery", "basketmaker")

def _craft_teacher_improvement_built(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    if ctx.get("improvement") in CRAFT_TEACHER_MAJORS:
        inst["data"]["credits"] = inst["data"].get("credits", 0) + 2
        ctx["log"].append(f"{player['name']}'s Craft Teacher grants 2 free "
                          "occupation plays")


def _craft_teacher_available(state, player, inst):
    return inst["data"].get("credits", 0) > 0 and bool(player["hand_occupations"])


def _craft_teacher_apply(state, player, inst, ctx):
    if inst["data"].get("credits", 0) <= 0:
        raise ValueError("Craft Teacher: no free play available")
    cid = (ctx.get("params") or {}).get("card")
    if cid not in player["hand_occupations"]:
        raise ValueError("Craft Teacher: choose an occupation in hand (params.card)")
    sub_actions.play_occupation(state, player, cid, ctx["log"],
                                cost_override="free")
    inst["data"]["credits"] -= 1
    ctx["log"].append(f"{player['name']}'s Craft Teacher grants this play")

compendium_card("A131",
    hooks={"improvement_built": _craft_teacher_improvement_built},
    card_action={"available": _craft_teacher_available,
                 "apply": _craft_teacher_apply,
                 "description": "Play an occupation for free (Craft Teacher)"})


# ── A134 Full Farmer ─────────────────────────────────────────────────
def _full_farmer_score(state, player, inst):
    bonus = 0
    for pasture in compute_pastures(player):
        atype = next((player["cells"][i]["animal"]["type"] for i in pasture
                     if player["cells"][i]["animal"]), None)
        cap = card_pasture_capacity(state, player, pasture, atype)
        count = sum(player["cells"][i]["animal"]["count"]
                    for i in pasture if player["cells"][i]["animal"])
        if count > 0 and count >= cap:
            bonus += 1
    return bonus

compendium_card("A134", hooks={"play": on_play_gain({"wood": 1, "clay": 1})},
                score_bonus=_full_farmer_score)


# ── A135/A136 Reeve occupations (shared play hook) ──────────────────
def _reeve_play_wood(state, player, inst, ctx):
    remaining = TOTAL_ROUNDS - state["round"]
    wood = 4 if remaining >= 9 else 3 if remaining >= 6 else \
        2 if remaining >= 3 else 1 if remaining >= 1 else 0
    if wood:
        player["resources"]["wood"] += wood
        ctx["log"].append(f"{player['name']}'s {spec(inst)['name']} grants "
                          f"{wood} wood")


def _animal_reeve_score(state, player, inst):
    totals = animal_totals_of(player)
    lowest = min(totals[t] for t in ANIMAL_TYPES)
    if lowest >= 4:
        return 5
    if lowest >= 3:
        return 3
    if lowest >= 2:
        return 1
    return 0

compendium_card("A135", hooks={"play": _reeve_play_wood},
                score_bonus=_animal_reeve_score)


def _drudgery_reeve_score(state, player, inst):
    lowest = min(player["resources"].get(g, 0)
                 for g in ("wood", "clay", "reed", "stone"))
    if lowest >= 3:
        return 5
    if lowest >= 2:
        return 3
    if lowest >= 1:
        return 1
    return 0

compendium_card("A136", hooks={"play": _reeve_play_wood},
                score_bonus=_drudgery_reeve_score)


# ── A137 Riverine Shepherd ───────────────────────────────────────────
SHEEP_REED_PAIR = {"sheep_market": ("reed_bank", "reed"),
                   "reed_bank": ("sheep_market", "sheep")}

def _riverine_shepherd_space_used(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    pair = SHEEP_REED_PAIR.get(ctx["space_id"])
    if not pair:
        return
    other_id, good = pair
    other = _space(state, other_id)
    if not other or other["supply"].get(good, 0) <= 0:
        return
    other["supply"][good] -= 1
    if other["supply"][good] <= 0:
        del other["supply"][good]
    add_goods(ctx["extra"], {good: 1})
    ctx["log"].append(f"{player['name']}'s Riverine Shepherd takes 1 {good}")

compendium_card("A137", hooks={"space_used": _riverine_shepherd_space_used})


# ── A139 Hollow Warden ───────────────────────────────────────────────
HOLLOW_SPACES = ("hollow_3p", "hollow_4p")

def _build_fireplace(state, player, imp, ctx):
    cost = MAJOR_IMPROVEMENTS[imp]["cost"]
    for g, a in cost.items():
        player["resources"][g] -= a
    state["available_improvements"].remove(imp)
    player["improvements"].append(imp)
    ctx["log"].append(f"{player['name']}'s Hollow Warden builds the "
                      f"{MAJOR_IMPROVEMENTS[imp]['name']}")
    built_ctx = {"improvement": imp, "actor": player["index"], "log": ctx["log"],
                "extra": {}}
    fire(state, "improvement_built", built_ctx)
    add_goods(ctx["extra"], built_ctx["extra"])


def _hollow_warden_play(state, player, inst, ctx):
    affordable = [
        imp for imp in FIREPLACES
        if imp in state["available_improvements"]
        and all(player["resources"].get(g, 0) >= a
                for g, a in MAJOR_IMPROVEMENTS[imp]["cost"].items())]
    if not affordable:
        return
    prompt_choice(state, player, inst["id"],
                  "Hollow Warden: build a Fireplace?",
                  ["Decline"] + [MAJOR_IMPROVEMENTS[imp]["name"]
                                for imp in affordable],
                  data={"options": affordable})


def _hollow_warden_choice(state, player, inst, ctx):
    if ctx["index"] == 0:
        return
    imp = ctx["data"]["options"][ctx["index"] - 1]
    if imp in state["available_improvements"]:
        _build_fireplace(state, player, imp, ctx)


def _hollow_warden_space_used(state, player, inst, ctx):
    if ctx["actor"] == player["index"] and ctx["space_id"] in HOLLOW_SPACES:
        add_goods(ctx["extra"], {"food": 1})
        ctx["log"].append(f"{player['name']}'s Hollow Warden adds 1 food")

compendium_card("A139", hooks={"play": _hollow_warden_play,
                               "space_used": _hollow_warden_space_used},
                resolve_choice=_hollow_warden_choice)


# ── A140 Shovel Bearer ───────────────────────────────────────────────
def _shovel_bearer_space_used(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or ctx["space_id"] not in CLAY_SPACES:
        return
    others = [s for s in state["action_spaces"]
              if s["id"] in CLAY_SPACES and s["id"] != ctx["space_id"]]
    food = sum(s["supply"].get("clay", 0) for s in others)
    if food:
        add_goods(ctx["extra"], {"food": food})
        ctx["log"].append(f"{player['name']}'s Shovel Bearer grants {food} food")

compendium_card("A140", hooks={"space_used": _shovel_bearer_space_used})


# ── A142 Cordmaker ───────────────────────────────────────────────────
def _cordmaker_space_used(state, player, inst, ctx):
    if ctx["space_id"] != "reed_bank" or ctx["goods"].get("reed", 0) < 2:
        return
    options = ["Decline", "Take 1 grain"]
    if player["resources"]["food"] >= 2:
        options.append("Buy 1 vegetable for 2 food")
    prompt_choice(state, player, inst["id"],
                  "Cordmaker: take 1 grain or buy 1 vegetable?", options)


def _cordmaker_choice(state, player, inst, ctx):
    if ctx["index"] == 0:
        return
    if ctx["option"] == "Take 1 grain":
        player["resources"]["grain"] += 1
        ctx["log"].append(f"{player['name']}'s Cordmaker takes 1 grain")
    elif player["resources"]["food"] >= 2:
        player["resources"]["food"] -= 2
        player["resources"]["vegetable"] += 1
        ctx["log"].append(f"{player['name']}'s Cordmaker buys 1 vegetable")

compendium_card("A142", hooks={"space_used": _cordmaker_space_used},
                resolve_choice=_cordmaker_choice)


# ── A144 Sequestrator ────────────────────────────────────────────────
def _sequestrator_space_used(state, player, inst, ctx):
    data = inst["data"]
    if not data.get("reed_awarded"):
        for p in state["players"]:
            if len(compute_pastures(p)) >= 3:
                p["resources"]["reed"] += 3
                data["reed_awarded"] = True
                ctx["log"].append(f"{p['name']} receives 3 reed (Sequestrator)")
                break
    if not data.get("clay_awarded"):
        for p in state["players"]:
            fields = sum(1 for c in p["cells"] if c["type"] == "field")
            if fields >= 5:
                p["resources"]["clay"] += 4
                data["clay_awarded"] = True
                ctx["log"].append(f"{p['name']} receives 4 clay (Sequestrator)")
                break

compendium_card("A144", hooks={"space_used": _sequestrator_space_used})


# ── A145 Ropemaker ───────────────────────────────────────────────────
def _ropemaker_harvest(state, player, inst, ctx):
    player["resources"]["reed"] += 1
    ctx["log"].append(f"{player['name']}'s Ropemaker grants 1 reed")

compendium_card("A145", hooks={"harvest_field": _ropemaker_harvest})


# ── A146 Storehouse Steward ──────────────────────────────────────────
STOREHOUSE_GOODS = {2: "stone", 3: "reed", 4: "clay", 5: "wood"}

def _storehouse_steward_space_used(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or ctx["space_id"] not in FOOD_ACC_SPACES:
        return
    good = STOREHOUSE_GOODS.get(ctx["goods"].get("food", 0))
    if good:
        add_goods(ctx["extra"], {good: 1})
        ctx["log"].append(f"{player['name']}'s Storehouse Steward adds 1 {good}")

compendium_card("A146", hooks={"space_used": _storehouse_steward_space_used})


# ── A148 Woolgrower ──────────────────────────────────────────────────
# "This card can hold a number of sheep equal to the number of
# completed feeding phases." No new hook needed: state["harvest_index"]
# already counts harvests started (bumped at the top of _start_harvest,
# before the feeding phase begins), so it equals the completed count
# exactly except while THIS harvest's own feeding phase is still in
# progress (state["phase"] == "feeding"), when it's one ahead -- that
# harvest's feeding isn't done yet. Verified against every phase this
# can be queried from: phase "work" (any round, including the one right
# after a harvest) and "breeding" (right after all players finish
# feeding, same harvest_index) both want harvest_index unadjusted; only
# "feeding" itself wants harvest_index - 1.
def _woolgrower_holds(state, player, inst):
    n = state["harvest_index"]
    if state.get("phase") == "feeding":
        n -= 1
    return {"types": {"sheep": max(0, n)}}

compendium_card("A148", holds_animals=_woolgrower_holds)


# ── A153 Pig Owner ───────────────────────────────────────────────────
def _pig_owner_play(state, player, inst, ctx):
    inst["data"]["needs_dip"] = animal_totals_of(player)["boar"] >= 5


def _pig_owner_space_used(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or inst["data"].get("awarded"):
        return
    boar = animal_totals_of(player)["boar"]
    if inst["data"].get("needs_dip"):
        if boar < 5:
            inst["data"]["needs_dip"] = False
        return
    if boar >= 5:
        inst["data"]["awarded"] = True
        ctx["log"].append(f"{player['name']}'s Pig Owner will score 3 bonus points")


def _pig_owner_score(state, player, inst):
    return 3 if inst["data"].get("awarded") else 0

compendium_card("A153", hooks={"play": _pig_owner_play,
                               "space_used": _pig_owner_space_used},
                score_bonus=_pig_owner_score)


# ── A154 Paymaster ───────────────────────────────────────────────────
def _paymaster_space_used(state, player, inst, ctx):
    if ctx["actor"] == player["index"] or ctx["space_id"] not in FOOD_ACC_SPACES:
        return
    if not ctx["goods"].get("food") or player["resources"]["grain"] < 1:
        return
    prompt_choice(state, player, inst["id"],
                  "Paymaster: give 1 grain to the actor for 1 bonus point?",
                  ["Decline", "Give 1 grain for 1 bonus point"],
                  data={"actor": ctx["actor"]})


def _paymaster_choice(state, player, inst, ctx):
    if ctx["index"] == 0 or player["resources"]["grain"] < 1:
        return
    player["resources"]["grain"] -= 1
    other = state["players"][ctx["data"]["actor"]]
    other["resources"]["grain"] += 1
    inst["data"]["bonus"] = inst["data"].get("bonus", 0) + 1
    ctx["log"].append(f"{player['name']}'s Paymaster gives 1 grain to "
                      f"{other['name']} for 1 bonus point")


def _paymaster_score(state, player, inst):
    return inst["data"].get("bonus", 0)

compendium_card("A154", hooks={"space_used": _paymaster_space_used},
                resolve_choice=_paymaster_choice,
                score_bonus=_paymaster_score)


# ── A156 Buyer ───────────────────────────────────────────────────────
BUYER_GOODS = ("reed", "stone", "sheep", "boar")

def _buyer_space_used(state, player, inst, ctx):
    if ctx["actor"] == player["index"]:
        return
    goods = [g for g in BUYER_GOODS if ctx["goods"].get(g)]
    if not goods or player["resources"]["food"] < 1:
        return
    good = goods[0]
    prompt_choice(state, player, inst["id"],
                  f"Buyer: pay 1 food to buy 1 {good}?",
                  ["Decline", f"Pay 1 food for 1 {good}"],
                  data={"actor": ctx["actor"], "good": good})


def _buyer_choice(state, player, inst, ctx):
    if ctx["index"] == 0 or player["resources"]["food"] < 1:
        return
    player["resources"]["food"] -= 1
    other = state["players"][ctx["data"]["actor"]]
    other["resources"]["food"] += 1
    good = ctx["data"]["good"]
    if good in ANIMAL_TYPES:
        add_goods(ctx["extra"], {good: 1})
    else:
        player["resources"][good] = player["resources"].get(good, 0) + 1
    ctx["log"].append(f"{player['name']}'s Buyer buys 1 {good}")

compendium_card("A156", hooks={"space_used": _buyer_space_used},
                resolve_choice=_buyer_choice)


# ── A158 Culinary Artist ─────────────────────────────────────────────
CULINARY_RATES = {"grain": 4, "sheep": 5, "vegetable": 7}

def _culinary_artist_space_used(state, player, inst, ctx):
    if ctx["actor"] == player["index"] or ctx["space_id"] != "traveling_players":
        return
    goods = []
    options = ["Decline"]
    for good, food in CULINARY_RATES.items():
        have = (animal_totals_of(player)["sheep"] if good == "sheep"
                else player["resources"].get(good, 0))
        if have > 0:
            options.append(f"Exchange 1 {good} for {food} food")
            goods.append(good)
    if not goods:
        return
    prompt_choice(state, player, inst["id"],
                  "Culinary Artist: exchange a good for food?",
                  options, data={"goods": goods})


def _culinary_artist_choice(state, player, inst, ctx):
    if ctx["index"] == 0:
        return
    good = ctx["data"]["goods"][ctx["index"] - 1]
    food = CULINARY_RATES[good]
    if good == "sheep":
        if not _remove_animal(player, "sheep"):
            return
    else:
        if player["resources"].get(good, 0) < 1:
            return
        player["resources"][good] -= 1
    player["resources"]["food"] += food
    ctx["log"].append(f"{player['name']}'s Culinary Artist exchanges 1 {good} "
                      f"for {food} food")

compendium_card("A158", hooks={"space_used": _culinary_artist_space_used},
                resolve_choice=_culinary_artist_choice)


# ── A159 Joiner of the Sea ───────────────────────────────────────────
JOINER_RATES = {"fishing": 2, "reed_bank": 3}

def _joiner_space_used(state, player, inst, ctx):
    if ctx["actor"] == player["index"] or ctx["space_id"] not in JOINER_RATES:
        return
    if player["resources"]["wood"] < 1:
        return
    food = JOINER_RATES[ctx["space_id"]]
    prompt_choice(state, player, inst["id"],
                  f"Joiner of the Sea: give 1 wood for {food} food?",
                  ["Decline", f"Give 1 wood for {food} food"],
                  data={"actor": ctx["actor"], "food": food})


def _joiner_choice(state, player, inst, ctx):
    if ctx["index"] == 0 or player["resources"]["wood"] < 1:
        return
    player["resources"]["wood"] -= 1
    other = state["players"][ctx["data"]["actor"]]
    other["resources"]["wood"] += 1
    player["resources"]["food"] += ctx["data"]["food"]
    ctx["log"].append(f"{player['name']}'s Joiner of the Sea trades 1 wood "
                      f"for {ctx['data']['food']} food")

compendium_card("A159", hooks={"space_used": _joiner_space_used},
                resolve_choice=_joiner_choice)


# ── A161 Patch Caretaker ─────────────────────────────────────────────
def _patch_caretaker_round_start(state, player, inst, ctx):
    inst["data"]["seen_goods"] = []


def _patch_caretaker_space_used(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    sp = _space(state, ctx["space_id"])
    if not sp or not sp["accumulates"]:
        return
    seen = inst["data"].setdefault("seen_goods", [])
    matched = False
    for good in ctx["goods"]:
        if good in seen:
            matched = True
        else:
            seen.append(good)
    if matched:
        add_goods(ctx["extra"], {"vegetable": 1})
        ctx["log"].append(f"{player['name']}'s Patch Caretaker adds 1 vegetable")

compendium_card("A161", hooks={"round_start": _patch_caretaker_round_start,
                               "space_used": _patch_caretaker_space_used})


# ── A163 Building Expert ─────────────────────────────────────────────
BUILDING_EXPERT_GOODS = {1: "wood", 2: "clay", 3: "reed", 4: "stone", 5: "stone"}

def _building_expert_space_used(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or \
            ctx["space_id"] not in ("resource_market_3p", "resource_market_4p"):
        return
    good = BUILDING_EXPERT_GOODS.get(player["people_placed"])
    if good:
        add_goods(ctx["extra"], {good: 1})
        ctx["log"].append(f"{player['name']}'s Building Expert adds 1 {good}")

compendium_card("A163", hooks={"space_used": _building_expert_space_used})


# ── A164 Wood Worker ─────────────────────────────────────────────────
def _wood_worker_space_used(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or ctx["space_id"] not in WOOD_SPACES:
        return
    if not ctx["goods"].get("wood") or player["resources"]["wood"] < 1:
        return
    prompt_choice(state, player, inst["id"],
                  "Wood Worker: exchange 1 wood for 1 sheep?",
                  ["Decline", "Exchange 1 wood for 1 sheep"],
                  data={"space_id": ctx["space_id"]})


def _wood_worker_choice(state, player, inst, ctx):
    if ctx["index"] == 0 or player["resources"]["wood"] < 1:
        return
    player["resources"]["wood"] -= 1
    sp = _space(state, ctx["data"]["space_id"])
    if sp is not None:
        sp["supply"]["wood"] = sp["supply"].get("wood", 0) + 1
    add_goods(ctx["extra"], {"sheep": 1})
    ctx["log"].append(f"{player['name']}'s Wood Worker exchanges 1 wood for 1 sheep")

compendium_card("A164", hooks={"space_used": _wood_worker_space_used},
                resolve_choice=_wood_worker_choice)


# ── A166 Haydryer ────────────────────────────────────────────────────
def _haydryer_harvest(state, player, inst, ctx):
    cost = max(0, 4 - len(compute_pastures(player)))
    if player["resources"]["food"] < cost:
        return
    label = "Buy 1 cattle for free" if cost == 0 else f"Buy 1 cattle for {cost} food"
    prompt_choice(state, player, inst["id"], "Haydryer: buy 1 cattle before harvest?",
                  ["Decline", label], data={"cost": cost})


def _haydryer_choice(state, player, inst, ctx):
    if ctx["index"] == 0:
        return
    cost = ctx["data"]["cost"]
    if player["resources"]["food"] < cost:
        return
    player["resources"]["food"] -= cost
    add_goods(ctx["extra"], {"cattle": 1})
    ctx["log"].append(f"{player['name']}'s Haydryer buys 1 cattle for {cost} food")

compendium_card("A166", hooks={"harvest_field": _haydryer_harvest},
                resolve_choice=_haydryer_choice)


# ── A167 Breeder Buyer ───────────────────────────────────────────────
ROOM_ANIMAL = {"wood": "sheep", "clay": "boar", "stone": "cattle"}

def _breeder_buyer_rooms(state, player, inst, ctx):
    inst["data"]["room_material"] = player["house_type"]


def _breeder_buyer_stables(state, player, inst, ctx):
    inst["data"]["stable_built"] = True


def _breeder_buyer_space_used(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or ctx["space_id"] != "farm_expansion":
        return
    data = inst["data"]
    material = data.pop("room_material", None)
    stable = data.pop("stable_built", False)
    if material and stable:
        animal = ROOM_ANIMAL.get(material)
        if animal:
            add_goods(ctx["extra"], {animal: 1})
            ctx["log"].append(f"{player['name']}'s Breeder Buyer grants 1 {animal}")

compendium_card("A167", hooks={"rooms_built": _breeder_buyer_rooms,
                               "stable_built": _breeder_buyer_stables,
                               "space_used": _breeder_buyer_space_used})


# ── A168 Animal Teacher ──────────────────────────────────────────────
ANIMAL_TEACHER_COSTS = {"sheep": 0, "boar": 1, "cattle": 2}

def _animal_teacher_space_used(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or ctx["space_id"] not in OCCUPATION_SPACES:
        return
    choices = []
    options = ["Decline"]
    for animal, cost in ANIMAL_TEACHER_COSTS.items():
        if player["resources"]["food"] >= cost:
            options.append(f"Buy 1 {animal} for {cost} food")
            choices.append(animal)
    if not choices:
        return
    prompt_choice(state, player, inst["id"], "Animal Teacher: buy an animal?",
                  options, data={"choices": choices})


def _animal_teacher_choice(state, player, inst, ctx):
    if ctx["index"] == 0:
        return
    animal = ctx["data"]["choices"][ctx["index"] - 1]
    cost = ANIMAL_TEACHER_COSTS[animal]
    if player["resources"]["food"] < cost:
        return
    player["resources"]["food"] -= cost
    add_goods(ctx["extra"], {animal: 1})
    ctx["log"].append(f"{player['name']}'s Animal Teacher buys 1 {animal}")

compendium_card("A168", hooks={"space_used": _animal_teacher_space_used},
                resolve_choice=_animal_teacher_choice)
