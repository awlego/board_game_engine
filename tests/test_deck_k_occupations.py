"""Tests for deck K occupations (server/agricola/decks/deck_k_occupations.py).

Helper functions below are copied from tests/test_agricola.py (make_state,
give, give_card, put_in_play, add_space, place, current_pid) and
tests/test_deck_b_occupations.py (resolve_all_prompts) per decks/GUIDE.md's
testing guidance.
"""

import json
import os
import random

import pytest

from server.agricola.engine import AgricolaEngine
from server.agricola import cards
from server.agricola.decks import deck_k_occupations as deck_k
from server.agricola.state import cell_edges, animal_counts, compute_pastures


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
        actual_pid = state["players"][prompt["player"]]["player_id"]
        if prompt["type"] == "choice":
            state = engine.apply_action(
                state, actual_pid, {"kind": "choice", "index": 0}).new_state
        elif prompt["type"] == "accommodate":
            gained = prompt["gained"]
            state = engine.apply_action(state, actual_pid, {
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
            if c["deck"] == "K" and c["type"] == "occupation"]


def test_registration_completeness():
    db_codes = set(_db_slice())
    registered = {cid for cid in cards.CARDS if cid in db_codes}
    unimplemented = set(deck_k.UNIMPLEMENTED)
    assert not (registered & unimplemented), "overlap between registered and UNIMPLEMENTED"
    assert registered | unimplemented == db_codes, (
        f"missing: {db_codes - registered - unimplemented}, "
        f"unexpected unimplemented: {unimplemented - db_codes}")


def _implemented_codes():
    db_codes = set(_db_slice())
    return sorted(cid for cid in cards.CARDS
                  if cid in db_codes and cid not in deck_k.UNIMPLEMENTED)


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

def test_serf_choice_of_grain_or_vegetable(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "K266")
    add_space(s, "grain_utilization", "Sow and/or Bake Bread")
    p = s["players"][first]
    p["cells"][6]["type"] = "field"
    give(s, first, grain=1)
    s = place(engine, s, {"kind": "place", "space": "grain_utilization",
                          "sow": [{"cell": 6, "crop": "grain"}]})
    prompt = s["prompts"][0]
    assert prompt["type"] == "choice"
    pid = p["player_id"]
    s = engine.apply_action(s, pid, {"kind": "choice", "index": 1}).new_state
    p = s["players"][first]
    assert p["resources"]["vegetable"] == 1


def test_adoptive_parents_places_offspring_immediately(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "K267")
    p = s["players"][first]
    p["cells"][0]["type"] = "room"  # 3rd room, enough headroom
    give(s, first, food=5)
    add_space(s, "basic_wish", "Basic Wish for Children")
    people_before = p["people_total"]
    placed_before = p["people_placed"]
    food_before = p["resources"]["food"]
    s = place(engine, s, {"kind": "place", "space": "basic_wish"})
    prompt = s["prompts"][0]
    assert prompt["type"] == "choice"
    pid = p["player_id"]
    s = engine.apply_action(s, pid, {"kind": "choice", "index": 1}).new_state
    p = s["players"][first]
    assert p["people_total"] == people_before + 1
    assert p["newborns"] == 0
    # people_placed only advanced by the 1 for the actual placement (the
    # "does not act this round" penalty was undone).
    assert p["people_placed"] == placed_before + 1
    assert p["resources"]["food"] == food_before - 1


def test_pieceworker_buys_extra_goods(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "K268")
    give(s, first, food=10)
    forest = next(sp for sp in s["action_spaces"] if sp["id"] == "forest")
    forest["supply"] = {"wood": 3}
    p = s["players"][first]
    wood_before = p["resources"]["wood"]
    food_before = p["resources"]["food"]
    s = place(engine, s, {"kind": "place", "space": "forest"})
    prompt = s["prompts"][0]
    assert prompt["type"] == "choice"
    assert prompt["data"]["good"] == "wood"
    pid = p["player_id"]
    s = engine.apply_action(s, pid, {"kind": "choice", "index": 1}).new_state
    p = s["players"][first]
    assert p["resources"]["wood"] == wood_before + 3 + 1
    assert p["resources"]["food"] == food_before - 1


def test_wet_nurse_family_growth_on_rooms(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "K270")
    p = s["players"][first]
    give(s, first, wood=10, reed=4, food=3)
    people_before = p["people_total"]
    food_before = p["resources"]["food"]
    s = place(engine, s, {"kind": "place", "space": "farm_expansion",
                          "rooms": [0]})
    prompt = s["prompts"][0]
    assert prompt["type"] == "choice"
    pid = p["player_id"]
    s = engine.apply_action(s, pid, {"kind": "choice", "index": 1}).new_state
    p = s["players"][first]
    assert p["people_total"] == people_before + 1
    assert p["resources"]["food"] == food_before - 1


def test_educator_reacts_to_other_player(engine):
    s = make_state(engine, 2)
    actor = s["current_player"]
    owner = 1 - actor
    put_in_play(s, owner, "K271")
    give_card(s, actor, "occ_woodcutter")
    give_card(s, owner, "occ_fisherman")
    give(s, actor, food=5)
    give(s, owner, food=10)
    food_before = s["players"][owner]["resources"]["food"]
    s = place(engine, s, {"kind": "place", "space": "lessons",
                          "card": "occ_woodcutter"})
    prompt = s["prompts"][0]
    assert prompt["type"] == "choice"
    assert prompt["player"] == owner
    idx = prompt["options"].index("Fisherman")
    owner_pid = s["players"][owner]["player_id"]
    s = engine.apply_action(s, owner_pid,
                            {"kind": "choice", "index": idx}).new_state
    p = s["players"][owner]
    assert any(i["id"] == "occ_fisherman" for i in p["occupations"])
    assert p["resources"]["food"] == food_before - 3


def test_frame_builder_room_cost_swap(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "K272")
    p = s["players"][first]
    give(s, first, clay=10, reed=4, wood=10)
    clay_before = p["resources"]["clay"]
    wood_before = p["resources"]["wood"]
    s = place(engine, s, {"kind": "place", "space": "farm_expansion",
                          "rooms": [0]})
    p = s["players"][first]
    # Normal wood-house room cost is {wood:5, reed:2}; house_type here is
    # "wood" so the clay/stone swap doesn't trigger. Verify no crash and
    # normal cost still applies (Frame Builder targets clay/stone houses).
    assert p["resources"]["clay"] == clay_before
    assert p["resources"]["wood"] == wood_before - 5


def test_frame_builder_renovation_cost_swap(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "K272")
    p = s["players"][first]
    p["house_type"] = "wood"
    give(s, first, clay=10, reed=4, wood=4)
    add_space(s, "house_redevelopment", "House Redevelopment")
    clay_before = p["resources"]["clay"]
    wood_before = p["resources"]["wood"]
    reed_before = p["resources"]["reed"]
    s = place(engine, s, {"kind": "place", "space": "house_redevelopment"})
    p = s["players"][first]
    # Renovation wood->clay normally costs {clay: rooms, reed: 1}; Frame
    # Builder replaces 1 clay with 1 wood.
    rooms = sum(1 for c in p["cells"] if c["type"] == "room")
    assert p["resources"]["clay"] == clay_before - (rooms - 1)
    assert p["resources"]["wood"] == wood_before - 1
    assert p["resources"]["reed"] == reed_before - 1


def test_organic_farmer_score(engine):
    s = make_state(engine, 2)
    p = s["players"][0]
    inst = put_in_play(s, 0, "K274")
    p["fences"] = sorted(cell_edges(4))
    p["cells"][4]["animal"] = {"type": "sheep", "count": 1}
    bonus = cards.CARDS["K274"]["score_bonus"](s, p, inst)
    # A single fenced cell (no stable) holds 2; occupied by 1, spare = 1
    # (< 3), so no bonus point yet.
    assert bonus == 0


def test_constable_score_and_wood(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    inst = put_in_play(s, first, "K276")
    p = s["players"][first]
    s["round"] = 7  # remaining = 14-7 = 7, in the 6..8 -> 3 wood tier
    wood_before = p["resources"]["wood"]
    ctx = {"log": [], "actor": first, "extra": {}}
    cards.CARDS["K276"]["hooks"]["play"](s, p, inst, ctx)
    assert p["resources"]["wood"] == wood_before + 3

    # A default fresh board scores -1 in most (empty) categories, so the
    # Constable's "no negative points" bonus should NOT apply yet.
    assert cards.CARDS["K276"]["score_bonus"](s, p, inst) == 0

    # Fill out every scoring category to be non-negative (rooms 5/10 are
    # the starting rooms; pasture at cell 2; unfenced stables with 1
    # animal each at 3/4/8; every other cell becomes a field so nothing
    # is left "unused").
    p["fences"] = sorted(cell_edges(2))
    for i in (3, 4, 8):
        p["cells"][i]["stable"] = True
    p["cells"][3]["animal"] = {"type": "sheep", "count": 1}
    p["cells"][4]["animal"] = {"type": "boar", "count": 1}
    p["cells"][8]["animal"] = {"type": "cattle", "count": 1}
    for i in (0, 1, 6, 7, 9, 11, 12, 13, 14):
        p["cells"][i]["type"] = "field"
    give(s, first, grain=1, vegetable=1)
    assert cards.CARDS["K276"]["score_bonus"](s, p, inst) == 5


def test_forester_stack_and_harvest(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    inst = put_in_play(s, first, "K278")
    p = s["players"][first]
    give(s, first, wood=3)
    p["cells"][6]["type"] = "field"
    give(s, first, grain=1)
    add_space(s, "grain_utilization", "Sow and/or Bake Bread")
    s = place(engine, s, {"kind": "place", "space": "grain_utilization",
                          "sow": [{"cell": 6, "crop": "grain"}]})
    prompt = s["prompts"][0]
    assert prompt["type"] == "choice"
    assert "Start stack 1" in prompt["options"][1]
    pid = p["player_id"]
    s = engine.apply_action(s, pid, {"kind": "choice", "index": 1}).new_state
    p = s["players"][first]
    inst = next(i for i in p["occupations"] if i["id"] == "K278")
    assert inst["data"]["stacks"][0] == 1
    ctx = {"log": [], "actor": first, "extra": {}}
    cards.CARDS["K278"]["hooks"]["harvest_field"](s, p, inst, ctx)
    assert inst["data"]["stacks"][0] == 0
    assert p["resources"]["wood"] == 2 + 1  # 3 - 1(stack) + 1(harvested)


def test_scholar_card_action_plays_occupation(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    inst = put_in_play(s, first, "K279")
    p = s["players"][first]
    p["house_type"] = "stone"
    give_card(s, first, "occ_woodcutter")
    give(s, first, food=5)
    food_before = p["resources"]["food"]
    assert cards.CARDS["K279"]["card_action"]["available"](s, p, inst)
    ctx = {"log": [], "actor": first, "extra": {},
          "params": {"kind": "occupation", "card": "occ_woodcutter"}}
    cards.CARDS["K279"]["card_action"]["apply"](s, p, inst, ctx)
    p = s["players"][first]
    assert any(i["id"] == "occ_woodcutter" for i in p["occupations"])
    assert p["resources"]["food"] == food_before - 1
    assert not cards.CARDS["K279"]["card_action"]["available"](s, p, inst)


def test_house_steward_reuses_base_helpers(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    inst = put_in_play(s, first, "K282")
    p = s["players"][first]
    s["round"] = 1
    wood_before = p["resources"]["wood"]
    ctx = {"log": [], "actor": first, "extra": {}}
    cards.CARDS["K282"]["hooks"]["play"](s, p, inst, ctx)
    assert p["resources"]["wood"] == wood_before + 4  # < round 3 tier
    other = s["players"][1 - first]
    other["cells"][0]["type"] = "room"
    other["cells"][1]["type"] = "room"
    bonus = cards.CARDS["K282"]["score_bonus"](s, other, None)
    assert bonus == 3  # most rooms


def test_wood_deliveryman_schedule(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give_card(s, first, "K283")
    s = place(engine, s, {"kind": "place", "space": "lessons",
                          "card": "K283"})
    for r in range(8, 15):
        assert s["round_goods"][str(r)][str(first)]["wood"] == 1
    assert "7" not in s["round_goods"] or str(first) not in \
        s["round_goods"].get("7", {})


def test_wood_distributor_redistributes(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    inst = put_in_play(s, first, "K284")
    p = s["players"][first]
    forest = next(sp for sp in s["action_spaces"] if sp["id"] == "forest")
    forest["supply"]["wood"] = 7
    clay_pit = next(sp for sp in s["action_spaces"] if sp["id"] == "clay_pit")
    ctx = {"log": [], "actor": first, "extra": {}}
    cards.CARDS["K284"]["hooks"]["round_start"](s, p, inst, ctx)
    assert forest["supply"]["wood"] == 1  # 7 % 3
    assert clay_pit["supply"]["wood"] == 2  # 7 // 3


def test_tinsmith_convert_clay(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    inst = put_in_play(s, first, "K285")
    p = s["players"][first]
    give(s, first, clay=4)
    food_before = p["resources"]["food"]
    assert cards.CARDS["K285"]["card_action"]["available"](s, p, inst)
    ctx = {"log": [], "actor": first, "extra": {}, "params": {"clay": 4}}
    cards.CARDS["K285"]["card_action"]["apply"](s, p, inst, ctx)
    assert p["resources"]["clay"] == 0
    assert p["resources"]["food"] == food_before + 4  # no well: 1:1


def test_tinsmith_convert_clay_with_well(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    inst = put_in_play(s, first, "K285")
    p = s["players"][first]
    p["improvements"] = ["well"]
    give(s, first, clay=4)
    food_before = p["resources"]["food"]
    ctx = {"log": [], "actor": first, "extra": {}, "params": {"clay": 4}}
    cards.CARDS["K285"]["card_action"]["apply"](s, p, inst, ctx)
    assert p["resources"]["clay"] == 0
    assert p["resources"]["food"] == food_before + 6  # 2 pairs * 3


def test_smallholder_sow_bonus(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "K286")
    p = s["players"][first]
    p["cells"][6]["type"] = "field"
    give(s, first, grain=1)
    add_space(s, "grain_utilization", "Sow and/or Bake Bread")
    s = place(engine, s, {"kind": "place", "space": "grain_utilization",
                          "sow": [{"cell": 6, "crop": "grain"}]})
    p = s["players"][first]
    assert p["cells"][6]["crops"]["count"] == 4  # 3 + 1 bonus


def test_storehouse_clerk_thresholds(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    inst = put_in_play(s, first, "K287")
    p = s["players"][first]
    p["resources"]["stone"] = 5
    p["resources"]["wood"] = 8
    ctx = {"log": [], "actor": first, "extra": {}}
    cards.CARDS["K287"]["hooks"]["round_start"](s, p, inst, ctx)
    assert p["resources"]["stone"] == 6
    assert p["resources"]["wood"] == 9
    assert p["resources"]["clay"] == 0
    assert p["resources"]["reed"] == 0


def test_storehouse_keeper_choice(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "K288")
    add_space(s, "resource_market_4p", "Resource Market")
    p = s["players"][first]
    grain_before = p["resources"]["grain"]
    s = place(engine, s, {"kind": "place", "space": "resource_market_4p"})
    prompt = s["prompts"][0]
    assert prompt["type"] == "choice"
    pid = p["player_id"]
    s = engine.apply_action(s, pid, {"kind": "choice", "index": 1}).new_state
    p = s["players"][first]
    assert p["resources"]["grain"] == grain_before + 1
    assert p["resources"]["clay"] == 0


def test_clay_worker_take_bonus(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "K290")
    clay_before = s["players"][first]["resources"]["clay"]
    s = place(engine, s, {"kind": "place", "space": "clay_pit"})
    p = s["players"][first]
    assert p["resources"]["clay"] == clay_before + 1 + 1


def test_lover_family_growth_without_room(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give_card(s, first, "K291")
    give(s, first, food=10)
    p = s["players"][first]
    people_before = p["people_total"]
    food_before = p["resources"]["food"]
    s = place(engine, s, {"kind": "place", "space": "lessons",
                          "card": "K291"})
    p = s["players"][first]
    assert p["people_total"] == people_before + 1
    assert p["resources"]["food"] == food_before - 4  # 0 occ cost (first) + 4


def test_market_woman_space_bonus(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "K292")
    add_space(s, "vegetable_seeds", "Vegetable Seeds")
    grain_before = s["players"][first]["resources"]["grain"]
    s = place(engine, s, {"kind": "place", "space": "vegetable_seeds"})
    p = s["players"][first]
    assert p["resources"]["grain"] == grain_before + 2
    assert p["resources"]["vegetable"] == 1


def test_ploughman_scheduled_auto_plow(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    inst = put_in_play(s, first, "K293")
    p = s["players"][first]
    ctx = {"log": [], "actor": first, "extra": {}}
    cards.CARDS["K293"]["hooks"]["play"](s, p, inst, ctx)
    # state["round"] is already 1 by the time a card could be played.
    assert inst["data"]["rounds"] == [5, 8, 11]
    give(s, first, food=3)
    food_before = p["resources"]["food"]
    fields_before = sum(1 for c in p["cells"] if c["type"] == "field")
    s["round"] = 5
    cards.CARDS["K293"]["hooks"]["round_start"](s, p, inst, ctx)
    p = s["players"][first]
    fields_after = sum(1 for c in p["cells"] if c["type"] == "field")
    assert fields_after == fields_before + 1
    assert p["resources"]["food"] == food_before - 1


def test_brushwood_collector_renovation(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "K294")
    p = s["players"][first]
    give(s, first, clay=10, wood=4)
    add_space(s, "house_redevelopment", "House Redevelopment")
    reed_before = p["resources"]["reed"]
    wood_before = p["resources"]["wood"]
    s = place(engine, s, {"kind": "place", "space": "house_redevelopment"})
    p = s["players"][first]
    assert p["resources"]["reed"] == reed_before
    assert p["resources"]["wood"] == wood_before - 1


def test_cattle_breeder_round13_breeding(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    inst = put_in_play(s, first, "K295")
    p = s["players"][first]
    p["fences"] = sorted(cell_edges(4)) + sorted(cell_edges(5))
    p["cells"][4]["animal"] = {"type": "cattle", "count": 1}
    p["cells"][5]["animal"] = {"type": "cattle", "count": 1}
    s["round"] = 13
    ctx = {"log": [], "actor": first, "extra": {}}
    cards.CARDS["K295"]["hooks"]["round_start"](s, p, inst, ctx)
    total = animal_counts(p)["cattle"]
    assert total == 3


def test_seed_seller_bonus_and_play(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give_card(s, first, "K296")
    grain_before = s["players"][first]["resources"]["grain"]
    s = place(engine, s, {"kind": "place", "space": "lessons",
                          "card": "K296"})
    p = s["players"][first]
    assert p["resources"]["grain"] == grain_before + 1  # on-play


def test_seed_seller_space_bonus_on_grain_seeds(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "K296")
    grain_before = s["players"][first]["resources"]["grain"]
    s = place(engine, s, {"kind": "place", "space": "grain_seeds"})
    p = s["players"][first]
    assert p["resources"]["grain"] == grain_before + 1 + 1


def test_sheep_farmer_take_bonus_and_exchange(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    inst = put_in_play(s, first, "K297")
    p = s["players"][first]
    p["pets"] = {"sheep": 3}
    assert cards.CARDS["K297"]["card_action"]["available"](s, p, inst)
    ctx = {"log": [], "actor": first, "extra": {}}
    cards.CARDS["K297"]["card_action"]["apply"](s, p, inst, ctx)
    assert animal_counts(p)["sheep"] == 0
    assert ctx["extra"] == {"cattle": 1, "boar": 1}


def test_shepherd_boy_schedules_when_stone(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    inst = put_in_play(s, first, "K298")
    p = s["players"][first]
    p["house_type"] = "stone"
    s["round"] = 3
    ctx = {"log": [], "actor": first, "extra": {}}
    cards.CARDS["K298"]["hooks"]["play"](s, p, inst, ctx)
    assert inst["data"]["rounds"][0] == 4
    s["round"] = 4
    cards.CARDS["K298"]["hooks"]["round_start"](s, p, inst, ctx)
    assert animal_counts(p)["sheep"] == 1


def test_schnaps_distiller_conversion_registered(engine):
    spec = cards.CARDS["K300"]
    assert spec["conversions"] == [
        {"give": {"vegetable": 1}, "get": {"food": 5}, "per_harvest": 1}]


def test_pig_whisperer_scheduled_boar(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    inst = put_in_play(s, first, "K302")
    p = s["players"][first]
    s["round"] = 2
    ctx = {"log": [], "actor": first, "extra": {}}
    cards.CARDS["K302"]["hooks"]["play"](s, p, inst, ctx)
    assert inst["data"]["rounds"] == [6, 9, 12]
    s["round"] = 6
    cards.CARDS["K302"]["hooks"]["round_start"](s, p, inst, ctx)
    assert animal_counts(p)["boar"] == 1


def test_stone_breaker_renovates_anytime(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    inst = put_in_play(s, first, "K303")
    p = s["players"][first]
    p["house_type"] = "clay"
    give(s, first, stone=5, reed=2)
    assert cards.CARDS["K303"]["card_action"]["available"](s, p, inst)
    ctx = {"log": [], "actor": first, "extra": {}}
    cards.CARDS["K303"]["card_action"]["apply"](s, p, inst, ctx)
    assert p["house_type"] == "stone"


def test_veterinarian_matching_pair(engine, monkeypatch):
    s = make_state(engine, 2)
    first = s["current_player"]
    inst = put_in_play(s, first, "K304")
    p = s["players"][first]
    monkeypatch.setattr(deck_k.random, "sample", lambda pool, k: ["sheep", "sheep"])
    ctx = {"log": [], "actor": first, "extra": {}}
    cards.CARDS["K304"]["hooks"]["round_start"](s, p, inst, ctx)
    assert animal_counts(p)["sheep"] == 1


def test_veterinarian_no_match(engine, monkeypatch):
    s = make_state(engine, 2)
    first = s["current_player"]
    inst = put_in_play(s, first, "K304")
    p = s["players"][first]
    monkeypatch.setattr(deck_k.random, "sample", lambda pool, k: ["sheep", "boar"])
    ctx = {"log": [], "actor": first, "extra": {}}
    cards.CARDS["K304"]["hooks"]["round_start"](s, p, inst, ctx)
    assert animal_counts(p)["sheep"] == 0
    assert animal_counts(p)["boar"] == 0


def test_animal_handler_auto_buy(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    inst = put_in_play(s, first, "K305")
    p = s["players"][first]
    give(s, first, food=3)
    food_before = p["resources"]["food"]
    s["round"] = 7
    ctx = {"log": [], "actor": first, "extra": {}}
    cards.CARDS["K305"]["hooks"]["round_start"](s, p, inst, ctx)
    assert animal_counts(p)["sheep"] == 1
    assert p["resources"]["food"] == food_before - 1


def test_animal_tamer_house_capacity(engine):
    s = make_state(engine, 2)
    p = s["players"][0]
    put_in_play(s, 0, "K306")
    p["cells"][0]["type"] = "room"
    p["cells"][1]["type"] = "room"
    assert cards.house_capacity(p) == max(1, sum(
        1 for c in p["cells"] if c["type"] == "room"))


def test_animal_breeder_buys_pair(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "K307")
    p = s["players"][first]
    give(s, first, wood=8, food=3)
    add_space(s, "fencing", "Fencing")
    s = place(engine, s, {"kind": "place", "space": "fencing",
                          "fences": sorted(cell_edges(4))})
    prompt = s["prompts"][0]
    assert prompt["type"] == "choice"
    pid = p["player_id"]
    s = engine.apply_action(s, pid, {"kind": "choice", "index": 1}).new_state
    # Place both sheep in the pasture we just fenced (cell 4, capacity 2).
    s = engine.apply_action(s, pid, {
        "kind": "accommodate",
        "placements": [{"cell": 4, "type": "sheep", "count": 2}],
        "discard": {},
    }).new_state
    p = s["players"][first]
    assert animal_counts(p)["sheep"] == 2


def test_weaver_round_income(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    inst = put_in_play(s, first, "K309")
    p = s["players"][first]
    p["pets"] = {"sheep": 2}
    food_before = p["resources"]["food"]
    ctx = {"log": [], "actor": first, "extra": {}}
    cards.CARDS["K309"]["hooks"]["round_start"](s, p, inst, ctx)
    assert p["resources"]["food"] == food_before + 1


def test_magician_last_person_bonus(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "K311")
    p = s["players"][first]
    add_space(s, "traveling_players", "Traveling Players", acc=True,
             supply={"food": 1})
    p["people_placed"] = p["people_total"] - 1
    grain_before = p["resources"]["grain"]
    s = place(engine, s, {"kind": "place", "space": "traveling_players"})
    p = s["players"][first]
    assert p["resources"]["grain"] == grain_before + 1


def test_fence_overseer_free_fence(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "K312")
    p = s["players"][first]
    give(s, first, wood=8, food=2)
    food_before = p["resources"]["food"]
    s = place(engine, s, {"kind": "place", "space": "farm_expansion",
                          "stables": [4]})
    prompt = s["prompts"][0]
    assert prompt["type"] == "choice"
    pid = p["player_id"]
    s = engine.apply_action(s, pid, {"kind": "choice", "index": 1}).new_state
    p = s["players"][first]
    assert set(cell_edges(4)) <= set(p["fences"])
    assert p["resources"]["food"] == food_before - 1


def test_animal_trainer_buys_from_traveling_players(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "K342")
    add_space(s, "traveling_players", "Traveling Players", acc=True,
             supply={"food": 3})
    p = s["players"][first]
    food_before = p["resources"]["food"]
    s = place(engine, s, {"kind": "place", "space": "traveling_players"})
    prompt = s["prompts"][0]
    assert prompt["type"] == "choice"
    idx = next(i for i, o in enumerate(prompt["options"]) if "cattle" in o)
    pid = p["player_id"]
    s = engine.apply_action(s, pid, {"kind": "choice", "index": idx}).new_state
    assert s["prompts"][0]["gained"] == {"cattle": 1}
    s = engine.apply_action(s, pid, {
        "kind": "accommodate", "placements": [], "discard": {"cattle": 1},
    }).new_state
    p = s["players"][first]
    assert p["resources"]["food"] == food_before + 3 - 3
