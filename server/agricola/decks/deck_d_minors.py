"""Deck D minor improvements (codes D001-D083 from the compendium DB).

Card DB quirk: several entries in compendium_cards.json concatenate the
text/rulings of more than one physical printing under a single code (the
General Compendium groups variants by slot number; there is no separate
`deck`/code per printing). Where a "(Cost X. Req Y.)" marker splits the
text into multiple sub-effects, only the FIRST one -- the one matching
this entry's top-level cost/vp/prereq fields -- is implemented here. The
remaining concatenated text describes a different printing of the same
slot and is out of scope for this registration.
"""

from server.agricola import cards
from server.agricola.cards import (
    compendium_card, add_goods, goods_str, prompt_choice,
    needs_occupations, combine, harvest_food, round_income, on_play_gain,
    animal_totals_of,
)
from server.agricola.state import (
    NUM_CELLS, TOTAL_ROUNDS, HARVEST_ROUNDS, FIREPLACES, MAJOR_IMPROVEMENTS,
    orthogonal_neighbors, compute_pastures, plowable_cells,
)

UNIMPLEMENTED = {
    "D011": "static per-pasture-size capacity (size-1 pastures hold 3, or "
            "6 with a stable) -- pasture_capacity_bonus is one flat "
            "additive value applied to every pasture regardless of its "
            "size, so a size-conditioned bonus can't be expressed",
    "D021": "lets you take a Family Growth action in place of a Minor "
            "Improvement action's usual effect -- the engine's per-space "
            "handlers (_resolve_space) don't expose a substitution point "
            "for card effects; adding one means editing engine.py",
    "D022": "places a person on a future round space ahead of normal turn "
            "order -- an out-of-turn/extra placement, the same class as "
            "guest tokens (not supported)",
    "D023": "is its own private action space with round-dependent effects "
            "-- engine.py dispatches action spaces by a fixed id set "
            "(_resolve_space); a new space type needs an engine change",
    "D024": "double placement without the Lasso's animal-market "
            "restriction -- the lasso flag is hardcoded in _apply_place "
            "to require a sheep/pig/cattle market; lifting that needs an "
            "engine change",
    "D025": "counts as a field, occupation, minor, or major improvement, "
            "'whichever is most convenient' -- a card instance's category "
            "(occupations[] vs. minors[] vs. field) is fixed at play time; "
            "there's no dynamic reclassification hook",
    "D026": "builds two majors in one action, or builds specific majors "
            "via the Minor-Improvement action slot -- _do_improvement "
            "only builds one major per major-improvement action",
    "D036": "requires tracking, over the whole game, how many sheep were "
            "bred vs. gained from other sources and whether any were ever "
            "cooked -- breeding (_finish_harvest) fires no card hook at "
            "all, so bred sheep can't be distinguished from other gains",
    "D051": "a shared action space with its own effect (bonus food, then "
            "use another unoccupied space with that person) -- requires a "
            "new action-space type engine.py doesn't dispatch",
    "D053": "skip your 2nd placement now and use it later in the same "
            "round -- a turn-order deferral the placement flow "
            "(_advance_work/_apply_place) doesn't support",
    "D056": "no hook fires during feeding-phase cook conversions "
            "(_apply_feed resolves them inline); modeling it as an extra "
            "cook-table entry would also be wrong for owners of a better "
            "cooking improvement, since cook tables merge via max, not "
            "additively",
    "D065": "primary effect needs to know how much grain was harvested "
            "this harvest; harvest_field's ctx only carries harvest_index "
            "(no yield amounts), and the field counts are already "
            "decremented by the time the hook fires",
    "D070": "needs to add crops to fields before the harvest's field-phase "
            "deduction; harvest_field only fires after that deduction, so "
            "there's no hook point early enough",
    "D074": "needs to know how much wood was spent building rooms/"
            "stables/improvements this turn; space_used's ctx reports "
            "goods the space granted, not resources the player paid",
    "D077": "reacts to ANY player's renovation, but the renovate event "
            "only fires for the acting player's own cards (_do_renovate "
            "calls cards.fire_player, not cards.fire) -- widening that "
            "means editing engine.py",
    "D082": "the discount is conditioned on building via House/Farm "
            "Redevelopment specifically, but cost_mod's ctx doesn't carry "
            "which action space triggered the build -- Redevelopment "
            "spaces reuse the same kind='improvement'/'fences' calls as "
            "the dedicated Major-Improvement/Fencing spaces",
    "D083": "would need to grant an animal through a feeding-phase "
            "conversion, but the engine's conversion 'get' path only "
            "credits player resources with no accommodation route for "
            "animal types -- crediting an animal this way would corrupt "
            "state instead of actually placing it",
}

# ── Shared local helpers ──────────────────────────────────────────────


def _planted_field_count(player):
    """Fields (cells or card fields) currently growing any crop."""
    n = sum(1 for c in player["cells"] if c["crops"])
    n += sum(1 for i in cards.card_fields(player) if i["crops"])
    return n


def _grain_field_count(player):
    n = sum(1 for c in player["cells"]
            if c["crops"] and c["crops"]["type"] == "grain")
    n += sum(1 for i in cards.card_fields(player)
             if i["crops"] and i["crops"]["type"] == "grain")
    return n


def _vegetable_field_count(player):
    n = sum(1 for c in player["cells"]
            if c["crops"] and c["crops"]["type"] == "vegetable")
    n += sum(1 for i in cards.card_fields(player)
             if i["crops"] and i["crops"]["type"] == "vegetable")
    return n


def _empty_field_cells(player):
    return [i for i, c in enumerate(player["cells"])
            if c["type"] == "field" and not c["crops"]]


def _field_tile_count(player):
    return sum(1 for c in player["cells"] if c["type"] == "field")


def _needs_planted_fields(n):
    return (lambda s, p: _planted_field_count(p) >= n,
            f"{n} planted field(s)")


def _needs_fields(n):
    return (lambda s, p: _field_tile_count(p) >= n, f"{n} field(s)")


def _at_most_occupations(n):
    return (lambda s, p: len(p["occupations"]) <= n,
            f"at most {n} occupation(s)")


def _unfenced_stables(player):
    pasture_cells = {i for pas in compute_pastures(player) for i in pas}
    return sum(1 for i, c in enumerate(player["cells"])
               if c["stable"] and i not in pasture_cells)


def _room_cells(player):
    return {i for i, c in enumerate(player["cells"]) if c["type"] == "room"}


def _buildable_room_cells(player):
    """Local replica of the engine's room-placement legality check (empty,
    not a stable/pasture cell, orthogonally adjacent to an existing room)."""
    pasture_cells = {i for pas in compute_pastures(player) for i in pas}
    rooms = _room_cells(player)
    out = []
    for i, c in enumerate(player["cells"]):
        if i in rooms or c["type"] != "empty" or c["stable"] \
                or i in pasture_cells:
            continue
        if any(nb in rooms for nb in orthogonal_neighbors(i)):
            out.append(i)
    return out


def _schedule_good(state, player, good, rounds, amount=1):
    for r in rounds:
        slot = state["round_goods"].setdefault(str(r), {}) \
            .setdefault(str(player["index"]), {})
        add_goods(slot, {good: amount})


# ── D001 Zigzag Harrow ─────────────────────────────────────────────────
# Cost 1W. Req 3 fields in an L shape. Immediately plow 1 field completing
# a "zigzag" pattern; the general plow-adjacency rule already enforces
# that the new field touches the existing ones. Traveling (auto-detected).

def _l_shape_fields(player):
    fields = {i for i, c in enumerate(player["cells"]) if c["type"] == "field"}
    for pivot in fields:
        row_nb = any(nb // 5 == pivot // 5 and nb in fields
                     for nb in orthogonal_neighbors(pivot))
        col_nb = any(nb % 5 == pivot % 5 and nb in fields
                     for nb in orthogonal_neighbors(pivot))
        if row_nb and col_nb:
            return True
    return False


ZIGZAG_HARROW_PREREQ = (lambda s, p: _l_shape_fields(p),
                        "3 fields in an L shape")


def _zigzag_harrow_play(state, player, inst, ctx):
    cell = (ctx.get("params") or {}).get("cell")
    if cell is None:
        return  # optional: "you CAN immediately plow"
    if not isinstance(cell, int) or cell not in plowable_cells(player):
        raise ValueError("Zigzag Harrow: choose a valid field (params.cell)")
    player["cells"][cell]["type"] = "field"
    ctx["log"].append(f"{player['name']} plows a field (Zigzag Harrow)")


compendium_card("D001", prereq=ZIGZAG_HARROW_PREREQ,
                hooks={"play": _zigzag_harrow_play})

# ── D002 Dwelling Plan ───────────────────────────────────────────────
# Cost 1F. Immediately take a Renovation action (optional). Traveling.

ROOM_COST_MATERIAL = {"wood": "wood", "clay": "clay", "stone": "stone"}
RENOVATION_TARGET = {"wood": "clay", "clay": "stone"}


def _inline_renovate(state, player, ctx):
    target = RENOVATION_TARGET.get(player["house_type"])
    if not target:
        raise ValueError("Your house is already stone")
    rooms = len(_room_cells(player))
    cost = cards.modified_cost(state, player, "renovation",
                               {target: rooms, "reed": 1})
    for res, amt in cost.items():
        if player["resources"][res] < amt:
            raise ValueError(f"Not enough {res} to renovate")
    for res, amt in cost.items():
        player["resources"][res] -= amt
    player["house_type"] = target
    ctx["log"].append(f"{player['name']} renovates to a {target} house")
    inner = {"free_stable_cell": None, "log": ctx["log"],
             "actor": player["index"], "extra": {}}
    cards.fire_player(state, player, "renovate", inner)
    add_goods(ctx["extra"], inner["extra"])


def _dwelling_plan_play(state, player, inst, ctx):
    if (ctx.get("params") or {}).get("renovate"):
        _inline_renovate(state, player, ctx)


compendium_card("D002", hooks={"play": _dwelling_plan_play})

# ── D003 Furrows ─────────────────────────────────────────────────────
# No cost. Immediately sow in exactly 1 field (optional). Traveling.


def _furrows_play(state, player, inst, ctx):
    params = ctx.get("params") or {}
    crop = params.get("crop")
    if crop is None:
        return
    if crop not in ("grain", "vegetable"):
        raise ValueError("Furrows: crop must be grain or vegetable")
    if player["resources"][crop] < 1:
        raise ValueError(f"Furrows: not enough {crop}")
    if "card" in params:
        cid = params["card"]
        target = next((i for i in cards.card_fields(player)
                       if i["id"] == cid), None)
        if target is None or target["crops"]:
            raise ValueError("Furrows: invalid card field")
        if crop not in cards.CARDS[cid]["field"]["crops"]:
            raise ValueError(f"Furrows: that field can't grow {crop}")
        target["crops"] = {"type": crop, "count": 3 if crop == "grain" else 2}
    else:
        cell = params.get("cell")
        if not isinstance(cell, int) or cell not in _empty_field_cells(player):
            raise ValueError("Furrows: choose an empty field (params.cell)")
        player["cells"][cell]["crops"] = {
            "type": crop, "count": 3 if crop == "grain" else 2}
    player["resources"][crop] -= 1
    ctx["log"].append(f"{player['name']} sows a field (Furrows)")


compendium_card("D003", hooks={"play": _furrows_play})

# ── D004 Cross-Cut Wood ────────────────────────────────────────────────
# Cost 1F. Req 3 occ. Get wood equal to stone in supply. Traveling.


def _crosscut_wood_play(state, player, inst, ctx):
    n = player["resources"]["stone"]
    if n:
        add_goods(ctx["extra"], {"wood": n})
        ctx["log"].append(f"{player['name']}'s Cross-Cut Wood grants {n} wood")


compendium_card("D004", prereq=needs_occupations(3),
                hooks={"play": _crosscut_wood_play})

# ── D005 Field Clay ────────────────────────────────────────────────────
# Cost 1F. Req 1 planted field. Get clay per planted field. Traveling.


def _field_clay_play(state, player, inst, ctx):
    n = _planted_field_count(player)
    if n:
        add_goods(ctx["extra"], {"clay": n})
        ctx["log"].append(f"{player['name']}'s Field Clay grants {n} clay")


compendium_card("D005", prereq=_needs_planted_fields(1),
                hooks={"play": _field_clay_play})

# ── D006 Petrified Wood ────────────────────────────────────────────────
# Req 2 occ. Immediately exchange up to 3 wood for 1 stone each. Traveling.


def _petrified_wood_play(state, player, inst, ctx):
    n = (ctx.get("params") or {}).get("wood", 0)
    if not isinstance(n, int) or n < 0:
        raise ValueError("Petrified Wood: invalid wood count")
    n = min(n, 3, player["resources"]["wood"])
    if n:
        player["resources"]["wood"] -= n
        add_goods(ctx["extra"], {"stone": n})
        ctx["log"].append(
            f"{player['name']} exchanges {n} wood for {n} stone (Petrified Wood)")


compendium_card("D006", prereq=needs_occupations(2),
                hooks={"play": _petrified_wood_play})

# ── D008 Fern Seeds ────────────────────────────────────────────────────
# Req 1 empty and 2 planted fields. Get 2 food and 1 grain, sown at once.
# Traveling.


def _fern_seeds_prereq(s, p):
    return len(_empty_field_cells(p)) >= 1 and _planted_field_count(p) >= 2


FERN_SEEDS_PREREQ = (_fern_seeds_prereq, "1 empty and 2 planted fields")


def _fern_seeds_play(state, player, inst, ctx):
    add_goods(ctx["extra"], {"food": 2})
    params = ctx.get("params") or {}
    if "card" in params:
        cid = params["card"]
        target = next((i for i in cards.card_fields(player)
                       if i["id"] == cid), None)
        if target is None or target["crops"]:
            raise ValueError("Fern Seeds: invalid card field to sow")
        if "grain" not in cards.CARDS[cid]["field"]["crops"]:
            raise ValueError("Fern Seeds: that field can't grow grain")
        target["crops"] = {"type": "grain", "count": 3}
    else:
        cell = params.get("cell")
        if not isinstance(cell, int) or cell not in _empty_field_cells(player):
            raise ValueError(
                "Fern Seeds: choose an empty field to sow (params.cell)")
        player["cells"][cell]["crops"] = {"type": "grain", "count": 3}
    ctx["log"].append(f"{player['name']} sows 1 grain (Fern Seeds)")


compendium_card("D008", prereq=FERN_SEEDS_PREREQ,
                hooks={"play": _fern_seeds_play})

# D011 Lawn Fertilizer -- see UNIMPLEMENTED

# ── D013 Trowel ────────────────────────────────────────────────────────
# Cost 1W. At any time, renovate straight to stone at a special cost.


def _trowel_cost(player):
    rooms = len(_room_cells(player))
    if player["house_type"] == "wood":
        return {"stone": rooms, "reed": rooms, "food": rooms}
    if player["house_type"] == "clay":
        return {"stone": rooms}
    return None


def _trowel_available(state, player, inst):
    return _trowel_cost(player) is not None


def _trowel_apply(state, player, inst, ctx):
    cost = _trowel_cost(player)
    if cost is None:
        raise ValueError("Your house is already stone")
    for res, amt in cost.items():
        if player["resources"][res] < amt:
            raise ValueError(f"Not enough {res}")
    for res, amt in cost.items():
        player["resources"][res] -= amt
    player["house_type"] = "stone"
    ctx["log"].append(f"{player['name']} renovates straight to stone (Trowel)")
    inner = {"free_stable_cell": None, "log": ctx["log"],
             "actor": player["index"], "extra": {}}
    cards.fire_player(state, player, "renovate", inner)
    add_goods(ctx["extra"], inner["extra"])


compendium_card("D013", card_action={
    "available": _trowel_available, "apply": _trowel_apply,
    "description": "Renovate straight to stone (Trowel)"})

# ── D014 Hammer Crusher ────────────────────────────────────────────────
# Cost 1W. Immediately before you renovate to stone, get 2 clay and 1
# reed, and you can take a Build Rooms action.


def _hammer_crusher_renovate(state, player, inst, ctx):
    if player["house_type"] != "stone":
        return
    add_goods(ctx["extra"], {"clay": 2, "reed": 1})
    inst["data"]["pending_rooms"] = True
    ctx["log"].append(f"{player['name']}'s Hammer Crusher grants 2 clay and 1 reed")


def _hammer_crusher_available(state, player, inst):
    return bool(inst["data"].get("pending_rooms")) \
        and bool(_buildable_room_cells(player))


def _hammer_crusher_build(state, player, inst, ctx):
    cells = (ctx.get("params") or {}).get("cells") or []
    if not cells:
        raise ValueError("Hammer Crusher: choose room cells (params.cells)")
    built = []
    for cell in cells:
        if cell in built or cell not in _buildable_room_cells(player):
            raise ValueError("Invalid room cell")
        cost = cards.modified_cost(
            state, player, "room", {player["house_type"]: 5, "reed": 2})
        for res, amt in cost.items():
            if player["resources"][res] < amt:
                raise ValueError(f"Not enough {res}")
        for res, amt in cost.items():
            player["resources"][res] -= amt
        player["cells"][cell]["type"] = "room"
        built.append(cell)
    inst["data"]["pending_rooms"] = False
    ctx["log"].append(f"{player['name']} builds {len(built)} room(s) (Hammer Crusher)")


compendium_card("D014", hooks={"renovate": _hammer_crusher_renovate},
                card_action={"available": _hammer_crusher_available,
                             "apply": _hammer_crusher_build,
                             "description": "Build rooms (Hammer Crusher)"})

# ── D016 Wooden Whey Bucket ────────────────────────────────────────────
# Cost 1W 1F. Each time before you use the Sheep/Cattle Market, you can
# build exactly 1 stable at no cost.


def _whey_bucket_space(state, player, inst, ctx):
    if ctx["actor"] != player["index"] \
            or ctx["space_id"] not in ("sheep_market", "cattle_market"):
        return
    eligible = [i for i, c in enumerate(player["cells"])
               if c["type"] == "empty" and not c["stable"]]
    if not eligible:
        return
    options = ["Skip"] + [f"Build a stable at cell {c}" for c in eligible]
    prompt_choice(state, player, inst["id"],
                 "Build a free stable before taking animals? (Wooden Whey Bucket)",
                 options, data={"cells": eligible})


def _whey_bucket_choice(state, player, inst, ctx):
    if ctx["index"] == 0:
        return
    cell = ctx["data"]["cells"][ctx["index"] - 1]
    player["cells"][cell]["stable"] = True
    ctx["log"].append(f"{player['name']} builds a free stable (Wooden Whey Bucket)")


compendium_card("D016", hooks={"space_used": _whey_bucket_space},
                resolve_choice=_whey_bucket_choice)

# ── D017 Drill Harrow ──────────────────────────────────────────────────
# Cost 1W. Pay 3 food to plow 1 field (repeatable ability).


def _drill_harrow_available(state, player, inst):
    return player["resources"]["food"] >= 3 and bool(plowable_cells(player))


def _drill_harrow_apply(state, player, inst, ctx):
    cell = (ctx.get("params") or {}).get("cell")
    if not isinstance(cell, int) or cell not in plowable_cells(player):
        raise ValueError("Drill Harrow: choose a field (params.cell)")
    if player["resources"]["food"] < 3:
        raise ValueError("Not enough food")
    player["resources"]["food"] -= 3
    player["cells"][cell]["type"] = "field"
    ctx["log"].append(f"{player['name']} pays 3 food to plow a field (Drill Harrow)")


compendium_card("D017", card_action={
    "available": _drill_harrow_available, "apply": _drill_harrow_apply,
    "description": "Pay 3 food to plow 1 field (Drill Harrow)"})

# ── D018 Steam Plow ────────────────────────────────────────────────────
# Cost 1W 1F. 1VP. Pay 2 wood and 1 food to use Farmland without placing
# a person (repeatable ability).


def _steam_plow_available(state, player, inst):
    return (player["resources"]["wood"] >= 2
            and player["resources"]["food"] >= 1
            and bool(plowable_cells(player)))


def _steam_plow_apply(state, player, inst, ctx):
    cell = (ctx.get("params") or {}).get("cell")
    if not isinstance(cell, int) or cell not in plowable_cells(player):
        raise ValueError("Steam Plow: choose a field (params.cell)")
    if player["resources"]["wood"] < 2 or player["resources"]["food"] < 1:
        raise ValueError("Not enough resources")
    player["resources"]["wood"] -= 2
    player["resources"]["food"] -= 1
    player["cells"][cell]["type"] = "field"
    ctx["log"].append(
        f"{player['name']} pays 2 wood and 1 food to plow a field (Steam Plow)")


compendium_card("D018", card_action={
    "available": _steam_plow_available, "apply": _steam_plow_apply,
    "description": "Pay 2 wood, 1 food to plow (Steam Plow)"})

# ── D019 Pulverizer Plow ───────────────────────────────────────────────
# Cost 2W. Req 1 occ. After using a clay space, pay 1 clay to plow 1
# field (that clay goes back on the accumulation space).

CLAY_SPACES = ("clay_pit", "hollow_3p", "hollow_4p")


def _pulverizer_plow_space(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or ctx["space_id"] not in CLAY_SPACES:
        return
    targets = plowable_cells(player)
    if player["resources"]["clay"] < 1 or not targets:
        return
    options = ["Skip"] + [f"Pay 1 clay to plow field at cell {c}" for c in targets]
    prompt_choice(state, player, inst["id"],
                 "Pay 1 clay to plow a field? (Pulverizer Plow)", options,
                 data={"cells": targets, "space_id": ctx["space_id"]})


def _pulverizer_plow_choice(state, player, inst, ctx):
    if ctx["index"] == 0:
        return
    cell = ctx["data"]["cells"][ctx["index"] - 1]
    player["resources"]["clay"] -= 1
    player["cells"][cell]["type"] = "field"
    sp = next((s for s in state["action_spaces"]
              if s["id"] == ctx["data"]["space_id"]), None)
    if sp is not None:
        sp["supply"]["clay"] = sp["supply"].get("clay", 0) + 1
    ctx["log"].append(
        f"{player['name']} plows a field with clay from the space (Pulverizer Plow)")


compendium_card("D019", prereq=needs_occupations(1),
                hooks={"space_used": _pulverizer_plow_space},
                resolve_choice=_pulverizer_plow_choice)

# D021 Recruitment -- see UNIMPLEMENTED
# D022 Work Permit -- see UNIMPLEMENTED
# D023 Pioneering Spirit -- see UNIMPLEMENTED
# D024 Brotherly Love -- see UNIMPLEMENTED
# D025 Witches Dance Floor -- see UNIMPLEMENTED
# D026 Carpenter's Yard -- see UNIMPLEMENTED

# ── D027 Retraining ────────────────────────────────────────────────────
# Cost 1F. 1VP. Req 1 occ. At the end of a turn in which you renovate,
# exchange Joinery for Pottery, or Pottery for Basketmaker's Workshop.


def _retraining_renovate(state, player, inst, ctx):
    options, swaps = [], []
    if "joinery" in player["improvements"] \
            and "pottery" in state["available_improvements"]:
        options.append("Exchange Joinery for Pottery")
        swaps.append(("joinery", "pottery"))
    if "pottery" in player["improvements"] \
            and "basketmaker" in state["available_improvements"]:
        options.append("Exchange Pottery for Basketmaker's Workshop")
        swaps.append(("pottery", "basketmaker"))
    if not options:
        return
    options.append("Skip")
    prompt_choice(state, player, inst["id"],
                 "Exchange a major improvement? (Retraining)",
                 options, data={"swaps": swaps})


def _retraining_choice(state, player, inst, ctx):
    swaps = ctx["data"]["swaps"]
    if ctx["index"] >= len(swaps):
        return
    old, new = swaps[ctx["index"]]
    player["improvements"].remove(old)
    state["available_improvements"].append(old)
    state["available_improvements"].sort()
    state["available_improvements"].remove(new)
    player["improvements"].append(new)
    ctx["log"].append(f"{player['name']} exchanges {old} for {new} (Retraining)")


compendium_card("D027", prereq=needs_occupations(1),
                hooks={"renovate": _retraining_renovate},
                resolve_choice=_retraining_choice)

# ── D030 Artisan District ──────────────────────────────────────────────
# Cost 1S. 1VP. Req 3 occ. Bonus points for majors from the "bottom row"
# (this engine's non-Fireplace/Cooking-Hearth majors).

BOTTOM_ROW_IMPROVEMENTS = ("joinery", "pottery", "basketmaker", "well",
                           "clay_oven", "stone_oven")


def _artisan_district_score(state, player, inst):
    n = sum(1 for i in player["improvements"] if i in BOTTOM_ROW_IMPROVEMENTS)
    for minimum, pts in ((5, 8), (4, 5), (3, 2)):
        if n >= minimum:
            return pts
    return 0


compendium_card("D030", prereq=needs_occupations(3),
                score_bonus=_artisan_district_score)

# ── D031 Storeroom ─────────────────────────────────────────────────────
# Cost 1W 2S. 1VP. 0.5 bonus points per grain+vegetable pair, rounded up.


def _storeroom_score(state, player, inst):
    grain = player["resources"]["grain"]
    veg = player["resources"]["vegetable"]
    for c in player["cells"]:
        if c["crops"]:
            if c["crops"]["type"] == "grain":
                grain += c["crops"]["count"]
            else:
                veg += c["crops"]["count"]
    for i in cards.card_fields(player):
        if i["crops"]:
            if i["crops"]["type"] == "grain":
                grain += i["crops"]["count"]
            else:
                veg += i["crops"]["count"]
    pairs = min(grain, veg)
    return (pairs + 1) // 2


compendium_card("D031", score_bonus=_storeroom_score)

# ── D032 Wood Rake ─────────────────────────────────────────────────────
# Cost 1W. Bonus points if 7+ goods sat in fields right before the final
# harvest (snapshotted at round 14's round_start, before that harvest).


def _wood_rake_round_start(state, player, inst, ctx):
    if ctx["round"] != 14:
        return
    total = sum(c["crops"]["count"] for c in player["cells"] if c["crops"])
    total += sum(i["crops"]["count"] for i in cards.card_fields(player)
                if i["crops"])
    inst["data"]["snapshot"] = total


def _wood_rake_score(state, player, inst):
    return 2 if inst["data"].get("snapshot", 0) >= 7 else 0


compendium_card("D032", hooks={"round_start": _wood_rake_round_start},
                score_bonus=_wood_rake_score)

# ── D033 Summer House ──────────────────────────────────────────────────
# Cost 3W 1S. Req still in wooden house (at play time). Bonus points per
# unused farmyard space adjacent to a stone house.

SUMMER_HOUSE_PREREQ = (lambda s, p: p["house_type"] == "wood",
                       "still in a wooden house")


def _summer_house_score(state, player, inst):
    if player["house_type"] != "stone":
        return 0
    pasture_cells = {i for pas in compute_pastures(player) for i in pas}
    rooms = _room_cells(player)
    count = 0
    for i, c in enumerate(player["cells"]):
        if c["type"] == "empty" and not c["stable"] and i not in pasture_cells:
            if any(nb in rooms for nb in orthogonal_neighbors(i)):
                count += 1
    return 2 * count


compendium_card("D033", prereq=SUMMER_HOUSE_PREREQ,
                score_bonus=_summer_house_score)

# ── D034 Luxurious Hostel ──────────────────────────────────────────────
# Cost 1W 2C. Bonus points if you end the game with more stone rooms
# than people.


def _luxurious_hostel_score(state, player, inst):
    if player["house_type"] != "stone":
        return 0
    rooms = len(_room_cells(player))
    return 4 if rooms > player["people_total"] else 0


compendium_card("D034", score_bonus=_luxurious_hostel_score)

# ── D035 Fodder Chamber ────────────────────────────────────────────────
# Cost 3 Grain 3S (hand-parsed: full-word tokens). 2VP. Bonus points
# scaling with total animals, divisor depends on player count.


def _fodder_chamber_score(state, player, inst):
    divisor = {1: 7, 2: 5, 3: 4}.get(state["player_count"], 3)
    return sum(animal_totals_of(player).values()) // divisor


compendium_card("D035", cost={"grain": 3, "stone": 3},
                score_bonus=_fodder_chamber_score)

# D036 Breed Registry -- see UNIMPLEMENTED

# ── D040 Cesspit ───────────────────────────────────────────────────────
# -1VP. Req 2 fields and 1 occ. Alternate 1 clay / 1 wild boar on each
# remaining round, starting with clay (round_start-based, so animals get
# routed through the normal accommodation flow).

CESSPIT_PREREQ = combine(needs_occupations(1), _needs_fields(2))


def _cesspit_play(state, player, inst, ctx):
    inst["data"]["played_round"] = state["round"]


def _cesspit_round_start(state, player, inst, ctx):
    played = inst["data"].get("played_round")
    if played is None:
        return
    offset = ctx["round"] - played
    if offset <= 0:
        return
    good = "clay" if offset % 2 == 1 else "boar"
    add_goods(ctx["extra"], {good: 1})
    ctx["log"].append(f"{player['name']}'s Cesspit grants 1 {good}")


compendium_card("D040", prereq=CESSPIT_PREREQ,
                hooks={"play": _cesspit_play,
                       "round_start": _cesspit_round_start})

# ── D044 Forest Well ───────────────────────────────────────────────────
# Cost 1S 1F. 1VP. Req 2 occ. Place 1 food on each remaining round space,
# up to the wood in supply at play time.


def _forest_well_play(state, player, inst, ctx):
    n = player["resources"]["wood"]
    rnd = state["round"]
    targets = list(range(rnd + 1, min(TOTAL_ROUNDS, rnd + n) + 1))
    _schedule_good(state, player, "food", targets)
    if targets:
        ctx["log"].append(
            f"{player['name']}'s Forest Well places food on rounds "
            + ", ".join(map(str, targets)))


compendium_card("D044", prereq=needs_occupations(2),
                hooks={"play": _forest_well_play})

# ── D046 Pellet Press ──────────────────────────────────────────────────
# Cost 2C. Req 2 occ. Once per round, pay 1 reed for 1 food on each of
# the next 4 round spaces.


def _pellet_press_available(state, player, inst):
    return (player["resources"]["reed"] >= 1
            and inst["data"].get("used_round") != state["round"])


def _pellet_press_apply(state, player, inst, ctx):
    if player["resources"]["reed"] < 1:
        raise ValueError("Not enough reed")
    player["resources"]["reed"] -= 1
    rnd = state["round"]
    targets = list(range(rnd + 1, min(TOTAL_ROUNDS, rnd + 4) + 1))
    _schedule_good(state, player, "food", targets)
    inst["data"]["used_round"] = rnd
    ctx["log"].append(
        f"{player['name']} pays 1 reed for food on upcoming rounds (Pellet Press)")


compendium_card("D046", prereq=needs_occupations(2), card_action={
    "available": _pellet_press_available, "apply": _pellet_press_apply,
    "description": "Pay 1 reed to schedule food (Pellet Press)"})

# ── D047 Churchyard ────────────────────────────────────────────────────
# Cost 1S 1R. Req 10 occupations+improvements in front of you. Place 2
# food on every remaining round space.


def _churchyard_prereq(s, p):
    return len(p["occupations"]) + len(p["minors"]) \
        + len(p["improvements"]) >= 10


CHURCHYARD_PREREQ = (_churchyard_prereq,
                    "10 occupations and improvements in front of you")


def _churchyard_play(state, player, inst, ctx):
    rnd = state["round"]
    targets = list(range(rnd + 1, TOTAL_ROUNDS + 1))
    _schedule_good(state, player, "food", targets, amount=2)
    if targets:
        ctx["log"].append(
            f"{player['name']}'s Churchyard places 2 food on all remaining rounds")


compendium_card("D047", prereq=CHURCHYARD_PREREQ,
                hooks={"play": _churchyard_play})

# ── D048 Civic Facade ──────────────────────────────────────────────────
# Cost 1C. Req 3 rooms. Before each round, if hand occupations outnumber
# hand minors, get 1 food.


def _civic_facade_prereq(s, p):
    return len(_room_cells(p)) >= 3


CIVIC_FACADE_PREREQ = (_civic_facade_prereq, "3 rooms")

compendium_card("D048", prereq=CIVIC_FACADE_PREREQ, hooks=round_income(
    {"food": 1},
    condition=lambda s, p: len(p["hand_occupations"]) > len(p["hand_minors"])))

# D051 Archway -- see UNIMPLEMENTED

# ── D052 Rolling Pin ───────────────────────────────────────────────────
# Cost 1W. Req 1 occ. Returning-home phase (modeled as round_start): if
# you have more clay than wood, get 1 food.

compendium_card("D052", prereq=needs_occupations(1), hooks=round_income(
    {"food": 1},
    condition=lambda s, p: p["resources"]["clay"] > p["resources"]["wood"]))

# D053 Tea House -- see UNIMPLEMENTED

# ── D054 Trout Pool ────────────────────────────────────────────────────
# Cost 2C. 1VP. At the start of each work phase, if Fishing holds >=3
# food, get 1 food.


def _trout_pool_round_start(state, player, inst, ctx):
    fishing = next((s for s in state["action_spaces"] if s["id"] == "fishing"),
                   None)
    if fishing and fishing["supply"].get("food", 0) >= 3:
        player["resources"]["food"] += 1
        ctx["log"].append(f"{player['name']}'s Trout Pool grants 1 food")


compendium_card("D054", hooks={"round_start": _trout_pool_round_start})

# ── D055 New Market ────────────────────────────────────────────────────
# Cost 1W 1C. 1VP. Using the action-space card revealed for rounds 8-11
# grants 1 additional food.


def _new_market_space(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    revealed = state.get("revealed", [])
    target_ids = set(revealed[7:11])
    if ctx["space_id"] in target_ids:
        add_goods(ctx["extra"], {"food": 1})
        ctx["log"].append(f"{player['name']}'s New Market adds 1 food")


compendium_card("D055", hooks={"space_used": _new_market_space})

# D056 Fatstock Stretcher -- see UNIMPLEMENTED

# ── D057 Wholesale Market ──────────────────────────────────────────────
# Cost 2 Vegetable 2W (hand-parsed). 3VP. Place 1 food on every remaining
# round space.


def _wholesale_market_play(state, player, inst, ctx):
    rnd = state["round"]
    targets = list(range(rnd + 1, TOTAL_ROUNDS + 1))
    _schedule_good(state, player, "food", targets)
    if targets:
        ctx["log"].append(
            f"{player['name']}'s Wholesale Market places 1 food on all remaining rounds")


compendium_card("D057", cost={"vegetable": 2, "wood": 2},
                hooks={"play": _wholesale_market_play})

# ── D058 Gritter ───────────────────────────────────────────────────────
# Cost 1W. Req play in round 5+. Sowing vegetables grants 1 food per
# vegetable field you have (including the new ones).

GRITTER_PREREQ = (lambda s, p: s["round"] >= 5, "play in round 5 or later")


def _gritter_sow(state, player, inst, ctx):
    if not any(crop == "vegetable" for _, crop in ctx["sown"]):
        return
    n = _vegetable_field_count(player)
    if n:
        add_goods(ctx["extra"], {"food": n})
        ctx["log"].append(f"{player['name']}'s Gritter grants {n} food")


compendium_card("D058", prereq=GRITTER_PREREQ, hooks={"sow": _gritter_sow})

# ── D059 Earth Oven ────────────────────────────────────────────────────
# 3VP. Req return a Fireplace. Cook/bake table like a Fireplace, with
# better animal/vegetable cook rates.

EARTH_OVEN_PREREQ = (lambda s, p: any(f in p["improvements"] for f in FIREPLACES),
                    "return a Fireplace")


def _earth_oven_play(state, player, inst, ctx):
    fp = next(f for f in FIREPLACES if f in player["improvements"])
    player["improvements"].remove(fp)
    state["available_improvements"].append(fp)
    state["available_improvements"].sort()
    ctx["log"].append(f"{player['name']} returns their Fireplace (Earth Oven)")


compendium_card(
    "D059", prereq=EARTH_OVEN_PREREQ, hooks={"play": _earth_oven_play},
    cook={"vegetable": 3, "sheep": 2, "boar": 3, "cattle": 3}, bake=(None, 2))

# ── D060 Large Pottery ─────────────────────────────────────────────────
# Cost 1C 1S. 3VP. Req return the Pottery. Convert clay to food; bonus
# points for stockpiled clay.

LARGE_POTTERY_PREREQ = (lambda s, p: "pottery" in p["improvements"],
                       "return the Pottery")


def _large_pottery_play(state, player, inst, ctx):
    player["improvements"].remove("pottery")
    state["available_improvements"].append("pottery")
    state["available_improvements"].sort()
    ctx["log"].append(f"{player['name']} returns their Pottery (Large Pottery)")


def _large_pottery_score(state, player, inst):
    clay = player["resources"]["clay"]
    for minimum, pts in ((7, 4), (6, 3), (5, 2), (3, 1)):
        if clay >= minimum:
            return pts
    return 0


compendium_card("D060", prereq=LARGE_POTTERY_PREREQ,
                hooks={"play": _large_pottery_play},
                conversions=[{"give": {"clay": 1}, "get": {"food": 2}}],
                score_bonus=_large_pottery_score)

# ── D061 Bale of Straw ─────────────────────────────────────────────────
# At the start of each harvest, 3+ grain fields grants 2 food.

compendium_card("D061", hooks=harvest_food(
    lambda s, p: 2 if _grain_field_count(p) >= 3 else 0))

# ── D062 Beer Tap ──────────────────────────────────────────────────────
# Cost 1W. On play, get 2 food. Bulk grain->food conversions in feeding.

BEER_TAP_CONVERSIONS = [
    {"give": {"grain": 2}, "get": {"food": 3}},
    {"give": {"grain": 3}, "get": {"food": 6}},
    {"give": {"grain": 4}, "get": {"food": 9}},
]

compendium_card("D062", hooks={"play": on_play_gain({"food": 2})},
                conversions=BEER_TAP_CONVERSIONS)

# ── D063 Lynchet ───────────────────────────────────────────────────────
# In the field phase of each harvest, 1 food per field tile adjacent to
# the house.


def _lynchet_food(state, player):
    rooms = _room_cells(player)
    return sum(1 for i, c in enumerate(player["cells"])
              if c["type"] == "field" and any(nb in rooms
                                              for nb in orthogonal_neighbors(i)))


compendium_card("D063", hooks=harvest_food(_lynchet_food))

# ── D064 Baking Course ─────────────────────────────────────────────────
# Req 1 occ. At the end of each non-harvest round, you may bake 1 grain
# into 2 food (modeled as a choice offered at the following round_start).


def _baking_course_round_start(state, player, inst, ctx):
    prev = ctx["round"] - 1
    if prev < 1 or prev in HARVEST_ROUNDS or player["resources"]["grain"] < 1:
        return
    prompt_choice(state, player, inst["id"],
                 "Bake 1 grain into 2 food? (Baking Course)",
                 ["Skip", "Bake 1 grain into 2 food"])


def _baking_course_choice(state, player, inst, ctx):
    if ctx["index"] == 0 or player["resources"]["grain"] < 1:
        return
    player["resources"]["grain"] -= 1
    player["resources"]["food"] += 2
    ctx["log"].append(f"{player['name']} bakes 1 grain into 2 food (Baking Course)")


compendium_card("D064", prereq=needs_occupations(1),
                hooks={"round_start": _baking_course_round_start},
                resolve_choice=_baking_course_choice)

# D065 Grain Sieve -- see UNIMPLEMENTED

# ── D067 Reap Hook ─────────────────────────────────────────────────────
# Cost 1W. Place 1 grain on the next 3 of round spaces 4/7/9/11/13/14.

REAP_HOOK_ROUNDS = (4, 7, 9, 11, 13, 14)


def _reap_hook_play(state, player, inst, ctx):
    rnd = state["round"]
    targets = [r for r in REAP_HOOK_ROUNDS if r > rnd][:3]
    _schedule_good(state, player, "grain", targets)
    if targets:
        ctx["log"].append(
            f"{player['name']}'s Reap Hook places grain on rounds "
            + ", ".join(map(str, targets)))


compendium_card("D067", hooks={"play": _reap_hook_play})

# ── D068 Small Basket ──────────────────────────────────────────────────
# Req 2 occ. Using Reed Bank, pay 1 reed for 1 vegetable (4+ players:
# that reed returns to the space).


def _small_basket_space(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or ctx["space_id"] != "reed_bank":
        return
    if player["resources"]["reed"] < 1:
        return
    prompt_choice(state, player, inst["id"],
                 "Pay 1 reed for 1 vegetable? (Small Basket)",
                 ["Skip", "Pay 1 reed for 1 vegetable"])


def _small_basket_choice(state, player, inst, ctx):
    if ctx["index"] == 0 or player["resources"]["reed"] < 1:
        return
    player["resources"]["reed"] -= 1
    player["resources"]["vegetable"] += 1
    if state["player_count"] >= 4:
        sp = next((s for s in state["action_spaces"] if s["id"] == "reed_bank"),
                  None)
        if sp is not None:
            sp["supply"]["reed"] = sp["supply"].get("reed", 0) + 1
    ctx["log"].append(
        f"{player['name']} exchanges 1 reed for 1 vegetable (Small Basket)")


compendium_card("D068", prereq=needs_occupations(2),
                hooks={"space_used": _small_basket_space},
                resolve_choice=_small_basket_choice)

# D070 Straw Manure -- see UNIMPLEMENTED

# ── D071 Changeover ────────────────────────────────────────────────────
# If a field holds exactly 1 good left, discard it and resow that field
# with the same crop for free.


def _changeover_targets(player):
    out = [("cell", i) for i, c in enumerate(player["cells"])
          if c["type"] == "field" and c["crops"] and c["crops"]["count"] == 1]
    out += [("card", i["id"]) for i in cards.card_fields(player)
           if i["crops"] and i["crops"]["count"] == 1]
    return out


def _changeover_available(state, player, inst):
    return bool(_changeover_targets(player))


def _changeover_apply(state, player, inst, ctx):
    params = ctx.get("params") or {}
    kind = params.get("kind")
    if kind == "cell":
        cell = params.get("cell")
        c = player["cells"][cell] if isinstance(cell, int) \
            and 0 <= cell < NUM_CELLS else None
        if c is None or c["type"] != "field" or not c["crops"] \
                or c["crops"]["count"] != 1:
            raise ValueError("Changeover: invalid field")
        crop = c["crops"]["type"]
        c["crops"] = {"type": crop, "count": 3 if crop == "grain" else 2}
    elif kind == "card":
        cid = params.get("card")
        target = next((i for i in cards.card_fields(player) if i["id"] == cid),
                     None)
        if target is None or not target["crops"] \
                or target["crops"]["count"] != 1:
            raise ValueError("Changeover: invalid card field")
        crop = target["crops"]["type"]
        target["crops"] = {"type": crop, "count": 3 if crop == "grain" else 2}
    else:
        raise ValueError("Changeover: choose a field (params.kind/cell or card)")
    ctx["log"].append(f"{player['name']} resows a field for free (Changeover)")


compendium_card("D071", card_action={
    "available": _changeover_available, "apply": _changeover_apply,
    "description": "Discard a field's last crop and resow it free (Changeover)"})

# ── D072 Stable Manure ─────────────────────────────────────────────────
# Req at most 1 occ. In the field phase of each harvest, harvest 1
# additional good from a number of fields equal to unfenced stables.


def _stable_manure_harvest(state, player, inst, ctx):
    n = _unfenced_stables(player)
    if not n:
        return
    gained = {"grain": 0, "vegetable": 0}
    remaining = n
    for c in player["cells"]:
        if remaining <= 0:
            break
        if c["type"] == "field" and c["crops"] and c["crops"]["count"] > 0:
            c["crops"]["count"] -= 1
            gained[c["crops"]["type"]] += 1
            if c["crops"]["count"] <= 0:
                c["crops"] = None
            remaining -= 1
    if gained["grain"] or gained["vegetable"]:
        player["resources"]["grain"] += gained["grain"]
        player["resources"]["vegetable"] += gained["vegetable"]
        ctx["log"].append(
            f"{player['name']}'s Stable Manure harvests " + goods_str(gained))


compendium_card("D072", prereq=_at_most_occupations(1),
                hooks={"harvest_field": _stable_manure_harvest})

# D074 Royal Wood -- see UNIMPLEMENTED

# ── D076 Social Benefits ───────────────────────────────────────────────
# Cost 1R. Req at most 1 occ. Immediately after a harvest's feeding phase
# (modeled as the following round_start), if you have no food, get 1
# wood and 1 clay.


def _social_benefits_round_start(state, player, inst, ctx):
    prev = ctx["round"] - 1
    if prev < 1 or prev not in HARVEST_ROUNDS:
        return
    if player["resources"]["food"] == 0:
        add_goods(ctx["extra"], {"wood": 1, "clay": 1})
        ctx["log"].append(
            f"{player['name']}'s Social Benefits grants 1 wood and 1 clay")


compendium_card("D076", prereq=_at_most_occupations(1),
                hooks={"round_start": _social_benefits_round_start})

# D077 Recycled Brick -- see UNIMPLEMENTED

# ── D080 Brick Hammer ──────────────────────────────────────────────────
# Cost "1W or 1F" (hand-parsed to 1W; the OR-alternative payment isn't
# representable, see judgment calls). Building a >=2-clay improvement
# grants 1 stone. Relies on the engine's improvement_built event (fired
# for every major build, though not yet documented in decks/GUIDE.md).


def _brick_hammer_built(state, player, inst, ctx):
    imp = ctx["improvement"]
    if MAJOR_IMPROVEMENTS.get(imp, {}).get("cost", {}).get("clay", 0) >= 2:
        add_goods(ctx["extra"], {"stone": 1})
        ctx["log"].append(f"{player['name']}'s Brick Hammer grants 1 stone")


compendium_card("D080", cost={"wood": 1},
                hooks={"improvement_built": _brick_hammer_built})

# ── D081 Roof Ladder ───────────────────────────────────────────────────
# Cost 1W. Renovating costs 1 less reed and grants 1 stone.


def _roof_ladder_cost_mod(state, player, kind, cost, ctx):
    if kind == "renovation" and cost.get("reed"):
        cost = dict(cost)
        cost["reed"] = max(0, cost["reed"] - 1)
    return cost


def _roof_ladder_renovate(state, player, inst, ctx):
    add_goods(ctx["extra"], {"stone": 1})
    ctx["log"].append(f"{player['name']}'s Roof Ladder grants 1 stone")


compendium_card("D081", cost_mod=_roof_ladder_cost_mod,
                hooks={"renovate": _roof_ladder_renovate})

# D082 Hunting Trophy -- see UNIMPLEMENTED
# D083 Pigswill -- see UNIMPLEMENTED
