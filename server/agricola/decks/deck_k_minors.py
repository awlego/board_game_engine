"""Deck K minor improvements (codes K105-K146, K339 from the compendium
DB). Deck K is an ORIGINAL-edition ("Komplex") deck; original-board
action-space names are mapped onto this engine's equivalents per
CARDS.md/GUIDE.md: "Plough 1 Field" -> "farmland", "Plough Field and
Sow" -> "cultivation" (the engine's plow-only and plow-and/or-sow
spaces, respectively).

No text-bleed artifacts (the "(N-M players)" mid-string / contradicting
clause pattern documented in deck_b_occupations.py) were found in this
slice; no _TEXT_FIXES is needed. K110's compendium *parse* is still off
in a different way -- its cost string ("2 grain 2S") didn't match the
parser's letter-code grammar and landed in the prereq field instead of
the cost field -- handled with a manual cost= override at that card.
"""

from server.agricola.cards import (
    compendium_card, CARDS, spec, prompt_choice,
    harvest_food, on_play_gain, schedule_on_play, space_bonus,
    animal_totals_of, needs_occupations, combine,
    card_fields, fire_player, draw_minors, discard_hand_minors,
)
from server.agricola import sub_actions
from server.agricola.state import (
    ANIMAL_TYPES, NUM_CELLS, TOTAL_ROUNDS,
    MAJOR_IMPROVEMENTS, FIREPLACES,
    compute_pastures, plowable_cells, orthogonal_neighbors,
)

UNIMPLEMENTED = {
    "K109": "requires detecting an animal->food conversion during the "
            "feeding phase specifically. Neither _apply_feed's "
            "conversion loop nor _apply_accommodate's cook loop fires "
            "any card hook (same gap noted for C053 in deck_c_minors.py) "
            "-- there is no event to react to.",
    "K111": "requires triggering a full bake-bread sub-action outside "
            "the spaces/moments the engine currently allows one "
            "(bake_on_spaces only wires into the farmland/cultivation "
            "space handlers, and grain_utilization/bake_on_build cover "
            "the rest); 'whenever you play an occupation' has no such "
            "hook, and cards cannot invoke the engine's private "
            "_do_bake from a deck module.",
    "K112": "requires granting food to the player BEFORE the "
            "occupation's food cost is deducted, so it can enable "
            "playing an occupation otherwise unaffordable. "
            "_play_occupation calls self._pay(...) before any hook "
            "fires (play/occupation_played both fire after payment), "
            "so there is no hook point earlier than the deduction.",
    "K124": "the bonus scales with how many 'plough' cards (of any "
            "type, played by ANY player, including cards from other "
            "compendium decks such as Harrow I68) are in play. No "
            "cross-deck registry of which minor/occupation instances "
            "count as a 'plough' exists or is exposed to a deck module "
            "-- hardcoding only this module's own plough cards (Swing "
            "Plough/Crooked Plough) would silently undercount.",
    "K138": "places an extra, non-scoring family member token that can "
            "act and must be fed but isn't part of the family -- the "
            "guest-token/extra-people mechanic the guide marks "
            "unsupported.",
    "K139": "lets the player use a family-growth action space another "
            "player has already placed on this round -- placing on an "
            "occupied space is the guide's unsupported mechanic.",
    "K339": "requires detecting each individual animal->food conversion "
            "during feeding (same unfired-hook gap as K109/C053) AND a "
            "novel per-room food-storage mechanic (max 1 food token per "
            "room, scored as bonus points) that nothing in the engine "
            "tracks.",
}


# ── Shared helpers ────────────────────────────────────────────────────

def _needs_total_improvements(n):
    """'n improvements' prereq text in the compendium counts majors,
    minors, and (per the Braggart ruling this mirrors) only what's
    currently in front of the player."""
    return (lambda s, p: len(p["improvements"]) + len(p["minors"]) >= n,
            f"{n} improvements")


def _needs_vegetable_field(n=1):
    def ok(s, p):
        fields = sum(1 for c in p["cells"]
                     if c["crops"] and c["crops"]["type"] == "vegetable")
        fields += sum(1 for i in card_fields(p)
                     if i["crops"] and i["crops"]["type"] == "vegetable")
        return fields >= n
    return (ok, f"{n} vegetable field(s)")


def _needs_animals(n):
    return (lambda s, p: sum(animal_totals_of(p).values()) >= n,
            f"{n} animals")


def _room_eligible_cells(player):
    """Empty, non-stable, non-pasture cells orthogonally adjacent to an
    existing room (mirrors engine._buildable_room_cells / deck_b's
    helper of the same name)."""
    rooms = {i for i, c in enumerate(player["cells"]) if c["type"] == "room"}
    pasture_cells = {i for pa in compute_pastures(player) for i in pa}
    out = []
    for i, c in enumerate(player["cells"]):
        if i in rooms or c["type"] != "empty" or c["stable"] or i in pasture_cells:
            continue
        if any(nb in rooms for nb in orthogonal_neighbors(i)):
            out.append(i)
    return out


_OVENS = ("clay_oven", "stone_oven")


# ── K105 Acreage ──────────────────────────────────────────────────────
# "When you sow, you can plant grain on this card. There can be up to 2
# stacks of grain on this card, as shown." Req 1 occ. No cost. The
# field={"crops": ..., "stacks": 2} architecture (engine phase 13) is
# the entire implementation -- 2 independent grain stacks, sown and
# harvested separately (see decks/GUIDE.md's "Field stacks" section).
# "(Does not count as a field when scoring)" is free, same as every
# card field. Two rulings are NOT modeled: "the Acreage counts as 2
# fields towards prerequisites of minor improvements" (this engine's
# various local "n fields" prereq helpers across deck_*.py modules only
# ever count farmyard cell tiles, not card fields at all -- a pre-
# existing limitation, not something specific to this card, and fixing
# it would mean touching every other deck's own prereq helper) and "the
# Acreage is not considered adjacent to any farmyard space" (moot --
# nothing about this card triggers an adjacency query).
compendium_card(
    "K105", prereq=needs_occupations(1),
    field={"crops": ("grain",), "stacks": 2},
)


# ── K106 Bakehouse ────────────────────────────────────────────────────
# "Whenever you use a bread baking action, you can use the Bakehouse to
# convert up to 2 grain into 5 food each. When you play this card, you
# can also bake bread immediately." Cost 3S parses cleanly; "Return 1
# oven" is a played-card prereq/cost, not auto-applied from the DB text.
def _bakehouse_play(state, player, inst, ctx):
    owned = next((o for o in _OVENS if o in player["improvements"]), None)
    if owned:
        player["improvements"].remove(owned)
        state["available_improvements"].append(owned)
        state["available_improvements"].sort()
        ctx["log"].append(f"{player['name']} returns their "
                          f"{MAJOR_IMPROVEMENTS[owned]['name']} (Bakehouse)")
    opts = [n for n in (1, 2) if player["resources"]["grain"] >= n]
    if opts:
        options = [f"Bake {n} grain for {n * 5} food" for n in opts] + ["Skip"]
        prompt_choice(state, player, inst["id"],
                     "Bakehouse: bake bread immediately?", options,
                     data={"opts": opts})

def _bakehouse_resolve(state, player, inst, ctx):
    opts = ctx["data"]["opts"]
    if ctx["index"] >= len(opts):
        return
    n = opts[ctx["index"]]
    if player["resources"]["grain"] >= n:
        player["resources"]["grain"] -= n
        player["resources"]["food"] += n * 5
        ctx["log"].append(f"{player['name']}'s Bakehouse bakes {n} grain "
                          f"for {n * 5} food")

compendium_card(
    "K106",
    prereq=(lambda s, p: any(o in p["improvements"] for o in _OVENS),
            "return 1 oven"),
    hooks={"play": _bakehouse_play},
    resolve_choice=_bakehouse_resolve,
    bake=(2, 5),
)


# ── K107 Lumber ───────────────────────────────────────────────────────
compendium_card("K107", hooks={"play": on_play_gain({"wood": 3})})


# ── K108 Beehive ──────────────────────────────────────────────────────
compendium_card(
    "K108", points=1,
    prereq=combine(_needs_total_improvements(2), needs_occupations(3)),
    hooks=schedule_on_play("food", fixed_rounds=(2, 4, 6, 8, 10, 12, 14),
                          amount=2),
)


# ── K110 Brewery ──────────────────────────────────────────────────────
# The DB parser choked on "2 grain 2S" (only letter-coded tokens like
# "2G 2S" parse) and dumped the whole clause into the prereq field
# instead of cost; hand-writing the cost here per Ground Rule 1.
def _brewery_score(state, player, inst):
    return 1 if player["resources"]["grain"] >= 9 else 0

compendium_card(
    "K110", cost={"grain": 2, "stone": 2}, points=2, prereq=None,
    conversions=[{"give": {"grain": 1}, "get": {"food": 3},
                 "per_harvest": 1}],
    score_bonus=_brewery_score,
)


# ── K113 Flail ────────────────────────────────────────────────────────
compendium_card("K113", prereq=needs_occupations(1),
                bake_on_spaces=("farmland", "cultivation"))


# ── K114 Duck Pond ────────────────────────────────────────────────────
compendium_card("K114", points=1, prereq=needs_occupations(2),
                hooks=schedule_on_play("food", rounds_ahead=3))


# ── K115 Swing Plough / K119 Crooked Plough ──────────────────────────
# "Twice"/"once during the game, when you use 'Plough 1 Field' [this
# engine's 'farmland'], you can plough 3 fields instead of 1." The
# space's own plow already ran by the time space_used fires (see
# engine._resolve_space: sid=='farmland' plows, THEN falls through to
# _fire_space_used), so this offers up to 2 EXTRA fields per activation,
# chained over up to 2 prompts; declining the very first prompt costs no
# use (only plowing at least 1 extra field consumes an activation).
def _extra_plow_play(uses):
    def hook(state, player, inst, ctx):
        inst["data"]["uses_left"] = uses
    return hook

def _extra_plow_space_used(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or ctx["space_id"] != "farmland":
        return
    if inst["data"].get("uses_left", 0) <= 0:
        return
    cells = plowable_cells(player)
    if not cells:
        return
    options = [f"Plow field at cell {c}" for c in cells] + \
             ["Skip (save this use)"]
    prompt_choice(state, player, inst["id"],
                 f"{spec(inst)['name']}: plow an extra field?", options,
                 data={"cells": cells, "extra_count": 0})

def _extra_plow_resolve(state, player, inst, ctx):
    data = ctx["data"]
    cells = data["cells"]
    if ctx["index"] >= len(cells):
        if data["extra_count"] > 0:
            inst["data"]["uses_left"] -= 1
        return
    cell = cells[ctx["index"]]
    if cell not in plowable_cells(player):
        return
    player["cells"][cell]["type"] = "field"
    ctx["log"].append(f"{player['name']}'s {spec(inst)['name']} plows an "
                      "extra field")
    extra_count = data["extra_count"] + 1
    if extra_count >= 2:
        inst["data"]["uses_left"] -= 1
        return
    cells2 = plowable_cells(player)
    if not cells2:
        inst["data"]["uses_left"] -= 1
        return
    options = [f"Plow field at cell {c}" for c in cells2] + ["Stop"]
    prompt_choice(state, player, inst["id"],
                 f"{spec(inst)['name']}: plow a second extra field?",
                 options, data={"cells": cells2, "extra_count": extra_count})

compendium_card(
    "K115", prereq=needs_occupations(3),
    hooks={"play": _extra_plow_play(2), "space_used": _extra_plow_space_used},
    resolve_choice=_extra_plow_resolve,
)

compendium_card(
    "K119", prereq=needs_occupations(1),
    hooks={"play": _extra_plow_play(1), "space_used": _extra_plow_space_used},
    resolve_choice=_extra_plow_resolve,
)


# ── K116 Granary ──────────────────────────────────────────────────────
# Cost "3W or 3C" doesn't parse (alternative payments aren't representable
# by a single cost dict, same judgment call as A004 in deck_a_minors.py);
# the first option (wood) is used. The effect itself doesn't depend on
# which resource paid the cost.
compendium_card(
    "K116", cost={"wood": 3}, points=1,
    hooks=schedule_on_play("grain", fixed_rounds=(8, 10, 12)),
)


# ── K117 Greenhouse ───────────────────────────────────────────────────
# Round-start hooks may not prompt (GUIDE.md), so the "you can pay 1
# food to take the vegetable" choice auto-applies: buy it whenever
# affordable (a sane default), otherwise it returns to the supply.
def _greenhouse_play(state, player, inst, ctx):
    rnd = state["round"]
    targets = [r for r in (rnd + 4, rnd + 7) if r <= TOTAL_ROUNDS]
    inst["data"]["veg_rounds"] = targets
    if targets:
        ctx["log"].append(f"{player['name']}'s Greenhouse schedules "
                          "vegetables for rounds "
                          + ", ".join(map(str, targets)))

def _greenhouse_round_start(state, player, inst, ctx):
    rounds = inst["data"].get("veg_rounds") or []
    if state["round"] not in rounds:
        return
    rounds.remove(state["round"])
    if player["resources"]["food"] >= 1:
        player["resources"]["food"] -= 1
        player["resources"]["vegetable"] += 1
        ctx["log"].append(f"{player['name']}'s Greenhouse buys 1 vegetable "
                          "for 1 food")
    else:
        ctx["log"].append(f"{player['name']}'s Greenhouse vegetable "
                          "returns to the supply (no food to pay)")

compendium_card(
    "K117", points=1, prereq=needs_occupations(1),
    hooks={"play": _greenhouse_play, "round_start": _greenhouse_round_start},
)


# ── K118 Liquid Manure ────────────────────────────────────────────────
def _liquid_manure_sow(state, player, inst, ctx):
    added = 0
    for target, crop in ctx["sown"]:
        crops = player["cells"][target]["crops"] if isinstance(target, int) \
            else target["crops"]
        if crops:
            crops["count"] += 1
            added += 1
    if added:
        ctx["log"].append(f"{player['name']}'s Liquid Manure adds "
                          f"{added} extra crop(s)")

compendium_card("K118", prereq=_needs_animals(4),
                hooks={"sow": _liquid_manure_sow})


# ── K120 House Goat ───────────────────────────────────────────────────
# house_capacity=-1 models "no other animal in your home" as a reduction
# of the base 1-pet house capacity to 0. The ruling that this disables
# the Animal Tamer's house_capacity="per_room" entirely isn't modeled
# (that would need a priority/override rule between two static keys);
# the primary effect -- 1 food/harvest, no room for other pets -- is
# faithful.
compendium_card("K120", points=1,
                hooks=harvest_food(lambda s, p: 1),
                house_capacity=-1)


# ── K121 Sawhorse ─────────────────────────────────────────────────────
# "The next stable... costs nothing" is now a genuine kind="stable"
# cost_mod (stables are routed through modified_cost like every other
# build): it zeroes the cost of whichever stable is priced first
# (ctx["index"] == ctx["start_index"] + 1) as long as this card hasn't
# granted its one-time freebie yet. Repeated previews (stable_possible)
# before any real build just recompute the same zero cost harmlessly --
# the `stable_built` hook (which only fires after a real build) is what
# permanently marks the freebie spent, mirroring FR080 Fencing Master's
# "commit on the real event" pattern. The fence clause is also a
# cost_mod, unchanged.
_SAWHORSE_FREE_FENCES = (3, 6, 9, 12, 15)

def _sawhorse_cost_mod(state, player, kind, cost, ctx):
    if kind == "fences" and cost.get("wood"):
        existing = len(player["fences"])
        count = ctx.get("count", 0)
        free = sum(1 for i in range(1, count + 1)
                  if (existing + i) in _SAWHORSE_FREE_FENCES)
        if free:
            cost = dict(cost)
            cost["wood"] = max(0, cost["wood"] - free)
        return cost
    if kind == "stable":
        inst = next((i for i in player["minors"] if i["id"] == "K121"), None)
        if inst is None or inst["data"].get("stable_used"):
            return cost
        if ctx.get("index") != ctx.get("start_index", 0) + 1:
            return cost
        # Recorded, not consumed yet -- same "the last cost_mod call
        # before payment is the real one" safety argument as FR080
        # Fencing Master: previews recompute (and overwrite) this same
        # pending flag harmlessly; only stable_built (which follows a
        # real payment) commits it.
        inst["data"]["_pending_stable"] = True
        cost = dict(cost)
        cost["wood"] = 0
        return cost
    return cost

def _sawhorse_stable_built(state, player, inst, ctx):
    if inst["data"].pop("_pending_stable", False):
        inst["data"]["stable_used"] = True
        ctx["log"].append(f"{player['name']}'s Sawhorse stable is free")

compendium_card("K121", cost={"wood": 2},
                hooks={"stable_built": _sawhorse_stable_built},
                cost_mod=_sawhorse_cost_mod)


# ── K122 Sawmill ──────────────────────────────────────────────────────
def _sawmill_play(state, player, inst, ctx):
    player["improvements"].remove("joinery")
    state["available_improvements"].append("joinery")
    state["available_improvements"].sort()
    ctx["log"].append(f"{player['name']} returns their Joinery (Sawmill)")

def _sawmill_score(state, player, inst):
    wood = player["resources"]["wood"]
    return 3 if wood >= 5 else 2 if wood >= 4 else 1 if wood >= 2 else 0

compendium_card(
    "K122", points=3,
    prereq=(lambda s, p: "joinery" in p["improvements"], "return Joinery"),
    hooks={"play": _sawmill_play},
    conversions=[{"give": {"wood": 1}, "get": {"food": 3},
                 "per_harvest": 1}],
    score_bonus=_sawmill_score,
)


# ── K123 Wooden Strongbox ─────────────────────────────────────────────
def _strongbox_score(state, player, inst):
    rooms = sum(1 for c in player["cells"] if c["type"] == "room")
    return 4 if rooms >= 6 else 2 if rooms >= 5 else 0

compendium_card("K123", score_bonus=_strongbox_score)


# ── K125 Broom ─────────────────────────────────────────────────────────
# Cost 1W. "Discard all the remaining minor improvements in your hand,
# and draw 7 new minor improvements. You can play 1 more minor
# improvement immediately." The motivating example for the draw/discard
# piles (engine phase 12) -- see decks/GUIDE.md's "Draw and discard
# piles (K125 Broom)" section for the discard/redraw recipe. Broom's own
# play hook runs after sub_actions.play_minor has already removed Broom
# from hand_minors and before it joins player["minors"], so
# discard_hand_minors only ever sees the REST of the hand (no special-
# casing needed). The follow-up "play 1 more minor immediately" clause
# is expressed via the play hook's own params channel (open-ended target
# -- which of the 7 freshly drawn cards -- same shape as every other
# on-play bonus-play card): optional, priced at that card's own normal
# cost (the ruling: "you must pay the costs of the new improvement").


def _broom_play(state, player, inst, ctx):
    discard_hand_minors(state, player, ctx["log"])
    draw_minors(state, player, 7, ctx["log"])
    extra_cid = (ctx.get("params") or {}).get("card2")
    if extra_cid is not None:
        if extra_cid not in player["hand_minors"]:
            raise ValueError("Broom: choose a minor from your fresh hand "
                             "to play immediately (params.card2)")
        sub_actions.play_minor(state, player, extra_cid, ctx["log"])


compendium_card("K125", hooks={"play": _broom_play})


# ── K126 Landing Net ──────────────────────────────────────────────────
def _landing_net_hook(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    goods = ctx["goods"]
    if not goods.get("reed"):
        return
    other_building = any(goods.get(g) for g in ("wood", "clay", "stone"))
    bonus = 1 if other_building else 2
    ctx["extra"]["food"] = ctx["extra"].get("food", 0) + bonus
    ctx["log"].append(f"{player['name']}'s Landing Net grants {bonus} food")

compendium_card("K126", hooks={"space_used": _landing_net_hook})


# ── K127 Clapper ──────────────────────────────────────────────────────
_FAMILY_GROWTH_SPACES = ("basic_wish", "urgent_wish")

def _clapper_bonus(player):
    added = 0
    for c in player["cells"]:
        if c["crops"] and c["crops"]["type"] == "grain" and c["crops"]["count"] >= 1:
            c["crops"]["count"] += 1
            added += 1
    for inst in card_fields(player):
        if inst["crops"] and inst["crops"]["type"] == "grain" \
                and inst["crops"]["count"] >= 1:
            inst["crops"]["count"] += 1
            added += 1
    return added

def _clapper_family_growth(state, player, inst, ctx):
    n = _clapper_bonus(player)
    if n:
        ctx["log"].append(f"{player['name']}'s Clapper adds 1 grain to "
                          f"{n} field(s)")

def _clapper_play(state, player, inst, ctx):
    used_this_round = any(
        sp["id"] in _FAMILY_GROWTH_SPACES and sp["occupied_by"] == player["index"]
        for sp in state["action_spaces"])
    if used_this_round:
        n = _clapper_bonus(player)
        if n:
            ctx["log"].append(f"{player['name']}'s Clapper (catch-up) adds "
                              f"1 grain to {n} field(s)")

compendium_card("K127", hooks={"family_growth": _clapper_family_growth,
                               "play": _clapper_play})


# ── K128 Cooking Hearth ───────────────────────────────────────────────
def _cooking_hearth_play(state, player, inst, ctx):
    owned = next((f for f in FIREPLACES if f in player["improvements"]), None)
    if owned:
        player["improvements"].remove(owned)
        state["available_improvements"].append(owned)
        state["available_improvements"].sort()
        ctx["log"].append(f"{player['name']} returns their Fireplace "
                          "(Cooking Hearth)")

compendium_card(
    "K128", points=1,
    prereq=(lambda s, p: any(f in p["improvements"] for f in FIREPLACES),
            "return a Fireplace"),
    hooks={"play": _cooking_hearth_play},
    cook={"vegetable": 3, "sheep": 2, "boar": 3, "cattle": 4},
    bake=(None, 3),
)


# ── K129 Corn Sheaf ───────────────────────────────────────────────────
compendium_card("K129", hooks={"play": on_play_gain({"grain": 1})})


# ── K130 Herb Garden ──────────────────────────────────────────────────
compendium_card("K130", points=1, prereq=_needs_vegetable_field(1),
                hooks=schedule_on_play("food", rounds_ahead=5))


# ── K131 Clay Pit ─────────────────────────────────────────────────────
compendium_card("K131", points=1, prereq=needs_occupations(3),
                hooks=space_bonus(["day_laborer"], {"clay": 3}))


# ── K132 Clay Hut Extension ───────────────────────────────────────────
def _clay_hut_extension_play(state, player, inst, ctx):
    cells = _room_eligible_cells(player)
    if not cells:
        ctx["log"].append(f"{player['name']}'s Clay Hut Extension has no "
                          "space to build")
        return
    cell = cells[0]
    player["cells"][cell]["type"] = "room"
    ctx["log"].append(f"{player['name']}'s Clay Hut Extension builds a "
                      "free room")
    fire_player(state, player, "rooms_built",
               {"cells": [cell], "log": ctx["log"],
                "actor": player["index"], "extra": {}})

compendium_card("K132", traveling=True,
                hooks={"play": _clay_hut_extension_play})


# ── K133 Milking Stool ────────────────────────────────────────────────
def _milking_stool_food(state, player):
    n = animal_totals_of(player)["cattle"]
    return 3 if n >= 5 else 2 if n >= 3 else 1 if n >= 1 else 0

compendium_card(
    "K133", prereq=needs_occupations(2),
    hooks=harvest_food(_milking_stool_food),
    score_bonus=lambda s, p, i: animal_totals_of(p)["cattle"] // 2,
)


# ── K134 Ox Team ──────────────────────────────────────────────────────
def _ox_team_play(state, player, inst, ctx):
    limit = min(TOTAL_ROUNDS - state["round"], 3)
    cells = (ctx.get("params") or {}).get("cells") or []
    if not isinstance(cells, list) or len(cells) > limit:
        raise ValueError(f"Ox Team: choose up to {limit} cells to plow "
                        "(params.cells)")
    seen = set()
    for cell in cells:
        if not isinstance(cell, int) or cell in seen:
            raise ValueError("Ox Team: invalid or duplicate cell")
        if cell not in plowable_cells(player):
            raise ValueError("Ox Team: that cell isn't plowable")
        seen.add(cell)
        player["cells"][cell]["type"] = "field"
    if cells:
        ctx["log"].append(f"{player['name']}'s Ox Team plows {len(cells)} "
                          "field(s)")

compendium_card(
    "K134", points=1,
    prereq=(lambda s, p: animal_totals_of(p)["cattle"] >= 2, "2 cattle"),
    hooks={"play": _ox_team_play},
)


# ── K135 Horse ────────────────────────────────────────────────────────
def _horse_score(state, player, inst):
    return 2 if any(animal_totals_of(player)[t] == 0 for t in ANIMAL_TYPES) \
        else 0

compendium_card("K135", score_bonus=_horse_score)


# ── K136 Brushwood Roof ───────────────────────────────────────────────
# "You can replace 1 or 2 reed with wood" is a player choice cost_mod
# can't represent directly (no choice mechanism at cost-fold time); it
# auto-applies only when it actually helps -- substituting only the
# portion of the reed cost the player couldn't otherwise pay -- which
# reproduces the card's practical purpose (never spends wood the player
# didn't need to).
def _brushwood_roof_mod(state, player, kind, cost, ctx):
    if kind not in ("room", "renovation"):
        return cost
    reed_cost = cost.get("reed", 0)
    if reed_cost <= 0:
        return cost
    # Room cost is now a batch total: the "1 or 2 reed" cap applies once
    # per room, so an N-room batch allows up to 2*N reed->wood.
    cap = 2 * ctx.get("count", 1) if kind == "room" else 2
    shortfall = max(0, reed_cost - player["resources"]["reed"])
    swap = min(cap, reed_cost, shortfall)
    if swap <= 0:
        return cost
    cost = dict(cost)
    cost["reed"] -= swap
    cost["wood"] = cost.get("wood", 0) + swap
    return cost

compendium_card("K136", prereq=needs_occupations(2),
                cost_mod=_brushwood_roof_mod)


# ── K137 Turnip Field ─────────────────────────────────────────────────
def _turnip_field_play(state, player, inst, ctx):
    items = (ctx.get("params") or {}).get("sow") or []
    if not isinstance(items, list):
        raise ValueError("Turnip Field: params.sow must be a list")
    sown = []
    seen_cells, seen_cards = set(), set()
    for item in items:
        crop = item.get("crop")
        if crop not in ("grain", "vegetable"):
            raise ValueError("Sow grain or vegetables")
        if player["resources"].get(crop, 0) < 1:
            raise ValueError(f"Not enough {crop}")
        if "card" in item:
            cid = item["card"]
            target = inst if cid == inst["id"] else \
                next((i for i in card_fields(player) if i["id"] == cid), None)
            if target is None or cid in seen_cards:
                raise ValueError("Turnip Field: invalid card field")
            allowed = CARDS[cid]["field"]["crops"]
            if crop not in allowed:
                raise ValueError("Turnip Field: that card field can't grow "
                                "that crop")
            if target["crops"]:
                raise ValueError("Turnip Field: that card field is already "
                                "planted")
            seen_cards.add(cid)
            target["crops"] = {"type": crop, "count": 3 if crop == "grain" else 2}
            sown.append((target, crop))
        else:
            cell = item.get("cell")
            if not isinstance(cell, int) or not (0 <= cell < NUM_CELLS) \
                    or cell in seen_cells:
                raise ValueError("Turnip Field: invalid field")
            c = player["cells"][cell]
            if c["type"] != "field" or c["crops"]:
                raise ValueError("Turnip Field: you can only sow empty fields")
            seen_cells.add(cell)
            c["crops"] = {"type": crop, "count": 3 if crop == "grain" else 2}
            sown.append((cell, crop))
        player["resources"][crop] -= 1
    if sown:
        ctx["log"].append(f"{player['name']}'s Turnip Field triggers an "
                          "immediate sowing action")
        fire_player(state, player, "sow",
                   {"sown": sown, "log": ctx["log"],
                    "actor": player["index"], "extra": {}})

compendium_card(
    "K137", points=1, prereq=needs_occupations(3),
    field={"crops": ("vegetable",)},
    hooks={"play": _turnip_field_play},
)


# ── K140 Swan Lake ────────────────────────────────────────────────────
compendium_card("K140", points=2, prereq=needs_occupations(4),
                hooks=schedule_on_play("food", rounds_ahead=5))


# ── K141 Boar Breeding ────────────────────────────────────────────────
# Not on_play_gain: that factory writes straight into player["resources"],
# but animals must flow through ctx["extra"] so the engine routes them
# through the accommodation prompt (see minor_harvest_totem in cards.py
# for the same pattern).
def _boar_breeding_play(state, player, inst, ctx):
    ctx["extra"]["boar"] = ctx["extra"].get("boar", 0) + 1
    ctx["log"].append(f"{player['name']} gets 1 wild boar")

compendium_card("K141", hooks={"play": _boar_breeding_play})


# ── K142 Stone Cart ───────────────────────────────────────────────────
compendium_card(
    "K142", prereq=needs_occupations(2),
    hooks=schedule_on_play("stone", fixed_rounds=(2, 4, 6, 8, 10, 12, 14)),
)


# ── K143 Stone Exchange ───────────────────────────────────────────────
# Cost "2W or 2C" doesn't parse (alternative payments; see K116's note);
# the first option (wood) is used -- the effect doesn't depend on which
# resource paid the cost.
compendium_card("K143", cost={"wood": 2},
                hooks={"play": on_play_gain({"stone": 2})})


# ── K144 Mansion ──────────────────────────────────────────────────────
def _mansion_score(state, player, inst):
    if player["house_type"] != "stone":
        return 0
    rooms = sum(1 for c in player["cells"] if c["type"] == "room")
    return rooms * 2

compendium_card("K144", score_bonus=_mansion_score)


# ── K145 Forest Pasture ──────────────────────────────────────────────
# "This card can hold an unlimited number of wild boar." Req 3 occ.
def _forest_pasture_holds(state, player, inst):
    return {"types": {"boar": None}}

compendium_card("K145", points=1, prereq=needs_occupations(3),
                holds_animals=_forest_pasture_holds)


# ── K146 Loom ─────────────────────────────────────────────────────────
# Same shape as the base pool's minor_loom (cards.py), registered
# independently under its own compendium code.
def _k146_loom_food(state, player):
    sheep = animal_totals_of(player)["sheep"]
    return 3 if sheep >= 7 else 2 if sheep >= 4 else 1 if sheep >= 1 else 0

compendium_card(
    "K146", points=1, prereq=needs_occupations(2),
    hooks=harvest_food(_k146_loom_food),
    score_bonus=lambda s, p, i: animal_totals_of(p)["sheep"] // 3,
)
