"""Tests for the Agricola engine (base game, no-hand-cards variant)."""

import random

import pytest

from server.agricola.engine import AgricolaEngine
from server.agricola.state import (
    ANIMAL_TYPES, HARVEST_ROUNDS, MAJOR_IMPROVEMENTS, STAGE_CARDS,
    animal_counts, cell_edges, compute_pastures, pasture_capacity,
    validate_fence_layout,
)
from server.agricola.scoring import score_player


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


# ── Setup ────────────────────────────────────────────────────────────

def test_initial_setup(engine):
    s = make_state(engine, 2)
    assert s["round"] == 1
    assert len(s["deck"]) == 14
    # Stage cards are ordered by stage in the deck.
    stages = [STAGE_CARDS[c]["stage"] for c in s["deck"]]
    assert stages == sorted(stages)
    # One stage-1 card revealed for round 1.
    assert len(s["revealed"]) == 1
    assert STAGE_CARDS[s["revealed"][0]]["stage"] == 1

    for p in s["players"]:
        assert p["people_total"] == 2
        rooms = [i for i, c in enumerate(p["cells"]) if c["type"] == "room"]
        assert rooms == [5, 10]
    # Starting player gets 2 food, other player 3.
    foods = sorted(p["resources"]["food"] for p in s["players"])
    assert foods == [2, 3]
    assert s["players"][s["starting_player"]]["resources"]["food"] == 2


def test_solo_setup(engine):
    s = make_state(engine, 1)
    assert s["players"][0]["resources"]["food"] == 0
    forest = next(sp for sp in s["action_spaces"] if sp["id"] == "forest")
    assert forest["supply"] == {"wood": 2}  # solo: 2 wood per round


def test_player_count_spaces(engine):
    ids2 = {sp["id"] for sp in make_state(engine, 2)["action_spaces"]}
    ids3 = {sp["id"] for sp in make_state(engine, 3)["action_spaces"]}
    ids4 = {sp["id"] for sp in make_state(engine, 4)["action_spaces"]}
    assert "grove" not in ids2
    assert {"grove", "hollow_3p", "resource_market_3p"} <= ids3
    assert {"copse", "grove", "hollow_4p", "resource_market_4p",
            "traveling_players"} <= ids4
    assert "hollow_3p" not in ids4


# ── Work phase basics ────────────────────────────────────────────────

def test_accumulation_and_turn_order(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    s = place(engine, s, {"kind": "place", "space": "forest"})
    assert s["players"][first]["resources"]["wood"] == 3
    assert s["current_player"] == (first + 1) % 2
    # Occupied space cannot be reused.
    pid = current_pid(engine, s)
    with pytest.raises(ValueError):
        engine.apply_action(s, pid, {"kind": "place", "space": "forest"})


def test_accumulation_replenishes_and_carries_over(engine):
    s = make_state(engine, 2)
    # Nobody takes wood in round 1; play 4 placements on other spaces.
    for space in ["day_laborer", "fishing", "grain_seeds", "farmland"]:
        act = {"kind": "place", "space": space}
        if space == "farmland":
            act["cell"] = 0
        s = place(engine, s, act)
    assert s["round"] == 2
    forest = next(sp for sp in s["action_spaces"] if sp["id"] == "forest")
    assert forest["supply"]["wood"] == 6  # 3 + 3


def test_meeting_place_starting_player(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    s = place(engine, s, {"kind": "place", "space": "day_laborer"})
    s = place(engine, s, {"kind": "place", "space": "meeting_place"})
    assert s["starting_player"] == other
    assert s["players"][other]["resources"]["food"] >= 4  # 3 start + 1 accumulated
    # Remaining placements finish the round; the new round starts with `other`.
    s = place(engine, s, {"kind": "place", "space": "forest"})
    s = place(engine, s, {"kind": "place", "space": "fishing"})
    assert s["round"] == 2
    assert s["current_player"] == other


def test_grain_seeds_and_day_laborer(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    food_before = s["players"][first]["resources"]["food"]
    s = place(engine, s, {"kind": "place", "space": "day_laborer"})
    assert s["players"][first]["resources"]["food"] == food_before + 2
    second = s["current_player"]
    s = place(engine, s, {"kind": "place", "space": "grain_seeds"})
    assert s["players"][second]["resources"]["grain"] == 1


# ── Farm development ─────────────────────────────────────────────────

def test_plow_adjacency(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    s = place(engine, s, {"kind": "place", "space": "farmland", "cell": 0})
    assert s["players"][first]["cells"][0]["type"] == "field"
    # Round continues; next round the same player plows again.
    s = place(engine, s, {"kind": "place", "space": "day_laborer"})
    s = place(engine, s, {"kind": "place", "space": "fishing"})
    s = place(engine, s, {"kind": "place", "space": "grain_seeds"})
    assert s["round"] == 2
    # Second field must be adjacent to the first (cell 0 → 1 or 5(room)... so 1).
    pid = s["players"][s["current_player"]]["player_id"]
    if s["current_player"] != first:
        s = place(engine, s, {"kind": "place", "space": "day_laborer"})
    with pytest.raises(ValueError):
        place(engine, s, {"kind": "place", "space": "farmland", "cell": 14})
    s = place(engine, s, {"kind": "place", "space": "farmland", "cell": 1})
    assert s["players"][first]["cells"][1]["type"] == "field"


def test_build_rooms_and_family_growth(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give(s, first, wood=10, reed=4)
    # Rooms must be adjacent to the house (cells 5, 10 are rooms; cell 0 or 6 ok).
    with pytest.raises(ValueError):
        place(engine, s, {"kind": "place", "space": "farm_expansion", "rooms": [4]})
    s = place(engine, s, {"kind": "place", "space": "farm_expansion",
                          "rooms": [0, 1]})
    p = s["players"][first]
    assert sum(1 for c in p["cells"] if c["type"] == "room") == 4
    assert p["resources"]["wood"] == 0 and p["resources"]["reed"] == 0

    # Inject basic_wish space and grow the family.
    s["action_spaces"].append({
        "id": "basic_wish", "name": "Basic Wish for Children", "desc": "",
        "stage": 2, "occupied_by": None, "supply": {}, "accumulates": False})
    s = place(engine, s, {"kind": "place", "space": "day_laborer"})  # other player
    s = place(engine, s, {"kind": "place", "space": "basic_wish"})
    p = s["players"][first]
    assert p["people_total"] == 3
    assert p["newborns"] == 1


def test_family_growth_requires_room(engine):
    s = make_state(engine, 2)
    s["action_spaces"].append({
        "id": "basic_wish", "name": "Basic Wish for Children", "desc": "",
        "stage": 2, "occupied_by": None, "supply": {}, "accumulates": False})
    pid = current_pid(engine, s)
    # 2 rooms, 2 people — not allowed.
    with pytest.raises(ValueError):
        engine.apply_action(s, pid, {"kind": "place", "space": "basic_wish"})
    # And it's not offered as a valid action.
    assert "basic_wish" not in {a["space"] for a in engine.get_valid_actions(s, pid)}


def test_urgent_wish_no_room_needed(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    s["action_spaces"].append({
        "id": "urgent_wish", "name": "Urgent Wish for Children", "desc": "",
        "stage": 5, "occupied_by": None, "supply": {}, "accumulates": False})
    s = place(engine, s, {"kind": "place", "space": "urgent_wish"})
    assert s["players"][first]["people_total"] == 3


def test_renovation(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give(s, first, clay=2, reed=1)
    s["action_spaces"].append({
        "id": "house_redevelopment", "name": "House Redevelopment", "desc": "",
        "stage": 2, "occupied_by": None, "supply": {}, "accumulates": False})
    s = place(engine, s, {"kind": "place", "space": "house_redevelopment"})
    p = s["players"][first]
    assert p["house_type"] == "clay"
    assert p["resources"]["clay"] == 0 and p["resources"]["reed"] == 0
    # Now rooms cost clay.
    give(s, first, clay=5, reed=2)
    s = place(engine, s, {"kind": "place", "space": "day_laborer"})
    s = place(engine, s, {"kind": "place", "space": "farm_expansion", "rooms": [0]})
    assert s["players"][first]["cells"][0]["type"] == "room"
    assert s["players"][first]["resources"]["clay"] == 0


def test_renovation_with_improvement(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give(s, first, clay=4, reed=1)
    s["action_spaces"].append({
        "id": "house_redevelopment", "name": "House Redevelopment", "desc": "",
        "stage": 2, "occupied_by": None, "supply": {}, "accumulates": False})
    s = place(engine, s, {"kind": "place", "space": "house_redevelopment",
                          "improvement": "fireplace_2"})
    p = s["players"][first]
    assert p["house_type"] == "clay"
    assert "fireplace_2" in p["improvements"]
    assert "fireplace_2" not in s["available_improvements"]


# ── Fencing and pastures ─────────────────────────────────────────────

def square_fences(cell):
    return cell_edges(cell)


def test_fence_single_pasture(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give(s, first, wood=4)
    s["action_spaces"].append({
        "id": "fencing", "name": "Fencing", "desc": "",
        "stage": 1, "occupied_by": None, "supply": {}, "accumulates": False})
    s = place(engine, s, {"kind": "place", "space": "fencing",
                          "fences": square_fences(4)})
    p = s["players"][first]
    assert p["resources"]["wood"] == 0
    pastures = compute_pastures(p)
    assert pastures == [[4]]
    assert pasture_capacity(p, [4]) == 2


def test_fence_validation():
    from server.agricola.state import create_player
    p = create_player(0, "p", "P")
    # Dangling fence — not enclosing anything.
    ok, err, _ = validate_fence_layout(p, ["h-0-0"])
    assert not ok
    # Enclosing a room is illegal.
    ok, err, _ = validate_fence_layout(p, cell_edges(5))
    assert not ok
    # Proper 1x2 pasture: cells 3,4.
    fences = set(cell_edges(3)) | set(cell_edges(4))
    fences.discard("v-0-4")  # interior edge... wait, v-0-4 is between 3 and 4
    ok, err, pastures = validate_fence_layout(p, sorted(fences))
    assert ok, err
    assert pastures == [[3, 4]]
    # Disconnected pastures are illegal.
    fences = list(cell_edges(0)) + list(cell_edges(14))
    ok, err, _ = validate_fence_layout(p, fences)
    assert not ok


def test_fence_subdivision_capacity():
    from server.agricola.state import create_player
    p = create_player(0, "p", "P")
    fences = set(cell_edges(3)) | set(cell_edges(4))
    fences.discard("v-0-4")
    p["fences"] = sorted(fences)
    assert compute_pastures(p) == [[3, 4]]
    assert pasture_capacity(p, [3, 4]) == 4
    # Add the interior fence: two 1x1 pastures.
    p["fences"] = sorted(fences | {"v-0-4"})
    assert compute_pastures(p) == [[3], [4]]
    # Stable doubles capacity.
    p["cells"][3]["stable"] = True
    assert pasture_capacity(p, [3]) == 4


def test_fencing_strands_animals_forces_accommodate(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    give(s, first, wood=1)
    # Pre-built 1x2 pasture with 4 sheep in one cell.
    fences = set(cell_edges(3)) | set(cell_edges(4))
    fences.discard("v-0-4")
    p["fences"] = sorted(fences)
    p["cells"][3]["animal"] = {"type": "sheep", "count": 4}
    s["action_spaces"].append({
        "id": "fencing", "name": "Fencing", "desc": "",
        "stage": 1, "occupied_by": None, "supply": {}, "accumulates": False})
    s = place(engine, s, {"kind": "place", "space": "fencing",
                          "fences": ["v-0-4"]})
    # 4 sheep in a 1x1 pasture is over capacity → pending accommodation.
    assert s["pending"] == {"player": first, "gained": {}}
    pid = p["player_id"]
    assert engine.get_valid_actions(s, pid)[0]["kind"] == "accommodate"
    # Redistribute 2+2.
    s = engine.apply_action(s, pid, {
        "kind": "accommodate",
        "placements": [{"cell": 3, "type": "sheep", "count": 2},
                       {"cell": 4, "type": "sheep", "count": 2}],
    }).new_state
    assert s["pending"] is None


# ── Animals ──────────────────────────────────────────────────────────

def sheep_pasture_state(engine, n_sheep=0):
    """2p state where current player has a fenced pasture at cell 4."""
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["fences"] = sorted(cell_edges(4))
    if n_sheep:
        p["cells"][4]["animal"] = {"type": "sheep", "count": n_sheep}
    return s, first


def test_take_sheep_accommodate(engine):
    s, first = sheep_pasture_state(engine)
    sheep_sp = next((sp for sp in s["action_spaces"] if sp["id"] == "sheep_market"), None)
    if sheep_sp is None:
        s["action_spaces"].append({
            "id": "sheep_market", "name": "Sheep Market", "desc": "",
            "stage": 1, "occupied_by": None, "supply": {"sheep": 2},
            "accumulates": True})
    else:
        sheep_sp["supply"] = {"sheep": 2}
    s = place(engine, s, {"kind": "place", "space": "sheep_market"})
    assert s["pending"]["gained"] == {"sheep": 2}
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {
        "kind": "accommodate",
        "placements": [{"cell": 4, "type": "sheep", "count": 2}],
    }).new_state
    assert animal_counts(s["players"][first])["sheep"] == 2


def test_accommodate_overflow_rejected(engine):
    s, first = sheep_pasture_state(engine)
    pid = s["players"][first]["player_id"]
    sheep_sp = next((sp for sp in s["action_spaces"] if sp["id"] == "sheep_market"), None)
    if sheep_sp is None:
        s["action_spaces"].append({
            "id": "sheep_market", "name": "Sheep Market", "desc": "",
            "stage": 1, "occupied_by": None, "supply": {"sheep": 3},
            "accumulates": True})
    else:
        sheep_sp["supply"] = {"sheep": 3}
    s = place(engine, s, {"kind": "place", "space": "sheep_market"})
    # 3 sheep into a capacity-2 pasture: rejected.
    with pytest.raises(ValueError):
        engine.apply_action(s, pid, {
            "kind": "accommodate",
            "placements": [{"cell": 4, "type": "sheep", "count": 3}]})
    # Pet + 2 in pasture works.
    s = engine.apply_action(s, pid, {
        "kind": "accommodate", "pet": "sheep",
        "placements": [{"cell": 4, "type": "sheep", "count": 2}]}).new_state
    assert animal_counts(s["players"][first])["sheep"] == 3


def test_accommodate_discard_and_cook(engine):
    s, first = sheep_pasture_state(engine)
    p = s["players"][first]
    p["improvements"].append("fireplace_2")
    s["available_improvements"].remove("fireplace_2")
    pid = p["player_id"]
    sheep_sp = next((sp for sp in s["action_spaces"] if sp["id"] == "sheep_market"), None)
    if sheep_sp is None:
        s["action_spaces"].append({
            "id": "sheep_market", "name": "Sheep Market", "desc": "",
            "stage": 1, "occupied_by": None, "supply": {"sheep": 4},
            "accumulates": True})
    else:
        sheep_sp["supply"] = {"sheep": 4}
    food_before = p["resources"]["food"]
    s = place(engine, s, {"kind": "place", "space": "sheep_market"})
    s = engine.apply_action(s, pid, {
        "kind": "accommodate",
        "cook": {"sheep": 1},          # 2 food via fireplace
        "discard": {"sheep": 1},
        "placements": [{"cell": 4, "type": "sheep", "count": 2}],
    }).new_state
    p = s["players"][first]
    assert p["resources"]["food"] == food_before + 2
    assert animal_counts(p)["sheep"] == 2


def test_mixed_types_in_pasture_rejected():
    from server.agricola.state import create_player, validate_animal_placement
    p = create_player(0, "p", "P")
    fences = set(cell_edges(3)) | set(cell_edges(4))
    fences.discard("v-0-4")
    p["fences"] = sorted(fences)
    p["cells"][3]["animal"] = {"type": "sheep", "count": 1}
    p["cells"][4]["animal"] = {"type": "boar", "count": 1}
    ok, err = validate_animal_placement(p)
    assert not ok


def test_unfenced_stable_holds_one():
    from server.agricola.state import create_player, validate_animal_placement
    p = create_player(0, "p", "P")
    p["cells"][0]["stable"] = True
    p["cells"][0]["animal"] = {"type": "cattle", "count": 1}
    ok, err = validate_animal_placement(p)
    assert ok, err
    p["cells"][0]["animal"]["count"] = 2
    ok, err = validate_animal_placement(p)
    assert not ok
    # Animals on a bare meadow are not allowed.
    p["cells"][0]["animal"] = None
    p["cells"][1]["animal"] = {"type": "cattle", "count": 1}
    ok, err = validate_animal_placement(p)
    assert not ok


# ── Harvest ──────────────────────────────────────────────────────────

def fast_forward_to_harvest(engine, s):
    """Place people on harmless spaces until the feeding phase starts."""
    guard = 0
    while s["phase"] == "work":
        pid = current_pid(engine, s)
        acts = engine.get_valid_actions(s, pid)
        chosen = next(a for a in acts if a["space"] in
                      ("day_laborer", "fishing", "grain_seeds", "meeting_place",
                       "forest", "clay_pit", "reed_bank", "traveling_players",
                       "western_quarry", "eastern_quarry", "vegetable_seeds",
                       "copse", "grove", "hollow_3p", "hollow_4p"))
        s = engine.apply_action(s, pid, {"kind": "place", "space": chosen["space"]}).new_state
        guard += 1
        assert guard < 200
    return s


def test_harvest_field_feeding_breeding(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    # A sown grain field, a pasture with 2 sheep, and enough food.
    p["cells"][0]["type"] = "field"
    p["cells"][0]["crops"] = {"type": "grain", "count": 3}
    p["fences"] = sorted(set(cell_edges(3)) | set(cell_edges(4)) - {"v-0-4"})
    p["cells"][3]["animal"] = {"type": "sheep", "count": 2}
    give(s, first, food=10)
    give(s, (first + 1) % 2, food=10)

    # Rounds 1-4 (harvest after round 4).
    while s["round"] < 4 or s["phase"] == "work":
        if s["phase"] != "work":
            break
        s = fast_forward_to_harvest(engine, s)
    assert s["phase"] == "feeding"

    p = s["players"][first]
    # Field phase took 1 grain from the field into supply.
    assert p["cells"][0]["crops"]["count"] == 2
    assert p["resources"]["grain"] >= 1

    for pl in list(s["players"]):
        pid = pl["player_id"]
        if not s["players"][pl["index"]]["fed"]:
            s = engine.apply_action(s, pid, {"kind": "feed"}).new_state
    # Breeding: 2 sheep → 3 sheep. Round advanced to 5.
    assert s["round"] == 5
    p = s["players"][first]
    assert animal_counts(p)["sheep"] == 3
    # Exact food depends on the spaces used; at least nobody begs.
    assert p["begging"] == 0


def test_feeding_shortfall_begging(engine):
    s = make_state(engine, 2)
    # Drain food so both players must beg.
    for p in s["players"]:
        p["resources"]["food"] = 0
    s = fast_forward_to_harvest(engine, s)
    while s["phase"] == "work" or s["round"] < 4:
        if s["phase"] == "feeding":
            break
        s = fast_forward_to_harvest(engine, s)
    assert s["phase"] == "feeding"
    for pl in list(s["players"]):
        pid = pl["player_id"]
        if not s["players"][pl["index"]]["fed"]:
            s = engine.apply_action(s, pid, {"kind": "feed"}).new_state
    # Players gathered some food on the way; everyone short begs the rest.
    for p in s["players"]:
        assert p["begging"] >= 0
    total_begging = sum(p["begging"] for p in s["players"])
    assert total_begging > 0


def test_feed_conversions(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["resources"].update({"food": 0, "grain": 3, "vegetable": 1})
    p["improvements"].append("fireplace_2")
    s["available_improvements"].remove("fireplace_2")
    s["phase"] = "feeding"
    for pl in s["players"]:
        pl["fed"] = False
    pid = p["player_id"]
    s = engine.apply_action(s, pid, {
        "kind": "feed",
        "conversions": [
            {"good": "grain", "via": "raw", "count": 2},
            {"good": "vegetable", "via": "cook", "count": 1},  # 2 via fireplace
        ]}).new_state
    p = s["players"][first]
    # 2 + 2 = 4 food, need 4 (2 people × 2) → 0 left, no begging.
    assert p["resources"]["food"] == 0
    assert p["begging"] == 0
    assert p["fed"]


def test_newborn_feeding_discount(engine):
    s = make_state(engine, 2)
    p = s["players"][0]
    p["people_total"] = 3
    p["newborns"] = 1
    assert engine._food_needed(s, p) == 5  # 2+2+1
    s["player_count"] = 1
    assert engine._food_needed(s, p) == 7  # 3+3+1


def test_breeding_needs_room(engine):
    s = make_state(engine, 2)
    p = s["players"][0]
    # 2 sheep as... only pet slot + nothing else: 1 pet sheep + no room.
    p["pet"] = "sheep"
    # No pasture, no stable → second sheep can't even exist; give a stable.
    p["cells"][0]["stable"] = True
    p["cells"][0]["animal"] = {"type": "sheep", "count": 1}
    s["phase"] = "feeding"
    for pl in s["players"]:
        pl["fed"] = False
        pl["resources"]["food"] = 10
    s["round"] = 3  # not the last round
    for pl in list(s["players"]):
        s = engine.apply_action(s, pl["player_id"], {"kind": "feed"}).new_state
    # 2 sheep but nowhere to put a newborn → still 2 sheep.
    assert animal_counts(s["players"][0])["sheep"] == 2


def test_breeding_places_newborn(engine):
    s = make_state(engine, 2)
    p = s["players"][0]
    p["fences"] = sorted(set(cell_edges(3)) | set(cell_edges(4)) - {"v-0-4"})
    p["cells"][3]["animal"] = {"type": "boar", "count": 2}
    s["phase"] = "feeding"
    s["round"] = 3
    for pl in s["players"]:
        pl["fed"] = False
        pl["resources"]["food"] = 10
    for pl in list(s["players"]):
        s = engine.apply_action(s, pl["player_id"], {"kind": "feed"}).new_state
    assert animal_counts(s["players"][0])["boar"] == 3


# ── Improvements ─────────────────────────────────────────────────────

def improvement_space():
    return {"id": "major_improvement", "name": "Major Improvement", "desc": "",
            "stage": 1, "occupied_by": None, "supply": {}, "accumulates": False}


def test_build_improvement_and_upgrade(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give(s, first, clay=2)
    s["action_spaces"].append(improvement_space())
    s = place(engine, s, {"kind": "place", "space": "major_improvement",
                          "improvement": "fireplace_2"})
    p = s["players"][first]
    assert "fireplace_2" in p["improvements"]
    assert p["resources"]["clay"] == 0

    # Other player cannot build the same card.
    give(s, s["current_player"], clay=2)
    sp = next(x for x in s["action_spaces"] if x["id"] == "major_improvement")
    sp["occupied_by"] = None
    with pytest.raises(ValueError):
        place(engine, s, {"kind": "place", "space": "major_improvement",
                          "improvement": "fireplace_2"})

    # Upgrade fireplace → cooking hearth (free), fireplace returns to supply.
    sp["occupied_by"] = None
    s["current_player"] = first
    s["players"][first]["people_placed"] = 0
    s = place(engine, s, {"kind": "place", "space": "major_improvement",
                          "improvement": "cooking_hearth_4", "upgrade": True})
    p = s["players"][first]
    assert "cooking_hearth_4" in p["improvements"]
    assert "fireplace_2" not in p["improvements"]
    assert "fireplace_2" in s["available_improvements"]


def test_oven_bake_on_build(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give(s, first, clay=3, stone=1, grain=1)
    s["action_spaces"].append(improvement_space())
    s = place(engine, s, {"kind": "place", "space": "major_improvement",
                          "improvement": "clay_oven", "bake": {"clay_oven": 1}})
    p = s["players"][first]
    assert p["resources"]["food"] >= 5  # 1 grain → 5 food
    assert p["resources"]["grain"] == 0


def test_bake_limits(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["improvements"] += ["clay_oven", "fireplace_2"]
    give(s, first, grain=5)
    s["action_spaces"].append({
        "id": "grain_utilization", "name": "Grain Utilization", "desc": "",
        "stage": 1, "occupied_by": None, "supply": {}, "accumulates": False})
    # Clay oven bakes at most 1 grain per action.
    with pytest.raises(ValueError):
        place(engine, s, {"kind": "place", "space": "grain_utilization",
                          "bake": {"clay_oven": 2}})
    food_before = p["resources"]["food"]
    s = place(engine, s, {"kind": "place", "space": "grain_utilization",
                          "bake": {"clay_oven": 1, "fireplace_2": 2}})
    p = s["players"][first]
    assert p["resources"]["food"] == food_before + 5 + 4
    assert p["resources"]["grain"] == 2


def test_well_round_food(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give(s, first, wood=1, stone=3)
    s["action_spaces"].append(improvement_space())
    s = place(engine, s, {"kind": "place", "space": "major_improvement",
                          "improvement": "well"})
    assert s["round_food"] == {str(r): {str(first): 1} for r in range(2, 7)}
    food_before = s["players"][first]["resources"]["food"]
    # Finish the round → round 2 preparation pays 1 food.
    # (first takes grain_seeds so their food only changes via the Well)
    s = place(engine, s, {"kind": "place", "space": "day_laborer"})
    s = place(engine, s, {"kind": "place", "space": "grain_seeds"})
    s = place(engine, s, {"kind": "place", "space": "fishing"})
    assert s["round"] == 2
    assert s["players"][first]["resources"]["food"] == food_before + 1
    assert str(2) not in s["round_food"]


def test_sow_and_grain_utilization(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["cells"][0]["type"] = "field"
    p["cells"][1]["type"] = "field"
    give(s, first, grain=1, vegetable=1)
    s["action_spaces"].append({
        "id": "grain_utilization", "name": "Grain Utilization", "desc": "",
        "stage": 1, "occupied_by": None, "supply": {}, "accumulates": False})
    s = place(engine, s, {"kind": "place", "space": "grain_utilization",
                          "sow": [{"cell": 0, "crop": "grain"},
                                  {"cell": 1, "crop": "vegetable"}]})
    p = s["players"][first]
    assert p["cells"][0]["crops"] == {"type": "grain", "count": 3}
    assert p["cells"][1]["crops"] == {"type": "vegetable", "count": 2}
    assert p["resources"]["grain"] == 0 and p["resources"]["vegetable"] == 0


def test_cultivation_plow_then_sow(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give(s, first, grain=1)
    s["action_spaces"].append({
        "id": "cultivation", "name": "Cultivation", "desc": "",
        "stage": 5, "occupied_by": None, "supply": {}, "accumulates": False})
    s = place(engine, s, {"kind": "place", "space": "cultivation",
                          "plow": 0, "sow": [{"cell": 0, "crop": "grain"}]})
    p = s["players"][first]
    assert p["cells"][0]["type"] == "field"
    assert p["cells"][0]["crops"] == {"type": "grain", "count": 3}


def test_side_job(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give(s, first, wood=1)
    s = place(engine, s, {"kind": "place", "space": "side_job", "stable": 0})
    p = s["players"][first]
    assert p["cells"][0]["stable"]
    assert p["resources"]["wood"] == 0


# ── Scoring ──────────────────────────────────────────────────────────

def test_scoring_starting_farm():
    from server.agricola.state import create_player
    p = create_player(0, "p", "P")
    sc = score_player(p)
    # -1 fields, -1 pastures, -1 grain, -1 veg, -1 sheep, -1 boar, -1 cattle,
    # -13 unused, 0 stables, 0 rooms (wood), 6 people, 0 improvements, 0 bonus
    assert sc["fields"] == -1
    assert sc["unused_spaces"] == -13
    assert sc["people"] == 6
    assert sc["total"] == -1 * 7 - 13 + 6


def test_scoring_example_categories():
    from server.agricola.state import create_player
    p = create_player(0, "p", "P")
    p["house_type"] = "stone"
    # 4 rooms total.
    p["cells"][0]["type"] = "room"
    p["cells"][1]["type"] = "room"
    p["people_total"] = 5
    # 4 fields (3 pts), two with crops.
    for i in (2, 3, 7, 8):
        p["cells"][i]["type"] = "field"
    p["cells"][2]["crops"] = {"type": "grain", "count": 2}
    p["cells"][3]["crops"] = {"type": "vegetable", "count": 1}
    p["resources"].update({"grain": 2, "vegetable": 0})
    # Two pastures: cells 4 and 9 (adjacent vertically), one with a stable.
    fences = set(cell_edges(4)) | set(cell_edges(9))
    p["fences"] = sorted(fences)
    p["cells"][9]["stable"] = True
    p["cells"][4]["animal"] = {"type": "sheep", "count": 2}
    p["cells"][9]["animal"] = {"type": "boar", "count": 4}
    p["pet"] = "cattle"
    p["improvements"] = ["well", "joinery"]
    p["resources"]["wood"] = 5
    p["begging"] = 1

    sc = score_player(p)
    assert sc["fields"] == 3
    assert sc["pastures"] == 2
    assert sc["grain"] == 2       # 2 supply + 2 on field = 4
    assert sc["vegetable"] == 1   # 1 on field
    assert sc["sheep"] == 1
    assert sc["boar"] == 2        # 4 boar → 2 pts (3-4 range)
    assert sc["cattle"] == 1
    assert sc["rooms"] == 8       # 4 stone rooms
    assert sc["people"] == 15
    assert sc["fenced_stables"] == 1
    assert sc["improvements"] == 6  # well 4 + joinery 2
    assert sc["bonus"] == 2 - 3     # joinery 5 wood → 2, begging -3
    # Unused: 15 - 4 rooms - 4 fields - 2 pastures = 5.
    assert sc["unused_spaces"] == -5


def test_game_over_and_winner(engine):
    s = make_state(engine, 2)
    s["round"] = 14
    s["phase"] = "feeding"
    for pl in s["players"]:
        pl["fed"] = False
        pl["resources"]["food"] = 20
    # Give player 0 a better farm.
    s["players"][0]["cells"][0]["type"] = "field"
    s["players"][0]["cells"][1]["type"] = "field"
    for pl in list(s["players"]):
        s = engine.apply_action(s, pl["player_id"], {"kind": "feed"}).new_state
    assert s["game_over"]
    assert s["phase"] == "game_over"
    assert s["scores"] is not None
    assert s["winners"] == [0]
    assert engine.get_waiting_for(s) == []


# ── Random full-game fuzz ────────────────────────────────────────────

def random_bot_action(engine, state, pid, rng):
    """Pick a random valid action with randomly generated parameters."""
    acts = engine.get_valid_actions(state, pid)
    if not acts:
        return None
    act = rng.choice(acts)
    kind = act["kind"]
    pidx = next(p["index"] for p in state["players"] if p["player_id"] == pid)
    p = state["players"][pidx]

    if kind == "feed":
        # Convert crops raw until fed or out of crops.
        need = act["food_needed"]
        conversions = []
        have = p["resources"]["food"]
        for crop in ("grain", "vegetable"):
            avail = p["resources"][crop]
            take = min(avail, max(0, need - have))
            if take:
                conversions.append({"good": crop, "via": "raw", "count": take})
                have += take
        return {"kind": "feed", "conversions": conversions}

    if kind == "accommodate":
        # Discard everything new; keep current placement (it was valid unless
        # fencing broke it, in which case discard all animals entirely).
        gained = dict(act["gained"])
        from server.agricola.state import validate_animal_placement, animal_counts
        ok, _ = validate_animal_placement(p)
        if ok:
            placements = []
            for i, c in enumerate(p["cells"]):
                if c["animal"]:
                    placements.append({"cell": i, "type": c["animal"]["type"],
                                       "count": c["animal"]["count"]})
            return {"kind": "accommodate", "placements": placements,
                    "pet": p["pet"], "discard": gained}
        totals = animal_counts(p)
        for a, n in gained.items():
            totals[a] += n
        return {"kind": "accommodate", "placements": [],
                "discard": {a: n for a, n in totals.items() if n}}

    # Placement: generate params per space.
    space = act["space"]
    action = {"kind": "place", "space": space}
    if space == "farmland":
        action["cell"] = rng.choice(engine._plowable_cells(p))
    elif space == "farm_expansion":
        cells = engine._buildable_room_cells(p)
        if cells and engine._room_cost_ok(p):
            action["rooms"] = [rng.choice(cells)]
        else:
            free = [i for i, c in enumerate(p["cells"])
                    if c["type"] == "empty" and not c["stable"]]
            action["stables"] = [rng.choice(free)]
    elif space == "side_job":
        if engine._stable_possible(p, 1):
            free = [i for i, c in enumerate(p["cells"])
                    if c["type"] == "empty" and not c["stable"]]
            action["stable"] = rng.choice(free)
        else:
            action["bake"] = bot_bake(p)
    elif space == "fencing":
        fences = bot_fence_plan(p)
        if fences is None:
            return None
        action["fences"] = fences
    elif space == "grain_utilization":
        if engine._can_sow(p) and rng.random() < 0.8:
            action["sow"] = bot_sow(p)
        else:
            action["bake"] = bot_bake(p)
        if not action.get("sow") and not action.get("bake"):
            return None
    elif space == "cultivation":
        if engine._plowable_cells(p):
            action["plow"] = rng.choice(engine._plowable_cells(p))
        if engine._can_sow(p):
            action["sow"] = bot_sow(p)
        if action.get("plow") is None and not action.get("sow"):
            return None
    elif space == "major_improvement":
        options = engine._buildable_improvements(state, p)
        imp = rng.choice(options)
        action["improvement"] = imp
        if not engine._can_afford(p, __import__(
                "server.agricola.state", fromlist=["MAJOR_IMPROVEMENTS"]
        ).MAJOR_IMPROVEMENTS[imp]["cost"]):
            action["upgrade"] = True
    elif space == "house_redevelopment":
        pass  # renovation only
    elif space == "farm_redevelopment":
        pass
    elif space == "resource_market_3p":
        action["choice"] = rng.choice(["reed", "stone"])
    return action


def bot_sow(p):
    sow = []
    crops = {"grain": p["resources"]["grain"],
             "vegetable": p["resources"]["vegetable"]}
    for i, c in enumerate(p["cells"]):
        if c["type"] == "field" and not c["crops"]:
            for crop in ("grain", "vegetable"):
                if crops[crop] > 0:
                    crops[crop] -= 1
                    sow.append({"cell": i, "crop": crop})
                    break
    return sow


def bot_bake(p):
    from server.agricola.state import MAJOR_IMPROVEMENTS
    grain = p["resources"]["grain"]
    bake = {}
    for imp in p["improvements"]:
        spec = MAJOR_IMPROVEMENTS[imp].get("bake")
        if not spec or grain <= 0:
            continue
        limit, _v = spec
        take = grain if limit is None else min(limit, grain)
        if take > 0:
            bake[imp] = take
            grain -= take
    return bake


def bot_fence_plan(p):
    """Fence the first free 1x1 cell, if affordable."""
    from server.agricola.state import (
        cell_edges as ce, validate_fence_layout as vfl)
    existing = set(p["fences"])
    for i, c in enumerate(p["cells"]):
        if c["type"] != "empty":
            continue
        new = [e for e in ce(i) if e not in existing]
        if not new or len(new) > p["resources"]["wood"]:
            continue
        if len(existing) + len(new) > 15:
            continue
        ok, _e, _p = vfl(p, sorted(existing | set(new)))
        if ok:
            return new
    return None


@pytest.mark.parametrize("n_players,seed", [(1, 1), (2, 2), (3, 3), (4, 4),
                                            (2, 5), (3, 6), (4, 7), (2, 8)])
def test_random_full_game(engine, n_players, seed):
    rng = random.Random(seed)
    random.seed(seed)
    ids = [f"p_{i}" for i in range(n_players)]
    names = [f"Bot{i}" for i in range(n_players)]
    s = engine.initial_state(ids, names)

    steps = 0
    while not s["game_over"]:
        steps += 1
        assert steps < 3000, "game did not terminate"
        waiting = engine.get_waiting_for(s)
        assert waiting, f"nobody to act but game not over (phase {s['phase']})"
        pid = waiting[0]
        action = random_bot_action(engine, s, pid, rng)
        if action is None:
            # Bot couldn't produce params; try a safe fallback space.
            acts = engine.get_valid_actions(s, pid)
            simple = [a for a in acts if a["kind"] == "place" and a["space"] in
                      ("day_laborer", "fishing", "grain_seeds", "forest",
                       "clay_pit", "reed_bank", "meeting_place")]
            assert simple, f"no fallback action ({[a.get('space') for a in acts]})"
            action = {"kind": "place", "space": rng.choice(simple)["space"]}
        result = engine.apply_action(s, pid, action)
        s = result.new_state

    assert s["round"] == 14
    assert s["scores"] is not None
    assert len(s["winners"]) >= 1
    # Sanity: score categories sum to totals.
    for sc in s["scores"]:
        cats = [v for k, v in sc.items()
                if k not in ("total", "player_index", "name", "tiebreak_resources")]
        assert sum(cats) == sc["total"]
