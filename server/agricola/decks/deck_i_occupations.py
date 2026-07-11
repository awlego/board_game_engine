"""Deck I occupations (Original-edition codes I219-I265, plus I340, from
the compendium DB).

Deck I is the original edition's "Interactive" deck: many cards react to
or affect OTHER players (their actions, their farms, their turn order).
Original-board action-space names are mapped onto this engine's revised
spaces where a faithful equivalent exists ("Take 1 Grain" -> grain_seeds,
"1 Cattle" -> cattle_market, "Starting Player" -> meeting_place, etc).
Mechanics that reach into other players' hands/farms, gate the legality
of another player's action, or need turn-order/board-reveal changes are
marked UNIMPLEMENTED per decks/GUIDE.md; no text corruption (parser
bleed) was found in this slice, so there is no _TEXT_FIXES table.
"""

from server.agricola.cards import (
    compendium_card, add_goods, prompt_choice, take_bonus, harvest_food,
)
import server.agricola.cards as cards
from server.agricola.state import (
    TOTAL_ROUNDS, NUM_CELLS, MAX_STABLES,
    compute_pastures, plowable_cells,
)

UNIMPLEMENTED = {
    "I220": "reclassifies the Well as a minor improvement with a special "
            "cost just for this player. cost_mod for kind='improvement' "
            "now carries ctx['improvement'] (engine phase 7), so a "
            "discount targeted at the Well alone is expressible -- but "
            "the major->minor reclassification itself is a separate, "
            "still-unsupported score/category-manipulation gap (same "
            "class as E15's Clay/Stone Oven reclassification). "
            "Reassessed per the fidelity rule: the reclassification is "
            "the whole point of the card (its own name is 'Well "
            "Builder'), and per its own ruling the Well is a minor "
            "improvement for most purposes but STILL a major one for "
            "others (Nosebag M022, Administration M070) -- a genuine "
            "dual-category state this engine's single occupations[]/"
            "minors[] instance-category split can't represent at all, "
            "not just a missing scoring tweak. Registering only the "
            "discount would misrepresent the card, so it stays fully "
            "unimplemented rather than half-registered.",
    "I222": "requires tracking the game-wide order in which players "
            "first renovate to clay/stone; the 'renovate' event only "
            "fires to the acting player's own cards (fire_player, not a "
            "broadcast), so no card can observe another player's "
            "renovation.",
    "I223": "takes a grain directly from another player's field; "
            "affecting other players' farms directly is unsupported.",
    "I224": "reacts to another player's sow action, but 'sow' only fires "
            "to the acting player's own cards (fire_player, not a "
            "broadcast) -- no card can observe another player's sow.",
    "I228": "grants a bonus Major OR Minor Improvement action; the minor "
            "half has a precedent (_offer_free_minor-style), but the "
            "major-improvement half needs cell/cost/available-"
            "improvements bookkeeping reproduced from a card hook (same "
            "gap as B150), and silently dropping the major half would "
            "misrepresent the card.",
    "I229": "forces other players to pay you before they may use the "
            "'Take 1 Grain' space -- a legality-gating replacement "
            "effect on other players' actions (same category as B138); "
            "space_used only fires after a space's action has already "
            "resolved, so it cannot block or charge as a precondition.",
    "I230": "adds an entirely new action space to the board (action-"
            "space deck/order/reveal is explicitly out of scope) and "
            "additionally requires other players to pay a legality-"
            "gating fee before using it (same as I229/B138).",
    "I233": "requires bypassing the 'Family Growth without Room' "
            "legality check on the basic_wish space for exactly one "
            "future use; the check runs inside _resolve_space before "
            "any card hook fires, and the static extra_rooms key is a "
            "flat per-card value with no per-instance 'used up' state to "
            "consume after one use.",
    "I237": "doubling Traveling Players' food requires then paying 1 "
            "food to each OTHER player who owns any of 8 named "
            "occupations (Magician, Conjurer, Dancer, Animal Trainer, "
            "Storyteller, Acrobat, Puppeteer, Street Musician), most "
            "from decks this engine hasn't implemented; enumerating only "
            "the in-scope subset would silently misrepresent the card's "
            "cost.",
    "I238": "reveals all remaining round-space cards early for one "
            "player only; the action-space list is shared global state "
            "with no per-player visibility, and modifying reveal order/"
            "timing is explicitly out of scope.",
    "I239": "the grain->food conversion itself maps to raw_values, but "
            "the card's defining mechanic is that any OTHER player can "
            "veto the conversion by buying the grain first; that "
            "legality-gating replacement effect on the actor's own "
            "action is unsupported, and implementing only the "
            "conversion half would silently drop the card's balancing "
            "risk.",
    "I244": "suppresses the field, breeding, AND feeding phases for one "
            "player for one whole harvest; those phases are "
            "unconditional per-player loops in _start_harvest/feeding "
            "with no per-player skip flag or hook -- there is nothing "
            "for a card to intercept.",
    "I248": "requires a distinct 'returning home' checkpoint between the "
            "work phase and the next round's preparation phase to claim "
            "a space's leftover food ahead of other players; no such "
            "hook exists, and by the time round_start fires, the "
            "Fishing space's supply has already been re-accumulated for "
            "the new round, corrupting the amount being claimed.",
    "I249": "paying 1 food to play an occupation (without its own play "
            "cost) off another player's action would require "
            "duplicating _play_occupation's internals (cost, hand "
            "removal, occs_played bookkeeping, occupation_played "
            "firing) inside a card hook; the identically-named Puppeteer "
            "in deck C (C152) declined this same duplication per the "
            "architecture's engine-extension-over-duplication guidance.",
    "I263": "reassessed against sub_actions.build_fences: the follow-up "
            "fence build itself (with player-chosen edges) is expressible "
            "now (space_used hook on a marked space + card_action, "
            "params channel for the edges). Still gated on a different, "
            "unfixed piece: placing the marker spends one of the "
            "player's 15 physical fence pieces ('from now on, you have "
            "only 14 fences available for building') PERMANENTLY, "
            "including for their ordinary Fencing-space builds -- but "
            "MAX_FENCES is a hardcoded global constant state.py's "
            "can_build_fences/build_fences check directly, with no "
            "per-player cap-reduction hook (cost_mod only adjusts PRICE, "
            "never the 15-fence count). Registering the marker+bonus-"
            "build half while silently dropping the '-1 max fences' "
            "consequence would make the card strictly better than "
            "printed (a free extra fence-build ability with no offsetting "
            "cost), which is the wrong direction to approximate in --"
            "unlike e.g. Carpenter's Axe's omitted clause, which only "
            "ever makes a card weaker than printed -- so it stays fully "
            "unimplemented rather than half-registered.",
    "I265": "pre-places unbuilt fences on future round spaces and lets "
            "the player build them for food instead of wood, but which "
            "of up to 15 edges to build is an open-ended choice with no "
            "channel outside the normal Build Fences flow (same gap as "
            "B088/B093/B130/I263), and it would have to be resolved from "
            "a round_start hook, where GUIDE.md disallows prompting.",
}

# ── Shared helpers ────────────────────────────────────────────────────

WOOD_SPACES = ("forest", "grove", "copse")


def _schedule_remaining(state, player, good, amount, log, label):
    """Place `amount` of `good` on every round space from next round to
    the end of the game (Manservant/Clay Hut Builder/Water Carrier)."""
    rnd = state["round"]
    targets = list(range(rnd + 1, TOTAL_ROUNDS + 1))
    for r in targets:
        slot = state["round_goods"].setdefault(str(r), {}) \
            .setdefault(str(player["index"]), {})
        add_goods(slot, {good: amount})
    if targets:
        log.append(f"{player['name']}'s {label} places {amount} {good} "
                   "on each remaining round space")


def _best_effort_place_animal(state, player, animal):
    """Auto-place one animal into a pasture, unfenced stable, or the
    house (pet), best-effort, mirroring the shape of the engine's own
    newborn-animal placement (unreachable from card code, since cards.py
    cannot import engine.py). Used instead of ctx["extra"]/prompts from
    round_start hooks, which GUIDE.md disallows. Returns True if placed."""
    for pasture in compute_pastures(player):
        occupant, count = None, 0
        for i in pasture:
            a = player["cells"][i]["animal"]
            if a:
                occupant, count = a["type"], a["count"]
        if occupant not in (None, animal):
            continue
        if count < cards.pasture_capacity(state, player, pasture, animal):
            cell = pasture[0]
            if player["cells"][cell]["animal"] is None:
                player["cells"][cell]["animal"] = {"type": animal, "count": 1}
            else:
                player["cells"][cell]["animal"]["count"] += 1
            return True
    pasture_cells = {i for pa in compute_pastures(player) for i in pa}
    for i, c in enumerate(player["cells"]):
        if c["stable"] and c["type"] == "empty" and not c["animal"] \
                and i not in pasture_cells:
            c["animal"] = {"type": animal, "count": 1}
            return True
    cap = cards.house_capacity(state, player)
    if sum(player["pets"].values()) < cap:
        player["pets"][animal] = player["pets"].get(animal, 0) + 1
        return True
    return False


def _wood_by_round(rnd):
    """Village Elder / Church Warden's shared wood-on-play schedule."""
    if rnd < 6:
        return 4
    if rnd <= 8:
        return 3
    if rnd <= 11:
        return 2
    if rnd <= 13:
        return 1
    return 0


def _make_buyer(good, first_time_only):
    """Wood/Reed/Stone Buyer: whenever another player receives `good`
    from a space, the owner may buy 1 for 1 food (the other player
    cannot refuse, per the card text). Reed/Stone Buyer are limited to
    the first time the good is taken in a round; Wood Buyer is not."""
    def space_used(state, player, inst, ctx):
        if ctx["actor"] == player["index"] or not ctx["goods"].get(good):
            return
        if first_time_only and inst["data"].get("bought_round") == state["round"]:
            return
        if player["resources"]["food"] < 1:
            return
        actor = state["players"][ctx["actor"]]
        if actor["resources"].get(good, 0) < 1:
            return
        prompt_choice(state, player, inst["id"],
                     f"Buy 1 {good} from {actor['name']} for 1 food?",
                     ["Decline", f"Pay 1 food for 1 {good}"],
                     data={"actor": ctx["actor"]})

    def choice(state, player, inst, ctx):
        if ctx["index"] == 0 or player["resources"]["food"] < 1:
            return
        actor = state["players"][ctx["data"]["actor"]]
        if actor["resources"].get(good, 0) < 1:
            return
        player["resources"]["food"] -= 1
        actor["resources"]["food"] += 1
        actor["resources"][good] -= 1
        player["resources"][good] = player["resources"].get(good, 0) + 1
        if first_time_only:
            inst["data"]["bought_round"] = state["round"]
        ctx["log"].append(f"{player['name']}'s Buyer buys 1 {good} from "
                          f"{actor['name']}")
    return space_used, choice


# ── I219 Fieldsman ────────────────────────────────────────────────────
def _fieldsman_sow(state, player, inst, ctx):
    sown = ctx["sown"]
    if len(sown) == 1:
        bonus = 2
    elif len(sown) == 2:
        bonus = 1
    else:
        return
    for target, _crop in sown:
        cell_crops = (player["cells"][target]["crops"] if isinstance(target, int)
                     else target["crops"])
        if cell_crops:
            cell_crops["count"] += bonus
    ctx["log"].append(f"{player['name']}'s Fieldsman adds {bonus} extra "
                      "good(s) to each sown field")

compendium_card("I219", hooks={"sow": _fieldsman_sow})


# ── I221 Village Elder ────────────────────────────────────────────────
def _village_elder_play(state, player, inst, ctx):
    wood = _wood_by_round(state["round"])
    if wood:
        add_goods(player["resources"], {"wood": wood})
        ctx["log"].append(f"{player['name']}'s Village Elder grants {wood} wood")


def _most_improvements_score(state, player, inst):
    def total(p):
        return len(p["improvements"]) + len(p["minors"])
    mine = total(player)
    return 3 if mine == max(total(p) for p in state["players"]) else 0

compendium_card("I221", hooks={"play": _village_elder_play},
                score_bonus=_most_improvements_score)


# ── I225 Field Watchman ───────────────────────────────────────────────
def _field_watchman_space(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or ctx["space_id"] != "grain_seeds":
        return
    options = plowable_cells(player)
    if not options:
        return
    prompt_choice(state, player, inst["id"],
                 "Field Watchman: plough an additional field?",
                 ["Decline"] + [f"Plow cell {c}" for c in options],
                 data={"cells": options})


def _field_watchman_choice(state, player, inst, ctx):
    if ctx["index"] == 0:
        return
    cell = ctx["data"]["cells"][ctx["index"] - 1]
    if cell not in plowable_cells(player):
        return
    player["cells"][cell]["type"] = "field"
    ctx["log"].append(f"{player['name']}'s Field Watchman ploughs an "
                      "additional field")

compendium_card("I225", hooks={"space_used": _field_watchman_space},
                resolve_choice=_field_watchman_choice)


# ── I226 Gardener ────────────────────────────────────────────────────
# "Take vegetables from the general supply and not from your vegetable
# field whenever you harvest them - you keep the vegetables on the
# fields." Not optional (ruling). This is exactly the motivating example
# for keep_crops_on_harvest -- credit as usual, but don't decrement.
compendium_card("I226", keep_crops_on_harvest=("vegetable",))


# ── I227 Church Warden ────────────────────────────────────────────────
def _church_warden_play(state, player, inst, ctx):
    wood = _wood_by_round(state["round"])
    if wood:
        add_goods(player["resources"], {"wood": wood})
        ctx["log"].append(f"{player['name']}'s Church Warden grants {wood} wood")


def _church_warden_score(state, player, inst):
    # Approximates "performed actions with 5 people in round 14": guests
    # and the Holiday House exception aren't modeled, and a forced
    # forfeiture (no space usable) also increments people_placed even
    # though no action was taken -- a documented, minor overcount.
    return 3 if state["round"] == TOTAL_ROUNDS and player["people_placed"] >= 5 else 0

compendium_card("I227", hooks={"play": _church_warden_play},
                score_bonus=_church_warden_score)


# ── I231 Manservant ───────────────────────────────────────────────────
def _manservant_check(state, player, inst, ctx):
    if player["house_type"] == "stone":
        _schedule_remaining(state, player, "food", 3, ctx["log"], "Manservant")

compendium_card("I231", hooks={"play": _manservant_check,
                               "renovate": _manservant_check})


# ── I232 Midwife ──────────────────────────────────────────────────────
def _midwife_hook(state, player, inst, ctx):
    if ctx["actor"] == player["index"]:
        return
    actor = state["players"][ctx["actor"]]
    diff = actor["people_total"] - player["people_total"]
    if diff >= 2:
        player["resources"]["food"] += 2
        ctx["log"].append(f"{player['name']}'s Midwife grants 2 food")
    elif diff >= 1:
        player["resources"]["food"] += 1
        ctx["log"].append(f"{player['name']}'s Midwife grants 1 food")

compendium_card("I232", hooks={"family_growth": _midwife_hook})


# ── I234 Wood Buyer ───────────────────────────────────────────────────
_wood_buyer_space, _wood_buyer_choice = _make_buyer("wood", first_time_only=False)
compendium_card("I234", hooks={"space_used": _wood_buyer_space},
                resolve_choice=_wood_buyer_choice)


# ── I235 Wood Collector ───────────────────────────────────────────────
def _wood_collector_play(state, player, inst, ctx):
    rnd = state["round"]
    targets = list(range(rnd + 1, min(TOTAL_ROUNDS, rnd + 5) + 1))
    for r in targets:
        slot = state["round_goods"].setdefault(str(r), {}) \
            .setdefault(str(player["index"]), {})
        add_goods(slot, {"wood": 1})
    if targets:
        ctx["log"].append("Wood Collector places 1 wood on rounds "
                          f"{', '.join(map(str, targets))}")

compendium_card("I235", hooks={"play": _wood_collector_play})


# ── I236 Hide Farmer ──────────────────────────────────────────────────
def _hide_farmer_score(state, player, inst):
    pastures = compute_pastures(player)
    pasture_cells = {i for pa in pastures for i in pa}
    unused = sum(1 for i, c in enumerate(player["cells"])
                if c["type"] == "empty" and not c["stable"]
                and i not in pasture_cells)
    # A bonus rather than an actual food payment: scoring is terminal and
    # nothing reads resources afterward, so there is no double-payment
    # risk, and this avoids mutating state from a scoring function.
    return min(unused, player["resources"]["food"])

compendium_card("I236", score_bonus=_hide_farmer_score)


# ── I240 Cowherd ──────────────────────────────────────────────────────
compendium_card("I240", hooks=take_bonus(["cattle"], {"cattle": 1}))


# ── I241 Clay Plasterer ───────────────────────────────────────────────
def _clay_plasterer_mod(state, player, kind, cost, ctx):
    if kind == "renovation" and player["house_type"] == "wood":
        return {"clay": 1, "reed": 1}
    if kind == "room" and player["house_type"] == "clay":
        n = ctx.get("count", 1)
        return {"clay": 3 * n, "reed": 2 * n}
    return cost

compendium_card("I241", cost_mod=_clay_plasterer_mod)


# ── I242 Clay Hut Builder ─────────────────────────────────────────────
def _clay_hut_builder_check(state, player, inst, ctx):
    if inst["data"].get("triggered"):
        return
    if player["house_type"] in ("clay", "stone"):
        inst["data"]["triggered"] = True
        _schedule_remaining(state, player, "clay", 2, ctx["log"],
                            "Clay Hut Builder")

compendium_card("I242", hooks={"play": _clay_hut_builder_check,
                               "renovate": _clay_hut_builder_check})


# ── I243 Bricklayer ───────────────────────────────────────────────────
def _bricklayer_mod(state, player, kind, cost, ctx):
    if kind in ("improvement", "renovation") and cost.get("clay"):
        cost = dict(cost)
        cost["clay"] = max(0, cost["clay"] - 1)
    elif kind == "room" and cost.get("clay"):
        cost = dict(cost)
        cost["clay"] = max(0, cost["clay"] - 2 * ctx.get("count", 1))
    return cost

compendium_card("I243", cost_mod=_bricklayer_mod)


# ── I245 Market Crier ─────────────────────────────────────────────────
def _i245_market_crier_space(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or ctx["space_id"] != "grain_seeds":
        return
    prompt_choice(state, player, inst["id"],
                 "Market Crier: take 1 additional grain and 1 vegetable "
                 "(other players each get 1 grain)?",
                 ["Decline", "Take extra"])


def _i245_market_crier_choice(state, player, inst, ctx):
    if ctx["index"] == 0:
        return
    player["resources"]["grain"] += 1
    player["resources"]["vegetable"] += 1
    for p in state["players"]:
        if p["index"] != player["index"]:
            p["resources"]["grain"] += 1
    ctx["log"].append(f"{player['name']}'s Market Crier takes 1 grain and "
                      "1 vegetable; other players each get 1 grain")

compendium_card("I245", hooks={"space_used": _i245_market_crier_space},
                resolve_choice=_i245_market_crier_choice)


# ── I246 Milking Hand ─────────────────────────────────────────────────
def _milking_hand_food(state, player):
    cattle = cards.animal_totals_of(player)["cattle"]
    return 3 if cattle >= 5 else 2 if cattle >= 3 else 1 if cattle >= 1 else 0

compendium_card(
    "I246", hooks=harvest_food(_milking_hand_food),
    score_bonus=lambda s, p, i: cards.animal_totals_of(p)["cattle"] // 2)


# ── I247 Butcher ──────────────────────────────────────────────────────
compendium_card("I247", conversions=[
    {"give": {"sheep": 1}, "get": {"food": 1}},
    {"give": {"boar": 1}, "get": {"food": 2}},
    {"give": {"cattle": 1}, "get": {"food": 3}},
])


# ── I250 Sheep Whisperer ──────────────────────────────────────────────
def _sheep_whisperer_play(state, player, inst, ctx):
    rnd = state["round"]
    targets = [r for r in (rnd + 4, rnd + 7, rnd + 9, rnd + 11)
              if r <= TOTAL_ROUNDS]
    inst["data"]["sheep_rounds"] = targets
    if targets:
        ctx["log"].append("Sheep Whisperer schedules 1 sheep on rounds "
                          f"{', '.join(map(str, targets))}")


def _sheep_whisperer_round_start(state, player, inst, ctx):
    if state["round"] not in inst["data"].get("sheep_rounds", ()):
        return
    if _best_effort_place_animal(state, player, "sheep"):
        ctx["log"].append(f"{player['name']}'s Sheep Whisperer places 1 sheep")
    else:
        ctx["log"].append(f"{player['name']}'s Sheep Whisperer has no room "
                          "for a sheep")

compendium_card("I250", hooks={"play": _sheep_whisperer_play,
                               "round_start": _sheep_whisperer_round_start})


# ── I251 Reed Buyer ───────────────────────────────────────────────────
_reed_buyer_space, _reed_buyer_choice = _make_buyer("reed", first_time_only=True)
compendium_card("I251", hooks={"space_used": _reed_buyer_space},
                resolve_choice=_reed_buyer_choice)


# ── I252 Pig Breeder ──────────────────────────────────────────────────
def _pig_breeder_play(state, player, inst, ctx):
    add_goods(ctx["extra"], {"boar": 1})
    ctx["log"].append(f"{player['name']}'s Pig Breeder grants 1 wild boar")


def _pig_breeder_round_start(state, player, inst, ctx):
    if state["round"] != 13:
        return
    if cards.animal_totals_of(player)["boar"] < 2:
        return
    if _best_effort_place_animal(state, player, "boar"):
        ctx["log"].append(f"{player['name']}'s wild boar breed (Pig Breeder)")

compendium_card("I252", hooks={"play": _pig_breeder_play,
                               "round_start": _pig_breeder_round_start})


# ── I253 Pig Catcher ──────────────────────────────────────────────────
def _pig_catcher_space(state, player, inst, ctx):
    if ctx["actor"] != player["index"] or ctx["space_id"] not in WOOD_SPACES:
        return
    if ctx["goods"].get("wood", 0) < 2:
        return
    prompt_choice(state, player, inst["id"],
                 "Pig Catcher: leave 2 wood for 1 wild boar instead?",
                 ["Decline", "Leave 2 wood for 1 wild boar"])


def _pig_catcher_choice(state, player, inst, ctx):
    if ctx["index"] == 0 or player["resources"]["wood"] < 2:
        return
    player["resources"]["wood"] -= 2
    add_goods(ctx["extra"], {"boar": 1})
    ctx["log"].append(f"{player['name']}'s Pig Catcher leaves 2 wood for "
                      "1 wild boar")

compendium_card("I253", hooks={"space_used": _pig_catcher_space},
                resolve_choice=_pig_catcher_choice)


# ── I254 Groom ────────────────────────────────────────────────────────
def _groom_available(state, player, inst):
    if state["phase"] != "work" or player["house_type"] != "stone":
        return False
    if inst["data"].get("used_round") == state["round"]:
        return False
    if player["resources"]["wood"] < 1:
        return False
    if sum(1 for c in player["cells"] if c["stable"]) >= MAX_STABLES:
        return False
    return any(c["type"] == "empty" and not c["stable"] for c in player["cells"])


def _groom_apply(state, player, inst, ctx):
    cell = (ctx.get("params") or {}).get("cell")
    if not isinstance(cell, int) or not (0 <= cell < NUM_CELLS):
        raise ValueError("Groom: choose a stable cell (params.cell)")
    c = player["cells"][cell]
    if c["type"] != "empty" or c["stable"]:
        raise ValueError("Groom: that space cannot hold a stable")
    player["resources"]["wood"] -= 1
    c["stable"] = True
    inst["data"]["used_round"] = state["round"]
    ctx["log"].append(f"{player['name']}'s Groom builds a stable for 1 wood")

compendium_card("I254", card_action={
    "available": _groom_available, "apply": _groom_apply,
    "description": "Groom: build 1 stable for 1 wood (once per round, "
                   "requires a stone house)"})


# ── I255 Stone Buyer ──────────────────────────────────────────────────
_stone_buyer_space, _stone_buyer_choice = _make_buyer("stone", first_time_only=True)
compendium_card("I255", hooks={"space_used": _stone_buyer_space},
                resolve_choice=_stone_buyer_choice)


# ── I256 Stone Carver ─────────────────────────────────────────────────
compendium_card("I256", conversions=[
    {"give": {"stone": 1}, "get": {"food": 3}, "per_harvest": 1}])


# ── I257 Street Musician ──────────────────────────────────────────────
def _street_musician_space(state, player, inst, ctx):
    if ctx["actor"] == player["index"] or ctx["space_id"] != "traveling_players":
        return
    player["resources"]["grain"] += 1
    ctx["log"].append(f"{player['name']}'s Street Musician grants 1 grain")

compendium_card("I257", hooks={"space_used": _street_musician_space})


# ── I258 Cabinetmaker ─────────────────────────────────────────────────
compendium_card("I258", conversions=[
    {"give": {"wood": 1}, "get": {"food": 2}, "per_harvest": 1}])


# ── I259 Animal Dealer ────────────────────────────────────────────────
ANIMAL_MARKET_GOOD = {"sheep_market": "sheep", "pig_market": "boar",
                      "cattle_market": "cattle"}


def _animal_dealer_space(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    good = ANIMAL_MARKET_GOOD.get(ctx["space_id"])
    if not good or not ctx["goods"].get(good) or player["resources"]["food"] < 1:
        return
    prompt_choice(state, player, inst["id"],
                 f"Animal Dealer: pay 1 food for 1 additional {good}?",
                 ["Decline", f"Pay 1 food for 1 {good}"], data={"good": good})


def _animal_dealer_choice(state, player, inst, ctx):
    if ctx["index"] == 0 or player["resources"]["food"] < 1:
        return
    good = ctx["data"]["good"]
    player["resources"]["food"] -= 1
    add_goods(ctx["extra"], {good: 1})
    ctx["log"].append(f"{player['name']}'s Animal Dealer buys 1 additional "
                      f"{good}")

compendium_card("I259", hooks={"space_used": _animal_dealer_space},
                resolve_choice=_animal_dealer_choice)


# ── I260 Taster ────────────────────────────────────────────────────────
# "Whenever another player is the starting player, you can pay them 1
# food at the start of the round and be the first to place a family
# member. After that, play starts with the starting player as usual."
# The worked _resume_from recipe (engine phase 11) -- see decks/
# GUIDE.md's "One-shot first-placer override (I260 Taster)" section for
# the full step-by-step. A round_start hook offers the choice (with its
# own food-affordability check, per that section's closing note);
# accepting pays the starting player, places the Taster's owner first
# via _pending_work_start, and stashes _resume_from so rotation resumes
# from the TRUE starting player once the owner's single out-of-turn
# placement is done.


def _taster_round_start(state, player, inst, ctx):
    if state["starting_player"] == player["index"]:
        return  # "you do not get any advantage" if you're already first
    if player["resources"]["food"] < 1:
        return
    prompt_choice(state, player, inst["id"],
                 "Taster: pay 1 food to place first this round?",
                 ["yes", "no"])


def _taster_resolve(state, player, inst, ctx):
    if ctx["option"] != "yes" or player["resources"]["food"] < 1:
        return
    starting_p = state["players"][state["starting_player"]]
    player["resources"]["food"] -= 1
    cards.grant_goods(state, starting_p, {"food": 1}, ctx["log"])
    ctx["log"].append(f"{player['name']} pays 1 food to place first (Taster)")
    state["_pending_work_start"] = player["index"]
    state["_resume_from"] = state["starting_player"]


compendium_card("I260", hooks={"round_start": _taster_round_start},
                resolve_choice=_taster_resolve)


# ── I261 Outrider ─────────────────────────────────────────────────────
def _outrider_space(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    if not state["revealed"] or ctx["space_id"] != state["revealed"][-1]:
        return
    player["resources"]["grain"] += 1
    ctx["log"].append(f"{player['name']}'s Outrider grants 1 grain")

compendium_card("I261", hooks={"space_used": _outrider_space})


# ── I262 Water Carrier ────────────────────────────────────────────────
def _water_carrier_trigger(state, player, inst, ctx):
    if inst["data"].get("triggered"):
        return
    inst["data"]["triggered"] = True
    _schedule_remaining(state, player, "food", 1, ctx["log"], "Water Carrier")


def _water_carrier_play(state, player, inst, ctx):
    if any("well" in p["improvements"] for p in state["players"]):
        _water_carrier_trigger(state, player, inst, ctx)


def _water_carrier_improvement(state, player, inst, ctx):
    if ctx["improvement"] == "well":
        _water_carrier_trigger(state, player, inst, ctx)

compendium_card("I262", hooks={"play": _water_carrier_play,
                               "improvement_built": _water_carrier_improvement})


# ── I264 Fencer ───────────────────────────────────────────────────────
def _fencer_play(state, player, inst, ctx):
    inst["data"]["fence_counts"] = {p["index"]: len(p["fences"])
                                    for p in state["players"]}


def _fencer_fences(state, player, inst, ctx):
    if ctx["actor"] == player["index"]:
        return
    counts = inst["data"].setdefault("fence_counts", {})
    actor = state["players"][ctx["actor"]]
    before = counts.get(actor["index"], 0)
    after = len(actor["fences"])
    counts[actor["index"]] = after
    built = after - before
    if built <= 0:
        return
    # Approximation: if another player builds fences twice in one turn
    # (e.g. via a bonus fencing action), this fires once per fences_built
    # event rather than once for the turn's total -- a documented,
    # minor edge-case deviation.
    wood = 2 if built >= 5 else 1
    player["resources"]["wood"] += wood
    ctx["log"].append(f"{player['name']}'s Fencer grants {wood} wood")

compendium_card("I264", hooks={"play": _fencer_play,
                               "fences_built": _fencer_fences})


# ── I340 Rancher ──────────────────────────────────────────────────────
def _farmyard_used(player):
    pastures = compute_pastures(player)
    pasture_cells = {i for pa in pastures for i in pa}
    unused = sum(1 for i, c in enumerate(player["cells"])
                if c["type"] == "empty" and not c["stable"]
                and i not in pasture_cells)
    return NUM_CELLS - unused


def _rancher_round_start(state, player, inst, ctx):
    mine = _farmyard_used(player)
    others = [_farmyard_used(p) for p in state["players"]
             if p["index"] != player["index"]]
    if others and mine > max(others):
        player["resources"]["wood"] += 1
        ctx["log"].append(f"{player['name']}'s Rancher grants 1 wood")

compendium_card("I340", hooks={"round_start": _rancher_round_start})
