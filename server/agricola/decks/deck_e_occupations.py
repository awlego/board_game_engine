"""Deck E occupations (codes E147-E218, E341 from the compendium DB).

Deck E is an ORIGINAL-edition deck (see decks/GUIDE.md point 4): card
texts reference the original board's action spaces and majors. Mapped
onto this (revised) engine's terms:
  - "Take 1 Vegetable" -> vegetable_seeds, "Take 1 Grain" -> grain_seeds,
    "Day Labourer" -> day_laborer, "Traveling Players" -> traveling_players,
    "Plough 1 Field" -> farmland, "Plough Field and Sow" -> cultivation,
    "1 Wild Boar" (accumulation) -> pig_market, wood accumulation spaces
    -> forest/grove/copse, clay accumulation spaces -> clay_pit/hollow.
  - "wooden hut"/"clay hut"/"stone house" -> house_type "wood"/"clay"/
    "stone"; named original majors with no equivalent here (Water Mill,
    Half-timbered House, Chicken Coop, Holiday House, Mansion, Corn
    Storehouse, Fishing Rod, Raft, Landing Net, ...) simply don't apply
    (none of this engine's majors cost reed, so text about discounting
    reed for those majors has nothing to discount and is dropped without
    changing behavior).

Checked the full extracted slice (73 occupation cards, codes E147-E218
and E341) against the DB-bleed pattern documented in deck_b_occupations
(an embedded "(N-M players)" tag introducing an unrelated card's text
mid-string): none of this slice's `text` fields show that artifact, so
no _TEXT_FIXES table is needed here.
"""

from server.agricola.cards import (
    compendium_card, add_goods, goods_str, prompt_choice, on_play_gain,
    space_bonus, take_bonus, schedule_on_play, harvest_food,
)
import server.agricola.cards as cards
from server.agricola import sub_actions
from server.agricola.state import (
    ANIMAL_TYPES, TOTAL_ROUNDS, BUILDING_RESOURCES, MAX_STABLES,
    MAJOR_IMPROVEMENTS, compute_pastures, plowable_cells, table_score,
)

UNIMPLEMENTED = {
    "E148": "Academic: 'counts as 2 occupations' must retroactively change "
            "how OTHER cards count occupations (minor-improvement "
            "prerequisites engine-wide, plus Reeve E217/Tutor E174's own "
            "scoring); needs_occupations/exact_occupations and occupation "
            "counts read len(player['occupations']) directly with no "
            "per-card weighting hook, and retrofitting every prereq/"
            "scoring consumer engine-wide is out of scope for one card.",
    "E149": "Master Baker: reacting to ANOTHER player baking bread needs "
            "cross-player observation of the 'bake' event, but engine.py's "
            "_do_bake fires it via cards.fire_player (only the baking "
            "player's own cards), never to other players' cards.",
    "E155": "Bread Seller: same gap as Master Baker -- needs to react to "
            "ANY player's bake (including others), but the 'bake' event "
            "only fires to the acting player's own in-play cards.",
    "E156": "Brush Maker: needs to count each time a wild boar is "
            "converted to food (cooked), but no hook fires for cooking/"
            "conversion -- the accommodate and feed 'cook' paths mutate "
            "resources directly with no card event.",
    "E159": "Head of the Family: requires using a room-building/family-"
            "growth action space already occupied by another player; "
            "placing on occupied spaces is explicitly unsupported.",
    "E161": "Fisherman: the optional double-take on Fishing requires "
            "paying food to OTHER players who own any of several named "
            "original-edition minors (Fishing Rod, Raft, Canoe, Fish "
            "Trap, Landing Net) that are outside this occupations deck's "
            "scope and not guaranteed to exist in the registry; paying an "
            "arbitrary/unknown set of other players in one action isn't "
            "expressible.",
    "E163": "Field Warden: requires using Take-1-Vegetable/Plough-1-"
            "Field/Plough-Field-and-Sow action spaces already occupied "
            "by another player; placing on occupied spaces is explicitly "
            "unsupported.",
    "E173": "Chief's Daughter: must react to another player playing the "
            "Chief (E172) WHILE STILL IN HAND (unplayed), but the hook "
            "system only fires events to already-in-play cards "
            "(cards.fire/fire_player iterate in_play(player)); a card "
            "sitting in hand can never observe or react to anything.",
    "E179": "Merchant: a second use of a 'minor OR major improvement' "
            "action requires reimplementing the engine-internal major-"
            "improvement transaction (cost, available_improvements "
            "bookkeeping, well/bake_on_build hooks) outside the normal "
            "placement flow -- the same gap flagged for B150.",
    "E192": "Patron: '2 food before you pay the costs' requires granting "
            "resources usable toward an occupation's own cost payment "
            "before that payment happens, but _play_occupation pays the "
            "food cost before any hook fires -- the same gap flagged for "
            "B155 (no alternate-resource-source channel for occupation "
            "costs).",
    "E193": "Pastor: must detect when another player builds their third "
            "room (to know who is 'last' at 2 rooms), but rooms_built "
            "fires with to_all=False -- only to the building player's "
            "own cards, never observable by other players.",
    "E198": "Ratcatcher: forces OTHER players to leave 1 family member "
            "unplaced in specific rounds; directly constraining other "
            "players' people/placements is explicitly unsupported.",
    "E208": "Stablemaster: raising one specific unfenced stable's cap to "
            "3 animals requires overriding state.validate_animal_"
            "placement's hardcoded 'an unfenced stable holds only 1 "
            "animal' rule, which has no per-stable override hook.",
    "E215": "Tenant Farmer: the loaned animals must be returnable via a "
            "deliberate action at any time before scoring (independent "
            "of ordinary cooking/discarding), and only trigger the point "
            "penalty for animals the player chooses not to (or cannot) "
            "give back. Approximating this from final animal counts would "
            "misrepresent the real trade-off between an unreturned loan "
            "animal and the player's own naturally bred stock of the "
            "same type; no explicit 'return a loan' action channel "
            "exists.",
    "E216": "Animal Keeper: mixed-species pastures require overriding "
            "state.validate_animal_placement's hardcoded single-type-per-"
            "pasture rule, which has no override hook (the same class of "
            "gap as Stablemaster/E208).",
}

# ── Shared helpers ────────────────────────────────────────────────────

WOOD_SPACES = ("forest", "grove", "copse")


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


def _empty_field_cells(player):
    return [i for i, c in enumerate(player["cells"])
            if c["type"] == "field" and not c["crops"]]


def _field_scoring_tables(player):
    """Recomputes the 7 category counts scoring.py's score_player uses
    (fields/pastures/grain/vegetable/sheep/boar/cattle), for cards that
    react to the scoring tables themselves (Yeoman Farmer, Lord of the
    Manor)."""
    cells = player["cells"]
    pastures = compute_pastures(player)
    animals = cards.animal_totals_of(player)
    grain = player["resources"]["grain"]
    vegetable = player["resources"]["vegetable"]
    for c in cells:
        if c["crops"]:
            if c["crops"]["type"] == "grain":
                grain += c["crops"]["count"]
            else:
                vegetable += c["crops"]["count"]
    for inst in cards.card_fields(player):
        if inst["crops"]:
            if inst["crops"]["type"] == "grain":
                grain += inst["crops"]["count"]
            else:
                vegetable += inst["crops"]["count"]
    fields = sum(1 for c in cells if c["type"] == "field")
    return {
        "fields": fields, "pastures": len(pastures), "grain": grain,
        "vegetable": vegetable, "sheep": animals["sheep"],
        "boar": animals["boar"], "cattle": animals["cattle"],
    }


def _free_room_available(min_rooms, house_type=None):
    """Factory for the 'once per game, free room extension' shape shared
    by Master Builder (E151) and Mason (E191)."""
    def fn(state, player, inst):
        if inst["data"].get("used"):
            return False
        if house_type and player["house_type"] != house_type:
            return False
        rooms = sum(1 for c in player["cells"] if c["type"] == "room")
        return rooms >= min_rooms and sub_actions.can_build_rooms(
            state, player, cost_override="free")
    return fn


def _free_room_apply(state, player, inst, ctx):
    cell = (ctx.get("params") or {}).get("cell")
    if not isinstance(cell, int):
        raise ValueError("choose a valid free room cell (params.cell)")
    sub_actions.build_rooms(state, player, [cell], ctx["log"],
                            cost_override="free")
    inst["data"]["used"] = True
    ctx["log"].append(f"{player['name']}'s {cards.spec(inst)['name']} "
                      "builds a free room")


# ── E147 Land Agent ───────────────────────────────────────────────────
_land_agent_space = space_bonus(["vegetable_seeds"], {"grain": 1})["space_used"]

compendium_card("E147", hooks={"play": on_play_gain({"vegetable": 1}),
                               "space_used": _land_agent_space})


# ── E150 Baker ────────────────────────────────────────────────────────
def _bake_specs(player):
    """Bake-capable sources this player owns: id -> (limit, food_per_grain).
    Mirrors engine._bake_spec_of, which cards.py cannot call directly."""
    specs = {}
    for imp in player["improvements"]:
        b = MAJOR_IMPROVEMENTS[imp].get("bake")
        if b:
            specs[imp] = b
    for inst in cards.in_play(player):
        b = cards.spec(inst).get("bake")
        if b:
            specs[inst["id"]] = b
    return specs


def _local_bake(player, bake, ctx):
    """Replica of engine._do_bake for a card-granted bake action that
    doesn't consume a work-phase action space."""
    specs = _bake_specs(player)
    total_grain = 0
    food = 0
    for key, count in bake.items():
        spec = specs.get(key)
        if not spec or not isinstance(count, int) or count < 1:
            raise ValueError(f"{key} cannot bake bread")
        limit, value = spec
        if limit is not None and count > limit:
            raise ValueError(f"That oven bakes at most {limit} grain")
        total_grain += count
        food += count * value
    if total_grain == 0:
        return
    if player["resources"]["grain"] < total_grain:
        raise ValueError("Not enough grain")
    food += cards.bake_bonus(player, total_grain)
    player["resources"]["grain"] -= total_grain
    player["resources"]["food"] += food
    ctx["log"].append(f"{player['name']} bakes {total_grain} grain into "
                      f"{food} food (Baker)")


def _baker_play(state, player, inst, ctx):
    bake = (ctx.get("params") or {}).get("bake")
    if bake:
        _local_bake(player, bake, ctx)


def _baker_available(state, player, inst):
    if state["phase"] != "feeding":
        return False
    if inst["data"].get("used_harvest") == state.get("harvest_index"):
        return False
    return player["resources"]["grain"] >= 1 and bool(_bake_specs(player))


def _baker_apply(state, player, inst, ctx):
    bake = (ctx.get("params") or {}).get("bake")
    if not bake:
        raise ValueError("Baker: choose grain to bake (params.bake)")
    _local_bake(player, bake, ctx)
    inst["data"]["used_harvest"] = state["harvest_index"]


compendium_card(
    "E150",
    hooks={"play": _baker_play},
    card_action={"available": _baker_available, "apply": _baker_apply,
                "description": "Bake bread without an action space "
                               "(once per harvest)"})


# ── E151 Master Builder ───────────────────────────────────────────────
compendium_card("E151", card_action={
    "available": _free_room_available(5), "apply": _free_room_apply,
    "description": "Extend your home by 1 free room (once per game, "
                   "needs 5+ rooms)"})


# ── E152 Berry Picker ─────────────────────────────────────────────────
compendium_card("E152", hooks=take_bonus(["wood"], {"food": 1}))


# ── E153 Mendicant ────────────────────────────────────────────────────
compendium_card("E153", score_bonus=lambda s, p, i: 3 * min(p["begging"], 2))


# ── E154 Master Brewer ────────────────────────────────────────────────
compendium_card("E154", conversions=[
    {"give": {"grain": 1}, "get": {"food": 3}, "per_harvest": 1}])


# ── E157 Thatcher ─────────────────────────────────────────────────────
# The named original majors (Water Mill, Half-timbered House, Chicken
# Coop, Holiday House, Mansion, Corn Storehouse) have no equivalent in
# this engine and none of this engine's majors cost reed, so only the
# room/renovation reed discount applies; nothing is silently dropped.
def _thatcher_mod(state, player, kind, cost, ctx):
    if kind in ("room", "renovation") and cost.get("reed"):
        cost = dict(cost)
        n = ctx.get("count", 1) if kind == "room" else 1
        cost["reed"] = max(0, cost["reed"] - n)
    return cost

compendium_card("E157", cost_mod=_thatcher_mod)


# ── E158 Turner ───────────────────────────────────────────────────────
def _turner_available(state, player, inst):
    return player["resources"]["wood"] >= 1


def _turner_apply(state, player, inst, ctx):
    n = (ctx.get("params") or {}).get("count", 1)
    if not isinstance(n, int) or n < 1 or n > player["resources"]["wood"]:
        raise ValueError("Turner: choose a valid wood count (params.count)")
    player["resources"]["wood"] -= n
    player["resources"]["food"] += n
    ctx["log"].append(f"{player['name']}'s Turner converts {n} wood to "
                      f"{n} food")

compendium_card("E158", card_action={
    "available": _turner_available, "apply": _turner_apply,
    "description": "Convert wood to food (1:1)"})


# ── E160 Farmer ───────────────────────────────────────────────────────
def _farmer_fences(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    if not inst["data"].get("triggered"):
        inst["data"]["triggered"] = True
        add_goods(ctx["extra"], {"boar": 1})
        ctx["log"].append(f"{player['name']}'s Farmer grants 1 wild boar")
    else:
        add_goods(ctx["extra"], {"cattle": 1})
        ctx["log"].append(f"{player['name']}'s Farmer grants 1 cattle")

compendium_card("E160", hooks={"fences_built": _farmer_fences})


# ── E162 Meat Seller ──────────────────────────────────────────────────
_MEAT_SELLER_RATES = {"sheep": 2, "boar": 3, "cattle": 4}


def _meat_seller_available(state, player, inst):
    if not any(imp in player["improvements"]
              for imp in ("clay_oven", "stone_oven")):
        return False
    totals = cards.animal_totals_of(player)
    return any(totals[a] > 0 for a in ANIMAL_TYPES)


def _meat_seller_apply(state, player, inst, ctx):
    params = ctx.get("params") or {}
    animal = params.get("animal")
    count = params.get("count", 1)
    if animal not in ANIMAL_TYPES or not isinstance(count, int) or count < 1:
        raise ValueError("Meat Seller: choose params.animal and params.count")
    if cards.animal_totals_of(player)[animal] < count:
        raise ValueError(f"You do not have {count} {animal}")
    _remove_animal(player, animal, count)
    food = count * _MEAT_SELLER_RATES[animal]
    player["resources"]["food"] += food
    ctx["log"].append(f"{player['name']}'s Meat Seller converts {count} "
                      f"{animal} to {food} food")

compendium_card("E162", card_action={
    "available": _meat_seller_available, "apply": _meat_seller_apply,
    "description": "Convert animals to food (requires a clay/stone oven)"})


# ── E164 Master Forester ────────────────────────────────────────────────
# Adds an extra forest-style action space that gives 2 wood per round
# (card_space "acc"), open to all -- but unlike a plain accumulation
# space, a non-owner placer must pay the owner 2 food first ("The food
# must be paid before the wood is collected"; the owner pays nothing for
# their own use). "acc" still drives replenishment/the usable-gate's
# supply check; "resolve" is required on top of it to implement the toll
# and to read/clear the supply itself (a resolve fn skips the generic
# accumulation-consuming branch entirely, see GUIDE.md's card_space
# section), which also naturally satisfies the ruling that a same-use
# bonus (e.g. Berry Picker) can't retroactively cover the toll -- the
# toll is deducted before this fn returns, and any other card's
# space_used-triggered bonus only fires after the engine credits the
# return value.
def _master_forester_resolve(state, player, inst, action, log):
    owner = cards.card_space_owner(state, inst)
    space = next(s for s in state["action_spaces"]
                if s["id"] == f"card:{inst['id']}")
    goods = {k: v for k, v in space["supply"].items() if v}
    if player is not owner:
        if player["resources"]["food"] < 2:
            raise ValueError("You must pay 2 food to use the Master Forester")
        player["resources"]["food"] -= 2
        cards.grant_goods(state, owner, {"food": 2}, log)
        log.append(f"{player['name']} pays {owner['name']} 2 food")
    space["supply"] = {}
    if goods:
        log.append(f"{player['name']} takes " + cards.goods_str(goods))
    return goods


compendium_card(
    "E164",
    card_space={"acc": {"wood": 2}, "resolve": _master_forester_resolve},
)


# ── E165 Yeoman Farmer ────────────────────────────────────────────────
def _yeoman_farmer_score(state, player, inst):
    tables = _field_scoring_tables(player)
    return sum(1 for name, amount in tables.items()
              if table_score(name, amount) < 0)

compendium_card("E165", score_bonus=_yeoman_farmer_score)


# ── E166 Undergardener ────────────────────────────────────────────────
compendium_card("E166", hooks=space_bonus(["day_laborer"], {"vegetable": 1}))


# ── E167 Conjurer ─────────────────────────────────────────────────────
compendium_card("E167", hooks=space_bonus(["traveling_players"], {"grain": 1}))


# ── E168 Greengrocer ──────────────────────────────────────────────────
compendium_card("E168", hooks=space_bonus(["grain_seeds"], {"vegetable": 1}))


# ── E169 Storyteller ──────────────────────────────────────────────────
def _storyteller_space(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or ctx["space_id"] != "traveling_players":
        return
    if ctx["goods"].get("food", 0) < 1:
        return
    prompt_choice(state, player, inst["id"],
                 "Storyteller: leave 1 food on the space for 1 vegetable?",
                 ["Decline", "Leave 1 food, take 1 vegetable"])


def _storyteller_choice(state, player, inst, ctx):
    if ctx["index"] == 0 or player["resources"]["food"] < 1:
        return
    space = next((s for s in state["action_spaces"]
                 if s["id"] == "traveling_players"), None)
    player["resources"]["food"] -= 1
    if space is not None:
        space["supply"]["food"] = space["supply"].get("food", 0) + 1
    player["resources"]["vegetable"] += 1
    ctx["log"].append(f"{player['name']}'s Storyteller leaves 1 food for "
                      "1 vegetable")

compendium_card("E169", hooks={"space_used": _storyteller_space},
                resolve_choice=_storyteller_choice)


# ── E170 Estate Manager ───────────────────────────────────────────────
def _estate_manager2_score(state, player, inst):
    mine = cards.animal_totals_of(player)
    for t in ANIMAL_TYPES:
        if any(cards.animal_totals_of(p)[t] > mine[t]
              for p in state["players"] if p is not player):
            return 0
    return {3: 2, 4: 3, 5: 4}.get(state["player_count"], 0)

compendium_card("E170", score_bonus=_estate_manager2_score)


# ── E171 Dock Worker ──────────────────────────────────────────────────
def _dock_worker_available(state, player, inst):
    return player["resources"]["wood"] >= 3 or any(
        player["resources"][g] >= 2 for g in ("clay", "reed", "stone"))


def _dock_worker_apply(state, player, inst, ctx):
    params = ctx.get("params") or {}
    give = params.get("give")
    get = params.get("get")
    if give == "wood":
        if player["resources"]["wood"] < 3 or get not in ("clay", "reed", "stone"):
            raise ValueError("Dock Worker: need 3 wood and a target resource")
        player["resources"]["wood"] -= 3
        player["resources"][get] += 1
    elif give in ("clay", "reed", "stone"):
        if player["resources"][give] < 2 or get not in BUILDING_RESOURCES \
                or get == give:
            raise ValueError("Dock Worker: need 2 of the given resource and "
                             "a different target building resource")
        player["resources"][give] -= 2
        player["resources"][get] += 1
    else:
        raise ValueError("Dock Worker: choose params.give/params.get")
    ctx["log"].append(f"{player['name']}'s Dock Worker converts building "
                      "resources")

compendium_card("E171", card_action={
    "available": _dock_worker_available, "apply": _dock_worker_apply,
    "description": "Convert building resources (3 wood->1 other, or "
                   "2 clay/reed/stone->1 other)"})


# ── E172 Chief ────────────────────────────────────────────────────────
compendium_card("E172", cost={"food": 2}, score_bonus=lambda s, p, i: (
    sum(1 for c in p["cells"] if c["type"] == "room")
    if p["house_type"] == "stone" else 0))


# ── E174 Tutor ────────────────────────────────────────────────────────
def _tutor_score(state, player, inst):
    try:
        idx = player["occupations"].index(inst)
    except ValueError:
        return 0
    return len(player["occupations"]) - idx - 1

compendium_card("E174", score_bonus=_tutor_score)


# ── E175 Hedge Keeper ─────────────────────────────────────────────────
def _e175_hedge_keeper_mod(state, player, kind, cost, ctx):
    if kind == "fences":
        cost = dict(cost)
        free = min(3, ctx.get("count", 0))
        cost["wood"] = max(0, cost.get("wood", 0) - free)
    return cost

compendium_card("E175", cost_mod=_e175_hedge_keeper_mod)


# ── E176 Woodcutter ───────────────────────────────────────────────────
compendium_card("E176", hooks=take_bonus(["wood"], {"wood": 1}))


# ── E177 Wooden Hut Builder ───────────────────────────────────────────
compendium_card("E177", score_bonus=lambda s, p, i: (
    sum(1 for c in p["cells"] if c["type"] == "room")
    if p["house_type"] == "wood" else 0))


# ── E178 Hut Builder ──────────────────────────────────────────────────
def _hut_builder_play(state, player, inst, ctx):
    inst["data"]["eligible"] = state["round"] <= 4


def _hut_builder_round_start(state, player, inst, ctx):
    if state["round"] != 11 or not inst["data"].get("eligible") \
            or inst["data"].get("used") or player["house_type"] == "stone":
        return
    cells = sub_actions.buildable_room_cells(player)
    if not cells:
        return
    sub_actions.build_rooms(state, player, [cells[0]], ctx["log"],
                            cost_override="free")
    inst["data"]["used"] = True
    ctx["log"].append(f"{player['name']}'s Hut Builder adds a free room")

compendium_card("E178", hooks={"play": _hut_builder_play,
                               "round_start": _hut_builder_round_start})


# ── E180 Hobby Farmer ─────────────────────────────────────────────────
def _hobby_farmer_play(state, player, inst, ctx):
    player["resources"]["vegetable"] += 1
    params = ctx.get("params") or {}
    if not params.get("sow") or player["resources"]["vegetable"] < 1:
        ctx["log"].append(f"{player['name']} gets 1 vegetable (Hobby Farmer)")
        return
    if "card" in params:
        cid = params["card"]
        target = next((i for i in cards.card_fields(player)
                       if i["id"] == cid), None)
        if target is None or target["crops"]:
            raise ValueError("Hobby Farmer: invalid card field")
        if "vegetable" not in cards.CARDS[cid]["field"]["crops"]:
            raise ValueError("Hobby Farmer: that field can't grow vegetables")
        target["crops"] = {"type": "vegetable", "count": 2}
    else:
        cell = params.get("cell")
        if not isinstance(cell, int) or cell not in _empty_field_cells(player):
            raise ValueError(
                "Hobby Farmer: choose an empty plowed field (params.cell)")
        player["cells"][cell]["crops"] = {"type": "vegetable", "count": 2}
    player["resources"]["vegetable"] -= 1
    ctx["log"].append(f"{player['name']} gets 1 vegetable and sows it "
                      "(Hobby Farmer)")

compendium_card("E180", hooks={"play": _hobby_farmer_play})


# ── E181 Cook ─────────────────────────────────────────────────────────
# Modeled as pre-granting the food this card saves (rather than reducing
# the amount needed, which engine._food_needed has no hook for): the
# harvest_field hook fires once per harvest, right before the feeding
# phase begins, so granting max(adults-2, 0) food here nets the same
# final food/begging outcome as "only 2 people eat 2 food, the rest eat
# 1" would. (Displayed food_needed in the client won't reflect the
# reduction, but the resulting game state does.)
def _cook_food_savings(state, player):
    adults = player["people_total"] - player["newborns"]
    return max(adults - 2, 0)

compendium_card("E181", hooks=harvest_food(_cook_food_savings))


# ── E182 Charcoal Burner ──────────────────────────────────────────────
_BAKE_MAJORS = {imp for imp, spec in MAJOR_IMPROVEMENTS.items()
               if spec.get("bake")}


def _charcoal_burner_improvement(state, player, inst, ctx):
    if ctx.get("improvement") not in _BAKE_MAJORS:
        return
    player["resources"]["food"] += 1
    player["resources"]["wood"] += 1
    ctx["log"].append(f"{player['name']}'s Charcoal Burner grants 1 food "
                      "and 1 wood")

compendium_card("E182", hooks={"improvement_built": _charcoal_burner_improvement})


# ── E183 Basketmaker ──────────────────────────────────────────────────
compendium_card("E183", conversions=[
    {"give": {"reed": 1}, "get": {"food": 3}, "per_harvest": 1}])


# ── E184 Grocer ───────────────────────────────────────────────────────
# DB text lists the pile bottom-to-top; "buy the top item" means the
# LAST-listed good is bought first.
_GROCER_PILE = ("reed", "grain", "stone", "vegetable", "wood", "clay",
               "reed", "vegetable")


def _grocer_available(state, player, inst):
    idx = inst["data"].get("idx", 0)
    return idx < len(_GROCER_PILE) and player["resources"]["food"] >= 1


def _grocer_apply(state, player, inst, ctx):
    idx = inst["data"].get("idx", 0)
    if idx >= len(_GROCER_PILE) or player["resources"]["food"] < 1:
        raise ValueError("Grocer: nothing left to buy, or not enough food")
    good = _GROCER_PILE[idx]
    player["resources"]["food"] -= 1
    player["resources"][good] += 1
    inst["data"]["idx"] = idx + 1
    ctx["log"].append(f"{player['name']}'s Grocer sells 1 {good} for 1 food")

compendium_card("E184", card_action={
    "available": _grocer_available, "apply": _grocer_apply,
    "description": "Buy the top item from the Grocer's pile for 1 food"})


# ── E185 Clay Firer ───────────────────────────────────────────────────
_CLAY_FIRER_RATES = {"2for1": (2, 1), "3for2": (3, 2)}


def _clay_firer_available(state, player, inst):
    return player["resources"]["clay"] >= 2


def _clay_firer_apply(state, player, inst, ctx):
    mode = (ctx.get("params") or {}).get("mode")
    if mode not in _CLAY_FIRER_RATES:
        raise ValueError("Clay Firer: choose params.mode '2for1' or '3for2'")
    clay, stone = _CLAY_FIRER_RATES[mode]
    if player["resources"]["clay"] < clay:
        raise ValueError("Not enough clay")
    player["resources"]["clay"] -= clay
    player["resources"]["stone"] += stone
    ctx["log"].append(f"{player['name']}'s Clay Firer converts {clay} clay "
                      f"to {stone} stone")

compendium_card("E185", card_action={
    "available": _clay_firer_available, "apply": _clay_firer_apply,
    "description": "Convert clay to stone (2:1 or 3:2)"})


# ── E186 Clay Seller ──────────────────────────────────────────────────
_CLAY_SELLER_RATES = {
    "sheep": (2, "sheep", 1), "reed": (2, "reed", 1),
    "boar": (3, "boar", 1), "stone": (3, "stone", 1),
    "cattle": (4, "cattle", 1),
}


def _clay_seller_available(state, player, inst):
    return player["resources"]["clay"] >= 2


def _clay_seller_apply(state, player, inst, ctx):
    option = (ctx.get("params") or {}).get("option")
    if option not in _CLAY_SELLER_RATES:
        raise ValueError("Clay Seller: choose params.option")
    clay, good, amount = _CLAY_SELLER_RATES[option]
    if player["resources"]["clay"] < clay:
        raise ValueError("Not enough clay")
    player["resources"]["clay"] -= clay
    if good in ANIMAL_TYPES:
        add_goods(ctx["extra"], {good: amount})
    else:
        player["resources"][good] += amount
    ctx["log"].append(f"{player['name']}'s Clay Seller converts {clay} clay "
                      f"to {amount} {good}")

compendium_card("E186", card_action={
    "available": _clay_seller_available, "apply": _clay_seller_apply,
    "description": "Convert clay to sheep/reed/boar/stone/cattle"})


# ── E187 Clay Deliveryman ─────────────────────────────────────────────
compendium_card("E187", hooks=schedule_on_play(
    "clay", fixed_rounds=tuple(range(6, 15))))


# ── E188 Clay Mixer ───────────────────────────────────────────────────
def _clay_mixer_space(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    goods = ctx["goods"]
    if goods.get("clay") and all(k == "clay" or not v for k, v in goods.items()):
        add_goods(ctx["extra"], {"clay": 2})
        ctx["log"].append(f"{player['name']}'s Clay Mixer adds 2 clay")

compendium_card("E188", hooks={"space_used": _clay_mixer_space})


# ── E189 Lord of the Manor ────────────────────────────────────────────
def _lord_of_manor_score(state, player, inst):
    tables = _field_scoring_tables(player)
    count = sum(1 for name, amount in tables.items()
               if table_score(name, amount) == 4)
    pasture_cells = {i for pa in compute_pastures(player) for i in pa}
    fenced_stables = sum(1 for i in pasture_cells if player["cells"][i]["stable"])
    if min(fenced_stables, 4) == 4:
        count += 1
    return count

compendium_card("E189", score_bonus=_lord_of_manor_score)


# ── E190 Maid ─────────────────────────────────────────────────────────
def _schedule_maid_food(state, player, ctx):
    rnd = state["round"]
    targets = list(range(rnd + 1, TOTAL_ROUNDS + 1))
    for r in targets:
        slot = state["round_goods"].setdefault(str(r), {}) \
            .setdefault(str(player["index"]), {})
        add_goods(slot, {"food": 1})
    if targets:
        ctx["log"].append(f"{player['name']}'s Maid places 1 food on rounds "
                          f"{', '.join(map(str, targets))}")


def _maid_play(state, player, inst, ctx):
    if player["house_type"] in ("clay", "stone"):
        _schedule_maid_food(state, player, ctx)
    else:
        inst["data"]["pending"] = True


def _maid_renovate(state, player, inst, ctx):
    if inst["data"].get("pending") and player["house_type"] in ("clay", "stone"):
        inst["data"]["pending"] = False
        _schedule_maid_food(state, player, ctx)

compendium_card("E190", hooks={"play": _maid_play, "renovate": _maid_renovate})


# ── E191 Mason ────────────────────────────────────────────────────────
compendium_card("E191", card_action={
    "available": _free_room_available(4, "stone"), "apply": _free_room_apply,
    "description": "Extend your stone house by 1 free room (once per "
                   "game, needs 4+ rooms)"})


# ── E194 Plough Driver ────────────────────────────────────────────────
# Auto-applies a best default (plow the first available cell) rather
# than prompting, per decks/GUIDE.md's round_start rule.
def _plough_driver_round_start(state, player, inst, ctx):
    if player["house_type"] != "stone" or player["resources"]["food"] < 1:
        return
    cells = plowable_cells(player)
    if not cells:
        return
    player["resources"]["food"] -= 1
    player["cells"][cells[0]]["type"] = "field"
    ctx["log"].append(f"{player['name']}'s Plough Driver plows a field for "
                      "1 food")

compendium_card("E194", hooks={"round_start": _plough_driver_round_start})


# ── E195 Plough Maker ─────────────────────────────────────────────────
def _plough_maker_space(state, player, inst, ctx):
    if ctx["actor"] != player["index"] \
            or ctx["space_id"] not in ("farmland", "cultivation"):
        return
    if player["resources"]["food"] < 1:
        return
    cells = plowable_cells(player)
    if not cells:
        return
    prompt_choice(state, player, inst["id"],
                 "Plough Maker: pay 1 food to plow an additional field?",
                 ["Decline"] + [f"Plow cell {c}" for c in cells],
                 data={"cells": cells})


def _plough_maker_choice(state, player, inst, ctx):
    if ctx["index"] == 0:
        return
    cell = ctx["data"]["cells"][ctx["index"] - 1]
    if cell not in plowable_cells(player) or player["resources"]["food"] < 1:
        return
    player["resources"]["food"] -= 1
    player["cells"][cell]["type"] = "field"
    ctx["log"].append(f"{player['name']}'s Plough Maker plows an additional "
                      "field for 1 food")

compendium_card("E195", hooks={"space_used": _plough_maker_space},
                resolve_choice=_plough_maker_choice)


# ── E196 Mushroom Collector ───────────────────────────────────────────
def _mushroom_collector_space(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or ctx["space_id"] not in WOOD_SPACES:
        return
    if ctx["goods"].get("wood", 0) < 1:
        return
    prompt_choice(state, player, inst["id"],
                 "Mushroom Collector: leave 1 wood on the space for 2 food?",
                 ["Decline", "Leave 1 wood, take 2 food"],
                 data={"space_id": ctx["space_id"]})


def _mushroom_collector_choice(state, player, inst, ctx):
    if ctx["index"] == 0 or player["resources"]["wood"] < 1:
        return
    space = next((s for s in state["action_spaces"]
                 if s["id"] == ctx["data"]["space_id"]), None)
    player["resources"]["wood"] -= 1
    if space is not None:
        space["supply"]["wood"] = space["supply"].get("wood", 0) + 1
    player["resources"]["food"] += 2
    ctx["log"].append(f"{player['name']}'s Mushroom Collector leaves 1 wood "
                      "for 2 food")

compendium_card("E196", hooks={"space_used": _mushroom_collector_space},
                resolve_choice=_mushroom_collector_choice)


# ── E197 Braggart ─────────────────────────────────────────────────────
def _e197_braggart_score(state, player, inst):
    n = len(player["improvements"]) + len(player["minors"])
    for minimum, pts in ((9, 9), (8, 7), (7, 5), (6, 3), (5, 1)):
        if n >= minimum:
            return pts
    return 0

compendium_card("E197", score_bonus=_e197_braggart_score)


# ── E199 Renovator ────────────────────────────────────────────────────
def _renovator_mod(state, player, kind, cost, ctx):
    if kind == "renovation":
        cost = dict(cost)
        for material in ("clay", "stone"):
            if cost.get(material):
                cost[material] = max(0, cost[material] - 2)
    return cost

compendium_card("E199", cost_mod=_renovator_mod)


# ── E200 Conservator ──────────────────────────────────────────────────
def _conservator_cost(state, player):
    rooms = sum(1 for c in player["cells"] if c["type"] == "room")
    return cards.modified_cost(state, player, "renovation",
                               {"stone": rooms, "reed": 1})


def _conservator_available(state, player, inst):
    if player["house_type"] != "wood":
        return False
    cost = _conservator_cost(state, player)
    return all(player["resources"].get(k, 0) >= v for k, v in cost.items())


def _conservator_apply(state, player, inst, ctx):
    if player["house_type"] != "wood":
        raise ValueError("Conservator: your house is not a wooden hut")
    cost = _conservator_cost(state, player)
    for res, amount in cost.items():
        if player["resources"].get(res, 0) < amount:
            raise ValueError(f"Not enough {res}")
    for res, amount in cost.items():
        player["resources"][res] -= amount
    player["house_type"] = "stone"
    ctx["log"].append(f"{player['name']} renovates straight to stone "
                      "(Conservator)")
    inner = {"free_stable_cell": None, "log": ctx["log"],
             "actor": player["index"], "extra": {}}
    cards.fire_player(state, player, "renovate", inner)
    add_goods(ctx["extra"], inner["extra"])

compendium_card("E200", card_action={
    "available": _conservator_available, "apply": _conservator_apply,
    "description": "Renovate straight to a stone house"})


# ── E201 Cattle Whisperer ─────────────────────────────────────────────
def _cattle_whisperer_play(state, player, inst, ctx):
    rnd = state["round"]
    targets = [r for r in (rnd + 5, rnd + 9) if r <= TOTAL_ROUNDS]
    inst["data"]["targets"] = targets
    if targets:
        ctx["log"].append(
            f"{player['name']}'s Cattle Whisperer marks rounds "
            f"{', '.join(map(str, targets))} for cattle")


def _cattle_whisperer_round_start(state, player, inst, ctx):
    if state["round"] not in inst["data"].get("targets", ()):
        return
    from server.agricola.engine import AgricolaEngine
    if AgricolaEngine()._place_newborn_animal(state, player, "cattle"):
        ctx["log"].append(f"{player['name']}'s Cattle Whisperer delivers "
                          "1 cattle")

compendium_card("E201", hooks={"play": _cattle_whisperer_play,
                               "round_start": _cattle_whisperer_round_start})


# ── E202 Seasonal Worker ──────────────────────────────────────────────
def _seasonal_worker_space(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or ctx["space_id"] != "day_laborer":
        return
    if state["round"] < 6:
        add_goods(ctx["extra"], {"grain": 1})
        ctx["log"].append(f"{player['name']}'s Seasonal Worker adds 1 grain")
        return
    prompt_choice(state, player, inst["id"],
                 "Seasonal Worker: take 1 grain or 1 vegetable?",
                 ["1 grain", "1 vegetable"])


def _seasonal_worker_choice(state, player, inst, ctx):
    good = "grain" if ctx["index"] == 0 else "vegetable"
    player["resources"][good] += 1
    ctx["log"].append(f"{player['name']}'s Seasonal Worker adds 1 {good}")

compendium_card("E202", hooks={"space_used": _seasonal_worker_space},
                resolve_choice=_seasonal_worker_choice)


# ── E203 Shepherd ─────────────────────────────────────────────────────
def _e203_shepherd_harvest(state, player, inst, ctx):
    if cards.animal_totals_of(player)["sheep"] < 4:
        return
    from server.agricola.engine import AgricolaEngine
    if AgricolaEngine()._place_newborn_animal(state, player, "sheep"):
        ctx["log"].append(f"{player['name']}'s Shepherd breeds an extra "
                          "lamb")

compendium_card("E203", hooks={"harvest_field": _e203_shepherd_harvest})


# ── E204 Master Shepherd ──────────────────────────────────────────────
def _master_shepherd_play(state, player, inst, ctx):
    rnd = state["round"]
    targets = list(range(rnd + 1, min(TOTAL_ROUNDS, rnd + 3) + 1))
    inst["data"]["targets"] = targets
    if targets:
        ctx["log"].append(
            f"{player['name']}'s Master Shepherd marks rounds "
            f"{', '.join(map(str, targets))} for sheep")


def _master_shepherd_round_start(state, player, inst, ctx):
    if state["round"] not in inst["data"].get("targets", ()):
        return
    from server.agricola.engine import AgricolaEngine
    if AgricolaEngine()._place_newborn_animal(state, player, "sheep"):
        ctx["log"].append(f"{player['name']}'s Master Shepherd delivers "
                          "1 sheep")

compendium_card("E204", hooks={"play": _master_shepherd_play,
                               "round_start": _master_shepherd_round_start})


# ── E205 Reed Collector ───────────────────────────────────────────────
compendium_card("E205", hooks=schedule_on_play("reed", rounds_ahead=4))


# ── E206 Swineherd ────────────────────────────────────────────────────
compendium_card("E206", hooks=space_bonus(["pig_market"], {"boar": 1}))


# ── E207 Stablehand ───────────────────────────────────────────────────
def _stablehand_fences(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    if sum(1 for c in player["cells"] if c["stable"]) >= MAX_STABLES:
        return
    cells = [i for i, c in enumerate(player["cells"])
            if c["type"] == "empty" and not c["stable"]]
    if not cells:
        return
    prompt_choice(state, player, inst["id"],
                 "Stablehand: build a free stable?",
                 ["Decline"] + [f"Build at cell {c}" for c in cells],
                 data={"cells": cells})


def _stablehand_choice(state, player, inst, ctx):
    if ctx["index"] == 0:
        return
    cell = ctx["data"]["cells"][ctx["index"] - 1]
    c = player["cells"][cell]
    if c["type"] != "empty" or c["stable"] or \
            sum(1 for x in player["cells"] if x["stable"]) >= MAX_STABLES:
        return
    c["stable"] = True
    ctx["log"].append(f"{player['name']}'s Stablehand builds a free stable")

compendium_card("E207", hooks={"fences_built": _stablehand_fences},
                resolve_choice=_stablehand_choice)


# ── E209 Quarryman ────────────────────────────────────────────────────
def _quarryman_available(state, player, inst):
    return player["resources"]["stone"] >= 1


def _quarryman_apply(state, player, inst, ctx):
    n = (ctx.get("params") or {}).get("count", 1)
    if not isinstance(n, int) or n < 1 or n > player["resources"]["stone"]:
        raise ValueError("Quarryman: choose a valid stone count (params.count)")
    player["resources"]["stone"] -= n
    player["resources"]["food"] += 2 * n
    ctx["log"].append(f"{player['name']}'s Quarryman converts {n} stone to "
                      f"{2 * n} food")

compendium_card("E209", card_action={
    "available": _quarryman_available, "apply": _quarryman_apply,
    "description": "Convert stone to food (1:2)"})


# ── E210 Stone Carrier ────────────────────────────────────────────────
def _stone_carrier_space(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or not ctx["goods"].get("stone"):
        return
    other_building = any(ctx["goods"].get(g) for g in ("wood", "clay", "reed"))
    if not other_building:
        add_goods(ctx["extra"], {"stone": 1})
        ctx["log"].append(f"{player['name']}'s Stone Carrier adds 1 stone")
        return
    if player["resources"]["food"] >= 1:
        prompt_choice(state, player, inst["id"],
                     "Stone Carrier: pay 1 food for 1 additional stone?",
                     ["Decline", "Pay 1 food"])


def _stone_carrier_choice(state, player, inst, ctx):
    if ctx["index"] == 0 or player["resources"]["food"] < 1:
        return
    player["resources"]["food"] -= 1
    player["resources"]["stone"] += 1
    ctx["log"].append(f"{player['name']}'s Stone Carrier buys 1 stone for "
                      "1 food")

compendium_card("E210", hooks={"space_used": _stone_carrier_space},
                resolve_choice=_stone_carrier_choice)


# ── E211 Stonecutter ──────────────────────────────────────────────────
def _e211_stonecutter_mod(state, player, kind, cost, ctx):
    if kind in ("room", "renovation", "improvement") and cost.get("stone"):
        cost = dict(cost)
        n = ctx.get("count", 1) if kind == "room" else 1
        cost["stone"] -= n
    return cost

compendium_card("E211", cost_mod=_e211_stonecutter_mod)


# ── E212 Dancer ───────────────────────────────────────────────────────
def _dancer_space(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or ctx["space_id"] != "traveling_players":
        return
    got = ctx["goods"].get("food", 0)
    if 0 < got < 4:
        add_goods(ctx["extra"], {"food": 4 - got})
        ctx["log"].append(f"{player['name']}'s Dancer tops up to 4 food")

compendium_card("E212", hooks={"space_used": _dancer_space})


# ── E213 Stockman ─────────────────────────────────────────────────────
def _stockman_stables(state, player, inst, ctx):
    total_after = sum(1 for c in player["cells"] if c["stable"])
    total_before = total_after - len(ctx["cells"])
    gains = {2: "cattle", 3: "boar", 4: "sheep"}
    got = {}
    for n in range(total_before + 1, total_after + 1):
        animal = gains.get(n)
        if animal:
            got[animal] = got.get(animal, 0) + 1
    if got:
        add_goods(ctx["extra"], got)
        ctx["log"].append(f"{player['name']}'s Stockman grants "
                          + goods_str(got))

compendium_card("E213", hooks={"stable_built": _stockman_stables})


# ── E214 Potter ───────────────────────────────────────────────────────
compendium_card("E214", conversions=[
    {"give": {"clay": 1}, "get": {"food": 2}, "per_harvest": 1}])


# ── E217 Reeve ────────────────────────────────────────────────────────
def _reeve_play(state, player, inst, ctx):
    rnd = state["round"]
    wood = 4 if rnd < 6 else 3 if rnd <= 8 else 2 if rnd <= 11 else \
        1 if rnd <= 13 else 0
    if wood:
        player["resources"]["wood"] += wood
        ctx["log"].append(f"{player['name']}'s Reeve grants {wood} wood")


def _reeve_score(state, player, inst):
    mine = len(player["occupations"])
    most = max(len(p["occupations"]) for p in state["players"])
    return 3 if mine == most else 0

compendium_card("E217", hooks={"play": _reeve_play}, score_bonus=_reeve_score)


# ── E218 Carpenter ────────────────────────────────────────────────────
def _e218_carpenter_mod(state, player, kind, cost, ctx):
    if kind == "room":
        cost = dict(cost)
        discount = 2 * ctx.get("count", 1)
        for material in ("wood", "clay", "stone"):
            if cost.get(material):
                cost[material] = max(0, cost[material] - discount)
    return cost

compendium_card("E218", cost_mod=_e218_carpenter_mod)


# ── E341 Guildmaster ──────────────────────────────────────────────────
_GUILDMASTER_MAJORS = {"joinery": ("wood", 4), "pottery": ("clay", 4),
                       "basketmaker": ("reed", 3)}
_GUILDMASTER_OCCS = {"E214": ("clay", 4), "E183": ("reed", 3)}


def _guildmaster_play(state, player, inst, ctx):
    gained = {}
    for imp, (good, _amt) in _GUILDMASTER_MAJORS.items():
        if imp in player["improvements"]:
            gained[good] = gained.get(good, 0) + 2
    for cid, (good, _amt) in _GUILDMASTER_OCCS.items():
        if any(i["id"] == cid for i in player["occupations"]):
            gained[good] = gained.get(good, 0) + 2
    if gained:
        add_goods(player["resources"], gained)
        ctx["log"].append(f"{player['name']}'s Guildmaster grants "
                          + goods_str(gained))


def _guildmaster_improvement(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    entry = _GUILDMASTER_MAJORS.get(ctx.get("improvement"))
    if not entry:
        return
    good, amount = entry
    player["resources"][good] += amount
    ctx["log"].append(f"{player['name']}'s Guildmaster grants {amount} {good}")


def _guildmaster_occ(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    entry = _GUILDMASTER_OCCS.get(ctx.get("card_id"))
    if not entry:
        return
    good, amount = entry
    player["resources"][good] += amount
    ctx["log"].append(f"{player['name']}'s Guildmaster grants {amount} {good}")

compendium_card("E341", hooks={"play": _guildmaster_play,
                               "improvement_built": _guildmaster_improvement,
                               "occupation_played": _guildmaster_occ})
