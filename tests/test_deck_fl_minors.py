"""Tests for the deck FL minor-improvement module
(server/agricola/decks/deck_fl_minors.py)."""

import pytest

from server.agricola.engine import AgricolaEngine
from server.agricola import cards, sub_actions
from server.agricola.decks import deck_fl_minors

from test_agricola import (
    make_state, give, give_card, put_in_play, add_space, place,
)


@pytest.fixture
def engine():
    return AgricolaEngine()


# ── Registration completeness ────────────────────────────────────────

def test_registration_completeness():
    db_codes = {c["code"] for c in cards.compendium().values()
               if c["deck"] == "FL" and c["type"] == "minor"}
    registered = {cid for cid in cards.CARDS if cid in db_codes}
    unimplemented = set(deck_fl_minors.UNIMPLEMENTED)
    assert unimplemented <= db_codes
    assert registered & unimplemented == set()
    assert registered | unimplemented == db_codes


# ── Smoke test: every implemented card gets played once ──────────────

_DUMMY_OCCS = ["occ_woodcutter", "occ_reed_collector", "occ_clay_digger",
              "occ_stonecutter"]

_EXTRA_PARAMS = {"FL014": {"wood": 4}}


def _prep_prereqs(state, pidx, cid):
    p = state["players"][pidx]
    n_occ = {"FL006": 2, "FL007": 3, "FL024": 2}.get(cid)
    if n_occ:
        for i in range(n_occ):
            put_in_play(state, pidx, _DUMMY_OCCS[i])
    if cid == "FL003":
        p["pets"]["sheep"] = 1
    if cid == "FL014":
        give(state, pidx, wood=4)
    if cid == "FL016":
        p["improvements"].append("well")
    if cid == "FL020":
        add_space(state, "basic_wish", "Basic Wish for Children")
    if cid == "FL021":
        state["round"] = 12
    if cid == "FL022":
        for idx in (0, 1):
            p["cells"][idx]["type"] = "field"
            p["cells"][idx]["crops"] = {"type": "grain", "count": 1}
    if cid == "FL028":
        give(state, pidx, grain=1, vegetable=1)
        p["pets"]["cattle"] = 1
    if cid == "FL029":
        p["cells"][0]["type"] = "field"
        p["cells"][0]["crops"] = {"type": "grain", "count": 1}


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


_DB_FL_MINOR_CODES = {c["code"] for c in cards.compendium().values()
                      if c["deck"] == "FL" and c["type"] == "minor"}
ALL_FL_MINORS = sorted(cid for cid in cards.CARDS if cid in _DB_FL_MINOR_CODES)


@pytest.mark.parametrize("cid", ALL_FL_MINORS)
def test_smoke_play_every_card(engine, cid):
    s = make_state(engine, 2)
    first = s["current_player"]
    _prep_prereqs(s, first, cid)
    give(s, first, **cards.CARDS[cid]["cost"])
    give_card(s, first, cid)
    minor_action = {"card": cid}
    if cid in _EXTRA_PARAMS:
        minor_action["params"] = _EXTRA_PARAMS[cid]
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": minor_action})
    pid = s["players"][first]["player_id"]
    s = _resolve_all_prompts(engine, s, pid)
    p = s["players"][first]
    if cards.CARDS[cid]["traveling"]:
        assert cid not in p["hand_minors"]
    else:
        assert cid not in p["hand_minors"]
        assert any(i["id"] == cid for i in p["minors"])


# ── Targeted effect tests ────────────────────────────────────────────

def test_belgian_shepherd_holds_two_sheep_and_blocks_house_pets(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    inst = put_in_play(s, first, "FL003")
    assert cards.house_capacity(s, p) == 0
    inst["held"] = {"sheep": 2}
    ok, err = cards.validate_held(s, p)
    assert ok, err
    inst["held"] = {"boar": 1}
    ok, err = cards.validate_held(s, p)
    assert not ok


def test_educational_building_usable_at_3rd_and_4th_placement(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    inst = put_in_play(s, first, "FL004")
    card_space = cards.CARDS["FL004"]["card_space"]
    p["people_placed"] = 0
    assert card_space["usable"](s, p, inst) is False
    p["people_placed"] = 2
    assert card_space["usable"](s, p, inst) is True

    log = []
    card_space["resolve"](s, p, inst, {}, log)
    assert inst["data"]["used_3rd"] is True
    assert card_space["usable"](s, p, inst) is False  # 3rd already used

    rnd = s["round"]
    good = "wood"
    for r in range(rnd + 1, 15):
        assert s["round_goods"][str(r)][str(first)][good] == 1
        good = "stone" if good == "wood" else "wood"

    p["people_placed"] = 3
    assert card_space["usable"](s, p, inst) is True  # 4th still available


def test_brabant_scores_for_two_or_three_zero_animal_categories(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "FL005")
    p["pets"] = {}
    assert cards.score_bonuses(s, p) == 5  # 3 zero categories
    p["pets"] = {"sheep": 1}
    assert cards.score_bonuses(s, p) == 5  # 2 zero categories
    p["pets"] = {"sheep": 1, "boar": 1}
    assert cards.score_bonuses(s, p) == 0  # only 1 zero category


def test_endive_field_is_sowable_and_grants_vegetable_on_play(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "occ_woodcutter")
    put_in_play(s, first, "occ_reed_collector")
    give(s, first, food=2)
    give_card(s, first, "FL006")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "FL006"}})
    p = s["players"][first]
    assert p["resources"]["vegetable"] == 1
    inst = next(i for i in p["minors"] if i["id"] == "FL006")
    give(s, first, vegetable=1)
    sub_actions.sow(s, p, [{"card": "FL006", "crop": "vegetable"}], [])
    assert inst["crops"] == {"type": "vegetable", "count": 2}


def test_diamond_trading_post_withdraw_and_harvest_return(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    inst = put_in_play(s, first, "FL007")
    inst["data"] = {"on_card": 3, "returns": 0}
    give(s, first, stone=5)
    pid = p["player_id"]

    s = engine.apply_action(s, pid, {
        "kind": "card_action", "card": "FL007", "params": {"n": 2}}).new_state
    p = s["players"][first]
    inst = next(i for i in p["minors"] if i["id"] == "FL007")
    assert inst["data"]["on_card"] == 1
    assert p["resources"]["stone"] == 7

    ctx = {"log": [], "actor": first, "extra": {}, "harvest_index": 1}
    cards.CARDS["FL007"]["hooks"]["harvest_start"](s, p, inst, ctx)
    assert s["prompts"][-1]["card"] == "FL007"
    s["prompts"].pop()

    ctx2 = {"index": 0, "option": "Yes", "data": {}, "log": [],
           "actor": first, "extra": {}}
    cards.CARDS["FL007"]["resolve_choice"](s, p, inst, ctx2)
    assert inst["data"]["on_card"] == 2
    assert inst["data"]["returns"] == 1
    assert ctx2["extra"].get("sheep") == 1


def test_jenever_distillery_withdraw_and_harvest_food(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    inst = put_in_play(s, first, "FL014")
    inst["data"]["wood"] = 4
    pid = p["player_id"]

    s = engine.apply_action(s, pid, {
        "kind": "card_action", "card": "FL014", "params": {"n": 1}}).new_state
    p = s["players"][first]
    inst = next(i for i in p["minors"] if i["id"] == "FL014")
    assert inst["data"]["wood"] == 3
    assert p["resources"]["wood"] == 1

    food_before = p["resources"]["food"]
    ctx = {"log": [], "actor": first, "extra": {}, "harvest_index": 1}
    cards.CARDS["FL014"]["hooks"]["harvest_field"](s, p, inst, ctx)
    assert p["resources"]["food"] == food_before + 3
    assert inst["data"]["wood"] == 3  # not removed by harvest


def test_courtyard_garden_scores_unused_spaces_adjacent_to_stone_house(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "FL015")
    p["house_type"] = "stone"
    assert cards.score_bonuses(s, p) == 6  # cells 0, 6, 11: 3 * 2
    p["house_type"] = "wood"
    assert cards.score_bonuses(s, p) == 0


def test_janneken_pis_usable_only_with_well_and_schedules_food(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    inst = put_in_play(s, first, "FL016")
    card_space = cards.CARDS["FL016"]["card_space"]
    assert card_space["usable"](s, p, inst) is False
    p["improvements"].append("well")
    assert card_space["usable"](s, p, inst) is True

    log = []
    card_space["resolve"](s, p, inst, {}, log)
    rnd = s["round"]
    for r in range(rnd + 1, min(14, rnd + 5) + 1):
        assert s["round_goods"][str(r)][str(first)]["food"] == 1


def test_janneken_pis_recognizes_any_card_named_ending_in_well(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    inst = put_in_play(s, first, "FL016")
    cards.card("temp_test_well", "Test Well", "minor", text="")
    try:
        put_in_play(s, first, "temp_test_well")
        assert cards.CARDS["FL016"]["card_space"]["usable"](s, p, inst) is True
    finally:
        cards.CARDS.pop("temp_test_well", None)


def test_bobbin_table_grants_occupied_ok_until_next_harvest(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    inst = put_in_play(s, first, "FL017")
    space = {"id": "day_laborer"}
    assert cards.occupied_ok(s, p, space) is False

    log = []
    cards.CARDS["FL017"]["card_space"]["resolve"](s, p, inst, {}, log)
    assert cards.occupied_ok(s, p, space) is True

    ctx = {"log": [], "actor": first, "extra": {}, "harvest_index": 1}
    cards.CARDS["FL017"]["hooks"]["harvest_start"](s, p, inst, ctx)
    assert cards.occupied_ok(s, p, space) is False


def test_office_three_exchanges(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "FL018")
    give(s, first, clay=1, grain=1)
    p["pets"] = {"boar": 1}
    pid = p["player_id"]

    s = engine.apply_action(s, pid, {
        "kind": "card_action", "card": "FL018",
        "params": {"exchange": "clay_to_stone"}}).new_state
    p = s["players"][first]
    assert p["resources"]["clay"] == 0 and p["resources"]["stone"] == 1

    s = engine.apply_action(s, pid, {
        "kind": "card_action", "card": "FL018",
        "params": {"exchange": "grain_to_vegetable"}}).new_state
    p = s["players"][first]
    assert p["resources"]["grain"] == 0 and p["resources"]["vegetable"] == 1

    s = engine.apply_action(s, pid, {
        "kind": "card_action", "card": "FL018",
        "params": {"exchange": "boar_to_cattle"}}).new_state
    p = s["players"][first]
    assert p["pets"].get("boar", 0) == 0
    # Cattle is an animal good -- it's queued for accommodation, not
    # credited straight into player["pets"] (matches every other
    # animal-granting card in this codebase, e.g. K141 Boar Breeding).
    s = engine.apply_action(s, pid, {
        "kind": "accommodate", "pets": {"cattle": 1}}).new_state
    p = s["players"][first]
    assert p["pets"].get("cattle") == 1


def test_lantern_wonder_negative_points_for_hand(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "FL019")
    p["hand_minors"] = ["FL003", "FL005"]
    p["hand_occupations"] = ["occ_woodcutter"]
    assert cards.score_bonuses(s, p) == -3


def test_love_garden_grants_newborn_when_all_relevant_spaces_occupied(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    inst = put_in_play(s, first, "FL020")
    p["cells"][0]["type"] = "room"
    p["cells"][1]["type"] = "room"  # 4 rooms > 2 people
    add_space(s, "basic_wish", "Basic Wish for Children")
    for sp in s["action_spaces"]:
        if sp["id"] in ("basic_wish", "farm_expansion"):
            sp["occupied_by"] = 0
    ctx = {"spaces": [], "log": [], "actor": first, "extra": {}}
    people_before = p["people_total"]
    cards.CARDS["FL020"]["hooks"]["returning_home"](s, p, inst, ctx)
    p = s["players"][first]
    assert p["people_total"] == people_before + 1


def test_love_garden_no_newborn_when_a_relevant_space_is_free(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    inst = put_in_play(s, first, "FL020")
    p["cells"][0]["type"] = "room"
    p["cells"][1]["type"] = "room"
    add_space(s, "basic_wish", "Basic Wish for Children")  # left unoccupied
    for sp in s["action_spaces"]:
        if sp["id"] == "farm_expansion":
            sp["occupied_by"] = 0
    ctx = {"spaces": [], "log": [], "actor": first, "extra": {}}
    people_before = p["people_total"]
    cards.CARDS["FL020"]["hooks"]["returning_home"](s, p, inst, ctx)
    p = s["players"][first]
    assert p["people_total"] == people_before


def test_lions_mound_scores_when_no_lower_round_space_occupied(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    p = s["players"][first]
    inst = put_in_play(s, first, "FL021")
    s["revealed"] = ["round1_card", "round2_card"]
    s["action_spaces"].append({"id": "round1_card", "name": "R1", "desc": "",
                              "stage": 1, "occupied_by": first,
                              "extra_occupants": [], "supply": {},
                              "accumulates": False})
    s["action_spaces"].append({"id": "round2_card", "name": "R2", "desc": "",
                              "stage": 1, "occupied_by": other,
                              "extra_occupants": [], "supply": {},
                              "accumulates": False})

    ctx = {"spaces": ["round1_card"], "log": [], "actor": first, "extra": {}}
    cards.CARDS["FL021"]["hooks"]["returning_home"](s, p, inst, ctx)
    assert inst["data"]["bp"] == 1

    inst["data"]["bp"] = 0
    # Flip occupancy: now "other" holds the LOWER-numbered space and
    # "first" (this card's owner) holds the higher one -> no bonus.
    for sp in s["action_spaces"]:
        if sp["id"] == "round1_card":
            sp["occupied_by"] = other
        elif sp["id"] == "round2_card":
            sp["occupied_by"] = first
    ctx2 = {"spaces": ["round2_card"], "log": [], "actor": first, "extra": {}}
    cards.CARDS["FL021"]["hooks"]["returning_home"](s, p, inst, ctx2)
    assert inst["data"]["bp"] == 0
    assert cards.score_bonuses(s, p) == 0


def test_corn_maze_grants_wood_reed_for_2x2_grain(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    inst = put_in_play(s, first, "FL022")
    for idx in (2, 3, 7, 8):
        p["cells"][idx]["type"] = "field"
        p["cells"][idx]["crops"] = {"type": "grain", "count": 1}
    ctx = {"round": s["round"], "log": [], "actor": first, "extra": {}}
    cards.CARDS["FL022"]["hooks"]["round_start"](s, p, inst, ctx)
    assert p["resources"]["wood"] == 1
    assert p["resources"]["reed"] == 1


def test_corn_maze_no_bonus_without_full_2x2(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    inst = put_in_play(s, first, "FL022")
    for idx in (2, 3, 7):
        p["cells"][idx]["type"] = "field"
        p["cells"][idx]["crops"] = {"type": "grain", "count": 1}
    ctx = {"round": s["round"], "log": [], "actor": first, "extra": {}}
    cards.CARDS["FL022"]["hooks"]["round_start"](s, p, inst, ctx)
    assert p["resources"]["wood"] == 0
    assert p["resources"]["reed"] == 0


def test_carrot_museum_grants_stone_and_wood_at_trigger_rounds(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    inst = put_in_play(s, first, "FL023")
    p["cells"][0]["type"] = "field"
    p["cells"][0]["crops"] = {"type": "vegetable", "count": 1}
    p["resources"]["vegetable"] = 2
    s["round"] = 9
    ctx = {"round": 9, "log": [], "actor": first, "extra": {}}
    cards.CARDS["FL023"]["hooks"]["round_start"](s, p, inst, ctx)
    assert p["resources"]["stone"] == 1
    assert p["resources"]["wood"] == 2

    p["resources"]["stone"] = 0
    s["round"] = 10
    ctx2 = {"round": 10, "log": [], "actor": first, "extra": {}}
    cards.CARDS["FL023"]["hooks"]["round_start"](s, p, inst, ctx2)
    assert p["resources"]["stone"] == 0  # round 10 is not a trigger round


def test_lovers_tryst_takes_family_growth_instead_of_starting_player(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    p = s["players"][first]
    put_in_play(s, first, "occ_woodcutter")
    put_in_play(s, first, "occ_reed_collector")
    p["cells"][0]["type"] = "room"  # 3 rooms > 2 people
    s["starting_player"] = other
    s["round_first_player"] = other
    give_card(s, first, "FL024")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "FL024"}})
    pid = s["players"][first]["player_id"]
    acts = engine.get_valid_actions(s, pid)
    choice = next(a for a in acts if a["kind"] == "choice")
    assert choice["options"][0].startswith("Yes")
    s = place(engine, s, {"kind": "choice", "index": 0})
    p = s["players"][first]
    assert p["people_total"] == 3
    assert s["starting_player"] == other


def test_lovers_tryst_no_prompt_without_room(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "occ_woodcutter")
    put_in_play(s, first, "occ_reed_collector")
    give_card(s, first, "FL024")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "FL024"}})
    assert s["prompts"] == []
    p = s["players"][first]
    assert any(i["id"] == "FL024" for i in p["minors"])


def test_cockaigne_skip_blocks_round_and_scores_seven(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "FL025")
    give(s, first, food=1, grain=1, vegetable=1)
    p["cells"][0]["animal"] = {"type": "sheep", "count": 1}
    p["cells"][1]["animal"] = {"type": "boar", "count": 1}
    pid = p["player_id"]

    acts = engine.get_valid_actions(s, pid)
    assert any(a["kind"] == "skip" and a["card"] == "FL025" for a in acts)
    s = engine.apply_action(s, pid, {"kind": "skip", "card": "FL025"}).new_state
    p = s["players"][first]
    inst = next(i for i in p["minors"] if i["id"] == "FL025")
    assert inst["data"]["used"] is True
    assert cards.placement_blocked(s, p) is True
    assert cards.score_bonuses(s, p) == 7


def test_speculoos_bakery_grants_guest_token_on_bake(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    inst = put_in_play(s, first, "FL026")
    give(s, first, grain=1)
    ctx = {"grain": 2, "log": [], "actor": first, "extra": {}}
    cards.CARDS["FL026"]["hooks"]["bake"](s, p, inst, ctx)
    assert s["prompts"][-1]["card"] == "FL026"
    s["prompts"].pop()

    ctx2 = {"index": 0, "option": "Yes", "data": {}, "log": [],
           "actor": first, "extra": {}}
    cards.CARDS["FL026"]["resolve_choice"](s, p, inst, ctx2)
    p = s["players"][first]
    assert p["resources"]["grain"] == 0
    assert p["guests"] == 1


def test_hash_with_fries_converts_and_scores(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    give(s, first, grain=1, vegetable=1)
    p["pets"] = {"cattle": 1}
    food_before = p["resources"]["food"]
    give_card(s, first, "FL028")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "FL028"}})
    p = s["players"][first]
    assert p["resources"]["grain"] == 0
    assert p["resources"]["vegetable"] == 0
    assert p["pets"].get("cattle", 0) == 0
    assert p["resources"]["food"] == food_before + 5
    assert cards.score_bonuses(s, p) == 3


def test_bird_trap_extra_harvest_from_chosen_field(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    inst = put_in_play(s, first, "FL029")
    p["cells"][0]["type"] = "field"
    p["cells"][0]["crops"] = {"type": "grain", "count": 2}
    food_before = p["resources"]["food"]
    ctx = {"log": [], "actor": first, "extra": {}, "harvest_index": 1}
    cards.CARDS["FL029"]["hooks"]["harvest_field"](s, p, inst, ctx)
    assert p["resources"]["food"] == food_before + 1
    assert s["prompts"][-1]["card"] == "FL029"
    prompt = s["prompts"].pop()

    ctx2 = {"index": 0, "option": prompt["options"][0], "data": prompt["data"],
           "log": [], "actor": first, "extra": {}}
    grain_before = p["resources"]["grain"]
    cards.CARDS["FL029"]["resolve_choice"](s, p, inst, ctx2)
    p = s["players"][first]
    assert p["resources"]["grain"] == grain_before + 1
    assert p["cells"][0]["crops"]["count"] == 1
