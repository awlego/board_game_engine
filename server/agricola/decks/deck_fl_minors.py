"""Deck FL minor improvements (codes FL001-FL030 from the compendium DB;
all 30 FL codes are minors -- there is no FL occupations deck). FL is a
fan-made ("Flanders"?) compendium deck; several cards reference mechanics
this engine cannot express (see UNIMPLEMENTED below) or, in one case
(FL002), a physical card image the parsed DB text never captured.

No mid-string "(N-M players)" text-bleed artifacts (the pattern documented
in deck_b_occupations.py) were found in this slice; no _TEXT_FIXES needed.
Every implemented card's cost string parses cleanly with cards.parse_cost.

Notable interpretation calls, documented again at each card below:
- FL004 Educational Building: "the first time you use your 3rd and/or 4th
  family member this game" is read as "the first time this is your 3rd/4th
  WORKER PLACEMENT OF A ROUND" (player["people_placed"] == 2 or 3 before
  the placement) rather than a cumulative game-wide ordinal -- the engine
  has no per-person identity to track the latter at all, and "Nth family
  member to act" reads this way on comparable real Agricola card text.
- FL021 Lions Mound / FL023 Carrot Museum: "round number" of an action
  space is its 1-based index in state["revealed"] (permanent spaces have
  none and are excluded from the comparison); "at the end of round
  8/10/12" is read as "at the start of round 9/11/13" -- 8/10/12 are never
  harvest rounds in this engine (HARVEST_ROUNDS = 4,7,9,11,13,14), so
  nothing happens between one ending and the next round's start.
- FL025 Cockaigne: "1 sheep and 1 wild boar ON your farmyard" is read
  literally (cells only, excluding house pets) since the card's own text
  distinguishes farmyard animals from goods "in your personal supply".
"""

from server.agricola.cards import (
    compendium_card, CARDS, prompt_choice, add_goods,
    needs_occupations, exact_occupations, needs_grain_field,
    animal_totals_of, card_fields, get_field_stack, set_field_stack,
    field_stacks, fire_gained, grant_guest, on_play_gain,
)
from server.agricola import sub_actions
from server.agricola.state import (
    ANIMAL_TYPES, TOTAL_ROUNDS, ROWS, COLS, cell_index,
    compute_pastures, orthogonal_neighbors,
)

UNIMPLEMENTED = {
    "FL001": "requires sowing a second crop type onto a field cell/stack "
             "slot that already holds a different crop -- the same "
             "still-gated class of gap as C069 Land Consolidation "
             "(decks/GUIDE.md item 19): one {type,count} slot can only "
             "ever hold one crop type; the multi-stack mechanism gives a "
             "CARD extra independent slots, not a way for one existing "
             "slot to hold two crops at once.",
    "FL002": "the '5-room home shaped like the picture below' geometry is "
             "only shown as an image on the physical card, never captured "
             "in the parsed compendium text -- there is no reliable "
             "source for which specific 5-cell polyomino qualifies, and "
             "guessing at a shape would be exactly the silent "
             "approximation rule 2 forbids.",
    "FL008": "requires a single person placement to use TWO orthogonally "
             "adjacent accumulation spaces at once, each left with a "
             "partial ('leave 2 items') withdrawal -- the engine's "
             "placement model ties one worker to exactly one action "
             "space with an all-or-nothing take; no dual-space placement "
             "or partial-withdrawal primitive exists.",
    "FL009": "the '3 bonus points to whichever player first has no "
             "unused farmyard spaces' clause needs points credited to a "
             "player who may not be this card's own owner -- score_bonus "
             "is only ever evaluated while summing the OWNING player's "
             "own in-play cards (cards.score_bonuses/scoring.score_"
             "player), so there is no way to award bonus points to an "
             "arbitrary other player triggered by their own board state.",
    "FL010": "'immediately place and use all the Family members that are "
             "in your home' needs a card hook to trigger one or more "
             "actual action-space placements -- there is no generic "
             "'place a person on an arbitrary action space and resolve "
             "it' primitive exposed to card code; sub_actions only "
             "covers the named build/play/sow/family-growth "
             "transactions, and engine._resolve_space's per-space "
             "dispatch is private to engine.py (see decks/GUIDE.md's "
             "B023 note making the same point).",
    "FL011": "forces a specific stage-2 round card ('Family growth also "
             "1 Minor Improvement') to appear face-down at round 6 and "
             "grants early access the round before -- modifying the "
             "action-space card deck's reveal order/identity is "
             "explicitly unsupported (decks/GUIDE.md's 'Not supported' "
             "list).",
    "FL012": "'only you can use this space with your first person' "
             "whenever its supply crosses a threshold requires DENYING "
             "other players' otherwise-legal placements -- no spec key "
             "exists for that (occupied_ok only grants extra access on "
             "top of the normal rule, it never revokes it; "
             "placement_blocked blocks a player from every space, not "
             "one specific space).",
    "FL013": "the purchased Sheep must never breed while every other "
             "identical sheep in the player's herd keeps breeding "
             "normally -- breeding (engine._finish_harvest) operates on "
             "aggregate per-type counts via state.animal_counts, with no "
             "per-unit animal tagging to exempt one individual.",
    "FL027": "'at the start of each Harvest, you may pay 1 Grain to skip "
             "all 3 phases of that Harvest' (a DB ruling confirms the "
             "base text is repeatable every harvest, not the 1-time cap "
             "a separate '(FotM)'-tagged ruling adds) needs a per-player "
             "exemption from three separate systemic engine phases "
             "(field, feeding, breeding) for one harvest -- no such "
             "'skip this harvest' primitive exists; harvest_start can "
             "react before the field phase but cannot suppress it or "
             "the feeding/breeding phases that follow.",
    "FL030": "the accumulation top-up (replenish +1 wild boar on even "
             "rounds) is expressible on its own, but the card's other "
             "clause -- 'other players can only use that space with "
             "their first person' -- needs the same missing 'deny an "
             "opponent's normal placement' primitive as FL012, so the "
             "whole card stays unimplemented rather than silently "
             "dropping that clause.",
}


def _remove_one_animal(player, species):
    """Remove a single animal of `species` from cells then pets (mirrors
    engine._remove_animals for a count of 1; module-local since deck
    modules can't import each other's private helpers)."""
    for cell in player["cells"]:
        a = cell.get("animal")
        if a and a["type"] == species and a["count"] > 0:
            a["count"] -= 1
            if a["count"] <= 0:
                cell["animal"] = None
            return True
    pets = player.get("pets", {})
    if pets.get(species):
        pets[species] -= 1
        if pets[species] <= 0:
            del pets[species]
        return True
    return False


def _space_occupants(sp):
    return ([sp["occupied_by"]] if sp["occupied_by"] is not None else []) \
        + list(sp.get("extra_occupants", []))


# ── FL003 Belgian Shepherd ──────────────────────────────────────────────
# Req 1 sheep. "You may keep up to 2 Sheep on exactly 1 Unused space...
# The space still counts as unused" maps cleanly onto card-held storage
# (holds_animals never touches a farmyard cell, so "still counts as
# unused" is true for free -- no cell needs to be reserved at all). "You
# may not keep any animal other than the Belgian Shepherd in your home"
# (ruling: "Even if you have special Occupations or Improvements" -- i.e.
# this restriction is meant to hold unconditionally) is modeled as
# house_capacity=-1 (K120 House Goat's same "reduce the base 1 pet slot to
# 0" pattern; the same fidelity caveat K120 documents applies here too --
# this doesn't model overriding another card's house_capacity override,
# just the flat reduction).
def _belgian_shepherd_holds(state, player, inst):
    return {"types": {"sheep": 2}, "total": 2}

compendium_card(
    "FL003", prereq=(lambda s, p: animal_totals_of(p)["sheep"] >= 1, "1 sheep"),
    holds_animals=_belgian_shepherd_holds, house_capacity=-1,
)


# ── FL004 Educational Building ──────────────────────────────────────────
# See module docstring for the "3rd/4th family member" = "3rd/4th
# placement of a round" interpretation. A card_space, owner only, usable
# exactly twice ever (once at people_placed==2, once at people_placed==3,
# independently -- "3rd and/or 4th"); using it schedules 1 wood, then 1
# stone, alternating, on every remaining round (Well/K108-style
# scheduling, but ALL remaining rounds rather than a fixed list).
def _edu_building_usable(state, player, inst):
    pp = player["people_placed"]
    if pp == 2 and not inst["data"].get("used_3rd"):
        return True
    if pp == 3 and not inst["data"].get("used_4th"):
        return True
    return False

def _edu_building_resolve(state, player, inst, action, log):
    pp = player["people_placed"]
    if pp == 2:
        inst["data"]["used_3rd"] = True
    elif pp == 3:
        inst["data"]["used_4th"] = True
    else:
        raise ValueError("Educational Building is not usable right now")
    good = "wood"
    for r in range(state["round"] + 1, TOTAL_ROUNDS + 1):
        slot = state["round_goods"].setdefault(str(r), {}) \
            .setdefault(str(player["index"]), {})
        slot[good] = slot.get(good, 0) + 1
        good = "stone" if good == "wood" else "wood"
    log.append(f"{player['name']} alternates wood/stone on the remaining "
              "round spaces (Educational Building)")
    return {}

compendium_card(
    "FL004", card_space={"owner_only": True, "usable": _edu_building_usable,
                         "resolve": _edu_building_resolve},
)


# ── FL005 Brabant ────────────────────────────────────────────────────────
# "2 or 3 animal categories where you score 1 negative point" -- the
# sheep/boar/cattle SCORING_TABLES all score -1 exactly at count 0 (state.
# SCORING_TABLES), so this counts animal types the player owns zero of.
def _brabant_score(state, player, inst):
    totals = animal_totals_of(player)
    zero_count = sum(1 for t in ANIMAL_TYPES if totals[t] == 0)
    return 5 if zero_count in (2, 3) else 0

compendium_card("FL005", score_bonus=_brabant_score)


# ── FL006 Endive Field ───────────────────────────────────────────────────
compendium_card(
    "FL006", prereq=needs_occupations(2),
    field={"crops": ("vegetable",)},
    hooks={"play": on_play_gain({"vegetable": 1})},
)


# ── FL007 Diamond Trading Post ──────────────────────────────────────────
# 3 stone (from the general supply, not the player's own) sit "on this
# card"; a card_action moves any amount of it into the player's own
# supply at any time (free, no limit); at the start of each Harvest, if
# the player's own stone supply exceeds what's still on the card, they
# may return 1 of their own stone TO the card for an animal -- sheep,
# then wild boar, then cattle, on the 1st/2nd/3rd such return (capped at
# 3 total, matching the card's own 1st/2nd/3rd wording).
_DIAMOND_ANIMALS = ("sheep", "boar", "cattle")

def _diamond_trading_play(state, player, inst, ctx):
    inst["data"]["on_card"] = 3
    inst["data"]["returns"] = 0
    ctx["log"].append(f"{player['name']} places 3 stone (general supply) "
                      "on Diamond Trading Post")

def _diamond_trading_withdraw_available(state, player, inst):
    return inst["data"].get("on_card", 0) > 0

def _diamond_trading_withdraw_apply(state, player, inst, ctx):
    on_card = inst["data"].get("on_card", 0)
    n = (ctx.get("params") or {}).get("n", on_card)
    if not isinstance(n, int) or n <= 0 or n > on_card:
        raise ValueError("Diamond Trading Post: invalid amount (params.n)")
    inst["data"]["on_card"] = on_card - n
    player["resources"]["stone"] += n
    ctx["log"].append(f"{player['name']} moves {n} stone off Diamond "
                      "Trading Post into their supply")

def _diamond_trading_harvest_start(state, player, inst, ctx):
    returns = inst["data"].get("returns", 0)
    on_card = inst["data"].get("on_card", 0)
    if returns >= 3 or player["resources"]["stone"] <= on_card:
        return
    animal = _DIAMOND_ANIMALS[returns]
    prompt_choice(state, player, inst["id"],
                 f"Return 1 stone to Diamond Trading Post for 1 {animal}?",
                 ["Yes", "No"])

def _diamond_trading_resolve(state, player, inst, ctx):
    if ctx["index"] != 0:
        return
    returns = inst["data"].get("returns", 0)
    on_card = inst["data"].get("on_card", 0)
    if returns >= 3 or player["resources"]["stone"] <= on_card:
        return
    player["resources"]["stone"] -= 1
    inst["data"]["on_card"] = on_card + 1
    animal = _DIAMOND_ANIMALS[returns]
    inst["data"]["returns"] = returns + 1
    ctx["extra"][animal] = ctx["extra"].get(animal, 0) + 1
    ctx["log"].append(f"{player['name']} returns 1 stone to Diamond "
                      f"Trading Post, gets 1 {animal}")

compendium_card(
    "FL007", prereq=needs_occupations(3),
    hooks={"play": _diamond_trading_play,
          "harvest_start": _diamond_trading_harvest_start},
    resolve_choice=_diamond_trading_resolve,
    card_action={"available": _diamond_trading_withdraw_available,
                "apply": _diamond_trading_withdraw_apply,
                "description": "Move stone off Diamond Trading Post into "
                               "your own supply"},
)


# ── FL014 Jenever Distillery ─────────────────────────────────────────────
# On play, a mandatory choice of 1-4 wood moves onto the card (params.wood
# -- an open-ended amount, so the params channel, not a prompt); a
# card_action moves any of it back to the player's own supply at any time;
# each Harvest's field phase grants 1 food per wood still on the card,
# without removing it. A DB ruling tags "place 1 to 3 wood" (max 3, not
# 4) as "(FotM)" -- a Farmers of the Moor variant, out of scope per
# CARDS.md ("Farmers of the Moor... M-deck cards stay unimplemented");
# the base text's "1 to 4" is used here, not the FotM variant.
def _jenever_play(state, player, inst, ctx):
    n = (ctx.get("params") or {}).get("wood")
    if not isinstance(n, int) or not (1 <= n <= 4) \
            or player["resources"]["wood"] < n:
        raise ValueError("Jenever Distillery: choose 1-4 wood to place "
                        "(params.wood)")
    player["resources"]["wood"] -= n
    inst["data"]["wood"] = n
    ctx["log"].append(f"{player['name']} places {n} wood on Jenever "
                      "Distillery")

def _jenever_withdraw_available(state, player, inst):
    return inst["data"].get("wood", 0) > 0

def _jenever_withdraw_apply(state, player, inst, ctx):
    have = inst["data"].get("wood", 0)
    n = (ctx.get("params") or {}).get("n", have)
    if not isinstance(n, int) or n <= 0 or n > have:
        raise ValueError("Jenever Distillery: invalid amount (params.n)")
    inst["data"]["wood"] = have - n
    player["resources"]["wood"] += n
    ctx["log"].append(f"{player['name']} moves {n} wood off Jenever "
                      "Distillery into their supply")

def _jenever_harvest_field(state, player, inst, ctx):
    n = inst["data"].get("wood", 0)
    if n:
        player["resources"]["food"] += n
        ctx["log"].append(f"{player['name']}'s Jenever Distillery "
                          f"provides {n} food")
        fire_gained(state, player, {"food": n}, "card", ctx["log"])

compendium_card(
    "FL014", hooks={"play": _jenever_play,
                   "harvest_field": _jenever_harvest_field},
    card_action={"available": _jenever_withdraw_available,
                "apply": _jenever_withdraw_apply,
                "description": "Move wood off Jenever Distillery into "
                               "your own supply"},
)


# ── FL015 Courtyard Garden ────────────────────────────────────────────────
# Prereq "Wooden Hut" is read as "playable only while still a wood house"
# (a strategic commit-to-renovate-later card); the scoring bonus itself
# checks house_type at SCORING time, when the player may well have
# renovated to stone since.
def _courtyard_garden_score(state, player, inst):
    if player["house_type"] != "stone":
        return 0
    rooms = {i for i, c in enumerate(player["cells"]) if c["type"] == "room"}
    pasture = {i for pa in compute_pastures(player) for i in pa}
    count = 0
    for i, c in enumerate(player["cells"]):
        if c["type"] == "empty" and not c["stable"] and i not in pasture:
            if any(nb in rooms for nb in orthogonal_neighbors(i)):
                count += 1
    return count * 2

compendium_card(
    "FL015", prereq=(lambda s, p: p["house_type"] == "wood", "Wooden Hut"),
    score_bonus=_courtyard_garden_score,
)


# ── FL016 Janneken Pis ───────────────────────────────────────────────────
# "Your Well is an additional Action space for you only. Whenever you use
# your Well, you activate its effect again." Modeled as its own owner-only
# card_space whose resolve fn re-runs the Well's own scheduling logic
# (sub_actions.build_improvement's well branch, duplicated here since that
# logic is inlined at build time, not exposed as a callable) -- functionally
# identical to "use your Well again," any number of times per game (a DB
# ruling caps this at "3 times maximum," but that ruling is tagged
# "(FotM)" -- a Farmers of the Moor variant, out of scope; the base text
# has no cap). Per the ruling "Wells are cards whose name ends with
# 'Well'", the prereq/usable check isn't limited to the base game's own
# "well" major improvement -- it also recognizes any in-play card (minor
# or occupation, from any deck) whose printed name ends with "Well".
def _has_well(player):
    if "well" in player["improvements"]:
        return True
    return any(CARDS[i["id"]]["name"].endswith("Well")
              for i in player["minors"] + player["occupations"])

def _janneken_pis_usable(state, player, inst):
    return _has_well(player)

def _janneken_pis_resolve(state, player, inst, action, log):
    targets = list(range(state["round"] + 1,
                         min(TOTAL_ROUNDS, state["round"] + 5) + 1))
    for r in targets:
        slot = state["round_goods"].setdefault(str(r), {}) \
            .setdefault(str(player["index"]), {})
        slot["food"] = slot.get("food", 0) + 1
    if targets:
        log.append(f"{player['name']} activates their Well again "
                  "(Janneken Pis)")
    return {}

compendium_card(
    "FL016", prereq=(lambda s, p: _has_well(p), "a Well"),
    card_space={"name": "Use your Well again",
               "owner_only": True, "usable": _janneken_pis_usable,
               "resolve": _janneken_pis_resolve},
)


# ── FL017 Bobbin Table ───────────────────────────────────────────────────
# An owner-only card_space with no goods of its own; using it flips a flag
# that lets the owner place on occupied action spaces (occupied_ok) until
# the flag is cleared at the start of the next Harvest (harvest_start,
# which is always safe to react from -- see decks/GUIDE.md).
def _bobbin_table_resolve(state, player, inst, action, log):
    inst["data"]["active"] = True
    log.append(f"{player['name']} activates Bobbin Table: may use "
              "occupied action spaces until the next Harvest")
    return {}

def _bobbin_table_occupied_ok(state, player, inst, space):
    return inst["data"].get("active", False)

def _bobbin_table_harvest_start(state, player, inst, ctx):
    if inst["data"].get("active"):
        inst["data"]["active"] = False
        ctx["log"].append(f"{player['name']}'s Bobbin Table effect expires")

compendium_card(
    "FL017", card_space={"owner_only": True, "resolve": _bobbin_table_resolve},
    occupied_ok=_bobbin_table_occupied_ok,
    hooks={"harvest_start": _bobbin_table_harvest_start},
)


# ── FL018 Office ─────────────────────────────────────────────────────────
# Three independent "at any time" 1:1 exchanges, folded into one
# card_action (params.exchange selects which) -- "at any time" is modeled
# as "on your own turn, work phase or feeding" (card_action's normal
# availability window), the same idiom every other "at any time" instant
# exchange in this codebase uses (e.g. C050 Stable Yard). A DB ruling
# tags a cheaper "3C 2W 2R" cost as "(FotM)" -- out of scope; the base
# cost "4C 3W 2R" (already what parse_cost/compendium_card fills in by
# default) is used.
def _office_available(state, player, inst):
    return (player["resources"]["clay"] >= 1
            or player["resources"]["grain"] >= 1
            or animal_totals_of(player)["boar"] >= 1)

def _office_apply(state, player, inst, ctx):
    ex = (ctx.get("params") or {}).get("exchange")
    if ex == "clay_to_stone":
        if player["resources"]["clay"] < 1:
            raise ValueError("Office: not enough clay")
        player["resources"]["clay"] -= 1
        player["resources"]["stone"] += 1
        ctx["log"].append(f"{player['name']}'s Office exchanges 1 clay "
                          "for 1 stone")
    elif ex == "grain_to_vegetable":
        if player["resources"]["grain"] < 1:
            raise ValueError("Office: not enough grain")
        player["resources"]["grain"] -= 1
        player["resources"]["vegetable"] += 1
        ctx["log"].append(f"{player['name']}'s Office exchanges 1 grain "
                          "for 1 vegetable")
    elif ex == "boar_to_cattle":
        if animal_totals_of(player)["boar"] < 1:
            raise ValueError("Office: not enough wild boar")
        _remove_one_animal(player, "boar")
        ctx["extra"]["cattle"] = ctx["extra"].get("cattle", 0) + 1
        ctx["log"].append(f"{player['name']}'s Office exchanges 1 wild "
                          "boar for 1 cattle")
    else:
        raise ValueError("Office: choose an exchange (params.exchange -- "
                        "clay_to_stone, grain_to_vegetable, or "
                        "boar_to_cattle)")

compendium_card(
    "FL018", card_action={"available": _office_available,
                         "apply": _office_apply,
                         "description": "Exchange 1 clay<->1 stone, 1 "
                                        "grain<->1 vegetable, or 1 wild "
                                        "boar<->1 cattle"},
)


# ── FL019 Lantern Wonder ─────────────────────────────────────────────────
compendium_card(
    "FL019", prereq=exact_occupations(0),
    score_bonus=lambda s, p, i: -(len(p["hand_occupations"])
                                  + len(p["hand_minors"])),
)


# ── FL020 Love Garden ────────────────────────────────────────────────────
# At the end of a Work phase (returning_home -- fires once per player,
# after everyone's occupied_by for this round is still populated), if
# every currently-existing "Family growth" space (basic_wish/urgent_wish)
# and "Build room(s)" space (farm_expansion) is occupied, and the player
# has room for a newborn (sub_actions.can_family_growth already checks
# exactly "more rooms than people"), they get a free newborn.
_FAMILY_GROWTH_SPACES = ("basic_wish", "urgent_wish")
_BUILD_ROOM_SPACES = ("farm_expansion",)

def _love_garden_returning_home(state, player, inst, ctx):
    relevant = [sp for sp in state["action_spaces"]
               if sp["id"] in _FAMILY_GROWTH_SPACES
               or sp["id"] in _BUILD_ROOM_SPACES]
    if not relevant or any(sp["occupied_by"] is None for sp in relevant):
        return
    if not sub_actions.can_family_growth(state, player, require_room=True):
        return
    sub_actions.family_growth(state, player, ctx["log"], require_room=True)
    ctx["log"].append(f"{player['name']}'s Love Garden grants a newborn "
                      "family member")

compendium_card(
    "FL020",
    prereq=(lambda s, p: any(sp["id"] in _FAMILY_GROWTH_SPACES
                             for sp in s["action_spaces"]),
            '"Family growth" Action space in play'),
    hooks={"returning_home": _love_garden_returning_home},
)


# ── FL021 Lions Mound ────────────────────────────────────────────────────
# "Round number" of a space = its 1-based index in state["revealed"] (see
# module docstring); permanent spaces have none and are excluded from the
# comparison both as "your" space and as an opponent's. Fires from
# returning_home, which runs for every player before any player's
# occupied_by/extra_occupants reset for the round -- so this player's own
# hook still sees everyone else's placements for the round it's scoring.
_LIONS_MOUND_ROUND = {1: 13, 2: 11, 3: 9, 4: 7}

def _lions_mound_round_number(state, sid):
    if sid in state["revealed"]:
        return state["revealed"].index(sid) + 1
    return None

def _lions_mound_returning_home(state, player, inst, ctx):
    my_rounds = [rn for sid in ctx["spaces"]
                if (rn := _lions_mound_round_number(state, sid)) is not None]
    if not my_rounds:
        return
    my_min = min(my_rounds)
    for sp in state["action_spaces"]:
        rn = _lions_mound_round_number(state, sp["id"])
        if rn is None or rn >= my_min:
            continue
        if any(o != player["index"] for o in _space_occupants(sp)):
            return
    inst["data"]["bp"] = inst["data"].get("bp", 0) + 1
    ctx["log"].append(f"{player['name']}'s Lions Mound scores 1 bonus "
                      "point")

compendium_card(
    "FL021",
    prereq=(lambda s, p: s["round"] > _LIONS_MOUND_ROUND.get(s["player_count"], 7),
            "after round 13/11/9/7 in a 1/2/3/4-player game"),
    hooks={"returning_home": _lions_mound_returning_home},
    score_bonus=lambda s, p, i: i["data"].get("bp", 0),
)


# ── FL022 Corn Maze ──────────────────────────────────────────────────────
def _corn_maze_has_2x2(player):
    for r in range(ROWS - 1):
        for c in range(COLS - 1):
            idxs = (cell_index(r, c), cell_index(r, c + 1),
                   cell_index(r + 1, c), cell_index(r + 1, c + 1))
            if all(player["cells"][i]["type"] == "field"
                  and player["cells"][i]["crops"]
                  and player["cells"][i]["crops"]["type"] == "grain"
                  for i in idxs):
                return True
    return False

def _corn_maze_round_start(state, player, inst, ctx):
    if not _corn_maze_has_2x2(player):
        return
    player["resources"]["wood"] += 1
    player["resources"]["reed"] += 1
    ctx["log"].append(f"{player['name']}'s Corn Maze grants 1 wood and 1 "
                      "reed (2x2 grain fields)")
    fire_gained(state, player, {"wood": 1, "reed": 1}, "card", ctx["log"])

compendium_card(
    "FL022", prereq=needs_grain_field(2),
    hooks={"round_start": _corn_maze_round_start},
)


# ── FL023 Carrot Museum ──────────────────────────────────────────────────
# "At the end of Rounds 8, 10, and 12" -- see module docstring: modeled at
# the START of rounds 9, 11, 13 (round_start), since none of 8/10/12 is a
# harvest round, so nothing happens between one round ending and the next
# starting. A DB ruling caps this at "2 Stone and 2 Wood" (and bumps
# printed VP to 2) tagged "(FotM)" -- out of scope; the base text's
# uncapped per-field/per-supply amounts (and the DB's own parsed vp=1)
# are used.
_CARROT_MUSEUM_TRIGGER_ROUNDS = (9, 11, 13)

def _carrot_museum_round_start(state, player, inst, ctx):
    if state["round"] not in _CARROT_MUSEUM_TRIGGER_ROUNDS:
        return
    veg_fields = sum(1 for c in player["cells"]
                     if c["crops"] and c["crops"]["type"] == "vegetable")
    veg_fields += sum(1 for i in card_fields(player)
                      for crop in field_stacks(i)
                      if crop and crop["type"] == "vegetable")
    veg_supply = player["resources"]["vegetable"]
    gain = {}
    if veg_fields:
        gain["stone"] = veg_fields
    if veg_supply:
        gain["wood"] = veg_supply
    if gain:
        add_goods(player["resources"], gain)
        ctx["log"].append(f"{player['name']}'s Carrot Museum grants "
                          + ", ".join(f"{v} {k}" for k, v in gain.items()))
        fire_gained(state, player, gain, "card", ctx["log"])

compendium_card(
    "FL023", prereq=(lambda s, p: s["round"] <= 7, "Round 7 or before"),
    hooks={"round_start": _carrot_museum_round_start},
)


# ── FL024 Lover's Tryst ──────────────────────────────────────────────────
# "If you play this on the 'Starting player' [Meeting Place] Action space,
# instead of taking Starting player, you may take a Family growth action."
# Meeting Place's dispatch sets state["starting_player"] unconditionally
# BEFORE _play_minor's play hook fires, so the play hook reverts it (to
# state["round_first_player"], the value it had at the start of THIS round
# -- meeting_place is the only thing that ever changes starting_player
# mid-round) and grants a real family_growth instead, gated behind the
# same "you must have room" check sub_actions.can_family_growth already
# encodes. A play hook has no ctx["space_id"] (B059's documented gap),
# so "played on the Starting player space" is detected from state: the
# offer only appears while meeting_place is occupied by this player AND
# starting_player is this player -- i.e. the claim this play just made.
# Without that gate, playing this via the Major Improvement space after
# an OPPONENT took meeting_place would let the revert undo the
# opponent's legitimate starting-player claim. (Corner lenience: a
# player who took meeting_place earlier this round and later plays this
# card via another space is still offered the swap -- accepted, the
# swap is exactly the trade the card offers on meeting_place.) A DB
# ruling adds a "from Round 5 only" restriction tagged "(FotM)" -- out
# of scope; the base prereq (2 occupations, no round restriction) is
# used.
def _played_from_meeting_place(state, player):
    sp = next((s for s in state["action_spaces"]
              if s["id"] == "meeting_place"), None)
    return (sp is not None and sp["occupied_by"] == player["index"]
            and state["starting_player"] == player["index"])

def _lovers_tryst_play(state, player, inst, ctx):
    if _played_from_meeting_place(state, player) \
            and sub_actions.can_family_growth(state, player, require_room=True):
        prompt_choice(state, player, inst["id"],
                     "Lover's Tryst: take Family growth instead of "
                     "Starting player?",
                     ["Yes, family growth instead",
                      "No, keep Starting player"])

def _lovers_tryst_resolve(state, player, inst, ctx):
    if ctx["index"] != 0:
        return
    if not _played_from_meeting_place(state, player) \
            or not sub_actions.can_family_growth(state, player, require_room=True):
        return
    state["starting_player"] = state["round_first_player"]
    sub_actions.family_growth(state, player, ctx["log"], require_room=True)
    ctx["log"].append(f"{player['name']}'s Lover's Tryst takes Family "
                      "growth instead of Starting player")

compendium_card(
    "FL024", prereq=needs_occupations(2),
    hooks={"play": _lovers_tryst_play}, resolve_choice=_lovers_tryst_resolve,
)


# ── FL025 Cockaigne ──────────────────────────────────────────────────────
# "Once during the game... you may choose to not place any people in a
# round and instead receive 7 Bonus points." Combines the skip_turn +
# placement_blocked recipe (decks/GUIDE.md's Turn structure section): the
# first skip_turn offer of a round (people_placed == 0) both banks the
# achievement (after_skip) and, via placement_blocked, forbids every
# further placement THIS round -- matching "not place any people in a
# round" exactly, since skip_turn alone only ever defers one placement.
# The skip's own "gain" is an inert placeholder ({"food": 0} -- a
# non-empty dict so skip_turn's truthy-return contract is satisfied, but
# amount 0 credits nothing); the real reward is a score_bonus flag set by
# after_skip, since skip_turn's gain channel only carries goods, not
# points. "On your farmyard" (sheep/wild boar) is read as farmyard cells
# only, excluding house pets, since the card's own text separately says
# "in your personal supply" for the food/grain/vegetable half.
def _cockaigne_farmyard_animal(player, species):
    return sum(c["animal"]["count"] for c in player["cells"]
              if c.get("animal") and c["animal"]["type"] == species)

def _cockaigne_skip(state, player, inst):
    if inst["data"].get("used") or player["people_placed"] != 0:
        return None
    p = player
    if not (p["resources"]["food"] >= 1 and p["resources"]["grain"] >= 1
           and p["resources"]["vegetable"] >= 1
           and _cockaigne_farmyard_animal(p, "sheep") >= 1
           and _cockaigne_farmyard_animal(p, "boar") >= 1):
        return None
    return {"food": 0}

def _cockaigne_after_skip(state, player, inst, log):
    inst["data"]["used"] = True
    inst["data"]["blocked_round"] = state["round"]
    log.append(f"{player['name']}'s Cockaigne forgoes all placements "
              "this round for 7 bonus points")

def _cockaigne_blocked(state, player, inst):
    return inst["data"].get("blocked_round") == state["round"]

compendium_card(
    "FL025",
    prereq=(lambda s, p: len(p["occupations"]) <= 1, "at most 1 occupation"),
    skip_turn=_cockaigne_skip, after_skip=_cockaigne_after_skip,
    placement_blocked=_cockaigne_blocked,
    score_bonus=lambda s, p, i: 7 if i["data"].get("used") else 0,
)


# ── FL026 Speculoos Bakery ───────────────────────────────────────────────
# "Return the Guest token after using" is automatically true --
# grant_guest's counter is reset to 0 by every _start_round, so an unused
# (or used) guest never carries into the next round. A DB ruling caps the
# exchange at "3 times maximum" tagged "(FotM)" -- out of scope; the base
# text has no stated cap (offered every time the owner bakes).
def _speculoos_bake(state, player, inst, ctx):
    if player["resources"]["grain"] >= 1:
        prompt_choice(state, player, inst["id"],
                     "Speculoos Bakery: exchange 1 grain for a guest "
                     "token to place this round?",
                     ["Yes", "No"])

def _speculoos_resolve(state, player, inst, ctx):
    if ctx["index"] != 0 or player["resources"]["grain"] < 1:
        return
    player["resources"]["grain"] -= 1
    grant_guest(player, 1)
    ctx["log"].append(f"{player['name']}'s Speculoos Bakery exchanges 1 "
                      "grain for a guest token this round")

compendium_card(
    "FL026", hooks={"bake": _speculoos_bake},
    resolve_choice=_speculoos_resolve,
)


# ── FL028 Hash with Fries ────────────────────────────────────────────────
# "3 Bonus points" is the card's own EFFECT, not its printed VP (the DB's
# own vp=0 confirms no printed value) -- score_bonus (the Bonus scoring
# category), not points= (the Improvements category), is the right
# channel (CARDS.md section 6). Once played, the conversion has already
# happened unconditionally (prereq guarantees the goods existed at play
# time and nothing else can remove this card from play), so the bonus is
# a flat, unconditional 3 for as long as the card is in play.
def _hash_with_fries_play(state, player, inst, ctx):
    p = player
    if p["resources"]["grain"] < 1 or p["resources"]["vegetable"] < 1 \
            or animal_totals_of(p)["cattle"] < 1:
        raise ValueError("Hash with Fries: need 1 grain, 1 vegetable, "
                        "and 1 cattle")
    p["resources"]["grain"] -= 1
    p["resources"]["vegetable"] -= 1
    _remove_one_animal(p, "cattle")
    p["resources"]["food"] += 5
    ctx["log"].append(f"{player['name']} converts 1 grain, 1 vegetable, "
                      "and 1 cattle into 3 bonus points and 5 food "
                      "(Hash with Fries)")

compendium_card(
    "FL028",
    prereq=(lambda s, p: p["resources"]["grain"] >= 1
            and p["resources"]["vegetable"] >= 1
            and animal_totals_of(p)["cattle"] >= 1,
            "1 grain, 1 vegetable, and 1 cattle"),
    hooks={"play": _hash_with_fries_play},
    score_bonus=lambda s, p, i: 3,
)


# ── FL029 Bird Trap ──────────────────────────────────────────────────────
# 1 flat food per Harvest, plus a chosen still-planted field (farmyard
# cell or card field/stack) yields 1 extra crop -- prompted from
# harvest_field itself (mid-effect prompting is documented safe from any
# hook point; CARDS.md section 3), after the normal per-field harvest
# credit (and decrement) has already run for this Harvest.
def _bird_trap_targets(player):
    targets = []
    for i, c in enumerate(player["cells"]):
        if c["crops"] and c["crops"]["count"] > 0:
            targets.append(("cell", i))
    for inst in card_fields(player):
        for si, crop in enumerate(field_stacks(inst)):
            if crop and crop["count"] > 0:
                targets.append(("card", (inst["id"], si)))
    return targets

def _bird_trap_harvest_field(state, player, inst, ctx):
    player["resources"]["food"] += 1
    ctx["log"].append(f"{player['name']}'s Bird Trap grants 1 food")
    fire_gained(state, player, {"food": 1}, "card", ctx["log"])
    targets = _bird_trap_targets(player)
    if not targets:
        return
    options = []
    for kind, ident in targets:
        if kind == "cell":
            options.append(f"Cell {ident} "
                          f"({player['cells'][ident]['crops']['type']})")
        else:
            cid, si = ident
            options.append(f"{CARDS[cid]['name']} (stack {si})")
    options.append("Skip")
    prompt_choice(state, player, inst["id"],
                 "Bird Trap: harvest 1 additional resource from which "
                 "field?", options, data={"targets": targets})

def _bird_trap_resolve(state, player, inst, ctx):
    targets = ctx["data"]["targets"]
    if ctx["index"] >= len(targets):
        return
    kind, ident = targets[ctx["index"]]
    if kind == "cell":
        c = player["cells"][ident]
        if not c["crops"] or c["crops"]["count"] <= 0:
            return
        crop_type = c["crops"]["type"]
        c["crops"]["count"] -= 1
        if c["crops"]["count"] <= 0:
            c["crops"] = None
    else:
        cid, si = ident
        target_inst = next((i for i in card_fields(player) if i["id"] == cid),
                           None)
        crop = get_field_stack(target_inst, si) if target_inst else None
        if not crop or crop["count"] <= 0:
            return
        crop_type = crop["type"]
        crop["count"] -= 1
        set_field_stack(target_inst, si, crop if crop["count"] > 0 else None)
    player["resources"][crop_type] += 1
    ctx["log"].append(f"{player['name']}'s Bird Trap harvests 1 extra "
                      f"{crop_type}")
    fire_gained(state, player, {crop_type: 1}, "harvest", ctx["log"])

compendium_card(
    "FL029", prereq=needs_grain_field(1),
    hooks={"harvest_field": _bird_trap_harvest_field},
    resolve_choice=_bird_trap_resolve,
)
