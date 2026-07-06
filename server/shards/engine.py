"""
Shards of Creation — server-authoritative rules engine.

Pure state machine ported 1:1 from overnightlemons.com
server/games/shards/engine.js: create_game() builds a state, apply_action()
validates and applies one player action and then auto-advances through any
non-choice steps (dealing, trump reveal, forced plays, round end, scoring).
No I/O in the core. State keys stay camelCase to match the JS engine exactly
(cross-language parity traces in tests/parity/ depend on it).

Rulebook: server/shards/rules/rules.pdf. Section references in comments
point at that document.

The ShardsEngine class at the bottom adapts the pure core to the
GameEngine ABC used by this server.
"""

import math
import random as _random
import time
from copy import deepcopy
from itertools import combinations

from server.game_engine import ActionResult, GameEngine
from server.shards.state import (
    SHARDS,
    build_shard_cards,
    next_rand,
    resonance_score,
    shard_set_score,
    shards_for_player_count,
    shuffle,
)

TRICKS_PER_ROUND = 10
ROUNDS = 3
HAND_SIZE = 11


# --- game creation ---

def create_game(player_ids, player_names=None, shard_ids=None, seed=None):
    player_count = len(player_ids)
    if player_count < 2 or player_count > 4:
        raise ValueError('Shards of Creation is for 2-4 players.')
    needed = shards_for_player_count(player_count)
    if not shard_ids:
        pool = [s["id"] for s in SHARDS.values() if s["complete"]]
        tmp = {"rng": (seed if seed is not None else int(time.time() * 1000)) & 0xFFFFFFFF}
        shard_ids = shuffle(tmp, list(pool))[:needed]
    if len(shard_ids) != needed:
        raise ValueError(f'A {player_count}-player game uses exactly {needed} Shards.')
    for sid in shard_ids:
        if sid not in SHARDS:
            raise ValueError(f'Unknown shard: {sid}')
        if not SHARDS[sid]["complete"]:
            raise ValueError(f'Shard {sid} has incomplete card data and cannot be played yet.')

    state = {
        "rng": (seed if seed is not None else int(time.time() * 1000)) & 0xFFFFFFFF,
        "shardIds": shard_ids,
        "playerCount": player_count,
        "players": [
            {
                "id": pid,
                "name": (player_names[i] if player_names is not None and i < len(player_names) else str(pid)),
                "hand": [],
                "scoringArea": [],
            }
            for i, pid in enumerate(player_ids)
        ],
        "cards": {},  # cardId -> {id, shard, rank, ability}
        "drawDeck": [],
        "discard": [],
        "trumpDeck": [],
        "usedTrumps": [],
        "trumpShard": None,
        "round": 0,
        "trickNum": 0,
        "leader": 0,  # player index leading current trick
        "turn": None,  # player index expected to act, None in simultaneous phases
        "phase": "setup",  # roundStartDiscard | play | ability | award | gameOver
        "trick": [],  # [{player, cardId}]
        "rankDeltas": {},  # cardId -> accumulated rank change (appendix p. 11: persists until scored/discarded)
        "pending": None,  # ability resolution context
        "lastTrickWinner": None,
        "log": [],
        "result": None,
    }

    for sid in shard_ids:
        for card in build_shard_cards(SHARDS[sid]):
            state["cards"][card["id"]] = card
            state["drawDeck"].append(card["id"])
    shuffle(state, state["drawDeck"])
    state["trumpDeck"] = shuffle(state, list(shard_ids))
    state["leader"] = math.floor(next_rand(state) * player_count)  # p. 5: first player chosen randomly

    start_round(state)
    return state


def log(state, msg, data=None):
    entry = {"t": len(state["log"]), "msg": msg}
    if data:
        entry.update(data)
    state["log"].append(entry)


# --- round lifecycle ---

def start_round(state):
    state["round"] += 1
    state["trickNum"] = 1
    state["trick"] = []
    state["rankDeltas"] = {}

    # p. 9: shuffle the discard pile into the draw deck between rounds
    if state["round"] > 1:
        state["drawDeck"].extend(state["discard"])
        state["discard"] = []
        shuffle(state, state["drawDeck"])

    # p. 5: reveal the top card of the trump deck
    state["trumpShard"] = state["trumpDeck"].pop(0)
    state["usedTrumps"].append(state["trumpShard"])
    log(state, f'Round {state["round"]} begins. Trump: {SHARDS[state["trumpShard"]]["name"]}.', {
        "round": state["round"], "trump": state["trumpShard"],
    })

    # Deal 11 cards to each player (p. 5), plus Cultivation trump extras (trump card).
    trump = SHARDS[state["trumpShard"]]["trump"]
    extra = trump["drawByRound"][state["round"]] if trump["effect"] == "cultivation_round_start_draw" else 0
    for p in state["players"]:
        p["hand"] = []
        for _ in range(HAND_SIZE + extra):
            p["hand"].append(draw_card(state))
        p["hand"] = [cid for cid in p["hand"] if cid]

    if extra > 0:
        state["phase"] = "roundStartDiscard"
        state["turn"] = None
        state["pending"] = {"type": "roundStartDiscard", "count": extra, "selections": {}}
        log(state, f'Cultivation trump: everyone drew {extra} extra card(s) and must discard {extra}.')
    elif trump["effect"] == "devotion_round_start_place":
        # Devotion trump: the scoring-area card moves to the START of the round;
        # the remaining 10 cards are all played out over the 10 tricks.
        state["phase"] = "roundStartPlace"
        state["turn"] = None
        state["pending"] = {"type": "roundStartPlace", "selections": {}}
        log(state, 'Devotion trump: everyone places a card in their scoring area now instead of at round end.')
    else:
        state["phase"] = "play"
        state["turn"] = state["leader"]


def draw_card(state):
    if len(state["drawDeck"]) == 0 and len(state["discard"]) > 0:
        # p. 5 note: if the draw deck is empty, shuffle the discard pile into a new draw deck
        state["drawDeck"] = shuffle(state, list(state["discard"]))
        state["discard"] = []
    return state["drawDeck"].pop(0) if state["drawDeck"] else None


# --- rank evaluation ---

def effective_rank(state, trick_entry):
    card = state["cards"][trick_entry["cardId"]]
    rank = card["rank"] + state["rankDeltas"].get(card["id"], 0)
    if card["ability"]:
        ab = SHARDS[card["shard"]]["abilities"].get(card["ability"])
        effect = ab["effect"] if ab else None
        if effect == "rank_plus2_per_autonomy_elsewhere":
            n = 0
            for i, p in enumerate(state["players"]):
                if i != trick_entry["player"]:
                    n += sum(1 for cid in p["scoringArea"] if state["cards"][cid]["shard"] == "autonomy")
            rank += 2 * n
        elif effect == "rank_plus1_per_preservation_own":
            own = state["players"][trick_entry["player"]]
            rank += sum(1 for cid in own["scoringArea"] if state["cards"][cid]["shard"] == "preservation")
        elif effect == "rank_plus2_per_shard_own":
            own = state["players"][trick_entry["player"]]
            rank += 2 * len({state["cards"][cid]["shard"] for cid in own["scoringArea"]})
    return rank


# Which cards may the trick winner take? Trump cards can restrict or extend
# this (trump effects take precedence over everything — appendix p. 11).
def award_options(state):
    played = [e["cardId"] for e in state["trick"]]
    trump_effect = SHARDS[state["trumpShard"]]["trump"]["effect"]
    if trump_effect in ("dominion_transfer_highest", "ruin_transfer_lowest"):
        ranks = [effective_rank(state, e) for e in state["trick"]]
        target = max(ranks) if trump_effect == "dominion_transfer_highest" else min(ranks)
        return {"played": [cid for i, cid in enumerate(played) if ranks[i] == target], "discard": []}
    if trump_effect == "preservation_discard_pick":
        return {
            "played": played,
            "discard": [cid for cid in state["discard"] if state["cards"][cid]["shard"] == "preservation"],
        }
    return {"played": played, "discard": []}


# --- legality helpers ---

def must_follow(state, player):
    if len(state["trick"]) == 0:
        return None
    lead_shard = state["cards"][state["trick"][0]["cardId"]]["shard"]
    has_lead = any(state["cards"][cid]["shard"] == lead_shard for cid in state["players"][player]["hand"])
    return lead_shard if has_lead else None


def legal_plays(state, player):
    hand = state["players"][player]["hand"]
    if len(state["trick"]) == 0:
        # Autonomy trump (p. 7 card): may not lead Autonomy unless hand is all Autonomy
        if state["trumpShard"] == "autonomy":
            non_autonomy = [cid for cid in hand if state["cards"][cid]["shard"] != "autonomy"]
            if len(non_autonomy) > 0:
                return non_autonomy
        return list(hand)
    follow = must_follow(state, player)
    if not follow:
        return list(hand)
    return [cid for cid in hand if state["cards"][cid]["shard"] == follow]


# --- actions ---

def apply_action(state, player, action):
    state = deepcopy(state)
    action_type = action.get("type")
    if action_type == "playCard":
        do_play_card(state, player, action)
    elif action_type == "abilityChoice":
        do_ability_choice(state, player, action)
    elif action_type == "chooseTrickCard":
        do_choose_trick_card(state, player, action)
    elif action_type == "roundStartDiscard":
        do_round_start_discard(state, player, action)
    elif action_type == "roundStartPlace":
        do_round_start_place(state, player, action)
    elif action_type == "odiumSteal":
        do_odium_steal(state, player, action)
    else:
        raise ValueError(f'Unknown action type: {action_type}')
    auto_advance(state)
    return state


def require_turn(state, player, phase):
    if state["phase"] != phase:
        raise ValueError(f'Not in {phase} phase.')
    if state["turn"] != player:
        raise ValueError('Not your turn.')


def remove_from_hand(state, player, card_id):
    hand = state["players"][player]["hand"]
    if card_id not in hand:
        raise ValueError('Card not in hand.')
    hand.remove(card_id)


def discard_card(state, card_id):
    state["rankDeltas"].pop(card_id, None)  # appendix p. 11: rank changes end when discarded
    state["discard"].append(card_id)


def do_play_card(state, player, action):
    card_id = action.get("cardId")
    require_turn(state, player, "play")
    if card_id not in legal_plays(state, player):
        raise ValueError('That card is not a legal play (you must follow the lead Shard if able).')
    remove_from_hand(state, player, card_id)
    state["trick"].append({"player": player, "cardId": card_id})
    card = state["cards"][card_id]
    log(state, f'{state["players"][player]["name"]} plays {SHARDS[card["shard"]]["name"]} {card["rank"]}.', {
        "player": player, "cardId": card_id,
    })

    # p. 5: resolve the card's non-scoring ability immediately after playing
    ability = SHARDS[card["shard"]]["abilities"][card["ability"]] if card["ability"] else None
    if ability and ability["kind"] == "play":
        begin_play_ability(state, player, card, ability)
    if state["phase"] == "play":
        advance_after_card_resolved(state)


def begin_play_ability(state, player, card, ability):
    effect = ability["effect"]
    if effect == "autonomy_discard_draw":
        targets = [cid for cid in state["players"][player]["hand"] if state["cards"][cid]["shard"] == "autonomy"]
        if len(targets) == 0:
            return  # p. 5: no valid target -> ignore
        if len(state["drawDeck"]) + len(state["discard"]) == 0:
            return  # no card to draw back
        state["phase"] = "ability"
        state["pending"] = {"type": "autonomy_discard_draw", "player": player, "cardId": card["id"], "targets": targets}
        return
    if effect == "cultivation_discard_draw":
        if len(state["players"][player]["hand"]) == 0:
            return
        state["phase"] = "ability"
        state["pending"] = {"type": "cultivation_discard_draw", "player": player, "cardId": card["id"]}
        return
    if effect == "preservation_draw_discard":
        drawn = draw_card(state)
        if drawn is None:
            log(state, 'Draw deck empty; ability has no effect.')
            return
        state["players"][player]["hand"].append(drawn)
        state["phase"] = "ability"
        state["pending"] = {"type": "preservation_discard", "player": player, "cardId": card["id"]}
        return
    if effect == "cultivation_reveal_add":
        revealed = draw_card(state)
        if revealed is None:
            log(state, 'Draw deck empty; nothing to reveal.')
            return
        rc = state["cards"][revealed]
        log(state, f'Revealed {SHARDS[rc["shard"]]["name"]} {rc["rank"]} from the deck.', {"revealed": revealed})
        state["phase"] = "ability"
        state["pending"] = {"type": "cultivation_reveal_add", "player": player, "cardId": card["id"], "revealed": revealed}
        return
    if effect == "ruin_reveal_subtract":
        revealed = draw_card(state)
        if revealed is None:
            log(state, 'Draw deck empty; nothing to reveal.')
            return
        rc = state["cards"][revealed]
        log(state, f'Revealed {SHARDS[rc["shard"]]["name"]} {rc["rank"]} from the deck.', {"revealed": revealed})
        targets = [e["cardId"] for e in state["trick"] if e["player"] != player]
        state["phase"] = "ability"
        state["pending"] = {"type": "ruin_reveal_subtract", "player": player, "cardId": card["id"], "revealed": revealed, "targets": targets}
        return
    if effect == "devotion_exchange":
        targets = list(state["players"][player]["scoringArea"])
        if len(targets) == 0:
            return  # no valid target -> ignore
        state["phase"] = "ability"
        state["pending"] = {"type": "devotion_exchange", "player": player, "cardId": card["id"], "targets": targets}
        return
    if effect == "dominion_draw_discard_lowest":
        drawn = draw_card(state)
        if drawn is None:
            log(state, 'Draw deck empty; ability has no effect.')
            return
        me = state["players"][player]
        me["hand"].append(drawn)
        lowest = min(state["cards"][cid]["rank"] for cid in me["hand"])
        tied = [cid for cid in me["hand"] if state["cards"][cid]["rank"] == lowest]
        if len(tied) == 1:
            remove_from_hand(state, player, tied[0])
            discard_card(state, tied[0])
            dc = state["cards"][tied[0]]
            log(state, f'{me["name"]} draws and discards their lowest card ({SHARDS[dc["shard"]]["name"]} {dc["rank"]}).')
            return
        state["phase"] = "ability"
        state["pending"] = {"type": "dominion_discard_lowest", "player": player, "cardId": card["id"], "targets": tied}
        return
    if effect == "odium_discard_take":
        me = state["players"][player]
        victims = [i for i in range(len(state["players"])) if i != player and len(state["players"][i]["hand"]) > 0]
        # Both halves must apply, or hand sizes drift and the round structure breaks.
        if len(me["hand"]) == 0 or len(victims) == 0:
            return
        state["phase"] = "ability"
        state["pending"] = {"type": "odium_discard_take", "player": player, "cardId": card["id"], "victims": victims}
        return
    raise ValueError(f'Unimplemented play ability: {effect}')


def do_ability_choice(state, player, action):
    require_turn(state, player, "ability")
    pending = state["pending"]
    me = state["players"][player]
    ptype = pending["type"]
    if ptype == "autonomy_discard_draw":
        # "You may discard an Autonomy card from your hand. If you do, draw a card."
        if action.get("cardId") is not None:
            if action["cardId"] not in pending["targets"]:
                raise ValueError('Must discard an Autonomy card from your hand.')
            remove_from_hand(state, player, action["cardId"])
            discard_card(state, action["cardId"])
            drawn = draw_card(state)
            if drawn:
                me["hand"].append(drawn)
            log(state, f'{me["name"]} discards an Autonomy card and draws.')
        else:
            log(state, f'{me["name"]} declines the ability.')
    elif ptype == "cultivation_discard_draw":
        # "Discard up to two cards, then draw that many cards."
        ids = action.get("cardIds")
        if ids is None:
            ids = []
        if not isinstance(ids, list) or len(ids) > 2:
            raise ValueError('Discard up to two cards.')
        if len(set(ids)) != len(ids):
            raise ValueError('Duplicate cards.')
        if len(ids) > len(state["drawDeck"]) + len(state["discard"]):
            raise ValueError('Not enough cards left in the deck to draw that many.')
        for cid in ids:
            remove_from_hand(state, player, cid)
            discard_card(state, cid)
        for _ in range(len(ids)):
            drawn = draw_card(state)
            if drawn:
                me["hand"].append(drawn)
        log(state, f'{me["name"]} discards {len(ids)} card(s) and draws {len(ids)}.')
    elif ptype == "preservation_discard":
        # "Draw a card, then discard a card." (draw already happened)
        if action.get("cardId") is None:
            raise ValueError('You must discard a card.')
        remove_from_hand(state, player, action["cardId"])
        discard_card(state, action["cardId"])
        log(state, f'{me["name"]} draws and discards.')
    elif ptype == "cultivation_reveal_add":
        # "You may discard the revealed card to add its rank to this card, or put it back."
        rc = state["cards"][pending["revealed"]]
        if action.get("take"):
            discard_card(state, pending["revealed"])
            state["rankDeltas"][pending["cardId"]] = state["rankDeltas"].get(pending["cardId"], 0) + rc["rank"]
            now_rank = state["cards"][pending["cardId"]]["rank"] + state["rankDeltas"][pending["cardId"]]
            log(state, f'{me["name"]} adds {rc["rank"]} to their card (now rank {now_rank}).')
        else:
            state["drawDeck"].insert(0, pending["revealed"])
            log(state, f'{me["name"]} puts the revealed card back.')
    elif ptype == "ruin_reveal_subtract":
        # "You may discard the revealed card to subtract its rank from another player's played card, or put it back."
        if action.get("targetCardId") is not None:
            if action["targetCardId"] not in pending["targets"]:
                raise ValueError("Target must be another player's played card.")
            rc = state["cards"][pending["revealed"]]
            discard_card(state, pending["revealed"])
            state["rankDeltas"][action["targetCardId"]] = state["rankDeltas"].get(action["targetCardId"], 0) - rc["rank"]
            tc = state["cards"][action["targetCardId"]]
            now_rank = tc["rank"] + state["rankDeltas"][tc["id"]]
            log(state, f'{me["name"]} subtracts {rc["rank"]} from {SHARDS[tc["shard"]]["name"]} {tc["rank"]} (now {now_rank}).')
        else:
            state["drawDeck"].insert(0, pending["revealed"])
            log(state, f'{me["name"]} puts the revealed card back.')
    elif ptype == "devotion_exchange":
        # "You may exchange it with a card in your scoring area and activate its effect."
        if action.get("targetCardId") is not None:
            if action["targetCardId"] not in pending["targets"]:
                raise ValueError('Target must be a card in your scoring area.')
            entry = next(e for e in state["trick"] if e["cardId"] == pending["cardId"])
            incoming = action["targetCardId"]
            me["scoringArea"].remove(incoming)
            state["rankDeltas"].pop(pending["cardId"], None)
            me["scoringArea"].append(pending["cardId"])
            entry["cardId"] = incoming
            ic = state["cards"][incoming]
            log(state, f'{me["name"]} exchanges the played Devotion card with {SHARDS[ic["shard"]]["name"]} {ic["rank"]} from their scoring area.')
            state["pending"] = None
            state["phase"] = "play"
            in_ability = SHARDS[ic["shard"]]["abilities"][ic["ability"]] if ic["ability"] else None
            if in_ability and in_ability["kind"] == "play":
                begin_play_ability(state, player, ic, in_ability)
                if state["phase"] == "ability":
                    return  # chained ability awaits its own choice
            advance_after_card_resolved(state)
            return
        log(state, f'{me["name"]} declines the exchange.')
    elif ptype == "dominion_discard_lowest":
        # tie for lowest rank: the player picks which to discard
        if action.get("cardId") not in pending["targets"]:
            raise ValueError('You must discard a card tied for the lowest rank.')
        remove_from_hand(state, player, action["cardId"])
        discard_card(state, action["cardId"])
        dc = state["cards"][action["cardId"]]
        log(state, f'{me["name"]} draws and discards their lowest card ({SHARDS[dc["shard"]]["name"]} {dc["rank"]}).')
    elif ptype == "odium_discard_take":
        # "Discard a card, then take a random card from another player's hand. Then they draw a card."
        card_id = action.get("cardId")
        target_player = action.get("targetPlayer")
        if card_id not in me["hand"]:
            raise ValueError('Card not in hand.')
        if target_player not in pending["victims"]:
            raise ValueError('Target must be another player with cards in hand.')
        remove_from_hand(state, player, card_id)
        discard_card(state, card_id)
        victim = state["players"][target_player]
        stolen_idx = math.floor(next_rand(state) * len(victim["hand"]))
        stolen = victim["hand"].pop(stolen_idx)
        me["hand"].append(stolen)
        drawn = draw_card(state)
        if drawn:
            victim["hand"].append(drawn)
        log(state, f'{me["name"]} discards a card, takes a random card from {victim["name"]}\'s hand, and {victim["name"]} draws.')
    else:
        raise ValueError(f'Unknown pending ability: {ptype}')
    state["pending"] = None
    state["phase"] = "play"
    advance_after_card_resolved(state)


def advance_after_card_resolved(state):
    if len(state["trick"]) == state["playerCount"]:
        finish_trick(state)
    else:
        state["turn"] = (state["trick"][-1]["player"] + 1) % state["playerCount"]


def finish_trick(state):
    # p. 6: strongest card wins — highest trump, else highest of the lead Shard,
    # ties won by the last tied card played.
    lead_shard = state["cards"][state["trick"][0]["cardId"]]["shard"]
    trumps = [e for e in state["trick"] if state["cards"][e["cardId"]]["shard"] == state["trumpShard"]]
    pool = trumps if len(trumps) > 0 else [e for e in state["trick"] if state["cards"][e["cardId"]]["shard"] == lead_shard]
    best = pool[0]
    for entry in pool[1:]:
        if effective_rank(state, entry) >= effective_rank(state, best):
            best = entry
    state["lastTrickWinner"] = best["player"]
    wc = state["cards"][best["cardId"]]
    log(state, f'{state["players"][best["player"]]["name"]} wins trick {state["trickNum"]} with {SHARDS[wc["shard"]]["name"]} {wc["rank"]}.', {
        "winner": best["player"], "trick": state["trickNum"],
    })
    state["phase"] = "award"
    state["turn"] = best["player"]


def do_choose_trick_card(state, player, action):
    card_id = action.get("cardId")
    require_turn(state, player, "award")
    options = award_options(state)
    from_discard = card_id in options["discard"]
    if card_id not in options["played"] and not from_discard:
        raise ValueError('That card cannot be taken (the trump Shard restricts the choice).')
    state["rankDeltas"].pop(card_id, None)  # rank changes end on transfer (appendix p. 11)
    state["players"][player]["scoringArea"].append(card_id)
    if from_discard:
        # Preservation trump: take a Preservation card from the discard pile
        # instead of a played card; every played card is discarded.
        state["discard"].remove(card_id)
        for e in state["trick"]:
            discard_card(state, e["cardId"])
    else:
        for e in state["trick"]:
            if e["cardId"] != card_id:
                discard_card(state, e["cardId"])
    cc = state["cards"][card_id]
    suffix = ' from the discard pile' if from_discard else ''
    log(state, f'{state["players"][player]["name"]} takes {SHARDS[cc["shard"]]["name"]} {cc["rank"]}{suffix} into their scoring area.', {
        "player": player, "cardId": card_id,
    })
    state["trick"] = []
    state["leader"] = player
    state["turn"] = player

    if state["trickNum"] >= TRICKS_PER_ROUND:
        end_round(state)
    else:
        state["trickNum"] += 1
        state["phase"] = "play"


def end_round(state):
    # p. 9: each player's last hand card goes to their scoring area. (With the
    # Devotion trump that happened at round start, so hands are already empty.)
    for p in state["players"]:
        if len(p["hand"]) > 0:
            cid = p["hand"].pop()
            state["rankDeltas"].pop(cid, None)
            p["scoringArea"].append(cid)
            c = state["cards"][cid]
            log(state, f'{p["name"]}\'s last card ({SHARDS[c["shard"]]["name"]} {c["rank"]}) goes to their scoring area.')

    # Odium trump: the player with strictly the fewest scoring-area cards may
    # steal a card from the (or a tied-for) largest scoring area.
    if SHARDS[state["trumpShard"]]["trump"]["effect"] == "odium_steal":
        counts = [len(p["scoringArea"]) for p in state["players"]]
        min_count = min(counts)
        fewest = [i for i, c in enumerate(counts) if c == min_count]
        max_count = max(counts)
        if len(fewest) == 1 and max_count > min_count:
            thief = fewest[0]
            targets = [cid for i, p in enumerate(state["players"]) if counts[i] == max_count for cid in p["scoringArea"]]
            state["phase"] = "odiumSteal"
            state["turn"] = thief
            state["pending"] = {"type": "odiumSteal", "player": thief, "targets": targets}
            log(state, f'Odium trump: {state["players"][thief]["name"]} has the fewest scoring cards and may steal one.')
            return  # finish_round continues after the choice
    finish_round(state)


def do_odium_steal(state, player, action):
    card_id = action.get("cardId")
    require_turn(state, player, "odiumSteal")
    pending = state["pending"]
    if card_id is not None:
        if card_id not in pending["targets"]:
            raise ValueError('Steal from a scoring area with the most cards.')
        owner = next(p for p in state["players"] if card_id in p["scoringArea"])
        owner["scoringArea"].remove(card_id)
        state["players"][player]["scoringArea"].append(card_id)
        c = state["cards"][card_id]
        log(state, f'{state["players"][player]["name"]} steals {SHARDS[c["shard"]]["name"]} {c["rank"]} from {owner["name"]}.')
    else:
        log(state, f'{state["players"][player]["name"]} declines to steal.')
    state["pending"] = None
    finish_round(state)


def finish_round(state):
    if state["round"] >= ROUNDS:
        score_game(state)
    else:
        state["leader"] = state["lastTrickWinner"]  # p. 5: first player is last round's last-trick winner
        start_round(state)


def do_round_start_place(state, player, action):
    card_id = action.get("cardId")
    if state["phase"] != "roundStartPlace":
        raise ValueError('Not in round-start placement phase.')
    pending = state["pending"]
    # Selections are keyed by str(player) to match the JS object keys exactly
    # (JS object keys are always strings; parity traces compare raw JSON).
    if pending["selections"].get(str(player)) is not None:
        raise ValueError('Already placed a card.')
    if card_id not in state["players"][player]["hand"]:
        raise ValueError('Card not in hand.')
    pending["selections"][str(player)] = card_id
    log(state, f'{state["players"][player]["name"]} sets aside a card for their scoring area.')

    if len(pending["selections"]) == state["playerCount"]:
        # JS iterates integer-like object keys in ascending numeric order.
        for p_idx, cid in sorted(pending["selections"].items(), key=lambda kv: int(kv[0])):
            p = state["players"][int(p_idx)]
            remove_from_hand(state, int(p_idx), cid)
            p["scoringArea"].append(cid)
            c = state["cards"][cid]
            log(state, f'{p["name"]} places {SHARDS[c["shard"]]["name"]} {c["rank"]} in their scoring area.')
        state["pending"] = None
        state["phase"] = "play"
        state["turn"] = state["leader"]


def do_round_start_discard(state, player, action):
    card_ids = action.get("cardIds")
    if state["phase"] != "roundStartDiscard":
        raise ValueError('Not in round-start discard phase.')
    pending = state["pending"]
    if pending["selections"].get(str(player)):
        raise ValueError('Already discarded.')
    if not isinstance(card_ids, list) or len(card_ids) != pending["count"]:
        raise ValueError(f'You must discard exactly {pending["count"]} card(s).')
    if len(set(card_ids)) != len(card_ids):
        raise ValueError('Duplicate cards.')
    for cid in card_ids:
        if cid not in state["players"][player]["hand"]:
            raise ValueError('Card not in hand.')
    pending["selections"][str(player)] = card_ids
    log(state, f'{state["players"][player]["name"]} sets aside {pending["count"]} discard(s).')

    if len(pending["selections"]) == state["playerCount"]:
        # simultaneous reveal (Cultivation trump card)
        # JS iterates integer-like object keys in ascending numeric order.
        for p_idx, ids in sorted(pending["selections"].items(), key=lambda kv: int(kv[0])):
            for cid in ids:
                remove_from_hand(state, int(p_idx), cid)
                discard_card(state, cid)
        state["pending"] = None
        state["phase"] = "play"
        state["turn"] = state["leader"]


def auto_advance(state):
    # Forced play: exactly one legal card (e.g. Devotion's 11th trick) — play it
    # automatically so the game never waits on a non-choice.
    while state["phase"] == "play":
        legal = legal_plays(state, state["turn"])
        if len(legal) != 1 or len(state["players"][state["turn"]]["hand"]) != 1:
            return
        do_play_card(state, state["turn"], {"cardId": legal[0]})


# --- end-game scoring (pp. 9-10) ---

# Devotion's scoring ability makes a card wild ("treat it as any Shard except
# Devotion") and Dominion's turns one other card into Dominion. Players may
# resolve scoring abilities in any order (p. 9), so pick the best assignment.
def best_wild_assignment(state, base_counts, n_wilds, n_converters):
    shard_ids = state["shardIds"]
    wild_options = [s for s in shard_ids if s != "devotion"]
    conv_options = [s for s in shard_ids if s != "dominion"]
    best = None

    def eval_counts(counts):
        points = sum(shard_set_score(counts[sid]) for sid in shard_ids)
        sets = min(counts[sid] for sid in shard_ids)
        return points + resonance_score(sets, state["playerCount"])

    def rec(counts, w, c):
        nonlocal best
        if w < n_wilds:
            for sid in wild_options:
                counts["devotion"] -= 1
                counts[sid] += 1
                rec(counts, w + 1, c)
                counts["devotion"] += 1
                counts[sid] -= 1
            return
        if c < n_converters:
            valid = [sid for sid in conv_options if counts[sid] > 0]
            if len(valid) == 0:
                rec(counts, w, c + 1)  # no valid target -> skip
                return
            for sid in valid:
                counts[sid] -= 1
                counts["dominion"] += 1
                rec(counts, w, c + 1)
                counts[sid] += 1
                counts["dominion"] -= 1
            return
        total = eval_counts(counts)
        if not best or total > best["total"]:
            best = {"total": total, "counts": dict(counts)}

    rec(dict(base_counts), 0, 0)
    return best


def score_game(state):
    scores = []
    for idx, p in enumerate(state["players"]):
        ability_points = 0
        wilds = 0
        converters = 0
        for cid in p["scoringArea"]:
            card = state["cards"][cid]
            ab = SHARDS[card["shard"]]["abilities"].get(card["ability"]) if card["ability"] else None
            if not ab or ab["kind"] != "scoring":
                continue
            if ab["effect"] == "score_minus2":
                ability_points -= 2
            elif ab["effect"] == "devotion_wild":
                wilds += 1
            elif ab["effect"] == "dominion_convert":
                converters += 1
        base_counts = {sid: 0 for sid in state["shardIds"]}
        for cid in p["scoringArea"]:
            base_counts[state["cards"][cid]["shard"]] += 1

        best = best_wild_assignment(state, base_counts, wilds, converters)
        by_shard = best["counts"]
        shard_points = {sid: shard_set_score(n) for sid, n in by_shard.items()}
        sets = min(by_shard.values())
        resonance = resonance_score(sets, state["playerCount"])
        total = ability_points + best["total"]
        scores.append({
            "player": idx, "name": p["name"], "abilityPoints": ability_points,
            "byShard": by_shard, "shardPoints": shard_points,
            "resonanceSets": sets, "resonance": resonance, "total": total,
            "cardCount": len(p["scoringArea"]),
        })

    # p. 10: highest total wins; ties broken by fewest scoring-area cards; then shared
    best_total = max(s["total"] for s in scores)
    contenders = [s for s in scores if s["total"] == best_total]
    fewest = min(s["cardCount"] for s in contenders)
    winners = [s["player"] for s in contenders if s["cardCount"] == fewest]

    state["result"] = {"scores": scores, "winners": winners}
    state["phase"] = "gameOver"
    state["turn"] = None
    log(state, f'Game over. Winner(s): {", ".join(state["players"][w]["name"] for w in winners)}.')


# --- per-player redacted view ---

def view_for(state, player):
    award = None
    if state["phase"] == "award":
        o = award_options(state)
        award = {"played": o["played"], "discard": [state["cards"][cid] for cid in o["discard"]]}
    return {
        "phase": state["phase"],
        "round": state["round"],
        "trickNum": state["trickNum"],
        "trumpShard": state["trumpShard"],
        "shardIds": state["shardIds"],
        "leader": state["leader"],
        "turn": state["turn"],
        "you": player,
        "hand": [state["cards"][cid] for cid in state["players"][player]["hand"]] if player is not None else [],
        "legal": legal_plays(state, player) if player is not None and state["phase"] == "play" and state["turn"] == player else [],
        "awardOptions": award,
        "trick": [
            {"player": e["player"], "card": state["cards"][e["cardId"]], "effectiveRank": effective_rank(state, e)}
            for e in state["trick"]
        ],
        "players": [
            {
                "name": p["name"],
                "handCount": len(p["hand"]),
                "scoringArea": [state["cards"][cid] for cid in p["scoringArea"]],
                "isYou": i == player,
            }
            for i, p in enumerate(state["players"])
        ],
        "drawCount": len(state["drawDeck"]),
        "discardCount": len(state["discard"]),
        "pending": redact_pending(state, player),
        "rankDeltas": state["rankDeltas"],
        "log": state["log"][-40:],
        "result": state["result"],
    }


def redact_pending(state, player):
    if not state["pending"]:
        return None
    p = state["pending"]
    if p["type"] in ("roundStartDiscard", "roundStartPlace"):
        return {
            "type": p["type"],
            "count": p.get("count", 1),
            "waitingOn": [i for i in range(len(state["players"])) if p["selections"].get(str(i)) is None],
        }
    base = {"type": p["type"], "player": p["player"], "targets": p.get("targets"), "victims": p.get("victims")}
    # revealed cards are public information (they are revealed face up)
    if p.get("revealed"):
        base["revealed"] = state["cards"][p["revealed"]]
    return base


# --- concrete action enumeration (used by the adapter and the parity driver) ---

def enumerate_actions(state, player):
    """Every concrete legal action dict for a seat, in a deterministic order.

    The JS parity-trace generator (tests/parity/gen_traces.mjs) mirrors this
    ordering exactly; keep the two in sync.
    """
    phase = state["phase"]
    if phase == "play":
        if state["turn"] != player:
            return []
        return [{"type": "playCard", "cardId": cid} for cid in legal_plays(state, player)]
    if phase == "ability":
        if state["turn"] != player:
            return []
        p = state["pending"]
        ptype = p["type"]
        hand = state["players"][player]["hand"]
        if ptype == "autonomy_discard_draw":
            return ([{"type": "abilityChoice", "cardId": cid} for cid in p["targets"]]
                    + [{"type": "abilityChoice", "cardId": None}])
        if ptype == "cultivation_discard_draw":
            avail = len(state["drawDeck"]) + len(state["discard"])
            acts = [{"type": "abilityChoice", "cardIds": []}]
            if avail >= 1:
                acts += [{"type": "abilityChoice", "cardIds": [cid]} for cid in hand]
            if avail >= 2:
                acts += [{"type": "abilityChoice", "cardIds": [a, b]} for a, b in combinations(hand, 2)]
            return acts
        if ptype == "preservation_discard":
            return [{"type": "abilityChoice", "cardId": cid} for cid in hand]
        if ptype == "cultivation_reveal_add":
            return [{"type": "abilityChoice", "take": True}, {"type": "abilityChoice", "take": False}]
        if ptype == "ruin_reveal_subtract":
            return ([{"type": "abilityChoice", "targetCardId": cid} for cid in p["targets"]]
                    + [{"type": "abilityChoice", "targetCardId": None}])
        if ptype == "devotion_exchange":
            return ([{"type": "abilityChoice", "targetCardId": cid} for cid in p["targets"]]
                    + [{"type": "abilityChoice", "targetCardId": None}])
        if ptype == "dominion_discard_lowest":
            return [{"type": "abilityChoice", "cardId": cid} for cid in p["targets"]]
        if ptype == "odium_discard_take":
            return [{"type": "abilityChoice", "cardId": cid, "targetPlayer": v}
                    for cid in hand for v in p["victims"]]
        return []
    if phase == "award":
        if state["turn"] != player:
            return []
        o = award_options(state)
        return [{"type": "chooseTrickCard", "cardId": cid} for cid in o["played"] + o["discard"]]
    if phase == "roundStartDiscard":
        p = state["pending"]
        if p["selections"].get(str(player)):
            return []
        hand = state["players"][player]["hand"]
        return [{"type": "roundStartDiscard", "cardIds": list(c)} for c in combinations(hand, p["count"])]
    if phase == "roundStartPlace":
        p = state["pending"]
        if p["selections"].get(str(player)) is not None:
            return []
        return [{"type": "roundStartPlace", "cardId": cid} for cid in state["players"][player]["hand"]]
    if phase == "odiumSteal":
        if state["turn"] != player:
            return []
        return ([{"type": "odiumSteal", "cardId": cid} for cid in state["pending"]["targets"]]
                + [{"type": "odiumSteal", "cardId": None}])
    return []  # gameOver / setup


# --- GameEngine adapter ---

class ShardsEngine(GameEngine):
    """Adapts the pure Shards of Creation core to the server's GameEngine ABC.

    Seats are indices into state["players"]; each seat stores the BGE
    player_id in players[i]["id"], so mapping is a simple lookup.
    """

    player_count_range = (2, 4)

    def initial_state(self, player_ids, player_names):
        # Random seed + random shard selection, mirroring the JS online.js
        # default (crypto.randomInt(2**31) seed, createGame picks a random
        # sample of playable shards). Randomness lives only here; the core
        # stays deterministic.
        seed = _random.randrange(2 ** 31)
        return create_game(player_ids=player_ids, player_names=player_names,
                           shard_ids=None, seed=seed)

    def _seat(self, state, player_id):
        for i, p in enumerate(state["players"]):
            if p["id"] == player_id:
                return i
        return None

    def get_player_view(self, state, player_id):
        seat = self._seat(state, player_id)
        view = view_for(state, seat)
        view["your_player_id"] = player_id
        view["valid_actions"] = self.get_valid_actions(state, player_id)
        return view

    def get_valid_actions(self, state, player_id):
        seat = self._seat(state, player_id)
        if seat is None:
            return []
        return enumerate_actions(state, seat)

    def apply_action(self, state, player_id, action):
        seat = self._seat(state, player_id)
        if seat is None:
            raise ValueError('You are not in this game.')
        prev_log_len = len(state["log"])
        new_state = apply_action(state, seat, action)
        return ActionResult(
            new_state=new_state,
            log=[e["msg"] for e in new_state["log"][prev_log_len:]],
            game_over=new_state["phase"] == "gameOver",
        )

    def get_waiting_for(self, state):
        if state["phase"] in ("roundStartDiscard", "roundStartPlace"):
            selections = state["pending"]["selections"]
            return [p["id"] for i, p in enumerate(state["players"]) if selections.get(str(i)) is None]
        if state["turn"] is not None:
            return [state["players"][state["turn"]]["id"]]
        return []

    def get_phase_info(self, state):
        phase = state["phase"]
        rnd = state["round"]
        trick = state["trickNum"]
        turn_name = state["players"][state["turn"]]["name"] if state["turn"] is not None else None
        if phase == "play":
            desc = f'Round {rnd}, Trick {trick} — {turn_name} to play'
        elif phase == "ability":
            desc = f'Round {rnd}, Trick {trick} — {turn_name} resolving an ability'
        elif phase == "award":
            desc = f'Round {rnd}, Trick {trick} — {turn_name} choosing a trick card'
        elif phase == "roundStartDiscard":
            count = state["pending"]["count"]
            desc = f'Round {rnd} — everyone discards {count} card(s) (Cultivation trump)'
        elif phase == "roundStartPlace":
            desc = f'Round {rnd} — everyone places a scoring-area card (Devotion trump)'
        elif phase == "odiumSteal":
            desc = f'Round {rnd} — {turn_name} may steal a scoring card (Odium trump)'
        elif phase == "gameOver":
            winners = ", ".join(state["players"][w]["name"] for w in state["result"]["winners"])
            desc = f'Game over. Winner(s): {winners}.'
        else:
            desc = phase
        return {
            "phase": phase,
            "round": rnd,
            "trick": trick,
            "trump": state["trumpShard"],
            "description": desc,
        }

    def get_spectator_view(self, state):
        # view_for(state, None) already hides every hand (counts only) and
        # redacts pending the same way it does for uninvolved players.
        view = view_for(state, None)
        view["your_player_id"] = None
        view["valid_actions"] = []
        return view
