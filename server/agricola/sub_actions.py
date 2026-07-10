"""
Card-facing API for Agricola's "bonus build/play sub-action" transactions.

Background: dozens of occupations/minor improvements grant something like
"immediately build a room / up to 2 stables / fences / a minor or major
improvement / sow / play an occupation, possibly at a discount or free".
Before this module existed, the *only* code that could build a room,
raise a fence layout, renovate a house, build a major improvement, play
an occupation/minor, or sow a field lived in `engine.py`'s private
`_do_*`/`_play_*` methods, reachable only through the normal action-space
dispatch (`_resolve_space`). Card authors were rightly forbidden from
duplicating that logic per-card (rules text like "adjacent to an
existing room", house-type-dependent renovation cost, `available_
improvements` bookkeeping, occupation hand/cost/`occs_played` bookkeeping
... are exactly the kind of thing that silently drifts if reimplemented
twice) -- see CARDS.md and `decks/GUIDE.md`'s "not supported" notes for
the paper trail, and the (now-migrated) cheaters this module replaces:
Builder's Trowel (E50), Educator (K271), Scholar (K279), Junior Artist
(B152), Craft Teacher (A131), Master Builder/Mason (E151/E191), Hut
Builder (E178), Hammer Crusher (D014), Dwelling Plan (D002), Renovation
Company (A013), Carpenter's Axe (A015), and Furrows (D003).

`engine.py` cannot be imported from here (it imports `cards`, which
would import this module, which would import it back), so these
functions are the canonical, engine-independent implementation of each
transaction; `engine.py`'s `_do_build_rooms`/`_do_build_stables`/
`_do_build_fences`/`_do_renovate`/`_do_improvement`/`_do_sow`/
`_play_occupation`/`_play_minor` (and the `can_*`/`*_possible`/
`buildable_*` predicates the placement-listing code uses) are now thin
wrappers that call straight into this module -- there is exactly one
implementation of each transaction, used by both the normal action-space
dispatch and any card hook/card_action that wants to trigger the same
transaction directly.

Cost handling
-------------
Every transaction that has a resource cost takes `cost_override`:

- `None` (default) -- the normal cost, run through `cards.modified_cost`
  exactly as the matching action space does (a Stonecutter/Carpenter/
  Hedge Keeper-style discount still applies).
- a goods dict -- an exact cost to charge instead, bypassing
  `modified_cost` entirely. This is how a card expresses "your own flat
  discount" (compute the discounted dict yourself and pass it) without
  a pile of boolean flags.
- the string `"free"` -- skip payment altogether.

Every cost-bearing transaction also takes an optional `ctx` dict, folded
into whatever ctx `cards.modified_cost` builds for that transaction's
own kind (batch `count`/`start_index`, the originating `space_id`, the
`improvement`/`card` id, a `payment` choice blob) -- see
`cards.modified_cost`'s docstring for the full per-kind contract and
`decks/GUIDE.md`. Pass `None` (default) if the caller has nothing to
add (a card-driven call with no originating space, for instance).
`ctx` is ignored when `cost_override` is a dict or `"free"`, same as
the base cost it would otherwise modify.

Validity preview (no mutation)
-------------------------------
Each transaction has a matching `can_*` predicate with the same
cost-handling contract, checking "is this possible in principle" (an
eligible target exists and, unless free, it's affordable) without
picking a specific target -- exactly the granularity `_space_usable`
already uses for the normal action spaces (it doesn't check a specific
cell either; the transaction itself validates the actual target and
raises `ValueError` on an illegal one, which the engine rolls back via
its deepcopy-on-apply). Use these from a `card_action`'s `available` or
a hook's own gating logic; don't try to run the transaction speculatively.

Input channels
--------------
Targets arrive via whichever channel is already appropriate for the
calling context (see CARDS.md/GUIDE.md): a `play` hook's synchronous
`ctx["params"]` (the card's own on-play bonus), a `card_action`'s
`ctx["params"]` (an activated ability -- the right channel for *any*
open-ended target: fence-edge sets, multi-cell room layouts, a chosen
major improvement), or `prompt_choice` for a small enumerable choice
(which occupation in hand, "wood or clay"). Open-ended targets MUST use
the params channel (play or card_action) -- there is no prompt shape for
"pick a set of fence edges".
"""

from server.agricola.state import (
    ANIMAL_TYPES, MAX_PEOPLE, MAX_STABLES, MAX_FENCES, NUM_CELLS,
    TOTAL_ROUNDS, MAJOR_IMPROVEMENTS, FIREPLACES, COOKING_HEARTHS,
    orthogonal_neighbors, compute_pastures, validate_fence_layout,
    validate_animal_placement,
)
from server.agricola import cards

ROOM_COST_MATERIAL = {"wood": "wood", "clay": "clay", "stone": "stone"}
RENOVATION_TARGET = {"wood": "clay", "clay": "stone"}


# ── Generic helpers (shared by several transactions) ─────────────────

def pay(player, cost):
    for res, amount in cost.items():
        if player["resources"][res] < amount:
            raise ValueError(f"Not enough {res}")
    for res, amount in cost.items():
        player["resources"][res] -= amount


def can_afford(player, cost):
    return all(player["resources"][r] >= a for r, a in cost.items())


def _effective_cost(cost_override, normal_cost_fn):
    """cost_override is None|dict|"free". Only calls normal_cost_fn (a
    zero-arg thunk) when actually needed, since it may itself run
    `modified_cost` (a query over every in-play card)."""
    if cost_override == "free":
        return {}
    if isinstance(cost_override, dict):
        return cost_override
    return normal_cost_fn()


def rooms(player):
    return sum(1 for c in player["cells"] if c["type"] == "room")


def stables(player):
    return sum(1 for c in player["cells"] if c["stable"])


def pasture_cells(player):
    return {i for p in compute_pastures(player) for i in p}


def gain_animals(state, player, gained, log):
    """Gained animals must be accommodated before play continues (queues
    or merges into the standard 'accommodate' prompt)."""
    for pr in state["prompts"]:
        if pr["type"] == "accommodate" and pr["player"] == player["index"]:
            for a, n in gained.items():
                pr["gained"][a] = pr["gained"].get(a, 0) + n
            return
    state["prompts"].append(
        {"type": "accommodate", "player": player["index"],
         "gained": dict(gained)})


def apply_extras(state, player, extra, log):
    """Route a ctx["extra"] goods dict to the player: non-animal goods
    directly, animals through gain_animals/accommodation. Fires `gained`
    (source "card") for the credited goods -- this is the single hub
    every hook-granted extra passes through, so it covers space_used
    bonuses, plow/sow/renovate/rooms_built/stable_built/bake extras,
    occupation_played/minor_played/improvement_built/fences_built extras,
    family_growth, and converted extras alike."""
    animals = {}
    credited = {}
    for good, amount in list(extra.items()):
        if amount <= 0:
            continue
        credited[good] = credited.get(good, 0) + amount
        if good in ANIMAL_TYPES:
            animals[good] = animals.get(good, 0) + amount
        else:
            player["resources"][good] += amount
    if animals:
        gain_animals(state, player, animals, log)
    if credited:
        cards.fire_gained(state, player, credited, "card", log)


def fire_any(state, event, actor_player, fields, log):
    """Broadcast the `<event>_any` twin of an owner-only event to every
    player's cards (see CARDS.md)."""
    ctx = {"actor": actor_player["index"], "log": log, "extra": {}}
    ctx.update(fields)
    cards.fire(state, event + "_any", ctx)
    apply_extras(state, actor_player, ctx["extra"], log)


def _fire_owner_and_any(state, event, player, fields, log):
    """The `renovate`/`plow`/`sow`/`rooms_built`/`stable_built`/`bake`
    firing pattern: owner-only first, then the broadcast `_any` twin."""
    ctx = dict(fields)
    ctx["log"] = log
    ctx["actor"] = player["index"]
    ctx["extra"] = {}
    cards.fire_player(state, player, event, ctx)
    apply_extras(state, player, ctx["extra"], log)
    fire_any(state, event, player, fields, log)


_HAND_REACT_EVENTS = ("occupation_played", "minor_played")


def _fire_broadcast(state, event, player, fields, log):
    """Events every player's cards see immediately (not just the owner's
    plus a delayed twin) -- fences_built, improvement_built,
    occupation_played, minor_played.

    For the two card-play events, this is also the single choke point
    for `hand_react` (item 18/engine phase 12: a card still IN HAND
    reacting to another card being played, e.g. E173 Chief's Daughter)
    -- see cards.fire_hand_react and decks/GUIDE.md's "Hand reactions"
    section. Deliberately narrow (not every fire() call) since scanning
    every player's hand on every event would cost more than any card so
    far needs."""
    ctx = dict(fields)
    ctx["log"] = log
    ctx["actor"] = player["index"]
    ctx["extra"] = {}
    cards.fire(state, event, ctx)
    apply_extras(state, player, ctx["extra"], log)
    if event in _HAND_REACT_EVENTS:
        cards.fire_hand_react(state, event, ctx)


# ── Build rooms ────────────────────────────────────────────────────────

def room_cost(state, player, count=1, cost_override=None, ctx=None):
    """Total cost for `count` rooms built in one batch (NOT per-room --
    every cost_mod fn handling kind="room" must scale a flat per-room
    discount by ctx["count"]; see cards.modified_cost's docstring)."""
    material = ROOM_COST_MATERIAL[player["house_type"]]
    full_ctx = dict(ctx or {})
    full_ctx["count"] = count
    return _effective_cost(
        cost_override,
        lambda: cards.modified_cost(state, player, "room",
                                     {material: 5 * count, "reed": 2 * count},
                                     full_ctx))


def buildable_room_cells(player, extra_rooms=()):
    """Empty cells adjacent to an existing room, eligible for a new
    room (not a pasture cell, not already claimed by `extra_rooms`, the
    cells this same batch has already built on)."""
    room_set = {i for i, c in enumerate(player["cells"]) if c["type"] == "room"}
    room_set |= set(extra_rooms)
    pastures = pasture_cells(player)
    eligible = []
    for i, c in enumerate(player["cells"]):
        if i in room_set or c["type"] != "empty" or c["stable"] or i in pastures:
            continue
        if any(nb in room_set for nb in orthogonal_neighbors(i)):
            eligible.append(i)
    return eligible


def can_build_rooms(state, player, cost_override=None, ctx=None):
    if not buildable_room_cells(player):
        return False
    if cost_override == "free":
        return True
    return can_afford(player, room_cost(state, player, 1, cost_override, ctx))


def build_rooms(state, player, cells, log, cost_override=None, ctx=None):
    """Build a room on each of `cells` (in order -- a cell only needs to
    be adjacent to a room that exists *after* the earlier cells in this
    same call are built, matching the normal Farm Expansion action).
    Cells are validated first, then the WHOLE BATCH is paid for as one
    total (see room_cost), then placed -- a card discounting "2+ rooms
    built at once" (A014-style) needs the final count before pricing."""
    built = []
    for cell in cells:
        if not isinstance(cell, int):
            raise ValueError("Invalid room space")
        if cell not in buildable_room_cells(player, built):
            raise ValueError(
                "Rooms must go on empty spaces adjacent to your house")
        built.append(cell)
    if not built:
        raise ValueError("Choose at least one room to build")
    cost = room_cost(state, player, len(built), cost_override, ctx)
    if cost_override != "free":
        pay(player, cost)
    for cell in built:
        player["cells"][cell]["type"] = "room"
    log.append(f"{player['name']} builds {len(built)} {player['house_type']} room(s)")
    _fire_owner_and_any(state, "rooms_built", player, {"cells": built}, log)
    return built


# ── Build stables ──────────────────────────────────────────────────────

def _stable_cost(state, player, cost_override, count, start_index, index, ctx):
    """Cost of ONE stable at 1-based overall position `index` (within a
    batch of `count`, `start_index` stables already built beforehand).
    Unlike rooms/fences, stables are priced one at a time -- per-Nth
    pricing (e.g. "your 3rd and 4th stable cost 1 wood less") can differ
    stable to stable within the same batch."""
    if isinstance(cost_override, dict):
        return cost_override
    full_ctx = dict(ctx or {})
    full_ctx.update({"count": count, "start_index": start_index, "index": index})
    return cards.modified_cost(state, player, "stable", {"wood": 2}, full_ctx)


def stable_possible(state, player, cost_override=None, ctx=None):
    if stables(player) >= MAX_STABLES:
        return False
    if not any(c["type"] == "empty" and not c["stable"] for c in player["cells"]):
        return False
    if cost_override == "free":
        return True
    n = stables(player)
    cost = _stable_cost(state, player, cost_override, 1, n, n + 1, ctx)
    return can_afford(player, cost)


can_build_stables = stable_possible


def build_stables(state, player, cells, log, cost_override=None, ctx=None):
    if not cells:
        raise ValueError("Choose at least one stable to build")
    if len(cells) != len(set(cells)):
        raise ValueError("Duplicate stable spaces")
    start_index = stables(player)
    count = len(cells)
    for i, cell in enumerate(cells):
        if not isinstance(cell, int) or not (0 <= cell < NUM_CELLS):
            raise ValueError("Invalid stable space")
        if stables(player) >= MAX_STABLES:
            raise ValueError(f"You only have {MAX_STABLES} stables")
        c = player["cells"][cell]
        if c["type"] != "empty" or c["stable"]:
            raise ValueError("Stables need an empty space without a tile")
        if cost_override != "free":
            cost = _stable_cost(state, player, cost_override, count,
                                start_index, start_index + i + 1, ctx)
            pay(player, cost)
        c["stable"] = True
    log.append(f"{player['name']} builds {len(cells)} stable(s)")
    _fire_owner_and_any(state, "stable_built", player, {"cells": list(cells)}, log)
    return list(cells)


# ── Build fences ─────────────────────────────────────────────────────

def can_build_fences(state, player, cost_override=None, ctx=None):
    if len(player["fences"]) >= MAX_FENCES:
        return False
    n = 1 if player["fences"] else 4
    if cost_override == "free":
        return True
    full_ctx = dict(ctx or {})
    full_ctx.update({"count": n, "start_index": len(player["fences"])})
    cost = cost_override if isinstance(cost_override, dict) else \
        cards.modified_cost(state, player, "fences", {"wood": n}, full_ctx)
    return can_afford(player, cost)


def build_fences(state, player, new_fences, log, cost_override=None, ctx=None):
    if not new_fences or not isinstance(new_fences, list):
        raise ValueError("Choose fences to build")
    if len(new_fences) != len(set(new_fences)):
        raise ValueError("Duplicate fences")
    for e in new_fences:
        if e in player["fences"]:
            raise ValueError("Fence already built there")
    old_pastures = {tuple(pa) for pa in compute_pastures(player)}
    start_index = len(player["fences"])
    layout = player["fences"] + list(new_fences)
    ok, err, _pastures = validate_fence_layout(player, layout)
    if not ok:
        raise ValueError(err)
    full_ctx = dict(ctx or {})
    full_ctx.update({"count": len(new_fences), "start_index": start_index})
    cost = _effective_cost(
        cost_override,
        lambda: cards.modified_cost(state, player, "fences",
                                     {"wood": len(new_fences)},
                                     full_ctx))
    if cost_override != "free":
        pay(player, cost)
    player["fences"] = sorted(layout)
    log.append(f"{player['name']} builds {len(new_fences)} fence(s)")

    new_pastures = [pa for pa in compute_pastures(player)
                    if tuple(pa) not in old_pastures]
    _fire_broadcast(state, "fences_built", player,
                    {"new_pastures": new_pastures}, log)

    # Subdividing can strand animals; force re-accommodation if so.
    ok, _err = validate_animal_placement(
        player, house_cap=cards.house_capacity(state, player),
        pasture_cap=lambda cells, atype: cards.pasture_capacity(
            state, player, cells, atype),
        unfenced_stable_cap=lambda atype: cards.unfenced_stable_capacity(
            state, player, atype),
        secondary_types=lambda info: cards.pasture_secondary_types(
            state, player, info))
    if not ok and not any(pr["type"] == "accommodate"
                          and pr["player"] == player["index"]
                          for pr in state["prompts"]):
        state["prompts"].append(
            {"type": "accommodate", "player": player["index"], "gained": {}})
        log.append(f"{player['name']} must rearrange their animals")
    return list(new_fences)


# ── Renovate ───────────────────────────────────────────────────────────

def renovation_possible(state, player, cost_override=None, ctx=None):
    target = RENOVATION_TARGET.get(player["house_type"])
    if not target:
        return False
    if cost_override == "free":
        return True
    cost = cost_override if isinstance(cost_override, dict) else \
        cards.modified_cost(state, player, "renovation",
                            {target: rooms(player), "reed": 1}, ctx or {})
    return can_afford(player, cost)


can_renovate = renovation_possible


def renovate(state, player, log, free_stable_cell=None, cost_override=None, ctx=None):
    target = RENOVATION_TARGET.get(player["house_type"])
    if not target:
        raise ValueError("Your house is already stone")
    cost = _effective_cost(
        cost_override,
        lambda: cards.modified_cost(state, player, "renovation",
                                    {target: rooms(player), "reed": 1}, ctx or {}))
    if cost_override != "free":
        pay(player, cost)
    player["house_type"] = target
    log.append(f"{player['name']} renovates to a {target} house")
    _fire_owner_and_any(state, "renovate", player,
                        {"free_stable_cell": free_stable_cell}, log)
    return target


# ── Major improvements ─────────────────────────────────────────────────

def buildable_improvements(state, player, ctx=None):
    """Major improvement ids the player could build right now (at
    normal cost, incl. Fireplace->Cooking Hearth upgrades)."""
    out = []
    owns_fireplace = any(i in FIREPLACES for i in player["improvements"])
    for imp in state["available_improvements"]:
        spec = MAJOR_IMPROVEMENTS[imp]
        full_ctx = dict(ctx or {})
        full_ctx["improvement"] = imp
        cost = cards.modified_cost(state, player, "improvement", spec["cost"], full_ctx)
        if can_afford(player, cost):
            out.append(imp)
        elif imp in COOKING_HEARTHS and owns_fireplace:
            out.append(imp)
    return out


def can_build_improvement(state, player, imp=None, cost_override=None, ctx=None):
    if imp is None:
        if cost_override == "free":
            return bool(state["available_improvements"])
        return bool(buildable_improvements(state, player, ctx))
    if imp not in state["available_improvements"]:
        return False
    if cost_override == "free":
        return True
    full_ctx = dict(ctx or {})
    full_ctx["improvement"] = imp
    cost = cost_override if isinstance(cost_override, dict) else \
        cards.modified_cost(state, player, "improvement",
                            MAJOR_IMPROVEMENTS[imp]["cost"], full_ctx)
    if can_afford(player, cost):
        return True
    owns_fireplace = any(i in FIREPLACES for i in player["improvements"])
    return imp in COOKING_HEARTHS and owns_fireplace


def build_improvement(state, player, imp, log, upgrade=False,
                       cost_override=None, ctx=None):
    if imp not in MAJOR_IMPROVEMENTS:
        raise ValueError("Unknown improvement")
    if imp not in state["available_improvements"]:
        raise ValueError("That improvement is taken")
    spec = MAJOR_IMPROVEMENTS[imp]

    if upgrade:
        if imp not in COOKING_HEARTHS:
            raise ValueError("Only Cooking Hearths can be upgrades")
        fireplace = next((i for i in player["improvements"] if i in FIREPLACES), None)
        if fireplace is None:
            raise ValueError("You need a Fireplace to upgrade")
        player["improvements"].remove(fireplace)
        state["available_improvements"].append(fireplace)
        state["available_improvements"].sort()
        log.append(f"{player['name']} upgrades their Fireplace to a {spec['name']}")
    else:
        full_ctx = dict(ctx or {})
        full_ctx["improvement"] = imp
        cost = _effective_cost(
            cost_override,
            lambda: cards.modified_cost(state, player, "improvement",
                                        spec["cost"], full_ctx))
        if cost_override != "free":
            pay(player, cost)
        log.append(f"{player['name']} builds the {spec['name']}")

    state["available_improvements"].remove(imp)
    player["improvements"].append(imp)

    if spec.get("well"):
        rnd = state["round"]
        for r in range(rnd + 1, min(TOTAL_ROUNDS, rnd + 5) + 1):
            slot = state["round_goods"].setdefault(str(r), {}) \
                .setdefault(str(player["index"]), {})
            slot["food"] = slot.get("food", 0) + 1
        log.append("The Well places food on the next round spaces")

    _fire_broadcast(state, "improvement_built", player, {"improvement": imp}, log)
    return imp


# ── Play occupation / minor improvement ────────────────────────────────

def _add_card_space(state, player, inst):
    """If `inst`'s card declares `card_space` (item 15: a card that IS an
    action space -- Chapel, Forest Inn, Master Forester, ...), append its
    action space to state["action_spaces"] now that the card has entered
    play. Shared by play_occupation and play_minor -- a card_space card
    is never `traveling=True` (asserted at registration in cards.card),
    so both call sites append exactly once, right after the instance
    joins player["occupations"]/["minors"]."""
    card_spec = cards.spec(inst).get("card_space")
    if not card_spec:
        return
    sid = f"card:{inst['id']}"
    # Every card is unique per game (the CARDS registry itself is keyed
    # by id, and a player's hand/in-play cards never repeat one), so this
    # can only fire if _add_card_space were ever called twice for the
    # same instance -- a bug, not a legal game state.
    assert not any(s["id"] == sid for s in state["action_spaces"]), sid
    state["action_spaces"].append({
        "id": sid,
        "name": card_spec.get("name", cards.spec(inst)["name"]),
        "desc": card_spec.get("desc", cards.spec(inst)["text"]),
        "occupied_by": None,
        "extra_occupants": [],
        "supply": {},
        "accumulates": bool(card_spec.get("acc")),
        "card_space": True,
        "card": inst["id"],
        "owner": player["index"],
    })


def lessons_occupation_cost(state, player, space_id):
    """The Lessons/Lessons(3-4p) action spaces' own escalating occupation
    cost (0/1 or 1/2 food, depending on how many occupations this player
    has already played this game, modified by `occ_cost_delta`, then by
    any kind="occupation" cost_mod -- e.g. FR024-style "pay up to 2 food
    less to play an occupation or minor"). A card granting an
    out-of-turn/bonus play should almost always use `play_occupation`'s
    generic 1-food default (`cost_override=None`) instead -- this
    space-specific pricing is only meaningful for the Lessons action
    spaces themselves, or a card that explicitly mirrors them (Junior
    Artist)."""
    if space_id == "lessons":
        base = 0 if player["occs_played"] == 0 else 1
    elif space_id == "lessons_b":
        base = (1 if player["occs_played"] < 2 else 2) \
            if state["player_count"] >= 4 else 2
    else:
        base = 1
    n = max(0, base + cards.occ_cost_delta(player))
    cost = cards.modified_cost(state, player, "occupation", {"food": n},
                               {"space_id": space_id})
    return cost.get("food", 0)


def can_play_occupation(state, player, cid, cost_override=None, ctx=None):
    spec = cards.CARDS.get(cid)
    if not spec or spec["type"] != "occupation":
        return False
    if cid not in player["hand_occupations"]:
        return False
    if not cards.check_prereq(state, player, cid):
        return False
    if cost_override == "free":
        return True
    if isinstance(cost_override, dict):
        cost = cost_override
    else:
        n = max(0, 1 + cards.occ_cost_delta(player))
        full_ctx = dict(ctx or {})
        full_ctx["card"] = cid
        cost = cards.modified_cost(state, player, "occupation", {"food": n}, full_ctx)
    return can_afford(player, cost)


def play_occupation(state, player, cid, log, params=None, cost_override=None, ctx=None):
    """Play an occupation from hand outside the normal Lessons dispatch
    (an out-of-turn/free/discounted play granted by another card, e.g.
    Educator/Scholar/Craft Teacher). `cost_override=None` charges the
    generic 1-food baseline (modified by occ_cost_delta, then any
    kind="occupation" cost_mod) -- pass an explicit dict/`"free"` for
    anything else; the space-specific Lessons pricing (occs_played-
    dependent escalation) is `engine._occupation_cost`/
    `lessons_occupation_cost`, only meaningful for the Lessons/
    Lessons(3-4p) action spaces themselves."""
    if cid not in player["hand_occupations"]:
        raise ValueError("That occupation is not in your hand")
    spec = cards.CARDS[cid]
    if spec["type"] != "occupation":
        raise ValueError("Not an occupation")
    if not cards.check_prereq(state, player, cid):
        raise ValueError(f"Prerequisite not met: {spec['prereq'][1]}")
    if isinstance(cost_override, dict):
        cost = cost_override
    elif cost_override == "free":
        cost = {}
    else:
        n = max(0, 1 + cards.occ_cost_delta(player))
        full_ctx = dict(ctx or {})
        full_ctx["card"] = cid
        cost = cards.modified_cost(state, player, "occupation", {"food": n}, full_ctx)
    if cost_override != "free":
        pay(player, cost)
    player["hand_occupations"].remove(cid)
    inst = cards.new_instance(cid)
    player["occupations"].append(inst)
    player["occs_played"] += 1
    _add_card_space(state, player, inst)
    # Occupations are always priced in food; report the total even when
    # it's 0 (matches the Lessons space's escalating-cost log lines).
    cost_str = "free" if cost_override == "free" else f"{cost.get('food', 0)} food"
    log.append(f"{player['name']} plays the occupation "
              f"\"{spec['name']}\" ({cost_str})")

    play_fn = spec["hooks"].get("play")
    if play_fn:
        ctx = {"params": params or {}, "log": log,
              "actor": player["index"], "extra": {}}
        play_fn(state, player, inst, ctx)
        apply_extras(state, player, ctx["extra"], log)

    _fire_broadcast(state, "occupation_played", player, {"card_id": cid}, log)
    return inst


def can_play_minor(state, player, cid, cost_override=None, ctx=None):
    spec = cards.CARDS.get(cid)
    if not spec or spec["type"] != "minor":
        return False
    if cid not in player["hand_minors"]:
        return False
    if not cards.check_prereq(state, player, cid):
        return False
    if cost_override == "free":
        return True
    if isinstance(cost_override, dict):
        cost = cost_override
    else:
        full_ctx = dict(ctx or {})
        full_ctx["card"] = cid
        cost = cards.modified_cost(state, player, "minor", spec["cost"], full_ctx)
    return can_afford(player, cost)


def play_minor(state, player, cid, log, params=None, cost_override=None, ctx=None):
    if cid not in player["hand_minors"]:
        raise ValueError("That minor improvement is not in your hand")
    spec = cards.CARDS[cid]
    if spec["type"] != "minor":
        raise ValueError("Not a minor improvement")
    if not cards.check_prereq(state, player, cid):
        raise ValueError(f"Prerequisite not met: {spec['prereq'][1]}")
    if isinstance(cost_override, dict):
        cost = cost_override
    elif cost_override == "free":
        cost = {}
    else:
        full_ctx = dict(ctx or {})
        full_ctx["card"] = cid
        cost = cards.modified_cost(state, player, "minor", spec["cost"], full_ctx)
    if cost_override != "free":
        pay(player, cost)
    player["hand_minors"].remove(cid)
    inst = cards.new_instance(cid)
    log.append(f"{player['name']} plays the minor improvement \"{spec['name']}\"")

    play_fn = spec["hooks"].get("play")
    if play_fn:
        ctx = {"params": params or {}, "log": log,
              "actor": player["index"], "extra": {}}
        play_fn(state, player, inst, ctx)
        apply_extras(state, player, ctx["extra"], log)

    if spec["traveling"]:
        if state["player_count"] > 1:
            left = state["players"][(player["index"] + 1) % state["player_count"]]
            left["hand_minors"].append(cid)
            log.append(f"\"{spec['name']}\" travels to {left['name']}'s hand")
        else:
            log.append(f"\"{spec['name']}\" is removed from play (solo)")
    else:
        player["minors"].append(inst)
        _add_card_space(state, player, inst)
    _fire_broadcast(state, "minor_played", player, {"card_id": cid}, log)
    return inst


# ── Sow ──────────────────────────────────────────────────────────────

def empty_fields(player):
    cells = [i for i, c in enumerate(player["cells"])
             if c["type"] == "field" and not c["crops"]]
    card_targets = [i for i in cards.card_fields(player) if not i["crops"]]
    return cells, card_targets


def can_sow(player):
    has_crop = player["resources"]["grain"] > 0 or player["resources"]["vegetable"] > 0
    cells, card_targets = empty_fields(player)
    return has_crop and bool(cells or card_targets)


def sow(state, player, sow_items, log):
    if not isinstance(sow_items, list) or not sow_items:
        raise ValueError("Choose fields to sow")
    seen_cells = set()
    seen_cards = set()
    sown = []
    counts = {"grain": 0, "vegetable": 0}
    for item in sow_items:
        crop = item.get("crop")
        if crop not in ("grain", "vegetable"):
            raise ValueError("Sow grain or vegetables")
        if player["resources"][crop] < 1:
            raise ValueError(f"Not enough {crop}")
        if "card" in item:
            cid = item["card"]
            inst = next((i for i in cards.card_fields(player)
                        if i["id"] == cid), None)
            if inst is None or cid in seen_cards:
                raise ValueError("Invalid card field")
            allowed = cards.CARDS[cid]["field"]["crops"]
            if crop not in allowed:
                raise ValueError(
                    f"{cards.CARDS[cid]['name']} can only grow "
                    + "/".join(allowed))
            if inst["crops"]:
                raise ValueError("That card field is already planted")
            seen_cards.add(cid)
            inst["crops"] = {"type": crop, "count": 3 if crop == "grain" else 2}
            sown.append((inst, crop))
        else:
            cell = item.get("cell")
            if not isinstance(cell, int) or not (0 <= cell < NUM_CELLS) \
                    or cell in seen_cells:
                raise ValueError("Invalid field")
            seen_cells.add(cell)
            c = player["cells"][cell]
            if c["type"] != "field" or c["crops"]:
                raise ValueError("You can only sow empty fields")
            c["crops"] = {"type": crop, "count": 3 if crop == "grain" else 2}
            sown.append((cell, crop))
        player["resources"][crop] -= 1
        counts[crop] += 1
    parts = [f"{v} {k} field(s)" for k, v in counts.items() if v]
    log.append(f"{player['name']} sows {', '.join(parts)}")
    _fire_owner_and_any(state, "sow", player, {"sown": sown}, log)
    return sown


# ── Family growth ──────────────────────────────────────────────────────

def can_family_growth(state, player, require_room=True):
    if player["people_total"] >= MAX_PEOPLE:
        return False
    if require_room:
        return rooms(player) + cards.extra_rooms(state, player) > player["people_total"]
    return True


def family_growth(state, player, log, require_room=True):
    """Add one person to `player`'s family outside the normal Basic/
    Urgent Wish action spaces (e.g. a card granting "family growth
    without a room" or a scheduled/free growth at a later harvest)."""
    if player["people_total"] >= MAX_PEOPLE:
        raise ValueError("You already have 5 people")
    if require_room and rooms(player) + cards.extra_rooms(state, player) \
            <= player["people_total"]:
        raise ValueError("You need more room than people")
    player["people_total"] += 1
    player["people_placed"] += 1  # the newborn does not act this round
    player["newborns"] += 1
    log.append(f"{player['name']}'s family grows by one")
    _fire_broadcast(state, "family_growth", player, {}, log)
