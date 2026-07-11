"""Tests for the deck A minor improvements (server/agricola/decks/deck_a_minors.py)."""

import json
import os

import pytest

from server.agricola.engine import AgricolaEngine
from server.agricola import cards, sub_actions
from server.agricola.decks import deck_a_minors
from server.agricola.state import cell_edges

from tests.test_agricola import (
    make_state, place, give, give_card, put_in_play, add_space,
)

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                       "server", "agricola", "data", "compendium_cards.json")


@pytest.fixture
def engine():
    return AgricolaEngine()


def deck_a_minor_codes():
    with open(DB_PATH) as f:
        db = json.load(f)
    return sorted(c["code"] for c in db if c["deck"] == "A" and c["type"] == "minor")


IMPLEMENTED = sorted(cid for cid, spec in cards.CARDS.items()
                     if spec["deck"] == "A" and spec["type"] == "minor")


# ── Registration completeness ─────────────────────────────────────────

def test_registration_completeness():
    codes = set(deck_a_minor_codes())
    registered = {c for c in codes if c in cards.CARDS}
    unimplemented = {c for c in codes if c in deck_a_minors.UNIMPLEMENTED}
    assert registered | unimplemented == codes, \
        f"missing: {codes - registered - unimplemented}"
    assert registered & unimplemented == set()
    assert len(IMPLEMENTED) == 49
    assert len(deck_a_minors.UNIMPLEMENTED) == 11


# ── Smoke test: play every implemented card ───────────────────────────

def _resolve_prompts(engine, state, pidx):
    guard = 0
    while state["prompts"]:
        guard += 1
        assert guard < 20, "too many prompts, possible infinite loop"
        prompt = state["prompts"][0]
        pid = state["players"][prompt["player"]]["player_id"]
        if prompt["type"] == "accommodate":
            state = engine.apply_action(state, pid, {
                "kind": "accommodate", "discard": dict(prompt.get("gained", {})),
            }).new_state
        else:
            state = engine.apply_action(state, pid, {
                "kind": "choice", "index": 0,
            }).new_state
    return state


# Per-card setup: mutates state to satisfy the card's prereq (beyond cost,
# which is paid automatically from the spec) and returns any `params` the
# play action needs.
def _setup_A001(s, pidx):
    s["players"][pidx]["fences"] = sorted(cell_edges(4))
    return {"stable_cell": 4}

def _setup_A003(s, pidx):
    for cid in ("occ_woodcutter", "occ_cook", "occ_baker"):
        give_card(s, pidx, cid)
    return {}

def _setup_A008(s, pidx):
    put_in_play(s, pidx, "occ_woodcutter")
    put_in_play(s, pidx, "occ_cook")
    s["players"][pidx]["improvements"] = ["well", "joinery"]
    return {}

def _setup_A020(s, pidx):
    return {"cells": [0]}

def _setup_A021(s, pidx):
    put_in_play(s, pidx, "occ_woodcutter")
    return {}

def _setup_A027(s, pidx):
    s["players"][pidx]["improvements"] = ["fireplace_2"]
    return {}

def _setup_A029(s, pidx):
    put_in_play(s, pidx, "occ_woodcutter")
    put_in_play(s, pidx, "occ_cook")
    return {}

def _setup_A035(s, pidx):
    put_in_play(s, pidx, "occ_woodcutter")
    put_in_play(s, pidx, "occ_cook")
    return {}

def _setup_A040(s, pidx):
    for i in (0, 1, 2, 6, 7, 8, 12):
        s["players"][pidx]["cells"][i]["type"] = "field"
    return {}

def _setup_A043(s, pidx):
    s["players"][pidx]["pets"] = {"sheep": 1}
    return {}

def _setup_A046(s, pidx):
    s["players"][pidx]["fences"] = sorted(cell_edges(4))
    return {}

def _setup_A057(s, pidx):
    return {}

def _setup_A060(s, pidx):
    s["players"][pidx]["improvements"] = ["fireplace_2"]
    return {}

def _setup_A061(s, pidx):
    s["players"][pidx]["improvements"] = ["fireplace_2"]
    return {}

def _setup_A062(s, pidx):
    give(s, pidx, grain=2)
    return {}

def _setup_A065(s, pidx):
    for i in (0, 1, 2):
        s["players"][pidx]["cells"][i]["type"] = "field"
    return {}

def _setup_A070(s, pidx):
    for i in (0, 1, 2):
        s["players"][pidx]["cells"][i]["type"] = "field"
    return {}

def _setup_A073(s, pidx):
    s["players"][pidx]["fences"] = sorted(cell_edges(4))
    return {}

def _setup_A082(s, pidx):
    for cid in ("occ_woodcutter", "occ_cook", "occ_baker"):
        put_in_play(s, pidx, cid)
    return {}

def _setup_A084(s, pidx):
    for i in (0, 1):
        s["players"][pidx]["cells"][i]["type"] = "field"
    return {}

def _setup_A052(s, pidx):
    s["round"] = 7
    return {}

SETUP = {
    "A001": _setup_A001, "A003": _setup_A003, "A008": _setup_A008,
    "A020": _setup_A020, "A021": _setup_A021, "A027": _setup_A027,
    "A029": _setup_A029, "A035": _setup_A035, "A040": _setup_A040,
    "A052": _setup_A052,
    "A043": _setup_A043, "A046": _setup_A046, "A057": _setup_A057,
    "A060": _setup_A060, "A061": _setup_A061, "A062": _setup_A062,
    "A065": _setup_A065, "A070": _setup_A070, "A073": _setup_A073,
    "A082": _setup_A082, "A084": _setup_A084,
}


@pytest.mark.parametrize("cid", IMPLEMENTED)
def test_smoke_play_every_card(engine, cid):
    s = make_state(engine, 2, seed=7)
    pidx = s["current_player"]
    p = s["players"][pidx]
    # First alternative of an "or" cost (GUIDE.md ground rule 1) --
    # headroom below covers affording any alternative regardless.
    for good, amount in sub_actions.cost_alternatives(cards.CARDS[cid]["cost"])[0].items():
        give(s, pidx, **{good: amount})
    give(s, pidx, food=20, wood=20, clay=20, reed=20, stone=20, grain=20,
        vegetable=20)  # headroom for any optional choice paths taken
    params = SETUP.get(cid, lambda s, pidx: {})(s, pidx)
    give_card(s, pidx, cid)
    add_space(s, "major_improvement", "Major Improvement")
    s = place(engine, s, {"kind": "place", "space": "major_improvement",
                          "minor": {"card": cid, "params": params}})
    s = _resolve_prompts(engine, s, pidx)
    p = s["players"][pidx]
    assert any(i["id"] == cid for i in p["minors"]), \
        f"{cid} did not end up in play"


# ── Targeted effect tests ─────────────────────────────────────────────

def test_shelter_free_stable(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["fences"] = sorted(cell_edges(4))
    give_card(s, first, "A001")
    add_space(s, "major_improvement", "Major Improvement")
    s = place(engine, s, {"kind": "place", "space": "major_improvement",
                          "minor": {"card": "A001",
                                    "params": {"stable_cell": 4}}})
    p = s["players"][first]
    assert p["cells"][4]["stable"] is True
    assert p["resources"]["wood"] == 0  # free


def test_paper_knife_plays_free_occupation(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["hand_occupations"] = []  # start from a clean, known hand
    for cid in ("occ_woodcutter", "occ_cook", "occ_baker"):
        give_card(s, first, cid)
    give_card(s, first, "A003")
    give(s, first, wood=1)
    add_space(s, "major_improvement", "Major Improvement")
    s = place(engine, s, {"kind": "place", "space": "major_improvement",
                          "minor": {"card": "A003"}})
    p = s["players"][first]
    assert p["occs_played"] == 1
    assert len(p["occupations"]) == 1
    assert len(p["hand_occupations"]) == 2


def test_baseboards_wood_per_room(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give(s, first, food=2)
    give_card(s, first, "A004")
    add_space(s, "major_improvement", "Major Improvement")
    wood = s["players"][first]["resources"]["wood"]
    s = place(engine, s, {"kind": "place", "space": "major_improvement",
                          "minor": {"card": "A004"}})
    # 2 rooms, not more rooms than people (2 == 2) -> +2 wood, no bonus.
    assert s["players"][first]["resources"]["wood"] == wood + 2


def test_baseboards_alt_cost_grain(engine):
    """Cost "2F or 1 Grain" -- paying the non-first (grain) alternative
    via cost_option still plays the card and grants the same effect
    (and leaves the food alternative's food untouched)."""
    s = make_state(engine, 2)
    first = s["current_player"]
    give(s, first, grain=1)
    give_card(s, first, "A004")
    add_space(s, "major_improvement", "Major Improvement")
    wood = s["players"][first]["resources"]["wood"]
    food = s["players"][first]["resources"]["food"]
    s = place(engine, s, {"kind": "place", "space": "major_improvement",
                          "minor": {"card": "A004", "cost_option": 1}})
    p = s["players"][first]
    assert p["resources"]["grain"] == 0
    assert p["resources"]["food"] == food
    assert p["resources"]["wood"] == wood + 2


def test_storage_barn_per_improvement(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["improvements"] = ["joinery", "well"]
    give_card(s, first, "A006")
    add_space(s, "major_improvement", "Major Improvement")
    s = place(engine, s, {"kind": "place", "space": "major_improvement",
                          "minor": {"card": "A006"}})
    p = s["players"][first]
    assert p["resources"]["wood"] == 1
    assert p["resources"]["stone"] == 1
    assert p["resources"]["clay"] == 0  # no Pottery
    assert p["resources"]["reed"] == 0  # no Basketmaker


def test_gardeners_knife_field_gains(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["cells"][0]["type"] = "field"
    p["cells"][0]["crops"] = {"type": "grain", "count": 1}
    p["cells"][1]["type"] = "field"
    p["cells"][1]["crops"] = {"type": "vegetable", "count": 1}
    give(s, first, wood=1)
    give_card(s, first, "A007")
    food_before = p["resources"]["food"]
    grain_before = p["resources"]["grain"]
    add_space(s, "major_improvement", "Major Improvement")
    s = place(engine, s, {"kind": "place", "space": "major_improvement",
                          "minor": {"card": "A007"}})
    p = s["players"][first]
    assert p["resources"]["food"] == food_before + 1
    assert p["resources"]["grain"] == grain_before + 1


def test_food_basket_prereq_and_gain(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give(s, first, reed=1)
    give_card(s, first, "A008")
    add_space(s, "major_improvement", "Major Improvement")
    with pytest.raises(ValueError):
        place(engine, s, {"kind": "place", "space": "major_improvement",
                          "minor": {"card": "A008"}})
    put_in_play(s, first, "occ_woodcutter")
    put_in_play(s, first, "occ_cook")
    s["players"][first]["improvements"] = ["well", "joinery"]
    s = place(engine, s, {"kind": "place", "space": "major_improvement",
                          "minor": {"card": "A008"}})
    p = s["players"][first]
    assert p["resources"]["grain"] == 1
    assert p["resources"]["vegetable"] == 1


def test_renovation_company_free_renovate(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give(s, first, wood=4)
    give_card(s, first, "A013")
    add_space(s, "major_improvement", "Major Improvement")
    s = place(engine, s, {"kind": "place", "space": "major_improvement",
                          "minor": {"card": "A013"}})
    p = s["players"][first]
    s = engine.apply_action(s, p["player_id"], {"kind": "choice", "index": 0}).new_state
    p = s["players"][first]
    assert p["resources"]["clay"] == 3  # got 3 clay, none spent on renovation
    assert p["house_type"] == "clay"


def test_carpenters_axe_card_action(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "A015")
    give(s, first, wood=7)
    inst = s["players"][first]["occupations"] + s["players"][first]["minors"]
    assert cards.card_actions(s, s["players"][first])
    s = engine.apply_action(s, s["players"][first]["player_id"], {
        "kind": "card_action", "card": "A015", "params": {"cell": 0},
    }).new_state
    p = s["players"][first]
    assert p["cells"][0]["stable"] is True
    assert p["resources"]["wood"] == 6


def test_double_turn_plow(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give(s, first, grain=1, food=1)
    food_before = s["players"][first]["resources"]["food"]
    give_card(s, first, "A020")
    add_space(s, "major_improvement", "Major Improvement")
    s = place(engine, s, {"kind": "place", "space": "major_improvement",
                          "minor": {"card": "A020", "params": {"cells": [0, 1]}}})
    p = s["players"][first]
    assert p["cells"][0]["type"] == "field"
    assert p["cells"][1]["type"] == "field"
    assert p["resources"]["food"] == food_before  # paid 1, refunded 1 (round <= 3)


def test_family_friendly_home_grants_growth(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "A021")
    put_in_play(s, first, "occ_woodcutter")
    give(s, first, wood=10, reed=4)
    people = s["players"][first]["people_total"]
    food = s["players"][first]["resources"]["food"]
    s = place(engine, s, {"kind": "place", "space": "farm_expansion",
                          "rooms": [0, 1]})
    p = s["players"][first]
    # 4 rooms > 2 people -> Family Growth + 1 food.
    assert p["people_total"] == people + 1
    assert p["newborns"] == 1
    assert p["resources"]["food"] == food + 1


def test_stone_company_card_action(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "A023")
    add_space(s, "western_quarry", "Western Quarry", acc=True, supply={"stone": 1})
    s = place(engine, s, {"kind": "place", "space": "western_quarry"})
    p = s["players"][first]
    assert p["resources"]["stone"] == 1
    inst = next(i for i in p["minors"] if i["id"] == "A023")
    assert inst["data"]["pending"] is True
    give(s, first, wood=1, stone=3)
    add_space(s, "major_improvement", "Major Improvement")
    # It's the other player's turn now; let them act so it becomes `first`'s
    # turn again (card_action requires state["current_player"] == actor).
    s = place(engine, s, {"kind": "place", "space": "day_laborer"})
    p = s["players"][first]
    s = engine.apply_action(s, p["player_id"], {
        "kind": "card_action", "card": "A023",
        "params": {"improvement": "well"},
    }).new_state
    p = s["players"][first]
    assert "well" in p["improvements"]
    inst = next(i for i in p["minors"] if i["id"] == "A023")
    assert inst["data"]["pending"] is False


def test_oven_site_builds_clay_oven(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["improvements"] = ["fireplace_2"]
    give(s, first, clay=1, stone=1)
    give_card(s, first, "A027")
    add_space(s, "major_improvement", "Major Improvement")
    s = place(engine, s, {"kind": "place", "space": "major_improvement",
                          "minor": {"card": "A027"}})
    s = engine.apply_action(s, p["player_id"], {"kind": "choice", "index": 0}).new_state
    p = s["players"][first]
    assert "clay_oven" in p["improvements"]
    assert p["resources"]["clay"] == 0 and p["resources"]["stone"] == 0
    assert p["resources"]["wood"] == 2


def test_ale_benches_round_start_prompt(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    put_in_play(s, first, "A029")
    give(s, first, grain=1)
    grain_before = s["players"][first]["resources"]["grain"]
    other_food = s["players"][other]["resources"]["food"]
    # None of these grant food, so `other`'s food only changes via Ale-Benches.
    for sp in ["forest", "clay_pit", "reed_bank", "grain_seeds"]:
        s = place(engine, s, {"kind": "place", "space": sp})
    assert s["round"] == 2
    p = s["players"][first]
    assert p["resources"]["grain"] == grain_before - 1
    assert s["players"][other]["resources"]["food"] == other_food + 1
    inst = next(i for i in p["minors"] if i["id"] == "A029")
    assert inst["data"]["bonus"] == 1


def test_baking_sheet_grain_exchange(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "A030")
    p["improvements"].append("fireplace_2")
    give(s, first, grain=3)
    add_space(s, "grain_utilization", "Grain Utilization")
    food_before = p["resources"]["food"]
    s = place(engine, s, {"kind": "place", "space": "grain_utilization",
                          "bake": {"fireplace_2": 2}})
    p = s["players"][first]
    # 2 grain baked at 2 food/grain = 4, plus Baking Sheet: -1 grain, +2 food.
    assert p["resources"]["food"] == food_before + 4 + 2
    assert p["resources"]["grain"] == 0
    inst = next(i for i in p["minors"] if i["id"] == "A030")
    assert inst["data"]["bonus"] == 1


def test_debt_security_score_bonus():
    from server.agricola.state import create_player
    p = create_player(0, "p", "P")
    p["improvements"] = ["well", "joinery", "pottery"]
    inst = cards.new_instance("A031")
    p["minors"].append(inst)
    # 15 cells, 2 rooms used -> 13 unused, well above 3 improvements.
    assert cards.score_bonuses({"players": [p]}, p) == 3


def test_swimming_class_returning_home_bonus(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "A035")
    p = s["players"][first]
    p["newborns"] = 2
    add_space(s, "fishing_dup", "Fishing dup")  # not used; real fishing exists
    s = place(engine, s, {"kind": "place", "space": "fishing"})
    for sp in ["day_laborer", "grain_seeds", "meeting_place"]:
        s = place(engine, s, {"kind": "place", "space": sp})
    assert s["round"] == 2
    p = s["players"][first]
    inst = next(i for i in p["minors"] if i["id"] == "A035")
    assert inst["data"]["bonus"] == 4  # 2 bonus points per newborn


def test_facades_carving_exchange(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["resources"]["wood"] = 3
    s["harvest_index"] = 2
    give(s, first, clay=2, food=5)
    give_card(s, first, "A036")
    add_space(s, "major_improvement", "Major Improvement")
    s = place(engine, s, {"kind": "place", "space": "major_improvement",
                          "minor": {"card": "A036"}})
    # Options are "0".."2" (min(harvest_index=2, food available)); pick "0".
    s = engine.apply_action(s, p["player_id"], {"kind": "choice", "index": 0}).new_state
    p = s["players"][first]
    inst = next(i for i in p["minors"] if i["id"] == "A036")
    assert inst["data"].get("bonus", 0) == 0  # index 0 -> "0" food exchanged


def test_potters_yard_redeems_on_room_build(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    for i in (0, 1, 2, 6, 7, 8, 12):
        s["players"][first]["cells"][i]["type"] = "field"
    give(s, first, wood=1, reed=1)
    give_card(s, first, "A040")
    add_space(s, "major_improvement", "Major Improvement")
    s = place(engine, s, {"kind": "place", "space": "major_improvement",
                          "minor": {"card": "A040"}})
    p = s["players"][first]
    inst = next(i for i in p["minors"] if i["id"] == "A040")
    marked_before = len(inst["data"]["marked"])
    assert marked_before > 0
    target = 11  # the only cell left both unmarked-as-field and room-adjacent
    assert target in inst["data"]["marked"]
    give(s, first, wood=10, reed=10)
    food_before = p["resources"]["food"]
    s["current_player"] = first  # it's `first`'s 2nd placement this round
    s = place(engine, s, {"kind": "place", "space": "farm_expansion",
                          "rooms": [target]})
    p = s["players"][first]
    inst = next(i for i in p["minors"] if i["id"] == "A040")
    assert target not in inst["data"]["marked"]
    assert p["resources"]["food"] == food_before + 2


def test_vegetable_slicer_on_upgrade(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["improvements"].append("fireplace_2")
    give(s, first, clay=4)
    put_in_play(s, first, "A041")
    add_space(s, "major_improvement", "Major Improvement")
    wood_before = p["resources"]["wood"]
    veg_before = p["resources"]["vegetable"]
    s = place(engine, s, {"kind": "place", "space": "major_improvement",
                          "improvement": "cooking_hearth_4", "upgrade": True})
    p = s["players"][first]
    assert p["resources"]["wood"] == wood_before + 2
    assert p["resources"]["vegetable"] == veg_before + 1


def test_farmyard_manure_schedules_wood_on_stable(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    s["players"][first]["pets"] = {"sheep": 1}
    put_in_play(s, first, "A043")
    give(s, first, wood=2)
    s = place(engine, s, {"kind": "place", "space": "farm_expansion",
                          "stables": [0]})
    for r in ("2", "3", "4"):
        assert s["round_goods"][r][str(first)]["food"] == 1


def test_throwing_axe_needs_boar_market_stocked(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "A052")
    s["round"] = 7
    add_space(s, "pig_market", "Pig Market", acc=True, supply={"boar": 1})
    food_before = s["players"][first]["resources"]["food"]
    s = place(engine, s, {"kind": "place", "space": "forest"})
    assert s["players"][first]["resources"]["food"] == food_before + 2


def test_milking_parlor_animal_tiers(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["pets"] = {"sheep": 3, "cattle": 1}
    give(s, first, wood=2)
    give_card(s, first, "A057")
    add_space(s, "major_improvement", "Major Improvement")
    food_before = p["resources"]["food"]
    s = place(engine, s, {"kind": "place", "space": "major_improvement",
                          "minor": {"card": "A057"}})
    p = s["players"][first]
    # 3 sheep -> 3 food, 1 cattle -> 2 food.
    assert p["resources"]["food"] == food_before + 5


def test_oriental_fireplace_cook_and_return(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["improvements"] = ["fireplace_2"]
    give_card(s, first, "A060")
    add_space(s, "major_improvement", "Major Improvement")
    s = place(engine, s, {"kind": "place", "space": "major_improvement",
                          "minor": {"card": "A060"}})
    p = s["players"][first]
    assert "fireplace_2" not in p["improvements"]
    assert "fireplace_2" in s["available_improvements"]
    inst = next(i for i in p["minors"] if i["id"] == "A060")
    assert cards.spec(inst)["cook"]["cattle"] == 5


def test_beer_keg_card_action_tiers(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "A062")
    p = s["players"][first]
    give(s, first, grain=3, food=10)
    food_before = p["resources"]["food"]
    s["phase"] = "feeding"
    for pl in s["players"]:
        pl["fed"] = False
    s = engine.apply_action(s, p["player_id"], {
        "kind": "card_action", "card": "A062", "params": {"grain": 3},
    }).new_state
    p = s["players"][first]
    assert p["resources"]["grain"] == 0
    assert p["resources"]["food"] == food_before + 3
    inst = next(i for i in p["minors"] if i["id"] == "A062")
    assert inst["data"]["bonus"] == 2
    # Using it again this harvest is not available.
    assert not cards.card_actions(s, p)


def test_agricultural_fertilizers_bonus_sow(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["fences"] = sorted(cell_edges(4))
    put_in_play(s, first, "A073")
    give(s, first, wood=10, reed=4, grain=1)
    p["cells"][0]["type"] = "field"
    # Cells 6 and 11 are the two starting-room-adjacent empty spaces.
    s = place(engine, s, {"kind": "place", "space": "farm_expansion",
                          "rooms": [6, 11]})
    p = s["players"][first]
    s["current_player"] = first  # still `first`'s turn (2nd placement)
    assert cards.card_actions(s, p)
    s = engine.apply_action(s, p["player_id"], {
        "kind": "card_action", "card": "A073",
        "params": {"cell": 0, "crop": "grain"},
    }).new_state
    p = s["players"][first]
    assert p["cells"][0]["crops"] == {"type": "grain", "count": 3}


def test_interim_storage_stash_and_release(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "A081")
    add_space(s, "clay_pit", "Clay Pit", acc=True, supply={"clay": 1})
    s = place(engine, s, {"kind": "place", "space": "clay_pit"})
    inst = next(i for i in s["players"][first]["minors"] if i["id"] == "A081")
    assert inst["data"]["stash"] == {"wood": 1}
    s["round"] = 6
    log = []
    engine._start_round(s, log)
    assert s["round"] == 7
    p = s["players"][first]
    assert p["resources"]["wood"] >= 1
    inst = next(i for i in p["minors"] if i["id"] == "A081")
    assert inst["data"]["stash"] == {}


def test_work_certificate_draws_stocked_space(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    for cid in ("occ_woodcutter", "occ_cook", "occ_baker"):
        put_in_play(s, first, cid)
    put_in_play(s, first, "A082")
    add_space(s, "western_quarry", "Western Quarry", acc=True, supply={"stone": 4})
    s = place(engine, s, {"kind": "place", "space": "day_laborer"})
    prompt = s["prompts"][0]
    assert prompt["type"] == "choice"
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {"kind": "choice", "index": 0}).new_state
    p = s["players"][first]
    assert p["resources"]["stone"] == 1
    quarry = next(sp for sp in s["action_spaces"] if sp["id"] == "western_quarry")
    assert quarry["supply"]["stone"] == 3


def test_shaving_horse_mandatory_exchange_at_7_wood(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "A048")
    give(s, first, wood=6)
    food_before = p["resources"]["food"]
    add_space(s, "wood_test_a048", "Wood Test", acc=True, supply={"wood": 1})
    s = place(engine, s, {"kind": "place", "space": "wood_test_a048"})
    p = s["players"][first]
    # 6 + 1 = 7 wood triggers the mandatory (no-prompt) exchange.
    assert s["prompts"] == []
    assert p["resources"]["wood"] == 6
    assert p["resources"]["food"] == food_before + 3


def test_shaving_horse_optional_exchange_at_5_wood(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "A048")
    give(s, first, wood=4)
    add_space(s, "wood_test_a048b", "Wood Test", acc=True, supply={"wood": 1})
    s = place(engine, s, {"kind": "place", "space": "wood_test_a048b"})
    p = s["players"][first]
    # 4 + 1 = 5 wood only offers the exchange.
    assert len(s["prompts"]) == 1
    assert p["resources"]["wood"] == 5
    food_before = p["resources"]["food"]
    pid = p["player_id"]
    s = engine.apply_action(s, pid, {"kind": "choice", "index": 0}).new_state
    p = s["players"][first]
    assert p["resources"]["wood"] == 4
    assert p["resources"]["food"] == food_before + 3


def test_barley_mill_pays_per_grain_field_harvested(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "A064")
    p["cells"][0]["type"] = "field"
    p["cells"][0]["crops"] = {"type": "grain", "count": 2}
    p["cells"][1]["type"] = "field"
    p["cells"][1]["crops"] = {"type": "grain", "count": 1}
    p["cells"][2]["type"] = "field"
    p["cells"][2]["crops"] = {"type": "vegetable", "count": 1}
    food_before = p["resources"]["food"]
    log = []
    engine._start_harvest(s, log)
    p = s["players"][first]
    # 2 grain field tiles harvested (vegetable field doesn't count).
    assert p["resources"]["food"] == food_before + 2


def test_barley_mill_alt_cost_stone(engine):
    """Cost "1W 4C/2S" (1 wood plus 4 clay, OR 1 wood plus 2 stone) --
    paying the non-first (wood+stone) alternative via cost_option still
    plays the card."""
    s = make_state(engine, 2)
    first = s["current_player"]
    give(s, first, wood=1, stone=2)
    give_card(s, first, "A064")
    add_space(s, "major_improvement", "Major Improvement")
    s = place(engine, s, {"kind": "place", "space": "major_improvement",
                          "minor": {"card": "A064", "cost_option": 1}})
    p = s["players"][first]
    assert p["resources"]["wood"] == 0 and p["resources"]["stone"] == 0
    assert p["resources"]["clay"] == 0
    assert any(i["id"] == "A064" for i in p["minors"])


def test_silage_breeds_from_returning_home(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["cells"][0]["type"] = "field"
    p["cells"][1]["type"] = "field"
    # A 1-cell pasture holds 2 sheep, so there's room for 1 more via breeding.
    p["fences"] = sorted(cell_edges(4))
    p["cells"][4]["animal"] = {"type": "sheep", "count": 1}
    give(s, first, grain=1)
    put_in_play(s, first, "A084")
    s["round"] = 3  # round 3 is not a harvest round
    log = []
    engine._start_round(s, log)
    p = s["players"][first]
    assert p["resources"]["grain"] == 0
    assert p["cells"][4]["animal"]["count"] == 2


def test_carpenters_hammer_batch_discount(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "A014")
    # >= 2 wood rooms at once: a flat 2-wood/2-reed discount off the batch
    # total (not scaled per room).
    cost = cards.modified_cost(s, p, "room", {"wood": 10, "reed": 4},
                               {"count": 2})
    assert cost == {"wood": 8, "reed": 2}
    # A single room in the batch gets no discount at all.
    cost1 = cards.modified_cost(s, p, "room", {"wood": 5, "reed": 2},
                                {"count": 1})
    assert cost1 == {"wood": 5, "reed": 2}
    # Clay house: 3 clay / 2 reed off the batch total.
    p["house_type"] = "clay"
    cost_clay = cards.modified_cost(s, p, "room", {"clay": 10, "reed": 4},
                                    {"count": 2})
    assert cost_clay == {"clay": 7, "reed": 2}
