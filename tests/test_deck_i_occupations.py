"""Tests for deck I occupations (server/agricola/decks/deck_i_occupations.py).

Helper functions below are copied from tests/test_agricola.py (make_state,
give, give_card, put_in_play, add_space, place, resolve_all_prompts) per
decks/GUIDE.md's testing guidance.
"""

import json
import os
import random

import pytest

from server.agricola.engine import AgricolaEngine
from server.agricola import cards, sub_actions
from server.agricola.decks import deck_i_occupations as deck_i
from server.agricola.state import cell_edges, compute_pastures


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
            if c["deck"] == "I" and c["type"] == "occupation"]


def test_registration_completeness():
    db_codes = set(_db_slice())
    registered = {cid for cid in cards.CARDS if cid in db_codes}
    unimplemented = set(deck_i.UNIMPLEMENTED)
    assert not (registered & unimplemented), "overlap between registered and UNIMPLEMENTED"
    assert registered | unimplemented == db_codes, (
        f"missing: {db_codes - registered - unimplemented}, "
        f"unexpected unimplemented: {unimplemented - db_codes}")


def _implemented_codes():
    db_codes = set(_db_slice())
    return sorted(cid for cid in cards.CARDS
                  if cid in db_codes and cid not in deck_i.UNIMPLEMENTED)


# ── Smoke test: every implemented card can be played ─────────────────

@pytest.mark.parametrize("code", _implemented_codes())
def test_card_can_be_played(engine, code):
    s = make_state(engine, 4, seed=7)
    first = s["current_player"]
    give_card(s, first, code)
    # Generous resources so any on-play cost/optional purchase is affordable.
    give(s, first, food=20, wood=20, clay=20, reed=20, stone=20, grain=20,
        vegetable=20)
    pid = s["players"][first]["player_id"]
    lessons_id = "lessons_b" if s["player_count"] >= 3 else "lessons"
    s = place(engine, s, {"kind": "place", "space": lessons_id, "card": code})
    s = resolve_all_prompts(engine, s, pid)
    p = s["players"][first]
    assert any(i["id"] == code for i in p["occupations"])
    assert code not in p["hand_occupations"]


# ── Targeted effect tests ─────────────────────────────────────────────

def test_fieldsman_single_field_bonus(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    inst = put_in_play(s, first, "I219")
    p["cells"][0]["type"] = "field"
    p["cells"][0]["crops"] = {"type": "grain", "count": 3}
    ctx = {"sown": [(0, "grain")], "log": [], "actor": first, "extra": {}}
    cards.CARDS["I219"]["hooks"]["sow"](s, p, inst, ctx)
    assert p["cells"][0]["crops"]["count"] == 5  # +2 for a single field


def test_fieldsman_two_fields_bonus(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    inst = put_in_play(s, first, "I219")
    for cell in (0, 1):
        p["cells"][cell]["type"] = "field"
        p["cells"][cell]["crops"] = {"type": "vegetable", "count": 2}
    ctx = {"sown": [(0, "vegetable"), (1, "vegetable")], "log": [],
          "actor": first, "extra": {}}
    cards.CARDS["I219"]["hooks"]["sow"](s, p, inst, ctx)
    assert p["cells"][0]["crops"]["count"] == 3  # +1 each
    assert p["cells"][1]["crops"]["count"] == 3


def test_village_elder_wood_by_round(engine):
    s = make_state(engine, 4)
    first = s["current_player"]
    p = s["players"][first]
    inst = put_in_play(s, first, "I221")
    s["round"] = 3
    wood_before = p["resources"]["wood"]
    ctx = {"log": [], "actor": first, "extra": {}}
    cards.CARDS["I221"]["hooks"]["play"](s, p, inst, ctx)
    assert p["resources"]["wood"] == wood_before + 4
    s["round"] = 12
    wood_before = p["resources"]["wood"]
    cards.CARDS["I221"]["hooks"]["play"](s, p, inst, ctx)
    assert p["resources"]["wood"] == wood_before + 1


def test_village_elder_most_improvements_bonus(engine):
    s = make_state(engine, 2)
    p0, p1 = s["players"][0], s["players"][1]
    put_in_play(s, 0, "I221")
    p0["improvements"] = ["fireplace_2", "well"]
    p1["improvements"] = ["fireplace_2"]
    inst = next(i for i in p0["occupations"] if i["id"] == "I221")
    assert cards.CARDS["I221"]["score_bonus"](s, p0, inst) == 3
    assert cards.CARDS["I221"]["score_bonus"](s, p1, inst) == 0


def test_field_watchman_extra_plow(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "I225")
    s = place(engine, s, {"kind": "place", "space": "grain_seeds"})
    prompt = s["prompts"][0]
    assert prompt["type"] == "choice"
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {"kind": "choice", "index": 1}).new_state
    p = s["players"][first]
    assert p["cells"][0]["type"] == "field"


def test_church_warden_bonus_at_round_14(engine):
    s = make_state(engine, 2)
    p = s["players"][0]
    put_in_play(s, 0, "I227")
    inst = next(i for i in p["occupations"] if i["id"] == "I227")
    s["round"] = 14
    p["people_placed"] = 5
    assert cards.CARDS["I227"]["score_bonus"](s, p, inst) == 3
    p["people_placed"] = 4
    assert cards.CARDS["I227"]["score_bonus"](s, p, inst) == 0
    s["round"] = 13
    p["people_placed"] = 5
    assert cards.CARDS["I227"]["score_bonus"](s, p, inst) == 0


def test_manservant_schedules_food_on_renovate(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "I231")
    p = s["players"][first]
    p["house_type"] = "clay"
    give(s, first, stone=5, reed=1)
    add_space(s, "house_redevelopment", "House Redevelopment")
    s = place(engine, s, {"kind": "place", "space": "house_redevelopment"})
    p = s["players"][first]
    assert p["house_type"] == "stone"
    for r in range(2, 15):
        assert s["round_goods"][str(r)][str(first)]["food"] == 3


def test_midwife_grants_food_on_others_growth(engine):
    s = make_state(engine, 4)
    p0, p1 = s["players"][0], s["players"][1]
    put_in_play(s, 0, "I232")
    p0["people_total"] = 2
    p1["people_total"] = 2
    inst = next(i for i in p0["occupations"] if i["id"] == "I232")
    ctx = {"actor": 1, "log": [], "extra": {}}
    # Simulate p1 growing to 4 (2 more than p0's 2).
    p1["people_total"] = 4
    food_before = p0["resources"]["food"]
    cards.CARDS["I232"]["hooks"]["family_growth"](s, p0, inst, ctx)
    assert p0["resources"]["food"] == food_before + 2


def test_wood_buyer_trade(engine):
    s = make_state(engine, 4, seed=29)
    first = s["current_player"]
    other = (first + 1) % 4
    put_in_play(s, other, "I234")
    give(s, other, food=2)
    forest = next(sp for sp in s["action_spaces"] if sp["id"] == "forest")
    forest["supply"] = {"wood": 3}
    food_first_before = s["players"][first]["resources"]["food"]
    food_other_before = s["players"][other]["resources"]["food"]
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {"kind": "place", "space": "forest"}).new_state
    pid2 = s["players"][other]["player_id"]
    s = engine.apply_action(s, pid2, {"kind": "choice", "index": 1}).new_state
    assert s["players"][first]["resources"]["wood"] == 2  # 3 taken - 1 sold
    assert s["players"][other]["resources"]["wood"] == 1
    assert s["players"][other]["resources"]["food"] == food_other_before - 1
    assert s["players"][first]["resources"]["food"] == food_first_before + 1


def test_wood_collector_scheduled_wood(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give_card(s, first, "I235")
    s = place(engine, s, {"kind": "place", "space": "lessons", "card": "I235"})
    for r in range(2, 7):
        assert s["round_goods"][str(r)][str(first)]["wood"] == 1


def test_hide_farmer_bonus_capped_by_food(engine):
    s = make_state(engine, 2)
    p = s["players"][0]
    inst = put_in_play(s, 0, "I236")
    p["resources"]["food"] = 2
    # Every cell empty (no rooms/fields/stables/pastures) -> 13 unused
    # (15 total minus the 2 starting rooms).
    assert cards.CARDS["I236"]["score_bonus"](s, p, inst) == 2


def test_cowherd_extra_cattle(engine):
    s = make_state(engine, 3)
    first = s["current_player"]
    put_in_play(s, first, "I240")
    add_space(s, "cattle_market", "Cattle Market", acc=True, supply={"cattle": 1})
    s = place(engine, s, {"kind": "place", "space": "cattle_market"})
    assert s["prompts"][0]["gained"] == {"cattle": 2}


def test_clay_plasterer_discounts(engine):
    s = make_state(engine, 2)
    p = s["players"][0]
    put_in_play(s, 0, "I241")
    p["house_type"] = "wood"
    cost = cards.modified_cost(s, p, "renovation", {"clay": 5, "reed": 2})
    assert cost == {"clay": 1, "reed": 1}
    p["house_type"] = "clay"
    cost = cards.modified_cost(s, p, "room", {"clay": 5, "reed": 2})
    assert cost == {"clay": 3, "reed": 2}


def test_clay_hut_builder_schedules_on_renovate(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "I242")
    p = s["players"][first]
    p["house_type"] = "wood"
    give(s, first, clay=5, reed=1)
    add_space(s, "house_redevelopment", "House Redevelopment")
    s = place(engine, s, {"kind": "place", "space": "house_redevelopment"})
    p = s["players"][first]
    assert p["house_type"] == "clay"
    for r in range(2, 7):
        assert s["round_goods"][str(r)][str(first)]["clay"] == 2


def test_bricklayer_discounts(engine):
    s = make_state(engine, 2)
    p = s["players"][0]
    put_in_play(s, 0, "I243")
    cost = cards.modified_cost(s, p, "room", {"clay": 5, "reed": 2})
    assert cost == {"clay": 3, "reed": 2}
    cost = cards.modified_cost(s, p, "improvement", {"clay": 2, "stone": 2})
    assert cost == {"clay": 1, "stone": 2}


def test_market_crier_i245_extra_grain(engine):
    s = make_state(engine, 3)
    first = s["current_player"]
    other = (first + 1) % 3
    put_in_play(s, first, "I245")
    s = place(engine, s, {"kind": "place", "space": "grain_seeds"})
    prompt = s["prompts"][0]
    assert prompt["type"] == "choice"
    pid = s["players"][first]["player_id"]
    grain_other_before = s["players"][other]["resources"]["grain"]
    s = engine.apply_action(s, pid, {"kind": "choice", "index": 1}).new_state
    p = s["players"][first]
    assert p["resources"]["grain"] == 2  # 1 from space + 1 bonus
    assert p["resources"]["vegetable"] == 1
    assert s["players"][other]["resources"]["grain"] == grain_other_before + 1


def test_milking_hand_harvest_food(engine):
    s = make_state(engine, 4)
    p = s["players"][0]
    inst = put_in_play(s, 0, "I246")
    p["cells"][4]["animal"] = {"type": "cattle", "count": 5}
    food_before = p["resources"]["food"]
    ctx = {"harvest_index": 1, "log": [], "actor": 0, "extra": {}}
    cards.CARDS["I246"]["hooks"]["harvest_field"](s, p, inst, ctx)
    assert p["resources"]["food"] == food_before + 3
    assert cards.CARDS["I246"]["score_bonus"](s, p, inst) == 2


def test_butcher_conversions_present(engine):
    s = make_state(engine, 2)
    p = s["players"][0]
    put_in_play(s, 0, "I247")
    opts = cards.conversion_options(p)
    gives = [c["give"] for _key, c, _inst in opts]
    assert {"sheep": 1} in gives
    assert {"boar": 1} in gives
    assert {"cattle": 1} in gives


def test_sheep_whisperer_schedule_and_placement(engine):
    s = make_state(engine, 4)
    first = s["current_player"]
    give_card(s, first, "I250")
    s = place(engine, s, {"kind": "place", "space": "lessons_b", "card": "I250"})
    p = s["players"][first]
    inst = next(i for i in p["occupations"] if i["id"] == "I250")
    assert inst["data"]["sheep_rounds"] == [5, 8, 10, 12]
    s["round"] = 5
    ctx = {"log": [], "actor": first, "extra": {}}
    cards.CARDS["I250"]["hooks"]["round_start"](s, p, inst, ctx)
    assert sum(1 for c in p["cells"] if c.get("animal")
              and c["animal"]["type"] == "sheep") == 1 \
        or p["pets"].get("sheep", 0) == 1


def test_reed_buyer_first_time_only(engine):
    s = make_state(engine, 4, seed=11)
    first = s["current_player"]
    other = (first + 1) % 4
    put_in_play(s, other, "I251")
    give(s, other, food=3)
    reed_bank = next(sp for sp in s["action_spaces"] if sp["id"] == "reed_bank")
    reed_bank["supply"] = {"reed": 1}
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {"kind": "place", "space": "reed_bank"}).new_state
    pid2 = s["players"][other]["player_id"]
    s = engine.apply_action(s, pid2, {"kind": "choice", "index": 1}).new_state
    assert s["players"][first]["resources"]["reed"] == 0
    assert s["players"][other]["resources"]["reed"] == 1
    inst = next(i for i in s["players"][other]["occupations"] if i["id"] == "I251")
    assert inst["data"]["bought_round"] == s["round"]


def test_pig_breeder_play_and_breed(engine):
    s = make_state(engine, 4)
    first = s["current_player"]
    give_card(s, first, "I252")
    s = place(engine, s, {"kind": "place", "space": "lessons_b", "card": "I252"})
    pid = s["players"][first]["player_id"]
    s = resolve_all_prompts(engine, s, pid)  # discards the on-play boar
    p = s["players"][first]
    p["fences"] = sorted(cell_edges(4))
    p["cells"][4]["stable"] = True
    p["cells"][4]["animal"] = {"type": "boar", "count": 2}
    inst = next(i for i in p["occupations"] if i["id"] == "I252")
    s["round"] = 13
    ctx = {"log": [], "actor": first, "extra": {}}
    cards.CARDS["I252"]["hooks"]["round_start"](s, p, inst, ctx)
    assert p["cells"][4]["animal"]["count"] == 3


def test_pig_catcher_leave_wood_for_boar(engine):
    s = make_state(engine, 4)
    first = s["current_player"]
    put_in_play(s, first, "I253")
    forest = next(sp for sp in s["action_spaces"] if sp["id"] == "forest")
    forest["supply"] = {"wood": 3}
    s = place(engine, s, {"kind": "place", "space": "forest"})
    prompt = s["prompts"][0]
    assert prompt["type"] == "choice"
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {"kind": "choice", "index": 1}).new_state
    p = s["players"][first]
    assert p["resources"]["wood"] == 1  # 3 taken, 2 left behind
    assert s["prompts"][0]["gained"] == {"boar": 1}


def test_groom_card_action(engine):
    s = make_state(engine, 4)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "I254")
    p["house_type"] = "stone"
    give(s, first, wood=2)
    inst = next(i for i in p["occupations"] if i["id"] == "I254")
    assert cards.CARDS["I254"]["card_action"]["available"](s, p, inst)
    pid = p["player_id"]
    s = engine.apply_action(s, pid, {"kind": "card_action", "card": "I254",
                                     "params": {"cell": 0}}).new_state
    p = s["players"][first]
    assert p["cells"][0]["stable"] is True
    assert p["resources"]["wood"] == 1
    inst = next(i for i in p["occupations"] if i["id"] == "I254")
    assert not cards.CARDS["I254"]["card_action"]["available"](s, p, inst)


def test_stone_buyer_first_time_only(engine):
    s = make_state(engine, 4, seed=13)
    first = s["current_player"]
    other = (first + 1) % 4
    put_in_play(s, other, "I255")
    give(s, other, food=3)
    add_space(s, "western_quarry", "Western Quarry", acc=True, supply={"stone": 1})
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {"kind": "place", "space": "western_quarry"}).new_state
    pid2 = s["players"][other]["player_id"]
    s = engine.apply_action(s, pid2, {"kind": "choice", "index": 1}).new_state
    assert s["players"][first]["resources"]["stone"] == 0
    assert s["players"][other]["resources"]["stone"] == 1


def test_stone_carver_conversion(engine):
    s = make_state(engine, 2)
    p = s["players"][0]
    put_in_play(s, 0, "I256")
    opts = cards.conversion_options(p)
    assert any(c["give"] == {"stone": 1} and c["get"] == {"food": 3}
              and c.get("per_harvest") == 1 for _k, c, _i in opts)


def test_street_musician_others_only(engine):
    s = make_state(engine, 4)
    p0 = s["players"][0]
    inst = put_in_play(s, 0, "I257")
    grain_before = p0["resources"]["grain"]
    ctx = {"space_id": "traveling_players", "goods": {"food": 1}, "extra": {},
          "log": [], "actor": 1}
    cards.CARDS["I257"]["hooks"]["space_used"](s, p0, inst, ctx)
    assert p0["resources"]["grain"] == grain_before + 1
    ctx["actor"] = 0
    cards.CARDS["I257"]["hooks"]["space_used"](s, p0, inst, ctx)
    assert p0["resources"]["grain"] == grain_before + 1  # no self-trigger


def test_cabinetmaker_conversion(engine):
    s = make_state(engine, 3)
    p = s["players"][0]
    put_in_play(s, 0, "I258")
    opts = cards.conversion_options(p)
    assert any(c["give"] == {"wood": 1} and c["get"] == {"food": 2}
              and c.get("per_harvest") == 1 for _k, c, _i in opts)


def test_animal_dealer_buys_extra_animal(engine):
    s = make_state(engine, 3)
    first = s["current_player"]
    put_in_play(s, first, "I259")
    give(s, first, food=2)
    add_space(s, "sheep_market", "Sheep Market", acc=True, supply={"sheep": 1})
    s = place(engine, s, {"kind": "place", "space": "sheep_market"})
    prompt = s["prompts"][0]
    assert prompt["type"] == "choice"
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {"kind": "choice", "index": 1}).new_state
    assert s["prompts"][0]["gained"] == {"sheep": 2}


def test_outrider_bonus_on_current_round_space(engine):
    s = make_state(engine, 4)
    first = s["current_player"]
    inst = put_in_play(s, first, "I261")
    p = s["players"][first]
    latest = s["revealed"][-1]
    grain_before = p["resources"]["grain"]
    ctx = {"space_id": latest, "goods": {}, "extra": {}, "log": [], "actor": first}
    cards.CARDS["I261"]["hooks"]["space_used"](s, p, inst, ctx)
    assert p["resources"]["grain"] == grain_before + 1


def test_water_carrier_schedules_after_well_built(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    put_in_play(s, first, "I262")
    ctx = {"improvement": "well", "log": [], "actor": other, "extra": {}}
    p = s["players"][first]
    inst = next(i for i in p["occupations"] if i["id"] == "I262")
    cards.CARDS["I262"]["hooks"]["improvement_built"](s, p, inst, ctx)
    for r in range(2, 15):
        assert s["round_goods"][str(r)][str(first)]["food"] == 1


def test_fencer_grants_wood_for_others_fences(engine):
    s = make_state(engine, 4)
    first = s["current_player"]
    other = (first + 1) % 4
    inst = put_in_play(s, first, "I264")
    p_owner = s["players"][first]
    cards.CARDS["I264"]["hooks"]["play"](s, p_owner, inst, {"log": []})
    p_other = s["players"][other]
    p_other["fences"] = sorted(cell_edges(4))  # 4 fence edges built
    ctx = {"new_pastures": [], "log": [], "actor": other, "extra": {}}
    wood_before = p_owner["resources"]["wood"]
    cards.CARDS["I264"]["hooks"]["fences_built"](s, p_owner, inst, ctx)
    assert p_owner["resources"]["wood"] == wood_before + 1


def test_rancher_most_used_spaces(engine):
    s = make_state(engine, 4)
    p0 = s["players"][0]
    inst = put_in_play(s, 0, "I340")
    p0["cells"][0]["type"] = "field"
    p0["cells"][1]["type"] = "field"
    p0["cells"][2]["type"] = "field"
    wood_before = p0["resources"]["wood"]
    ctx = {"round": s["round"], "log": [], "actor": 0, "extra": {}}
    cards.CARDS["I340"]["hooks"]["round_start"](s, p0, inst, ctx)
    assert p0["resources"]["wood"] == wood_before + 1


def test_gardener_keeps_vegetables_on_the_field(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "I226")
    p["cells"][0]["type"] = "field"
    p["cells"][0]["crops"] = {"type": "vegetable", "count": 2}
    p["cells"][1]["type"] = "field"
    p["cells"][1]["crops"] = {"type": "grain", "count": 2}
    veg_before = p["resources"]["vegetable"]
    grain_before = p["resources"]["grain"]
    log = []
    engine._start_harvest(s, log)
    p = s["players"][first]
    # Vegetables are credited as usual but the field count doesn't drop.
    assert p["resources"]["vegetable"] == veg_before + 1
    assert p["cells"][0]["crops"]["count"] == 2
    # Grain is unaffected -- it still decrements normally.
    assert p["resources"]["grain"] == grain_before + 1
    assert p["cells"][1]["crops"]["count"] == 1


def test_taster_places_first_then_rotation_resumes(engine):
    """I260 Taster, the real card: the _resume_from recipe (decks/
    GUIDE.md's "Turn structure" section; test_first_placer_override_
    full_round_order in tests/test_agricola.py is the temp_card version
    of this same flow). Owner pays the starting player 1 food and places
    first; rotation then resumes from the TRUE starting player."""
    s = make_state(engine, 4, seed=3)
    starting = s["starting_player"]
    owner_idx = next(i for i in range(4) if i != starting)
    put_in_play(s, owner_idx, "I260")
    give(s, owner_idx, food=5)

    owner_food_before = s["players"][owner_idx]["resources"]["food"]
    starting_food_before = s["players"][starting]["resources"]["food"]

    log = []
    engine._start_round(s, log)  # round 2
    assert s["prompts"][0]["type"] == "choice"
    owner_pid = s["players"][owner_idx]["player_id"]
    s = engine.apply_action(
        s, owner_pid, {"kind": "choice", "index": 0}).new_state  # "yes"

    assert s["players"][owner_idx]["resources"]["food"] == owner_food_before - 1
    assert s["players"][starting]["resources"]["food"] == starting_food_before + 1
    assert s["current_player"] == owner_idx

    s = place(engine, s, {"kind": "place", "space": "grain_seeds"})  # owner
    # Rotation resumed from the TRUE starting player, not owner+1.
    assert s["current_player"] == starting


def test_taster_no_advantage_when_already_starting_player(engine):
    """"If you are the starting player yourself, you do not get any
    advantage" -- the round_start hook never offers the choice."""
    s = make_state(engine, 4, seed=3)
    starting = s["starting_player"]
    put_in_play(s, starting, "I260")
    give(s, starting, food=5)

    log = []
    engine._start_round(s, log)
    assert not s["prompts"]


# ── I222 Social Climber ────────────────────────────────────────────────

def test_social_climber_first_to_renovate_gets_three_stone(engine):
    s = make_state(engine, 3)
    owner = 0
    put_in_play(s, owner, "I222")
    p = s["players"][owner]
    give(s, owner, clay=2, reed=1)
    sub_actions.renovate(s, p, [])
    assert p["house_type"] == "clay"
    assert p["resources"]["stone"] == 3


def test_social_climber_third_to_renovate_gets_one_stone(engine):
    s = make_state(engine, 3)
    owner = 0
    put_in_play(s, owner, "I222")
    for other in (1, 2):
        give(s, other, clay=2, reed=1)
        sub_actions.renovate(s, s["players"][other], [])
    p = s["players"][owner]
    give(s, owner, clay=2, reed=1)
    stone_before = p["resources"]["stone"]
    sub_actions.renovate(s, p, [])
    assert p["resources"]["stone"] == stone_before + 1


def test_social_climber_no_double_reward_on_second_renovation(engine):
    """Only the player's FIRST qualifying renovation ranks them -- a
    later clay -> stone renovation by the same player must not re-rank
    them (and must not re-grant a reward)."""
    s = make_state(engine, 3)
    owner = 0
    put_in_play(s, owner, "I222")
    p = s["players"][owner]
    inst = next(i for i in p["occupations"] if i["id"] == "I222")
    give(s, owner, clay=4, reed=2)
    sub_actions.renovate(s, p, [])  # wood -> clay: rank 1, +3 stone
    assert p["resources"]["stone"] == 3
    assert inst["data"]["order"] == [owner]
    give(s, owner, stone=10, reed=2)  # cover the clay -> stone build cost
    stone_before = p["resources"]["stone"]
    sub_actions.renovate(s, p, [])  # clay -> stone: already ranked, no bonus
    assert inst["data"]["order"] == [owner]
    rooms = sum(1 for c in p["cells"] if c["type"] == "room")
    assert p["resources"]["stone"] == stone_before - rooms  # cost only, no bonus


# ── I224 Field Worker ────────────────────────────────────────────────

def test_field_worker_grain_in_three_player_game(engine):
    s = make_state(engine, 3)
    owner, other = 0, 1
    put_in_play(s, owner, "I224")
    s["players"][other]["cells"][0]["type"] = "field"
    give(s, other, grain=1)
    grain_before = s["players"][owner]["resources"]["grain"]
    sub_actions.sow(s, s["players"][other], [{"cell": 0, "crop": "grain"}], [])
    assert s["players"][owner]["resources"]["grain"] == grain_before + 1


def test_field_worker_food_in_four_player_game(engine):
    s = make_state(engine, 4)
    owner, other = 0, 1
    put_in_play(s, owner, "I224")
    s["players"][other]["cells"][0]["type"] = "field"
    give(s, other, grain=1)
    food_before = s["players"][owner]["resources"]["food"]
    sub_actions.sow(s, s["players"][other], [{"cell": 0, "crop": "grain"}], [])
    assert s["players"][owner]["resources"]["food"] == food_before + 1


def test_field_worker_ignores_own_sow(engine):
    s = make_state(engine, 3)
    owner = 0
    put_in_play(s, owner, "I224")
    p = s["players"][owner]
    p["cells"][0]["type"] = "field"
    give(s, owner, grain=1)
    grain_before = p["resources"]["grain"]
    sub_actions.sow(s, p, [{"cell": 0, "crop": "grain"}], [])
    assert p["resources"]["grain"] == grain_before - 1  # spent sowing, no bonus
