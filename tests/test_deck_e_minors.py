"""Tests for the deck E minor-improvement module
(server/agricola/decks/deck_e_minors.py)."""

import pytest

from server.agricola.engine import AgricolaEngine
from server.agricola import cards
from server.agricola.decks import deck_e_minors
from server.agricola.state import (
    MAJOR_IMPROVEMENTS, TOTAL_ROUNDS, plowable_cells, compute_pastures,
    cell_edges,
)

from test_agricola import (
    make_state, give, give_card, put_in_play, add_space, place, current_pid,
)


@pytest.fixture
def engine():
    return AgricolaEngine()


def _grant_major(state, pidx, imp):
    """Give a player a major improvement, keeping available_improvements
    consistent (as a real build would)."""
    if imp in state["available_improvements"]:
        state["available_improvements"].remove(imp)
    state["players"][pidx]["improvements"].append(imp)


# ── Registration completeness ─────────────────────────────────────────

def test_registration_completeness():
    db_codes = {c["code"] for c in cards.compendium().values()
               if c["deck"] == "E" and c["type"] == "minor"}
    registered = {cid for cid in cards.CARDS if cid in db_codes}
    unimplemented = set(deck_e_minors.UNIMPLEMENTED)
    assert unimplemented <= db_codes
    assert registered & unimplemented == set()
    assert registered | unimplemented == db_codes


# ── Smoke test: every implemented card gets played once ───────────────

_DUMMY_OCCS = ["occ_woodcutter", "occ_reed_collector", "occ_clay_digger",
              "occ_stonecutter"]

_NEEDS_OCC = {"E18": 2, "E24": 4, "E30": 2, "E36": 1, "E43": 3, "E46": 2,
             "E47": 3, "E48": 3, "E49": 2, "E54": 4, "E58": 1, "E61": 3,
             "E62": 2}


def _prep_prereqs_and_params(state, pidx, cid):
    """Satisfy any prereq and return the `params` dict (or None) needed
    to play `cid` once, with no other assumptions about board state."""
    p = state["players"][pidx]
    n = _NEEDS_OCC.get(cid)
    if n:
        for i in range(n):
            put_in_play(state, pidx, _DUMMY_OCCS[i])

    params = None
    if cid == "E14":
        _grant_major(state, pidx, "clay_oven")
    elif cid == "E31":
        p["improvements"] += ["well", "pottery"]
        put_in_play(state, pidx, "occ_woodcutter")
    elif cid == "E33":
        _grant_major(state, pidx, "clay_oven")
    elif cid == "E38":
        put_in_play(state, pidx, "minor_basket")
        put_in_play(state, pidx, "minor_canoe")
        params = {"discard": ["minor_basket", "minor_canoe"]}
    elif cid == "E42":
        give(state, pidx, wood=1)
        params = {"pay": "wood", "gain": "stone"}
    elif cid == "E60":
        p["cells"][0]["animal"] = {"type": "sheep", "count": 1}
    elif cid == "E29":
        p["cells"][0]["animal"] = {"type": "sheep", "count": 1}
    elif cid == "E11":
        params = {"cell": plowable_cells(p)[0]}
    elif cid == "E16":
        params = {"good": "wood"}
    elif cid == "E40":
        cell = next(i for i, c in enumerate(p["cells"]) if c["type"] == "empty")
        params = {"cell": cell}
    elif cid == "E52":
        cell = next(i for i, c in enumerate(p["cells"])
                   if c["type"] == "empty" and not c["stable"])
        params = {"cell": cell}
    elif cid == "E55":
        p["house_type"] = "stone"
        params = {"cell": deck_e_minors._room_eligible_cells(p)[0]}
    return params


def _resolve_all_prompts(engine, state, pid):
    while True:
        acts = engine.get_valid_actions(state, pid)
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


_DB_E_MINOR_CODES = {c["code"] for c in cards.compendium().values()
                    if c["deck"] == "E" and c["type"] == "minor"}
ALL_E_MINORS = sorted(cid for cid in cards.CARDS if cid in _DB_E_MINOR_CODES)


@pytest.mark.parametrize("cid", ALL_E_MINORS)
def test_smoke_play_every_card(engine, cid):
    s = make_state(engine, 2)
    first = s["current_player"]
    params = _prep_prereqs_and_params(s, first, cid)
    give(s, first, **cards.CARDS[cid]["cost"])
    give_card(s, first, cid)
    minor = {"card": cid}
    if params is not None:
        minor["params"] = params
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": minor})
    pid = s["players"][first]["player_id"]
    s = _resolve_all_prompts(engine, s, pid)
    p = s["players"][first]
    assert cid not in p["hand_minors"]
    if not cards.CARDS[cid]["traveling"]:
        assert any(i["id"] == cid for i in p["minors"])


# ── Targeted effect tests ──────────────────────────────────────────────

def test_fishing_rod_scales_by_round(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    food_before = p["resources"]["food"]
    put_in_play(s, first, "E12")
    add_space(s, "fishing", "Fishing", acc=True, supply={"food": 1})
    s = place(engine, s, {"kind": "place", "space": "fishing"})
    assert s["players"][first]["resources"]["food"] == food_before + 1 + 1  # round < 8

    inst = next(i for i in s["players"][first]["minors"] if i["id"] == "E12")
    p = s["players"][first]
    s["round"] = 8
    ctx = {"space_id": "fishing", "goods": {"food": 1}, "log": [], "extra": {},
          "actor": first}
    cards.CARDS["E12"]["hooks"]["space_used"](s, p, inst, ctx)
    assert ctx["extra"]["food"] == 2


def test_axe_discounts_wooden_room_cost(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "E13")
    cost = cards.modified_cost(s, p, "room", {"wood": 5, "reed": 2})
    assert cost == {"wood": 2, "reed": 2}


def test_bakers_oven_returns_oven_and_bakes(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["resources"]["food"] = 0
    p["resources"]["grain"] = 2
    _grant_major(s, first, "clay_oven")
    give_card(s, first, "E14")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "E14"}})
    p = s["players"][first]
    assert "clay_oven" not in p["improvements"]
    assert "clay_oven" in s["available_improvements"]
    pid = p["player_id"]
    acts = engine.get_valid_actions(s, pid)
    choice = next(a for a in acts if a["kind"] == "choice")
    idx = choice["options"].index("Bake 2 grain for 10 food")
    s = place(engine, s, {"kind": "choice", "index": idx})
    p = s["players"][first]
    assert p["resources"]["grain"] == 0
    assert p["resources"]["food"] == 10
    assert any(i["id"] == "E14" for i in p["minors"])
    assert cards.CARDS["E14"]["bake"] == (2, 5)


def test_windmill_raw_grain_rate(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "E17")
    assert cards.raw_values(s["players"][first])["grain"] == 2


def test_bean_field_is_sowable(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "E18")
    give(s, first, vegetable=1)
    inst = next(i for i in p["minors"] if i["id"] == "E18")
    add_space(s, "grain_utilization", "Sow and/or Bake Bread")
    s = place(engine, s, {"kind": "place", "space": "grain_utilization",
                          "sow": [{"card": "E18", "crop": "vegetable"}]})
    inst = next(i for i in s["players"][first]["minors"] if i["id"] == "E18")
    assert inst["crops"] == {"type": "vegetable", "count": 2}


def test_simple_fireplace_cooks_and_bakes(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "E20")
    assert cards.card_cook_specs(p)[0]["cattle"] == 3
    p["resources"]["grain"] = 1
    p["resources"]["food"] = 0
    add_space(s, "grain_utilization", "Sow and/or Bake Bread")
    s = place(engine, s, {"kind": "place", "space": "grain_utilization",
                          "bake": {"E20": 1}})
    assert s["players"][first]["resources"]["food"] == 2


def test_half_timbered_house_scores_stone_rooms_only(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    inst = put_in_play(s, first, "E21")
    assert deck_e_minors._e21_score(s, p, inst) == 0  # still a wood house
    p["house_type"] = "stone"
    assert deck_e_minors._e21_score(s, p, inst) == 2  # 2 starting rooms


def test_raft_choice_food_or_reed(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "E22")
    add_space(s, "fishing", "Fishing", acc=True, supply={"food": 1})
    s = place(engine, s, {"kind": "place", "space": "fishing"})
    pid = s["players"][first]["player_id"]
    acts = engine.get_valid_actions(s, pid)
    choice = next(a for a in acts if a["kind"] == "choice")
    idx = choice["options"].index("1 reed")
    s = place(engine, s, {"kind": "choice", "index": idx})
    assert s["players"][first]["resources"]["reed"] == 1


def test_manger_score_thresholds(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    inst = put_in_play(s, first, "E23")
    assert deck_e_minors._e23_score(s, p, inst) == 0
    # Fence 6 farmyard cells into 1 pasture via row 0 (cells 0..4) + fencing
    # is fiddly to construct by hand; verify via a direct compute_pastures
    # stub instead.
    import server.agricola.decks.deck_e_minors as m
    orig = compute_pastures
    try:
        m.compute_pastures = lambda pl: [[0, 1, 2, 3, 4, 5, 6, 7, 8]]
        assert m._e23_score(s, p, inst) == 4
        m.compute_pastures = lambda pl: [[0, 1, 2, 3, 4, 5]]
        assert m._e23_score(s, p, inst) == 1
    finally:
        m.compute_pastures = orig


def test_animal_pen_schedules_food_every_round(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "occ_woodcutter")
    put_in_play(s, first, "occ_reed_collector")
    put_in_play(s, first, "occ_clay_digger")
    put_in_play(s, first, "occ_stonecutter")
    give(s, first, wood=2)
    give_card(s, first, "E24")
    rnd = s["round"]
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "E24"}})
    for r in range(rnd + 1, TOTAL_ROUNDS + 1):
        assert s["round_goods"][str(r)][str(first)]["food"] == 2


def test_wood_fired_oven_unlimited_bake(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["resources"]["food"] = 0
    p["resources"]["grain"] = 3
    give(s, first, wood=3, stone=1)
    give_card(s, first, "E27")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "E27"}})
    pid = s["players"][first]["player_id"]
    acts = engine.get_valid_actions(s, pid)
    choice = next(a for a in acts if a["kind"] == "choice")
    idx = choice["options"].index("Bake 3 grain for 9 food")
    s = place(engine, s, {"kind": "choice", "index": idx})
    p = s["players"][first]
    assert p["resources"]["grain"] == 0
    assert p["resources"]["food"] == 9


def test_clogs_scores_by_house_type(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    inst = put_in_play(s, first, "E28")
    assert deck_e_minors._e28_score(s, p, inst) == 0
    p["house_type"] = "clay"
    assert deck_e_minors._e28_score(s, p, inst) == 1
    p["house_type"] = "stone"
    assert deck_e_minors._e28_score(s, p, inst) == 2


def test_canoe_fishing_bonus(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    food_before = s["players"][first]["resources"]["food"]
    put_in_play(s, first, "occ_woodcutter")
    put_in_play(s, first, "occ_reed_collector")
    put_in_play(s, first, "E30")
    add_space(s, "fishing", "Fishing", acc=True, supply={"food": 1})
    s = place(engine, s, {"kind": "place", "space": "fishing"})
    p = s["players"][first]
    assert p["resources"]["food"] == food_before + 2
    assert p["resources"]["reed"] == 1


def test_carp_pond_schedules_odd_rounds_only(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["improvements"] += ["well", "pottery"]
    put_in_play(s, first, "occ_woodcutter")
    give_card(s, first, "E31")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "E31"}})
    # Round 1 (the current round) is not "remaining"; only later odd
    # rounds get scheduled.
    for r in (3, 5, 7, 9, 11, 13):
        assert s["round_goods"][str(r)][str(first)]["food"] == 1
    assert "2" not in s["round_goods"] or str(first) not in \
        s["round_goods"].get("2", {})


def test_potato_dibber_extra_vegetable_on_sow(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["cells"][0]["type"] = "field"
    put_in_play(s, first, "E32")
    give(s, first, vegetable=1)
    add_space(s, "grain_utilization", "Sow and/or Bake Bread")
    s = place(engine, s, {"kind": "place", "space": "grain_utilization",
                          "sow": [{"cell": 0, "crop": "vegetable"}]})
    assert s["players"][first]["cells"][0]["crops"]["count"] == 3  # 2 + 1


def test_ceramics_needs_oven_and_grants_food(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["resources"]["food"] = 0
    assert not cards.check_prereq(s, p, "E33")
    _grant_major(s, first, "clay_oven")
    assert cards.check_prereq(s, p, "E33")
    give(s, first, clay=1)
    give_card(s, first, "E33")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "E33"}})
    assert s["players"][first]["resources"]["food"] == 2


def test_basket_exchanges_wood_for_food(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    food_before = p["resources"]["food"]
    put_in_play(s, first, "E34")
    add_space(s, "forest", "Forest", acc=True, supply={"wood": 3})
    s = place(engine, s, {"kind": "place", "space": "forest"})
    p = s["players"][first]
    assert p["resources"]["wood"] == 3
    pid = p["player_id"]
    acts = engine.get_valid_actions(s, pid)
    choice = next(a for a in acts if a["kind"] == "choice")
    idx = choice["options"].index("Leave 2 wood for 3 food")
    s = place(engine, s, {"kind": "choice", "index": idx})
    p = s["players"][first]
    assert p["resources"]["wood"] == 1
    assert p["resources"]["food"] == food_before + 3
    forest = next(sp for sp in s["action_spaces"] if sp["id"] == "forest")
    assert forest["supply"]["wood"] == 2


def test_clay_supports_discounts_clay_room_cost(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["house_type"] = "clay"
    put_in_play(s, first, "E37")
    cost = cards.modified_cost(s, p, "room", {"clay": 5, "reed": 2})
    assert cost == {"clay": 2, "wood": 1, "reed": 1}


def test_madonna_statue_discards_two_improvements(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "minor_basket")
    _grant_major(s, first, "well")
    assert cards.check_prereq(s, p, "E38")
    give_card(s, first, "E38")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "E38",
                                   "params": {"discard": ["minor_basket",
                                                          "well"]}}})
    p = s["players"][first]
    assert not any(i["id"] == "minor_basket" for i in p["minors"])
    assert "well" not in p["improvements"]
    assert "well" in s["available_improvements"]


def test_market_stall_travels_after_playing(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    give(s, first, grain=1)
    give_card(s, first, "E39")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "E39"}})
    p = s["players"][first]
    assert p["resources"]["vegetable"] == 1
    assert not any(i["id"] == "E39" for i in p["minors"])
    assert "E39" in s["players"][other]["hand_minors"]


def test_mini_pasture_fences_one_free_cell(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    cell = next(i for i, c in enumerate(p["cells"]) if c["type"] == "empty")
    give(s, first, food=2)
    give_card(s, first, "E40")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "E40", "params": {"cell": cell}}})
    p = s["players"][first]
    pastures = compute_pastures(p)
    assert any(pa == [cell] for pa in pastures)


def test_millstone_flat_bake_bonus(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["resources"]["food"] = 0
    p["resources"]["grain"] = 2
    _grant_major(s, first, "fireplace_2")
    put_in_play(s, first, "E41")
    add_space(s, "grain_utilization", "Sow and/or Bake Bread")
    s = place(engine, s, {"kind": "place", "space": "grain_utilization",
                          "bake": {"fireplace_2": 2}})
    p = s["players"][first]
    # fireplace_2 bakes 2 grain @ 2 food each (4) + Millstone's flat +2.
    assert p["resources"]["food"] == 6
    assert p["resources"]["grain"] == 0


def test_helpful_neighbours_pays_and_gains_via_params(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    p = s["players"][first]
    give(s, first, clay=1)
    give_card(s, first, "E42")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "E42",
                                   "params": {"pay": "clay", "gain": "reed"}}})
    p = s["players"][first]
    assert p["resources"]["clay"] == 0
    assert p["resources"]["reed"] == 1
    assert "E42" in s["players"][other]["hand_minors"]


def test_fruit_tree_schedules_rounds_8_to_14(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    for i in range(3):
        put_in_play(s, first, _DUMMY_OCCS[i])
    give_card(s, first, "E43")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "E43"}})
    for r in range(8, 15):
        assert s["round_goods"][str(r)][str(first)]["food"] == 1
    assert "7" not in s["round_goods"] or str(first) not in \
        s["round_goods"].get("7", {})


def test_outhouse_requires_another_player_below_two_occs(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    p = s["players"][first]
    assert cards.check_prereq(s, p, "E44")
    put_in_play(s, other, "occ_woodcutter")
    put_in_play(s, other, "occ_reed_collector")
    assert not cards.check_prereq(s, p, "E44")


def test_private_forest_schedules_even_rounds_only(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give(s, first, food=2)
    give_card(s, first, "E45")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "E45"}})
    for r in (2, 4, 6, 8, 10, 12, 14):
        assert s["round_goods"][str(r)][str(first)]["wood"] == 1


def test_lettuce_patch_converts_only_its_own_vegetables(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["resources"]["food"] = 0
    for i in range(3):
        put_in_play(s, first, _DUMMY_OCCS[i])
    give_card(s, first, "E47")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "E47"}})
    p = s["players"][first]
    give(s, first, vegetable=1)
    add_space(s, "grain_utilization", "Sow and/or Bake Bread")
    s = place(engine, s, {"kind": "place", "space": "day_laborer"})  # other's turn
    s = place(engine, s, {"kind": "place", "space": "grain_utilization",
                          "sow": [{"card": "E47", "crop": "vegetable"}]})
    # Simulate a harvest field phase.
    p = s["players"][first]
    inst = next(i for i in p["minors"] if i["id"] == "E47")
    inst["crops"]["count"] -= 1  # mirror engine's harvest decrement
    p["resources"]["vegetable"] += 1
    ctx = {"harvest_index": 1, "log": [], "actor": first, "extra": {}}
    cards.CARDS["E47"]["hooks"]["harvest_field"](s, p, inst, ctx)
    assert p["resources"]["vegetable"] == 0
    assert p["resources"]["food"] == 4
    assert inst["data"]["active"] is True  # count still > 0 (started at 2)


def test_writing_desk_plays_second_occupation(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "occ_woodcutter")
    put_in_play(s, first, "occ_reed_collector")
    give(s, first, wood=1, food=2)
    give_card(s, first, "E49")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "E49"}})
    p = s["players"][first]
    p["resources"]["food"] = 2
    give_card(s, first, "occ_clay_digger")
    s = place(engine, s, {"kind": "place", "space": "day_laborer"})  # other's turn
    s = place(engine, s, {"kind": "place", "space": "lessons",
                          "card": "occ_clay_digger"})
    pid = s["players"][first]["player_id"]
    acts = engine.get_valid_actions(s, pid)
    choice = next((a for a in acts if a["kind"] == "choice"), None)
    assert choice is not None
    idx = next(i for i, o in enumerate(choice["options"])
              if o != "Skip")
    s = place(engine, s, {"kind": "choice", "index": idx})
    p = s["players"][first]
    assert p["resources"]["food"] == 0
    assert p["occs_played"] == 2


def test_builders_trowel_renovates_out_of_turn(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "E50")
    give(s, first, clay=2, reed=1)
    assert p["house_type"] == "wood"
    pid = p["player_id"]
    s = engine.apply_action(s, pid, {
        "kind": "card_action", "card": "E50"}).new_state
    p = s["players"][first]
    assert p["house_type"] == "clay"
    assert p["resources"]["clay"] == 0
    assert p["resources"]["reed"] == 0


def test_spindle_food_tiers(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "E51")
    assert deck_e_minors._e51_food(s, p) == 0
    p["cells"][0]["animal"] = {"type": "sheep", "count": 3}
    assert deck_e_minors._e51_food(s, p) == 1
    p["cells"][0]["animal"] = {"type": "sheep", "count": 5}
    assert deck_e_minors._e51_food(s, p) == 2


def test_stable_builds_free_stable_via_params(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    cell = next(i for i, c in enumerate(p["cells"])
               if c["type"] == "empty" and not c["stable"])
    give(s, first, wood=1)
    give_card(s, first, "E52")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "E52", "params": {"cell": cell}}})
    assert s["players"][first]["cells"][cell]["stable"] is True


def test_quarry_bonus_on_day_laborer(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    for i in range(4):
        put_in_play(s, first, _DUMMY_OCCS[i])
    put_in_play(s, first, "E54")
    s = place(engine, s, {"kind": "place", "space": "day_laborer"})
    assert s["players"][first]["resources"]["stone"] == 3


def test_stone_house_extension_free_room(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["house_type"] = "stone"
    cell = deck_e_minors._room_eligible_cells(p)[0]
    give(s, first, reed=1, stone=3)
    give_card(s, first, "E55")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "E55", "params": {"cell": cell}}})
    assert s["players"][first]["cells"][cell]["type"] == "room"


def test_stone_tongs_bonus_on_quarries(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "E56")
    add_space(s, "western_quarry", "Western Quarry", acc=True, supply={"stone": 1})
    s = place(engine, s, {"kind": "place", "space": "western_quarry"})
    assert s["players"][first]["resources"]["stone"] == 2


def test_dovecote_schedules_late_rounds(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give(s, first, stone=2)
    give_card(s, first, "E57")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "E57"}})
    for r in (10, 11, 12, 13, 14):
        assert s["round_goods"][str(r)][str(first)]["food"] == 1
    assert "9" not in s["round_goods"] or str(first) not in \
        s["round_goods"].get("9", {})


def test_drinking_trough_pasture_bonus(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "E59")
    assert cards.pasture_bonus(s["players"][first]) == 2


def test_cattle_market_trades_sheep_for_cattle(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    p = s["players"][first]
    p["cells"][0]["animal"] = {"type": "sheep", "count": 1}
    give_card(s, first, "E60")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "E60"}})
    pid = p["player_id"]
    acts = engine.get_valid_actions(s, pid)
    acc = next(a for a in acts if a["kind"] == "accommodate")
    s = place(engine, s, {"kind": "accommodate", "placements": [],
                          "pets": {"cattle": 1}})
    p = s["players"][first]
    assert cards.animal_totals_of(p)["sheep"] == 0
    assert cards.animal_totals_of(p)["cattle"] == 1
    assert "E60" in s["players"][other]["hand_minors"]


def test_riding_plough_plows_up_to_three_fields_twice(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    for i in range(3):
        put_in_play(s, first, _DUMMY_OCCS[i])
    inst = put_in_play(s, first, "E61")
    add_space(s, "farmland", "Farmland")
    s = place(engine, s, {"kind": "place", "space": "farmland",
                          "cell": next(i for i, c in
                                      enumerate(s["players"][first]["cells"])
                                      if c["type"] == "empty")})
    pid = s["players"][first]["player_id"]
    fields_before = sum(1 for c in s["players"][first]["cells"]
                       if c["type"] == "field")
    assert fields_before == 1
    acts = engine.get_valid_actions(s, pid)
    choice = next(a for a in acts if a["kind"] == "choice")
    s = place(engine, s, {"kind": "choice", "index": 0})
    acts = engine.get_valid_actions(s, pid)
    choice = next((a for a in acts if a["kind"] == "choice"), None)
    if choice:
        stop_idx = choice["options"].index("Stop")
        s = place(engine, s, {"kind": "choice", "index": stop_idx})
    fields_after = sum(1 for c in s["players"][first]["cells"]
                      if c["type"] == "field")
    assert fields_after >= 2
    inst = next(i for i in s["players"][first]["minors"] if i["id"] == "E61")
    assert inst["data"]["uses_left"] == 1


def test_clay_roof_reed_to_clay_payment(engine):
    """E36: replace 1 or 2 reed with the same amount of clay, driven by
    the client action's own "payment" field -- decks/GUIDE.md's worked
    ctx["payment"] example, verbatim, for the real card."""
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "E36")
    rooms_n = sum(1 for c in p["cells"] if c["type"] == "room")
    # The unmodified-cost preview (_space_usable) needs the normal reed
    # too, even though the real payment ends up not spending it.
    give(s, first, clay=rooms_n + 1, reed=1)
    add_space(s, "house_redevelopment", "House Redevelopment")
    s = place(engine, s, {"kind": "place", "space": "house_redevelopment",
                          "payment": {"reed_to_clay": 1}})
    p = s["players"][first]
    assert p["house_type"] == "clay"
    assert p["resources"]["reed"] == 1 and p["resources"]["clay"] == 0

    # Also applies to kind="room"; garbage payment raises rather than
    # being silently ignored.
    cost = cards.modified_cost(s, p, "room", {"wood": 5, "reed": 2},
                               {"count": 1, "payment": {"reed_to_clay": 2}})
    assert cost == {"wood": 5, "clay": 2}
    with pytest.raises(ValueError):
        cards.modified_cost(s, p, "room", {"wood": 5, "reed": 2},
                            {"count": 1, "payment": {"reed_to_clay": 5}})


def test_shepherds_pipe_sheep_pasture_and_stable(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["cells"][0]["animal"] = {"type": "sheep", "count": 1}  # meet the prereq
    put_in_play(s, first, "E29")
    p["fences"] = sorted(cell_edges(4))  # size-1 pasture, base capacity 2

    assert cards.pasture_capacity(s, p, [4], "sheep") == 4  # 2 base + 2
    assert cards.pasture_capacity(s, p, [4], "cattle") == 2  # unaffected
    assert cards.unfenced_stable_capacity(s, p, "sheep") == 2
    assert cards.unfenced_stable_capacity(s, p, "cattle") == 1


def test_animal_yard_holds_2_any_mix(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "occ_woodcutter")  # meet the 1-occupation prereq
    inst = put_in_play(s, first, "E58")

    inst["held"] = {"sheep": 1, "boar": 1}
    ok, err = cards.validate_held(s, p)
    assert ok, err

    inst["held"] = {"sheep": 2, "boar": 1}
    ok, err = cards.validate_held(s, p)
    assert not ok
