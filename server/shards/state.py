"""
Shards of Creation — card database + deterministic RNG.

Ported 1:1 from overnightlemons.com server/games/shards/cards.js (plus the
mulberry32 RNG from engine.js). State keys stay camelCase to match the JS
engine exactly; cross-language parity traces depend on it.

Sources: the rulebook PDF (reference cards pp. 3-4, examples pp. 7-9,
appendix p. 11) plus frame-verified card closeups from three videos:
Watch It Played tutorial (WITP), "Cosmere 16" unboxing (C16), and the
How Lou Sees It unboxing (LOU), which shows every reference card.

Pattern (RB appendix): each shard has 8 no-ability cards, 4 with ability 1,
4 with ability 2 — except Honor (no abilities) and Preservation (abilities
on 15 of 16). Every verified observation fits "ability 1 on the 4 lowest
ranks, ability 2 on the next 4"; ranks marked UNVERIFIED below follow that
pattern but weren't individually eyeballed.
"""

MASK32 = 0xFFFFFFFF


# --- deterministic RNG (mulberry32) so games are replayable/testable ---
#
# Bit-exact port of the JS version. JS mixes signed/unsigned 32-bit ops
# (Math.imul, >>>, ^, +); doing everything on the unsigned 32-bit
# representation with a mask after every add/multiply yields the same bit
# patterns, because XOR/OR/shift/low-32-multiply/mod-2^32-add are identical
# on the two's-complement encoding regardless of sign interpretation.

def next_rand(state):
    state["rng"] = (state["rng"] + 0x6D2B79F5) & MASK32
    t = state["rng"]
    t = ((t ^ (t >> 15)) * (t | 1)) & MASK32
    t = (t ^ (t + (((t ^ (t >> 7)) * (t | 61)) & MASK32))) & MASK32
    return ((t ^ (t >> 14)) & MASK32) / 4294967296


def shuffle(state, arr):
    import math
    for i in range(len(arr) - 1, 0, -1):
        j = math.floor(next_rand(state) * (i + 1))
        arr[i], arr[j] = arr[j], arr[i]
    return arr


SHARDS = {
    "autonomy": {
        "id": "autonomy",
        "name": "Autonomy",
        "color": "#b3452c",
        "complete": True,
        "ranks": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16],  # LOU 312s
        "abilities": {
            "a1": {
                "kind": "play",
                "text": "You may discard an Autonomy card from your hand. If you do, draw a card.",
                "effect": "autonomy_discard_draw",
            },
            "a2": {
                "kind": "rank",
                "text": "This card's rank has +2 for each Autonomy card in other players' scoring areas.",
                "effect": "rank_plus2_per_autonomy_elsewhere",
            },
        },
        # a1 seen on 1,2,4; a2 seen on 5,7. Ranks 3,6,8 UNVERIFIED (pattern).
        "abilityRanks": {"a1": [1, 2, 3, 4], "a2": [5, 6, 7, 8]},
        "trump": {
            "text": "You may not lead with Autonomy unless you have no other Shard in your hand.",
            "effect": "autonomy_lead_restriction",
        },
    },

    "cultivation": {
        "id": "cultivation",
        "name": "Cultivation",
        "color": "#6b8f2e",
        "complete": True,
        "ranks": [2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17],  # RB p. 3
        "abilities": {
            "a1": {
                "kind": "play",
                "text": "Reveal the top card of the deck. You may discard the revealed card to add its rank to this card, or put it back.",
                "effect": "cultivation_reveal_add",
            },
            "a2": {
                "kind": "play",
                "text": "Discard up to two cards, then draw that many cards.",
                "effect": "cultivation_discard_draw",
            },
        },
        # a1 seen on 2,3; a2 seen on 6. Ranks 4,5,7,8,9 UNVERIFIED (pattern).
        "abilityRanks": {"a1": [2, 3, 4, 5], "a2": [6, 7, 8, 9]},
        "trump": {
            "text": "At the start of the round, all players draw additional cards as follows, then all simultaneously discard that many cards. Round 1: Draw 3 cards. Round 2: Draw 2 cards. Round 3: Draw 1 card.",
            "effect": "cultivation_round_start_draw",
            "drawByRound": {1: 3, 2: 2, 3: 1},
        },
    },

    "devotion": {
        "id": "devotion",
        "name": "Devotion",
        "color": "#7b4b94",
        "complete": True,
        "ranks": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 16],  # skips 15 (LOU 478s)
        "abilities": {
            "a1": {
                "kind": "scoring",
                "text": "At the end of the game, if this card is in your scoring area, you must treat it as any Shard except Devotion.",
                "effect": "devotion_wild",
            },
            "a2": {
                "kind": "play",
                "text": "When you play this, you may exchange it with a card in your scoring area and activate its effect.",
                "effect": "devotion_exchange",
            },
        },
        # a1 seen on 0,1,2; a2 seen on 5,6,7. Ranks 3,4 UNVERIFIED (pattern).
        "abilityRanks": {"a1": [0, 1, 2, 3], "a2": [4, 5, 6, 7]},
        "trump": {
            # Card prints "all 10 rounds", meaning the 10 tricks (LOU 486s; RB p. 8).
            "text": "Place a card in your scoring area at the start of the round instead of the end of the round. Play all 10 tricks, with no cards remaining after the last trick.",
            "effect": "devotion_round_start_place",
        },
    },

    "dominion": {
        "id": "dominion",
        "name": "Dominion",
        "color": "#8a6d3b",
        "complete": True,
        "ranks": [2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 20],  # LOU 556s
        "abilities": {
            "a1": {
                "kind": "scoring",
                "text": "At the end of the game, if this card is in your scoring area, treat one of your non-Dominion scoring cards as Dominion.",
                "effect": "dominion_convert",
            },
            "a2": {
                "kind": "play",
                "text": "Draw a card. Discard the card in your hand with the lowest rank.",
                "effect": "dominion_draw_discard_lowest",
            },
        },
        # a1 VERIFIED on 2,3,4; a2 VERIFIED on 6,8. Whether the 4th a1 is the 5 or
        # the 20 is UNRESOLVED — 2-5/6-9 fits the every-shard pattern and the 20
        # showed no ability plate on close inspection.
        "abilityRanks": {"a1": [2, 3, 4, 5], "a2": [6, 7, 8, 9]},
        "trump": {
            "text": "The winner of each trick must choose the card with the highest rank to place in their scoring area.",
            "effect": "dominion_transfer_highest",
        },
    },

    "honor": {
        "id": "honor",
        "name": "Honor",
        "color": "#2e6e8f",
        "complete": True,
        "ranks": [0, 1, 2, 3, 4, 5, 6, 7, 8, 10, 11, 12, 13, 14, 15, 16],  # skips 9 (RB p. 4)
        "abilities": {},
        "abilityRanks": {},
        "trump": {"text": "No trump ability.", "effect": None},
    },

    "odium": {
        "id": "odium",
        "name": "Odium",
        "color": "#b89b2c",
        "complete": True,
        "ranks": [1, 2, 3, 4, 5, 6, 7, 8, 9, 9, 11, 12, 13, 14, 15, 16],  # two 9s, no 10 (WITP; LOU 761s)
        "abilities": {
            "a1": {
                "kind": "play",
                "text": "Discard a card, then take a random card from another player's hand. Then they draw a card.",
                "effect": "odium_discard_take",
            },
            "a2": {
                "kind": "rank",
                "text": "This card's rank has +2 for each different Shard in your scoring area.",
                "effect": "rank_plus2_per_shard_own",
            },
        },
        # a1 seen on 3,4; a2 seen on 5,8. Ranks 1,2,6,7 UNVERIFIED (pattern).
        "abilityRanks": {"a1": [1, 2, 3, 4], "a2": [5, 6, 7, 8]},
        "trump": {
            "text": "At the end of the round, if one player has fewer cards in their scoring area than any other player, they may steal a card from the scoring area with the most (or tied for most).",
            "effect": "odium_steal",
        },
    },

    "preservation": {
        "id": "preservation",
        "name": "Preservation",
        "color": "#5f8f7b",
        "complete": True,
        "ranks": [8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 16],  # LOU 824s
        "abilities": {
            "a1": {
                "kind": "play",
                "text": "Draw a card, then discard a card.",
                "effect": "preservation_draw_discard",
            },
            "a2": {
                "kind": "rank",
                "text": "This card's rank has +1 for each Preservation card in your scoring area.",
                "effect": "rank_plus1_per_preservation_own",
            },
        },
        # The 16 is the ability-less card. The a1/a2 split among the fifteen 8s is
        # UNKNOWN (no source shows it) — 8/7 chosen; correct once counted from a
        # physical deck.
        "abilityCounts": {"a1": 8, "a2": 7},
        "trump": {
            "text": "The winner of each trick may choose a Preservation card from the discard pile to place in their scoring area instead of a played card.",
            "effect": "preservation_discard_pick",
        },
    },

    "ruin": {
        "id": "ruin",
        "name": "Ruin",
        "color": "#5a3535",
        "complete": True,
        "ranks": [-1, -1, -1, -1, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16],  # RB p. 4; LOU 616s
        "abilities": {
            "a1": {
                "kind": "scoring",
                "text": "At the end of the game, if this card is in your scoring area, score -2 points.",
                "effect": "score_minus2",
            },
            "a2": {
                "kind": "play",
                "text": "Reveal the top card of the deck. You may discard the revealed card to subtract its rank from another player's played card, or put it back.",
                "effect": "ruin_reveal_subtract",
            },
        },
        # a1 on all four -1s (VERIFIED); a2 seen on 5,7,8. Rank 6 UNVERIFIED (pattern).
        "abilityRanks": {"a1": [-1, -1, -1, -1], "a2": [5, 6, 7, 8]},
        "trump": {
            "text": "The winner of each trick must choose the card with the lowest rank to place in their scoring area.",
            "effect": "ruin_transfer_lowest",
        },
    },
}


# Scoring aid (RB pp. 3-4, 10). Shard sets score triangular numbers, capped at 136 for 16+.
def shard_set_score(count):
    n = min(count, 16)
    return (n * (n + 1)) // 2


# Resonance: totals for N complete sets (one card of every shard in the game).
# 2/3-player aid: 5/10/20/40/80; 4-player aid: 8/16/32/64 (doubling).
def resonance_score(sets, player_count):
    if sets <= 0:
        return 0
    base = 8 if player_count == 4 else 5
    return base * 2 ** (sets - 1)


def shards_for_player_count(player_count):
    return 5 if player_count == 4 else 4  # RB p. 4 setup


def playable_shards():
    return [s for s in SHARDS.values() if s["complete"]]


# Build the 16 card instances for one shard. Preservation's identical ranks make
# per-rank mapping meaningless, so abilityCounts assigns abilities by copy index.
def build_shard_cards(shard):
    cards = []
    remaining_by_ability = {}
    if shard.get("abilityCounts"):
        remaining_by_ability.update(shard["abilityCounts"])
    ability_rank_pool = {}
    for ab, ranks in (shard.get("abilityRanks") or {}).items():
        ability_rank_pool[ab] = list(ranks)
    for i, rank in enumerate(shard["ranks"]):
        ability = None
        if shard.get("abilityCounts"):
            is_plain = rank == max(shard["ranks"])
            if not is_plain:
                if remaining_by_ability.get("a1", 0) > 0:
                    ability = "a1"
                    remaining_by_ability["a1"] -= 1
                elif remaining_by_ability.get("a2", 0) > 0:
                    ability = "a2"
                    remaining_by_ability["a2"] -= 1
        else:
            for ab, pool in ability_rank_pool.items():
                if rank in pool:
                    ability = ab
                    pool.remove(rank)
                    break
        cards.append({
            "id": f"{shard['id']}:{rank}:{i}",
            "shard": shard["id"],
            "rank": rank,
            "ability": ability,
        })
    return cards
