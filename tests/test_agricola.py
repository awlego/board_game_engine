"""Tests for the Agricola engine (full game with hand cards)."""

import random

import pytest

from server.agricola.engine import AgricolaEngine
from server.agricola import cards
from server.agricola.state import (
    ANIMAL_TYPES, HARVEST_ROUNDS, MAJOR_IMPROVEMENTS, STAGE_CARDS,
    animal_counts, cell_edges, compute_pastures, pasture_capacity,
    validate_fence_layout, validate_animal_placement, create_player,
)
from server.agricola.scoring import score_player


@pytest.fixture
def engine():
    return AgricolaEngine()


@pytest.fixture
def temp_card():
    """Register a throwaway card spec (cards.card(...) args/kwargs) for
    one test, then unregister it -- otherwise it leaks into the global
    cards.CARDS registry and breaks test_agricola_catalog.py, which
    exports every registered card to the client catalog."""
    registered = []

    def _register(cid, *args, **kwargs):
        spec = cards.card(cid, *args, **kwargs)
        registered.append(cid)
        return spec

    yield _register
    for cid in registered:
        cards.CARDS.pop(cid, None)


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
    """Put a specific card into a player's hand."""
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


SAFE_SPACES = ("day_laborer", "fishing", "grain_seeds", "meeting_place",
               "forest", "clay_pit", "reed_bank", "traveling_players",
               "western_quarry", "eastern_quarry", "vegetable_seeds",
               "copse", "grove", "hollow_3p", "hollow_4p")


# ── Setup ────────────────────────────────────────────────────────────

def test_initial_setup(engine):
    s = make_state(engine, 2)
    assert s["round"] == 1
    assert len(s["deck"]) == 14
    stages = [STAGE_CARDS[c]["stage"] for c in s["deck"]]
    assert stages == sorted(stages)
    assert len(s["revealed"]) == 1
    assert STAGE_CARDS[s["revealed"][0]]["stage"] == 1

    dealt_occs = []
    dealt_minors = []
    for p in s["players"]:
        assert p["people_total"] == 2
        rooms = [i for i, c in enumerate(p["cells"]) if c["type"] == "room"]
        assert rooms == [5, 10]
        # Full game: 7 occupations + 7 minor improvements in hand.
        assert len(p["hand_occupations"]) == 7
        assert len(p["hand_minors"]) == 7
        dealt_occs += p["hand_occupations"]
        dealt_minors += p["hand_minors"]
        # 2-player game: no occ-3/occ-4 cards dealt.
        for cid in p["hand_occupations"]:
            assert cards.CARDS[cid]["min_players"] <= 2
    # No duplicates across players.
    assert len(set(dealt_occs)) == len(dealt_occs)
    assert len(set(dealt_minors)) == len(dealt_minors)

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
    assert "lessons" in ids2 and "lessons_b" not in ids2
    assert "side_job" not in ids2  # beginner-variant tile removed
    assert {"grove", "hollow_3p", "resource_market_3p", "lessons_b"} <= ids3
    assert {"copse", "grove", "hollow_4p", "resource_market_4p",
            "traveling_players", "lessons_b"} <= ids4
    assert "hollow_3p" not in ids4


def test_hands_hidden_in_views(engine):
    s = make_state(engine, 2)
    pid0 = s["players"][0]["player_id"]
    view = engine.get_player_view(s, pid0)
    assert isinstance(view["players"][0]["hand_occupations"], list)
    assert isinstance(view["players"][1]["hand_occupations"], int)
    spec_view = engine.get_spectator_view(s)
    assert all(isinstance(p["hand_occupations"], int)
               for p in spec_view["players"])


# ── Work phase basics ────────────────────────────────────────────────

def test_accumulation_and_turn_order(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    s = place(engine, s, {"kind": "place", "space": "forest"})
    assert s["players"][first]["resources"]["wood"] == 3
    assert s["current_player"] == (first + 1) % 2
    pid = current_pid(engine, s)
    with pytest.raises(ValueError):
        engine.apply_action(s, pid, {"kind": "place", "space": "forest"})


def test_meeting_place_starting_player(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    s = place(engine, s, {"kind": "place", "space": "day_laborer"})
    s = place(engine, s, {"kind": "place", "space": "meeting_place"})
    assert s["starting_player"] == other
    s = place(engine, s, {"kind": "place", "space": "forest"})
    s = place(engine, s, {"kind": "place", "space": "fishing"})
    assert s["round"] == 2
    assert s["current_player"] == other


def test_meeting_place_plays_minor(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give_card(s, first, "minor_clay_deposit")
    give(s, first, food=1)
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "minor_clay_deposit"}})
    p = s["players"][first]
    assert s["starting_player"] == first
    assert p["resources"]["clay"] == 3
    assert any(i["id"] == "minor_clay_deposit" for i in p["minors"])
    assert "minor_clay_deposit" not in p["hand_minors"]


# ── Occupations and Lessons ──────────────────────────────────────────

def test_lessons_costs(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    give_card(s, first, "occ_woodcutter")
    give_card(s, first, "occ_cook")
    food = p["resources"]["food"]
    # First occupation is free on the base Lessons space.
    s = place(engine, s, {"kind": "place", "space": "lessons",
                          "card": "occ_woodcutter"})
    p = s["players"][first]
    assert p["resources"]["food"] == food
    assert p["occs_played"] == 1
    assert any(i["id"] == "occ_woodcutter" for i in p["occupations"])
    # Second occupation costs 1 food; finish the round first.
    s = place(engine, s, {"kind": "place", "space": "day_laborer"})
    lessons = next(sp for sp in s["action_spaces"] if sp["id"] == "lessons")
    lessons["occupied_by"] = None
    s["current_player"] = first
    s = place(engine, s, {"kind": "place", "space": "lessons",
                          "card": "occ_cook"})
    p = s["players"][first]
    assert p["resources"]["food"] == food - 1
    assert p["occs_played"] == 2


def test_lessons_b_costs(engine):
    s = make_state(engine, 4, seed=9)
    first = s["current_player"]
    p = s["players"][first]
    cid = p["hand_occupations"][0]
    food_before = p["resources"]["food"]
    # 4p lessons_b: first two occupations cost 1 food.
    s = place(engine, s, {"kind": "place", "space": "lessons_b", "card": cid})
    assert s["players"][first]["resources"]["food"] == food_before - 1


def test_tutor_discount(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "occ_tutor")
    p["occs_played"] = 1  # not the first occupation anymore
    give_card(s, first, "occ_cook")
    food = p["resources"]["food"]
    s = place(engine, s, {"kind": "place", "space": "lessons",
                          "card": "occ_cook"})
    # 1 food base - 1 tutor = free
    assert s["players"][first]["resources"]["food"] == food


def test_woodcutter_take_bonus(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "occ_woodcutter")
    s = place(engine, s, {"kind": "place", "space": "forest"})
    assert s["players"][first]["resources"]["wood"] == 4  # 3 + 1


def test_fisherman_space_bonus(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "occ_fisherman")
    food = s["players"][first]["resources"]["food"]
    s = place(engine, s, {"kind": "place", "space": "fishing"})
    assert s["players"][first]["resources"]["food"] == food + 1 + 2


def test_on_play_gain_routes_animals_to_accommodation(engine):
    # on_play_gain must not write animal goods into player["resources"]
    # (they never live there); they should be queued as extras and left
    # for the engine to route through the normal accommodation prompt.
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    inst = cards.new_instance("occ_woodcutter")  # any instance works
    hook = cards.on_play_gain({"sheep": 2, "wood": 1})
    ctx = {"log": [], "actor": first, "extra": {}}
    hook(s, p, inst, ctx)
    assert ctx["extra"] == {"sheep": 2}
    assert p["resources"]["wood"] == 1
    assert "sheep" not in p["resources"]


def test_space_bonus_others_routes_animals_to_accommodation(engine):
    # space_bonus(..., others=True) grants goods to the card OWNER when
    # another player uses a watched space. Animal goods must not be
    # written into the owner's resources -- they should be queued via an
    # accommodation prompt for the owner instead.
    s = make_state(engine, 2)
    owner_idx, actor_idx = 0, 1
    owner = s["players"][owner_idx]
    inst = cards.new_instance("occ_woodcutter")
    hooks = cards.space_bonus(["sheep_market"], {"cattle": 1}, others=True)
    ctx = {"space_id": "sheep_market", "goods": {"sheep": 1}, "extra": {},
           "log": [], "actor": actor_idx}
    hooks["space_used"](s, owner, inst, ctx)
    assert "cattle" not in owner["resources"]
    assert s["prompts"] == [{"type": "accommodate", "player": owner_idx,
                             "gained": {"cattle": 1}}]


def test_small_scale_farmer_round_income(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "occ_small_scale_farmer")
    wood = s["players"][first]["resources"]["wood"]
    for sp in ["day_laborer", "fishing", "grain_seeds", "meeting_place"]:
        s = place(engine, s, {"kind": "place", "space": sp})
    assert s["round"] == 2
    # 2 rooms → +1 wood at the start of round 2.
    assert s["players"][first]["resources"]["wood"] == wood + 1


def test_hedge_keeper_free_fences(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "occ_hedge_keeper")
    give(s, first, wood=1)
    add_space(s, "fencing", "Fencing")
    # 4 fences, 3 free via Hedge Keeper → costs 1 wood.
    s = place(engine, s, {"kind": "place", "space": "fencing",
                          "fences": cell_edges(4)})
    p = s["players"][first]
    assert p["resources"]["wood"] == 0
    assert compute_pastures(p) == [[4]]


def test_stonecutter_cost_mod(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "occ_stonecutter")
    give(s, first, wood=1, stone=2)  # well costs 1 wood + 3 stone
    add_space(s, "major_improvement", "Major Improvement")
    s = place(engine, s, {"kind": "place", "space": "major_improvement",
                          "improvement": "well"})
    assert "well" in s["players"][first]["improvements"]


def test_animal_tamer_house_capacity(engine):
    p = create_player(0, "p", "P")
    p["occupations"].append(cards.new_instance("occ_animal_tamer"))
    p["pets"] = {"sheep": 1, "boar": 1}
    ok, err = validate_animal_placement(
        p, house_cap=cards.house_capacity(p))
    assert ok, err  # 2 rooms → 2 pets allowed
    p["pets"] = {"sheep": 2, "boar": 1}
    ok, err = validate_animal_placement(
        p, house_cap=cards.house_capacity(p))
    assert not ok


def test_cook_raw_values(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "occ_cook")
    p["resources"].update({"food": 0, "grain": 2})
    s["phase"] = "feeding"
    for pl in s["players"]:
        pl["fed"] = False
    s = engine.apply_action(s, p["player_id"], {
        "kind": "feed",
        "conversions": [{"good": "grain", "via": "raw", "count": 2}],
    }).new_state
    p = s["players"][first]
    # 2 grain × 2 food (Cook) = 4 food, need 4 → 0 left, no begging.
    assert p["resources"]["food"] == 0
    assert p["begging"] == 0


def test_baker_bake_bonus(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "occ_baker")
    p["improvements"].append("fireplace_2")
    s["available_improvements"].remove("fireplace_2")
    give(s, first, grain=2)
    add_space(s, "grain_utilization", "Grain Utilization")
    food = p["resources"]["food"]
    s = place(engine, s, {"kind": "place", "space": "grain_utilization",
                          "bake": {"fireplace_2": 2}})
    # 2 grain × (2 + 1 Baker) = 6 food
    assert s["players"][first]["resources"]["food"] == food + 6


def test_house_steward_play_and_score(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give_card(s, first, "occ_house_steward")
    wood = s["players"][first]["resources"]["wood"]
    s = place(engine, s, {"kind": "place", "space": "lessons",
                          "card": "occ_house_steward"})
    # Round 1 → 13 rounds remain → +4 wood.
    assert s["players"][first]["resources"]["wood"] == wood + 4
    sc = score_player(s["players"][first], s)
    assert sc["bonus"] >= 3  # tied for most rooms


# ── Minor improvements ───────────────────────────────────────────────

def test_minor_prereq_enforced(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give_card(s, first, "minor_loom")  # needs 2 occupations
    give(s, first, wood=2)
    add_space(s, "major_improvement", "Major Improvement")
    with pytest.raises(ValueError):
        place(engine, s, {"kind": "place", "space": "major_improvement",
                          "minor": {"card": "minor_loom"}})
    put_in_play(s, first, "occ_woodcutter")
    put_in_play(s, first, "occ_cook")
    s = place(engine, s, {"kind": "place", "space": "major_improvement",
                          "minor": {"card": "minor_loom"}})
    assert any(i["id"] == "minor_loom" for i in s["players"][first]["minors"])


# ── Occupations ──────────────────────────────────────────────────────

def test_occupation_prereq_enforced(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give(s, first, food=10)
    p = s["players"][first]
    p["hand_occupations"] = ["B154"]  # Sheep Keeper: needs fewer than 7 sheep
    p["pets"] = {"sheep": 7}
    # No occupation in hand meets its prereq, so Lessons isn't even offered.
    actions = engine.get_valid_actions(s, p["player_id"])
    assert not any(a["kind"] == "place" and a["space"] == "lessons"
                  for a in actions)
    with pytest.raises(ValueError):
        place(engine, s, {"kind": "place", "space": "lessons", "card": "B154"})
    p["pets"] = {"sheep": 3}
    s = place(engine, s, {"kind": "place", "space": "lessons", "card": "B154"})
    assert any(i["id"] == "B154" for i in s["players"][first]["occupations"])


def test_traveling_card_passes_left(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    give_card(s, first, "minor_market_stall")
    give(s, first, grain=1)
    add_space(s, "major_improvement", "Major Improvement")
    veg = s["players"][first]["resources"]["vegetable"]
    s = place(engine, s, {"kind": "place", "space": "major_improvement",
                          "minor": {"card": "minor_market_stall"}})
    p = s["players"][first]
    assert p["resources"]["vegetable"] == veg + 1
    assert p["resources"]["grain"] == 0
    # Travels to the left player's hand, not into play.
    assert not any(i["id"] == "minor_market_stall" for i in p["minors"])
    assert "minor_market_stall" in s["players"][other]["hand_minors"]


def test_shifting_cultivation_plows(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give_card(s, first, "minor_shifting_cultivation")
    give(s, first, food=2)
    add_space(s, "major_improvement", "Major Improvement")
    s = place(engine, s, {"kind": "place", "space": "major_improvement",
                          "minor": {"card": "minor_shifting_cultivation",
                                    "params": {"cell": 0}}})
    assert s["players"][first]["cells"][0]["type"] == "field"


def test_beanfield_card_field(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "occ_woodcutter")
    put_in_play(s, first, "occ_cook")
    give_card(s, first, "minor_beanfield")
    give(s, first, food=1, grain=1, vegetable=1)
    add_space(s, "major_improvement", "Major Improvement")
    add_space(s, "grain_utilization", "Grain Utilization")
    s = place(engine, s, {"kind": "place", "space": "major_improvement",
                          "minor": {"card": "minor_beanfield"}})
    # Sow a vegetable on the Beanfield card.
    s = place(engine, s, {"kind": "place", "space": "day_laborer"})
    s = place(engine, s, {"kind": "place", "space": "grain_utilization",
                          "sow": [{"card": "minor_beanfield",
                                   "crop": "vegetable"}]})
    p = s["players"][first]
    inst = next(i for i in p["minors"] if i["id"] == "minor_beanfield")
    assert inst["crops"] == {"type": "vegetable", "count": 2}
    # Grain is not allowed on the Beanfield.
    with pytest.raises(ValueError):
        engine._do_sow(s, p, [{"card": "minor_beanfield", "crop": "grain"}], [])
    # Scoring counts the planted vegetables.
    sc = score_player(p, s)
    assert sc["vegetable"] >= 2


def test_pond_hut_schedules_food(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "occ_woodcutter")
    put_in_play(s, first, "occ_cook")
    give_card(s, first, "minor_pond_hut")
    give(s, first, wood=1)
    add_space(s, "major_improvement", "Major Improvement")
    s = place(engine, s, {"kind": "place", "space": "major_improvement",
                          "minor": {"card": "minor_pond_hut"}})
    # Food scheduled on rounds 2, 3, 4.
    for r in ("2", "3", "4"):
        assert s["round_goods"][r][str(first)]["food"] == 1


def test_pond_hut_exact_occupations_prereq(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    for cid in ("occ_woodcutter", "occ_cook", "occ_baker"):
        put_in_play(s, first, cid)  # 3 occupations — exactly 2 required
    give_card(s, first, "minor_pond_hut")
    give(s, first, wood=1)
    add_space(s, "major_improvement", "Major Improvement")
    with pytest.raises(ValueError):
        place(engine, s, {"kind": "place", "space": "major_improvement",
                          "minor": {"card": "minor_pond_hut"}})


def test_drinking_trough_capacity(engine):
    p = create_player(0, "p", "P")
    p["minors"].append(cards.new_instance("minor_drinking_trough"))
    p["fences"] = sorted(cell_edges(4))
    bonus = cards.pasture_bonus(p)
    assert bonus == 2
    assert pasture_capacity(p, [4], bonus) == 4  # 2 base + 2


def test_mining_hammer_free_stable_on_renovation(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "minor_mining_hammer")
    give(s, first, clay=2, reed=1)
    add_space(s, "house_redevelopment", "House Redevelopment")
    s = place(engine, s, {"kind": "place", "space": "house_redevelopment",
                          "stable": 0})
    p = s["players"][first]
    assert p["house_type"] == "clay"
    assert p["cells"][0]["stable"]
    assert p["resources"]["wood"] == 0  # stable was free


def test_shepherds_crook(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "minor_shepherds_crook")
    give(s, first, wood=10)
    add_space(s, "fencing", "Fencing")
    # Fence a 2x2 pasture (cells 3, 4, 8, 9) — 8 fences.
    fences = set()
    for c in (3, 4, 8, 9):
        fences |= set(cell_edges(c))
    fences -= {"v-0-4", "v-1-4", "h-1-3", "h-1-4"}
    s = place(engine, s, {"kind": "place", "space": "fencing",
                          "fences": sorted(fences)})
    # 2 sheep granted → accommodation pending.
    assert s["prompts"][0]["gained"] == {"sheep": 2}
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {
        "kind": "accommodate",
        "placements": [{"cell": 3, "type": "sheep", "count": 2}],
    }).new_state
    assert animal_counts(s["players"][first])["sheep"] == 2


def test_lasso_double_placement(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "minor_lasso")
    sheep_sp = next((sp for sp in s["action_spaces"]
                     if sp["id"] == "sheep_market"), None)
    if sheep_sp is None:
        add_space(s, "sheep_market", "Sheep Market", acc=True,
                  supply={"sheep": 1})
        sheep_sp = s["action_spaces"][-1]
    else:
        sheep_sp["supply"] = {"sheep": 1}
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {
        "kind": "place", "space": "sheep_market", "lasso": True}).new_state
    # Accommodate the sheep; afterward it is STILL the same player's turn.
    s = engine.apply_action(s, pid, {
        "kind": "accommodate", "pets": {"sheep": 1}}).new_state
    assert s["current_player"] == first
    s = engine.apply_action(s, pid, {
        "kind": "place", "space": "day_laborer"}).new_state
    # Now the other player finally gets a turn.
    assert s["current_player"] == (first + 1) % 2


def test_milk_jug_other_player_trigger(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    put_in_play(s, other, "minor_milk_jug")
    add_space(s, "cattle_market", "Cattle Market", acc=True,
              supply={"cattle": 1})
    food = s["players"][other]["resources"]["food"]
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {
        "kind": "place", "space": "cattle_market"}).new_state
    # The card owner (other player) got 2 food from the actor's use.
    assert s["players"][other]["resources"]["food"] == food + 2


def test_harvest_totem_custom_card(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    for cid in ("occ_woodcutter", "occ_cook", "occ_baker"):
        put_in_play(s, first, cid)
    p["cells"][0]["type"] = "field"
    p["cells"][0]["crops"] = {"type": "grain", "count": 1}
    put_in_play(s, first, "minor_harvest_totem")
    give_card(s, first, "occ_tutor")
    give(s, first, food=2)
    s = place(engine, s, {"kind": "place", "space": "lessons",
                          "card": "occ_tutor"})
    # Harvest Totem grants a wild boar on playing an occupation.
    assert s["prompts"][0]["gained"] == {"boar": 1}


# ── Farm development (unchanged core rules) ──────────────────────────

def test_plow_adjacency(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    s = place(engine, s, {"kind": "place", "space": "farmland", "cell": 0})
    assert s["players"][first]["cells"][0]["type"] == "field"
    s = place(engine, s, {"kind": "place", "space": "day_laborer"})
    s = place(engine, s, {"kind": "place", "space": "fishing"})
    s = place(engine, s, {"kind": "place", "space": "grain_seeds"})
    assert s["round"] == 2
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
    with pytest.raises(ValueError):
        place(engine, s, {"kind": "place", "space": "farm_expansion", "rooms": [4]})
    s = place(engine, s, {"kind": "place", "space": "farm_expansion",
                          "rooms": [0, 1]})
    p = s["players"][first]
    assert sum(1 for c in p["cells"] if c["type"] == "room") == 4
    assert p["resources"]["wood"] == 0 and p["resources"]["reed"] == 0

    add_space(s, "basic_wish", "Basic Wish for Children")
    s = place(engine, s, {"kind": "place", "space": "day_laborer"})
    s = place(engine, s, {"kind": "place", "space": "basic_wish"})
    p = s["players"][first]
    assert p["people_total"] == 3
    assert p["newborns"] == 1


def test_family_growth_requires_room(engine):
    s = make_state(engine, 2)
    add_space(s, "basic_wish", "Basic Wish for Children")
    pid = current_pid(engine, s)
    with pytest.raises(ValueError):
        engine.apply_action(s, pid, {"kind": "place", "space": "basic_wish"})
    assert "basic_wish" not in {a["space"] for a in engine.get_valid_actions(s, pid)}


def test_caravan_provides_room(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "minor_caravan")
    add_space(s, "basic_wish", "Basic Wish for Children")
    # 2 rooms + caravan = room for 3 → growth allowed at 2 people.
    s = place(engine, s, {"kind": "place", "space": "basic_wish"})
    assert s["players"][first]["people_total"] == 3


def test_urgent_wish_no_room_needed(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    add_space(s, "urgent_wish", "Urgent Wish for Children")
    s = place(engine, s, {"kind": "place", "space": "urgent_wish"})
    assert s["players"][first]["people_total"] == 3


def test_renovation(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give(s, first, clay=2, reed=1)
    add_space(s, "house_redevelopment", "House Redevelopment")
    s = place(engine, s, {"kind": "place", "space": "house_redevelopment"})
    p = s["players"][first]
    assert p["house_type"] == "clay"
    assert p["resources"]["clay"] == 0 and p["resources"]["reed"] == 0
    give(s, first, clay=5, reed=2)
    s = place(engine, s, {"kind": "place", "space": "day_laborer"})
    s = place(engine, s, {"kind": "place", "space": "farm_expansion", "rooms": [0]})
    assert s["players"][first]["cells"][0]["type"] == "room"
    assert s["players"][first]["resources"]["clay"] == 0


def test_renovation_with_improvement(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give(s, first, clay=4, reed=1)
    add_space(s, "house_redevelopment", "House Redevelopment")
    s = place(engine, s, {"kind": "place", "space": "house_redevelopment",
                          "improvement": "fireplace_2"})
    p = s["players"][first]
    assert p["house_type"] == "clay"
    assert "fireplace_2" in p["improvements"]
    assert "fireplace_2" not in s["available_improvements"]


# ── Fencing and pastures ─────────────────────────────────────────────

def test_fence_single_pasture(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    give(s, first, wood=4)
    add_space(s, "fencing", "Fencing")
    s = place(engine, s, {"kind": "place", "space": "fencing",
                          "fences": cell_edges(4)})
    p = s["players"][first]
    assert p["resources"]["wood"] == 0
    assert compute_pastures(p) == [[4]]
    assert pasture_capacity(p, [4]) == 2


def test_fence_validation():
    p = create_player(0, "p", "P")
    ok, err, _ = validate_fence_layout(p, ["h-0-0"])
    assert not ok
    ok, err, _ = validate_fence_layout(p, cell_edges(5))
    assert not ok
    fences = set(cell_edges(3)) | set(cell_edges(4))
    fences.discard("v-0-4")
    ok, err, pastures = validate_fence_layout(p, sorted(fences))
    assert ok, err
    assert pastures == [[3, 4]]
    fences = list(cell_edges(0)) + list(cell_edges(14))
    ok, err, _ = validate_fence_layout(p, fences)
    assert not ok


def test_fence_subdivision_capacity():
    p = create_player(0, "p", "P")
    fences = set(cell_edges(3)) | set(cell_edges(4))
    fences.discard("v-0-4")
    p["fences"] = sorted(fences)
    assert compute_pastures(p) == [[3, 4]]
    assert pasture_capacity(p, [3, 4]) == 4
    p["fences"] = sorted(fences | {"v-0-4"})
    assert compute_pastures(p) == [[3], [4]]
    p["cells"][3]["stable"] = True
    assert pasture_capacity(p, [3]) == 4


def test_fencing_strands_animals_forces_accommodate(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    give(s, first, wood=1)
    fences = set(cell_edges(3)) | set(cell_edges(4))
    fences.discard("v-0-4")
    p["fences"] = sorted(fences)
    p["cells"][3]["animal"] = {"type": "sheep", "count": 4}
    add_space(s, "fencing", "Fencing")
    s = place(engine, s, {"kind": "place", "space": "fencing",
                          "fences": ["v-0-4"]})
    prompt = s["prompts"][0]
    assert (prompt["type"], prompt["player"], prompt["gained"]) == \
        ("accommodate", first, {})
    pid = p["player_id"]
    s = engine.apply_action(s, pid, {
        "kind": "accommodate",
        "placements": [{"cell": 3, "type": "sheep", "count": 2},
                       {"cell": 4, "type": "sheep", "count": 2}],
    }).new_state
    assert s["prompts"] == []


# ── Animals ──────────────────────────────────────────────────────────

def sheep_pasture_state(engine, n_sheep=0):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["fences"] = sorted(cell_edges(4))
    if n_sheep:
        p["cells"][4]["animal"] = {"type": "sheep", "count": n_sheep}
    return s, first


def set_sheep_market(s, count):
    sp = next((x for x in s["action_spaces"] if x["id"] == "sheep_market"), None)
    if sp is None:
        add_space(s, "sheep_market", "Sheep Market", acc=True,
                  supply={"sheep": count})
    else:
        sp["supply"] = {"sheep": count}


def test_take_sheep_accommodate(engine):
    s, first = sheep_pasture_state(engine)
    set_sheep_market(s, 2)
    s = place(engine, s, {"kind": "place", "space": "sheep_market"})
    assert s["prompts"][0]["gained"] == {"sheep": 2}
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {
        "kind": "accommodate",
        "placements": [{"cell": 4, "type": "sheep", "count": 2}],
    }).new_state
    assert animal_counts(s["players"][first])["sheep"] == 2


def test_accommodate_overflow_rejected(engine):
    s, first = sheep_pasture_state(engine)
    pid = s["players"][first]["player_id"]
    set_sheep_market(s, 3)
    s = place(engine, s, {"kind": "place", "space": "sheep_market"})
    with pytest.raises(ValueError):
        engine.apply_action(s, pid, {
            "kind": "accommodate",
            "placements": [{"cell": 4, "type": "sheep", "count": 3}]})
    s = engine.apply_action(s, pid, {
        "kind": "accommodate", "pets": {"sheep": 1},
        "placements": [{"cell": 4, "type": "sheep", "count": 2}]}).new_state
    assert animal_counts(s["players"][first])["sheep"] == 3


def test_accommodate_discard_and_cook(engine):
    s, first = sheep_pasture_state(engine)
    p = s["players"][first]
    p["improvements"].append("fireplace_2")
    s["available_improvements"].remove("fireplace_2")
    pid = p["player_id"]
    set_sheep_market(s, 4)
    food_before = p["resources"]["food"]
    s = place(engine, s, {"kind": "place", "space": "sheep_market"})
    s = engine.apply_action(s, pid, {
        "kind": "accommodate",
        "cook": {"sheep": 1},
        "discard": {"sheep": 1},
        "placements": [{"cell": 4, "type": "sheep", "count": 2}],
    }).new_state
    p = s["players"][first]
    assert p["resources"]["food"] == food_before + 2
    assert animal_counts(p)["sheep"] == 2


def test_mixed_types_in_pasture_rejected():
    p = create_player(0, "p", "P")
    fences = set(cell_edges(3)) | set(cell_edges(4))
    fences.discard("v-0-4")
    p["fences"] = sorted(fences)
    p["cells"][3]["animal"] = {"type": "sheep", "count": 1}
    p["cells"][4]["animal"] = {"type": "boar", "count": 1}
    ok, err = validate_animal_placement(p)
    assert not ok


def test_unfenced_stable_holds_one():
    p = create_player(0, "p", "P")
    p["cells"][0]["stable"] = True
    p["cells"][0]["animal"] = {"type": "cattle", "count": 1}
    ok, err = validate_animal_placement(p)
    assert ok, err
    p["cells"][0]["animal"]["count"] = 2
    ok, err = validate_animal_placement(p)
    assert not ok
    p["cells"][0]["animal"] = None
    p["cells"][1]["animal"] = {"type": "cattle", "count": 1}
    ok, err = validate_animal_placement(p)
    assert not ok


# ── Harvest ──────────────────────────────────────────────────────────

def fast_forward_to_harvest(engine, s):
    guard = 0
    while s["phase"] == "work":
        pid = current_pid(engine, s)
        acts = engine.get_valid_actions(s, pid)
        chosen = next(a for a in acts if a["space"] in SAFE_SPACES)
        s = engine.apply_action(s, pid, {"kind": "place", "space": chosen["space"]}).new_state
        guard += 1
        assert guard < 200
    return s


def test_harvest_field_feeding_breeding(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["cells"][0]["type"] = "field"
    p["cells"][0]["crops"] = {"type": "grain", "count": 3}
    p["fences"] = sorted(set(cell_edges(3)) | set(cell_edges(4)) - {"v-0-4"})
    p["cells"][3]["animal"] = {"type": "sheep", "count": 2}
    give(s, first, food=10)
    give(s, (first + 1) % 2, food=10)

    while s["round"] < 4 or s["phase"] == "work":
        if s["phase"] != "work":
            break
        s = fast_forward_to_harvest(engine, s)
    assert s["phase"] == "feeding"

    p = s["players"][first]
    assert p["cells"][0]["crops"]["count"] == 2
    assert p["resources"]["grain"] >= 1

    for pl in list(s["players"]):
        pid = pl["player_id"]
        if not s["players"][pl["index"]]["fed"]:
            s = engine.apply_action(s, pid, {"kind": "feed"}).new_state
    assert s["round"] == 5
    p = s["players"][first]
    assert animal_counts(p)["sheep"] == 3
    assert p["begging"] == 0


def test_feeding_shortfall_begging(engine):
    s = make_state(engine, 2)
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
    total_begging = sum(p["begging"] for p in s["players"])
    assert total_begging > 0


def test_newborn_feeding_discount(engine):
    s = make_state(engine, 2)
    p = s["players"][0]
    p["people_total"] = 3
    p["newborns"] = 1
    assert engine._food_needed(s, p) == 5
    s["player_count"] = 1
    assert engine._food_needed(s, p) == 7


def test_breeding_needs_room(engine):
    s = make_state(engine, 2)
    p = s["players"][0]
    p["pets"] = {"sheep": 1}
    p["cells"][0]["stable"] = True
    p["cells"][0]["animal"] = {"type": "sheep", "count": 1}
    s["phase"] = "feeding"
    for pl in s["players"]:
        pl["fed"] = False
        pl["resources"]["food"] = 10
    s["round"] = 3
    for pl in list(s["players"]):
        s = engine.apply_action(s, pl["player_id"], {"kind": "feed"}).new_state
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


def test_deaconess_harvest_income(engine):
    s = make_state(engine, 2)
    put_in_play(s, 0, "occ_deaconess")
    for p in s["players"]:
        p["resources"]["food"] = 10
    s["round"] = 4
    s["phase"] = "work"
    for p in s["players"]:
        p["people_placed"] = p["people_total"]
    wood = s["players"][0]["resources"]["wood"]
    food = s["players"][0]["resources"]["food"]
    log = []
    engine._end_work_phase(s, log)
    # First harvest, 2 family members → +1 wood, +1 food.
    assert s["players"][0]["resources"]["wood"] == wood + 1
    assert s["players"][0]["resources"]["food"] == food + 1


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

    give(s, s["current_player"], clay=2)
    sp = next(x for x in s["action_spaces"] if x["id"] == "major_improvement")
    sp["occupied_by"] = None
    with pytest.raises(ValueError):
        place(engine, s, {"kind": "place", "space": "major_improvement",
                          "improvement": "fireplace_2"})

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
    assert p["resources"]["food"] >= 5
    assert p["resources"]["grain"] == 0


def test_bake_limits(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["improvements"] += ["clay_oven", "fireplace_2"]
    give(s, first, grain=5)
    add_space(s, "grain_utilization", "Grain Utilization")
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
    assert s["round_goods"] == {
        str(r): {str(first): {"food": 1}} for r in range(2, 7)}
    food_before = s["players"][first]["resources"]["food"]
    s = place(engine, s, {"kind": "place", "space": "day_laborer"})
    s = place(engine, s, {"kind": "place", "space": "grain_seeds"})
    s = place(engine, s, {"kind": "place", "space": "fishing"})
    assert s["round"] == 2
    assert s["players"][first]["resources"]["food"] == food_before + 1
    assert str(2) not in s["round_goods"]


def test_sow_and_grain_utilization(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["cells"][0]["type"] = "field"
    p["cells"][1]["type"] = "field"
    give(s, first, grain=1, vegetable=1)
    add_space(s, "grain_utilization", "Grain Utilization")
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
    add_space(s, "cultivation", "Cultivation")
    s = place(engine, s, {"kind": "place", "space": "cultivation",
                          "plow": 0, "sow": [{"cell": 0, "crop": "grain"}]})
    p = s["players"][first]
    assert p["cells"][0]["type"] == "field"
    assert p["cells"][0]["crops"] == {"type": "grain", "count": 3}


# ── Scoring ──────────────────────────────────────────────────────────

def test_scoring_starting_farm():
    p = create_player(0, "p", "P")
    sc = score_player(p)
    assert sc["fields"] == -1
    assert sc["unused_spaces"] == -13
    assert sc["people"] == 6
    assert sc["total"] == -1 * 7 - 13 + 6


def test_scoring_example_categories():
    p = create_player(0, "p", "P")
    p["house_type"] = "stone"
    p["cells"][0]["type"] = "room"
    p["cells"][1]["type"] = "room"
    p["people_total"] = 5
    for i in (2, 3, 7, 8):
        p["cells"][i]["type"] = "field"
    p["cells"][2]["crops"] = {"type": "grain", "count": 2}
    p["cells"][3]["crops"] = {"type": "vegetable", "count": 1}
    p["resources"].update({"grain": 2, "vegetable": 0})
    fences = set(cell_edges(4)) | set(cell_edges(9))
    p["fences"] = sorted(fences)
    p["cells"][9]["stable"] = True
    p["cells"][4]["animal"] = {"type": "sheep", "count": 2}
    p["cells"][9]["animal"] = {"type": "boar", "count": 4}
    p["pets"] = {"cattle": 1}
    p["improvements"] = ["well", "joinery"]
    p["resources"]["wood"] = 5
    p["begging"] = 1

    sc = score_player(p)
    assert sc["fields"] == 3
    assert sc["pastures"] == 2
    assert sc["grain"] == 2
    assert sc["vegetable"] == 1
    assert sc["sheep"] == 1
    assert sc["boar"] == 2
    assert sc["cattle"] == 1
    assert sc["rooms"] == 8
    assert sc["people"] == 15
    assert sc["fenced_stables"] == 1
    assert sc["improvements"] == 6
    assert sc["bonus"] == 2 - 3
    assert sc["unused_spaces"] == -5


def test_card_points_and_bonuses(engine):
    s = make_state(engine, 2)
    p = s["players"][0]
    put_in_play(s, 0, "minor_loom")       # 1 printed point + 1/3 sheep
    put_in_play(s, 0, "minor_hermits_stick")  # exactly 2 people → 4 bonus
    p["fences"] = sorted(cell_edges(4))
    p["cells"][4]["animal"] = {"type": "sheep", "count": 2}
    p["pets"] = {"sheep": 1}
    sc = score_player(p, s)
    assert sc["improvements"] == 1        # loom printed point
    # 3 sheep → 1 loom bonus; 2 people → 4 hermit's stick bonus.
    assert sc["bonus"] == 1 + 4


def test_braggart_score(engine):
    s = make_state(engine, 2)
    p = s["players"][0]
    put_in_play(s, 0, "occ_braggart")
    p["improvements"] = ["fireplace_2", "well", "joinery"]
    for cid in ("minor_loom", "minor_basket"):
        put_in_play(s, 0, cid)
    # 3 majors + 2 minors = 5 improvements → 2 bonus points.
    assert cards.CARDS["occ_braggart"]["score_bonus"](s, p, None) == 2


def test_estate_manager_score(engine):
    s = make_state(engine, 2)
    p0, p1 = s["players"]
    put_in_play(s, 0, "occ_estate_manager")
    p0["pets"] = {"sheep": 1}
    p0["fences"] = sorted(cell_edges(4))
    p0["cells"][4]["animal"] = {"type": "boar", "count": 2}
    p1["pets"] = {"boar": 1}
    # p0 leads sheep (1 vs 0) and boar (2 vs 1), no cattle → 2 types → 4.
    fn = cards.CARDS["occ_estate_manager"]["score_bonus"]
    assert fn(s, p0, None) == 4


def test_game_over_and_winner(engine):
    s = make_state(engine, 2)
    s["round"] = 14
    s["phase"] = "feeding"
    for pl in s["players"]:
        pl["fed"] = False
        pl["resources"]["food"] = 20
    s["players"][0]["cells"][0]["type"] = "field"
    s["players"][0]["cells"][1]["type"] = "field"
    for pl in list(s["players"]):
        s = engine.apply_action(s, pl["player_id"], {"kind": "feed"}).new_state
    assert s["game_over"]
    assert s["phase"] == "game_over"
    assert s["scores"] is not None
    assert s["winners"] == [0]
    assert engine.get_waiting_for(s) == []


# ── converted / returning_home / broadcast (_any) event hooks ────────

def test_converted_event_feed_raw(engine, temp_card):
    """`converted` fires with give/get/via for a feeding-phase raw
    conversion, reaching the converting player's own cards."""
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    cid = "test_converted_feed_raw"
    temp_card(cid, "Test Card", "minor", "test",
               hooks={"converted": lambda state, player, inst, ctx:
                      inst["data"].setdefault("seen", []).append(
                          {"give": ctx["give"], "get": ctx["get"],
                           "via": ctx["via"], "actor": ctx["actor"]})})
    put_in_play(s, first, cid)
    p["resources"].update({"food": 0, "grain": 2})
    s["phase"] = "feeding"
    for pl in s["players"]:
        pl["fed"] = False
    s = engine.apply_action(s, p["player_id"], {
        "kind": "feed",
        "conversions": [{"good": "grain", "via": "raw", "count": 2}],
    }).new_state
    inst = next(i for i in s["players"][first]["minors"] if i["id"] == cid)
    assert inst["data"]["seen"] == [
        {"give": {"grain": 2}, "get": {"food": 2}, "via": "raw", "actor": first}]


def test_converted_event_broadcasts_to_other_players(engine, temp_card):
    """`converted` fires to ALL players' cards (like space_used), not
    just the converting player's own, so "each time another player
    converts..." cards can react."""
    s = make_state(engine, 2)
    first = s["current_player"]
    other = 1 - first
    p = s["players"][first]
    cid = "test_converted_observer"
    temp_card(cid, "Observer", "minor", "test",
               hooks={"converted": lambda state, player, inst, ctx:
                      inst["data"].setdefault("seen_actors", []).append(ctx["actor"])})
    put_in_play(s, other, cid)
    p["resources"].update({"food": 0, "grain": 1})
    s["phase"] = "feeding"
    for pl in s["players"]:
        pl["fed"] = False
    s = engine.apply_action(s, p["player_id"], {
        "kind": "feed",
        "conversions": [{"good": "grain", "via": "raw", "count": 1}],
    }).new_state
    inst = next(i for i in s["players"][other]["minors"] if i["id"] == cid)
    assert inst["data"]["seen_actors"] == [first]


def test_converted_event_accommodate_cook(engine, temp_card):
    """`converted` also fires for the cook branch of accommodate
    (cooking gained animals instead of placing them), via="cook"."""
    s, first = sheep_pasture_state(engine)
    p = s["players"][first]
    p["improvements"].append("fireplace_2")
    s["available_improvements"].remove("fireplace_2")
    cid = "test_converted_cook_branch"
    temp_card(cid, "Test Card", "minor", "test",
               hooks={"converted": lambda state, player, inst, ctx:
                      inst["data"].setdefault("seen", []).append(
                          {"give": ctx["give"], "get": ctx["get"],
                           "via": ctx["via"]})})
    put_in_play(s, first, cid)
    pid = p["player_id"]
    set_sheep_market(s, 2)
    s = place(engine, s, {"kind": "place", "space": "sheep_market"})
    s = engine.apply_action(s, pid, {
        "kind": "accommodate",
        "cook": {"sheep": 1},
        "placements": [{"cell": 4, "type": "sheep", "count": 1}],
    }).new_state
    inst = next(i for i in s["players"][first]["minors"] if i["id"] == cid)
    assert inst["data"]["seen"] == [
        {"give": {"sheep": 1}, "get": {"food": 2}, "via": "cook"}]


def test_returning_home_spaces_non_harvest_round(engine, temp_card):
    """`returning_home` fires once per player at the end of the work
    phase with the action-space ids that player's people occupy --
    verified on a non-harvest round."""
    s = make_state(engine, 2)
    cid = "test_returning_home_spaces_nh"
    temp_card(cid, "Test Card", "minor", "test",
               hooks={"returning_home": lambda state, player, inst, ctx:
                      inst["data"].setdefault("fires", []).append(list(ctx["spaces"]))})
    put_in_play(s, 0, cid)
    s["round"] = 2  # not a harvest round
    s["phase"] = "work"
    forest = next(sp for sp in s["action_spaces"] if sp["id"] == "forest")
    clay_pit = next(sp for sp in s["action_spaces"] if sp["id"] == "clay_pit")
    forest["occupied_by"] = 0
    clay_pit["occupied_by"] = 1
    for pl in s["players"]:
        pl["people_placed"] = pl["people_total"]
    log = []
    engine._end_work_phase(s, log)
    inst = next(i for i in s["players"][0]["minors"] if i["id"] == cid)
    assert inst["data"]["fires"] == [["forest"]]  # fired exactly once


def test_returning_home_spaces_harvest_round(engine, temp_card):
    """Same as above, verified on a harvest round: still fires exactly
    once with the correct spaces, at the same single choke point."""
    s = make_state(engine, 2)
    cid = "test_returning_home_spaces_h"
    temp_card(cid, "Test Card", "minor", "test",
               hooks={"returning_home": lambda state, player, inst, ctx:
                      inst["data"].setdefault("fires", []).append(list(ctx["spaces"]))})
    put_in_play(s, 0, cid)
    s["round"] = 4  # a harvest round
    s["phase"] = "work"
    forest = next(sp for sp in s["action_spaces"] if sp["id"] == "forest")
    forest["occupied_by"] = 0
    for pl in s["players"]:
        pl["people_placed"] = pl["people_total"]
    log = []
    engine._end_work_phase(s, log)
    assert s["phase"] == "feeding"  # harvest ran
    inst = next(i for i in s["players"][0]["minors"] if i["id"] == cid)
    assert inst["data"]["fires"] == [["forest"]]  # fired exactly once


def test_returning_home_prompt_discarded_on_non_harvest_round(engine, temp_card):
    """Prompting (or granting animals via ctx["extra"]) from
    returning_home is unsafe except on harvest rounds: on a non-harvest
    round the very next _start_round wipes state["prompts"] before the
    player ever gets to respond -- the same hazard already documented
    for round_start hooks. See decks/GUIDE.md."""
    s = make_state(engine, 2)
    cid = "test_returning_home_prompt_nh"

    def hook(state, player, inst, ctx):
        cards.prompt_choice(state, player, inst["id"], "Take wood or clay?",
                             ["wood", "clay"])

    temp_card(cid, "Test Card", "minor", "test",
               hooks={"returning_home": hook},
               resolve_choice=lambda state, player, inst, ctx:
                   inst["data"].setdefault("resolved", True))
    put_in_play(s, 0, cid)
    s["round"] = 2  # not a harvest round
    s["phase"] = "work"
    for pl in s["players"]:
        pl["people_placed"] = pl["people_total"]
    log = []
    engine._end_work_phase(s, log)
    assert s["prompts"] == []  # queued, then silently wiped
    inst = next(i for i in s["players"][0]["minors"] if i["id"] == cid)
    assert "resolved" not in inst["data"]


def test_renovate_any_broadcasts_to_other_players(engine, temp_card):
    """`renovate_any` fires to ALL players' cards (ctx = the same fields
    plus actor), unlike the owner-only `renovate` event -- enabling
    "each time ANOTHER player renovates..." cards."""
    s = make_state(engine, 2)
    first = s["current_player"]
    other = 1 - first
    cid = "test_renovate_any_observer"
    temp_card(cid, "Observer", "minor", "test",
               hooks={"renovate_any": lambda state, player, inst, ctx:
                      inst["data"].setdefault("seen", []).append(ctx["actor"])})
    put_in_play(s, other, cid)
    give(s, first, clay=2, reed=1)
    add_space(s, "house_redevelopment", "House Redevelopment")
    s = place(engine, s, {"kind": "place", "space": "house_redevelopment"})
    inst = next(i for i in s["players"][other]["minors"] if i["id"] == cid)
    assert inst["data"]["seen"] == [first]


def test_sow_any_broadcasts_to_other_players(engine, temp_card):
    """`sow_any` fires to ALL players' cards, unlike the owner-only
    `sow` event."""
    s = make_state(engine, 2)
    first = s["current_player"]
    other = 1 - first
    p = s["players"][first]
    cid = "test_sow_any_observer"
    temp_card(cid, "Observer", "minor", "test",
               hooks={"sow_any": lambda state, player, inst, ctx:
                      inst["data"].setdefault("seen", []).append(ctx["actor"])})
    put_in_play(s, other, cid)
    p["cells"][0]["type"] = "field"
    give(s, first, grain=1)
    add_space(s, "grain_utilization", "Grain Utilization")
    s = place(engine, s, {"kind": "place", "space": "grain_utilization",
                          "sow": [{"cell": 0, "crop": "grain"}]})
    inst = next(i for i in s["players"][other]["minors"] if i["id"] == cid)
    assert inst["data"]["seen"] == [first]


def test_plow_any_broadcasts_to_other_players(engine, temp_card):
    """`plow_any` fires to ALL players' cards; covers the self._fire(...,
    to_all=False) call sites (plow/rooms_built/stable_built), distinct
    from the cards.fire_player(...) sites (renovate/bake/sow)."""
    s = make_state(engine, 2)
    first = s["current_player"]
    other = 1 - first
    cid = "test_plow_any_observer"
    temp_card(cid, "Observer", "minor", "test",
               hooks={"plow_any": lambda state, player, inst, ctx:
                      inst["data"].setdefault("seen", []).append(
                          (ctx["actor"], ctx["cell"]))})
    put_in_play(s, other, cid)
    s = place(engine, s, {"kind": "place", "space": "farmland", "cell": 0})
    inst = next(i for i in s["players"][other]["minors"] if i["id"] == cid)
    assert inst["data"]["seen"] == [(first, 0)]


# ── Random full-game fuzz ────────────────────────────────────────────

def random_bot_action(engine, state, pid, rng):
    """Pick a random valid action with randomly generated parameters."""
    acts = engine.get_valid_actions(state, pid)
    # Answer card choice prompts randomly.
    choice = next((a for a in acts if a["kind"] == "choice"), None)
    if choice:
        return {"kind": "choice", "index": rng.randrange(len(choice["options"]))}
    # Skip optional activated card abilities in the fuzz (they may need
    # params); deck tests cover them directly.
    acts = [a for a in acts if a["kind"] != "card_action"]
    if not acts:
        return None
    act = rng.choice(acts)
    kind = act["kind"]
    pidx = next(p["index"] for p in state["players"] if p["player_id"] == pid)
    p = state["players"][pidx]

    if kind == "feed":
        need = act["food_needed"]
        conversions = []
        have = p["resources"]["food"]
        raw = cards.raw_values(p)
        for crop in ("grain", "vegetable"):
            avail = p["resources"][crop]
            if have >= need or not avail:
                continue
            take = min(avail, max(1, (need - have) // raw[crop] + 1))
            conversions.append({"good": crop, "via": "raw", "count": take})
            have += take * raw[crop]
        return {"kind": "feed", "conversions": conversions}

    if kind == "accommodate":
        gained = dict(act["gained"])
        ok, _ = engine._validate_animals(p)
        if ok:
            placements = []
            for i, c in enumerate(p["cells"]):
                if c["animal"]:
                    placements.append({"cell": i, "type": c["animal"]["type"],
                                       "count": c["animal"]["count"]})
            return {"kind": "accommodate", "placements": placements,
                    "pets": dict(p["pets"]), "discard": gained}
        totals = animal_counts(p)
        for a, n in gained.items():
            totals[a] += n
        return {"kind": "accommodate", "placements": [],
                "discard": {a: n for a, n in totals.items() if n}}

    space = act["space"]
    action = {"kind": "place", "space": space}
    from server.agricola.state import plowable_cells
    if space in ("lessons", "lessons_b"):
        action["card"] = rng.choice(p["hand_occupations"])
    elif space == "meeting_place":
        minor = bot_pick_minor(engine, state, p, rng)
        if minor and rng.random() < 0.7:
            action["minor"] = minor
    elif space == "farmland":
        action["cell"] = rng.choice(plowable_cells(p))
    elif space == "farm_expansion":
        cells = engine._buildable_room_cells(p)
        if cells and engine._can_afford(p, engine._room_cost(state, p)):
            action["rooms"] = [rng.choice(cells)]
        else:
            free = [i for i, c in enumerate(p["cells"])
                    if c["type"] == "empty" and not c["stable"]]
            action["stables"] = [rng.choice(free)]
    elif space == "fencing":
        fences = bot_fence_plan(engine, state, p)
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
        if plowable_cells(p):
            action["plow"] = rng.choice(plowable_cells(p))
        if engine._can_sow(p):
            action["sow"] = bot_sow(p)
        if action.get("plow") is None and not action.get("sow"):
            return None
    elif space == "major_improvement":
        options = engine._buildable_improvements(state, p)
        minor = bot_pick_minor(engine, state, p, rng)
        if options and (not minor or rng.random() < 0.6):
            imp = rng.choice(options)
            action["improvement"] = imp
            cost = cards.modified_cost(
                state, p, "improvement", MAJOR_IMPROVEMENTS[imp]["cost"])
            if not engine._can_afford(p, cost):
                action["upgrade"] = True
        elif minor:
            action["minor"] = minor
        else:
            return None
    elif space == "house_redevelopment":
        pass
    elif space == "farm_redevelopment":
        pass
    elif space == "resource_market_3p":
        action["choice"] = rng.choice(["reed", "stone"])
    return action


def bot_fallback(engine, state, pid, rng):
    """A safe action that must exist: feed plainly, resolve prompts,
    or place on an always-usable space."""
    acts = engine.get_valid_actions(state, pid)
    choice = next((a for a in acts if a["kind"] == "choice"), None)
    if choice:
        return {"kind": "choice", "index": 0}
    if any(a["kind"] == "accommodate" for a in acts):
        p = next(pl for pl in state["players"] if pl["player_id"] == pid)
        gained = next(a for a in acts if a["kind"] == "accommodate")["gained"]
        totals = animal_counts(p)
        for a, n in gained.items():
            totals[a] += n
        return {"kind": "accommodate", "placements": [],
                "discard": {a: n for a, n in totals.items() if n}}
    if any(a["kind"] == "feed" for a in acts):
        return {"kind": "feed"}
    simple = [a for a in acts if a["kind"] == "place"
              and a["space"] in SAFE_SPACES]
    assert simple, f"no fallback action ({[a.get('space') for a in acts]})"
    return {"kind": "place", "space": rng.choice(simple)["space"]}


def bot_pick_minor(engine, state, p, rng):
    playable = [cid for cid in p["hand_minors"]
                if engine._minor_playable(state, p, cid)]
    if not playable:
        return None
    cid = rng.choice(playable)
    minor = {"card": cid}
    if cid == "minor_shifting_cultivation":
        from server.agricola.state import plowable_cells
        cells = plowable_cells(p)
        if not cells:
            others = [c for c in playable if c != cid]
            if not others:
                return None
            minor = {"card": rng.choice(others)}
        else:
            minor["params"] = {"cell": rng.choice(cells)}
    return minor


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
    for inst in cards.card_fields(p):
        if not inst["crops"]:
            allowed = cards.CARDS[inst["id"]]["field"]["crops"]
            for crop in allowed:
                if crops[crop] > 0:
                    crops[crop] -= 1
                    sow.append({"card": inst["id"], "crop": crop})
                    break
    return sow


def bot_bake(p):
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


def bot_fence_plan(engine, state, p):
    """Fence the first free 1x1 cell, if affordable."""
    existing = set(p["fences"])
    for i, c in enumerate(p["cells"]):
        if c["type"] != "empty":
            continue
        new = [e for e in cell_edges(i) if e not in existing]
        if not new:
            continue
        cost = cards.modified_cost(state, p, "fences",
                                   {"wood": len(new)}, {"count": len(new)})
        if not engine._can_afford(p, cost):
            continue
        if len(existing) + len(new) > 15:
            continue
        ok, _e, _p = validate_fence_layout(p, sorted(existing | set(new)))
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
    cards_played = 0
    while not s["game_over"]:
        steps += 1
        assert steps < 3000, "game did not terminate"
        waiting = engine.get_waiting_for(s)
        assert waiting, f"nobody to act but game not over (phase {s['phase']})"
        pid = waiting[0]
        action = random_bot_action(engine, s, pid, rng)
        if action is None:
            action = bot_fallback(engine, s, pid, rng)
        try:
            s = engine.apply_action(s, pid, action).new_state
        except ValueError:
            # Bot-generated params can be invalid for cards that need
            # richer input; fall back to a safe space (state unchanged).
            s = engine.apply_action(s, pid,
                                    bot_fallback(engine, s, pid, rng)).new_state

    assert s["round"] == 14
    assert s["scores"] is not None
    assert len(s["winners"]) >= 1
    for sc in s["scores"]:
        cats = [v for k, v in sc.items()
                if k not in ("total", "player_index", "name", "tiebreak_resources")]
        assert sum(cats) == sc["total"]
    # The bots actually exercised the card system.
    total_played = sum(len(p["occupations"]) + len(p["minors"])
                      for p in s["players"])
    assert total_played > 0


@pytest.mark.parametrize("n_players,seed", [(2, 31), (3, 32), (4, 33),
                                            (2, 34), (4, 35)])
def test_random_full_game_compendium_decks(engine, n_players, seed):
    """Fuzz full games dealing from every implemented compendium deck."""
    decks = [d for d in cards.implemented_decks()
             if d not in ("base", "custom")]
    if not decks:
        pytest.skip("no compendium decks implemented yet")
    rng = random.Random(seed)
    random.seed(seed)
    ids = [f"p_{i}" for i in range(n_players)]
    names = [f"Bot{i}" for i in range(n_players)]
    s = engine.initial_state(ids, names, {"decks": decks})
    assert s["decks"] == decks

    steps = 0
    while not s["game_over"]:
        steps += 1
        assert steps < 4000, "game did not terminate"
        waiting = engine.get_waiting_for(s)
        assert waiting, f"nobody to act but game not over (phase {s['phase']})"
        pid = waiting[0]
        action = random_bot_action(engine, s, pid, rng)
        if action is None:
            action = bot_fallback(engine, s, pid, rng)
        try:
            s = engine.apply_action(s, pid, action).new_state
        except ValueError:
            s = engine.apply_action(s, pid,
                                    bot_fallback(engine, s, pid, rng)).new_state

    assert s["round"] == 14
    assert s["scores"] is not None
