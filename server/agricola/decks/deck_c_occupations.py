"""Deck C occupations (codes C085-C168 from the compendium DB).

Data-quality note: several entries in this slice of the compendium DB
concatenate what appear to be two or more unrelated card printings under
one code (a repeated "(N-M players)" tag reappears *mid-text* even though
it matches the entry's own declared player band, and/or the trailing text
is verbatim identical to an unrelated card already in `cards.py`, e.g.
C134's tail duplicates House Steward's play effect). Where that pattern
appears, only the first, self-contained clause is treated as this card's
real rules text; the rest is a scrape artifact and is not implemented.
This is noted per-card below.

Engine limitations discovered while implementing this slice (see the
final report / UNIMPLEMENTED reasons for detail; stables now route
through `modified_cost` via `kind="stable"`, added in engine phase 7 --
see C088's UNIMPLEMENTED note, which predates that fix): `extra_rooms` is a
static per-spec constant (can't be gated on a runtime purchase);
`renovate` and `harvest_field` fire only to the acting/own player's cards
(`fire_player`), not broadcast, so "each time *another player*
renovates/harvests" cannot be observed; `bake_on_spaces` is only wired to
the `farmland`/`cultivation` space handlers, not `major_improvement`;
`occupation_played` ctx carries only `card_id`, not the space used or
food paid; action spaces carry no adjacency/position data.
"""

from server.agricola.cards import (
    compendium_card, CARDS, prompt_choice,
    add_goods, space_bonus, round_income, on_play_gain,
    animal_totals_of, card_fields, fire_player,
)
from server.agricola.state import (
    ANIMAL_TYPES, HARVEST_ROUNDS, TOTAL_ROUNDS, BUILDING_RESOURCES,
    compute_pastures, plowable_cells,
)

UNIMPLEMENTED = {}

CROP_START_COUNT = {"grain": 3, "vegetable": 2}


# ── Shared helpers ────────────────────────────────────────────────────

def _sow_one(state, player, params, log, label):
    """Card-granted single-field Sow (Sower, Sowing Director)."""
    if not isinstance(params, dict):
        raise ValueError("Choose a field and crop to sow")
    crop = params.get("crop")
    if crop not in ("grain", "vegetable"):
        raise ValueError("Choose grain or vegetable to sow")
    if player["resources"].get(crop, 0) < 1:
        raise ValueError(f"Not enough {crop}")
    card_id = params.get("card")
    if card_id is not None:
        inst = next((i for i in card_fields(player) if i["id"] == card_id), None)
        if inst is None or inst["crops"]:
            raise ValueError("Invalid card field")
        allowed = CARDS[card_id]["field"]["crops"]
        if crop not in allowed:
            raise ValueError("That field cannot grow that crop")
        inst["crops"] = {"type": crop, "count": CROP_START_COUNT[crop]}
        target = inst
    else:
        cell = params.get("cell")
        if not isinstance(cell, int):
            raise ValueError("Choose a field to sow")
        c = player["cells"][cell]
        if c["type"] != "field" or c["crops"]:
            raise ValueError("You can only sow empty fields")
        c["crops"] = {"type": crop, "count": CROP_START_COUNT[crop]}
        target = cell
    player["resources"][crop] -= 1
    log.append(f"{player['name']} sows 1 {crop} field ({label})")
    ctx2 = {"sown": [(target, crop)], "log": log,
            "actor": player["index"], "extra": {}}
    fire_player(state, player, "sow", ctx2)
    add_goods(player["resources"], ctx2["extra"])


def _grant_animal_to_owner(state, player, animal, count):
    """Queue accommodation for an animal gained by a player who is not
    the acting player (mirrors the engine's own _gain_animals, which
    isn't reachable from card code)."""
    for pr in state["prompts"]:
        if pr["type"] == "accommodate" and pr["player"] == player["index"]:
            pr["gained"][animal] = pr["gained"].get(animal, 0) + count
            return
    state["prompts"].append({"type": "accommodate", "player": player["index"],
                             "gained": {animal: count}})


def _unfenced_stables(player):
    pasture_cells = {i for pa in compute_pastures(player) for i in pa}
    return sum(1 for i, c in enumerate(player["cells"])
              if c["stable"] and i not in pasture_cells)


# ═════════════════════════════════════════════════════════════════════
# C085 Den Builder — UNIMPLEMENTED
# ═════════════════════════════════════════════════════════════════════
UNIMPLEMENTED["C085"] = (
    "'this card provides room for 1 person' after a runtime purchase "
    "(pay 1 grain + 2 food in a clay/stone house): extra_rooms() sums a "
    "static per-spec constant, not something a card can toggle at "
    "runtime, so a purchased/conditional room can't be expressed")

# ═════════════════════════════════════════════════════════════════════
# C088 Carpenter's Apprentice — UNIMPLEMENTED
# ═════════════════════════════════════════════════════════════════════
UNIMPLEMENTED["C088"] = (
    "wood-room and fence discounts are easy (cost_mod); the '3rd/4th "
    "stable costs 1 wood less' clause needed a cost_mod kind for "
    "stables, which now exists (kind='stable', with 'index'/'start_index' "
    "ctx for per-Nth pricing -- engine phase 7), so all 3 clauses are "
    "expressible -- this is now a plain implementation gap, not a "
    "plumbing one")

# ═════════════════════════════════════════════════════════════════════
# C089 Stablemaster
# "When you play this card, you can immediately build exactly 1 stable
# for 1 wood. Each time you use the 'Grain Seeds' action space, you can
# also plow 1 field."
# ═════════════════════════════════════════════════════════════════════
def _stablemaster_play(state, player, inst, ctx):
    cell = (ctx.get("params") or {}).get("stable_cell")
    if cell is None:
        return  # optional ("can")
    if not isinstance(cell, int):
        raise ValueError("Stablemaster: choose params.stable_cell")
    c = player["cells"][cell]
    stables = sum(1 for x in player["cells"] if x["stable"])
    if stables >= 4 or c["type"] != "empty" or c["stable"]:
        raise ValueError("Invalid stable space")
    if player["resources"]["wood"] < 1:
        raise ValueError("Not enough wood")
    player["resources"]["wood"] -= 1
    c["stable"] = True
    ctx["log"].append(f"{player['name']}'s Stablemaster builds a cheap stable")
    fire_player(state, player, "stable_built", {"cells": [cell], "log": ctx["log"],
                                                "actor": player["index"], "extra": {}})


def _stablemaster_grain_seeds(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or ctx["space_id"] != "grain_seeds":
        return
    inst["data"]["plow_round"] = state["round"]


def _stablemaster_available(state, player, inst):
    return (inst["data"].get("plow_round") == state["round"]
            and bool(plowable_cells(player)))


def _stablemaster_apply(state, player, inst, ctx):
    cell = (ctx.get("params") or {}).get("cell")
    if not isinstance(cell, int) or cell not in plowable_cells(player):
        raise ValueError("Choose params.cell to plow")
    player["cells"][cell]["type"] = "field"
    inst["data"]["plow_round"] = None
    ctx["log"].append(f"{player['name']}'s Stablemaster plows an extra field")


compendium_card("C089", hooks={
    "play": _stablemaster_play,
    "space_used": _stablemaster_grain_seeds,
}, card_action={
    "available": _stablemaster_available,
    "apply": _stablemaster_apply,
    "description": "Stablemaster: plow 1 extra field",
})

# ═════════════════════════════════════════════════════════════════════
# C091 Plow Hero
# "Each time you use the 'Plow 1 field' or 'Plow 1 field and/or Sow'
# action space with the first person you place in a round, you can plow
# 1 additional field for 1 food."
# ═════════════════════════════════════════════════════════════════════
def _plow_hero_space_used(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    if ctx["space_id"] not in ("farmland", "cultivation"):
        return
    if player["people_placed"] == 1:
        inst["data"]["available_round"] = state["round"]


def _plow_hero_available(state, player, inst):
    return (inst["data"].get("available_round") == state["round"]
            and player["resources"]["food"] >= 1
            and bool(plowable_cells(player)))


def _plow_hero_apply(state, player, inst, ctx):
    cell = (ctx.get("params") or {}).get("cell")
    if not isinstance(cell, int) or cell not in plowable_cells(player):
        raise ValueError("Choose params.cell to plow")
    player["resources"]["food"] -= 1
    player["cells"][cell]["type"] = "field"
    inst["data"]["available_round"] = None
    ctx["log"].append(f"{player['name']}'s Plow Hero plows an extra field")


compendium_card("C091", hooks={"space_used": _plow_hero_space_used},
                card_action={"available": _plow_hero_available,
                             "apply": _plow_hero_apply,
                             "description": "Plow Hero: plow 1 field for 1 food"})

# ═════════════════════════════════════════════════════════════════════
# C092 Autumn Mother
# "Immediately before each harvest, if you have room in your house, you
# can take a 'Family Growth' action for 3 food."
# (No hook fires strictly "before" a harvest; the closest available
# window is the owner's own turn during a harvest round's work phase,
# which precedes that harvest. Limited to once per harvest occurrence.)
# ═════════════════════════════════════════════════════════════════════
def _autumn_mother_available(state, player, inst):
    from server.agricola.state import MAX_PEOPLE
    if state["round"] not in HARVEST_ROUNDS:
        return False
    if inst["data"].get("used_for_harvest") == state["round"]:
        return False
    rooms = sum(1 for c in player["cells"] if c["type"] == "room")
    return (player["people_total"] < MAX_PEOPLE
            and rooms > player["people_total"]
            and player["resources"]["food"] >= 3)


def _autumn_mother_apply(state, player, inst, ctx):
    player["resources"]["food"] -= 3
    player["people_total"] += 1
    player["newborns"] += 1
    inst["data"]["used_for_harvest"] = state["round"]
    ctx["log"].append(f"{player['name']}'s Autumn Mother grows the family")
    fire_player(state, player, "family_growth", ctx)


compendium_card("C092", card_action={
    "available": _autumn_mother_available, "apply": _autumn_mother_apply,
    "description": "Autumn Mother: Family Growth for 3 food"})

# ═════════════════════════════════════════════════════════════════════
# C093 Inner Districts Director — UNIMPLEMENTED
# ═════════════════════════════════════════════════════════════════════
UNIMPLEMENTED["C093"] = (
    "grants an extra person placement (place again immediately) after "
    "using the wood/clay accumulation spaces; the engine only supports "
    "double placement via the Lasso's hardcoded animal-market case "
    "(lasso=True), not a generalized per-card replacement/extra turn")

# ═════════════════════════════════════════════════════════════════════
# C094 Stable Cleaner
# "At any time, you can take the 'Build Stables' action without placing
# a person. If you do, each stable costs you 1 wood and 1 food."
# ═════════════════════════════════════════════════════════════════════
def _stable_cleaner_available(state, player, inst):
    from server.agricola.state import MAX_STABLES
    stables = sum(1 for c in player["cells"] if c["stable"])
    if stables >= MAX_STABLES:
        return False
    if not any(c["type"] == "empty" and not c["stable"] for c in player["cells"]):
        return False
    return player["resources"]["wood"] >= 1 and player["resources"]["food"] >= 1


def _stable_cleaner_apply(state, player, inst, ctx):
    from server.agricola.state import MAX_STABLES
    cells = (ctx.get("params") or {}).get("cells")
    if not isinstance(cells, list) or not cells:
        raise ValueError("Choose params.cells to build stables on")
    if len(cells) != len(set(cells)):
        raise ValueError("Duplicate stable spaces")
    built = []
    for cell in cells:
        if not isinstance(cell, int):
            raise ValueError("Invalid stable space")
        stables = sum(1 for c in player["cells"] if c["stable"])
        if stables >= MAX_STABLES:
            raise ValueError("You only have 4 stables")
        c = player["cells"][cell]
        if c["type"] != "empty" or c["stable"]:
            raise ValueError("Stables need an empty space without a tile")
        if player["resources"]["wood"] < 1 or player["resources"]["food"] < 1:
            raise ValueError("Not enough wood/food")
        player["resources"]["wood"] -= 1
        player["resources"]["food"] -= 1
        c["stable"] = True
        built.append(cell)
    ctx["log"].append(f"{player['name']}'s Stable Cleaner builds "
                      f"{len(built)} stable(s) without placing a person")
    fire_player(state, player, "stable_built",
                {"cells": built, "log": ctx["log"], "actor": player["index"],
                 "extra": {}})


compendium_card("C094", card_action={
    "available": _stable_cleaner_available, "apply": _stable_cleaner_apply,
    "description": "Stable Cleaner: build stables for 1 wood + 1 food each"})

# ═════════════════════════════════════════════════════════════════════
# C095 Basket Weaver
# "When you play this card, immediately build the 'Basketmaker's
# Workshop' major improvement for 1 stone and 1 reed."
# ═════════════════════════════════════════════════════════════════════
def _basket_weaver_play(state, player, inst, ctx):
    if "basketmaker" not in state["available_improvements"]:
        ctx["log"].append("Basket Weaver: Basketmaker's Workshop unavailable")
        return
    if player["resources"]["stone"] < 1 or player["resources"]["reed"] < 1:
        ctx["log"].append("Basket Weaver: not enough stone/reed")
        return
    player["resources"]["stone"] -= 1
    player["resources"]["reed"] -= 1
    state["available_improvements"].remove("basketmaker")
    player["improvements"].append("basketmaker")
    ctx["log"].append(f"{player['name']}'s Basket Weaver builds the "
                      "Basketmaker's Workshop")
    fire_player(state, player, "improvement_built",
                {"improvement": "basketmaker", "log": ctx["log"],
                 "actor": player["index"], "extra": {}})


compendium_card("C095", hooks={"play": _basket_weaver_play})

# ═════════════════════════════════════════════════════════════════════
# C096 Merchant — UNIMPLEMENTED
# ═════════════════════════════════════════════════════════════════════
UNIMPLEMENTED["C096"] = (
    "'use the Minor/Major Improvement action a second time for 1 food' "
    "would require replaying _do_improvement/_play_minor's engine "
    "internals (cost, available_improvements bookkeeping, well/bake_on_"
    "build side effects) from inside a card hook — the architecture's "
    "own guidance is to extend the engine for new mechanisms rather "
    "than duplicate its logic per-card")

# ═════════════════════════════════════════════════════════════════════
# C097 Seed Researcher — UNIMPLEMENTED
# ═════════════════════════════════════════════════════════════════════
UNIMPLEMENTED["C097"] = (
    "granting a free 'play 1 occupation without paying its cost' action "
    "would require duplicating _play_occupation's internals (cost, hand "
    "removal, occs_played bookkeeping, occupation_played firing) from a "
    "card hook; also needs a 'people return from both spaces this round' "
    "condition with no corresponding engine event")

# ═════════════════════════════════════════════════════════════════════
# C098 Cube Cutter
# "When you play this card, you immediately get 1 wood. In the field
# phase of each harvest, you can use this card to exchange exactly 1
# wood and 1 food for 1 bonus point."
# ═════════════════════════════════════════════════════════════════════
def _cube_cutter_harvest(state, player, inst, ctx):
    if player["resources"]["wood"] >= 1 and player["resources"]["food"] >= 1:
        prompt_choice(state, player, inst["id"],
                      "Cube Cutter: exchange 1 wood + 1 food for 1 bonus point?",
                      ["Yes", "No"])


def _cube_cutter_choice(state, player, inst, ctx):
    if ctx["index"] == 0:
        player["resources"]["wood"] -= 1
        player["resources"]["food"] -= 1
        inst["data"]["points"] = inst["data"].get("points", 0) + 1
        ctx["log"].append(f"{player['name']}'s Cube Cutter earns 1 bonus point")


compendium_card("C098", hooks={
    "play": on_play_gain({"wood": 1}),
    "harvest_field": _cube_cutter_harvest,
}, resolve_choice=_cube_cutter_choice,
   score_bonus=lambda s, p, i: i["data"].get("points", 0))

# ═════════════════════════════════════════════════════════════════════
# C099 Garden Designer
# "At the start of scoring, you can place food in your empty fields. You
# get 1/2/3 bonus point(s) for each such field in which you place 1/4/7
# food."
# (Spending food at final scoring has no other cost since unused food
# never scores, so the "choice" is always to maximize: fill 1-food fields
# first, then upgrade with any food left over.)
# ═════════════════════════════════════════════════════════════════════
def _garden_designer_score(state, player, inst):
    empty_fields = sum(1 for c in player["cells"]
                       if c["type"] == "field" and not c["crops"])
    empty_fields += sum(1 for i in card_fields(player) if not i["crops"])
    food = player["resources"]["food"]
    if empty_fields == 0 or food == 0:
        return 0
    base = min(empty_fields, food)
    points = base
    food -= base
    upgraded_to_4 = min(base, food // 3)
    points += upgraded_to_4
    food -= upgraded_to_4 * 3
    upgraded_to_7 = min(upgraded_to_4, food // 3)
    points += upgraded_to_7
    return points


compendium_card("C099", score_bonus=_garden_designer_score)

# ═════════════════════════════════════════════════════════════════════
# C100 Butler
# "If you play this card in round 11 or before, during scoring, you get
# 4 bonus points if you then have more rooms than people."
# ═════════════════════════════════════════════════════════════════════
def _butler_play(state, player, inst, ctx):
    if state["round"] <= 11:
        inst["data"]["eligible"] = True


def _butler_score(state, player, inst):
    if not inst["data"].get("eligible"):
        return 0
    rooms = sum(1 for c in player["cells"] if c["type"] == "room")
    return 4 if rooms > player["people_total"] else 0


compendium_card("C100", hooks={"play": _butler_play}, score_bonus=_butler_score)

# ═════════════════════════════════════════════════════════════════════
# C101 Stall Holder
# "Once per round, if you have 0/1/2/3/4 unfenced stables on your farm,
# you can exchange 2 grain for 1 bonus point and 1/2/3/4/5 food."
# ═════════════════════════════════════════════════════════════════════
def _stall_holder_available(state, player, inst):
    return (inst["data"].get("used_round") != state["round"]
            and player["resources"]["grain"] >= 2)


def _stall_holder_apply(state, player, inst, ctx):
    food = min(4, _unfenced_stables(player)) + 1
    player["resources"]["grain"] -= 2
    player["resources"]["food"] += food
    inst["data"]["used_round"] = state["round"]
    inst["data"]["points"] = inst["data"].get("points", 0) + 1
    ctx["log"].append(f"{player['name']}'s Stall Holder trades 2 grain for "
                      f"{food} food and 1 bonus point")


compendium_card("C101", card_action={
    "available": _stall_holder_available, "apply": _stall_holder_apply,
    "description": "Stall Holder: 2 grain -> food + 1 bonus point"},
    score_bonus=lambda s, p, i: i["data"].get("points", 0))

# ═════════════════════════════════════════════════════════════════════
# C102 Tree Guard
# "Each time after you use a wood accumulation space, you can place 4
# wood from your supply on that space to get 2 stone, 1 clay, 1 reed,
# and 1 grain."
# ═════════════════════════════════════════════════════════════════════
WOOD_SPACES = ("forest", "grove", "copse")


def _tree_guard_space_used(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or ctx["space_id"] not in WOOD_SPACES:
        return
    inst["data"]["pending"] = inst["data"].get("pending", 0) + 1


def _tree_guard_available(state, player, inst):
    return inst["data"].get("pending", 0) > 0 and player["resources"]["wood"] >= 4


def _tree_guard_apply(state, player, inst, ctx):
    player["resources"]["wood"] -= 4
    add_goods(player["resources"], {"stone": 2, "clay": 1, "reed": 1, "grain": 1})
    inst["data"]["pending"] -= 1
    ctx["log"].append(f"{player['name']}'s Tree Guard trades 4 wood for "
                      "2 stone, 1 clay, 1 reed, and 1 grain")


compendium_card("C102", hooks={"space_used": _tree_guard_space_used},
                card_action={"available": _tree_guard_available,
                             "apply": _tree_guard_apply,
                             "description": "Tree Guard: 4 wood -> 2 stone/1 clay/1 reed/1 grain"})

# ═════════════════════════════════════════════════════════════════════
# C103 Green Grocer
# "At the start of each round, you can make exactly one of the following
# exchanges: 1 cattle for 1 vegetable; 2 sheep for 1 vegetable; 1
# vegetable for 1 cattle; 1 vegetable for 2 sheep; 2 food for 1 grain; 1
# grain for 2 food."
# (The trailing "This card is an action space for you only..." clause
# repeats this entry's own "(1-5 players)" band mid-text — a DB merge
# artifact from an unrelated card — and is not implemented: cards can't
# be placement targets in this engine.)
# ═════════════════════════════════════════════════════════════════════
_GREEN_GROCER_OPTIONS = [
    ("1 cattle -> 1 vegetable", {"cattle": 1}, {"vegetable": 1}),
    ("2 sheep -> 1 vegetable", {"sheep": 2}, {"vegetable": 1}),
    ("1 vegetable -> 1 cattle", {"vegetable": 1}, {"cattle": 1}),
    ("1 vegetable -> 2 sheep", {"vegetable": 1}, {"sheep": 2}),
    ("2 food -> 1 grain", {"food": 2}, {"grain": 1}),
    ("1 grain -> 2 food", {"grain": 1}, {"food": 2}),
]


def _affordable(player, give):
    for good, amount in give.items():
        if good in ANIMAL_TYPES:
            if animal_totals_of(player)[good] < amount:
                return False
        elif player["resources"].get(good, 0) < amount:
            return False
    return True


def _green_grocer_round_start(state, player, inst, ctx):
    options = [label for label, give, get in _GREEN_GROCER_OPTIONS
              if _affordable(player, give)]
    if not options:
        return
    prompt_choice(state, player, inst["id"], "Green Grocer: make an exchange?",
                 options + ["Skip"])


def _green_grocer_choice(state, player, inst, ctx):
    options = [o for o in _GREEN_GROCER_OPTIONS if _affordable(player, o[1])]
    if ctx["index"] >= len(options):
        return
    label, give, get = options[ctx["index"]]
    for good, amount in give.items():
        if good in ANIMAL_TYPES:
            # Animals leaving supply: remove from cells/pets directly.
            remaining = amount
            for cell in player["cells"]:
                a = cell.get("animal")
                if a and a["type"] == good and remaining > 0:
                    take = min(a["count"], remaining)
                    a["count"] -= take
                    remaining -= take
                    if a["count"] <= 0:
                        cell["animal"] = None
            if remaining > 0 and player["pets"].get(good):
                take = min(player["pets"][good], remaining)
                player["pets"][good] -= take
                if player["pets"][good] <= 0:
                    del player["pets"][good]
        else:
            player["resources"][good] -= amount
    for good, amount in get.items():
        if good in ANIMAL_TYPES:
            add_goods(ctx["extra"], {good: amount})
        else:
            player["resources"][good] = player["resources"].get(good, 0) + amount
    ctx["log"].append(f"{player['name']}'s Green Grocer exchange: {label}")


compendium_card("C103", hooks={"round_start": _green_grocer_round_start},
                resolve_choice=_green_grocer_choice)

# ═════════════════════════════════════════════════════════════════════
# C105 Basket Carrier
# "Once each harvest, you can buy 1 wood, 1 reed, and 1 grain for 2 food
# total."
# (This entry's remaining text concatenates at least five more unrelated
# effects, each behind a repeated "(1-5 players)" tag — a DB merge
# artifact; only the first, clean clause above is implemented.)
# ═════════════════════════════════════════════════════════════════════
def _basket_carrier_harvest(state, player, inst, ctx):
    if player["resources"]["food"] >= 2:
        prompt_choice(state, player, inst["id"],
                      "Basket Carrier: buy 1 wood, 1 reed, and 1 grain for 2 food?",
                      ["Yes", "No"])


def _basket_carrier_choice(state, player, inst, ctx):
    if ctx["index"] == 0 and player["resources"]["food"] >= 2:
        player["resources"]["food"] -= 2
        add_goods(player["resources"], {"wood": 1, "reed": 1, "grain": 1})
        ctx["log"].append(f"{player['name']}'s Basket Carrier buys "
                          "1 wood, 1 reed, and 1 grain")


compendium_card("C105", hooks={"harvest_field": _basket_carrier_harvest},
                resolve_choice=_basket_carrier_choice)

# ═════════════════════════════════════════════════════════════════════
# C111 Small Animal Breeder
# "At the start of each round, if you have food equal to or higher than
# the current round number (e.g., 8+ food in round 8), you get 1 food."
# (Trailing clauses again repeat the "(1-5 players)" band mid-text — DB
# merge artifact — and are not implemented.)
# ═════════════════════════════════════════════════════════════════════
compendium_card("C111", hooks=round_income(
    {"food": 1}, condition=lambda s, p: p["resources"]["food"] >= s["round"]))

# ═════════════════════════════════════════════════════════════════════
# C114 Soil Scientist
# "Each time after you use a clay/stone accumulation space, you can
# place 1 stone/2 clay from your supply on the space to get 2
# grain/1 vegetable, respectively."
# ═════════════════════════════════════════════════════════════════════
def _soil_scientist_space_used(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    if ctx["space_id"] == "clay_pit":
        inst["data"]["clay_pending"] = inst["data"].get("clay_pending", 0) + 1
    elif ctx["space_id"] in ("western_quarry", "eastern_quarry"):
        inst["data"]["stone_pending"] = inst["data"].get("stone_pending", 0) + 1


def _soil_scientist_available(state, player, inst):
    return ((inst["data"].get("clay_pending", 0) > 0
             and player["resources"]["stone"] >= 1)
            or (inst["data"].get("stone_pending", 0) > 0
                and player["resources"]["clay"] >= 2))


def _soil_scientist_apply(state, player, inst, ctx):
    which = (ctx.get("params") or {}).get("which")
    if which == "clay" and inst["data"].get("clay_pending", 0) > 0 \
            and player["resources"]["stone"] >= 1:
        player["resources"]["stone"] -= 1
        player["resources"]["grain"] += 2
        inst["data"]["clay_pending"] -= 1
        ctx["log"].append(f"{player['name']}'s Soil Scientist trades 1 stone "
                          "for 2 grain")
    elif which == "stone" and inst["data"].get("stone_pending", 0) > 0 \
            and player["resources"]["clay"] >= 2:
        player["resources"]["clay"] -= 2
        player["resources"]["vegetable"] += 1
        inst["data"]["stone_pending"] -= 1
        ctx["log"].append(f"{player['name']}'s Soil Scientist trades 2 clay "
                          "for 1 vegetable")
    else:
        raise ValueError("Choose params.which ('clay' or 'stone')")


compendium_card("C114", hooks={"space_used": _soil_scientist_space_used},
                card_action={"available": _soil_scientist_available,
                             "apply": _soil_scientist_apply,
                             "description": "Soil Scientist: exchange stone/clay"})

# ═════════════════════════════════════════════════════════════════════
# C115 Sower
# "Each time you build a major improvement, you can place 1 reed from
# the general supply on this card. At any time, you can move the reed to
# your supply or exchange it for a 'Sow' action."
# ═════════════════════════════════════════════════════════════════════
def _sower_improvement_built(state, player, inst, ctx):
    inst["data"]["reed"] = inst["data"].get("reed", 0) + 1
    ctx["log"].append(f"{player['name']}'s Sower gets 1 stored reed")


def _sower_available(state, player, inst):
    return inst["data"].get("reed", 0) > 0


def _sower_apply(state, player, inst, ctx):
    params = ctx.get("params") or {}
    action = params.get("action")
    if action == "collect":
        inst["data"]["reed"] -= 1
        player["resources"]["reed"] += 1
        ctx["log"].append(f"{player['name']}'s Sower collects 1 reed")
    elif action == "sow":
        _sow_one(state, player, params, ctx["log"], "Sower")
        inst["data"]["reed"] -= 1
    else:
        raise ValueError("Choose params.action ('collect' or 'sow')")


compendium_card("C115", hooks={"improvement_built": _sower_improvement_built},
                card_action={"available": _sower_available, "apply": _sower_apply,
                             "description": "Sower: collect reed or sow a field"})

# ═════════════════════════════════════════════════════════════════════
# C116 Furniture Maker — UNIMPLEMENTED
# ═════════════════════════════════════════════════════════════════════
UNIMPLEMENTED["C116"] = (
    "'get 1 wood per food paid as occupation cost' needs the food cost "
    "and which Lessons space was used; the occupation_played ctx only "
    "carries card_id, not the cost or space_id, so the amount paid can't "
    "be recovered")

# ═════════════════════════════════════════════════════════════════════
# C117 Legworker — UNIMPLEMENTED
# ═════════════════════════════════════════════════════════════════════
UNIMPLEMENTED["C117"] = (
    "'action space orthogonally adjacent to another occupied space' "
    "requires a board layout for action spaces; unlike farmyard cells "
    "(orthogonal_neighbors), action spaces carry no position/adjacency "
    "data in this engine")

# ═════════════════════════════════════════════════════════════════════
# C120 Agricultural Labourer — UNIMPLEMENTED
# ═════════════════════════════════════════════════════════════════════
UNIMPLEMENTED["C120"] = (
    "'for each grain you obtain, get 1 clay from this card' needs a "
    "generic 'resource gained' event; grain enters a player's supply "
    "through many different, not-unified sources (grain_seeds, harvest, "
    "other cards' conversions/hooks), so this can't be tracked "
    "comprehensively without a new engine-wide hook")

# ═════════════════════════════════════════════════════════════════════
# C124 Stone Importer
# "In the breeding phase of the 1st/2nd/3rd/4th/5th/6th harvest, you can
# use this card once to buy 2 stone for 2/2/3/3/4/1 food."
# (No hook fires during the automatic breeding phase; the feeding phase
# immediately precedes it and is the closest available card_action
# window, so the ability is offered there instead, once per harvest.)
# ═════════════════════════════════════════════════════════════════════
_STONE_IMPORTER_COST = {1: 2, 2: 2, 3: 3, 4: 3, 5: 4, 6: 1}


def _stone_importer_available(state, player, inst):
    h = state["harvest_index"]
    cost = _STONE_IMPORTER_COST.get(h)
    return (cost is not None and inst["data"].get("used_harvest") != h
            and player["resources"]["food"] >= cost)


def _stone_importer_apply(state, player, inst, ctx):
    h = state["harvest_index"]
    cost = _STONE_IMPORTER_COST[h]
    player["resources"]["food"] -= cost
    player["resources"]["stone"] += 2
    inst["data"]["used_harvest"] = h
    ctx["log"].append(f"{player['name']}'s Stone Importer buys 2 stone for "
                      f"{cost} food")


compendium_card("C124", card_action={
    "available": _stone_importer_available, "apply": _stone_importer_apply,
    "description": "Stone Importer: buy 2 stone"})

# ═════════════════════════════════════════════════════════════════════
# C125 Nightworker — UNIMPLEMENTED
# ═════════════════════════════════════════════════════════════════════
UNIMPLEMENTED["C125"] = (
    "an extra person placement before the normal work phase begins is a "
    "guest-token/extra-people style mechanic, explicitly out of scope "
    "for this engine's turn structure")

# ═════════════════════════════════════════════════════════════════════
# C127 Lover — min 3 players
# "When you play this card, immediately pay an amount of food equal to
# the number of complete rounds left to play to take a 'Family Growth
# Even without Room' action."
# (The food cost here is the number of *remaining* rounds -- a value that
# varies with state["round"] and can't be expressed as this engine's
# static cost dict or a static prereq predicate, so the "can't afford it"
# guard still has to live in the play hook itself, raising like any other
# invalid-action check in this codebase; apply_action's deepcopy-at-entry
# makes that a clean rollback. This is unrelated to occupation prereqs,
# which _play_occupation does now enforce via check_prereq, same as
# _play_minor.)
# ═════════════════════════════════════════════════════════════════════
def _lover_play(state, player, inst, ctx):
    from server.agricola.state import MAX_PEOPLE
    remaining = TOTAL_ROUNDS - state["round"]
    if player["people_total"] >= MAX_PEOPLE:
        raise ValueError("Lover: family is already at maximum size")
    if player["resources"]["food"] < remaining:
        raise ValueError(f"Lover: not enough food ({remaining} needed)")
    player["resources"]["food"] -= remaining
    player["people_total"] += 1
    player["newborns"] += 1
    ctx["log"].append(f"{player['name']}'s Lover grows the family for "
                      f"{remaining} food")
    fire_player(state, player, "family_growth", ctx)


compendium_card("C127", hooks={"play": _lover_play})

# ═════════════════════════════════════════════════════════════════════
# C128 Wooden Hut Extender — min 3 players
# "Wood rooms now cost you 1 reed, and additionally 5 wood through round
# 5, 4 wood in rounds 6 and 7, and 3 wood in round 8 and later."
# ═════════════════════════════════════════════════════════════════════
def _wooden_hut_extender_mod(state, player, kind, cost, ctx):
    if kind == "room" and player["house_type"] == "wood" and cost.get("wood"):
        cost = dict(cost)
        n = ctx.get("count", 1)
        rnd = state["round"]
        cost["wood"] = (5 if rnd <= 5 else 4 if rnd <= 7 else 3) * n
        cost["reed"] = 1 * n
    return cost


compendium_card("C128", cost_mod=_wooden_hut_extender_mod)

# ═════════════════════════════════════════════════════════════════════
# C129 Second Spouse — UNIMPLEMENTED
# ═════════════════════════════════════════════════════════════════════
UNIMPLEMENTED["C129"] = (
    "using the 'Family Growth Even without Room' space even when already "
    "occupied is placing on an occupied space, explicitly out of scope")

# ═════════════════════════════════════════════════════════════════════
# C130 Outskirts Director — UNIMPLEMENTED
# ═════════════════════════════════════════════════════════════════════
UNIMPLEMENTED["C130"] = (
    "same extra-placement mechanic as Inner Districts Director (C093): "
    "the engine only supports double placement through the Lasso's "
    "hardcoded animal-market case")

# ═════════════════════════════════════════════════════════════════════
# C131 Private Teacher — UNIMPLEMENTED
# ═════════════════════════════════════════════════════════════════════
UNIMPLEMENTED["C131"] = (
    "'you can also play an occupation for 1 food' triggered off using "
    "the Take 1 Grain space would require duplicating _play_occupation's "
    "internals inside a card hook, same concern as Seed Researcher (C097)")

# ═════════════════════════════════════════════════════════════════════
# C132 Timber Shingle Maker — min 3 players
# "When you renovate to stone, you can place up to 1 wood from your
# supply in each of your rooms. During scoring, each such wood is worth
# 1 bonus point."
# ═════════════════════════════════════════════════════════════════════
def _timber_shingle_maker_renovate(state, player, inst, ctx):
    if player["house_type"] != "stone":
        return
    rooms = sum(1 for c in player["cells"] if c["type"] == "room")
    limit = min(rooms, player["resources"]["wood"])
    if limit <= 0:
        return
    options = [f"{n} wood" for n in range(limit + 1)]
    prompt_choice(state, player, inst["id"],
                 "Timber Shingle Maker: place how much wood in your rooms?",
                 options)


def _timber_shingle_maker_choice(state, player, inst, ctx):
    n = ctx["index"]
    if n <= 0:
        return
    n = min(n, player["resources"]["wood"])
    player["resources"]["wood"] -= n
    inst["data"]["wood"] = inst["data"].get("wood", 0) + n
    ctx["log"].append(f"{player['name']}'s Timber Shingle Maker stores "
                      f"{n} wood ({n} future bonus points)")


compendium_card("C132", hooks={"renovate": _timber_shingle_maker_renovate},
                resolve_choice=_timber_shingle_maker_choice,
                score_bonus=lambda s, p, i: i["data"].get("wood", 0))

# ═════════════════════════════════════════════════════════════════════
# C133 Soldier — min 3 players
# "During scoring, you get 1 bonus point for each stone-wood pair in
# your supply."
# ═════════════════════════════════════════════════════════════════════
compendium_card("C133", score_bonus=lambda s, p, i: min(
    p["resources"]["stone"], p["resources"]["wood"]))

# ═════════════════════════════════════════════════════════════════════
# C134 Cow Prince — min 3 players
# "During scoring, you get 1 bonus point for each space in your farmyard
# (including rooms) holding at least 1 cattle."
# (Pets held via house_capacity aren't assigned to a specific room cell
# in this engine's data model — only cattle placed on farmyard cells are
# counted here; cattle kept purely as house pets, e.g. via Animal Tamer,
# are not counted toward this bonus.)
# (The trailing "(3-5 players) If there are still 1/3/6/9 rounds
# left..." clause is verbatim House Steward's play effect from
# cards.py — a DB cross-contamination artifact — and is not
# implemented here.)
# ═════════════════════════════════════════════════════════════════════
def _cow_prince_score(state, player, inst):
    return sum(1 for c in player["cells"]
              if c.get("animal") and c["animal"]["type"] == "cattle"
              and c["animal"]["count"] >= 1)


compendium_card("C134", score_bonus=_cow_prince_score)

# ═════════════════════════════════════════════════════════════════════
# C136 Ranch Provost — min 3 players
# "If there are still 3/6/9 complete rounds left to play, you
# immediately get 2/3/4 wood. During scoring, each player with a pasture
# of highest capacity gets 3 bonus points."
# ═════════════════════════════════════════════════════════════════════
def _ranch_provost_play(state, player, inst, ctx):
    remaining = TOTAL_ROUNDS - state["round"]
    wood = 4 if remaining >= 9 else 3 if remaining >= 6 else \
        2 if remaining >= 3 else 0
    if wood:
        player["resources"]["wood"] += wood
        ctx["log"].append(f"{player['name']}'s Ranch Provost grants {wood} wood")


def _pasture_best_capacity(state, player):
    from server.agricola import cards as _cards
    pastures = compute_pastures(player)
    if not pastures:
        return 0
    # No single animal type occupies every pasture, so this asks each
    # pasture's capacity type-agnostically (animal_type=None); a
    # type-conditioned pasture_capacity_mod simply doesn't apply here,
    # same as it wouldn't for an actually-empty pasture.
    return max(_cards.pasture_capacity(state, player, pa, None) for pa in pastures)


def _ranch_provost_score(state, player, inst):
    mine = _pasture_best_capacity(state, player)
    if mine <= 0:
        return 0
    best = max(_pasture_best_capacity(state, p) for p in state["players"])
    return 3 if mine == best else 0


compendium_card("C136", hooks={"play": _ranch_provost_play},
                score_bonus=_ranch_provost_score)

# ═════════════════════════════════════════════════════════════════════
# C137 Charcoal Burner — min 3 players
# "You receive 1 Food and 1 Wood whenever any player (including you)
# builds a Baking Improvement with a bread symbol."
# (Clay Oven and Stone Oven are this engine's "bake_on_build" majors.)
# ═════════════════════════════════════════════════════════════════════
def _charcoal_burner_improvement_built(state, player, inst, ctx):
    if ctx["improvement"] in ("clay_oven", "stone_oven"):
        player["resources"]["food"] += 1
        player["resources"]["wood"] += 1
        ctx["log"].append(f"{player['name']}'s Charcoal Burner grants "
                          "1 food and 1 wood")


compendium_card("C137", hooks={"improvement_built": _charcoal_burner_improvement_built})

# ═════════════════════════════════════════════════════════════════════
# C138 Animal Feeder — min 3 players
# "On the 'Day Laborer' action space, you also get your choice of 1
# sheep or 1 grain. Instead of that good, you can buy 1 wild boar for 1
# food or 1 cattle for 2 food."
# ═════════════════════════════════════════════════════════════════════
def _animal_feeder_space_used(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or ctx["space_id"] != "day_laborer":
        return
    options = ["1 sheep", "1 grain"]
    if player["resources"]["food"] >= 1:
        options.append("1 wild boar (pay 1 food)")
    if player["resources"]["food"] >= 2:
        options.append("1 cattle (pay 2 food)")
    prompt_choice(state, player, inst["id"], "Animal Feeder: choose a bonus good",
                 options)


def _animal_feeder_choice(state, player, inst, ctx):
    label = ctx["option"]
    if label == "1 sheep":
        add_goods(ctx["extra"], {"sheep": 1})
    elif label == "1 grain":
        player["resources"]["grain"] += 1
    elif "wild boar" in label:
        player["resources"]["food"] -= 1
        add_goods(ctx["extra"], {"boar": 1})
    else:
        player["resources"]["food"] -= 2
        add_goods(ctx["extra"], {"cattle": 1})
    ctx["log"].append(f"{player['name']}'s Animal Feeder grants {label}")


compendium_card("C138", hooks={"space_used": _animal_feeder_space_used},
                resolve_choice=_animal_feeder_choice)

# ═════════════════════════════════════════════════════════════════════
# C139 Basketmaker's Wife — min 3 players
# "When you play this card, you immediately get 1 reed and 1 food. At
# any time, you can turn 1 reed into 2 food."
# ═════════════════════════════════════════════════════════════════════
compendium_card("C139", hooks={"play": on_play_gain({"reed": 1, "food": 1})},
                conversions=[{"give": {"reed": 1}, "get": {"food": 2}}])

# ═════════════════════════════════════════════════════════════════════
# C140 Packaging Artist — UNIMPLEMENTED
# ═════════════════════════════════════════════════════════════════════
UNIMPLEMENTED["C140"] = (
    "'each time you get a Minor Improvement action, take a Bake Bread "
    "action instead' needs bake_on_spaces wired to the major_improvement "
    "space, but that space's resolver never checks action.get('bake') "
    "(only farmland/cultivation do)")

# ═════════════════════════════════════════════════════════════════════
# C141 Sheep Provider — min 3 players
# "Each time any player (including you) uses the 'Take 1 Sheep'
# accumulation space, you get 1 grain."
# (Trailing clause repeats this entry's own "(3-5 players)" band mid-
# text — a DB merge artifact — and is not implemented.)
# ═════════════════════════════════════════════════════════════════════
compendium_card("C141", hooks=space_bonus(["sheep_market"], {"grain": 1}, others=True))

# ═════════════════════════════════════════════════════════════════════
# C143 Stone Buyer — min 3 players
# "When you play this card, you can immediately buy exactly 2 stone for
# 1 food. From the next round on, once per round, you can buy 1 stone
# for 2 food."
# (Trailing clause repeats this entry's own "(3-5 players)" band mid-
# text — a DB merge artifact — and is not implemented.)
# ═════════════════════════════════════════════════════════════════════
def _stone_buyer_play(state, player, inst, ctx):
    if player["resources"]["food"] >= 1:
        prompt_choice(state, player, inst["id"],
                     "Stone Buyer: buy 2 stone for 1 food?", ["Yes", "No"])


def _stone_buyer_choice(state, player, inst, ctx):
    if ctx["index"] == 0 and player["resources"]["food"] >= 1:
        player["resources"]["food"] -= 1
        player["resources"]["stone"] += 2
        ctx["log"].append(f"{player['name']}'s Stone Buyer buys 2 stone")


def _stone_buyer_available(state, player, inst):
    return (inst["data"].get("used_round") != state["round"]
            and player["resources"]["food"] >= 2)


def _stone_buyer_apply(state, player, inst, ctx):
    player["resources"]["food"] -= 2
    player["resources"]["stone"] += 1
    inst["data"]["used_round"] = state["round"]
    ctx["log"].append(f"{player['name']}'s Stone Buyer buys 1 stone")


compendium_card("C143", hooks={"play": _stone_buyer_play},
                resolve_choice=_stone_buyer_choice,
                card_action={"available": _stone_buyer_available,
                             "apply": _stone_buyer_apply,
                             "description": "Stone Buyer: buy 1 stone for 2 food"})

# ═════════════════════════════════════════════════════════════════════
# C145 Forest Reviewer — min 3 players
# "Each time after any player (including you) uses the unoccupied
# '+2/+2/+4 Wood' or 'Take 3 Wood' accumulation space (in a 3/4/5 player
# game) while the other of the two is occupied, you get 1 reed."
# ═════════════════════════════════════════════════════════════════════
def _forest_reviewer_space_used(state, player, inst, ctx):
    if ctx["space_id"] not in WOOD_SPACES:
        return
    others_occupied = any(
        sp["id"] in WOOD_SPACES and sp["id"] != ctx["space_id"]
        and sp["occupied_by"] is not None
        for sp in state["action_spaces"])
    if others_occupied:
        player["resources"]["reed"] += 1
        ctx["log"].append(f"{player['name']}'s Forest Reviewer grants 1 reed")


compendium_card("C145", hooks={"space_used": _forest_reviewer_space_used})

# ═════════════════════════════════════════════════════════════════════
# C146 Workshop Assistant — UNIMPLEMENTED
# ═════════════════════════════════════════════════════════════════════
UNIMPLEMENTED["C146"] = (
    "'place a pair of resources on each of your improvements' needs "
    "per-improvement mutable storage; player['improvements'] is a bare "
    "list of improvement ids with no attached data slot for stored goods")

# ═════════════════════════════════════════════════════════════════════
# C148 Mud Wallower — min 4 players
# ═════════════════════════════════════════════════════════════════════
UNIMPLEMENTED["C148"] = (
    "'exchange 4 clay on this card for 1 wild boar, held by this card' "
    "needs animals stored outside cells/pets that still participate in "
    "breeding and scoring; animal_counts/animal_totals_of and the "
    "breeding phase only read cells and pets")

# ═════════════════════════════════════════════════════════════════════
# C149 Resource Recycler — UNIMPLEMENTED
# ═════════════════════════════════════════════════════════════════════
UNIMPLEMENTED["C149"] = (
    "'each time another player renovates to stone...' can't be observed: "
    "the renovate event only fires to the renovating player's own cards "
    "(fire_player), it is never broadcast to other players")

# ═════════════════════════════════════════════════════════════════════
# C150 Parrot Breeder — UNIMPLEMENTED
# ═════════════════════════════════════════════════════════════════════
UNIMPLEMENTED["C150"] = (
    "'use the same action space the player to your right just used' is a "
    "placing-on-an-occupied-space / replacement mechanic, explicitly out "
    "of scope")

# ═════════════════════════════════════════════════════════════════════
# C151 Sowing Director — min 4 players
# "Each time after another player uses the 'Sow and/or Bake Bread'
# action space, you get a 'Sow' action."
# ═════════════════════════════════════════════════════════════════════
def _sowing_director_space_used(state, player, inst, ctx):
    if ctx["space_id"] == "grain_utilization" and ctx["actor"] != player["index"]:
        inst["data"]["free_sow"] = inst["data"].get("free_sow", 0) + 1


def _sowing_director_available(state, player, inst):
    if inst["data"].get("free_sow", 0) <= 0:
        return False
    has_crop = player["resources"]["grain"] > 0 or player["resources"]["vegetable"] > 0
    empty_cells = any(c["type"] == "field" and not c["crops"] for c in player["cells"])
    empty_cards = any(not i["crops"] for i in card_fields(player))
    return has_crop and (empty_cells or empty_cards)


def _sowing_director_apply(state, player, inst, ctx):
    _sow_one(state, player, ctx.get("params") or {}, ctx["log"], "Sowing Director")
    inst["data"]["free_sow"] -= 1


compendium_card("C151", hooks={"space_used": _sowing_director_space_used},
                card_action={"available": _sowing_director_available,
                             "apply": _sowing_director_apply,
                             "description": "Sowing Director: free Sow action"})

# ═════════════════════════════════════════════════════════════════════
# C152 Puppeteer — UNIMPLEMENTED
# ═════════════════════════════════════════════════════════════════════
UNIMPLEMENTED["C152"] = (
    "'pay another player 1 food to play an occupation without paying an "
    "occupation cost' needs the same _play_occupation duplication as "
    "Seed Researcher (C097) / Private Teacher (C131)")

# ═════════════════════════════════════════════════════════════════════
# C154 Twin Researcher — min 4 players
# "Each time you use one of two accumulation spaces for the same type of
# good containing exactly the same number of goods, you can also buy 1
# bonus point for 1 food."
# ═════════════════════════════════════════════════════════════════════
def _twin_researcher_space_used(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or not ctx["goods"]:
        return
    good, amount = next(iter(ctx["goods"].items()))
    siblings = [sp for sp in state["action_spaces"]
               if sp["accumulates"] and sp["id"] != ctx["space_id"]
               and good in sp["supply"]]
    if len(siblings) != 1:
        return  # only a clean "pair" of spaces for this good is unambiguous
    if siblings[0]["supply"].get(good, 0) == amount:
        inst["data"]["available"] = True


def _twin_researcher_available(state, player, inst):
    return inst["data"].get("available") and player["resources"]["food"] >= 1


def _twin_researcher_apply(state, player, inst, ctx):
    player["resources"]["food"] -= 1
    inst["data"]["points"] = inst["data"].get("points", 0) + 1
    inst["data"]["available"] = False
    ctx["log"].append(f"{player['name']}'s Twin Researcher buys 1 bonus point")


compendium_card("C154", hooks={"space_used": _twin_researcher_space_used},
                card_action={"available": _twin_researcher_available,
                             "apply": _twin_researcher_apply,
                             "description": "Twin Researcher: 1 food -> 1 bonus point"},
                score_bonus=lambda s, p, i: i["data"].get("points", 0))

# ═════════════════════════════════════════════════════════════════════
# C155 Food Distributor — UNIMPLEMENTED
# ═════════════════════════════════════════════════════════════════════
UNIMPLEMENTED["C155"] = (
    "'at the start of this returning home phase' has no corresponding "
    "hook on non-harvest rounds: _end_work_phase fires nothing, and by "
    "the next round_start the occupied_by flags have already been reset "
    "to None, so the occupied-space count at that moment is unrecoverable")

# ═════════════════════════════════════════════════════════════════════
# C156 Hoof Caregiver — min 4 players
# "Immediately add 1 cattle from the general supply to the 'Take 1
# Cattle' accumulation space. Afterward, you get 1 grain plus 1 food for
# each cattle on the 'Take 1 Cattle' space."
# ═════════════════════════════════════════════════════════════════════
def _hoof_caregiver_play(state, player, inst, ctx):
    space = next((s for s in state["action_spaces"] if s["id"] == "cattle_market"),
                 None)
    if space is None:
        return
    space["supply"]["cattle"] = space["supply"].get("cattle", 0) + 1
    count = space["supply"]["cattle"]
    player["resources"]["grain"] += 1
    player["resources"]["food"] += count
    ctx["log"].append(f"{player['name']}'s Hoof Caregiver adds 1 cattle to "
                      f"Cattle Market and grants 1 grain + {count} food")


compendium_card("C156", hooks={"play": _hoof_caregiver_play})

# ═════════════════════════════════════════════════════════════════════
# C157 Resource Analyzer — min 4 players
# "Before the start of each round, if you have more building resources
# than all players of at least two types, you get 1 food."
# ═════════════════════════════════════════════════════════════════════
def _resource_analyzer_round_start(state, player, inst, ctx):
    leads = 0
    for good in BUILDING_RESOURCES:
        mine = player["resources"][good]
        if mine > 0 and all(mine > other["resources"][good]
                            for other in state["players"] if other is not player):
            leads += 1
    if leads >= 2:
        player["resources"]["food"] += 1
        ctx["log"].append(f"{player['name']}'s Resource Analyzer grants 1 food")


compendium_card("C157", hooks={"round_start": _resource_analyzer_round_start})

# ═════════════════════════════════════════════════════════════════════
# C158 Forest Campaigner — min 4 players
# "Each time before you place a person, if there are at least 8 wood
# total on accumulation spaces, you get 1 food."
# ═════════════════════════════════════════════════════════════════════
def _forest_campaigner_space_used(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    total = sum(sp["supply"].get("wood", 0) for sp in state["action_spaces"])
    total += ctx["goods"].get("wood", 0)  # reconstruct the pre-placement total
    if total >= 8:
        player["resources"]["food"] += 1
        ctx["log"].append(f"{player['name']}'s Forest Campaigner grants 1 food")


compendium_card("C158", hooks={"space_used": _forest_campaigner_space_used})

# ═════════════════════════════════════════════════════════════════════
# C159 Fisherman's Friend — min 4 players
# "At the start of each round, if there is more food on the 'Traveling
# Players' than on the 'Fishing' accumulation space, you get the
# difference from the general supply."
# ═════════════════════════════════════════════════════════════════════
def _fishermans_friend_round_start(state, player, inst, ctx):
    tp = next((s for s in state["action_spaces"] if s["id"] == "traveling_players"),
             None)
    fishing = next((s for s in state["action_spaces"] if s["id"] == "fishing"), None)
    if tp is None or fishing is None:
        return
    diff = tp["supply"].get("food", 0) - fishing["supply"].get("food", 0)
    if diff > 0:
        player["resources"]["food"] += diff
        ctx["log"].append(f"{player['name']}'s Fisherman's Friend grants {diff} food")


compendium_card("C159", hooks={"round_start": _fishermans_friend_round_start})

# ═════════════════════════════════════════════════════════════════════
# C160 Outrider — min 4 players
# "Each time before you use the action space on the most recently
# revealed action space card (after it has been placed on the round
# space), you get 1 grain."
# ═════════════════════════════════════════════════════════════════════
def _outrider_space_used(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or not state["revealed"]:
        return
    if ctx["space_id"] == state["revealed"][-1]:
        player["resources"]["grain"] += 1
        ctx["log"].append(f"{player['name']}'s Outrider grants 1 grain")


compendium_card("C160", hooks={"space_used": _outrider_space_used})

# ═════════════════════════════════════════════════════════════════════
# C161 Potato Digger — min 4 players
# "When you play this card, if you have at least 2/4/5 unplanted field
# tiles, you immediately get 1/2/3 vegetables."
# ═════════════════════════════════════════════════════════════════════
def _potato_digger_play(state, player, inst, ctx):
    unplanted = sum(1 for c in player["cells"]
                    if c["type"] == "field" and not c["crops"])
    gain = 3 if unplanted >= 5 else 2 if unplanted >= 4 else 1 if unplanted >= 2 else 0
    if gain:
        player["resources"]["vegetable"] += gain
        ctx["log"].append(f"{player['name']}'s Potato Digger grants {gain} vegetable(s)")


compendium_card("C161", hooks={"play": _potato_digger_play})

# ═════════════════════════════════════════════════════════════════════
# C162 Forest Owner — UNIMPLEMENTED
# ═════════════════════════════════════════════════════════════════════
UNIMPLEMENTED["C162"] = (
    "'this card is an action space for all' — cards can't be placement "
    "targets; action spaces are only entries in state['action_spaces']")

# ═════════════════════════════════════════════════════════════════════
# C163 Material Deliveryman — min 4 players
# "Each time any player takes 5/6/7/8+ goods from an accumulation space,
# you get 1 wood/clay/reed/stone from the general supply."
# ═════════════════════════════════════════════════════════════════════
def _material_deliveryman_space_used(state, player, inst, ctx):
    total = sum(ctx["goods"].values())
    if total >= 8:
        good = "stone"
    elif total >= 7:
        good = "reed"
    elif total >= 6:
        good = "clay"
    elif total >= 5:
        good = "wood"
    else:
        return
    player["resources"][good] += 1
    ctx["log"].append(f"{player['name']}'s Material Deliveryman grants 1 {good}")


compendium_card("C163", hooks={"space_used": _material_deliveryman_space_used})

# ═════════════════════════════════════════════════════════════════════
# C164 German Heath Keeper — min 4 players
# "Each time any player (including you) uses the 'Take 1 Wild Boar'
# accumulation space, you get 1 sheep from the general supply."
# (Predates space_bonus(others=True) being animal-safe -- the factory
# now routes "other player" animal gains through accommodation via
# cards.grant_goods(), same as this hand-rolled version below.)
# ═════════════════════════════════════════════════════════════════════
def _german_heath_keeper_space_used(state, player, inst, ctx):
    if ctx["space_id"] != "pig_market":
        return
    if ctx["actor"] == player["index"]:
        add_goods(ctx["extra"], {"sheep": 1})
    else:
        _grant_animal_to_owner(state, player, "sheep", 1)
    ctx["log"].append(f"{player['name']}'s German Heath Keeper grants 1 sheep")


compendium_card("C164", hooks={"space_used": _german_heath_keeper_space_used})

# ═════════════════════════════════════════════════════════════════════
# C165 Game Catcher — min 4 players
# "When you play this card, pay 1 food for each remaining harvest to
# immediately get 1 cattle and 1 wild boar."
# (Trailing rulings text describes an unrelated card-forced-play edge
# case (Paper Knife) not present in this engine, plus further merged
# clauses; the primary effect above is implemented.)
# ═════════════════════════════════════════════════════════════════════
def _game_catcher_remaining(state):
    return sum(1 for h in HARVEST_ROUNDS if h >= state["round"])


def _game_catcher_play(state, player, inst, ctx):
    # (See the C127 Lover note: this is a round-dependent cost, not a
    # static prereq, so the affordability check still has to raise from
    # inside the play hook.)
    cost = _game_catcher_remaining(state)
    if player["resources"]["food"] < cost:
        raise ValueError(f"Game Catcher: not enough food ({cost} needed)")
    player["resources"]["food"] -= cost
    add_goods(ctx["extra"], {"cattle": 1, "boar": 1})
    ctx["log"].append(f"{player['name']}'s Game Catcher pays {cost} food for "
                      "1 cattle and 1 wild boar")


compendium_card("C165", hooks={"play": _game_catcher_play})

# ═════════════════════════════════════════════════════════════════════
# C168 Animal Catcher — min 4 players
# "Each time you use the 'Day Laborer' action space, instead of 2 food,
# you can get 3 different animals from the general supply. If you do,
# you must pay 1 food for each harvest left to play."
# (This engine has exactly 3 animal types (sheep/boar/cattle, no
# horses), so "3 different animals" is unambiguously 1 of each. The
# remaining rulings text is a large, unrelated concatenation of "(5-6
# players)"-tagged effects beyond this engine's 4-player cap and is not
# implemented.)
# ═════════════════════════════════════════════════════════════════════
def _animal_catcher_space_used(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or ctx["space_id"] != "day_laborer":
        return
    cost = _game_catcher_remaining(state)
    if player["resources"]["food"] >= cost + 2:  # already has the default 2 food
        prompt_choice(state, player, inst["id"],
                     f"Animal Catcher: swap your 2 food for 1 each of sheep, "
                     f"wild boar, and cattle (pay {cost} food)?", ["Yes", "No"],
                     data={"cost": cost})


def _animal_catcher_choice(state, player, inst, ctx):
    if ctx["index"] != 0:
        return
    cost = ctx["data"]["cost"]
    player["resources"]["food"] -= (2 + cost)
    add_goods(ctx["extra"], {"sheep": 1, "boar": 1, "cattle": 1})
    ctx["log"].append(f"{player['name']}'s Animal Catcher trades the 2 food for "
                      f"1 sheep, 1 wild boar, and 1 cattle (paying {cost} food)")


compendium_card("C168", hooks={"space_used": _animal_catcher_space_used},
                resolve_choice=_animal_catcher_choice)
