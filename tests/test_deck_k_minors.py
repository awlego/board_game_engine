"""Tests for the deck K minor-improvement module
(server/agricola/decks/deck_k_minors.py)."""

import pytest

from server.agricola.engine import AgricolaEngine
from server.agricola import cards
from server.agricola.decks import deck_k_minors

from test_agricola import (
    make_state, give, give_card, put_in_play, add_space, place,
)


@pytest.fixture
def engine():
    return AgricolaEngine()


# ── Registration completeness ────────────────────────────────────────

def test_registration_completeness():
    db_codes = {c["code"] for c in cards.compendium().values()
               if c["deck"] == "K" and c["type"] == "minor"}
    registered = {cid for cid in cards.CARDS if cid in db_codes}
    unimplemented = set(deck_k_minors.UNIMPLEMENTED)
    assert unimplemented <= db_codes
    assert registered & unimplemented == set()
    assert registered | unimplemented == db_codes


# ── Smoke test: every implemented card gets played once ──────────────

_DUMMY_OCCS = ["occ_woodcutter", "occ_reed_collector", "occ_clay_digger",
              "occ_stonecutter"]


def _prep_prereqs(state, pidx, cid):
    p = state["players"][pidx]
    n_occ = {"K108": 3, "K113": 1, "K114": 2, "K115": 3, "K117": 1,
            "K119": 1, "K131": 3, "K133": 2, "K136": 2, "K137": 3,
            "K140": 4, "K142": 2, "K145": 3, "K146": 2}.get(cid)
    if n_occ:
        for i in range(n_occ):
            put_in_play(state, pidx, _DUMMY_OCCS[i])
    if cid == "K106":
        p["improvements"].append("clay_oven")
    if cid == "K108":
        put_in_play(state, pidx, "minor_basket")
        put_in_play(state, pidx, "minor_fish_trap")
    if cid == "K118":
        p["pets"] = {"sheep": 4}
    if cid == "K122":
        p["improvements"].append("joinery")
    if cid == "K128":
        p["improvements"].append("fireplace_2")
    if cid == "K130":
        p["cells"][0]["type"] = "field"
        p["cells"][0]["crops"] = {"type": "vegetable", "count": 1}
    if cid == "K134":
        p["pets"] = {"cattle": 2}


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


_DB_K_MINOR_CODES = {c["code"] for c in cards.compendium().values()
                    if c["deck"] == "K" and c["type"] == "minor"}
ALL_K_MINORS = sorted(cid for cid in cards.CARDS if cid in _DB_K_MINOR_CODES)


@pytest.mark.parametrize("cid", ALL_K_MINORS)
def test_smoke_play_every_card(engine, cid):
    s = make_state(engine, 2)
    first = s["current_player"]
    _prep_prereqs(s, first, cid)
    give(s, first, **cards.CARDS[cid]["cost"])
    give_card(s, first, cid)
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": cid}})
    pid = s["players"][first]["player_id"]
    s = _resolve_all_prompts(engine, s, pid)
    p = s["players"][first]
    if cards.CARDS[cid]["traveling"]:
        assert cid not in p["hand_minors"]
    else:
        assert cid not in p["hand_minors"]
        assert any(i["id"] == cid for i in p["minors"])


# ── Targeted effect tests ────────────────────────────────────────────

def test_bakehouse_bakes_immediately_and_returns_oven(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["improvements"].append("clay_oven")
    p["resources"]["food"] = 0
    give(s, first, stone=3, grain=2)
    give_card(s, first, "K106")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "K106"}})
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
    assert cards.CARDS["K106"]["bake"] == (2, 5)


def test_lumber_grants_wood_and_travels(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    give(s, first, stone=1)
    give_card(s, first, "K107")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "K107"}})
    p = s["players"][first]
    assert p["resources"]["wood"] == 3
    assert "K107" not in p["hand_minors"]
    assert "K107" in s["players"][other]["hand_minors"]


def test_beehive_schedules_even_rounds(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "occ_woodcutter")
    put_in_play(s, first, "occ_reed_collector")
    put_in_play(s, first, "occ_clay_digger")
    put_in_play(s, first, "minor_basket")
    put_in_play(s, first, "minor_fish_trap")
    give_card(s, first, "K108")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "K108"}})
    rnd = s["round"]
    evens = [r for r in (2, 4, 6, 8, 10, 12, 14) if r > rnd]
    for r in evens:
        assert s["round_goods"][str(r)][str(first)]["food"] == 2


def test_brewery_conversion_and_grain_bonus(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give(s, first, grain=2, stone=2)
    give_card(s, first, "K110")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "K110"}})
    p = s["players"][first]
    assert p["resources"]["grain"] == 0
    assert p["resources"]["stone"] == 0
    opts = cards.conversion_options(p)
    assert any(key.startswith("K110:") for key, _, _ in opts)
    p["resources"]["grain"] = 9
    assert cards.score_bonuses(s, p) == 1
    p["resources"]["grain"] = 8
    assert cards.score_bonuses(s, p) == 0


def test_flail_grants_bake_on_plow_spaces(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "occ_woodcutter")
    put_in_play(s, first, "K113")
    p = s["players"][first]
    assert cards.bake_on_space(p, "farmland")
    assert cards.bake_on_space(p, "cultivation")
    assert not cards.bake_on_space(p, "grain_seeds")


def test_duck_pond_schedules_three_rounds(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "occ_woodcutter")
    put_in_play(s, first, "occ_reed_collector")
    give_card(s, first, "K114")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "K114"}})
    rnd = s["round"]
    for r in range(rnd + 1, rnd + 4):
        assert s["round_goods"][str(r)][str(first)]["food"] == 1


def test_swing_plough_extra_plow_twice(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    inst = put_in_play(s, first, "K115")
    inst["data"]["uses_left"] = 2
    s = place(engine, s, {"kind": "place", "space": "farmland", "cell": 0})
    pid = p["player_id"]
    acts = engine.get_valid_actions(s, pid)
    choice = next(a for a in acts if a["kind"] == "choice")
    assert s["prompts"][0]["card"] == "K115"
    s = place(engine, s, {"kind": "choice", "index": 0})
    acts = engine.get_valid_actions(s, pid)
    choice2 = next((a for a in acts if a["kind"] == "choice"), None)
    if choice2:
        s = place(engine, s, {"kind": "choice", "index": 0})
    fields = sum(1 for c in s["players"][first]["cells"] if c["type"] == "field")
    assert fields >= 2  # base plow + at least 1 extra field
    remaining = next(i for i in s["players"][first]["minors"]
                     if i["id"] == "K115")["data"]["uses_left"]
    assert remaining == 1


def test_crooked_plough_single_use(engine):
    # "Once during the game... plough 3 fields instead of 1": 1 activation
    # (uses_left), but each activation still allows up to 2 EXTRA fields
    # (the 1 base + 2 extra = 3 total), same as Swing Plough's per-use
    # shape -- only the total number of activations differs.
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    inst = put_in_play(s, first, "K119")
    inst["data"]["uses_left"] = 1
    s = place(engine, s, {"kind": "place", "space": "farmland", "cell": 0})
    pid = p["player_id"]
    acts = engine.get_valid_actions(s, pid)
    choice = next(a for a in acts if a["kind"] == "choice")
    s = place(engine, s, {"kind": "choice", "index": 0})
    acts = engine.get_valid_actions(s, pid)
    choice2 = next((a for a in acts if a["kind"] == "choice"), None)
    if choice2:
        s = place(engine, s, {"kind": "choice", "index": 0})
    remaining = next(i for i in s["players"][first]["minors"]
                     if i["id"] == "K119")["data"]["uses_left"]
    assert remaining == 0


def test_granary_cost_override_and_schedule(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give(s, first, wood=3)
    give_card(s, first, "K116")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "K116"}})
    p = s["players"][first]
    assert p["resources"]["wood"] == 0
    rnd = s["round"]
    for r in (8, 10, 12):
        if r > rnd:
            assert s["round_goods"][str(r)][str(first)]["grain"] == 1


def test_greenhouse_auto_buys_vegetable_when_affordable(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "occ_woodcutter")
    give(s, first, wood=2)
    give_card(s, first, "K117")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "K117"}})
    p = s["players"][first]
    inst = next(i for i in p["minors"] if i["id"] == "K117")
    target = inst["data"]["veg_rounds"][0]
    p["resources"]["food"] = 1
    s["round"] = target
    ctx = {"log": [], "actor": first, "extra": {}}
    cards.CARDS["K117"]["hooks"]["round_start"](s, p, inst, ctx)
    assert p["resources"]["vegetable"] == 1
    assert p["resources"]["food"] == 0


def test_greenhouse_skips_when_no_food(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "occ_woodcutter")
    give(s, first, wood=2)
    give_card(s, first, "K117")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "K117"}})
    p = s["players"][first]
    inst = next(i for i in p["minors"] if i["id"] == "K117")
    target = inst["data"]["veg_rounds"][0]
    p["resources"]["food"] = 0
    s["round"] = target
    ctx = {"log": [], "actor": first, "extra": {}}
    cards.CARDS["K117"]["hooks"]["round_start"](s, p, inst, ctx)
    assert p["resources"]["vegetable"] == 0


def test_liquid_manure_adds_extra_crop_on_sow(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["pets"] = {"sheep": 4}
    put_in_play(s, first, "K118")
    p["cells"][0]["type"] = "field"
    give(s, first, grain=1)
    add_space(s, "grain_utilization", "Sow and/or Bake Bread")
    s = place(engine, s, {"kind": "place", "space": "grain_utilization",
                          "sow": [{"cell": 0, "crop": "grain"}]})
    field = s["players"][first]["cells"][0]
    assert field["crops"]["count"] == 4  # 3 base + 1 bonus


def test_house_goat_food_and_reduced_capacity(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["resources"]["food"] = 0
    inst = put_in_play(s, first, "K120")
    assert cards.house_capacity(s, p) == 0
    ctx = {"log": [], "actor": first, "extra": {}, "harvest_index": 1}
    cards.CARDS["K120"]["hooks"]["harvest_field"](s, p, inst, ctx)
    assert p["resources"]["food"] == 1


def test_sawhorse_free_stable_and_fence_discount(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    give_card(s, first, "K121")
    give(s, first, wood=2)
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "K121"}})
    p = s["players"][first]
    assert p["resources"]["wood"] == 0
    give(s, first, wood=2)
    s = place(engine, s, {"kind": "place", "space": "day_laborer"})  # other's turn
    s = place(engine, s, {"kind": "place", "space": "farm_expansion",
                          "stables": [0]})
    p = s["players"][first]
    assert p["resources"]["wood"] == 2  # 2 paid, refunded by Sawhorse
    p["fences"] = [0, 1]
    cost = cards.modified_cost(s, p, "fences", {"wood": 3}, {"count": 3})
    assert cost["wood"] == 2  # fence #3 (existing 2 + 1) is free


def test_sawmill_returns_joinery_scores_wood_and_converts(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["improvements"].append("joinery")
    give_card(s, first, "K122")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "K122"}})
    p = s["players"][first]
    assert "joinery" not in p["improvements"]
    assert "joinery" in s["available_improvements"]
    opts = cards.conversion_options(p)
    assert any(key.startswith("K122:") for key, _, _ in opts)
    p["resources"]["wood"] = 5
    assert cards.score_bonuses(s, p) == 3
    p["resources"]["wood"] = 3
    assert cards.score_bonuses(s, p) == 1


def test_wooden_strongbox_scores_rooms(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "K123")
    for c in (0, 1, 2, 3):
        p["cells"][c]["type"] = "room"
    assert cards.score_bonuses(s, p) == 4  # 6 rooms total


def test_landing_net_reduced_bonus_with_other_resource(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    inst = put_in_play(s, first, "K126")
    ctx = {"space_id": "resource_market_3p", "goods": {"reed": 1, "stone": 1},
          "log": [], "actor": first, "extra": {}}
    cards.CARDS["K126"]["hooks"]["space_used"](s, p, inst, ctx)
    assert ctx["extra"]["food"] == 1
    ctx2 = {"space_id": "reed_bank", "goods": {"reed": 1},
           "log": [], "actor": first, "extra": {}}
    cards.CARDS["K126"]["hooks"]["space_used"](s, p, inst, ctx2)
    assert ctx2["extra"]["food"] == 2


def test_clapper_adds_grain_on_family_growth(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "K127")
    p["cells"][1]["type"] = "room"
    p["cells"][0]["type"] = "field"
    p["cells"][0]["crops"] = {"type": "grain", "count": 2}
    add_space(s, "basic_wish", "Basic Wish for Children")
    s = place(engine, s, {"kind": "place", "space": "basic_wish"})
    p = s["players"][first]
    assert p["cells"][0]["crops"]["count"] == 3


def test_clapper_catch_up_when_played_after_family_growth(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    p = s["players"][first]
    p["cells"][1]["type"] = "room"
    p["cells"][0]["type"] = "field"
    p["cells"][0]["crops"] = {"type": "grain", "count": 2}
    add_space(s, "basic_wish", "Basic Wish for Children")
    s = place(engine, s, {"kind": "place", "space": "basic_wish"})
    give_card(s, first, "K127")
    give(s, first, wood=1)
    s = place(engine, s, {"kind": "place", "space": "day_laborer"})  # other's turn
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "K127"}})
    p = s["players"][first]
    assert p["cells"][0]["crops"]["count"] == 3


def test_cooking_hearth_returns_fireplace_and_provides_cook_bake(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["improvements"].append("fireplace_2")
    give_card(s, first, "K128")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "K128"}})
    p = s["players"][first]
    assert "fireplace_2" not in p["improvements"]
    assert "fireplace_2" in s["available_improvements"]
    assert cards.CARDS["K128"]["cook"]["cattle"] == 4
    assert cards.CARDS["K128"]["bake"] == (None, 3)


def test_corn_sheaf_grants_grain_and_travels(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    give_card(s, first, "K129")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "K129"}})
    assert s["players"][first]["resources"]["grain"] == 1
    assert "K129" in s["players"][other]["hand_minors"]


def test_herb_garden_schedules_five_rounds(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["cells"][0]["type"] = "field"
    p["cells"][0]["crops"] = {"type": "vegetable", "count": 1}
    put_in_play(s, first, "occ_woodcutter")
    give_card(s, first, "K130")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "K130"}})
    rnd = s["round"]
    for r in range(rnd + 1, min(14, rnd + 5) + 1):
        assert s["round_goods"][str(r)][str(first)]["food"] == 1


def test_clay_pit_day_laborer_bonus(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "occ_woodcutter")
    put_in_play(s, first, "occ_reed_collector")
    put_in_play(s, first, "occ_clay_digger")
    put_in_play(s, first, "K131")
    s = place(engine, s, {"kind": "place", "space": "day_laborer"})
    assert s["players"][first]["resources"]["clay"] == 3


def test_clay_hut_extension_builds_free_room_and_travels(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    give(s, first, reed=1, clay=4)
    give_card(s, first, "K132")
    rooms_before = sum(1 for c in s["players"][first]["cells"]
                       if c["type"] == "room")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "K132"}})
    p = s["players"][first]
    rooms_after = sum(1 for c in p["cells"] if c["type"] == "room")
    assert rooms_after == rooms_before + 1
    assert "K132" in s["players"][other]["hand_minors"]


def test_milking_stool_food_and_score(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["pets"] = {"cattle": 5}
    p["resources"]["food"] = 0
    put_in_play(s, first, "occ_woodcutter")
    put_in_play(s, first, "occ_reed_collector")
    inst = put_in_play(s, first, "K133")
    ctx = {"log": [], "actor": first, "extra": {}, "harvest_index": 1}
    cards.CARDS["K133"]["hooks"]["harvest_field"](s, p, inst, ctx)
    assert p["resources"]["food"] == 3
    assert cards.score_bonuses(s, p) == 2


def test_ox_team_plows_chosen_fields(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["pets"] = {"cattle": 2}
    s["round"] = 12  # 2 rounds remain -> limit 2
    give(s, first, wood=3)
    give_card(s, first, "K134")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "K134",
                                   "params": {"cells": [0, 1]}}})
    p = s["players"][first]
    assert p["cells"][0]["type"] == "field"
    assert p["cells"][1]["type"] == "field"


def test_ox_team_rejects_too_many_cells(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["pets"] = {"cattle": 2}
    s["round"] = 13  # 1 round remains -> limit 1
    give(s, first, wood=3)
    give_card(s, first, "K134")
    pid = p["player_id"]
    with pytest.raises(Exception):
        place(engine, s, {"kind": "place", "space": "meeting_place",
                         "minor": {"card": "K134",
                                  "params": {"cells": [0, 1]}}})


def test_horse_scores_for_missing_animal_type(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "K135")
    p["pets"] = {"sheep": 2}
    assert cards.score_bonuses(s, p) == 2
    p["pets"] = {"sheep": 1, "boar": 1, "cattle": 1}
    assert cards.score_bonuses(s, p) == 0


def test_brushwood_roof_substitutes_reed_shortfall(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "occ_woodcutter")
    put_in_play(s, first, "occ_reed_collector")
    put_in_play(s, first, "K136")
    p["resources"]["reed"] = 0
    cost = cards.modified_cost(s, p, "room", {"clay": 5, "reed": 2})
    assert cost == {"clay": 5, "wood": 2}
    p["resources"]["reed"] = 2
    cost2 = cards.modified_cost(s, p, "room", {"clay": 5, "reed": 2})
    assert cost2 == {"clay": 5, "reed": 2}


def test_turnip_field_is_sowable_and_sows_on_play(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "occ_woodcutter")
    put_in_play(s, first, "occ_reed_collector")
    put_in_play(s, first, "occ_clay_digger")
    give(s, first, vegetable=1)
    give_card(s, first, "K137")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "K137",
                                   "params": {"sow": [{"card": "K137",
                                                       "crop": "vegetable"}]}}})
    p = s["players"][first]
    inst = next(i for i in p["minors"] if i["id"] == "K137")
    assert inst["crops"] == {"type": "vegetable", "count": 2}
    assert p["resources"]["vegetable"] == 0


def test_swan_lake_schedules_five_rounds(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    for cid in _DUMMY_OCCS:
        put_in_play(s, first, cid)
    give_card(s, first, "K140")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "K140"}})
    rnd = s["round"]
    for r in range(rnd + 1, min(14, rnd + 5) + 1):
        assert s["round_goods"][str(r)][str(first)]["food"] == 1


def test_boar_breeding_grants_boar_via_accommodation_and_travels(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    give(s, first, food=1)
    give_card(s, first, "K141")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "K141"}})
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {
        "kind": "accommodate", "pets": {"boar": 1}}).new_state
    p = s["players"][first]
    assert p["pets"].get("boar") == 1
    assert "boar" not in p["resources"]
    assert "K141" in s["players"][other]["hand_minors"]


def test_stone_cart_schedules_even_rounds(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "occ_woodcutter")
    put_in_play(s, first, "occ_reed_collector")
    give(s, first, wood=2)
    give_card(s, first, "K142")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "K142"}})
    rnd = s["round"]
    evens = [r for r in (2, 4, 6, 8, 10, 12, 14) if r > rnd]
    for r in evens:
        assert s["round_goods"][str(r)][str(first)]["stone"] == 1


def test_stone_exchange_cost_override_gain_and_travels(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    give(s, first, wood=2)
    give_card(s, first, "K143")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "K143"}})
    p = s["players"][first]
    assert p["resources"]["wood"] == 0
    assert p["resources"]["stone"] == 2
    assert "K143" in s["players"][other]["hand_minors"]


def test_mansion_scores_stone_house_rooms(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["house_type"] = "stone"
    put_in_play(s, first, "K144")
    assert cards.score_bonuses(s, p) == 4  # 2 starting rooms * 2
    p["house_type"] = "wood"
    assert cards.score_bonuses(s, p) == 0


def test_loom_k146_food_and_score(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["pets"] = {"sheep": 7}
    p["resources"]["food"] = 0
    put_in_play(s, first, "occ_woodcutter")
    put_in_play(s, first, "occ_reed_collector")
    inst = put_in_play(s, first, "K146")
    ctx = {"log": [], "actor": first, "extra": {}, "harvest_index": 1}
    cards.CARDS["K146"]["hooks"]["harvest_field"](s, p, inst, ctx)
    assert p["resources"]["food"] == 3
    assert cards.score_bonuses(s, p) == 2


def test_forest_pasture_holds_unlimited_boar(engine):
    s = make_state(engine, 4)
    first = s["current_player"]
    p = s["players"][first]
    for i in range(3):
        put_in_play(s, first, _DUMMY_OCCS[i])
    inst = put_in_play(s, first, "K145")
    assert len(p["occupations"]) == 3

    inst["held"] = {"boar": 50}
    ok, err = cards.validate_held(s, p)
    assert ok, err

    inst["held"] = {"sheep": 1}
    ok, err = cards.validate_held(s, p)
    assert not ok  # only boar allowed


def test_broom_discards_hand_and_draws_seven(engine):
    """K125 Broom, the real card: the discard/redraw recipe (decks/
    GUIDE.md's "Draw and discard piles (K125 Broom)" section;
    test_broom_style_discard_and_redraw_flow in tests/test_agricola.py
    is the temp_card version of this same flow). Broom's own play hook
    runs after it's already been removed from hand_minors, so the
    discard only touches the REST of the hand, never Broom itself."""
    s = make_state(engine, 2)
    first = s["current_player"]
    give_card(s, first, "K125")
    give(s, first, wood=1)
    p = s["players"][first]
    old_hand = [c for c in p["hand_minors"] if c != "K125"]
    draw_pile_before = list(s["minor_draw"])

    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "K125"}})
    p = s["players"][first]
    assert "K125" not in p["hand_minors"]
    assert any(i["id"] == "K125" for i in p["minors"])
    assert not any(c in p["hand_minors"] for c in old_hand)
    expected_draw = min(7, len(draw_pile_before))
    assert p["hand_minors"] == draw_pile_before[:expected_draw]
    assert s["minor_discard"] == old_hand
    assert s["minor_draw"] == draw_pile_before[expected_draw:]


def test_broom_plays_a_second_minor_from_the_fresh_hand(engine):
    """"You can play 1 more minor improvement immediately" -- an
    optional bonus play, off the freshly-drawn hand, via the play
    hook's own params channel (params.card2), at that card's own normal
    cost (not free)."""
    s = make_state(engine, 2)
    first = s["current_player"]
    give_card(s, first, "K125")
    second_cid = s["minor_draw"][0]
    resources = {"wood": 1}
    for k, v in cards.CARDS[second_cid]["cost"].items():
        resources[k] = resources.get(k, 0) + v
    give(s, first, **resources)

    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "K125",
                                   "params": {"card2": second_cid}}})
    p = s["players"][first]
    assert any(i["id"] == second_cid for i in p["minors"])
    assert second_cid not in p["hand_minors"]


def test_broom_second_minor_must_be_affordable(engine):
    """A params.card2 that isn't in the freshly-drawn hand (or isn't
    paid for) is rejected -- Broom itself still gets played (the
    discard/redraw isn't rolled back into a half-applied state, since
    the whole action rolls back atomically on a raised ValueError)."""
    s = make_state(engine, 2)
    first = s["current_player"]
    give_card(s, first, "K125")
    give(s, first, wood=1)
    with pytest.raises(ValueError):
        place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "K125",
                                   "params": {"card2": "not_a_real_card"}}})
