"""Tests for the deck D minor-improvement module
(server/agricola/decks/deck_d_minors.py)."""

import pytest

from server.agricola.engine import AgricolaEngine
from server.agricola import cards
from server.agricola.decks import deck_d_minors
from server.agricola.state import HARVEST_ROUNDS, cell_edges

from test_agricola import (
    make_state, give, give_card, put_in_play, add_space, place, current_pid,
)


@pytest.fixture
def engine():
    return AgricolaEngine()


# ── Registration completeness ──────────────────────────────────────────

def test_registration_completeness():
    db_codes = {c["code"] for c in cards.compendium().values()
               if c["deck"] == "D" and c["type"] == "minor"}
    registered = {cid for cid in cards.CARDS if cid in db_codes}
    unimplemented = set(deck_d_minors.UNIMPLEMENTED)
    assert unimplemented <= db_codes
    assert registered & unimplemented == set()
    assert registered | unimplemented == db_codes


# ── Smoke test: every implemented card gets played once ────────────────

_DUMMY_OCCS = ["occ_woodcutter", "occ_reed_collector", "occ_clay_digger"]

_NEEDS_OCC = {"D004": 3, "D006": 2, "D019": 1, "D027": 1, "D030": 3,
             "D040": 1, "D044": 2, "D046": 2, "D052": 1, "D064": 1,
             "D068": 2}


def _prep_prereqs(state, pidx, cid):
    p = state["players"][pidx]
    n = _NEEDS_OCC.get(cid)
    if n:
        for i in range(n):
            put_in_play(state, pidx, _DUMMY_OCCS[i % len(_DUMMY_OCCS)])
    if cid == "D001":
        # 3 fields forming an L (pivot cell 1 with arms 0 and 6).
        for c in (0, 1, 6):
            p["cells"][c]["type"] = "field"
    if cid == "D005":
        p["cells"][0]["type"] = "field"
        p["cells"][0]["crops"] = {"type": "grain", "count": 1}
    if cid == "D008":
        p["cells"][0]["type"] = "field"
        p["cells"][0]["crops"] = {"type": "grain", "count": 1}
        p["cells"][1]["type"] = "field"
        p["cells"][1]["crops"] = {"type": "vegetable", "count": 1}
        p["cells"][2]["type"] = "field"  # empty field
    if cid == "D040":
        p["cells"][0]["type"] = "field"
        p["cells"][1]["type"] = "field"
    if cid == "D047":
        for i in range(10):
            put_in_play(state, pidx, _DUMMY_OCCS[i % len(_DUMMY_OCCS)])
    if cid == "D048":
        for c in (0, 1, 2):
            p["cells"][c]["type"] = "room"
    if cid == "D058":
        state["round"] = 5
    if cid == "D059":
        p["improvements"].append("fireplace_2")
        if "fireplace_2" in state["available_improvements"]:
            state["available_improvements"].remove("fireplace_2")
    if cid == "D060":
        p["improvements"].append("pottery")
        if "pottery" in state["available_improvements"]:
            state["available_improvements"].remove("pottery")
    if cid == "D070":
        p["cells"][0]["type"] = "field"
        p["cells"][1]["type"] = "field"
    if cid == "D082":
        p["cells"][0]["animal"] = {"type": "boar", "count": 1}


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


_DB_D_MINORS = {c["code"] for c in cards.compendium().values()
                if c["deck"] == "D" and c["type"] == "minor"}
ALL_D_MINORS = sorted(cid for cid in cards.CARDS if cid in _DB_D_MINORS)

_MINOR_PARAMS = {"D008": {"cell": 2}}


@pytest.mark.parametrize("cid", ALL_D_MINORS)
def test_smoke_play_every_card(engine, cid):
    s = make_state(engine, 2)
    first = s["current_player"]
    _prep_prereqs(s, first, cid)
    give(s, first, **cards.CARDS[cid]["cost"])
    give_card(s, first, cid)
    minor = {"card": cid}
    if cid in _MINOR_PARAMS:
        minor["params"] = _MINOR_PARAMS[cid]
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": minor})
    pid = s["players"][first]["player_id"]
    s = _resolve_all_prompts(engine, s, pid)
    p = s["players"][first]
    if cards.CARDS[cid]["traveling"]:
        assert cid not in p["hand_minors"]
    else:
        assert cid not in p["hand_minors"]
        assert any(i["id"] == cid for i in p["minors"])


# ── Targeted effect tests ───────────────────────────────────────────────

def test_zigzag_harrow_prereq_and_plow(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    give_card(s, first, "D001")
    give(s, first, wood=1)
    # No L-shaped fields yet: prereq should reject.
    with pytest.raises(ValueError):
        place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "D001"}})
    for c in (0, 1, 6):
        p["cells"][c]["type"] = "field"
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "D001", "params": {"cell": 2}}})
    assert s["players"][first]["cells"][2]["type"] == "field"
    # Traveling: passed to the left neighbor's hand.
    assert "D001" in s["players"][(first + 1) % 2]["hand_minors"]


def test_dwelling_plan_inline_renovation(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give_card(s, first, "D002")
    give(s, first, food=1, clay=2, reed=1)
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "D002", "params": {"renovate": True}}})
    assert s["players"][first]["house_type"] == "clay"
    assert s["players"][first]["resources"]["clay"] == 0


def test_furrows_sows_a_field(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["cells"][0]["type"] = "field"
    give_card(s, first, "D003")
    give(s, first, grain=1)
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "D003",
                                   "params": {"cell": 0, "crop": "grain"}}})
    p = s["players"][first]
    assert p["cells"][0]["crops"] == {"type": "grain", "count": 3}
    assert p["resources"]["grain"] == 0


def test_cross_cut_wood_matches_stone(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    for cid in ("occ_woodcutter", "occ_reed_collector", "occ_clay_digger"):
        put_in_play(s, first, cid)
    give(s, first, food=1, stone=3)
    give_card(s, first, "D004")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "D004"}})
    assert s["players"][first]["resources"]["wood"] == 3


def test_field_clay_counts_planted_fields(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["cells"][0]["type"] = "field"
    p["cells"][0]["crops"] = {"type": "grain", "count": 2}
    p["cells"][1]["type"] = "field"
    p["cells"][1]["crops"] = {"type": "vegetable", "count": 1}
    give(s, first, food=1)
    give_card(s, first, "D005")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "D005"}})
    assert s["players"][first]["resources"]["clay"] == 2


def test_petrified_wood_exchanges_wood_for_stone(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "occ_woodcutter")
    put_in_play(s, first, "occ_reed_collector")
    give(s, first, wood=5)
    give_card(s, first, "D006")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "D006", "params": {"wood": 3}}})
    p = s["players"][first]
    assert p["resources"]["wood"] == 2
    assert p["resources"]["stone"] == 3


def test_trowel_card_action_renovates_straight_to_stone(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "D013")
    give(s, first, stone=2, reed=2, food=2)
    food_before = s["players"][first]["resources"]["food"]
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {"kind": "card_action", "card": "D013"}).new_state
    p = s["players"][first]
    assert p["house_type"] == "stone"
    assert p["resources"]["stone"] == 0
    assert p["resources"]["reed"] == 0
    assert p["resources"]["food"] == food_before - 2


def test_hammer_crusher_grants_goods_and_free_room_action(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "D014")
    p["house_type"] = "clay"
    give(s, first, stone=2, reed=1)
    add_space(s, "house_redevelopment", "House Redevelopment")
    s = place(engine, s, {"kind": "place", "space": "house_redevelopment"})
    p = s["players"][first]
    assert p["house_type"] == "stone"
    assert p["resources"]["clay"] == 2
    assert p["resources"]["reed"] == 1  # 0 leftover + 1 from Hammer Crusher
    # Free "Build Rooms" card_action is now available -- but card_action
    # requires it be your turn during the work phase, so let the other
    # player take a placement first (this is still round 1).
    other = (first + 1) % 2
    s = place(engine, s, {"kind": "place", "space": "day_laborer"})
    assert s["current_player"] == first
    p = s["players"][first]
    give(s, first, stone=5, reed=2)  # house is now stone, so rooms cost stone
    pid = p["player_id"]
    room_cell = next(c for c in (0, 1, 2, 3, 4)
                     if p["cells"][c]["type"] == "empty" and not p["cells"][c]["stable"])
    s = engine.apply_action(s, pid, {
        "kind": "card_action", "card": "D014",
        "params": {"cells": [room_cell]}}).new_state
    assert s["players"][first]["cells"][room_cell]["type"] == "room"


def test_wooden_whey_bucket_free_stable_before_market(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "D016")
    add_space(s, "sheep_market", "Sheep Market", acc=True, supply={"sheep": 1})
    p = s["players"][first]
    p["fences"] = []
    s = place(engine, s, {"kind": "place", "space": "sheep_market"})
    # First prompt: build a free stable (choose the first eligible cell).
    prompt = s["prompts"][0]
    assert prompt["type"] == "choice"
    cell = prompt["data"]["cells"][0]
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {"kind": "choice", "index": 1}).new_state
    assert s["players"][first]["cells"][cell]["stable"]
    # Then accommodate the sheep.
    s = engine.apply_action(s, pid, {
        "kind": "accommodate", "placements": [{"cell": cell, "type": "sheep",
                                              "count": 1}]}).new_state
    assert s["players"][first]["cells"][cell]["animal"] == \
        {"type": "sheep", "count": 1}


def test_drill_harrow_card_action_plows_for_food(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "D017")
    give(s, first, food=3)
    food_before = s["players"][first]["resources"]["food"]
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {
        "kind": "card_action", "card": "D017", "params": {"cell": 0}}).new_state
    p = s["players"][first]
    assert p["cells"][0]["type"] == "field"
    assert p["resources"]["food"] == food_before - 3


def test_pulverizer_plow_plows_via_clay_pit(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "occ_woodcutter")
    put_in_play(s, first, "D019")
    clay_pit = next(sp for sp in s["action_spaces"] if sp["id"] == "clay_pit")
    clay_pit["supply"] = {"clay": 1}
    s = place(engine, s, {"kind": "place", "space": "clay_pit"})
    prompt = s["prompts"][0]
    cell = prompt["data"]["cells"][0]
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {"kind": "choice", "index": 1}).new_state
    p = s["players"][first]
    assert p["cells"][cell]["type"] == "field"
    assert p["resources"]["clay"] == 0  # gained 1 from the space, spent it to plow
    # the plowed clay-for-plow was placed back on the space's supply
    clay_pit = next(sp for sp in s["action_spaces"] if sp["id"] == "clay_pit")
    assert clay_pit["supply"].get("clay", 0) == 1


def test_retraining_exchanges_joinery_for_pottery(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "occ_woodcutter")
    put_in_play(s, first, "D027")
    p["improvements"].append("joinery")
    give(s, first, clay=2, reed=1)
    add_space(s, "house_redevelopment", "House Redevelopment")
    s = place(engine, s, {"kind": "place", "space": "house_redevelopment"})
    prompt = s["prompts"][0]
    assert "Exchange Joinery for Pottery" in prompt["options"]
    pid = p["player_id"]
    s = engine.apply_action(s, pid, {"kind": "choice", "index": 0}).new_state
    p = s["players"][first]
    assert "pottery" in p["improvements"]
    assert "joinery" not in p["improvements"]
    assert "joinery" in s["available_improvements"]


def test_artisan_district_score_bonus(engine):
    s = make_state(engine, 2)
    p = s["players"][0]
    p["improvements"] = ["joinery", "pottery", "basketmaker"]
    assert cards.CARDS["D030"]["score_bonus"](s, p, None) == 2
    p["improvements"].append("well")
    assert cards.CARDS["D030"]["score_bonus"](s, p, None) == 5


def test_storeroom_score_bonus_rounds_up(engine):
    s = make_state(engine, 2)
    p = s["players"][0]
    p["resources"]["grain"] = 3
    p["resources"]["vegetable"] = 1
    # min(3,1) = 1 pair -> 0.5 -> rounds up to 1.
    assert cards.CARDS["D031"]["score_bonus"](s, p, None) == 1
    p["resources"]["vegetable"] = 4
    # min(3,4) = 3 pairs -> 1.5 -> rounds up to 2.
    assert cards.CARDS["D031"]["score_bonus"](s, p, None) == 2


def test_wood_rake_snapshots_round_14(engine):
    s = make_state(engine, 2)
    p = s["players"][0]
    inst = {"id": "D032", "crops": None, "data": {}}
    p["cells"][0]["type"] = "field"
    p["cells"][0]["crops"] = {"type": "grain", "count": 3}
    p["cells"][1]["type"] = "field"
    p["cells"][1]["crops"] = {"type": "vegetable", "count": 4}
    hook = cards.CARDS["D032"]["hooks"]["round_start"]
    hook(s, p, inst, {"round": 13, "log": [], "actor": 0, "extra": {}})
    assert cards.CARDS["D032"]["score_bonus"](s, p, inst) == 0
    hook(s, p, inst, {"round": 14, "log": [], "actor": 0, "extra": {}})
    assert cards.CARDS["D032"]["score_bonus"](s, p, inst) == 2


def test_summer_house_score_bonus(engine):
    s = make_state(engine, 2)
    p = s["players"][0]
    p["house_type"] = "stone"
    assert cards.CARDS["D033"]["score_bonus"](s, p, None) > 0


def test_luxurious_hostel_needs_more_rooms_than_people(engine):
    s = make_state(engine, 2)
    p = s["players"][0]
    p["house_type"] = "stone"
    p["people_total"] = 2
    assert cards.CARDS["D034"]["score_bonus"](s, p, None) == 0
    p["cells"][0]["type"] = "room"
    p["cells"][1]["type"] = "room"
    assert cards.CARDS["D034"]["score_bonus"](s, p, None) == 4


def test_fodder_chamber_score_scales_with_player_count(engine):
    s = make_state(engine, 2)
    p = s["players"][0]
    p["pets"] = {"sheep": 5}
    assert cards.CARDS["D035"]["score_bonus"](s, p, None) == 1  # 5 // 5
    s["player_count"] = 4
    assert cards.CARDS["D035"]["score_bonus"](s, p, None) == 1  # 5 // 3


def test_cesspit_alternates_clay_and_boar(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "occ_woodcutter")
    p["cells"][0]["type"] = "field"
    p["cells"][1]["type"] = "field"
    give(s, first, food=0)
    give_card(s, first, "D040")
    s["round"] = 2
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "D040"}})
    inst = next(i for i in s["players"][first]["minors"] if i["id"] == "D040")
    hook = cards.CARDS["D040"]["hooks"]["round_start"]
    ctx = {"round": 3, "log": [], "actor": first, "extra": {}}
    hook(s, p, inst, ctx)
    assert ctx["extra"] == {"clay": 1}
    ctx2 = {"round": 4, "log": [], "actor": first, "extra": {}}
    hook(s, p, inst, ctx2)
    assert ctx2["extra"] == {"boar": 1}


def test_forest_well_schedules_food_by_wood(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "occ_woodcutter")
    put_in_play(s, first, "occ_reed_collector")
    give(s, first, stone=1, food=1, wood=3)
    give_card(s, first, "D044")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "D044"}})
    for r in ("2", "3", "4"):
        assert s["round_goods"][r][str(first)]["food"] == 1
    assert "5" not in s["round_goods"]


def test_pellet_press_once_per_round(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "occ_woodcutter")
    put_in_play(s, first, "occ_reed_collector")
    put_in_play(s, first, "D046")
    give(s, first, reed=2)
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {"kind": "card_action", "card": "D046"}).new_state
    assert s["players"][first]["resources"]["reed"] == 1
    with pytest.raises(ValueError):
        engine.apply_action(s, pid, {"kind": "card_action", "card": "D046"})


def test_civic_facade_round_income(engine):
    s = make_state(engine, 2)
    p = s["players"][0]
    p["hand_occupations"] = ["occ_woodcutter", "occ_cook"]
    p["hand_minors"] = ["minor_basket"]
    food = p["resources"]["food"]
    hooks = cards.CARDS["D048"]["hooks"]
    hooks["round_start"](s, p, {"id": "D048", "data": {}},
                         {"round": 2, "log": [], "actor": 0, "extra": {}})
    assert p["resources"]["food"] == food + 1


def test_trout_pool_checks_fishing_supply(engine):
    s = make_state(engine, 2)
    p = s["players"][0]
    fishing = next(sp for sp in s["action_spaces"] if sp["id"] == "fishing")
    fishing["supply"] = {"food": 2}
    hook = cards.CARDS["D054"]["hooks"]["round_start"]
    food = p["resources"]["food"]
    hook(s, p, {"id": "D054", "data": {}}, {"round": 2, "log": [], "actor": 0})
    assert p["resources"]["food"] == food
    fishing["supply"] = {"food": 3}
    hook(s, p, {"id": "D054", "data": {}}, {"round": 2, "log": [], "actor": 0})
    assert p["resources"]["food"] == food + 1


def test_new_market_bonus_on_rounds_8_to_11(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "D055")
    s["revealed"] = ["r1", "r2", "r3", "r4", "r5", "r6", "r7",
                    "day_laborer", "r9", "r10", "r11"]
    food_before = s["players"][first]["resources"]["food"]
    s = place(engine, s, {"kind": "place", "space": "day_laborer"})
    assert s["players"][first]["resources"]["food"] == food_before + 2 + 1


def test_gritter_grants_food_per_vegetable_field(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "D058")
    s["round"] = 5
    p["cells"][0]["type"] = "field"
    p["cells"][1]["type"] = "field"
    p["cells"][1]["crops"] = {"type": "vegetable", "count": 1}  # pre-existing
    give(s, first, vegetable=1)
    add_space(s, "grain_utilization", "Grain Utilization")
    food_before = p["resources"]["food"]
    s = place(engine, s, {"kind": "place", "space": "grain_utilization",
                          "sow": [{"cell": 0, "crop": "vegetable"}]})
    # 2 vegetable fields now (cell 0 freshly sown + cell 1) -> +2 food.
    assert s["players"][first]["resources"]["food"] == food_before + 2


def test_earth_oven_returns_fireplace_and_grants_cook_bake(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["improvements"].append("fireplace_2")
    s["available_improvements"].remove("fireplace_2")
    give_card(s, first, "D059")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "D059"}})
    p = s["players"][first]
    assert "fireplace_2" not in p["improvements"]
    assert "fireplace_2" in s["available_improvements"]
    assert cards.CARDS["D059"]["cook"]["sheep"] == 2
    assert cards.CARDS["D059"]["bake"] == (None, 2)


def test_large_pottery_conversion_and_score(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["improvements"].append("pottery")
    s["available_improvements"].remove("pottery")
    give(s, first, clay=1, stone=1)
    give_card(s, first, "D060")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "D060"}})
    p = s["players"][first]
    assert "pottery" not in p["improvements"]
    assert "pottery" in s["available_improvements"]
    conv = cards.conversion_options(p)
    key, spec_conv, _inst = next(c for c in conv if c[0].startswith("D060"))
    assert spec_conv == {"give": {"clay": 1}, "get": {"food": 2}}
    p["resources"]["clay"] = 5
    assert cards.CARDS["D060"]["score_bonus"](s, p, None) == 2


def test_bale_of_straw_harvest_bonus(engine):
    s = make_state(engine, 2)
    p = s["players"][0]
    for c in (0, 1, 2):
        p["cells"][c]["type"] = "field"
        p["cells"][c]["crops"] = {"type": "grain", "count": 1}
    hook = cards.CARDS["D061"]["hooks"]["harvest_field"]
    food = p["resources"]["food"]
    hook(s, p, {"id": "D061", "data": {}},
        {"harvest_index": 1, "log": [], "actor": 0, "extra": {}})
    assert p["resources"]["food"] == food + 2


def test_beer_tap_on_play_and_conversions(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give(s, first, wood=1)
    give_card(s, first, "D062")
    food_before = s["players"][first]["resources"]["food"]
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "D062"}})
    assert s["players"][first]["resources"]["food"] == food_before + 2
    assert cards.CARDS["D062"]["conversions"][2] == \
        {"give": {"grain": 4}, "get": {"food": 9}}


def test_lynchet_counts_fields_adjacent_to_house(engine):
    s = make_state(engine, 2)
    p = s["players"][0]
    # Cell 6 is adjacent to room cell 5 (starting wood room).
    p["cells"][6]["type"] = "field"
    p["cells"][2]["type"] = "field"  # not adjacent to any room
    hook = cards.CARDS["D063"]["hooks"]["harvest_field"]
    food = p["resources"]["food"]
    hook(s, p, {"id": "D063", "data": {}},
        {"harvest_index": 1, "log": [], "actor": 0, "extra": {}})
    assert p["resources"]["food"] == food + 1


def test_baking_course_offers_choice_on_non_harvest_rounds(engine):
    s = make_state(engine, 2)
    p = s["players"][0]
    p["resources"]["grain"] = 2
    inst = {"id": "D064", "crops": None, "data": {}}
    hook = cards.CARDS["D064"]["hooks"]["round_start"]
    ctx = {"round": 2, "log": [], "actor": 0, "extra": {}}  # prev=1, not a harvest
    hook(s, p, inst, ctx)
    assert len(s["prompts"]) == 1
    assert s["prompts"][0]["card"] == "D064"
    # Round 8's prev round (7) IS a harvest -> no bake offer.
    s["prompts"] = []
    hook(s, p, inst, {"round": 8, "log": [], "actor": 0, "extra": {}})
    assert s["prompts"] == []


def test_reap_hook_schedules_first_three_future_rounds(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give(s, first, wood=1)
    give_card(s, first, "D067")
    s["round"] = 5
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "D067"}})
    assert s["round_goods"]["7"][str(first)]["grain"] == 1
    assert s["round_goods"]["9"][str(first)]["grain"] == 1
    assert s["round_goods"]["11"][str(first)]["grain"] == 1
    assert "13" not in s["round_goods"]


def test_small_basket_exchanges_reed_for_vegetable(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "occ_woodcutter")
    put_in_play(s, first, "occ_reed_collector")
    put_in_play(s, first, "D068")
    reed_bank = next(sp for sp in s["action_spaces"] if sp["id"] == "reed_bank")
    reed_bank["supply"] = {"reed": 1}
    s = place(engine, s, {"kind": "place", "space": "reed_bank"})
    prompt = s["prompts"][0]
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {"kind": "choice", "index": 1}).new_state
    p = s["players"][first]
    assert p["resources"]["vegetable"] == 1


def test_changeover_resows_a_near_empty_field(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "D071")
    p["cells"][0]["type"] = "field"
    p["cells"][0]["crops"] = {"type": "grain", "count": 1}
    pid = p["player_id"]
    s = engine.apply_action(s, pid, {
        "kind": "card_action", "card": "D071",
        "params": {"kind": "cell", "cell": 0}}).new_state
    assert s["players"][first]["cells"][0]["crops"] == \
        {"type": "grain", "count": 3}


def test_stable_manure_extra_harvest_per_unfenced_stable(engine):
    s = make_state(engine, 2)
    p = s["players"][0]
    p["cells"][0]["stable"] = True  # unfenced stable
    p["cells"][1]["type"] = "field"
    p["cells"][1]["crops"] = {"type": "grain", "count": 3}
    hook = cards.CARDS["D072"]["hooks"]["harvest_field"]
    grain = p["resources"]["grain"]
    hook(s, p, {"id": "D072", "data": {}},
        {"harvest_index": 1, "log": [], "actor": 0, "extra": {}})
    assert p["resources"]["grain"] == grain + 1
    assert p["cells"][1]["crops"]["count"] == 2


def test_social_benefits_grants_wood_and_clay_when_broke(engine):
    s = make_state(engine, 2)
    p = s["players"][0]
    p["resources"]["food"] = 0
    inst = {"id": "D076", "data": {}}
    hook = cards.CARDS["D076"]["hooks"]["round_start"]
    ctx = {"round": 5, "log": [], "actor": 0, "extra": {}}  # prev round 4 (harvest)
    hook(s, p, inst, ctx)
    assert ctx["extra"] == {"wood": 1, "clay": 1}
    ctx2 = {"round": 6, "log": [], "actor": 0, "extra": {}}  # prev round 5 (no harvest)
    hook(s, p, inst, ctx2)
    assert ctx2["extra"] == {}


def test_brick_hammer_grants_stone_on_clay_heavy_build(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "D080")
    give(s, first, clay=2)
    add_space(s, "major_improvement", "Major Improvement")
    s = place(engine, s, {"kind": "place", "space": "major_improvement",
                          "improvement": "fireplace_2"})
    assert s["players"][first]["resources"]["stone"] == 1


def test_roof_ladder_cost_mod_and_bonus_stone(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "D081")
    give(s, first, clay=2)  # reed cost should be waived (2 rooms -> normally 1 reed)
    add_space(s, "house_redevelopment", "House Redevelopment")
    s = place(engine, s, {"kind": "place", "space": "house_redevelopment"})
    p = s["players"][first]
    assert p["house_type"] == "clay"
    assert p["resources"]["reed"] == 0  # never had any, and none was charged
    assert p["resources"]["stone"] == 1


def test_breed_registry_tracks_source_and_cooking(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    inst = put_in_play(s, first, "D036")
    assert cards.CARDS["D036"]["prereq"][0](s, p)  # starts with no sheep

    gained = cards.CARDS["D036"]["hooks"]["gained"]
    converted = cards.CARDS["D036"]["hooks"]["converted"]
    score = cards.CARDS["D036"]["score_bonus"]

    # 2 non-breeding sheep -> still within the "at most 2" limit.
    gained(s, p, inst, {"goods": {"sheep": 2}, "source": "space",
                       "log": [], "actor": first})
    assert inst["data"]["non_breeding_sheep"] == 2
    assert score(s, p, inst) == 3

    # A 3rd non-breeding sheep breaks the limit.
    gained(s, p, inst, {"goods": {"sheep": 1}, "source": "space",
                       "log": [], "actor": first})
    assert score(s, p, inst) == 0

    # Breeding-sourced sheep never count against the limit.
    inst["data"]["non_breeding_sheep"] = 0
    gained(s, p, inst, {"goods": {"sheep": 5}, "source": "breeding",
                       "log": [], "actor": first})
    assert inst["data"]["non_breeding_sheep"] == 0
    assert score(s, p, inst) == 3

    # Cooking a sheep zeroes the bonus regardless of the sheep count.
    converted(s, p, inst, {"give": {"sheep": 1}, "get": {"food": 2},
                          "actor": first})
    assert score(s, p, inst) == 0


def test_grain_sieve_bonus_grain_needs_2_harvested(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "D065")
    p["cells"][0]["type"] = "field"
    p["cells"][0]["crops"] = {"type": "grain", "count": 1}
    grain_before = p["resources"]["grain"]
    log = []
    engine._start_harvest(s, log)
    p = s["players"][first]
    assert p["resources"]["grain"] == grain_before + 1  # just the 1 harvested


def test_grain_sieve_bonus_grain_at_2_harvested(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "D065")
    p["cells"][0]["type"] = "field"
    p["cells"][0]["crops"] = {"type": "grain", "count": 3}
    p["cells"][1]["type"] = "field"
    p["cells"][1]["crops"] = {"type": "grain", "count": 3}
    grain_before = p["resources"]["grain"]
    log = []
    engine._start_harvest(s, log)
    p = s["players"][first]
    # 2 grain harvested (1 per field) + 1 bonus grain.
    assert p["resources"]["grain"] == grain_before + 2 + 1


def test_straw_manure_pays_grain_to_add_vegetables(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "D070")
    p["cells"][0]["type"] = "field"
    p["cells"][0]["crops"] = {"type": "vegetable", "count": 1}
    p["cells"][1]["type"] = "field"
    p["cells"][1]["crops"] = {"type": "vegetable", "count": 1}
    give(s, first, grain=1)
    veg_before = p["resources"]["vegetable"]
    log = []
    engine._start_harvest(s, log)
    p = s["players"][first]
    assert s["phase"] == "work"  # stalled on the prompt
    assert len(s["prompts"]) == 1
    prompt = s["prompts"][0]
    # Choose the combo that covers both fields.
    idx = next(i for i, combo in enumerate(prompt["data"]["combos"])
              if len(combo) == 2)
    pid = p["player_id"]
    s = engine.apply_action(s, pid, {"kind": "choice", "index": idx}).new_state
    p = s["players"][first]
    assert p["resources"]["grain"] == 0
    assert s["phase"] == "feeding"  # field phase ran once the prompt resolved
    # Straw Manure adds 1 to each field before the field phase (1 -> 2),
    # then the field phase's normal -1 harvest deduction brings each
    # back to 1 (not 0) -- net effect is the extra crop was harvested
    # instead of emptying the field.
    assert p["cells"][0]["crops"]["count"] == 1
    assert p["cells"][1]["crops"]["count"] == 1
    assert p["resources"]["vegetable"] == veg_before + 2


def test_hunting_trophy_space_conditioned_discounts(engine):
    """D082: majors/minors built via House Redevelopment get 1 building
    resource (the largest cost entry, standing in for "of your choice")
    off; fences via Farm Redevelopment get a flat 3 wood off. Neither
    discount applies when the build didn't come from that specific
    space."""
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "D082")

    imp_cost = cards.modified_cost(
        s, p, "improvement", {"wood": 2, "clay": 3},
        {"space_id": "house_redevelopment"})
    assert imp_cost == {"wood": 2, "clay": 2}  # 1 off the largest entry

    minor_cost = cards.modified_cost(
        s, p, "minor", {"stone": 1},
        {"space_id": "house_redevelopment"})
    assert minor_cost == {}  # discounted to 0, dropped

    fence_cost = cards.modified_cost(
        s, p, "fences", {"wood": 5},
        {"count": 5, "space_id": "farm_redevelopment"})
    assert fence_cost == {"wood": 2}

    # Wrong space (or no space at all): no discount.
    unaffected = cards.modified_cost(
        s, p, "improvement", {"wood": 2, "clay": 3},
        {"space_id": "major_improvement"})
    assert unaffected == {"wood": 2, "clay": 3}
    unaffected_fences = cards.modified_cost(
        s, p, "fences", {"wood": 5}, {"count": 5})
    assert unaffected_fences == {"wood": 5}


def test_hunting_trophy_prereq_and_play_consumes_boar(engine):
    """D082's prereq/cost is 'return or cook 1 wild boar', not a plain
    resource dict: unplayable with no wild boar, and playing it removes
    one -- for food if a cooking improvement is owned, otherwise a plain
    return."""
    s = make_state(engine, 2)
    first = s["current_player"]
    give_card(s, first, "D082")
    add_space(s, "meeting_place", "Meeting Place")
    with pytest.raises(ValueError):
        place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "D082"}})

    s2 = make_state(engine, 2)
    first2 = s2["current_player"]
    p2 = s2["players"][first2]
    give_card(s2, first2, "D082")
    p2["cells"][0]["animal"] = {"type": "boar", "count": 1}
    food_before2 = p2["resources"]["food"]
    add_space(s2, "meeting_place", "Meeting Place")
    s2 = place(engine, s2, {"kind": "place", "space": "meeting_place",
                            "minor": {"card": "D082"}})
    p2 = s2["players"][first2]
    assert p2["cells"][0]["animal"] is None  # the boar was consumed
    assert p2["resources"]["food"] == food_before2  # no oven/hearth -> plain return

    s3 = make_state(engine, 2)
    first3 = s3["current_player"]
    p3 = s3["players"][first3]
    give_card(s3, first3, "D082")
    p3["cells"][0]["animal"] = {"type": "boar", "count": 1}
    p3["improvements"].append("cooking_hearth_4")
    food_before3 = p3["resources"]["food"]
    add_space(s3, "meeting_place", "Meeting Place")
    s3 = place(engine, s3, {"kind": "place", "space": "meeting_place",
                            "minor": {"card": "D082"}})
    p3 = s3["players"][first3]
    assert p3["cells"][0]["animal"] is None
    # Cooking Hearth cooks boar for 3.
    assert p3["resources"]["food"] == food_before3 + 3


def test_lawn_fertilizer_size1_capacity_and_house_lockout(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "D011")
    p["fences"] = sorted(cell_edges(4))  # size-1 pasture at cell 4

    assert cards.pasture_capacity(s, p, [4], "sheep") == 3  # 2 base + 1
    p["cells"][4]["stable"] = True
    assert cards.pasture_capacity(s, p, [4], "sheep") == 6  # 2*2 base + 2

    # A differently-sized pasture is unaffected (size-1 mod doesn't apply).
    p["cells"][4]["stable"] = False
    fences = set(cell_edges(3)) | set(cell_edges(4))
    fences.discard("v-0-4")
    p["fences"] = sorted(fences)
    assert cards.pasture_capacity(s, p, [3, 4], "sheep") == 4  # plain 2*2

    # No animals in the house, not even via another card.
    assert cards.house_capacity(s, p) == 0

    food = p["resources"]["food"]
    hook = cards.CARDS["D011"]["hooks"]["harvest_field"]
    hook(s, p, {"id": "D011", "data": {}},
        {"harvest_index": 1, "log": [], "actor": first, "extra": {}})
    assert p["resources"]["food"] == food + 1


def test_pioneering_spirit_owner_only_and_round_windows(engine):
    """D023 card_space: owner_only, unusable outside rounds 3-8, a
    Renovation action in rounds 3-5, a choice of 1 vegetable/wild boar/
    cattle in rounds 6-8."""
    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    give_card(s, first, "D023")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "D023"}})
    first_pid = s["players"][first]["player_id"]
    other_pid = s["players"][other]["player_id"]

    # Round 1: outside the window -- unusable even by the owner.
    s["current_player"] = first
    valid = engine.get_valid_actions(s, first_pid)
    assert not any(a["kind"] == "place" and a["space"] == "card:D023"
                  for a in valid)

    # Round 3: in the window, but owner_only -- not offered to `other`.
    s["round"] = 3
    s["current_player"] = other
    valid = engine.get_valid_actions(s, other_pid)
    assert not any(a["kind"] == "place" and a["space"] == "card:D023"
                  for a in valid)

    s["current_player"] = first
    p = s["players"][first]
    p["house_type"] = "wood"
    p["resources"]["clay"] = 10
    p["resources"]["reed"] = 5
    rooms_before = sum(1 for c in p["cells"] if c["type"] == "room")
    s = engine.apply_action(
        s, first_pid, {"kind": "place", "space": "card:D023"}).new_state
    p = s["players"][first]
    assert p["house_type"] == "clay"
    assert p["resources"]["clay"] == 10 - rooms_before  # room count x 1 clay
    assert p["resources"]["reed"] == 5 - 1

    # Round 6: the choice-of-goods window.
    s["round"] = 6
    s["current_player"] = first
    s["players"][first]["people_placed"] = 0
    space = next(sp for sp in s["action_spaces"] if sp["id"] == "card:D023")
    space["occupied_by"] = None
    space["extra_occupants"] = []
    s = engine.apply_action(
        s, first_pid, {"kind": "place", "space": "card:D023"}).new_state
    assert s["prompts"][0]["type"] == "choice"
    assert s["prompts"][0]["options"] == \
        ["1 vegetable", "1 wild boar", "1 cattle"]
    veg_before = s["players"][first]["resources"]["vegetable"]
    s = engine.apply_action(s, first_pid, {"kind": "choice", "index": 0}).new_state
    assert s["players"][first]["resources"]["vegetable"] == veg_before + 1
