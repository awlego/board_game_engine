"""Tests for the deck D occupation module
(server/agricola/decks/deck_d_occupations.py)."""

import pytest

from server.agricola.engine import AgricolaEngine
from server.agricola import cards
from server.agricola.decks import deck_d_occupations as deck_d
from server.agricola.state import MAX_STABLES, TOTAL_ROUNDS

from test_agricola import (
    make_state, give, give_card, put_in_play, add_space, place, current_pid,
)


@pytest.fixture
def engine():
    return AgricolaEngine()


# ── Registration completeness ────────────────────────────────────────

def test_registration_completeness():
    db_codes = {c["code"] for c in cards.compendium().values()
               if c["deck"] == "D" and c["type"] == "occupation"}
    registered = {cid for cid in cards.CARDS if cid in db_codes}
    unimplemented = set(deck_d.UNIMPLEMENTED)
    assert unimplemented <= db_codes
    assert registered & unimplemented == set()
    assert registered | unimplemented == db_codes


# ── Smoke test: every implemented card gets played once ──────────────

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


ALL_D_OCCS = sorted(cid for cid in cards.CARDS
                    if cards.CARDS[cid]["deck"] == "D"
                    and cards.CARDS[cid]["type"] == "occupation")


@pytest.mark.parametrize("cid", ALL_D_OCCS)
def test_smoke_play_every_card(engine, cid):
    s = make_state(engine, 2)
    first = s["current_player"]
    give(s, first, food=5)
    give_card(s, first, cid)
    s = place(engine, s, {"kind": "place", "space": "lessons", "card": cid})
    pid = s["players"][first]["player_id"]
    s = _resolve_all_prompts(engine, s, pid)
    p = s["players"][first]
    assert cid not in p["hand_occupations"]
    assert any(i["id"] == cid for i in p["occupations"])


# ── Targeted effect tests ────────────────────────────────────────────

def test_stablehand_free_stable_after_fences(engine):
    from server.agricola.state import cell_edges
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "D089")
    give(s, first, wood=4)
    add_space(s, "fencing", "Fencing")
    s = place(engine, s, {"kind": "place", "space": "fencing",
                          "fences": sorted(cell_edges(0))})
    pid = s["players"][first]["player_id"]
    acts = engine.get_valid_actions(s, pid)
    choice = next(a for a in acts if a["kind"] == "choice")
    idx = next(i for i, o in enumerate(choice["options"]) if o.startswith("Cell"))
    s = place(engine, s, {"kind": "choice", "index": idx})
    assert sum(1 for c in s["players"][first]["cells"] if c["stable"]) == 1


def test_child_ombudsman_free_family_growth_and_penalty(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "D092")
    p["cells"][0]["type"] = "room"  # a spare room beyond people_total
    s["round"] = 5
    pid = p["player_id"]
    assert deck_d._child_ombudsman_available(s, p, p["occupations"][-1])
    s = engine.apply_action(s, pid, {
        "kind": "card_action", "card": "D092"}).new_state
    p = s["players"][first]
    assert p["people_total"] == 3
    assert p["people_placed"] == 1
    inst = p["occupations"][-1]
    assert cards.score_bonuses(s, p) == -2


def test_earthenware_potter_bonus_capped_by_people_and_clay(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "D099")
    inst = p["occupations"][-1]
    deck_d._earthenware_potter_play(s, p, inst, {})
    give(s, first, clay=1)
    # people_total=2, clay=1 -> bonus capped at 1.
    assert cards.score_bonuses(s, p) == 1
    inst["data"]["round_played"] = 6  # played too late
    assert cards.score_bonuses(s, p) == 0


def test_lord_of_the_manor_counts_maxed_categories(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "D100")
    give(s, first, grain=8, vegetable=4)  # both hit the max-4-point tier
    assert cards.score_bonuses(s, p) == 2


def test_sculptor_clay_space_bonus(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "D105")
    food_before = s["players"][first]["resources"]["food"]
    add_space(s, "clay_pit", "Clay Pit", acc=True, supply={"clay": 1})
    s = place(engine, s, {"kind": "place", "space": "clay_pit"})
    assert s["players"][first]["resources"]["food"] == food_before + 1


def test_sculptor_stone_space_bonus(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "D105")
    add_space(s, "western_quarry", "Western Quarry", acc=True, supply={"stone": 1})
    s = place(engine, s, {"kind": "place", "space": "western_quarry"})
    assert s["players"][first]["resources"]["grain"] == 1


def test_whisky_distiller_schedules_food_two_rounds_ahead(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "D106")
    give(s, first, grain=1)
    pid = p["player_id"]
    assert deck_d._whisky_distiller_available(s, p, p["occupations"][-1])
    s = engine.apply_action(s, pid, {
        "kind": "card_action", "card": "D106"}).new_state
    assert s["players"][first]["resources"]["grain"] == 0
    assert s["round_goods"][str(s["round"] + 2)][str(first)]["food"] == 4


def test_sowing_master_play_gain_and_sow_bonus(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "D109")
    inst = p["occupations"][-1]
    food_before = p["resources"]["food"]
    ctx = {"log": [], "actor": first, "extra": {}, "sown": [(0, "grain")]}
    cards.CARDS["D109"]["hooks"]["sow"](s, p, inst, ctx)
    assert p["resources"]["food"] == food_before + 2


def test_fish_farmer_bonus_when_fishing_has_food(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "D110")
    inst = p["occupations"][-1]
    fishing = next(sp for sp in s["action_spaces"] if sp["id"] == "fishing")
    fishing["supply"] = {"food": 1}
    ctx = {"log": [], "actor": first, "extra": {}, "space_id": "reed_bank",
          "goods": {"reed": 1}}
    cards.CARDS["D110"]["hooks"]["space_used"](s, p, inst, ctx)
    assert ctx["extra"].get("food") == 2


def test_fish_farmer_no_bonus_when_fishing_empty(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "D110")
    inst = p["occupations"][-1]
    fishing = next(sp for sp in s["action_spaces"] if sp["id"] == "fishing")
    fishing["supply"] = {"food": 0}
    ctx = {"log": [], "actor": first, "extra": {}, "space_id": "forest",
          "goods": {"wood": 3}}
    cards.CARDS["D110"]["hooks"]["space_used"](s, p, inst, ctx)
    assert ctx["extra"].get("food") is None


def test_clay_seller_once_per_round(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "D122")
    inst = p["occupations"][-1]
    give(s, first, food=4)
    food_before = p["resources"]["food"]
    pid = p["player_id"]
    assert deck_d._clay_seller_available(s, p, inst)
    s = engine.apply_action(s, pid, {
        "kind": "card_action", "card": "D122"}).new_state
    p = s["players"][first]
    assert p["resources"]["clay"] == 2
    assert p["resources"]["food"] == food_before - 2
    assert not deck_d._clay_seller_available(s, p, p["occupations"][-1])
    # A new round resets the once-per-round flag.
    ctx = {"log": [], "actor": first, "extra": {}}
    cards.CARDS["D122"]["hooks"]["round_start"](s, p, p["occupations"][-1], ctx)
    assert deck_d._clay_seller_available(s, p, p["occupations"][-1])


def test_renovation_preparer_grants_clay_per_wood_room(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "D123")
    give(s, first, wood=10, reed=4)
    s = place(engine, s, {"kind": "place", "space": "farm_expansion",
                          "rooms": [0, 1]})
    assert s["players"][first]["resources"]["clay"] == 4


def test_emissary_places_distinct_goods_including_animal(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "D124")
    p["pets"]["sheep"] = 1
    give(s, first, wood=1)
    pid = p["player_id"]
    s = engine.apply_action(s, pid, {
        "kind": "card_action", "card": "D124",
        "params": {"good": "sheep"}}).new_state
    p = s["players"][first]
    assert p["resources"]["stone"] == 1
    assert "sheep" not in p["pets"]
    s = engine.apply_action(s, pid, {
        "kind": "card_action", "card": "D124",
        "params": {"good": "wood"}}).new_state
    p = s["players"][first]
    assert p["resources"]["stone"] == 2
    assert p["resources"]["wood"] == 0
    # Wood was already placed -- can't place it again.
    give(s, first, wood=1)
    with pytest.raises(ValueError):
        engine.apply_action(s, pid, {
            "kind": "card_action", "card": "D124", "params": {"good": "wood"}})


def test_forest_trader_buys_resource_after_wood_space(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "D125")
    give(s, first, food=2)
    food_before = s["players"][first]["resources"]["food"]
    add_space(s, "forest", "Forest", acc=True, supply={"wood": 3})
    s = place(engine, s, {"kind": "place", "space": "forest"})
    pid = s["players"][first]["player_id"]
    acts = engine.get_valid_actions(s, pid)
    choice = next(a for a in acts if a["kind"] == "choice")
    idx = next(i for i, o in enumerate(choice["options"]) if o.startswith("Buy 1 stone"))
    s = place(engine, s, {"kind": "choice", "index": idx})
    p = s["players"][first]
    assert p["resources"]["stone"] == 1
    assert p["resources"]["food"] == food_before - 2  # paid 2 food for stone


def test_beer_tent_operator_conversion_and_bonus(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "D133")
    give(s, first, wood=1, grain=1)
    food_before = p["resources"]["food"]
    s["phase"] = "feeding"
    inst = p["occupations"][-1]
    assert deck_d._beer_tent_available(s, p, inst)
    pid = p["player_id"]
    s = engine.apply_action(s, pid, {
        "kind": "card_action", "card": "D133"}).new_state
    p = s["players"][first]
    assert p["resources"]["food"] == food_before + 2
    assert p["resources"]["wood"] == 0 and p["resources"]["grain"] == 0
    assert cards.score_bonuses(s, p) == 1


def test_oyster_eater_bonus_point_and_skips_placement(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "D134")
    inst = p["occupations"][-1]
    ctx = {"log": [], "actor": first, "extra": {}, "space_id": "fishing"}
    cards.CARDS["D134"]["hooks"]["space_used"](s, p, inst, ctx)
    assert cards.score_bonuses(s, p) == 1
    assert p["people_placed"] == 1


def test_gardening_head_official_wood_tiers_and_veg_score(engine):
    s = make_state(engine, 3)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "D135")
    inst = p["occupations"][-1]
    ctx = {"log": [], "actor": first, "extra": {}}
    cards.CARDS["D135"]["hooks"]["play"](s, p, inst, ctx)
    assert p["resources"]["wood"] == 4  # round 1 -> 13 remaining -> tier 4
    p["cells"][0]["type"] = "field"
    p["cells"][0]["crops"] = {"type": "vegetable", "count": 2}
    assert cards.score_bonuses(s, p) == 2


def test_animal_activist_fenced_stables_score(engine):
    from server.agricola.state import cell_edges
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "D136")
    inst = p["occupations"][-1]
    fences = set(cell_edges(0)) | set(cell_edges(1)) | \
        set(cell_edges(5)) | set(cell_edges(6))
    fences -= {"v-0-2", "v-1-2"}
    p["fences"] = sorted(fences)
    p["cells"][0]["stable"] = True
    assert cards.score_bonuses(s, p) == 2


def test_trade_teacher_buys_two_goods_after_lessons(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "D137")
    give(s, first, food=3)
    food_before = s["players"][first]["resources"]["food"]
    give_card(s, first, "occ_woodcutter")
    s = place(engine, s, {"kind": "place", "space": "lessons",
                          "card": "occ_woodcutter"})
    pid = s["players"][first]["player_id"]
    acts = engine.get_valid_actions(s, pid)
    choice = next(a for a in acts if a["kind"] == "choice")
    idx = choice["options"].index("1 grain (1 food)")
    s = place(engine, s, {"kind": "choice", "index": idx})
    # A second purchase prompt should follow.
    acts = engine.get_valid_actions(s, s["players"][first]["player_id"])
    choice2 = next(a for a in acts if a["kind"] == "choice")
    idx2 = choice2["options"].index("1 stone (1 food)")
    s = place(engine, s, {"kind": "choice", "index": idx2})
    p = s["players"][first]
    assert p["resources"]["grain"] == 1
    assert p["resources"]["stone"] == 1
    assert p["resources"]["food"] == food_before - 2


def test_chairman_grants_food_to_both_on_other_players_use(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    put_in_play(s, other, "D139")
    p_other = s["players"][other]
    inst = p_other["occupations"][-1]
    first_food_before = s["players"][first]["resources"]["food"]
    other_food_before = p_other["resources"]["food"]
    ctx = {"log": [], "actor": first, "extra": {}, "space_id": "meeting_place"}
    cards.CARDS["D139"]["hooks"]["space_used"](s, p_other, inst, ctx)
    assert s["players"][first]["resources"]["food"] == first_food_before + 1
    assert s["players"][other]["resources"]["food"] == other_food_before + 1


def test_loudmouth_bonus_on_four_of_a_kind(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "D140")
    inst = p["occupations"][-1]
    ctx = {"log": [], "actor": first, "extra": {}, "goods": {"wood": 4}}
    cards.CARDS["D140"]["hooks"]["space_used"](s, p, inst, ctx)
    assert ctx["extra"].get("food") == 1
    ctx2 = {"log": [], "actor": first, "extra": {}, "goods": {"wood": 3}}
    cards.CARDS["D140"]["hooks"]["space_used"](s, p, inst, ctx2)
    assert ctx2["extra"].get("food") is None


def test_tree_cutter_bonus_wood_on_triple(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "D143")
    inst = p["occupations"][-1]
    ctx = {"log": [], "actor": first, "extra": {}, "goods": {"food": 3}}
    cards.CARDS["D143"]["hooks"]["space_used"](s, p, inst, ctx)
    assert ctx["extra"].get("wood") == 1
    ctx2 = {"log": [], "actor": first, "extra": {}, "goods": {"wood": 3}}
    cards.CARDS["D143"]["hooks"]["space_used"](s, p, inst, ctx2)
    assert ctx2["extra"].get("wood") is None  # excludes wood itself


def test_roof_examiner_reed_scales_with_improvements(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["improvements"] = ["fireplace_2", "well", "joinery"]
    put_in_play(s, first, "D145")
    inst = p["occupations"][-1]
    ctx = {"log": [], "actor": first, "extra": {}}
    cards.CARDS["D145"]["hooks"]["play"](s, p, inst, ctx)
    assert p["resources"]["reed"] == 4  # 3 improvements -> tier 4


def test_casual_worker_offers_food_or_free_stable(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    put_in_play(s, first, "D149")
    add_space(s, "western_quarry", "Western Quarry", acc=True, supply={"stone": 1})
    s["current_player"] = other
    pid_other = s["players"][other]["player_id"]
    s = engine.apply_action(s, pid_other, {
        "kind": "place", "space": "western_quarry"}).new_state
    pid = s["players"][first]["player_id"]
    acts = engine.get_valid_actions(s, pid)
    choice = next(a for a in acts if a["kind"] == "choice")
    idx = choice["options"].index("Build a free stable")
    s = place(engine, s, {"kind": "choice", "index": idx})
    acts = engine.get_valid_actions(s, s["players"][first]["player_id"])
    choice2 = next(a for a in acts if a["kind"] == "choice")
    s = place(engine, s, {"kind": "choice", "index": 0})
    assert sum(1 for c in s["players"][first]["cells"] if c["stable"]) == 1


def test_wealthy_man_scores_bonus_per_qualifying_harvest(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "D153")
    p["cells"][0]["type"] = "field"
    p["cells"][0]["crops"] = {"type": "grain", "count": 3}
    inst = p["occupations"][-1]
    ctx = {"log": [], "actor": first, "extra": {}, "harvest_index": 1}
    cards.CARDS["D153"]["hooks"]["harvest_field"](s, p, inst, ctx)
    assert cards.score_bonuses(s, p) == 1


def test_chimney_sweep_renovation_discount_and_score(engine):
    s = make_state(engine, 3)
    first = s["current_player"]
    p = s["players"][first]
    p["house_type"] = "clay"
    put_in_play(s, first, "D154")
    cost = cards.modified_cost(s, p, "renovation", {"stone": 2, "reed": 1})
    assert cost.get("stone", 0) == 0
    s["players"][(first + 1) % 3]["house_type"] = "stone"
    s["players"][(first + 2) % 3]["house_type"] = "stone"
    assert cards.score_bonuses(s, p) == 2


def test_ebonist_once_per_harvest(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "D155")
    give(s, first, wood=2)
    food_before = p["resources"]["food"]
    s["harvest_index"] = 1
    pid = p["player_id"]
    s = engine.apply_action(s, pid, {
        "kind": "card_action", "card": "D155"}).new_state
    p = s["players"][first]
    assert p["resources"]["food"] == food_before + 1
    assert p["resources"]["grain"] == 1
    assert p["resources"]["wood"] == 1
    inst = p["occupations"][-1]
    assert not deck_d._ebonist_available(s, p, inst)  # already used this harvest
    s["harvest_index"] = 2
    assert deck_d._ebonist_available(s, p, inst)


def test_retail_dealer_stash_drains_on_resource_market(engine):
    s = make_state(engine, 3)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "D156")
    inst = p["occupations"][-1]
    deck_d._retail_dealer_play(s, p, inst, {})
    assert inst["data"] == {"grain": 3, "food": 3}
    ctx = {"log": [], "actor": first, "extra": {},
          "space_id": "resource_market_3p"}
    cards.CARDS["D156"]["hooks"]["space_used"](s, p, inst, ctx)
    assert ctx["extra"] == {"grain": 1, "food": 1}
    assert inst["data"] == {"grain": 2, "food": 2}


def test_party_organizer_food_and_score(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    put_in_play(s, first, "D157")
    p = s["players"][first]
    inst = p["occupations"][-1]
    food_before = p["resources"]["food"]
    s["players"][other]["people_total"] = 5
    ctx = {"log": [], "actor": other, "extra": {}}
    cards.CARDS["D157"]["hooks"]["family_growth"](s, p, inst, ctx)
    assert p["resources"]["food"] == food_before + 8
    # Doesn't retrigger.
    ctx2 = {"log": [], "actor": other, "extra": {}}
    cards.CARDS["D157"]["hooks"]["family_growth"](s, p, inst, ctx2)
    assert p["resources"]["food"] == food_before + 8
    p["people_total"] = 5
    assert cards.score_bonuses(s, p) == 0  # other also has 5


def test_midwife_grain_on_others_first_placement(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    put_in_play(s, first, "D160")
    p = s["players"][first]
    inst = p["occupations"][-1]
    s["players"][other]["people_placed"] = 1
    ctx = {"log": [], "actor": other, "extra": {}}
    cards.CARDS["D160"]["hooks"]["family_growth"](s, p, inst, ctx)
    assert p["resources"]["grain"] == 1


def test_stable_milker_two_stables_grants_cattle(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "D166")
    give(s, first, wood=4)
    s = place(engine, s, {"kind": "place", "space": "farm_expansion",
                          "stables": [0, 1]})
    assert s["prompts"][0]["gained"] == {"cattle": 1}
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {
        "kind": "accommodate", "pets": {"cattle": 1}}).new_state
    assert s["players"][first]["pets"]["cattle"] == 1


def test_field_cultivator_pile_sequence_over_two_tiles(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "D126")
    p["cells"][0]["type"] = "field"
    p["cells"][0]["crops"] = {"type": "grain", "count": 1}
    p["cells"][1]["type"] = "field"
    p["cells"][1]["crops"] = {"type": "vegetable", "count": 1}
    wood_before = p["resources"]["wood"]
    log = []
    engine._start_harvest(s, log)
    p = s["players"][first]
    assert len(s["prompts"]) == 1  # 2 tiles harvested -> chained prompts
    pid = p["player_id"]
    # First tile: take the top good ("wood").
    s = engine.apply_action(s, pid, {"kind": "choice", "index": 0}).new_state
    p = s["players"][first]
    assert p["resources"]["wood"] == wood_before + 1
    # Second prompt is now queued (2nd tile), offering the new top ("clay").
    assert len(s["prompts"]) == 1
    clay_before = p["resources"]["clay"]
    s = engine.apply_action(s, pid, {"kind": "choice", "index": 1}).new_state
    p = s["players"][first]
    assert p["resources"]["clay"] == clay_before  # declined
    inst = next(i for i in p["occupations"] if i["id"] == "D126")
    assert inst["data"]["fc_idx"] == 1  # only the accepted pick advanced the pile
    assert inst["data"]["fc_remaining"] == 0
    assert s["prompts"] == []


def test_millwright_grain_gain_and_payment_substitution(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    give_card(s, first, "D088")
    grain_before = p["resources"]["grain"]
    add_space(s, "lessons", "Lessons")
    s = place(engine, s, {"kind": "place", "space": "lessons", "card": "D088"})
    p = s["players"][first]
    assert p["resources"]["grain"] == grain_before + 1

    # Up to 2 building resources (any type) swapped for 1 grain each.
    cost = cards.modified_cost(
        s, p, "room", {"wood": 5, "reed": 2},
        {"count": 1, "payment": {"millwright_grain": {"wood": 1, "reed": 1}}})
    assert cost == {"wood": 4, "reed": 1, "grain": 2}

    # More than 2 total substitutions, or more than the cost actually
    # has of that resource, raises instead of being silently clamped.
    with pytest.raises(ValueError):
        cards.modified_cost(
            s, p, "room", {"wood": 5, "reed": 2},
            {"count": 1, "payment": {"millwright_grain": {"wood": 3}}})


def test_site_manager_on_play_build_with_food_substitution(engine):
    """D095: on-play mandatory major-improvement build, with up to 1
    resource of each type swappable for 1 food each -- scoped to just
    this one build (not a standing cost_mod for later purchases)."""
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    give_card(s, first, "D095")
    # Joinery costs 2 wood + 2 stone; give 1 short of wood, covered by
    # substituting 1 food for it. (Playing an occupation via Lessons is
    # free for a player's first occupation, so no extra food is needed
    # for the card's own play cost.)
    give(s, first, wood=1, stone=2, food=1)
    food_before = p["resources"]["food"]
    add_space(s, "lessons", "Lessons")
    s = place(engine, s, {"kind": "place", "space": "lessons", "card": "D095",
                          "params": {"improvement": "joinery",
                                    "food_sub": {"wood": 1}}})
    p = s["players"][first]
    assert "joinery" in p["improvements"]
    assert p["resources"]["wood"] == 0
    assert p["resources"]["stone"] == 0
    assert p["resources"]["food"] == food_before - 1  # 1 food for the swap

    # A later, normal Major Improvement purchase gets no such
    # substitution -- the ability doesn't outlive the on-play build.
    cost = cards.modified_cost(s, p, "improvement", {"clay": 2, "stone": 2},
                               {"improvement": "pottery"})
    assert cost == {"clay": 2, "stone": 2}

    # Nothing affordable at all (even with the best possible
    # substitution): the mandatory build is skipped gracefully rather
    # than blocking the whole card play.
    s2 = make_state(engine, 2)
    first2 = s2["current_player"]
    give_card(s2, first2, "D095")
    add_space(s2, "lessons", "Lessons")
    s2 = place(engine, s2, {"kind": "place", "space": "lessons", "card": "D095"})
    p2 = s2["players"][first2]
    assert p2["improvements"] == []

    # Something IS affordable, but naming an unaffordable improvement
    # raises rather than silently doing nothing.
    s3 = make_state(engine, 2)
    first3 = s3["current_player"]
    give_card(s3, first3, "D095")
    give(s3, first3, clay=2)  # fireplace_2 is affordable outright
    add_space(s3, "lessons", "Lessons")
    with pytest.raises(ValueError):
        place(engine, s3, {"kind": "place", "space": "lessons", "card": "D095",
                          "params": {"improvement": "well"}})
