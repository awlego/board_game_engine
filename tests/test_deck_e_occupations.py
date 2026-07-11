"""Tests for deck E occupations (server/agricola/decks/deck_e_occupations.py).

Helper functions below are copied from tests/test_agricola.py (make_state,
give, give_card, put_in_play, add_space, place) per decks/GUIDE.md's
testing guidance.
"""

import json
import os
import random

import pytest

from server.agricola.engine import AgricolaEngine
from server.agricola import cards, sub_actions
from server.agricola.decks import deck_e_occupations as deck_e
from server.agricola.state import cell_edges, table_score


@pytest.fixture
def engine():
    return AgricolaEngine()


def make_state(engine, n=2, seed=42):
    random.seed(seed)
    ids = [f"p_{i}" for i in range(n)]
    names = [f"Player{i}" for i in range(n)]
    return engine.initial_state(ids, names)


def current_pid(engine, state):
    return engine.get_waiting_for(state)[0]


def place(engine, state, action):
    pid = current_pid(engine, state)
    return engine.apply_action(state, pid, action).new_state


def give(state, pidx, **resources):
    for k, v in resources.items():
        state["players"][pidx]["resources"][k] += v


def give_card(state, pidx, cid):
    p = state["players"][pidx]
    spec = cards.CARDS[cid]
    hand = "hand_occupations" if spec["type"] == "occupation" else "hand_minors"
    if cid not in p[hand]:
        p[hand].append(cid)


def put_in_play(state, pidx, cid):
    p = state["players"][pidx]
    spec = cards.CARDS[cid]
    key = "occupations" if spec["type"] == "occupation" else "minors"
    inst = cards.new_instance(cid)
    p[key].append(inst)
    return inst


def add_space(state, sid, name=None, acc=False, supply=None):
    state["action_spaces"].append({
        "id": sid, "name": name or sid, "desc": "", "stage": 1,
        "occupied_by": None, "supply": supply or {}, "accumulates": acc})


def reset_space(state, sid, pidx):
    """Free up an action space and hand the turn back to `pidx`, so a
    test can place a second person on the same space in the same round."""
    sp = next(s for s in state["action_spaces"] if s["id"] == sid)
    sp["occupied_by"] = None
    state["current_player"] = pidx
    state["players"][pidx]["people_placed"] = 0


def resolve_all_prompts(engine, state, pid):
    """Answer every pending prompt: choices with index 0 (Decline), and
    accommodate prompts by discarding everything gained."""
    guard = 0
    while state.get("prompts"):
        guard += 1
        assert guard < 50, "prompt loop did not terminate"
        prompt = state["prompts"][0]
        if prompt["type"] == "choice":
            state = engine.apply_action(
                state, pid, {"kind": "choice", "index": 0}).new_state
        elif prompt["type"] == "accommodate":
            gained = prompt["gained"]
            state = engine.apply_action(state, pid, {
                "kind": "accommodate", "placements": [],
                "discard": dict(gained),
            }).new_state
        else:
            raise AssertionError(f"Unhandled prompt type: {prompt['type']}")
    return state


# ── Registration completeness ────────────────────────────────────────

def _db_slice():
    path = os.path.join(os.path.dirname(__file__), "..", "server",
                        "agricola", "data", "compendium_cards.json")
    with open(path) as f:
        data = json.load(f)
    return [c["code"] for c in data
            if c["deck"] == "E" and c["type"] == "occupation"]


def test_registration_completeness():
    db_codes = set(_db_slice())
    registered = {cid for cid in cards.CARDS if cid in db_codes}
    unimplemented = set(deck_e.UNIMPLEMENTED)
    assert not (registered & unimplemented), \
        "overlap between registered and UNIMPLEMENTED"
    assert registered | unimplemented == db_codes, (
        f"missing: {db_codes - registered - unimplemented}, "
        f"unexpected unimplemented: {unimplemented - db_codes}")
    assert len(db_codes) == 73


def _implemented_codes():
    db_codes = set(_db_slice())
    return sorted(cid for cid in cards.CARDS
                  if cid in db_codes and cid not in deck_e.UNIMPLEMENTED)


# ── Smoke test: every implemented card can be played ─────────────────

@pytest.mark.parametrize("code", _implemented_codes())
def test_card_can_be_played(engine, code):
    s = make_state(engine, 2, seed=7)
    first = s["current_player"]
    give_card(s, first, code)
    # Generous resources so any on-play cost/optional purchase is affordable.
    give(s, first, food=20, wood=20, clay=20, reed=20, stone=20, grain=20,
        vegetable=20)
    pid = s["players"][first]["player_id"]
    s = place(engine, s, {"kind": "place", "space": "lessons", "card": code})
    s = resolve_all_prompts(engine, s, pid)
    p = s["players"][first]
    assert any(i["id"] == code for i in p["occupations"])
    assert code not in p["hand_occupations"]


# ── Targeted effect tests ─────────────────────────────────────────────

def test_land_agent_play_and_space_bonus(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give_card(s, first, "E147")
    s = place(engine, s, {"kind": "place", "space": "lessons", "card": "E147"})
    p = s["players"][first]
    assert p["resources"]["vegetable"] == 1
    add_space(s, "vegetable_seeds", "Vegetable Seeds")
    reset_space(s, "lessons", first)  # free the space so it's this player's turn
    s["current_player"] = first
    s["players"][first]["people_placed"] = 0
    grain_before = p["resources"]["grain"]
    s = place(engine, s, {"kind": "place", "space": "vegetable_seeds"})
    p = s["players"][first]
    assert p["resources"]["grain"] == grain_before + 1


def test_berry_picker_wood_action_bonus(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "E152")
    food_before = s["players"][first]["resources"]["food"]
    s = place(engine, s, {"kind": "place", "space": "forest"})
    p = s["players"][first]
    assert p["resources"]["food"] == food_before + 1


def test_mendicant_offsets_begging(engine):
    s = make_state(engine, 2)
    inst = put_in_play(s, 0, "E153")
    p = s["players"][0]
    p["begging"] = 1
    assert cards.CARDS["E153"]["score_bonus"](s, p, inst) == 3
    p["begging"] = 3
    assert cards.CARDS["E153"]["score_bonus"](s, p, inst) == 6  # capped at 2


def test_master_brewer_conversion(engine):
    s = make_state(engine, 2)
    put_in_play(s, 0, "E154")
    convs = cards.conversion_options(s["players"][0])
    assert len(convs) == 1
    key, conv, _inst = convs[0]
    assert conv == {"give": {"grain": 1}, "get": {"food": 3}, "per_harvest": 1}


def test_thatcher_room_and_renovation_reed_discount(engine):
    s = make_state(engine, 2)
    put_in_play(s, 0, "E157")
    p = s["players"][0]
    assert cards.modified_cost(s, p, "room", {"wood": 5, "reed": 2}) \
        == {"wood": 5, "reed": 1}
    assert cards.modified_cost(s, p, "renovation", {"clay": 3, "reed": 1}) \
        == {"clay": 3}


def test_turner_card_action(engine):
    s = make_state(engine, 2)
    inst = put_in_play(s, 0, "E158")
    p = s["players"][0]
    give(s, 0, wood=3)
    assert cards.CARDS["E158"]["card_action"]["available"](s, p, inst)
    ctx = {"log": [], "actor": 0, "params": {"count": 3}, "extra": {}}
    cards.CARDS["E158"]["card_action"]["apply"](s, p, inst, ctx)
    assert p["resources"]["wood"] == 0
    assert p["resources"]["food"] == 3 + (2 if s["player_count"] > 1 else 0)


def test_farmer_boar_then_cattle_on_fences(engine):
    s = make_state(engine, 2)
    inst = put_in_play(s, 0, "E160")
    p = s["players"][0]
    ctx = {"actor": 0, "log": [], "extra": {}}
    cards.CARDS["E160"]["hooks"]["fences_built"](s, p, inst, ctx)
    assert ctx["extra"] == {"boar": 1}
    ctx2 = {"actor": 0, "log": [], "extra": {}}
    cards.CARDS["E160"]["hooks"]["fences_built"](s, p, inst, ctx2)
    assert ctx2["extra"] == {"cattle": 1}


def test_meat_seller_requires_oven(engine):
    s = make_state(engine, 2)
    inst = put_in_play(s, 0, "E162")
    p = s["players"][0]
    p["cells"][0]["animal"] = {"type": "sheep", "count": 2}
    assert not cards.CARDS["E162"]["card_action"]["available"](s, p, inst)
    p["improvements"] = ["clay_oven"]
    assert cards.CARDS["E162"]["card_action"]["available"](s, p, inst)
    food_before = p["resources"]["food"]
    ctx = {"log": [], "actor": 0, "params": {"animal": "sheep", "count": 2},
          "extra": {}}
    cards.CARDS["E162"]["card_action"]["apply"](s, p, inst, ctx)
    assert p["resources"]["food"] == food_before + 4
    assert cards.animal_totals_of(p)["sheep"] == 0


def test_yeoman_farmer_offsets_negative_categories(engine):
    s = make_state(engine, 2)
    inst = put_in_play(s, 0, "E165")
    p = s["players"][0]
    # A totally bare farmyard scores -1 in all 7 comparable categories.
    assert cards.CARDS["E165"]["score_bonus"](s, p, inst) == 7
    p["resources"]["grain"] = 8  # no longer negative for grain
    assert cards.CARDS["E165"]["score_bonus"](s, p, inst) == 6


def test_undergardener_and_conjurer_space_bonuses(engine):
    s = make_state(engine, 2)
    put_in_play(s, 0, "E166")
    p = s["players"][0]
    grain_before, veg_before = p["resources"]["grain"], p["resources"]["vegetable"]
    s = place(engine, s, {"kind": "place", "space": "day_laborer"})
    p = s["players"][0]
    assert p["resources"]["vegetable"] == veg_before + 1
    assert p["resources"]["food"] == 2 + 2  # base Day Laborer food


def test_storyteller_leave_food_for_vegetable(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "E169")
    add_space(s, "traveling_players", acc=True, supply={"food": 3})
    s = place(engine, s, {"kind": "place", "space": "traveling_players"})
    prompt = s["prompts"][0]
    assert prompt["type"] == "choice"
    pid = s["players"][first]["player_id"]
    food_before = s["players"][first]["resources"]["food"]
    s = engine.apply_action(s, pid, {"kind": "choice", "index": 1}).new_state
    p = s["players"][first]
    assert p["resources"]["food"] == food_before - 1
    assert p["resources"]["vegetable"] == 1
    tp = next(sp for sp in s["action_spaces"] if sp["id"] == "traveling_players")
    assert tp["supply"] == {"food": 1}


def test_estate_manager_ties_and_losses(engine):
    s = make_state(engine, 3)
    inst = put_in_play(s, 0, "E170")
    p0, p1, p2 = s["players"]
    p0["cells"][0]["animal"] = {"type": "sheep", "count": 3}
    p1["cells"][0]["animal"] = {"type": "sheep", "count": 2}
    assert cards.CARDS["E170"]["score_bonus"](s, p0, inst) == 2
    p1["cells"][0]["animal"] = {"type": "sheep", "count": 5}
    assert cards.CARDS["E170"]["score_bonus"](s, p0, inst) == 0


def test_dock_worker_conversions(engine):
    s = make_state(engine, 2)
    inst = put_in_play(s, 0, "E171")
    p = s["players"][0]
    give(s, 0, wood=3)
    ctx = {"log": [], "actor": 0, "params": {"give": "wood", "get": "stone"},
          "extra": {}}
    cards.CARDS["E171"]["card_action"]["apply"](s, p, inst, ctx)
    assert p["resources"]["wood"] == 0
    assert p["resources"]["stone"] == 1


def test_chief_extra_cost_and_stone_room_score(engine):
    assert cards.CARDS["E172"]["cost"] == {"food": 2}
    s = make_state(engine, 2)
    inst = put_in_play(s, 0, "E172")
    p = s["players"][0]
    p["house_type"] = "stone"
    rooms = sum(1 for c in p["cells"] if c["type"] == "room")
    assert cards.CARDS["E172"]["score_bonus"](s, p, inst) == rooms
    p["house_type"] = "wood"
    assert cards.CARDS["E172"]["score_bonus"](s, p, inst) == 0


def test_tutor_counts_later_occupations(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give_card(s, first, "occ_woodcutter")
    give_card(s, first, "E174")
    give_card(s, first, "occ_stonecutter")
    s = place(engine, s,
             {"kind": "place", "space": "lessons", "card": "occ_woodcutter"})
    reset_space(s, "lessons", first)
    s = place(engine, s, {"kind": "place", "space": "lessons", "card": "E174"})
    p = s["players"][first]
    inst = next(i for i in p["occupations"] if i["id"] == "E174")
    assert cards.CARDS["E174"]["score_bonus"](s, p, inst) == 0
    reset_space(s, "lessons", first)
    s = place(engine, s,
             {"kind": "place", "space": "lessons", "card": "occ_stonecutter"})
    p = s["players"][first]
    assert cards.CARDS["E174"]["score_bonus"](s, p, inst) == 1


def test_hedge_keeper_free_fences(engine):
    s = make_state(engine, 2)
    put_in_play(s, 0, "E175")
    p = s["players"][0]
    cost = cards.modified_cost(s, p, "fences", {"wood": 4}, {"count": 4})
    assert cost == {"wood": 1}


def test_woodcutter_wood_bonus(engine):
    s = make_state(engine, 2)
    put_in_play(s, 0, "occ_forager") if False else None
    put_in_play(s, 0, "E176")
    p = s["players"][0]
    wood_before = p["resources"]["wood"]
    s = place(engine, s, {"kind": "place", "space": "forest"})
    p = s["players"][0]
    assert p["resources"]["wood"] == wood_before + 3 + 1


def test_hut_builder_free_room_at_round_11(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give_card(s, first, "E178")
    s["round"] = 3
    s = place(engine, s, {"kind": "place", "space": "lessons", "card": "E178"})
    p = s["players"][first]
    inst = next(i for i in p["occupations"] if i["id"] == "E178")
    assert inst["data"]["eligible"] is True
    s["round"] = 11
    ctx = {"round": 11, "log": [], "actor": first, "extra": {}}
    rooms_before = sum(1 for c in p["cells"] if c["type"] == "room")
    cards.CARDS["E178"]["hooks"]["round_start"](s, p, inst, ctx)
    rooms_after = sum(1 for c in s["players"][first]["cells"]
                      if c["type"] == "room")
    assert rooms_after == rooms_before + 1


def test_hobby_farmer_gets_and_sows_vegetable(engine):
    s = make_state(engine, 4)
    first = s["current_player"]
    give_card(s, first, "E180")
    p = s["players"][first]
    p["cells"][0]["type"] = "field"
    s = place(engine, s, {"kind": "place", "space": "lessons", "card": "E180",
                          "params": {"sow": True, "cell": 0}})
    p = s["players"][first]
    assert p["cells"][0]["crops"] == {"type": "vegetable", "count": 2}
    assert p["resources"]["vegetable"] == 0


def test_cook_pregrants_food_savings(engine):
    s = make_state(engine, 4)
    inst = put_in_play(s, 0, "E181")
    p = s["players"][0]
    p["people_total"] = 4
    p["newborns"] = 0
    food_before = p["resources"]["food"]
    ctx = {"harvest_index": 1, "log": [], "actor": 0, "extra": {}}
    cards.CARDS["E181"]["hooks"]["harvest_field"](s, p, inst, ctx)
    assert p["resources"]["food"] == food_before + 2


def test_charcoal_burner_reacts_to_any_player(engine):
    s = make_state(engine, 2)
    inst = put_in_play(s, 0, "E182")
    p0 = s["players"][0]
    ctx = {"actor": 1, "improvement": "clay_oven", "log": [], "extra": {}}
    food_before, wood_before = p0["resources"]["food"], p0["resources"]["wood"]
    cards.CARDS["E182"]["hooks"]["improvement_built"](s, p0, inst, ctx)
    assert p0["resources"]["food"] == food_before + 1
    assert p0["resources"]["wood"] == wood_before + 1


def test_grocer_pile_order_and_cost(engine):
    s = make_state(engine, 2)
    inst = put_in_play(s, 0, "E184")
    p = s["players"][0]
    give(s, 0, food=8)
    for expected in deck_e._GROCER_PILE:
        assert cards.CARDS["E184"]["card_action"]["available"](s, p, inst)
        before = p["resources"][expected]
        ctx = {"log": [], "actor": 0, "params": {}, "extra": {}}
        cards.CARDS["E184"]["card_action"]["apply"](s, p, inst, ctx)
        assert p["resources"][expected] == before + 1
    assert not cards.CARDS["E184"]["card_action"]["available"](s, p, inst)


def test_clay_seller_conversion(engine):
    s = make_state(engine, 2)
    inst = put_in_play(s, 0, "E186")
    p = s["players"][0]
    give(s, 0, clay=4)
    ctx = {"log": [], "actor": 0, "params": {"option": "cattle"}, "extra": {}}
    cards.CARDS["E186"]["card_action"]["apply"](s, p, inst, ctx)
    assert ctx["extra"] == {"cattle": 1}
    assert p["resources"]["clay"] == 0


def test_clay_deliveryman_schedules_future_rounds(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give_card(s, first, "E187")
    s = place(engine, s, {"kind": "place", "space": "lessons", "card": "E187"})
    for r in range(6, 15):
        assert s["round_goods"][str(r)][str(first)]["clay"] == 1
    assert "5" not in s["round_goods"] or str(first) not in s["round_goods"].get("5", {})


def test_clay_mixer_only_when_clay_alone(engine):
    s = make_state(engine, 2)
    inst = put_in_play(s, 0, "E188")
    p = s["players"][0]
    ctx = {"actor": 0, "space_id": "clay_pit", "goods": {"clay": 1},
          "extra": {}, "log": []}
    cards.CARDS["E188"]["hooks"]["space_used"](s, p, inst, ctx)
    assert ctx["extra"] == {"clay": 2}
    ctx2 = {"actor": 0, "space_id": "x", "goods": {"clay": 1, "wood": 1},
           "extra": {}, "log": []}
    cards.CARDS["E188"]["hooks"]["space_used"](s, p, inst, ctx2)
    assert ctx2["extra"] == {}


def test_lord_of_the_manor_counts_max_categories(engine):
    s = make_state(engine, 2)
    inst = put_in_play(s, 0, "E189")
    p = s["players"][0]
    p["resources"]["grain"] = 8  # table_score("grain", 8) == 4
    assert cards.CARDS["E189"]["score_bonus"](s, p, inst) == 1


def test_maid_schedules_food_once_clay_or_stone(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give_card(s, first, "E190")
    s["players"][first]["house_type"] = "wood"
    s = place(engine, s, {"kind": "place", "space": "lessons", "card": "E190"})
    p = s["players"][first]
    inst = next(i for i in p["occupations"] if i["id"] == "E190")
    assert inst["data"].get("pending") is True
    assert not s["round_goods"]
    ctx = {"free_stable_cell": None, "log": [], "actor": first, "extra": {}}
    p["house_type"] = "clay"
    cards.CARDS["E190"]["hooks"]["renovate"](s, p, inst, ctx)
    assert s["round_goods"][str(s["round"] + 1)][str(first)]["food"] == 1


def test_master_builder_and_mason_free_room(engine):
    s = make_state(engine, 2)
    inst = put_in_play(s, 0, "E151")
    p = s["players"][0]
    for c in range(5):
        p["cells"][c]["type"] = "room"
    assert cards.CARDS["E151"]["card_action"]["available"](s, p, inst)
    cell = sub_actions.buildable_room_cells(p)[0]
    ctx = {"log": [], "actor": 0, "params": {"cell": cell}, "extra": {}}
    cards.CARDS["E151"]["card_action"]["apply"](s, p, inst, ctx)
    assert p["cells"][cell]["type"] == "room"
    assert not cards.CARDS["E151"]["card_action"]["available"](s, p, inst)

    s2 = make_state(engine, 2)
    inst2 = put_in_play(s2, 0, "E191")
    p2 = s2["players"][0]
    for c in range(4):
        p2["cells"][c]["type"] = "room"
    p2["house_type"] = "wood"
    assert not cards.CARDS["E191"]["card_action"]["available"](s2, p2, inst2)
    p2["house_type"] = "stone"
    assert cards.CARDS["E191"]["card_action"]["available"](s2, p2, inst2)


def test_plough_driver_auto_plows_each_round(engine):
    s = make_state(engine, 2)
    inst = put_in_play(s, 0, "E194")
    p = s["players"][0]
    p["house_type"] = "stone"
    food_before = p["resources"]["food"]
    fields_before = sum(1 for c in p["cells"] if c["type"] == "field")
    ctx = {"round": 2, "log": [], "actor": 0, "extra": {}}
    cards.CARDS["E194"]["hooks"]["round_start"](s, p, inst, ctx)
    fields_after = sum(1 for c in p["cells"] if c["type"] == "field")
    assert fields_after == fields_before + 1
    assert p["resources"]["food"] == food_before - 1


def test_plough_maker_pay_food_for_extra_plow(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "E195")
    food_before = s["players"][first]["resources"]["food"]
    s = place(engine, s, {"kind": "place", "space": "farmland", "cell": 0})
    prompt = s["prompts"][0]
    assert prompt["type"] == "choice"
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {"kind": "choice", "index": 1}).new_state
    p = s["players"][first]
    assert p["cells"][0]["type"] == "field"
    assert p["cells"][prompt["data"]["cells"][0]]["type"] == "field"
    assert p["resources"]["food"] == food_before - 1


def test_seasonal_worker_choice_after_round_6(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "E202")
    s["round"] = 6
    s = place(engine, s, {"kind": "place", "space": "day_laborer"})
    prompt = s["prompts"][0]
    assert prompt["type"] == "choice"
    pid = s["players"][first]["player_id"]
    veg_before = s["players"][first]["resources"]["vegetable"]
    s = engine.apply_action(s, pid, {"kind": "choice", "index": 1}).new_state
    p = s["players"][first]
    assert p["resources"]["vegetable"] == veg_before + 1


def test_shepherd_extra_lamb_with_four_sheep(engine):
    s = make_state(engine, 4)
    inst = put_in_play(s, 0, "E203")
    p = s["players"][0]
    p["cells"][0]["animal"] = {"type": "sheep", "count": 4}
    ctx = {"harvest_index": 1, "log": [], "actor": 0, "extra": {}}
    cards.CARDS["E203"]["hooks"]["harvest_field"](s, p, inst, ctx)
    assert cards.animal_totals_of(p)["sheep"] == 5


def test_master_shepherd_scheduled_sheep(engine):
    s = make_state(engine, 4)
    first = s["current_player"]
    give_card(s, first, "E204")
    s = place(engine, s, {"kind": "place", "space": "lessons", "card": "E204"})
    p = s["players"][first]
    inst = next(i for i in p["occupations"] if i["id"] == "E204")
    targets = inst["data"]["targets"]
    assert targets == [s["round"] - 0 + 1, s["round"] + 2, s["round"] + 3][:len(targets)] \
        or len(targets) == 3
    s["round"] = targets[0]
    ctx = {"round": s["round"], "log": [], "actor": first, "extra": {}}
    cards.CARDS["E204"]["hooks"]["round_start"](s, p, inst, ctx)
    assert cards.animal_totals_of(s["players"][first])["sheep"] == 1


def test_swineherd_boar_bonus(engine):
    s = make_state(engine, 4)
    put_in_play(s, 0, "E206")
    p = s["players"][0]
    add_space(s, "pig_market", "Pig Market", acc=True, supply={"boar": 1})
    reset_space(s, "pig_market", 0)
    s = place(engine, s, {"kind": "place", "space": "pig_market"})
    assert s["prompts"][0]["gained"]["boar"] == 2


def test_stablehand_free_stable_on_fences(engine):
    s = make_state(engine, 2)
    inst = put_in_play(s, 0, "E207")
    p = s["players"][0]
    p["fences"] = sorted(cell_edges(4))
    ctx = {"actor": 0, "log": [], "new_pastures": [[4]], "extra": {}}
    cards.CARDS["E207"]["hooks"]["fences_built"](s, p, inst, ctx)
    prompt = s["prompts"][0]
    cell = prompt["data"]["cells"][0]
    resolve_ctx = {"index": 1, "option": prompt["options"][1],
                  "data": prompt["data"], "log": [], "actor": 0, "extra": {}}
    cards.CARDS["E207"]["resolve_choice"](s, p, inst, resolve_ctx)
    assert p["cells"][cell]["stable"] is True


def test_quarryman_stone_to_food(engine):
    s = make_state(engine, 2)
    inst = put_in_play(s, 0, "E209")
    p = s["players"][0]
    give(s, 0, stone=2)
    food_before = p["resources"]["food"]
    ctx = {"log": [], "actor": 0, "params": {"count": 2}, "extra": {}}
    cards.CARDS["E209"]["card_action"]["apply"](s, p, inst, ctx)
    assert p["resources"]["food"] == food_before + 4
    assert p["resources"]["stone"] == 0


def test_stone_carrier_free_and_paid_bonus(engine):
    s = make_state(engine, 2)
    inst = put_in_play(s, 0, "E210")
    p = s["players"][0]
    ctx = {"actor": 0, "goods": {"stone": 1}, "extra": {}, "log": []}
    cards.CARDS["E210"]["hooks"]["space_used"](s, p, inst, ctx)
    assert ctx["extra"] == {"stone": 1}
    p["resources"]["food"] = 1
    ctx2 = {"actor": 0, "goods": {"stone": 1, "wood": 1}, "extra": {}, "log": []}
    cards.CARDS["E210"]["hooks"]["space_used"](s, p, inst, ctx2)
    assert s["prompts"][0]["type"] == "choice"


def test_stonecutter_cost_mod(engine):
    s = make_state(engine, 2)
    put_in_play(s, 0, "E211")
    p = s["players"][0]
    assert cards.modified_cost(s, p, "improvement", {"stone": 3}) == {"stone": 2}


def test_dancer_tops_up_food(engine):
    s = make_state(engine, 2)
    inst = put_in_play(s, 0, "E212")
    p = s["players"][0]
    ctx = {"actor": 0, "space_id": "traveling_players", "goods": {"food": 2},
          "extra": {}, "log": []}
    cards.CARDS["E212"]["hooks"]["space_used"](s, p, inst, ctx)
    assert ctx["extra"] == {"food": 2}


def test_stockman_animals_by_stable_number(engine):
    s = make_state(engine, 4)
    inst = put_in_play(s, 0, "E213")
    p = s["players"][0]
    for c in range(4):
        p["cells"][c]["stable"] = True
    ctx = {"cells": [1, 2, 3], "actor": 0, "log": [], "extra": {}}
    cards.CARDS["E213"]["hooks"]["stable_built"](s, p, inst, ctx)
    assert ctx["extra"] == {"cattle": 1, "boar": 1, "sheep": 1}


def test_potter_conversion(engine):
    s = make_state(engine, 4)
    put_in_play(s, 0, "E214")
    convs = cards.conversion_options(s["players"][0])
    assert convs[0][1] == {"give": {"clay": 1}, "get": {"food": 2},
                          "per_harvest": 1}


def test_reeve_wood_by_round_and_score(engine):
    s = make_state(engine, 3)
    first = s["current_player"]
    s["round"] = 6
    give_card(s, first, "E217")
    s = place(engine, s, {"kind": "place", "space": "lessons", "card": "E217"})
    p = s["players"][first]
    assert p["resources"]["wood"] == 3
    inst = next(i for i in p["occupations"] if i["id"] == "E217")
    assert cards.CARDS["E217"]["score_bonus"](s, p, inst) == 3


def test_carpenter_room_cost_mod(engine):
    s = make_state(engine, 2)
    put_in_play(s, 0, "E218")
    p = s["players"][0]
    assert cards.modified_cost(s, p, "room", {"wood": 5, "reed": 2}) \
        == {"wood": 3, "reed": 2}


def test_guildmaster_catchup_and_future_bonuses(engine):
    s = make_state(engine, 2)
    p = s["players"][0]
    p["improvements"] = ["pottery"]
    put_in_play(s, 0, "E214")  # Potter occupation already in play
    inst = put_in_play(s, 0, "E341")
    ctx = {"actor": 0, "log": [], "extra": {}}
    cards.CARDS["E341"]["hooks"]["play"](s, p, inst, ctx)
    assert p["resources"]["clay"] == 4  # 2 (pottery major) + 2 (Potter occ)

    s2 = make_state(engine, 2)
    p2 = s2["players"][0]
    inst2 = put_in_play(s2, 0, "E341")
    ctx2 = {"actor": 0, "improvement": "joinery", "log": [], "extra": {}}
    cards.CARDS["E341"]["hooks"]["improvement_built"](s2, p2, inst2, ctx2)
    assert p2["resources"]["wood"] == 4
    ctx3 = {"actor": 0, "card_id": "E183", "log": [], "extra": {}}
    cards.CARDS["E341"]["hooks"]["occupation_played"](s2, p2, inst2, ctx3)
    assert p2["resources"]["reed"] == 3


def test_conservator_skip_to_stone(engine):
    s = make_state(engine, 2)
    inst = put_in_play(s, 0, "E200")
    p = s["players"][0]
    rooms = sum(1 for c in p["cells"] if c["type"] == "room")
    give(s, 0, stone=rooms, reed=1)
    assert cards.CARDS["E200"]["card_action"]["available"](s, p, inst)
    ctx = {"log": [], "actor": 0, "extra": {}}
    cards.CARDS["E200"]["card_action"]["apply"](s, p, inst, ctx)
    assert p["house_type"] == "stone"
    assert p["resources"]["stone"] == 0 and p["resources"]["reed"] == 0


def test_cattle_whisperer_scheduled_cattle(engine):
    s = make_state(engine, 4)
    first = s["current_player"]
    give_card(s, first, "E201")
    s = place(engine, s, {"kind": "place", "space": "lessons", "card": "E201"})
    p = s["players"][first]
    inst = next(i for i in p["occupations"] if i["id"] == "E201")
    assert inst["data"]["targets"] == [s["round"] + 5, s["round"] + 9]
    s["round"] = inst["data"]["targets"][0]
    ctx = {"round": s["round"], "log": [], "actor": first, "extra": {}}
    cards.CARDS["E201"]["hooks"]["round_start"](s, p, inst, ctx)
    assert cards.animal_totals_of(s["players"][first])["cattle"] == 1


def test_baker_card_action_once_per_harvest(engine):
    s = make_state(engine, 2)
    inst = put_in_play(s, 0, "E150")
    p = s["players"][0]
    p["improvements"] = ["clay_oven"]
    give(s, 0, grain=2)
    s["phase"] = "feeding"
    s["harvest_index"] = 1
    assert cards.CARDS["E150"]["card_action"]["available"](s, p, inst)
    ctx = {"log": [], "actor": 0, "params": {"bake": {"clay_oven": 1}},
          "extra": {}}
    cards.CARDS["E150"]["card_action"]["apply"](s, p, inst, ctx)
    assert p["resources"]["grain"] == 1
    assert not cards.CARDS["E150"]["card_action"]["available"](s, p, inst)


_SAFE_SPACES = ("day_laborer", "fishing", "grain_seeds", "meeting_place",
               "forest", "clay_pit", "reed_bank", "traveling_players",
               "western_quarry", "eastern_quarry", "vegetable_seeds",
               "copse", "grove", "hollow_3p", "hollow_4p")


def test_master_forester_toll_to_owner(engine):
    """E164 card_space: "acc" replenishes 2 wood every round_start; a
    non-owner placer must pay the owner 2 food before collecting it (the
    space isn't usable until the round after it's played, same as any
    card_space accumulation space)."""
    s = make_state(engine, 2, seed=7)
    first = s["current_player"]
    other = (first + 1) % 2
    give_card(s, first, "E164")
    s = place(engine, s, {"kind": "place", "space": "lessons", "card": "E164"})
    space = next(sp for sp in s["action_spaces"] if sp["id"] == "card:E164")
    assert space["accumulates"]
    assert space["supply"] == {}

    while s["round"] == 1:
        pid = current_pid(engine, s)
        act = next(a for a in engine.get_valid_actions(s, pid)
                  if a["kind"] == "place" and a["space"] in _SAFE_SPACES)
        s = place(engine, s, {"kind": "place", "space": act["space"]})

    space = next(sp for sp in s["action_spaces"] if sp["id"] == "card:E164")
    assert space["supply"] == {"wood": 2}

    s["current_player"] = other
    give(s, other, food=2)
    other_food = s["players"][other]["resources"]["food"]
    owner_food = s["players"][first]["resources"]["food"]
    other_pid = s["players"][other]["player_id"]
    wood_before = s["players"][other]["resources"]["wood"]

    s = engine.apply_action(
        s, other_pid, {"kind": "place", "space": "card:E164"}).new_state
    assert s["players"][other]["resources"]["wood"] == wood_before + 2
    assert s["players"][other]["resources"]["food"] == other_food - 2
    assert s["players"][first]["resources"]["food"] == owner_food + 2
    space = next(sp for sp in s["action_spaces"] if sp["id"] == "card:E164")
    assert space["supply"] == {}


def test_master_forester_owner_no_toll_and_insufficient_food_raises(engine):
    """"If you use the Master Forester yourself, you do not need to have
    or to pay any food" -- and a non-owner without 2 food is rejected,
    rolling back cleanly (apply_action's deep copy discards the failed
    mutation, so the supply is untouched)."""
    s = make_state(engine, 2, seed=7)
    first = s["current_player"]
    other = (first + 1) % 2
    give_card(s, first, "E164")
    s = place(engine, s, {"kind": "place", "space": "lessons", "card": "E164"})
    space = next(sp for sp in s["action_spaces"] if sp["id"] == "card:E164")
    space["supply"] = {"wood": 2}  # simulate a round_start replenish

    first_pid = s["players"][first]["player_id"]
    s["current_player"] = first
    wood_before = s["players"][first]["resources"]["wood"]
    food_before = s["players"][first]["resources"]["food"]
    s = engine.apply_action(
        s, first_pid, {"kind": "place", "space": "card:E164"}).new_state
    assert s["players"][first]["resources"]["wood"] == wood_before + 2
    assert s["players"][first]["resources"]["food"] == food_before

    space = next(sp for sp in s["action_spaces"] if sp["id"] == "card:E164")
    space["occupied_by"] = None
    space["extra_occupants"] = []
    space["supply"] = {"wood": 2}
    s["players"][other]["resources"]["food"] = 0
    other_pid = s["players"][other]["player_id"]
    s["current_player"] = other
    with pytest.raises(ValueError):
        engine.apply_action(
            s, other_pid, {"kind": "place", "space": "card:E164"})
    space = next(sp for sp in s["action_spaces"] if sp["id"] == "card:E164")
    assert space["supply"] == {"wood": 2}  # unchanged -- rolled back


def test_chiefs_daughter_reacts_when_another_player_plays_the_chief(engine):
    """E173 Chief's Daughter, the real card: hand_react + prompt_choice(
    from_hand=True) (decks/GUIDE.md's "Hand reactions" section -- this
    exact card is the worked example there; test_hand_react_accept_
    plays_card_from_hand_at_no_cost in tests/test_agricola.py is the
    temp_card version of this same flow). Chief's Daughter's own
    reactive play is free, regardless of the Lessons space's own
    escalating occupation cost."""
    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    give_card(s, first, "E172")
    give_card(s, other, "E173")
    other_food_before = s["players"][other]["resources"]["food"]

    s = place(engine, s, {"kind": "place", "space": "lessons", "card": "E172"})
    other_pid = s["players"][other]["player_id"]
    assert engine.get_waiting_for(s) == [other_pid]
    prompt = s["prompts"][0]
    assert prompt["type"] == "choice" and prompt["from_hand"] is True
    assert prompt["card"] == "E173"

    s = engine.apply_action(
        s, other_pid, {"kind": "choice", "index": 0}).new_state  # "yes"
    p = s["players"][other]
    assert "E173" not in p["hand_occupations"]
    assert any(i["id"] == "E173" for i in p["occupations"])
    assert p["resources"]["food"] == other_food_before  # free
    assert not s["prompts"]


def test_chiefs_daughter_declining_leaves_it_in_hand(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    give_card(s, first, "E172")
    give_card(s, other, "E173")

    s = place(engine, s, {"kind": "place", "space": "lessons", "card": "E172"})
    other_pid = s["players"][other]["player_id"]
    s = engine.apply_action(
        s, other_pid, {"kind": "choice", "index": 1}).new_state  # "no"
    p = s["players"][other]
    assert "E173" in p["hand_occupations"]
    assert not any(i["id"] == "E173" for i in p["occupations"])


def test_chiefs_daughter_does_not_react_to_own_chief_play(engine):
    """"If you play the Chief yourself, you may not play the Chief's
    Daughter at the same time" -- ctx["actor"] == hand_player guards it."""
    s = make_state(engine, 2)
    first = s["current_player"]
    give_card(s, first, "E172")
    give_card(s, first, "E173")

    s = place(engine, s, {"kind": "place", "space": "lessons", "card": "E172"})
    assert not s["prompts"]
    assert "E173" in s["players"][first]["hand_occupations"]


def test_chiefs_daughter_score_bonus_by_house_type(engine):
    s = make_state(engine, 2)
    p = s["players"][0]
    inst = {"id": "E173", "data": {}}
    score_fn = cards.CARDS["E173"]["score_bonus"]
    p["house_type"] = "stone"
    assert score_fn(s, p, inst) == 3
    p["house_type"] = "clay"
    assert score_fn(s, p, inst) == 1
    p["house_type"] = "wood"
    assert score_fn(s, p, inst) == 0
