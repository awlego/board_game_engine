"""Deck FR occupations (codes FR061-FR120 from the compendium DB).

These are original-edition cards (per-card "edition": "Original"); terms
are mapped per GUIDE.md's rule 4 ("wooden hut" -> wood house, etc.).

Data-quality check: this slice was scanned for the deck_b-style parser
artifact (an embedded "(N-M players)" tag mid-string introducing bleed
from an adjacent card) and none was found -- every card's `cost`, `vp`,
and `prereq` fields are empty/zero with no internal contradiction. One
card (FR077 Dove Hunter) does show a different kind of corruption: its
whole DB text describes "the Chandler" (an unrelated, differently-named
occupation) rather than a Dove-Hunter-flavored effect, with no clean
leading clause recoverable as this card's own text. Unlike the deck_b
cases there is no trailing-bleed boundary to cut at, so it is left
UNIMPLEMENTED rather than guess-patched; no _TEXT_FIXES dict is needed
for this module.
"""

from server.agricola.cards import (
    compendium_card, add_goods, goods_str, prompt_choice,
)
import server.agricola.cards as cards
from server.agricola.state import (
    ANIMAL_TYPES, TOTAL_ROUNDS, BUILDING_RESOURCES, NUM_CELLS, MAX_STABLES,
    compute_pastures, plowable_cells, orthogonal_neighbors,
    validate_fence_layout, MAJOR_IMPROVEMENTS,
)

UNIMPLEMENTED = {
    "FR061": "Agrarian: needs to reclaim placed food (or trigger a free "
             "plow) at the END of a round if the 'Plow 1 field' space went "
             "unused; there is no end-of-round hook (only round_start/"
             "space_used/etc.), so the refund-or-forfeit logic can't be "
             "resolved.",
    "FR067": "Cabbage Lover: needs to count each vegetable converted into "
             "food during feeding, but _apply_feed's raw/cook conversions "
             "fire no hook at all -- there is no per-conversion event to "
             "count against.",
    "FR068": "Card Player: needs to react to receiving ANY of 4 resource "
             "types 'in any way', plus an end-of-round stack rotation; no "
             "generic 'you received resource X' event exists (only "
             "specific space_used/occupation_played/etc. hooks), so the "
             "trigger can't be observed comprehensively.",
    "FR072": "Cocotte: the Minor Improvement branch is a simple bonus "
             "play, but the Major Improvement branch ('pay 1 food to play "
             "a Major Improvement') needs the full build-improvement flow "
             "(upgrade check, Fireplace handling, Well trigger, cost "
             "affordability) replicated outside the normal placement "
             "dispatch -- matches the B150 precedent for 'bonus Major "
             "Improvement' cards.",
    "FR074": "Country Doctor: needs to bypass the room-vs-people-total "
             "legality check on the basic_wish/urgent_wish action spaces; "
             "there is no mod_valid-style hook for action-space legality "
             "(only costs are pluggable, via cost_mod).",
    "FR075": "Cowboy And Mother: needs an end-of-work-phase trigger "
             "(counting spaces used and family size that phase) plus an "
             "optional reward choice (food, or from round 8/12 an animal/"
             "plow instead); no end-of-work-phase hook exists, and the "
             "choice can't be offered from round_start (the nearest "
             "proxy) per the no-prompt-at-round_start rule.",
    "FR076": "Debt Collector: Guest tokens are explicitly unsupported.",
    "FR077": "Dove Hunter: DB text is corrupted -- it describes 'the "
             "Chandler' (an unrelated card that converts sheep/cattle to "
             "food) rather than an effect belonging to Dove Hunter, with "
             "no clean leading clause recoverable as this card's own "
             "text (see module docstring).",
    "FR078": "Drawing Genius: same Major Improvement re-implementation "
             "gap as FR072/B150 -- 'play 1 Major or Minor Improvement' "
             "after using the newest round space needs the full build "
             "flow replicated outside the placement dispatch.",
    "FR081": "Fiddler: needs to know whether you received ANY building "
             "resource during the round (from all sources) to reward "
             "receiving none; there is no generic per-round gain ledger, "
             "and a before/after snapshot can't distinguish gains from "
             "spends.",
    "FR085": "Harvester: the wood-on-play tiers are easy, but 'ALL "
             "players who harvest at least 5 goods in the final harvest "
             "get 3 bonus points' awards points to players other than "
             "this card's owner; score_bonus is scoped to the owning "
             "player only, with no mechanism to award other players.",
    "FR086": "Head Of The Revolution: same scoring-scope gap as FR085 -- "
             "'the next player to have no unused farmyard spaces receives "
             "3 Bonus points' awards points to a player who may not own "
             "this card; score_bonus can't target other players.",
    "FR088": "Journeyman: 'at the start of each round, pay 1 food for 1 "
             "animal of your choice' is an optional round_start choice "
             "(which animal); round_start hooks cannot prompt (see "
             "GUIDE.md and the deck_d D167 precedent) and there is no "
             "action-parameter channel to take the choice up front "
             "instead.",
    "FR089": "Landscape Gardener: 'sow on this card as if it were 2 "
             "fields' needs a card field slot holding 2 independent crop "
             "plantings; the field={...} architecture (and inst['crops']) "
             "supports exactly one crop per card instance. It also grants "
             "an immediate bonus Sow action on play, which would need the "
             "full sow-action flow replicated outside the placement "
             "dispatch.",
    "FR091": "Manual Labourer: same generic-gain-tracking gap as FR068/"
             "FR081 -- 'received exactly 1 type of building resource in "
             "any way' can't be observed without a comprehensive per-"
             "round gain ledger.",
    "FR094": "Miser: the discount only applies when you build EXACTLY 1 "
             "room. cost_mod for kind='room' now gets a batch count in "
             "ctx (engine phase 7), so the condition is expressible -- "
             "this is now a plain implementation gap, not a plumbing one.",
    "FR095": "Musketeer: 'place an Arrow marker at the intersection of 4 "
             "action spaces' needs 2-D adjacency/positioning between "
             "action spaces, which this engine doesn't model (same gap "
             "as B120).",
    "FR098": "Pasteurization Expert: 'receive the top item when you "
             "receive that type of animal outside of breeding' needs a "
             "generic 'animal gained' event covering every source "
             "(markets, other cards, occupations); only specific hooks "
             "exist, so this can't be observed without missing sources.",
    "FR102": "Prefect: needs to count every player's feeding-phase "
             "conversions (Joinery/Pottery/Basketmaker/raw) to food; "
             "_apply_feed fires no hook per conversion (same gap as "
             "FR067), and this card additionally needs visibility into "
             "OTHER players' feedings, which conversions don't broadcast.",
    "FR105": "Reformer: 'keep 1 animal on each occupation card' needs a "
             "house_capacity variant scaled by the number of occupations "
             "in play; house_capacity only supports a flat int or "
             "'per_room', and extending it means editing cards.py's "
             "shared query function, outside this module's two "
             "deliverables.",
    "FR113": "Trailblazer: 'at the start of each round, you may pay 1 "
             "Food to Plow 1 field' is an optional round_start choice; "
             "round_start hooks cannot prompt, and there is no parameter "
             "channel to take the choice up front (same precedent as "
             "FR088/FR120).",
    "FR115": "Unicycle Driver: 'place a fence as a road between 2 "
             "orthogonally adjacent Action spaces' needs 2-D adjacency "
             "between action spaces, which this engine doesn't model "
             "(same gap as FR095).",
    "FR118": "Wood Gatherers: needs the actual (post-cost_mod) wood spent "
             "on improvements/rooms/stables/fences within a round; the "
             "corresponding hooks (rooms_built/stable_built/fences_built/"
             "improvement_built) expose cell/id counts, not the resource "
             "amount actually paid after other cards' discounts, so the "
             "'spent >=4 wood' condition can't be reconstructed reliably.",
    "FR120": "Writing Maniac: needs an optional pay-2-food-or-return "
             "choice at the start of rounds 5/7/9/11; round_start hooks "
             "cannot prompt (same precedent as FR088/FR113).",
}


# ── Shared helpers ────────────────────────────────────────────────────

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


def _apply_flat_discount(cost, amount):
    """Reduce `cost` by `amount`, taking from the largest entries first --
    a defensible default for '...fewer resources of your choice' effects,
    since cost_mod is a pure query with no channel for the player to
    specify which resource to discount."""
    cost = dict(cost)
    remaining = amount
    for k in sorted(cost, key=lambda k: -cost[k]):
        if remaining <= 0:
            break
        take = min(cost[k], remaining)
        cost[k] -= take
        remaining -= take
    return cost


def _rooms(player):
    return sum(1 for c in player["cells"] if c["type"] == "room")


def _buildable_room_cells(player):
    """Mirror engine._buildable_room_cells (duplicated here: cards.py and
    deck modules cannot import the engine at module scope, which imports
    cards.py -- see deck_b_occupations.py's _bonus_occ_cost for the same
    kind of duplication)."""
    pasture_cells = {i for pa in compute_pastures(player) for i in pa}
    rooms = {i for i, c in enumerate(player["cells"]) if c["type"] == "room"}
    eligible = []
    for i, c in enumerate(player["cells"]):
        if i in rooms or c["type"] != "empty" or c["stable"] or i in pasture_cells:
            continue
        if any(nb in rooms for nb in orthogonal_neighbors(i)):
            eligible.append(i)
    return eligible


# ── FR062 Animal Welfarist ────────────────────────────────────────────
# "End of work phase" is approximated as: track which round each of the
# two triggers (stable/fences) last fired in, then check for a match at
# the following round_start. Animals are placed via the same silent
# best-effort placement the breeding phase uses (engine._place_newborn_
# animal), not through ctx["extra"], since a round_start hook must never
# leave an accommodate prompt pending (see GUIDE.md).
def _animal_welfarist_stable(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    inst["data"]["stable_round"] = state["round"]


def _animal_welfarist_fences(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    inst["data"]["fence_round"] = state["round"]


def _animal_welfarist_round_start(state, player, inst, ctx):
    ended = ctx["round"] - 1
    if inst["data"].get("stable_round") == ended and \
            inst["data"].get("fence_round") == ended:
        from server.agricola.engine import AgricolaEngine
        engine = AgricolaEngine()
        gained = [a for a in ANIMAL_TYPES if engine._place_newborn_animal(state, player, a)]
        if gained:
            ctx["log"].append(f"{player['name']}'s Animal Welfarist grants "
                              + ", ".join(f"1 {a}" for a in gained))

compendium_card(
    "FR062",
    hooks={"stable_built": _animal_welfarist_stable,
          "fences_built": _animal_welfarist_fences,
          "round_start": _animal_welfarist_round_start})


# ── FR063 Art Director ────────────────────────────────────────────────
def _art_director_space(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or ctx["space_id"] != "traveling_players":
        return
    prompt_choice(state, player, inst["id"],
                 "Art Director: choose a building resource",
                 ["1 wood", "1 clay", "1 reed"],
                 data={"goods": ["wood", "clay", "reed"]})


def _art_director_choice(state, player, inst, ctx):
    good = ctx["data"]["goods"][ctx["index"]]
    add_goods(ctx["extra"], {"food": 1, good: 1})
    ctx["log"].append(f"{player['name']}'s Art Director grants 1 food and 1 {good}")

compendium_card("FR063", hooks={"space_used": _art_director_space},
                resolve_choice=_art_director_choice)


# ── FR064 Award Winner ────────────────────────────────────────────────
def _award_winner_check(state, player, inst, ctx, cost):
    if ctx["actor"] != player["index"]:
        return
    if inst["data"].get("used_round") == state["round"]:
        return
    options = [g for g, v in cost.items()
              if v > 0 and player["resources"].get(g, 0) >= 1]
    if not options:
        return
    prompt_choice(state, player, inst["id"],
                 "Award Winner: pay 1 additional resource for 1 bonus point?",
                 ["Decline"] + [f"Pay 1 {g}" for g in options],
                 data={"options": options})


def _award_winner_improvement(state, player, inst, ctx):
    _award_winner_check(state, player, inst, ctx,
                       MAJOR_IMPROVEMENTS[ctx["improvement"]]["cost"])


def _award_winner_minor(state, player, inst, ctx):
    _award_winner_check(state, player, inst, ctx,
                       cards.CARDS[ctx["card_id"]]["cost"])


def _award_winner_choice(state, player, inst, ctx):
    if ctx["index"] == 0:
        return
    good = ctx["data"]["options"][ctx["index"] - 1]
    if player["resources"].get(good, 0) < 1:
        return
    player["resources"][good] -= 1
    inst["data"]["used_round"] = state["round"]
    inst["data"]["bonus"] = inst["data"].get("bonus", 0) + 1
    ctx["log"].append(f"{player['name']}'s Award Winner pays 1 {good} for "
                      "1 bonus point")

compendium_card(
    "FR064",
    hooks={"improvement_built": _award_winner_improvement,
          "minor_played": _award_winner_minor},
    resolve_choice=_award_winner_choice,
    score_bonus=lambda s, p, i: i["data"].get("bonus", 0))


# ── FR065 Benefactor ──────────────────────────────────────────────────
# Two chained prompts (animal, then room cell); resolve_choice dispatches
# on a "stage" tag in the prompt data since a card has only one
# resolve_choice slot.
def _benefactor_fences(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or inst["data"].get("used"):
        return
    if player["house_type"] != "wood" or len(compute_pastures(player)) < 4:
        return
    totals = cards.animal_totals_of(player)
    available = [a for a in ANIMAL_TYPES if totals[a] > 0]
    if not available or not _buildable_room_cells(player):
        return
    prompt_choice(state, player, inst["id"],
                 "Benefactor: return an animal to extend your wooden hut?",
                 ["Decline"] + [f"Return 1 {a}" for a in available],
                 data={"animals": available})


def _benefactor_animal_choice(state, player, inst, ctx):
    if ctx["index"] == 0:
        return
    animal = ctx["data"]["animals"][ctx["index"] - 1]
    cells = _buildable_room_cells(player)
    if not cells or not _remove_animal(player, animal, 1):
        return
    inst["data"]["used"] = True
    ctx["log"].append(f"{player['name']}'s Benefactor returns 1 {animal}")
    prompt_choice(state, player, inst["id"],
                 "Benefactor: choose the free room's space",
                 [f"Cell {c}" for c in cells],
                 data={"cells": cells, "stage": "room"})


def _benefactor_room_choice(state, player, inst, ctx):
    cell = ctx["data"]["cells"][ctx["index"]]
    if cell not in _buildable_room_cells(player):
        return
    player["cells"][cell]["type"] = "room"
    ctx["log"].append(f"{player['name']}'s Benefactor builds a free room")


def _benefactor_choice(state, player, inst, ctx):
    if ctx["data"].get("stage") == "room":
        _benefactor_room_choice(state, player, inst, ctx)
    else:
        _benefactor_animal_choice(state, player, inst, ctx)

compendium_card("FR065", hooks={"fences_built": _benefactor_fences},
                resolve_choice=_benefactor_choice)


# ── FR066 Boatswain ───────────────────────────────────────────────────
# "May" is auto-applied: costless and strictly beneficial, no downside.
def _boatswain_space(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or ctx["space_id"] != "fishing":
        return
    filled = 0
    for c in player["cells"]:
        if c["type"] == "field" and not c["crops"]:
            c["crops"] = {"type": "grain", "count": 1}
            filled += 1
    if filled:
        ctx["log"].append(f"{player['name']}'s Boatswain places grain on "
                          f"{filled} empty field(s)")

compendium_card("FR066", hooks={"space_used": _boatswain_space})


# ── FR069 Cat Lover ───────────────────────────────────────────────────
def _cat_lover_mod(state, player, kind, cost, ctx):
    if kind != "room" or player["house_type"] != "wood":
        return cost
    total = sum(cards.animal_totals_of(player).values())
    if total >= 7:
        discount = 4
    elif total >= 4:
        discount = 3
    elif total >= 2:
        discount = 2
    elif total >= 1:
        discount = 1
    else:
        return cost
    return _apply_flat_discount(cost, discount * ctx.get("count", 1))

compendium_card("FR069", cost_mod=_cat_lover_mod)


# ── FR070 Cattle Dealer ───────────────────────────────────────────────
def _cattle_dealer_fences(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    if not any(len(pa) >= 3 for pa in ctx["new_pastures"]):
        return
    if player["resources"]["reed"] < 1:
        return
    prompt_choice(state, player, inst["id"],
                 "Cattle Dealer: pay 1 reed for 2 cattle?",
                 ["Decline", "Pay 1 reed"])


def _cattle_dealer_choice(state, player, inst, ctx):
    if ctx["index"] == 0 or player["resources"]["reed"] < 1:
        return
    player["resources"]["reed"] -= 1
    add_goods(ctx["extra"], {"cattle": 2})
    ctx["log"].append(f"{player['name']}'s Cattle Dealer converts 1 reed "
                      "to 2 cattle")

compendium_card("FR070", hooks={"fences_built": _cattle_dealer_fences},
                resolve_choice=_cattle_dealer_choice)


# ── FR071 Child Care Worker ───────────────────────────────────────────
def _child_care_worker_growth(state, player, inst, ctx):
    if ctx["actor"] == player["index"]:
        return
    options, data = ["Decline"], []
    if player["resources"]["wood"] >= 1:
        options.append("Convert 1 wood to 2 food")
        data.append(("wood", 2))
    if player["resources"]["wood"] >= 1 and player["resources"]["reed"] >= 1:
        options.append("Convert 1 wood and 1 reed to 5 food")
        data.append(("wood_reed", 5))
    if len(options) > 1:
        prompt_choice(state, player, inst["id"],
                     "Child Care Worker: convert resources to food?",
                     options, data={"choices": data})


def _child_care_worker_choice(state, player, inst, ctx):
    if ctx["index"] == 0:
        return
    kind, food = ctx["data"]["choices"][ctx["index"] - 1]
    if kind == "wood":
        if player["resources"]["wood"] < 1:
            return
        player["resources"]["wood"] -= 1
    else:
        if player["resources"]["wood"] < 1 or player["resources"]["reed"] < 1:
            return
        player["resources"]["wood"] -= 1
        player["resources"]["reed"] -= 1
    player["resources"]["food"] += food
    ctx["log"].append(f"{player['name']}'s Child Care Worker converts to "
                      f"{food} food")

compendium_card("FR071", hooks={"family_growth": _child_care_worker_growth},
                resolve_choice=_child_care_worker_choice)


# ── FR073 Convict Number 24601 ────────────────────────────────────────
# The lost family member is approximated as reserving 1 placement slot
# every round from the round after play onward (rather than pinning a
# specific person), which matches the net effect ("1 fewer usable family
# member") regardless of family size changes.
def _convict_play(state, player, inst, ctx):
    inst["data"]["played_round"] = state["round"]


def _convict_round_start(state, player, inst, ctx):
    played = inst["data"].get("played_round")
    if played is None or ctx["round"] <= played:
        return
    player["people_placed"] = min(player["people_total"],
                                  player["people_placed"] + 1)


def _convict_score(state, player, inst):
    played = inst["data"].get("played_round")
    if played is None:
        return 0
    return 2 * (TOTAL_ROUNDS - played + 1)

compendium_card("FR073", hooks={"play": _convict_play,
                                "round_start": _convict_round_start},
                score_bonus=_convict_score)


# ── FR079 Drinker of Absinthe ─────────────────────────────────────────
compendium_card("FR079", hooks=cards.schedule_on_play(
    "food", fixed_rounds=tuple(range(7, 15))))


# ── FR080 Fencing Master ──────────────────────────────────────────────
# Fences banked on this card deplete when spent; cost_mod is a pure query
# with no instance parameter, so the instance is looked up by this
# card's fixed id. The discount computed by the LAST cost_mod call
# before payment is always the real one (any earlier calls are legality
# previews from _space_usable that get overwritten before consumption),
# so recording it and consuming it in fences_built (which always follows
# the real payment immediately) is safe.
def _fencing_master_occ(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    amt = 2 if player["occs_played"] <= 3 else 1
    inst["data"]["banked"] = inst["data"].get("banked", 0) + amt
    ctx["log"].append(f"{player['name']}'s Fencing Master banks {amt} fence(s)")


def _fencing_master_mod(state, player, kind, cost, ctx):
    if kind != "fences":
        return cost
    inst = next((i for i in player["occupations"] if i["id"] == "FR080"), None)
    if inst is None:
        return cost
    banked = inst["data"].get("banked", 0)
    if banked <= 0:
        return cost
    discount = min(banked, ctx.get("count", 0))
    inst["data"]["_pending_discount"] = discount
    cost = dict(cost)
    cost["wood"] = max(0, cost.get("wood", 0) - discount)
    return cost


def _fencing_master_fences_built(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    consumed = inst["data"].pop("_pending_discount", 0)
    if consumed:
        inst["data"]["banked"] = max(0, inst["data"].get("banked", 0) - consumed)
        ctx["log"].append(f"{player['name']}'s Fencing Master spends "
                          f"{consumed} banked fence(s)")

compendium_card(
    "FR080",
    hooks={"occupation_played": _fencing_master_occ,
          "fences_built": _fencing_master_fences_built},
    cost_mod=_fencing_master_mod)


# ── FR082 Gardening Enthusiast ────────────────────────────────────────
def _gardening_enthusiast_play(state, player, inst, ctx):
    player["resources"]["grain"] += 1
    player["resources"]["vegetable"] += 1
    inst["data"]["loan"] = {"grain": 1, "vegetable": 1}
    ctx["log"].append(f"{player['name']}'s Gardening Enthusiast takes a "
                      "loan of 1 grain and 1 vegetable")


def _gardening_enthusiast_available(state, player, inst):
    loan = inst["data"].get("loan") or {}
    return any(v > 0 and player["resources"].get(g, 0) >= 1
              for g, v in loan.items())


def _gardening_enthusiast_apply(state, player, inst, ctx):
    loan = inst["data"].get("loan") or {}
    good = (ctx.get("params") or {}).get("good")
    if good not in loan or loan[good] <= 0:
        raise ValueError("Gardening Enthusiast: choose a crop to repay "
                         "(params.good)")
    if player["resources"].get(good, 0) < 1:
        raise ValueError(f"Not enough {good} to repay")
    player["resources"][good] -= 1
    loan[good] -= 1
    ctx["log"].append(f"{player['name']}'s Gardening Enthusiast repays "
                      f"1 {good}")


def _gardening_enthusiast_score(state, player, inst):
    loan = inst["data"].get("loan") or {}
    return -sum(v for v in loan.values() if v > 0)

compendium_card(
    "FR082",
    hooks={"play": _gardening_enthusiast_play},
    card_action={"available": _gardening_enthusiast_available,
                "apply": _gardening_enthusiast_apply,
                "description": "Repay 1 grain or vegetable of the "
                               "Gardening Enthusiast's loan"},
    score_bonus=_gardening_enthusiast_score)


# ── FR083 Good Friend ─────────────────────────────────────────────────
def _good_friend_play(state, player, inst, ctx):
    inst["data"].update({"wood": 3, "clay": 2, "stone": 2, "reed": 1})


def _good_friend_minor(state, player, inst, ctx):
    if ctx["actor"] == player["index"]:
        return
    cost = cards.CARDS[ctx["card_id"]]["cost"]
    options, data = ["Decline"], []
    for good, amount in cost.items():
        if amount > 0 and inst["data"].get(good, 0) > 0:
            options.append(f"Take 1 {good}")
            data.append(good)
    if len(options) > 1:
        prompt_choice(state, player, inst["id"],
                     "Good Friend: take a building resource?", options,
                     data={"goods": data})


def _good_friend_choice(state, player, inst, ctx):
    if ctx["index"] == 0:
        return
    good = ctx["data"]["goods"][ctx["index"] - 1]
    if inst["data"].get(good, 0) <= 0:
        return
    inst["data"][good] -= 1
    player["resources"][good] += 1
    ctx["log"].append(f"{player['name']}'s Good Friend releases 1 {good}")

compendium_card("FR083", hooks={"play": _good_friend_play,
                                "minor_played": _good_friend_minor},
                resolve_choice=_good_friend_choice)


# ── FR084 Grain Speculator ────────────────────────────────────────────
def _grain_speculator_play(state, player, inst, ctx):
    rnd = state["round"]
    targets = [r for r in (rnd + 1, rnd + 3, rnd + 5) if r <= TOTAL_ROUNDS]
    for r in targets:
        slot = state["round_goods"].setdefault(str(r), {}) \
            .setdefault(str(player["index"]), {})
        add_goods(slot, {"grain": 1})
    if targets:
        ctx["log"].append("Grain Speculator places 1 grain on rounds "
                          f"{', '.join(map(str, targets))}")

compendium_card("FR084", hooks={"play": _grain_speculator_play})


# ── FR087 Immigrants Son ──────────────────────────────────────────────
def _immigrants_son_occ(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or ctx["card_id"] == inst["id"]:
        return
    if player["occs_played"] not in (5, 6, 7):
        return
    options = plowable_cells(player)
    if not options:
        return
    prompt_choice(state, player, inst["id"],
                 "Immigrants Son: plow a field?",
                 ["Decline"] + [f"Plow cell {c}" for c in options],
                 data={"cells": options})


def _immigrants_son_choice(state, player, inst, ctx):
    if ctx["index"] == 0:
        return
    cell = ctx["data"]["cells"][ctx["index"] - 1]
    if cell not in plowable_cells(player):
        return
    player["cells"][cell]["type"] = "field"
    ctx["log"].append(f"{player['name']}'s Immigrants Son plows a field")

compendium_card("FR087", hooks={"occupation_played": _immigrants_son_occ},
                resolve_choice=_immigrants_son_choice)


# ── FR090 Lemon Trader ────────────────────────────────────────────────
def _lemon_trader_round_start(state, player, inst, ctx):
    inst["data"]["used"] = 0


def _lemon_trader_available(state, player, inst):
    if state["phase"] != "work" or inst["data"].get("used", 0) >= 2:
        return False
    return player["resources"]["grain"] >= 1 or player["resources"]["vegetable"] >= 1


def _lemon_trader_apply(state, player, inst, ctx):
    params = ctx.get("params") or {}
    give = params.get("give")
    resources = params.get("resources") or []
    if give == "grain":
        if player["resources"]["grain"] < 1 or len(resources) != 1 \
                or resources[0] not in BUILDING_RESOURCES:
            raise ValueError("Lemon Trader: choose 1 building resource "
                             "(params.resources)")
        player["resources"]["grain"] -= 1
        player["resources"][resources[0]] += 1
    elif give == "vegetable":
        if player["resources"]["vegetable"] < 1 or len(set(resources)) != 2 \
                or any(r not in BUILDING_RESOURCES for r in resources):
            raise ValueError("Lemon Trader: choose 2 different building "
                             "resources (params.resources)")
        player["resources"]["vegetable"] -= 1
        for r in resources:
            player["resources"][r] += 1
    else:
        raise ValueError("Lemon Trader: choose params.give ('grain' or "
                         "'vegetable')")
    inst["data"]["used"] = inst["data"].get("used", 0) + 1
    ctx["log"].append(f"{player['name']}'s Lemon Trader exchanges 1 {give}")

compendium_card(
    "FR090",
    hooks={"round_start": _lemon_trader_round_start},
    card_action={"available": _lemon_trader_available,
                "apply": _lemon_trader_apply,
                "description": "Exchange 1 grain for a building resource, "
                               "or 1 vegetable for 2 different ones "
                               "(twice per round)"})


# ── FR092 Martial Artist ──────────────────────────────────────────────
def _martial_artist_offer(state, player, inst, ctx):
    if inst["data"].get("discarded_this_harvest", 0) >= 2:
        return
    if not player["hand_minors"]:
        return
    options = ["Decline"] + [cards.CARDS[cid]["name"]
                             for cid in player["hand_minors"]]
    prompt_choice(state, player, inst["id"],
                 "Martial Artist: discard a minor improvement for 2 food?",
                 options, data={"hand": list(player["hand_minors"])})


def _martial_artist_harvest(state, player, inst, ctx):
    inst["data"]["discarded_this_harvest"] = 0
    _martial_artist_offer(state, player, inst, ctx)


def _martial_artist_choice(state, player, inst, ctx):
    if ctx["index"] == 0:
        return
    cid = ctx["data"]["hand"][ctx["index"] - 1]
    if cid not in player["hand_minors"]:
        return
    player["hand_minors"].remove(cid)
    player["resources"]["food"] += 2
    inst["data"]["discarded_this_harvest"] = \
        inst["data"].get("discarded_this_harvest", 0) + 1
    ctx["log"].append(f"{player['name']}'s Martial Artist discards "
                      f"\"{cards.CARDS[cid]['name']}\" for 2 food")
    _martial_artist_offer(state, player, inst, ctx)

compendium_card("FR092", hooks={"harvest_field": _martial_artist_harvest},
                resolve_choice=_martial_artist_choice)


# ── FR093 Mastermind ──────────────────────────────────────────────────
def _mastermind_track(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    inst["data"]["count"] = inst["data"].get("count", 0) + 1


def _mastermind_occ(state, player, inst, ctx):
    if cards.CARDS[ctx["card_id"]].get("points", 0):
        _mastermind_track(state, player, inst, ctx)


def _mastermind_minor(state, player, inst, ctx):
    if cards.CARDS[ctx["card_id"]].get("points", 0):
        _mastermind_track(state, player, inst, ctx)


def _mastermind_improvement(state, player, inst, ctx):
    if MAJOR_IMPROVEMENTS[ctx["improvement"]].get("points", 0):
        _mastermind_track(state, player, inst, ctx)

compendium_card(
    "FR093",
    hooks={"occupation_played": _mastermind_occ,
          "minor_played": _mastermind_minor,
          "improvement_built": _mastermind_improvement},
    score_bonus=lambda s, p, i: i["data"].get("count", 0))


# ── FR096 Oceanographer ───────────────────────────────────────────────
OCEANOGRAPHER_STACK = ["wood", "clay", "grain", "stone", "vegetable"]


def _oceanographer_plow(state, player, inst, ctx):
    idx = inst["data"].get("index", 0)
    if idx >= len(OCEANOGRAPHER_STACK):
        return
    good = OCEANOGRAPHER_STACK[idx]
    prompt_choice(state, player, inst["id"],
                 f"Oceanographer: take 1 {good}?",
                 ["Decline", f"Take 1 {good}"], data={"good": good, "index": idx})


def _oceanographer_choice(state, player, inst, ctx):
    if ctx["index"] == 0:
        return
    if inst["data"].get("index", 0) != ctx["data"]["index"]:
        return
    good = ctx["data"]["good"]
    add_goods(ctx["extra"], {good: 1})
    inst["data"]["index"] = ctx["data"]["index"] + 1
    ctx["log"].append(f"{player['name']}'s Oceanographer grants 1 {good}")

compendium_card("FR096", hooks={"plow": _oceanographer_plow},
                resolve_choice=_oceanographer_choice)


# ── FR097 Parquet Setter ──────────────────────────────────────────────
def _unused_farmyard(player):
    return sum(1 for c in player["cells"] if c["type"] == "empty" and not c["stable"])


def _parquet_setter_round_start(state, player, inst, ctx):
    current = _unused_farmyard(player)
    last = inst["data"].get("last_unused")
    if last is not None:
        reduced = last - current
        if reduced > 0:
            player["resources"]["wood"] += 1
            gain = "1 wood"
            if reduced >= 2:
                player["resources"]["food"] += 1
                gain += " and 1 food"
            ctx["log"].append(f"{player['name']}'s Parquet Setter grants {gain}")
    inst["data"]["last_unused"] = current

compendium_card("FR097", hooks={"round_start": _parquet_setter_round_start})


# ── FR099 Pear Peeler ─────────────────────────────────────────────────
def _pear_peeler_space(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    wood = ctx["goods"].get("wood", 0)
    if wood < 1:
        return
    tiers = [(1, {"grain": 1}, "Leave 1 wood for 1 grain"),
            (2, {"vegetable": 1}, "Leave 2 wood for 1 vegetable"),
            (3, {"grain": 1, "vegetable": 1},
             "Leave 3 wood for 1 grain and 1 vegetable")]
    options, data = ["Decline"], []
    for leave, gain, label in tiers:
        if wood >= leave:
            options.append(label)
            data.append((leave, gain))
    if len(options) > 1:
        prompt_choice(state, player, inst["id"],
                     "Pear Peeler: leave wood on the space for crops?",
                     options, data={"choices": data, "space_id": ctx["space_id"]})


def _pear_peeler_choice(state, player, inst, ctx):
    if ctx["index"] == 0:
        return
    leave, gain = ctx["data"]["choices"][ctx["index"] - 1]
    if player["resources"]["wood"] < leave:
        return
    space = next((s for s in state["action_spaces"]
                 if s["id"] == ctx["data"]["space_id"]), None)
    if space is None:
        return
    player["resources"]["wood"] -= leave
    space["supply"]["wood"] = space["supply"].get("wood", 0) + leave
    add_goods(player["resources"], gain)
    ctx["log"].append(f"{player['name']}'s Pear Peeler leaves {leave} wood "
                      "for " + goods_str(gain))

compendium_card("FR099", hooks={"space_used": _pear_peeler_space},
                resolve_choice=_pear_peeler_choice)


# ── FR100 Pipe Smoker ─────────────────────────────────────────────────
def _pipe_smoker_harvest(state, player, inst, ctx):
    has_grain_field = any(
        c["type"] == "field" and c["crops"] and c["crops"]["type"] == "grain"
        for c in player["cells"]) or any(
        i["crops"] and i["crops"]["type"] == "grain"
        for i in cards.card_fields(player))
    if has_grain_field:
        player["resources"]["wood"] += 2
        ctx["log"].append(f"{player['name']}'s Pipe Smoker grants 2 wood")

compendium_card("FR100", hooks={"harvest_field": _pipe_smoker_harvest})


# ── FR101 Powerhouse ──────────────────────────────────────────────────
def _powerhouse_round_start(state, player, inst, ctx):
    stone = player["resources"]["stone"]
    amount = 2 if stone >= 5 else 1 if stone >= 3 else 0
    if amount:
        player["resources"]["food"] += amount
        ctx["log"].append(f"{player['name']}'s Powerhouse grants {amount} food")

compendium_card("FR101", hooks={"round_start": _powerhouse_round_start})


# ── FR103 Prosecutor ──────────────────────────────────────────────────
def _prosecutor_mod(state, player, kind, cost, ctx):
    if kind != "improvement":
        return cost
    mine = len(player["improvements"])
    fewer_than = sum(1 for p2 in state["players"]
                     if p2 is not player and len(p2["improvements"]) > mine)
    if fewer_than >= 4:
        discount = 3
    elif fewer_than >= 3:
        discount = 2
    elif fewer_than >= 2:
        discount = 1
    else:
        return cost
    return _apply_flat_discount(cost, discount)

compendium_card("FR103", cost_mod=_prosecutor_mod)


# ── FR104 Racing Stable Manager ───────────────────────────────────────
def _racing_stable_manager_stable(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or not ctx["cells"]:
        return
    if player["resources"]["food"] < 1 or not plowable_cells(player):
        return
    prompt_choice(state, player, inst["id"],
                 "Racing Stable Manager: pay 1 food to plow a field?",
                 ["Decline", "Pay 1 food"])


def _racing_stable_manager_pay_choice(state, player, inst, ctx):
    if ctx["index"] == 0 or player["resources"]["food"] < 1:
        return
    options = plowable_cells(player)
    if not options:
        return
    player["resources"]["food"] -= 1
    inst["data"]["pending_plow"] = True
    prompt_choice(state, player, inst["id"],
                 "Racing Stable Manager: choose a field to plow",
                 [f"Plow cell {c}" for c in options], data={"cells": options})


def _racing_stable_manager_plow_choice(state, player, inst, ctx):
    cell = ctx["data"]["cells"][ctx["index"]]
    if cell not in plowable_cells(player):
        return
    player["cells"][cell]["type"] = "field"
    ctx["log"].append(f"{player['name']}'s Racing Stable Manager plows a field")


def _racing_stable_manager_choice(state, player, inst, ctx):
    if inst["data"].pop("pending_plow", False):
        _racing_stable_manager_plow_choice(state, player, inst, ctx)
    else:
        _racing_stable_manager_pay_choice(state, player, inst, ctx)

compendium_card("FR104", hooks={"stable_built": _racing_stable_manager_stable},
                resolve_choice=_racing_stable_manager_choice)


# ── FR106 Sailboat Constructor ────────────────────────────────────────
SAILBOAT_STACKS = [
    ("wood", 3, 2), ("clay", 3, 3), ("stone", 2, 4), ("grain_vegetable", None, 5),
]


def _sailboat_offer(state, player, inst, ctx):
    idx = inst["data"].get("bought", 0)
    if idx >= len(SAILBOAT_STACKS):
        return
    good, amount, cost = SAILBOAT_STACKS[idx]
    if player["resources"]["food"] < cost:
        return
    label = (f"Buy 1 grain and 1 vegetable for {cost} food" if good == "grain_vegetable"
             else f"Buy {amount} {good} for {cost} food")
    prompt_choice(state, player, inst["id"],
                 "Sailboat Constructor: buy the next stack?",
                 ["Decline", label], data={"index": idx})


def _sailboat_choice(state, player, inst, ctx):
    if ctx["index"] == 0:
        return
    idx = ctx["data"]["index"]
    if inst["data"].get("bought", 0) != idx:
        return
    good, amount, cost = SAILBOAT_STACKS[idx]
    if player["resources"]["food"] < cost:
        return
    player["resources"]["food"] -= cost
    if good == "grain_vegetable":
        player["resources"]["grain"] += 1
        player["resources"]["vegetable"] += 1
        gained = "1 grain and 1 vegetable"
    else:
        player["resources"][good] += amount
        gained = f"{amount} {good}"
    inst["data"]["bought"] = idx + 1
    ctx["log"].append(f"{player['name']}'s Sailboat Constructor buys "
                      f"{gained} for {cost} food")
    _sailboat_offer(state, player, inst, ctx)

compendium_card("FR106", hooks={"harvest_field": _sailboat_offer},
                resolve_choice=_sailboat_choice)


# ── FR107 Sculptors Son ───────────────────────────────────────────────
# Upgrade minors (which retain the bonus per the ruling) aren't tracked;
# only the base major improvements are checked -- a documented gap.
SCULPTORS_SON_MAP = {"wood": ("joinery", 2), "clay": ("pottery", 2),
                     "reed": ("basketmaker", 1)}


def _sculptors_son_space(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or len(ctx["goods"]) != 1:
        return
    good = next(iter(ctx["goods"]))
    mapping = SCULPTORS_SON_MAP.get(good)
    if not mapping:
        return
    improvement, bonus = mapping
    if improvement in player["improvements"]:
        add_goods(ctx["extra"], {good: bonus})
        ctx["log"].append(f"{player['name']}'s Sculptors Son adds {bonus} {good}")

compendium_card("FR107", hooks={"space_used": _sculptors_son_space})


# ── FR108 Shovel Worker ───────────────────────────────────────────────
def _shovel_worker_food(state, player):
    n = len(compute_pastures(player))
    return 4 if n >= 5 else 3 if n >= 4 else 2 if n >= 3 else 1 if n >= 2 else 0

compendium_card("FR108", hooks=cards.harvest_food(_shovel_worker_food))


# ── FR109 Stage Star ──────────────────────────────────────────────────
def _stage_star_play(state, player, inst, ctx):
    if state["stage"] == 1:
        player["resources"]["wood"] += 6
        ctx["log"].append(f"{player['name']}'s Stage Star grants 6 wood")


def _stage_star_space(state, player, inst, ctx):
    if ctx["space_id"] != "traveling_players" or ctx["actor"] == player["index"]:
        return
    if player["resources"]["wood"] < 1:
        return
    player["resources"]["wood"] -= 1
    actor = state["players"][ctx["actor"]]
    actor["resources"]["wood"] += 1
    ctx["log"].append(f"{player['name']}'s Stage Star pays {actor['name']} 1 wood")

compendium_card("FR109", hooks={"play": _stage_star_play,
                                "space_used": _stage_star_space})


# ── FR110 Stroller ────────────────────────────────────────────────────
def _stroller_play(state, player, inst, ctx):
    stack = (ctx.get("params") or {}).get("stack")
    if not isinstance(stack, list) or len(stack) != 6 \
            or any(g not in BUILDING_RESOURCES for g in stack) \
            or not all(g in stack for g in BUILDING_RESOURCES):
        raise ValueError("Stroller: choose a 6-item stack including at "
                         "least 1 of each building resource (params.stack)")
    inst["data"]["stack"] = list(stack)


def _stroller_harvest(state, player, inst, ctx):
    stack = inst["data"].get("stack") or []
    if not stack:
        return
    good = stack.pop(0)
    player["resources"][good] += 1
    ctx["log"].append(f"{player['name']}'s Stroller grants 1 {good}")

compendium_card("FR110", hooks={"play": _stroller_play,
                                "harvest_field": _stroller_harvest})


# ── FR111 Sun Farmer ──────────────────────────────────────────────────
def _sun_farmer_space(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    if ctx["space_id"] == "grain_seeds":
        add_goods(ctx["extra"], {"sheep": 1})
        ctx["log"].append(f"{player['name']}'s Sun Farmer adds 1 sheep")
    elif ctx["space_id"] == "sheep_market" and ctx["goods"].get("sheep"):
        add_goods(ctx["extra"], {"grain": 1})
        ctx["log"].append(f"{player['name']}'s Sun Farmer adds 1 grain")

compendium_card("FR111", hooks={"space_used": _sun_farmer_space})


# ── FR112 Tower Builder ───────────────────────────────────────────────
def _tower_builder_rooms(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    built = len(ctx["cells"])
    before = _rooms(player) - built
    if before != 2:
        return
    if any(_rooms(p2) == 2 for p2 in state["players"] if p2 is not player):
        return
    options = _buildable_room_cells(player)
    if not options:
        return
    prompt_choice(state, player, inst["id"],
                 "Tower Builder: build 1 additional room for free?",
                 ["Decline"] + [f"Cell {c}" for c in options],
                 data={"cells": options})


def _tower_builder_choice(state, player, inst, ctx):
    if ctx["index"] == 0:
        return
    cell = ctx["data"]["cells"][ctx["index"] - 1]
    if cell not in _buildable_room_cells(player):
        return
    player["cells"][cell]["type"] = "room"
    ctx["log"].append(f"{player['name']}'s Tower Builder builds a free room")

compendium_card("FR112", hooks={"rooms_built": _tower_builder_rooms},
                resolve_choice=_tower_builder_choice)


# ── FR114 Turkey Breeder ──────────────────────────────────────────────
def _turkey_breeder_play(state, player, inst, ctx):
    params = ctx.get("params") or {}
    fences = params.get("fences") or []
    stable_cell = params.get("stable_cell")
    if len(fences) > 4:
        raise ValueError("Turkey Breeder: choose up to 4 fence edges "
                         "(params.fences)")
    if fences:
        layout = sorted(set(player["fences"]) | set(fences))
        ok, err, _pastures = validate_fence_layout(player, layout)
        if not ok:
            raise ValueError(f"Turkey Breeder: {err}")
        player["fences"] = layout
    if stable_cell is not None:
        if not isinstance(stable_cell, int) or not (0 <= stable_cell < NUM_CELLS):
            raise ValueError("Turkey Breeder: invalid stable cell")
        c = player["cells"][stable_cell]
        stables = sum(1 for x in player["cells"] if x["stable"])
        if stables >= MAX_STABLES or c["type"] != "empty" or c["stable"]:
            raise ValueError("Turkey Breeder: invalid stable placement")
        c["stable"] = True
    inst["data"]["owed_wood"] = 4
    ctx["log"].append(f"{player['name']}'s Turkey Breeder builds "
                      f"{len(fences)} free fence(s)"
                      + (" and 1 free stable" if stable_cell is not None else ""))


def _turkey_breeder_harvest(state, player, inst, ctx):
    if ctx["harvest_index"] != 6:
        return
    owed = inst["data"].get("owed_wood", 0)
    if owed <= 0:
        return
    paid = min(owed, player["resources"]["wood"])
    player["resources"]["wood"] -= paid
    shortfall = owed - paid
    if shortfall:
        player["begging"] += shortfall
        ctx["log"].append(f"{player['name']}'s Turkey Breeder returns {paid} "
                          f"wood and takes {shortfall} begging marker(s)")
    else:
        ctx["log"].append(f"{player['name']}'s Turkey Breeder returns "
                          f"{paid} wood")
    inst["data"]["owed_wood"] = 0

compendium_card("FR114", hooks={"play": _turkey_breeder_play,
                                "harvest_field": _turkey_breeder_harvest})


# ── FR116 Village Druid ───────────────────────────────────────────────
def _village_druid_occ(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    add_goods(ctx["extra"], {"sheep": 1})
    ctx["log"].append(f"{player['name']}'s Village Druid grants 1 sheep")

compendium_card("FR116", hooks={"occupation_played": _village_druid_occ})


# ── FR117 Wealthiest European ─────────────────────────────────────────
def _wealthiest_european_play(state, player, inst, ctx):
    if player["occs_played"] != 1:
        return
    n = state["round"] - 1
    if n <= 0:
        return
    prompt_choice(state, player, inst["id"],
                 "Wealthiest European: choose a building resource",
                 [f"{n} wood", f"{n} clay", f"{n} reed", f"{n} stone"],
                 data={"goods": ["wood", "clay", "reed", "stone"], "n": n})


def _wealthiest_european_choice(state, player, inst, ctx):
    good = ctx["data"]["goods"][ctx["index"]]
    n = ctx["data"]["n"]
    player["resources"][good] += n
    ctx["log"].append(f"{player['name']}'s Wealthiest European grants {n} {good}")

compendium_card("FR117", hooks={"play": _wealthiest_european_play},
                resolve_choice=_wealthiest_european_choice)


# ── FR119 Workaholic ──────────────────────────────────────────────────
def _workaholic_play(state, player, inst, ctx):
    if sum(cards.animal_totals_of(player).values()) == 0:
        inst["data"]["bank"] = {"wood": 5, "clay": 4, "stone": 3}
        ctx["log"].append(f"{player['name']}'s Workaholic stores 5 wood, "
                          "4 clay, and 3 stone")


def _workaholic_threshold(state):
    return 9 if state["player_count"] >= 5 else 7


def _workaholic_available(state, player, inst):
    bank = inst["data"].get("bank")
    if not bank or not any(v > 0 for v in bank.values()):
        return False
    return sum(cards.animal_totals_of(player).values()) >= _workaholic_threshold(state)


def _workaholic_apply(state, player, inst, ctx):
    bank = inst["data"].get("bank") or {}
    add_goods(player["resources"], bank)
    ctx["log"].append(f"{player['name']}'s Workaholic releases " + goods_str(bank))
    inst["data"]["bank"] = {}

compendium_card(
    "FR119",
    hooks={"play": _workaholic_play},
    card_action={"available": _workaholic_available,
                "apply": _workaholic_apply,
                "description": "Claim the Workaholic's stored wood/clay/"
                               "stone once you have enough animals"})
