"""Tests for the pre-game pick-and-pass card draft (server/agricola/draft.py)."""

import json
import random

import pytest

from server.agricola.engine import AgricolaEngine
from server.agricola import cards, draft

from tests.test_agricola import random_bot_action, bot_fallback


@pytest.fixture
def engine():
    return AgricolaEngine()


# Every implemented deck together comfortably covers deal 7 (or 10)
# packets for 4 players, so tests never hit deal_hands' auto-shrink
# unless they mean to.
ALL_DECKS = None  # filled lazily; implemented_decks() imports deck modules


def all_decks():
    global ALL_DECKS
    if ALL_DECKS is None:
        ALL_DECKS = cards.implemented_decks()
    return ALL_DECKS


def start_draft(engine, n=2, seed=1, **opts):
    """initial_state with the draft enabled (all decks, deterministic)."""
    random.seed(seed)
    ids = [f"p_{i}" for i in range(n)]
    names = [f"Bot{i}" for i in range(n)]
    options = {"decks": all_decks(), "draft_mode": "pick_and_pass", **opts}
    return engine.initial_state(ids, names, options), ids


def run_draft(engine, s, ids, rng, max_steps=200):
    """Random-bot the state through the rest of the draft."""
    steps = 0
    while s["phase"] == "draft":
        steps += 1
        assert steps < max_steps, "draft did not terminate"
        waiting = engine.get_waiting_for(s)
        assert waiting, "draft stalled with nobody to act"
        pid = rng.choice(waiting)
        action = random_bot_action(engine, s, pid, rng)
        s = engine.apply_action(s, pid, action).new_state
    return s


# ── Options / setup ──────────────────────────────────────────────────

def test_no_draft_by_default(engine):
    random.seed(1)
    s = engine.initial_state(["a", "b"], ["A", "B"])
    assert "draft" not in s
    assert s["phase"] == "work"
    assert s["round"] == 1


def test_resolve_options_clamping():
    assert draft.resolve_options(None) is None
    assert draft.resolve_options({"draft_mode": "nope"}) is None
    cfg = draft.resolve_options({"draft_mode": "pick_and_pass"})
    assert cfg == {"deal": 7, "keep": 7,
                   "directions": {"occupations": 1, "minors": -1}}
    cfg = draft.resolve_options({
        "draft_mode": "pick_and_pass", "draft_deal": 99, "draft_keep": 0,
        "draft_directions": "sideways"})
    assert cfg["deal"] == 14 and cfg["keep"] == 1
    assert cfg["directions"] == {"occupations": 1, "minors": -1}
    # keep clamps to deal; junk values fall back to defaults
    cfg = draft.resolve_options({
        "draft_mode": "pick_and_pass", "draft_deal": 5, "draft_keep": "9",
        "draft_directions": "right"})
    assert cfg["deal"] == 5 and cfg["keep"] == 5
    assert cfg["directions"] == {"occupations": -1, "minors": -1}
    cfg = draft.resolve_options({
        "draft_mode": "pick_and_pass", "draft_deal": "junk",
        "draft_directions": "left"})
    assert cfg["deal"] == 7
    assert cfg["directions"] == {"occupations": 1, "minors": 1}


def test_draft_setup(engine):
    s, ids = start_draft(engine, n=2)
    assert s["phase"] == "draft"
    assert s["round"] == 0
    assert s["revealed"] == []
    d = s["draft"]
    assert d["stage"] == "occupations"
    assert d["deal"] == 7 and d["keep"] == 7
    assert s["hand_size"] == 7
    for p in s["players"]:
        assert p["hand_occupations"] == [] and p["hand_minors"] == []
    for i in range(2):
        assert len(d["queues"][i]) == 1
        assert len(d["queues"][i][0]) == 7
        assert len(d["minor_packets"][i]) == 7
    assert engine.get_waiting_for(s) == ids
    acts = engine.get_valid_actions(s, ids[0])
    assert [a["card"] for a in acts] == d["queues"][0][0]
    assert all(a["kind"] == "draft_pick" for a in acts)
    # The whole state stays JSON-serializable (room persistence).
    json.dumps(s)


# ── Passing and pipelining ───────────────────────────────────────────

def test_pick_passes_left_and_pipelines(engine):
    s, ids = start_draft(engine, n=3)
    d = s["draft"]
    p0_packet = list(d["queues"][0][0])
    picked = p0_packet[0]

    s = engine.apply_action(s, ids[0], {"kind": "draft_pick",
                                        "card": picked}).new_state
    d = s["draft"]
    assert s["players"][0]["hand_occupations"] == [picked]
    assert d["picks_made"] == [1, 0, 0]
    # Occupations pass left (= next index): p0's packet lands behind
    # p1's own, p2 is untouched, p0 now waits empty-handed.
    assert d["queues"][0] == []
    assert len(d["queues"][1]) == 2
    assert d["queues"][1][1] == [c for c in p0_packet if c != picked]
    assert len(d["queues"][2]) == 1
    assert engine.get_waiting_for(s) == [ids[1], ids[2]]

    # Only the FRONT packet is offered.
    acts = engine.get_valid_actions(s, ids[1])
    assert [a["card"] for a in acts] == d["queues"][1][0]

    # p1 picks from their own packet, then immediately again from p0's
    # old packet -- pipelining, no waiting on p2.
    for _ in range(2):
        front = s["draft"]["queues"][1][0]
        s = engine.apply_action(s, ids[1], {"kind": "draft_pick",
                                            "card": front[0]}).new_state
    assert s["draft"]["picks_made"][1] == 2
    assert len(s["players"][1]["hand_occupations"]) == 2

    # Picking with an empty queue or a card not in the front packet fails.
    with pytest.raises(ValueError):
        engine.apply_action(s, ids[1], {"kind": "draft_pick",
                                        "card": picked})
    with pytest.raises(ValueError):
        engine.apply_action(s, ids[0], {"kind": "draft_pick",
                                        "card": "A001"})


def test_direction_options(engine):
    s, ids = start_draft(engine, n=3, seed=3, draft_directions="right")
    d = s["draft"]
    assert d["directions"] == {"occupations": -1, "minors": -1}
    picked = d["queues"][1][0][0]
    s = engine.apply_action(s, ids[1], {"kind": "draft_pick",
                                        "card": picked}).new_state
    # "right" = previous index: p1's packet lands behind p0's own.
    assert len(s["draft"]["queues"][0]) == 2


# ── Hidden information ───────────────────────────────────────────────

def test_view_redaction(engine):
    s, ids = start_draft(engine, n=2, draft_deal=5, draft_keep=3)
    view = engine.get_player_view(s, ids[0])
    dv = view["draft"]
    assert "queues" not in dv and "minor_packets" not in dv
    assert dv["your_packet"] == s["draft"]["queues"][0][0]
    assert dv["queue_counts"] == [1, 1]
    # Opponent hands stay counts, exactly as mid-game.
    assert view["players"][1]["hand_occupations"] == 0

    spec = engine.get_spectator_view(s)
    assert spec["draft"]["your_packet"] is None
    assert spec["draft"]["queue_counts"] == [1, 1]

    # Removed cards (deal > keep leftovers) hide behind a count.
    rng = random.Random(0)
    s = run_draft(engine, s, ids, rng)
    view = engine.get_player_view(s, ids[0])
    assert isinstance(view["removed_cards"], int)
    assert view["removed_cards"] > 0


def test_draft_log_never_names_cards(engine):
    s, ids = start_draft(engine, n=2)
    picked = s["draft"]["queues"][0][0][0]
    result = engine.apply_action(s, ids[0], {"kind": "draft_pick",
                                             "card": picked})
    name = cards.CARDS[picked]["name"]
    assert result.log, "draft picks should be logged"
    assert all(picked not in line and name not in line
               for line in result.log)


# ── Completion ───────────────────────────────────────────────────────

@pytest.mark.parametrize("n_players,seed", [(1, 11), (2, 12), (4, 13)])
def test_full_draft_completion(engine, n_players, seed):
    s, ids = start_draft(engine, n=n_players, seed=seed)
    occ_deck = set(cards.deck_for("occupation", n_players, all_decks()))
    minor_deck = set(cards.deck_for("minor", n_players, all_decks()))
    rng = random.Random(seed)

    while s["phase"] == "draft":
        waiting = engine.get_waiting_for(s)
        # With deal == keep the last card of a packet is auto-picked, so
        # nobody is ever offered a single-card "choice".
        for pid in waiting:
            assert len(engine.get_valid_actions(s, pid)) >= 2
        pid = rng.choice(waiting)
        s = engine.apply_action(s, pid, random_bot_action(
            engine, s, pid, rng)).new_state

    # Draft over: normal round 1 has begun.
    assert "draft" not in s
    assert s["phase"] == "work" and s["round"] == 1
    assert len(s["revealed"]) == 1

    # Every player drafted a full hand; nothing duplicated or lost.
    hands_occ, hands_minor = [], []
    for p in s["players"]:
        assert len(p["hand_occupations"]) == 7
        assert len(p["hand_minors"]) == 7
        hands_occ += p["hand_occupations"]
        hands_minor += p["hand_minors"]
    assert len(set(hands_occ)) == len(hands_occ)
    assert len(set(hands_minor)) == len(hands_minor)
    assert s.get("removed_cards", []) == []  # deal == keep removes nothing
    assert set(hands_occ) | set(s["occupation_draw"]) == occ_deck
    assert set(hands_minor) | set(s["minor_draw"]) == minor_deck


def test_deal_greater_than_keep_removes_leftovers(engine):
    n = 3
    s, ids = start_draft(engine, n=n, seed=21, draft_deal=5, draft_keep=3)
    assert s["draft"]["deal"] == 5 and s["draft"]["keep"] == 3
    assert s["hand_size"] == 3
    rng = random.Random(21)
    s = run_draft(engine, s, ids, rng)

    assert s["phase"] == "work" and s["round"] == 1
    for p in s["players"]:
        assert len(p["hand_occupations"]) == 3
        assert len(p["hand_minors"]) == 3
    # Each packet retires with deal - keep = 2 cards, per stage.
    removed = s["removed_cards"]
    assert len(removed) == n * 2 * 2
    in_hands = {c for p in s["players"]
                for c in p["hand_occupations"] + p["hand_minors"]}
    assert not in_hands & set(removed)
    assert not set(removed) & set(s["occupation_draw"])
    assert not set(removed) & set(s["minor_draw"])


def test_solo_draft(engine):
    """One player passes packets to themselves: pick keep of deal."""
    s, ids = start_draft(engine, n=1, seed=31, draft_deal=6, draft_keep=4)
    rng = random.Random(31)
    s = run_draft(engine, s, ids, rng)
    assert s["phase"] == "work" and s["round"] == 1
    assert len(s["players"][0]["hand_occupations"]) == 4
    assert len(s["players"][0]["hand_minors"]) == 4
    assert len(s["removed_cards"]) == 4  # (6-4) per stage


def test_one_card_deal_resolves_instantly(engine):
    """deal == keep == 1 is all forced picks: the draft finishes inside
    initial_state and the game starts normally."""
    s, ids = start_draft(engine, n=2, seed=41, draft_deal=1, draft_keep=1)
    assert "draft" not in s
    assert s["phase"] == "work" and s["round"] == 1
    for p in s["players"]:
        assert len(p["hand_occupations"]) == 1
        assert len(p["hand_minors"]) == 1


# ── Full-game integration ────────────────────────────────────────────

@pytest.mark.parametrize("n_players,seed", [(2, 51), (3, 52)])
def test_random_full_game_with_draft(engine, n_players, seed):
    """The draft hands off into a full random game exactly like a dealt
    one (mirrors test_random_full_game)."""
    rng = random.Random(seed)
    random.seed(seed)
    ids = [f"p_{i}" for i in range(n_players)]
    names = [f"Bot{i}" for i in range(n_players)]
    s = engine.initial_state(ids, names, {
        "decks": all_decks(), "draft_mode": "pick_and_pass",
        "draft_deal": 8, "draft_keep": 7})
    assert s["phase"] == "draft"

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
