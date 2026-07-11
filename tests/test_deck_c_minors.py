"""Tests for the deck C minor-improvement module
(server/agricola/decks/deck_c_minors.py)."""

import pytest

from server.agricola.engine import AgricolaEngine
from server.agricola import cards
from server.agricola.decks import deck_c_minors
from server.agricola.state import cell_edges, compute_pastures, HARVEST_ROUNDS

from test_agricola import (
    make_state, give, give_card, put_in_play, add_space, place, current_pid,
)


@pytest.fixture
def engine():
    return AgricolaEngine()


def _fence_single_cell_pastures(state, pidx, cells):
    """Directly fence `cells` as independent single-cell pastures (bypasses
    _do_build_fences/validate_fence_layout -- fine for test setup since
    compute_pastures only checks per-region enclosure, not connectivity)."""
    p = state["players"][pidx]
    fences = set(p["fences"])
    for c in cells:
        fences |= set(cell_edges(c))
    p["fences"] = sorted(fences)


# ── Registration completeness ────────────────────────────────────────

def test_registration_completeness():
    db_codes = {c["code"] for c in cards.compendium().values()
               if c["deck"] == "C" and c["type"] == "minor"}
    registered = {cid for cid in cards.CARDS if cid in db_codes}
    unimplemented = set(deck_c_minors.UNIMPLEMENTED)
    assert unimplemented <= db_codes
    assert registered & unimplemented == set()
    assert registered | unimplemented == db_codes


# ── Smoke test: every implemented card gets played once ──────────────

_NEEDS_OCC = {"C034": 2, "C036": 1, "C058": 1, "C076": 3, "C084": 2,
             "C015": 2, "C028": 1}
_DUMMY_OCCS = ["occ_woodcutter", "occ_reed_collector", "occ_clay_digger"]


def _prep_prereqs(state, pidx, cid):
    p = state["players"][pidx]
    n = _NEEDS_OCC.get(cid)
    if n:
        for i in range(n):
            put_in_play(state, pidx, _DUMMY_OCCS[i])
    if cid in ("C007", "C083"):
        _fence_single_cell_pastures(state, pidx, [0])
    if cid == "C050":
        _fence_single_cell_pastures(state, pidx, [0, 2, 4])
        for c in (6, 7, 9):
            p["cells"][c]["stable"] = True
    if cid == "C066":
        p["cells"][3]["type"] = "field"
        p["cells"][3]["crops"] = {"type": "grain", "count": 3}
    if cid == "C017":
        for c in (0, 1, 2):
            p["cells"][c]["type"] = "field"
    if cid == "C020":
        state["round"] = 9
    if cid == "C038":
        p["pets"] = {"sheep": 1}
    if cid == "C052":
        p["improvements"] = ["fireplace_2"]
    if cid == "C029":
        p["resources"]["grain"] = 0  # prereq: no grain in supply


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


_DB_C_MINOR_CODES = {c["code"] for c in cards.compendium().values()
                     if c["deck"] == "C" and c["type"] == "minor"}
ALL_C_MINORS = sorted(cid for cid in cards.CARDS
                     if cid in _DB_C_MINOR_CODES)


@pytest.mark.parametrize("cid", ALL_C_MINORS)
def test_smoke_play_every_card(engine, cid):
    s = make_state(engine, 2)
    first = s["current_player"]
    give(s, first, **cards.CARDS[cid]["cost"])
    give(s, first, food=5, grain=5, vegetable=5, wood=5, clay=5, reed=5,
        stone=5)
    _prep_prereqs(s, first, cid)
    give_card(s, first, cid)
    params = {"cell": 3} if cid == "C017" else None
    minor = {"card": cid}
    if params:
        minor["params"] = params
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": minor})
    pid = s["players"][first]["player_id"]
    s = _resolve_all_prompts(engine, s, pid)
    p = s["players"][first]
    assert cid not in p["hand_minors"]
    assert any(i["id"] == cid for i in p["minors"])


# ── Targeted effect tests ────────────────────────────────────────────

def test_writing_boards_scales_with_occupations(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "occ_woodcutter")
    put_in_play(s, first, "occ_reed_collector")
    give(s, first, food=1)
    give_card(s, first, "C004")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "C004"}})
    assert s["players"][first]["resources"]["wood"] == 2


def test_remodeling_grants_clay_for_clay_rooms_and_majors(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["house_type"] = "clay"
    p["improvements"] = ["well"]
    give(s, first, food=1)
    give_card(s, first, "C005")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "C005"}})
    # 2 starting rooms (both now "clay") + 1 major improvement = 3 clay.
    assert s["players"][first]["resources"]["clay"] == 3


def test_early_cattle_grants_cattle_via_accommodation(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    _fence_single_cell_pastures(s, first, [0])
    give_card(s, first, "C083")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "C083"}})
    assert s["prompts"][0]["gained"] == {"cattle": 2}
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {
        "kind": "accommodate",
        "placements": [{"cell": 0, "type": "cattle", "count": 2}],
    }).new_state
    assert s["players"][first]["cells"][0]["animal"] == \
        {"type": "cattle", "count": 2}


def test_christianity_grants_food_to_others(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    s["players"][first]["pets"] = {"sheep": 1}
    give_card(s, first, "C038")
    other_food_before = s["players"][other]["resources"]["food"]
    first_food_before = s["players"][first]["resources"]["food"]
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "C038"}})
    assert s["players"][other]["resources"]["food"] == other_food_before + 1
    assert s["players"][first]["resources"]["food"] == first_food_before


def test_abort_oriel_blocked_when_a_player_has_five_cards(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    # Give the other player 5 cards in front of them.
    s["players"][other]["occupations"] = [cards.new_instance("occ_woodcutter")
                                          for _ in range(5)]
    give_card(s, first, "C032")
    give(s, first, clay=2)
    pid = s["players"][first]["player_id"]
    with pytest.raises(ValueError):
        engine.apply_action(s, pid, {
            "kind": "place", "space": "meeting_place",
            "minor": {"card": "C032"}}).new_state


def test_blade_shears_choice_flat_option(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    _fence_single_cell_pastures(s, first, [0])
    food_before = s["players"][first]["resources"]["food"]
    give(s, first, wood=1)
    give_card(s, first, "C007")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "C007"}})
    s = place(engine, s, {"kind": "choice", "index": 0})
    assert s["players"][first]["resources"]["food"] == food_before + 3


def test_blade_shears_choice_per_sheep(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    _fence_single_cell_pastures(s, first, [0])
    s["players"][first]["pets"] = {"sheep": 4}
    food_before = s["players"][first]["resources"]["food"]
    give(s, first, wood=1)
    give_card(s, first, "C007")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "C007"}})
    s = place(engine, s, {"kind": "choice", "index": 1})
    assert s["players"][first]["resources"]["food"] == food_before + 4


def test_crudite_buys_on_play(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    food_before = s["players"][first]["resources"]["food"]
    give(s, first, food=3)
    give_card(s, first, "C057")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "C057"}})
    s = place(engine, s, {"kind": "choice", "index": 0})
    p = s["players"][first]
    assert p["resources"]["food"] == food_before
    assert p["resources"]["vegetable"] == 1


def test_crudite_discards_vegetable_for_food(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "C057")
    p = s["players"][first]
    food_before = p["resources"]["food"]
    p["resources"]["vegetable"] = 1
    p["cells"][3]["type"] = "field"
    p["cells"][3]["crops"] = {"type": "vegetable", "count": 1}
    pid = p["player_id"]
    s = engine.apply_action(s, pid, {
        "kind": "card_action", "card": "C057"}).new_state
    p = s["players"][first]
    assert p["resources"]["vegetable"] == 0
    assert p["resources"]["food"] == food_before + 4


def test_beer_table_banks_bonus_and_feeds_others(engine):
    s = make_state(engine, 2)
    put_in_play(s, 0, "C029")
    other = 1
    for p in s["players"]:
        p["resources"]["food"] = 10
    s["players"][0]["resources"]["grain"] = 1
    other_food = s["players"][other]["resources"]["food"]
    s["round"] = 4
    s["phase"] = "work"
    for p in s["players"]:
        p["people_placed"] = p["people_total"]
    log = []
    engine._end_work_phase(s, log)
    pid0 = s["players"][0]["player_id"]
    s = engine.apply_action(s, pid0, {"kind": "choice", "index": 0}).new_state
    assert s["players"][0]["resources"]["grain"] == 0
    assert s["players"][other]["resources"]["food"] == other_food + 1
    inst = next(i for i in s["players"][0]["minors"] if i["id"] == "C029")
    assert inst["data"]["bp"] == 2


def test_elephantgrass_plant_banks_bonus_points(engine):
    s = make_state(engine, 2)
    put_in_play(s, 0, "C034")
    for p in s["players"]:
        p["resources"]["food"] = 10
    s["players"][0]["resources"]["reed"] = 2
    s["round"] = 4
    s["phase"] = "work"
    for p in s["players"]:
        p["people_placed"] = p["people_total"]
    log = []
    engine._end_work_phase(s, log)
    pid0 = s["players"][0]["player_id"]
    s = engine.apply_action(s, pid0, {"kind": "choice", "index": 0}).new_state
    inst = next(i for i in s["players"][0]["minors"] if i["id"] == "C034")
    assert inst["data"]["bp"] == 1
    assert s["players"][0]["resources"]["reed"] == 1


def test_eternal_rye_cultivation_grain_tier(engine):
    s = make_state(engine, 2)
    put_in_play(s, 0, "C066")
    for p in s["players"]:
        p["resources"]["food"] = 10
    s["players"][0]["resources"]["grain"] = 3
    s["round"] = 4
    s["phase"] = "work"
    for p in s["players"]:
        p["people_placed"] = p["people_total"]
    log = []
    engine._end_work_phase(s, log)
    assert s["players"][0]["resources"]["grain"] == 4


def test_eternal_rye_cultivation_food_tier(engine):
    s = make_state(engine, 2)
    put_in_play(s, 0, "C066")
    for p in s["players"]:
        p["resources"]["food"] = 10
    s["players"][0]["resources"]["grain"] = 2
    s["round"] = 4
    s["phase"] = "work"
    for p in s["players"]:
        p["people_placed"] = p["people_total"]
    log = []
    engine._end_work_phase(s, log)
    assert s["players"][0]["resources"]["grain"] == 2
    assert s["players"][0]["resources"]["food"] == 11


def test_mineral_feeder_grants_grain_off_harvest_round(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    put_in_play(s, first, "C067")
    _fence_single_cell_pastures(s, first, [0])
    s["players"][first]["cells"][0]["animal"] = {"type": "sheep", "count": 1}
    grain_before = s["players"][first]["resources"]["grain"]
    # Advance round 1 -> round 2 (not a harvest round).
    s = place(engine, s, {"kind": "place", "space": "day_laborer"})
    s = place(engine, s, {"kind": "place", "space": "meeting_place"})
    s = place(engine, s, {"kind": "place", "space": "forest"})
    s = place(engine, s, {"kind": "place", "space": "fishing"})
    assert s["round"] == 2
    assert s["players"][first]["resources"]["grain"] == grain_before + 1


def test_huntsmans_hat_grants_food_per_boar(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "C052")
    s["players"][first]["improvements"] = ["fireplace_2"]
    food_before = s["players"][first]["resources"]["food"]
    add_space(s, "boar_space", "Wild Boar", acc=True, supply={"boar": 2})
    s = place(engine, s, {"kind": "place", "space": "boar_space"})
    s = _drain_prompts(engine, s, s["players"][first]["player_id"])
    assert s["players"][first]["resources"]["food"] == food_before + 2


def _drain_prompts(engine, state, pid):
    while True:
        acts = engine.get_valid_actions(state, pid)
        acc = next((a for a in acts if a["kind"] == "accommodate"), None)
        if not acc:
            break
        state = engine.apply_action(state, pid, {
            "kind": "accommodate", "placements": [],
            "discard": dict(acc["gained"])}).new_state
    return state


def test_woodcraft_grants_food_under_wood_threshold(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "occ_woodcutter")
    put_in_play(s, first, "C058")
    wood_before = s["players"][first]["resources"]["wood"]
    food_before = s["players"][first]["resources"]["food"]
    add_space(s, "forest2", "Forest 2", acc=True, supply={"wood": 3})
    s = place(engine, s, {"kind": "place", "space": "forest2"})
    # 3 (space) + 1 (Woodcutter) = 4 wood <= 5 => Woodcraft grants 1 food.
    assert s["players"][first]["resources"]["wood"] == wood_before + 4
    assert s["players"][first]["resources"]["food"] == food_before + 1


def test_wood_cart_take_bonus(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "C076")
    add_space(s, "forest3", "Forest 3", acc=True, supply={"wood": 2})
    s = place(engine, s, {"kind": "place", "space": "forest3"})
    assert s["players"][first]["resources"]["wood"] == 4


def test_hardware_store_buy_bundle(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "C082")
    food_before = s["players"][first]["resources"]["food"]
    s = place(engine, s, {"kind": "place", "space": "day_laborer"})
    s = place(engine, s, {"kind": "choice", "index": 0})
    p = s["players"][first]
    # +2 food from Day Laborer, -2 food spent on the Hardware Store buy.
    assert p["resources"]["food"] == food_before
    assert p["resources"]["wood"] == 1
    assert p["resources"]["clay"] == 1
    assert p["resources"]["reed"] == 1
    assert p["resources"]["stone"] == 1


def test_clay_deposit_c036_exchange_and_space_supply(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "occ_woodcutter")
    put_in_play(s, first, "C036")
    add_space(s, "clay_pit", "Clay Pit", acc=True, supply={"clay": 2})
    s = place(engine, s, {"kind": "place", "space": "clay_pit"})
    clay_before = s["players"][first]["resources"]["clay"]
    s = place(engine, s, {"kind": "choice", "index": 0})
    assert s["players"][first]["resources"]["clay"] == clay_before - 1
    space = next(sp for sp in s["action_spaces"] if sp["id"] == "clay_pit")
    assert space["supply"]["clay"] == 1
    inst = next(i for i in s["players"][first]["minors"] if i["id"] == "C036")
    assert inst["data"]["bp"] == 1


def test_material_hub_banks_and_grants(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give(s, first, wood=1, clay=1)
    give_card(s, first, "C081")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "C081"}})
    inst = next(i for i in s["players"][first]["minors"] if i["id"] == "C081")
    assert inst["data"] == {"wood": 2, "clay": 2, "reed": 2, "stone": 2}
    # C081's own cost (1W 1C) was just paid, so first's building resources
    # are at 0 going into the next space use.
    add_space(s, "big_wood", "Big Wood", acc=True, supply={"wood": 5})
    s = place(engine, s, {"kind": "place", "space": "big_wood"})
    # The 5 wood from the space goes to whoever placed there (the other
    # player, since turn passes after every placement); only the Material
    # Hub's +1 threshold bonus reaches the card's owner ("first").
    assert s["players"][first]["resources"]["wood"] == 1
    inst = next(i for i in s["players"][first]["minors"] if i["id"] == "C081")
    assert inst["data"]["wood"] == 1


def test_rocky_terrain_buy_stone_on_plow(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "C080")
    food_before = s["players"][first]["resources"]["food"]
    give(s, first, food=1)
    add_space(s, "farmland", "Farmland", supply={})
    s = place(engine, s, {"kind": "place", "space": "farmland", "cell": 3})
    s = place(engine, s, {"kind": "choice", "index": 0})
    p = s["players"][first]
    assert p["resources"]["food"] == food_before
    assert p["resources"]["stone"] == 1
    assert p["cells"][3]["type"] == "field"


def test_beer_stein_bake_bonus(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "C061")
    p = s["players"][first]
    p["improvements"] = ["fireplace_2"]
    p["resources"]["grain"] = 3
    add_space(s, "grain_utilization", "Grain Utilization", supply={})
    s = place(engine, s, {"kind": "place", "space": "grain_utilization",
                          "bake": {"fireplace_2": 1}})
    s = place(engine, s, {"kind": "choice", "index": 0})
    p = s["players"][first]
    assert p["resources"]["grain"] == 1  # 3 - 1 baked - 1 to Beer Stein
    inst = next(i for i in p["minors"] if i["id"] == "C061")
    assert inst["data"]["bp"] == 1


def test_newly_plowed_field_ignores_adjacency(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    for c in (0, 1, 2):
        p["cells"][c]["type"] = "field"
    give_card(s, first, "C017")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "C017",
                                   "params": {"cell": 12}}})
    assert s["players"][first]["cells"][12]["type"] == "field"


def test_roll_over_plow_card_action(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "C018")
    p = s["players"][first]
    for c in (0, 1, 2):
        p["cells"][c]["type"] = "field"
        p["cells"][c]["crops"] = {"type": "grain", "count": 1}
    pid = p["player_id"]
    s = engine.apply_action(s, pid, {
        "kind": "card_action", "card": "C018",
        "params": {"field_cell": 0, "plow_cell": 3}}).new_state
    p = s["players"][first]
    assert p["cells"][0]["crops"] is None
    assert p["cells"][3]["type"] == "field"


def test_mole_plow_extra_plow_choice(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    s["round"] = 9
    put_in_play(s, first, "C020")
    give(s, first, wood=2, grain=1)
    add_space(s, "farmland", "Farmland", supply={})
    s = place(engine, s, {"kind": "place", "space": "farmland", "cell": 3})
    acts = engine.get_valid_actions(s, s["players"][first]["player_id"])
    choice = next(a for a in acts if a["kind"] == "choice")
    idx = choice["options"].index("Plow cell 4")
    s = place(engine, s, {"kind": "choice", "index": idx})
    assert s["players"][first]["cells"][4]["type"] == "field"


def test_plant_fertilizer_card_action(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "C008")
    p = s["players"][first]
    p["cells"][3]["type"] = "field"
    p["cells"][3]["crops"] = {"type": "grain", "count": 1}
    pid = p["player_id"]
    s = engine.apply_action(s, pid, {
        "kind": "card_action", "card": "C008", "params": {"cell": 3}
    }).new_state
    assert s["players"][first]["cells"][3]["crops"]["count"] == 2


def test_automatic_water_trough_buys_animal(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "C009")
    food_before = s["players"][first]["resources"]["food"]
    give(s, first, food=1)
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {
        "kind": "card_action", "card": "C009", "params": {"species": "boar"}
    }).new_state
    assert s["prompts"][0]["gained"] == {"boar": 1}
    assert s["players"][first]["resources"]["food"] == food_before


def test_stable_yard_play_grants_food_for_remaining_rounds(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    _fence_single_cell_pastures(s, first, [0, 2, 4])
    p = s["players"][first]
    for c in (6, 7, 9):
        p["cells"][c]["stable"] = True
    food_before = p["resources"]["food"]
    give_card(s, first, "C050")
    s["round"] = 5
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "C050"}})
    p = s["players"][first]
    # +9 food: TOTAL_ROUNDS(14) - round(5).
    assert p["resources"]["food"] == food_before + 9


def test_stable_yard_exchanges_sheep_and_boar_for_cattle(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "C050")
    p = s["players"][first]
    p["pets"] = {"sheep": 1, "boar": 1}
    pid = p["player_id"]
    s = engine.apply_action(s, pid, {
        "kind": "card_action", "card": "C050"}).new_state
    p = s["players"][first]
    assert p["pets"].get("sheep", 0) == 0
    assert p["pets"].get("boar", 0) == 0
    assert s["prompts"][0]["gained"] == {"cattle": 1}


def test_perennial_rye_breeds_animal(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "occ_woodcutter")
    put_in_play(s, first, "occ_reed_collector")
    put_in_play(s, first, "C084")
    p = s["players"][first]
    p["pets"] = {"sheep": 1}
    p["resources"]["grain"] = 1
    s["round"] = 2  # not a harvest round
    pid = p["player_id"]
    s = engine.apply_action(s, pid, {
        "kind": "card_action", "card": "C084", "params": {"species": "sheep"}
    }).new_state
    assert s["players"][first]["resources"]["grain"] == 0
    assert s["prompts"][0]["gained"] == {"sheep": 1}


def test_mandoline_once_per_round(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "C046")
    p = s["players"][first]
    p["resources"]["vegetable"] = 2
    pid = p["player_id"]
    s = engine.apply_action(s, pid, {
        "kind": "card_action", "card": "C046"}).new_state
    assert s["players"][first]["resources"]["vegetable"] == 1
    with pytest.raises(ValueError):
        engine.apply_action(s, pid, {"kind": "card_action", "card": "C046"})
    inst = next(i for i in s["players"][first]["minors"] if i["id"] == "C046")
    assert inst["data"]["bp"] == 1
    assert s["round_goods"]["2"][str(first)]["food"] == 1


def test_corn_schnapps_distillery_schedules_food(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "C064")
    p = s["players"][first]
    p["resources"]["grain"] = 1
    pid = p["player_id"]
    s = engine.apply_action(s, pid, {
        "kind": "card_action", "card": "C064"}).new_state
    assert s["players"][first]["resources"]["grain"] == 0
    for r in (2, 3, 4, 5):
        assert s["round_goods"][str(r)][str(first)]["food"] == 1


def test_clay_supply_schedules_clay(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give_card(s, first, "C077")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "C077"}})
    for r in (2, 3, 4):
        assert s["round_goods"][str(r)][str(first)]["clay"] == 1


def test_toad_schedules_reed_relative_rounds(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give_card(s, first, "C078")
    give(s, first, food=1)
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "C078"}})
    for r in (6, 8, 10, 12, 14):
        assert s["round_goods"][str(r)][str(first)]["reed"] == 1


def test_stew_schedules_food_on_day_laborer(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "C045")
    s = place(engine, s, {"kind": "place", "space": "day_laborer"})
    for r in (2, 3, 4, 5):
        assert s["round_goods"][str(r)][str(first)]["food"] == 1


def test_garden_claw_scales_with_planted_fields(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    for c in (0, 1):
        p["cells"][c]["type"] = "field"
        p["cells"][c]["crops"] = {"type": "grain", "count": 1}
    give(s, first, wood=1)
    give_card(s, first, "C047")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "C047"}})
    # 2 planted fields * 3 = 6 rounds worth of food, starting round 2.
    for r in range(2, 8):
        assert s["round_goods"][str(r)][str(first)]["food"] == 1


def test_greening_plan_score_bonus_tiers(engine):
    s = make_state(engine, 2)
    inst = put_in_play(s, 0, "C033")
    p = s["players"][0]
    for c in (0, 1, 2, 3):
        p["cells"][c]["type"] = "field"
    spec = cards.CARDS["C033"]
    assert spec["score_bonus"](s, p, inst) == 2  # 4 unplanted fields -> 2pts


def test_studio_conversions_generic_pathway(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "C055")
    p = s["players"][first]
    p["resources"]["wood"] = 1
    convs = dict((key, conv) for key, conv, _inst in
                cards.conversion_options(p))
    key = next(k for k in convs if k.startswith("C055:0"))
    assert convs[key] == {"give": {"wood": 1}, "get": {"food": 2}}


def test_slurry_grants_free_sow_after_diverse_breeding(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    inst = put_in_play(s, first, "C071")
    hook = cards.CARDS["C071"]["hooks"]["breeding"]

    ctx = {"newborns": {"sheep": 1}, "unplaced": {}, "harvest_index": 1,
          "log": [], "actor": first, "extra": {}}
    hook(s, p, inst, ctx)
    assert inst["data"].get("sow_credits", 0) == 0  # only 1 type -> no credit

    ctx2 = {"newborns": {"sheep": 1, "boar": 1}, "unplaced": {},
           "harvest_index": 1, "log": [], "actor": first, "extra": {}}
    hook(s, p, inst, ctx2)
    assert inst["data"]["sow_credits"] == 1

    p["cells"][0]["type"] = "field"
    give(s, first, grain=1)
    assert cards.CARDS["C071"]["card_action"]["available"](s, p, inst)
    pid = p["player_id"]
    s = engine.apply_action(s, pid, {
        "kind": "card_action", "card": "C071",
        "params": {"sow_items": [{"cell": 0, "crop": "grain"}]},
    }).new_state
    p = s["players"][first]
    assert p["cells"][0]["crops"] == {"type": "grain", "count": 3}
    inst = next(i for i in p["minors"] if i["id"] == "C071")
    assert inst["data"]["sow_credits"] == 0


def test_cattle_farm_holds_1_cattle_per_pasture(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    inst = put_in_play(s, first, "C012")

    inst["held"] = {"cattle": 0}
    ok, err = cards.validate_held(s, p)
    assert ok, err

    # No pastures yet -> 0 cattle held.
    inst["held"] = {"cattle": 1}
    ok, err = cards.validate_held(s, p)
    assert not ok

    p["fences"] = sorted(cell_edges(4)) + sorted(cell_edges(8))
    assert len(compute_pastures(p)) == 2
    inst["held"] = {"cattle": 2}
    ok, err = cards.validate_held(s, p)
    assert ok, err

    inst["held"] = {"cattle": 3}
    ok, err = cards.validate_held(s, p)
    assert not ok

    inst["held"] = {"sheep": 1}
    ok, err = cards.validate_held(s, p)
    assert not ok  # only cattle allowed


# ── C015 Trellis ────────────────────────────────────────────────────

def test_trellis_pig_market_bonus_fence(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "occ_woodcutter")
    put_in_play(s, first, "occ_reed_collector")  # satisfy Req 2 occ.
    put_in_play(s, first, "C015")
    p = s["players"][first]
    log = []
    engine._fire(s, "space_used", p,
                {"space_id": "pig_market", "goods": {"boar": 1}, "occupants": [first]},
                log)
    inst = next(i for i in p["minors"] if i["id"] == "C015")
    assert inst["data"]["credits"] == 1
    give(s, first, wood=4)
    pid = p["player_id"]
    s = engine.apply_action(s, pid, {
        "kind": "card_action", "card": "C015",
        "params": {"fences": list(cell_edges(0))}}).new_state
    p = s["players"][first]
    assert len(compute_pastures(p)) == 1
    assert p["resources"]["wood"] == 0


# ── C028 Teacher's Desk ──────────────────────────────────────────────

def test_teachers_desk_occupation_after_major_improvement_space(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "occ_woodcutter")  # satisfy Req 1 occ.
    put_in_play(s, first, "C028")
    give_card(s, first, "occ_reed_collector")
    give(s, first, food=1)
    p = s["players"][first]
    food_before = p["resources"]["food"]
    log = []
    engine._fire(s, "space_used", p,
                {"space_id": "major_improvement", "goods": {}, "occupants": [first]},
                log)
    assert s["prompts"] and s["prompts"][0]["type"] == "choice"
    idx = s["prompts"][0]["data"]["hand"].index("occ_reed_collector")
    pid = p["player_id"]
    s = engine.apply_action(s, pid, {"kind": "choice", "index": idx + 1}).new_state
    p = s["players"][first]
    assert any(i["id"] == "occ_reed_collector" for i in p["occupations"])
    assert p["resources"]["food"] == food_before - 1
