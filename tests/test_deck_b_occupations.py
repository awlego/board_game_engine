"""Tests for deck B occupations (server/agricola/decks/deck_b_occupations.py).

Helper functions below are copied from tests/test_agricola.py (make_state,
give, give_card, put_in_play, add_space, place, current_pid) per
decks/GUIDE.md's testing guidance.
"""

import json
import os
import random

import pytest

from server.agricola.engine import AgricolaEngine
from server.agricola import cards
from server.agricola.decks import deck_b_occupations as deck_b
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
            if c["deck"] == "B" and c["type"] == "occupation"]


def test_registration_completeness():
    db_codes = set(_db_slice())
    registered = {cid for cid in cards.CARDS if cid in db_codes}
    unimplemented = set(deck_b.UNIMPLEMENTED)
    assert not (registered & unimplemented), "overlap between registered and UNIMPLEMENTED"
    assert registered | unimplemented == db_codes, (
        f"missing: {db_codes - registered - unimplemented}, "
        f"unexpected unimplemented: {unimplemented - db_codes}")


def _implemented_codes():
    db_codes = set(_db_slice())
    return sorted(cid for cid in cards.CARDS
                  if cid in db_codes and cid not in deck_b.UNIMPLEMENTED)


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
    action = {"kind": "place", "space": "lessons", "card": code}
    if code == "B093":
        # Needs an explicit params.rounds choice (2, 3, or 4) -- no
        # default makes sense, so the smoke test picks one.
        action["params"] = {"rounds": 2}
    s = place(engine, s, action)
    s = resolve_all_prompts(engine, s, pid)
    p = s["players"][first]
    assert any(i["id"] == code for i in p["occupations"])
    assert code not in p["hand_occupations"]


# ── Targeted effect tests ─────────────────────────────────────────────

def test_cooperative_plower_extra_plow(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "B090")
    gs = next(sp for sp in s["action_spaces"] if sp["id"] == "grain_seeds")
    gs["occupied_by"] = (first + 1) % 2
    s = place(engine, s, {"kind": "place", "space": "farmland", "cell": 1})
    prompt = s["prompts"][0]
    assert prompt["type"] == "choice"
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {"kind": "choice", "index": 1}).new_state
    p = s["players"][first]
    assert p["cells"][1]["type"] == "field"
    assert p["cells"][0]["type"] == "field"


def test_equipper_offers_minor(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "B131")
    give_card(s, first, "minor_clay_deposit")
    give(s, first, food=2)
    s = place(engine, s, {"kind": "place", "space": "forest"})
    prompt = s["prompts"][0]
    assert prompt["type"] == "choice"
    # The dealt starting hand may offer other playable minors too; pick the
    # option that actually corresponds to Clay Deposit.
    idx = prompt["data"]["candidates"].index("minor_clay_deposit")
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {"kind": "choice", "index": idx + 1}).new_state
    p = s["players"][first]
    assert any(i["id"] == "minor_clay_deposit" for i in p["minors"])
    assert p["resources"]["clay"] == 3


def test_little_stick_knitter_family_growth(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "B092")
    s["players"][first]["cells"][0]["type"] = "room"  # 3rd room
    s["round"] = 5
    sp = next((x for x in s["action_spaces"] if x["id"] == "sheep_market"), None)
    if sp is None:
        add_space(s, "sheep_market", "Sheep Market", acc=True, supply={"sheep": 1})
    else:
        sp["supply"] = {"sheep": 1}
    people_before = s["players"][first]["people_total"]
    s = place(engine, s, {"kind": "place", "space": "sheep_market"})
    prompt = s["prompts"][0]
    assert prompt["type"] == "choice"
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {"kind": "choice", "index": 1}).new_state
    assert s["players"][first]["people_total"] == people_before + 1
    # A sheep was also gained from the space itself; clear it.
    s = resolve_all_prompts(engine, s, pid)


def test_field_caretaker_exchange(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give_card(s, first, "B141")
    give(s, first, clay=1)
    grain_before = s["players"][first]["resources"]["grain"]
    s = place(engine, s, {"kind": "place", "space": "lessons", "card": "B141"})
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {"kind": "choice", "index": 2}).new_state
    p = s["players"][first]
    assert p["resources"]["clay"] == 0
    assert p["resources"]["grain"] == grain_before + 2


def test_case_builder_gains(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give_card(s, first, "B105")
    give(s, first, grain=2, reed=2)
    before = dict(s["players"][first]["resources"])
    s = place(engine, s, {"kind": "place", "space": "lessons", "card": "B105"})
    p = s["players"][first]
    assert p["resources"]["grain"] == before["grain"] + 1
    assert p["resources"]["reed"] == before["reed"] + 1
    assert p["resources"]["food"] == before["food"] + 1  # starting food is 2 or 3
    assert p["resources"]["vegetable"] == before["vegetable"]
    assert p["resources"]["wood"] == before["wood"]


def test_rustic_clay_room_bonus(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "B111")
    p = s["players"][first]
    p["house_type"] = "clay"
    give(s, first, clay=5, reed=2)
    food_before = p["resources"]["food"]
    s = place(engine, s, {"kind": "place", "space": "farm_expansion", "rooms": [0]})
    p = s["players"][first]
    assert p["resources"]["food"] == food_before + 2
    inst = next(i for i in p["occupations"] if i["id"] == "B111")
    assert cards.CARDS["B111"]["score_bonus"](s, p, inst) == 1


def test_informant_round_start_direct(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "B117")
    p = s["players"][first]
    p["resources"]["stone"] = 2
    p["resources"]["clay"] = 1
    wood_before = p["resources"]["wood"]
    ctx = {"round": s["round"], "log": [], "actor": first, "extra": {}}
    cards.CARDS["B117"]["hooks"]["round_start"](s, p, None, ctx)
    assert p["resources"]["wood"] == wood_before + 1


def test_mineralogist_clay_to_stone(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "B122")
    clay_before = s["players"][first]["resources"]["clay"]
    stone_before = s["players"][first]["resources"]["stone"]
    s = place(engine, s, {"kind": "place", "space": "clay_pit"})
    p = s["players"][first]
    assert p["resources"]["clay"] == clay_before + 1
    assert p["resources"]["stone"] == stone_before + 1


def test_plumber_discounted_renovation(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "B128")
    give_card(s, first, "minor_clay_deposit")
    give(s, first, food=1, reed=1)
    add_space(s, "major_improvement", "Major Improvement")
    s = place(engine, s, {"kind": "place", "space": "major_improvement",
                          "minor": {"card": "minor_clay_deposit"}})
    prompt = s["prompts"][0]
    assert prompt["type"] == "choice"
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {"kind": "choice", "index": 1}).new_state
    p = s["players"][first]
    assert p["house_type"] == "clay"
    assert p["resources"]["reed"] == 0


def test_illusionist_discard_for_resource(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "B146")
    give_card(s, first, "occ_woodcutter")
    wood_before = s["players"][first]["resources"]["wood"]
    hand_size_before = len(s["players"][first]["hand_occupations"])
    s = place(engine, s, {"kind": "place", "space": "forest"})
    prompt = s["prompts"][0]
    idx = prompt["data"]["hand"].index(("occ_woodcutter", "hand_occupations"))
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {"kind": "choice", "index": idx + 1}).new_state
    p = s["players"][first]
    assert "occ_woodcutter" not in p["hand_occupations"]
    assert len(p["hand_occupations"]) == hand_size_before - 1
    assert p["resources"]["wood"] == wood_before + 3 + 1


def test_huntsman_wood_to_boar(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "B147")
    give(s, first, grain=1)
    s = place(engine, s, {"kind": "place", "space": "forest"})
    prompt = s["prompts"][0]
    assert prompt["type"] == "choice"
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {"kind": "choice", "index": 1}).new_state
    assert s["prompts"][0]["gained"] == {"boar": 1}
    s = engine.apply_action(s, pid, {"kind": "accommodate", "placements": [],
                                     "discard": {"boar": 1}}).new_state
    assert s["players"][first]["resources"]["grain"] == 0


def test_housemaster_score(engine):
    s = make_state(engine, 2)
    p = s["players"][0]
    p["improvements"] = ["fireplace_2", "well"]  # points 1 and 4
    bonus = cards.CARDS["B153"]["score_bonus"](s, p, None)
    # smallest (1) counts double: total = 1+4+1 = 6 -> tier(5) = 1
    assert bonus == 1


def test_housebook_master_bonus(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "B134")
    p = s["players"][first]
    p["house_type"] = "clay"
    give(s, first, stone=2, reed=1)
    add_space(s, "house_redevelopment", "House Redevelopment")
    food_before = p["resources"]["food"]
    s = place(engine, s, {"kind": "place", "space": "house_redevelopment"})
    p = s["players"][first]
    assert p["house_type"] == "stone"
    assert p["resources"]["food"] == food_before + 3
    inst = next(i for i in p["occupations"] if i["id"] == "B134")
    assert cards.CARDS["B134"]["score_bonus"](s, p, inst) == 3


def test_clutterer_bonus(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "B100")
    # occ_forager's registered text literally says "accumulation space".
    put_in_play(s, first, "occ_forager")
    inst = next(i for i in p["occupations"] if i["id"] == "B100")
    ctx = {"card_id": "occ_forager", "actor": first, "log": [], "extra": {}}
    cards.CARDS["B100"]["hooks"]["occupation_played"](s, p, inst, ctx)
    assert cards.CARDS["B100"]["score_bonus"](s, p, inst) == 1


def test_village_peasant_bonus(engine):
    s = make_state(engine, 2)
    p = s["players"][0]
    put_in_play(s, 0, "B133")
    put_in_play(s, 0, "occ_woodcutter")
    put_in_play(s, 0, "minor_basket")
    p["improvements"] = ["fireplace_2"]
    p["resources"]["vegetable"] = 0
    n = min(len(p["improvements"]), len(p["minors"]), len(p["occupations"]))
    assert n == 1
    bonus = cards.CARDS["B133"]["score_bonus"](s, p, None)
    assert bonus == 2  # table_score(1)=1 minus table_score(0)=-1


def test_wholesaler_store_and_release(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give_card(s, first, "B137")
    s = place(engine, s, {"kind": "place", "space": "lessons", "card": "B137"})
    inst = next(i for i in s["players"][first]["occupations"] if i["id"] == "B137")
    assert inst["data"] == {"vegetable": 1, "boar": 1, "stone": 1, "cattle": 1}
    add_space(s, "cattle_market", "Cattle Market", acc=True, supply={"cattle": 1})
    # Playing B137 ended this player's turn; make it their turn again so
    # the Wholesaler's owner is the one using Cattle Market.
    s["current_player"] = first
    s["players"][first]["people_placed"] = 0
    s2 = place(engine, s, {"kind": "place", "space": "cattle_market"})
    inst2 = next(i for i in s2["players"][first]["occupations"] if i["id"] == "B137")
    assert inst2["data"]["cattle"] == 0
    assert s2["prompts"][0]["gained"] == {"cattle": 2}


def test_pasture_master_renovate_bonus(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "B168")
    p = s["players"][first]
    p["fences"] = sorted(cell_edges(4))
    p["cells"][4]["stable"] = True
    p["cells"][4]["animal"] = {"type": "sheep", "count": 1}
    give(s, first, clay=2, reed=1)
    add_space(s, "house_redevelopment", "House Redevelopment")
    food_before = p["resources"]["food"]
    s = place(engine, s, {"kind": "place", "space": "house_redevelopment"})
    p = s["players"][first]
    assert p["resources"]["food"] == food_before + 2
    assert s["prompts"][0]["gained"] == {"sheep": 1}


def test_lumberjack_scheduled_wood(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["fences"] = sorted(cell_edges(4))  # 4 fence edges already built
    give_card(s, first, "B119")
    wood_before = p["resources"]["wood"]
    s = place(engine, s, {"kind": "place", "space": "lessons", "card": "B119"})
    p = s["players"][first]
    assert p["resources"]["wood"] == wood_before + 1
    for r in range(2, 6):
        assert s["round_goods"][str(r)][str(first)]["wood"] == 1


def test_estate_worker_scheduled_resources(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give_card(s, first, "B125")
    s = place(engine, s, {"kind": "place", "space": "lessons", "card": "B125"})
    goods = ["wood", "clay", "reed", "stone"]
    for i, r in enumerate(range(2, 6)):
        assert s["round_goods"][str(r)][str(first)][goods[i]] == 1


def test_moral_crusader_round_income(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "B106")
    p = s["players"][first]
    s["round"] = 3
    s["round_goods"] = {"5": {str(first): {"wood": 1}}}
    food_before = p["resources"]["food"]
    ctx = {"round": 3, "log": [], "actor": first, "extra": {}}
    cards.CARDS["B106"]["hooks"]["round_start"](s, p, None, ctx)
    assert p["resources"]["food"] == food_before + 1


def test_pavior_round_income(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "B110")
    p = s["players"][first]
    p["resources"]["stone"] = 1
    food_before = p["resources"]["food"]
    s["round"] = 5
    ctx = {"round": 5, "log": [], "actor": first, "extra": {}}
    cards.CARDS["B110"]["hooks"]["round_start"](s, p, None, ctx)
    assert p["resources"]["food"] == food_before + 1
    s["round"] = 14
    veg_before = p["resources"]["vegetable"]
    cards.CARDS["B110"]["hooks"]["round_start"](s, p, None, ctx)
    assert p["resources"]["vegetable"] == veg_before + 1


def test_furniture_carpenter_card_action(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "B101")
    p = s["players"][first]
    p["improvements"] = ["joinery"]
    s["phase"] = "feeding"
    s["harvest_index"] = 2
    give(s, first, food=5)
    food_before = p["resources"]["food"]
    inst = next(i for i in p["occupations"] if i["id"] == "B101")
    assert cards.CARDS["B101"]["card_action"]["available"](s, p, inst)
    ctx = {"log": [], "actor": first, "extra": {}}
    cards.CARDS["B101"]["card_action"]["apply"](s, p, inst, ctx)
    assert p["resources"]["food"] == food_before - 2
    assert cards.CARDS["B101"]["score_bonus"](s, p, inst) == 1
    assert not cards.CARDS["B101"]["card_action"]["available"](s, p, inst)


def test_salter_card_action(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "B157")
    p = s["players"][first]
    p["fences"] = sorted(cell_edges(4))
    p["cells"][4]["animal"] = {"type": "boar", "count": 1}
    inst = next(i for i in p["occupations"] if i["id"] == "B157")
    assert cards.CARDS["B157"]["card_action"]["available"](s, p, inst)
    ctx = {"log": [], "actor": first, "params": {"animal": "boar"}, "extra": {}}
    cards.CARDS["B157"]["card_action"]["apply"](s, p, inst, ctx)
    assert animal_counts(p)["boar"] == 0
    for r in range(2, 7):
        assert s["round_goods"][str(r)][str(first)]["food"] == 1


def test_sheep_keeper_prereq_and_trigger(engine):
    s = make_state(engine, 2)
    p = s["players"][0]
    p["pets"] = {"sheep": 8}
    assert not cards.check_prereq(s, p, "B154")
    p["pets"] = {"sheep": 3}
    assert cards.check_prereq(s, p, "B154")
    put_in_play(s, 0, "B154")
    inst = next(i for i in p["occupations"] if i["id"] == "B154")
    p["pets"] = {"sheep": 7}
    food_before = p["resources"]["food"]
    ctx = {"log": [], "actor": 0, "extra": {}}
    cards.CARDS["B154"]["hooks"]["round_start"](s, p, inst, ctx)
    assert p["resources"]["food"] == food_before + 2
    assert cards.CARDS["B154"]["score_bonus"](s, p, inst) == 3


def test_sheep_keeper_prereq_blocks_play(engine):
    # The engine enforces the declared prereq before the card is played
    # -- playing with 7+ sheep already must be rejected up front.
    s = make_state(engine, 2)
    first = s["current_player"]
    give_card(s, first, "B154")
    give(s, first, food=10)
    p = s["players"][first]
    p["pets"] = {"sheep": 7}
    with pytest.raises(ValueError):
        place(engine, s, {"kind": "place", "space": "lessons", "card": "B154"})
    p["pets"] = {"sheep": 3}
    s = place(engine, s, {"kind": "place", "space": "lessons", "card": "B154"})
    assert any(i["id"] == "B154" for i in s["players"][first]["occupations"])


def test_tree_farm_joiner_offers_minor(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give_card(s, first, "B096")
    give_card(s, first, "minor_clay_deposit")
    give(s, first, food=2)
    s = place(engine, s, {"kind": "place", "space": "lessons", "card": "B096"})
    p = s["players"][first]
    inst = next(i for i in p["occupations"] if i["id"] == "B096")
    odd_rounds = inst["data"]["odd_rounds"]
    assert odd_rounds == [3, 5]
    s["round"] = odd_rounds[0]
    ctx = {"round": odd_rounds[0], "log": [], "actor": first, "extra": {}}
    cards.CARDS["B096"]["hooks"]["round_start"](s, p, inst, ctx)
    assert s["prompts"]
    prompt = s["prompts"][-1]
    assert prompt["type"] == "choice"
    idx = prompt["options"].index("Clay Deposit")
    resolve_ctx = {"index": idx, "option": prompt["options"][idx],
                  "data": prompt["data"], "log": [], "actor": first, "extra": {}}
    cards.CARDS["B096"]["resolve_choice"](s, p, inst, resolve_ctx)
    assert any(i["id"] == "minor_clay_deposit" for i in p["minors"])


def test_forest_scientist_no_wood_on_board(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    inst = put_in_play(s, first, "B139")
    p = s["players"][first]
    forest = next(sp for sp in s["action_spaces"] if sp["id"] == "forest")
    food_before = p["resources"]["food"]
    ctx = {"space_id": "forest", "goods": {"wood": 3}, "extra": {},
          "log": [], "actor": first}
    forest["supply"] = {"wood": 0}
    cards.CARDS["B139"]["hooks"]["space_used"](s, p, inst, ctx)
    assert p["resources"]["food"] == food_before + 1  # round < 5


def test_weakling_grants_vegetable(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "B161")
    other = next(sp for sp in s["action_spaces"] if sp["id"] == "reed_bank")
    other["supply"] = {"reed": 5}
    veg_before = s["players"][first]["resources"]["vegetable"]
    ctx = {"space_id": "day_laborer", "goods": {"food": 2}, "extra": {},
          "log": [], "actor": first}
    cards.CARDS["B161"]["hooks"]["space_used"](s, s["players"][first], None, ctx)
    assert s["players"][first]["resources"]["vegetable"] == veg_before + 1


def test_estate_master_locks_in_bonus_once_farm_is_full(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    inst = put_in_play(s, first, "B132")
    for c in p["cells"]:
        c["type"] = "room"  # no unused farmyard spaces left

    play_hook = cards.CARDS["B132"]["hooks"]["play"]
    play_hook(s, p, inst, {"log": [], "actor": first, "extra": {}})
    assert inst["data"]["unlocked"]

    harvest_hook = cards.CARDS["B132"]["hooks"]["harvest_field"]
    harvest_hook(s, p, inst, {"got": {"grain": 1, "vegetable": 3},
                             "tiles": {}, "card_fields": {}, "log": [],
                             "actor": first, "extra": {}})
    assert inst["data"]["bonus"] == 3

    # Bonus keeps accruing even after the farmyard gains unused space
    # again (ruling: the "once" trigger is permanent).
    p["cells"][0]["type"] = "empty"
    harvest_hook(s, p, inst, {"got": {"grain": 0, "vegetable": 2},
                             "tiles": {}, "card_fields": {}, "log": [],
                             "actor": first, "extra": {}})
    assert inst["data"]["bonus"] == 5
    assert cards.CARDS["B132"]["score_bonus"](s, p, inst) == 5


def test_tinsmith_master_stable_less_pasture_and_sow_bonus(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "B115")
    p["fences"] = sorted(cell_edges(4))  # size-1 pasture, no stable
    assert cards.pasture_capacity(s, p, [4], "sheep") == 3  # 2 base + 1
    p["cells"][4]["stable"] = True
    assert cards.pasture_capacity(s, p, [4], "sheep") == 4  # stabled -> no bonus

    p["cells"][0]["type"] = "field"
    p["cells"][0]["crops"] = {"type": "grain", "count": 1}
    hook = cards.CARDS["B115"]["hooks"]["sow"]
    hook(s, p, {"id": "B115", "data": {}},
        {"sown": [(0, p["cells"][0]["crops"])], "log": []})
    assert p["cells"][0]["crops"]["count"] == 2


def test_pet_broker_gains_sheep_and_holds_by_occupation_count(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "occ_woodcutter")
    inst = put_in_play(s, first, "B148")
    assert len(p["occupations"]) == 2

    assert cards.CARDS["B148"]["holds_animals"](s, p, inst) == {"types": {"sheep": 2}}
    inst["held"] = {"sheep": 2}
    ok, err = cards.validate_held(s, p)
    assert ok, err
    inst["held"] = {"sheep": 3}
    ok, err = cards.validate_held(s, p)
    assert not ok

    play_hook = cards.CARDS["B148"]["hooks"]["play"]
    ctx = {"log": [], "actor": first, "extra": {}}
    play_hook(s, p, inst, ctx)
    assert ctx["extra"]["sheep"] == 1


def test_sweep_bonus_on_left_neighbor_of_round_space(engine):
    """Rounds run horizontally, so in round 2 the card left of the
    newest round card is the round-1 card (in round 1 there is nothing
    to Sweep's left at all -- meadow borders the round-1 slot; the
    Compendium's B120 ruling). Pick a seed whose round-1 card is Major
    Improvement so using the target space needs no other setup than
    2 clay for a Fireplace."""
    seed = next(k for k in range(200)
                if make_state(engine, 2, seed=k)["revealed"][0]
                == "major_improvement")
    s = make_state(engine, 2, seed=seed)
    first = s["current_player"]
    put_in_play(s, first, "B120")
    assert cards.left_neighbor(s, s["revealed"][-1]) is None  # round 1
    engine._start_round(s, [])  # round 2
    assert cards.left_neighbor(s, s["revealed"][-1]) == s["revealed"][0]
    give(s, first, clay=2)
    clay_before = s["players"][first]["resources"]["clay"]
    s = place(engine, s, {"kind": "place", "space": "major_improvement",
                          "improvement": "fireplace_2"})
    # Paid 2 clay for the Fireplace, got 2 back from the Sweep.
    assert s["players"][first]["resources"]["clay"] == clay_before


def test_sweep_no_bonus_off_target(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "B120")
    clay_before = s["players"][first]["resources"]["clay"]
    s = place(engine, s, {"kind": "place", "space": "grain_seeds"})
    assert s["players"][first]["resources"]["clay"] == clay_before


# ── B088 Established Person ─────────────────────────────────────────

def test_established_person_free_renovation(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give_card(s, first, "B088")
    give(s, first, food=1)
    pid = s["players"][first]["player_id"]
    s = place(engine, s, {"kind": "place", "space": "lessons", "card": "B088"})
    p = s["players"][first]
    assert p["house_type"] == "clay"
    assert p["resources"]["clay"] == 0  # renovation was free


def test_established_person_renovation_then_fence(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give_card(s, first, "B088")
    give(s, first, food=1, wood=4)  # fences cost the normal amount here
    pid = s["players"][first]["player_id"]
    s = place(engine, s, {"kind": "place", "space": "lessons", "card": "B088",
                          "params": {"fences": list(cell_edges(0))}})
    p = s["players"][first]
    assert p["house_type"] == "clay"
    assert p["resources"]["wood"] == 0
    assert len(compute_pastures(p)) == 1


def test_established_person_fence_unaffordable_rolls_back(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give_card(s, first, "B088")
    give(s, first, food=1)  # no wood -- the follow-on fence build can't be paid
    with pytest.raises(ValueError):
        place(engine, s, {"kind": "place", "space": "lessons", "card": "B088",
                          "params": {"fences": list(cell_edges(0))}})
    # The whole action (including the free renovation) rolled back.
    p = s["players"][first]
    assert "B088" in p["hand_occupations"]
    assert p["house_type"] == "wood"


def test_established_person_requires_exactly_2_rooms(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["cells"][0]["type"] = "room"  # now 3 rooms
    give_card(s, first, "B088")
    give(s, first, food=1)
    with pytest.raises(ValueError):
        place(engine, s, {"kind": "place", "space": "lessons", "card": "B088"})


# ── B093 Little Stick Knitter (Sow/Build Fences via scheduled food) ──

def test_little_stick_knitter_b_schedule_and_sow(engine):
    # Solo, so current_player never advances away from this player between
    # the play and the later card_action -- sidesteps the (expected, see
    # decks/GUIDE.md) rotation-timing gap for banked-credit card_actions.
    s = make_state(engine, 1)
    first = s["current_player"]
    give_card(s, first, "B093")
    give(s, first, food=3)  # 1 for the occupation, 2 to schedule
    pid = s["players"][first]["player_id"]
    s = place(engine, s, {"kind": "place", "space": "lessons", "card": "B093",
                          "params": {"rounds": 2}})
    p = s["players"][first]
    inst = next(i for i in p["occupations"] if i["id"] == "B093")
    start_round = s["round"]
    assert inst["data"]["scheduled_rounds"] == [start_round + 1, start_round + 2]
    target = inst["data"]["scheduled_rounds"][0]
    assert s["round_goods"][str(target)][str(first)]["food"] == 1

    s["round"] = target
    log = []
    engine._fire(s, "round_start", p, {"round": target}, log, to_all=False)
    assert inst["data"]["active_round"] == target

    p["cells"][3]["type"] = "field"
    give(s, first, grain=1)
    s = engine.apply_action(s, pid, {
        "kind": "card_action", "card": "B093",
        "params": {"kind": "sow", "sow": [{"cell": 3, "crop": "grain"}]}}).new_state
    p = s["players"][first]
    assert p["cells"][3]["crops"] == {"type": "grain", "count": 3}
    inst = next(i for i in p["occupations"] if i["id"] == "B093")
    assert inst["data"]["used_this_round"] is True


def test_lieutenant_general_food_next_to_existing_field(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    put_in_play(s, first, "B159")
    s["players"][other]["cells"][0]["type"] = "field"
    s = place(engine, s, {"kind": "place", "space": "meeting_place"})  # first
    food_before = s["players"][first]["resources"]["food"]
    s = place(engine, s, {"kind": "place", "space": "farmland", "cell": 1})  # other
    assert s["players"][first]["resources"]["food"] == food_before + 1


def test_lieutenant_general_no_reward_without_adjacent_field(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "B159")
    s = place(engine, s, {"kind": "place", "space": "meeting_place"})  # first
    food_before = s["players"][first]["resources"]["food"]
    s = place(engine, s, {"kind": "place", "space": "farmland", "cell": 1})  # other
    assert s["players"][first]["resources"]["food"] == food_before


def test_lieutenant_general_grants_grain_in_round_14(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    put_in_play(s, first, "B159")
    s["players"][other]["cells"][0]["type"] = "field"
    s["round"] = 14
    s = place(engine, s, {"kind": "place", "space": "meeting_place"})  # first
    grain_before = s["players"][first]["resources"]["grain"]
    s = place(engine, s, {"kind": "place", "space": "farmland", "cell": 1})  # other
    assert s["players"][first]["resources"]["grain"] == grain_before + 1


def test_lieutenant_general_ignores_own_plow(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "B159")
    s["players"][first]["cells"][0]["type"] = "field"
    food_before = s["players"][first]["resources"]["food"]
    s = place(engine, s, {"kind": "place", "space": "farmland", "cell": 1})  # first's own plow
    assert s["players"][first]["resources"]["food"] == food_before
