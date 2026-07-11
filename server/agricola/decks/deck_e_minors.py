"""Deck E minor improvements (codes E11..E62, E338 from the compendium DB).

Deck E is an ORIGINAL-edition deck. Card texts reference the original
board's action spaces and major-improvement names; these are mapped onto
this engine's equivalents per decks/GUIDE.md's rule 4:
  - "Take 1 Grain" -> grain_seeds, "Day Labourer" -> day_laborer,
    "Fishing" -> fishing, "1 Stone" accumulation spaces -> western_quarry
    / eastern_quarry.
  - Fireplace A1/A2 -> fireplace_2/3, Cooking Hearth A3/A4 ->
    cooking_hearth_4/5, Clay Oven A5 -> clay_oven, Stone Oven A6 ->
    stone_oven, Pottery A8 -> pottery, Joinery A7 -> joinery.

Data-quality note: unlike deck B, no clause-bleed artifacts (an
unrelated "(N-M players)" tag, or a clause contradicting this card's own
cost/vp/prereq, mid-text) were found in this slice -- every DB text here
reads as a single coherent card, so no _TEXT_FIXES patch table is needed.

General simplifications (documented once, not repeated per card):
- Effects requiring a genuine free-form choice with no parameter or
  prompt channel in the engine (e.g. "your choice of which resource to
  discount") are marked UNIMPLEMENTED rather than collapsed to a fixed
  choice, per the guide's "don't approximate silently" rule.
- A handful of cards partially implement their printed text (documented
  inline) where one clause is expressible and a second clause hits an
  engine limitation already catalogued in sibling decks (score-category
  manipulation, etc. -- cost_mod's lack of an improvement-id channel was
  one of these until engine phase 7 added ctx['improvement']/ctx['card'],
  see E15/E36's UNIMPLEMENTED notes) -- these are registered (not
  UNIMPLEMENTED) since real effect remains.
- Traveling cards (pass to the left neighbor) never keep their instance
  in play, so any decision they need is taken as a `params` value at
  play time (mirroring Shifting Cultivation in the base deck), never via
  `prompt_choice` -- a queued prompt referencing a traveling card's
  instance id can never be resolved, since `_play_minor` only appends
  non-traveling instances to `player["minors"]`.
"""

from server.agricola.cards import (
    compendium_card, CARDS, new_instance, spec, in_play, add_goods,
    goods_str, prompt_choice, parse_cost, take_bonus, space_bonus,
    round_income, schedule_on_play, harvest_food, on_play_gain,
    animal_totals_of, needs_occupations, exact_occupations,
    needs_grain_field, combine, card_fields, modified_cost, fire,
    fire_player, check_prereq,
)
from server.agricola import sub_actions
from server.agricola.state import (
    ANIMAL_TYPES, TOTAL_ROUNDS, NUM_CELLS, MAX_STABLES,
    MAJOR_IMPROVEMENTS, compute_pastures, plowable_cells,
    orthogonal_neighbors, cell_edges, validate_fence_layout,
)

UNIMPLEMENTED = {
    "E15": "makes Clay Oven/Stone Oven count as minor improvements for "
           "you (score-category manipulation beyond bonus points is "
           "still unsupported -- this half remains blocked) and "
           "discounts their cost by 1 resource of your choice. "
           "modified_cost's kind='improvement'/'minor' calls now carry "
           "ctx['improvement']/ctx['card'] (engine phase 7), so a "
           "cost_mod CAN target just these cards (and the Wood-fired "
           "Oven minor, E27) without misfiring on every other "
           "improvement/minor -- the discount half is no longer a "
           "plumbing gap, only the score-category-manipulation half is. "
           "Reassessed per the fidelity rule: the reclassification is the "
           "card's headline clause (its own name is 'Baking Tray' after "
           "this effect), not a cosmetic aside -- it changes which "
           "scoring category and which other cards' 'how many minors do "
           "you have' conditions the Ovens count against. Registering "
           "only the discount and dropping the reclassification would "
           "silently misrepresent the card, so it stays fully "
           "unimplemented rather than half-registered.",
    "E19": "requires detecting a Fireplace/Cooking Hearth 'convert 2+ "
           "goods to food at once' event; the cook conversion in "
           "_apply_feed's feeding-phase loop never fires a card event "
           "(same gap as B029).",
    "E25": "same gap as Gypsy's Crock (E19): the vegetable-to-food cook "
           "conversion in _apply_feed never fires a card event to react "
           "to.",
    "E26": "requires reacting to a Joinery/Sawmill/Cabinetmaker "
           "wood-to-food conversion; the via='joinery' branch of "
           "_apply_feed's conversion loop doesn't fire any card event "
           "either (same class of gap as D083's harvest-food "
           "conversions).",
    "E29": "two blocked mechanics: the +2 pasture capacity only for "
           "pastures 'where you keep sheep' can't be expressed since "
           "pasture_capacity_bonus is a flat additive across every "
           "pasture regardless of animal type; and unfenced-stable "
           "capacity is hardcoded to 1 in validate_animal_placement "
           "with no card-modifier hook (same class of gap as B012).",
    "E58": "a card-held slot for 2 animals of any type, kept outside the "
           "house/pastures; validate_animal_placement only recognizes "
           "pastures, unfenced stables, and the house as accommodation "
           "buckets (same gap as B012).",
    "E338": "would gain an animal (duplicating one already owned) "
            "through a feeding-phase conversion, but the engine's "
            "conversion 'get' path only credits player['resources'], "
            "with no accommodation route for animal types (same gap as "
            "D083).",
}

_WOOD_ACC_SPACES = ("forest", "grove", "copse")
_OVENS = ("clay_oven", "stone_oven")


def _owns_oven(state, player):
    """'An oven' for Ceramics' prereq: Clay/Stone Oven majors, or the
    Baker's Oven/Wood-fired Oven cards (E14/E27)."""
    if any(i in _OVENS for i in player["improvements"]):
        return True
    return any(i["id"] in ("E14", "E27") for i in player["minors"])


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


# ── E11 Field ──────────────────────────────────────────────────────────
def _e11_play(state, player, inst, ctx):
    cell = (ctx.get("params") or {}).get("cell")
    if not isinstance(cell, int) or cell not in plowable_cells(player):
        raise ValueError("Field: choose a space to plow (params.cell)")
    player["cells"][cell]["type"] = "field"
    ctx["log"].append(f"{player['name']} plows a field (Field)")

compendium_card("E11", cost={"food": 1}, traveling=True,
                hooks={"play": _e11_play})


# ── E12 Fishing Rod ─────────────────────────────────────────────────────
def _e12_hook(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or ctx["space_id"] != "fishing":
        return
    gain = 2 if state["round"] >= 8 else 1
    add_goods(ctx["extra"], {"food": gain})
    ctx["log"].append(f"{player['name']}'s Fishing Rod adds {gain} food")

compendium_card("E12", cost={"wood": 1}, hooks={"space_used": _e12_hook})


# ── E13 Axe ──────────────────────────────────────────────────────────────
# Sets a new base room cost for a WOODEN hut (2 wood + 2 reed, down from
# the default 5 wood + 2 reed); mirrors occ_carpenter's shape.
def _e13_mod(state, player, kind, cost, ctx):
    if kind == "room" and player["house_type"] == "wood" and cost.get("wood"):
        cost = dict(cost)
        cost["wood"] = 2 * ctx.get("count", 1)
    return cost

compendium_card("E13", cost={"wood": 1, "stone": 1}, cost_mod=_e13_mod)


# ── E14 Baker's Oven ─────────────────────────────────────────────────────
def _e14_play(state, player, inst, ctx):
    owned = next((i for i in player["improvements"] if i in _OVENS), None)
    if owned:
        player["improvements"].remove(owned)
        state["available_improvements"].append(owned)
        state["available_improvements"].sort()
        ctx["log"].append(f"{player['name']} returns their "
                          f"{MAJOR_IMPROVEMENTS[owned]['name']} to build "
                          "the Baker's Oven")
    grain = player["resources"]["grain"]
    if grain >= 1:
        maxn = min(2, grain)
        options = [f"Bake {n} grain for {n * 5} food"
                  for n in range(1, maxn + 1)] + ["Skip baking now"]
        prompt_choice(state, player, inst["id"],
                      "Baker's Oven: bake bread immediately?", options)

def _e14_resolve(state, player, inst, ctx):
    opt = ctx["option"]
    if opt.startswith("Bake"):
        n = int(opt.split()[1])
        if player["resources"]["grain"] >= n:
            player["resources"]["grain"] -= n
            gained = n * 5
            add_goods(ctx["extra"], {"food": gained})
            ctx["log"].append(f"{player['name']}'s Baker's Oven bakes "
                              f"{n} grain for {gained} food")

compendium_card(
    "E14", cost={}, points=3,
    prereq=(lambda s, p: any(i in _OVENS for i in p["improvements"]),
            "an oven (Clay Oven or Stone Oven) to return"),
    hooks={"play": _e14_play}, resolve_choice=_e14_resolve,
    bake=(2, 5))


# ── E15 Baking Tray — UNIMPLEMENTED (see module dict) ────────────────


# ── E16 Building Material ───────────────────────────────────────────────
def _e16_play(state, player, inst, ctx):
    good = (ctx.get("params") or {}).get("good")
    if good not in ("wood", "clay"):
        raise ValueError("Building Material: choose params.good: "
                         "'wood' or 'clay'")
    add_goods(ctx["extra"], {good: 1})
    ctx["log"].append(f"{player['name']} takes 1 {good} (Building Material)")

compendium_card("E16", cost={}, traveling=True, hooks={"play": _e16_play})


# ── E17 Windmill ─────────────────────────────────────────────────────────
compendium_card("E17", cost={"wood": 3, "stone": 1}, points=2,
                raw_values={"grain": 2})


# ── E18 Bean Field ───────────────────────────────────────────────────────
compendium_card("E18", cost={}, points=1, prereq=needs_occupations(2),
                field={"crops": ("vegetable",)})


# ── E19 Gypsy's Crock — UNIMPLEMENTED (see module dict) ──────────────


# ── E20 Simple Fireplace ─────────────────────────────────────────────────
# Registers the cook/bake tables (identical mechanism to a Fireplace
# major). Skips the "counts as a Fireplace, so it can be upgraded to a
# Cooking Hearth (and is then removed)" clause: the Cooking-Hearth-upgrade
# path only checks membership in the MAJOR_IMPROVEMENTS-backed FIREPLACES
# list, which a card-granted ability can't join without engine changes.
# The card's core function (cook + bake) works standalone regardless.
compendium_card(
    "E20", cost={"clay": 1}, points=1,
    cook={"vegetable": 2, "sheep": 1, "boar": 2, "cattle": 3},
    bake=(None, 2))


# ── E21 Half-timbered House ──────────────────────────────────────────────
def _e21_score(state, player, inst):
    if player["house_type"] != "stone":
        return 0
    return sum(1 for c in player["cells"] if c["type"] == "room")

compendium_card("E21", cost={"wood": 1, "clay": 1, "reed": 1, "stone": 2},
                score_bonus=_e21_score)


# ── E22 Raft ─────────────────────────────────────────────────────────────
def _e22_space_used(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or ctx["space_id"] != "fishing":
        return
    prompt_choice(state, player, inst["id"],
                  "Raft: take 1 food or 1 reed?", ["1 food", "1 reed"])

def _e22_resolve(state, player, inst, ctx):
    good = "food" if ctx["index"] == 0 else "reed"
    add_goods(ctx["extra"], {good: 1})
    ctx["log"].append(f"{player['name']}'s Raft grants 1 {good}")

compendium_card("E22", cost={"wood": 2}, points=1,
                hooks={"space_used": _e22_space_used},
                resolve_choice=_e22_resolve)


# ── E23 Manger ───────────────────────────────────────────────────────────
def _e23_score(state, player, inst):
    covered = sum(len(pa) for pa in compute_pastures(player))
    return 4 if covered >= 9 else 3 if covered >= 8 else \
        2 if covered >= 7 else 1 if covered >= 6 else 0

compendium_card("E23", cost={"wood": 2}, score_bonus=_e23_score)


# ── E24 Animal Pen ───────────────────────────────────────────────────────
compendium_card("E24", cost={"wood": 2}, points=1,
                prereq=needs_occupations(4),
                hooks=schedule_on_play("food", rounds_ahead=TOTAL_ROUNDS,
                                      amount=2))


# ── E25 Spices — UNIMPLEMENTED (see module dict) ─────────────────────


# ── E26 Plane — UNIMPLEMENTED (see module dict) ──────────────────────


# ── E27 Wood-fired Oven ──────────────────────────────────────────────────
def _e27_play(state, player, inst, ctx):
    grain = player["resources"]["grain"]
    if grain >= 1:
        options = [f"Bake {n} grain for {n * 3} food"
                  for n in range(1, grain + 1)] + ["Skip baking now"]
        prompt_choice(state, player, inst["id"],
                      "Wood-fired Oven: bake bread immediately?", options)

def _e27_resolve(state, player, inst, ctx):
    opt = ctx["option"]
    if opt.startswith("Bake"):
        n = int(opt.split()[1])
        if player["resources"]["grain"] >= n:
            player["resources"]["grain"] -= n
            gained = n * 3
            add_goods(ctx["extra"], {"food": gained})
            ctx["log"].append(f"{player['name']}'s Wood-fired Oven bakes "
                              f"{n} grain for {gained} food")

compendium_card("E27", cost={"wood": 3, "stone": 1}, points=2,
                hooks={"play": _e27_play}, resolve_choice=_e27_resolve,
                bake=(None, 3))


# ── E28 Clogs ────────────────────────────────────────────────────────────
def _e28_score(state, player, inst):
    return 2 if player["house_type"] == "stone" else \
        1 if player["house_type"] == "clay" else 0

compendium_card("E28", cost={"wood": 1}, score_bonus=_e28_score)


# ── E29 Shepherd's Pipe — UNIMPLEMENTED (see module dict) ────────────


# ── E30 Canoe ────────────────────────────────────────────────────────────
compendium_card("E30", cost={"wood": 2}, points=1,
                prereq=needs_occupations(2),
                hooks=space_bonus(["fishing"], {"food": 1, "reed": 1}))


# ── E31 Carp Pond ────────────────────────────────────────────────────────
compendium_card(
    "E31", cost={}, points=1,
    prereq=combine((lambda s, p: len(p["improvements"]) >= 2,
                    "2 improvements"), needs_occupations(1)),
    hooks=schedule_on_play("food", fixed_rounds=(1, 3, 5, 7, 9, 11, 13)))


# ── E32 Potato Dibber ────────────────────────────────────────────────────
def _e32_sow(state, player, inst, ctx):
    hit = False
    for target, crop in ctx["sown"]:
        if crop != "vegetable":
            continue
        if isinstance(target, int):
            player["cells"][target]["crops"]["count"] += 1
        else:
            target["crops"]["count"] += 1
        hit = True
    if hit:
        ctx["log"].append(f"{player['name']}'s Potato Dibber adds 1 "
                          "vegetable to each newly sown vegetable field")

compendium_card("E32", cost={"wood": 1}, hooks={"sow": _e32_sow})


# ── E33 Ceramics ─────────────────────────────────────────────────────────
# Implements the +2 food on play. Skips "the Pottery is a minor
# improvement for you and costs nothing": the free-cost half needs a
# cost_mod that can single out Pottery specifically, which hits the same
# missing-improvement-id-in-ctx problem as Baking Tray (E15); the
# "counts as a minor" half is score-category manipulation (unsupported).
compendium_card(
    "E33", cost={"clay": 1},
    prereq=(_owns_oven,
            "an oven (Clay Oven, Stone Oven, Baker's Oven, or "
            "Wood-fired Oven)"),
    hooks={"play": on_play_gain({"food": 2})})


# ── E34 Basket ───────────────────────────────────────────────────────────
def _e34_space_used(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or ctx["space_id"] not in _WOOD_ACC_SPACES:
        return
    if ctx["goods"].get("wood", 0) < 2:
        return
    prompt_choice(state, player, inst["id"],
                  "Basket: leave 2 wood on the space for 3 food instead?",
                  ["Leave 2 wood for 3 food", "Keep the wood"],
                  data={"space_id": ctx["space_id"]})

def _e34_resolve(state, player, inst, ctx):
    if ctx["index"] != 0 or player["resources"]["wood"] < 2:
        return
    player["resources"]["wood"] -= 2
    sp = next((s for s in state["action_spaces"]
              if s["id"] == ctx["data"]["space_id"]), None)
    if sp is not None:
        sp["supply"]["wood"] = sp["supply"].get("wood", 0) + 2
    add_goods(ctx["extra"], {"food": 3})
    ctx["log"].append(f"{player['name']}'s Basket exchanges 2 wood for 3 food")

compendium_card("E34", cost={"reed": 1}, hooks={"space_used": _e34_space_used},
                resolve_choice=_e34_resolve)


# ── E35 Corn Scoop ───────────────────────────────────────────────────────
compendium_card("E35", cost={"wood": 1},
                hooks=space_bonus(["grain_seeds"], {"grain": 1}))


# ── E36 Clay Roof ───────────────────────────────────────────────────────
# "You can replace 1 or 2 reed with the same amount of clay whenever you
# extend or renovate your home." This is decks/GUIDE.md's own worked
# ctx["payment"] example verbatim (the card that motivated the
# payment-channel mechanism) -- a per-build-ACTION cap of 2, not scaled
# by ctx["count"]: the printed text is "whenever you [do the action]",
# and the DB ruling ("can be used for every room you build, if you build
# more than 1 room") reads as clarifying reusability across separate
# build actions, not a per-room-scaled cap within one batch.
def _clay_roof_mod(state, player, kind, cost, ctx):
    if kind not in ("room", "renovation"):
        return cost
    payment = ctx.get("payment")
    if not isinstance(payment, dict) or "reed_to_clay" not in payment:
        return cost  # not addressed to this card (another card's payment)
    n = payment["reed_to_clay"]
    if not isinstance(n, int) or n <= 0 or n > 2 or n > cost.get("reed", 0):
        raise ValueError("Clay Roof: invalid payment")
    cost = dict(cost)
    cost["reed"] -= n
    cost["clay"] = cost.get("clay", 0) + n
    return cost

compendium_card("E36", prereq=needs_occupations(1), cost_mod=_clay_roof_mod)


# ── E37 Clay Supports ────────────────────────────────────────────────────
# Sets a new base room cost for a CLAY hut (2 clay + 1 wood + 1 reed,
# instead of the default 5 clay + 2 reed); same shape as the Axe (E13).
def _e37_mod(state, player, kind, cost, ctx):
    if kind == "room" and player["house_type"] == "clay" and cost.get("clay"):
        cost = dict(cost)
        n = ctx.get("count", 1)
        cost["clay"] = 2 * n
        cost["wood"] = cost.get("wood", 0) + 1 * n
        cost["reed"] = 1 * n
    return cost

compendium_card("E37", cost={"wood": 2}, cost_mod=_e37_mod)


# ── E38 Madonna Statue ───────────────────────────────────────────────────
def _e38_play(state, player, inst, ctx):
    params = ctx.get("params") or {}
    discard = params.get("discard")
    if not isinstance(discard, list) or len(discard) != 2:
        raise ValueError("Madonna Statue: choose 2 improvements to "
                         "discard (params.discard)")
    for ref in discard:
        if ref in player["improvements"]:
            player["improvements"].remove(ref)
            state["available_improvements"].append(ref)
            state["available_improvements"].sort()
        elif any(i["id"] == ref for i in player["minors"]):
            player["minors"] = [i for i in player["minors"] if i["id"] != ref]
        else:
            raise ValueError(f"You do not have improvement {ref!r} in "
                             "front of you")
    ctx["log"].append(f"{player['name']} discards 2 improvements "
                      "(Madonna Statue)")

compendium_card(
    "E38", cost={}, points=2,
    prereq=(lambda s, p: len(p["improvements"]) + len(p["minors"]) >= 2,
            "2 improvements (major or minor) in front of you"),
    hooks={"play": _e38_play})


# ── E39 Market Stall ─────────────────────────────────────────────────────
compendium_card("E39", cost={"grain": 1}, traveling=True,
                hooks={"play": on_play_gain({"vegetable": 1})})


# ── E40 Mini Pasture ─────────────────────────────────────────────────────
def _e40_play(state, player, inst, ctx):
    params = ctx.get("params") or {}
    cell = params.get("cell")
    if not isinstance(cell, int) or not (0 <= cell < NUM_CELLS):
        raise ValueError("Mini Pasture: choose a farmyard space (params.cell)")
    c = player["cells"][cell]
    if c["type"] != "empty":
        raise ValueError("Mini Pasture: space must be empty")
    edges = cell_edges(cell)
    if any(e in player["fences"] for e in edges):
        raise ValueError("Mini Pasture: that space is already fenced")
    layout = sorted(set(player["fences"] + edges))
    ok, err, _pastures = validate_fence_layout(player, layout)
    if not ok:
        raise ValueError(f"Mini Pasture: {err}")
    player["fences"] = layout
    ctx["log"].append(f"{player['name']} fences 1 space for free (Mini Pasture)")
    sub_ctx = {"actor": player["index"], "new_pastures": [[cell]],
              "log": ctx["log"], "extra": {}}
    fire(state, "fences_built", sub_ctx)
    add_goods(ctx["extra"], sub_ctx["extra"])

compendium_card("E40", cost={"food": 2}, traveling=True,
                hooks={"play": _e40_play})


# ── E41 Millstone ────────────────────────────────────────────────────────
compendium_card("E41", cost={"stone": 1}, bake_bonus_flat=2)


# ── E42 Helpful Neighbours ───────────────────────────────────────────────
# Cost is a genuine either/or (1 wood OR 1 clay), which parse_cost/the
# declarative `cost` dict can't express; paid manually in the play hook
# instead (cost={}), like the "pay 2 food" branch of Moonshine (B003).
# Both choices are taken as play-time params (not prompt_choice): this
# card is traveling, so its instance never lands in a hand-resolvable
# player["minors"]/["occupations"] list for a later prompt to find.
def _e42_play(state, player, inst, ctx):
    params = ctx.get("params") or {}
    pay = params.get("pay")
    gain = params.get("gain")
    if pay not in ("wood", "clay") or player["resources"][pay] < 1:
        raise ValueError("Helpful Neighbours: choose a resource you have "
                         "to pay (params.pay: 'wood' or 'clay')")
    if gain not in ("stone", "reed"):
        raise ValueError("Helpful Neighbours: choose what to receive "
                         "(params.gain: 'stone' or 'reed')")
    player["resources"][pay] -= 1
    add_goods(ctx["extra"], {gain: 1})
    ctx["log"].append(f"{player['name']} pays 1 {pay} and receives 1 "
                      f"{gain} (Helpful Neighbours)")

compendium_card(
    "E42", cost={}, traveling=True,
    prereq=(lambda s, p: p["resources"]["wood"] >= 1
            or p["resources"]["clay"] >= 1, "1 wood or 1 clay"),
    hooks={"play": _e42_play})


# ── E43 Fruit Tree ───────────────────────────────────────────────────────
compendium_card(
    "E43", cost={}, points=1, prereq=needs_occupations(3),
    hooks=schedule_on_play("food", fixed_rounds=(8, 9, 10, 11, 12, 13, 14)))


# ── E44 Outhouse ─────────────────────────────────────────────────────────
compendium_card(
    "E44", cost={"wood": 1, "clay": 1}, points=2,
    prereq=(lambda s, p: any(op["index"] != p["index"]
                             and len(op["occupations"]) < 2
                             for op in s["players"]),
            "another player with fewer than 2 occupations"))


# ── E45 Private Forest ───────────────────────────────────────────────────
compendium_card(
    "E45", cost={"food": 2},
    hooks=schedule_on_play("wood", fixed_rounds=(2, 4, 6, 8, 10, 12, 14)))


# ── E46 Sack Cart ────────────────────────────────────────────────────────
compendium_card(
    "E46", cost={"wood": 2}, prereq=needs_occupations(2),
    hooks=schedule_on_play("grain", fixed_rounds=(5, 8, 11, 14)))


# ── E47 Lettuce Patch ────────────────────────────────────────────────────
# The field mechanism is shared with Bean Field/Herb Patch. The "4 food
# per harvested vegetable" clause is precise, not approximated: an
# "active" flag set at sow time (cleared once the card's crop stack is
# exhausted) tracks exactly whether THIS card yielded a vegetable at this
# harvest's field phase (that vegetable is already merged into the
# player's fungible resources by the time harvest_field fires, so the
# flag -- not the post-harvest resource total -- is what identifies it).
def _e47_sow(state, player, inst, ctx):
    for target, crop in ctx["sown"]:
        if target is inst and crop == "vegetable":
            inst["data"]["active"] = True

def _e47_harvest(state, player, inst, ctx):
    if not inst["data"].get("active"):
        return
    player["resources"]["vegetable"] -= 1
    player["resources"]["food"] += 4
    ctx["log"].append(f"{player['name']}'s Lettuce Patch converts 1 "
                      "vegetable to 4 food")
    if not inst.get("crops"):
        inst["data"]["active"] = False

compendium_card("E47", cost={}, points=1, prereq=needs_occupations(3),
                field={"crops": ("vegetable",)},
                hooks={"sow": _e47_sow, "harvest_field": _e47_harvest})


# ── E48 Reed Pond ────────────────────────────────────────────────────────
compendium_card("E48", cost={}, points=1, prereq=needs_occupations(3),
                hooks=schedule_on_play("reed", rounds_ahead=3))


# ── E49 Writing Desk ─────────────────────────────────────────────────────
def _play_second_occupation(state, player, cid, food_cost, log):
    played_spec = CARDS[cid]
    player["hand_occupations"].remove(cid)
    inst = new_instance(cid)
    player["occupations"].append(inst)
    player["occs_played"] += 1
    log.append(f"{player['name']} plays the occupation "
               f"\"{played_spec['name']}\" for {food_cost} food "
               "(Writing Desk)")
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

def _e49_space_used(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or \
            ctx["space_id"] not in ("lessons", "lessons_b"):
        return
    if player["resources"]["food"] < 2:
        return
    eligible = [cid for cid in player["hand_occupations"]
               if check_prereq(state, player, cid)]
    if not eligible:
        return
    options = [f"Play {CARDS[cid]['name']} for 2 food" for cid in eligible] \
        + ["Skip"]
    prompt_choice(state, player, inst["id"],
                  "Writing Desk: play a second occupation for 2 food?",
                  options, data={"cids": eligible})

def _e49_resolve(state, player, inst, ctx):
    cids = ctx["data"]["cids"]
    if ctx["index"] >= len(cids) or player["resources"]["food"] < 2:
        return
    cid = cids[ctx["index"]]
    if cid not in player["hand_occupations"]:
        return
    player["resources"]["food"] -= 2
    extra = _play_second_occupation(state, player, cid, 2, ctx["log"])
    add_goods(ctx["extra"], extra)

compendium_card("E49", cost={"wood": 1}, points=1,
                prereq=needs_occupations(2),
                hooks={"space_used": _e49_space_used},
                resolve_choice=_e49_resolve)


# ── E50 Builder's Trowel ─────────────────────────────────────────────────
# "Renovate out of turn" is offered as a card_action (available any time
# on the owner's turn or during feeding, no action-space placement
# needed), at the normal renovation cost -- only usable wood->clay, so
# gated to that house type in addition to the normal cost/possibility
# check sub_actions.can_renovate already does.
def _e50_available(state, player, inst):
    return player["house_type"] == "wood" and sub_actions.can_renovate(state, player)

def _e50_apply(state, player, inst, ctx):
    if player["house_type"] != "wood":
        raise ValueError("Builder's Trowel only renovates a wooden hut")
    sub_actions.renovate(state, player, ctx["log"])

compendium_card(
    "E50", cost={"wood": 1},
    card_action={"available": _e50_available, "apply": _e50_apply,
                "description": "Builder's Trowel: renovate your wooden "
                               "hut to a clay hut anytime"})


# ── E51 Spindle ──────────────────────────────────────────────────────────
def _e51_food(state, player):
    sheep = animal_totals_of(player)["sheep"]
    return 2 if sheep >= 5 else 1 if sheep >= 3 else 0

compendium_card("E51", cost={"wood": 1}, hooks=harvest_food(_e51_food))


# ── E52 Stable ───────────────────────────────────────────────────────────
def _e52_play(state, player, inst, ctx):
    params = ctx.get("params") or {}
    cell = params.get("cell")
    if not isinstance(cell, int) or not (0 <= cell < NUM_CELLS):
        raise ValueError("Stable: choose a farmyard space (params.cell)")
    c = player["cells"][cell]
    stables = sum(1 for x in player["cells"] if x["stable"])
    if stables >= MAX_STABLES or c["type"] != "empty" or c["stable"]:
        raise ValueError("Stable: invalid space")
    c["stable"] = True
    ctx["log"].append(f"{player['name']} builds a free stable (Stable)")
    sub_ctx = {"cells": [cell], "log": ctx["log"], "actor": player["index"],
              "extra": {}}
    fire_player(state, player, "stable_built", sub_ctx)
    add_goods(ctx["extra"], sub_ctx["extra"])

compendium_card("E52", cost={"wood": 1}, traveling=True,
                hooks={"play": _e52_play})


# ── E53 Butter Churn ─────────────────────────────────────────────────────
def _e53_food(state, player):
    totals = animal_totals_of(player)
    return totals["sheep"] // 3 + totals["cattle"] // 2

compendium_card("E53", cost={"wood": 2}, hooks=harvest_food(_e53_food))


# ── E54 Quarry ───────────────────────────────────────────────────────────
compendium_card("E54", cost={}, points=2, prereq=needs_occupations(4),
                hooks=space_bonus(["day_laborer"], {"stone": 3}))


# ── E55 Stone House Extension ────────────────────────────────────────────
def _e55_play(state, player, inst, ctx):
    if player["house_type"] != "stone":
        raise ValueError("Stone House Extension: you must live in a "
                         "stone house")
    params = ctx.get("params") or {}
    cell = params.get("cell")
    eligible = _room_eligible_cells(player)
    if not isinstance(cell, int) or cell not in eligible:
        raise ValueError("Stone House Extension: choose an eligible "
                         "space (params.cell)")
    player["cells"][cell]["type"] = "room"
    ctx["log"].append(f"{player['name']} extends their stone house by 1 "
                      "room (Stone House Extension)")
    sub_ctx = {"cells": [cell], "log": ctx["log"], "actor": player["index"],
              "extra": {}}
    fire_player(state, player, "rooms_built", sub_ctx)
    add_goods(ctx["extra"], sub_ctx["extra"])

compendium_card("E55", cost={"reed": 1, "stone": 3}, traveling=True,
                hooks={"play": _e55_play})


# ── E56 Stone Tongs ──────────────────────────────────────────────────────
compendium_card(
    "E56", cost={"wood": 1},
    hooks=space_bonus(["western_quarry", "eastern_quarry"], {"stone": 1}))


# ── E57 Dovecote ─────────────────────────────────────────────────────────
compendium_card(
    "E57", cost={"stone": 2}, points=2,
    hooks=schedule_on_play("food", fixed_rounds=(10, 11, 12, 13, 14)))


# ── E58 Animal Yard — UNIMPLEMENTED (see module dict) ────────────────


# ── E59 Drinking Trough ──────────────────────────────────────────────────
compendium_card("E59", cost={"wood": 2}, points=1,
                pasture_capacity_bonus=2)


# ── E60 Cattle Market ────────────────────────────────────────────────────
def _take_animal(player, animal_type, count=1):
    """Remove `count` animals of `animal_type` from cells/pets. Returns
    True if the full amount was removed."""
    remaining = count
    for c in player["cells"]:
        a = c.get("animal")
        if a and a["type"] == animal_type and remaining > 0:
            take = min(a["count"], remaining)
            a["count"] -= take
            remaining -= take
            if a["count"] <= 0:
                c["animal"] = None
    if remaining > 0 and player.get("pets", {}).get(animal_type):
        take = min(player["pets"][animal_type], remaining)
        player["pets"][animal_type] -= take
        if player["pets"][animal_type] <= 0:
            player["pets"].pop(animal_type, None)
        remaining -= take
    return remaining == 0

def _e60_play(state, player, inst, ctx):
    if not _take_animal(player, "sheep", 1):
        raise ValueError("Cattle Market: requires 1 sheep")
    add_goods(ctx["extra"], {"cattle": 1})
    ctx["log"].append(f"{player['name']} trades 1 sheep for 1 cattle "
                      "(Cattle Market)")

compendium_card(
    "E60", cost={}, traveling=True,
    prereq=(lambda s, p: animal_totals_of(p)["sheep"] >= 1, "1 sheep"),
    hooks={"play": _e60_play})


# ── E61/E62 Riding Plough / Turnwrest Plough ─────────────────────────────
# On using a plowing space, offer up to `max_uses` activations of "plow
# up to 2 additional fields" (the space's own action already plows 1,
# for 3 total); mirrors Forest Plow/Grassland Harrow's space_used-prompt
# pattern in deck_b_minors.py.
def _make_plough(max_uses):
    def space_used(state, player, inst, ctx):
        if ctx["actor"] != player["index"] or \
                ctx["space_id"] not in ("farmland", "cultivation"):
            return
        if inst["data"].get("uses_left", max_uses) <= 0:
            return
        cells = plowable_cells(player)
        if not cells:
            return
        options = [f"Plow cell {c}" for c in cells] + ["Skip"]
        prompt_choice(state, player, inst["id"],
                      "Plow an additional field?", options,
                      data={"remaining": 2})

    def resolve(state, player, inst, ctx):
        cells = plowable_cells(player)
        remaining = ctx["data"]["remaining"]
        idx = ctx["index"]
        if idx < len(cells):
            cell = cells[idx]
            player["cells"][cell]["type"] = "field"
            ctx["log"].append(f"{player['name']} plows an additional field")
            remaining -= 1
            cells2 = plowable_cells(player)
            if remaining > 0 and cells2:
                prompt_choice(state, player, inst["id"],
                              "Plow another field?",
                              [f"Plow cell {c}" for c in cells2] + ["Stop"],
                              data={"remaining": remaining})
                return
        inst["data"]["uses_left"] = inst["data"].get("uses_left", max_uses) - 1
    return space_used, resolve

_e61_space_used, _e61_resolve = _make_plough(2)
_e62_space_used, _e62_resolve = _make_plough(1)

compendium_card("E61", cost={"wood": 4}, prereq=needs_occupations(3),
                hooks={"space_used": _e61_space_used},
                resolve_choice=_e61_resolve)

compendium_card("E62", cost={"wood": 3}, prereq=needs_occupations(2),
                hooks={"space_used": _e62_space_used},
                resolve_choice=_e62_resolve)


# ── E338 Feed Pellets — UNIMPLEMENTED (see module dict) ──────────────
