"""Tests for the deck FR minor-improvement module
(server/agricola/decks/deck_fr_minors.py)."""

import pytest

from server.agricola.engine import AgricolaEngine
from server.agricola import cards
from server.agricola.decks import deck_fr_minors as m
from server.agricola.state import cell_edges, MAJOR_IMPROVEMENTS

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
             "FR052": 3, "FR053": 2, "FR054": 4}

_PLAY_PARAMS = {
    "FR012": {"spaces": ["forest", "clay_pit", "reed_bank"]},
}


def _prep_prereqs(state, pidx, cid):
    p = state["players"][pidx]
    n = _NEEDS_OCC.get(cid)
    if n:
        for i in range(n):
            put_in_play(state, pidx, _DUMMY_OCCS[i])
    if cid == "FR003":
        p["fences"] = sorted(cell_edges(4))
    if cid == "FR016":
        p["cells"][0]["type"] = "field"
    if cid == "FR021":
        for c in range(4):
            p["cells"][c]["type"] = "field"
            p["cells"][c]["crops"] = {"type": "grain", "count": 1}
    if cid == "FR022":
        p["pets"]["sheep"] = 3
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
    give(s, first, **cards.CARDS[cid]["cost"])
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
