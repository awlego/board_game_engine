"""Tests for deck FR occupations (server/agricola/decks/deck_fr_occupations.py).

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
from server.agricola.decks import deck_fr_occupations as deck_fr
from server.agricola.state import (
    cell_edges, animal_counts, compute_pastures, shared_edge,
)


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
            if c["deck"] == "FR" and c["type"] == "occupation"]


def test_registration_completeness():
    db_codes = set(_db_slice())
    registered = {cid for cid in cards.CARDS if cid in db_codes}
    unimplemented = set(deck_fr.UNIMPLEMENTED)
    assert not (registered & unimplemented), \
        "overlap between registered and UNIMPLEMENTED"
    assert registered | unimplemented == db_codes, (
        f"missing: {db_codes - registered - unimplemented}, "
        f"unexpected unimplemented: {unimplemented - db_codes}")


def _implemented_codes():
    db_codes = set(_db_slice())
    return sorted(cid for cid in cards.CARDS
                  if cid in db_codes and cid not in deck_fr.UNIMPLEMENTED)


# Stroller (FR110) requires a 6-item stack via ctx["params"] on play; it
# gets its own dedicated smoke test below instead of the generic loop.
_PARAMS_CODES = {"FR110"}


# ── Smoke test: every implemented card can be played ─────────────────

@pytest.mark.parametrize("code", [c for c in _implemented_codes()
                                  if c not in _PARAMS_CODES])
def test_card_can_be_played(engine, code):
    s = make_state(engine, 2, seed=7)
    first = s["current_player"]
    give_card(s, first, code)
    give(s, first, food=20, wood=20, clay=20, reed=20, stone=20, grain=20,
        vegetable=20)
    pid = s["players"][first]["player_id"]
    s = place(engine, s, {"kind": "place", "space": "lessons", "card": code})
    s = resolve_all_prompts(engine, s, pid)
    p = s["players"][first]
    assert any(i["id"] == code for i in p["occupations"])
    assert code not in p["hand_occupations"]


def test_stroller_can_be_played(engine):
    s = make_state(engine, 2, seed=7)
    first = s["current_player"]
    give_card(s, first, "FR110")
    give(s, first, food=20, wood=20, clay=20, reed=20, stone=20, grain=20,
        vegetable=20)
    pid = s["players"][first]["player_id"]
    stack = ["wood", "clay", "reed", "stone", "wood", "clay"]
    s = place(engine, s, {"kind": "place", "space": "lessons", "card": "FR110",
                          "params": {"stack": stack}})
    s = resolve_all_prompts(engine, s, pid)
    p = s["players"][first]
    assert any(i["id"] == "FR110" for i in p["occupations"])


# ── Targeted effect tests ─────────────────────────────────────────────

def test_animal_welfarist_grants_animals_next_round(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    inst = put_in_play(s, first, "FR062")
    p = s["players"][first]
    ctx = {"actor": first, "log": [], "extra": {}}
    cards.CARDS["FR062"]["hooks"]["stable_built"](s, p, inst, {**ctx, "cells": [4]})
    cards.CARDS["FR062"]["hooks"]["fences_built"](s, p, inst, {**ctx, "new_pastures": [[4]]})
    assert inst["data"]["stable_round"] == s["round"]
    assert inst["data"]["fence_round"] == s["round"]
    round_ctx = {"round": s["round"] + 1, "log": [], "actor": first, "extra": {}}
    cards.CARDS["FR062"]["hooks"]["round_start"](s, p, inst, round_ctx)
    # With no pastures/stables built, only the base house capacity (1 pet)
    # can hold an animal, so exactly the first animal type is placed.
    assert animal_counts(p)["sheep"] == 1
    assert sum(animal_counts(p).values()) == 1


def test_art_director_bonus(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "FR063")
    add_space(s, "traveling_players", "Traveling Players", acc=True,
             supply={"food": 1})
    wood_before = s["players"][first]["resources"]["wood"]
    food_before = s["players"][first]["resources"]["food"]
    s = place(engine, s, {"kind": "place", "space": "traveling_players"})
    prompt = s["prompts"][0]
    assert prompt["type"] == "choice"
    idx = prompt["data"]["goods"].index("wood")
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {"kind": "choice", "index": idx}).new_state
    p = s["players"][first]
    assert p["resources"]["wood"] == wood_before + 1
    assert p["resources"]["food"] == food_before + 1 + 1  # space + bonus


def test_award_winner_pays_for_bonus_point(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "FR064")
    give_card(s, first, "minor_clay_deposit")
    p = s["players"][first]
    p["resources"]["food"] = 2  # cost (1) + the extra bonus payment (1)
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "minor_clay_deposit"}})
    prompt = s["prompts"][0]
    assert prompt["type"] == "choice"
    idx = prompt["data"]["options"].index("food")
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {"kind": "choice", "index": idx + 1}).new_state
    p = s["players"][first]
    assert p["resources"]["food"] == 0
    inst = next(i for i in p["occupations"] if i["id"] == "FR064")
    assert cards.CARDS["FR064"]["score_bonus"](s, p, inst) == 1


def test_benefactor_returns_animal_for_room(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "FR065")
    p = s["players"][first]
    p["fences"] = sorted(set(cell_edges(0)) | set(cell_edges(1))
                         | set(cell_edges(2)) | set(cell_edges(3)))
    assert len(compute_pastures(p)) >= 1
    # Force 4 pastures worth of fencing by faking compute_pastures result
    # is impractical here; instead call the hook directly with a state
    # that already has >=4 pastures via 4 separate 1-cell pastures.
    p["fences"] = sorted(
        set(cell_edges(0)) | set(cell_edges(1)) | set(cell_edges(2))
        | set(cell_edges(3)))
    pastures = compute_pastures(p)
    p["pets"] = {"sheep": 1}
    inst = next(i for i in p["occupations"] if i["id"] == "FR065")
    ctx = {"actor": first, "log": [], "extra": {}, "new_pastures": pastures}
    if len(pastures) < 4:
        pytest.skip("fence layout helper did not yield 4 pastures")
    cards.CARDS["FR065"]["hooks"]["fences_built"](s, p, inst, ctx)
    assert s["prompts"], "expected an animal-return prompt"
    prompt = s["prompts"][-1]
    idx = prompt["options"].index("Return 1 sheep")
    resolve_ctx = {"index": idx, "option": prompt["options"][idx],
                  "data": prompt["data"], "log": [], "actor": first, "extra": {}}
    cards.CARDS["FR065"]["resolve_choice"](s, p, inst, resolve_ctx)
    assert animal_counts(p)["sheep"] == 0
    assert s["prompts"], "expected a chained room-choice prompt"
    room_prompt = s["prompts"][-1]
    resolve_ctx2 = {"index": 0, "option": room_prompt["options"][0],
                   "data": room_prompt["data"], "log": [], "actor": first,
                   "extra": {}}
    cards.CARDS["FR065"]["resolve_choice"](s, p, inst, resolve_ctx2)
    rooms_after = sum(1 for c in p["cells"] if c["type"] == "room")
    assert rooms_after == 3


def test_boatswain_grants_grain_on_fields(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "FR066")
    p = s["players"][first]
    p["cells"][1]["type"] = "field"
    p["cells"][2]["type"] = "field"
    p["cells"][2]["crops"] = {"type": "vegetable", "count": 1}  # not empty
    s = place(engine, s, {"kind": "place", "space": "fishing"})
    p = s["players"][first]
    assert p["cells"][1]["crops"] == {"type": "grain", "count": 1}
    assert p["cells"][2]["crops"] == {"type": "vegetable", "count": 1}


def test_cat_lover_room_discount(engine):
    s = make_state(engine, 2)
    p = s["players"][0]
    put_in_play(s, 0, "FR069")
    p["pets"] = {"sheep": 4}
    cost = cards.modified_cost(s, p, "room", {"wood": 5, "reed": 2})
    assert cost == {"wood": 2, "reed": 2}  # discount 3


def test_cattle_dealer_converts_reed(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "FR070")
    p = s["players"][first]
    give(s, first, reed=1, wood=10)
    add_space(s, "fencing", "Fencing")
    # A 3-cell pasture (0, 1, 2): union of each cell's edges minus the
    # shared internal edges between adjacent cells.
    fences = set(cell_edges(0)) | set(cell_edges(1)) | set(cell_edges(2))
    fences -= {shared_edge(0, 1), shared_edge(1, 2)}
    s = place(engine, s, {"kind": "place", "space": "fencing",
                          "fences": sorted(fences)})
    prompt = s["prompts"][0]
    assert prompt["type"] == "choice"
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {"kind": "choice", "index": 1}).new_state
    assert s["prompts"][0]["gained"] == {"cattle": 2}
    s = engine.apply_action(s, pid, {"kind": "accommodate", "placements": [],
                                     "discard": {"cattle": 2}}).new_state
    assert s["players"][first]["resources"]["reed"] == 0


def test_child_care_worker_other_family_growth(engine):
    s = make_state(engine, 2)
    p0 = s["players"][0]
    put_in_play(s, 0, "FR071")
    inst = next(i for i in p0["occupations"] if i["id"] == "FR071")
    p0["resources"]["wood"] = 1
    ctx = {"actor": 1, "log": [], "extra": {}}
    cards.CARDS["FR071"]["hooks"]["family_growth"](s, p0, inst, ctx)
    assert s["prompts"]
    prompt = s["prompts"][-1]
    resolve_ctx = {"index": 1, "option": prompt["options"][1],
                  "data": prompt["data"], "log": [], "actor": 1, "extra": {}}
    cards.CARDS["FR071"]["resolve_choice"](s, p0, inst, resolve_ctx)
    assert p0["resources"]["wood"] == 0
    assert p0["resources"]["food"] == 2 + 2  # starting food + conversion


def test_convict_reserves_slot_and_scores(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give_card(s, first, "FR073")
    s = place(engine, s, {"kind": "place", "space": "lessons", "card": "FR073"})
    p = s["players"][first]
    inst = next(i for i in p["occupations"] if i["id"] == "FR073")
    assert inst["data"]["played_round"] == 1
    round_ctx = {"round": 2, "log": [], "actor": first, "extra": {}}
    p["people_placed"] = 0
    cards.CARDS["FR073"]["hooks"]["round_start"](s, p, inst, round_ctx)
    assert p["people_placed"] == 1
    assert cards.CARDS["FR073"]["score_bonus"](s, p, inst) == 2 * 14


def test_drinker_of_absinthe_schedules_food(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give_card(s, first, "FR079")
    s = place(engine, s, {"kind": "place", "space": "lessons", "card": "FR079"})
    for r in range(7, 15):
        assert s["round_goods"][str(r)][str(first)]["food"] == 1
    assert "6" not in s["round_goods"] or str(first) not in s["round_goods"].get("6", {})


def test_fencing_master_banks_and_spends(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    inst = put_in_play(s, first, "FR080")
    p = s["players"][first]
    ctx = {"actor": first, "log": [], "extra": {}}
    cards.CARDS["FR080"]["hooks"]["occupation_played"](s, p, inst, ctx)
    assert inst["data"]["banked"] == 2
    cost = cards.modified_cost(s, p, "fences", {"wood": 4}, {"count": 4})
    assert cost == {"wood": 2}
    assert inst["data"]["_pending_discount"] == 2
    fences_ctx = {"actor": first, "log": [], "extra": {}, "new_pastures": []}
    cards.CARDS["FR080"]["hooks"]["fences_built"](s, p, inst, fences_ctx)
    assert inst["data"]["banked"] == 0
    assert "_pending_discount" not in inst["data"]


def test_gardening_enthusiast_loan_and_repay(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give_card(s, first, "FR082")
    grain_before = s["players"][first]["resources"]["grain"]
    s = place(engine, s, {"kind": "place", "space": "lessons", "card": "FR082"})
    p = s["players"][first]
    assert p["resources"]["grain"] == grain_before + 1
    inst = next(i for i in p["occupations"] if i["id"] == "FR082")
    assert cards.CARDS["FR082"]["score_bonus"](s, p, inst) == -2
    ctx = {"log": [], "actor": first, "params": {"good": "grain"}, "extra": {}}
    cards.CARDS["FR082"]["card_action"]["apply"](s, p, inst, ctx)
    assert p["resources"]["grain"] == grain_before
    assert cards.CARDS["FR082"]["score_bonus"](s, p, inst) == -1


def test_good_friend_releases_resource(engine):
    s = make_state(engine, 2)
    p0 = s["players"][0]
    put_in_play(s, 0, "FR083")
    inst = next(i for i in p0["occupations"] if i["id"] == "FR083")
    ctx = {"actor": 0, "log": [], "extra": {}}
    cards.CARDS["FR083"]["hooks"]["play"](s, p0, inst, ctx)
    assert inst["data"]["wood"] == 3
    minor_ctx = {"actor": 1, "card_id": "minor_clay_deposit", "log": [],
                "extra": {}}
    cards.CARDS["FR083"]["hooks"]["minor_played"](s, p0, inst, minor_ctx)
    assert not s["prompts"]  # clay_deposit costs food, not a tracked good

    minor_ctx2 = {"actor": 1, "card_id": "minor_canoe", "log": [], "extra": {}}
    cards.CARDS["FR083"]["hooks"]["minor_played"](s, p0, inst, minor_ctx2)
    assert s["prompts"]
    prompt = s["prompts"][-1]
    idx = prompt["data"]["goods"].index("wood")
    resolve_ctx = {"index": idx + 1, "option": prompt["options"][idx + 1],
                  "data": prompt["data"], "log": [], "actor": 0, "extra": {}}
    cards.CARDS["FR083"]["resolve_choice"](s, p0, inst, resolve_ctx)
    assert inst["data"]["wood"] == 2
    assert p0["resources"]["wood"] == 1


def test_grain_speculator_schedules_grain(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give_card(s, first, "FR084")
    s = place(engine, s, {"kind": "place", "space": "lessons", "card": "FR084"})
    for r in (2, 4, 6):
        assert s["round_goods"][str(r)][str(first)]["grain"] == 1


def test_immigrants_son_plows_on_fifth_occupation(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    inst = put_in_play(s, first, "FR087")
    p = s["players"][first]
    p["occs_played"] = 5
    ctx = {"actor": first, "card_id": "occ_woodcutter", "log": [], "extra": {}}
    cards.CARDS["FR087"]["hooks"]["occupation_played"](s, p, inst, ctx)
    assert s["prompts"]
    prompt = s["prompts"][-1]
    resolve_ctx = {"index": 1, "option": prompt["options"][1],
                  "data": prompt["data"], "log": [], "actor": first, "extra": {}}
    cards.CARDS["FR087"]["resolve_choice"](s, p, inst, resolve_ctx)
    cell = prompt["data"]["cells"][0]
    assert p["cells"][cell]["type"] == "field"


def test_lemon_trader_card_action(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    inst = put_in_play(s, first, "FR090")
    p = s["players"][first]
    give(s, first, grain=1, vegetable=1)
    assert cards.CARDS["FR090"]["card_action"]["available"](s, p, inst)
    ctx = {"log": [], "actor": first, "extra": {},
          "params": {"give": "grain", "resources": ["wood"]}}
    cards.CARDS["FR090"]["card_action"]["apply"](s, p, inst, ctx)
    assert p["resources"]["grain"] == 0
    assert p["resources"]["wood"] == 1
    ctx2 = {"log": [], "actor": first, "extra": {},
           "params": {"give": "vegetable", "resources": ["clay", "stone"]}}
    cards.CARDS["FR090"]["card_action"]["apply"](s, p, inst, ctx2)
    assert p["resources"]["vegetable"] == 0
    assert p["resources"]["clay"] == 1
    assert p["resources"]["stone"] == 1
    assert inst["data"]["used"] == 2
    assert not cards.CARDS["FR090"]["card_action"]["available"](s, p, inst)


def test_martial_artist_discards_up_to_two(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    inst = put_in_play(s, first, "FR092")
    p = s["players"][first]
    p["hand_minors"] = ["minor_clay_deposit", "minor_canoe", "minor_basket"]
    hand_before = len(p["hand_minors"])
    food_before = p["resources"]["food"]
    ctx = {"harvest_index": 1, "log": [], "actor": first, "extra": {}}
    cards.CARDS["FR092"]["hooks"]["harvest_field"](s, p, inst, ctx)
    assert s["prompts"]
    prompt = s["prompts"][-1]
    resolve_ctx = {"index": 1, "option": prompt["options"][1],
                  "data": prompt["data"], "log": [], "actor": first, "extra": {}}
    cards.CARDS["FR092"]["resolve_choice"](s, p, inst, resolve_ctx)
    assert s["prompts"], "expected a second discard offer"
    prompt2 = s["prompts"][-1]
    resolve_ctx2 = {"index": 1, "option": prompt2["options"][1],
                   "data": prompt2["data"], "log": [], "actor": first, "extra": {}}
    cards.CARDS["FR092"]["resolve_choice"](s, p, inst, resolve_ctx2)
    assert p["resources"]["food"] == food_before + 4
    assert inst["data"]["discarded_this_harvest"] == 2
    assert len(p["hand_minors"]) == hand_before - 2


def test_mastermind_counts_bonus_cards(engine):
    s = make_state(engine, 2)
    p0 = s["players"][0]
    put_in_play(s, 0, "FR093")
    inst = next(i for i in p0["occupations"] if i["id"] == "FR093")
    ctx = {"actor": 0, "card_id": "minor_basket", "log": [], "extra": {}}
    cards.CARDS["FR093"]["hooks"]["minor_played"](s, p0, inst, ctx)
    ctx2 = {"actor": 0, "improvement": "well", "log": [], "extra": {}}
    cards.CARDS["FR093"]["hooks"]["improvement_built"](s, p0, inst, ctx2)
    assert cards.CARDS["FR093"]["score_bonus"](s, p0, inst) == 2


def test_oceanographer_stack_on_plow(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    inst = put_in_play(s, first, "FR096")
    p = s["players"][first]
    ctx = {"cell": 1, "log": [], "actor": first, "extra": {}}
    cards.CARDS["FR096"]["hooks"]["plow"](s, p, inst, ctx)
    prompt = s["prompts"][-1]
    resolve_ctx = {"index": 1, "option": prompt["options"][1],
                  "data": prompt["data"], "log": [], "actor": first, "extra": {}}
    cards.CARDS["FR096"]["resolve_choice"](s, p, inst, resolve_ctx)
    cards.add_goods(p["resources"], resolve_ctx["extra"])  # engine normally merges this
    assert p["resources"]["wood"] == 1
    assert inst["data"]["index"] == 1


def test_parquet_setter_reduces_unused(engine):
    s = make_state(engine, 2)
    p = s["players"][0]
    inst = put_in_play(s, 0, "FR097")
    ctx1 = {"round": 1, "log": [], "actor": 0, "extra": {}}
    cards.CARDS["FR097"]["hooks"]["round_start"](s, p, inst, ctx1)
    p["cells"][1]["type"] = "field"
    p["cells"][2]["type"] = "field"
    wood_before = p["resources"]["wood"]
    food_before = p["resources"]["food"]
    ctx2 = {"round": 2, "log": [], "actor": 0, "extra": {}}
    cards.CARDS["FR097"]["hooks"]["round_start"](s, p, inst, ctx2)
    assert p["resources"]["wood"] == wood_before + 1
    assert p["resources"]["food"] == food_before + 1  # reduced by 2+


def test_pear_peeler_leaves_wood_for_crops(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "FR099")
    wood_before = s["players"][first]["resources"]["wood"]
    s = place(engine, s, {"kind": "place", "space": "forest"})  # 3 wood
    prompt = s["prompts"][0]
    assert prompt["type"] == "choice"
    idx = next(i for i, c in enumerate(prompt["data"]["choices"]) if c[0] == 3)
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {"kind": "choice", "index": idx + 1}).new_state
    p = s["players"][first]
    assert p["resources"]["wood"] == wood_before  # took 3, left all 3 back
    assert p["resources"]["grain"] == 1
    assert p["resources"]["vegetable"] == 1
    forest = next(sp for sp in s["action_spaces"] if sp["id"] == "forest")
    assert forest["supply"]["wood"] == 3


def test_pipe_smoker_harvest_wood(engine):
    s = make_state(engine, 2)
    p = s["players"][0]
    inst = put_in_play(s, 0, "FR100")
    p["cells"][1]["type"] = "field"
    p["cells"][1]["crops"] = {"type": "grain", "count": 1}
    wood_before = p["resources"]["wood"]
    ctx = {"harvest_index": 1, "log": [], "actor": 0, "extra": {}}
    cards.CARDS["FR100"]["hooks"]["harvest_field"](s, p, inst, ctx)
    assert p["resources"]["wood"] == wood_before + 2


def test_powerhouse_round_start(engine):
    s = make_state(engine, 2)
    p = s["players"][0]
    inst = put_in_play(s, 0, "FR101")
    p["resources"]["stone"] = 3
    food_before = p["resources"]["food"]
    ctx = {"round": 2, "log": [], "actor": 0, "extra": {}}
    cards.CARDS["FR101"]["hooks"]["round_start"](s, p, inst, ctx)
    assert p["resources"]["food"] == food_before + 1
    p["resources"]["stone"] = 5
    cards.CARDS["FR101"]["hooks"]["round_start"](s, p, inst, ctx)
    assert p["resources"]["food"] == food_before + 1 + 2


def test_prosecutor_improvement_discount(engine):
    s = make_state(engine, 2)
    p0 = s["players"][0]
    p1 = s["players"][1]
    put_in_play(s, 0, "FR103")
    p1["improvements"] = ["fireplace_2", "well"]
    cost = cards.modified_cost(s, p0, "improvement", {"clay": 2, "stone": 3})
    assert cost == {"clay": 2, "stone": 3}  # only 1 other player has more


def test_racing_stable_manager_chain(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "FR104")
    give(s, first, wood=2, food=1)
    food_before = s["players"][first]["resources"]["food"]
    add_space(s, "farm_expansion", "Farm Expansion")
    free = [i for i, c in enumerate(s["players"][first]["cells"])
           if c["type"] == "empty" and not c["stable"]]
    s = place(engine, s, {"kind": "place", "space": "farm_expansion",
                          "stables": [free[0]]})
    prompt = s["prompts"][0]
    assert prompt["type"] == "choice"
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {"kind": "choice", "index": 1}).new_state
    prompt2 = s["prompts"][0]
    assert prompt2["type"] == "choice"
    cell = prompt2["data"]["cells"][0]
    s = engine.apply_action(s, pid, {"kind": "choice", "index": 0}).new_state
    p = s["players"][first]
    assert p["cells"][cell]["type"] == "field"
    assert p["resources"]["food"] == food_before - 1


def test_sailboat_constructor_buys_multiple_stacks(engine):
    s = make_state(engine, 2)
    p = s["players"][0]
    inst = put_in_play(s, 0, "FR106")
    p["resources"]["food"] = 10
    ctx = {"harvest_index": 1, "log": [], "actor": 0, "extra": {}}
    cards.CARDS["FR106"]["hooks"]["harvest_field"](s, p, inst, ctx)
    prompt = s["prompts"][-1]
    resolve_ctx = {"index": 1, "option": prompt["options"][1],
                  "data": prompt["data"], "log": [], "actor": 0, "extra": {}}
    cards.CARDS["FR106"]["resolve_choice"](s, p, inst, resolve_ctx)
    assert p["resources"]["wood"] == 3
    assert s["prompts"], "expected the next stack to be offered"
    prompt2 = s["prompts"][-1]
    resolve_ctx2 = {"index": 1, "option": prompt2["options"][1],
                   "data": prompt2["data"], "log": [], "actor": 0, "extra": {}}
    cards.CARDS["FR106"]["resolve_choice"](s, p, inst, resolve_ctx2)
    assert p["resources"]["clay"] == 3
    assert inst["data"]["bought"] == 2
    assert p["resources"]["food"] == 10 - 2 - 3


def test_sculptors_son_joinery_bonus(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "FR107")
    p = s["players"][first]
    p["improvements"] = ["joinery"]
    wood_before = p["resources"]["wood"]
    s = place(engine, s, {"kind": "place", "space": "forest"})  # only wood
    p = s["players"][first]
    assert p["resources"]["wood"] == wood_before + 3 + 2


def test_shovel_worker_pasture_food(engine):
    s = make_state(engine, 2)
    p = s["players"][0]
    inst = put_in_play(s, 0, "FR108")
    p["fences"] = sorted(
        set(cell_edges(0)) | set(cell_edges(1)) | set(cell_edges(3))
        | set(cell_edges(4)))
    n = len(compute_pastures(p))
    food_before = p["resources"]["food"]
    ctx = {"harvest_index": 1, "log": [], "actor": 0, "extra": {}}
    cards.CARDS["FR108"]["hooks"]["harvest_field"](s, p, inst, ctx)
    expected = 4 if n >= 5 else 3 if n >= 4 else 2 if n >= 3 else 1 if n >= 2 else 0
    assert p["resources"]["food"] == food_before + expected


def test_stage_star_stage1_wood_and_pays_traveler(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give_card(s, first, "FR109")
    wood_before = s["players"][first]["resources"]["wood"]
    s = place(engine, s, {"kind": "place", "space": "lessons", "card": "FR109"})
    p = s["players"][first]
    assert p["resources"]["wood"] == wood_before + 6
    inst = next(i for i in p["occupations"] if i["id"] == "FR109")
    other_idx = 1 - first
    other = s["players"][other_idx]
    ctx = {"space_id": "traveling_players", "actor": other_idx, "log": [],
          "extra": {}, "goods": {"food": 1}}
    wood_before2 = p["resources"]["wood"]
    other_wood_before = other["resources"]["wood"]
    cards.CARDS["FR109"]["hooks"]["space_used"](s, p, inst, ctx)
    assert p["resources"]["wood"] == wood_before2 - 1
    assert other["resources"]["wood"] == other_wood_before + 1


def test_stroller_stack_on_harvest(engine):
    s = make_state(engine, 2)
    p = s["players"][0]
    inst = put_in_play(s, 0, "FR110")
    ctx = {"params": {"stack": ["wood", "clay", "reed", "stone", "wood", "clay"]},
          "log": [], "actor": 0, "extra": {}}
    cards.CARDS["FR110"]["hooks"]["play"](s, p, inst, ctx)
    wood_before = p["resources"]["wood"]
    harvest_ctx = {"harvest_index": 1, "log": [], "actor": 0, "extra": {}}
    cards.CARDS["FR110"]["hooks"]["harvest_field"](s, p, inst, harvest_ctx)
    assert p["resources"]["wood"] == wood_before + 1
    assert inst["data"]["stack"] == ["clay", "reed", "stone", "wood", "clay"]

    with pytest.raises(ValueError):
        bad_ctx = {"params": {"stack": ["wood"] * 6}, "log": [], "actor": 0,
                  "extra": {}}
        cards.CARDS["FR110"]["hooks"]["play"](s, p, cards.new_instance("FR110"),
                                              bad_ctx)


def test_sun_farmer_double_bonus(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "FR111")
    grain_before = s["players"][first]["resources"]["grain"]
    add_space(s, "sheep_market", "Sheep Market", acc=True, supply={"sheep": 1})
    s["current_player"] = first
    s["players"][first]["people_placed"] = 0
    s = place(engine, s, {"kind": "place", "space": "sheep_market"})
    # Grain is a non-animal good, so it's applied immediately; only the
    # sheep goes through the accommodation prompt.
    assert s["prompts"][0]["gained"] == {"sheep": 1}
    p = s["players"][first]
    assert p["resources"]["grain"] == grain_before + 1


def test_tower_builder_free_room(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "FR112")
    p = s["players"][first]
    other = s["players"][1 - first]
    other["cells"][0]["type"] = "empty"
    other["cells"][1]["type"] = "room"  # only 1 room for the other player
    give(s, first, wood=5, reed=2)
    add_space(s, "farm_expansion", "Farm Expansion")
    free = [i for i, c in enumerate(p["cells"])
           if c["type"] == "empty" and not c["stable"]
           and any(nb in (5, 10) for nb in
                   __import__("server.agricola.state", fromlist=["orthogonal_neighbors"])
                   .orthogonal_neighbors(i))]
    s = place(engine, s, {"kind": "place", "space": "farm_expansion",
                          "rooms": [free[0]]})
    prompt = s["prompts"][0]
    assert prompt["type"] == "choice"
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {"kind": "choice", "index": 1}).new_state
    p = s["players"][first]
    rooms = sum(1 for c in p["cells"] if c["type"] == "room")
    assert rooms == 4  # 2 starting + 1 built + 1 free


def test_turkey_breeder_free_fences_and_returns_wood(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give_card(s, first, "FR114")
    p = s["players"][first]
    free_cell = next(i for i, c in enumerate(p["cells"])
                     if c["type"] == "empty" and not c["stable"])
    s = place(engine, s, {"kind": "place", "space": "lessons", "card": "FR114",
                          "params": {"fences": cell_edges(4),
                                    "stable_cell": free_cell}})
    p = s["players"][first]
    assert p["cells"][free_cell]["stable"] is True
    assert len(p["fences"]) == len(cell_edges(4))
    inst = next(i for i in p["occupations"] if i["id"] == "FR114")
    assert inst["data"]["owed_wood"] == 4
    give(s, first, wood=2)
    begging_before = p["begging"]
    ctx = {"harvest_index": 6, "log": [], "actor": first, "extra": {}}
    cards.CARDS["FR114"]["hooks"]["harvest_field"](s, p, inst, ctx)
    assert p["resources"]["wood"] == 0
    assert p["begging"] == begging_before + 2


def test_village_druid_grants_sheep(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    inst = put_in_play(s, first, "FR116")
    p = s["players"][first]
    ctx = {"actor": first, "log": [], "extra": {}}
    cards.CARDS["FR116"]["hooks"]["occupation_played"](s, p, inst, ctx)
    assert ctx["extra"] == {"sheep": 1}


def test_wealthiest_european_grants_resources(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    s["round"] = 4
    give_card(s, first, "FR117")
    s = place(engine, s, {"kind": "place", "space": "lessons", "card": "FR117"})
    prompt = s["prompts"][0]
    assert prompt["type"] == "choice"
    idx = prompt["data"]["goods"].index("stone")
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {"kind": "choice", "index": idx}).new_state
    p = s["players"][first]
    assert p["resources"]["stone"] == 3  # completed rounds = 4 - 1


def test_workaholic_claims_bank(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give_card(s, first, "FR119")
    s = place(engine, s, {"kind": "place", "space": "lessons", "card": "FR119"})
    p = s["players"][first]
    inst = next(i for i in p["occupations"] if i["id"] == "FR119")
    assert inst["data"]["bank"] == {"wood": 5, "clay": 4, "stone": 3}
    assert not cards.CARDS["FR119"]["card_action"]["available"](s, p, inst)
    p["pets"] = {"sheep": 7}  # 2p/3p threshold is 7
    assert cards.CARDS["FR119"]["card_action"]["available"](s, p, inst)
    ctx = {"log": [], "actor": first, "extra": {}}
    cards.CARDS["FR119"]["card_action"]["apply"](s, p, inst, ctx)
    assert p["resources"]["wood"] == 5
    assert p["resources"]["clay"] == 4
    assert p["resources"]["stone"] == 3
    assert not cards.CARDS["FR119"]["card_action"]["available"](s, p, inst)


def test_card_player_bonus_malus_and_rotation(engine):
    """FR068: bottom-to-top stack starts stone/reed/clay/wood, so top=wood
    (bonus), bottom=stone (malus). returning_home rotates top->bottom."""
    s = make_state(engine, 1)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "FR068")
    wood_before = p["resources"]["wood"]
    stone_before = p["resources"]["stone"]
    add_space(s, "wood_test_fr068", "Wood Test", acc=True, supply={"wood": 2})
    add_space(s, "stone_test_fr068", "Stone Test", acc=True, supply={"stone": 1})
    # Top (wood) matches -> +1 bonus wood.
    s = place(engine, s, {"kind": "place", "space": "wood_test_fr068"})
    p = s["players"][first]
    assert p["resources"]["wood"] == wood_before + 2 + 1  # space + bonus
    # Bottom (stone) matches -> -1 malus stone. This is the 1-player
    # game's 2nd (last) placement of the round, so it also drives the
    # round to completion -- returning_home fires and rotates the stack
    # before this call returns.
    s = place(engine, s, {"kind": "place", "space": "stone_test_fr068"})
    p = s["players"][first]
    assert p["resources"]["stone"] == stone_before + 1 - 1  # space, minus malus
    assert s["round"] == 2

    inst = next(i for i in p["occupations"] if i["id"] == "FR068")
    # Rotated once: [wood, stone, reed, clay] -> top=clay, bottom=wood.
    assert inst["data"]["stack"] == ["wood", "stone", "reed", "clay"]


def test_pasteurization_expert_ignores_breeding_source(engine):
    """FR098: top-to-bottom draw order sheep/boar/sheep/cattle, and only
    fires outside the breeding phase (source == "breeding" is exactly
    that). Exercised by calling the registered gained hook directly, the
    same way FR119's card_action is tested elsewhere in this file."""
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    inst = put_in_play(s, first, "FR098")
    hook = cards.CARDS["FR098"]["hooks"]["gained"]

    ctx = {"goods": {"sheep": 1}, "source": "breeding", "log": [], "extra": {}}
    hook(s, p, inst, ctx)
    assert inst["data"].get("idx", 0) == 0  # pile untouched
    assert ctx["extra"] == {}

    ctx2 = {"goods": {"sheep": 1}, "source": "space", "log": [], "extra": {}}
    hook(s, p, inst, ctx2)
    assert ctx2["extra"] == {"sheep": 1}  # matches the top ("sheep")
    assert inst["data"]["idx"] == 1


def test_miser_discount_only_exactly_one_room_via_a_space(engine):
    """FR094: 1 wood + 1 reed off, but only for a single-room build made
    through an action space (ctx["space_id"] set, ctx["count"] == 1)."""
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "FR094")

    cell = sub_actions.buildable_room_cells(p)[0]
    give(s, first, wood=5, reed=2)
    s = place(engine, s, {"kind": "place", "space": "farm_expansion",
                          "rooms": [cell]})
    p = s["players"][first]
    # Normal cost is 5 wood + 2 reed; Miser knocks 1 off each.
    assert p["resources"]["wood"] == 1 and p["resources"]["reed"] == 1

    # No discount for a 2-room batch, or for a card-driven build with no
    # originating space (ctx["space_id"] absent).
    cost_batch = cards.modified_cost(s, p, "room", {"wood": 10, "reed": 4},
                                     {"count": 2, "space_id": "farm_expansion"})
    assert cost_batch == {"wood": 10, "reed": 4}
    cost_no_space = cards.modified_cost(s, p, "room", {"wood": 5, "reed": 2},
                                        {"count": 1})
    assert cost_no_space == {"wood": 5, "reed": 2}


def test_reformer_holds_1_any_animal_per_occupation(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "occ_woodcutter")
    inst = put_in_play(s, first, "FR105")
    assert len(p["occupations"]) == 2

    inst["held"] = {"sheep": 1, "boar": 1}
    ok, err = cards.validate_held(s, p)
    assert ok, err

    inst["held"] = {"sheep": 2, "boar": 1}
    ok, err = cards.validate_held(s, p)
    assert not ok
