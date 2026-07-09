"""Tests for the deck A occupations module (server/agricola/decks/deck_a_occupations.py)."""

import json
import os

import pytest

from server.agricola.engine import AgricolaEngine
from server.agricola import cards
from server.agricola.decks import deck_a_occupations
from server.agricola.state import (
    cell_edges, compute_pastures, create_player,
)

from test_agricola import make_state, give, give_card, put_in_play, add_space, place


@pytest.fixture
def engine():
    return AgricolaEngine()


def resolve_prompts(engine, state, player_id):
    """Resolve every pending prompt: choices with option 0 (always the
    'Decline'/no-op option in this deck), accommodate prompts by
    discarding whatever was gained."""
    guard = 0
    while state.get("prompts"):
        guard += 1
        assert guard < 20, "prompt loop did not terminate"
        prompt = state["prompts"][0]
        if prompt["type"] == "choice":
            state = engine.apply_action(
                state, player_id, {"kind": "choice", "index": 0}).new_state
        elif prompt["type"] == "accommodate":
            state = engine.apply_action(state, player_id, {
                "kind": "accommodate", "placements": [],
                "discard": prompt["gained"],
            }).new_state
        else:
            break
    return state


def play_occupation_fresh(engine, cid, seed=7):
    """Fresh state sized for this card's min_players; give it plenty of
    resources, then play it via the Lessons/Lessons B space."""
    n = max(2, cards.CARDS[cid]["min_players"])
    s = make_state(engine, n, seed=seed)
    first = s["current_player"]
    give_card(s, first, cid)
    give(s, first, food=20, wood=20, clay=20, reed=20, stone=20, grain=20,
         vegetable=20)
    space = "lessons" if n <= 2 else "lessons_b"
    s = place(engine, s, {"kind": "place", "space": space, "card": cid})
    pid = s["players"][first]["player_id"]
    s = resolve_prompts(engine, s, pid)
    return s, first


# ── Registration completeness ────────────────────────────────────────

def test_registration_completeness():
    db_path = os.path.join(os.path.dirname(cards.__file__), "data",
                           "compendium_cards.json")
    with open(db_path) as f:
        db = json.load(f)
    slice_codes = {c["code"] for c in db
                   if c["deck"] == "A" and c["type"] == "occupation"}
    registered = {cid for cid, spec in cards.CARDS.items()
                  if spec["deck"] == "A" and spec["type"] == "occupation"}
    unimplemented = set(deck_a_occupations.UNIMPLEMENTED)

    assert registered & unimplemented == set(), \
        "cards registered AND marked unimplemented"
    assert slice_codes == registered | unimplemented, \
        "every deck-A occupation must be registered xor unimplemented"


# ── Smoke test: every implemented card can be played ─────────────────

IMPLEMENTED_CODES = sorted(
    cid for cid, spec in cards.CARDS.items()
    if spec["deck"] == "A" and spec["type"] == "occupation")


@pytest.mark.parametrize("cid", IMPLEMENTED_CODES)
def test_every_card_can_be_played(engine, cid):
    s, first = play_occupation_fresh(engine, cid)
    p = s["players"][first]
    assert any(i["id"] == cid for i in p["occupations"])
    assert cid not in p["hand_occupations"]
    assert s["prompts"] == []


# ── Targeted effect tests ─────────────────────────────────────────────

def test_stable_planner_free_stable(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "A089")
    s["round"] = 3
    p = s["players"][first]
    empty_cell = next(i for i, c in enumerate(p["cells"])
                      if c["type"] == "empty" and not c["stable"])
    pid = p["player_id"]
    s = engine.apply_action(s, pid, {
        "kind": "card_action", "card": "A089",
        "params": {"cell": empty_cell}}).new_state
    p = s["players"][first]
    assert p["cells"][empty_cell]["stable"]
    assert p["resources"]["wood"] == 0
    inst = next(i for i in p["occupations"] if i["id"] == "A089")
    assert inst["data"]["built"] == [3]


def test_shifting_cultivator_prompt(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "A091")
    give(s, first, food=3)
    food_before = s["players"][first]["resources"]["food"]
    s = place(engine, s, {"kind": "place", "space": "forest"})
    prompt = s["prompts"][0]
    idx = prompt["options"].index("Plow cell 0")
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {"kind": "choice", "index": idx}).new_state
    p = s["players"][first]
    assert p["cells"][0]["type"] == "field"
    assert p["resources"]["food"] == food_before - 3


def test_bed_maker_family_growth(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "A093")
    give(s, first, wood=11, reed=4, grain=1)
    people_before = s["players"][first]["people_total"]
    s = place(engine, s, {"kind": "place", "space": "farm_expansion",
                          "rooms": [0, 1]})
    prompt = s["prompts"][0]
    assert prompt["type"] == "choice"
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {"kind": "choice", "index": 1}).new_state
    p = s["players"][first]
    assert p["people_total"] == people_before + 1
    assert p["newborns"] == 1
    assert p["resources"]["wood"] == 0 and p["resources"]["grain"] == 0


def test_fellow_grazer_score():
    p = create_player(0, "p", "P")
    fences = set()
    for c in (3, 4, 8, 9):
        fences |= set(cell_edges(c))
    fences -= {"v-0-4", "v-1-4", "h-1-3", "h-1-4"}
    p["fences"] = sorted(fences)
    assert compute_pastures(p) == [[3, 4, 8, 9]]
    fn = cards.CARDS["A099"]["score_bonus"]
    assert fn(None, p, None) == 2


def test_cookery_outfitter_score():
    p = create_player(0, "p", "P")
    p["improvements"] = ["fireplace_2", "clay_oven", "well"]
    fn = cards.CARDS["A101"]["score_bonus"]
    assert fn(None, p, None) == 1


def test_portmonger_food_bonus(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "A103")
    fishing = next(sp for sp in s["action_spaces"] if sp["id"] == "fishing")
    fishing["supply"] = {"food": 2}
    s = place(engine, s, {"kind": "place", "space": "fishing"})
    assert s["players"][first]["resources"]["grain"] == 1


def test_wood_harvester_harvest(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "A104")
    forest = next(sp for sp in s["action_spaces"] if sp["id"] == "forest")
    forest["supply"] = {"wood": 2}
    wood_before = s["players"][first]["resources"]["wood"]
    log = []
    engine._start_harvest(s, log)
    assert s["players"][first]["resources"]["wood"] == wood_before + 1


def test_barrow_pusher_plow(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "A105")
    clay_before = s["players"][first]["resources"]["clay"]
    food_before = s["players"][first]["resources"]["food"]
    s = place(engine, s, {"kind": "place", "space": "farmland", "cell": 0})
    p = s["players"][first]
    assert p["cells"][0]["type"] == "field"
    assert p["resources"]["clay"] == clay_before + 1
    assert p["resources"]["food"] == food_before + 1


def test_slurry_spreader_harvest(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "A106")
    p = s["players"][first]
    p["cells"][0]["type"] = "field"
    p["cells"][0]["crops"] = {"type": "grain", "count": 1}
    p["cells"][1]["type"] = "field"
    p["cells"][1]["crops"] = {"type": "vegetable", "count": 1}
    log = []
    cards.fire_player(s, p, "round_start",
                      {"round": s["round"], "log": log, "actor": p["index"],
                       "extra": {}})
    food_before = p["resources"]["food"]
    engine._start_harvest(s, log)
    p = s["players"][first]
    assert p["resources"]["food"] == food_before + 3  # 2 (grain) + 1 (veg)


def test_catcher_first_placement_bonus(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "A107")
    forest = next(sp for sp in s["action_spaces"] if sp["id"] == "forest")
    forest["supply"] = {"wood": 5}
    food_before = s["players"][first]["resources"]["food"]
    s = place(engine, s, {"kind": "place", "space": "forest"})
    assert s["players"][first]["resources"]["food"] == food_before + 1


def test_chief_forester_sow_prompt(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "A115")
    p = s["players"][first]
    p["cells"][0]["type"] = "field"
    give(s, first, grain=1)
    s = place(engine, s, {"kind": "place", "space": "forest"})
    prompt = s["prompts"][0]
    idx = prompt["options"].index("Sow grain on cell 0")
    pid = p["player_id"]
    s = engine.apply_action(s, pid, {"kind": "choice", "index": idx}).new_state
    p = s["players"][first]
    assert p["cells"][0]["crops"] == {"type": "grain", "count": 3}
    assert p["resources"]["grain"] == 0


def test_wood_carrier_play(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["improvements"] = ["well", "joinery"]
    p["minors"].append(cards.new_instance("minor_basket"))
    give_card(s, first, "A117")
    give(s, first, food=5)
    wood_before = p["resources"]["wood"]
    s = place(engine, s, {"kind": "place", "space": "lessons", "card": "A117"})
    p = s["players"][first]
    assert p["resources"]["wood"] == wood_before + 3


def test_treegardener_buy_wood(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "A118")
    p = s["players"][first]
    log = []
    cards.fire_player(s, p, "harvest_field",
                      {"harvest_index": 1, "log": log, "actor": first,
                       "extra": {}})
    give(s, first, food=5)
    s["phase"] = "feeding"
    pid = p["player_id"]
    wood_before = p["resources"]["wood"]
    food_before = p["resources"]["food"]
    s = engine.apply_action(s, pid, {"kind": "card_action", "card": "A118"}).new_state
    p = s["players"][first]
    assert p["resources"]["wood"] == wood_before + 1
    assert p["resources"]["food"] == food_before - 1


def test_clay_puncher_play_and_lessons(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give_card(s, first, "A121")
    give(s, first, food=5)
    clay_before = s["players"][first]["resources"]["clay"]
    s = place(engine, s, {"kind": "place", "space": "lessons", "card": "A121"})
    p = s["players"][first]
    # Play bonus (+1 clay) and lessons-space trigger (+1 clay).
    assert p["resources"]["clay"] == clay_before + 2


def test_pan_baker_space_bonus(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "A122")
    add_space(s, "grain_utilization", "Grain Utilization")
    p = s["players"][first]
    p["cells"][0]["type"] = "field"
    give(s, first, grain=1)
    clay_before = p["resources"]["clay"]
    wood_before = p["resources"]["wood"]
    s = place(engine, s, {"kind": "place", "space": "grain_utilization",
                          "sow": [{"cell": 0, "crop": "grain"}]})
    p = s["players"][first]
    assert p["resources"]["clay"] == clay_before + 2
    assert p["resources"]["wood"] == wood_before + 1


def test_knapper_reveal_window(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "A124")
    s["revealed"] = ["x1", "x2", "x3", "x4", "sheep_market", "x6", "x7"]
    add_space(s, "sheep_market", "Sheep Market", acc=True, supply={"sheep": 1})
    stone_before = s["players"][first]["resources"]["stone"]
    s = place(engine, s, {"kind": "place", "space": "sheep_market"})
    assert s["players"][first]["resources"]["stone"] == stone_before + 1


def test_master_workman_reveal_mapping(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "A126")
    s["revealed"] = ["sheep_market"]
    add_space(s, "sheep_market", "Sheep Market", acc=True, supply={"sheep": 1})
    wood_before = s["players"][first]["resources"]["wood"]
    s = place(engine, s, {"kind": "place", "space": "sheep_market"})
    assert s["players"][first]["resources"]["wood"] == wood_before + 1


def test_craft_teacher_free_occupation(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "A131")
    p = s["players"][first]
    give_card(s, first, "occ_woodcutter")
    log = []
    engine._fire(s, "improvement_built", p, {"improvement": "joinery"}, log)
    pid = p["player_id"]
    s = engine.apply_action(s, pid, {
        "kind": "card_action", "card": "A131",
        "params": {"card": "occ_woodcutter"}}).new_state
    p = s["players"][first]
    assert any(i["id"] == "occ_woodcutter" for i in p["occupations"])
    assert "occ_woodcutter" not in p["hand_occupations"]
    inst = next(i for i in p["occupations"] if i["id"] == "A131")
    assert inst["data"]["credits"] == 1


def test_full_farmer_score():
    p = create_player(0, "p", "P")
    p["fences"] = sorted(cell_edges(4))
    p["cells"][4]["animal"] = {"type": "sheep", "count": 2}  # capacity 2 -> full
    fn = cards.CARDS["A134"]["score_bonus"]
    assert fn(None, p, None) == 1


def test_reeve_play_wood_scaling(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give_card(s, first, "A135")
    give(s, first, food=5)
    wood_before = s["players"][first]["resources"]["wood"]
    s = place(engine, s, {"kind": "place", "space": "lessons", "card": "A135"})
    assert s["players"][first]["resources"]["wood"] == wood_before + 4


def test_animal_reeve_score():
    p = create_player(0, "p", "P")
    p["pets"] = {"sheep": 3, "boar": 3, "cattle": 3}
    fn = cards.CARDS["A135"]["score_bonus"]
    assert fn(None, p, None) == 3


def test_drudgery_reeve_score():
    p = create_player(0, "p", "P")
    p["resources"].update({"wood": 2, "clay": 2, "reed": 2, "stone": 2})
    fn = cards.CARDS["A136"]["score_bonus"]
    assert fn(None, p, None) == 3


def test_riverine_shepherd_pair(engine):
    s = make_state(engine, 3, seed=3)
    first = s["current_player"]
    put_in_play(s, first, "A137")
    add_space(s, "sheep_market", "Sheep Market", acc=True, supply={"sheep": 1})
    reed_bank = next(sp for sp in s["action_spaces"] if sp["id"] == "reed_bank")
    reed_bank["supply"] = {"reed": 3}
    s = place(engine, s, {"kind": "place", "space": "sheep_market"})
    p = s["players"][first]
    assert p["resources"]["reed"] == 1
    reed_bank2 = next(sp for sp in s["action_spaces"] if sp["id"] == "reed_bank")
    assert reed_bank2["supply"]["reed"] == 2


def test_hollow_warden_builds_fireplace(engine):
    s = make_state(engine, 3, seed=5)
    first = s["current_player"]
    give_card(s, first, "A139")
    give(s, first, food=5, clay=5)
    s = place(engine, s, {"kind": "place", "space": "lessons_b", "card": "A139"})
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {"kind": "choice", "index": 1}).new_state
    p = s["players"][first]
    assert any(imp in p["improvements"] for imp in ("fireplace_2", "fireplace_3"))


def test_shovel_bearer_other_space(engine):
    s = make_state(engine, 3, seed=8)
    first = s["current_player"]
    put_in_play(s, first, "A140")
    clay_pit = next(sp for sp in s["action_spaces"] if sp["id"] == "clay_pit")
    clay_pit["supply"] = {"clay": 1}
    hollow = next(sp for sp in s["action_spaces"] if sp["id"] == "hollow_3p")
    hollow["supply"] = {"clay": 3}
    food_before = s["players"][first]["resources"]["food"]
    s = place(engine, s, {"kind": "place", "space": "clay_pit"})
    assert s["players"][first]["resources"]["food"] == food_before + 3


def test_cordmaker_any_player_trigger(engine):
    s = make_state(engine, 3, seed=11)
    first = s["current_player"]
    other = (first + 1) % 3
    put_in_play(s, other, "A142")
    reed_bank = next(sp for sp in s["action_spaces"] if sp["id"] == "reed_bank")
    reed_bank["supply"] = {"reed": 2}
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {"kind": "place", "space": "reed_bank"}).new_state
    prompt = s["prompts"][0]
    assert prompt["player"] == other
    pid2 = s["players"][other]["player_id"]
    s = engine.apply_action(s, pid2, {"kind": "choice", "index": 1}).new_state
    assert s["players"][other]["resources"]["grain"] == 1


def test_sequestrator_first_to_pastures(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    put_in_play(s, first, "A144")
    p = s["players"][other]
    fences = set()
    for c in (0, 2, 4):
        fences |= set(cell_edges(c))
    p["fences"] = sorted(fences)
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {"kind": "place", "space": "day_laborer"}).new_state
    assert s["players"][other]["resources"]["reed"] == 3


def test_ropemaker_harvest(engine):
    s = make_state(engine, 3, seed=13)
    first = s["current_player"]
    put_in_play(s, first, "A145")
    reed_before = s["players"][first]["resources"]["reed"]
    log = []
    engine._start_harvest(s, log)
    assert s["players"][first]["resources"]["reed"] == reed_before + 1


def test_storehouse_steward_food_bonus(engine):
    s = make_state(engine, 3, seed=17)
    first = s["current_player"]
    put_in_play(s, first, "A146")
    fishing = next(sp for sp in s["action_spaces"] if sp["id"] == "fishing")
    fishing["supply"] = {"food": 4}
    clay_before = s["players"][first]["resources"]["clay"]
    s = place(engine, s, {"kind": "place", "space": "fishing"})
    assert s["players"][first]["resources"]["clay"] == clay_before + 1


def test_pig_owner_threshold_score():
    inst = cards.new_instance("A153")
    p = create_player(0, "p", "P")
    state = {}
    play_fn = cards.CARDS["A153"]["hooks"]["play"]
    space_fn = cards.CARDS["A153"]["hooks"]["space_used"]
    score_fn = cards.CARDS["A153"]["score_bonus"]
    log = []
    play_fn(state, p, inst, {"log": log, "actor": 0, "extra": {}, "params": {}})
    assert score_fn(state, p, inst) == 0
    p["pets"] = {"boar": 5}
    space_fn(state, p, inst, {"log": log, "actor": 0, "extra": {}, "goods": {},
                             "space_id": "forest"})
    assert score_fn(state, p, inst) == 3


def test_paymaster_trade_bonus(engine):
    s = make_state(engine, 4, seed=23)
    first = s["current_player"]
    other = (first + 1) % 4
    put_in_play(s, other, "A154")
    give(s, other, grain=2)
    fishing = next(sp for sp in s["action_spaces"] if sp["id"] == "fishing")
    fishing["supply"] = {"food": 1}
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {"kind": "place", "space": "fishing"}).new_state
    prompt = s["prompts"][0]
    assert prompt["player"] == other
    pid2 = s["players"][other]["player_id"]
    s = engine.apply_action(s, pid2, {"kind": "choice", "index": 1}).new_state
    assert s["players"][first]["resources"]["grain"] == 1
    assert s["players"][other]["resources"]["grain"] == 1
    inst = next(i for i in s["players"][other]["occupations"] if i["id"] == "A154")
    assert inst["data"]["bonus"] == 1


def test_buyer_trade(engine):
    s = make_state(engine, 4, seed=29)
    first = s["current_player"]
    other = (first + 1) % 4
    put_in_play(s, other, "A156")
    give(s, other, food=2)
    reed_bank = next(sp for sp in s["action_spaces"] if sp["id"] == "reed_bank")
    reed_bank["supply"] = {"reed": 1}
    food_first_before = s["players"][first]["resources"]["food"]
    food_other_before = s["players"][other]["resources"]["food"]
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {"kind": "place", "space": "reed_bank"}).new_state
    pid2 = s["players"][other]["player_id"]
    s = engine.apply_action(s, pid2, {"kind": "choice", "index": 1}).new_state
    assert s["players"][other]["resources"]["reed"] == 1
    assert s["players"][other]["resources"]["food"] == food_other_before - 1
    assert s["players"][first]["resources"]["food"] == food_first_before + 1


def test_culinary_artist_exchange(engine):
    s = make_state(engine, 4, seed=31)
    first = s["current_player"]
    other = (first + 1) % 4
    put_in_play(s, other, "A158")
    give(s, other, grain=1)
    tp = next(sp for sp in s["action_spaces"] if sp["id"] == "traveling_players")
    tp["supply"] = {"food": 1}
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {"kind": "place",
                                     "space": "traveling_players"}).new_state
    prompt = s["prompts"][0]
    idx = prompt["options"].index("Exchange 1 grain for 4 food")
    pid2 = s["players"][other]["player_id"]
    s = engine.apply_action(s, pid2, {"kind": "choice", "index": idx}).new_state
    p = s["players"][other]
    assert p["resources"]["grain"] == 0
    assert p["resources"]["food"] >= 4


def test_joiner_of_the_sea_trade(engine):
    s = make_state(engine, 4, seed=37)
    first = s["current_player"]
    other = (first + 1) % 4
    put_in_play(s, other, "A159")
    give(s, other, wood=1)
    fishing = next(sp for sp in s["action_spaces"] if sp["id"] == "fishing")
    fishing["supply"] = {"food": 1}
    food_other_before = s["players"][other]["resources"]["food"]
    wood_first_before = s["players"][first]["resources"]["wood"]
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {"kind": "place", "space": "fishing"}).new_state
    pid2 = s["players"][other]["player_id"]
    s = engine.apply_action(s, pid2, {"kind": "choice", "index": 1}).new_state
    assert s["players"][other]["resources"]["wood"] == 0
    assert s["players"][other]["resources"]["food"] == food_other_before + 2
    assert s["players"][first]["resources"]["wood"] == wood_first_before + 1


def test_patch_caretaker_repeat_good(engine):
    s = make_state(engine, 4, seed=41)
    first = s["current_player"]
    put_in_play(s, first, "A161")
    forest = next(sp for sp in s["action_spaces"] if sp["id"] == "forest")
    forest["supply"] = {"wood": 3}
    copse = next(sp for sp in s["action_spaces"] if sp["id"] == "copse")
    copse["supply"] = {"wood": 1}
    vegetable_before = s["players"][first]["resources"]["vegetable"]
    s = place(engine, s, {"kind": "place", "space": "forest"})
    assert s["players"][first]["resources"]["vegetable"] == vegetable_before
    s["current_player"] = first
    s = place(engine, s, {"kind": "place", "space": "copse"})
    assert s["players"][first]["resources"]["vegetable"] == vegetable_before + 1


def test_building_expert_ordinal(engine):
    s = make_state(engine, 4, seed=43)
    first = s["current_player"]
    put_in_play(s, first, "A163")
    wood_before = s["players"][first]["resources"]["wood"]
    s = place(engine, s, {"kind": "place", "space": "resource_market_4p"})
    assert s["players"][first]["resources"]["wood"] == wood_before + 1


def test_wood_worker_exchange(engine):
    s = make_state(engine, 4, seed=47)
    first = s["current_player"]
    put_in_play(s, first, "A164")
    forest = next(sp for sp in s["action_spaces"] if sp["id"] == "forest")
    forest["supply"] = {"wood": 3}
    s = place(engine, s, {"kind": "place", "space": "forest"})
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {"kind": "choice", "index": 1}).new_state
    p = s["players"][first]
    assert p["resources"]["wood"] == 2
    forest2 = next(sp for sp in s["action_spaces"] if sp["id"] == "forest")
    assert forest2["supply"]["wood"] == 1


def test_haydryer_harvest_purchase(engine):
    s = make_state(engine, 4, seed=53)
    first = s["current_player"]
    put_in_play(s, first, "A166")
    p = s["players"][first]
    p["fences"] = sorted(cell_edges(0))
    give(s, first, food=3)
    food_before = p["resources"]["food"]
    log = []
    engine._start_harvest(s, log)
    prompt = s["prompts"][0]
    pid = p["player_id"]
    s = engine.apply_action(s, pid, {"kind": "choice", "index": 1}).new_state
    p = s["players"][first]
    assert p["resources"]["food"] == food_before - 3  # 4 - 1 pasture


def test_breeder_buyer_same_turn(engine):
    s = make_state(engine, 4, seed=59)
    first = s["current_player"]
    put_in_play(s, first, "A167")
    give(s, first, wood=7, reed=2)
    s = place(engine, s, {"kind": "place", "space": "farm_expansion",
                          "rooms": [0], "stables": [2]})
    prompt = s["prompts"][0]
    assert prompt["type"] == "accommodate" and prompt["gained"] == {"sheep": 1}
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {
        "kind": "accommodate",
        "placements": [{"cell": 2, "type": "sheep", "count": 1}],
    }).new_state
    p = s["players"][first]
    totals = cards.animal_totals_of(p)
    assert totals["sheep"] == 1


def test_animal_teacher_purchase(engine):
    s = make_state(engine, 4, seed=61)
    first = s["current_player"]
    give_card(s, first, "A168")
    give(s, first, food=3)
    s = place(engine, s, {"kind": "place", "space": "lessons_b", "card": "A168"})
    prompt = s["prompts"][0]
    idx = prompt["options"].index("Buy 1 sheep for 0 food")
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {"kind": "choice", "index": idx}).new_state
    s = engine.apply_action(s, pid, {
        "kind": "accommodate", "placements": [], "pets": {"sheep": 1},
    }).new_state
    p = s["players"][first]
    totals = cards.animal_totals_of(p)
    assert totals["sheep"] == 1
