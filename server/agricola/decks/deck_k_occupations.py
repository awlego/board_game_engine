"""Deck K occupations (codes K266-K312, K342 from the compendium DB).

Deck K is an ORIGINAL-edition deck. Card texts reference original-board
action spaces; they are mapped onto this engine's (revised-edition)
spaces where a faithful equivalent exists:
    "Sow and Bake Bread"                 -> grain_utilization
    "Take 1 Grain"                       -> grain_seeds
    "Take 1 Vegetable"                   -> vegetable_seeds
    "Plough 1 Field"                     -> farmland
    "Plough Field and Sow"               -> cultivation
    "1 Reed, Stone, and Food" (4p)       -> resource_market_4p
    "Traveling Players" (4p acc. space)  -> traveling_players
    "minor improvement" action           -> meeting_place
    "major or minor improvement" action  -> major_improvement

No DB text-bleed artifacts (the "(N-M players)" mid-string marker that
afflicts deck B) were found in this slice, so _TEXT_FIXES is empty --
every text read by compendium_card() here is the card's own.
"""

import random

import server.agricola.cards as cards
from server.agricola.cards import (
    compendium_card, add_goods, goods_str, prompt_choice,
)
from server.agricola import sub_actions
from server.agricola.state import (
    MAX_PEOPLE, TOTAL_ROUNDS, MAJOR_IMPROVEMENTS,
    compute_pastures, pasture_capacity, plowable_cells, cell_edges,
    validate_fence_layout, table_score,
)

UNIMPLEMENTED = {
    "K269": "grants a deferred move of an already-placed person (from "
            "\"Traveling Players\") to another space after all players "
            "have finished their turns; no hook fires between the end "
            "of the work phase and the start of harvest/next round for "
            "a card to grant a delayed placement.",
    "K273": "requires reacting to converting wild boar into food; no "
            "event fires when animals are cooked (neither the "
            "accommodate-prompt \"cook\" branch nor the feeding "
            "\"via: cook\" conversion calls cards.fire/fire_player).",
    "K275": "requires the normal Lessons-space play-occupation action to "
            "accept \"play a random card from my hand\" instead of a "
            "specific card id; the only way to add that is editing "
            "_play_occupation's parameter handling in engine.py, which "
            "per GUIDE.md we don't do per-card.",
    "K277": "requires redirecting specific major improvements (Joinery/"
            "Pottery/Basketmaker's Workshop) through the minor-"
            "improvement action-space flow, plus a player-chosen "
            "2-resource discount; no hook lets a card change which "
            "action-space channel builds an improvement.",
    "K280": "requires reacting to converting wild boar or cattle into "
            "food (same gap as K273: no cook/convert event exists).",
    "K281": "requires letting a \"minor improvement\" action space build "
            "a major instead, or a \"major or minor\" space play 2 "
            "minors; this needs re-implementing the build/play "
            "parameter shapes of _resolve_space outside the normal "
            "placement flow (same category of gap as B150 in deck B).",
    "K289": "grants a deferred move of an already-placed person (from "
            "\"Take 1 Grain\"/\"Take 1 Vegetable\") to a sow space after "
            "all players have finished their turns; same missing "
            "post-work-phase hook as K269 Acrobat.",
    "K299": "requires reacting to any player converting animals to food "
            "(no cook/convert event, as K273/K280) and reordering the "
            "feeding turn sequence; this engine's feeding phase has no "
            "turn order at all (get_waiting_for returns every not-yet-"
            "fed player at once), so \"feed last\" has no mechanical "
            "meaning here.",
    "K301": "the wood discount applies once per ROUND across any "
            "qualifying purchase (not once per item, unlike Stonecutter's "
            "unconditional per-item discount); cost_mod is a pure "
            "function queried speculatively for validity checks as well "
            "as at actual payment, so a stateful \"already used this "
            "round\" flag can't be consumed safely there without also "
            "corrupting cost previews.",
    "K308": "grants \"place 1 food on an action space of your choice\" "
            "at the start of each round; round_start hooks must not "
            "prompt (a prompt pending at round start blocks every "
            "player's placements per GUIDE.md), and there is no sane "
            "default space to auto-select -- the whole value of the "
            "card is the open-ended choice.",
    "K310": "requires reacting to gaining stone/clay/reed/wood from ANY "
            "source (action spaces, occupations, improvements, round-"
            "start effects) in a fixed sequence; only space_used exposes "
            "goods (via ctx[\"goods\"]), so gains from cards or round-"
            "start hooks are invisible to this card.",
}

_TEXT_FIXES = {}


# ── Shared helpers ────────────────────────────────────────────────────

def _remove_animal(player, animal_type, count=1):
    """Remove `count` animals of a type from cells/pets. Returns True if
    the full amount was removed. (Copied from deck_b_occupations.py --
    cards.py cannot import the engine, and each deck module keeps its
    own small copy of these engine-adjacent helpers.)"""
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


def _place_animal_best_effort(player, animal_type, count=1):
    """Accommodate scheduled/bred animals without a prompt, mirroring
    the engine's own breeding-phase placement algorithm
    (AgricolaEngine._place_newborn_animal). Used from round_start hooks,
    which per GUIDE.md must not prompt or route gains through
    ctx["extra"] (that would create a pending prompt at round start).
    Returns True if every animal found room."""
    bonus = cards.pasture_bonus(player)
    for _ in range(count):
        pastures = compute_pastures(player)
        pasture_of = {}
        occupants = {}
        for pi, pasture in enumerate(pastures):
            occupants[pi] = {"type": None, "count": 0}
            for i in pasture:
                pasture_of[i] = pi
        for i, cell in enumerate(player["cells"]):
            a = cell.get("animal")
            if a and i in pasture_of:
                occ = occupants[pasture_of[i]]
                occ["type"] = a["type"]
                occ["count"] += a["count"]

        placed = False
        for pi, pasture in enumerate(pastures):
            occ = occupants[pi]
            if occ["type"] == animal_type and \
                    occ["count"] < pasture_capacity(player, pasture, bonus):
                for i in pasture:
                    a = player["cells"][i]["animal"]
                    if a and a["type"] == animal_type:
                        a["count"] += 1
                        placed = True
                        break
                if placed:
                    break
        if not placed:
            for pi, pasture in enumerate(pastures):
                if occupants[pi]["count"] == 0:
                    player["cells"][pasture[0]]["animal"] = \
                        {"type": animal_type, "count": 1}
                    placed = True
                    break
        if not placed:
            for i, cell in enumerate(player["cells"]):
                if (cell["stable"] and cell["type"] == "empty"
                        and not cell["animal"] and i not in pasture_of):
                    cell["animal"] = {"type": animal_type, "count": 1}
                    placed = True
                    break
        if not placed:
            if sum(player["pets"].values()) < cards.house_capacity(player):
                player["pets"][animal_type] = \
                    player["pets"].get(animal_type, 0) + 1
                placed = True
        if not placed:
            return False
    return True


# ── K266 Serf ─────────────────────────────────────────────────────────
def _serf_space(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or ctx["space_id"] != "grain_utilization":
        return
    prompt_choice(state, player, inst["id"],
                 "Serf: take 1 grain, or exchange it for 1 vegetable?",
                 ["1 grain", "1 vegetable"])


def _serf_choice(state, player, inst, ctx):
    good = "grain" if ctx["index"] == 0 else "vegetable"
    add_goods(ctx["extra"], {good: 1})
    ctx["log"].append(f"{player['name']}'s Serf grants 1 {good}")

compendium_card("K266", hooks={"space_used": _serf_space},
                resolve_choice=_serf_choice)


# ── K267 Adoptive Parents ─────────────────────────────────────────────
def _adoptive_parents_growth(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or player["resources"]["food"] < 1:
        return
    prompt_choice(state, player, inst["id"],
                 "Adoptive Parents: pay 1 food to place the offspring "
                 "immediately (it can act this round, but does not count "
                 "as \"newborn\")?", ["Decline", "Pay 1 food"])


def _adoptive_parents_choice(state, player, inst, ctx):
    if ctx["index"] == 0:
        return
    if player["resources"]["food"] < 1 or player["newborns"] < 1:
        return
    player["resources"]["food"] -= 1
    player["newborns"] -= 1
    player["people_placed"] -= 1
    ctx["log"].append(f"{player['name']}'s Adoptive Parents places the "
                      "offspring immediately (1 food)")

compendium_card("K267", hooks={"family_growth": _adoptive_parents_growth},
                resolve_choice=_adoptive_parents_choice)


# ── K268 Pieceworker ──────────────────────────────────────────────────
_PIECEWORKER_COST = {"wood": 1, "clay": 1, "reed": 1, "stone": 1,
                     "grain": 1, "vegetable": 2}


def _pieceworker_offer(state, player, inst, queue):
    while queue:
        good = queue[0]
        cost = _PIECEWORKER_COST[good]
        if player["resources"]["food"] >= cost:
            prompt_choice(state, player, inst["id"],
                         f"Pieceworker: buy 1 more {good} for {cost} food?",
                         ["Decline", f"Buy 1 {good}"],
                         data={"good": good, "cost": cost,
                              "queue": queue[1:]})
            return
        queue = queue[1:]


def _pieceworker_space(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    queue = [g for g in ("wood", "clay", "reed", "stone", "grain", "vegetable")
             if ctx["goods"].get(g)]
    if queue:
        _pieceworker_offer(state, player, inst, queue)


def _pieceworker_choice(state, player, inst, ctx):
    good = ctx["data"]["good"]
    cost = ctx["data"]["cost"]
    if ctx["index"] == 1 and player["resources"]["food"] >= cost:
        player["resources"]["food"] -= cost
        add_goods(ctx["extra"], {good: 1})
        ctx["log"].append(f"{player['name']}'s Pieceworker buys 1 {good} "
                          f"for {cost} food")
    _pieceworker_offer(state, player, inst, ctx["data"]["queue"])

compendium_card("K268", hooks={"space_used": _pieceworker_space},
                resolve_choice=_pieceworker_choice)


# ── K270 Wet Nurse ────────────────────────────────────────────────────
def _wet_nurse_rooms(state, player, inst, ctx):
    n = len(ctx["cells"])
    if n <= 0:
        return
    rooms = sum(1 for c in player["cells"] if c["type"] == "room")
    headroom = rooms + cards.extra_rooms(player) - player["people_total"]
    max_growth = min(n, max(headroom, 0), MAX_PEOPLE - player["people_total"],
                     player["resources"]["food"])
    if max_growth <= 0:
        return
    options = ["Decline"] + [f"Grow by {k} ({k} food)"
                             for k in range(1, max_growth + 1)]
    prompt_choice(state, player, inst["id"],
                 "Wet Nurse: grow your family?", options)


def _wet_nurse_choice(state, player, inst, ctx):
    if ctx["index"] == 0:
        return
    k = ctx["index"]
    if player["resources"]["food"] < k:
        return
    player["resources"]["food"] -= k
    for _ in range(k):
        player["people_total"] += 1
        player["people_placed"] += 1
        player["newborns"] += 1
    ctx["log"].append(f"{player['name']}'s Wet Nurse grows the family by "
                      f"{k} ({k} food)")
    cards.fire(state, "family_growth", ctx)

compendium_card("K270", hooks={"rooms_built": _wet_nurse_rooms},
                resolve_choice=_wet_nurse_choice)


# ── K271 Educator ─────────────────────────────────────────────────────
def _educator_play_occ(state, player, cid, cost, ctx):
    """Play an occupation out-of-turn, granted by another player's
    Educator (sub_actions.play_occupation is the same transaction the
    Lessons action spaces use)."""
    sub_actions.play_occupation(state, player, cid, ctx["log"],
                                cost_override={"food": cost})


def _educator_occ_played(state, player, inst, ctx):
    if ctx["actor"] == player["index"] or not player["hand_occupations"]:
        return
    cost = 2 if len(player["occupations"]) >= 3 else 3
    if player["resources"]["food"] < cost:
        return
    options = ["Decline"] + [cards.CARDS[cid]["name"]
                             for cid in player["hand_occupations"]]
    prompt_choice(state, player, inst["id"],
                 f"Educator: pay {cost} food to play an occupation?",
                 options, data={"hand": list(player["hand_occupations"]),
                                "cost": cost})


def _educator_choice(state, player, inst, ctx):
    if ctx["index"] == 0:
        return
    cid = ctx["data"]["hand"][ctx["index"] - 1]
    cost = ctx["data"]["cost"]
    if cid not in player["hand_occupations"] or player["resources"]["food"] < cost:
        return
    _educator_play_occ(state, player, cid, cost, ctx)

compendium_card("K271", hooks={"occupation_played": _educator_occ_played},
                resolve_choice=_educator_choice)


# ── K272 Frame Builder ────────────────────────────────────────────────
def _frame_builder_mod(state, player, kind, cost, ctx):
    cost = dict(cost)
    if kind == "renovation":
        for material in ("clay", "stone"):
            if cost.get(material, 0) >= 1:
                cost[material] -= 1
                cost["wood"] = cost.get("wood", 0) + 1
                break
    elif kind == "room":
        for material in ("clay", "stone"):
            if cost.get(material, 0) >= 2:
                cost[material] -= 2
                cost["wood"] = cost.get("wood", 0) + 1
                break
    return cost

compendium_card("K272", cost_mod=_frame_builder_mod)


# ── K274 Organic Farmer ───────────────────────────────────────────────
def _organic_farmer_score(state, player, inst):
    bonus = cards.pasture_bonus(player)
    count = 0
    for pasture in compute_pastures(player):
        occ = 0
        atype = None
        for i in pasture:
            a = player["cells"][i]["animal"]
            if a:
                atype = a["type"]
                occ += a["count"]
        cap = pasture_capacity(player, pasture, bonus)
        if atype is not None and occ >= 1 and cap - occ >= 3:
            count += 1
    return count

compendium_card("K274", score_bonus=_organic_farmer_score)


# ── K276 Constable ────────────────────────────────────────────────────
# On-play wood bonus is identical to the base "House Steward" card
# (also K282 below); reuse the existing helper rather than duplicate it.
def _constable_no_negatives(player):
    animals = cards.animal_totals_of(player)
    fields = sum(1 for c in player["cells"] if c["type"] == "field")
    pasture_cells = {i for p in compute_pastures(player) for i in p}
    unused = sum(1 for i, c in enumerate(player["cells"])
                if c["type"] == "empty" and not c["stable"]
                and i not in pasture_cells)
    grain = player["resources"]["grain"]
    vegetable = player["resources"]["vegetable"]
    for c in player["cells"]:
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
    categories = [
        table_score("fields", fields),
        table_score("pastures", len(compute_pastures(player))),
        table_score("grain", grain),
        table_score("vegetable", vegetable),
        table_score("sheep", animals["sheep"]),
        table_score("boar", animals["boar"]),
        table_score("cattle", animals["cattle"]),
        -unused,
    ]
    # Approximation: does not additionally check other cards' own
    # negative bonus contributions (recomputing the full scoring pass
    # here would recurse into cards.score_bonuses -> this very
    # function). Begging markers and the categories above cover the
    # common cases the card's rulings call out.
    return player["begging"] == 0 and all(c >= 0 for c in categories)


def _constable_score(state, player, inst):
    return 5 if _constable_no_negatives(player) else 0

compendium_card("K276", hooks={"play": cards._house_steward_play},
                score_bonus=_constable_score)


# ── K278 Forester ─────────────────────────────────────────────────────
def _forester_sow(state, player, inst, ctx):
    stacks = inst["data"].setdefault("stacks", [0, 0, 0])
    options, data = ["Decline"], []
    for i, s in enumerate(stacks):
        if s == 0 and player["resources"]["wood"] >= 1:
            options.append(f"Start stack {i + 1} (costs 1 wood)")
            data.append(("start", i))
        elif s > 0:
            options.append(f"Add 3 wood to stack {i + 1} (free)")
            data.append(("add", i))
    if len(options) > 1:
        prompt_choice(state, player, inst["id"],
                     "Forester: plant wood on this card?", options,
                     data={"choices": data})


def _forester_choice(state, player, inst, ctx):
    if ctx["index"] == 0:
        return
    kind, i = ctx["data"]["choices"][ctx["index"] - 1]
    stacks = inst["data"]["stacks"]
    if kind == "start":
        if player["resources"]["wood"] < 1 or stacks[i] != 0:
            return
        player["resources"]["wood"] -= 1
        stacks[i] = 1
        ctx["log"].append(f"{player['name']}'s Forester starts a wood stack")
    else:
        # Simplification: always adds 3 (the default grain-field sowing
        # amount), regardless of occupations that scale sowing amounts.
        stacks[i] += 3
        ctx["log"].append(f"{player['name']}'s Forester adds 3 wood to a "
                          "stack")


def _forester_harvest(state, player, inst, ctx):
    stacks = inst["data"].get("stacks", [0, 0, 0])
    gained = 0
    for i, s in enumerate(stacks):
        if s > 0:
            stacks[i] -= 1
            gained += 1
    if gained:
        player["resources"]["wood"] += gained
        ctx["log"].append(f"{player['name']}'s Forester harvests {gained} "
                          "wood")

compendium_card("K278", hooks={"sow": _forester_sow,
                               "harvest_field": _forester_harvest},
                resolve_choice=_forester_choice)


# ── K279 Scholar ──────────────────────────────────────────────────────
def _scholar_buildable_improvements(state, player):
    return [imp for imp in state["available_improvements"]
           if all(player["resources"].get(r, 0) >= a
                  for r, a in cards.modified_cost(
                      state, player, "improvement",
                      MAJOR_IMPROVEMENTS[imp]["cost"]).items())]


def _scholar_available(state, player, inst):
    if player["house_type"] != "stone":
        return False
    if inst["data"].get("last_round") == state["round"]:
        return False
    can_occ = bool(player["hand_occupations"]) and player["resources"]["food"] >= 1
    return can_occ or bool(_scholar_buildable_improvements(state, player))


def _scholar_apply(state, player, inst, ctx):
    params = ctx.get("params") or {}
    kind = params.get("kind")
    if kind == "occupation":
        cid = params.get("card")
        if cid not in player["hand_occupations"] or player["resources"]["food"] < 1:
            raise ValueError("Scholar: choose an affordable occupation "
                             "(params.card)")
        sub_actions.play_occupation(state, player, cid, ctx["log"],
                                    cost_override={"food": 1})
    elif kind == "improvement":
        # Upgrading a Fireplace to a Cooking Hearth via the Scholar is
        # not offered here (out of scope); only fresh builds are.
        imp = params.get("improvement")
        if imp not in _scholar_buildable_improvements(state, player):
            raise ValueError("Scholar: choose an affordable improvement "
                             "(params.improvement)")
        sub_actions.build_improvement(state, player, imp, ctx["log"])
    else:
        raise ValueError("Scholar: choose kind 'occupation' or 'improvement'")
    inst["data"]["last_round"] = state["round"]

compendium_card(
    "K279",
    card_action={"available": _scholar_available, "apply": _scholar_apply,
                "description": "Play an occupation for 1 food, or build a "
                               "major improvement (once per round, "
                               "requires a stone house)"})


# ── K282 House Steward ────────────────────────────────────────────────
# Identical text/effect to the base card "occ_house_steward" in
# cards.py; reuse its helpers instead of duplicating them.
compendium_card("K282", hooks={"play": cards._house_steward_play},
                score_bonus=cards._house_steward_score)


# ── K283 Wood Deliveryman ─────────────────────────────────────────────
compendium_card("K283", hooks=cards.schedule_on_play(
    "wood", fixed_rounds=(8, 9, 10, 11, 12, 13, 14)))


# ── K284 Wood Distributor ─────────────────────────────────────────────
_WOOD_DIST_TARGETS = ("clay_pit", "reed_bank", "fishing")


def _wood_distributor_round_start(state, player, inst, ctx):
    forest = next((s for s in state["action_spaces"] if s["id"] == "forest"),
                  None)
    if not forest:
        return
    wood = forest["supply"].get("wood", 0)
    per_space, remainder = divmod(wood, 3)
    if per_space <= 0:
        return
    forest["supply"]["wood"] = remainder
    for sid in _WOOD_DIST_TARGETS:
        sp = next((s for s in state["action_spaces"] if s["id"] == sid), None)
        if sp is not None:
            sp["supply"]["wood"] = sp["supply"].get("wood", 0) + per_space
    ctx["log"].append(f"{player['name']}'s Wood Distributor spreads "
                      f"{per_space} wood each onto Clay Pit, Reed Bank, "
                      "and Fishing")

compendium_card("K284", hooks={"play": cards.on_play_gain({"wood": 2}),
                               "round_start": _wood_distributor_round_start})


# ── K285 Tinsmith ─────────────────────────────────────────────────────
def _tinsmith_available(state, player, inst):
    return player["resources"]["clay"] >= 1


def _tinsmith_apply(state, player, inst, ctx):
    amount = (ctx.get("params") or {}).get("clay")
    if not isinstance(amount, int) or amount < 1:
        raise ValueError("Tinsmith: choose how much clay to convert "
                         "(params.clay)")
    if player["resources"]["clay"] < amount:
        raise ValueError("Not enough clay")
    has_well = any("well" in pl["improvements"] for pl in state["players"])
    if has_well:
        pairs, remainder = divmod(amount, 2)
        food = pairs * 3 + remainder
    else:
        food = amount
    player["resources"]["clay"] -= amount
    player["resources"]["food"] += food
    ctx["log"].append(f"{player['name']}'s Tinsmith converts {amount} clay "
                      f"into {food} food")

compendium_card("K285", card_action={
    "available": _tinsmith_available, "apply": _tinsmith_apply,
    "description": "Convert clay to food (1:1, or 3 food per 2 clay if "
                   "any player has built a Well)"})


# ── K286 Smallholder ──────────────────────────────────────────────────
def _smallholder_field_count(player):
    return (sum(1 for c in player["cells"] if c["type"] == "field")
           + len(cards.card_fields(player)))


def _smallholder_sow(state, player, inst, ctx):
    if _smallholder_field_count(player) > 2:
        return
    extra = {}
    for target, crop in ctx["sown"]:
        cell = player["cells"][target] if isinstance(target, int) else target
        if cell.get("crops"):
            cell["crops"]["count"] += 1
            extra[crop] = extra.get(crop, 0) + 1
    if extra:
        ctx["log"].append(f"{player['name']}'s Smallholder adds "
                          + goods_str(extra) + " to freshly sown fields")

# Smallholder's other clause ("pastures that can hold 2 animals can hold
# 3") is not implemented: pasture_capacity_bonus (see Drinking Trough)
# applies uniformly to every pasture, with no way to condition the bonus
# on a pasture's own base capacity being exactly 2 (same gap as B115 in
# deck B).
compendium_card("K286", hooks={"sow": _smallholder_sow})


# ── K287 Storehouse Clerk ─────────────────────────────────────────────
def _storehouse_clerk_round_start(state, player, inst, ctx):
    gains = {}
    if player["resources"]["stone"] >= 5:
        gains["stone"] = 1
    if player["resources"]["reed"] >= 6:
        gains["reed"] = 1
    if player["resources"]["clay"] >= 7:
        gains["clay"] = 1
    if player["resources"]["wood"] >= 8:
        gains["wood"] = 1
    if gains:
        add_goods(player["resources"], gains)
        ctx["log"].append(f"{player['name']}'s Storehouse Clerk grants "
                          + goods_str(gains))

compendium_card("K287", hooks={"round_start": _storehouse_clerk_round_start})


# ── K288 Storehouse Keeper ────────────────────────────────────────────
def _storehouse_keeper_space(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or ctx["space_id"] != "resource_market_4p":
        return
    prompt_choice(state, player, inst["id"],
                 "Storehouse Keeper: take 1 clay or 1 grain?",
                 ["1 clay", "1 grain"])


def _storehouse_keeper_choice(state, player, inst, ctx):
    good = "clay" if ctx["index"] == 0 else "grain"
    add_goods(ctx["extra"], {good: 1})
    ctx["log"].append(f"{player['name']}'s Storehouse Keeper grants 1 "
                      f"{good}")

compendium_card("K288", hooks={"space_used": _storehouse_keeper_space},
                resolve_choice=_storehouse_keeper_choice)


# ── K290 Clay Worker ──────────────────────────────────────────────────
compendium_card("K290", hooks=cards.take_bonus(["wood", "clay"], {"clay": 1}))


# ── K291 Lover ────────────────────────────────────────────────────────
# The declared prereq= below (at least 4 food) is enforced by
# _play_occupation before this hook ever runs.
def _lover_play(state, player, inst, ctx):
    if player["people_total"] >= MAX_PEOPLE:
        return
    player["resources"]["food"] -= 4
    player["people_total"] += 1
    player["people_placed"] += 1
    player["newborns"] += 1
    ctx["log"].append(f"{player['name']}'s Lover grants family growth "
                      "(4 food)")
    cards.fire(state, "family_growth", ctx)

compendium_card(
    "K291",
    prereq=(lambda s, p: p["resources"]["food"] >= 4, "at least 4 food"),
    hooks={"play": _lover_play})


# ── K292 Market Woman ─────────────────────────────────────────────────
# Also triggers on vegetables gained through a minor improvement (e.g.
# Market Stall), but minor_played fires after the minor's own play hook
# already resolved, with no gains delta available in ctx to detect it
# (same gap as B132 in deck B) -- only the "person's action" (space)
# clause is implemented.
compendium_card("K292", hooks=cards.space_bonus(["vegetable_seeds"],
                                                {"grain": 2}))


# ── K293 Ploughman ────────────────────────────────────────────────────
def _ploughman_play(state, player, inst, ctx):
    rnd = state["round"]
    targets = [r for r in (rnd + 4, rnd + 7, rnd + 10) if r <= TOTAL_ROUNDS]
    inst["data"]["rounds"] = targets
    if targets:
        ctx["log"].append("Ploughman schedules a field for rounds "
                          f"{', '.join(map(str, targets))}")


def _ploughman_round_start(state, player, inst, ctx):
    # The printed card lets you decline paying 1 food; round_start hooks
    # cannot prompt, so we auto-plow whenever affordable/possible (the
    # sane default per GUIDE.md, since declining is rarely correct).
    if state["round"] not in inst["data"].get("rounds", ()):
        return
    if player["resources"]["food"] < 1:
        return
    cells = plowable_cells(player)
    if not cells:
        return
    player["resources"]["food"] -= 1
    player["cells"][cells[0]]["type"] = "field"
    ctx["log"].append(f"{player['name']}'s Ploughman plows a field (1 food)")

compendium_card("K293", hooks={"play": _ploughman_play,
                               "round_start": _ploughman_round_start})


# ── K294 Brushwood Collector ──────────────────────────────────────────
def _brushwood_collector_mod(state, player, kind, cost, ctx):
    if kind in ("room", "renovation") and cost.get("reed"):
        cost = dict(cost)
        cost["reed"] = 0
        cost["wood"] = cost.get("wood", 0) + 1
    return cost

compendium_card("K294", cost_mod=_brushwood_collector_mod)


# ── K295 Cattle Breeder ───────────────────────────────────────────────
def _cattle_breeder_round_start(state, player, inst, ctx):
    if state["round"] != 13:
        return
    if cards.animal_totals_of(player)["cattle"] >= 2:
        if _place_animal_best_effort(player, "cattle", 1):
            ctx["log"].append(f"{player['name']}'s Cattle Breeder breeds "
                              "1 cattle (end of round 12)")

compendium_card("K295", hooks={"play": cards.on_play_gain({"cattle": 1}),
                               "round_start": _cattle_breeder_round_start})


# ── K296 Seed Seller ──────────────────────────────────────────────────
_k296_hooks = dict(cards.space_bonus(["grain_seeds"], {"grain": 1}))
_k296_hooks["play"] = cards.on_play_gain({"grain": 1})
compendium_card("K296", hooks=_k296_hooks)


# ── K297 Sheep Farmer ─────────────────────────────────────────────────
def _sheep_farmer_available(state, player, inst):
    return cards.animal_totals_of(player)["sheep"] >= 3


def _sheep_farmer_apply(state, player, inst, ctx):
    if cards.animal_totals_of(player)["sheep"] < 3:
        raise ValueError("Not enough sheep")
    _remove_animal(player, "sheep", 3)
    add_goods(ctx["extra"], {"cattle": 1, "boar": 1})
    ctx["log"].append(f"{player['name']}'s Sheep Farmer exchanges 3 sheep "
                      "for 1 cattle and 1 wild boar")

compendium_card(
    "K297", hooks=cards.take_bonus(["sheep"], {"sheep": 1}),
    card_action={"available": _sheep_farmer_available,
                "apply": _sheep_farmer_apply,
                "description": "Exchange 3 sheep for 1 cattle and 1 wild "
                               "boar"})


# ── K298 Shepherd Boy ─────────────────────────────────────────────────
def _shepherd_boy_schedule(state, player, inst, ctx):
    if inst["data"].get("scheduled"):
        return
    inst["data"]["scheduled"] = True
    rnd = state["round"]
    inst["data"]["rounds"] = list(range(rnd + 1, TOTAL_ROUNDS + 1))
    ctx["log"].append(f"{player['name']}'s Shepherd Boy schedules a free "
                      "sheep on every remaining round")


def _shepherd_boy_play(state, player, inst, ctx):
    if player["house_type"] == "stone":
        _shepherd_boy_schedule(state, player, inst, ctx)


def _shepherd_boy_renovate(state, player, inst, ctx):
    if player["house_type"] == "stone":
        _shepherd_boy_schedule(state, player, inst, ctx)


def _shepherd_boy_round_start(state, player, inst, ctx):
    if state["round"] in inst["data"].get("rounds", ()):
        if _place_animal_best_effort(player, "sheep", 1):
            ctx["log"].append(f"{player['name']}'s Shepherd Boy grants 1 "
                              "sheep")

compendium_card("K298", hooks={"play": _shepherd_boy_play,
                               "renovate": _shepherd_boy_renovate,
                               "round_start": _shepherd_boy_round_start})


# ── K300 Schnaps Distiller ────────────────────────────────────────────
compendium_card("K300", conversions=[
    {"give": {"vegetable": 1}, "get": {"food": 5}, "per_harvest": 1}])


# ── K302 Pig Whisperer ────────────────────────────────────────────────
def _pig_whisperer_play(state, player, inst, ctx):
    rnd = state["round"]
    targets = [r for r in (rnd + 4, rnd + 7, rnd + 10) if r <= TOTAL_ROUNDS]
    inst["data"]["rounds"] = targets
    if targets:
        ctx["log"].append("Pig Whisperer schedules a wild boar for rounds "
                          f"{', '.join(map(str, targets))}")


def _pig_whisperer_round_start(state, player, inst, ctx):
    if state["round"] not in inst["data"].get("rounds", ()):
        return
    if _place_animal_best_effort(player, "boar", 1):
        ctx["log"].append(f"{player['name']}'s Pig Whisperer grants 1 "
                          "wild boar")

compendium_card("K302", hooks={"play": _pig_whisperer_play,
                               "round_start": _pig_whisperer_round_start})


# ── K303 Stone Breaker ────────────────────────────────────────────────
def _stone_breaker_available(state, player, inst):
    if player["house_type"] != "clay":
        return False
    rooms = sum(1 for c in player["cells"] if c["type"] == "room")
    cost = cards.modified_cost(state, player, "renovation",
                               {"stone": rooms, "reed": 1})
    return all(player["resources"].get(r, 0) >= a for r, a in cost.items())


def _stone_breaker_apply(state, player, inst, ctx):
    if player["house_type"] != "clay":
        raise ValueError("Stone Breaker: you must live in a clay hut")
    rooms = sum(1 for c in player["cells"] if c["type"] == "room")
    cost = cards.modified_cost(state, player, "renovation",
                               {"stone": rooms, "reed": 1})
    if not all(player["resources"].get(r, 0) >= a for r, a in cost.items()):
        raise ValueError("Cannot afford the renovation")
    for res, amount in cost.items():
        player["resources"][res] -= amount
    player["house_type"] = "stone"
    ctx["log"].append(f"{player['name']}'s Stone Breaker renovates to a "
                      "stone house")
    cards.fire_player(state, player, "renovate",
                      {"free_stable_cell": None, "actor": ctx["actor"],
                       "log": ctx["log"], "extra": ctx["extra"]})

compendium_card("K303", card_action={
    "available": _stone_breaker_available, "apply": _stone_breaker_apply,
    "description": "Renovate your clay hut to a stone house (any time)"})


# ── K304 Veterinarian ─────────────────────────────────────────────────
_VET_POOL = ["sheep"] * 4 + ["boar"] * 3 + ["cattle"] * 2


def _veterinarian_round_start(state, player, inst, ctx):
    a, b = random.sample(_VET_POOL, 2)
    if a == b and _place_animal_best_effort(player, a, 1):
        ctx["log"].append(f"{player['name']}'s Veterinarian draws a "
                          f"matching pair and keeps 1 {a}")

compendium_card("K304", hooks={"round_start": _veterinarian_round_start})


# ── K305 Animal Handler ───────────────────────────────────────────────
_ANIMAL_HANDLER_SCHEDULE = {7: "sheep", 10: "boar", 14: "cattle"}


def _animal_handler_round_start(state, player, inst, ctx):
    animal = _ANIMAL_HANDLER_SCHEDULE.get(state["round"])
    if not animal or player["resources"]["food"] < 1:
        return
    player["resources"]["food"] -= 1
    if _place_animal_best_effort(player, animal, 1):
        ctx["log"].append(f"{player['name']}'s Animal Handler buys 1 "
                          f"{animal} (1 food)")
    else:
        player["resources"]["food"] += 1

compendium_card("K305", hooks={"round_start": _animal_handler_round_start})


# ── K306 Animal Tamer ─────────────────────────────────────────────────
# Identical text/effect to the base card "occ_animal_tamer".
compendium_card("K306", house_capacity="per_room")


# ── K307 Animal Breeder ───────────────────────────────────────────────
_ANIMAL_BREEDER_PAIRS = [("sheep", 1), ("boar", 2), ("cattle", 3)]


def _animal_breeder_fences(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or not ctx["new_pastures"]:
        return
    options, data = ["Decline"], []
    for animal, cost in _ANIMAL_BREEDER_PAIRS:
        if player["resources"]["food"] >= cost:
            options.append(f"Buy 2 {animal} for {cost} food")
            data.append((animal, cost))
    if len(options) > 1:
        prompt_choice(state, player, inst["id"],
                     "Animal Breeder: buy a pair of animals?", options,
                     data={"choices": data})


def _animal_breeder_choice(state, player, inst, ctx):
    if ctx["index"] == 0:
        return
    animal, cost = ctx["data"]["choices"][ctx["index"] - 1]
    if player["resources"]["food"] < cost:
        return
    player["resources"]["food"] -= cost
    add_goods(ctx["extra"], {animal: 2})
    ctx["log"].append(f"{player['name']}'s Animal Breeder buys 2 {animal} "
                      f"for {cost} food")

compendium_card("K307", hooks={"fences_built": _animal_breeder_fences},
                resolve_choice=_animal_breeder_choice)


# ── K309 Weaver ───────────────────────────────────────────────────────
def _weaver_round_start(state, player, inst, ctx):
    if cards.animal_totals_of(player)["sheep"] >= 2:
        player["resources"]["food"] += 1
        ctx["log"].append(f"{player['name']}'s Weaver grants 1 food")

compendium_card("K309", hooks={"round_start": _weaver_round_start})


# ── K311 Magician ─────────────────────────────────────────────────────
def _magician_space(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or ctx["space_id"] != "traveling_players":
        return
    if player["people_placed"] != player["people_total"]:
        return
    add_goods(ctx["extra"], {"grain": 1, "food": 1})
    ctx["log"].append(f"{player['name']}'s Magician grants 1 grain and 1 "
                      "food")

compendium_card("K311", hooks={"space_used": _magician_space})


# ── K312 Fence Overseer ───────────────────────────────────────────────
def _fence_overseer_stable(state, player, inst, ctx):
    if inst["data"].get("used_round") == state["round"]:
        return
    if player["resources"]["food"] < 1:
        return
    for cell in ctx["cells"]:
        edges = [e for e in cell_edges(cell) if e not in player["fences"]]
        if not edges:
            continue
        layout = player["fences"] + edges
        ok, _err, _pastures = validate_fence_layout(player, layout)
        if ok:
            prompt_choice(state, player, inst["id"],
                         "Fence Overseer: pay 1 food to fence the stable "
                         "you just built?", ["Decline", "Pay 1 food"],
                         data={"edges": edges})
            return


def _fence_overseer_choice(state, player, inst, ctx):
    if ctx["index"] == 0:
        return
    if player["resources"]["food"] < 1:
        return
    edges = ctx["data"]["edges"]
    layout = player["fences"] + edges
    ok, _err, _pastures = validate_fence_layout(player, layout)
    if not ok:
        return
    player["resources"]["food"] -= 1
    player["fences"] = sorted(layout)
    inst["data"]["used_round"] = state["round"]
    ctx["log"].append(f"{player['name']}'s Fence Overseer fences the new "
                      "stable for 1 food")

compendium_card("K312", hooks={"stable_built": _fence_overseer_stable},
                resolve_choice=_fence_overseer_choice)


# ── K342 Animal Trainer ───────────────────────────────────────────────
_ANIMAL_TRAINER_COST = {"sheep": 2, "boar": 2, "cattle": 3}


def _animal_trainer_space(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or ctx["space_id"] != "traveling_players":
        return
    food = ctx["goods"].get("food", 0)
    if food < 2:
        return
    options, data = ["Decline"], []
    for animal, cost in _ANIMAL_TRAINER_COST.items():
        if food >= cost:
            options.append(f"Buy 1 {animal} for {cost} food")
            data.append((animal, cost))
    if len(options) > 1:
        prompt_choice(state, player, inst["id"],
                     "Animal Trainer: use the Traveling Players food to "
                     "buy an animal?", options, data={"choices": data})


def _animal_trainer_choice(state, player, inst, ctx):
    if ctx["index"] == 0:
        return
    animal, cost = ctx["data"]["choices"][ctx["index"] - 1]
    if player["resources"]["food"] < cost:
        return
    player["resources"]["food"] -= cost
    add_goods(ctx["extra"], {animal: 1})
    ctx["log"].append(f"{player['name']}'s Animal Trainer buys 1 {animal} "
                      f"for {cost} food")

compendium_card("K342", hooks={"space_used": _animal_trainer_space},
                resolve_choice=_animal_trainer_choice)
