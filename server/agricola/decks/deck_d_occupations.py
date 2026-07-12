"""Deck D occupations (codes D085..D167 from the compendium DB).

Data-quality note: several deck-D compendium entries have their `text` (and
especially `rulings`) fields corrupted by a PDF-parsing artifact — a
subsequent card's meta/body got appended to the previous card's `text` or
`ruling_items` list when the parser's code-line detection missed a
boundary (see tools/parse_compendium.py, the "name_stash"/"ruling_items"
fallback paths). The tell is a clean sentence, then a *repeated*
"(N-M players)" tag mid-string introducing an unrelated mechanic. Where
that happens, only the text up to the first such embedded tag is treated
as this card's real effect; everything after is discarded as bleed from a
neighboring entry, not implemented under this code. This is called out
per-card below wherever it applies.

FotM-only rulings (Farmers of the Moor variants) are ignored per
GUIDE.md (deck M / FotM content is out of scope).
"""

from server.agricola.cards import (
    compendium_card, card, CARDS, new_instance, spec, in_play,
    add_goods, goods_str, prompt_choice, parse_cost,
    take_bonus, space_bonus, round_income, schedule_on_play,
    harvest_food, on_play_gain, animal_totals_of,
    needs_occupations, exact_occupations, needs_grain_field, combine,
)
from server.agricola import cards, sub_actions
from server.agricola.state import (
    ANIMAL_TYPES, BUILDING_RESOURCES, MAJOR_IMPROVEMENTS, MAX_PEOPLE,
    MAX_STABLES, TOTAL_ROUNDS,
    table_score, compute_pastures, animal_counts,
)

UNIMPLEMENTED = {
    "D086": "Sheep Agent: 'keep 1 sheep on each occupation card' needs a "
            "per-occupation-card animal-capacity mode; house_capacity only "
            "supports a flat int or 'per_room'.",
    "D093": "Sheep Inspector: 'return another person you placed home' "
            "requires vacating an occupied action space mid-round -- the "
            "replacement-effect gap noted in CARDS.md as a known future "
            "engine feature, not yet supported.",
    "D094": "Henpecked Husband: same 'return a placed person home' gap "
            "as D093 -- no way to vacate an occupied action space.",
    "D102": "Sample Stable Maker: fires 'at the start of each returning "
            "home phase', a phase this engine has no hook for (only "
            "round_start/harvest_field/etc.), and requires removing a "
            "built stable, akin to the unsupported removal of built "
            "structures.",
    "D103": "Canal Boatman: 'place another person on this card' is an "
            "extra person placement -- explicitly unsupported (guest "
            "tokens / extra people).",
    "D112": "Young Farmer: 'afterward you can take a Sow action' needs an "
            "extra-action-after-this-space mechanism; only bake_on_spaces "
            "is wired for that (Threshing Board's shape), no analogous "
            "sow-on-spaces hook exists.",
    "D113": "Food Merchant: 'for each grain you harvest, you can buy 1 "
            "vegetable for food' is an interactive per-harvest purchase "
            "decision with no clean hook (not a trigger->gain shape).",
    "D116": "Tree Inspector: 'this card is an accumulation space for you "
            "only' is a private per-player action space; the engine's "
            "action spaces are a single shared/global list.",
    "D127": "Hardworking Man: 'this card is an action space for you only' "
            "-- same private-action-space gap as D116.",
    "D131": "Craftsmanship Promoter: 'build majors from the bottom row "
            "even via a Minor Improvement action' assumes an original-"
            "edition major-improvement-row / minor-vs-major action "
            "distinction this engine doesn't model (majors and minors "
            "already share one action space here).",
    "D138": "Pet Lover: 'leave it on the space and get one from the "
            "supply instead' requires rescinding the space's automatic "
            "animal grant -- hooks can only add via ctx['extra'], there's "
            "no veto/cancel of the base grant.",
    "D142": "Potato Planter: needs an end-of-work-phase snapshot of who "
            "occupies Clay Pit vs Reed Bank, but action-space occupancy "
            "is reset before round_start fires, and there's no "
            "end-of-work-phase hook.",
    "D147": "Trap Builder: schedules a future round's wild boar via the "
            "round_goods mechanism, but round_goods delivers straight "
            "into player resources (bypassing accommodation) -- unsafe "
            "for animals, so the wild-boar half of the reward can't be "
            "scheduled faithfully.",
    "D148": "Domestician Expert: 'keep 2 sheep on the border between "
            "each pair of orthogonally adjacent rooms' needs a per-"
            "adjacent-room-pair capacity model; house_capacity only "
            "supports a flat int or 'per_room'.",
    "D150": "Godly Spouse: 'return the first person you placed home' -- "
            "same replacement-effect gap as D093/D094.",
    "D151": "Spin Doctor: 'place another person on an action space of "
            "your choice' is an extra person placement -- explicitly "
            "unsupported.",
    "D159": "Reed Seller: lets *other* players counter-offer to buy the "
            "reed before the conversion happens -- no engine mechanism "
            "for cross-player intervention on a card ability.",
    "D161": "Cabbage Buyer: reassessed now that renovate_any (broadcast "
            "twin) exists -- it fixes the *visibility* half of the "
            "original gap (any player's renovation is now observable), "
            "but the SECOND half stands: pricing depends on whether the "
            "SAME action also built 0/1 minor/1 major improvement "
            "immediately after, and renovate's ctx carries no space_id "
            "or other action-boundary marker to correlate with a later "
            "minor_played/improvement_built broadcast. space_used fires "
            "once per placement action and could serve as that boundary "
            "for the house_redevelopment-space case, but not every "
            "sub_actions.renovate call site is followed by one at all -- "
            "card_action- and resolve_choice-driven renovates (Builder's "
            "Trowel E50, Renovation Company A013, B023 Final Scenario) "
            "happen entirely outside _resolve_space, so a "
            "'no build followed' pending flag could dangle indefinitely "
            "or misattribute a later, unrelated build. Still gated.",
    "D164": "Pet Grower: 'if afterward you have no animal in your house' "
            "depends on state *after* accommodation resolves, but "
            "space_used fires before the just-gained animal is "
            "accommodated -- no hook exists post-accommodation.",
    "D167": "Pure Breeder: 'breed exactly one type of animal' after each "
            "non-harvest round needs a player choice at round_start; "
            "queuing a prompt there (before _advance_work's placement "
            "loop resumes) is an untested combination that risks the "
            "engine treating every player as unable to place and "
            "forfeiting the round -- and there's no action-parameter "
            "channel at round_start to take the choice up front instead.",
}

GOOD_TYPES = BUILDING_RESOURCES + ("food", "grain", "vegetable") + ANIMAL_TYPES


def _remove_one_animal(player, animal_type):
    """Remove exactly 1 animal of `animal_type` from wherever it lives
    (house pets first, then the first matching pasture/stable cell)."""
    if player["pets"].get(animal_type, 0) > 0:
        player["pets"][animal_type] -= 1
        if player["pets"][animal_type] == 0:
            del player["pets"][animal_type]
        return True
    for c in player["cells"]:
        a = c.get("animal")
        if a and a["type"] == animal_type:
            a["count"] -= 1
            if a["count"] <= 0:
                c["animal"] = None
            return True
    return False


# ── D085 Reader ───────────────────────────────────────────────────────
# "As soon as you have 6 occupations in front of you (including this
# one), this card provides room for one person." This is the GUIDE.md
# worked example for a computed extra_rooms fn (engine phase 8): a
# static per-card int can't express the "once you have 6" condition, but
# extra_rooms=fn(state, player, inst) -> int can.
def _reader_extra_rooms(state, player, inst):
    return 1 if len(player["occupations"]) >= 6 else 0

compendium_card("D085", extra_rooms=_reader_extra_rooms)


# ── D088 Millwright ──────────────────────────────────────────────────
# "You immediately get 1 grain. Each time you build fences, stables, and
# rooms, or renovate your house, you can replace up to 2 building
# resources of any type with 1 grain each." Ruling: "1 or 2
# substitutions per type of thing built" -- a genuine per-build-action
# choice (not strictly beneficial: it trades a building resource for a
# food-phase-relevant grain), so it's a payment-channel cost_mod, same
# shape as E36 Clay Roof, just generalized to any of the four building
# resources and to all four buildable-batch kinds.
def _millwright_mod(state, player, kind, cost, ctx):
    if kind not in ("room", "stable", "fences", "renovation"):
        return cost
    payment = ctx.get("payment")
    if not isinstance(payment, dict) or "millwright_grain" not in payment:
        return cost  # not addressed to this card (another card's payment)
    sub = payment["millwright_grain"]
    if not isinstance(sub, dict) or not sub:
        raise ValueError("Millwright: invalid payment")
    cost = dict(cost)
    total = 0
    for res, n in sub.items():
        if res not in BUILDING_RESOURCES:
            raise ValueError("Millwright: invalid payment resource")
        if not isinstance(n, int) or n <= 0 or n > cost.get(res, 0):
            raise ValueError("Millwright: invalid payment amount")
        total += n
    if total > 2:
        raise ValueError("Millwright: invalid payment amount")
    for res, n in sub.items():
        cost[res] -= n
    cost["grain"] = cost.get("grain", 0) + total
    return cost


def _millwright_play(state, player, inst, ctx):
    add_goods(ctx["extra"], {"grain": 1})
    ctx["log"].append(f"{player['name']}'s Millwright grants 1 grain")


compendium_card("D088", hooks={"play": _millwright_play}, cost_mod=_millwright_mod)


# ── D089 Stablehand ──────────────────────────────────────────────────
# "Each time you build at least 1 fence, you can also build a stable
# without paying wood for the stable." (trailing ruling text is bleed
# from a different Stablehand printing (E207) -- ignored.)

def _stablehand_fences(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    stables = sum(1 for c in player["cells"] if c["stable"])
    cells = [i for i, c in enumerate(player["cells"])
             if c["type"] == "empty" and not c["stable"]]
    if stables < MAX_STABLES and cells:
        opts = [f"Cell {i}" for i in cells] + ["Skip"]
        prompt_choice(state, player, inst["id"],
                     "Stablehand: build a free stable?", opts,
                     data={"cells": cells})


def _stablehand_resolve(state, player, inst, ctx):
    data = ctx["data"]
    if ctx["index"] >= len(data["cells"]):
        return
    cell = data["cells"][ctx["index"]]
    c = player["cells"][cell]
    if c["type"] != "empty" or c["stable"]:
        return
    c["stable"] = True
    ctx["log"].append(f"{player['name']}'s Stablehand builds a free stable")
    cards.fire_player(state, player, "stable_built",
                      {"cells": [cell], "log": ctx["log"],
                       "actor": player["index"], "extra": ctx["extra"]})


compendium_card("D089", hooks={"fences_built": _stablehand_fences},
                resolve_choice=_stablehand_resolve)


# ── D092 Child Ombudsman ─────────────────────────────────────────────
# "From round 5 on, if you have room in your house, at the end of each
# person action, you can take a Family Growth action with that person.
# If you do, you get 2 negative points." Modeled as a card_action
# (usable any time on your work turn rather than strictly "right after a
# person action" -- card_action has no narrower timing hook, but it
# never consumes a placement or advances the turn, matching the spirit).

def _child_ombudsman_available(state, player, inst):
    if state["round"] < 5:
        return False
    if player["people_total"] >= MAX_PEOPLE:
        return False
    rooms = (sum(1 for c in player["cells"] if c["type"] == "room")
             + cards.extra_rooms(state, player))
    return rooms > player["people_total"]


def _child_ombudsman_apply(state, player, inst, ctx):
    player["people_total"] += 1
    player["people_placed"] += 1
    player["newborns"] += 1
    inst["data"]["uses"] = inst["data"].get("uses", 0) + 1
    ctx["log"].append(f"{player['name']}'s Child Ombudsman grows the "
                      "family (-2 points)")
    cards.fire(state, "family_growth",
              {"log": ctx["log"], "actor": player["index"], "extra": ctx["extra"]})


compendium_card("D092", card_action={
    "available": _child_ombudsman_available,
    "apply": _child_ombudsman_apply,
    "description": "Free Family Growth (-2 points)",
}, score_bonus=lambda s, p, i: -2 * i["data"].get("uses", 0))


# ── D095 Site Manager ────────────────────────────────────────────────
# DB text concatenates multiple printings under one code (see module
# docstring); only the first, self-contained clause is this card's real
# text: "When you play this card, immediately build a major improvement.
# When paying its cost, you can replace up to 1 building resource of
# each type with 1 food each."
#
# The substitution is scoped to just this one on-play build, not a
# standing ability for the rest of the game -- unlike D088 Millwright's
# genuinely repeatable payment-channel ability, a persistent cost_mod
# keyed on a payment blob would leak: engine._do_improvement threads
# ANY action's own "payment" field into every future Major Improvement
# build, so a spec-level cost_mod recognizing a fixed payment key would
# let the player invoke "Site Manager's" substitution again on a later,
# unrelated improvement purchase. Instead the play hook computes the
# (already-modified-by-other-cards) cost itself, applies the one-time
# substitution inline, and hands sub_actions.build_improvement an exact
# cost_override -- so the ability can never outlive this single call.
def _site_manager_can_afford(player, cost):
    """True if SOME 0-or-1-per-type substitution (each building resource
    swapped for 1 food) makes `cost` affordable."""
    types = [r for r in BUILDING_RESOURCES if cost.get(r, 0) > 0]
    for mask in range(1 << len(types)):
        trial = dict(cost)
        subbed = 0
        for i, r in enumerate(types):
            if mask & (1 << i):
                trial[r] -= 1
                subbed += 1
        if subbed:
            trial["food"] = trial.get("food", 0) + subbed
        if all(player["resources"].get(k, 0) >= v for k, v in trial.items()):
            return True
    return False


def _site_manager_play(state, player, inst, ctx):
    candidates = []
    for imp in state["available_improvements"]:
        base = cards.modified_cost(state, player, "improvement",
                                   MAJOR_IMPROVEMENTS[imp]["cost"],
                                   {"improvement": imp})
        if _site_manager_can_afford(player, base):
            candidates.append(imp)
    if not candidates:
        ctx["log"].append(
            f"{player['name']}'s Site Manager has no affordable major "
            "improvement to build")
        return
    params = ctx.get("params") or {}
    imp = params.get("improvement")
    if imp not in candidates:
        raise ValueError(
            "Site Manager: choose an affordable available major "
            "improvement (params.improvement)")
    cost = dict(cards.modified_cost(state, player, "improvement",
                                    MAJOR_IMPROVEMENTS[imp]["cost"],
                                    {"improvement": imp}))
    sub = params.get("food_sub") or {}
    if not isinstance(sub, dict):
        raise ValueError("Site Manager: invalid food_sub")
    total_food = 0
    for res, n in sub.items():
        if res not in BUILDING_RESOURCES:
            raise ValueError("Site Manager: invalid food_sub resource")
        if not isinstance(n, int) or n <= 0 or n > 1 or n > cost.get(res, 0):
            raise ValueError("Site Manager: invalid food_sub amount")
        cost[res] -= n
        total_food += n
    if total_food:
        cost["food"] = cost.get("food", 0) + total_food
    sub_actions.build_improvement(state, player, imp, ctx["log"],
                                  cost_override=cost)


compendium_card("D095", hooks={"play": _site_manager_play})


# ── D099 Earthenware Potter ──────────────────────────────────────────
# "If you play this card in round 4 or before, after the final harvest,
# you get 1 bonus point for each person for which you then pay 1 clay."

def _earthenware_potter_play(state, player, inst, ctx):
    inst["data"]["round_played"] = state["round"]


def _earthenware_potter_score(state, player, inst):
    if inst["data"].get("round_played", 99) > 4:
        return 0
    return min(player["people_total"], player["resources"]["clay"])


compendium_card("D099", hooks={"play": _earthenware_potter_play},
                score_bonus=_earthenware_potter_score)


# ── D100 Lord of the Manor ───────────────────────────────────────────
# "During scoring, you get 1 bonus point for each scoring category in
# which you score the maximum 4 points. (Also awarded for 4 fenced
# stables.)"

def _lord_of_the_manor_score(state, player, inst):
    cells = player["cells"]
    pastures = compute_pastures(player)
    pasture_cells = {i for p in pastures for i in p}
    animals = animal_counts(player)
    fields = sum(1 for c in cells if c["type"] == "field")
    grain = player["resources"]["grain"]
    vegetable = player["resources"]["vegetable"]
    for c in cells:
        if c["crops"]:
            if c["crops"]["type"] == "grain":
                grain += c["crops"]["count"]
            else:
                vegetable += c["crops"]["count"]
    for fi in cards.card_fields(player):
        if fi["crops"]:
            if fi["crops"]["type"] == "grain":
                grain += fi["crops"]["count"]
            else:
                vegetable += fi["crops"]["count"]
    fenced_stables = sum(1 for i in pasture_cells if cells[i]["stable"])
    values = [
        table_score("fields", fields), table_score("pastures", len(pastures)),
        table_score("grain", grain), table_score("vegetable", vegetable),
        table_score("sheep", animals["sheep"]), table_score("boar", animals["boar"]),
        table_score("cattle", animals["cattle"]), min(fenced_stables, 4),
    ]
    return sum(1 for v in values if v == 4)


compendium_card("D100", score_bonus=_lord_of_the_manor_score)


# ── D105 Sculptor ─────────────────────────────────────────────────────
# "Each time you use a clay accumulation space, you also get 1 food.
# Each time you use a stone accumulation space, you also get 1 grain."

_CLAY_ACC_SPACES = ("clay_pit", "hollow_3p", "hollow_4p")
_STONE_ACC_SPACES = ("western_quarry", "eastern_quarry")


def _sculptor_hook(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    if ctx["space_id"] in _CLAY_ACC_SPACES:
        add_goods(ctx["extra"], {"food": 1})
        ctx["log"].append(f"{player['name']}'s Sculptor adds 1 food")
    elif ctx["space_id"] in _STONE_ACC_SPACES:
        add_goods(ctx["extra"], {"grain": 1})
        ctx["log"].append(f"{player['name']}'s Sculptor adds 1 grain")


compendium_card("D105", hooks={"space_used": _sculptor_hook})


# ── D106 Whisky Distiller ─────────────────────────────────────────────
# "At any time, you can pay 1 grain. If you do, add 2 to the current
# round and place 4 food on the corresponding round space. At the start
# of that round, you get the food."

def _whisky_distiller_available(state, player, inst):
    return (player["resources"]["grain"] >= 1
            and state["round"] + 2 <= TOTAL_ROUNDS)


def _whisky_distiller_apply(state, player, inst, ctx):
    player["resources"]["grain"] -= 1
    target = state["round"] + 2
    slot = state["round_goods"].setdefault(str(target), {}) \
        .setdefault(str(player["index"]), {})
    add_goods(slot, {"food": 4})
    ctx["log"].append(f"{player['name']}'s Whisky Distiller schedules "
                      f"4 food for round {target}")


compendium_card("D106", card_action={
    "available": _whisky_distiller_available,
    "apply": _whisky_distiller_apply,
    "description": "Pay 1 grain: 4 food in 2 rounds",
})


# ── D109 Sowing Master ────────────────────────────────────────────────
# "When you play this card, you immediately get 1 wood. Each time after
# you use an action space with the Sow action, you get 2 food."

def _sowing_master_sow(state, player, inst, ctx):
    player["resources"]["food"] += 2
    ctx["log"].append(f"{player['name']}'s Sowing Master grants 2 food")


compendium_card("D109", hooks={"play": on_play_gain({"wood": 1}),
                               "sow": _sowing_master_sow})


# ── D110 Fish Farmer ──────────────────────────────────────────────────
# "Each time there is 1/2/3+ food on the Fishing accumulation space, you
# get an additional 2 food on Reed Bank/Clay Pit/Grove(->Forest, per the
# card's own errata) accumulation spaces." Read as: whenever Fishing
# currently holds >=1 food, using Reed Bank/Clay Pit/Forest also grants a
# flat 2 food (the "1/2/3+" enumerates Fishing's possible food counts,
# not a tiered reward -- only one reward value is given).

_FISH_FARMER_SPACES = ("forest", "grove", "copse",
                       "clay_pit", "hollow_3p", "hollow_4p", "reed_bank")


def _fish_farmer_hook(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or ctx["space_id"] not in _FISH_FARMER_SPACES:
        return
    fishing = next((s for s in state["action_spaces"] if s["id"] == "fishing"), None)
    if fishing and fishing["supply"].get("food", 0) >= 1:
        add_goods(ctx["extra"], {"food": 2})
        ctx["log"].append(f"{player['name']}'s Fish Farmer adds 2 food")


compendium_card("D110", hooks={"space_used": _fish_farmer_hook})


# ── D117 Wood Expert ─────────────────────────────────────────────────
# "When you play this card, you immediately get 2 wood. Each improvement
# costs you up to 2 wood less, if you pay 1 food instead." (the trailing
# DB text after the second sentence is bleed from unrelated cards, per
# this module's docstring.) Same optional-substitution shape as D088
# Millwright, now expressible via ctx["payment"] (engine phase 7) --
# scoped to kind="improvement" instead of the four building kinds.
def _wood_expert_play(state, player, inst, ctx):
    add_goods(ctx["extra"], {"wood": 2})
    ctx["log"].append(f"{player['name']}'s Wood Expert grants 2 wood")


def _wood_expert_mod(state, player, kind, cost, ctx):
    if kind != "improvement":
        return cost
    payment = ctx.get("payment")
    if not isinstance(payment, dict) or "wood_expert_food" not in payment:
        return cost  # not addressed to this card
    n = payment["wood_expert_food"]
    if not isinstance(n, int) or n <= 0 or n > 2 or n > cost.get("wood", 0):
        raise ValueError("Wood Expert: invalid payment")
    cost = dict(cost)
    cost["wood"] -= n
    cost["food"] = cost.get("food", 0) + n
    return cost

compendium_card("D117", hooks={"play": _wood_expert_play}, cost_mod=_wood_expert_mod)


# ── D122 Clay Seller ──────────────────────────────────────────────────
# "When you play this card, you immediately get 2 clay. At any time, but
# only once per round, you can buy 2 clay for 2 food."

def _clay_seller_reset(state, player, inst, ctx):
    inst["data"]["bought_this_round"] = False


def _clay_seller_available(state, player, inst):
    return (not inst["data"].get("bought_this_round")
            and player["resources"]["food"] >= 2)


def _clay_seller_apply(state, player, inst, ctx):
    player["resources"]["food"] -= 2
    player["resources"]["clay"] += 2
    inst["data"]["bought_this_round"] = True
    ctx["log"].append(f"{player['name']}'s Clay Seller buys 2 clay")


compendium_card("D122", hooks={"play": on_play_gain({"clay": 2}),
                               "round_start": _clay_seller_reset},
                card_action={"available": _clay_seller_available,
                             "apply": _clay_seller_apply,
                             "description": "Buy 2 clay for 2 food"})


# ── D123 Renovation Preparer ──────────────────────────────────────────
# "For each new wood/clay room you build, you get 2 clay/2 stone."

def _renovation_preparer_hook(state, player, inst, ctx):
    n = len(ctx["cells"])
    if player["house_type"] == "wood":
        player["resources"]["clay"] += 2 * n
        ctx["log"].append(f"{player['name']}'s Renovation Preparer grants "
                          f"{2 * n} clay")
    elif player["house_type"] == "clay":
        player["resources"]["stone"] += 2 * n
        ctx["log"].append(f"{player['name']}'s Renovation Preparer grants "
                          f"{2 * n} stone")


compendium_card("D123", hooks={"rooms_built": _renovation_preparer_hook})


# ── D124 Emissary ─────────────────────────────────────────────────────
# "At any time, you can place a good from your supply on this card to
# get 1 stone. You must place different goods on this card. (Food is
# also considered a good.)"

def _emissary_available(state, player, inst):
    placed = inst["data"].get("placed", [])
    totals = animal_totals_of(player)
    for good in GOOD_TYPES:
        if good in placed:
            continue
        have = totals[good] if good in ANIMAL_TYPES else player["resources"][good]
        if have >= 1:
            return True
    return False


def _emissary_apply(state, player, inst, ctx):
    good = (ctx.get("params") or {}).get("good")
    placed = inst["data"].setdefault("placed", [])
    if good not in GOOD_TYPES or good in placed:
        raise ValueError("Emissary: choose a not-yet-placed good (params.good)")
    if good in ANIMAL_TYPES:
        if not _remove_one_animal(player, good):
            raise ValueError(f"Emissary: no {good} to place")
    else:
        if player["resources"][good] < 1:
            raise ValueError(f"Emissary: no {good} to place")
        player["resources"][good] -= 1
    placed.append(good)
    player["resources"]["stone"] += 1
    ctx["log"].append(f"{player['name']}'s Emissary trades 1 {good} for 1 stone")


compendium_card("D124", card_action={
    "available": _emissary_available,
    "apply": _emissary_apply,
    "description": "Place a good (once per type) for 1 stone",
})


# ── D125 Forest Trader ────────────────────────────────────────────────
# "Each time you use a wood or clay accumulation space, you can also buy
# exactly 1 building resource. Wood, clay, and reed cost 1 food each;
# stone costs 2 food."

_FOREST_TRADER_SPACES = ("forest", "grove", "copse",
                         "clay_pit", "hollow_3p", "hollow_4p")
_FOREST_TRADER_COSTS = {"wood": 1, "clay": 1, "reed": 1, "stone": 2}


def _forest_trader_hook(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or ctx["space_id"] not in _FOREST_TRADER_SPACES:
        return
    food = player["resources"]["food"]
    opts, goods = [], []
    for good, cost in _FOREST_TRADER_COSTS.items():
        if food >= cost:
            opts.append(f"Buy 1 {good} ({cost} food)")
            goods.append(good)
    if not opts:
        return
    opts.append("Skip")
    goods.append(None)
    prompt_choice(state, player, inst["id"], "Forest Trader: buy a building "
                 "resource?", opts, data={"goods": goods})


def _forest_trader_resolve(state, player, inst, ctx):
    good = ctx["data"]["goods"][ctx["index"]]
    if good is None:
        return
    cost = _FOREST_TRADER_COSTS[good]
    player["resources"]["food"] -= cost
    add_goods(ctx["extra"], {good: 1})
    ctx["log"].append(f"{player['name']}'s Forest Trader buys 1 {good}")


compendium_card("D125", hooks={"space_used": _forest_trader_hook},
                resolve_choice=_forest_trader_resolve)


# ── D126 Field Cultivator ────────────────────────────────────────────
# "Pile 1 wood, 1 clay, 1 reed, 1 stone, 1 reed, 1 clay, and 1 wood on
# this card. Each time you harvest a field tile, you can also take the
# top good from the pile." harvest_field's ctx["tiles"] now gives the
# count of farmyard field tiles harvested this harvest; each one offers
# one yes/no prompt over the current top of the (palindromic, so
# direction-independent) pile, chained one at a time (K115/K119-style)
# since a decline leaves the pile's top unchanged for the next tile.
_FIELD_CULTIVATOR_PILE = ("wood", "clay", "reed", "stone", "reed", "clay", "wood")

def _field_cultivator_prompt_next(state, player, inst):
    remaining = inst["data"].get("fc_remaining", 0)
    idx = inst["data"].get("fc_idx", 0)
    if remaining <= 0 or idx >= len(_FIELD_CULTIVATOR_PILE):
        inst["data"]["fc_remaining"] = 0
        return
    good = _FIELD_CULTIVATOR_PILE[idx]
    prompt_choice(state, player, inst["id"],
                 f"Field Cultivator: take 1 {good} from the pile?",
                 ["yes", "no"])

def _field_cultivator_harvest_field(state, player, inst, ctx):
    # harvest_field fires once per harvest (not once per tile), and any
    # prompt chain from an earlier harvest is always fully resolved
    # (the game blocks on it) before the next one fires, so fc_remaining
    # is always 0 here.
    n = ctx["tiles"].get("grain", 0) + ctx["tiles"].get("vegetable", 0)
    if n <= 0:
        return
    inst["data"]["fc_remaining"] = n
    _field_cultivator_prompt_next(state, player, inst)

def _field_cultivator_resolve(state, player, inst, ctx):
    idx = inst["data"].get("fc_idx", 0)
    good = _FIELD_CULTIVATOR_PILE[idx]
    if ctx["option"] == "yes":
        player["resources"][good] += 1
        ctx["log"].append(f"{player['name']}'s Field Cultivator grants "
                          f"1 {good}")
        cards.fire_gained(state, player, {good: 1}, "card", ctx["log"])
        inst["data"]["fc_idx"] = idx + 1
    inst["data"]["fc_remaining"] -= 1
    _field_cultivator_prompt_next(state, player, inst)

compendium_card("D126", hooks={"harvest_field": _field_cultivator_harvest_field},
                resolve_choice=_field_cultivator_resolve)


# ── D128 Building Tycoon ─────────────────────────────────────────────
# "Each time after another player builds 1 or more rooms, you can give
# them 1 food to build exactly 1 room yourself. (You must pay the
# building cost of the room.)" rooms_built_any (broadcast twin) makes
# the trigger observable; the room's cell is an open-ended target, so
# this banks a credit -- one per triggering player, since the 1 food is
# owed to THAT specific player -- spent via card_action (House Artist's
# shape). "You must pay the building cost" means no discount
# (cost_override=None still runs the normal build through modified_cost).
def _building_tycoon_rooms_built(state, player, inst, ctx):
    if ctx["actor"] == player["index"]:
        return
    inst["data"].setdefault("credits", []).append(ctx["actor"])
    other = state["players"][ctx["actor"]]
    ctx["log"].append(f"{player['name']}'s Building Tycoon may build a "
                      f"room (after {other['name']}'s room(s))")


def _building_tycoon_available(state, player, inst):
    if not inst["data"].get("credits") or player["resources"]["food"] < 1:
        return False
    return sub_actions.can_build_rooms(state, player)


def _building_tycoon_apply(state, player, inst, ctx):
    credits = inst["data"].get("credits") or []
    if not credits:
        raise ValueError("Building Tycoon: no reactive build available")
    if player["resources"]["food"] < 1:
        raise ValueError("Building Tycoon: requires 1 food to give away")
    cells = (ctx.get("params") or {}).get("cells") or []
    if len(cells) != 1:
        raise ValueError(
            "Building Tycoon: choose exactly 1 room (params.cells)")
    sub_actions.build_rooms(state, player, cells, ctx["log"])
    player["resources"]["food"] -= 1
    other = state["players"][credits.pop(0)]
    cards.grant_goods(state, other, {"food": 1}, ctx["log"])
    ctx["log"].append(f"{player['name']} gives {other['name']} 1 food "
                      "(Building Tycoon)")


compendium_card(
    "D128",
    hooks={"rooms_built_any": _building_tycoon_rooms_built},
    card_action={"available": _building_tycoon_available,
                "apply": _building_tycoon_apply,
                "description": "Give 1 food to build exactly 1 room "
                               "(Building Tycoon, after another player "
                               "builds rooms)"})


# ── D129 Lumber Virtuoso ─────────────────────────────────────────────
# "Each harvest in which you have at least 5 wood in your supply, you
# can discard down to 5 wood to take a Build Stables or Build Wood Rooms
# action by paying the usual costs." sub_actions.build_stables/
# build_rooms are the real transactions now (the old "risks diverging"
# reason predates them); "Wood Rooms" restricts the room option to a
# wood house (room material always follows house_type). The discard-
# down-to-5 cost is applied at spend time (not bank time), and rechecked
# there since wood may have moved between harvest and use; banked as a
# credit (usable during that harvest's feeding phase or a later work
# turn) since room/stable cells are open-ended.
def _lumber_virtuoso_harvest(state, player, inst, ctx):
    if player["resources"]["wood"] >= 5:
        inst["data"]["credits"] = inst["data"].get("credits", 0) + 1
        ctx["log"].append(f"{player['name']}'s Lumber Virtuoso grants a "
                          "discard-down-to-5 Build Stables/Wood Rooms action")


def _lumber_virtuoso_available(state, player, inst):
    if inst["data"].get("credits", 0) <= 0 or player["resources"]["wood"] < 5:
        return False
    can_stables = sub_actions.can_build_stables(state, player)
    can_rooms = player["house_type"] == "wood" and sub_actions.can_build_rooms(state, player)
    return can_stables or can_rooms


def _lumber_virtuoso_apply(state, player, inst, ctx):
    if inst["data"].get("credits", 0) <= 0 or player["resources"]["wood"] < 5:
        raise ValueError("Lumber Virtuoso: no discard-down-to-5 action available")
    params = ctx.get("params") or {}
    kind = params.get("kind")
    if kind not in ("stables", "rooms"):
        raise ValueError("Lumber Virtuoso: choose kind 'stables' or 'rooms'")
    if kind == "rooms" and player["house_type"] != "wood":
        raise ValueError("Lumber Virtuoso: Wood Rooms needs a wood house")
    discarded = player["resources"]["wood"] - 5
    player["resources"]["wood"] = 5
    if discarded:
        ctx["log"].append(f"{player['name']} discards {discarded} wood "
                          "(Lumber Virtuoso)")
    cells = params.get("cells") or []
    if kind == "stables":
        sub_actions.build_stables(state, player, cells, ctx["log"])
    else:
        sub_actions.build_rooms(state, player, cells, ctx["log"])
    inst["data"]["credits"] -= 1

compendium_card(
    "D129", hooks={"harvest_field": _lumber_virtuoso_harvest},
    card_action={"available": _lumber_virtuoso_available,
                "apply": _lumber_virtuoso_apply,
                "description": "Discard wood down to 5 for a Build Stables "
                               "or Wood Rooms action (Lumber Virtuoso, "
                               "once per qualifying harvest)"})


# ── D130 Recreational Carpenter ──────────────────────────────────────
# "At the end of each work phase in which you did not use the Meeting
# Place action space, you can take a Build Rooms action without placing
# a person." returning_home fires once per player at the end of the
# work phase with ctx["spaces"] = every space that player occupied that
# round -- exactly the "did not use Meeting Place" check. Room cells are
# open-ended, so this is the banked-credit/card_action shape (the
# K269/K289 returning_home recipe, generalized from "perform the target
# space's effect" to "grant a bonus build").
def _rec_carpenter_returning_home(state, player, inst, ctx):
    if "meeting_place" in ctx["spaces"]:
        return
    inst["data"]["credits"] = inst["data"].get("credits", 0) + 1
    ctx["log"].append(f"{player['name']}'s Recreational Carpenter grants a "
                      "Build Rooms action")


def _rec_carpenter_available(state, player, inst):
    return inst["data"].get("credits", 0) > 0 and sub_actions.can_build_rooms(state, player)


def _rec_carpenter_apply(state, player, inst, ctx):
    if inst["data"].get("credits", 0) <= 0:
        raise ValueError("Recreational Carpenter: no Build Rooms action available")
    cells = (ctx.get("params") or {}).get("cells") or []
    sub_actions.build_rooms(state, player, cells, ctx["log"])
    inst["data"]["credits"] -= 1

compendium_card(
    "D130", hooks={"returning_home": _rec_carpenter_returning_home},
    card_action={"available": _rec_carpenter_available,
                "apply": _rec_carpenter_apply,
                "description": "Build rooms without placing a person "
                               "(Recreational Carpenter)"})


# ── D133 Beer Tent Operator ───────────────────────────────────────────
# "In the feeding phase of each harvest, you can use this card to turn
# 1 wood plus 1 grain into 1 bonus point and 2 food."

def _beer_tent_available(state, player, inst):
    return (state["phase"] == "feeding" and player["resources"]["wood"] >= 1
            and player["resources"]["grain"] >= 1)


def _beer_tent_apply(state, player, inst, ctx):
    player["resources"]["wood"] -= 1
    player["resources"]["grain"] -= 1
    player["resources"]["food"] += 2
    inst["data"]["bonus"] = inst["data"].get("bonus", 0) + 1
    ctx["log"].append(f"{player['name']}'s Beer Tent Operator turns 1 wood + "
                      "1 grain into 2 food and 1 bonus point")


compendium_card("D133", card_action={
    "available": _beer_tent_available,
    "apply": _beer_tent_apply,
    "description": "1 wood + 1 grain -> 2 food + 1 bonus point",
}, score_bonus=lambda s, p, i: i["data"].get("bonus", 0))


# ── D134 Oyster Eater ─────────────────────────────────────────────────
# "Each time the Fishing accumulation space is used, you get 1 bonus
# point and must skip placing your next person that round."

def _oyster_eater_hook(state, player, inst, ctx):
    if ctx["space_id"] != "fishing":
        return
    inst["data"]["bonus"] = inst["data"].get("bonus", 0) + 1
    player["people_placed"] = min(player["people_total"],
                                  player["people_placed"] + 1)
    ctx["log"].append(f"{player['name']}'s Oyster Eater scores a bonus point "
                      "and skips a placement")


compendium_card("D134", hooks={"space_used": _oyster_eater_hook},
                score_bonus=lambda s, p, i: i["data"].get("bonus", 0))


# ── D135 Gardening Head Official ──────────────────────────────────────
# "If there are still 3/6/9 complete rounds left to play, you
# immediately get 2/3/4 wood. During scoring, each player with the most
# vegetables in their fields gets 2 bonus points."

def _rounds_remaining_wood_play(state, player, inst, ctx):
    remaining = TOTAL_ROUNDS - state["round"]
    wood = 4 if remaining >= 9 else 3 if remaining >= 6 else 2 if remaining >= 3 else 0
    if wood:
        player["resources"]["wood"] += wood
        ctx["log"].append(f"{player['name']}'s {spec(inst)['name']} grants "
                          f"{wood} wood")


def _veg_in_fields(player):
    total = 0
    for c in player["cells"]:
        if c["type"] == "field" and c["crops"] and c["crops"]["type"] == "vegetable":
            total += c["crops"]["count"]
    for fi in cards.card_fields(player):
        if fi["crops"] and fi["crops"]["type"] == "vegetable":
            total += fi["crops"]["count"]
    return total


def _gardening_head_score(state, player, inst):
    mine = _veg_in_fields(player)
    if mine > 0 and all(_veg_in_fields(p) <= mine for p in state["players"]):
        return 2
    return 0


compendium_card("D135", hooks={"play": _rounds_remaining_wood_play},
                score_bonus=_gardening_head_score)


# ── D136 Animal Activist ──────────────────────────────────────────────
# "If there are still 3/6/9 complete rounds left to play, you
# immediately get 2/3/4 wood. During scoring, each player with the most
# fenced stables gets 2 bonus points."

def _fenced_stables_count(player):
    pasture_cells = {i for p in compute_pastures(player) for i in p}
    return sum(1 for i in pasture_cells if player["cells"][i]["stable"])


def _animal_activist_score(state, player, inst):
    mine = _fenced_stables_count(player)
    if mine > 0 and all(_fenced_stables_count(p) <= mine for p in state["players"]):
        return 2
    return 0


compendium_card("D136", hooks={"play": _rounds_remaining_wood_play},
                score_bonus=_animal_activist_score)


# ── D137 Trade Teacher ────────────────────────────────────────────────
# "Each time after you use a Lessons action space, you can buy up to 2
# different goods: grain, stone, sheep, and wild boar for 1 food each;
# cattle and vegetable for 2 food each."

_TRADE_TEACHER_COSTS = {"grain": 1, "stone": 1, "sheep": 1, "boar": 1,
                        "cattle": 2, "vegetable": 2}


def _trade_teacher_prompt(state, player, inst, stage, bought):
    food = player["resources"]["food"]
    opts, goods = [], []
    for good, cost in _TRADE_TEACHER_COSTS.items():
        if good in bought or food < cost:
            continue
        opts.append(f"1 {good} ({cost} food)")
        goods.append(good)
    if not opts:
        return
    opts.append("Skip")
    goods.append(None)
    prompt_choice(state, player, inst["id"], "Trade Teacher: buy a good?",
                 opts, data={"stage": stage, "bought": bought, "goods": goods})


def _trade_teacher_hook(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or ctx["space_id"] not in ("lessons", "lessons_b"):
        return
    _trade_teacher_prompt(state, player, inst, 1, [])


def _trade_teacher_resolve(state, player, inst, ctx):
    data = ctx["data"]
    good = data["goods"][ctx["index"]]
    if good is None:
        return
    cost = _TRADE_TEACHER_COSTS[good]
    player["resources"]["food"] -= cost
    add_goods(ctx["extra"], {good: 1})
    ctx["log"].append(f"{player['name']}'s Trade Teacher buys 1 {good}")
    if data["stage"] < 2:
        _trade_teacher_prompt(state, player, inst, data["stage"] + 1,
                              data["bought"] + [good])


compendium_card("D137", hooks={"space_used": _trade_teacher_hook},
                resolve_choice=_trade_teacher_resolve)


# ── D139 Chairman ─────────────────────────────────────────────────────
# "Each time another player uses the Meeting Place action space, both
# they and you get 1 food (before taking the actions). If you use it,
# you get 1 food."

def _chairman_hook(state, player, inst, ctx):
    if ctx["space_id"] != "meeting_place":
        return
    if ctx["actor"] == player["index"]:
        add_goods(ctx["extra"], {"food": 1})
        ctx["log"].append(f"{player['name']}'s Chairman grants 1 food")
    else:
        player["resources"]["food"] += 1
        state["players"][ctx["actor"]]["resources"]["food"] += 1
        ctx["log"].append(f"{player['name']}'s Chairman grants 1 food to "
                          "both players")


compendium_card("D139", hooks={"space_used": _chairman_hook})


# ── D140 Loudmouth ────────────────────────────────────────────────────
# "Each time you take at least 4 building resources or 4 animals from an
# accumulation space, you also get 1 food." (Trailing "(3-5 players)
# When you play this card, you immediately get 1 grain..." duplicates
# the already-implemented base Grain Farmer occupation -- bleed from a
# different card, ignored.)

def _loudmouth_hook(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    for good, amount in ctx["goods"].items():
        if amount >= 4 and (good in BUILDING_RESOURCES or good in ANIMAL_TYPES):
            add_goods(ctx["extra"], {"food": 1})
            ctx["log"].append(f"{player['name']}'s Loudmouth adds 1 food")
            break


compendium_card("D140", hooks={"space_used": _loudmouth_hook})


# ── D143 Tree Cutter ──────────────────────────────────────────────────
# "Each time you use an accumulation space providing at least 3 goods of
# the same type except wood, you get 1 additional wood."

def _tree_cutter_hook(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    for good, amount in ctx["goods"].items():
        if good != "wood" and amount >= 3:
            add_goods(ctx["extra"], {"wood": 1})
            ctx["log"].append(f"{player['name']}'s Tree Cutter adds 1 wood")
            break


compendium_card("D143", hooks={"space_used": _tree_cutter_hook})


# ── D144 Water Worker ───────────────────────────────────────────────
# "Each time after you use the 'Fishing' accumulation space or one of
# the three orthogonally adjacent action spaces, you get 1 additional
# reed." (The "three" is a ground-truth check on state.py's board
# geometry, not something this hook needs to hardcode -- see
# decks/GUIDE.md's board-geometry section.)

def _water_worker_hook(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    if ctx["space_id"] == "fishing" or \
            ctx["space_id"] in cards.adjacent_spaces(state, "fishing"):
        add_goods(ctx["extra"], {"reed": 1})
        ctx["log"].append(f"{player['name']}'s Water Worker adds 1 reed")


compendium_card("D144", hooks={"space_used": _water_worker_hook})


# ── D145 Roof Examiner ────────────────────────────────────────────────
# "When you play this card, if you have 1/2/3/4 major improvements, you
# immediately get 2/3/4/5 reed." (Trailing "(3-5 players) Each time you
# take at least 4 of the same building resource..." is bleed from a
# different card -- ignored.)

def _roof_examiner_play(state, player, inst, ctx):
    n = len(player["improvements"])
    reed = 5 if n >= 4 else 4 if n >= 3 else 3 if n >= 2 else 2 if n >= 1 else 0
    if reed:
        player["resources"]["reed"] += reed
        ctx["log"].append(f"{player['name']}'s Roof Examiner grants {reed} reed")


compendium_card("D145", hooks={"play": _roof_examiner_play})


# ── D149 Casual Worker ────────────────────────────────────────────────
# "Each time another player uses a Quarry accumulation space, you can
# choose to get 1 food or build a stable without paying wood."

_QUARRY_SPACES = ("western_quarry", "eastern_quarry")


def _casual_worker_hook(state, player, inst, ctx):
    if ctx["space_id"] not in _QUARRY_SPACES or ctx["actor"] == player["index"]:
        return
    stables = sum(1 for c in player["cells"] if c["stable"])
    empty_cells = [i for i, c in enumerate(player["cells"])
                   if c["type"] == "empty" and not c["stable"]]
    if stables < MAX_STABLES and empty_cells:
        prompt_choice(state, player, inst["id"],
                     "Casual Worker: 1 food or a free stable?",
                     ["1 food", "Build a free stable"], data={})
    else:
        player["resources"]["food"] += 1
        ctx["log"].append(f"{player['name']}'s Casual Worker grants 1 food")


def _casual_worker_resolve(state, player, inst, ctx):
    data = ctx["data"]
    if "cells" in data:
        cell = data["cells"][ctx["index"]]
        c = player["cells"][cell]
        if c["type"] != "empty" or c["stable"]:
            return
        c["stable"] = True
        ctx["log"].append(f"{player['name']}'s Casual Worker builds a free "
                          "stable")
        cards.fire_player(state, player, "stable_built",
                          {"cells": [cell], "log": ctx["log"],
                           "actor": player["index"], "extra": ctx["extra"]})
        return
    if ctx["option"] == "1 food":
        player["resources"]["food"] += 1
        ctx["log"].append(f"{player['name']}'s Casual Worker grants 1 food")
    else:
        cells = [i for i, c in enumerate(player["cells"])
                 if c["type"] == "empty" and not c["stable"]]
        opts = [f"Cell {i}" for i in cells]
        prompt_choice(state, player, inst["id"],
                     "Choose a space for the free stable", opts,
                     data={"cells": cells})


compendium_card("D149", hooks={"space_used": _casual_worker_hook},
                resolve_choice=_casual_worker_resolve)


# ── D153 Wealthy Man ──────────────────────────────────────────────────
# "At the start of each of the 1st/2nd/3rd/4th/5th/6th harvest, if you
# have at least 1/2/3/4/5/6 grain fields, you get 1 bonus point."

def _grain_field_count(player):
    n = sum(1 for c in player["cells"]
            if c["crops"] and c["crops"]["type"] == "grain")
    n += sum(1 for i in cards.card_fields(player)
            if i["crops"] and i["crops"]["type"] == "grain")
    return n


def _wealthy_man_hook(state, player, inst, ctx):
    if _grain_field_count(player) >= ctx["harvest_index"]:
        inst["data"]["bonus"] = inst["data"].get("bonus", 0) + 1
        ctx["log"].append(f"{player['name']}'s Wealthy Man scores a bonus "
                          "point")


compendium_card("D153", hooks={"harvest_field": _wealthy_man_hook},
                score_bonus=lambda s, p, i: i["data"].get("bonus", 0))


# ── D154 Chimney Sweep ────────────────────────────────────────────────
# "Renovating to stone costs you 2 stone less. During scoring, you get
# 1 bonus point for each other player living in a stone house."

def _chimney_sweep_mod(state, player, kind, cost, ctx):
    if kind == "renovation" and cost.get("stone"):
        cost = dict(cost)
        cost["stone"] = max(0, cost["stone"] - 2)
    return cost


def _chimney_sweep_score(state, player, inst):
    return sum(1 for p in state["players"]
               if p["index"] != player["index"] and p["house_type"] == "stone")


compendium_card("D154", cost_mod=_chimney_sweep_mod,
                score_bonus=_chimney_sweep_score)


# ── D155 Ebonist ──────────────────────────────────────────────────────
# "Each harvest, you can use this card to turn exactly 1 wood into 1
# food and 1 grain."

def _ebonist_available(state, player, inst):
    return (player["resources"]["wood"] >= 1
            and inst["data"].get("used_harvest") != state.get("harvest_index"))


def _ebonist_apply(state, player, inst, ctx):
    player["resources"]["wood"] -= 1
    player["resources"]["food"] += 1
    player["resources"]["grain"] += 1
    inst["data"]["used_harvest"] = state.get("harvest_index")
    ctx["log"].append(f"{player['name']}'s Ebonist turns 1 wood into 1 food "
                      "and 1 grain")


compendium_card("D155", card_action={
    "available": _ebonist_available,
    "apply": _ebonist_apply,
    "description": "1 wood -> 1 food + 1 grain (once per harvest)",
})


# ── D156 Retail Dealer ────────────────────────────────────────────────
# "Place 3 grain and 3 food on this card. Each time you use the Resource
# Market action space, you also get 1 grain and 1 food from this card."

def _retail_dealer_play(state, player, inst, ctx):
    inst["data"]["grain"] = 3
    inst["data"]["food"] = 3


def _retail_dealer_hook(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or \
            ctx["space_id"] not in ("resource_market_3p", "resource_market_4p"):
        return
    d = inst["data"]
    if d.get("grain", 0) > 0 and d.get("food", 0) > 0:
        d["grain"] -= 1
        d["food"] -= 1
        add_goods(ctx["extra"], {"grain": 1, "food": 1})
        ctx["log"].append(f"{player['name']}'s Retail Dealer provides 1 "
                          "grain and 1 food")


compendium_card("D156", hooks={"play": _retail_dealer_play,
                               "space_used": _retail_dealer_hook})


# ── D157 Party Organizer ──────────────────────────────────────────────
# "As soon as the next player but you gains their 5th person, you
# immediately get 8 food (not retroactively). During scoring, if only
# you have 5 people, you get 3 bonus points."

def _party_organizer_hook(state, player, inst, ctx):
    if ctx["actor"] == player["index"] or inst["data"].get("triggered"):
        return
    if state["players"][ctx["actor"]]["people_total"] == 5:
        inst["data"]["triggered"] = True
        player["resources"]["food"] += 8
        ctx["log"].append(f"{player['name']}'s Party Organizer grants 8 food")


def _party_organizer_score(state, player, inst):
    if player["people_total"] != 5:
        return 0
    others_have_5 = any(p["people_total"] == 5 for p in state["players"]
                        if p["index"] != player["index"])
    return 0 if others_have_5 else 3


compendium_card("D157", hooks={"family_growth": _party_organizer_hook},
                score_bonus=_party_organizer_score)


# ── D160 Midwife ──────────────────────────────────────────────────────
# "Each time another player uses the first person they place in a round
# to take a Family Growth action, you get 1 grain from the general
# supply."

def _midwife_hook(state, player, inst, ctx):
    if ctx["actor"] == player["index"]:
        return
    if state["players"][ctx["actor"]]["people_placed"] == 1:
        player["resources"]["grain"] += 1
        ctx["log"].append(f"{player['name']}'s Midwife grants 1 grain")


compendium_card("D160", hooks={"family_growth": _midwife_hook})


# ── D163 Journeyman Bricklayer ──────────────────────────────────────
# "When you play this card, you immediately get 2 stone. Each time
# another player renovates to stone or build a stone room, you get 1
# stone." Both reactive clauses are now observable via the broadcast
# twins: renovate_any and rooms_built_any. "A stone room" is read as "a
# room built while the builder currently lives in a stone house" (room
# cells carry no material of their own -- it always follows the
# builder's house_type). One flat stone per triggering event (the text
# doesn't say "per room"), matching D077's precedent of reading the
# actor's post-event house_type off state["players"].

def _bricklayer_renovate(state, player, inst, ctx):
    if ctx["actor"] == player["index"]:
        return
    if state["players"][ctx["actor"]]["house_type"] != "stone":
        return
    cards.grant_goods(state, player, {"stone": 1}, ctx["log"])
    ctx["log"].append(f"{player['name']}'s Journeyman Bricklayer grants "
                      "1 stone")


def _bricklayer_rooms_built(state, player, inst, ctx):
    if ctx["actor"] == player["index"]:
        return
    if state["players"][ctx["actor"]]["house_type"] != "stone":
        return
    cards.grant_goods(state, player, {"stone": 1}, ctx["log"])
    ctx["log"].append(f"{player['name']}'s Journeyman Bricklayer grants "
                      "1 stone")


compendium_card("D163", hooks={"play": on_play_gain({"stone": 2}),
                               "renovate_any": _bricklayer_renovate,
                               "rooms_built_any": _bricklayer_rooms_built})


# ── D165 Pig Stalker ──────────────────────────────────────────────────
# "Each time you use an animal accumulation space, if you occupy
# either the action space immediately above or below that
# accumulation space, you also get 1 wild boar." "Animal accumulation
# space" is read off ctx["goods"] (whatever the space just paid out
# includes an animal type) rather than a static space definition --
# state["action_spaces"] entries don't retain the original PERMANENT_
# SPACES/STAGE_CARDS "acc" recipe dict at runtime (only a bool
# "accumulates" flag plus the live "supply"), and by the time
# space_used fires that supply has already been zeroed out. Only
# vertical neighbors count (unlike D144's all-4-directions check).

def _pig_stalker_hook(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    if not any(g in ANIMAL_TYPES for g in ctx["goods"]):
        return
    for nid in cards.vertical_neighbors(state, ctx["space_id"]):
        neighbor = next(s for s in state["action_spaces"] if s["id"] == nid)
        occupants = ([neighbor["occupied_by"]]
                    if neighbor["occupied_by"] is not None else []) \
            + neighbor.get("extra_occupants", [])
        if player["index"] in occupants:
            add_goods(ctx["extra"], {"boar": 1})
            ctx["log"].append(f"{player['name']}'s Pig Stalker adds 1 wild boar")
            return


compendium_card("D165", hooks={"space_used": _pig_stalker_hook})


# ── D166 Stable Milker ────────────────────────────────────────────────
# "Each time you build at least 2 stables on the same turn, you also
# get 1 cattle."

def _stable_milker_hook(state, player, inst, ctx):
    if len(ctx["cells"]) >= 2:
        add_goods(ctx["extra"], {"cattle": 1})
        ctx["log"].append(f"{player['name']}'s Stable Milker adds 1 cattle")


compendium_card("D166", hooks={"stable_built": _stable_milker_hook})
