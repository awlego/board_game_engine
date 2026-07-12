"""Tests for the deck FR minor-improvement module
(server/agricola/decks/deck_fr_minors.py)."""

import pytest

from server.agricola.engine import AgricolaEngine
from server.agricola import cards, sub_actions
from server.agricola.decks import deck_fr_minors as m
from server.agricola.state import (
    cell_edges, MAJOR_IMPROVEMENTS, plowable_cells, validate_fence_layout,
    table_score,
)
from server.agricola.scoring import score_player

from test_agricola import (
    make_state, give, give_card, put_in_play, add_space, place, current_pid,
)


@pytest.fixture
def engine():
    return AgricolaEngine()


# ── Registration completeness ────────────────────────────────────────

def test_registration_completeness():
    db_codes = {c["code"] for c in cards.compendium().values()
               if c["deck"] == "FR" and c["type"] == "minor"}
    registered = {cid for cid in cards.CARDS if cid in db_codes}
    unimplemented = set(m.UNIMPLEMENTED)
    assert unimplemented <= db_codes
    assert registered & unimplemented == set()
    assert registered | unimplemented == db_codes


# ── Smoke test: every implemented card gets played once ──────────────

_DUMMY_OCCS = ["occ_woodcutter", "occ_reed_collector", "occ_clay_digger",
              "occ_shepherd"]

_NEEDS_OCC = {"FR002": 3, "FR011": 2, "FR012": 2, "FR017": 2, "FR026": 2,
             "FR037": 1, "FR052": 3, "FR053": 2, "FR054": 4}

_PLAY_PARAMS = {
    "FR001": {"cell": 0},
    "FR006": {"space": "day_laborer"},
    "FR012": {"spaces": ["forest", "clay_pit", "reed_bank"]},
}


def _prep_prereqs(state, pidx, cid):
    p = state["players"][pidx]
    n = _NEEDS_OCC.get(cid)
    if n:
        for i in range(n):
            put_in_play(state, pidx, _DUMMY_OCCS[i])
    if cid == "FR001":
        p["cells"][0]["type"] = "field"
    if cid == "FR003":
        p["fences"] = sorted(cell_edges(4))
    if cid == "FR013":
        p["pets"]["sheep"] = 1
    if cid == "FR016":
        p["cells"][0]["type"] = "field"
    if cid == "FR021":
        for c in range(4):
            p["cells"][c]["type"] = "field"
            p["cells"][c]["crops"] = {"type": "grain", "count": 1}
    if cid == "FR022":
        p["pets"]["sheep"] = 3
    if cid == "FR024":
        p["cells"][0]["type"] = "field"
        p["cells"][0]["crops"] = {"type": "grain", "count": 1}
    if cid == "FR028":
        for c in range(4):
            p["cells"][c]["type"] = "room"
        p["pets"]["sheep"] = 1
    if cid == "FR030":
        p["pets"] = {"sheep": 3, "boar": 2, "cattle": 1}
    if cid == "FR031":
        p["house_type"] = "clay"
    if cid == "FR035":
        p["house_type"] = "stone"
    if cid == "FR038":
        p["cells"][0]["type"] = "field"
        p["cells"][0]["crops"] = {"type": "grain", "count": 1}
    if cid == "FR041":
        p["improvements"].append("basketmaker")
    if cid == "FR044":
        p["house_type"] = "stone"
    if cid == "FR046":
        for c in range(2):
            p["cells"][c]["type"] = "field"
            p["cells"][c]["crops"] = {"type": "grain", "count": 1}
    if cid in ("FR049", "FR055"):
        p["improvements"].append("clay_oven")
    if cid == "FR057":
        p["pets"]["boar"] = 1


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


_DB_FR_MINOR_CODES = {c["code"] for c in cards.compendium().values()
                      if c["deck"] == "FR" and c["type"] == "minor"}
ALL_FR_MINORS = sorted(cid for cid in cards.CARDS if cid in _DB_FR_MINOR_CODES)


@pytest.mark.parametrize("cid", ALL_FR_MINORS)
def test_smoke_play_every_card(engine, cid):
    s = make_state(engine, 2)
    first = s["current_player"]
    _prep_prereqs(s, first, cid)
    give(s, first, **sub_actions.cost_alternatives(cards.CARDS[cid]["cost"])[0])
    give_card(s, first, cid)
    minor = {"card": cid}
    if cid in _PLAY_PARAMS:
        minor["params"] = _PLAY_PARAMS[cid]
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": minor})
    pid = s["players"][first]["player_id"]
    s = _resolve_all_prompts(engine, s, pid)
    p = s["players"][first]
    assert cid not in p["hand_minors"]
    assert any(i["id"] == cid for i in p["minors"])


# ── Targeted effect tests ────────────────────────────────────────────

def test_absinthe_places_food_on_chosen_accumulation_space(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    for i in range(3):
        put_in_play(s, first, _DUMMY_OCCS[i])
    give_card(s, first, "FR002")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "FR002"}})
    p = s["players"][first]
    inst = next(i for i in p["minors"] if i["id"] == "FR002")
    assert inst["data"]["rounds"] == [2, 3, 4, 5, 6]
    s = place(engine, s, {"kind": "place", "space": "day_laborer"})  # other's turn
    s["round"] = 2
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {
        "kind": "card_action", "card": "FR002",
        "params": {"space_id": "forest"}}).new_state
    forest = next(sp for sp in s["action_spaces"] if sp["id"] == "forest")
    assert forest["supply"]["food"] == 1
    inst = next(i for i in s["players"][first]["minors"] if i["id"] == "FR002")
    assert inst["data"]["rounds"] == [3, 4, 5, 6]


def test_amusement_park_schedules_by_pasture_count(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["fences"] = sorted(cell_edges(4))
    give_card(s, first, "FR003")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "FR003"}})
    rnd = s["round"]
    for r in range(rnd + 1, rnd + 3):  # 1 pasture -> 2 rounds
        assert s["round_goods"][str(r)][str(first)]["food"] == 1
    assert str(rnd + 3) not in s["round_goods"] or \
        str(first) not in s["round_goods"].get(str(rnd + 3), {})


def test_apple_garden_scores_for_missing_crop(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["resources"]["grain"] = 0
    p["resources"]["vegetable"] = 3
    inst = cards.new_instance("FR004")
    p["minors"].append(inst)
    assert m._apple_garden_score(s, p, inst) == 2
    p["resources"]["vegetable"] = 0
    assert m._apple_garden_score(s, p, inst) == 2  # missing both: still 2
    p["resources"]["grain"] = 2
    p["resources"]["vegetable"] = 2
    assert m._apple_garden_score(s, p, inst) == 0


def test_baguette_bakes_bread_for_wood_during_feeding(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["improvements"].append("clay_oven")
    put_in_play(s, first, "FR007")
    p["resources"]["wood"] = 1
    p["resources"]["grain"] = 2
    p["resources"]["food"] = 0
    s["phase"] = "feeding"
    s["harvest_index"] = 1
    pid = p["player_id"]
    s = engine.apply_action(s, pid, {
        "kind": "card_action", "card": "FR007", "params": {"grain": 1}}).new_state
    p = s["players"][first]
    assert p["resources"]["wood"] == 0
    assert p["resources"]["grain"] == 1
    assert p["resources"]["food"] == MAJOR_IMPROVEMENTS["clay_oven"]["bake"][1]


def test_barber_shop_banks_points_for_remaining_harvests(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    give(s, first, wood=3, reed=1)
    give_card(s, first, "FR008")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "FR008"}})
    p = s["players"][first]
    inst = next(i for i in p["minors"] if i["id"] == "FR008")
    # Round 1: all 6 harvests still remain.
    assert inst["data"]["bonus"] == 6
    assert cards.score_bonuses(s, p) == 6


def test_breakfast_outdoors_alt_cost_grain(engine):
    """Cost "1 vegetable or 2 grains" (note: NOT a symmetric quantity)
    -- paying the non-first (2 grain) alternative via cost_option still
    plays the card."""
    s = make_state(engine, 2)
    first = s["current_player"]
    give(s, first, grain=2)
    give_card(s, first, "FR010")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "FR010", "cost_option": 1}})
    p = s["players"][first]
    assert p["resources"]["grain"] == 0 and p["resources"]["vegetable"] == 0
    assert any(i["id"] == "FR010" for i in p["minors"])


def test_brickyard_stashes_and_releases_clay(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "occ_woodcutter")
    put_in_play(s, first, "occ_reed_collector")
    inst = put_in_play(s, first, "FR011")
    p = s["players"][first]
    p["resources"]["clay"] = 0
    for _ in range(4):
        ctx = {"log": [], "actor": first, "extra": {}}
        cards.CARDS["FR011"]["hooks"]["round_start"](s, p, inst, ctx)
    assert inst["data"]["stash"] == 4
    pid = p["player_id"]
    s = engine.apply_action(s, pid, {
        "kind": "card_action", "card": "FR011"}).new_state
    p = s["players"][first]
    assert p["resources"]["clay"] == 4
    inst = next(i for i in p["minors"] if i["id"] == "FR011")
    assert inst["data"]["stash"] == 0


def test_camembert_places_food_on_three_spaces(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "occ_woodcutter")
    put_in_play(s, first, "occ_reed_collector")
    give_card(s, first, "FR012")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "FR012",
                                   "params": {"spaces": ["forest", "clay_pit",
                                                        "reed_bank"]}}})
    for sid in ("forest", "clay_pit", "reed_bank"):
        sp = next(x for x in s["action_spaces"] if x["id"] == sid)
        assert sp["supply"]["food"] == 1


def test_coffee_break_cancels_on_next_occupation(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give(s, first, clay=1)
    give_card(s, first, "FR015")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "FR015"}})
    rnd = s["round"]
    for r in range(rnd + 1, rnd + 6):
        assert s["round_goods"][str(r)][str(first)]["food"] == 1
    s = place(engine, s, {"kind": "place", "space": "day_laborer"})  # other's turn
    give_card(s, first, "occ_woodcutter")
    s = place(engine, s, {"kind": "place", "space": "lessons",
                          "card": "occ_woodcutter"})
    for r in range(rnd + 1, rnd + 6):
        assert s["round_goods"][str(r)][str(first)]["food"] == 0


def test_diary_grants_wood_on_occupation_play(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "occ_woodcutter")
    put_in_play(s, first, "occ_reed_collector")
    put_in_play(s, first, "FR017")
    give_card(s, first, "occ_clay_digger")
    wood = s["players"][first]["resources"]["wood"]
    s = place(engine, s, {"kind": "place", "space": "lessons",
                          "card": "occ_clay_digger"})
    assert s["players"][first]["resources"]["wood"] == wood + 1


def test_evening_prayer_plays_free_occupations_for_food(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    for c in range(2):
        p["cells"][c]["type"] = "field"
    p["resources"]["food"] = 0
    give(s, first, food=2)
    give_card(s, first, "FR019")
    give_card(s, first, "occ_woodcutter")
    give_card(s, first, "occ_reed_collector")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "FR019",
                                   "params": {"cids": ["occ_woodcutter",
                                                      "occ_reed_collector"]}}})
    p = s["players"][first]
    assert p["resources"]["food"] == 0
    assert any(i["id"] == "occ_woodcutter" for i in p["occupations"])
    assert any(i["id"] == "occ_reed_collector" for i in p["occupations"])


def test_five_rings_discounts_wood_on_improvements(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "FR020")
    cost = cards.modified_cost(s, p, "improvement",
                               MAJOR_IMPROVEMENTS["joinery"]["cost"])
    assert cost["wood"] == MAJOR_IMPROVEMENTS["joinery"]["cost"]["wood"] - 1


def test_flat_hill_renovates_wood_to_clay_for_free(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    for c in range(4):
        p["cells"][c]["type"] = "field"
        p["cells"][c]["crops"] = {"type": "grain", "count": 1}
    give_card(s, first, "FR021")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "FR021"}})
    assert s["players"][first]["house_type"] == "clay"


def test_full_bottomed_wig_waives_reed_on_renovate(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "FR022")
    cost = cards.modified_cost(s, p, "renovation", {"clay": 2, "reed": 1})
    assert cost.get("reed", 0) == 0


def test_goblet_doubles_well_food(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "FR023")
    p = s["players"][first]
    give(s, first, wood=1, stone=3)
    add_space(s, "major_improvement", "Major Improvement")
    s = place(engine, s, {"kind": "place", "space": "major_improvement",
                          "improvement": "well"})
    rnd = s["round"]
    for r in range(rnd + 1, rnd + 6):
        assert s["round_goods"][str(r)][str(first)]["food"] == 2


def test_grotto_stash_withdrawal(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "occ_woodcutter")
    put_in_play(s, first, "occ_reed_collector")
    give_card(s, first, "FR026")
    s["players"][first]["resources"]["food"] = 0
    give(s, first, food=2)
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "FR026"}})
    s = place(engine, s, {"kind": "place", "space": "day_laborer"})  # other's turn
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {
        "kind": "card_action", "card": "FR026",
        "params": {"good": "stone"}}).new_state
    p = s["players"][first]
    assert p["resources"]["stone"] == 1
    assert p["resources"]["food"] == 0
    inst = next(i for i in p["minors"] if i["id"] == "FR026")
    assert inst["data"]["stone"] == 1


def test_hammock_consumes_sheep_and_grants_extra_room(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    for c in range(4):
        p["cells"][c]["type"] = "room"
    p["pets"]["sheep"] = 1
    give(s, first, wood=2)
    give_card(s, first, "FR028")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "FR028"}})
    p = s["players"][first]
    assert p["pets"].get("sheep", 0) == 0
    assert cards.extra_rooms(s, p) == 1


def test_haystack_schedules_food_for_remaining_rounds(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["pets"] = {"sheep": 3, "boar": 2, "cattle": 1}
    give(s, first, wood=1)
    give_card(s, first, "FR030")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "FR030"}})
    rnd = s["round"]
    for r in range(rnd + 1, 15):
        assert s["round_goods"][str(r)][str(first)]["food"] == 3


def test_heatwave_builds_fireplace_for_free(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["house_type"] = "clay"
    give(s, first, stone=1)
    give_card(s, first, "FR031")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "FR031"}})
    pid = s["players"][first]["player_id"]
    acts = engine.get_valid_actions(s, pid)
    choice = next(a for a in acts if a["kind"] == "choice")
    idx = next(i for i, o in enumerate(choice["options"])
              if o.startswith("Build"))
    s = place(engine, s, {"kind": "choice", "index": idx})
    p = s["players"][first]
    assert any(i in ("fireplace_2", "fireplace_3") for i in p["improvements"])


def test_homework_plays_two_free_occupations(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give(s, first, reed=2, food=2)
    give_card(s, first, "FR032")
    give_card(s, first, "occ_woodcutter")
    give_card(s, first, "occ_reed_collector")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "FR032",
                                   "params": {"cids": ["occ_woodcutter",
                                                      "occ_reed_collector"]}}})
    p = s["players"][first]
    assert len(p["occupations"]) == 2


def test_kids_corner_house_capacity(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "FR033")
    assert cards.house_capacity(s, p) == 2


def test_lighthouse_extra_room_requires_two_stone_rooms(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["house_type"] = "stone"
    assert cards.check_prereq(s, p, "FR035")
    p["cells"][0]["type"] = "room"
    assert not cards.check_prereq(s, p, "FR035")


def test_march_returns_animals_to_plow_two_fields(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["pets"] = {"sheep": 1, "boar": 1}
    give_card(s, first, "FR036")
    plowable = cards.state.plowable_cells(p) if hasattr(cards, "state") else None
    from server.agricola.state import plowable_cells
    cells = plowable_cells(p)[:2]
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "FR036",
                                   "params": {"animals": ["sheep", "boar"],
                                            "cells": cells}}})
    p = s["players"][first]
    assert p["pets"] == {}
    for c in cells:
        assert p["cells"][c]["type"] == "field"


def test_orchard_schedules_food_by_planted_fields(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["cells"][0]["type"] = "field"
    p["cells"][0]["crops"] = {"type": "grain", "count": 1}
    p["cells"][1]["type"] = "field"
    p["cells"][1]["crops"] = {"type": "vegetable", "count": 1}
    give_card(s, first, "FR038")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "FR038"}})
    rnd = s["round"]
    for r in range(rnd + 1, rnd + 3):
        assert s["round_goods"][str(r)][str(first)]["food"] == 1


def test_par_force_hunting_pays_food_for_boar_on_marked_rounds(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give(s, first, wood=2)
    give_card(s, first, "FR039")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "FR039"}})
    p = s["players"][first]
    inst = next(i for i in p["minors"] if i["id"] == "FR039")
    target = inst["data"]["rounds"][0]
    s = place(engine, s, {"kind": "place", "space": "day_laborer"})  # other's turn
    s["round"] = target
    s["players"][first]["resources"]["food"] = 0
    give(s, first, food=1)
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {
        "kind": "card_action", "card": "FR039"}).new_state
    p = s["players"][first]
    assert p["resources"]["food"] == 0
    s = engine.apply_action(s, pid, {
        "kind": "accommodate", "placements": [], "pets": {"boar": 1}}).new_state
    assert s["players"][first]["pets"]["boar"] == 1


def test_park_cemetery_grants_stone_on_farmland_use(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give_card(s, first, "FR040")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "FR040"}})
    farmland = next(sp for sp in s["action_spaces"] if sp["id"] == "farmland")
    assert farmland["supply"]["stone"] == 3
    p = s["players"][first]
    ctx = {"space_id": "farmland", "actor": first, "log": [], "extra": {}}
    cards.CARDS["FR040"]["hooks"]["space_used"](s, p, p["minors"][-1], ctx)
    assert ctx["extra"] == {"stone": 1}
    assert farmland["supply"]["stone"] == 2


def test_park_cemetery_discard_to_plow_once_stone_gone(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "FR040")
    add_space(s, "farmland", "Farmland")  # 0 stone supply by default
    from server.agricola.state import plowable_cells
    cell = plowable_cells(p)[0]
    pid = p["player_id"]
    s = engine.apply_action(s, pid, {
        "kind": "card_action", "card": "FR040",
        "params": {"cell": cell}}).new_state
    p = s["players"][first]
    assert p["cells"][cell]["type"] == "field"
    assert not any(i["id"] == "FR040" for i in p["minors"])


def test_peasants_boutique_conversion_and_score(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["improvements"].append("basketmaker")
    put_in_play(s, first, "FR041")
    p["resources"]["reed"] = 5
    p["resources"]["food"] = 0
    inst = p["minors"][-1]
    # At 5 reed, both this card's own tiers ((4,3),(3,2),(1,1)) and
    # Basketmaker's own scoring_bonus tiers ((5,3),(4,2),(2,1)) award 3
    # points; scoring.py already adds Basketmaker's 3 separately, so this
    # card's own score_bonus should contribute 0 net (not another 3).
    assert m._peasants_boutique_score(s, p, inst) == 0
    p["resources"]["reed"] = 1
    # At 1 reed: this card awards 1, Basketmaker's own tiers award 0.
    assert m._peasants_boutique_score(s, p, inst) == 1


def test_sofa_scores_by_room_count(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    inst = cards.new_instance("FR043")
    p["minors"].append(inst)
    assert m._sofa_score(s, p, inst) == 4  # 2 starting rooms
    p["cells"][0]["type"] = "room"
    assert m._sofa_score(s, p, inst) == 2  # 3 rooms
    for c in range(4):
        p["cells"][c]["type"] = "room"
    assert m._sofa_score(s, p, inst) == 0  # 5+ rooms


def test_stone_house_reconstruction_renovates_anytime(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["house_type"] = "clay"
    give_card(s, first, "FR045")
    give(s, first, stone=1)
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "FR045"}})
    s = place(engine, s, {"kind": "place", "space": "day_laborer"})  # other's turn
    give(s, first, stone=2, reed=1)
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {
        "kind": "card_action", "card": "FR045"}).new_state
    p = s["players"][first]
    assert p["house_type"] == "stone"
    assert p["resources"]["stone"] == 0
    assert p["resources"]["reed"] == 0


def test_straw_thatched_hut_alt_room_cost(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["house_type"] = "clay"
    put_in_play(s, first, "FR046")
    cost = cards.modified_cost(s, p, "room", {"clay": 5, "reed": 2})
    assert cost == {"clay": 2, "grain": 1, "food": 1}


def test_swimming_studio_converts_wood_on_fishing(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "FR048")
    give(s, first, wood=1)
    food = s["players"][first]["resources"]["food"]
    s = place(engine, s, {"kind": "place", "space": "fishing"})
    pid = s["players"][first]["player_id"]
    acts = engine.get_valid_actions(s, pid)
    choice = next(a for a in acts if a["kind"] == "choice")
    s = place(engine, s, {"kind": "choice", "index": 0})
    p = s["players"][first]
    assert p["resources"]["wood"] == 0
    assert p["resources"]["food"] == food + 1 + 3  # fishing's own food + conversion


def test_port_le_havre_converts_clay_on_bake(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["improvements"].append("clay_oven")
    put_in_play(s, first, "FR049")
    p["resources"]["clay"] = 2
    p["resources"]["stone"] = 0
    p["resources"]["grain"] = 2
    add_space(s, "grain_utilization", "Sow and/or Bake Bread")
    s = place(engine, s, {"kind": "place", "space": "grain_utilization",
                          "bake": {"clay_oven": 1}})
    pid = p["player_id"]
    acts = engine.get_valid_actions(s, pid)
    choice = next(a for a in acts if a["kind"] == "choice")
    idx = choice["options"].index("Convert 2 clay to 2 stone")
    s = place(engine, s, {"kind": "choice", "index": idx})
    p = s["players"][first]
    assert p["resources"]["clay"] == 0
    assert p["resources"]["stone"] == 2


def test_threshing_machine_plow_plows_middle_cells_once(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "FR050")
    add_space(s, "farmland", "Farmland")
    s = place(engine, s, {"kind": "place", "space": "farmland", "cell": 0})
    p = s["players"][first]
    for c in m._MIDDLE_CELLS:
        assert p["cells"][c]["type"] == "field"
    assert p["cells"][0]["type"] == "field"


def test_trees_for_citizens_joinery_refund_and_score(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    for i in range(3):
        put_in_play(s, first, _DUMMY_OCCS[i])
    p["improvements"].append("joinery")
    give(s, first, wood=3)
    give_card(s, first, "FR052")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "FR052"}})
    p = s["players"][first]
    assert p["resources"]["wood"] == 3  # refunded
    inst = p["minors"][-1]
    # joinery (wood cost) + FR052 itself (wood cost) = 2 improvements -> 1 pt
    assert m._trees_for_citizens_score(s, p, inst) == 1


def test_trip_to_the_lake_space_bonus(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "occ_woodcutter")
    put_in_play(s, first, "occ_reed_collector")
    put_in_play(s, first, "FR053")
    food = s["players"][first]["resources"]["food"]
    wood = s["players"][first]["resources"]["wood"]
    s = place(engine, s, {"kind": "place", "space": "fishing"})
    p = s["players"][first]
    assert p["resources"]["food"] == food + 1 + 1
    assert p["resources"]["wood"] == wood + 1


def test_tuileries_garden_sows_free_crop_after_plow(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    for i in range(4):
        put_in_play(s, first, _DUMMY_OCCS[i])
    put_in_play(s, first, "FR054")
    add_space(s, "farmland", "Farmland")
    s = place(engine, s, {"kind": "place", "space": "farmland", "cell": 0})
    pid = s["players"][first]["player_id"]
    acts = engine.get_valid_actions(s, pid)
    choice = next(a for a in acts if a["kind"] == "choice")
    idx = choice["options"].index("Sow vegetable")
    s = place(engine, s, {"kind": "choice", "index": idx})
    p = s["players"][first]
    assert p["cells"][0]["crops"] == {"type": "vegetable", "count": 2}


def test_vegetable_harvest_raw_conversion_requires_oven(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["improvements"].append("clay_oven")
    put_in_play(s, first, "FR055")
    assert cards.raw_values(p)["vegetable"] == 4


def test_watering_can_adds_crops_to_planted_fields(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["cells"][0]["type"] = "field"
    p["cells"][0]["crops"] = {"type": "grain", "count": 1}
    give(s, first, food=1)
    give_card(s, first, "FR056")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "FR056"}})
    assert s["players"][first]["cells"][0]["crops"]["count"] == 2


def test_wild_game_consumes_boar_for_food(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["pets"]["boar"] = 1
    p["resources"]["food"] = 0
    give_card(s, first, "FR057")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "FR057"}})
    p = s["players"][first]
    assert p["pets"].get("boar", 0) == 0
    assert p["resources"]["food"] == 5


def test_wood_saw_builds_room_when_behind_in_people(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    p = s["players"][first]
    op = s["players"][other]
    op["people_total"] = 3
    give(s, first, wood=1)
    give_card(s, first, "FR060")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "FR060"}})
    s = place(engine, s, {"kind": "place", "space": "day_laborer"})  # other's turn
    give(s, first, wood=5, reed=2)
    p = s["players"][first]
    cells = m._room_eligible_cells(p)
    pid = p["player_id"]
    s = engine.apply_action(s, pid, {
        "kind": "card_action", "card": "FR060",
        "params": {"cells": [cells[0]]}}).new_state
    p = s["players"][first]
    assert p["cells"][cells[0]]["type"] == "room"


def test_golden_rose_food_discount_occupation_and_minor(engine):
    """FR024: up to 2 food off playing an occupation or minor -- never
    below 0, and only the food entry of the cost, nothing else."""
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["cells"][0]["type"] = "field"
    p["cells"][0]["crops"] = {"type": "grain", "count": 1}  # prereq
    put_in_play(s, first, "FR024")

    # Occupations normally cost 1 food; fully waived (capped at 0, not
    # negative).
    p["resources"]["food"] = 0
    give_card(s, first, "occ_woodcutter")
    add_space(s, "lessons", "Lessons")
    s = place(engine, s, {"kind": "place", "space": "lessons",
                          "card": "occ_woodcutter"})
    p = s["players"][first]
    assert "occ_woodcutter" in [i["id"] for i in p["occupations"]]
    assert p["resources"]["food"] == 0

    # A hypothetical 3-food minor/occupation only gets 2 off, and a
    # cost with no food is untouched.
    cost = cards.modified_cost(s, p, "minor", {"food": 3}, {"card": "x"})
    assert cost == {"food": 1}
    cost2 = cards.modified_cost(s, p, "occupation", {"wood": 1}, {"card": "y"})
    assert cost2 == {"wood": 1}


def test_chameleon_costs_sheep_grants_boar_and_shares_pasture(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["pets"]["sheep"] = 1
    give_card(s, first, "FR013")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "FR013"}})
    p = s["players"][first]
    assert p["pets"].get("sheep", 0) == 0  # 1 sheep spent as this card's cost

    pid = p["player_id"]
    acts = engine.get_valid_actions(s, pid)
    acc = next(a for a in acts if a["kind"] == "accommodate")
    assert acc["gained"] == {"boar": 1}
    s = place(engine, s, {"kind": "accommodate", "placements": [],
                          "discard": {"boar": 1}})

    inst = next(i for i in p["minors"] if i["id"] == "FR013")
    secondary = cards.CARDS["FR013"]["pasture_secondary_types"](
        s, p, inst, {"cells": [4], "size": 1, "stables": 0, "animal_type": "sheep"})
    assert secondary == {"boar": 1}
    secondary2 = cards.CARDS["FR013"]["pasture_secondary_types"](
        s, p, inst, {"cells": [4], "size": 1, "stables": 0, "animal_type": "cattle"})
    assert secondary2 == {}


# ── FR006 Badger ──────────────────────────────────────────────────────

def test_badger_grants_food_to_whoever_uses_marked_space(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    other = 1 - first
    give_card(s, first, "FR006")
    give(s, first, clay=1)
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "FR006",
                                    "params": {"space": "forest"}}})
    inst = next(i for i in s["players"][first]["minors"] if i["id"] == "FR006")
    assert inst["data"]["marker"] == "forest"

    food_before = s["players"][other]["resources"]["food"]
    s = place(engine, s, {"kind": "place", "space": "forest"})  # other's turn
    assert s["players"][other]["resources"]["food"] == food_before + 1


def test_badger_moves_to_single_adjacent_space_at_round_start(engine):
    """When exactly one adjacent space exists, the move is automatic --
    no prompt. No printed space has exactly one neighbor once round 1
    is revealed (round 1 tops the accumulation column, giving even
    Farm Expansion a second neighbor), so surgically hide the round-1
    card to isolate the single-option branch."""
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "FR006")
    inst = next(i for i in s["players"][first]["minors"] if i["id"] == "FR006")
    inst["data"]["marker"] = "farm_expansion"
    s["action_spaces"] = [sp for sp in s["action_spaces"]
                          if sp["id"] != s["revealed"][0]]
    assert cards.adjacent_spaces(s, "farm_expansion") == ["meeting_place"]
    engine._start_round(s, [])  # round 2
    assert inst["data"]["marker"] == "meeting_place"


def test_badger_prompts_when_multiple_adjacent_spaces(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "FR006")
    inst = next(i for i in s["players"][first]["minors"] if i["id"] == "FR006")
    inst["data"]["marker"] = "reed_bank"
    options_expected = set(cards.adjacent_spaces(s, "reed_bank"))
    assert len(options_expected) > 1
    engine._start_round(s, [])  # round 2
    prompt = s["prompts"][0]
    assert prompt["type"] == "choice"
    assert set(prompt["options"]) == options_expected

    pid = s["players"][first]["player_id"]
    idx = prompt["options"].index("fishing")
    s = engine.apply_action(s, pid, {"kind": "choice", "index": idx}).new_state
    inst2 = next(i for i in s["players"][first]["minors"] if i["id"] == "FR006")
    assert inst2["data"]["marker"] == "fishing"


# ── FR027 Ground Pickaxe Plow ────────────────────────────────────────

def test_ground_pickaxe_plow_bonus_and_once_per_game(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "FR027")
    give(s, first, wood=2)
    s = place(engine, s, {"kind": "place", "space": "farmland", "cell": 0})
    p = s["players"][first]
    assert p["cells"][0]["type"] == "field"  # farmland's own plow

    prompt = s["prompts"][0]
    assert prompt["type"] == "choice"
    idx = next(i for i, o in enumerate(prompt["options"])
              if o.startswith("Plow cell"))
    plowed_cell = int(prompt["options"][idx].split()[2])
    pid = p["player_id"]
    wood_before = p["resources"]["wood"]
    s = engine.apply_action(s, pid, {"kind": "choice", "index": idx}).new_state
    p = s["players"][first]
    assert p["cells"][plowed_cell]["type"] == "field"
    assert p["resources"]["wood"] == wood_before - 1

    # Cap is 2 -- a second offer chains immediately; decline it.
    prompt2 = s["prompts"][0]
    decline_idx = prompt2["options"].index("Decline")
    s = engine.apply_action(s, pid, {"kind": "choice",
                                     "index": decline_idx}).new_state
    assert not s["prompts"]

    # "Once during the game" -- a later use of a qualifying space does
    # not re-offer, even though it was only partially used above.
    inst = next(i for i in s["players"][first]["minors"] if i["id"] == "FR027")
    assert inst["data"]["used"] is True
    ctx2 = {"space_id": "farmland", "goods": {}, "log": [], "extra": {},
           "actor": first}
    cards.CARDS["FR027"]["hooks"]["space_used"](s, s["players"][first], inst, ctx2)
    assert not s["prompts"]


# ── FR037 Necklace ────────────────────────────────────────────────────

def test_necklace_bonus_when_two_adjacent_spaces_occupied(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "FR037")
    food_before = s["players"][first]["resources"]["food"]
    s = place(engine, s, {"kind": "place", "space": "grain_seeds"})  # first
    s = place(engine, s, {"kind": "place", "space": "clay_pit"})  # other
    s = place(engine, s, {"kind": "place", "space": "forest"})  # first, adjacent
    s = place(engine, s, {"kind": "place", "space": "reed_bank"})  # other
    assert s["round"] == 2  # work phase ended -> returning_home fired
    assert s["players"][first]["resources"]["food"] == food_before + 1


def test_necklace_no_bonus_without_adjacent_pair(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "FR037")
    food_before = s["players"][first]["resources"]["food"]
    s = place(engine, s, {"kind": "place", "space": "grain_seeds"})  # first
    s = place(engine, s, {"kind": "place", "space": "clay_pit"})  # other
    # Reed Bank (1, 4) is not orthogonally adjacent to Grain Seeds (0, 2).
    s = place(engine, s, {"kind": "place", "space": "reed_bank"})  # first
    s = place(engine, s, {"kind": "place", "space": "forest"})  # other
    assert s["round"] == 2
    assert s["players"][first]["resources"]["food"] == food_before


# ── FR001 Abandoned Willow ─────────────────────────────────────────────

def test_abandoned_willow_removes_empty_field_for_4_wood(engine):
    """FR001: immediately remove 1 empty field, receive 4 wood. Mirrors
    test_agricola.py's temp_card-only regression
    (test_fr001_style_remove_empty_field_recipe) with the real card,
    verifying no hidden invariant breaks on the reverted cell."""
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["cells"][0]["type"] = "field"
    give_card(s, first, "FR001")
    wood_before = p["resources"]["wood"]
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "FR001", "params": {"cell": 0}}})
    p = s["players"][first]
    assert p["cells"][0]["type"] == "empty"
    assert p["resources"]["wood"] == wood_before + 4
    assert any(i["id"] == "FR001" for i in p["minors"])

    # Regression: plow targets, pasture validity, and scoring all still
    # behave normally on the reverted (now-unused) cell.
    assert 0 in plowable_cells(p)
    ok, err, pastures = validate_fence_layout(p, cell_edges(0))
    assert ok, err
    assert pastures == [[0]]
    sc = score_player(p, s)
    assert sc["fields"] == table_score(
        "fields", sum(1 for c in p["cells"] if c["type"] == "field"))
    assert sc["unused_spaces"] <= 0


def test_abandoned_willow_requires_an_empty_field(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give_card(s, first, "FR001")
    with pytest.raises(ValueError):
        place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "FR001"}})


def test_abandoned_willow_cannot_remove_planted_or_non_field_cell(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["cells"][0]["type"] = "field"  # satisfies the prereq
    p["cells"][1]["type"] = "field"
    p["cells"][1]["crops"] = {"type": "grain", "count": 3}
    give_card(s, first, "FR001")
    with pytest.raises(ValueError):
        place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "FR001", "params": {"cell": 1}}})
    with pytest.raises(ValueError):
        place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "FR001", "params": {"cell": 2}}})


# ── FR018 Encircling Wall ────────────────────────────────────────────

def test_encircling_wall_free_fence(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give_card(s, first, "FR018")
    give(s, first, stone=4)  # the card's own printed cost
    pid = s["players"][first]["player_id"]
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "FR018",
                                   "params": {"fences": list(cell_edges(0))}}})
    p = s["players"][first]
    assert p["resources"]["wood"] == 0  # no wood spent on the fence
    from server.agricola.state import compute_pastures
    assert len(compute_pastures(p)) == 1


def test_encircling_wall_rejects_more_than_one_space(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give_card(s, first, "FR018")
    give(s, first, stone=4)
    # Enclosing cells 0 and 2 as one pasture opens 2 new farmyard spaces,
    # not the printed "1 space".
    fences = sorted(set(cell_edges(0)) | set(cell_edges(1)) | set(cell_edges(2)))
    with pytest.raises(ValueError):
        place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "FR018", "params": {"fences": fences}}})
