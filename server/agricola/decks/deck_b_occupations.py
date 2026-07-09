"""Deck B occupations (codes B085-B168 from the compendium DB).

Several DB entries in this deck have `text`/`rulings` fields that are
visibly the concatenation of two or more adjacent cards (a
tools/parse_compendium.py artifact: an embedded "(N-M players)" marker
appears mid-string, restarting an unrelated card's text). For cards we
implement, the corrupted DB text is patched in-place (see _TEXT_FIXES)
to the clean leading clause before compendium_card() reads it; the
trailing bleed after the marker belongs to some other card, not this
one. Cards left UNIMPLEMENTED are not patched (their DB text is never
read by compendium_card in this module).
"""

from server.agricola.cards import (
    compendium_card, add_goods, goods_str, prompt_choice,
)
import server.agricola.cards as cards
from server.agricola import sub_actions
from server.agricola.state import (
    ANIMAL_TYPES, TOTAL_ROUNDS, MAX_PEOPLE,
    compute_pastures, plowable_cells, table_score, MAJOR_IMPROVEMENTS,
)

UNIMPLEMENTED = {
    "B085": "requires a stable in the conceptual 'center' of a 2x2 field "
            "block; the 15-cell farmyard grid has no such position.",
    "B086": "DB text corrupted by a parse error; the card-storage "
            "mechanism ('holds N wild boar') has no specified "
            "acquisition/release trigger once the contaminating text is "
            "removed.",
    "B088": "grants a follow-up Build Fences action (with player-chosen "
            "fence edges) after a free renovation; no channel carries "
            "open-ended fence-layout parameters outside the normal "
            "placement flow.",
    "B093": "grants a full Sow or Build Fences action with player-chosen "
            "field/crop or fence-edge targets at scheduled future round "
            "starts; not expressible via the discrete choice prompt.",
    "B094": "grants an extra worker placement after Fencing; the "
            "engine's only double-placement mechanism (Lasso) is "
            "hardcoded to the animal markets.",
    "B103": "requires a 'decline the Major/Minor Improvement action' "
            "outcome; the major_improvement space always requires "
            "building an improvement or playing a minor (no pass/decline "
            "option exists).",
    "B115": "requires a pasture-capacity bonus that applies only to "
            "stable-less pastures; the existing pasture_capacity_bonus "
            "key is applied uniformly to every pasture by "
            "state.pasture_capacity(), with no per-pasture stable "
            "condition.",
    "B116": "requires reacting to the preparation-phase accumulation-"
            "space replenishment (reed_bank going from empty to 1 reed); "
            "no hook fires at replenish time.",
    "B120": "requires the physical 2D layout of round-space cards ('the "
            "card left of...'); this engine stores revealed cards as an "
            "ordered list with no positional/adjacency layout.",
    "B129": "requires using an action space already occupied by other "
            "players; placing on occupied spaces is explicitly "
            "unsupported.",
    "B130": "grants a full bonus use of Sow/Bake Bread or Fencing with "
            "player-chosen targets; no channel carries those open-ended "
            "parameters (fence edges, per-field sow lists) outside the "
            "normal placement flow.",
    "B132": "requires the amount of vegetable harvested in a given "
            "harvest's field phase, but the harvest_field hook fires "
            "after harvest resources are already merged into general "
            "stock with no delta in ctx -- the per-harvest amount can't "
            "be recovered.",
    "B138": "requires forcing another player to pay you before they can "
            "take wood from an accumulation space (a legality-gating "
            "replacement effect on other players' actions); directly "
            "affecting other players' actions is unsupported.",
    "B140": "requires tracking 'placed a good on a farmyard space' "
            "across a whole work phase, including animal accommodation; "
            "no hook fires when animals are placed via the accommodate "
            "flow, so this could only be partially detected (sowing "
            "only), misrepresenting the card.",
    "B143": "DB text is internally contradictory ('you get 1 clay' "
            "immediately followed by 'you also get 1 clay/food' with an "
            "unclear player-count mapping); likely still parse-"
            "contaminated and not reliably implementable.",
    "B148": "grants extra house capacity for sheep specifically, scaled "
            "by occupation count; the existing house_capacity ability is "
            "animal-type-agnostic and would incorrectly extend capacity "
            "to non-sheep pets too.",
    "B149": "spends 3 of the player's 4 available stables permanently; "
            "MAX_STABLES is a fixed engine constant with no per-player "
            "reduction mechanism, so this cost can't be paid faithfully.",
    "B150": "grants a full bonus use of Build Rooms/Stables or Major "
            "Improvement (cell/room/improvement/minor choice plus cost) "
            "after the other space is unoccupied; would require "
            "re-implementing large parts of the building/improvement "
            "rules outside the normal placement flow.",
    "B151": "requires action spaces not being considered occupied for "
            "this player; placing on occupied spaces is explicitly "
            "unsupported.",
    "B155": "requires substituting food from the Traveling Players "
            "accumulation space as payment for occupation costs; the "
            "engine's occupation-cost payment has no hook allowing an "
            "alternate resource source.",
    "B159": "requires reacting to another player's plow action, but the "
            "'plow' event only fires to the acting player's own cards "
            "(to_all=False) -- no card can observe another player's "
            "plow.",
}

# ── DB text-corruption fixes (see module docstring) ──────────────────
_TEXT_FIXES = {
    "B090": 'Each time you use the "Plow 1 field" action space while the '
            '"Take 1 Grain" action space is occupied, you can plow 1 '
            "additional field.",
    "B096": "Place 1 wood on each of the next 2 odd-numbered round "
            "spaces. At the start of these rounds, you get the wood and, "
            'immediately afterward, a "Minor Improvement" action.',
    "B106": "Immediately before the start of each round, if there are "
            "goods on remaining round spaces that are promised to you, "
            "you get 1 food.",
    "B113": "When you play this card, you can choose to buy 1 grain for "
            "1 food, or 1 vegetable for 3 food. This card is a field.",
    "B117": "When you play this card, you immediately get 1 wood. After "
            "each work phase, if you have more stone than clay in your "
            "supply, you get 1 wood.",
    "B122": "Each time you use a clay/stone accumulation space, you also "
            "get 1 of the other good, stone/clay.",
    "B125": "Place 1 wood, 1 clay, 1 reed, and 1 stone in this order on "
            "the next 4 round spaces. At the start of these rounds, you "
            "get the respective building resource.",
    "B141": "When you play this card, you can immediately exchange 0/1/3 "
            "clay for 1/2/3 grain. This card is a field.",
    "B165": "Immediately before each harvest, you can discard 1/3/4 "
            "grain from different fields to get 1/2/3 wild boars.",
    "B168": "Each time you renovate, you get 2 food and 1 additional "
            "animal of the respective type in each of your pastures "
            "with stable.",
}
for _code, _text in _TEXT_FIXES.items():
    cards.compendium()[_code]["text"] = _text


# ── Shared helpers ────────────────────────────────────────────────────

WOOD_SPACES = ("forest", "grove", "copse")
CLAY_SPACES = ("clay_pit", "hollow_3p", "hollow_4p")


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


# The Lessons spaces' own escalating occupation cost, shared with
# engine._occupation_cost via sub_actions.py (see that module).
_bonus_occ_cost = sub_actions.lessons_occupation_cost


# Minors that read ctx["params"] in their own play hook (Shifting
# Cultivation needs a plow-cell target); the bonus-play channel below
# only supports "which card", so these are excluded from the pool.
_PARAMS_MINORS = {"minor_shifting_cultivation"}


def _free_minor_candidates(state, player):
    return [cid for cid in player["hand_minors"]
            if cid not in _PARAMS_MINORS
            and cards.check_prereq(state, player, cid)
            and all(player["resources"].get(k, 0) >= v
                   for k, v in cards.CARDS[cid]["cost"].items())]


def _offer_free_minor(state, player, inst, label):
    candidates = _free_minor_candidates(state, player)
    if not candidates:
        return
    options = ["Decline"] + [cards.CARDS[cid]["name"] for cid in candidates]
    prompt_choice(state, player, inst["id"], f"{label}: play a minor "
                 "improvement?", options, data={"candidates": candidates})


def _resolve_free_minor(state, player, inst, ctx):
    if ctx["index"] == 0:
        return
    cid = ctx["data"]["candidates"][ctx["index"] - 1]
    spec = cards.CARDS[cid]
    for res, amount in spec["cost"].items():
        player["resources"][res] -= amount
    player["hand_minors"].remove(cid)
    new_inst = cards.new_instance(cid)
    ctx["log"].append(f"{player['name']} plays the minor improvement "
                      f"\"{spec['name']}\"")
    play_fn = spec["hooks"].get("play")
    if play_fn:
        play_fn(state, player, new_inst, {"params": {}, "log": ctx["log"],
                                          "actor": ctx["actor"],
                                          "extra": ctx["extra"]})
    if spec["traveling"]:
        if state["player_count"] > 1:
            left = state["players"][(player["index"] + 1)
                                    % state["player_count"]]
            left["hand_minors"].append(cid)
            ctx["log"].append(f"\"{spec['name']}\" travels to "
                              f"{left['name']}'s hand")
    else:
        player["minors"].append(new_inst)
    cards.fire(state, "minor_played", {"card_id": cid, "actor": ctx["actor"],
                                       "log": ctx["log"],
                                       "extra": ctx["extra"]})


def _total_vegetable(player):
    total = player["resources"]["vegetable"]
    for c in player["cells"]:
        if c["crops"] and c["crops"]["type"] == "vegetable":
            total += c["crops"]["count"]
    for inst in cards.card_fields(player):
        if inst["crops"] and inst["crops"]["type"] == "vegetable":
            total += inst["crops"]["count"]
    return total


# ── B090 Cooperative Plower ───────────────────────────────────────────
def _cooperative_plower_space(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or ctx["space_id"] != "farmland":
        return
    grain_sp = next((s for s in state["action_spaces"]
                     if s["id"] == "grain_seeds"), None)
    if not grain_sp or grain_sp["occupied_by"] is None:
        return
    options = plowable_cells(player)
    if not options:
        return
    prompt_choice(state, player, inst["id"],
                 "Cooperative Plower: plow an additional field?",
                 ["Decline"] + [f"Plow cell {c}" for c in options],
                 data={"cells": options})


def _cooperative_plower_choice(state, player, inst, ctx):
    if ctx["index"] == 0:
        return
    cell = ctx["data"]["cells"][ctx["index"] - 1]
    if cell not in plowable_cells(player):
        return
    player["cells"][cell]["type"] = "field"
    ctx["log"].append(f"{player['name']}'s Cooperative Plower plows an "
                      "additional field")

compendium_card("B090", hooks={"space_used": _cooperative_plower_space},
                resolve_choice=_cooperative_plower_choice)


# ── B092 Little Stick Knitter (Family Growth on Take 1 Sheep) ────────
def _little_stick_knitter_a_space(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or ctx["space_id"] != "sheep_market":
        return
    if state["round"] < 5:
        return
    rooms = sum(1 for c in player["cells"] if c["type"] == "room") \
        + cards.extra_rooms(player)
    if player["people_total"] >= MAX_PEOPLE or rooms <= player["people_total"]:
        return
    prompt_choice(state, player, inst["id"],
                 "Little Stick Knitter: take Family Growth (room only)?",
                 ["Decline", "Family Growth"])


def _little_stick_knitter_a_choice(state, player, inst, ctx):
    if ctx["index"] == 0:
        return
    player["people_total"] += 1
    player["people_placed"] += 1
    player["newborns"] += 1
    ctx["log"].append(f"{player['name']}'s Little Stick Knitter grants "
                      "Family Growth")
    cards.fire(state, "family_growth", ctx)

compendium_card("B092", hooks={"space_used": _little_stick_knitter_a_space},
                resolve_choice=_little_stick_knitter_a_choice)


# ── B096 Tree Farm Joiner ─────────────────────────────────────────────
def _tree_farm_joiner_play(state, player, inst, ctx):
    rnd = state["round"]
    targets = [r for r in range(rnd + 1, TOTAL_ROUNDS + 1) if r % 2 == 1][:2]
    for r in targets:
        slot = state["round_goods"].setdefault(str(r), {}) \
            .setdefault(str(player["index"]), {})
        add_goods(slot, {"wood": 1})
    if targets:
        ctx["log"].append("Tree Farm Joiner places 1 wood on rounds "
                          f"{', '.join(map(str, targets))}")
    inst["data"]["odd_rounds"] = targets


def _tree_farm_joiner_round_start(state, player, inst, ctx):
    if state["round"] not in inst["data"].get("odd_rounds", ()):
        return
    _offer_free_minor(state, player, inst, "Tree Farm Joiner")

compendium_card("B096", hooks={"play": _tree_farm_joiner_play,
                               "round_start": _tree_farm_joiner_round_start},
                resolve_choice=_resolve_free_minor)


# ── B100 Clutterer ────────────────────────────────────────────────────
def _clutterer_track(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    cid = ctx.get("card_id")
    if not cid or cid == inst["id"]:
        return
    text = (cards.CARDS.get(cid) or {}).get("text", "") or ""
    if "accumulation space" in text.lower():
        inst["data"]["count"] = inst["data"].get("count", 0) + 1

compendium_card(
    "B100",
    hooks={"occupation_played": _clutterer_track,
          "minor_played": _clutterer_track},
    score_bonus=lambda s, p, i: i["data"].get("count", 0))


# ── B101 Furniture Carpenter ──────────────────────────────────────────
def _furniture_carpenter_available(state, player, inst):
    if state["phase"] != "feeding":
        return False
    if inst["data"].get("last_used") == state.get("harvest_index"):
        return False
    if player["resources"]["food"] < 2:
        return False
    return any("joinery" in pl["improvements"] for pl in state["players"])


def _furniture_carpenter_apply(state, player, inst, ctx):
    player["resources"]["food"] -= 2
    inst["data"]["last_used"] = state["harvest_index"]
    inst["data"]["bonus"] = inst["data"].get("bonus", 0) + 1
    ctx["log"].append(f"{player['name']}'s Furniture Carpenter buys 1 "
                      "bonus point for 2 food")

compendium_card(
    "B101",
    card_action={"available": _furniture_carpenter_available,
                "apply": _furniture_carpenter_apply,
                "description": "Buy 1 bonus point for 2 food (once per "
                               "harvest, requires any Joinery in play)"},
    score_bonus=lambda s, p, i: i["data"].get("bonus", 0))


# ── B105 Case Builder ─────────────────────────────────────────────────
def _case_builder_play(state, player, inst, ctx):
    gained = {}
    for good in ("food", "grain", "vegetable", "reed", "wood"):
        if player["resources"][good] >= 2:
            gained[good] = 1
    if gained:
        add_goods(player["resources"], gained)
        ctx["log"].append(f"{player['name']}'s Case Builder grants "
                          + goods_str(gained))

compendium_card("B105", hooks={"play": _case_builder_play})


# ── B106 Moral Crusader ───────────────────────────────────────────────
def _moral_crusader_round_start(state, player, inst, ctx):
    rnd = state["round"]
    pidx = str(player["index"])
    for r in range(rnd + 1, TOTAL_ROUNDS + 1):
        slot = state["round_goods"].get(str(r), {}).get(pidx)
        if slot and any(v > 0 for v in slot.values()):
            player["resources"]["food"] += 1
            ctx["log"].append(f"{player['name']}'s Moral Crusader grants "
                              "1 food")
            return

compendium_card("B106", hooks={"round_start": _moral_crusader_round_start})


# ── B110 Pavior ───────────────────────────────────────────────────────
def _pavior_round_start(state, player, inst, ctx):
    if player["resources"]["stone"] < 1:
        return
    good = "vegetable" if state["round"] == 14 else "food"
    player["resources"][good] += 1
    ctx["log"].append(f"{player['name']}'s Pavior grants 1 {good}")

compendium_card("B110", hooks={"round_start": _pavior_round_start})


# ── B111 Rustic ───────────────────────────────────────────────────────
def _rustic_rooms(state, player, inst, ctx):
    if player["house_type"] != "clay":
        return
    n = len(ctx["cells"])
    player["resources"]["food"] += 2 * n
    inst["data"]["bonus"] = inst["data"].get("bonus", 0) + n
    ctx["log"].append(f"{player['name']}'s Rustic grants {2 * n} food")

compendium_card("B111", hooks={"rooms_built": _rustic_rooms},
                score_bonus=lambda s, p, i: i["data"].get("bonus", 0))


# ── B112 Silokeeper ───────────────────────────────────────────────────
def _silokeeper_harvest(state, player, inst, ctx):
    rnd = state["round"]
    if 1 <= rnd <= len(state["revealed"]):
        inst["data"]["watched"] = state["revealed"][rnd - 1]


def _silokeeper_space(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    if ctx["space_id"] == inst["data"].get("watched"):
        player["resources"]["grain"] += 1
        ctx["log"].append(f"{player['name']}'s Silokeeper grants 1 grain")

compendium_card("B112", hooks={"harvest_field": _silokeeper_harvest,
                               "space_used": _silokeeper_space})


# ── B113 Patch Caregiver ──────────────────────────────────────────────
def _patch_caregiver_play(state, player, inst, ctx):
    options, data = ["Decline"], []
    if player["resources"]["food"] >= 1:
        options.append("Buy 1 grain for 1 food")
        data.append(("grain", 1, 1))
    if player["resources"]["food"] >= 3:
        options.append("Buy 1 vegetable for 3 food")
        data.append(("vegetable", 1, 3))
    if len(options) > 1:
        prompt_choice(state, player, inst["id"],
                     "Patch Caregiver: buy a crop?", options,
                     data={"choices": data})


def _patch_caregiver_choice(state, player, inst, ctx):
    if ctx["index"] == 0:
        return
    crop, amount, cost = ctx["data"]["choices"][ctx["index"] - 1]
    if player["resources"]["food"] < cost:
        return
    player["resources"]["food"] -= cost
    player["resources"][crop] += amount
    ctx["log"].append(f"{player['name']}'s Patch Caregiver buys {amount} "
                      f"{crop}")

compendium_card(
    "B113",
    hooks={"play": _patch_caregiver_play},
    resolve_choice=_patch_caregiver_choice,
    field={"crops": ("grain", "vegetable")})


# ── B117 Informant ────────────────────────────────────────────────────
def _informant_play(state, player, inst, ctx):
    add_goods(player["resources"], {"wood": 1})
    ctx["log"].append(f"{player['name']} gets 1 wood (Informant)")


def _informant_round_start(state, player, inst, ctx):
    # Approximates "after each work phase": nothing else changes stone/
    # clay between a work phase ending and the next round_start, except
    # there is no round 15, so the check after round 14's work phase
    # never fires (a documented, minor gap).
    if player["resources"]["stone"] > player["resources"]["clay"]:
        player["resources"]["wood"] += 1
        ctx["log"].append(f"{player['name']}'s Informant grants 1 wood")

compendium_card("B117", hooks={"play": _informant_play,
                               "round_start": _informant_round_start})


# ── B119 Lumberjack ───────────────────────────────────────────────────
def _lumberjack_play(state, player, inst, ctx):
    add_goods(player["resources"], {"wood": 1})
    n = len(player["fences"])
    rnd = state["round"]
    targets = list(range(rnd + 1, min(TOTAL_ROUNDS, rnd + n) + 1))
    for r in targets:
        slot = state["round_goods"].setdefault(str(r), {}) \
            .setdefault(str(player["index"]), {})
        add_goods(slot, {"wood": 1})
    msg = f"{player['name']} gets 1 wood (Lumberjack)"
    if targets:
        msg += (" and schedules 1 wood on rounds "
               f"{', '.join(map(str, targets))}")
    ctx["log"].append(msg)

compendium_card("B119", hooks={"play": _lumberjack_play})


# ── B122 Mineralogist ─────────────────────────────────────────────────
def _mineralogist_space(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    gain = {}
    if ctx["goods"].get("clay"):
        gain["stone"] = gain.get("stone", 0) + 1
    if ctx["goods"].get("stone"):
        gain["clay"] = gain.get("clay", 0) + 1
    if gain:
        add_goods(ctx["extra"], gain)
        ctx["log"].append(f"{player['name']}'s Mineralogist adds "
                          + goods_str(gain))

compendium_card("B122", hooks={"space_used": _mineralogist_space})


# ── B124 Trimmer ──────────────────────────────────────────────────────
def _trimmer_play(state, player, inst, ctx):
    inst["data"]["last_total"] = sum(len(pa) for pa in compute_pastures(player))


def _trimmer_fences(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    total = sum(len(pa) for pa in compute_pastures(player))
    last = inst["data"].get("last_total", 0)
    if total > last:
        player["resources"]["stone"] += 2
        ctx["log"].append(f"{player['name']}'s Trimmer grants 2 stone")
    inst["data"]["last_total"] = total

compendium_card("B124", hooks={"play": _trimmer_play,
                               "fences_built": _trimmer_fences})


# ── B125 Estate Worker ────────────────────────────────────────────────
def _estate_worker_play(state, player, inst, ctx):
    rnd = state["round"]
    goods = ["wood", "clay", "reed", "stone"]
    targets = list(range(rnd + 1, min(TOTAL_ROUNDS, rnd + 4) + 1))
    for i, r in enumerate(targets):
        good = goods[i]
        slot = state["round_goods"].setdefault(str(r), {}) \
            .setdefault(str(player["index"]), {})
        add_goods(slot, {good: 1})
    if targets:
        ctx["log"].append("Estate Worker schedules wood/clay/reed/stone "
                          f"on rounds {', '.join(map(str, targets))}")

compendium_card("B125", hooks={"play": _estate_worker_play})


# ── B127 Seducer ──────────────────────────────────────────────────────
def _seducer_play(state, player, inst, ctx):
    if state["round"] < 5:
        return
    if player["resources"]["stone"] < 1 or player["resources"]["grain"] < 1 \
            or player["resources"]["vegetable"] < 1:
        return
    if cards.animal_totals_of(player)["sheep"] < 1:
        return
    if player["people_total"] >= MAX_PEOPLE:
        return
    prompt_choice(state, player, inst["id"],
                 "Seducer: pay 1 stone, 1 grain, 1 vegetable, 1 sheep for "
                 "Family Growth?", ["Decline", "Pay and grow"])


def _seducer_choice(state, player, inst, ctx):
    if ctx["index"] == 0:
        return
    player["resources"]["stone"] -= 1
    player["resources"]["grain"] -= 1
    player["resources"]["vegetable"] -= 1
    _remove_animal(player, "sheep", 1)
    player["people_total"] += 1
    player["people_placed"] += 1
    player["newborns"] += 1
    ctx["log"].append(f"{player['name']}'s Seducer grants Family Growth")
    cards.fire(state, "family_growth", ctx)

compendium_card("B127", hooks={"play": _seducer_play},
                resolve_choice=_seducer_choice)


# ── B128 Plumber ──────────────────────────────────────────────────────
def _plumber_space(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or ctx["space_id"] != "major_improvement":
        return
    target = {"wood": "clay", "clay": "stone"}.get(player["house_type"])
    if not target:
        return
    rooms = sum(1 for c in player["cells"] if c["type"] == "room")
    cost = cards.modified_cost(state, player, "renovation",
                               {target: rooms, "reed": 1})
    cost[target] = max(0, cost.get(target, 0) - 2)
    cost = {k: v for k, v in cost.items() if v > 0}
    if not all(player["resources"].get(k, 0) >= v for k, v in cost.items()):
        return
    prompt_choice(state, player, inst["id"],
                 "Plumber: renovate for 2 less?", ["Decline", "Renovate"],
                 data={"cost": cost, "target": target})


def _plumber_choice(state, player, inst, ctx):
    if ctx["index"] == 0:
        return
    cost = ctx["data"]["cost"]
    for res, amount in cost.items():
        player["resources"][res] -= amount
    player["house_type"] = ctx["data"]["target"]
    ctx["log"].append(f"{player['name']}'s Plumber renovates to a "
                      f"{ctx['data']['target']} house")
    cards.fire_player(state, player, "renovate",
                      {"free_stable_cell": None, "actor": ctx["actor"],
                       "log": ctx["log"], "extra": ctx["extra"]})

compendium_card("B128", hooks={"space_used": _plumber_space},
                resolve_choice=_plumber_choice)


# ── B131 Equipper ─────────────────────────────────────────────────────
def _equipper_space(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or ctx["space_id"] not in WOOD_SPACES:
        return
    _offer_free_minor(state, player, inst, "Equipper")

compendium_card("B131", hooks={"space_used": _equipper_space},
                resolve_choice=_resolve_free_minor)


# ── B133 Village Peasant ──────────────────────────────────────────────
def _village_peasant_score(state, player, inst):
    n = min(len(player["improvements"]), len(player["minors"]),
           len(player["occupations"]))
    if n <= 0:
        return 0
    veg = _total_vegetable(player)
    return table_score("vegetable", veg + n) - table_score("vegetable", veg)

compendium_card("B133", score_bonus=_village_peasant_score)


# ── B134 Housebook Master ─────────────────────────────────────────────
def _housebook_master_renovate(state, player, inst, ctx):
    if player["house_type"] != "stone" or inst["data"].get("triggered"):
        return
    inst["data"]["triggered"] = True
    rnd = state["round"]
    tier = 3 if rnd <= 11 else 2 if rnd == 12 else 1 if rnd == 13 else 0
    if tier:
        player["resources"]["food"] += tier
        inst["data"]["bonus"] = tier
        ctx["log"].append(f"{player['name']}'s Housebook Master grants "
                          f"{tier} food and {tier} bonus points")

compendium_card("B134", hooks={"renovate": _housebook_master_renovate},
                score_bonus=lambda s, p, i: i["data"].get("bonus", 0))


# ── B135 Nutrition Expert ─────────────────────────────────────────────
def _nutrition_expert_round_start(state, player, inst, ctx):
    if player["resources"]["grain"] < 1 or player["resources"]["vegetable"] < 1:
        return
    totals = cards.animal_totals_of(player)
    available = [a for a in ANIMAL_TYPES if totals[a] > 0]
    if not available:
        return
    options = ["Decline"] + [
        f"Exchange 1 {a} + 1 grain + 1 vegetable for 5 food + 2 points"
        for a in available]
    prompt_choice(state, player, inst["id"], "Nutrition Expert: exchange?",
                 options, data={"animals": available})


def _nutrition_expert_choice(state, player, inst, ctx):
    if ctx["index"] == 0:
        return
    animal = ctx["data"]["animals"][ctx["index"] - 1]
    if player["resources"]["grain"] < 1 or player["resources"]["vegetable"] < 1:
        return
    _remove_animal(player, animal, 1)
    player["resources"]["grain"] -= 1
    player["resources"]["vegetable"] -= 1
    player["resources"]["food"] += 5
    inst["data"]["bonus"] = inst["data"].get("bonus", 0) + 2
    ctx["log"].append(f"{player['name']}'s Nutrition Expert exchanges for "
                      "5 food and 2 points")

compendium_card(
    "B135",
    hooks={"round_start": _nutrition_expert_round_start},
    resolve_choice=_nutrition_expert_choice,
    score_bonus=lambda s, p, i: i["data"].get("bonus", 0))


# ── B137 Wholesaler ───────────────────────────────────────────────────
WHOLESALER_MAP = {"vegetable_seeds": "vegetable", "pig_market": "boar",
                  "cattle_market": "cattle", "eastern_quarry": "stone"}


def _wholesaler_play(state, player, inst, ctx):
    inst["data"].update({"vegetable": 1, "boar": 1, "stone": 1, "cattle": 1})
    ctx["log"].append(f"{player['name']}'s Wholesaler stores 1 vegetable, "
                      "1 wild boar, 1 stone, and 1 cattle")


def _wholesaler_space(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    good = WHOLESALER_MAP.get(ctx["space_id"])
    if good and inst["data"].get(good):
        inst["data"][good] = 0
        add_goods(ctx["extra"], {good: 1})
        ctx["log"].append(f"{player['name']}'s Wholesaler releases 1 {good}")

compendium_card("B137", hooks={"play": _wholesaler_play,
                               "space_used": _wholesaler_space})


# ── B139 Forest Scientist ─────────────────────────────────────────────
def _forest_scientist_space(state, player, inst, ctx):
    if ctx["space_id"] not in WOOD_SPACES:
        return
    if inst["data"].get("rewarded_round") == state["round"]:
        return
    total_wood = sum(sp["supply"].get("wood", 0) for sp in state["action_spaces"]
                     if sp["id"] in WOOD_SPACES)
    if total_wood > 0:
        return
    amount = 2 if state["round"] >= 5 else 1
    player["resources"]["food"] += amount
    inst["data"]["rewarded_round"] = state["round"]
    ctx["log"].append(f"{player['name']}'s Forest Scientist grants "
                      f"{amount} food")

compendium_card("B139", hooks={"space_used": _forest_scientist_space})


# ── B141 Field Caretaker ──────────────────────────────────────────────
def _field_caretaker_play(state, player, inst, ctx):
    tiers = [(0, 1), (1, 2), (3, 3)]
    options, data = ["Decline"], []
    for clay, grain in tiers:
        if player["resources"]["clay"] >= clay:
            options.append(f"Pay {clay} clay for {grain} grain")
            data.append((clay, grain))
    if len(options) > 1:
        prompt_choice(state, player, inst["id"],
                     "Field Caretaker: exchange clay for grain?", options,
                     data={"choices": data})


def _field_caretaker_choice(state, player, inst, ctx):
    if ctx["index"] == 0:
        return
    clay, grain = ctx["data"]["choices"][ctx["index"] - 1]
    if player["resources"]["clay"] < clay:
        return
    player["resources"]["clay"] -= clay
    player["resources"]["grain"] += grain
    ctx["log"].append(f"{player['name']}'s Field Caretaker exchanges "
                      f"{clay} clay for {grain} grain")

compendium_card(
    "B141",
    hooks={"play": _field_caretaker_play},
    resolve_choice=_field_caretaker_choice,
    field={"crops": ("grain", "vegetable")})


# ── B144 Collier ──────────────────────────────────────────────────────
# The DB text's second sentence ("With 3+ players, +1 additional wood on
# the Take 1/2/3 Clay space...") is internally redundant/ambiguous (it
# references a nonexistent "3 clay" space and restates a player-count
# gate this card already has via min_players); omitted as unreliable,
# likely still DB-parse contamination. Only the clean first sentence is
# implemented.
compendium_card("B144", hooks=cards.space_bonus(["clay_pit"],
                                                {"reed": 1, "wood": 1}))


# ── B146 Illusionist ──────────────────────────────────────────────────
def _illusionist_space(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    sp = next((s for s in state["action_spaces"]
              if s["id"] == ctx["space_id"]), None)
    if not sp or not sp["accumulates"]:
        return
    good = next((g for g in ("wood", "clay", "reed", "stone")
                if ctx["goods"].get(g)), None)
    if not good:
        return
    hand = [(cid, "hand_occupations") for cid in player["hand_occupations"]] \
        + [(cid, "hand_minors") for cid in player["hand_minors"]]
    if not hand:
        return
    options = ["Decline"] + [cards.CARDS[cid]["name"] for cid, _ in hand]
    prompt_choice(state, player, inst["id"],
                 f"Illusionist: discard a card for 1 additional {good}?",
                 options, data={"hand": hand, "good": good})


def _illusionist_choice(state, player, inst, ctx):
    if ctx["index"] == 0:
        return
    cid, key = ctx["data"]["hand"][ctx["index"] - 1]
    if cid in player[key]:
        player[key].remove(cid)
    good = ctx["data"]["good"]
    player["resources"][good] += 1
    ctx["log"].append(f"{player['name']}'s Illusionist discards a card "
                      f"for 1 {good}")

compendium_card("B146", hooks={"space_used": _illusionist_space},
                resolve_choice=_illusionist_choice)


# ── B147 Huntsman ─────────────────────────────────────────────────────
def _huntsman_space(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or ctx["space_id"] not in WOOD_SPACES:
        return
    if player["resources"]["grain"] < 1:
        return
    prompt_choice(state, player, inst["id"],
                 "Huntsman: pay 1 grain for 1 wild boar?",
                 ["Decline", "Pay 1 grain"])


def _huntsman_choice(state, player, inst, ctx):
    if ctx["index"] == 0 or player["resources"]["grain"] < 1:
        return
    player["resources"]["grain"] -= 1
    add_goods(ctx["extra"], {"boar": 1})
    ctx["log"].append(f"{player['name']}'s Huntsman converts 1 grain to "
                      "1 wild boar")

compendium_card("B147", hooks={"space_used": _huntsman_space},
                resolve_choice=_huntsman_choice)


# ── B152 Junior Artist ────────────────────────────────────────────────
def _junior_artist_space(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or ctx["space_id"] != "day_laborer":
        return
    if player["resources"]["food"] < 1:
        return
    choices = []  # (label, kind, payload)
    tp = next((s for s in state["action_spaces"]
              if s["id"] == "traveling_players"), None)
    if tp and tp["occupied_by"] is None:
        choices.append(("Take Traveling Players", "traveling", None))
    lessons_id = "lessons_b" if state["player_count"] >= 3 else "lessons"
    lessons_sp = next((s for s in state["action_spaces"]
                       if s["id"] == lessons_id), None)
    if lessons_sp and lessons_sp["occupied_by"] is None:
        occ_cost = _bonus_occ_cost(state, player, lessons_id)
        for cid in player["hand_occupations"]:
            if player["resources"]["food"] >= 1 + occ_cost:
                choices.append((f"Play {cards.CARDS[cid]['name']}",
                               "occupation", cid))
    if not choices:
        return
    options = ["Decline"] + [c[0] for c in choices]
    prompt_choice(state, player, inst["id"],
                 "Junior Artist: use another space for 1 food?", options,
                 data={"choices": [(k, p) for _, k, p in choices],
                      "lessons_id": lessons_id})


def _junior_artist_choice(state, player, inst, ctx):
    if ctx["index"] == 0:
        return
    kind, payload = ctx["data"]["choices"][ctx["index"] - 1]
    if player["resources"]["food"] < 1:
        return
    player["resources"]["food"] -= 1
    if kind == "traveling":
        tp = next(s for s in state["action_spaces"]
                 if s["id"] == "traveling_players")
        goods = dict(tp["supply"])
        tp["supply"] = {}
        tp["occupied_by"] = player["index"]
        add_goods(ctx["extra"], goods)
        ctx["log"].append(f"{player['name']}'s Junior Artist uses "
                          "Traveling Players")
    else:
        cid = payload
        lessons_id = ctx["data"]["lessons_id"]
        lessons_sp = next(s for s in state["action_spaces"]
                          if s["id"] == lessons_id)
        occ_cost = _bonus_occ_cost(state, player, lessons_id)
        if lessons_sp["occupied_by"] is not None \
                or cid not in player["hand_occupations"] \
                or player["resources"]["food"] < occ_cost:
            return
        lessons_sp["occupied_by"] = player["index"]
        sub_actions.play_occupation(state, player, cid, ctx["log"],
                                    cost_override={"food": occ_cost})
        ctx["log"].append(f"{player['name']}'s Junior Artist enabled this play")

compendium_card("B152", hooks={"space_used": _junior_artist_space},
                resolve_choice=_junior_artist_choice)


# ── B153 Housemaster ──────────────────────────────────────────────────
def _housemaster_score(state, player, inst):
    values = sorted(MAJOR_IMPROVEMENTS[i]["points"]
                    for i in player["improvements"])
    if not values:
        return 0
    total = sum(values) + values[0]  # smallest counts double
    for minimum, pts in ((11, 4), (9, 3), (7, 2), (5, 1)):
        if total >= minimum:
            return pts
    return 0

compendium_card("B153", score_bonus=_housemaster_score)


# ── B154 Sheep Keeper ─────────────────────────────────────────────────
# The engine's _play_occupation enforces check_prereq (like minors do),
# so the declarative `prereq=` below (fewer than 7 sheep) already blocks
# playing this card with 7+ sheep -- no play hook needed for that guard.
def _sheep_keeper_check(state, player, inst, ctx):
    if inst["data"].get("triggered"):
        return
    if cards.animal_totals_of(player)["sheep"] >= 7:
        inst["data"]["triggered"] = True
        player["resources"]["food"] += 2
        inst["data"]["bonus"] = 3
        ctx["log"].append(f"{player['name']}'s Sheep Keeper grants 3 "
                          "bonus points and 2 food")

compendium_card(
    "B154",
    prereq=(lambda s, p: cards.animal_totals_of(p)["sheep"] < 7,
           "fewer than 7 sheep"),
    hooks={"round_start": _sheep_keeper_check,
          "harvest_field": _sheep_keeper_check,
          "space_used": _sheep_keeper_check},
    score_bonus=lambda s, p, i: i["data"].get("bonus", 0))


# ── B157 Salter ───────────────────────────────────────────────────────
SALTER_ROUNDS = {"sheep": 3, "boar": 5, "cattle": 7}


def _salter_available(state, player, inst):
    totals = cards.animal_totals_of(player)
    return any(totals[a] > 0 for a in ANIMAL_TYPES)


def _salter_apply(state, player, inst, ctx):
    animal = (ctx.get("params") or {}).get("animal")
    if animal not in ANIMAL_TYPES:
        raise ValueError("Salter: choose an animal type (params.animal)")
    if cards.animal_totals_of(player)[animal] < 1:
        raise ValueError(f"You have no {animal} to pay")
    _remove_animal(player, animal, 1)
    n = SALTER_ROUNDS[animal]
    rnd = state["round"]
    targets = list(range(rnd + 1, min(TOTAL_ROUNDS, rnd + n) + 1))
    for r in targets:
        slot = state["round_goods"].setdefault(str(r), {}) \
            .setdefault(str(player["index"]), {})
        add_goods(slot, {"food": 1})
    ctx["log"].append(f"{player['name']}'s Salter pays 1 {animal} to "
                      "schedule food on rounds "
                      f"{', '.join(map(str, targets))}")

compendium_card("B157", card_action={
    "available": _salter_available, "apply": _salter_apply,
    "description": "Pay an animal to schedule food on future rounds"})


# ── B158 District Manager ─────────────────────────────────────────────
def _district_manager_check(state, player, inst, ctx):
    if ctx["space_id"] not in ("forest", "grove", "copse"):
        return
    if ctx["actor"] != player["index"]:
        return
    if inst["data"].get("rewarded_round") == state["round"]:
        return
    forest = next((s for s in state["action_spaces"] if s["id"] == "forest"),
                  None)
    other = next((s for s in state["action_spaces"]
                 if s["id"] in ("grove", "copse")), None)
    if forest and other and forest["occupied_by"] is not None \
            and other["occupied_by"] is not None:
        amount = 5 if state["player_count"] >= 4 else 0
        if amount:
            player["resources"]["food"] += amount
            inst["data"]["rewarded_round"] = state["round"]
            ctx["log"].append(f"{player['name']}'s District Manager "
                              f"grants {amount} food")

compendium_card("B158", hooks={"space_used": _district_manager_check})


# ── B160 Pub Owner ────────────────────────────────────────────────────
def _pub_owner_check(state, player, inst, ctx):
    if inst["data"].get("rewarded_round") == state["round"]:
        return
    ids = ("forest", "clay_pit", "reed_bank")
    spaces = [next((s for s in state["action_spaces"] if s["id"] == i), None)
             for i in ids]
    if all(sp and sp["occupied_by"] is not None for sp in spaces):
        player["resources"]["grain"] += 1
        inst["data"]["rewarded_round"] = state["round"]
        ctx["log"].append(f"{player['name']}'s Pub Owner grants 1 grain")


def _pub_owner_space(state, player, inst, ctx):
    if ctx["space_id"] in ("forest", "clay_pit", "reed_bank"):
        _pub_owner_check(state, player, inst, ctx)

compendium_card("B160", hooks={"play": _pub_owner_check,
                               "space_used": _pub_owner_space})


# ── B161 Weakling ─────────────────────────────────────────────────────
def _weakling_space(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    used_id = ctx["space_id"]
    qualifying = [sp for sp in state["action_spaces"]
                 if sp["accumulates"] and sp["id"] != used_id
                 and sum(sp["supply"].values()) >= 5]
    if qualifying:
        player["resources"]["vegetable"] += 1
        ctx["log"].append(f"{player['name']}'s Weakling grants 1 vegetable")

compendium_card("B161", hooks={"space_used": _weakling_space})


# ── B162 Forest Clearer ───────────────────────────────────────────────
def _forest_clearer_space(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or ctx["space_id"] not in WOOD_SPACES:
        return
    wood = ctx["goods"].get("wood", 0)
    if wood not in (2, 3, 4):
        return
    bonus_food = {2: 1, 3: 0, 4: 1}[wood]
    gain = {"wood": 1}
    if bonus_food:
        gain["food"] = bonus_food
    add_goods(ctx["extra"], gain)
    ctx["log"].append(f"{player['name']}'s Forest Clearer adds "
                      + goods_str(gain))

compendium_card("B162", hooks={"space_used": _forest_clearer_space})


# ── B165 Game Provider ────────────────────────────────────────────────
# "Immediately before each harvest" is approximated with harvest_field,
# the only harvest-adjacent hook available; it fires after this
# harvest's own field-phase crop gain, not strictly before it.
def _game_provider_harvest(state, player, inst, ctx):
    grain_fields = [i for i, c in enumerate(player["cells"])
                   if c["type"] == "field" and c["crops"]
                   and c["crops"]["type"] == "grain"]
    tiers = [(4, 3), (3, 2), (1, 1)]
    options, data = ["Decline"], []
    for need, boars in tiers:
        if len(grain_fields) >= need:
            options.append(f"Discard {need} grain field(s) for {boars} "
                           "wild boar")
            data.append((need, boars))
    if len(options) > 1:
        prompt_choice(state, player, inst["id"],
                     "Game Provider: discard grain fields for boar?",
                     options, data={"choices": data, "fields": grain_fields})


def _game_provider_choice(state, player, inst, ctx):
    if ctx["index"] == 0:
        return
    need, boars = ctx["data"]["choices"][ctx["index"] - 1]
    fields = ctx["data"]["fields"][:need]
    for i in fields:
        player["cells"][i]["crops"] = None
    add_goods(ctx["extra"], {"boar": boars})
    ctx["log"].append(f"{player['name']}'s Game Provider discards {need} "
                      f"grain field(s) for {boars} wild boar")

compendium_card(
    "B165",
    hooks={"harvest_field": _game_provider_harvest},
    resolve_choice=_game_provider_choice)


# ── B167 Stable Sergeant ──────────────────────────────────────────────
def _stable_sergeant_play(state, player, inst, ctx):
    if player["resources"]["food"] >= 2:
        prompt_choice(state, player, inst["id"],
                     "Stable Sergeant: pay 2 food for 1 sheep, 1 wild "
                     "boar, and 1 cattle?", ["Decline", "Pay 2 food"])


def _stable_sergeant_choice(state, player, inst, ctx):
    if ctx["index"] == 0 or player["resources"]["food"] < 2:
        return
    player["resources"]["food"] -= 2
    add_goods(ctx["extra"], {"sheep": 1, "boar": 1, "cattle": 1})
    ctx["log"].append(f"{player['name']}'s Stable Sergeant buys 1 sheep, "
                      "1 wild boar, and 1 cattle")

compendium_card(
    "B167",
    hooks={"play": _stable_sergeant_play},
    resolve_choice=_stable_sergeant_choice)


# ── B168 Pasture Master ───────────────────────────────────────────────
def _pasture_master_renovate(state, player, inst, ctx):
    player["resources"]["food"] += 2
    for pasture in compute_pastures(player):
        if not any(player["cells"][i]["stable"] for i in pasture):
            continue
        atype = next((player["cells"][i]["animal"]["type"] for i in pasture
                     if player["cells"][i]["animal"]), None)
        if atype:
            ctx["extra"][atype] = ctx["extra"].get(atype, 0) + 1
    ctx["log"].append(f"{player['name']}'s Pasture Master grants 2 food")

compendium_card("B168", hooks={"renovate": _pasture_master_renovate})
