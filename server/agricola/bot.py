"""Random Bot — a server-drivable Agricola player.

Policy: take a random legal action each time the game waits on the bot
(random space, randomly generated parameters), falling back to a safe
always-available action when the random parameterization is rejected.
Grown out of the full-game fuzz in tests/test_agricola.py, which now
imports these helpers, so the shipped bot is exactly the policy the
fuzz suite exercises.

Entry point for the server: bot_turn(engine, state, player_id, rng) —
picks and applies one action, returning the engine's ActionResult
(see AgricolaEngine.bot_turn / GameServer._run_bots).
"""

from server.agricola import cards
from server.agricola.state import (
    MAJOR_IMPROVEMENTS, animal_counts, cell_edges, plowable_cells,
    validate_fence_layout,
)

# Spaces with no parameters and no cost that any player can always use
# (accumulation spaces and unconditional gains).
SAFE_SPACES = ("day_laborer", "fishing", "grain_seeds", "meeting_place",
               "forest", "clay_pit", "reed_bank", "traveling_players",
               "western_quarry", "eastern_quarry", "vegetable_seeds",
               "copse", "grove", "hollow_3p", "hollow_4p")


def bot_turn(engine, state, player_id, rng):
    """Pick and apply one action for a bot seat (ActionResult).

    The randomly parameterized action can be invalid for cards that
    need richer input; retry with a safe fallback (state unchanged on
    the failed attempt — apply_action validates before mutating).
    """
    action = random_bot_action(engine, state, player_id, rng)
    if action is None:
        action = bot_fallback(engine, state, player_id, rng)
    try:
        return engine.apply_action(state, player_id, action)
    except ValueError:
        return engine.apply_action(
            state, player_id, bot_fallback(engine, state, player_id, rng))


def random_bot_action(engine, state, pid, rng):
    """Pick a random valid action with randomly generated parameters."""
    acts = engine.get_valid_actions(state, pid)
    # During the pre-game draft every action is a draft_pick.
    if acts and acts[0]["kind"] == "draft_pick":
        return {"kind": "draft_pick", "card": rng.choice(acts)["card"]}
    # Answer card choice prompts randomly.
    choice = next((a for a in acts if a["kind"] == "choice"), None)
    if choice:
        return {"kind": "choice", "index": rng.randrange(len(choice["options"]))}
    # Skip optional activated card abilities (they may need params);
    # deck tests cover them directly.
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
