"""Tests for the Agricola engine (full game with hand cards)."""

import random

import pytest

from server.agricola.engine import AgricolaEngine
from server.agricola import cards, sub_actions
from server.agricola.state import (
    ANIMAL_TYPES, HARVEST_ROUNDS, MAJOR_IMPROVEMENTS, STAGE_CARDS, NUM_CELLS,
    animal_counts, cell_edges, compute_pastures, pasture_capacity,
    validate_fence_layout, validate_animal_placement, create_player,
    plowable_cells, is_border_edge, table_score, all_edge_keys,
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
        p, house_cap=cards.house_capacity(None, p))
    assert ok, err  # 2 rooms → 2 pets allowed
    p["pets"] = {"sheep": 2, "boar": 1}
    ok, err = validate_animal_placement(
        p, house_cap=cards.house_capacity(None, p))
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


# ── Guest tokens / occupied-space placement (CARDS.md gaps) ─────────

def test_guest_extra_turn_in_rotation_and_reset(engine, temp_card):
    """A guest token is one more placement this round only, folded into
    the normal rotation (not an immediate second turn like the Lasso)."""
    def grant_once(state, player, inst, ctx):
        if ctx["actor"] == player["index"] and not inst["data"].get("granted"):
            inst["data"]["granted"] = True
            cards.grant_guest(player)

    temp_card("test_guest_source", "Test Guest Source", "minor", cost={},
              text="test", hooks={"space_used": grant_once})

    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    put_in_play(s, first, "test_guest_source")

    s = place(engine, s, {"kind": "place", "space": "grain_seeds"})
    assert s["players"][first]["guests"] == 1
    assert s["current_player"] == other

    s = place(engine, s, {"kind": "place", "space": "day_laborer"})
    # first's capacity is people_total(2) + guests(1) = 3: normal rotation
    # gives them another turn instead of skipping to `other`.
    assert s["current_player"] == first

    s = place(engine, s, {"kind": "place", "space": "forest"})
    assert s["players"][first]["people_placed"] == 2
    assert s["current_player"] == other

    s = place(engine, s, {"kind": "place", "space": "clay_pit"})
    assert s["players"][other]["people_placed"] == 2  # other is done
    assert s["current_player"] == first  # first still has the guest turn

    s = place(engine, s, {"kind": "place", "space": "reed_bank"})
    # first's 3rd placement (the guest turn) was also everyone's last --
    # the round rolls straight into round 2 within this same action.
    assert s["round"] == 2
    # The guest doesn't carry over.
    assert s["players"][first]["guests"] == 0
    assert s["players"][first]["people_placed"] == 0


def test_guests_do_not_affect_feeding_or_scoring(engine):
    s = make_state(engine, 2)
    p = s["players"][0]
    food_before = engine._food_needed(s, p)
    score_before = score_player(p, s)

    cards.grant_guest(p, 3)

    assert p["people_total"] == 2  # untouched by the guest grant
    assert engine._food_needed(s, p) == food_before
    assert score_player(p, s) == score_before


def test_occupied_ok_allows_placing_on_occupied_space(engine, temp_card):
    temp_card("test_occupied_any", "Test Occupied Any", "minor", cost={},
              text="test", occupied_ok=lambda state, player, inst, space: True)

    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2

    s = place(engine, s, {"kind": "place", "space": "forest"})
    forest = next(sp for sp in s["action_spaces"] if sp["id"] == "forest")
    assert forest["occupied_by"] == first

    other_pid = s["players"][other]["player_id"]
    valid = engine.get_valid_actions(s, other_pid)
    assert not any(a["kind"] == "place" and a["space"] == "forest"
                   for a in valid)
    with pytest.raises(ValueError):
        engine.apply_action(s, other_pid, {"kind": "place", "space": "forest"})

    put_in_play(s, other, "test_occupied_any")
    valid = engine.get_valid_actions(s, other_pid)
    assert any(a["kind"] == "place" and a["space"] == "forest" for a in valid)

    s = place(engine, s, {"kind": "place", "space": "forest"})
    forest = next(sp for sp in s["action_spaces"] if sp["id"] == "forest")
    assert forest["occupied_by"] == first  # the original occupant is kept
    assert forest["extra_occupants"] == [other]


def test_occupied_ok_restricted_to_specific_spaces(engine, temp_card):
    """3 players so the restricted override isn't the round's very last
    placement -- otherwise the round rolls over (and occupied_by/
    extra_occupants reset) inside the same apply_action call, before we
    get a chance to inspect them."""
    temp_card("test_occupied_forest_only", "Test Occupied Forest Only",
              "minor", cost={}, text="test",
              occupied_ok=lambda state, player, inst, space:
                  space["id"] == "forest")

    s = make_state(engine, 3)
    first = s["current_player"]
    holder = (first + 1) % 3
    third = (first + 2) % 3
    put_in_play(s, holder, "test_occupied_forest_only")

    s = place(engine, s, {"kind": "place", "space": "clay_pit"})  # first

    holder_pid = s["players"][holder]["player_id"]
    valid = engine.get_valid_actions(s, holder_pid)
    # clay_pit is occupied but not one of this card's allowed spaces.
    assert not any(a["kind"] == "place" and a["space"] == "clay_pit"
                   for a in valid)
    with pytest.raises(ValueError):
        engine.apply_action(s, holder_pid,
                            {"kind": "place", "space": "clay_pit"})
    s = place(engine, s, {"kind": "place", "space": "grain_seeds"})  # holder
    s = place(engine, s, {"kind": "place", "space": "day_laborer"})  # third
    s = place(engine, s, {"kind": "place", "space": "forest"})  # first, again

    holder_pid = s["players"][holder]["player_id"]
    valid = engine.get_valid_actions(s, holder_pid)
    # forest is occupied and IS the allowed space.
    assert any(a["kind"] == "place" and a["space"] == "forest" for a in valid)
    s = place(engine, s, {"kind": "place", "space": "forest"})  # holder overrides
    assert s["round"] == 1  # third still has a placement left this round
    forest = next(sp for sp in s["action_spaces"] if sp["id"] == "forest")
    assert forest["occupied_by"] == first
    assert forest["extra_occupants"] == [holder]


def test_returning_home_spaces_shared_occupancy(engine, temp_card):
    """returning_home must credit a shared space to every player who has
    a person on it, not just the first (original occupied_by) occupant."""
    def record(state, player, inst, ctx):
        inst["data"]["spaces"] = list(ctx["spaces"])

    temp_card("test_shared_space", "Test Shared Space", "minor", cost={},
              text="test", occupied_ok=lambda state, player, inst, space: True,
              hooks={"returning_home": record})

    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    put_in_play(s, first, "test_shared_space")
    put_in_play(s, other, "test_shared_space")

    s = place(engine, s, {"kind": "place", "space": "forest"})
    s = place(engine, s, {"kind": "place", "space": "forest"})  # other shares it
    s = place(engine, s, {"kind": "place", "space": "clay_pit"})
    s = place(engine, s, {"kind": "place", "space": "reed_bank"})
    assert s["round"] == 2  # the round rolled over cleanly

    inst_first = next(i for i in s["players"][first]["minors"]
                       if i["id"] == "test_shared_space")
    inst_other = next(i for i in s["players"][other]["minors"]
                       if i["id"] == "test_shared_space")
    assert "forest" in inst_first["data"]["spaces"]
    assert "forest" in inst_other["data"]["spaces"]


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


def test_returning_home_prompt_survives_non_harvest_round(engine, temp_card):
    """Prompting from returning_home is now safe on a non-harvest round
    too: the queued prompt blocks the _end_work_phase -> _end_round ->
    _start_round cascade (instead of being silently wiped by the next
    _start_round) until the owning player answers it, and answering it
    resumes the cascade into the next round. See decks/GUIDE.md."""
    s = make_state(engine, 2)
    cid = "test_returning_home_prompt_nh"

    def hook(state, player, inst, ctx):
        cards.prompt_choice(state, player, inst["id"], "Take wood or clay?",
                             ["wood", "clay"])

    def resolve(state, player, inst, ctx):
        inst["data"]["resolved"] = ctx["option"]

    temp_card(cid, "Test Card", "minor", "test",
               hooks={"returning_home": hook}, resolve_choice=resolve)
    put_in_play(s, 0, cid)
    s["round"] = 2  # not a harvest round
    s["phase"] = "work"
    for pl in s["players"]:
        pl["people_placed"] = pl["people_total"]
    log = []
    engine._end_work_phase(s, log)
    # The cascade stops at the pending prompt instead of steamrolling
    # into round 3.
    assert s["round"] == 2
    assert len(s["prompts"]) == 1
    owner_pid = s["players"][0]["player_id"]
    assert engine.get_waiting_for(s) == [owner_pid]
    other_pid = s["players"][1]["player_id"]
    assert engine.get_valid_actions(s, other_pid) == []

    s = engine.apply_action(s, owner_pid, {"kind": "choice", "index": 1}).new_state
    inst = next(i for i in s["players"][0]["minors"] if i["id"] == cid)
    assert inst["data"]["resolved"] == "clay"
    # Resolving it lets the cascade continue into round 3; nobody's
    # placements were forfeited by the stall.
    assert s["round"] == 3
    assert s["prompts"] == []
    assert all(p["people_placed"] == 0 for p in s["players"])


def test_returning_home_prompt_resolves_through_harvest_round(engine, temp_card):
    """Same hazard, on a harvest round: the queued prompt must resolve
    before the harvest's field/feeding flow proceeds, and answering it
    correctly continues into the harvest."""
    s = make_state(engine, 2)
    cid = "test_returning_home_prompt_h"

    def hook(state, player, inst, ctx):
        cards.prompt_choice(state, player, inst["id"], "Take wood or clay?",
                             ["wood", "clay"])

    def resolve(state, player, inst, ctx):
        inst["data"]["resolved"] = ctx["option"]

    temp_card(cid, "Test Card", "minor", "test",
               hooks={"returning_home": hook}, resolve_choice=resolve)
    put_in_play(s, 0, cid)
    s["round"] = 4  # a harvest round
    s["phase"] = "work"
    for pl in s["players"]:
        pl["people_placed"] = pl["people_total"]
    log = []
    engine._end_work_phase(s, log)
    # Harvest hasn't started yet -- still "work" phase, waiting on the
    # prompt.
    assert s["phase"] == "work"
    assert len(s["prompts"]) == 1
    owner_pid = s["players"][0]["player_id"]

    s = engine.apply_action(s, owner_pid, {"kind": "choice", "index": 0}).new_state
    inst = next(i for i in s["players"][0]["minors"] if i["id"] == cid)
    assert inst["data"]["resolved"] == "wood"
    # Resolving it runs the harvest: field phase applied, phase moves to
    # feeding.
    assert s["phase"] == "feeding"
    assert s["harvest_index"] == 1


def test_round_start_choice_prompt_does_not_forfeit_round(engine, temp_card):
    """A round_start hook may safely prompt: the engine holds the game
    on the prompt's owner (instead of every player's placement query
    coming back empty and _advance_work treating that as "nobody can
    place, forfeit the round") until it's answered, then placement
    continues normally."""
    s = make_state(engine, 2)
    first = s["current_player"]
    other = 1 - first
    cid = "test_round_start_choice"

    def hook(state, player, inst, ctx):
        cards.prompt_choice(state, player, inst["id"], "Wood or clay?",
                             ["wood", "clay"])

    def resolve(state, player, inst, ctx):
        player["resources"][ctx["option"]] += 1

    temp_card(cid, "Test Card", "minor", "test",
               hooks={"round_start": hook}, resolve_choice=resolve)
    put_in_play(s, first, cid)
    log = []
    engine._start_round(s, log)

    prompt = s["prompts"][0]
    assert prompt["type"] == "choice"
    assert prompt["player"] == first
    owner_pid = s["players"][first]["player_id"]
    other_pid = s["players"][other]["player_id"]
    assert engine.get_waiting_for(s) == [owner_pid]
    assert engine.get_valid_actions(s, other_pid) == []

    wood_before = s["players"][first]["resources"]["wood"]
    s = engine.apply_action(s, owner_pid, {"kind": "choice", "index": 0}).new_state
    assert s["players"][first]["resources"]["wood"] == wood_before + 1
    assert s["prompts"] == []
    # The round was not forfeited: nobody's placements were consumed,
    # and real placement actions are available again.
    assert all(p["people_placed"] == 0 for p in s["players"])
    cur_pid = s["players"][s["current_player"]]["player_id"]
    actions = engine.get_valid_actions(s, cur_pid)
    assert any(a["kind"] == "place" for a in actions)


def test_round_start_animal_grant_accommodates(engine, temp_card):
    """A round_start hook granting an animal via ctx["extra"] queues the
    normal accommodation prompt (instead of the grant being silently
    discarded, or every player's round being forfeited)."""
    s, first = sheep_pasture_state(engine)
    other = 1 - first
    cid = "test_round_start_sheep_grant"
    temp_card(cid, "Test Card", "minor", "test",
               hooks={"round_start": lambda state, player, inst, ctx:
                      ctx["extra"].update({"sheep": 1})})
    put_in_play(s, first, cid)
    log = []
    engine._start_round(s, log)

    prompt = s["prompts"][0]
    assert prompt["type"] == "accommodate"
    assert prompt["player"] == first
    assert prompt["gained"] == {"sheep": 1}
    owner_pid = s["players"][first]["player_id"]
    other_pid = s["players"][other]["player_id"]
    assert engine.get_waiting_for(s) == [owner_pid]
    assert engine.get_valid_actions(s, other_pid) == []

    s = engine.apply_action(s, owner_pid, {
        "kind": "accommodate",
        "placements": [{"cell": 4, "type": "sheep", "count": 1}],
    }).new_state
    assert s["prompts"] == []
    assert s["players"][first]["cells"][4]["animal"] == {"type": "sheep", "count": 1}
    # The round was not forfeited: nobody's placements were consumed.
    assert all(p["people_placed"] == 0 for p in s["players"])


def test_returning_home_and_round_start_prompts_cascade_in_one_call(engine, temp_card):
    """A single apply_action resolving a returning_home prompt can
    cascade all the way through _end_round/_start_round into a NEW
    prompt queued by the next round's own round_start hook. The
    cascade must stop at that new prompt (not steamroll past it into
    forfeited placements), and round setup for the next round must run
    exactly once (no double-firing round_start)."""
    s = make_state(engine, 2)
    cid = "test_double_stall"
    starts = []

    def returning_home_hook(state, player, inst, ctx):
        cards.prompt_choice(state, player, inst["id"],
                             "Returning: wood or clay?", ["wood", "clay"],
                             data={"stage": "returning"})

    def round_start_hook(state, player, inst, ctx):
        starts.append(ctx["round"])
        cards.prompt_choice(state, player, inst["id"],
                             "Round start: wood or clay?", ["wood", "clay"],
                             data={"stage": "round_start"})

    def resolve(state, player, inst, ctx):
        inst["data"].setdefault("resolved", []).append(
            (ctx["data"]["stage"], ctx["option"]))

    temp_card(cid, "Test Card", "minor", "test",
               hooks={"returning_home": returning_home_hook,
                      "round_start": round_start_hook},
               resolve_choice=resolve)
    put_in_play(s, 0, cid)
    s["round"] = 2  # not a harvest round
    s["phase"] = "work"
    for pl in s["players"]:
        pl["people_placed"] = pl["people_total"]
    log = []
    engine._end_work_phase(s, log)
    assert s["round"] == 2
    assert len(s["prompts"]) == 1
    owner_pid = s["players"][0]["player_id"]

    s = engine.apply_action(s, owner_pid, {"kind": "choice", "index": 0}).new_state
    # Round setup for round 3 ran, and its own round_start prompt is now
    # pending -- the cascade stopped there instead of skipping it.
    assert s["round"] == 3
    assert starts == [3]  # round_start fired exactly once, for round 3
    assert len(s["prompts"]) == 1
    inst = next(i for i in s["players"][0]["minors"] if i["id"] == cid)
    assert inst["data"]["resolved"] == [("returning", "wood")]

    s = engine.apply_action(s, owner_pid, {"kind": "choice", "index": 1}).new_state
    assert s["prompts"] == []
    inst = next(i for i in s["players"][0]["minors"] if i["id"] == cid)
    assert inst["data"]["resolved"] == [("returning", "wood"), ("round_start", "clay")]
    assert all(p["people_placed"] == 0 for p in s["players"])


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
        ok, _ = engine._validate_animals(state, p)
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


# ── sub_actions.py: card-facing build/play transactions ──────────────
#
# These cover the "bonus build/play sub-action" shape (~25 previously
# UNIMPLEMENTED compendium cards): a card hook or card_action invokes
# the SAME transaction implementation the normal action-space dispatch
# uses (build rooms/stables/fences, renovate, build a major improvement,
# play a minor improvement, sow, play an occupation), at full price, a
# flat discount, or free. See server/agricola/sub_actions.py.

def test_sub_action_build_rooms_free_from_hook(engine, temp_card):
    """A play hook building a free room via sub_actions.build_rooms,
    with the target cell(s) supplied through the play params channel."""
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    cid = "test_free_room"
    temp_card(cid, "Test Free Room", "minor", "test", hooks={
        "play": lambda state, player, inst, ctx: sub_actions.build_rooms(
            state, player, (ctx.get("params") or {}).get("cells", []),
            ctx["log"], cost_override="free"),
    })
    give_card(s, first, cid)
    cell = sub_actions.buildable_room_cells(p)[0]
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": cid, "params": {"cells": [cell]}}})
    p = s["players"][first]
    assert p["cells"][cell]["type"] == "room"
    # Free: no building resources spent.
    assert p["resources"][p["house_type"]] == 0
    assert p["resources"]["reed"] == 0


def test_sub_action_build_stables_discounted_from_hook(engine, temp_card):
    """A play hook building a stable at a flat discount (1 wood instead
    of the normal 2)."""
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    cid = "test_cheap_stable"
    temp_card(cid, "Test Cheap Stable", "minor", "test", hooks={
        "play": lambda state, player, inst, ctx: sub_actions.build_stables(
            state, player, (ctx.get("params") or {}).get("cells", []),
            ctx["log"], cost_override={"wood": 1}),
    })
    give_card(s, first, cid)
    give(s, first, wood=1)
    cell = next(i for i, c in enumerate(p["cells"])
               if c["type"] == "empty" and not c["stable"])
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": cid, "params": {"cells": [cell]}}})
    p = s["players"][first]
    assert p["cells"][cell]["stable"] is True
    assert p["resources"]["wood"] == 0


def test_sub_action_build_fences_normal_cost_from_hook(engine, temp_card):
    """A play hook building fences at the normal (modified_cost-folded)
    cost -- the fence edge set is an open-ended target, so it MUST
    arrive via the params channel."""
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    cid = "test_bonus_fences"
    temp_card(cid, "Test Bonus Fences", "minor", "test", hooks={
        "play": lambda state, player, inst, ctx: sub_actions.build_fences(
            state, player, (ctx.get("params") or {}).get("fences", []),
            ctx["log"]),
    })
    give_card(s, first, cid)
    give(s, first, wood=4)
    fences = cell_edges(4)
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": cid, "params": {"fences": fences}}})
    p = s["players"][first]
    assert set(fences) <= set(p["fences"])
    assert p["resources"]["wood"] == 0


def test_sub_action_renovate_free_from_hook(engine, temp_card):
    """A play hook renovating for free -- proves the same transaction
    engine._do_renovate uses is reachable from a card hook."""
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    cid = "test_free_renovate"
    temp_card(cid, "Test Free Renovate", "minor", "test", hooks={
        "play": lambda state, player, inst, ctx: sub_actions.renovate(
            state, player, ctx["log"], cost_override="free"),
    })
    give_card(s, first, cid)
    assert p["house_type"] == "wood"
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": cid}})
    p = s["players"][first]
    assert p["house_type"] == "clay"
    assert p["resources"]["clay"] == 0
    assert p["resources"]["reed"] == 0


def test_sub_action_build_improvement_from_hook(engine, temp_card):
    """A play hook building a major improvement at normal cost."""
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    cid = "test_bonus_improvement"
    temp_card(cid, "Test Bonus Improvement", "minor", "test", hooks={
        "play": lambda state, player, inst, ctx: sub_actions.build_improvement(
            state, player, (ctx.get("params") or {}).get("improvement"),
            ctx["log"]),
    })
    give_card(s, first, cid)
    give(s, first, clay=2)
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": cid,
                                   "params": {"improvement": "fireplace_2"}}})
    p = s["players"][first]
    assert "fireplace_2" in p["improvements"]
    assert p["resources"]["clay"] == 0


def test_sub_action_play_minor_free_from_hook(engine, temp_card):
    """An occupation's play hook playing ANOTHER card (a minor
    improvement) from hand for free -- the Craft Teacher/Scholar shape,
    generalized as a one-liner via sub_actions.play_minor."""
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    target_cid = "test_minor_target"
    temp_card(target_cid, "Test Minor Target", "minor", "test", cost={"wood": 3})
    source_cid = "test_free_minor_source"
    temp_card(source_cid, "Test Free Minor Source", "occupation", "test", hooks={
        "play": lambda state, player, inst, ctx: sub_actions.play_minor(
            state, player, (ctx.get("params") or {}).get("card"), ctx["log"],
            cost_override="free"),
    })
    give_card(s, first, target_cid)
    give_card(s, first, source_cid)
    s = place(engine, s, {"kind": "place", "space": "lessons",
                          "card": source_cid,
                          "params": {"card": target_cid}})
    p = s["players"][first]
    assert any(i["id"] == target_cid for i in p["minors"])
    assert target_cid not in p["hand_minors"]
    assert p["resources"]["wood"] == 0  # free: the 3-wood cost was skipped


def test_sub_action_sow_from_hook(engine, temp_card):
    """A play hook sowing a field, using sub_actions.sow (fires the same
    `sow`/`sow_any` events _do_sow does)."""
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["cells"][0]["type"] = "field"
    cid = "test_bonus_sow"
    temp_card(cid, "Test Bonus Sow", "minor", "test", hooks={
        "play": lambda state, player, inst, ctx: sub_actions.sow(
            state, player, (ctx.get("params") or {}).get("sow", []), ctx["log"]),
    })
    give_card(s, first, cid)
    give(s, first, grain=1)
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": cid,
                                   "params": {"sow": [{"cell": 0, "crop": "grain"}]}}})
    p = s["players"][first]
    assert p["cells"][0]["crops"] == {"type": "grain", "count": 3}
    assert p["resources"]["grain"] == 0


def test_sub_action_play_occupation_free_from_hook(engine, temp_card):
    """A play hook playing an occupation from hand for free (the
    Craft Teacher shape): sub_actions.play_occupation is the same
    transaction the Lessons action spaces use."""
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    cid = "test_free_occ_source"
    temp_card(cid, "Test Free Occ Source", "minor", "test", hooks={
        "play": lambda state, player, inst, ctx: sub_actions.play_occupation(
            state, player, (ctx.get("params") or {}).get("card"), ctx["log"],
            cost_override="free"),
    })
    give_card(s, first, cid)
    give_card(s, first, "occ_woodcutter")
    food_before = p["resources"]["food"]
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": cid,
                                   "params": {"card": "occ_woodcutter"}}})
    p = s["players"][first]
    assert any(i["id"] == "occ_woodcutter" for i in p["occupations"])
    assert "occ_woodcutter" not in p["hand_occupations"]
    assert p["resources"]["food"] == food_before  # free: no food spent


def test_sub_action_illegal_target_raises_and_rolls_back(engine, temp_card):
    """An illegal target raises ValueError from inside the transaction;
    since apply_action deepcopies before mutating, the caller's state
    is left untouched (no partial application)."""
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    cid = "test_bad_room_target"
    temp_card(cid, "Test Bad Room Target", "minor", "test", hooks={
        "play": lambda state, player, inst, ctx: sub_actions.build_rooms(
            state, player, (ctx.get("params") or {}).get("cells", []),
            ctx["log"], cost_override="free"),
    })
    give_card(s, first, cid)
    resources_before = dict(p["resources"])
    cells_before = [dict(c) for c in p["cells"]]
    # Cell 4 is not adjacent to any existing room -- illegal target.
    with pytest.raises(ValueError):
        engine.apply_action(s, p["player_id"], {
            "kind": "place", "space": "meeting_place",
            "minor": {"card": cid, "params": {"cells": [4]}}})
    # The pre-call state (still referenced by `s`/`p`) is unaffected.
    assert p["resources"] == resources_before
    assert p["cells"] == cells_before
    assert cid in p["hand_minors"]


def test_sub_action_card_action_wraps_build_rooms(engine, temp_card):
    """A card_action wrapping sub_actions.build_rooms: `available`
    reflects can_build_rooms without mutating anything, and get_valid_actions
    lists it as a card_action alongside the normal placement options."""
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    cid = "test_room_card_action"

    def available(state, player, inst):
        return not inst["data"].get("used") and \
            sub_actions.can_build_rooms(state, player, cost_override="free")

    def apply(state, player, inst, ctx):
        cell = (ctx.get("params") or {}).get("cell")
        sub_actions.build_rooms(state, player, [cell], ctx["log"],
                                cost_override="free")
        inst["data"]["used"] = True

    temp_card(cid, "Test Room Card Action", "minor", "test",
              card_action={"available": available, "apply": apply,
                          "description": "Build a free room"})
    inst = put_in_play(s, first, cid)

    assert cards.CARDS[cid]["card_action"]["available"](s, p, inst)
    kinds = [a["kind"] for a in engine.get_valid_actions(s, p["player_id"])]
    assert "card_action" in kinds

    cell = sub_actions.buildable_room_cells(p)[0]
    pid = p["player_id"]
    s = engine.apply_action(s, pid, {
        "kind": "card_action", "card": cid, "params": {"cell": cell}}).new_state
    p = s["players"][first]
    assert p["cells"][cell]["type"] == "room"

    inst = next(i for i in p["minors"] if i["id"] == cid)
    assert not cards.CARDS[cid]["card_action"]["available"](s, p, inst)


# ── Generic `gained` event ────────────────────────────────────────────

def test_gained_fires_on_accumulation_space_with_animals(engine, temp_card):
    """gained fires for accumulation-space goods (which may include
    animal types) at receipt time -- before the resulting accommodate
    prompt is even queued, let alone resolved."""
    def hook(state, player, inst, ctx):
        inst["data"].setdefault("seen", []).append(
            (dict(ctx["goods"]), ctx["source"], ctx.get("space_id"),
             animal_counts(player)["sheep"], len(state["prompts"])))

    cid = "test_gained_space_acc"
    temp_card(cid, "Test Gained Space Acc", "minor", "test",
              hooks={"gained": hook})
    s, first = sheep_pasture_state(engine)
    put_in_play(s, first, cid)
    set_sheep_market(s, 2)
    s = place(engine, s, {"kind": "place", "space": "sheep_market"})
    inst = next(i for i in s["players"][first]["minors"] if i["id"] == cid)
    goods, source, space_id, sheep_at_fire, prompts_at_fire = \
        inst["data"]["seen"][0]
    assert goods == {"sheep": 2}
    assert source == "space"
    assert space_id == "sheep_market"
    assert sheep_at_fire == 0  # not yet accommodated
    assert prompts_at_fire == 0  # not yet queued


def test_gained_fires_on_fixed_space_gain(engine, temp_card):
    def hook(state, player, inst, ctx):
        inst["data"].setdefault("seen", []).append(
            (dict(ctx["goods"]), ctx["source"], ctx.get("space_id")))

    cid = "test_gained_fixed_space"
    temp_card(cid, "Test Gained Fixed Space", "minor", "test",
              hooks={"gained": hook})
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, cid)
    s = place(engine, s, {"kind": "place", "space": "grain_seeds"})
    inst = next(i for i in s["players"][first]["minors"] if i["id"] == cid)
    assert inst["data"]["seen"] == [({"grain": 1}, "space", "grain_seeds")]


def test_gained_fires_on_card_extra_via_apply_extras(engine, temp_card):
    """apply_extras is the hub every hook-granted extra passes through
    (space_used bonuses among them); it fires gained(source="card")."""
    def hook(state, player, inst, ctx):
        inst["data"].setdefault("seen", []).append(
            (dict(ctx["goods"]), ctx["source"]))

    cid = "test_gained_card_extra"
    temp_card(cid, "Test Gained Card Extra", "minor", "test",
              hooks={"gained": hook})
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, cid)
    put_in_play(s, first, "occ_woodcutter")
    add_space(s, "wood_test_space", "Wood Test", acc=True, supply={"wood": 1})
    s = place(engine, s, {"kind": "place", "space": "wood_test_space"})
    inst = next(i for i in s["players"][first]["minors"] if i["id"] == cid)
    # "space" gained fires for the space's own 1 wood, then "card" for
    # Woodcutter's +1 wood bonus (routed through ctx["extra"]).
    sources = [src for _, src in inst["data"]["seen"]]
    assert sources == ["space", "card"]
    assert inst["data"]["seen"][1][0] == {"wood": 1}


def test_gained_fires_via_grant_goods(engine, temp_card):
    """grant_goods (used by space_bonus(others=True), e.g. Milk Jug) fires
    gained(source="card") for the card owner, who isn't the actor."""
    def hook(state, player, inst, ctx):
        inst["data"].setdefault("seen", []).append(
            (dict(ctx["goods"]), ctx["source"]))

    cid = "test_gained_grant_goods"
    temp_card(cid, "Test Gained Grant Goods", "minor", "test",
              hooks={"gained": hook})
    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    put_in_play(s, other, "minor_milk_jug")
    put_in_play(s, other, cid)
    add_space(s, "cattle_market", "Cattle Market", acc=True,
              supply={"cattle": 1})
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(
        s, pid, {"kind": "place", "space": "cattle_market"}).new_state
    inst = next(i for i in s["players"][other]["minors"] if i["id"] == cid)
    assert ({"food": 2}, "card") in inst["data"]["seen"]


def test_gained_fires_on_bake(engine, temp_card):
    def hook(state, player, inst, ctx):
        inst["data"].setdefault("seen", []).append(
            (dict(ctx["goods"]), ctx["source"]))

    cid = "test_gained_bake"
    temp_card(cid, "Test Gained Bake", "minor", "test",
              hooks={"gained": hook})
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, cid)
    p["improvements"].append("fireplace_2")
    s["available_improvements"].remove("fireplace_2")
    give(s, first, grain=2)
    add_space(s, "grain_utilization", "Grain Utilization")
    s = place(engine, s, {"kind": "place", "space": "grain_utilization",
                          "bake": {"fireplace_2": 2}})
    inst = next(i for i in s["players"][first]["minors"] if i["id"] == cid)
    assert inst["data"]["seen"] == [({"food": 4}, "bake")]


def test_gained_fires_on_feeding_conversion(engine, temp_card):
    def hook(state, player, inst, ctx):
        inst["data"].setdefault("seen", []).append(
            (dict(ctx["goods"]), ctx["source"]))

    cid = "test_gained_convert"
    temp_card(cid, "Test Gained Convert", "minor", "test",
              hooks={"gained": hook})
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
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
    assert ({"food": 2}, "convert") in inst["data"]["seen"]


def test_gained_fires_on_harvest_crops(engine, temp_card):
    def hook(state, player, inst, ctx):
        inst["data"].setdefault("seen", []).append(
            (dict(ctx["goods"]), ctx["source"]))

    cid = "test_gained_harvest"
    temp_card(cid, "Test Gained Harvest", "minor", "test",
              hooks={"gained": hook})
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, cid)
    p["cells"][0]["type"] = "field"
    p["cells"][0]["crops"] = {"type": "grain", "count": 3}
    log = []
    engine._start_harvest(s, log)
    inst = next(i for i in p["minors"] if i["id"] == cid)
    assert ({"grain": 1}, "harvest") in inst["data"]["seen"]


def test_gained_fires_on_round_goods_payout(engine, temp_card):
    def hook(state, player, inst, ctx):
        inst["data"].setdefault("seen", []).append(
            (dict(ctx["goods"]), ctx["source"]))

    cid = "test_gained_round_goods"
    temp_card(cid, "Test Gained Round Goods", "minor", "test",
              hooks={"gained": hook})
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, cid)
    s["round_goods"][str(s["round"] + 1)] = {str(first): {"food": 1}}
    for p in s["players"]:
        p["people_placed"] = p["people_total"]
    log = []
    engine._end_work_phase(s, log)
    inst = next(i for i in s["players"][first]["minors"] if i["id"] == cid)
    assert ({"food": 1}, "round_goods") in inst["data"]["seen"]


def test_gained_prompt_from_round_goods_stalls_placement(engine, temp_card):
    """A gained hook fired from the round_goods payout (very early in
    _start_round) may queue a prompt; _advance_work must stall on it
    (via _pending_work_start), the same as a round_start hook's prompt,
    instead of the next round ever starting with the prompt unresolved."""
    def hook(state, player, inst, ctx):
        if ctx["source"] == "round_goods":
            cards.prompt_choice(state, player, inst["id"], "Wood or clay?",
                                 ["wood", "clay"])

    def resolve(state, player, inst, ctx):
        player["resources"][ctx["option"]] += 1

    cid = "test_gained_round_goods_prompt"
    temp_card(cid, "Test Gained Round Goods Prompt", "minor", "test",
              hooks={"gained": hook}, resolve_choice=resolve)
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, cid)
    s["round_goods"][str(s["round"] + 1)] = {str(first): {"food": 1}}
    for p in s["players"]:
        p["people_placed"] = p["people_total"]
    log = []
    engine._end_work_phase(s, log)
    assert s["round"] == 2
    assert len(s["prompts"]) == 1
    owner_pid = s["players"][first]["player_id"]
    assert engine.get_waiting_for(s) == [owner_pid]
    other_pid = s["players"][1 - first]["player_id"]
    assert engine.get_valid_actions(s, other_pid) == []

    s = engine.apply_action(
        s, owner_pid, {"kind": "choice", "index": 0}).new_state
    assert s["prompts"] == []
    assert all(p["people_placed"] == 0 for p in s["players"])
    cur_pid = s["players"][s["current_player"]]["player_id"]
    assert any(a["kind"] == "place"
              for a in engine.get_valid_actions(s, cur_pid))


def test_gained_chained_grant_fires_again(engine, temp_card):
    """A card granting "each time you gain wood, get 1 food" (via
    ctx["extra"]) causes the food credit to itself fire gained(source=
    "card") -- observable by a second card watching for food gains."""
    def wood_to_food(state, player, inst, ctx):
        if ctx["goods"].get("wood"):
            ctx["extra"]["food"] = ctx["extra"].get("food", 0) + 1

    def observer(state, player, inst, ctx):
        if "food" in ctx["goods"]:
            inst["data"]["food_gained"] = \
                inst["data"].get("food_gained", 0) + ctx["goods"]["food"]

    temp_card("test_wood_to_food", "Test Wood To Food", "minor", "test",
              hooks={"gained": wood_to_food})
    temp_card("test_food_observer", "Test Food Observer", "minor", "test",
              hooks={"gained": observer})
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "test_wood_to_food")
    put_in_play(s, first, "test_food_observer")
    p = s["players"][first]
    food_before = p["resources"]["food"]
    add_space(s, "wood_only_space", "Wood Only", acc=True, supply={"wood": 1})
    s = place(engine, s, {"kind": "place", "space": "wood_only_space"})
    p = s["players"][first]
    assert p["resources"]["food"] == food_before + 1
    inst = next(i for i in p["minors"] if i["id"] == "test_food_observer")
    assert inst["data"]["food_gained"] == 1


def test_gained_depth_guard_terminates_pathological_loop(engine, temp_card):
    """"Each time you gain food, get 1 food" would loop forever without
    the depth guard. Verify it terminates with a small, bounded amount
    of extra food credited (not an unbounded/looping amount)."""
    def food_loop(state, player, inst, ctx):
        if ctx["goods"].get("food"):
            ctx["extra"]["food"] = ctx["extra"].get("food", 0) + 1

    temp_card("test_food_loop", "Test Food Loop", "minor", "test",
              hooks={"gained": food_loop})
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "test_food_loop")
    p = s["players"][first]
    food_before = p["resources"]["food"]
    add_space(s, "food_only_space", "Food Only", acc=True, supply={"food": 1})
    s = place(engine, s, {"kind": "place", "space": "food_only_space"})
    p = s["players"][first]
    # 1 (the space's own food) + 3 chained credits (depth guard caps the
    # chain at depth 3), not an infinite/huge amount.
    assert p["resources"]["food"] - food_before == 4


# ── Breeding event ────────────────────────────────────────────────────

def test_breeding_event_ctx_and_gained(engine, temp_card):
    def breeding_hook(state, player, inst, ctx):
        inst["data"]["newborns"] = dict(ctx["newborns"])
        inst["data"]["unplaced"] = dict(ctx["unplaced"])
        inst["data"]["harvest_index"] = ctx["harvest_index"]

    def gained_hook(state, player, inst, ctx):
        if ctx["source"] == "breeding":
            inst["data"].setdefault("seen", []).append(dict(ctx["goods"]))

    cid_ctx = "test_breeding_ctx"
    cid_gained = "test_breeding_gained"
    temp_card(cid_ctx, "Test Breeding Ctx", "minor", "test",
              hooks={"breeding": breeding_hook})
    temp_card(cid_gained, "Test Breeding Gained", "minor", "test",
              hooks={"gained": gained_hook})

    s = make_state(engine, 2)
    p = s["players"][0]
    put_in_play(s, 0, cid_ctx)
    put_in_play(s, 0, cid_gained)
    # Boar: one 2-cell pasture (capacity 4) holding 2 -> headroom to breed.
    p["fences"] = sorted((set(cell_edges(3)) | set(cell_edges(4))) - {"v-0-4"})
    p["cells"][3]["animal"] = {"type": "boar", "count": 2}
    # Sheep: an unfenced stable already occupied, plus a full house pet
    # slot -> nowhere to place a newborn.
    p["cells"][0]["stable"] = True
    p["cells"][0]["animal"] = {"type": "sheep", "count": 1}
    p["pets"] = {"sheep": 1}
    s["phase"] = "feeding"
    s["harvest_index"] = 2
    for pl in s["players"]:
        pl["fed"] = False
        pl["resources"]["food"] = 10
    for pl in list(s["players"]):
        s = engine.apply_action(s, pl["player_id"], {"kind": "feed"}).new_state

    p = s["players"][0]
    inst = next(i for i in p["minors"] if i["id"] == cid_ctx)
    assert inst["data"]["newborns"] == {"boar": 1}
    assert inst["data"]["unplaced"] == {"sheep": 1}
    assert inst["data"]["harvest_index"] == 2
    inst2 = next(i for i in p["minors"] if i["id"] == cid_gained)
    assert inst2["data"]["seen"] == [{"boar": 1}]


def test_breeding_hook_choice_prompt_stalls_and_resumes_once(engine, temp_card):
    """A breeding hook that queues a choice prompt (C071-style: gain a
    bonus action after breeding) must stall _end_round; resolving the
    prompt continues the round transition exactly once. If phase stayed
    "feeding" instead of the transient "breeding", resolving this prompt
    would fall into the "phase == feeding and all fed" dispatch and
    re-enter _finish_harvest, breeding a second time."""
    calls = {"n": 0}

    def breeding_hook(state, player, inst, ctx):
        calls["n"] += 1
        cards.prompt_choice(state, player, inst["id"], "Bonus Sow?",
                             ["yes", "no"])

    def resolve(state, player, inst, ctx):
        inst["data"]["resolved"] = ctx["option"]

    cid = "test_breeding_prompt"
    temp_card(cid, "Test Breeding Prompt", "minor", "test",
              hooks={"breeding": breeding_hook}, resolve_choice=resolve)
    s = make_state(engine, 2)
    put_in_play(s, 0, cid)
    s["phase"] = "feeding"
    s["round"] = 3
    for pl in s["players"]:
        pl["fed"] = False
        pl["resources"]["food"] = 10
    for pl in list(s["players"]):
        s = engine.apply_action(s, pl["player_id"], {"kind": "feed"}).new_state
    # Breeding ran (the hook fired once) and its prompt is pending; the
    # round has NOT advanced yet.
    assert s["phase"] == "breeding"
    assert len(s["prompts"]) == 1
    assert calls["n"] == 1
    round_before = s["round"]
    owner_pid = s["players"][0]["player_id"]
    s = engine.apply_action(
        s, owner_pid, {"kind": "choice", "index": 0}).new_state
    inst = next(i for i in s["players"][0]["minors"] if i["id"] == cid)
    assert inst["data"]["resolved"] == "yes"
    assert s["round"] == round_before + 1
    assert s["prompts"] == []
    assert s["phase"] == "work"
    # The breeding hook did not fire a second time.
    assert calls["n"] == 1


def test_breeding_hook_animal_grant_does_not_rerun_breeding(engine, temp_card):
    """A breeding hook granting an animal via ctx["extra"] queues the
    normal accommodate prompt; resolving it must not re-enter
    _finish_harvest (phase stays "breeding", not "feeding", while the
    prompt is pending) -- proven by the animal counts only reflecting
    ONE breeding pass."""
    def breeding_hook(state, player, inst, ctx):
        ctx["extra"]["boar"] = ctx["extra"].get("boar", 0) + 1

    cid = "test_breeding_animal_grant"
    temp_card(cid, "Test Breeding Animal Grant", "minor", "test",
              hooks={"breeding": breeding_hook})
    s = make_state(engine, 2)
    p = s["players"][0]
    put_in_play(s, 0, cid)
    p["fences"] = sorted((set(cell_edges(3)) | set(cell_edges(4))) - {"v-0-4"})
    p["cells"][3]["animal"] = {"type": "boar", "count": 2}
    s["phase"] = "feeding"
    s["round"] = 3
    for pl in s["players"]:
        pl["fed"] = False
        pl["resources"]["food"] = 10
    for pl in list(s["players"]):
        s = engine.apply_action(s, pl["player_id"], {"kind": "feed"}).new_state
    assert s["phase"] == "breeding"
    prompt = s["prompts"][0]
    assert prompt["type"] == "accommodate"
    assert prompt["gained"] == {"boar": 1}
    owner_pid = s["players"][0]["player_id"]
    # Accommodation replaces the whole animal layout, so the 3 boar
    # already on the farm (2 initial + 1 bred) must be re-placed too,
    # alongside the 1 the card just granted.
    s = engine.apply_action(s, owner_pid, {
        "kind": "accommodate",
        "placements": [{"cell": 3, "type": "boar", "count": 3}],
        "pets": {"boar": 1},
    }).new_state
    assert s["prompts"] == []
    assert s["phase"] == "work"
    # 2 initial + 1 bred (placed automatically in the pasture) + 1 from
    # the card's own grant (placed as a pet) = 4. If breeding had re-run,
    # this would be higher.
    assert animal_counts(s["players"][0])["boar"] == 4


# ── harvest_start / field-phase yield data ───────────────────────────

def test_harvest_start_fires_before_crops_move(engine, temp_card):
    def hook(state, player, inst, ctx):
        inst["data"]["crop_count_at_fire"] = player["cells"][0]["crops"]["count"]

    cid = "test_harvest_start_early"
    temp_card(cid, "Test Harvest Start Early", "minor", "test",
              hooks={"harvest_start": hook})
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, cid)
    p["cells"][0]["type"] = "field"
    p["cells"][0]["crops"] = {"type": "grain", "count": 3}
    log = []
    engine._start_harvest(s, log)
    inst = next(i for i in p["minors"] if i["id"] == cid)
    assert inst["data"]["crop_count_at_fire"] == 3
    assert p["cells"][0]["crops"]["count"] == 2  # field phase already ran
    assert s["phase"] == "feeding"


def test_harvest_start_prompt_stalls_field_phase_and_runs_once(engine, temp_card):
    def hook(state, player, inst, ctx):
        cards.prompt_choice(state, player, inst["id"], "Add a vegetable?",
                             ["yes", "no"])

    def resolve(state, player, inst, ctx):
        inst["data"]["resolved"] = ctx["option"]

    cid = "test_harvest_start_prompt"
    temp_card(cid, "Test Harvest Start Prompt", "minor", "test",
              hooks={"harvest_start": hook}, resolve_choice=resolve)
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, cid)
    p["cells"][0]["type"] = "field"
    p["cells"][0]["crops"] = {"type": "grain", "count": 3}
    log = []
    engine._start_harvest(s, log)
    # Field phase must not have run yet -- still "work" phase, crops
    # untouched, waiting on the prompt.
    assert s["phase"] == "work"
    assert p["cells"][0]["crops"]["count"] == 3
    assert len(s["prompts"]) == 1
    owner_pid = p["player_id"]
    s = engine.apply_action(
        s, owner_pid, {"kind": "choice", "index": 0}).new_state
    p = s["players"][first]
    inst = next(i for i in p["minors"] if i["id"] == cid)
    assert inst["data"]["resolved"] == "yes"
    # Field phase now ran -- exactly once (count went 3 -> 2, not lower).
    assert s["phase"] == "feeding"
    assert p["cells"][0]["crops"]["count"] == 2


def test_harvest_field_ctx_tiles_and_card_fields_breakdown(engine, temp_card):
    def hook(state, player, inst, ctx):
        inst["data"]["ctx"] = {
            "got": dict(ctx["got"]), "tiles": dict(ctx["tiles"]),
            "card_fields": dict(ctx["card_fields"]),
        }

    cid = "test_harvest_field_ctx"
    temp_card(cid, "Test Harvest Field Ctx", "minor", "test",
              field={"crops": ("vegetable",)},
              hooks={"harvest_field": hook})
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    inst = put_in_play(s, first, cid)
    inst["crops"] = {"type": "vegetable", "count": 2}
    p["cells"][0]["type"] = "field"
    p["cells"][0]["crops"] = {"type": "grain", "count": 3}
    p["cells"][1]["type"] = "field"
    p["cells"][1]["crops"] = {"type": "grain", "count": 3}
    log = []
    engine._start_harvest(s, log)
    inst = next(i for i in p["minors"] if i["id"] == cid)
    ctx = inst["data"]["ctx"]
    assert ctx["got"] == {"grain": 2, "vegetable": 1}
    assert ctx["tiles"] == {"grain": 2, "vegetable": 0}
    assert ctx["card_fields"] == {"grain": 0, "vegetable": 1}


def test_keep_crops_on_harvest_credits_without_decrementing_field(
        engine, temp_card):
    cid = "test_keep_crops"
    temp_card(cid, "Test Keep Crops", "minor", "test",
              keep_crops_on_harvest=("vegetable",))
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, cid)
    p["cells"][0]["type"] = "field"
    p["cells"][0]["crops"] = {"type": "vegetable", "count": 2}
    veg_before = p["resources"]["vegetable"]
    log = []
    engine._start_harvest(s, log)
    assert p["resources"]["vegetable"] == veg_before + 1
    # The field keeps its crop count -- not decremented.
    assert p["cells"][0]["crops"]["count"] == 2


# ── Engine phase 7: cost_mod ctx (count/start_index/index/space_id/
# improvement/card/payment) -- see decks/GUIDE.md's cost_mod section ──

def advance_until_turn(engine, s, player_id):
    """Cycle safe filler placements until it's `player_id`'s turn AND a
    new round has started (action spaces -- including single-use ones
    like farm_expansion/fencing -- reset each round, so a space already
    used this round becomes available again)."""
    start_round = s["round"]
    guard = 0
    while s["round"] == start_round or current_pid(engine, s) != player_id:
        pid = current_pid(engine, s)
        acts = engine.get_valid_actions(s, pid)
        chosen = next(a for a in acts
                      if a["kind"] == "place" and a["space"] in SAFE_SPACES)
        s = engine.apply_action(s, pid, {"kind": "place",
                                         "space": chosen["space"]}).new_state
        guard += 1
        assert guard < 50
    return s


def test_room_batch_cost_mod_uses_count(engine, temp_card):
    """A014-style: a total discount that only kicks in for a 2+ room
    batch (needs ctx["count"])."""
    cid = "test_carpenters_hammer_style"
    def mod(state, player, kind, cost, ctx):
        if kind == "room" and ctx.get("count", 1) >= 2:
            cost = dict(cost)
            cost["reed"] = max(0, cost.get("reed", 0) - 2)
        return cost
    temp_card(cid, "Batch Hammer Style", "occupation", "test", cost_mod=mod)

    # 2 rooms at once: 10 wood + 4 reed normally, -2 reed total.
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, cid)
    give(s, first, wood=10, reed=4)
    s = place(engine, s, {"kind": "place", "space": "farm_expansion",
                          "rooms": [0, 1]})
    p = s["players"][first]
    assert p["resources"]["wood"] == 0
    assert p["resources"]["reed"] == 2

    # 1 room: count == 1, no discount.
    s2 = make_state(engine, 2)
    first2 = s2["current_player"]
    put_in_play(s2, first2, cid)
    give(s2, first2, wood=5, reed=2)
    s2 = place(engine, s2, {"kind": "place", "space": "farm_expansion",
                           "rooms": [0]})
    p2 = s2["players"][first2]
    assert p2["resources"]["wood"] == 0 and p2["resources"]["reed"] == 0


def test_room_batch_migration_carpenter_regression(engine):
    """Regression for the count-scaling migration: an existing per-room
    discount card (Carpenter, -2 wood/clay/stone per room) must still
    charge the correct TOTAL for a 2-room batch."""
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "occ_carpenter")
    # 2 rooms * (5-2) wood + 2 * 2 reed.
    give(s, first, wood=6, reed=4)
    s = place(engine, s, {"kind": "place", "space": "farm_expansion",
                          "rooms": [0, 1]})
    p = s["players"][first]
    assert p["resources"]["wood"] == 0 and p["resources"]["reed"] == 0


def test_stable_cost_mod_indexed_across_batches(engine, temp_card):
    """C088-style: 'your 3rd and 4th stable each cost 1 wood less',
    built across two separate batches (2 stables each) spanning the
    3rd/4th boundary."""
    cid = "test_carpenters_apprentice_style"
    def mod(state, player, kind, cost, ctx):
        if kind == "stable" and ctx.get("index") in (3, 4) and cost.get("wood"):
            cost = dict(cost)
            cost["wood"] = max(0, cost["wood"] - 1)
        return cost
    temp_card(cid, "Apprentice Style", "occupation", "test", cost_mod=mod)

    s = make_state(engine, 2)
    first = s["current_player"]
    first_pid = s["players"][first]["player_id"]
    put_in_play(s, first, cid)
    give(s, first, wood=10)
    s = place(engine, s, {"kind": "place", "space": "farm_expansion",
                          "stables": [0, 1]})
    p = s["players"][first]
    assert p["resources"]["wood"] == 6  # stables #1-2: full price (2 each)

    s = advance_until_turn(engine, s, first_pid)
    s = place(engine, s, {"kind": "place", "space": "farm_expansion",
                          "stables": [2, 3]})
    p = s["players"][first]
    assert p["resources"]["wood"] == 4  # stables #3-4: 1 wood less each


def test_stable_cost_mod_next_stable_free_one_time(engine, temp_card):
    """K121-style: 'the next stable you place costs nothing', a one-time
    freebie committed only when a real build follows (mirrors FR080
    Fencing Master's pending/commit pattern)."""
    cid = "test_sawhorse_stable_style"
    def mod(state, player, kind, cost, ctx):
        if kind != "stable":
            return cost
        inst = next((i for i in player["minors"] if i["id"] == cid), None)
        if inst is None or inst["data"].get("used"):
            return cost
        if ctx.get("index") != ctx.get("start_index", 0) + 1:
            return cost
        inst["data"]["_pending"] = True
        cost = dict(cost)
        cost["wood"] = 0
        return cost
    def built_hook(state, player, inst, ctx):
        if inst["data"].pop("_pending", False):
            inst["data"]["used"] = True
    temp_card(cid, "Sawhorse Stable Style", "minor", "test", cost_mod=mod,
              hooks={"stable_built": built_hook})

    s = make_state(engine, 2)
    first = s["current_player"]
    first_pid = s["players"][first]["player_id"]
    put_in_play(s, first, cid)
    give(s, first, wood=6)
    s = place(engine, s, {"kind": "place", "space": "farm_expansion",
                          "stables": [0, 1]})
    p = s["players"][first]
    assert p["resources"]["wood"] == 4  # 1st free, 2nd costs 2

    s = advance_until_turn(engine, s, first_pid)
    s = place(engine, s, {"kind": "place", "space": "farm_expansion",
                          "stables": [2]})
    p = s["players"][first]
    assert p["resources"]["wood"] == 2  # freebie already spent


def test_fences_cost_mod_uses_start_index(engine, temp_card):
    """A '3rd/6th fence built is free' card needs ctx["start_index"] to
    know how many fences existed before this batch, so it can tell
    which absolute positions the new batch covers."""
    cid = "test_every_3rd_fence_free"
    free_positions = (3, 6)
    def mod(state, player, kind, cost, ctx):
        if kind != "fences" or not cost.get("wood"):
            return cost
        existing = ctx.get("start_index", 0)
        count = ctx.get("count", 0)
        free = sum(1 for i in range(1, count + 1)
                  if (existing + i) in free_positions)
        if free:
            cost = dict(cost)
            cost["wood"] = max(0, cost["wood"] - free)
        return cost
    temp_card(cid, "Every 3rd Fence Free", "occupation", "test", cost_mod=mod)

    s = make_state(engine, 2)
    first = s["current_player"]
    first_pid = s["players"][first]["player_id"]
    put_in_play(s, first, cid)
    give(s, first, wood=10)
    add_space(s, "fencing", "Fencing")
    # Fences #1-4 (single-cell pasture around cell 4): #3 is free.
    s = place(engine, s, {"kind": "place", "space": "fencing",
                          "fences": cell_edges(4)})
    p = s["players"][first]
    assert p["resources"]["wood"] == 7  # 4 - 1 free = 3 spent

    s = advance_until_turn(engine, s, first_pid)
    # Fences #5-7 (extends the pasture to cell 9, below cell 4 -- the
    # shared edge h-1-4 is already fenced): #6 is free.
    s = place(engine, s, {"kind": "place", "space": "fencing",
                          "fences": ["h-2-4", "v-1-4", "v-1-5"]})
    p = s["players"][first]
    assert p["resources"]["wood"] == 5  # 3 - 1 free = 2 spent


def test_cost_mod_space_id_targets_specific_space(engine, temp_card):
    """D082-style: a discount conditioned on the originating action
    space (House Redevelopment), not on kind alone -- kind='improvement'
    is also reachable from the plain Major Improvement space, so the
    card must check ctx["space_id"]."""
    cid = "test_hunting_trophy_style"
    def mod(state, player, kind, cost, ctx):
        if (kind == "improvement" and ctx.get("space_id") == "house_redevelopment"
                and cost.get("stone")):
            cost = dict(cost)
            cost["stone"] -= 1
        return cost
    temp_card(cid, "Hunting Trophy Style", "occupation", "test", cost_mod=mod)

    # Via House Redevelopment: renovate wood->clay, then build the Well
    # (normally 1 wood + 3 stone) at a 1-stone discount.
    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, cid)
    p = s["players"][first]
    rooms_n = sum(1 for c in p["cells"] if c["type"] == "room")
    give(s, first, clay=rooms_n, reed=1, wood=1, stone=2)
    add_space(s, "house_redevelopment", "House Redevelopment")
    s = place(engine, s, {"kind": "place", "space": "house_redevelopment",
                          "improvement": "well"})
    p = s["players"][first]
    assert "well" in p["improvements"]
    assert p["resources"]["stone"] == 0 and p["resources"]["wood"] == 0

    # Via the plain Major Improvement space: no discount, 2 stone isn't
    # enough (needs the full 3).
    s2 = make_state(engine, 2)
    first2 = s2["current_player"]
    put_in_play(s2, first2, cid)
    give(s2, first2, wood=1, stone=2)
    add_space(s2, "major_improvement", "Major Improvement")
    with pytest.raises(ValueError):
        place(engine, s2, {"kind": "place", "space": "major_improvement",
                          "improvement": "well"})


def test_minor_kind_cost_mod_food_discount(engine, temp_card):
    """FR024-style: 'pay up to 2 food less to play an occupation or
    minor' needs minor costs routed through modified_cost (kind='minor'),
    which they weren't before engine phase 7."""
    minor_cid = "test_costly_minor"
    temp_card(minor_cid, "Costly Minor", "minor", "test",
              cost={"food": 2, "wood": 1})
    disc_cid = "test_golden_rose_style"
    def mod(state, player, kind, cost, ctx):
        if kind in ("minor", "occupation") and cost.get("food"):
            cost = dict(cost)
            cost["food"] = max(0, cost["food"] - 2)
        return cost
    temp_card(disc_cid, "Golden Rose Style", "occupation", "test", cost_mod=mod)

    # Without the discount card: 0 food isn't enough (needs 2). (Base
    # game grants starting food, so zero it out first.)
    s = make_state(engine, 2)
    first = s["current_player"]
    s["players"][first]["resources"]["food"] = 0
    give_card(s, first, minor_cid)
    give(s, first, wood=1)
    add_space(s, "meeting_place", "Meeting Place")
    with pytest.raises(ValueError):
        place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": minor_cid}})

    # With the discount card: food cost fully waived (net cost is just
    # the 1 wood).
    s2 = make_state(engine, 2)
    first2 = s2["current_player"]
    s2["players"][first2]["resources"]["food"] = 0
    put_in_play(s2, first2, disc_cid)
    give_card(s2, first2, minor_cid)
    give(s2, first2, wood=1)
    add_space(s2, "meeting_place", "Meeting Place")
    s2 = place(engine, s2, {"kind": "place", "space": "meeting_place",
                           "minor": {"card": minor_cid}})
    p2 = s2["players"][first2]
    assert minor_cid in [i["id"] for i in p2["minors"]]
    assert p2["resources"]["food"] == 0
    assert p2["resources"]["wood"] == 0


def test_occupation_kind_cost_mod_food_discount(engine, temp_card):
    """FR024-style, occupation half: the Lessons space's food cost now
    also runs through modified_cost (kind='occupation')."""
    disc_cid = "test_golden_rose_occ_style"
    def mod(state, player, kind, cost, ctx):
        if kind == "occupation" and cost.get("food"):
            cost = dict(cost)
            cost["food"] = max(0, cost["food"] - 2)
        return cost
    temp_card(disc_cid, "Golden Rose Occ Style", "occupation", "test",
              cost_mod=mod)
    occ_cid = "test_playable_occ"
    temp_card(occ_cid, "Playable Occ", "occupation", "test")

    # occs_played > 0 forces the Lessons space's escalating cost to 1
    # food, so there's something to discount. (Base game grants starting
    # food, so zero it out first.)

    # Without the discount card: 0 food isn't enough.
    s = make_state(engine, 2)
    first = s["current_player"]
    s["players"][first]["occs_played"] = 1
    s["players"][first]["resources"]["food"] = 0
    give_card(s, first, occ_cid)
    add_space(s, "lessons", "Lessons")
    with pytest.raises(ValueError):
        place(engine, s, {"kind": "place", "space": "lessons", "card": occ_cid})

    # With the discount card: the 1-food cost is waived.
    s2 = make_state(engine, 2)
    first2 = s2["current_player"]
    s2["players"][first2]["occs_played"] = 1
    s2["players"][first2]["resources"]["food"] = 0
    put_in_play(s2, first2, disc_cid)
    give_card(s2, first2, occ_cid)
    add_space(s2, "lessons", "Lessons")
    s2 = place(engine, s2, {"kind": "place", "space": "lessons",
                           "card": occ_cid})
    p2 = s2["players"][first2]
    assert occ_cid in [i["id"] for i in p2["occupations"]]
    assert p2["resources"]["food"] == 0


def test_payment_channel_reed_to_clay(engine, temp_card):
    """E36-style: 'replace 1 or 2 reed with the same amount of clay when
    you renovate', driven by the client action's own "payment" field
    (threaded into ctx by engine._resolve_space). Garbage payment must
    raise ValueError rather than being silently ignored."""
    cid = "test_clay_roof_style"
    def mod(state, player, kind, cost, ctx):
        if kind != "renovation":
            return cost
        payment = ctx.get("payment")
        if payment is None:
            return cost
        if not isinstance(payment, dict) or set(payment) != {"reed_to_clay"}:
            raise ValueError("Clay Roof: invalid payment")
        n = payment["reed_to_clay"]
        if not isinstance(n, int) or n <= 0 or n > 2 or n > cost.get("reed", 0):
            raise ValueError("Clay Roof: invalid payment")
        cost = dict(cost)
        cost["reed"] -= n
        cost["clay"] = cost.get("clay", 0) + n
        return cost
    temp_card(cid, "Clay Roof Style", "occupation", "test", cost_mod=mod)

    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    rooms_n = sum(1 for c in p["cells"] if c["type"] == "room")
    put_in_play(s, first, cid)
    # renovation_possible (the _space_usable preview) has no visibility
    # into ctx["payment"] -- it only knows the space is usable at all if
    # the UNMODIFIED cost {clay: rooms_n, reed: 1} is affordable, so the
    # player needs the normal reed too even though the real payment
    # won't end up spending it. Give clay for the swap ON TOP of that.
    give(s, first, clay=rooms_n + 1, reed=1)
    add_space(s, "house_redevelopment", "House Redevelopment")
    s = place(engine, s, {"kind": "place", "space": "house_redevelopment",
                          "payment": {"reed_to_clay": 1}})
    p = s["players"][first]
    assert p["house_type"] == "clay"
    # The 1 reed was never charged (cost["reed"] became 0 and was
    # dropped, so pay() never touches it) -- it's saved, not spent --
    # while the extra clay covering the swap WAS spent.
    assert p["resources"]["reed"] == 1 and p["resources"]["clay"] == 0

    s2 = make_state(engine, 2)
    first2 = s2["current_player"]
    put_in_play(s2, first2, cid)
    give(s2, first2, clay=rooms_n, reed=1)
    add_space(s2, "house_redevelopment", "House Redevelopment")
    with pytest.raises(ValueError):
        place(engine, s2, {"kind": "place", "space": "house_redevelopment",
                          "payment": {"reed_to_clay": 5}})


# ── Engine phase 8: card-aware animal capacity ───────────────────────
# D011 Lawn Fertilizer, E29 Shepherd's Pipe, FR013 Chameleon, C012/E58/
# I102/K145 (card-held storage), D085 Reader (computed extra_rooms).

def test_pasture_capacity_mod_size_conditioned(engine, temp_card):
    """D011-style: pastures of size 1 hold up to 3 animals (6 with a
    stable) -- pasture_capacity_mod=fn(state, player, inst, info)."""
    def _mod(state, player, inst, info):
        if info["size"] != 1:
            return 0
        return 2 if info["stables"] >= 1 else 1

    temp_card("test_lawn_fert", "Test Lawn Fertilizer", "minor", "test",
              cost={}, pasture_capacity_mod=_mod)
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "test_lawn_fert")
    p["fences"] = sorted(cell_edges(4))  # size-1 pasture at cell 4

    assert cards.pasture_capacity(s, p, [4], "sheep") == 3  # 2 base + 1
    p["cells"][4]["animal"] = {"type": "sheep", "count": 3}
    ok, err = engine._validate_animals(s, p)
    assert ok, err
    p["cells"][4]["animal"]["count"] = 4
    ok, err = engine._validate_animals(s, p)
    assert not ok

    p["cells"][4]["stable"] = True  # doubled by a stable: 6
    assert cards.pasture_capacity(s, p, [4], "sheep") == 6
    p["cells"][4]["animal"]["count"] = 6
    ok, err = engine._validate_animals(s, p)
    assert ok, err
    p["cells"][4]["animal"]["count"] = 7
    ok, err = engine._validate_animals(s, p)
    assert not ok


def test_pasture_capacity_mod_stacks_with_flat_bonus(engine, temp_card):
    """Regression: the existing flat pasture_capacity_bonus (Drinking
    Trough) still applies alongside a pasture_capacity_mod card."""
    def _mod(state, player, inst, info):
        return 1 if info["size"] == 1 else 0

    temp_card("test_lawn_fert2", "Test Lawn Fertilizer", "minor", "test",
              cost={}, pasture_capacity_mod=_mod)
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "test_lawn_fert2")
    put_in_play(s, first, "minor_drinking_trough")
    p["fences"] = sorted(cell_edges(4))
    # base 2 + Drinking Trough's flat 2 + Lawn Fertilizer's mod 1 = 5.
    assert cards.pasture_capacity(s, p, [4], "sheep") == 5


def test_pasture_capacity_mod_type_conditioned(engine, temp_card):
    """E29-style: +2 sheep in each pasture where you keep sheep; up to 2
    sheep (vs the normal 1) in an unfenced stable."""
    def _pasture_mod(state, player, inst, info):
        return 2 if info["animal_type"] == "sheep" else 0

    def _stable_mod(state, player, inst, animal_type):
        return 1 if animal_type == "sheep" else 0

    temp_card("test_shepherds_pipe", "Test Shepherd's Pipe", "minor", "test",
              cost={}, pasture_capacity_mod=_pasture_mod,
              unfenced_stable_capacity_mod=_stable_mod)
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "test_shepherds_pipe")
    p["fences"] = sorted(cell_edges(4))  # size-1 pasture, base capacity 2

    assert cards.pasture_capacity(s, p, [4], "sheep") == 4  # 2 base + 2
    assert cards.pasture_capacity(s, p, [4], "cattle") == 2  # unaffected
    assert cards.unfenced_stable_capacity(s, p, "sheep") == 2
    assert cards.unfenced_stable_capacity(s, p, "cattle") == 1

    p["cells"][0]["stable"] = True
    p["cells"][0]["animal"] = {"type": "sheep", "count": 2}
    ok, err = engine._validate_animals(s, p)
    assert ok, err  # unfenced stable holds 2 sheep
    p["cells"][0]["animal"] = {"type": "cattle", "count": 2}
    ok, err = engine._validate_animals(s, p)
    assert not ok  # ... but only 1 cattle


def test_pasture_secondary_types_mixed(engine, temp_card):
    """FR013-style: 1 wild boar allowed in each pasture that holds sheep,
    still counting against the pasture's normal total capacity; a
    pasture mixing two non-sheep types gets no allowance at all ("no
    sheep -> no allowance")."""
    def _secondary(state, player, inst, info):
        return {"boar": 1} if info["animal_type"] == "sheep" else {}

    temp_card("test_chameleon", "Test Chameleon", "minor", "test",
              cost={}, pasture_secondary_types=_secondary)
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "test_chameleon")
    fences = set(cell_edges(3)) | set(cell_edges(4))
    fences.discard("v-0-4")
    p["fences"] = sorted(fences)  # size-2 pasture [3, 4], capacity 4

    p["cells"][3]["animal"] = {"type": "sheep", "count": 3}
    p["cells"][4]["animal"] = {"type": "boar", "count": 1}
    ok, err = engine._validate_animals(s, p)
    assert ok, err  # 3 sheep + 1 boar = 4 <= capacity; boar <= allowance

    p["cells"][4]["animal"]["count"] = 2
    ok, err = engine._validate_animals(s, p)
    assert not ok  # 2 boar > the 1-boar allowance

    p["cells"][3]["animal"]["count"] = 4
    p["cells"][4]["animal"]["count"] = 1
    ok, err = engine._validate_animals(s, p)
    assert not ok  # 4 sheep + 1 boar = 5 > capacity 4 (total still enforced)

    # No sheep anywhere in the pasture -> the mixing allowance never
    # applies, so two non-sheep types still can't share it.
    p["cells"][3]["animal"] = {"type": "cattle", "count": 1}
    p["cells"][4]["animal"] = {"type": "boar", "count": 1}
    ok, err = engine._validate_animals(s, p)
    assert not ok


def test_holds_animals_typed_caps(engine, temp_card):
    """I102-style: 1 sheep, 1 wild boar, and 1 cattle on the card."""
    def _rule(state, player, inst):
        return {"types": {"sheep": 1, "boar": 1, "cattle": 1}}

    temp_card("test_wildlife_reserve", "Test Wildlife Reserve", "minor",
              "test", cost={}, holds_animals=_rule)
    s = make_state(engine, 2)
    p = s["players"][s["current_player"]]
    inst = put_in_play(s, p["index"], "test_wildlife_reserve")

    inst["held"] = {"sheep": 1, "boar": 1, "cattle": 1}
    ok, err = cards.validate_held(s, p)
    assert ok, err

    inst["held"] = {"sheep": 2}
    ok, err = cards.validate_held(s, p)
    assert not ok


def test_holds_animals_total_cap(engine, temp_card):
    """E58-style: up to 2 animals total, of any mix of types."""
    def _rule(state, player, inst):
        return {"total": 2}

    temp_card("test_animal_yard", "Test Animal Yard", "minor", "test",
              cost={}, holds_animals=_rule)
    s = make_state(engine, 2)
    p = s["players"][s["current_player"]]
    inst = put_in_play(s, p["index"], "test_animal_yard")

    inst["held"] = {"sheep": 1, "boar": 1}
    ok, err = cards.validate_held(s, p)
    assert ok, err

    inst["held"] = {"sheep": 2, "boar": 1}
    ok, err = cards.validate_held(s, p)
    assert not ok


def test_holds_animals_unlimited(engine, temp_card):
    """K145-style: unlimited wild boar (and nothing else)."""
    def _rule(state, player, inst):
        return {"types": {"boar": None}}

    temp_card("test_forest_pasture", "Test Forest Pasture", "minor",
              "test", cost={}, holds_animals=_rule)
    s = make_state(engine, 2)
    p = s["players"][s["current_player"]]
    inst = put_in_play(s, p["index"], "test_forest_pasture")

    inst["held"] = {"boar": 50}
    ok, err = cards.validate_held(s, p)
    assert ok, err

    inst["held"] = {"sheep": 1}
    ok, err = cards.validate_held(s, p)
    assert not ok  # sheep isn't in "types" at all


def test_animal_counts_includes_held_and_breeds(engine, temp_card):
    """animal_counts folds in card-held animals -- so two cattle (1 on
    the farm, 1 held on a card) breed a newborn like any other pair."""
    def _rule(state, player, inst):
        return {"types": {"cattle": None}}

    temp_card("test_cattle_farm", "Test Cattle Farm", "minor", "test",
              cost={}, holds_animals=_rule)
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    inst = put_in_play(s, first, "test_cattle_farm")
    p["fences"] = sorted(cell_edges(4))
    p["cells"][4]["animal"] = {"type": "cattle", "count": 1}
    inst["held"] = {"cattle": 1}
    assert animal_counts(p)["cattle"] == 2

    s["phase"] = "feeding"
    s["round"] = 3
    for pl in s["players"]:
        pl["fed"] = False
        pl["resources"]["food"] = 10
    for pl in list(s["players"]):
        s = engine.apply_action(s, pl["player_id"], {"kind": "feed"}).new_state
    assert animal_counts(s["players"][first])["cattle"] == 3


def test_place_newborn_animal_into_holder_card(engine, temp_card):
    """When no pasture/stable/house room is left, a newborn falls back
    to a holder card with headroom instead of being lost."""
    def _rule(state, player, inst):
        return {"types": {"boar": None}}

    temp_card("test_forest_pasture2", "Test Forest Pasture", "minor",
              "test", cost={}, holds_animals=_rule)
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    inst = put_in_play(s, first, "test_forest_pasture2")
    p["pets"] = {"sheep": cards.house_capacity(s, p)}  # house already full

    placed = engine._place_newborn_animal(s, p, "boar")
    assert placed
    assert inst["held"] == {"boar": 1}


def test_accommodate_places_into_holder_card(engine, temp_card):
    """Accommodate prompt round-trip with a {"card": ...} placement."""
    def _rule(state, player, inst):
        return {"types": {"sheep": None}}

    temp_card("test_animal_yard2", "Test Animal Yard", "minor", "test",
              cost={}, holds_animals=_rule)
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "test_animal_yard2")
    set_sheep_market(s, 2)
    s = place(engine, s, {"kind": "place", "space": "sheep_market"})
    pid = s["players"][first]["player_id"]
    s = engine.apply_action(s, pid, {
        "kind": "accommodate",
        "placements": [{"card": "test_animal_yard2", "type": "sheep",
                        "count": 2}],
    }).new_state
    p = s["players"][first]
    inst = next(i for i in p["minors"] if i["id"] == "test_animal_yard2")
    assert inst["held"] == {"sheep": 2}
    assert animal_counts(p)["sheep"] == 2


def test_callable_extra_rooms_gates_family_growth(engine, temp_card):
    """D085-style: extra_rooms may be a computed fn(state, player, inst)
    -> int (room for one person once you have 6 occupations in play),
    not just a static per-card value."""
    def _extra(state, player, inst):
        return 1 if len(player["occupations"]) >= 6 else 0

    temp_card("test_reader", "Test Reader", "occupation", "test",
              cost={}, extra_rooms=_extra)
    for i in range(5):
        temp_card(f"test_filler_occ_{i}", f"Test Filler {i}",
                  "occupation", "test", cost={})

    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, "test_reader")
    for i in range(4):
        put_in_play(s, first, f"test_filler_occ_{i}")
    assert len(p["occupations"]) == 5

    add_space(s, "basic_wish", "Basic Wish for Children")
    acts = engine.get_valid_actions(s, p["player_id"])
    assert not any(a["kind"] == "place" and a["space"] == "basic_wish"
                  for a in acts)

    put_in_play(s, first, "test_filler_occ_4")  # 6th occupation
    assert len(p["occupations"]) == 6
    acts = engine.get_valid_actions(s, p["player_id"])
    assert any(a["kind"] == "place" and a["space"] == "basic_wish"
              for a in acts)

    s = place(engine, s, {"kind": "place", "space": "basic_wish"})
    assert s["players"][first]["people_total"] == 3


# ── Card action spaces (card_space, engine phase 9) ───────────────────
# A card whose spec declares `card_space={...}` IS an action space: once
# played, it's appended to state["action_spaces"] as
# {"id": "card:<cid>", ..., "card_space": True, "card": cid,
#  "owner": <owner index>}. See decks/GUIDE.md's "card_space" section.

def test_card_space_for_all_resolve(engine, temp_card):
    """A card_space open to everyone (owner_only defaults False): another
    player places on it and gets the resolve fn's goods; both space_used
    and gained fire with the card's own space id. The owner can use it
    too, in a later placement."""
    events = []

    def resolve(state, player, inst, action, log):
        log.append(f"{player['name']} takes 2 stone from the card space")
        return {"stone": 2}

    def on_space_used(state, player, inst, ctx):
        # space_used is broadcast to every player's cards (unlike
        # gained, which is owner-only) -- one instance (the card_space
        # itself, in `first`'s minors) sees every placer's use.
        if ctx["space_id"] == "card:test_stone_deposit":
            events.append(("space_used", ctx["actor"], dict(ctx["goods"])))

    def on_gained(state, player, inst, ctx):
        events.append(("gained", ctx["actor"], ctx["source"], dict(ctx["goods"])))

    temp_card("test_stone_deposit", "Test Stone Deposit", "minor", "test",
              cost={}, card_space={"resolve": resolve},
              hooks={"space_used": on_space_used})
    # `gained` only fires a player's OWN cards (see cards.fire_gained),
    # so observing it for whichever player places (owner or not) needs
    # an observer card in EACH player's own in_play, not just the
    # card_space's own card.
    temp_card("test_gained_observer", "Test Gained Observer", "minor",
              "test", cost={}, hooks={"gained": on_gained})

    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    give_card(s, first, "test_stone_deposit")
    put_in_play(s, first, "test_gained_observer")
    put_in_play(s, other, "test_gained_observer")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "test_stone_deposit"}})
    space = next(sp for sp in s["action_spaces"]
                if sp["id"] == "card:test_stone_deposit")
    assert space["owner"] == first
    assert space["card"] == "test_stone_deposit"

    assert s["current_player"] == other  # other's turn next
    stone_before = s["players"][other]["resources"]["stone"]
    s = place(engine, s, {"kind": "place", "space": "card:test_stone_deposit"})
    assert s["players"][other]["resources"]["stone"] == stone_before + 2
    assert ("space_used", other, {"stone": 2}) in events
    assert ("gained", other, "space", {"stone": 2}) in events

    # The owner can use it too (force it to be their turn again, same
    # round -- both players still have a second placement available).
    space = next(sp for sp in s["action_spaces"]
                if sp["id"] == "card:test_stone_deposit")
    space["occupied_by"] = None
    space["extra_occupants"] = []
    s["current_player"] = first
    stone_before = s["players"][first]["resources"]["stone"]
    s = place(engine, s, {"kind": "place", "space": "card:test_stone_deposit"})
    assert s["players"][first]["resources"]["stone"] == stone_before + 2
    assert ("space_used", first, {"stone": 2}) in events
    assert ("gained", first, "space", {"stone": 2}) in events


def test_card_space_owner_only(engine, temp_card):
    """D023-style owner_only=True: no other player sees or may use it."""
    def resolve(state, player, inst, action, log):
        return {"food": 1}

    temp_card("test_owner_only_space", "Test Owner Only Space", "minor",
              "test", cost={},
              card_space={"owner_only": True, "resolve": resolve})

    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    give_card(s, first, "test_owner_only_space")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "test_owner_only_space"}})

    other_pid = s["players"][other]["player_id"]
    valid = engine.get_valid_actions(s, other_pid)
    assert not any(a["kind"] == "place"
                  and a["space"] == "card:test_owner_only_space"
                  for a in valid)
    with pytest.raises(ValueError):
        engine.apply_action(
            s, other_pid,
            {"kind": "place", "space": "card:test_owner_only_space"})

    first_pid = s["players"][first]["player_id"]
    s["current_player"] = first
    food_before = s["players"][first]["resources"]["food"]
    s = engine.apply_action(
        s, first_pid,
        {"kind": "place", "space": "card:test_owner_only_space"}).new_state
    assert s["players"][first]["resources"]["food"] == food_before + 1


def test_card_space_toll_to_owner(engine, temp_card):
    """I337-style: a non-owner placer pays the owner 1 food and gets 5
    clay -- exact resource flow both sides. The owner pays no toll."""
    def resolve(state, player, inst, action, log):
        owner = cards.card_space_owner(state, inst)
        if player is not owner:
            if player["resources"]["food"] < 1:
                raise ValueError("You must pay 1 food to use this space")
            player["resources"]["food"] -= 1
            cards.grant_goods(state, owner, {"food": 1}, log)
            log.append(f"{player['name']} pays {owner['name']} 1 food")
        log.append(f"{player['name']} takes 5 clay")
        return {"clay": 5}

    temp_card("test_clay_deposit_space", "Test Clay Deposit Space", "minor",
              "test", cost={}, card_space={"resolve": resolve})

    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    give_card(s, first, "test_clay_deposit_space")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "test_clay_deposit_space"}})
    give(s, other, food=1)
    other_food = s["players"][other]["resources"]["food"]
    owner_food = s["players"][first]["resources"]["food"]

    s = place(engine, s, {"kind": "place", "space": "card:test_clay_deposit_space"})
    assert s["players"][other]["resources"]["clay"] == 5
    assert s["players"][other]["resources"]["food"] == other_food - 1
    assert s["players"][first]["resources"]["food"] == owner_food + 1


def test_card_space_toll_insufficient_food_raises_atomically(engine, temp_card):
    """Placing on the I337-style toll space without the toll raises, and
    the whole action rolls back cleanly (apply_action deep-copies state
    before mutating it, so a raise anywhere -- even after occupied_by
    was already set -- leaves the caller's state object untouched)."""
    def resolve(state, player, inst, action, log):
        owner = cards.card_space_owner(state, inst)
        if player is not owner:
            if player["resources"]["food"] < 1:
                raise ValueError("You must pay 1 food to use this space")
            player["resources"]["food"] -= 1
            cards.grant_goods(state, owner, {"food": 1}, log)
        return {"clay": 5}

    temp_card("test_clay_deposit_space2", "Test Clay Deposit Space", "minor",
              "test", cost={}, card_space={"resolve": resolve})

    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    give_card(s, first, "test_clay_deposit_space2")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "test_clay_deposit_space2"}})
    s["players"][other]["resources"]["food"] = 0
    other_pid = s["players"][other]["player_id"]
    space = next(sp for sp in s["action_spaces"]
                if sp["id"] == "card:test_clay_deposit_space2")
    people_placed_before = s["players"][other]["people_placed"]
    owner_food_before = s["players"][first]["resources"]["food"]

    with pytest.raises(ValueError):
        engine.apply_action(
            s, other_pid,
            {"kind": "place", "space": "card:test_clay_deposit_space2"})

    # Nothing changed: the mutations happened on apply_action's internal
    # deepcopy, discarded when it raised instead of returning.
    assert space["occupied_by"] is None
    assert space["extra_occupants"] == []
    assert s["players"][other]["resources"]["clay"] == 0
    assert s["players"][other]["resources"]["food"] == 0
    assert s["players"][other]["people_placed"] == people_placed_before
    assert s["players"][first]["resources"]["food"] == owner_food_before


def test_card_space_accumulation(engine, temp_card):
    """E164-style: a pure accumulation card_space (no resolve fn)
    replenishes at round_start and empties on use, same as any other
    accumulation space -- and is NOT usable the round it's played
    (its supply starts empty, and only the next round_start fills it)."""
    temp_card("test_extra_forest", "Test Extra Forest", "occupation",
              "test", cost={}, card_space={"acc": {"wood": 2}})

    s = make_state(engine, 2)
    first = s["current_player"]
    give_card(s, first, "test_extra_forest")
    s = place(engine, s, {"kind": "place", "space": "lessons",
                          "card": "test_extra_forest"})
    space = next(sp for sp in s["action_spaces"]
                if sp["id"] == "card:test_extra_forest")
    assert space["accumulates"]
    assert space["supply"] == {}
    valid = engine.get_valid_actions(s, current_pid(engine, s))
    assert not any(a["kind"] == "place" and a["space"] == "card:test_extra_forest"
                  for a in valid)

    # Finish round 1; the space should not be pickable (empty supply).
    while s["round"] == 1:
        pid = current_pid(engine, s)
        act = next(a for a in engine.get_valid_actions(s, pid)
                  if a["kind"] == "place" and a["space"] in SAFE_SPACES)
        s = place(engine, s, {"kind": "place", "space": act["space"]})

    space = next(sp for sp in s["action_spaces"]
                if sp["id"] == "card:test_extra_forest")
    assert space["supply"] == {"wood": 2}

    pid = current_pid(engine, s)
    pidx = engine._player_idx(s, pid)
    wood_before = s["players"][pidx]["resources"]["wood"]
    s = place(engine, s, {"kind": "place", "space": "card:test_extra_forest"})
    assert s["players"][pidx]["resources"]["wood"] == wood_before + 2
    space = next(sp for sp in s["action_spaces"]
                if sp["id"] == "card:test_extra_forest")
    assert space["supply"] == {}  # taken, empties like any accumulation space


def test_card_space_accumulation_solo_not_halved(engine, temp_card):
    """A card_space's own "acc" is unaffected by the Forest-only solo
    halving rule (that special case is keyed on the literal id "forest",
    never on a "card:..." id) -- it replenishes its declared amount in
    solo play just like any multiplayer game."""
    temp_card("test_extra_forest_solo", "Test Extra Forest Solo",
              "occupation", "test", cost={}, card_space={"acc": {"wood": 2}})

    s = make_state(engine, 1)
    give_card(s, 0, "test_extra_forest_solo")
    s = place(engine, s, {"kind": "place", "space": "lessons",
                          "card": "test_extra_forest_solo"})
    while s["round"] == 1:
        pid = current_pid(engine, s)
        act = next(a for a in engine.get_valid_actions(s, pid)
                  if a["kind"] == "place" and a["space"] in SAFE_SPACES)
        s = place(engine, s, {"kind": "place", "space": act["space"]})

    space = next(sp for sp in s["action_spaces"]
                if sp["id"] == "card:test_extra_forest_solo")
    assert space["supply"] == {"wood": 2}


def test_card_space_usable_gate_by_round(engine, temp_card):
    """D023-style: owner_only plus a round-window `usable` gate, both
    enforced at once."""
    def usable(state, player, inst):
        return 3 <= state["round"] <= 5

    def resolve(state, player, inst, action, log):
        return {"reed": 1}

    temp_card("test_pioneering", "Test Pioneering", "minor", "test",
              cost={}, card_space={"owner_only": True, "usable": usable,
                                   "resolve": resolve})

    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    give_card(s, first, "test_pioneering")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "test_pioneering"}})
    first_pid = s["players"][first]["player_id"]
    other_pid = s["players"][other]["player_id"]

    # Round 1: outside the usable window -- unavailable even to the owner.
    s["current_player"] = first
    valid = engine.get_valid_actions(s, first_pid)
    assert not any(a["kind"] == "place" and a["space"] == "card:test_pioneering"
                  for a in valid)

    # Round 3: in the window, but still owner_only.
    s["round"] = 3
    s["current_player"] = other
    valid = engine.get_valid_actions(s, other_pid)
    assert not any(a["kind"] == "place" and a["space"] == "card:test_pioneering"
                  for a in valid)
    s["current_player"] = first
    valid = engine.get_valid_actions(s, first_pid)
    assert any(a["kind"] == "place" and a["space"] == "card:test_pioneering"
              for a in valid)
    s = engine.apply_action(
        s, first_pid,
        {"kind": "place", "space": "card:test_pioneering"}).new_state
    assert s["players"][first]["resources"]["reed"] == 1


def test_card_space_resolve_animals_route_through_accommodation(engine, temp_card):
    """A resolve fn returning an animal type routes it through the
    normal accommodation prompt, same as any other space's goods."""
    def resolve(state, player, inst, action, log):
        return {"sheep": 1}

    temp_card("test_sheep_space", "Test Sheep Space", "minor", "test",
              cost={}, card_space={"resolve": resolve})

    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    give_card(s, first, "test_sheep_space")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "test_sheep_space"}})
    other_pid = s["players"][other]["player_id"]
    s = engine.apply_action(
        s, other_pid,
        {"kind": "place", "space": "card:test_sheep_space"}).new_state

    assert engine.get_waiting_for(s) == [other_pid]
    prompt = s["prompts"][0]
    assert prompt["type"] == "accommodate"
    assert prompt["gained"] == {"sheep": 1}
    s = engine.apply_action(s, other_pid, {
        "kind": "accommodate", "placements": [], "pets": {"sheep": 1},
    }).new_state
    assert s["players"][other]["pets"]["sheep"] == 1
    assert not s["prompts"]


def test_card_space_resolve_choice_prompt(engine, temp_card):
    """A resolve fn may queue a mid-effect choice like any other space
    resolution -- placement stalls on the prompt and resumes once it's
    answered (same mechanism GUIDE.md documents for round_start/
    returning_home/harvest_start prompts)."""
    def resolve(state, player, inst, action, log):
        cards.prompt_choice(state, player, inst["id"], "Wood or clay?",
                            ["wood", "clay"])
        return {}

    def resolve_choice(state, player, inst, ctx):
        good = ctx["option"]
        player["resources"][good] += 3
        ctx["log"].append(f"{player['name']} takes 3 {good}")

    temp_card("test_choice_space", "Test Choice Space", "minor", "test",
              cost={}, card_space={"resolve": resolve},
              resolve_choice=resolve_choice)

    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    give_card(s, first, "test_choice_space")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "test_choice_space"}})
    other_pid = s["players"][other]["player_id"]
    s = engine.apply_action(
        s, other_pid,
        {"kind": "place", "space": "card:test_choice_space"}).new_state

    assert engine.get_waiting_for(s) == [other_pid]
    assert s["prompts"][0]["type"] == "choice"
    first_pid = s["players"][first]["player_id"]
    assert engine.get_valid_actions(s, first_pid) == []

    s = engine.apply_action(s, other_pid, {"kind": "choice", "index": 1}).new_state
    assert s["players"][other]["resources"]["clay"] == 3
    assert not s["prompts"]


def test_card_space_occupancy_blocks_and_resets(engine, temp_card):
    """A card_space occupied this round blocks a second placement (the
    standard occupied-space semantics -- no occupied_ok on this card),
    and resets to unoccupied next round like any other space."""
    def resolve(state, player, inst, action, log):
        return {"food": 1}

    temp_card("test_occupancy_space", "Test Occupancy Space", "minor",
              "test", cost={}, card_space={"resolve": resolve})

    s = make_state(engine, 2)
    first = s["current_player"]
    other = (first + 1) % 2
    give_card(s, first, "test_occupancy_space")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "test_occupancy_space"}})
    s = place(engine, s, {"kind": "place", "space": "card:test_occupancy_space"})
    space = next(sp for sp in s["action_spaces"]
                if sp["id"] == "card:test_occupancy_space")
    assert space["occupied_by"] == other

    first_pid = s["players"][first]["player_id"]
    s["current_player"] = first
    valid = engine.get_valid_actions(s, first_pid)
    assert not any(a["kind"] == "place" and a["space"] == "card:test_occupancy_space"
                  for a in valid)
    with pytest.raises(ValueError):
        engine.apply_action(
            s, first_pid,
            {"kind": "place", "space": "card:test_occupancy_space"})

    while s["round"] == 1:
        pid = current_pid(engine, s)
        act = next(a for a in engine.get_valid_actions(s, pid)
                  if a["kind"] == "place" and a["space"] in SAFE_SPACES)
        s = place(engine, s, {"kind": "place", "space": act["space"]})

    space = next(sp for sp in s["action_spaces"]
                if sp["id"] == "card:test_occupancy_space")
    assert space["occupied_by"] is None
    assert space["extra_occupants"] == []


# ── Board geometry (engine phase 10) ─────────────────────────────────

def reveal_all_rounds(engine, state):
    """Advance state["revealed"]/state["action_spaces"] straight through
    all 14 rounds without playing them out (same shortcut the
    round_start-hook tests above use: call the engine's own
    _start_round directly). Good enough for geometry tests, which only
    care that every round space exists with the right id, not that a
    full game was played."""
    while state["round"] < 14:
        engine._start_round(state, [])
    return state


def test_space_positions_no_duplicates(engine):
    """Every action space on the board (permanent + all 14 revealed
    round spaces) has a position, and no two spaces share one, for
    every player count."""
    for n in (1, 2, 3, 4):
        s = make_state(engine, n, seed=100 + n)
        reveal_all_rounds(engine, s)
        seen = {}
        for space in s["action_spaces"]:
            pos = cards.space_position(s, space["id"])
            assert pos is not None, f"{n}p: {space['id']} has no position"
            assert pos not in seen, (
                f"{n}p: {space['id']} and {seen.get(pos)} share position {pos}")
            seen[pos] = space["id"]
        assert len(seen) == len(s["action_spaces"])


def test_fishing_has_three_neighbors_4p(engine):
    """D144 Water Worker's own text: Fishing has exactly three
    orthogonally adjacent action spaces. Ground-truth check that the
    derived layout (state.py's SPACE_POSITIONS/ROUND_SLOTS) matches the
    printed board once every round space exists."""
    s = make_state(engine, 4, seed=7)
    reveal_all_rounds(engine, s)
    neighbors = cards.adjacent_spaces(s, "fishing")
    # Day Laborer and Reed Bank are always neighbors (same board every
    # game); the third is whichever stage-1 card landed on round 4 --
    # column 2 is the only round-space column tall enough to reach
    # Fishing's row (see decks/GUIDE.md's "Board geometry" section).
    assert set(neighbors) == {"day_laborer", "reed_bank", s["revealed"][3]}
    assert len(neighbors) == 3


def test_adjacent_spaces_only_existing():
    """adjacent_spaces never returns a round slot that hasn't been
    revealed yet -- it's simply absent from state["action_spaces"]."""
    engine = AgricolaEngine()
    s = make_state(engine, 2)
    assert s["round"] == 1 and len(s["revealed"]) == 1
    # Reed Bank's round-space neighbor (round 3, same column) doesn't
    # exist yet -- only its three permanent neighbors (Lessons, Clay
    # Pit, Fishing) do.
    assert set(cards.adjacent_spaces(s, "reed_bank")) == \
        {"lessons", "clay_pit", "fishing"}

    engine._start_round(s, [])  # round 2
    engine._start_round(s, [])  # round 3 -- reveals round 3's card
    assert set(cards.adjacent_spaces(s, "reed_bank")) == \
        {"lessons", "clay_pit", "fishing", s["revealed"][2]}


def test_card_space_has_no_position_and_no_adjacency(engine, temp_card):
    """A card_space ("card:<cid>") sits beside the board, not on it: no
    position, and never adjacent to anything (nor is anything adjacent
    to it)."""
    temp_card("test_geo_card_space", "Test Card Space", "minor", "test",
              cost={}, card_space={"resolve": lambda state, player, inst,
                                    action, log: {}})
    s = make_state(engine, 2)
    first = s["current_player"]
    give_card(s, first, "test_geo_card_space")
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": "test_geo_card_space"}})

    assert cards.space_position(s, "card:test_geo_card_space") is None
    assert cards.adjacent_spaces(s, "card:test_geo_card_space") == []
    # And no real space considers the card_space one of its neighbors.
    assert "card:test_geo_card_space" not in cards.adjacent_spaces(s, "meeting_place")
    assert "card:test_geo_card_space" not in cards.adjacent_spaces(s, "farm_expansion")


def test_spaces_adjacent_symmetric(engine):
    s = make_state(engine, 2)
    pairs = [("farm_expansion", "meeting_place"), ("grain_seeds", "forest"),
             ("day_laborer", "fishing"), ("farm_expansion", "day_laborer")]
    for a, b in pairs:
        assert cards.spaces_adjacent(s, a, b) == cards.spaces_adjacent(s, b, a)
    assert cards.spaces_adjacent(s, "farm_expansion", "meeting_place") is True
    assert cards.spaces_adjacent(s, "farm_expansion", "day_laborer") is False


def test_extra_adjacency_grove_farm_expansion(engine):
    """The Appendix's documented pair ("The Grove is adjacent to both
    Farm Expansion and Meeting Place") is restored via EXTRA_ADJACENCY
    at 3p and 4p; the override doesn't leak into other player counts."""
    for n in (3, 4):
        s = make_state(engine, n)
        assert cards.spaces_adjacent(s, "grove", "farm_expansion") is True
        assert cards.spaces_adjacent(s, "farm_expansion", "grove") is True
        assert cards.spaces_adjacent(s, "grove", "meeting_place") is True
        assert "grove" in cards.adjacent_spaces(s, "farm_expansion")
    s2 = make_state(engine, 2)  # no grove on the 2p board at all
    assert "grove" not in cards.adjacent_spaces(s2, "farm_expansion")
    assert sorted(cards.adjacent_spaces(s2, "farm_expansion")) == \
        ["meeting_place"]


def test_left_neighbor_of_round_spaces(engine):
    """B120 Sweep's recipe: left_neighbor(state, state["revealed"][-1])."""
    s = make_state(engine, 2)
    assert cards.left_neighbor(s, s["revealed"][-1]) == "forest"
    engine._start_round(s, [])  # round 2
    assert cards.left_neighbor(s, s["revealed"][-1]) == "clay_pit"
    engine._start_round(s, [])  # round 3
    assert cards.left_neighbor(s, s["revealed"][-1]) == "reed_bank"
    engine._start_round(s, [])  # round 4
    assert cards.left_neighbor(s, s["revealed"][-1]) == "fishing"
    engine._start_round(s, [])  # round 5 -- left neighbor is round 1
    assert cards.left_neighbor(s, s["revealed"][-1]) == s["revealed"][0]


def test_legworker_style_hook_adjacent_occupancy(engine, temp_card):
    """A C117 Legworker-style card: using a space adjacent to another
    space occupied by the SAME player's own person grants 1 wood;
    using a space with no such adjacent occupant does not."""
    def hook(state, player, inst, ctx):
        if ctx["actor"] != player["index"]:
            return
        for sid in cards.adjacent_spaces(state, ctx["space_id"]):
            space = next(sp for sp in state["action_spaces"] if sp["id"] == sid)
            occupants = ([space["occupied_by"]]
                        if space["occupied_by"] is not None else []) \
                + space.get("extra_occupants", [])
            if player["index"] in occupants:
                cards.add_goods(ctx["extra"], {"wood": 1})
                return

    cid = "test_legworker"
    temp_card(cid, "Test Legworker", "occupation", "test",
              hooks={"space_used": hook})

    s = make_state(engine, 2)
    first = s["current_player"]
    other = 1 - first
    put_in_play(s, first, cid)

    # Round 1: first places on Grain Seeds, then (after other's turn)
    # on Forest -- orthogonally adjacent (state.py's SPACE_POSITIONS:
    # (0, 2) and (1, 2)) and occupied by first's own person -> +1 wood.
    # (Both spaces take a bare placement -- no build cost to worry
    # about, unlike Farm Expansion/Meeting Place.)
    s = place(engine, s, {"kind": "place", "space": "grain_seeds"})
    s = place(engine, s, {"kind": "place", "space": "clay_pit"})  # other, safe
    wood_before = s["players"][first]["resources"]["wood"]
    s = place(engine, s, {"kind": "place", "space": "forest"})
    # Forest's own accumulation reward (3 wood, 2p) plus the hook's +1.
    assert s["players"][first]["resources"]["wood"] == wood_before + 3 + 1

    while s["round"] == 1:
        pid = current_pid(engine, s)
        act = next(a for a in engine.get_valid_actions(s, pid)
                  if a["kind"] == "place" and a["space"] in SAFE_SPACES)
        s = place(engine, s, {"kind": "place", "space": act["space"]})

    # Round 2: first places on Grain Seeds, then Day Laborer -- NOT
    # adjacent ((0, 2) vs (0, 5)) -> no bonus.
    s = place(engine, s, {"kind": "place", "space": "grain_seeds"})
    s = place(engine, s, {"kind": "place", "space": "reed_bank"})  # other, safe
    wood_before = s["players"][first]["resources"]["wood"]
    s = place(engine, s, {"kind": "place", "space": "day_laborer"})
    assert s["players"][first]["resources"]["wood"] == wood_before


# ── Turn structure (engine phase 11) ─────────────────────────────────

def test_skip_turn_full_flow(engine, temp_card):
    """D053 Tea House-style: skip a placement for a gain, keep the
    deferred person's capacity, and get revisited later in rotation."""
    def skip_gain(state, player, inst):
        if inst["data"].get("used_round") == state["round"]:
            return None
        if player["people_placed"] != 1:
            return None
        return {"food": 1}

    def after_skip(state, player, inst, log):
        inst["data"]["used_round"] = state["round"]

    cid = "test_tea_house"
    temp_card(cid, "Test Tea House", "minor", cost={}, text="test",
              skip_turn=skip_gain, after_skip=after_skip)

    s = make_state(engine, 2)
    first = s["current_player"]
    other = 1 - first
    put_in_play(s, first, cid)

    s = place(engine, s, {"kind": "place", "space": "grain_seeds"})  # first
    assert s["current_player"] == other
    s = place(engine, s, {"kind": "place", "space": "day_laborer"})  # other
    assert s["current_player"] == first
    assert s["players"][first]["people_placed"] == 1

    pid = s["players"][first]["player_id"]
    actions = engine.get_valid_actions(s, pid)
    skip_action = next((a for a in actions if a["kind"] == "skip"), None)
    assert skip_action == {"kind": "skip", "card": cid, "gain": {"food": 1}}

    food_before = s["players"][first]["resources"]["food"]
    s = engine.apply_action(s, pid, {"kind": "skip", "card": cid}).new_state
    assert s["players"][first]["resources"]["food"] == food_before + 1
    # The deferred placement is not consumed.
    assert s["players"][first]["people_placed"] == 1
    # Rotation moves on to the other player instead of stalling.
    assert s["current_player"] == other

    s = place(engine, s, {"kind": "place", "space": "forest"})  # other
    # first is revisited (still has capacity) to place their deferred
    # 2nd person now -- and the skip is no longer offered this round.
    assert s["current_player"] == first
    actions = engine.get_valid_actions(s, pid)
    assert not any(a["kind"] == "skip" for a in actions)
    s = place(engine, s, {"kind": "place", "space": "clay_pit"})
    # first's deferred 2nd person did place -- this was everyone's last
    # placement this round, so the round rolled straight into round 2.
    assert s["round"] == 2


def test_skip_turn_not_offered_once_all_others_done(engine, temp_card):
    """The engine-level guard: skipping when no OTHER player still has
    an unplaced person would be a no-op turn, so it's not offered."""
    temp_card("test_tea_house_others", "Test Tea House", "minor", cost={},
              text="test",
              skip_turn=lambda state, player, inst: {"food": 1})

    s = make_state(engine, 2)
    first = s["current_player"]
    other = 1 - first
    put_in_play(s, other, "test_tea_house_others")

    s = place(engine, s, {"kind": "place", "space": "grain_seeds"})  # first
    s = place(engine, s, {"kind": "place", "space": "day_laborer"})  # other
    s = place(engine, s, {"kind": "place", "space": "forest"})  # first, done
    assert s["players"][first]["people_placed"] == 2
    assert s["current_player"] == other

    actions = engine.get_valid_actions(s, s["players"][other]["player_id"])
    assert not any(a["kind"] == "skip" for a in actions)


def test_skip_turn_not_offered_when_card_declines(engine, temp_card):
    """A `skip_turn` fn returning None (its own condition not met) means
    no skip action is offered, full stop."""
    temp_card("test_tea_house_none", "Test Tea House", "minor", cost={},
              text="test",
              skip_turn=lambda state, player, inst: None)

    s = make_state(engine, 2)
    first = s["current_player"]
    put_in_play(s, first, "test_tea_house_none")

    actions = engine.get_valid_actions(s, s["players"][first]["player_id"])
    assert not any(a["kind"] == "skip" for a in actions)


def test_first_placer_override_full_round_order(engine, temp_card):
    """I260 Taster recipe (documented in decks/GUIDE.md's "Turn
    structure" section): a round_start hook queues a choice; accepting
    pays the starting player 1 food and places the accepting (non-
    starting) player first via _pending_work_start + _resume_from --
    after their single placement, rotation resumes from the TRUE
    starting player in normal order."""
    s = make_state(engine, 2)
    starting = s["starting_player"]
    owner_idx = 1 - starting
    cid = "test_taster"

    def round_start_hook(state, player, inst, ctx):
        if player["index"] == owner_idx and state["starting_player"] != owner_idx:
            cards.prompt_choice(state, player, inst["id"],
                                "Pay 1 food to place first?", ["yes", "no"])

    def resolve(state, player, inst, ctx):
        if ctx["option"] != "yes":
            return
        starting_p = state["players"][state["starting_player"]]
        player["resources"]["food"] -= 1
        starting_p["resources"]["food"] += 1
        ctx["log"].append(f"{player['name']} pays 1 food to place first")
        state["_pending_work_start"] = player["index"]
        state["_resume_from"] = state["starting_player"]

    temp_card(cid, "Test Taster", "occupation", "test",
              hooks={"round_start": round_start_hook}, resolve_choice=resolve)
    put_in_play(s, owner_idx, cid)
    give(s, owner_idx, food=5)

    owner_food_before = s["players"][owner_idx]["resources"]["food"]
    starting_food_before = s["players"][starting]["resources"]["food"]

    log = []
    engine._start_round(s, log)  # round 2

    assert s["prompts"][0]["type"] == "choice"
    owner_pid = s["players"][owner_idx]["player_id"]
    s = engine.apply_action(s, owner_pid, {"kind": "choice", "index": 0}).new_state

    assert s["players"][owner_idx]["resources"]["food"] == owner_food_before - 1
    assert s["players"][starting]["resources"]["food"] == starting_food_before + 1
    assert s["current_player"] == owner_idx
    assert "_resume_from" in s  # not consumed until the placement resolves

    s = place(engine, s, {"kind": "place", "space": "grain_seeds"})  # owner, 1st
    # Rotation resumed from the TRUE starting player, not owner+1.
    assert s["current_player"] == starting
    assert "_resume_from" not in s

    s = place(engine, s, {"kind": "place", "space": "day_laborer"})  # starting
    assert s["current_player"] == owner_idx  # owner's remaining person
    s = place(engine, s, {"kind": "place", "space": "forest"})  # owner, 2nd
    assert s["current_player"] == starting
    s = place(engine, s, {"kind": "place", "space": "clay_pit"})  # starting
    assert s["round"] == 3


def test_resume_from_consumed_exactly_once(engine):
    """`_resume_from` is a one-shot fallback: `_advance_work` consumes
    it on the first call that reaches that branch and never re-reads a
    stale value on a later call within the same round."""
    s = make_state(engine, 3)
    log = []
    target = (s["current_player"] + 2) % 3
    s["_resume_from"] = target
    engine._advance_work(s, log)
    assert s["current_player"] == target
    assert "_resume_from" not in s

    prev = s["current_player"]
    s["players"][prev]["people_placed"] += 1  # pretend they just placed
    engine._advance_work(s, log)
    # Falls through to normal rotation (current_player + 1), not a
    # stale/re-read _resume_from.
    assert s["current_player"] == (prev + 1) % 3


def test_resume_from_does_not_leak_into_next_round(engine, temp_card):
    """A `_resume_from` consumed mid-round must not still be present (or
    accidentally re-triggered) once the next round starts."""
    s = make_state(engine, 2)
    starting = s["starting_player"]
    owner_idx = 1 - starting
    cid = "test_taster_leak"

    def round_start_hook(state, player, inst, ctx):
        if player["index"] == owner_idx and state["round"] == 2:
            state["_pending_work_start"] = owner_idx
            state["_resume_from"] = state["starting_player"]

    temp_card(cid, "Test Taster Leak", "occupation", "test",
              hooks={"round_start": round_start_hook})
    put_in_play(s, owner_idx, cid)

    log = []
    engine._start_round(s, log)  # round 2: sets _pending_work_start/_resume_from
    assert "_resume_from" in s

    # Play out round 2 entirely.
    while s["round"] == 2:
        pid = current_pid(engine, s)
        act = next(a for a in engine.get_valid_actions(s, pid)
                  if a["kind"] == "place" and a["space"] in SAFE_SPACES)
        s = place(engine, s, {"kind": "place", "space": act["space"]})

    assert "_resume_from" not in s
    assert "_pending_work_start" not in s


def test_placement_lockout_forfeits_with_sensible_log(engine, temp_card):
    """I71 Holiday House recipe: `placement_blocked` makes a player
    forfeit their placements for the round (with a log line distinct
    from the generic "no usable space" one), without offering them a
    skip either, while other players and feeding are unaffected."""
    def blocked(state, player, inst):
        return state["round"] == 2

    cid = "test_holiday_house"
    temp_card(cid, "Test Holiday House", "minor", cost={}, text="test",
              placement_blocked=blocked)

    s = make_state(engine, 2)
    put_in_play(s, 0, cid)
    other = 1

    # Fast-forward straight into round 2's work phase.
    s["phase"] = "work"
    for p in s["players"]:
        p["people_placed"] = p["people_total"]
    log = []
    engine._end_work_phase(s, log)
    assert s["round"] == 2
    assert s["phase"] == "work"

    # The blocked player already forfeited during round setup (before
    # anyone placed), with a lockout-specific log line -- not the
    # generic "no usable space" one.
    assert s["players"][0]["people_placed"] == s["players"][0]["people_total"]
    assert any("cannot place any people this round and forfeits" in line
              for line in log)
    assert not any("cannot use any action space and forfeits" in line
                  for line in log)

    # The blocked player is offered neither a placement nor a skip.
    assert engine._placement_actions(s, 0) == []
    assert engine._skip_actions(s, 0) == []

    # Feeding math is untouched by the lockout.
    assert engine._food_needed(s, s["players"][0]) == \
        engine._food_needed(s, s["players"][other])

    # The other player places normally for the rest of round 2.
    assert s["current_player"] == other
    while s["round"] == 2:
        pid = current_pid(engine, s)
        act = next(a for a in engine.get_valid_actions(s, pid)
                  if a["kind"] == "place" and a["space"] in SAFE_SPACES)
        s = place(engine, s, {"kind": "place", "space": act["space"]})


# ── Hand and deck (engine phase 12) ───────────────────────────────────
# state["occupation_draw"]/["minor_draw"] persist whatever's left of the
# shuffled decks after the opening deal (cards.deal_hands); ["occupation_
# discard"]/["minor_discard"] start empty. See cards.draw_minors/
# draw_occupations/discard_hand_minors/discard_hand_occupations and
# decks/GUIDE.md's "Hand and deck" section.

def test_initial_state_draw_piles_cover_full_deck(engine):
    """Every dealt hand plus its draw pile must reconstruct the full
    implemented deck for this player count/decks, with no overlap or
    duplicates -- and the discard piles start empty."""
    s = make_state(engine, 3)
    decks = s["decks"]
    full_occs = set(cards.deck_for("occupation", 3, decks))
    full_minors = set(cards.deck_for("minor", 3, decks))

    hand_occs = [cid for p in s["players"] for cid in p["hand_occupations"]]
    hand_minors = [cid for p in s["players"] for cid in p["hand_minors"]]
    all_occs = hand_occs + s["occupation_draw"]
    all_minors = hand_minors + s["minor_draw"]

    assert len(all_occs) == len(set(all_occs))
    assert len(all_minors) == len(set(all_minors))
    assert set(all_occs) == full_occs
    assert set(all_minors) == full_minors
    assert s["occupation_discard"] == []
    assert s["minor_discard"] == []


def test_draw_minors_fewer_when_short_and_zero_when_empty(engine):
    s = make_state(engine, 2)
    p = s["players"][0]
    log = []
    pile_before = list(s["minor_draw"])
    n = min(3, len(pile_before))
    drawn = cards.draw_minors(s, p, n, log)
    assert drawn == pile_before[:n]
    assert p["hand_minors"][-n:] == drawn
    assert s["minor_draw"] == pile_before[n:]

    # Asking for more than the pile holds draws only what's left.
    remaining = len(s["minor_draw"])
    drawn2 = cards.draw_minors(s, p, remaining + 50, log)
    assert len(drawn2) == remaining
    assert s["minor_draw"] == []

    # An empty pile yields nothing -- no reshuffle-when-empty.
    drawn3 = cards.draw_minors(s, p, 5, log)
    assert drawn3 == []
    assert s["minor_draw"] == []


def test_discard_hand_minors_moves_everything(engine):
    s = make_state(engine, 2)
    p = s["players"][0]
    log = []
    hand_before = list(p["hand_minors"])
    discarded = cards.discard_hand_minors(s, p, log)
    assert discarded == hand_before
    assert p["hand_minors"] == []
    assert s["minor_discard"] == hand_before


def test_draw_and_discard_occupations_are_symmetric(engine):
    """K125 only needs the minor twins, but the occupation helpers are
    cheap and genuinely symmetric -- exercise them directly."""
    s = make_state(engine, 2)
    p = s["players"][0]
    log = []
    hand_before = list(p["hand_occupations"])
    discarded = cards.discard_hand_occupations(s, p, log)
    assert discarded == hand_before
    assert p["hand_occupations"] == []
    assert s["occupation_discard"] == hand_before

    pile_before = list(s["occupation_draw"])
    drawn = cards.draw_occupations(s, p, 4, log)
    assert drawn == pile_before[:4]
    assert p["hand_occupations"] == drawn
    assert s["occupation_draw"] == pile_before[4:]


def test_broom_style_discard_and_redraw_flow(engine, temp_card):
    """K125 Broom recipe: discard your whole remaining minor hand, then
    draw 7 new ones from the persistent draw pile. Hand size ends up
    correct (or fewer, if the pile is short) and the piles stay
    consistent (nothing duplicated, nothing lost)."""
    def broom_play(state, player, inst, ctx):
        cards.discard_hand_minors(state, player, ctx["log"])
        cards.draw_minors(state, player, 7, ctx["log"])

    cid = "test_broom"
    temp_card(cid, "Test Broom", "minor", "test", hooks={"play": broom_play})

    s = make_state(engine, 2)
    first = s["current_player"]
    give_card(s, first, cid)
    old_hand = [c for c in s["players"][first]["hand_minors"] if c != cid]
    draw_pile_before = list(s["minor_draw"])

    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": cid}})
    p = s["players"][first]
    # Broom itself joined play (not traveling); the old hand is gone,
    # replaced by freshly drawn cards.
    assert cid not in p["hand_minors"]
    assert any(i["id"] == cid for i in p["minors"])
    assert not any(c in p["hand_minors"] for c in old_hand)
    expected_draw = min(7, len(draw_pile_before))
    assert len(p["hand_minors"]) == expected_draw
    assert p["hand_minors"] == draw_pile_before[:expected_draw]
    assert s["minor_discard"] == old_hand
    assert s["minor_draw"] == draw_pile_before[expected_draw:]


def test_view_hides_draw_and_discard_pile_contents(engine):
    """Nobody -- not even the current player -- can see the shuffled
    draw pile's order, or (per this pass's chosen contract) the discard
    pile's contents; both views only get a count, same treatment as an
    opponent's hidden hand."""
    s = make_state(engine, 2)
    p0, p1 = s["players"][0]["player_id"], s["players"][1]["player_id"]
    s["minor_discard"] = [s["minor_draw"].pop(), s["minor_draw"].pop()]

    for pid in (p0, p1):
        view = engine.get_player_view(s, pid)
        assert view["occupation_draw"] == len(s["occupation_draw"])
        assert view["minor_draw"] == len(s["minor_draw"])
        assert view["occupation_discard"] == len(s["occupation_discard"])
        assert view["minor_discard"] == len(s["minor_discard"]) == 2

    spectator = engine.get_spectator_view(s)
    assert spectator["minor_draw"] == len(s["minor_draw"])
    assert spectator["minor_discard"] == 2


def test_view_hides_stage_deck_order(engine):
    """The future round-card order is hidden info: views carry only a
    count for state["deck"]; "revealed" stays the public record."""
    s = make_state(engine, 2)
    for pid in (s["players"][0]["player_id"], s["players"][1]["player_id"]):
        view = engine.get_player_view(s, pid)
        assert view["deck"] == len(s["deck"])
        assert view["revealed"] == s["revealed"]
    assert engine.get_spectator_view(s)["deck"] == len(s["deck"])


def test_draw_discard_helpers_tolerate_missing_state_keys(engine):
    """Persisted-state compatibility: an old save made before this phase
    has no draw/discard keys at all. Every helper must use .get()/
    .setdefault() rather than crash on the bare KeyError."""
    s = make_state(engine, 2)
    p = s["players"][0]
    for key in ("occupation_draw", "minor_draw",
               "occupation_discard", "minor_discard"):
        del s[key]

    log = []
    assert cards.draw_minors(s, p, 3, log) == []
    assert cards.draw_occupations(s, p, 3, log) == []
    hand_minors_before = list(p["hand_minors"])
    hand_occs_before = list(p["hand_occupations"])
    assert cards.discard_hand_minors(s, p, log) == hand_minors_before
    assert cards.discard_hand_occupations(s, p, log) == hand_occs_before
    assert s["minor_discard"] == hand_minors_before
    assert s["occupation_discard"] == hand_occs_before

    # get_player_view must not crash either, even with the keys absent.
    view = engine.get_player_view(s, p["player_id"])
    assert "minor_draw" not in view or view["minor_draw"] == 0


# ── Hand reactions (`hand_react`, engine phase 12) ────────────────────
# E173 Chief's Daughter-style: a card still IN HAND reacts to another
# card being played. Spec key `hand_react={"event", "fn"}`; `fn(state,
# hand_player, ctx)` gets no `inst` (the card isn't in play yet). Wired
# only to occupation_played/minor_played (sub_actions._fire_broadcast).
# A "yes" answer plays the card from hand via a `from_hand=True` prompt
# (cards.prompt_choice) whose resolve_choice runs with inst=None and
# must play the card itself.

def _register_chiefs_daughter(temp_card, trigger_cid="test_chief",
                              daughter_cid="test_daughter"):
    def daughter_hand_react(state, hand_player, ctx):
        if ctx["card_id"] != trigger_cid:
            return
        cards.prompt_choice(
            state, hand_player, daughter_cid,
            "Play Test Daughter now at no cost?", ["yes", "no"],
            from_hand=True)

    def daughter_resolve(state, player, inst, ctx):
        assert inst is None  # the from_hand contract
        if ctx["option"] == "yes":
            sub_actions.play_occupation(state, player, daughter_cid,
                                        ctx["log"], cost_override="free")
        else:
            ctx["log"].append(
                f"{player['name']} declines to play Test Daughter")

    temp_card(trigger_cid, "Test Chief", "occupation", "test")
    temp_card(daughter_cid, "Test Daughter", "occupation", "test",
              hand_react={"event": "occupation_played",
                         "fn": daughter_hand_react},
              resolve_choice=daughter_resolve)


def test_hand_react_accept_plays_card_from_hand_at_no_cost(engine, temp_card):
    _register_chiefs_daughter(temp_card)
    s = make_state(engine, 2)
    first = s["current_player"]
    other = 1 - first
    give_card(s, first, "test_chief")
    give_card(s, other, "test_daughter")
    other_food_before = s["players"][other]["resources"]["food"]

    s = place(engine, s, {"kind": "place", "space": "lessons",
                          "card": "test_chief"})
    other_pid = s["players"][other]["player_id"]
    assert engine.get_waiting_for(s) == [other_pid]
    prompt = s["prompts"][0]
    assert prompt["type"] == "choice" and prompt["from_hand"] is True
    assert prompt["card"] == "test_daughter"

    s = engine.apply_action(s, other_pid, {"kind": "choice", "index": 0}
                            ).new_state  # "yes"
    p = s["players"][other]
    assert "test_daughter" not in p["hand_occupations"]
    assert any(i["id"] == "test_daughter" for i in p["occupations"])
    assert p["resources"]["food"] == other_food_before  # free
    assert not s["prompts"]


def test_hand_react_decline_leaves_card_in_hand(engine, temp_card):
    _register_chiefs_daughter(temp_card)
    s = make_state(engine, 2)
    first = s["current_player"]
    other = 1 - first
    give_card(s, first, "test_chief")
    give_card(s, other, "test_daughter")

    s = place(engine, s, {"kind": "place", "space": "lessons",
                          "card": "test_chief"})
    other_pid = s["players"][other]["player_id"]
    s = engine.apply_action(s, other_pid, {"kind": "choice", "index": 1}
                            ).new_state  # "no"
    p = s["players"][other]
    assert "test_daughter" in p["hand_occupations"]
    assert not any(i["id"] == "test_daughter" for i in p["occupations"])
    assert not s["prompts"]


def test_hand_react_ignoring_card_id_never_prompts(engine, temp_card):
    """A hand_react fn that checks ctx["card_id"] against a card that
    was NOT the one played never queues a prompt."""
    def indifferent_react(state, hand_player, ctx):
        if ctx["card_id"] != "some_other_card_entirely":
            return
        cards.prompt_choice(state, hand_player, "test_bystander",
                            "Play now?", ["yes", "no"], from_hand=True)

    temp_card("test_trigger", "Test Trigger", "occupation", "test")
    temp_card("test_bystander", "Test Bystander", "occupation", "test",
              hand_react={"event": "occupation_played",
                         "fn": indifferent_react})

    s = make_state(engine, 2)
    first = s["current_player"]
    other = 1 - first
    give_card(s, first, "test_trigger")
    give_card(s, other, "test_bystander")

    s = place(engine, s, {"kind": "place", "space": "lessons",
                          "card": "test_trigger"})
    assert not s["prompts"]
    assert "test_bystander" in s["players"][other]["hand_occupations"]
    # Rotation moved straight on to the other player's real turn.
    assert s["current_player"] == other


def test_hand_react_reactive_play_does_not_loop(engine, temp_card):
    """The reactive play itself fires occupation_played again (removed
    from hand before the broadcast, per sub_actions.play_occupation's
    existing ordering) -- this must not re-trigger the SAME card's own
    hand_react (it's no longer in anyone's hand) or hang. A second,
    unrelated bystander card confirms the second broadcast still reaches
    other hands normally."""
    _register_chiefs_daughter(temp_card)
    seen = []

    def bystander_react(state, hand_player, ctx):
        seen.append(ctx["card_id"])

    temp_card("test_bystander2", "Test Bystander 2", "occupation", "test",
              hand_react={"event": "occupation_played", "fn": bystander_react})

    s = make_state(engine, 2)
    first = s["current_player"]
    other = 1 - first
    give_card(s, first, "test_chief")
    give_card(s, other, "test_daughter")
    give_card(s, other, "test_bystander2")

    s = place(engine, s, {"kind": "place", "space": "lessons",
                          "card": "test_chief"})
    other_pid = s["players"][other]["player_id"]
    s = engine.apply_action(s, other_pid, {"kind": "choice", "index": 0}
                            ).new_state  # "yes"
    assert not s["prompts"]
    # Both broadcasts (test_chief, then test_daughter) reached the
    # bystander, in order, and the process terminated.
    assert seen == ["test_chief", "test_daughter"]


# ── B023 Final Scenario recipe (engine phase 12 assessment) ───────────
# Assessment (see decks/GUIDE.md and CARDS.md item 18 for the full
# writeup): expressible TODAY with existing card_space + sub_actions
# plumbing, no new engine change, because this engine's stage 6 has
# exactly one card (farm_redevelopment) -- state["deck"][13] is
# deterministic, not randomly revealed, so a card_space `resolve` fn can
# safely mirror farm_redevelopment's own sub_actions calls directly.

def test_b023_style_recipe_owner_only_until_round_14(engine, temp_card):
    def resolve(state, player, inst, action, log):
        sub_actions.renovate(state, player, log,
                             free_stable_cell=action.get("stable"))
        if action.get("fences"):
            sub_actions.build_fences(state, player, action["fences"], log)
        return {}

    def usable(state, player, inst):
        return state["round"] < 14 and sub_actions.renovation_possible(state, player)

    cid = "test_final_scenario"
    temp_card(cid, "Test Final Scenario", "minor", "test",
              card_space={"owner_only": True, "usable": usable,
                         "resolve": resolve})

    s = make_state(engine, 2)
    owner = s["current_player"]
    other = 1 - owner
    give_card(s, owner, cid)
    give(s, owner, clay=5, reed=5)
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": cid}})
    # It's `other`'s turn now; force it back to `owner` so the rest of
    # this test can exercise the card_space directly (both players still
    # have a second placement available this round).
    s["current_player"] = owner

    sid = f"card:{cid}"
    # The other player can never place there (owner_only).
    space = next(sp for sp in s["action_spaces"] if sp["id"] == sid)
    assert not engine._card_space_usable(s, s["players"][other], space)
    assert engine._card_space_usable(s, s["players"][owner], space)

    s = engine.apply_action(
        s, s["players"][owner]["player_id"],
        {"kind": "place", "space": sid}).new_state
    assert s["players"][owner]["house_type"] in ("clay", "stone")

    # Gate closes once round 14 starts -- the real farm_redevelopment
    # space (id == the actual stage-6 card) takes over from here.
    s["round"] = 14
    space = next(sp for sp in s["action_spaces"] if sp["id"] == sid)
    assert not engine._card_space_usable(s, s["players"][owner], space)


# ── Engine phase 13: field/fence/grid extensions ──────────────────────
# Multi-stack card fields (K105 Acreage, FR089 Landscape Gardener), the
# fence-token mechanism (B030 Wood Palisades), and the FR001 Abandoned
# Willow "remove a field" recipe -- see decks/GUIDE.md's "Field stacks"
# and "Fence tokens" sections, and CARDS.md item 19 for the full
# per-card assessment (C069 and FR059 remain gated; see there for why).
# None of the motivating compendium cards are registered by this pass --
# temp_card-only tests exercise each mechanism, matching phases 8-12's
# precedent.

def test_multi_stack_field_sow_and_harvest_independently(engine, temp_card):
    """K105-style: 2 independent grain stacks on one card field, each
    sown and harvested separately."""
    cid = "test_acreage"
    temp_card(cid, "Test Acreage", "minor", "test",
              field={"crops": ("grain",), "stacks": 2})
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    inst = put_in_play(s, first, cid)
    assert inst["stacks"] == [None, None]
    give(s, first, grain=2)
    sub_actions.sow(s, p, [{"card": cid, "crop": "grain", "stack": 0}], [])
    assert inst["stacks"][0] == {"type": "grain", "count": 3}
    assert inst["stacks"][1] is None
    # can_sow/empty_fields still see the second, still-open stack.
    assert sub_actions.can_sow(p)
    _cells, card_targets = sub_actions.empty_fields(p)
    assert inst in card_targets
    sub_actions.sow(s, p, [{"card": cid, "crop": "grain", "stack": 1}], [])
    assert inst["stacks"][1] == {"type": "grain", "count": 3}
    _cells, card_targets = sub_actions.empty_fields(p)
    assert inst not in card_targets
    # Harvest yields 2 grain (one per stack), decrementing each stack
    # independently of the other.
    grain_before = p["resources"]["grain"]
    engine._start_harvest(s, [])
    assert p["resources"]["grain"] == grain_before + 2
    assert inst["stacks"][0]["count"] == 2
    assert inst["stacks"][1]["count"] == 2


def test_multi_stack_field_can_sow_both_in_one_call(engine, temp_card):
    """Both stacks may also be addressed in a single sow() call (two
    separate sow_items entries naming the same card, different stacks)."""
    cid = "test_acreage_batch"
    temp_card(cid, "Test Acreage Batch", "minor", "test",
              field={"crops": ("grain",), "stacks": 2})
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    inst = put_in_play(s, first, cid)
    give(s, first, grain=2)
    sub_actions.sow(s, p, [{"card": cid, "crop": "grain", "stack": 0},
                          {"card": cid, "crop": "grain", "stack": 1}], [])
    assert inst["stacks"][0] == {"type": "grain", "count": 3}
    assert inst["stacks"][1] == {"type": "grain", "count": 3}


def test_multi_stack_field_sow_same_stack_twice_rejected(engine, temp_card):
    cid = "test_acreage_dup"
    temp_card(cid, "Test Acreage Dup", "minor", "test",
              field={"crops": ("grain",), "stacks": 2})
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, cid)
    give(s, first, grain=2)
    with pytest.raises(ValueError):
        sub_actions.sow(s, p, [{"card": cid, "crop": "grain", "stack": 0},
                              {"card": cid, "crop": "grain", "stack": 0}], [])


def test_multi_stack_field_invalid_stack_index_rejected(engine, temp_card):
    cid = "test_acreage_bad_idx"
    temp_card(cid, "Test Acreage Bad Idx", "minor", "test",
              field={"crops": ("grain",), "stacks": 2})
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, cid)
    give(s, first, grain=1)
    with pytest.raises(ValueError):
        sub_actions.sow(s, p, [{"card": cid, "crop": "grain", "stack": 2}], [])
    with pytest.raises(ValueError):
        sub_actions.sow(s, p, [{"card": cid, "crop": "grain", "stack": -1}], [])


def test_multi_stack_field_exhausted_stack_reverts_to_none_independently(
        engine, temp_card):
    """A 2-count vegetable stack empties (-> None) after 2 harvests,
    independent of its still-unsown sibling stack."""
    cid = "test_acreage_exhaust"
    temp_card(cid, "Test Acreage Exhaust", "minor", "test",
              field={"crops": ("vegetable",), "stacks": 2})
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    inst = put_in_play(s, first, cid)
    give(s, first, vegetable=1)
    sub_actions.sow(s, p, [{"card": cid, "crop": "vegetable", "stack": 0}], [])
    assert inst["stacks"][0]["count"] == 2
    engine._start_harvest(s, [])
    assert inst["stacks"][0]["count"] == 1
    assert inst["stacks"][1] is None
    engine._start_harvest(s, [])
    assert inst["stacks"][0] is None
    assert inst["stacks"][1] is None


def test_fr089_style_sow_as_two_fields_mixed_crops_and_scoring(engine, temp_card):
    """FR089 Landscape Gardener-style: 2 stacks, either crop, sown
    independently. Its crops count toward grain/vegetable scoring but
    never toward the 'fields' category -- true of every card field
    already (score_player's `fields` tally only ever counts farmyard
    cell tiles, never card_fields), which is exactly what FR089's own
    '(this card does not count as a field when scoring)' asks for, for
    free -- see CARDS.md item 19's FR089 scoring finding."""
    cid = "test_landscape_gardener"
    temp_card(cid, "Test Landscape Gardener", "minor", "test",
              field={"crops": ("grain", "vegetable"), "stacks": 2})
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    inst = put_in_play(s, first, cid)
    give(s, first, grain=1, vegetable=1)
    sub_actions.sow(s, p, [{"card": cid, "crop": "grain", "stack": 0},
                          {"card": cid, "crop": "vegetable", "stack": 1}], [])
    assert inst["stacks"][0] == {"type": "grain", "count": 3}
    assert inst["stacks"][1] == {"type": "vegetable", "count": 2}
    sc = score_player(p, s)
    assert sc["fields"] == table_score(
        "fields", sum(1 for c in p["cells"] if c["type"] == "field"))
    assert sc["grain"] == table_score("grain", 3)
    assert sc["vegetable"] == table_score("vegetable", 2)


def test_stacks_1_card_field_still_uses_legacy_crops_dict(engine):
    """Regression: a stacks=1 (default) card field, e.g. minor_beanfield,
    is completely unaffected by the multi-stack mechanism -- no
    inst['stacks'] key at all, sown/harvested via the flat inst['crops']
    slot exactly as before phase 13."""
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    inst = put_in_play(s, first, "minor_beanfield")
    assert "stacks" not in inst
    give(s, first, vegetable=1)
    sub_actions.sow(s, p, [{"card": "minor_beanfield", "crop": "vegetable"}], [])
    assert inst["crops"] == {"type": "vegetable", "count": 2}
    assert cards.field_stacks(inst) == [{"type": "vegetable", "count": 2}]
    engine._start_harvest(s, [])
    assert inst["crops"]["count"] == 1


def test_field_stack_helpers_read_old_save_shape_safely():
    """An old (pre-phase-13) save's stacks=1 card-field instance has
    'crops' but no 'stacks' key at all -- the field-stack helpers read/
    write it exactly like a fresh stacks=1 instance."""
    inst = {"id": "minor_beanfield", "crops": {"type": "vegetable", "count": 2},
            "data": {}}
    assert "stacks" not in inst
    assert cards.field_stacks(inst) == [{"type": "vegetable", "count": 2}]
    assert cards.get_field_stack(inst) == {"type": "vegetable", "count": 2}
    assert cards.open_field_stacks(inst) == []
    cards.set_field_stack(inst, 0, None)
    assert inst["crops"] is None
    assert cards.open_field_stacks(inst) == [0]


# ── FR001 Abandoned Willow: "remove an empty field" recipe ────────────

def test_fr001_style_remove_empty_field_recipe(engine, temp_card):
    """FR001 Abandoned Willow: 'immediately remove 1 empty field from
    your farmyard and receive 4 wood. (That space now counts as
    unused.)' No new engine plumbing is needed -- a play hook can just
    flip cell['type'] back to 'empty' via the existing params channel
    (Shifting Cultivation's plow-via-params precedent, run in reverse).
    See decks/GUIDE.md's 'FR001 recipe' section."""
    def hook(state, player, inst, ctx):
        cell = (ctx.get("params") or {}).get("cell")
        if not isinstance(cell, int) or not (0 <= cell < NUM_CELLS):
            raise ValueError("Choose an empty field to remove (params.cell)")
        c = player["cells"][cell]
        if c["type"] != "field" or c["crops"]:
            raise ValueError("You can only remove an empty field")
        c["type"] = "empty"
        player["resources"]["wood"] += 4
        ctx["log"].append(
            f"{player['name']} removes an empty field, gets 4 wood")

    cid = "test_fr001"
    temp_card(cid, "Test FR001", "minor", "test", hooks={"play": hook})
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    p["cells"][0]["type"] = "field"
    give_card(s, first, cid)
    wood_before = p["resources"]["wood"]
    s = place(engine, s, {"kind": "place", "space": "meeting_place",
                          "minor": {"card": cid, "params": {"cell": 0}}})
    p = s["players"][first]
    assert p["cells"][0]["type"] == "empty"
    assert p["resources"]["wood"] == wood_before + 4

    # Regression: plow targets, pasture validity, and scoring all still
    # behave normally on the reverted (now-unused) cell.
    assert 0 in plowable_cells(p)
    ok, err, pastures = validate_fence_layout(p, cell_edges(0))
    assert ok, err
    assert pastures == [[0]]
    sc = score_player(p, s)
    assert sc["fields"] == table_score(
        "fields", sum(1 for c in p["cells"] if c["type"] == "field"))
    assert sc["unused_spaces"] <= 0

    # Cannot remove a field that's already planted, or a non-field cell.
    p["cells"][1]["type"] = "field"
    p["cells"][1]["crops"] = {"type": "grain", "count": 3}
    with pytest.raises(ValueError):
        hook(s, p, None, {"params": {"cell": 1}, "log": []})
    with pytest.raises(ValueError):
        hook(s, p, None, {"params": {"cell": 2}, "log": []})  # empty, not a field


# ── B030 Wood Palisades: fence-token mechanism ─────────────────────────

def test_fence_tokens_mixed_layout_geometry_cost_and_max_fences_bypass(
        engine, temp_card):
    """A mixed layout (normal fences + 1 wood-token border edge)
    validates as ONE geometric layout, prices only the normal edges
    through the usual wood cost (the token is paid separately, by the
    card, at its own rate), and the wood-token edge is excluded from the
    15-fence cap -- 16 total edges (the whole farmyard's outer
    perimeter) would be rejected as all-normal fences, but succeeds with
    1 of them as a token."""
    cid = "test_wood_palisades"
    temp_card(cid, "Test Wood Palisades", "minor", "test",
              fence_token={"cost": {"wood": 2}})
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, cid)
    # Make every farmyard cell pasture-eligible (no starting rooms), so
    # the whole 16-edge outer perimeter is one legal pasture.
    for c in p["cells"]:
        c["type"] = "empty"
    border = [e for e in all_edge_keys() if is_border_edge(e)]
    assert len(border) == 16
    # All-normal, no tokens: 16 > MAX_FENCES(15), rejected outright.
    ok, err, _pastures = validate_fence_layout(p, border)
    assert not ok

    token_edge = border[0]
    give(s, first, wood=17)  # 15 normal fences + 2 for the 1 token
    sub_actions.build_fences(s, p, border, [], tokens=[token_edge])
    assert set(p["fences"]) == set(border)
    assert p["fence_tokens"] == {token_edge: cid}
    assert p["resources"]["wood"] == 0
    assert compute_pastures(p) == [sorted(range(NUM_CELLS))]
    # 16 real edges, but only 15 count against MAX_FENCES.
    assert len(p["fences"]) - len(p["fence_tokens"]) == 15


def test_fence_token_non_border_edge_rejected(engine, temp_card):
    cid = "test_wood_palisades_interior"
    temp_card(cid, "Test Wood Palisades Interior", "minor", "test",
              fence_token={"cost": {"wood": 2}})
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, cid)
    give(s, first, wood=10)
    fences = cell_edges(6)  # cell 6: all 4 edges are interior (row/col 1)
    interior_edge = next(e for e in fences if not is_border_edge(e))
    with pytest.raises(ValueError):
        sub_actions.build_fences(s, p, fences, [], tokens=[interior_edge])


def test_fence_token_requires_owning_card(engine):
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    give(s, first, wood=10)
    fences = cell_edges(4)
    border_edge = next(e for e in fences if is_border_edge(e))
    with pytest.raises(ValueError):
        sub_actions.build_fences(s, p, fences, [], tokens=[border_edge])


def test_fence_token_score_bonus_counts_tokens(engine, temp_card):
    cid = "test_wood_palisades_score"
    temp_card(cid, "Test Wood Palisades Score", "minor", "test",
              fence_token={"cost": {"wood": 2}},
              score_bonus=lambda s, p, i: sum(
                  1 for owner in p.get("fence_tokens", {}).values()
                  if owner == i["id"]))
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    put_in_play(s, first, cid)
    give(s, first, wood=10)
    fences = cell_edges(4)
    token_edge = next(e for e in fences if is_border_edge(e))
    sub_actions.build_fences(s, p, fences, [], tokens=[token_edge])
    sc = score_player(p, s)
    assert sc["bonus"] >= 1


def test_fence_tokens_missing_key_old_save_safe(engine):
    """An old save's player dict predates `fence_tokens` entirely --
    every read goes through `.get(..., {})`, so normal (tokenless)
    fence-building still works with the key absent."""
    s = make_state(engine, 2)
    first = s["current_player"]
    p = s["players"][first]
    del p["fence_tokens"]
    give(s, first, wood=4)
    assert sub_actions.can_build_fences(s, p)
    sub_actions.build_fences(s, p, cell_edges(4), [])
    assert compute_pastures(p) == [[4]]
