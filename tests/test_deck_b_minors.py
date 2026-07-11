"""Tests for the deck B minor-improvement module
(server/agricola/decks/deck_b_minors.py)."""

import pytest

from server.agricola.engine import AgricolaEngine
from server.agricola import cards, sub_actions
from server.agricola.decks import deck_b_minors
from server.agricola.state import (
    MAJOR_IMPROVEMENTS, NUM_CELLS, cell_edges, compute_pastures,
    is_border_edge, all_edge_keys,
)

from test_agricola import (
    make_state, give, give_card, put_in_play, add_space, place, current_pid,
)


@pytest.fixture
def engine():
    return AgricolaEngine()


# ── Registration completeness ────────────────────────────────────────

def test_registration_completeness():
    db_codes = {c["code"] for c in cards.compendium().values()
               if c["deck"] == "B" and c["type"] == "minor"}
    registered = {cid for cid in cards.CARDS if cid in db_codes}
    unimplemented = set(deck_b_minors.UNIMPLEMENTED)
    assert unimplemented <= db_codes
    assert registered & unimplemented == set()
    assert registered | unimplemented == db_codes


# ── Smoke test: every implemented card gets played once ──────────────

_DUMMY_OCCS = ["occ_woodcutter", "occ_reed_collector", "occ_clay_digger"]

_NEEDS_OCC = {"B006": 1, "B040": 2, "B041": 3, "B048": 1, "B055": 2,
             "B073": 3, "B075": 1, "B076": 1, "B018": 2, "B021": 1}


def _prep_prereqs(state, pidx, cid):
    p = state["players"][pidx]
    n = _NEEDS_OCC.get(cid)
    if n:
        for i in range(n):
            put_in_play(state, pidx, _DUMMY_OCCS[i])
    if cid == "B031":
        p["improvements"].append("pottery")
    if cid == "B037":
        for c in range(6):
            p["cells"][c]["type"] = "field"
        p["pets"] = {"sheep": 1, "boar": 1, "cattle": 1}
    if cid == "B051":
        state["round"] = 7
    if cid == "B003":
        give_card(state, pidx, "occ_woodcutter")


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


_DB_B_MINOR_CODES = {c["code"] for c in cards.compendium().values()
                    if c["deck"] == "B" and c["type"] == "minor"}
ALL_B_MINORS = sorted(cid for cid in cards.CARDS if cid in _DB_B_MINOR_CODES)


@pytest.mark.parametrize("cid", ALL_B_MINORS)
def test_smoke_play_every_card(engine, cid):
    s = make_state(engine, 2)
    first = s["current_player"]
    _prep_prereqs(s, first, cid)
    give(s, first, **sub_actions.cost_alternatives(cards.CARDS[cid]["cost"])[0])
    give_card(s, first, cid)
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": cid}})
    pid = s["players"][first]["player_id"]
    s = _resolve_all_prompts(engine, s, pid)
    p = s["players"][first]
    assert cid not in p["hand_minors"]
    assert any(i["id"] == cid for i in p["minors"])


# ── Targeted effect tests ────────────────────────────────────────────

def test_wood_pile_counts_accumulation_workers(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    add_space(s, "forest2", "Forest 2", acc=True, supply={"wood": 3})
    sp = next(x for x in s["action_spaces"] if x["id"] == "forest2")
    sp["occupied_by"] = first
    give_card(s, first, "B004")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "B004"}})
    assert s["players"][first]["resources"]["wood"] == 1


def test_store_of_experience_tiers(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["hand_occupations"] = ["occ_woodcutter"] * 7  # 7 left => wood
    give_card(s, first, "B005")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "B005"}})
    assert s["players"][first]["resources"]["wood"] == 1


def test_excursion_to_quarry_scales_with_people(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "occ_woodcutter")
    give(s, first, food=2)
    give_card(s, first, "B006")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "B006"}})
    assert s["players"][first]["resources"]["stone"] == \
        s["players"][first]["people_total"] == 2


def test_beating_rod_exchange_reed_for_cattle(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give(s, first, reed=1)
    give_card(s, first, "B009")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "B009"}})
    s = place(engine, s, {"kind": "choice", "index": 1})
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {
        "kind": "accommodate", "pets": {"cattle": 1}}).new_state
    p = s["players"][first]
    assert p["resources"]["reed"] == 0
    assert p["pets"]["cattle"] == 1


def test_hawktower_builds_free_room_in_stone_house(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["house_type"] = "stone"
    inst = put_in_play(s, first, "B014")
    s["round"] = 12
    rooms_before = sum(1 for c in p["cells"] if c["type"] == "room")
    ctx = {"log": [], "actor": first, "extra": {}}
    cards.CARDS["B014"]["hooks"]["round_start"](s, p, inst, ctx)
    rooms_after = sum(1 for c in p["cells"] if c["type"] == "room")
    assert rooms_after == rooms_before + 1


def test_hawktower_discards_without_stone_house(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    inst = put_in_play(s, first, "B014")
    s["round"] = 12
    ctx = {"log": [], "actor": first, "extra": {}}
    cards.CARDS["B014"]["hooks"]["round_start"](s, p, inst, ctx)
    rooms = sum(1 for c in p["cells"] if c["type"] == "room")
    assert rooms == 2  # unchanged — house is still wood


def test_forest_plow_pays_wood_and_plows_field(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "B017")
    add_space(s, "forest", "Forest", acc=True, supply={"wood": 3})
    s = place(engine, s, {"kind": "place", "space": "forest"})
    p = s["players"][first]
    assert p["resources"]["wood"] == 3
    pid = p["player_id"]
    acts = engine.get_valid_actions(s, pid)
    choice = next(a for a in acts if a["kind"] == "choice")
    s = place(engine, s, {"kind": "choice", "index": 0})
    p = s["players"][first]
    assert p["resources"]["wood"] == 1  # paid 2 of the 3
    assert any(c["type"] == "field" for c in p["cells"])
    forest = next(sp for sp in s["action_spaces"] if sp["id"] == "forest")
    assert forest["supply"]["wood"] == 2  # refunded


def test_grassland_harrow_schedules_and_offers_plow(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "occ_woodcutter")
    put_in_play(s, first, "occ_reed_collector")
    give(s, first, wood=1)
    put_in_play(s, first, "B018")
    inst = next(i for i in s["players"][first]["minors"] if i["id"] == "B018")
    play_fn = cards.CARDS["B018"]["hooks"]["play"]
    log = []
    ctx = {"log": log, "actor": first, "extra": {}, "params": {}}
    play_fn(s, s["players"][first], inst, ctx)
    target = inst["data"]["plow_round"]
    assert target == s["round"] + 1  # 1 wood in supply
    s["round"] = target
    p = s["players"][first]
    round_ctx = {"log": [], "actor": first, "extra": {}}
    cards.CARDS["B018"]["hooks"]["round_start"](s, p, inst, round_ctx)
    assert s["prompts"] and s["prompts"][0]["card"] == "B018"
    pid = p["player_id"]
    s = place(engine, s, {"kind": "choice", "index": 0})
    assert any(c["type"] == "field" for c in s["players"][first]["cells"])


def test_chain_float_schedules_three_rounds(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give(s, first, wood=3)
    put_in_play(s, first, "B020")
    inst = s["players"][first]["minors"][-1]
    play_fn = cards.CARDS["B020"]["hooks"]["play"]
    ctx = {"log": [], "actor": first, "extra": {}, "params": {}}
    play_fn(s, s["players"][first], inst, ctx)
    assert inst["data"]["plow_rounds"] == [8, 9, 10]


def test_toolbox_offers_major_after_stable(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "B027")
    give(s, first, wood=2, clay=2, stone=2)
    s = place(engine, s, {"kind": "place", "space": "farm_expansion",
                          "stables": [0]})
    pid = s["players"][first]["player_id"]
    acts = engine.get_valid_actions(s, pid)
    choice = next(a for a in acts if a["kind"] == "choice")
    idx = choice["options"].index("Build Pottery")
    s = place(engine, s, {"kind": "choice", "index": idx})
    assert "pottery" in s["players"][first]["improvements"]
    assert s["players"][first]["resources"]["clay"] == 0
    assert s["players"][first]["resources"]["stone"] == 0


def test_forestry_studies_free_occupation(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "B028")
    give(s, first, food=2)
    give_card(s, first, "occ_woodcutter")
    add_space(s, "forest", "Forest", acc=True, supply={"wood": 3})
    s = place(engine, s, {"kind": "place", "space": "forest"})
    p = s["players"][first]
    assert p["resources"]["wood"] == 3
    pid = p["player_id"]
    acts = engine.get_valid_actions(s, pid)
    choice = next(a for a in acts if a["kind"] == "choice")
    idx = choice["options"].index("Play Woodcutter for free")
    s = place(engine, s, {"kind": "choice", "index": idx})
    p = s["players"][first]
    assert p["resources"]["wood"] == 1  # returned 2 wood
    assert any(i["id"] == "occ_woodcutter" for i in p["occupations"])
    forest = next(sp for sp in s["action_spaces"] if sp["id"] == "forest")
    assert forest["supply"]["wood"] == 2


def test_pottery_yard_scores_adjacent_empty_spaces(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["improvements"].append("pottery")
    put_in_play(s, first, "B031")
    inst = p["minors"][-1]
    assert deck_b_minors._pottery_yard_score(s, p, inst) == 2  # lots of empty cells


def test_kettle_conversion_tiers_and_bonus_points(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["resources"]["food"] = 0
    put_in_play(s, first, "B032")
    give(s, first, grain=8)
    pid = p["player_id"]
    s = engine.apply_action(s, pid, {
        "kind": "card_action", "card": "B032", "params": {"tier": 2}}).new_state
    p = s["players"][first]
    assert p["resources"]["grain"] == 3
    assert p["resources"]["food"] == 5
    inst = p["minors"][-1]
    assert cards.score_bonuses(s, p) == 2


def test_grange_requires_fields_and_animals(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["resources"]["food"] = 0
    for c in range(6):
        p["cells"][c]["type"] = "field"
    p["pets"] = {"sheep": 1, "boar": 1, "cattle": 1}
    give_card(s, first, "B037")
    assert cards.check_prereq(s, p, "B037")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "B037"}})
    assert s["players"][first]["resources"]["food"] == 1


def test_brewery_pond_space_bonus(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "occ_woodcutter")
    put_in_play(s, first, "occ_reed_collector")
    put_in_play(s, first, "B040")
    add_space(s, "reed_bank", "Reed Bank", acc=True, supply={"reed": 1})
    s = place(engine, s, {"kind": "place", "space": "reed_bank"})
    p = s["players"][first]
    assert p["resources"]["grain"] == 1
    assert p["resources"]["wood"] == 1


def test_hauberg_alternates_wood_and_boar(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "B041")
    inst = p["minors"][-1]
    play_fn = cards.CARDS["B041"]["hooks"]["play"]
    ctx = {"log": [], "actor": first, "extra": {}, "params": {"start": "wood"}}
    play_fn(s, p, inst, ctx)
    rnd = s["round"]
    rg = s["round_goods"]
    assert rg[str(rnd + 1)][str(first)]["wood"] == 2
    assert rg[str(rnd + 2)][str(first)]["boar"] == 1
    assert rg[str(rnd + 3)][str(first)]["wood"] == 2
    assert rg[str(rnd + 4)][str(first)]["boar"] == 1


def test_chophouse_schedules_food_on_grain_seeds(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "B043")
    s = place(engine, s, {"kind": "place", "space": "grain_seeds"})
    rnd = s["round"]
    for r in range(rnd + 1, rnd + 4):
        assert s["round_goods"][str(r)][str(first)]["food"] == 1


def test_chophouse_alt_cost_clay(engine):
    """Cost "2W or 2C" -- paying the non-first (clay) alternative via
    cost_option still plays the card."""
    s = make_state(engine, 2)
    first = s["current_player"]
    give(s, first, clay=2)
    give_card(s, first, "B043")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "B043", "cost_option": 1}})
    p = s["players"][first]
    assert p["resources"]["clay"] == 0 and p["resources"]["wood"] == 0
    assert any(i["id"] == "B043" for i in p["minors"])


def test_chick_stable_alt_cost_clay(engine):
    """Cost "1W or 1C" -- paying the non-first (clay) alternative via
    cost_option still plays the card and schedules food."""
    s = make_state(engine, 2)
    first = s["current_player"]
    give(s, first, clay=1)
    give_card(s, first, "B044")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "B044", "cost_option": 1}})
    p = s["players"][first]
    assert p["resources"]["clay"] == 0 and p["resources"]["wood"] == 0
    rnd = s["round"]
    for off in (3, 4):
        r = rnd + off
        if r <= 14:
            assert s["round_goods"][str(r)][str(first)]["food"] == 2


def test_club_house_alt_cost_clay(engine):
    """Cost "3W or 2C" (note: NOT a symmetric quantity) -- paying the
    non-first (2 clay) alternative via cost_option still plays the card
    and schedules food/stone."""
    s = make_state(engine, 2)
    first = s["current_player"]
    give(s, first, clay=2)
    give_card(s, first, "B046")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "B046", "cost_option": 1}})
    p = s["players"][first]
    assert p["resources"]["clay"] == 0 and p["resources"]["wood"] == 0
    rnd = s["round"]
    for r in range(rnd + 1, min(14, rnd + 4) + 1):
        assert s["round_goods"][str(r)][str(first)]["food"] == 1
    stone_round = rnd + 5
    if stone_round <= 14:
        assert s["round_goods"][str(stone_round)][str(first)]["stone"] == 1


def test_forest_stone_stores_and_releases_food(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    put_in_play(s, first, "occ_woodcutter")
    give(s, first, wood=2)
    give_card(s, first, "B048")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "B048"}})
    inst = next(i for i in s["players"][first]["minors"] if i["id"] == "B048")
    assert inst["data"]["food"] == 2
    food_before = s["players"][first]["resources"]["food"]
    s = place(engine, s, {"kind": "place", "space": "day_laborer"})  # other's turn
    s = place(engine, s, {"kind": "place", "space": "forest"})  # back to first
    p = s["players"][first]
    inst = next(i for i in p["minors"] if i["id"] == "B048")
    assert inst["data"]["food"] == 1
    assert p["resources"]["food"] == food_before + 1
    add_space(s, "western_quarry", "Western Quarry", acc=True, supply={"stone": 1})
    s = place(engine, s, {"kind": "place", "space": "fishing"})  # other's turn
    s = place(engine, s, {"kind": "place", "space": "western_quarry"})  # back to first
    inst = next(i for i in s["players"][first]["minors"] if i["id"] == "B048")
    assert inst["data"]["food"] == 3


def test_forest_stone_alt_cost_stone(engine):
    """Cost "2W or 1S" (note: NOT a symmetric quantity) -- paying the
    non-first (1 stone) alternative via cost_option still plays the
    card."""
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "occ_woodcutter")
    give(s, first, stone=1)
    give_card(s, first, "B048")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "B048", "cost_option": 1}})
    p = s["players"][first]
    assert p["resources"]["stone"] == 0 and p["resources"]["wood"] == 0
    inst = next(i for i in p["minors"] if i["id"] == "B048")
    assert inst["data"]["food"] == 2


def test_scales_grants_food_on_matching_counts(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["resources"]["food"] = 1
    put_in_play(s, first, "B049")  # 0 improvements/minors == 0 occupations
    give_card(s, first, "occ_woodcutter")
    s = place(engine, s, {"kind": "place", "space": "lessons",
                          "card": "occ_woodcutter"})
    # After playing: 1 occupation, 1 minor (Scales itself) => equal => +2 food.
    assert s["players"][first]["resources"]["food"] == 1 + 2


def test_digging_spate_scales_with_boar(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["resources"]["food"] = 0
    p["cells"][0]["animal"] = {"type": "boar", "count": 2}
    s["round"] = 7
    put_in_play(s, first, "B051")
    s = place(engine, s, {"kind": "place", "space": "clay_pit"})
    assert s["players"][first]["resources"]["food"] == 2


def test_growing_farm_prereq_and_gain(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["resources"]["food"] = 0
    give(s, first, clay=2, reed=1)
    give_card(s, first, "B052")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "B052"}})
    assert s["players"][first]["resources"]["food"] == s["round"] == 1


def test_tumbrel_food_per_stable_on_sow(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["resources"]["food"] = 0
    p["cells"][0]["stable"] = True
    p["cells"][1]["stable"] = True
    p["cells"][2]["type"] = "field"
    give(s, first, wood=1)
    give_card(s, first, "B054")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "B054"}})
    p = s["players"][first]
    assert p["resources"]["food"] == 2  # on-play gain
    add_space(s, "grain_utilization", "Sow and/or Bake Bread")
    give(s, first, grain=1)
    s = place(engine, s, {"kind": "place", "space": "day_laborer"})  # other's turn
    s = place(engine, s, {"kind": "place", "space": "grain_utilization",
                          "sow": [{"cell": 2, "crop": "grain"}]})
    assert s["players"][first]["resources"]["food"] == 2 + 2  # +2 stables


def test_maintenance_premium_restocks_on_renovate(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "occ_woodcutter")
    put_in_play(s, first, "occ_reed_collector")
    give_card(s, first, "B055")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "B055"}})
    inst = next(i for i in s["players"][first]["minors"] if i["id"] == "B055")
    assert inst["data"]["food"] == 3
    p = s["players"][first]
    p["resources"]["clay"] = 5
    p["resources"]["reed"] = 3
    add_space(s, "house_redevelopment", "House Redevelopment")
    s = place(engine, s, {"kind": "place", "space": "day_laborer"})  # other's turn
    s = place(engine, s, {"kind": "place", "space": "house_redevelopment"})
    inst = next(i for i in s["players"][first]["minors"] if i["id"] == "B055")
    assert inst["data"]["food"] == 3


def test_crack_weeder_food_per_vegetable_field(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["resources"]["food"] = 0
    p["cells"][0]["type"] = "field"
    p["cells"][0]["crops"] = {"type": "vegetable", "count": 2}
    p["cells"][1]["type"] = "field"
    p["cells"][1]["crops"] = {"type": "vegetable", "count": 1}
    put_in_play(s, first, "B058")
    ctx = {"log": [], "actor": first, "extra": {}, "harvest_index": 1}
    cards.CARDS["B058"]["hooks"]["harvest_field"](s, p, p["minors"][-1], ctx)
    assert p["resources"]["food"] == 2


def test_tasting_exchange_grain_on_lessons(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["resources"]["food"] = 0
    put_in_play(s, first, "B063")
    give(s, first, grain=1)
    give_card(s, first, "occ_woodcutter")
    s = place(engine, s, {"kind": "place", "space": "lessons",
                          "card": "occ_woodcutter"})
    pid = s["players"][first]["player_id"]
    acts = engine.get_valid_actions(s, pid)
    choice = next(a for a in acts if a["kind"] == "choice")
    s = place(engine, s, {"kind": "choice", "index": 0})
    p = s["players"][first]
    assert p["resources"]["grain"] == 0
    assert p["resources"]["food"] == 4


def test_mill_wheel_bonus_when_fishing_occupied(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    p = s["players"][first]
    p["resources"]["food"] = 0
    p["cells"][0]["type"] = "field"
    put_in_play(s, first, "B064")
    add_space(s, "grain_utilization", "Sow and/or Bake Bread")
    fishing = next(sp for sp in s["action_spaces"] if sp["id"] == "fishing")
    fishing["occupied_by"] = other
    give(s, first, grain=1)
    s = place(engine, s, {"kind": "place", "space": "grain_utilization",
                          "sow": [{"cell": 0, "crop": "grain"}]})
    assert s["players"][first]["resources"]["food"] == 2


def test_gift_basket_scales_with_rooms(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "occ_woodcutter")
    put_in_play(s, first, "occ_reed_collector")
    put_in_play(s, first, "occ_clay_digger")
    give(s, first, reed=1)
    give_card(s, first, "B073")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "B073"}})
    # Default 2 rooms => vegetable.
    assert s["players"][first]["resources"]["vegetable"] == 1


def test_wood_workshop_discounts_improvement_wood(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "occ_woodcutter")
    put_in_play(s, first, "B075")
    cost = cards.modified_cost(s, p, "improvement", MAJOR_IMPROVEMENTS["joinery"]["cost"])
    assert cost["wood"] == MAJOR_IMPROVEMENTS["joinery"]["cost"]["wood"] - 1


def test_ceilings_schedule_and_renovate_removal(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "occ_woodcutter")
    give(s, first, clay=1)
    give_card(s, first, "B076")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "B076"}})
    rnd = s["round"]
    assert s["round_goods"][str(rnd + 1)][str(first)]["wood"] == 1
    p = s["players"][first]
    p["resources"]["clay"] = 5
    p["resources"]["reed"] = 3
    add_space(s, "house_redevelopment", "House Redevelopment")
    s = place(engine, s, {"kind": "place", "space": "day_laborer"})  # other's turn
    s = place(engine, s, {"kind": "place", "space": "house_redevelopment"})
    assert s["round_goods"][str(rnd + 1)][str(first)]["wood"] == 0


def test_corf_triggers_on_any_player(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    put_in_play(s, other, "B079")
    add_space(s, "western_quarry", "Western Quarry", acc=True, supply={"stone": 3})
    s = place(engine, s, {"kind": "place", "space": "western_quarry"})
    assert s["players"][other]["resources"]["stone"] == 1


def test_handcart_takes_from_full_space(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    inst = put_in_play(s, first, "B081")
    forest = next(sp for sp in s["action_spaces"] if sp["id"] == "forest")
    forest["supply"]["wood"] = 6
    ctx = {"log": [], "actor": first, "extra": {}}
    cards.CARDS["B081"]["hooks"]["round_start"](s, p, inst, ctx)
    assert s["prompts"] and s["prompts"][0]["card"] == "B081"
    choice_options = s["prompts"][0]["options"]
    assert any("forest" in o.lower() or "wood" in o.lower() for o in choice_options)
    pid = p["player_id"]
    s = place(engine, s, {"kind": "choice", "index": 0})
    assert s["players"][first]["resources"]["wood"] == 1
    forest = next(sp for sp in s["action_spaces"] if sp["id"] == "forest")
    assert forest["supply"]["wood"] == 5


def test_value_assets_buys_after_harvest(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["resources"]["food"] = 0
    put_in_play(s, first, "B082")
    give(s, first, food=2)
    ctx = {"log": [], "actor": first, "extra": {}, "harvest_index": 1}
    cards.CARDS["B082"]["hooks"]["harvest_field"](s, p, p["minors"][-1], ctx)
    pid = p["player_id"]
    acts = engine.get_valid_actions(s, pid)
    choice = next(a for a in acts if a["kind"] == "choice")
    idx = choice["options"].index("Buy 1 stone for 2 food")
    s = place(engine, s, {"kind": "choice", "index": idx})
    assert s["players"][first]["resources"]["stone"] == 1
    assert s["players"][first]["resources"]["food"] == 0


def test_b083_stack_card_action(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    give_card(s, first, "B083")
    give(s, first, clay=2)
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "B083"}})
    give(s, first, clay=1)
    s = place(engine, s, {"kind": "place", "space": "day_laborer"})  # other's turn
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {
        "kind": "card_action", "card": "B083"}).new_state
    assert s["prompts"][0]["gained"] == {"sheep": 1}
    s = engine.apply_action(s, pid, {
        "kind": "accommodate", "pets": {"sheep": 1}}).new_state
    p = s["players"][first]
    assert p["resources"]["clay"] == 0
    assert p["pets"].get("sheep") == 1  # top of the stack


def test_moonshine_plays_random_occupation_for_food(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["hand_occupations"] = ["occ_woodcutter"]
    p["resources"]["food"] = 0
    give(s, first, food=2)
    give_card(s, first, "B003")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "B003"}})
    pid = p["player_id"]
    acts = engine.get_valid_actions(s, pid)
    choice = next(a for a in acts if a["kind"] == "choice")
    idx = choice["options"].index("Play Woodcutter for 2 food")
    s = place(engine, s, {"kind": "choice", "index": idx})
    p = s["players"][first]
    assert p["resources"]["food"] == 0
    assert any(i["id"] == "occ_woodcutter" for i in p["occupations"])


def test_moonshine_passes_left_when_declined(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    p = s["players"][first]
    p["hand_occupations"] = ["occ_woodcutter"]
    give_card(s, first, "B003")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "B003"}})
    pid = p["player_id"]
    acts = engine.get_valid_actions(s, pid)
    choice = next(a for a in acts if a["kind"] == "choice")
    idx = choice["options"].index("Give it to the left neighbor")
    s = place(engine, s, {"kind": "choice", "index": idx})
    assert "occ_woodcutter" in s["players"][other]["hand_occupations"]
    assert "occ_woodcutter" not in s["players"][first]["hand_occupations"]


def test_feedyard_capacity_and_breeding_payout(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    inst = put_in_play(s, first, "B011")
    p["fences"] = sorted(set(cell_edges(4)) | set(cell_edges(9)))
    assert len(compute_pastures(p)) == 2
    assert cards.CARDS["B011"]["holds_animals"](s, p, inst) == {"total": 2}

    inst["held"] = {"sheep": 1}  # 1 of 2 spots used -> 1 unused
    hook = cards.CARDS["B011"]["hooks"]["breeding"]
    ctx = {"log": [], "extra": {}, "newborns": {}, "unplaced": {},
          "harvest_index": 1, "actor": first}
    hook(s, p, inst, ctx)
    assert ctx["extra"] == {"food": 1}


def test_hayloft_barn_food_pile_and_family_growth_bypass(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    inst = put_in_play(s, first, "B021")
    add_space(s, "grain_test_b021", "Grain Test", acc=True, supply={"grain": 5})
    food_before = p["resources"]["food"]
    s = place(engine, s, {"kind": "place", "space": "grain_test_b021"})
    p = s["players"][first]
    assert p["resources"]["food"] == food_before + 1
    inst = next(i for i in p["minors"] if i["id"] == "B021")
    assert inst["data"]["food"] == 3

    # Drain the rest of the pile directly to reach "once it is empty".
    inst["data"]["food"] = 0
    assert cards.CARDS["B021"]["card_action"]["available"](s, p, inst)
    people_before = p["people_total"]
    rooms = sum(1 for c in p["cells"] if c["type"] == "room")
    assert rooms <= people_before  # no spare room -- the bypass is the point
    ctx = {"log": []}
    cards.CARDS["B021"]["card_action"]["apply"](s, p, inst, ctx)
    p = s["players"][first]
    assert p["people_total"] == people_before + 1
    assert not cards.CARDS["B021"]["card_action"]["available"](s, p, inst)


def test_forest_inn_toll_and_wood_exchange(engine):
    """B042 card_space: a non-owner placer pays the owner 1 food first
    (I337-style toll), then may exchange wood at one of the affordable
    tiers (5/7/9 wood for a flat 8 wood plus 2/4/7 food)."""
    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    give_card(s, first, "B042")
    give(s, first, clay=1, reed=1)
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "B042"}})
    give(s, other, food=3, wood=5)
    other_food = s["players"][other]["resources"]["food"]
    owner_food = s["players"][first]["resources"]["food"]
    other_pid = s["players"][other]["player_id"]

    s = engine.apply_action(
        s, other_pid, {"kind": "place", "space": "card:B042"}).new_state
    assert s["players"][other]["resources"]["food"] == other_food - 1
    assert s["players"][first]["resources"]["food"] == owner_food + 1
    assert s["prompts"][0]["type"] == "choice"
    # Only the 5-wood tier is affordable (other has exactly 5 wood).
    assert s["prompts"][0]["options"] == [
        "Pay 5 wood for 8 wood and 2 food", "Skip"]

    s = engine.apply_action(s, other_pid, {"kind": "choice", "index": 0}).new_state
    assert s["players"][other]["resources"]["wood"] == 5 - 5 + 8
    assert s["players"][other]["resources"]["food"] == other_food - 1 + 2


def test_forest_inn_owner_use_has_no_toll(engine):
    """The card's own owner pays no toll for their own placement."""
    s = make_state(engine, 2)
    first = s["current_player"]
    give_card(s, first, "B042")
    give(s, first, clay=1, reed=1)
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "B042"}})
    other = (first + 1) % 2
    s["current_player"] = first
    give(s, first, wood=0)
    owner_food = s["players"][first]["resources"]["food"]
    first_pid = s["players"][first]["player_id"]

    s = engine.apply_action(
        s, first_pid, {"kind": "place", "space": "card:B042"}).new_state
    assert s["players"][first]["resources"]["food"] == owner_food
    # No wood to exchange -- only Skip offered.
    assert s["prompts"][0]["options"] == ["Skip"]
    s = engine.apply_action(s, first_pid, {"kind": "choice", "index": 0}).new_state


def test_final_scenario_owner_only_renovation_until_round_14(engine):
    """B023 Final Scenario, the real card: the card_space + sub_actions
    recipe (decks/GUIDE.md's "B023 Final Scenario: engine assessment"
    section; test_b023_style_recipe_owner_only_until_round_14 in
    tests/test_agricola.py is the temp_card version of this same
    flow). Reveals round 14's action space (a Renovation, mirroring
    farm_redevelopment) early as a private, owner-only space; the gate
    closes once round 14 itself starts."""
    s = make_state(engine, 2)
    owner = s["current_player"]
    other = 1 - owner
    give_card(s, owner, "B023")
    give(s, owner, reed=2, clay=5)  # 1 reed for B023's own cost, 1 for the renovation
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "B023"}})
    # It's `other`'s turn now; force it back to `owner` so the rest of
    # this test can exercise the card_space directly.
    s["current_player"] = owner

    sid = "card:B023"
    space = next(sp for sp in s["action_spaces"] if sp["id"] == sid)
    assert not engine._card_space_usable(s, s["players"][other], space)
    assert engine._card_space_usable(s, s["players"][owner], space)

    s = engine.apply_action(
        s, s["players"][owner]["player_id"],
        {"kind": "place", "space": sid}).new_state
    assert s["players"][owner]["house_type"] in ("clay", "stone")

    # Gate closes once round 14 starts -- the real farm_redevelopment
    # space takes over from here.
    s["round"] = 14
    space = next(sp for sp in s["action_spaces"] if sp["id"] == sid)
    assert not engine._card_space_usable(s, s["players"][owner], space)


def test_final_scenario_cannot_be_played_after_round_13(engine):
    s = make_state(engine, 2)
    s["round"] = 14
    give_card(s, 0, "B023")
    give(s, 0, reed=1)
    with pytest.raises(ValueError):
        place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "B023"}})


def test_wood_palisades_border_token_priced_scored_and_bypasses_cap(engine):
    """B030: a wood-token border edge is priced at the card's own 2
    wood (independent of normal fence pricing), excluded from the
    15-fence cap, restricted to border edges, and worth 1 bonus point
    per token -- mirrors test_agricola.py's temp_card proof
    (test_fence_tokens_mixed_layout_geometry_cost_and_max_fences_bypass)
    with the real card."""
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    give_card(s, first, "B030")
    give(s, first, food=1)
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "B030"}})
    p = s["players"][first]
    inst = next(i for i in p["minors"] if i["id"] == "B030")

    for c in p["cells"]:
        c["type"] = "empty"
    border = [e for e in all_edge_keys() if is_border_edge(e)]
    assert len(border) == 16
    token_edge = border[0]
    give(s, first, wood=17)  # 15 normal fences + 2 for the 1 token
    sub_actions.build_fences(s, p, border, [], tokens=[token_edge])
    assert p["fence_tokens"] == {token_edge: "B030"}
    assert p["resources"]["wood"] == 0
    assert compute_pastures(p) == [sorted(range(NUM_CELLS))]
    assert len(p["fences"]) - len(p["fence_tokens"]) == 15  # cap bypassed

    assert cards.CARDS["B030"]["score_bonus"](s, p, inst) == 1

    # Border-only: an interior edge can't be a token.
    interior_edge = next(e for e in cell_edges(6) if not is_border_edge(e))
    with pytest.raises(ValueError):
        sub_actions.build_fences(s, p, cell_edges(6), [],
                                 tokens=[interior_edge])


def test_wood_palisades_bonus_uncapped_in_base_play():
    """The '(FotM) Up to a maximum of 4 bonus points' ruling is
    FotM-only and ignored per the module convention -- base play scores
    1 point per token with no ceiling."""
    p = {"fence_tokens": {i: "B030" for i in range(5)}}
    inst = {"id": "B030"}
    assert cards.CARDS["B030"]["score_bonus"](None, p, inst) == 5


# ── B001 Upscale Lifestyle ───────────────────────────────────────────

def test_upscale_lifestyle_clay_and_renovation(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give_card(s, first, "B001")
    give(s, first, wood=3, reed=1)
    pid = s["players"][first]["player_id"]
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "B001", "params": {"renovate": True}}})
    p = s["players"][first]
    assert p["house_type"] == "clay"
    # 5 clay granted, then renovation costs {clay: rooms(2), reed: 1}.
    assert p["resources"]["clay"] == 3
    assert p["resources"]["reed"] == 0


def test_upscale_lifestyle_renovation_then_free_fence(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give_card(s, first, "B001")
    give(s, first, wood=3, reed=1)  # 3 wood pays to play the card itself
    pid = s["players"][first]["player_id"]
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "B001",
                                   "params": {"renovate": True,
                                              "fences": list(cell_edges(0))}}})
    p = s["players"][first]
    assert p["resources"]["wood"] == 0  # fences cost no wood
    assert len(compute_pastures(p)) == 1


def test_upscale_lifestyle_skip_renovation(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give_card(s, first, "B001")
    give(s, first, wood=3)  # to play the card itself
    pid = s["players"][first]["player_id"]
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "B001"}})
    p = s["players"][first]
    assert p["house_type"] == "wood"
    assert p["resources"]["clay"] == 5
