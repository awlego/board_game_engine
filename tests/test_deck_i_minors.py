"""Tests for the deck I minor-improvement module
(server/agricola/decks/deck_i_minors.py)."""

import pytest

from server.agricola.engine import AgricolaEngine
from server.agricola import cards
from server.agricola.decks import deck_i_minors
from server.agricola.state import plowable_cells

from test_agricola import (
    make_state, give, give_card, put_in_play, add_space, place,
)


@pytest.fixture
def engine():
    return AgricolaEngine()


# ── Registration completeness ────────────────────────────────────────

def test_registration_completeness():
    db_codes = {c["code"] for c in cards.compendium().values()
               if c["deck"] == "I" and c["type"] == "minor"}
    registered = {cid for cid in cards.CARDS if cid in db_codes}
    unimplemented = set(deck_i_minors.UNIMPLEMENTED)
    assert unimplemented <= db_codes
    assert registered & unimplemented == set()
    assert registered | unimplemented == db_codes


# ── Smoke test: every implemented card gets played once ──────────────

_NEEDS_OCC = {"I63": 1, "I67": 2, "I72": 3, "I74": 2, "I79": 3, "I80": 3,
             "I90": 2, "I102": 2, "I337": 3}
_DUMMY_OCCS = ["occ_woodcutter", "occ_reed_collector", "occ_clay_digger"]


def _grow_field(p, cell, crop, count):
    p["cells"][cell]["type"] = "field"
    p["cells"][cell]["crops"] = {"type": crop, "count": count}


def _prep_prereqs(state, pidx, cid):
    p = state["players"][pidx]
    n = _NEEDS_OCC.get(cid)
    if n:
        for i in range(n):
            put_in_play(state, pidx, _DUMMY_OCCS[i])
    if cid == "I69":  # 2 vegetable fields
        _grow_field(p, 3, "vegetable", 1)
        _grow_field(p, 4, "vegetable", 1)
    if cid == "I92":  # 2 animals
        p["pets"] = {"sheep": 2}
    if cid == "I99":  # 3 grain fields
        _grow_field(p, 3, "grain", 1)
        _grow_field(p, 4, "grain", 1)
        _grow_field(p, 5, "grain", 1)
    if cid == "I101":  # 4 planted fields (any crop)
        for i, cell in enumerate((3, 4, 5, 6)):
            _grow_field(p, cell, "grain" if i % 2 else "vegetable", 1)
    if cid == "I65":  # an oven built
        p["improvements"].append("clay_oven")
        state["available_improvements"].remove("clay_oven")
    if cid == "I66":  # the Well built
        p["improvements"].append("well")
        state["available_improvements"].remove("well")
    if cid == "I85":  # a Cooking Hearth built
        p["improvements"].append("cooking_hearth_4")
        state["available_improvements"].remove("cooking_hearth_4")


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


_DB_I_MINOR_CODES = {c["code"] for c in cards.compendium().values()
                    if c["deck"] == "I" and c["type"] == "minor"}
ALL_I_MINORS = sorted(cid for cid in cards.CARDS if cid in _DB_I_MINOR_CODES)


@pytest.mark.parametrize("cid", ALL_I_MINORS)
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
    assert cid not in p["hand_minors"]
    # Traveling cards leave `minors` (passed on); everything else stays.
    if not cards.CARDS[cid]["traveling"]:
        assert any(i["id"] == cid for i in p["minors"])


# ── Targeted effect tests ────────────────────────────────────────────

def test_moldboard_plough_twice_per_game(engine):
    # Exercised at the hook level (not through a full multi-round game) --
    # farmland is single-use per round, so triggering it 3 times through
    # the real engine would require simulating 3 separate rounds.
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    inst = put_in_play(s, first, "I63")
    deck_i_minors._moldboard_play(s, p, inst, {})
    assert inst["data"]["uses_left"] == 2

    for expected_after in (1, 0):
        s["prompts"] = []
        ctx = {"actor": first, "space_id": "farmland", "log": [], "extra": {}}
        deck_i_minors._moldboard_space_used(s, p, inst, ctx)
        assert s["prompts"] and s["prompts"][-1]["card"] == "I63"
        prompt = s["prompts"].pop()
        cell = prompt["data"]["cells"][0]
        resolve_ctx = {"index": 1, "data": prompt["data"], "log": []}
        deck_i_minors._moldboard_resolve(s, p, inst, resolve_ctx)
        assert p["cells"][cell]["type"] == "field"
        assert inst["data"]["uses_left"] == expected_after

    # A third use offers no additional-plough choice.
    s["prompts"] = []
    ctx = {"actor": first, "space_id": "farmland", "log": [], "extra": {}}
    deck_i_minors._moldboard_space_used(s, p, inst, ctx)
    assert not s["prompts"]


def test_alms_grants_food_per_completed_round_and_travels(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    s["round"] = 4
    give_card(s, first, "I64")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "I64"}})
    p = s["players"][first]
    # The starting player begins with 2 food; +3 for 3 completed rounds.
    assert p["resources"]["food"] == 2 + 3
    assert "I64" in s["players"][other]["hand_minors"]
    assert not any(i["id"] == "I64" for i in p["minors"])


def test_bakers_kitchen_returns_oven_and_bakes_immediately(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["improvements"].append("clay_oven")
    s["available_improvements"].remove("clay_oven")
    p["resources"]["food"] = 0
    give(s, first, stone=2, grain=2)
    give_card(s, first, "I65")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "I65", "params": {"bake_grain": 2}}})
    p = s["players"][first]
    assert "clay_oven" not in p["improvements"]
    assert "clay_oven" in s["available_improvements"]
    assert p["resources"]["grain"] == 0
    assert p["resources"]["food"] == 10


def test_village_well_returns_well_and_schedules_food(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["improvements"].append("well")
    s["available_improvements"].remove("well")
    give_card(s, first, "I66")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "I66"}})
    p = s["players"][first]
    assert "well" not in p["improvements"]
    assert "well" in s["available_improvements"]
    rnd = s["round"]
    for r in range(rnd + 1, rnd + 4):
        assert s["round_goods"][str(r)][str(first)]["food"] == 1


def test_threshing_board_bake_on_spaces(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "I67")
    p = s["players"][first]
    p["improvements"].append("clay_oven")
    give(s, first, grain=2)
    cell = plowable_cells(p)[0]
    s = place(engine, s, {"kind": "place", "space": "farmland",
                          "cell": cell, "bake": {"clay_oven": 1}})
    assert s["players"][first]["resources"]["grain"] == 1  # baked 1


def test_strawberry_patch_schedules_food(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    _grow_field(p, 3, "vegetable", 1)
    _grow_field(p, 4, "vegetable", 1)
    give_card(s, first, "I69")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "I69"}})
    rnd = s["round"]
    for r in range(rnd + 1, rnd + 4):
        assert s["round_goods"][str(r)][str(first)]["food"] == 1


def test_grain_cart_bonus(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "occ_woodcutter")
    put_in_play(s, first, "occ_reed_collector")
    put_in_play(s, first, "I74")
    s = place(engine, s, {"kind": "place", "space": "grain_seeds"})
    assert s["players"][first]["resources"]["grain"] == 3  # 1 base + 2 bonus


def test_hand_mill_raw_values(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "I75")
    assert cards.raw_values(p)["grain"] == 2


def test_rake_score_threshold_needs_6_fields_with_a_plough(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    for c in range(5):
        p["cells"][c]["type"] = "field"
    put_in_play(s, first, "I76")
    inst = p["minors"][-1]
    assert deck_i_minors._rake_score(s, p, inst) == 2  # 5 fields, no plough
    put_in_play(s, first, "I63")
    assert deck_i_minors._rake_score(s, p, inst) == 0  # now needs 6


def test_shepherds_crook_i77_bonus_sheep(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    inst = put_in_play(s, first, "I77")
    ctx = {"log": [], "actor": first, "extra": {},
          "new_pastures": [[0, 1, 2, 3]]}
    cards.CARDS["I77"]["hooks"]["fences_built"](s, p, inst, ctx)
    assert ctx["extra"] == {"sheep": 2}


def test_wood_cart_take_bonus(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "I79")
    s = place(engine, s, {"kind": "place", "space": "forest"})
    assert s["players"][first]["resources"]["wood"] == 5  # 3 base + 2 bonus


def test_spinney_transfers_wood_from_other_player(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    put_in_play(s, other, "occ_woodcutter")
    put_in_play(s, other, "occ_reed_collector")
    put_in_play(s, other, "occ_clay_digger")
    put_in_play(s, other, "I80")
    s = place(engine, s, {"kind": "place", "space": "forest"})
    assert s["players"][first]["resources"]["wood"] == 2  # 3 base - 1 given
    assert s["players"][other]["resources"]["wood"] == 1


def test_wooden_hut_extension_builds_free_room(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    give(s, first, reed=1, wood=5)
    give_card(s, first, "I81")
    rooms_before = sum(1 for c in p["cells"] if c["type"] == "room")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "I81"}})
    other = (first + 1) % 2
    rooms_after = sum(1 for c in s["players"][first]["cells"]
                      if c["type"] == "room")
    assert rooms_after == rooms_before + 1
    assert "I81" in s["players"][other]["hand_minors"]


def test_street_bonus_awards_highest_tier_only(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    put_in_play(s, first, "I83")   # Wooden Path, tier 1
    put_in_play(s, other, "I89")  # Clay Path, tier 2
    p_first = s["players"][first]
    p_other = s["players"][other]
    inst_first = next(i for i in p_first["minors"] if i["id"] == "I83")
    inst_other = next(i for i in p_other["minors"] if i["id"] == "I89")
    assert cards.score_bonuses(s, p_first) == 0
    assert deck_i_minors._street_score(s, p_other, inst_other) == 2
    assert deck_i_minors._street_score(s, p_first, inst_first) == 0


def test_chicken_coop_schedules_food_x8(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give(s, first, wood=2, reed=1)
    give_card(s, first, "I84")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "I84"}})
    rnd = s["round"]
    for r in range(rnd + 1, min(14, rnd + 8) + 1):
        assert s["round_goods"][str(r)][str(first)]["food"] == 1


def test_cooking_corner_returns_hearth_and_provides_cook_bake(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["improvements"].append("cooking_hearth_4")
    s["available_improvements"].remove("cooking_hearth_4")
    give_card(s, first, "I85")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "I85"}})
    p = s["players"][first]
    assert "cooking_hearth_4" not in p["improvements"]
    assert "cooking_hearth_4" in s["available_improvements"]
    inst = next(i for i in p["minors"] if i["id"] == "I85")
    assert cards.CARDS["I85"]["cook"]["cattle"] == 4
    assert cards.CARDS["I85"]["bake"] == (None, 3)


def test_corn_storehouse_resows_empty_fields(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["cells"][3]["type"] = "field"  # empty field (no crops)
    inst = put_in_play(s, first, "I86")
    ctx = {"log": [], "actor": first, "extra": {}, "harvest_index": 1}
    cards.CARDS["I86"]["hooks"]["harvest_field"](s, p, inst, ctx)
    assert s["prompts"] and s["prompts"][0]["card"] == "I86"
    pid = p["player_id"]
    s = place(engine, s, {"kind": "choice", "index": 0})
    assert s["players"][first]["cells"][3]["crops"] == {"type": "grain",
                                                        "count": 2}


def test_flagon_distributes_food_when_well_built(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    put_in_play(s, first, "I87")
    give(s, other, wood=1, stone=3)
    add_space(s, "major_improvement", "Major Improvement")
    s["players"][other]["resources"]["food"] = 0
    s["players"][first]["resources"]["food"] = 0
    s = place(engine, s, {"kind": "place", "space": "day_laborer"})  # first's turn
    s = place(engine, s, {"kind": "place", "space": "major_improvement",
                          "improvement": "well"})
    # first (Flagon owner) got 2 food from Day Laborer, then +4 as owner;
    # other (the builder) gets +1 as a non-owner.
    assert s["players"][first]["resources"]["food"] == 2 + 4
    assert s["players"][other]["resources"]["food"] == 1


def test_planter_box_adds_bonus_on_sow(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "I90")
    p["cells"][6]["type"] = "field"  # orthogonally adjacent to room cell 5
    give(s, first, grain=1)
    add_space(s, "grain_utilization", "Sow and/or Bake Bread")
    s = place(engine, s, {"kind": "place", "space": "grain_utilization",
                          "sow": [{"cell": 6, "crop": "grain"}]})
    assert s["players"][first]["cells"][6]["crops"]["count"] == 3 + 2


def test_ladder_reduces_room_reed_cost(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "I91")
    cost = cards.modified_cost(s, p, "room", {"wood": 5, "reed": 2})
    assert cost["reed"] == 1


def test_manure_auto_harvests_at_non_harvest_round_end(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["pets"] = {"sheep": 2}
    p["cells"][3]["type"] = "field"
    p["cells"][3]["crops"] = {"type": "grain", "count": 3}
    inst = put_in_play(s, first, "I92")
    ctx = {"round": 2, "log": [], "actor": first, "extra": {}}
    cards.CARDS["I92"]["hooks"]["round_start"](s, p, inst, ctx)
    assert p["resources"]["grain"] == 1
    assert p["cells"][3]["crops"]["count"] == 2


def test_manure_skips_after_a_harvest_round(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["pets"] = {"sheep": 2}
    p["cells"][3]["type"] = "field"
    p["cells"][3]["crops"] = {"type": "grain", "count": 3}
    inst = put_in_play(s, first, "I92")
    ctx = {"round": 5, "log": [], "actor": first, "extra": {}}  # round 4 was a harvest
    cards.CARDS["I92"]["hooks"]["round_start"](s, p, inst, ctx)
    assert p["resources"]["grain"] == 0
    assert p["cells"][3]["crops"]["count"] == 3


def test_milking_shed_counts_global_animals(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    p = s["players"][first]
    p["resources"]["food"] = 0
    p["pets"] = {"sheep": 3}
    s["players"][other]["pets"] = {"sheep": 2, "cattle": 3}
    inst = put_in_play(s, first, "I93")
    ctx = {"harvest_index": 1, "log": [], "actor": first, "extra": {}}
    cards.CARDS["I93"]["hooks"]["harvest_field"](s, p, inst, ctx)
    assert p["resources"]["food"] == 1 + 1  # 5 sheep // 5 = 1, 3 cattle // 3 = 1


def test_fish_trap_triggers_on_fishing_and_reed(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["resources"]["food"] = 0
    put_in_play(s, first, "I95")
    s = place(engine, s, {"kind": "place", "space": "fishing"})
    assert s["players"][first]["resources"]["food"] == 1 + 1  # 1 from fishing +1
    s = place(engine, s, {"kind": "place", "space": "day_laborer"})  # other
    s = place(engine, s, {"kind": "place", "space": "reed_bank"})
    assert s["players"][first]["resources"]["food"] == 2 + 1


def test_reed_exchange_on_play_and_travels(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    give(s, first, wood=2)
    give_card(s, first, "I96")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "I96"}})
    assert s["players"][first]["resources"]["reed"] == 2
    assert "I96" in s["players"][other]["hand_minors"]


def test_schnaps_distillery_conversion_and_score(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["resources"]["food"] = 0
    put_in_play(s, first, "I98")
    give(s, first, vegetable=6)
    inst = p["minors"][-1]
    assert deck_i_minors._schnaps_score(s, p, inst) == 2  # 6 vegetables
    match = next(c for key, c, _inst in cards.conversion_options(p)
                if key.startswith("I98"))
    assert match == {"give": {"vegetable": 1}, "get": {"food": 4},
                     "per_harvest": 1}


def test_straw_thatched_roof_removes_reed_cost(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "I99")
    cost = cards.modified_cost(s, p, "renovation", {"clay": 2, "reed": 1})
    assert cost.get("reed", 0) == 0


def test_animal_feed_score_bonus(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["pets"] = {"sheep": 3}
    inst = put_in_play(s, first, "I101")
    from server.agricola.state import table_score
    expected = table_score("sheep", 4) - table_score("sheep", 3)
    assert deck_i_minors._animal_feed_score(s, p, inst) == expected


def test_weekly_market_on_play_and_travels(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    give(s, first, grain=3)
    give_card(s, first, "I104")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "I104"}})
    assert s["players"][first]["resources"]["vegetable"] == 2
    assert "I104" in s["players"][other]["hand_minors"]


def test_wildlife_reserve_holds_1_sheep_1_boar_1_cattle(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    for i in range(2):
        put_in_play(s, first, ["occ_woodcutter", "occ_reed_collector"][i])
    inst = put_in_play(s, first, "I102")
    assert len(p["occupations"]) == 2

    inst["held"] = {"sheep": 1, "boar": 1, "cattle": 1}
    ok, err = cards.validate_held(s, p)
    assert ok, err

    inst["held"] = {"sheep": 2}
    ok, err = cards.validate_held(s, p)
    assert not ok

    inst["held"] = {"boar": 2}
    ok, err = cards.validate_held(s, p)
    assert not ok


def test_tavern_no_toll_for_non_owner_and_owner_choice(engine):
    """I100 card_space: no toll ("you yourself do not receive anything
    from it" when another player uses it) -- a non-owner placer just
    gets 3 food. The owner's own placement is instead a choice between
    3 food or 2 bonus points."""
    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    give(s, first, wood=2, stone=2)
    give_card(s, first, "I100")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "I100"}})
    owner_food = s["players"][first]["resources"]["food"]
    other_food = s["players"][other]["resources"]["food"]
    other_pid = s["players"][other]["player_id"]

    s = engine.apply_action(
        s, other_pid, {"kind": "place", "space": "card:I100"}).new_state
    assert s["players"][other]["resources"]["food"] == other_food + 3
    assert s["players"][first]["resources"]["food"] == owner_food  # untouched

    # The owner's own use is a choice, not an automatic 3 food.
    space = next(sp for sp in s["action_spaces"] if sp["id"] == "card:I100")
    space["occupied_by"] = None
    space["extra_occupants"] = []
    s["current_player"] = first
    first_pid = s["players"][first]["player_id"]
    s = engine.apply_action(
        s, first_pid, {"kind": "place", "space": "card:I100"}).new_state
    assert s["prompts"][0]["type"] == "choice"
    assert s["prompts"][0]["options"] == ["3 food", "2 bonus points"]
    s = engine.apply_action(s, first_pid, {"kind": "choice", "index": 1}).new_state
    inst = next(i for i in s["players"][first]["minors"] if i["id"] == "I100")
    assert inst["data"]["bonus"] == 2
    assert cards.CARDS["I100"]["score_bonus"](s, s["players"][first], inst) == 2


def test_clay_deposit_toll_and_owner_choice(engine):
    """I337 card_space: a non-owner placer pays the owner 1 food and
    receives 5 clay; the owner's own placement is a no-toll choice
    between the 5 clay or 2 bonus points instead."""
    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    for i in range(3):
        put_in_play(s, first, ["occ_woodcutter", "occ_reed_collector",
                              "occ_clay_digger"][i])
    give_card(s, first, "I337")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "I337"}})
    give(s, other, food=1)
    other_food = s["players"][other]["resources"]["food"]
    owner_food = s["players"][first]["resources"]["food"]
    other_pid = s["players"][other]["player_id"]

    s = engine.apply_action(
        s, other_pid, {"kind": "place", "space": "card:I337"}).new_state
    assert s["players"][other]["resources"]["clay"] == 5
    assert s["players"][other]["resources"]["food"] == other_food - 1
    assert s["players"][first]["resources"]["food"] == owner_food + 1

    space = next(sp for sp in s["action_spaces"] if sp["id"] == "card:I337")
    space["occupied_by"] = None
    space["extra_occupants"] = []
    s["current_player"] = first
    first_pid = s["players"][first]["player_id"]
    owner_food = s["players"][first]["resources"]["food"]
    s = engine.apply_action(
        s, first_pid, {"kind": "place", "space": "card:I337"}).new_state
    assert s["prompts"][0]["type"] == "choice"
    assert s["prompts"][0]["options"] == ["5 clay", "2 bonus points"]
    s = engine.apply_action(s, first_pid, {"kind": "choice", "index": 1}).new_state
    assert s["players"][first]["resources"]["food"] == owner_food  # no toll
    inst = next(i for i in s["players"][first]["minors"] if i["id"] == "I337")
    assert inst["data"]["bonus"] == 2


def test_holiday_house_blocks_round_14_placement(engine):
    """I71 Holiday House, the real card: placement_blocked (decks/
    GUIDE.md's "Placement lockout" section; test_placement_lockout_
    forfeits_with_sensible_log in tests/test_agricola.py is the
    temp_card version of this same flow, using round 13's transition
    into round 14 -- round 13 is itself a harvest round, so this test
    sets round 14 directly rather than re-simulating that harvest).
    Feeding/scoring are unaffected -- only round 14's placements are
    blocked, and only for the owner."""
    s = make_state(engine, 2)
    put_in_play(s, 0, "I71")
    other = 1
    s["round"] = 14
    s["phase"] = "work"

    assert engine._placement_actions(s, 0) == []
    assert engine._skip_actions(s, 0) == []
    assert engine._placement_actions(s, other) != []
    assert engine._food_needed(s, s["players"][0]) == \
        engine._food_needed(s, s["players"][other])

    # The forfeit branch (exercised at the temp_card level in
    # tests/test_agricola.py) picks a blocked player up with a lockout-
    # specific log line, not the generic "no usable space" one.
    s["players"][0]["people_placed"] = 0
    log = []
    engine._advance_work(s, log, start_pidx=0)
    assert s["players"][0]["people_placed"] == s["players"][0]["people_total"]
    assert any("cannot place any people this round and forfeits" in line
              for line in log)


def test_holiday_house_cannot_be_played_after_round_13(engine):
    # "Play this card at the latest during round 13."
    s = make_state(engine, 2)
    s["round"] = 14
    give_card(s, 0, "I71")
    give(s, 0, wood=3, reed=2)
    with pytest.raises(ValueError):
        place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "I71"}})


def test_punner_plows_a_field_after_another_players_plow(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "I70")
    s = place(engine, s, {"kind": "place", "space": "meeting_place"})  # first
    s = place(engine, s, {"kind": "place", "space": "farmland", "cell": 0})  # other
    prompt = s["prompts"][0]
    assert prompt["card"] == "I70" and prompt["player"] == first
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {"kind": "choice", "index": 1}).new_state
    cell = prompt["data"]["cells"][0]
    assert s["players"][first]["cells"][cell]["type"] == "field"


def test_punner_can_decline(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "I70")
    s = place(engine, s, {"kind": "place", "space": "meeting_place"})  # first
    s = place(engine, s, {"kind": "place", "space": "farmland", "cell": 0})  # other
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {"kind": "choice", "index": 0}).new_state
    assert all(c["type"] != "field" for c in s["players"][first]["cells"])


def test_punner_ignores_own_plow(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "I70")
    s = place(engine, s, {"kind": "place", "space": "farmland", "cell": 0})  # first's own plow
    assert not s["prompts"]
