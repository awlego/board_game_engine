"""Deck A minor improvements (codes A001..A084 from the compendium DB).

Data-quality note: several entries in compendium_cards.json for deck A have
their "text" field bleed into the *next* printed card on the source page —
a clean sentence is followed by a spurious mid-paragraph "(Cost X.)" or
"(Req X.)" marker introducing an unrelated effect (confirmed independently:
some of those tails are verbatim duplicates of cards already in the base
deck, e.g. Manger/Shepherd's Crook-style text turning up mid-entry). Where
that pattern appears, only the head clause — the part consistent with the
entry's own `cost`/`prereq`/`vp` fields — is implemented; the tail is
treated as noise from the adjacent card, not a second effect of this card.
That call is noted per card below.

General simplifications applied across this module (documented once here
rather than repeated on every card):
- Effects phrased "you can/may ..." that are strictly beneficial with no
  real downside are auto-applied rather than offered as a prompt, to keep
  the module tractable; effects with a genuine trade-off use
  `prompt_choice`.
- "Returning home phase of each round" effects have no dedicated engine
  hook. They are implemented on `round_start` (which fires right after the
  returning-home/harvest housekeeping for the round that just ended),
  treating `ctx["round"] - 1` as "the round that just ended". This misses
  the very last round's returning-home window (no further round_start
  fires after the game ends) — a minor, documented gap.
- A round_start hook may NOT raise a `prompt_choice`: `_start_round` resets
  `state["prompts"] = []` before firing round_start hooks, and any pending
  prompt makes `_placement_actions()` report nothing playable for every
  player; if the prompt isn't answered before the round-transition cascade
  moves on, it is silently discarded by the next round's reset (confirmed
  empirically). Cards whose printed text is "at [round boundary], you can
  ..." (A029, A076) therefore auto-apply from round_start rather than
  prompting, even where the effect has a genuine trade-off.
"""

from server.agricola.cards import (
    compendium_card, new_instance, spec, in_play,
    add_goods, goods_str, prompt_choice,
    space_bonus, on_play_gain,
    animal_totals_of, needs_occupations, combine, check_prereq, card_fields,
)
from server.agricola import sub_actions
from server.agricola.state import (
    TOTAL_ROUNDS, NUM_CELLS, ANIMAL_TYPES, HARVEST_ROUNDS, MAX_PEOPLE,
    MAX_STABLES, MAJOR_IMPROVEMENTS, FIREPLACES, COOKING_HEARTHS,
    compute_pastures, plowable_cells,
)

UNIMPLEMENTED = {}


# ── Shared helpers ────────────────────────────────────────────────────

def _schedule(state, player, good, rounds_ahead, amount=1):
    """Place `good` on each of the next `rounds_ahead` round spaces."""
    rnd = state["round"]
    targets = list(range(rnd + 1, min(TOTAL_ROUNDS, rnd + rounds_ahead) + 1))
    for r in targets:
        slot = state["round_goods"].setdefault(str(r), {}) \
            .setdefault(str(player["index"]), {})
        add_goods(slot, {good: amount})
    return targets


def _unused_cells(player):
    """Farmyard cells with nothing on them at all: no room/field/stable,
    no fence-enclosed pasture animal, nothing. Used by cards that count or
    target genuinely untouched farmyard spaces."""
    return [i for i, c in enumerate(player["cells"])
            if c["type"] == "empty" and not c["stable"] and not c["animal"]]


def _field_cells(player, crop=None):
    out = []
    for i, c in enumerate(player["cells"]):
        if c["type"] != "field":
            continue
        if crop is None or (c["crops"] and c["crops"]["type"] == crop):
            out.append(i)
    return out


def _owns_hearth_tier(player):
    return any(i in FIREPLACES or i in COOKING_HEARTHS
               for i in player["improvements"])


def _owns_bake(player):
    if any(MAJOR_IMPROVEMENTS[i].get("bake") for i in player["improvements"]):
        return True
    return any(spec(i).get("bake") for i in in_play(player))


def _best_bake_rate(player):
    best = 0
    for i in player["improvements"]:
        b = MAJOR_IMPROVEMENTS[i].get("bake")
        if b:
            best = max(best, b[1])
    for i in in_play(player):
        b = spec(i).get("bake")
        if b:
            best = max(best, b[1])
    return best


def _is_accumulation_space(state, space_id):
    sp = next((s for s in state["action_spaces"] if s["id"] == space_id), None)
    return bool(sp and sp.get("accumulates"))


# ── A001 Shelter ──────────────────────────────────────────────────────
# Head clause only: "You can immediately build a stable at no cost, but
# only if you place it in a pasture covering exactly 1 farmyard space."
# The tail ("(Cost 2F.) Immediately plow 1 field.") is a different card's
# text bleeding in (its own cost field is empty, matching only the head).
def _shelter_play(state, player, inst, ctx):
    params = ctx.get("params") or {}
    cell = params.get("stable_cell")
    if cell is not None:
        pastures = compute_pastures(player)
        c = player["cells"][cell] if isinstance(cell, int) and 0 <= cell < NUM_CELLS else None
        if c is None or c["type"] != "empty" or c["stable"] \
                or not any(p == [cell] for p in pastures):
            raise ValueError("Shelter: stable_cell must be a 1-cell pasture")
        c["stable"] = True
        ctx["log"].append(f"{player['name']}'s Shelter builds a free stable")

compendium_card(
    "A001",
    cost={},
    hooks={"play": _shelter_play},
)


# ── A003 Paper Knife ──────────────────────────────────────────────────
def _paper_knife_play(state, player, inst, ctx):
    import random
    hand = player["hand_occupations"]
    if len(hand) < 3:
        return
    pool = random.sample(hand, 3)
    cid = random.choice(pool)
    if not check_prereq(state, player, cid):
        hand.remove(cid)
        ctx["log"].append(
            f"{player['name']}'s Paper Knife selects an occupation it "
            "cannot play; it is removed from the game")
        return
    hand.remove(cid)
    occ_inst = new_instance(cid)
    player["occupations"].append(occ_inst)
    player["occs_played"] += 1
    ctx["log"].append(
        f"{player['name']}'s Paper Knife plays \"{spec(cid)['name']}\" for free")
    play_fn = spec(cid)["hooks"].get("play")
    if play_fn:
        sub_ctx = {"params": {}, "log": ctx["log"], "actor": player["index"],
                   "extra": {}}
        play_fn(state, player, occ_inst, sub_ctx)
        add_goods(ctx["extra"], sub_ctx["extra"])

compendium_card(
    "A003",
    prereq=(lambda s, p: len(p["hand_occupations"]) >= 3,
            "3 occupations in hand"),
    hooks={"play": _paper_knife_play},
)


# ── A004 Baseboards ───────────────────────────────────────────────────
# Head clause only: "You immediately get 1 wood for each room you have.
# If you have more rooms than people, you get 1 additional wood." DB cost
# "2F or 1 Grain" doesn't parse (alternative costs aren't supported by a
# plain cost dict); the first option (2 food) is used.
def _baseboards_play(state, player, inst, ctx):
    rooms = sum(1 for c in player["cells"] if c["type"] == "room")
    wood = rooms + (1 if rooms > player["people_total"] else 0)
    add_goods(ctx["extra"], {"wood": wood})
    ctx["log"].append(f"{player['name']}'s Baseboards grants {wood} wood")

compendium_card(
    "A004",
    cost={"food": 2},
    hooks={"play": _baseboards_play},
)


# ── A006 Storage Barn ─────────────────────────────────────────────────
def _storage_barn_play(state, player, inst, ctx):
    gain = {}
    if "joinery" in player["improvements"]:
        gain["wood"] = 1
    if "pottery" in player["improvements"]:
        gain["clay"] = 1
    if "basketmaker" in player["improvements"]:
        gain["reed"] = 1
    if "well" in player["improvements"]:
        gain["stone"] = 1
    if gain:
        add_goods(ctx["extra"], gain)
        ctx["log"].append(f"{player['name']}'s Storage Barn grants "
                          + goods_str(gain))

compendium_card("A006", hooks={"play": _storage_barn_play})


# ── A007 Gardener's Knife ─────────────────────────────────────────────
def _gardeners_knife_play(state, player, inst, ctx):
    grain_fields = len(_field_cells(player, "grain")) + \
        sum(1 for i in card_fields(player)
            if i["crops"] and i["crops"]["type"] == "grain")
    veg_fields = len(_field_cells(player, "vegetable")) + \
        sum(1 for i in card_fields(player)
            if i["crops"] and i["crops"]["type"] == "vegetable")
    gain = {}
    if grain_fields:
        gain["food"] = grain_fields
    if veg_fields:
        gain["grain"] = veg_fields
    if gain:
        add_goods(ctx["extra"], gain)
        ctx["log"].append(f"{player['name']}'s Gardener's Knife grants "
                          + goods_str(gain))

compendium_card("A007", hooks={"play": _gardeners_knife_play})


# ── A008 Food Basket ──────────────────────────────────────────────────
# Head clause only: "You immediately receive 1 grain and 1 vegetable."
# The tail (sheep-for-cattle exchange) is a different card's text.
compendium_card(
    "A008",
    prereq=combine(needs_occupations(2),
                   (lambda s, p: len(p["improvements"]) >= 2, "2 improvements")),
    hooks={"play": on_play_gain({"grain": 1, "vegetable": 1})},
)


UNIMPLEMENTED["A010"] = (
    "Wooden Shed grants room for a person but its cost is 'you cannot "
    "renovate anymore' — no engine hook can block a future renovate action, "
    "so the drawback can't be enforced (implementing only the benefit would "
    "misrepresent the card)")

UNIMPLEMENTED["A011"] = (
    "Mud Patch's core ability is holding 1 wild boar on each unplanted "
    "field tile — animal placement validation (state.validate_animal_placement) "
    "has no concept of field cells as animal storage; would need engine "
    "changes")


# ── A013 Renovation Company ───────────────────────────────────────────
def _renovation_company_play(state, player, inst, ctx):
    add_goods(ctx["extra"], {"clay": 3})
    ctx["log"].append(f"{player['name']}'s Renovation Company grants 3 clay")
    prompt_choice(state, player, inst["id"],
                 "Renovate now at no cost? (Renovation Company)",
                 ["Renovate for free", "Skip"])

def _renovation_company_choice(state, player, inst, ctx):
    if ctx["index"] != 0 or not sub_actions.can_renovate(state, player, cost_override="free"):
        return
    sub_actions.renovate(state, player, ctx["log"], cost_override="free")
    ctx["log"].append(f"{player['name']} pays nothing (Renovation Company)")

compendium_card(
    "A013",
    prereq=(lambda s, p: p["house_type"] == "wood"
            and sum(1 for c in p["cells"] if c["type"] == "room") == 2,
            "in a wooden house with exactly 2 rooms"),
    hooks={"play": _renovation_company_play},
    resolve_choice=_renovation_company_choice,
)


UNIMPLEMENTED["A014"] = (
    "Carpenter's Hammer discounts room-building only when >=2 rooms are "
    "built at once, but cost_mod for kind='room' isn't given a build-count "
    "in ctx (only 'fences' passes ctx['count']), so the condition can't be "
    "evaluated")


# ── A015 Carpenter's Axe ──────────────────────────────────────────────
# Implements 2 of 3 clauses. Omitted: "you can use clay instead of wood to
# build fences" — cost payment has no player-choice/substitution channel
# (modified_cost is a pure fold with no way to offer 'pay X or Y').
def _carpenters_axe_action_available(state, player, inst):
    if state["phase"] != "work" or state["current_player"] != player["index"]:
        return False
    if player["resources"]["wood"] < 7:
        return False
    return sub_actions.can_build_stables(state, player, cost_override={"wood": 1})

def _carpenters_axe_action_apply(state, player, inst, ctx):
    cell = (ctx.get("params") or {}).get("cell")
    if not isinstance(cell, int):
        raise ValueError("Carpenter's Axe: choose a cell")
    sub_actions.build_stables(state, player, [cell], ctx["log"],
                              cost_override={"wood": 1})
    ctx["log"].append(f"{player['name']} used the Carpenter's Axe discount")

compendium_card(
    "A015",
    hooks={"play": on_play_gain({"clay": 1})},
    card_action={
        "available": _carpenters_axe_action_available,
        "apply": _carpenters_axe_action_apply,
        "description": "Build 1 stable for 1 wood (Carpenter's Axe, needs 7+ wood)",
    },
)


UNIMPLEMENTED["A017"] = (
    "Reclamation Plow triggers after animals are 'accommodated' following "
    "an accumulation-space take, but the accommodate action has no fire() "
    "event — nothing hooks the resolution of an accommodate prompt")

UNIMPLEMENTED["A018"] = (
    "Wheel Plow needs 'first person placed this round' plus a once-per-game "
    "flag plus a 2-cell follow-up choice, triggered off the 'plow' event; "
    "the plow ctx carries only the single cell just plowed with no action-"
    "space id, and chaining a reliable multi-cell choice on top of that is "
    "too fragile to implement with confidence")


# ── A020 Double-Turn Plow ─────────────────────────────────────────────
# Prereq ruling: "cannot play after Round 5" resolves the odd "3 (5)" text.
def _double_turn_plow_play(state, player, inst, ctx):
    cells = (ctx.get("params") or {}).get("cells") or []
    if not isinstance(cells, list) or len(cells) > 2:
        raise ValueError("Double-Turn Plow: choose up to 2 cells to plow")
    plowed = []
    for cell in cells:
        if not isinstance(cell, int) or cell not in plowable_cells(player) \
                or cell in plowed:
            raise ValueError("Double-Turn Plow: invalid cell to plow")
        player["cells"][cell]["type"] = "field"
        plowed.append(cell)
    if plowed:
        ctx["log"].append(f"{player['name']} plows {len(plowed)} field(s) "
                          "(Double-Turn Plow)")
    if state["round"] <= 3:
        add_goods(ctx["extra"], {"food": 1})
        ctx["log"].append(f"{player['name']}'s Double-Turn Plow grants 1 food")

compendium_card(
    "A020",
    cost={"grain": 1, "food": 1},
    prereq=(lambda s, p: s["round"] <= 5, "played in round 5 or before"),
    hooks={"play": _double_turn_plow_play},
)


# ── A021 Family Friendly Home ─────────────────────────────────────────
def _family_friendly_home_rooms(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    rooms = sum(1 for c in player["cells"] if c["type"] == "room")
    if rooms > player["people_total"] and player["people_total"] < MAX_PEOPLE:
        player["people_total"] += 1
        player["newborns"] += 1
        add_goods(ctx["extra"], {"food": 1})
        ctx["log"].append(f"{player['name']}'s Family Friendly Home grants "
                          "a Family Growth and 1 food")

compendium_card(
    "A021",
    prereq=needs_occupations(1),
    hooks={"rooms_built": _family_friendly_home_rooms},
)


UNIMPLEMENTED["A022"] = (
    "Telegram places a guest as an additional person for a round — guest "
    "tokens / extra people aren't supported")


# ── A023 Stone Company ────────────────────────────────────────────────
# Scope note: the granted "Major or Minor Improvement" action is restricted
# to major improvements here (none of the implemented minors in this pool
# cost stone, so the minor-improvement branch would be untestable dead
# code); it also skips the well/upgrade/bake-on-build special cases that
# the full _do_improvement path handles.
def _stone_company_space(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    if not ctx["goods"].get("stone") or \
            not _is_accumulation_space(state, ctx["space_id"]):
        return
    inst["data"]["pending"] = True

def _stone_company_available(state, player, inst):
    return bool(inst["data"].get("pending")) and state["phase"] == "work" \
        and state["current_player"] == player["index"]

def _stone_company_apply(state, player, inst, ctx):
    from server.agricola.cards import modified_cost
    imp = (ctx.get("params") or {}).get("improvement")
    if imp not in MAJOR_IMPROVEMENTS or imp not in state["available_improvements"]:
        raise ValueError("Stone Company: choose an available major improvement")
    cost = modified_cost(state, player, "improvement", MAJOR_IMPROVEMENTS[imp]["cost"])
    if cost.get("stone", 0) < 1:
        raise ValueError("Stone Company: must spend at least 1 stone")
    if any(player["resources"].get(k, 0) < v for k, v in cost.items()):
        raise ValueError("Cannot afford that improvement")
    for k, v in cost.items():
        player["resources"][k] -= v
    state["available_improvements"].remove(imp)
    player["improvements"].append(imp)
    inst["data"]["pending"] = False
    ctx["log"].append(f"{player['name']} builds {MAJOR_IMPROVEMENTS[imp]['name']} "
                      "(Stone Company)")

compendium_card(
    "A023",
    hooks={"space_used": _stone_company_space},
    card_action={
        "available": _stone_company_available,
        "apply": _stone_company_apply,
        "description": "Build a major improvement spending >=1 stone (Stone Company)",
    },
)


UNIMPLEMENTED["A025"] = (
    "Bassinet lets you place an additional person on an occupied action "
    "space — guest tokens / placing on occupied spaces aren't supported")


# ── A027 Oven Site ────────────────────────────────────────────────────
# Prereq judgment call: DB says "both fireplace and cooking hearth", but a
# player can never own both simultaneously (upgrading removes the
# fireplace) — read as "or" (owns some hearth-tier improvement), matching
# how Clay/Stone Oven's own normal prereq works.
def _oven_site_play(state, player, inst, ctx):
    add_goods(ctx["extra"], {"wood": 2})
    ctx["log"].append(f"{player['name']}'s Oven Site grants 2 wood")
    prompt_choice(state, player, inst["id"],
                 "Build Clay Oven or Stone Oven for 1 clay + 1 stone? (Oven Site)",
                 ["Clay Oven", "Stone Oven", "Skip"])

def _oven_site_choice(state, player, inst, ctx):
    imp = {"0": "clay_oven", "1": "stone_oven"}.get(str(ctx["index"]))
    if imp is None:
        return
    if imp not in state["available_improvements"]:
        ctx["log"].append(f"{MAJOR_IMPROVEMENTS[imp]['name']} is not available")
        return
    cost = {"clay": 1, "stone": 1}
    if any(player["resources"].get(k, 0) < v for k, v in cost.items()):
        ctx["log"].append(f"{player['name']} cannot afford the "
                          f"{MAJOR_IMPROVEMENTS[imp]['name']}")
        return
    for k, v in cost.items():
        player["resources"][k] -= v
    state["available_improvements"].remove(imp)
    player["improvements"].append(imp)
    ctx["log"].append(f"{player['name']} builds the {MAJOR_IMPROVEMENTS[imp]['name']} "
                      "for 1 clay + 1 stone (Oven Site)")

compendium_card(
    "A027",
    prereq=(lambda s, p: _owns_hearth_tier(p), "a fireplace or cooking hearth"),
    hooks={"play": _oven_site_play},
    resolve_choice=_oven_site_choice,
)


UNIMPLEMENTED["A028"] = (
    "Forest School's two clauses (treat occupation spaces as unoccupied; "
    "pay occupation cost in wood instead of food) are both unsupported: "
    "placing on occupied spaces is on the not-supported list, and "
    "occupation cost payment has no substitution channel")


# ── A029 Ale-Benches ──────────────────────────────────────────────────
# Auto-applies rather than prompting: `_start_round` resets state["prompts"]
# to [] *before* firing round_start hooks, and any pending prompt makes
# _placement_actions() report "nothing to do" for every player, so a
# prompt raised here would just get silently discarded by the next round's
# reset if not answered before the round-transition cascade moves on
# (confirmed empirically — the round_start-created prompt vanished after
# a couple of forfeited rounds). Auto-applying sidesteps that hazard.
def _ale_benches_round_start(state, player, inst, ctx):
    ended = ctx["round"] - 1
    if ended < 1 or player["resources"]["grain"] < 1:
        return
    player["resources"]["grain"] -= 1
    inst["data"]["bonus"] = inst["data"].get("bonus", 0) + 1
    for p in state["players"]:
        if p["index"] != player["index"]:
            p["resources"]["food"] += 1
    ctx["log"].append(f"{player['name']} pays 1 grain for 1 bonus point; "
                      "other players get 1 food (Ale-Benches)")

compendium_card(
    "A029",
    prereq=needs_occupations(2),
    hooks={"round_start": _ale_benches_round_start},
    score_bonus=lambda s, p, i: i["data"].get("bonus", 0),
)


# ── A030 Baking Sheet ─────────────────────────────────────────────────
def _baking_sheet_bake(state, player, inst, ctx):
    if ctx["grain"] < 1 or player["resources"]["grain"] < 1:
        return
    player["resources"]["grain"] -= 1
    player["resources"]["food"] += 2
    inst["data"]["bonus"] = inst["data"].get("bonus", 0) + 1
    ctx["log"].append(f"{player['name']}'s Baking Sheet exchanges 1 grain "
                      "for 2 food and 1 bonus point")

compendium_card(
    "A030",
    prereq=(lambda s, p: not _field_cells(p, "grain"), "no grain field"),
    hooks={"bake": _baking_sheet_bake},
    score_bonus=lambda s, p, i: i["data"].get("bonus", 0),
)


# ── A031 Debt Security ────────────────────────────────────────────────
# Head clause only: bonus per major improvement, capped by unused
# farmyard spaces. The tail duplicates minor_manger's text (a bleed).
def _debt_security_score(state, player, inst):
    return min(len(player["improvements"]), len(_unused_cells(player)))

compendium_card("A031", score_bonus=_debt_security_score)


UNIMPLEMENTED["A034"] = (
    "Loppers spends '1 fence in your supply' (a future fence-building "
    "allowance) alongside 1 wood — the engine has no concept of a "
    "per-player consumable fence-piece pool distinct from the wood cost "
    "of building fences")


# ── A035 Swimming Class ───────────────────────────────────────────────
# "Returning home ... if you return a person from Fishing" is tracked via a
# space_used flag (set when the actor uses Fishing) and consumed at the
# next round_start, sidestepping the fact that action_spaces' occupied_by
# is already reset by the time round_start fires. The newborn count is
# snapshotted (on fishing, refreshed on any later family_growth that round)
# rather than read at round_start time, because _end_round zeroes
# player["newborns"] *before* calling _start_round — by the time round_start
# fires the real returning-home newborn count is already gone.
def _swimming_class_space(state, player, inst, ctx):
    if ctx["actor"] == player["index"] and ctx["space_id"] == "fishing":
        inst["data"]["fished"] = True
        inst["data"]["newborns_snapshot"] = player["newborns"]

def _swimming_class_growth(state, player, inst, ctx):
    if inst["data"].get("fished"):
        inst["data"]["newborns_snapshot"] = player["newborns"]

def _swimming_class_round_start(state, player, inst, ctx):
    if not inst["data"].get("fished"):
        return
    inst["data"]["fished"] = False
    bonus = 2 * inst["data"].get("newborns_snapshot", 0)
    inst["data"]["newborns_snapshot"] = 0
    if bonus:
        inst["data"]["bonus"] = inst["data"].get("bonus", 0) + bonus
        ctx["log"].append(f"{player['name']}'s Swimming Class grants "
                          f"{bonus} bonus points")

compendium_card(
    "A035",
    prereq=needs_occupations(2),
    hooks={"space_used": _swimming_class_space,
           "family_growth": _swimming_class_growth,
           "round_start": _swimming_class_round_start},
    score_bonus=lambda s, p, i: i["data"].get("bonus", 0),
)


# ── A036 Facades Carving ──────────────────────────────────────────────
def _facades_carving_play(state, player, inst, ctx):
    n = min(state["harvest_index"], player["resources"]["food"])
    if n <= 0:
        return
    prompt_choice(state, player, inst["id"],
                 "Exchange food for bonus points (1:1, up to completed "
                 "harvests)? (Facades Carving)",
                 [str(k) for k in range(n + 1)])

def _facades_carving_choice(state, player, inst, ctx):
    n = int(ctx["option"])
    if n <= 0 or player["resources"]["food"] < n:
        return
    player["resources"]["food"] -= n
    inst["data"]["bonus"] = inst["data"].get("bonus", 0) + n
    ctx["log"].append(f"{player['name']} exchanges {n} food for {n} bonus "
                      "points (Facades Carving)")

compendium_card(
    "A036",
    prereq=(lambda s, p: p["resources"]["wood"] >= s["round"],
            "wood in supply >= current round"),
    hooks={"play": _facades_carving_play},
    resolve_choice=_facades_carving_choice,
    score_bonus=lambda s, p, i: i["data"].get("bonus", 0),
)


# ── A037 Bucksaw ──────────────────────────────────────────────────────
# Head clause only ("Each time you renovate, you can also pay 1 wood to
# get 1 bonus point and 1 grain."); the tail ("Req 5 Sheep... scoring by
# house type") is a different card bleeding in.
def _bucksaw_renovate(state, player, inst, ctx):
    if player["resources"]["wood"] < 1:
        return
    prompt_choice(state, player, inst["id"],
                 "Pay 1 wood for 1 bonus point and 1 grain? (Bucksaw)",
                 ["Pay 1 wood", "Skip"])

def _bucksaw_choice(state, player, inst, ctx):
    if ctx["index"] != 0 or player["resources"]["wood"] < 1:
        return
    player["resources"]["wood"] -= 1
    player["resources"]["grain"] += 1
    inst["data"]["bonus"] = inst["data"].get("bonus", 0) + 1
    ctx["log"].append(f"{player['name']} pays 1 wood for 1 bonus point and "
                      "1 grain (Bucksaw)")

compendium_card(
    "A037",
    hooks={"renovate": _bucksaw_renovate},
    resolve_choice=_bucksaw_choice,
    score_bonus=lambda s, p, i: i["data"].get("bonus", 0),
)


UNIMPLEMENTED["A039"] = (
    "Chapel is itself a usable action space open to all players — the "
    "engine has no mechanism for a card to add a new action space to the "
    "board at runtime")


# ── A040 Potter's Yard ────────────────────────────────────────────────
# Simplifies "receive the clay, which you can immediately exchange for 2
# food" to a flat +2 food per redeemed cell (skips the option to keep it
# as clay instead, which nothing else in this pool makes meaningful use
# of).
def _potters_yard_play(state, player, inst, ctx):
    inst["data"]["marked"] = _unused_cells(player)
    ctx["log"].append(f"{player['name']}'s Potter's Yard marks "
                      f"{len(inst['data']['marked'])} farmyard spaces")

def _potters_yard_redeem(state, player, inst, cells, ctx):
    marked = inst["data"].get("marked", [])
    hit = [c for c in cells if c in marked]
    if not hit:
        return
    for c in hit:
        marked.remove(c)
    add_goods(ctx["extra"], {"food": 2 * len(hit)})
    ctx["log"].append(f"{player['name']}'s Potter's Yard pays out "
                      f"{2 * len(hit)} food")

compendium_card(
    "A040",
    prereq=(lambda s, p: len(_unused_cells(p)) <= 7,
            "at most 7 unused farmyard spaces"),
    hooks={
        "play": _potters_yard_play,
        "plow": lambda s, p, i, ctx: _potters_yard_redeem(s, p, i, [ctx["cell"]], ctx),
        "rooms_built": lambda s, p, i, ctx: _potters_yard_redeem(s, p, i, ctx["cells"], ctx),
        "stable_built": lambda s, p, i, ctx: _potters_yard_redeem(s, p, i, ctx["cells"], ctx),
        "fences_built": lambda s, p, i, ctx: _potters_yard_redeem(
            s, p, i, [c for pasture in ctx["new_pastures"] for c in pasture], ctx),
    },
)


# ── A041 Vegetable Slicer ─────────────────────────────────────────────
# Uses the "improvement_built" hook (fired by engine._do_improvement for
# every major-improvement build/upgrade; not in GUIDE's table but wired
# identically to the documented events). Can't distinguish an upgrade from
# a fresh direct build of a Cooking Hearth (ctx carries only the resulting
# improvement id), so this also fires on a fresh hearth build without a
# prior Fireplace — a minor broadening, not a narrowing, of the real card.
def _vegetable_slicer_built(state, player, inst, ctx):
    if ctx["improvement"] in COOKING_HEARTHS:
        add_goods(ctx["extra"], {"wood": 2, "vegetable": 1})
        ctx["log"].append(f"{player['name']}'s Vegetable Slicer grants "
                          "2 wood and 1 vegetable")

compendium_card("A041", hooks={"improvement_built": _vegetable_slicer_built})


# ── A042 Forest Lake Hut ──────────────────────────────────────────────
def _forest_lake_hut_space(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    if ctx["space_id"] == "fishing":
        add_goods(ctx["extra"], {"wood": 1})
        ctx["log"].append(f"{player['name']}'s Forest Lake Hut grants 1 wood")
    elif ctx["goods"].get("wood") and _is_accumulation_space(state, ctx["space_id"]):
        add_goods(ctx["extra"], {"food": 1})
        ctx["log"].append(f"{player['name']}'s Forest Lake Hut grants 1 food")

compendium_card("A042", hooks={"space_used": _forest_lake_hut_space})


# ── A043 Farmyard Manure ──────────────────────────────────────────────
def _farmyard_manure_stable(state, player, inst, ctx):
    targets = _schedule(state, player, "food", 3)
    if targets:
        ctx["log"].append(f"{player['name']}'s Farmyard Manure schedules "
                          "food on the next 3 round spaces")

compendium_card(
    "A043",
    prereq=(lambda s, p: sum(animal_totals_of(p).values()) >= 1, "1 animal"),
    hooks={"stable_built": _farmyard_manure_stable},
)


# ── A045 Fire Protection Pond ─────────────────────────────────────────
def _fire_protection_pond_renovate(state, player, inst, ctx):
    if inst["data"].get("done") or player["house_type"] == "wood":
        return
    inst["data"]["done"] = True
    targets = _schedule(state, player, "food", 6)
    if targets:
        ctx["log"].append(f"{player['name']}'s Fire Protection Pond schedules "
                          "food on the next 6 round spaces")

compendium_card(
    "A045",
    prereq=(lambda s, p: p["house_type"] == "wood", "still in a wooden house"),
    hooks={"renovate": _fire_protection_pond_renovate},
)


# ── A046 Claw Knife ───────────────────────────────────────────────────
def _claw_knife_space(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or ctx["space_id"] != "sheep_market":
        return
    targets = _schedule(state, player, "food", 2)
    if targets:
        ctx["log"].append(f"{player['name']}'s Claw Knife schedules food on "
                          "the next 2 round spaces")

compendium_card(
    "A046",
    prereq=(lambda s, p: len(compute_pastures(p)) == 1, "exactly 1 pasture"),
    hooks={"space_used": _claw_knife_space},
)


# ── A047 Trellises ────────────────────────────────────────────────────
def _trellises_play(state, player, inst, ctx):
    n = len(player["fences"])
    if n <= 0:
        return
    targets = _schedule(state, player, "food", n)
    if targets:
        ctx["log"].append(f"{player['name']}'s Trellises schedules food on "
                          f"the next {len(targets)} round spaces")

compendium_card("A047", hooks={"play": _trellises_play})


UNIMPLEMENTED["A048"] = (
    "Shaving Horse reacts to 'each time you obtain 1 wood' from any "
    "source at all — there is no generic 'resource gained' hook (only "
    "specific ones like space_used/round_start/bake), so a universal "
    "wood-gain trigger can't be expressed")

UNIMPLEMENTED["A049"] = (
    "Nest Site's head effect triggers on reed being added to a "
    "non-empty Reed Bank during the preparation-phase replenishment; "
    "that replenishment isn't a hookable event (round_start fires after "
    "it already happened, with no 'was it empty before' information)")


# ── A051 Drift-Net Boat ───────────────────────────────────────────────
compendium_card("A051", hooks=space_bonus(["fishing"], {"food": 2}))


# ── A052 Throwing Axe ─────────────────────────────────────────────────
# Head clause only: the tail ("returning home... 7 building resources")
# is a different card's text bleeding in.
def _throwing_axe_space(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or not ctx["goods"].get("wood"):
        return
    boar_space = next((s for s in state["action_spaces"]
                       if s["id"] == "pig_market"), None)
    if boar_space and boar_space["supply"].get("boar", 0) >= 1:
        add_goods(ctx["extra"], {"food": 2})
        ctx["log"].append(f"{player['name']}'s Throwing Axe grants 2 food")

compendium_card(
    "A052",
    prereq=(lambda s, p: s["round"] >= 7, "played in round 7 or later"),
    hooks={"space_used": _throwing_axe_space},
)


# ── A054 Credit ───────────────────────────────────────────────────────
# Head clause only: "get 5 food; at the end of each non-harvest round pay
# 1 food or take a begging marker." Tail (build-improvement food, wood
# accumulation exchange) is bleed from other cards.
def _credit_round_start(state, player, inst, ctx):
    ended = ctx["round"] - 1
    if ended < 1 or ended in HARVEST_ROUNDS:
        return
    if player["resources"]["food"] >= 1:
        player["resources"]["food"] -= 1
    else:
        player["begging"] += 1
        ctx["log"].append(f"{player['name']} takes a begging marker (Credit)")

compendium_card(
    "A054",
    prereq=(lambda s, p: len(p["occupations"]) <= 3, "at most 3 occupations"),
    hooks={"play": on_play_gain({"food": 5}),
           "round_start": _credit_round_start},
)


# ── A057 Milking Parlor ───────────────────────────────────────────────
def _milking_parlor_play(state, player, inst, ctx):
    totals = animal_totals_of(player)
    gain = 0
    if totals["sheep"] >= 4:
        gain += 4
    elif totals["sheep"] >= 3:
        gain += 3
    elif totals["sheep"] >= 1:
        gain += 2
    if totals["cattle"] >= 3:
        gain += 4
    elif totals["cattle"] >= 2:
        gain += 3
    elif totals["cattle"] >= 1:
        gain += 2
    if gain:
        add_goods(ctx["extra"], {"food": gain})
        ctx["log"].append(f"{player['name']}'s Milking Parlor grants {gain} food")

compendium_card(
    "A057",
    prereq=(lambda s, p: len(_unused_cells(p)) >= 4, "at least 4 unused farmyard spaces"),
    hooks={"play": _milking_parlor_play},
)


# ── A058 Asparagus Knife ──────────────────────────────────────────────
def _asparagus_knife_round_start(state, player, inst, ctx):
    if ctx["round"] - 1 not in (8, 10, 12):
        return
    veg_fields = _field_cells(player, "vegetable")
    if not veg_fields:
        return
    cell = veg_fields[0]
    crops = player["cells"][cell]["crops"]
    crops["count"] -= 1
    if crops["count"] <= 0:
        player["cells"][cell]["crops"] = None
    player["resources"]["food"] += 3
    inst["data"]["bonus"] = inst["data"].get("bonus", 0) + 1
    ctx["log"].append(f"{player['name']}'s Asparagus Knife takes 1 vegetable "
                      "for 3 food and 1 bonus point")

compendium_card(
    "A058",
    hooks={"round_start": _asparagus_knife_round_start},
    score_bonus=lambda s, p, i: i["data"].get("bonus", 0),
)


# ── A059 Potato Ridger ────────────────────────────────────────────────
def _potato_ridger_harvest(state, player, inst, ctx):
    if player["resources"]["vegetable"] >= 3:
        player["resources"]["vegetable"] -= 1
        player["resources"]["food"] += 6
        ctx["log"].append(f"{player['name']}'s Potato Ridger exchanges 1 "
                          "vegetable for 6 food")

compendium_card("A059", hooks={"harvest_field": _potato_ridger_harvest})


# ── A060 Oriental Fireplace ───────────────────────────────────────────
# Skips the "counts as minor or major, whichever is convenient, never
# both" scoring-category rule — score-sheet category manipulation is
# beyond bonus points and isn't supported; the card simply scores as a
# minor improvement (its registered type).
def _oriental_fireplace_play(state, player, inst, ctx):
    owned = next((i for i in player["improvements"]
                 if i in FIREPLACES or i in COOKING_HEARTHS), None)
    if owned:
        player["improvements"].remove(owned)
        state["available_improvements"].append(owned)
        state["available_improvements"].sort()
        ctx["log"].append(f"{player['name']} returns their {owned} to pay for "
                          "the Oriental Fireplace")

compendium_card(
    "A060",
    prereq=(lambda s, p: _owns_hearth_tier(p), "a fireplace or cooking hearth"),
    hooks={"play": _oriental_fireplace_play},
    cook={"vegetable": 4, "sheep": 3, "cattle": 5},
    bake=(None, 2),
)


# ── A061 Winnowing Fan ────────────────────────────────────────────────
def _winnowing_fan_harvest(state, player, inst, ctx):
    rate = _best_bake_rate(player)
    if rate and player["resources"]["grain"] >= 1:
        player["resources"]["grain"] -= 1
        player["resources"]["food"] += rate
        ctx["log"].append(f"{player['name']}'s Winnowing Fan bakes 1 grain "
                          f"for {rate} food")

compendium_card(
    "A061",
    prereq=(lambda s, p: _owns_bake(p), "a baking improvement"),
    hooks={"harvest_field": _winnowing_fan_harvest},
)


# ── A062 Beer Keg ─────────────────────────────────────────────────────
def _beer_keg_available(state, player, inst):
    return (state["phase"] == "feeding" and not player["fed"]
            and player["resources"]["grain"] >= 1
            and inst["data"].get("used_harvest") != state["harvest_index"])

def _beer_keg_apply(state, player, inst, ctx):
    n = (ctx.get("params") or {}).get("grain")
    if n not in (1, 2, 3):
        raise ValueError("Beer Keg: choose 1, 2, or 3 grain")
    if player["resources"]["grain"] < n:
        raise ValueError("Not enough grain")
    player["resources"]["grain"] -= n
    player["resources"]["food"] += 3
    inst["data"]["bonus"] = inst["data"].get("bonus", 0) + (n - 1)
    inst["data"]["used_harvest"] = state["harvest_index"]
    ctx["log"].append(f"{player['name']} uses Beer Keg: {n} grain for 3 food "
                      f"and {n - 1} bonus point(s)")

compendium_card(
    "A062",
    prereq=(lambda s, p: p["resources"]["grain"] >= 2, "2 grain in supply"),
    card_action={
        "available": _beer_keg_available,
        "apply": _beer_keg_apply,
        "description": "Exchange 1/2/3 grain for 3 food and 0/1/2 bonus points (Beer Keg)",
    },
    score_bonus=lambda s, p, i: i["data"].get("bonus", 0),
)


UNIMPLEMENTED["A064"] = (
    "Barley Mill pays 1 food per grain field harvested this harvest, but "
    "harvest_field fires after the field-phase loop has already decremented "
    "(and possibly cleared to None) each field's crop count, and its ctx "
    "carries no per-player harvested-count — the number of grain fields "
    "harvested this turn can't be recovered")


# ── A065 Seed Pellets ──────────────────────────────────────────────────
def _seed_pellets_sow(state, player, inst, ctx):
    if ctx["sown"]:
        player["resources"]["grain"] += 1
        ctx["log"].append(f"{player['name']}'s Seed Pellets grants 1 grain")

compendium_card(
    "A065",
    prereq=(lambda s, p: len(_field_cells(p)) >= 3, "3 fields"),
    hooks={"sow": _seed_pellets_sow},
)


# ── A066 Feeding Dish ──────────────────────────────────────────────────
# Head clause only; the tail (Grain Seeds bonus grain) duplicates
# occ_grain_farmer's effect and is bleed from another card.
def _feeding_dish_space(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    totals = animal_totals_of(player)
    for t in ANIMAL_TYPES:
        if ctx["goods"].get(t) and totals[t] > 0:
            player["resources"]["grain"] += 1
            ctx["log"].append(f"{player['name']}'s Feeding Dish grants 1 grain")
            return

compendium_card("A066", hooks={"space_used": _feeding_dish_space})


UNIMPLEMENTED["A068"] = (
    "Asparagus Gift compares fences built in one action to the current "
    "round number, but the fences_built ctx carries only 'new_pastures' "
    "(completed pasture cell-groups), not the count of fence edges built "
    "this action")


# ── A070 Lifting Machine ──────────────────────────────────────────────
def _lifting_machine_round_start(state, player, inst, ctx):
    ended = ctx["round"] - 1
    if ended < 1 or ended in HARVEST_ROUNDS:
        return
    veg_fields = _field_cells(player, "vegetable")
    if not veg_fields:
        return
    cell = veg_fields[0]
    crops = player["cells"][cell]["crops"]
    crops["count"] -= 1
    if crops["count"] <= 0:
        player["cells"][cell]["crops"] = None
    player["resources"]["vegetable"] += 1
    ctx["log"].append(f"{player['name']}'s Lifting Machine moves 1 vegetable "
                      "to supply")

compendium_card(
    "A070",
    prereq=(lambda s, p: len(_field_cells(p)) >= 3, "3 fields"),
    hooks={"round_start": _lifting_machine_round_start},
)


# ── A072 Calcium Fertilizers ──────────────────────────────────────────
def _calcium_fertilizers_space(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or not ctx["goods"].get("stone") \
            or not _is_accumulation_space(state, ctx["space_id"]):
        return
    grown = 0
    for i, c in enumerate(player["cells"]):
        if c["type"] == "field" and c["crops"]:
            c["crops"]["count"] += 1
            grown += 1
    for inst2 in card_fields(player):
        if inst2["crops"]:
            inst2["crops"]["count"] += 1
            grown += 1
    if grown:
        ctx["log"].append(f"{player['name']}'s Calcium Fertilizers adds a "
                          f"crop to {grown} planted field(s)")

compendium_card(
    "A072",
    prereq=(lambda s, p: not _field_cells(p), "no field tiles"),
    hooks={"space_used": _calcium_fertilizers_space},
)


# ── A073 Agricultural Fertilizers ─────────────────────────────────────
def _agricultural_fertilizers_trigger(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or len(ctx["cells"]) < 2:
        return
    inst["data"]["pending"] = True

def _agri_available(state, player, inst):
    return bool(inst["data"].get("pending")) and state["phase"] == "work" \
        and state["current_player"] == player["index"]

def _agri_apply(state, player, inst, ctx):
    params = ctx.get("params") or {}
    crop = params.get("crop")
    if crop not in ("grain", "vegetable"):
        raise ValueError("Choose grain or vegetable to sow")
    if player["resources"][crop] < 1:
        raise ValueError(f"Not enough {crop}")
    count = 3 if crop == "grain" else 2
    if "card" in params:
        target = next((i for i in card_fields(player)
                       if i["id"] == params["card"] and not i["crops"]), None)
        if target is None:
            raise ValueError("Invalid card field")
        allowed = spec(target["id"])["field"]["crops"]
        if crop not in allowed:
            raise ValueError("That field cannot grow that crop")
        target["crops"] = {"type": crop, "count": count}
    else:
        cell = params.get("cell")
        if not isinstance(cell, int) or not (0 <= cell < NUM_CELLS):
            raise ValueError("Invalid field")
        c = player["cells"][cell]
        if c["type"] != "field" or c["crops"]:
            raise ValueError("You can only sow empty fields")
        c["crops"] = {"type": crop, "count": count}
    player["resources"][crop] -= 1
    inst["data"]["pending"] = False
    ctx["log"].append(f"{player['name']} sows {crop} (Agricultural Fertilizers)")

compendium_card(
    "A073",
    prereq=(lambda s, p: len(compute_pastures(p)) >= 1, "1 pasture"),
    hooks={"rooms_built": _agricultural_fertilizers_trigger,
           "stable_built": _agricultural_fertilizers_trigger},
    card_action={
        "available": _agri_available,
        "apply": _agri_apply,
        "description": "Bonus Sow action (Agricultural Fertilizers)",
    },
)


# ── A074 Stable Tree ──────────────────────────────────────────────────
# Head clause only; the tail ("Every improvement costs 1 wood less") is a
# different card bleeding in.
def _stable_tree_stable(state, player, inst, ctx):
    targets = _schedule(state, player, "wood", 3)
    if targets:
        ctx["log"].append(f"{player['name']}'s Stable Tree schedules wood on "
                          "the next 3 round spaces")

compendium_card("A074", hooks={"stable_built": _stable_tree_stable})


# ── A076 Cob ──────────────────────────────────────────────────────────
# Auto-applies (see the note on A029 above: a round_start-raised prompt
# gets silently discarded by _start_round's prompt reset if the round-
# transition cascade moves past it). Always strictly beneficial (net +2
# clay, +1 food for -1 grain), so auto-applying changes nothing a rational
# player wouldn't already choose.
def _cob_round_start(state, player, inst, ctx):
    if player["resources"]["clay"] >= 1 and player["resources"]["grain"] >= 1:
        player["resources"]["clay"] += 2  # net: -1 +3
        player["resources"]["grain"] -= 1
        player["resources"]["food"] += 1
        ctx["log"].append(f"{player['name']} exchanges 1 clay + 1 grain for "
                          "3 clay + 1 food (Cob)")

compendium_card(
    "A076",
    hooks={"round_start": _cob_round_start},
)


# ── A077 Hod ──────────────────────────────────────────────────────────
# Head clause only; the tail (Fishing bonus food+reed) is a different card.
compendium_card(
    "A077",
    hooks={
        "play": on_play_gain({"clay": 1}),
        **space_bonus(["pig_market"], {"clay": 2}, others=True),
    },
)


# ── A079 Garden Hoe ────────────────────────────────────────────────────
# Head clause only; the tail (bonus stone from stone spaces) is a
# different card bleeding in.
def _garden_hoe_sow(state, player, inst, ctx):
    if any(crop == "vegetable" for _target, crop in ctx["sown"]):
        add_goods(ctx["extra"], {"clay": 1, "stone": 1})
        ctx["log"].append(f"{player['name']}'s Garden Hoe grants 1 clay "
                          "and 1 stone")

compendium_card("A079", hooks={"sow": _garden_hoe_sow})


# ── A081 Interim Storage ──────────────────────────────────────────────
def _interim_storage_space(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    stash = inst["data"].setdefault("stash", {})
    if ctx["goods"].get("clay"):
        add_goods(stash, {"wood": 1})
    if ctx["goods"].get("reed"):
        add_goods(stash, {"clay": 1})
    if ctx["goods"].get("stone"):
        add_goods(stash, {"reed": 1})

def _interim_storage_round_start(state, player, inst, ctx):
    if ctx["round"] not in (7, 11, 14):
        return
    stash = inst["data"].get("stash") or {}
    if any(stash.values()):
        add_goods(player["resources"], stash)
        ctx["log"].append(f"{player['name']}'s Interim Storage releases "
                          + goods_str(stash))
    inst["data"]["stash"] = {}

compendium_card(
    "A081",
    hooks={"space_used": _interim_storage_space,
           "round_start": _interim_storage_round_start},
)


# ── A082 Work Certificate ─────────────────────────────────────────────
# Head clause only; the tail (2 sheep for a >=4-space pasture) duplicates
# minor_shepherds_crook and is bleed from another card.
def _work_certificate_space(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    choices = []
    for sp in state["action_spaces"]:
        for good in ("wood", "clay", "reed", "stone"):
            if sp.get("supply", {}).get(good, 0) >= 4:
                choices.append((sp["id"], good))
    if not choices:
        return
    options = [f"1 {good} from {sid}" for sid, good in choices] + ["Skip"]
    prompt_choice(state, player, inst["id"],
                 "Take 1 building resource? (Work Certificate)", options,
                 data={"choices": choices})

def _work_certificate_choice(state, player, inst, ctx):
    choices = ctx["data"]["choices"]
    if ctx["index"] >= len(choices):
        return
    sid, good = choices[ctx["index"]]
    sp = next((s for s in state["action_spaces"] if s["id"] == sid), None)
    if not sp or sp.get("supply", {}).get(good, 0) < 1:
        return
    sp["supply"][good] -= 1
    add_goods(ctx["extra"], {good: 1})
    ctx["log"].append(f"{player['name']}'s Work Certificate takes 1 {good} "
                      f"from {sid}")

compendium_card(
    "A082",
    prereq=needs_occupations(3),
    hooks={"space_used": _work_certificate_space},
    resolve_choice=_work_certificate_choice,
)


# ── A084 Silage ────────────────────────────────────────────────────────
# Simplifies away the choice of grain source (supply vs. a field) and of
# which animal type to breed: pays from supply if possible (else the
# first available field grain), and breeds the first animal type owned.
# Places the bred animal via the same silent best-effort placement engine
# breeding itself uses (engine._place_newborn_animal), rather than through
# ctx["extra"] / the accommodate prompt: a round_start hook must never
# leave a prompt pending (see the module docstring) — an accommodate
# prompt is just as unsafe here as a choice prompt would be.
def _silage_round_start(state, player, inst, ctx):
    ended = ctx["round"] - 1
    if ended < 1 or ended in HARVEST_ROUNDS:
        return
    totals = animal_totals_of(player)
    animal = next((t for t in ANIMAL_TYPES if totals[t] >= 1), None)
    if animal is None:
        return
    from server.agricola.engine import AgricolaEngine
    if not AgricolaEngine()._place_newborn_animal(player, animal):
        return  # no room to breed; nothing spent
    if player["resources"]["grain"] >= 1:
        player["resources"]["grain"] -= 1
    else:
        grain_fields = _field_cells(player, "grain")
        if not grain_fields:
            return
        cell = grain_fields[0]
        crops = player["cells"][cell]["crops"]
        crops["count"] -= 1
        if crops["count"] <= 0:
            player["cells"][cell]["crops"] = None
    ctx["log"].append(f"{player['name']}'s Silage breeds 1 {animal} for 1 grain")

compendium_card(
    "A084",
    prereq=(lambda s, p: len(_field_cells(p)) >= 2, "2 fields"),
    hooks={"round_start": _silage_round_start},
)
