"""
Agricola card registry: occupations and minor improvements.

Architecture: see CARDS.md. Card *specs* (data + pure functions) live here
in the CARDS registry; card *instances* (mutable state) live in the game
state as plain dicts: {"id": ..., "crops": None, "data": {}}.

Hook signature: fn(state, player, card_instance, ctx) -> None.
ctx always carries "log" (list of strings to broadcast). Event-specific
fields are documented at each fire() site in engine.py. Hooks may add goods
to ctx["extra"] where present — the engine merges them (animals are routed
through the accommodation flow).

Text style note: per the CSV dump's README, community card text is
re-templated here into consistent phrasing; cards adopted from the dump are
tagged deck="custom".
"""

import json
import os
import re

from server.agricola.state import (
    ANIMAL_TYPES, TOTAL_ROUNDS, NUM_CELLS,
    SPACE_POSITIONS, ROUND_SLOTS, EXTRA_ADJACENCY,
    pasture_capacity as _base_pasture_capacity,
)

CARDS = {}

# ── Compendium database ──────────────────────────────────────────────
# Every card in the Agricola General Compendium v11.2, parsed by
# tools/parse_compendium.py. Cards present here but not registered in
# CARDS are "known but unimplemented": they appear in the catalog but
# are excluded from deal pools.

_DB_PATH = os.path.join(os.path.dirname(__file__), "data",
                        "compendium_cards.json")
_compendium = None


def compendium():
    """code → compendium entry for all 1,649 compendium cards."""
    global _compendium
    if _compendium is None:
        with open(_DB_PATH) as f:
            _compendium = {c["code"]: c for c in json.load(f)}
    return _compendium


COST_LETTERS = {"W": "wood", "C": "clay", "R": "reed", "S": "stone",
                "F": "food", "G": "grain", "V": "vegetable"}


def parse_cost(cost_str):
    """Parse compendium cost strings like "1W 1S" or "2F" into a goods
    dict. Unknown tokens raise so deck authors notice special costs."""
    cost = {}
    for token in re.split(r"[,\s]+", (cost_str or "").strip()):
        if not token:
            continue
        m = re.match(r"^(\d+)(W|C|R|S|F|G|V)$", token)
        if not m:
            raise ValueError(f"Unparsed cost token: {token!r}")
        cost[COST_LETTERS[m.group(2)]] = \
            cost.get(COST_LETTERS[m.group(2)], 0) + int(m.group(1))
    return cost


def parse_players(players_str):
    """Compendium players strings ("3-5 players", "1+", "4+") →
    min_players for this engine (capped at 4)."""
    m = re.match(r"^(\d)", (players_str or "").strip())
    return min(int(m.group(1)), 4) if m else 1


def card(cid, name, ctype, text, deck="base", min_players=1, cost=None,
         prereq=None, points=0, traveling=False, hooks=None, **abilities):
    """Register a card spec. `prereq` is (predicate, text). Extra keyword
    abilities are static keys read by the modifier queries below (cook,
    bake, raw_values, bake_bonus, cost_mod, occ_cost_delta,
    pasture_capacity_bonus, pasture_capacity_mod, unfenced_stable_capacity_mod,
    pasture_secondary_types, holds_animals, house_capacity, extra_rooms,
    field, bake_on_spaces, lasso, score_bonus, card_space, skip_turn,
    after_skip, placement_blocked)."""
    assert cid not in CARDS, cid
    # A traveling card is handed to the left neighbor (or removed, solo)
    # right after its `play` hook runs and never sits in `player["minors"]`
    # -- there's nowhere for `state["action_spaces"]` to point an "owner"
    # at, so a card_space can never also be traveling (see GUIDE.md).
    assert not (traveling and abilities.get("card_space")), \
        f"{cid}: a traveling card cannot declare card_space"
    CARDS[cid] = {
        "id": cid, "name": name, "type": ctype, "text": text,
        "deck": deck, "min_players": min_players, "cost": cost or {},
        "prereq": prereq, "points": points, "traveling": traveling,
        "hooks": hooks or {}, **abilities,
    }
    return CARDS[cid]


def new_instance(cid):
    return {"id": cid, "crops": None, "data": {}}


def spec(card_instance_or_id):
    cid = card_instance_or_id if isinstance(card_instance_or_id, str) \
        else card_instance_or_id["id"]
    return CARDS[cid]


def in_play(player):
    """All card instances a player has in front of them."""
    return player["occupations"] + player["minors"]


def add_goods(target, goods):
    for k, v in goods.items():
        target[k] = target.get(k, 0) + v


def compendium_card(code, hooks=None, prereq=None, cost=None, points=None,
                    min_players=None, traveling=False, **abilities):
    """Register a card from the compendium DB by its code. Name, text,
    cost, points, and player count default to the parsed compendium
    entry; pass overrides only where the parse needs correction."""
    entry = compendium()[code]
    return card(
        code,
        entry["name"],
        entry["type"],
        entry["text"],
        deck=entry["deck"],
        min_players=(min_players if min_players is not None
                     else parse_players(entry.get("players"))),
        cost=cost if cost is not None else parse_cost(entry.get("cost")),
        prereq=prereq,
        points=points if points is not None else entry.get("vp", 0),
        traveling=traveling or "pass it to the player on your left"
        in entry["text"],
        hooks=hooks,
        edition=entry.get("edition"),
        **abilities,
    )


# ── Prompts (mid-effect choices) ─────────────────────────────────────

def prompt_choice(state, player, card_id, prompt, options, data=None,
                  from_hand=False):
    """Queue a blocking choice for `player`. The card's spec must define
    resolve_choice(state, player, inst, ctx) — ctx carries "index" (the
    chosen option), "data", "log", and "extra". `from_hand=True` marks a
    choice about a card still sitting in `player`'s hand (an E173-style
    `hand_react` prompt, e.g. "play this card now at no cost?") rather
    than one about a card already in play -- there is no instance yet,
    so `engine._apply_choice` falls back to the CARDS registry spec and
    calls resolve_choice with inst=None (see decks/GUIDE.md's "Hand
    reactions" section for the full contract)."""
    entry = {
        "type": "choice",
        "player": player["index"] if isinstance(player, dict) else player,
        "card": card_id,
        "prompt": prompt,
        "options": list(options),
        "data": data or {},
    }
    if from_hand:
        entry["from_hand"] = True
    state["prompts"].append(entry)


# ── Event firing ─────────────────────────────────────────────────────

def fire(state, event, ctx):
    """Fire an event to every in-play card of every player, actor first."""
    n = state["player_count"]
    actor = ctx.get("actor", 0)
    for step in range(n):
        p = state["players"][(actor + step) % n]
        for inst in list(in_play(p)):
            fn = spec(inst)["hooks"].get(event)
            if fn:
                fn(state, p, inst, ctx)


def fire_player(state, player, event, ctx):
    """Fire an event to one player's in-play cards only."""
    for inst in list(in_play(player)):
        fn = spec(inst)["hooks"].get(event)
        if fn:
            fn(state, player, inst, ctx)


def fire_hand_react(state, event, ctx):
    """Scan every player's HAND (occupations then minors), actor first,
    for a card spec declaring `hand_react = {"event": <name>, "fn": fn}`
    matching `event`, calling `fn(state, hand_player, ctx)` for each
    match. Unlike fire()/fire_player(), there is no card instance (the
    card hasn't been played yet), so `fn` gets no `inst` argument --
    E173-style reactions ("if another player plays X, you may play this
    from hand") typically call `fn(state, hand_player, ctx)` -> check
    `ctx["card_id"]`/`ctx["actor"]` and, if it matches, `prompt_choice(...,
    from_hand=True)`.

    Only wired to the two broadcast card-play events (occupation_played,
    minor_played) from sub_actions._fire_broadcast -- not a general
    per-event hook (scanning every hand on every fire() would cost more
    than any card so far needs; see decks/GUIDE.md's "Hand reactions"
    section)."""
    n = state["player_count"]
    actor = ctx.get("actor", 0)
    for step in range(n):
        p = state["players"][(actor + step) % n]
        for cid in list(p["hand_occupations"]) + list(p["hand_minors"]):
            s = CARDS.get(cid)
            hr = s.get("hand_react") if s else None
            if hr and hr.get("event") == event:
                hr["fn"](state, p, ctx)


# ── Modifier queries (pull) ──────────────────────────────────────────

def modified_cost(state, player, kind, cost, ctx=None):
    """Fold every in-play card's cost_mod over a base cost dict.

    `kind` is one of "room", "stable", "fences", "renovation",
    "improvement", "minor", "occupation". `ctx` is whichever of the
    following the caller has available (any subset; a cost_mod fn must
    use `.get()` defensively since unrelated kinds won't populate all of
    them):

    - "count": items in this batch (rooms/fences; stables also get a
      per-stable call, see below).
    - "start_index": how many of that thing the player already had
      *before* this batch (fences already built, stables already
      built) -- lets a "your Nth item" card compute overlap with
      [start_index+1 .. start_index+count]. Rooms don't carry this
      (room count is a lifetime total, not slotted).
    - "index": for kind="stable" only -- the 1-based overall index of
      THIS stable within the player's stable history (start_index plus
      its position in the batch); stables are priced one at a time
      (per-Nth pricing can differ stable to stable) so each call gets
      its own index, unlike rooms/fences which are priced as one
      batch total.
    - "space_id": the originating action-space id, when the build came
      from an action space (threaded from engine._resolve_space);
      absent/None for a card-driven build (card_action, or a hook
      calling a sub_actions transaction directly with no space).
    - "improvement": the major-improvement id, for kind="improvement".
    - "card": the card id, for kind="minor".
    - "payment": the raw payment-choice value from the client action
      dict (opaque -- the card's own cost_mod decides the schema, e.g.
      {"reed_to_clay": 2}). A cost_mod that consumes ctx["payment"]
      must validate it and raise ValueError on garbage.

    Rooms and fences are priced as a BATCH TOTAL (the base `cost` passed
    in is already `count`x the per-item amount) -- a cost_mod expressing
    a flat per-item discount must scale its subtraction by
    `ctx.get("count", 1)` to keep a fixed discount per item over the
    whole batch (see decks/GUIDE.md)."""
    cost = dict(cost)
    for inst in in_play(player):
        fn = spec(inst).get("cost_mod")
        if fn:
            cost = fn(state, player, kind, cost, ctx or {})
    return {k: v for k, v in cost.items() if v > 0}


def occ_cost_delta(player):
    return sum(spec(i).get("occ_cost_delta", 0) for i in in_play(player))


def pasture_bonus(player):
    """Extra capacity for each pasture (additive across cards). This is
    the flat, type-agnostic `pasture_capacity_bonus` key only (e.g.
    Drinking Trough) -- folded into `pasture_capacity` below, which is
    what callers should use; kept as a standalone query since a few
    cards' own logic (scoring heuristics, best-effort placement) still
    reads the flat bonus directly."""
    return sum(spec(i).get("pasture_capacity_bonus", 0) for i in in_play(player))


def _pasture_info(state, player, pasture_cells, animal_type):
    stables = sum(1 for i in pasture_cells if player["cells"][i]["stable"])
    return {"cells": list(pasture_cells), "size": len(pasture_cells),
            "stables": stables, "animal_type": animal_type}


def pasture_capacity(state, player, pasture_cells, animal_type=None):
    """Capacity of one specific pasture (`pasture_cells`, a sorted list
    of cell indices) for `animal_type` (None when checking type-
    agnostically, e.g. an empty pasture). Folds together: the base
    geometry (`state.pasture_capacity` -- 2 per cell, doubled per stable
    inside), the flat `pasture_capacity_bonus` (existing cards, e.g.
    Drinking Trough), and every in-play card's `pasture_capacity_mod=
    fn(state, player, inst, info) -> int` (a per-pasture-size and/or
    type-conditioned delta -- e.g. D011 Lawn Fertilizer: "+1 in size-1
    pastures", E29 Shepherd's Pipe: "+2 where you keep sheep"). `info`
    is `{"cells", "size", "stables", "animal_type"}`."""
    info = _pasture_info(state, player, pasture_cells, animal_type)
    bonus = pasture_bonus(player)
    for inst in in_play(player):
        fn = spec(inst).get("pasture_capacity_mod")
        if fn:
            bonus += fn(state, player, inst, info)
    return _base_pasture_capacity(player, pasture_cells, bonus)


def unfenced_stable_capacity(state, player, animal_type):
    """Capacity of an unfenced stable for `animal_type`. Base 1, plus
    every in-play card's `unfenced_stable_capacity_mod=fn(state, player,
    inst, animal_type) -> int` (e.g. E29 Shepherd's Pipe: "up to 2 sheep
    in each unfenced stable")."""
    cap = 1
    for inst in in_play(player):
        fn = spec(inst).get("unfenced_stable_capacity_mod")
        if fn:
            cap += fn(state, player, inst, animal_type)
    return cap


def pasture_secondary_types(state, player, info):
    """Animal types (other than `info["animal_type"]`) permitted
    alongside it in the same pasture, and the max of each -- e.g. FR013
    Chameleon: "1 wild boar in each pasture that holds sheep". Merges
    every in-play card's `pasture_secondary_types=fn(state, player,
    inst, info) -> {type: max_count}` (max per type across cards). The
    secondary animals still count against the pasture's own total
    `pasture_capacity` -- this only grants permission to mix types, not
    extra room."""
    allowed = {}
    for inst in in_play(player):
        fn = spec(inst).get("pasture_secondary_types")
        if fn:
            for t, n in fn(state, player, inst, info).items():
                allowed[t] = max(allowed.get(t, 0), n)
    return allowed


def holder_cards(state, player):
    """In-play card instances that can hold animals on themselves (spec
    key `holds_animals`) -- e.g. C012 Cattle Farm, E58 Animal Yard."""
    return [i for i in in_play(player) if spec(i).get("holds_animals")]


def validate_held(state, player):
    """Check every holder card's `inst["held"]` against its own caps
    (`holds_animals=fn(state, player, inst) -> {"types": {type:
    max_or_None}?, "total": max_or_None?}`; missing "types" = any type
    allowed, None = unlimited). Returns (ok, error_message)."""
    for inst in in_play(player):
        fn = spec(inst).get("holds_animals")
        if not fn:
            continue
        held = inst.get("held") or {}
        if any(n < 0 for n in held.values()):
            return False, "Invalid held animals"
        rule = fn(state, player, inst) or {}
        types = rule.get("types")
        if types is not None:
            for t, n in held.items():
                if t not in types:
                    return False, f"{spec(inst)['name']} cannot hold {t}"
                cap = types[t]
                if cap is not None and n > cap:
                    return False, (f"{spec(inst)['name']} holds at most "
                                   f"{cap} {t}")
        total_cap = rule.get("total")
        if total_cap is not None and sum(held.values()) > total_cap:
            return False, f"{spec(inst)['name']} is over capacity"
    return True, ""


def house_capacity(state, player):
    """How many animals the house holds (pets). Base 1, plus each
    in-play card's `house_capacity` (a flat int, `"per_room"`, or
    `fn(state, player, inst) -> int` for a computed delta -- e.g. a
    "occupations you have" scaled bonus)."""
    cap = 1
    per_room = False
    for inst in in_play(player):
        hc = spec(inst).get("house_capacity")
        if hc == "per_room":
            per_room = True
        elif callable(hc):
            cap += hc(state, player, inst)
        elif isinstance(hc, int):
            cap += hc
    if per_room:
        rooms = sum(1 for c in player["cells"] if c["type"] == "room")
        cap = max(cap, rooms)
    return cap


def extra_rooms(state, player):
    """Card-provided room for people (family-growth room check). Each
    in-play card's `extra_rooms` is a flat int (unchanged) or `fn(state,
    player, inst) -> int` for a computed value -- e.g. D085 Reader:
    "room for one person once you have 6 occupations in play"."""
    total = 0
    for i in in_play(player):
        val = spec(i).get("extra_rooms", 0)
        total += val(state, player, i) if callable(val) else val
    return total


def raw_values(player):
    """Best at-any-time raw conversion rate for crops (base 1 food each)."""
    best = {"grain": 1, "vegetable": 1}
    for inst in in_play(player):
        for good, val in spec(inst).get("raw_values", {}).items():
            best[good] = max(best[good], val)
    return best


def card_cook_specs(player):
    """Cook tables provided by in-play cards (same shape as improvements)."""
    return [spec(i)["cook"] for i in in_play(player) if spec(i).get("cook")]


def bake_bonus(player, grain_baked):
    """Extra food from cards when baking (per-grain and flat bonuses)."""
    extra = 0
    for inst in in_play(player):
        extra += spec(inst).get("bake_bonus_per_grain", 0) * grain_baked
        if grain_baked > 0:
            extra += spec(inst).get("bake_bonus_flat", 0)
    return extra


def bake_on_space(player, space_id):
    return any(space_id in spec(i).get("bake_on_spaces", ())
               for i in in_play(player))


def has_lasso(player):
    return any(spec(i).get("lasso") for i in in_play(player))


def grant_guest(player, n=1):
    """Grant `player` n extra work-phase placement(s) ("guest tokens")
    for the *current* round. Consumed by the placement flow (folded into
    the same capacity as people_total, but never written to people_total
    itself, so feeding/scoring/family-growth -- all of which read
    people_total directly -- are unaffected). Reset to 0 by every
    _start_round before that round's round_start hooks fire, so an
    unused guest never carries over and a round_start hook may grant one
    for the round it is firing in."""
    player["guests"] = player.get("guests", 0) + n


def occupied_ok(state, player, space):
    """True if some in-play card lets `player` place a person on `space`
    even though it is already occupied by (an)other player(s). Must be a
    pure predicate: it is evaluated on every get_valid_actions call (not
    only on an actual placement), so a card's `occupied_ok` fn must not
    mutate state -- restrictions like "once per round" or "only the 3rd
    person you place" need to be expressed as a check against existing
    state (space id, current occupants, player["people_placed"], round
    number, ...), not as a side effect. Card-local flags in
    inst["data"] may still be *read* here."""
    for inst in in_play(player):
        fn = spec(inst).get("occupied_ok")
        if fn and fn(state, player, inst, space):
            return True
    return False


def placement_blocked(state, player):
    """True if any in-play card's `placement_blocked=fn(state, player,
    inst) -> bool` currently blocks `player` from placing anyone this
    round (I71 Holiday House: "In round 14, you cannot place any
    people."). `_placement_actions` and `_skip_actions` both return []
    for a blocked player, so `_advance_work`'s existing forfeit branch
    (no usable space, no skip option) picks them up automatically."""
    for inst in in_play(player):
        fn = spec(inst).get("placement_blocked")
        if fn and fn(state, player, inst):
            return True
    return False


def card_space_owner(state, inst):
    """The player who played the card behind a `card_space` action space
    (`state["action_spaces"]`'s `id="card:<cid>"` entry's `owner` index).
    A card_space's `resolve`/`usable` fn only receives the PLACING player
    (who may not be the owner), so this is how it finds whoever should be
    paid a toll or credited a bonus -- see GUIDE.md's `card_space` section."""
    space = next(s for s in state["action_spaces"]
                 if s["id"] == f"card:{inst['id']}")
    return state["players"][space["owner"]]


def space_position(state, space_id):
    """(col, row) of `space_id` on the physical board, or None if it has
    no position -- a `card_space` ("card:<cid>", it sits beside the
    board), or an id not on this player count's board at all. Permanent
    spaces come from the static SPACE_POSITIONS map (keyed by
    state["player_count"]); a revealed round space's position comes
    from its ROUND_SLOTS entry, keyed by which ROUND revealed it (its
    index in state["revealed"]), not by which stage card it is -- see
    state.py's "Board geometry" comment and GUIDE.md for the derivation
    and fidelity caveats."""
    positions = SPACE_POSITIONS.get(state["player_count"], {})
    if space_id in positions:
        return positions[space_id]
    if space_id in state["revealed"]:
        rnd = state["revealed"].index(space_id) + 1
        return ROUND_SLOTS.get(rnd)
    return None


def adjacent_spaces(state, space_id):
    """Ids of action spaces EXISTING right now (in state["action_spaces"])
    that are orthogonally adjacent (grid distance 1) to `space_id`,
    plus any EXTRA_ADJACENCY override pairs (printed-board adjacencies
    the single-cell grid can't express -- Grove/Farm Expansion). A
    space with no position (see space_position) has no grid neighbors,
    and a round slot not revealed yet is simply absent from the search --
    it's not in state["action_spaces"] to be found."""
    extra = set()
    for a, b in EXTRA_ADJACENCY.get(state["player_count"], ()):
        if a == space_id:
            extra.add(b)
        elif b == space_id:
            extra.add(a)
    pos = space_position(state, space_id)
    if pos is None:
        targets = set()
    else:
        col, row = pos
        targets = {(col - 1, row), (col + 1, row), (col, row - 1), (col, row + 1)}
    return [s["id"] for s in state["action_spaces"]
            if s["id"] != space_id
            and (s["id"] in extra
                 or space_position(state, s["id"]) in targets)]


def spaces_adjacent(state, a, b):
    """True if action spaces `a` and `b` are orthogonally adjacent."""
    return b in adjacent_spaces(state, a)


def left_neighbor(state, space_id):
    """The existing action space directly to the left of `space_id`
    (same row, column - 1), or None -- B120 Sweep's recipe: pass
    state["revealed"][-1] (the round space most recently placed)."""
    pos = space_position(state, space_id)
    if pos is None:
        return None
    col, row = pos
    target = (col - 1, row)
    return next((s["id"] for s in state["action_spaces"]
                 if space_position(state, s["id"]) == target), None)


def card_fields(player):
    """In-play minor-improvement instances that are fields."""
    return [i for i in player["minors"] if spec(i).get("field")]


def keep_crops_on_harvest(state, player):
    """Crop types (a subset of "grain"/"vegetable") that the field phase
    should count and credit as usual but NOT decrement from the field --
    "you keep the vegetables on the fields" (I226-style). Union of every
    in-play card's `keep_crops_on_harvest` spec key: a static tuple/set of
    crop types, or fn(state, player, inst) -> iterable of crop types."""
    keep = set()
    for inst in in_play(player):
        val = spec(inst).get("keep_crops_on_harvest")
        if val is None:
            continue
        keep.update(val(state, player, inst) if callable(val) else val)
    return keep


def score_bonuses(state, player):
    total = 0
    for inst in in_play(player):
        fn = spec(inst).get("score_bonus")
        if fn:
            total += fn(state, player, inst)
    return total


def printed_points(player):
    return sum(spec(i)["points"] for i in in_play(player))


# ── Prerequisite helpers ─────────────────────────────────────────────

def needs_occupations(n):
    return (lambda s, p: len(p["occupations"]) >= n, f"{n} occupations")


def exact_occupations(n):
    return (lambda s, p: len(p["occupations"]) == n, f"exactly {n} occupations")


def needs_grain_field(n=1):
    def ok(s, p):
        fields = sum(1 for c in p["cells"]
                     if c["crops"] and c["crops"]["type"] == "grain")
        fields += sum(1 for i in card_fields(p)
                      if i["crops"] and i["crops"]["type"] == "grain")
        return fields >= n
    return (ok, f"{n} grain field(s)")


def combine(*prereqs):
    fns = [p[0] for p in prereqs]
    return (lambda s, p: all(f(s, p) for f in fns),
            " and ".join(p[1] for p in prereqs))


def check_prereq(state, player, cid):
    pr = CARDS[cid]["prereq"]
    return pr is None or pr[0](state, player)


# ── Hook factories ───────────────────────────────────────────────────

def _queue_accommodation(state, player, animals):
    """Queue an accommodation prompt for `player` for newly gained animal
    goods. Used when a card grants animals to a player who is not the
    current actor, so there is no ctx["extra"] to stash them in (mirrors
    the engine's own _gain_animals, which per-card hooks can't call
    directly)."""
    for pr in state["prompts"]:
        if pr["type"] == "accommodate" and pr["player"] == player["index"]:
            add_goods(pr["gained"], animals)
            return
    state["prompts"].append({"type": "accommodate", "player": player["index"],
                             "gained": dict(animals)})


def fire_gained(state, player, goods, source, log, space_id=None):
    """Fire the `gained` event: `goods` -- which may include animal types,
    already credited to `player` by the caller (resources incremented, or
    animals queued for accommodation) -- were obtained from `source`
    ("space", "card", "harvest", "bake", "convert", "round_goods",
    "breeding"). Owner-only (fire_player): every target card says "each
    time YOU obtain...". Skips firing entirely if `goods` is empty or all
    amounts are <= 0.

    A hook may add to ctx["extra"]; that's credited the same way (resources
    directly, animals via the accommodation queue) and then re-fired as a
    chained gained(source="card") so "each time you gain wood, get 1 food"
    itself notifies further hooks -- guarded by a depth counter on `state`
    so a pathological "each time you gain food, get 1 food" card can't
    loop forever: at depth >= 3 the goods are still credited (by the
    caller, one level up) but this call returns without firing or
    chaining further."""
    positive = {g: v for g, v in goods.items() if v and v > 0}
    if not positive:
        return
    depth = state.get("_gained_depth", 0)
    if depth >= 3:
        return
    ctx = {"goods": dict(positive), "source": source, "log": log,
           "actor": player["index"], "extra": {}}
    if space_id is not None:
        ctx["space_id"] = space_id
    state["_gained_depth"] = depth + 1
    try:
        fire_player(state, player, "gained", ctx)
        animals = {g: v for g, v in ctx["extra"].items()
                  if v > 0 and g in ANIMAL_TYPES}
        non_animals = {g: v for g, v in ctx["extra"].items()
                      if v > 0 and g not in ANIMAL_TYPES}
        if non_animals:
            add_goods(player["resources"], non_animals)
        if animals:
            _queue_accommodation(state, player, animals)
        chained = {**non_animals, **animals}
        if chained:
            fire_gained(state, player, chained, "card", log)
    finally:
        state["_gained_depth"] = depth


def grant_goods(state, player, gain, log=None):
    """Credit `gain` directly to `player`. Non-animal goods go straight
    into player["resources"]; animal goods ("sheep", "boar", "cattle")
    never live there, so they are routed through the normal accommodation
    prompt instead. Use this (instead of add_goods(player["resources"], ...))
    whenever the recipient is not the current actor -- e.g. an "each time
    ANY player does X, the card owner gets Y" effect -- so an animal gain
    can't silently corrupt state. Fires `gained` (source "card") for the
    credited goods."""
    log = log if log is not None else []
    animals = {g: v for g, v in gain.items() if g in ANIMAL_TYPES}
    goods = {g: v for g, v in gain.items() if g not in ANIMAL_TYPES}
    add_goods(player["resources"], goods)
    if any(animals.values()):
        _queue_accommodation(state, player, animals)
    fire_gained(state, player, gain, "card", log)


def take_bonus(goods_watched, gain):
    """Each time YOU take any of `goods_watched` from an action space."""
    def hook(state, player, inst, ctx):
        if ctx["actor"] != player["index"]:
            return
        if any(ctx["goods"].get(g) for g in goods_watched):
            add_goods(ctx["extra"], gain)
            ctx["log"].append(f"{player['name']}'s {spec(inst)['name']} adds "
                              + goods_str(gain))
    return {"space_used": hook}


def space_bonus(space_ids, gain, others=False):
    """Each time you (or, with others=True, ANY player) use given spaces."""
    def hook(state, player, inst, ctx):
        mine = ctx["actor"] == player["index"]
        if ctx["space_id"] not in space_ids or (not others and not mine):
            return
        if others and not mine:
            # Goods for the card owner, not the actor (animals route
            # through accommodation -- see grant_goods).
            grant_goods(state, player, gain, ctx["log"])
            ctx["log"].append(f"{player['name']}'s {spec(inst)['name']} grants "
                              + goods_str(gain))
        else:
            add_goods(ctx["extra"], gain)
            ctx["log"].append(f"{player['name']}'s {spec(inst)['name']} adds "
                              + goods_str(gain))
    return {"space_used": hook}


def round_income(gain, condition=None):
    """At the start of each round, get `gain` (if condition(state, p))."""
    def hook(state, player, inst, ctx):
        if condition and not condition(state, player):
            return
        add_goods(player["resources"], gain)
        ctx["log"].append(f"{player['name']}'s {spec(inst)['name']} grants "
                          + goods_str(gain))
        fire_gained(state, player, gain, "card", ctx["log"])
    return {"round_start": hook}


def schedule_on_play(good, rounds_ahead=None, fixed_rounds=None, amount=1):
    """On play: place goods on future round spaces (Well-style)."""
    def hook(state, player, inst, ctx):
        rnd = state["round"]
        if fixed_rounds is not None:
            targets = [r for r in fixed_rounds if r > rnd]
        else:
            targets = list(range(rnd + 1,
                                 min(TOTAL_ROUNDS, rnd + rounds_ahead) + 1))
        for r in targets:
            slot = state["round_goods"].setdefault(str(r), {}) \
                .setdefault(str(player["index"]), {})
            add_goods(slot, {good: amount})
        if targets:
            ctx["log"].append(
                f"{spec(inst)['name']} places {amount} {good} on "
                f"round spaces {', '.join(map(str, targets))}")
    return {"play": hook}


def harvest_food(fn, label=None):
    """In the field phase of each harvest, +fn(state, player) food."""
    def hook(state, player, inst, ctx):
        amount = fn(state, player)
        if amount > 0:
            player["resources"]["food"] += amount
            ctx["log"].append(
                f"{player['name']}'s {spec(inst)['name']} provides {amount} food")
            fire_gained(state, player, {"food": amount}, "card", ctx["log"])
    return {"harvest_field": hook}


def on_play_gain(gain):
    """On play: grant `gain` to the player playing the card. Animal goods
    go through ctx["extra"] (the engine routes them to accommodation);
    other goods are credited directly, firing `gained` (source "card") for
    that part immediately (the animal part's `gained` fires once the
    caller's apply_extras credits it)."""
    def hook(state, player, inst, ctx):
        animals = {g: v for g, v in gain.items() if g in ANIMAL_TYPES}
        goods = {g: v for g, v in gain.items() if g not in ANIMAL_TYPES}
        add_goods(player["resources"], goods)
        add_goods(ctx["extra"], animals)
        ctx["log"].append(f"{player['name']} gets " + goods_str(gain))
        fire_gained(state, player, goods, "card", ctx["log"])
    return hook


def goods_str(goods):
    return ", ".join(f"{v} {k}" for k, v in goods.items() if v)


def animal_totals_of(player):
    totals = {t: 0 for t in ANIMAL_TYPES}
    for c in player["cells"]:
        if c.get("animal"):
            totals[c["animal"]["type"]] += c["animal"]["count"]
    for t, n in player.get("pets", {}).items():
        totals[t] += n
    return totals


# ═════════════════════════════════════════════════════════════════════
# OCCUPATIONS
# ═════════════════════════════════════════════════════════════════════

# ── Take-goods bonuses (factory) ─────────────────────────────────────
card("occ_woodcutter", "Woodcutter", "occupation",
     "Each time you take wood from an action space, you get 1 additional wood.",
     hooks=take_bonus(["wood"], {"wood": 1}))

card("occ_reed_collector", "Reed Collector", "occupation",
     "Each time you take reed from an action space, you get 1 additional reed.",
     hooks=take_bonus(["reed"], {"reed": 1}))

card("occ_clay_digger", "Clay Digger", "occupation",
     "Each time you take clay from an action space, you get 1 additional clay.",
     hooks=take_bonus(["clay"], {"clay": 1}))

card("occ_shepherd", "Shepherd", "occupation", min_players=3,
     text="Each time you take sheep from an action space, you get 1 additional sheep.",
     hooks=take_bonus(["sheep"], {"sheep": 1}))

card("occ_pig_breeder", "Pig Breeder", "occupation", min_players=3,
     text="Each time you take wild boar from an action space, you get 1 additional wild boar.",
     hooks=take_bonus(["boar"], {"boar": 1}))

card("occ_cattle_feeder", "Cattle Feeder", "occupation", min_players=4,
     text="Each time you take cattle from an action space, you get 1 additional cattle.",
     hooks=take_bonus(["cattle"], {"cattle": 1}))

# ── Space-use bonuses (factory) ──────────────────────────────────────
card("occ_fisherman", "Fisherman", "occupation",
     "Each time you use the \"Fishing\" action space, you get 2 additional food.",
     hooks=space_bonus(["fishing"], {"food": 2}))

card("occ_firewood_collector", "Firewood Collector", "occupation",
     "Each time you use the \"Grain Seeds\", \"Farmland\", \"Grain Utilization\", "
     "or \"Cultivation\" action space, you also get 1 wood.",
     hooks=space_bonus(["grain_seeds", "farmland", "grain_utilization",
                        "cultivation"], {"wood": 1}))

card("occ_seasonal_worker", "Seasonal Worker", "occupation",
     "Each time you use the \"Day Laborer\" action space, you also get 1 grain.",
     hooks=space_bonus(["day_laborer"], {"grain": 1}))

card("occ_greengrocer", "Greengrocer", "occupation",
     "Each time you use the \"Grain Seeds\" action space, you also get 1 vegetable.",
     hooks=space_bonus(["grain_seeds"], {"vegetable": 1}))

card("occ_forager", "Forager", "occupation",
     "Each time you use the \"Forest\" accumulation space, you also get 1 food.",
     hooks=space_bonus(["forest"], {"food": 1}))

card("occ_quarryman", "Quarryman", "occupation", min_players=3,
     text="Each time you use the \"Western Quarry\" or \"Eastern Quarry\" "
     "accumulation space, you also get 1 food.",
     hooks=space_bonus(["western_quarry", "eastern_quarry"], {"food": 1}))

card("occ_grain_farmer", "Grain Farmer", "occupation", min_players=3,
     text="Each time you use the \"Grain Seeds\" action space, you get 1 additional grain.",
     hooks=space_bonus(["grain_seeds"], {"grain": 1}))

card("occ_conjurer", "Conjurer", "occupation", min_players=4,
     text="Each time you use the \"Traveling Players\" accumulation space, "
     "you also get 1 wood and 1 grain.",
     hooks=space_bonus(["traveling_players"], {"wood": 1, "grain": 1}))

card("occ_market_crier", "Market Crier", "occupation", min_players=4,
     text="Each time you use the \"Vegetable Seeds\" action space, you get "
     "1 additional vegetable.",
     hooks=space_bonus(["vegetable_seeds"], {"vegetable": 1}))

# ── Round income ─────────────────────────────────────────────────────
card("occ_small_scale_farmer", "Small-Scale Farmer", "occupation",
     "As long as you live in a house with exactly 2 rooms, you get 1 wood "
     "at the start of each round.",
     hooks=round_income({"wood": 1},
                        condition=lambda s, p: sum(
                            1 for c in p["cells"] if c["type"] == "room") == 2))

card("occ_water_carrier", "Water Carrier", "occupation", min_players=4,
     text="At the start of each round, you get 1 food.",
     hooks=round_income({"food": 1}))

# ── Cost modifiers ───────────────────────────────────────────────────
def _stonecutter_mod(state, player, kind, cost, ctx):
    if kind in ("room", "renovation", "improvement") and cost.get("stone"):
        cost = dict(cost)
        # "room" is priced as a batch total; scale the flat 1-stone
        # discount by the room count so it still applies once per room.
        n = ctx.get("count", 1) if kind == "room" else 1
        cost["stone"] -= n
    return cost

card("occ_stonecutter", "Stonecutter", "occupation",
     "Every room, renovation, and major improvement costs you 1 stone less.",
     cost_mod=_stonecutter_mod)


def _carpenter_mod(state, player, kind, cost, ctx):
    if kind == "room":
        cost = dict(cost)
        # Room cost is a batch total; a fixed -2 per room becomes
        # -2*count over the whole batch.
        discount = 2 * ctx.get("count", 1)
        for material in ("wood", "clay", "stone"):
            if cost.get(material):
                cost[material] = max(0, cost[material] - discount)
    return cost

card("occ_carpenter", "Carpenter", "occupation",
     "Each new room costs you only 3 of the appropriate building resource "
     "(and 2 reed).",
     cost_mod=_carpenter_mod)


def _roofer_mod(state, player, kind, cost, ctx):
    if kind == "renovation" and cost.get("reed"):
        cost = dict(cost)
        cost["reed"] = 0
    return cost

card("occ_roofer", "Roofer", "occupation", min_players=3,
     text="When you renovate, you do not need to pay reed.",
     cost_mod=_roofer_mod)


def _hedge_keeper_mod(state, player, kind, cost, ctx):
    if kind == "fences":
        cost = dict(cost)
        free = min(3, ctx.get("count", 0))
        cost["wood"] = max(0, cost.get("wood", 0) - free)
    return cost

card("occ_hedge_keeper", "Hedge Keeper", "occupation",
     "Each time you build fences, up to 3 of them are free.",
     cost_mod=_hedge_keeper_mod)

card("occ_tutor", "Tutor", "occupation",
     "Each occupation you play after this one costs you 1 food less.",
     occ_cost_delta=-1)

# ── Capacity / conversion statics ────────────────────────────────────
card("occ_animal_tamer", "Animal Tamer", "occupation",
     "Instead of just 1 animal total, you can keep 1 animal in each room "
     "of your house.",
     house_capacity="per_room")

card("occ_cook", "Cook", "occupation",
     "At any time, you can convert grain to 2 food and vegetables to 3 food.",
     raw_values={"grain": 2, "vegetable": 3})

card("occ_baker", "Baker", "occupation",
     "Each time you bake bread, you get 1 additional food per grain baked.",
     bake_bonus_per_grain=1)


def _stable_hand_hook(state, player, inst, ctx):
    gain = {"food": len(ctx["cells"])}
    add_goods(player["resources"], gain)
    ctx["log"].append(f"{player['name']}'s Stable Hand grants "
                      f"{len(ctx['cells'])} food")
    fire_gained(state, player, gain, "card", ctx["log"])

card("occ_stable_hand", "Stable Hand", "occupation", min_players=4,
     text="Each time you build one or more stables, you also get 1 food per stable.",
     hooks={"stable_built": _stable_hand_hook})

# ── Scoring occupations ──────────────────────────────────────────────
def _braggart_score(state, player, inst):
    n = len(player["improvements"]) + len(player["minors"])
    for minimum, pts in ((10, 9), (9, 7), (8, 5), (7, 4), (6, 3), (5, 2)):
        if n >= minimum:
            return pts
    return 0

card("occ_braggart", "Braggart", "occupation",
     "During scoring, you get 2/3/4/5/7/9 bonus points for having at least "
     "5/6/7/8/9/10 improvements in front of you.",
     score_bonus=_braggart_score)


def _house_steward_score(state, player, inst):
    my_rooms = sum(1 for c in player["cells"] if c["type"] == "room")
    most = max(sum(1 for c in p["cells"] if c["type"] == "room")
               for p in state["players"])
    return 3 if my_rooms == most else 0


def _house_steward_play(state, player, inst, ctx):
    remaining = TOTAL_ROUNDS - state["round"]
    wood = 4 if remaining >= 9 else 3 if remaining >= 6 else \
        2 if remaining >= 3 else 1 if remaining >= 1 else 0
    if wood:
        add_goods(player["resources"], {"wood": wood})
        ctx["log"].append(f"House Steward grants {wood} wood")
        fire_gained(state, player, {"wood": wood}, "card", ctx["log"])

card("occ_house_steward", "House Steward", "occupation",
     "If there are still 1/3/6/9 complete rounds left to play, you "
     "immediately get 1/2/3/4 wood. During scoring, each player with the "
     "most rooms gets 3 bonus points.",
     hooks={"play": _house_steward_play},
     score_bonus=_house_steward_score)

# ── Custom cards adopted from the play-agricola.com CSV dump ─────────
def _deaconess_hook(state, player, inst, ctx):
    if player["people_total"] == 2:
        h = state["harvest_index"]
        gain = {"wood": 1, "food": h}
        add_goods(player["resources"], gain)
        ctx["log"].append(f"{player['name']}'s Deaconess grants 1 wood "
                          f"and {h} food")
        fire_gained(state, player, gain, "card", ctx["log"])

card("occ_deaconess", "Deaconess", "occupation", deck="custom",
     text="As long as you have exactly 2 family members, in the field phase "
     "of harvest 1/2/3/4/5/6, you get 1 wood and 1/2/3/4/5/6 food.",
     hooks={"harvest_field": _deaconess_hook})


def _estate_manager_score(state, player, inst):
    mine = animal_totals_of(player)
    won = 0
    for t in ANIMAL_TYPES:
        if mine[t] > 0 and all(animal_totals_of(p)[t] <= mine[t]
                               for p in state["players"]):
            won += 1
    return {0: 0, 1: 2, 2: 4, 3: 5}[won]

card("occ_estate_manager", "Estate Manager", "occupation", deck="custom",
     min_players=3,
     text="At the end of the game, if no player has more animals of 1/2/3 "
     "types than you (and you have at least 1 of the type), you get "
     "2/4/5 bonus points.",
     score_bonus=_estate_manager_score)


# ═════════════════════════════════════════════════════════════════════
# MINOR IMPROVEMENTS
# ═════════════════════════════════════════════════════════════════════

# ── Space/take bonuses ───────────────────────────────────────────────
card("minor_corn_scoop", "Corn Scoop", "minor", cost={"wood": 1},
     text="Each time you use the \"Grain Seeds\" action space, you get 1 "
     "additional grain.",
     hooks=space_bonus(["grain_seeds"], {"grain": 1}))

card("minor_basket", "Basket", "minor", cost={"reed": 1}, points=1,
     text="Each time you use the \"Reed Bank\" accumulation space, you also "
     "get 1 food.",
     hooks=space_bonus(["reed_bank"], {"food": 1}))

card("minor_canoe", "Canoe", "minor", cost={"wood": 2}, points=1,
     prereq=needs_occupations(1),
     text="Each time you use the \"Fishing\" accumulation space, you also "
     "get 1 food and 1 reed.",
     hooks=space_bonus(["fishing"], {"food": 1, "reed": 1}))

card("minor_fish_trap", "Fish Trap", "minor", cost={"wood": 1},
     text="Each time you use the \"Fishing\" accumulation space, you also "
     "get 1 food.",
     hooks=space_bonus(["fishing"], {"food": 1}))

card("minor_clay_shovel", "Clay Shovel", "minor", cost={"wood": 1},
     text="Each time you use the \"Clay Pit\" or \"Hollow\" accumulation "
     "space, you get 1 additional clay.",
     hooks=space_bonus(["clay_pit", "hollow_3p", "hollow_4p"], {"clay": 1}))

card("minor_quarry_cart", "Quarry Cart", "minor", cost={"wood": 1},
     prereq=needs_occupations(1),
     text="Each time you use the \"Western Quarry\" or \"Eastern Quarry\" "
     "accumulation space, you get 1 additional stone.",
     hooks=space_bonus(["western_quarry", "eastern_quarry"], {"stone": 1}))

card("minor_milk_jug", "Milk Jug", "minor", cost={"clay": 1}, points=1,
     text="Each time ANY player uses the \"Cattle Market\" accumulation "
     "space, you get 2 food.",
     hooks=space_bonus(["cattle_market"], {"food": 2}, others=True))

# ── Schedulers (round-space goods) ───────────────────────────────────
card("minor_pond_hut", "Pond Hut", "minor", cost={"wood": 1}, points=1,
     prereq=exact_occupations(2),
     text="When you play this card, place 1 food on each of the next 3 "
     "round spaces. At the start of these rounds, you get the food.",
     hooks=schedule_on_play("food", rounds_ahead=3))

card("minor_private_forest", "Private Forest", "minor", cost={"food": 2},
     text="When you play this card, place 1 wood on each of the next 5 "
     "round spaces. At the start of these rounds, you get the wood.",
     hooks=schedule_on_play("wood", rounds_ahead=5))

card("minor_sack_cart", "Sack Cart", "minor", cost={"wood": 2}, points=1,
     prereq=needs_occupations(2),
     text="When you play this card, place 1 grain on each of the remaining "
     "round spaces for rounds 5, 8, 11, and 14.",
     hooks=schedule_on_play("grain", fixed_rounds=(5, 8, 11, 14)))

# ── Harvest food providers ───────────────────────────────────────────
def _loom_food(state, player):
    sheep = animal_totals_of(player)["sheep"]
    return 3 if sheep >= 7 else 2 if sheep >= 4 else 1 if sheep >= 1 else 0

card("minor_loom", "Loom", "minor", cost={"wood": 2}, points=1,
     prereq=needs_occupations(2),
     text="In the field phase of each harvest, if you have at least 1/4/7 "
     "sheep, you get 1/2/3 food. During scoring, you get 1 bonus point "
     "for every 3 sheep.",
     hooks=harvest_food(_loom_food),
     score_bonus=lambda s, p, i: animal_totals_of(p)["sheep"] // 3)


def _butter_churn_food(state, player):
    totals = animal_totals_of(player)
    return totals["sheep"] // 3 + totals["cattle"] // 2

card("minor_butter_churn", "Butter Churn", "minor", cost={"clay": 1},
     points=1,
     text="In the field phase of each harvest, you get 1 food for every 3 "
     "sheep and 1 food for every 2 cattle you have.",
     hooks=harvest_food(_butter_churn_food))

# ── Statics: capacity, conversions, rooms ────────────────────────────
card("minor_drinking_trough", "Drinking Trough", "minor", cost={"wood": 2},
     points=1,
     text="Each of your pastures (with or without a stable) can hold 2 "
     "more animals.",
     pasture_capacity_bonus=2)

card("minor_animal_pen", "Animal Pen", "minor", cost={"wood": 1}, points=1,
     prereq=needs_occupations(1),
     text="Your house can hold 1 additional pet.",
     house_capacity=1)

card("minor_caravan", "Caravan", "minor", cost={"wood": 3, "food": 3},
     text="This card provides room for 1 person.",
     extra_rooms=1)

card("minor_bread_paddle", "Bread Paddle", "minor", cost={"wood": 1},
     text="Each time you bake bread, you get 1 additional food.",
     bake_bonus_flat=1)

card("minor_threshing_board", "Threshing Board", "minor",
     cost={"wood": 1}, points=1, prereq=needs_occupations(2),
     text="Each time you use the \"Farmland\" or \"Cultivation\" action "
     "space, you also get a \"Bake Bread\" action.",
     bake_on_spaces=("farmland", "cultivation"))

# ── Field cards ──────────────────────────────────────────────────────
card("minor_beanfield", "Beanfield", "minor",
     cost={"food": 1, "grain": 1}, points=1, prereq=needs_occupations(2),
     text="This card is a field that can only grow vegetables.",
     field={"crops": ("vegetable",)})

card("minor_herb_patch", "Herb Patch", "minor",
     cost={"food": 1}, points=1, prereq=needs_occupations(3),
     text="This card is a field that can only grow grain.",
     field={"crops": ("grain",)})

# ── On-play effects with parameters ──────────────────────────────────
def _shifting_cultivation_play(state, player, inst, ctx):
    from server.agricola.state import plowable_cells
    cell = (ctx.get("params") or {}).get("cell")
    if not isinstance(cell, int) or cell not in plowable_cells(player):
        raise ValueError("Shifting Cultivation: choose a space to plow "
                         "(params.cell)")
    player["cells"][cell]["type"] = "field"
    ctx["log"].append(f"{player['name']} plows a field (Shifting Cultivation)")

card("minor_shifting_cultivation", "Shifting Cultivation", "minor",
     cost={"food": 2}, traveling=True,
     text="When you play this card, immediately plow 1 field. Then pass "
     "this card to the player on your left, who adds it to their hand.",
     hooks={"play": _shifting_cultivation_play})

card("minor_market_stall", "Market Stall", "minor", cost={"grain": 1},
     traveling=True,
     text="When you play this card, you immediately get 1 vegetable. Then "
     "pass this card to the player on your left, who adds it to their hand.",
     hooks={"play": on_play_gain({"vegetable": 1})})


def _mining_hammer_renovate(state, player, inst, ctx):
    cell = ctx.get("free_stable_cell")
    if cell is None:
        return
    c = player["cells"][cell]
    stables = sum(1 for x in player["cells"] if x["stable"])
    if stables >= 4 or c["type"] != "empty" or c["stable"]:
        raise ValueError("Invalid free stable placement")
    c["stable"] = True
    ctx["log"].append(f"{player['name']}'s Mining Hammer builds a free stable")

card("minor_mining_hammer", "Mining Hammer", "minor", cost={"wood": 1},
     text="When you play this card, you immediately get 1 food. Each time "
     "you renovate, you can also build 1 stable without paying wood.",
     hooks={"play": on_play_gain({"food": 1}),
            "renovate": _mining_hammer_renovate})

# ── Trigger cards: fences, sow, occupations ──────────────────────────
def _shepherds_crook_fences(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    if any(len(p) >= 4 for p in ctx["new_pastures"]):
        add_goods(ctx["extra"], {"sheep": 2})
        ctx["log"].append(f"{player['name']}'s Shepherd's Crook grants 2 sheep")

card("minor_shepherds_crook", "Shepherd's Crook", "minor", cost={"wood": 1},
     text="Each time you fence a new pasture covering at least 4 farmyard "
     "spaces, you immediately get 2 sheep.",
     hooks={"fences_built": _shepherds_crook_fences})


def _seed_drill_sow(state, player, inst, ctx):
    for target, crop in ctx["sown"]:
        if crop == "grain":
            if isinstance(target, int):
                player["cells"][target]["crops"]["count"] += 1
            else:
                target["crops"]["count"] += 1
            ctx["log"].append(f"{player['name']}'s Seed Drill adds 1 grain "
                              "to a freshly sown field")
            return

card("minor_seed_drill", "Seed Drill", "minor", cost={"wood": 1}, points=1,
     prereq=needs_occupations(1),
     text="Each time you sow, the first grain field you sow gets 1 "
     "additional grain on it.",
     hooks={"sow": _seed_drill_sow})

# ── Turn-structure card ──────────────────────────────────────────────
card("minor_lasso", "Lasso", "minor", cost={"reed": 1},
     text="You can place exactly two people immediately after one another "
     "if at least one of them uses the \"Sheep Market\", \"Pig Market\", "
     "or \"Cattle Market\" accumulation space.",
     lasso=True)

# ── Scoring minors ───────────────────────────────────────────────────
def _manger_score(state, player, inst):
    from server.agricola.state import compute_pastures
    covered = sum(len(p) for p in compute_pastures(player))
    return 4 if covered >= 10 else 3 if covered >= 8 else \
        2 if covered >= 7 else 1 if covered >= 6 else 0

card("minor_manger", "Manger", "minor", cost={"wood": 2},
     text="During scoring, if your pastures cover at least 6/7/8/10 "
     "farmyard spaces, you get 1/2/3/4 bonus points.",
     score_bonus=_manger_score)

# ── Custom cards adopted from the CSV dump ───────────────────────────
def _hermits_stick_score(state, player, inst):
    return {4: 1, 3: 2, 2: 4}.get(player["people_total"], 0)

card("minor_hermits_stick", "Hermit's Stick", "minor", deck="custom",
     text="At the end of the game, you get 1/2/4 bonus points for having "
     "exactly 4/3/2 family members.",
     score_bonus=_hermits_stick_score)


def _harvest_totem_occ(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    add_goods(ctx["extra"], {"boar": 1})
    ctx["log"].append(f"{player['name']}'s Harvest Totem grants 1 wild boar")

card("minor_harvest_totem", "Harvest Totem", "minor", deck="custom",
     cost={"wood": 1}, points=1,
     prereq=combine(needs_occupations(3), needs_grain_field(1)),
     text="Each time you play an occupation, you get 1 wild boar.",
     hooks={"occupation_played": _harvest_totem_occ})

# ── Simple on-play minors (fill out the deck) ────────────────────────
card("minor_clay_deposit", "Clay Deposit", "minor", cost={"food": 1},
     text="When you play this card, you immediately get 3 clay.",
     hooks={"play": on_play_gain({"clay": 3})})

card("minor_seed_pouch", "Seed Pouch", "minor", cost={"wood": 1},
     prereq=needs_occupations(1),
     text="When you play this card, you immediately get 1 grain and 1 vegetable.",
     hooks={"play": on_play_gain({"grain": 1, "vegetable": 1})})

card("minor_wool_blankets", "Wool Blankets", "minor", cost={"wood": 1},
     points=1,
     text="During scoring, you get 2 bonus points if you live in a wooden "
     "house with at least 3 rooms.",
     score_bonus=lambda s, p, i: 2 if (
         p["house_type"] == "wood"
         and sum(1 for c in p["cells"] if c["type"] == "room") >= 3) else 0)

def _scythe_hook(state, player, inst, ctx):
    fields = sum(1 for c in player["cells"]
                 if c["crops"] and c["crops"]["type"] == "grain")
    fields += sum(1 for i in card_fields(player)
                  if i["crops"] and i["crops"]["type"] == "grain")
    if fields >= 2:
        player["resources"]["grain"] += 1
        ctx["log"].append(f"{player['name']}'s Scythe grants 1 grain")

card("minor_scythe", "Scythe", "minor", cost={"wood": 1}, points=1,
     prereq=needs_grain_field(1),
     text="In the field phase of each harvest, if you have at least 2 grain "
     "fields, you get 1 additional grain.",
     hooks={"harvest_field": _scythe_hook})


# ── Conversions and card actions (queries) ───────────────────────────

def conversion_options(player):
    """All card-provided conversions: list of (key, spec_conv) where key
    is "<card_id>:<index>". Conversions look like
    {"give": {...}, "get": {...}, "per_harvest": n?}."""
    out = []
    for inst in in_play(player):
        for i, conv in enumerate(spec(inst).get("conversions", ())):
            out.append((f"{inst['id']}:{i}", conv, inst))
    return out


def card_actions(state, player):
    """Activated abilities currently available to the player."""
    out = []
    for inst in in_play(player):
        ca = spec(inst).get("card_action")
        if ca and ca["available"](state, player, inst):
            out.append({
                "kind": "card_action",
                "card": inst["id"],
                "description": ca.get("description", spec(inst)["name"]),
            })
    return out


# ── Deck helpers ─────────────────────────────────────────────────────

def load_decks():
    """Import all compendium deck modules (idempotent). Returns the
    UNIMPLEMENTED map {code: reason}."""
    from server.agricola import decks
    return decks.UNIMPLEMENTED


def implemented_decks():
    load_decks()
    return sorted({c["deck"] for c in CARDS.values()})


def deck_for(ctype, player_count, decks):
    return sorted(
        cid for cid, c in CARDS.items()
        if c["type"] == ctype and c["min_players"] <= player_count
        and c["deck"] in decks)


def deal_hands(player_count, rng, decks, hand_size=7):
    """Returns (occ_hands, minor_hands, hand_size, occ_draw, minor_draw).
    If the selected decks cannot cover 7+7 per player, the hand size
    shrinks to fit. `occ_draw`/`minor_draw` are whatever's left of the
    shuffled decks after every hand is dealt (in shuffled order, index 0
    = top of the pile) -- callers (today, only `engine.initial_state`)
    store these as the persistent `state["occupation_draw"]`/
    ["minor_draw"] piles that `draw_occupations`/`draw_minors` below
    draw from; together, every dealt hand plus its draw pile accounts
    for the FULL implemented deck with no overlap or duplicates."""
    occs = deck_for("occupation", player_count, decks)
    minors = deck_for("minor", player_count, decks)
    rng.shuffle(occs)
    rng.shuffle(minors)
    size = min(hand_size, len(occs) // player_count,
               len(minors) // player_count)
    if size < 1:
        raise ValueError("Not enough implemented cards in the selected decks")
    occ_hands = [occs[i * size:(i + 1) * size] for i in range(player_count)]
    minor_hands = [minors[i * size:(i + 1) * size] for i in range(player_count)]
    occ_draw = occs[player_count * size:]
    minor_draw = minors[player_count * size:]
    return occ_hands, minor_hands, size, occ_draw, minor_draw


# ── Draw piles (engine phase 12: hand/deck manipulation) ─────────────
#
# `state["occupation_draw"]`/["minor_draw"] are the persistent remainder
# of the shuffled decks after the opening deal (see deal_hands above);
# `state["occupation_discard"]`/["minor_discard"] start empty and only
# grow as hand cards are discarded (discard_hand_* below). There is
# deliberately NO reshuffle-when-empty -- the physical game never
# reshuffles a spent draw pile, so draw_minors/draw_occupations simply
# return fewer than requested (down to zero) once the pile runs out.
# Every access below uses .setdefault()/.get() so a state dict missing
# these keys (an old save from before this phase) doesn't crash.

def draw_minors(state, player, n, log):
    """Draw up to `n` minor improvements from the top of state[
    "minor_draw"] into player["hand_minors"] (fewer if the pile is
    short, zero if it's empty). Returns the drawn card ids."""
    pile = state.setdefault("minor_draw", [])
    n = max(0, min(n, len(pile)))
    drawn = pile[:n]
    del pile[:n]
    player["hand_minors"].extend(drawn)
    if drawn:
        log.append(f"{player['name']} draws {len(drawn)} minor "
                  f"improvement(s)")
    return drawn


def draw_occupations(state, player, n, log):
    """Occupation twin of draw_minors -- see its docstring."""
    pile = state.setdefault("occupation_draw", [])
    n = max(0, min(n, len(pile)))
    drawn = pile[:n]
    del pile[:n]
    player["hand_occupations"].extend(drawn)
    if drawn:
        log.append(f"{player['name']} draws {len(drawn)} occupation(s)")
    return drawn


def discard_hand_minors(state, player, log):
    """Discard all of `player`'s hand_minors to state["minor_discard"]
    (no reshuffle-when-empty; see draw_minors). Returns the discarded
    card ids."""
    discarded = list(player["hand_minors"])
    state.setdefault("minor_discard", []).extend(discarded)
    player["hand_minors"] = []
    if discarded:
        log.append(f"{player['name']} discards {len(discarded)} minor "
                  f"improvement(s)")
    return discarded


def discard_hand_occupations(state, player, log):
    """Occupation twin of discard_hand_minors -- see its docstring."""
    discarded = list(player["hand_occupations"])
    state.setdefault("occupation_discard", []).extend(discarded)
    player["hand_occupations"] = []
    if discarded:
        log.append(f"{player['name']} discards {len(discarded)} "
                  f"occupation(s)")
    return discarded
