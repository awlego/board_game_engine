"""Tests for the deck C occupation module
(server/agricola/decks/deck_c_occupations.py)."""

import pytest

from server.agricola.engine import AgricolaEngine
from server.agricola import cards
from server.agricola.decks import deck_c_occupations
from server.agricola.state import cell_edges, compute_pastures, pasture_capacity
from server.agricola.scoring import score_player

from test_agricola import (
    make_state, give, give_card, put_in_play, add_space, place,
)


@pytest.fixture
def engine():
    return AgricolaEngine()


# ── Registration completeness ────────────────────────────────────────

def test_registration_completeness():
    db_codes = {c["code"] for c in cards.compendium().values()
               if c["deck"] == "C" and c["type"] == "occupation"}
    registered = {cid for cid in cards.CARDS if cid in db_codes}
    unimplemented = set(deck_c_occupations.UNIMPLEMENTED)
    assert unimplemented <= db_codes
    assert registered & unimplemented == set()
    assert registered | unimplemented == db_codes


# ── Smoke test: every implemented card gets played once ──────────────

ALL_C_OCCS = sorted(cid for cid, spec in cards.CARDS.items()
                    if spec["deck"] == "C" and spec["type"] == "occupation")

# Extra resources some cards' play hooks need to run their "happy path"
# instead of silently no-op'ing (e.g. Lover/Game Catcher pay for family
# growth / animals from *inside* the play hook, since occupation
# prereqs are never engine-enforced — see the comments in the deck
# module next to C127/C165).
_EXTRA_RESOURCES = {
    "C095": {"stone": 1, "reed": 1},
    "C127": {"food": 13},
    "C143": {"food": 1},
    "C165": {"food": 6},
}


def _prep(state, pidx, cid):
    extra = _EXTRA_RESOURCES.get(cid)
    if extra:
        give(state, pidx, **extra)
    if cid == "C156":
        add_space(state, "cattle_market", "Cattle Market", acc=True,
                  supply={"cattle": 1})


def _resolve_all_prompts(engine, state):
    while True:
        pid = engine.get_waiting_for(state)
        if not pid:
            break
        acts = engine.get_valid_actions(state, pid[0])
        choice = next((a for a in acts if a["kind"] == "choice"), None)
        if choice:
            state = place(engine, state, {"kind": "choice", "index": 0})
            continue
        acc = next((a for a in acts if a["kind"] == "accommodate"), None)
        if acc:
            state = place(engine, state,
                         {"kind": "accommodate", "placements": [],
                          "discard": dict(acc["gained"])})
            continue
        break
    return state


@pytest.mark.parametrize("cid", ALL_C_OCCS)
def test_smoke_play_every_card(engine, cid):
    s = make_state(engine, 4)
    first = s["current_player"]
    _prep(s, first, cid)
    give_card(s, first, cid)
    s = place(engine, s, {"kind": "place", "space": "lessons", "card": cid})
    s = _resolve_all_prompts(engine, s)
    p = s["players"][first]
    assert cid not in p["hand_occupations"]
    assert any(i["id"] == cid for i in p["occupations"])


def _set_supply(state, sid, goods):
    """Set the supply of an action space that already exists by default
    (e.g. permanent spaces like Forest/Fishing) — add_space would create
    a harmless-looking but WRONG duplicate entry, since the engine's
    lookups (_space, and any hook doing next(... if id==sid)) always
    resolve to the first matching entry, i.e. the original."""
    sp = next(s for s in state["action_spaces"] if s["id"] == sid)
    sp["supply"] = dict(goods)
    return sp


def _cycle_to(engine, state, first):
    """Place dummy accumulation-space actions for whichever player isn't
    `first` until it becomes `first`'s turn again (placements rotate one
    person at a time, not all of one player's people before the next)."""
    i = 0
    while state["current_player"] != first:
        sid = f"_cycle_dummy_{i}"
        add_space(state, sid, sid, acc=True, supply={"wood": 1})
        state = place(engine, state, {"kind": "place", "space": sid})
        i += 1
    return state


# ── Targeted effect tests ────────────────────────────────────────────

def test_plow_hero_extra_plow_first_placement(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "C091")
    give(s, first, food=1)
    food_before = s["players"][first]["resources"]["food"]
    add_space(s, "farmland", "Farmland")
    add_space(s, "dummy", "Dummy", acc=True, supply={"wood": 1})
    # first's turn 1: plow via Farmland (their first placement this round).
    s = place(engine, s, {"kind": "place", "space": "farmland", "cell": 1})
    p = s["players"][first]
    assert p["cells"][1]["type"] == "field"
    # other player's turn 1.
    s = place(engine, s, {"kind": "place", "space": "dummy"})
    # Back to first (about to place their 2nd person): the card action is
    # now available, usable before submitting that placement.
    p = s["players"][first]
    acts = engine.get_valid_actions(s, p["player_id"])
    assert any(a["kind"] == "card_action" for a in acts)
    s = engine.apply_action(s, p["player_id"], {
        "kind": "card_action", "card": "C091", "params": {"cell": 0}}).new_state
    p = s["players"][first]
    assert p["cells"][0]["type"] == "field"
    assert p["resources"]["food"] == food_before - 1  # paid 1 food


def test_plow_hero_not_available_on_second_placement(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "C091")
    give(s, first, food=2)
    add_space(s, "farmland", "Farmland")
    add_space(s, "dummy", "Dummy", acc=True, supply={"wood": 1})
    add_space(s, "dummy2", "Dummy 2", acc=True, supply={"wood": 1})
    # first's turn 1 is NOT Farmland/Cultivation.
    s = place(engine, s, {"kind": "place", "space": "dummy"})
    s = place(engine, s, {"kind": "place", "space": "dummy2"})
    # first's turn 2: uses Farmland, but it is not their *first* placement.
    s = place(engine, s, {"kind": "place", "space": "farmland", "cell": 0})
    p = s["players"][first]
    acts = engine.get_valid_actions(s, p["player_id"])
    assert not any(a["kind"] == "card_action" for a in acts)


def test_stable_cleaner_builds_without_placing_person(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "C094")
    p = s["players"][first]
    p["resources"]["wood"] = 1
    p["resources"]["food"] = 1
    placed_before = p["people_placed"]
    s = engine.apply_action(s, p["player_id"], {
        "kind": "card_action", "card": "C094", "params": {"cells": [0]}}).new_state
    p = s["players"][first]
    assert p["cells"][0]["stable"]
    assert p["resources"]["wood"] == 0 and p["resources"]["food"] == 0
    assert p["people_placed"] == placed_before  # no person consumed


def test_basket_weaver_builds_basketmakers_workshop(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give(s, first, stone=1, reed=1)
    give_card(s, first, "C095")
    s = place(engine, s, {"kind": "place", "space": "lessons", "card": "C095"})
    p = s["players"][first]
    assert "basketmaker" in p["improvements"]
    assert "basketmaker" not in s["available_improvements"]
    assert p["resources"]["stone"] == 0 and p["resources"]["reed"] == 0


def test_cube_cutter_harvest_exchange_and_score(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    inst = put_in_play(s, first, "C098")
    p = s["players"][first]
    p["resources"]["wood"] = 1
    p["resources"]["food"] = 1
    ctx = {"harvest_index": 1, "log": [], "actor": first, "extra": {}}
    cards.fire_player(s, p, "harvest_field", ctx)
    assert s["prompts"][0]["type"] == "choice"
    pid = p["player_id"]
    s = engine.apply_action(s, pid, {"kind": "choice", "index": 0}).new_state
    p = s["players"][first]
    assert p["resources"]["wood"] == 0 and p["resources"]["food"] == 0
    sc = score_player(p, s)
    assert sc["bonus"] == 1


def test_garden_designer_score_allocation(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "C099")
    p = s["players"][first]
    p["cells"][0]["type"] = "field"
    p["cells"][1]["type"] = "field"
    # 5 food, 2 empty fields: best allocation is 4 food -> 2 pts on one
    # field and 1 food -> 1 pt on the other = 3 bonus points.
    p["resources"]["food"] = 5
    sc = score_player(p, s)
    assert sc["bonus"] == 3


def test_butler_score_bonus_conditional_on_round_played(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give_card(s, first, "C100")
    s["round"] = 11
    s = place(engine, s, {"kind": "place", "space": "lessons", "card": "C100"})
    p = s["players"][first]
    p["cells"][0]["type"] = "room"  # 3 rooms now, more than 2 people
    sc = score_player(p, s)
    assert sc["bonus"] == 4


def test_stall_holder_unfenced_stable_tiers(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    inst = put_in_play(s, first, "C101")
    p = s["players"][first]
    p["resources"]["grain"] = 2
    p["resources"]["food"] = 0
    p["cells"][0]["stable"] = True
    p["cells"][1]["stable"] = True
    pid = p["player_id"]
    s = engine.apply_action(s, pid, {
        "kind": "card_action", "card": "C101", "params": {}}).new_state
    p = s["players"][first]
    assert p["resources"]["grain"] == 0
    assert p["resources"]["food"] == 3  # 2 unfenced stables -> 3 food
    sc = score_player(p, s)
    assert sc["bonus"] == 1


def test_tree_guard_wood_exchange(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "C102")
    _set_supply(s, "forest", {"wood": 3})
    add_space(s, "dummy", "Dummy", acc=True, supply={"wood": 1})
    give(s, first, wood=4)
    s = place(engine, s, {"kind": "place", "space": "forest"})
    # Other player's turn, then back to first (whose card action is
    # available any time it is their turn, not only immediately after).
    s = place(engine, s, {"kind": "place", "space": "dummy"})
    p = s["players"][first]
    wood_before = p["resources"]["wood"]
    pid = p["player_id"]
    s = engine.apply_action(s, pid, {"kind": "card_action", "card": "C102"}).new_state
    p = s["players"][first]
    assert p["resources"]["wood"] == wood_before - 4
    assert p["resources"]["stone"] == 2
    assert p["resources"]["clay"] == 1 and p["resources"]["reed"] == 1
    assert p["resources"]["grain"] == 1


def test_green_grocer_round_start_exchange(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "C103")
    give(s, first, grain=1)
    # Force a fresh round to fire round_start again.
    for sp in s["action_spaces"]:
        sp["occupied_by"] = first  # let the round end immediately
    p = s["players"][first]
    p["people_placed"] = p["people_total"]
    other = s["players"][(first + 1) % 2]
    other["people_placed"] = other["people_total"]
    s["current_player"] = first
    engine._advance_work(s, [])
    assert s["prompts"], "Green Grocer should offer an exchange at round start"
    pid = p["player_id"]
    # Choose "1 grain -> 2 food" explicitly.
    options = s["prompts"][0]["options"]
    idx = options.index("1 grain -> 2 food")
    s = engine.apply_action(s, pid, {"kind": "choice", "index": idx}).new_state
    p = s["players"][first]
    assert p["resources"]["grain"] == 0
    assert p["resources"]["food"] >= 2


def test_small_animal_breeder_conditional_income(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "C111")
    p = s["players"][first]
    p["resources"]["food"] = 0  # round is 1 -> 0 < 1, condition fails
    cards.fire_player(s, p, "round_start", {"round": s["round"], "log": [],
                                            "actor": first, "extra": {}})
    assert p["resources"]["food"] == 0
    p["resources"]["food"] = 1  # now 1 >= round 1
    cards.fire_player(s, p, "round_start", {"round": s["round"], "log": [],
                                            "actor": first, "extra": {}})
    assert p["resources"]["food"] == 2


def test_stone_importer_tiered_cost_by_harvest(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    inst = put_in_play(s, first, "C124")
    p = s["players"][first]
    s["harvest_index"] = 6  # cheapest tier: 1 food
    p["resources"]["food"] = 1
    pid = p["player_id"]
    s = engine.apply_action(s, pid, {"kind": "card_action", "card": "C124"}).new_state
    p = s["players"][first]
    assert p["resources"]["food"] == 0
    assert p["resources"]["stone"] == 2


def test_lover_pays_food_for_family_growth(engine):
    s = make_state(engine, 3)
    first = s["current_player"]
    give_card(s, first, "C127")
    s["players"][first]["resources"]["food"] = 13  # 14 - round(1) remaining
    people = s["players"][first]["people_total"]
    s = place(engine, s, {"kind": "place", "space": "lessons", "card": "C127"})
    p = s["players"][first]
    assert p["people_total"] == people + 1
    assert p["resources"]["food"] == 0


def test_lover_rejected_if_unaffordable(engine):
    s = make_state(engine, 3)
    first = s["current_player"]
    give_card(s, first, "C127")
    s["players"][first]["resources"]["food"] = 12  # 1 short of 13
    with pytest.raises(ValueError):
        place(engine, s, {"kind": "place", "space": "lessons", "card": "C127"})


def test_wooden_hut_extender_room_cost_mod(engine):
    s = make_state(engine, 3)
    first = s["current_player"]
    put_in_play(s, first, "C128")
    p = s["players"][first]
    give(s, first, wood=5, reed=1)
    add_space(s, "farm_expansion", "Farm Expansion")
    s["round"] = 1  # <=5 -> 5 wood + 1 reed
    s = place(engine, s, {"kind": "place", "space": "farm_expansion",
                          "rooms": [0]})
    p = s["players"][first]
    assert p["cells"][0]["type"] == "room"
    assert p["resources"]["wood"] == 0
    assert p["resources"]["reed"] == 0


def test_soldier_score_bonus_stone_wood_pairs(engine):
    s = make_state(engine, 3)
    first = s["current_player"]
    put_in_play(s, first, "C133")
    p = s["players"][first]
    give(s, first, stone=3, wood=5)
    sc = score_player(p, s)
    assert sc["bonus"] == 3


def test_cow_prince_score_counts_cattle_cells_only(engine):
    s = make_state(engine, 3)
    first = s["current_player"]
    put_in_play(s, first, "C134")
    p = s["players"][first]
    p["cells"][0]["animal"] = {"type": "cattle", "count": 2}
    p["cells"][1]["animal"] = {"type": "sheep", "count": 1}
    p["pets"] = {"cattle": 3}  # not counted: pets aren't tied to a cell
    sc = score_player(p, s)
    assert sc["bonus"] == 1


def test_ranch_provost_play_wood_and_pasture_score(engine):
    s = make_state(engine, 3)
    first = s["current_player"]
    give_card(s, first, "C136")
    wood = s["players"][first]["resources"]["wood"]
    s["round"] = 5  # 14-5=9 remaining -> +4 wood
    s = place(engine, s, {"kind": "place", "space": "lessons", "card": "C136"})
    p = s["players"][first]
    assert p["resources"]["wood"] == wood + 4
    p["fences"] = sorted(cell_edges(4))
    sc = score_player(p, s)
    assert sc["bonus"] == 3  # only player with a pasture -> highest capacity


def test_charcoal_burner_triggers_on_any_players_oven(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    put_in_play(s, first, "C137")
    p = s["players"][first]
    o = s["players"][other]
    food, wood = p["resources"]["food"], p["resources"]["wood"]
    cards.fire_player(s, o, "improvement_built", {
        "improvement": "clay_oven", "log": [], "actor": other, "extra": {}})
    # improvement_built is fired to all cards, not just the actor's own —
    # re-fire the way the engine actually would (fire, not fire_player).
    s["players"][first]["resources"]["food"] = food
    s["players"][first]["resources"]["wood"] = wood
    cards.fire(s, "improvement_built", {
        "improvement": "clay_oven", "log": [], "actor": other, "extra": {}})
    assert s["players"][first]["resources"]["food"] == food + 1
    assert s["players"][first]["resources"]["wood"] == wood + 1


def test_animal_feeder_day_laborer_choice(engine):
    s = make_state(engine, 3)
    first = s["current_player"]
    put_in_play(s, first, "C138")
    add_space(s, "day_laborer", "Day Laborer")
    food_before = s["players"][first]["resources"]["food"]
    s = place(engine, s, {"kind": "place", "space": "day_laborer"})
    assert s["prompts"][0]["type"] == "choice"
    options = s["prompts"][0]["options"]
    idx = options.index("1 sheep")
    p = s["players"][first]
    pid = p["player_id"]
    s = engine.apply_action(s, pid, {"kind": "choice", "index": idx}).new_state
    if s["prompts"]:
        s = engine.apply_action(s, pid, {
            "kind": "accommodate", "placements": [], "pets": {"sheep": 1}}).new_state
    assert s["players"][first]["resources"]["food"] == food_before + 2  # base Day Laborer food
    from server.agricola.state import animal_counts
    assert animal_counts(s["players"][first])["sheep"] == 1


def test_basketmakers_wife_play_and_conversion(engine):
    s = make_state(engine, 3)
    first = s["current_player"]
    food_before = s["players"][first]["resources"]["food"]
    give_card(s, first, "C139")
    s = place(engine, s, {"kind": "place", "space": "lessons", "card": "C139"})
    p = s["players"][first]
    assert p["resources"]["reed"] == 1
    assert p["resources"]["food"] == food_before + 1
    convs = cards.conversion_options(p)
    key, conv, inst = next(c for c in convs if c[0].startswith("C139"))
    assert conv == {"give": {"reed": 1}, "get": {"food": 2}}


def test_sheep_provider_grants_grain_to_any_actor(engine):
    s = make_state(engine, 3)
    first = s["current_player"]
    other = (first + 1) % 3
    put_in_play(s, first, "C141")
    add_space(s, "sheep_market", "Sheep Market", acc=True, supply={"sheep": 1})
    s["current_player"] = other
    grain_before = s["players"][first]["resources"]["grain"]
    other_pid = s["players"][other]["player_id"]
    s = engine.apply_action(s, other_pid, {
        "kind": "place", "space": "sheep_market"}).new_state
    if s["prompts"]:
        s = engine.apply_action(s, other_pid, {
            "kind": "accommodate", "placements": [], "pets": {"sheep": 1}}).new_state
    assert s["players"][first]["resources"]["grain"] == grain_before + 1


def test_forest_reviewer_grants_reed_when_sibling_occupied(engine):
    s = make_state(engine, 3)
    first = s["current_player"]
    put_in_play(s, first, "C145")
    _set_supply(s, "forest", {"wood": 3})
    grove = _set_supply(s, "grove", {"wood": 2})
    grove["occupied_by"] = (first + 1) % 3  # someone already sat on Grove
    reed_before = s["players"][first]["resources"]["reed"]
    s = place(engine, s, {"kind": "place", "space": "forest"})
    assert s["players"][first]["resources"]["reed"] == reed_before + 1


def test_twin_researcher_matching_pair(engine):
    s = make_state(engine, 4)
    first = s["current_player"]
    put_in_play(s, first, "C154")
    add_space(s, "western_quarry", "Western Quarry", acc=True, supply={"stone": 2})
    add_space(s, "eastern_quarry", "Eastern Quarry", acc=True, supply={"stone": 2})
    s["players"][first]["resources"]["food"] = 1
    s = place(engine, s, {"kind": "place", "space": "western_quarry"})
    s = _cycle_to(engine, s, first)
    p = s["players"][first]
    pid = p["player_id"]
    acts = engine.get_valid_actions(s, pid)
    assert any(a["kind"] == "card_action" for a in acts)
    s = engine.apply_action(s, pid, {"kind": "card_action", "card": "C154"}).new_state
    p = s["players"][first]
    assert p["resources"]["food"] == 0
    sc = score_player(p, s)
    assert sc["bonus"] == 1


def test_hoof_caregiver_scales_with_cattle_market_supply(engine):
    s = make_state(engine, 4)
    first = s["current_player"]
    add_space(s, "cattle_market", "Cattle Market", acc=True, supply={"cattle": 2})
    food_before = s["players"][first]["resources"]["food"]
    give_card(s, first, "C156")
    s = place(engine, s, {"kind": "place", "space": "lessons", "card": "C156"})
    p = s["players"][first]
    market = next(sp for sp in s["action_spaces"] if sp["id"] == "cattle_market")
    assert market["supply"]["cattle"] == 3
    assert p["resources"]["grain"] == 1
    assert p["resources"]["food"] == food_before + 3


def test_resource_analyzer_needs_two_leading_types(engine):
    s = make_state(engine, 4)
    first = s["current_player"]
    put_in_play(s, first, "C157")
    p = s["players"][first]
    for other in s["players"]:
        if other is not p:
            other["resources"]["wood"] = 0
            other["resources"]["clay"] = 0
    give(s, first, wood=5, clay=5)
    food_before = p["resources"]["food"]
    cards.fire_player(s, p, "round_start", {"round": s["round"], "log": [],
                                            "actor": first, "extra": {}})
    assert p["resources"]["food"] == food_before + 1


def test_forest_campaigner_reconstructs_total_before_own_take(engine):
    s = make_state(engine, 4)
    first = s["current_player"]
    put_in_play(s, first, "C158")
    _set_supply(s, "forest", {"wood": 8})
    food_before = s["players"][first]["resources"]["food"]
    s = place(engine, s, {"kind": "place", "space": "forest"})
    assert s["players"][first]["resources"]["food"] == food_before + 1


def test_fishermans_friend_food_gap(engine):
    s = make_state(engine, 4)
    first = s["current_player"]
    put_in_play(s, first, "C159")
    _set_supply(s, "traveling_players", {"food": 3})
    _set_supply(s, "fishing", {"food": 1})
    p = s["players"][first]
    food_before = p["resources"]["food"]
    cards.fire_player(s, p, "round_start", {"round": s["round"], "log": [],
                                            "actor": first, "extra": {}})
    assert p["resources"]["food"] == food_before + 2


def test_outrider_grants_grain_on_newest_space(engine):
    s = make_state(engine, 4)
    first = s["current_player"]
    put_in_play(s, first, "C160")
    add_space(s, "special_newest", "Special Newest", acc=True)
    s["revealed"].append("special_newest")
    grain_before = s["players"][first]["resources"]["grain"]
    s = place(engine, s, {"kind": "place", "space": "special_newest"})
    assert s["players"][first]["resources"]["grain"] == grain_before + 1


def test_material_deliveryman_tiered_broadcast_gain(engine):
    s = make_state(engine, 4)
    first = s["current_player"]
    other = (first + 1) % 4
    put_in_play(s, first, "C163")
    _set_supply(s, "forest", {"wood": 6})
    s["current_player"] = other
    clay_before = s["players"][first]["resources"]["clay"]
    other_pid = s["players"][other]["player_id"]
    s = engine.apply_action(s, other_pid, {
        "kind": "place", "space": "forest"}).new_state
    assert s["players"][first]["resources"]["clay"] == clay_before + 1


def test_german_heath_keeper_routes_sheep_through_accommodation(engine):
    s = make_state(engine, 4)
    first = s["current_player"]
    other = (first + 1) % 4
    put_in_play(s, first, "C164")
    add_space(s, "pig_market", "Pig Market", acc=True, supply={"boar": 1})
    s["current_player"] = other
    other_pid = s["players"][other]["player_id"]
    s = engine.apply_action(s, other_pid, {
        "kind": "place", "space": "pig_market"}).new_state
    # A sheep is queued for the *owner* (first), not the actor (other).
    assert any(pr["type"] == "accommodate" and pr["player"] == first
              and pr["gained"].get("sheep") == 1 for pr in s["prompts"])


def test_game_catcher_cost_scales_with_remaining_harvests(engine):
    s = make_state(engine, 4)
    first = s["current_player"]
    give_card(s, first, "C165")
    s["players"][first]["resources"]["food"] = 6  # all 6 harvests remain
    s = place(engine, s, {"kind": "place", "space": "lessons", "card": "C165"})
    p = s["players"][first]
    assert p["resources"]["food"] == 0
    if s["prompts"]:
        p["cells"][0]["stable"] = True
        p["cells"][1]["stable"] = True
        s = engine.apply_action(s, p["player_id"], {
            "kind": "accommodate",
            "placements": [{"cell": 0, "type": "cattle", "count": 1},
                           {"cell": 1, "type": "boar", "count": 1}],
        }).new_state
    from server.agricola.state import animal_counts
    counts = animal_counts(s["players"][first])
    assert counts["cattle"] == 1 and counts["boar"] == 1


def test_game_catcher_rejected_if_unaffordable(engine):
    s = make_state(engine, 4)
    first = s["current_player"]
    give_card(s, first, "C165")
    s["players"][first]["resources"]["food"] = 5  # 1 short of the 6 needed
    with pytest.raises(ValueError):
        place(engine, s, {"kind": "place", "space": "lessons", "card": "C165"})


def test_animal_catcher_swap_option(engine):
    s = make_state(engine, 4)
    first = s["current_player"]
    put_in_play(s, first, "C168")
    add_space(s, "day_laborer", "Day Laborer")
    give(s, first, food=10)
    s = place(engine, s, {"kind": "place", "space": "day_laborer"})
    assert s["prompts"][0]["type"] == "choice"
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {"kind": "choice", "index": 0}).new_state
    if s["prompts"]:
        p = s["players"][first]
        p["cells"][0]["stable"] = True
        p["cells"][1]["stable"] = True
        p["cells"][2]["stable"] = True
        s = engine.apply_action(s, pid, {
            "kind": "accommodate",
            "placements": [{"cell": 0, "type": "sheep", "count": 1},
                           {"cell": 1, "type": "boar", "count": 1},
                           {"cell": 2, "type": "cattle", "count": 1}],
        }).new_state
    from server.agricola.state import animal_counts
    counts = animal_counts(s["players"][first])
    assert counts == {"sheep": 1, "boar": 1, "cattle": 1}


def test_agricultural_labourer_grants_clay_per_grain_gain(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "C120")
    add_space(s, "grain_test_c120", "Grain Test", acc=True, supply={"grain": 2})
    clay_before = p["resources"]["clay"]
    s = place(engine, s, {"kind": "place", "space": "grain_test_c120"})
    p = s["players"][first]
    # "For each grain you obtain" is proportional: 2 grain -> 2 clay,
    # and the 8-clay pile decrements by that amount.
    assert p["resources"]["clay"] == clay_before + 2
    inst = next(i for i in p["occupations"] if i["id"] == "C120")
    assert inst["data"]["clay"] == 6


def test_carpenters_apprentice_three_clauses(engine):
    """C088: wood rooms 2 wood less (batch-scaled, wood house only);
    3rd/4th stable 1 wood less each; 13th-15th fence free."""
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "C088")

    cost = cards.modified_cost(s, p, "room", {"wood": 10, "reed": 4},
                               {"count": 2})
    assert cost == {"wood": 6, "reed": 4}
    p["house_type"] = "clay"
    cost_clay = cards.modified_cost(s, p, "room", {"clay": 5, "reed": 2},
                                    {"count": 1})
    assert cost_clay == {"clay": 5, "reed": 2}  # only wood rooms qualify
    p["house_type"] = "wood"

    for index, expected in [(1, 2), (2, 2), (3, 1), (4, 1), (5, 2)]:
        stable_cost = cards.modified_cost(s, p, "stable", {"wood": 2},
                                          {"count": 1, "index": index})
        assert stable_cost == {"wood": expected}, index

    # A 4-fence batch starting after 12 already built spans positions
    # 13-16: the first 3 are free, the 16th still costs 1.
    fence_cost = cards.modified_cost(s, p, "fences", {"wood": 4},
                                     {"count": 4, "start_index": 12})
    assert fence_cost == {"wood": 1}


def test_mud_wallower_accumulates_clay_and_exchanges_for_boar(engine):
    s = make_state(engine, 4)
    first = s["current_player"]
    p = s["players"][first]
    inst = put_in_play(s, first, "C148")
    add_space(s, "forest3", "Forest 3", acc=True, supply={"wood": 3})
    hook = cards.CARDS["C148"]["hooks"]["space_used"]

    for _ in range(3):
        ctx = {"space_id": "forest3", "goods": {"wood": 3}, "log": [],
              "extra": {}, "actor": first}
        hook(s, p, inst, ctx)
    assert inst["data"]["clay"] == 3
    assert inst.get("held", {}) == {}

    ctx = {"space_id": "forest3", "goods": {"wood": 3}, "log": [],
          "extra": {}, "actor": first}
    hook(s, p, inst, ctx)  # 4th clay -> mandatory exchange
    assert inst["data"]["clay"] == 0
    assert inst["held"] == {"boar": 1}

    # Another player's use of an accumulation space doesn't add clay.
    ctx2 = {"space_id": "forest3", "goods": {"wood": 3}, "log": [],
           "extra": {}, "actor": (first + 1) % 4}
    hook(s, p, inst, ctx2)
    assert inst["data"]["clay"] == 0

    assert cards.CARDS["C148"]["holds_animals"](s, p, inst) == {"types": {"boar": None}}
    ok, err = cards.validate_held(s, p)
    assert ok, err
    inst["held"] = {"sheep": 1}
    ok, err = cards.validate_held(s, p)
    assert not ok  # only boar allowed


def test_legworker_bonus_wood_on_adjacent_occupancy(engine):
    """Grain Seeds (0, 2) and Forest (1, 2) are orthogonally adjacent --
    using Forest after occupying Grain Seeds grants 1 wood on top of
    Forest's own payout."""
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "C117")

    s = place(engine, s, {"kind": "place", "space": "grain_seeds"})
    s = place(engine, s, {"kind": "place", "space": "clay_pit"})  # other
    wood_before = s["players"][first]["resources"]["wood"]
    s = place(engine, s, {"kind": "place", "space": "forest"})
    assert s["players"][first]["resources"]["wood"] == wood_before + 3 + 1


def test_legworker_no_bonus_without_adjacent_occupancy(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "C117")

    s = place(engine, s, {"kind": "place", "space": "grain_seeds"})
    s = place(engine, s, {"kind": "place", "space": "reed_bank"})  # other
    wood_before = s["players"][first]["resources"]["wood"]
    s = place(engine, s, {"kind": "place", "space": "day_laborer"})
    assert s["players"][first]["resources"]["wood"] == wood_before


def test_resource_recycler_reactive_clay_room(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    put_in_play(s, first, "C149")
    p = s["players"][first]
    p["house_type"] = "clay"
    s["players"][other]["house_type"] = "clay"
    give(s, other, stone=3, reed=1)
    add_space(s, "house_redevelopment", "House Redevelopment")
    s = place(engine, s, {"kind": "place", "space": "meeting_place"})  # first
    s = place(engine, s, {"kind": "place", "space": "house_redevelopment"})  # other
    assert s["players"][other]["house_type"] == "stone"
    p = s["players"][first]
    inst = next(i for i in p["occupations"] if i["id"] == "C149")
    assert inst["data"]["credits"] == 1

    give(s, first, food=2)
    food_before = p["resources"]["food"]
    pid = p["player_id"]
    s = engine.apply_action(s, pid, {
        "kind": "card_action", "card": "C149",
        "params": {"cells": [0]}}).new_state
    p = s["players"][first]
    assert p["cells"][0]["type"] == "room"
    assert p["resources"]["food"] == food_before - 2


def test_resource_recycler_ignores_own_renovation(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "C149")
    p = s["players"][first]
    p["house_type"] = "clay"
    give(s, first, stone=3, reed=1)
    add_space(s, "house_redevelopment", "House Redevelopment")
    s = place(engine, s, {"kind": "place", "space": "house_redevelopment"})
    p = s["players"][first]
    assert p["house_type"] == "stone"
    inst = next(i for i in p["occupations"] if i["id"] == "C149")
    assert inst["data"].get("credits", 0) == 0
