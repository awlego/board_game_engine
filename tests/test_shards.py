"""
Tests for the Shards of Creation game engine.

Ported 1:1 from overnightlemons.com server/games/shards/engine.test.js,
including replays of the rulebook's worked examples (pp. 7-8).
"""

import json
import math
from copy import deepcopy

import pytest

from server.game_engine import ActionResult
from server.shards.engine import (
    TRICKS_PER_ROUND,
    ShardsEngine,
    apply_action,
    award_options,
    create_game,
    effective_rank,
    legal_plays,
)
from server.shards.state import SHARDS, resonance_score, shard_set_score

BASE_SHARDS = ["autonomy", "ruin", "honor", "preservation"]

_UNSET = object()


def new_game(players=3, ids=None, names=None, shard_ids=None, seed=42):
    return create_game(
        player_ids=ids if ids is not None else [1, 2, 3][:players],
        player_names=names if names is not None else ["Azure", "Hoid", "Khriss"][:players],
        shard_ids=shard_ids if shard_ids is not None else BASE_SHARDS,
        seed=seed,
    )


def cards_of(state, shard, rank=_UNSET, ability=_UNSET, unability=_UNSET):
    return [
        c for c in state["cards"].values()
        if c["shard"] == shard
        and (rank is _UNSET or c["rank"] == rank)
        and (ability is _UNSET or c["ability"] == ability)
        and (unability is _UNSET or c["ability"] != unability)
    ]


def rig(state, trump=None, leader=None, hands=None, deck_top=None):
    """Force a specific table situation: hands, draw deck top, trump, leader."""
    s = deepcopy(state)
    if trump:
        s["trumpShard"] = trump
    if leader is not None:
        s["leader"] = leader
        s["turn"] = leader
    s["phase"] = "play"
    s["pending"] = None
    s["trick"] = []
    if hands:
        used = {c["id"] for h in hands for c in h}
        for i, p in enumerate(s["players"]):
            p["hand"] = [c["id"] for c in hands[i]]
        # rebuild draw deck from unused cards so conservation holds
        s["drawDeck"] = [cid for cid in s["cards"] if cid not in used]
        s["discard"] = []
        for p in s["players"]:
            p["scoringArea"] = []
    if deck_top:
        top_ids = [c["id"] for c in deck_top]
        s["drawDeck"] = [cid for cid in s["drawDeck"] if cid not in top_ids]
        s["drawDeck"] = top_ids + s["drawDeck"]
    return s


# --- scoring tables ---

def test_shard_set_scoring_is_triangular_capped_at_136():
    assert shard_set_score(0) == 0
    assert shard_set_score(1) == 1
    assert shard_set_score(2) == 3
    assert shard_set_score(3) == 6
    assert shard_set_score(8) == 36
    assert shard_set_score(16) == 136
    assert shard_set_score(20) == 136


def test_resonance_scoring_per_player_count():
    assert resonance_score(0, 3) == 0
    assert resonance_score(1, 3) == 5
    assert resonance_score(2, 3) == 10
    assert resonance_score(3, 3) == 20
    assert resonance_score(4, 3) == 40
    assert resonance_score(5, 3) == 80
    assert resonance_score(1, 4) == 8
    assert resonance_score(2, 4) == 16
    assert resonance_score(4, 4) == 64


# --- deck construction ---

def test_deck_composition_matches_reference_cards():
    s = new_game()
    assert len(s["cards"]) == 64
    # Honor: ranks 0-16 skipping 9, no abilities
    honor = cards_of(s, "honor")
    assert len(honor) == 16
    assert sorted(c["rank"] for c in honor) == [0, 1, 2, 3, 4, 5, 6, 7, 8, 10, 11, 12, 13, 14, 15, 16]
    assert all(c["ability"] is None for c in honor)
    # Ruin: four -1s with the scoring ability, 5-16 otherwise
    ruin = cards_of(s, "ruin")
    assert len([c for c in ruin if c["rank"] == -1]) == 4
    assert all(c["ability"] == "a1" for c in ruin if c["rank"] == -1)
    assert len([c for c in ruin if c["ability"] == "a2"]) == 4
    # Preservation: fifteen 8s + one 16, abilities on 15 of 16
    pres = cards_of(s, "preservation")
    assert len([c for c in pres if c["rank"] == 8]) == 15
    assert len([c for c in pres if c["rank"] == 16]) == 1
    assert len([c for c in pres if c["ability"] is not None]) == 15
    assert next(c for c in pres if c["rank"] == 16)["ability"] is None
    # Autonomy: 1-16, 4 + 4 abilities
    auto = cards_of(s, "autonomy")
    assert len([c for c in auto if c["ability"] == "a1"]) == 4
    assert len([c for c in auto if c["ability"] == "a2"]) == 4


def test_setup_deals_11_cards_each_and_reveals_a_trump():
    s = new_game()
    assert s["round"] == 1
    for p in s["players"]:
        assert len(p["hand"]) == 11
    assert s["trumpShard"] in BASE_SHARDS
    assert len(s["drawDeck"]) == 64 - 33
    if s["phase"] != "roundStartDiscard":
        assert s["turn"] == s["leader"]


def test_player_counts_pick_correct_shard_counts():
    with pytest.raises(ValueError):
        create_game(player_ids=[1], shard_ids=BASE_SHARDS, seed=1)
    with pytest.raises(ValueError):
        create_game(player_ids=[1, 2, 3, 4], player_names=["a", "b", "c", "d"],
                    shard_ids=BASE_SHARDS, seed=1)
    s4 = create_game(
        player_ids=[1, 2, 3, 4], player_names=["a", "b", "c", "d"],
        shard_ids=["autonomy", "ruin", "honor", "preservation", "cultivation"], seed=7,
    )
    assert len(s4["cards"]) == 80


def test_unknown_shards_are_rejected():
    with pytest.raises(ValueError, match="Unknown shard"):
        new_game(shard_ids=["autonomy", "ruin", "honor", "adonalsium"])


# --- following and trick resolution ---

def test_must_follow_the_lead_shard_when_able():
    s = new_game()
    s = rig(s, trump="honor", leader=0, hands=[
        cards_of(s, "ruin", rank=10) + cards_of(s, "honor", rank=5),
        cards_of(s, "ruin", rank=12) + cards_of(s, "honor", rank=6),
        cards_of(s, "honor", rank=7) + cards_of(s, "preservation", rank=16),
    ])
    s = apply_action(s, 0, {"type": "playCard", "cardId": cards_of(s, "ruin", rank=10)[0]["id"]})
    # player 1 holds a ruin, so honor is illegal
    legal = legal_plays(s, 1)
    assert all(s["cards"][cid]["shard"] == "ruin" for cid in legal)
    with pytest.raises(ValueError, match="follow"):
        apply_action(s, 1, {"type": "playCard", "cardId": cards_of(s, "honor", rank=6)[0]["id"]})
    # player 2 has no ruin: anything goes
    s = apply_action(s, 1, {"type": "playCard", "cardId": cards_of(s, "ruin", rank=12)[0]["id"]})
    assert len(legal_plays(s, 2)) == 2


def test_trump_beats_lead_highest_lead_wins_last_tied_card_wins():
    s = new_game()
    # no trump played: highest of lead shard wins (ruin 9-16 carry no abilities)
    t = rig(s, trump="autonomy", leader=0, hands=[
        [cards_of(s, "ruin", rank=10)[0], cards_of(s, "honor", rank=0)[0]],
        [cards_of(s, "ruin", rank=12)[0], cards_of(s, "honor", rank=1)[0]],
        [cards_of(s, "ruin", rank=9)[0], cards_of(s, "honor", rank=2)[0]],
    ])
    t = apply_action(t, 0, {"type": "playCard", "cardId": cards_of(s, "ruin", rank=10)[0]["id"]})
    t = apply_action(t, 1, {"type": "playCard", "cardId": cards_of(s, "ruin", rank=12)[0]["id"]})
    t = apply_action(t, 2, {"type": "playCard", "cardId": cards_of(s, "ruin", rank=9)[0]["id"]})
    assert t["phase"] == "award"
    assert t["turn"] == 1  # ruin 12 is strongest

    # a lone low trump beats high lead cards
    t = rig(s, trump="honor", leader=0, hands=[
        [cards_of(s, "ruin", rank=16)[0], cards_of(s, "ruin", rank=9)[0]],
        [cards_of(s, "honor", rank=1)[0], cards_of(s, "ruin", rank=11)[0]],
        [cards_of(s, "ruin", rank=15)[0], cards_of(s, "ruin", rank=10)[0]],
    ])
    t = apply_action(t, 0, {"type": "playCard", "cardId": cards_of(s, "ruin", rank=16)[0]["id"]})
    t = apply_action(t, 1, {"type": "playCard", "cardId": cards_of(s, "ruin", rank=11)[0]["id"]})  # must follow ruin
    t = apply_action(t, 2, {"type": "playCard", "cardId": cards_of(s, "ruin", rank=15)[0]["id"]})
    assert t["phase"] == "award"
    assert t["turn"] == 0  # ruin 16 wins: nobody could play the honor trump

    # now an actual trump lands: lowest honor beats every ruin
    t = rig(s, trump="honor", leader=0, hands=[
        [cards_of(s, "ruin", rank=16)[0], cards_of(s, "ruin", rank=9)[0]],
        [cards_of(s, "honor", rank=1)[0], cards_of(s, "honor", rank=2)[0]],
        [cards_of(s, "ruin", rank=15)[0], cards_of(s, "ruin", rank=10)[0]],
    ])
    t = apply_action(t, 0, {"type": "playCard", "cardId": cards_of(s, "ruin", rank=16)[0]["id"]})
    t = apply_action(t, 1, {"type": "playCard", "cardId": cards_of(s, "honor", rank=1)[0]["id"]})  # no ruin in hand
    t = apply_action(t, 2, {"type": "playCard", "cardId": cards_of(s, "ruin", rank=15)[0]["id"]})
    assert t["phase"] == "award"
    assert t["turn"] == 1  # honor 1 is trump and wins


def test_winner_drafts_one_trick_card_the_rest_are_discarded():
    s = new_game()
    ruin7 = cards_of(s, "ruin", rank=7, unability=None)
    s = rig(s, trump="autonomy", leader=0, hands=[
        [cards_of(s, "honor", rank=16)[0], cards_of(s, "ruin", rank=5)[0]],
        [cards_of(s, "honor", rank=2)[0], cards_of(s, "ruin", rank=6)[0]],
        [cards_of(s, "honor", rank=3)[0], ruin7[0] if ruin7 else cards_of(s, "ruin", rank=9)[0]],
    ])
    h16 = cards_of(s, "honor", rank=16)[0]["id"]
    h2 = cards_of(s, "honor", rank=2)[0]["id"]
    h3 = cards_of(s, "honor", rank=3)[0]["id"]
    s = apply_action(s, 0, {"type": "playCard", "cardId": h16})
    s = apply_action(s, 1, {"type": "playCard", "cardId": h2})
    s = apply_action(s, 2, {"type": "playCard", "cardId": h3})
    assert s["turn"] == 0
    with pytest.raises(ValueError, match="turn"):
        apply_action(s, 1, {"type": "chooseTrickCard", "cardId": h2})
    before = len(s["discard"])
    s = apply_action(s, 0, {"type": "chooseTrickCard", "cardId": h2})  # may take any trick card
    assert s["players"][0]["scoringArea"] == [h2]
    assert len(s["discard"]) == before + 2
    assert s["leader"] == 0
    assert s["trickNum"] == 2


# --- rulebook example replays ---

def test_rulebook_example_1_ruin_ability_trump_win_drafting():
    s = new_game()
    # 3p, Autonomy trump. Azure: Ruin 10 (no ability). Hoid: Ruin 7 (a2).
    # Khriss: no Ruin, plays Autonomy 1 (a1, declines). Top of deck: Honor 8.
    ruin10 = cards_of(s, "ruin", rank=10)[0]
    ruin7 = cards_of(s, "ruin", rank=7)[0]
    assert ruin7["ability"] == "a2"  # per example 1, the Ruin 7 has the reveal ability
    auto1 = cards_of(s, "autonomy", rank=1)[0]
    assert auto1["ability"] == "a1"  # per example 1, the Autonomy 1 has a "may" ability
    honor8 = cards_of(s, "honor", rank=8)[0]
    s = rig(
        s, trump="autonomy", leader=0,
        hands=[
            [ruin10, cards_of(s, "honor", rank=0)[0]],
            [ruin7, cards_of(s, "honor", rank=1)[0]],
            [auto1, cards_of(s, "honor", rank=2)[0]],
        ],
        deck_top=[honor8],
    )
    s = apply_action(s, 0, {"type": "playCard", "cardId": ruin10["id"]})
    s = apply_action(s, 1, {"type": "playCard", "cardId": ruin7["id"]})
    assert s["phase"] == "ability"
    assert s["pending"]["type"] == "ruin_reveal_subtract"
    assert s["pending"]["revealed"] == honor8["id"]
    # Hoid discards the revealed Honor 8 to subtract 8 from Azure's Ruin 10
    s = apply_action(s, 1, {"type": "abilityChoice", "targetCardId": ruin10["id"]})
    assert effective_rank(s, s["trick"][0]) == 2
    assert honor8["id"] in s["discard"]
    # Khriss has no Ruin, may play anything; plays Autonomy 1 and declines its ability
    s = apply_action(s, 2, {"type": "playCard", "cardId": auto1["id"]})
    if s["phase"] == "ability":
        s = apply_action(s, 2, {"type": "abilityChoice", "cardId": None})
    # Autonomy is trump, so Khriss wins despite rank 1
    assert s["phase"] == "award"
    assert s["turn"] == 2
    s = apply_action(s, 2, {"type": "chooseTrickCard", "cardId": ruin7["id"]})
    assert s["players"][2]["scoringArea"] == [ruin7["id"]]
    assert s["leader"] == 2


def test_rulebook_example_2_preservation_tie_last_tied_card_wins():
    s = new_game()
    p8s = cards_of(s, "preservation", rank=8)
    khriss8 = next(c for c in p8s if c["ability"] == "a1")
    azure8 = next(c for c in p8s if c["ability"] == "a2")
    hoid8 = [c for c in p8s if c["ability"] == "a1"][1]
    # order: Khriss(2) leads, Azure(0), Hoid(1)
    s = rig(
        s, trump="autonomy", leader=2,
        hands=[
            [azure8, cards_of(s, "honor", rank=0)[0]],
            [hoid8, cards_of(s, "honor", rank=1)[0]],
            [khriss8, cards_of(s, "honor", rank=2)[0]],
        ],
    )
    s = apply_action(s, 2, {"type": "playCard", "cardId": khriss8["id"]})
    assert s["pending"]["type"] == "preservation_discard"  # drew a card, must discard
    assert len(s["players"][2]["hand"]) == 2
    s = apply_action(s, 2, {"type": "abilityChoice", "cardId": s["players"][2]["hand"][0]})
    s = apply_action(s, 0, {"type": "playCard", "cardId": azure8["id"]})
    # a2 is passive: no scoring-area preservation cards -> still rank 8, no choice needed
    assert s["phase"] == "play"
    assert effective_rank(s, s["trick"][1]) == 8
    s = apply_action(s, 1, {"type": "playCard", "cardId": hoid8["id"]})
    s = apply_action(s, 1, {"type": "abilityChoice", "cardId": s["players"][1]["hand"][0]})
    # three-way tie at 8: Hoid played the last tied card and wins
    assert s["phase"] == "award"
    assert s["turn"] == 1


# --- abilities ---

def test_autonomy_a1_may_discard_an_autonomy_card_to_draw():
    s = new_game()
    a1card = cards_of(s, "autonomy", ability="a1")[0]
    spare = cards_of(s, "autonomy", ability=None)[0]
    s = rig(s, trump="honor", leader=0, hands=[
        [a1card, spare],
        [cards_of(s, "honor", rank=1)[0], cards_of(s, "honor", rank=2)[0]],
        [cards_of(s, "honor", rank=3)[0], cards_of(s, "honor", rank=4)[0]],
    ])
    s = apply_action(s, 0, {"type": "playCard", "cardId": a1card["id"]})
    assert s["pending"]["type"] == "autonomy_discard_draw"
    with pytest.raises(ValueError, match="Autonomy"):
        apply_action(s, 0, {"type": "abilityChoice", "cardId": "honor:1:1"})
    s2 = apply_action(s, 0, {"type": "abilityChoice", "cardId": spare["id"]})
    assert spare["id"] in s2["discard"]
    assert len(s2["players"][0]["hand"]) == 1  # discarded one, drew one
    s3 = apply_action(s, 0, {"type": "abilityChoice", "cardId": None})  # decline
    assert len(s3["players"][0]["hand"]) == 1
    assert spare["id"] not in s3["discard"]


def test_autonomy_a2_plus2_rank_per_autonomy_card_in_other_scoring_areas():
    s = new_game()
    a2card = cards_of(s, "autonomy", ability="a2")[0]
    s = rig(s, trump="honor", leader=0, hands=[
        [a2card, cards_of(s, "honor", rank=1)[0]],
        [cards_of(s, "honor", rank=2)[0], cards_of(s, "honor", rank=3)[0]],
        [cards_of(s, "honor", rank=4)[0], cards_of(s, "honor", rank=5)[0]],
    ])
    # seed other players' scoring areas with autonomy cards from the draw deck
    autos = [c["id"] for c in cards_of(s, "autonomy", ability=None)[1:4]]
    s["players"][1]["scoringArea"].extend([autos[0], autos[1]])
    s["players"][2]["scoringArea"].append(autos[2])
    s["drawDeck"] = [cid for cid in s["drawDeck"] if cid not in autos]
    s = apply_action(s, 0, {"type": "playCard", "cardId": a2card["id"]})
    assert effective_rank(s, s["trick"][0]) == a2card["rank"] + 6  # 3 autonomy cards elsewhere


def test_cultivation_a1_reveal_and_optionally_absorb_the_top_card():
    s = new_game(shard_ids=["cultivation", "ruin", "honor", "preservation"])
    c = cards_of(s, "cultivation", ability="a1")[0]
    top = cards_of(s, "honor", rank=10)[0]
    s = rig(
        s, trump="honor", leader=0,
        hands=[
            [c, cards_of(s, "honor", rank=1)[0]],
            [cards_of(s, "honor", rank=2)[0], cards_of(s, "honor", rank=3)[0]],
            [cards_of(s, "honor", rank=4)[0], cards_of(s, "honor", rank=5)[0]],
        ],
        deck_top=[top],
    )
    s = apply_action(s, 0, {"type": "playCard", "cardId": c["id"]})
    assert s["pending"]["type"] == "cultivation_reveal_add"
    took = apply_action(s, 0, {"type": "abilityChoice", "take": True})
    assert effective_rank(took, took["trick"][0]) == c["rank"] + 10
    assert top["id"] in took["discard"]
    declined = apply_action(s, 0, {"type": "abilityChoice", "take": False})
    assert declined["drawDeck"][0] == top["id"]  # put back on top
    assert effective_rank(declined, declined["trick"][0]) == c["rank"]


def test_cultivation_a2_discard_up_to_two_draw_that_many():
    s = new_game(shard_ids=["cultivation", "ruin", "honor", "preservation"])
    c = cards_of(s, "cultivation", ability="a2")[0]
    extra1 = cards_of(s, "honor", rank=1)[0]
    extra2 = cards_of(s, "honor", rank=2)[0]
    s = rig(s, trump="honor", leader=0, hands=[
        [c, extra1, extra2],
        [cards_of(s, "honor", rank=3)[0], cards_of(s, "honor", rank=4)[0], cards_of(s, "honor", rank=5)[0]],
        [cards_of(s, "honor", rank=6)[0], cards_of(s, "honor", rank=7)[0], cards_of(s, "honor", rank=8)[0]],
    ])
    s = apply_action(s, 0, {"type": "playCard", "cardId": c["id"]})
    assert s["pending"]["type"] == "cultivation_discard_draw"
    with pytest.raises(ValueError, match="Duplicate"):
        apply_action(s, 0, {"type": "abilityChoice", "cardIds": [extra1["id"], extra1["id"]]})
    s2 = apply_action(s, 0, {"type": "abilityChoice", "cardIds": [extra1["id"], extra2["id"]]})
    assert len(s2["players"][0]["hand"]) == 2  # discarded 2, drew 2
    assert extra1["id"] in s2["discard"] and extra2["id"] in s2["discard"]
    s3 = apply_action(s, 0, {"type": "abilityChoice", "cardIds": []})  # zero is allowed
    assert len(s3["players"][0]["hand"]) == 2


def test_rank_deltas_persist_within_the_trick_and_clear_on_transfer_discard():
    s = new_game()
    ruin_a2 = cards_of(s, "ruin", ability="a2")[0]
    victim = cards_of(s, "honor", rank=16)[0]
    top = cards_of(s, "honor", rank=10)[0]
    s = rig(
        s, trump="preservation", leader=0,
        hands=[
            [victim, cards_of(s, "honor", rank=1)[0]],
            [cards_of(s, "honor", rank=15)[0], cards_of(s, "honor", rank=2)[0]],
            [ruin_a2, cards_of(s, "preservation", rank=16)[0]],  # no honor: free to play ruin
        ],
        deck_top=[top],
    )
    s = apply_action(s, 0, {"type": "playCard", "cardId": victim["id"]})
    s = apply_action(s, 1, {"type": "playCard", "cardId": cards_of(s, "honor", rank=15)[0]["id"]})
    s = apply_action(s, 2, {"type": "playCard", "cardId": ruin_a2["id"]})
    s = apply_action(s, 2, {"type": "abilityChoice", "targetCardId": victim["id"]})
    # honor 16 dropped to 6; honor 15 now wins the trick
    assert s["phase"] == "award"
    assert s["turn"] == 1
    s = apply_action(s, 1, {"type": "chooseTrickCard", "cardId": victim["id"]})
    assert len(s["rankDeltas"]) == 0  # cleared on transfer + discard


# --- trump abilities ---

def test_autonomy_trump_cannot_lead_autonomy_with_other_shards_in_hand():
    s = new_game()
    auto = cards_of(s, "autonomy", ability=None)[0]
    honor1 = cards_of(s, "honor", rank=1)[0]
    s = rig(s, trump="autonomy", leader=0, hands=[
        [auto, honor1],
        [cards_of(s, "honor", rank=2)[0], cards_of(s, "honor", rank=3)[0]],
        [cards_of(s, "honor", rank=4)[0], cards_of(s, "honor", rank=5)[0]],
    ])
    assert auto["id"] not in legal_plays(s, 0)
    with pytest.raises(ValueError, match="legal"):
        apply_action(s, 0, {"type": "playCard", "cardId": auto["id"]})
    # all-autonomy hand may lead autonomy
    auto2 = cards_of(s, "autonomy", ability=None)[1]
    s["players"][0]["hand"] = [auto["id"], auto2["id"]]
    s["drawDeck"] = [cid for cid in s["drawDeck"] if cid != auto2["id"]]
    s["drawDeck"].append(honor1["id"])
    assert auto["id"] in legal_plays(s, 0)
    # and following with autonomy is always fine
    s2 = apply_action(s, 0, {"type": "playCard", "cardId": auto["id"]})
    assert len(s2["trick"]) == 1


def test_cultivation_trump_extra_deal_then_simultaneous_discard():
    # force cultivation to be round 1 trump by seed hunting
    s = None
    for seed in range(1, 200):
        t = create_game(
            player_ids=[1, 2], player_names=["A", "B"],
            shard_ids=["cultivation", "ruin", "honor", "preservation"], seed=seed,
        )
        if t["trumpShard"] == "cultivation":
            s = t
            break
    assert s, "found a seed with cultivation trump"
    assert s["phase"] == "roundStartDiscard"
    assert len(s["players"][0]["hand"]) == 14  # 11 + 3 in round 1
    with pytest.raises(ValueError, match="exactly 3"):
        apply_action(s, 0, {"type": "roundStartDiscard", "cardIds": s["players"][0]["hand"][:2]})
    s2 = apply_action(s, 0, {"type": "roundStartDiscard", "cardIds": s["players"][0]["hand"][:3]})
    assert s2["phase"] == "roundStartDiscard"  # waiting on player 2
    assert len(s2["players"][0]["hand"]) == 14  # not applied until everyone picks
    s2 = apply_action(s2, 1, {"type": "roundStartDiscard", "cardIds": s2["players"][1]["hand"][:3]})
    assert s2["phase"] == "play"
    assert len(s2["players"][0]["hand"]) == 11
    assert len(s2["players"][1]["hand"]) == 11
    assert len(s2["discard"]) == 6


# --- full-game simulation ---

def random_bot(state, rand):
    def pick(arr):
        return arr[math.floor(rand() * len(arr))]

    phase = state["phase"]
    if phase == "play":
        legal = legal_plays(state, state["turn"])
        return state["turn"], {"type": "playCard", "cardId": pick(legal)}
    if phase == "ability":
        p = state["pending"]
        ptype = p["type"]
        if ptype == "autonomy_discard_draw":
            return state["turn"], {"type": "abilityChoice", "cardId": pick(p["targets"]) if rand() < 0.5 else None}
        if ptype == "cultivation_discard_draw":
            hand = state["players"][p["player"]]["hand"]
            n = min(len(hand), math.floor(rand() * 3))
            return state["turn"], {"type": "abilityChoice", "cardIds": hand[:n]}
        if ptype == "preservation_discard":
            return state["turn"], {"type": "abilityChoice", "cardId": pick(state["players"][p["player"]]["hand"])}
        if ptype == "cultivation_reveal_add":
            return state["turn"], {"type": "abilityChoice", "take": rand() < 0.5}
        if ptype == "ruin_reveal_subtract":
            return state["turn"], {"type": "abilityChoice", "targetCardId": pick(p["targets"]) if p["targets"] and rand() < 0.7 else None}
        if ptype == "devotion_exchange":
            return state["turn"], {"type": "abilityChoice", "targetCardId": pick(p["targets"]) if rand() < 0.6 else None}
        if ptype == "dominion_discard_lowest":
            return state["turn"], {"type": "abilityChoice", "cardId": pick(p["targets"])}
        if ptype == "odium_discard_take":
            return state["turn"], {
                "type": "abilityChoice",
                "cardId": pick(state["players"][p["player"]]["hand"]),
                "targetPlayer": pick(p["victims"]),
            }
        raise AssertionError(f"bot: unknown pending {ptype}")
    if phase == "award":
        opts = award_options(state)
        all_opts = opts["played"] + opts["discard"]
        return state["turn"], {"type": "chooseTrickCard", "cardId": pick(all_opts)}
    if phase == "roundStartDiscard":
        waiting = [i for i in range(len(state["players"])) if not state["pending"]["selections"].get(str(i))]
        pl = waiting[0]
        return pl, {"type": "roundStartDiscard", "cardIds": state["players"][pl]["hand"][:state["pending"]["count"]]}
    if phase == "roundStartPlace":
        waiting = [i for i in range(len(state["players"])) if state["pending"]["selections"].get(str(i)) is None]
        pl = waiting[0]
        return pl, {"type": "roundStartPlace", "cardId": pick(state["players"][pl]["hand"])}
    if phase == "odiumSteal":
        return state["turn"], {"type": "odiumSteal", "cardId": pick(state["pending"]["targets"]) if rand() < 0.8 else None}
    raise AssertionError(f"bot: unexpected phase {phase}")


def make_lcg(seed):
    rng_state = seed & 0xFFFFFFFF

    def rand():
        nonlocal rng_state
        rng_state = (rng_state * 1664525 + 1013904223) & 0xFFFFFFFF
        return rng_state / 4294967296

    return rand


def check_conservation(state, total):
    n = len(state["drawDeck"]) + len(state["discard"]) + len(state["trick"])
    for p in state["players"]:
        n += len(p["hand"]) + len(p["scoringArea"])
    if state["pending"] and state["pending"].get("revealed"):
        n += 1
    assert n == total, "card conservation"


def test_random_full_games_complete_correctly_at_all_player_counts():
    configs = [
        {"players": 2, "shardIds": ["autonomy", "ruin", "honor", "preservation"]},
        {"players": 2, "shardIds": ["devotion", "dominion", "odium", "preservation"]},
        {"players": 3, "shardIds": ["autonomy", "cultivation", "honor", "ruin"]},
        {"players": 3, "shardIds": ["devotion", "dominion", "odium", "cultivation"]},
        {"players": 4, "shardIds": ["autonomy", "devotion", "dominion", "odium", "ruin"]},
        {"players": 4, "shardIds": ["cultivation", "devotion", "honor", "preservation", "odium"]},
    ]
    for cfg in configs:
        for seed in range(1, 13):
            ids = [1, 2, 3, 4][:cfg["players"]]
            s = create_game(
                player_ids=ids, player_names=[f"P{i}" for i in ids],
                shard_ids=cfg["shardIds"], seed=seed * 977,
            )
            total = len(cfg["shardIds"]) * 16
            rand = make_lcg(seed * 1013904223 + 1)
            steps = 0
            while s["phase"] != "gameOver":
                player, action = random_bot(s, rand)
                s = apply_action(s, player, action)
                check_conservation(s, total)
                steps += 1
                assert steps < 3000, "game terminates"
            assert s["round"] == 3
            # every player banked 10+ tricks' worth? No—only winners bank. But everyone
            # banked 3 end-of-round cards unless devotion; sanity: scoring areas hold
            # exactly (tricks played) + (3 or 0 end cards) cards in total.
            banked = sum(len(p["scoringArea"]) for p in s["players"])
            assert banked == 3 * TRICKS_PER_ROUND + 3 * s["playerCount"]
            assert all(isinstance(sc["total"], int) for sc in s["result"]["scores"])
            assert len(s["result"]["winners"]) >= 1
            # verify shard set scores match the aid
            for sc in s["result"]["scores"]:
                for sid in cfg["shardIds"]:
                    assert sc["shardPoints"][sid] == shard_set_score(sc["byShard"][sid])
                expected_sets = min(sc["byShard"][sid] for sid in cfg["shardIds"])
                assert sc["resonance"] == resonance_score(expected_sets, s["playerCount"])


# --- newly-verified shard behaviors ---

FULL_SHARDS = ["devotion", "dominion", "odium", "honor"]


def test_dominion_trump_winner_must_take_the_highest_rank_card():
    s = new_game(shard_ids=FULL_SHARDS)
    s = rig(s, trump="dominion", leader=0, hands=[
        [cards_of(s, "honor", rank=16)[0], cards_of(s, "honor", rank=0)[0]],
        [cards_of(s, "honor", rank=2)[0], cards_of(s, "honor", rank=3)[0]],
        [cards_of(s, "honor", rank=5)[0], cards_of(s, "honor", rank=6)[0]],
    ])
    h16 = cards_of(s, "honor", rank=16)[0]["id"]
    h2 = cards_of(s, "honor", rank=2)[0]["id"]
    s = apply_action(s, 0, {"type": "playCard", "cardId": h16})
    s = apply_action(s, 1, {"type": "playCard", "cardId": h2})
    s = apply_action(s, 2, {"type": "playCard", "cardId": cards_of(s, "honor", rank=5)[0]["id"]})
    assert s["phase"] == "award"
    assert award_options(s)["played"] == [h16]
    with pytest.raises(ValueError, match="trump"):
        apply_action(s, 0, {"type": "chooseTrickCard", "cardId": h2})
    s = apply_action(s, 0, {"type": "chooseTrickCard", "cardId": h16})
    assert s["players"][0]["scoringArea"] == [h16]


def test_ruin_trump_winner_must_take_the_lowest_rank_card():
    s = new_game()
    s = rig(s, trump="ruin", leader=0, hands=[
        [cards_of(s, "honor", rank=16)[0], cards_of(s, "honor", rank=0)[0]],
        [cards_of(s, "honor", rank=2)[0], cards_of(s, "honor", rank=3)[0]],
        [cards_of(s, "honor", rank=5)[0], cards_of(s, "honor", rank=6)[0]],
    ])
    h16 = cards_of(s, "honor", rank=16)[0]["id"]
    h2 = cards_of(s, "honor", rank=2)[0]["id"]
    s = apply_action(s, 0, {"type": "playCard", "cardId": h16})
    s = apply_action(s, 1, {"type": "playCard", "cardId": h2})
    s = apply_action(s, 2, {"type": "playCard", "cardId": cards_of(s, "honor", rank=5)[0]["id"]})
    assert award_options(s)["played"] == [h2]
    s = apply_action(s, 0, {"type": "chooseTrickCard", "cardId": h2})
    assert s["players"][0]["scoringArea"] == [h2]


def test_preservation_trump_winner_may_take_a_preservation_card_from_the_discard():
    s = new_game()
    s = rig(s, trump="preservation", leader=0, hands=[
        [cards_of(s, "honor", rank=16)[0], cards_of(s, "honor", rank=0)[0]],
        [cards_of(s, "honor", rank=2)[0], cards_of(s, "honor", rank=3)[0]],
        [cards_of(s, "honor", rank=5)[0], cards_of(s, "honor", rank=6)[0]],
    ])
    # seed the discard pile with a preservation card
    pres = cards_of(s, "preservation", rank=16)[0]["id"]
    s["drawDeck"] = [cid for cid in s["drawDeck"] if cid != pres]
    s["discard"].append(pres)
    h16 = cards_of(s, "honor", rank=16)[0]["id"]
    s = apply_action(s, 0, {"type": "playCard", "cardId": h16})
    s = apply_action(s, 1, {"type": "playCard", "cardId": cards_of(s, "honor", rank=2)[0]["id"]})
    s = apply_action(s, 2, {"type": "playCard", "cardId": cards_of(s, "honor", rank=5)[0]["id"]})
    opts = award_options(s)
    assert len(opts["played"]) == 3
    assert opts["discard"] == [pres]
    s = apply_action(s, 0, {"type": "chooseTrickCard", "cardId": pres})
    assert s["players"][0]["scoringArea"] == [pres]
    # every played card went to the discard instead
    assert h16 in s["discard"]
    assert len(s["discard"]) == 3


def test_devotion_trump_everyone_banks_a_card_at_round_start():
    s = None
    for seed in range(1, 400):
        t = create_game(
            player_ids=[1, 2], player_names=["A", "B"],
            shard_ids=["devotion", "honor", "ruin", "preservation"], seed=seed,
        )
        if t["trumpShard"] == "devotion":
            s = t
            break
    assert s, "found a devotion round-1 trump seed"
    assert s["phase"] == "roundStartPlace"
    with pytest.raises(ValueError, match="phase"):
        apply_action(s, 0, {"type": "playCard", "cardId": s["players"][0]["hand"][0]})
    s = apply_action(s, 0, {"type": "roundStartPlace", "cardId": s["players"][0]["hand"][0]})
    assert len(s["players"][0]["scoringArea"]) == 0  # simultaneous: applied when all have chosen
    s = apply_action(s, 1, {"type": "roundStartPlace", "cardId": s["players"][1]["hand"][0]})
    assert s["phase"] == "play"
    assert len(s["players"][0]["scoringArea"]) == 1
    assert len(s["players"][0]["hand"]) == 10


def test_odium_trump_strictly_fewest_player_may_steal_from_a_largest_scoring_area():
    s = new_game(shard_ids=FULL_SHARDS)
    s = rig(s, trump="odium", leader=0, hands=[
        [cards_of(s, "honor", rank=16)[0]],
        [cards_of(s, "honor", rank=2)[0]],
        [cards_of(s, "honor", rank=5)[0]],
    ])
    s["trickNum"] = TRICKS_PER_ROUND  # final trick of round 3
    s["round"] = 3
    # player 1 has a big scoring area, player 2 a middling one, player 0 nothing
    bank = [c["id"] for c in cards_of(s, "honor")[6:11]]
    s["players"][1]["scoringArea"] = bank[:3]
    s["players"][2]["scoringArea"] = bank[3:5]
    s["drawDeck"] = [cid for cid in s["drawDeck"] if cid not in bank]
    h16 = cards_of(s, "honor", rank=16)[0]["id"]
    # single-card hands: the rest of the trick auto-plays after the lead
    s = apply_action(s, 0, {"type": "playCard", "cardId": h16})
    assert s["phase"] == "award"
    s = apply_action(s, 0, {"type": "chooseTrickCard", "cardId": h16})
    # hands were emptied by the trick, so no last-card transfers:
    # p0 has 1, p1 has 3, p2 has 2 -> p0 strictly fewest, steals from p1
    assert s["phase"] == "odiumSteal"
    assert s["turn"] == 0
    assert sorted(s["pending"]["targets"]) == sorted(s["players"][1]["scoringArea"])
    stolen = s["pending"]["targets"][0]
    with pytest.raises(ValueError, match="most"):
        apply_action(s, 0, {"type": "odiumSteal", "cardId": s["players"][2]["scoringArea"][0]})
    s = apply_action(s, 0, {"type": "odiumSteal", "cardId": stolen})
    assert stolen in s["players"][0]["scoringArea"]
    assert len(s["players"][1]["scoringArea"]) == 2  # one card stolen
    assert s["phase"] == "gameOver"  # round 3 ended


def test_devotion_a2_exchange_with_a_scoring_area_card_and_activate_its_effect():
    s = new_game(shard_ids=FULL_SHARDS)
    dev = cards_of(s, "devotion", ability="a2")[0]
    banked = cards_of(s, "dominion", ability="a2")[0]  # draw-discard-lowest
    s = rig(s, trump="honor", leader=0, hands=[
        [dev, cards_of(s, "honor", rank=1)[0]],
        [cards_of(s, "honor", rank=2)[0], cards_of(s, "honor", rank=3)[0]],
        [cards_of(s, "honor", rank=5)[0], cards_of(s, "honor", rank=6)[0]],
    ])
    s["players"][0]["scoringArea"] = [banked["id"]]
    s["drawDeck"] = [cid for cid in s["drawDeck"] if cid != banked["id"]]
    # ensure the chained dominion draw pulls a high card, so honor 1 is the lowest
    h12 = cards_of(s, "honor", rank=12)[0]["id"]
    s["drawDeck"] = [h12] + [cid for cid in s["drawDeck"] if cid != h12]
    s = apply_action(s, 0, {"type": "playCard", "cardId": dev["id"]})
    assert s["pending"]["type"] == "devotion_exchange"
    s = apply_action(s, 0, {"type": "abilityChoice", "targetCardId": banked["id"]})
    # devotion went to the scoring area; the dominion card is now the played card
    assert s["players"][0]["scoringArea"] == [dev["id"]]
    assert s["trick"][0]["cardId"] == banked["id"]
    # and its own ability activated (draw a card, discard lowest: honor 1 leaves)
    h1 = cards_of(s, "honor", rank=1)[0]["id"]
    assert h1 in s["discard"] or (s["pending"] and s["pending"]["type"] == "dominion_discard_lowest")


def test_dominion_a2_draw_then_auto_discard_the_lowest_card():
    s = new_game(shard_ids=FULL_SHARDS)
    dom = cards_of(s, "dominion", ability="a2")[0]
    low = cards_of(s, "honor", rank=0)[0]
    top = cards_of(s, "honor", rank=14)[0]
    s = rig(
        s, trump="honor", leader=0,
        hands=[
            [dom, low, cards_of(s, "honor", rank=12)[0]],
            [cards_of(s, "honor", rank=2)[0], cards_of(s, "honor", rank=3)[0], cards_of(s, "honor", rank=4)[0]],
            [cards_of(s, "honor", rank=5)[0], cards_of(s, "honor", rank=6)[0], cards_of(s, "honor", rank=7)[0]],
        ],
        deck_top=[top],
    )
    s = apply_action(s, 0, {"type": "playCard", "cardId": dom["id"]})
    # drew honor 14; lowest is honor 0, unique -> auto-discarded, no pending
    assert s["phase"] == "play"
    assert low["id"] in s["discard"]
    assert top["id"] in s["players"][0]["hand"]
    assert len(s["players"][0]["hand"]) == 2


def test_odium_a1_discard_take_a_random_card_victim_draws():
    s = new_game(shard_ids=FULL_SHARDS)
    od = cards_of(s, "odium", ability="a1")[0]
    mine = cards_of(s, "honor", rank=1)[0]
    s = rig(s, trump="honor", leader=0, hands=[
        [od, mine],
        [cards_of(s, "honor", rank=2)[0], cards_of(s, "honor", rank=3)[0]],
        [cards_of(s, "honor", rank=5)[0], cards_of(s, "honor", rank=6)[0]],
    ])
    s = apply_action(s, 0, {"type": "playCard", "cardId": od["id"]})
    assert s["pending"]["type"] == "odium_discard_take"
    assert s["pending"]["victims"] == [1, 2]
    victim_before = list(s["players"][1]["hand"])
    s = apply_action(s, 0, {"type": "abilityChoice", "cardId": mine["id"], "targetPlayer": 1})
    assert mine["id"] in s["discard"]
    assert len(s["players"][0]["hand"]) == 1  # discarded 1, took 1
    assert len(s["players"][1]["hand"]) == 2  # lost 1, drew 1
    assert any(cid in s["players"][0]["hand"] for cid in victim_before)


def test_odium_a2_plus2_rank_per_distinct_shard_in_own_scoring_area():
    s = new_game(shard_ids=FULL_SHARDS)
    od = cards_of(s, "odium", ability="a2")[0]
    s = rig(s, trump="honor", leader=0, hands=[
        [od, cards_of(s, "honor", rank=1)[0]],
        [cards_of(s, "honor", rank=2)[0], cards_of(s, "honor", rank=3)[0]],
        [cards_of(s, "honor", rank=5)[0], cards_of(s, "honor", rank=6)[0]],
    ])
    bank = [
        cards_of(s, "honor", rank=12)[0]["id"],
        cards_of(s, "honor", rank=13)[0]["id"],
        cards_of(s, "dominion", rank=11)[0]["id"],
        cards_of(s, "devotion", rank=11)[0]["id"],
    ]
    s["players"][0]["scoringArea"] = bank
    s["drawDeck"] = [cid for cid in s["drawDeck"] if cid not in bank]
    s = apply_action(s, 0, {"type": "playCard", "cardId": od["id"]})
    # 3 distinct shards banked -> +6
    assert effective_rank(s, s["trick"][0]) == od["rank"] + 6


def test_scoring_devotion_wilds_and_dominion_converters_resolve_optimally():
    s = new_game(shard_ids=FULL_SHARDS)
    area = [c["id"] for c in (
        # 3 honor + 2 odium + a devotion wild + a dominion converter
        cards_of(s, "honor")[:3]
        + cards_of(s, "odium", ability=None)[:2]
        + [cards_of(s, "devotion", ability="a1")[0]]
        + [cards_of(s, "dominion", ability="a1")[0]]
    )]
    s["players"][0]["scoringArea"] = area
    s["players"][1]["scoringArea"] = []
    s["players"][2]["scoringArea"] = []
    s["round"] = 3
    s["phase"] = "play"
    # drive to game over via a bare finish: rig one last trick
    s["trickNum"] = TRICKS_PER_ROUND
    hands = [
        [cards_of(s, "honor", rank=14)[0]],
        [cards_of(s, "honor", rank=4)[0]],
        [cards_of(s, "honor", rank=5)[0]],
    ]
    for i, p in enumerate(s["players"]):
        p["hand"] = [c["id"] for c in hands[i]]
    s["trumpShard"] = "honor"
    s["leader"] = 0
    s["turn"] = 0
    hand_ids = {c["id"] for h in hands for c in h}
    s["drawDeck"] = [cid for cid in s["cards"] if cid not in area and cid not in hand_ids]
    s["discard"] = []
    h14 = hands[0][0]["id"]
    # single-card hands: the trick auto-completes after the lead
    s = apply_action(s, 0, {"type": "playCard", "cardId": h14})
    assert s["phase"] == "award"
    s = apply_action(s, 0, {"type": "chooseTrickCard", "cardId": h14})
    assert s["phase"] == "gameOver"
    sc = s["result"]["scores"][0]
    # area at scoring: 4 honor (incl. trick card), 2 odium, 1 devotion wild,
    # 1 dominion converter. The wild must leave Devotion (so no resonance is
    # possible) and the converter must fire. Optimal assignment: wild -> honor
    # (5), converter takes an odium (dominion 2, odium 1):
    # tri(5)+tri(1)+tri(0)+tri(2) = 15+1+0+3 = 19.
    assert sc["byShard"]["devotion"] == 0
    assert sc["byShard"]["honor"] == 5
    assert sc["byShard"]["dominion"] == 2
    assert sc["byShard"]["odium"] == 1
    assert sc["resonanceSets"] == 0
    assert sc["abilityPoints"] == 0
    assert sc["total"] == 19


def test_round_transitions_3_rounds_distinct_trumps_discard_reshuffled():
    s = create_game(
        player_ids=[1, 2], player_names=["A", "B"],
        shard_ids=["autonomy", "ruin", "honor", "preservation"], seed=5,
    )
    rand = make_lcg(99)
    trumps = [s["trumpShard"]]
    rnd = s["round"]
    while s["phase"] != "gameOver":
        player, action = random_bot(s, rand)
        s = apply_action(s, player, action)
        if s["round"] != rnd:
            rnd = s["round"]
            trumps.append(s["trumpShard"])
            for p in s["players"]:
                assert len(p["hand"]) >= 11  # 11 + any cultivation extras pre-discard
    assert len(trumps) == 3
    assert len(set(trumps)) == 3  # trump deck drawn without replacement


# --- GameEngine adapter ---

def test_adapter_initial_state_and_views():
    engine = ShardsEngine()
    state = engine.initial_state(["p_a", "p_b"], ["Alice", "Bob"])
    json.dumps(state)  # must stay JSON-serializable
    assert state["playerCount"] == 2
    assert [p["id"] for p in state["players"]] == ["p_a", "p_b"]

    view = engine.get_player_view(state, "p_a")
    json.dumps(view)
    assert view["your_player_id"] == "p_a"
    assert view["you"] == 0
    assert view["valid_actions"] == engine.get_valid_actions(state, "p_a")
    # own hand visible as card dicts; opponents only as counts
    assert all(isinstance(c, dict) for c in view["hand"])
    assert view["players"][1]["handCount"] == len(state["players"][1]["hand"])
    assert "hand" not in view["players"][1]
    assert "drawDeck" not in view

    spectator = engine.get_spectator_view(state)
    json.dumps(spectator)
    assert spectator["your_player_id"] is None
    assert spectator["valid_actions"] == []
    assert spectator["hand"] == []
    assert all(not p["isYou"] for p in spectator["players"])

    # a non-player gets no actions
    assert engine.get_valid_actions(state, "p_stranger") == []


def test_adapter_full_game_via_valid_actions():
    engine = ShardsEngine()
    # deterministic core state wrapped by the adapter
    state = create_game(
        player_ids=["p_a", "p_b", "p_c"], player_names=["Alice", "Bob", "Carol"],
        shard_ids=["cultivation", "devotion", "odium", "preservation"], seed=31337,
    )
    rand = make_lcg(2024)
    steps = 0
    game_over = False
    while not game_over:
        waiting = engine.get_waiting_for(state)
        assert waiting, f"nobody to act in phase {state['phase']}"
        pid = waiting[0]
        actions = engine.get_valid_actions(state, pid)
        assert actions, f"{pid} is waited on but has no actions in {state['phase']}"
        action = actions[math.floor(rand() * len(actions))]
        info = engine.get_phase_info(state)
        assert info["phase"] == state["phase"]
        assert isinstance(info["description"], str) and info["description"]
        result = engine.apply_action(state, pid, action)
        assert isinstance(result, ActionResult)
        assert result.log and all(isinstance(m, str) for m in result.log)
        state = result.new_state
        game_over = result.game_over
        steps += 1
        assert steps < 3000, "game terminates"
    assert state["phase"] == "gameOver"
    assert engine.get_waiting_for(state) == []
    info = engine.get_phase_info(state)
    assert info["phase"] == "gameOver"
    assert "Winner" in info["description"]

    with pytest.raises(ValueError):
        engine.apply_action(state, "p_stranger", {"type": "playCard", "cardId": "x"})
