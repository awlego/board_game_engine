"""
Agricola (Revised Edition) game engine — full game with hand cards.

Each player is dealt 7 occupations and 7 minor improvements. Card effects
are implemented via the registry/hook architecture described in CARDS.md;
the engine fires events and asks modifier queries but never references
individual cards.

Round flow: preparation (auto) → work (players place people one at a
time) → returning home (auto) → harvest on rounds 4/7/9/11/13/14
(field phase auto → feeding: every player submits a "feed" action →
breeding auto). Game ends after round 14's harvest.

Sub-decisions (accommodating newly gained animals) are modeled with a
`state["prompts"]` queue (accommodating animals, card choices) that
must be resolved by the target player before play continues.
"""

import random
from copy import deepcopy

from server.game_engine import GameEngine, ActionResult
from server.agricola.state import (
    ANIMAL_TYPES, MAX_PEOPLE, MAX_STABLES, MAX_FENCES,
    NUM_CELLS, TOTAL_ROUNDS, HARVEST_ROUNDS,
    PERMANENT_SPACES, STAGE_CARDS, MAJOR_IMPROVEMENTS,
    FIREPLACES, COOKING_HEARTHS,
    stage_of_round, build_stage_deck, create_player, create_action_spaces,
    orthogonal_neighbors, compute_pastures, pasture_capacity,
    validate_fence_layout, validate_animal_placement, animal_counts,
    plowable_cells,
)
from server.agricola import cards
from server.agricola.scoring import final_scores

# Register all compendium deck modules at import time so restored
# rooms (persistence) can resolve card specs without a fresh deal.
cards.load_decks()

ROOM_COST_MATERIAL = {"wood": "wood", "clay": "clay", "stone": "stone"}
RENOVATION_TARGET = {"wood": "clay", "clay": "stone"}

# Spaces that allow playing a minor improvement "afterward".
MINOR_AFTER_SPACES = ("meeting_place", "basic_wish")
ANIMAL_MARKETS = ("sheep_market", "pig_market", "cattle_market")


class AgricolaEngine(GameEngine):
    player_count_range = (1, 4)

    # ── Core interface ───────────────────────────────────────────────

    # Decks used when the room does not specify any. The hand-written
    # "base"/"custom" decks are always valid; compendium decks join the
    # default set once implemented.
    DEFAULT_DECKS = ("base", "custom")

    def initial_state(self, player_ids, player_names, options=None):
        n = len(player_ids)
        options = options or {}
        players = [create_player(i, pid, pname)
                   for i, (pid, pname) in enumerate(zip(player_ids, player_names))]

        starting = random.randrange(n)
        for p in players:
            if n == 1:
                p["resources"]["food"] = 0
            else:
                p["resources"]["food"] = 2 if p["index"] == starting else 3

        decks = options.get("decks") or list(self.DEFAULT_DECKS)
        known = cards.implemented_decks()
        decks = [d for d in decks if d in known] or list(self.DEFAULT_DECKS)

        rng = random.Random(random.randrange(2 ** 31))
        occ_hands, minor_hands, hand_size = cards.deal_hands(n, rng, decks)
        for p in players:
            p["hand_occupations"] = occ_hands[p["index"]]
            p["hand_minors"] = minor_hands[p["index"]]

        state = {
            "game": "agricola",
            "player_ids": player_ids,
            "player_count": n,
            "players": players,
            "decks": decks,
            "hand_size": hand_size,
            "action_spaces": create_action_spaces(n),
            "deck": build_stage_deck(rng),
            "revealed": [],
            "round": 0,
            "stage": 1,
            "harvest_index": 0,
            "starting_player": starting,
            "round_first_player": starting,
            "current_player": starting,
            "phase": "work",
            "prompts": [],
            "round_goods": {},
            "available_improvements": sorted(MAJOR_IMPROVEMENTS.keys()),
            "game_over": False,
            "scores": None,
            "winners": None,
        }
        self._start_round(state, [])
        return state

    def get_player_view(self, state, player_id):
        view = deepcopy(state)
        pidx = self._player_idx(state, player_id)
        view["your_player_id"] = player_id
        view["your_player_idx"] = pidx
        # Hide other players' hands (show counts only).
        for p in view["players"]:
            if p["index"] != pidx:
                p["hand_occupations"] = len(p["hand_occupations"])
                p["hand_minors"] = len(p["hand_minors"])
        if pidx is not None:
            me = state["players"][pidx]
            view["playable_minors"] = [
                cid for cid in me["hand_minors"]
                if self._minor_playable(state, me, cid)]
            view["playable_occupations"] = [
                cid for cid in me["hand_occupations"]
                if self._occupation_playable(state, me, cid)]
            view["occ_costs"] = {
                sid: self._occupation_cost(state, me, sid)
                for sid in ("lessons", "lessons_b")}
        view["valid_actions"] = self.get_valid_actions(state, player_id)
        return view

    def get_spectator_view(self, state):
        view = deepcopy(state)
        view["your_player_id"] = None
        view["valid_actions"] = []
        for p in view["players"]:
            p["hand_occupations"] = len(p["hand_occupations"])
            p["hand_minors"] = len(p["hand_minors"])
        return view

    def get_valid_actions(self, state, player_id):
        pidx = self._player_idx(state, player_id)
        if pidx is None or state["game_over"]:
            return []

        prompt = self._prompt(state)
        if prompt:
            if prompt["player"] != pidx:
                return []
            if prompt["type"] == "accommodate":
                return [{
                    "kind": "accommodate",
                    "gained": prompt["gained"],
                    "description": "Accommodate your animals (place, cook, or discard)",
                }]
            if prompt["type"] == "choice":
                return [{
                    "kind": "choice",
                    "card": prompt["card"],
                    "prompt": prompt["prompt"],
                    "options": prompt["options"],
                    "description": prompt["prompt"],
                }]
            return []

        p = state["players"][pidx]
        if state["phase"] == "feeding":
            if p["fed"]:
                return []
            return [{
                "kind": "feed",
                "food_needed": self._food_needed(state, p),
                "description": "Feed your family",
            }] + cards.card_actions(state, p)

        if state["phase"] == "work" and state["current_player"] == pidx:
            return self._placement_actions(state, pidx) + \
                cards.card_actions(state, p)

        return []

    def apply_action(self, state, player_id, action):
        state = deepcopy(state)
        pidx = self._player_idx(state, player_id)
        if pidx is None:
            raise ValueError("Unknown player")
        if state["game_over"]:
            raise ValueError("The game is over")

        kind = action.get("kind")
        if kind == "accommodate":
            return self._apply_accommodate(state, pidx, action)
        if kind == "choice":
            return self._apply_choice(state, pidx, action)
        if kind == "card_action":
            return self._apply_card_action(state, pidx, action)
        if kind == "feed":
            return self._apply_feed(state, pidx, action)
        if kind == "place":
            return self._apply_place(state, pidx, action)
        raise ValueError(f"Unknown action kind: {kind}")

    def get_waiting_for(self, state):
        if state["game_over"]:
            return []
        prompt = self._prompt(state)
        if prompt:
            return [state["players"][prompt["player"]]["player_id"]]
        if state["phase"] == "feeding":
            return [p["player_id"] for p in state["players"] if not p["fed"]]
        if state["phase"] == "work":
            return [state["players"][state["current_player"]]["player_id"]]
        return []

    def get_phase_info(self, state):
        rnd = state["round"]
        desc = ""
        prompt = self._prompt(state)
        if state["game_over"]:
            desc = "Game over"
        elif prompt:
            p = state["players"][prompt["player"]]
            desc = (f"{p['name']} accommodates animals"
                    if prompt["type"] == "accommodate"
                    else f"{p['name']} decides: {prompt.get('prompt', '')}")
        elif state["phase"] == "feeding":
            desc = "Harvest — feed your family"
        else:
            p = state["players"][state["current_player"]]
            desc = f"{p['name']} places a person"
        return {
            "phase": state["phase"],
            "round": rnd,
            "stage": state["stage"],
            "harvest": rnd in HARVEST_ROUNDS,
            "description": desc,
        }

    # ── Small helpers ────────────────────────────────────────────────

    def _player_idx(self, state, player_id):
        for p in state["players"]:
            if p["player_id"] == player_id:
                return p["index"]
        return None

    def _result(self, state, log=None):
        return ActionResult(new_state=state, log=log or [],
                            game_over=state.get("game_over", False))

    def _prompt(self, state):
        prompts = state.get("prompts") or []
        return prompts[0] if prompts else None

    def _space(self, state, space_id):
        for s in state["action_spaces"]:
            if s["id"] == space_id:
                return s
        return None

    def _pay(self, player, cost):
        for res, amount in cost.items():
            if player["resources"][res] < amount:
                raise ValueError(f"Not enough {res}")
        for res, amount in cost.items():
            player["resources"][res] -= amount

    def _can_afford(self, player, cost):
        return all(player["resources"][r] >= a for r, a in cost.items())

    def _rooms(self, player):
        return sum(1 for c in player["cells"] if c["type"] == "room")

    def _stables(self, player):
        return sum(1 for c in player["cells"] if c["stable"])

    def _pasture_cells(self, player):
        return {i for p in compute_pastures(player) for i in p}

    def _food_needed(self, state, player):
        adult_cost = 3 if state["player_count"] == 1 else 2
        adults = player["people_total"] - player["newborns"]
        return adult_cost * adults + player["newborns"]

    def _validate_animals(self, player):
        return validate_animal_placement(
            player,
            house_cap=cards.house_capacity(player),
            pasture_bonus=cards.pasture_bonus(player))

    def _cook_values(self, player):
        """Best cook table across major improvements and cards (or None)."""
        best = None
        tables = [MAJOR_IMPROVEMENTS[i].get("cook")
                  for i in player["improvements"]]
        tables += cards.card_cook_specs(player)
        for cook in tables:
            if cook:
                if best is None:
                    best = dict(cook)
                else:
                    for k, v in cook.items():
                        best[k] = max(best.get(k, 0), v)
        return best

    def _fire(self, state, event, actor_player, ctx, log, to_all=True):
        """Fire an event and apply any goods hooks placed in ctx["extra"]
        (extras always go to the acting player; animals via accommodation)."""
        ctx.setdefault("extra", {})
        ctx["log"] = log
        ctx["actor"] = actor_player["index"]
        if to_all:
            cards.fire(state, event, ctx)
        else:
            cards.fire_player(state, actor_player, event, ctx)
        self._apply_extras(state, actor_player, ctx["extra"], log)

    def _apply_extras(self, state, player, extra, log):
        animals = {}
        for good, amount in list(extra.items()):
            if amount <= 0:
                continue
            if good in ANIMAL_TYPES:
                animals[good] = animals.get(good, 0) + amount
            else:
                player["resources"][good] += amount
        if animals:
            self._gain_animals(state, player, animals, log)

    # ── Round setup ──────────────────────────────────────────────────

    def _start_round(self, state, log):
        state["round"] += 1
        rnd = state["round"]
        state["stage"] = stage_of_round(rnd)
        state["phase"] = "work"
        state["prompts"] = []
        log.append(f"— Round {rnd} (stage {state['stage']}) —")

        # Reveal this round's action space card.
        card_id = state["deck"][rnd - 1]
        card = STAGE_CARDS[card_id]
        state["revealed"].append(card_id)
        state["action_spaces"].append({
            "id": card_id,
            "name": card["name"],
            "desc": card["desc"],
            "stage": card["stage"],
            "occupied_by": None,
            "supply": {},
            "accumulates": bool(card.get("acc")),
        })
        log.append(f"New action space: {card['name']}")

        # Goods placed on this round space (Well, Pond Hut, Sack Cart, ...).
        payouts = state["round_goods"].pop(str(rnd), None)
        if payouts:
            for pidx_str, goods in payouts.items():
                p = state["players"][int(pidx_str)]
                for good, amount in goods.items():
                    p["resources"][good] += amount
                log.append(f"{p['name']} gets {cards.goods_str(goods)} "
                           "from the round space")

        # Replenish accumulation spaces.
        for space in state["action_spaces"]:
            acc = self._accumulation_of(state, space["id"])
            if acc:
                for good, amount in acc.items():
                    space["supply"][good] = space["supply"].get(good, 0) + amount
            space["occupied_by"] = None

        for p in state["players"]:
            p["people_placed"] = 0

        # Start-of-round card effects.
        for p in state["players"]:
            ctx = {"round": rnd, "log": log, "actor": p["index"], "extra": {}}
            cards.fire_player(state, p, "round_start", ctx)
            self._apply_extras(state, p, ctx["extra"], log)

        state["round_first_player"] = state["starting_player"]
        self._advance_work(state, log, state["starting_player"])

    def _accumulation_of(self, state, space_id):
        for spec in PERMANENT_SPACES:
            if spec["id"] == space_id:
                acc = spec.get("acc")
                if acc and space_id == "forest" and state["player_count"] == 1:
                    return {"wood": 2}  # solo rule
                return acc
        card = STAGE_CARDS.get(space_id)
        return card.get("acc") if card else None

    # ── Work phase flow ──────────────────────────────────────────────

    def _advance_work(self, state, log, start_pidx=None):
        """
        Move current_player to the next player (clockwise) who still has
        people to place and at least one usable action space. Players
        with people but no usable space forfeit their remaining
        placements. Ends the work phase when nobody can place.
        """
        n = state["player_count"]
        if start_pidx is None:
            start_pidx = (state["current_player"] + 1) % n

        for step in range(n):
            pidx = (start_pidx + step) % n
            p = state["players"][pidx]
            if p["people_placed"] >= p["people_total"]:
                continue
            if self._placement_actions(state, pidx):
                state["current_player"] = pidx
                return
            remaining = p["people_total"] - p["people_placed"]
            p["people_placed"] = p["people_total"]
            log.append(
                f"{p['name']} cannot use any action space and forfeits "
                f"{remaining} placement(s)")

        self._end_work_phase(state, log)

    def _end_work_phase(self, state, log):
        log.append("All people return home")
        if state["round"] in HARVEST_ROUNDS:
            self._start_harvest(state, log)
        else:
            self._end_round(state, log)

    def _start_harvest(self, state, log):
        state["harvest_index"] += 1
        log.append(f"— Harvest after round {state['round']} —")
        # Field phase: exactly 1 crop from every planted field (tiles + cards).
        for p in state["players"]:
            got = {"grain": 0, "vegetable": 0}
            for cell in p["cells"]:
                crops = cell.get("crops")
                if crops:
                    got[crops["type"]] += 1
                    crops["count"] -= 1
                    if crops["count"] <= 0:
                        cell["crops"] = None
            for inst in cards.card_fields(p):
                crops = inst.get("crops")
                if crops:
                    got[crops["type"]] += 1
                    crops["count"] -= 1
                    if crops["count"] <= 0:
                        inst["crops"] = None
            p["resources"]["grain"] += got["grain"]
            p["resources"]["vegetable"] += got["vegetable"]
            if got["grain"] or got["vegetable"]:
                parts = [f"{v} {k}" for k, v in got.items() if v]
                log.append(f"{p['name']} harvests {', '.join(parts)}")
            p["fed"] = False
            p["harvest_conversions_used"] = []
        # Harvest card effects (Loom, Scythe, Deaconess, ...).
        for p in state["players"]:
            ctx = {"harvest_index": state["harvest_index"], "log": log,
                   "actor": p["index"], "extra": {}}
            cards.fire_player(state, p, "harvest_field", ctx)
            self._apply_extras(state, p, ctx["extra"], log)
        state["phase"] = "feeding"
        log.append("Feeding phase — each player must feed their family")

    def _finish_harvest(self, state, log):
        # Breeding phase.
        for p in state["players"]:
            totals = animal_counts(p)
            for animal in ANIMAL_TYPES:
                if totals[animal] >= 2:
                    if self._place_newborn_animal(p, animal):
                        log.append(f"{p['name']}'s {animal} breed (+1 {animal})")
                    else:
                        log.append(
                            f"{p['name']}'s {animal} cannot breed (no room)")
        self._end_round(state, log)

    def _end_round(self, state, log):
        for p in state["players"]:
            p["newborns"] = 0
        if state["round"] >= TOTAL_ROUNDS:
            scores, winners = final_scores(state)
            state["scores"] = scores
            state["winners"] = winners
            state["game_over"] = True
            state["phase"] = "game_over"
            names = [state["players"][w]["name"] for w in winners]
            log.append("— Game over —")
            for s in scores:
                log.append(f"{s['name']}: {s['total']} points")
            log.append(f"Winner: {', '.join(names)}")
        else:
            self._start_round(state, log)

    # ── Placement actions (work phase) ───────────────────────────────

    def _placement_actions(self, state, pidx):
        p = state["players"][pidx]
        if state["phase"] != "work" or self._prompt(state):
            return []
        if p["people_placed"] >= p["people_total"]:
            return []

        actions = []
        for space in state["action_spaces"]:
            if space["occupied_by"] is not None:
                continue
            if self._space_usable(state, p, space):
                entry = {
                    "kind": "place",
                    "space": space["id"],
                    "name": space["name"],
                    "description": space["desc"],
                    "supply": dict(space["supply"]),
                }
                if space["id"] in ("lessons", "lessons_b"):
                    entry["occ_cost"] = self._occupation_cost(state, p, space["id"])
                actions.append(entry)
        return actions

    def _occupation_cost(self, state, player, space_id):
        if space_id == "lessons":
            base = 0 if player["occs_played"] == 0 else 1
        elif space_id == "lessons_b":
            if state["player_count"] >= 4:
                base = 1 if player["occs_played"] < 2 else 2
            else:
                base = 2
        else:
            base = 1
        return max(0, base + cards.occ_cost_delta(player))

    def _minor_playable(self, state, player, cid):
        spec = cards.CARDS.get(cid)
        if not spec or spec["type"] != "minor":
            return False
        if not cards.check_prereq(state, player, cid):
            return False
        return self._can_afford(player, spec["cost"])

    def _any_minor_playable(self, state, player):
        return any(self._minor_playable(state, player, cid)
                   for cid in player["hand_minors"])

    def _occupation_playable(self, state, player, cid):
        spec = cards.CARDS.get(cid)
        if not spec or spec["type"] != "occupation":
            return False
        return cards.check_prereq(state, player, cid)

    def _any_occupation_playable(self, state, player):
        return any(self._occupation_playable(state, player, cid)
                   for cid in player["hand_occupations"])

    def _space_usable(self, state, p, space):
        sid = space["id"]
        if space["accumulates"]:
            return True  # taking the goods is always a valid action
        if sid in ("grain_seeds", "day_laborer", "vegetable_seeds",
                   "resource_market_3p", "resource_market_4p",
                   "meeting_place"):
            return True
        if sid in ("lessons", "lessons_b"):
            cost = self._occupation_cost(state, p, sid)
            return (p["resources"]["food"] >= cost
                    and self._any_occupation_playable(state, p))
        if sid == "farmland":
            return bool(plowable_cells(p))
        if sid == "farm_expansion":
            room_ok = (self._can_afford(p, self._room_cost(state, p))
                       and self._buildable_room_cells(p))
            return bool(room_ok) or self._stable_possible(p)
        if sid == "fencing":
            # From scratch, the smallest pasture needs 4 fences
            # (cost modifiers like the Hedge Keeper can make them free).
            n = 1 if p["fences"] else 4
            cost = cards.modified_cost(state, p, "fences", {"wood": n},
                                       {"count": n})
            return (self._can_afford(p, cost)
                    and len(p["fences"]) < MAX_FENCES)
        if sid == "grain_utilization":
            return self._can_sow(p) or self._can_bake(p)
        if sid == "cultivation":
            return bool(plowable_cells(p)) or self._can_sow(p)
        if sid == "major_improvement":
            return bool(self._buildable_improvements(state, p)) or \
                self._any_minor_playable(state, p)
        if sid == "basic_wish":
            return (p["people_total"] < MAX_PEOPLE
                    and self._rooms(p) + cards.extra_rooms(p) > p["people_total"])
        if sid == "urgent_wish":
            return p["people_total"] < MAX_PEOPLE
        if sid == "house_redevelopment" or sid == "farm_redevelopment":
            return self._renovation_possible(state, p)
        return False

    def _apply_place(self, state, pidx, action):
        if state["phase"] != "work":
            raise ValueError("Not the work phase")
        if self._prompt(state):
            raise ValueError("A pending decision must be resolved first")
        if state["current_player"] != pidx:
            raise ValueError("Not your turn")

        p = state["players"][pidx]
        if p["people_placed"] >= p["people_total"]:
            raise ValueError("No people left to place")

        space = self._space(state, action.get("space"))
        if space is None:
            raise ValueError("Unknown action space")
        if space["occupied_by"] is not None:
            raise ValueError("That action space is occupied")
        if not self._space_usable(state, p, space):
            raise ValueError("You cannot use that action space")

        # Lasso: request a second placement immediately after this one.
        lasso = bool(action.get("lasso"))
        if lasso:
            if not cards.has_lasso(p):
                raise ValueError("You do not have the Lasso")
            if space["id"] not in ANIMAL_MARKETS:
                raise ValueError("The Lasso requires an animal market")

        log = [f"{p['name']} places a person on {space['name']}"]
        space["occupied_by"] = pidx
        p["people_placed"] += 1

        self._resolve_space(state, p, space, action, log)

        # Prompts (accommodation, choices) may be queued; otherwise move on.
        if not self._prompt(state):
            if lasso and p["people_placed"] < p["people_total"]:
                log.append(f"{p['name']} uses the Lasso to place again")
                self._advance_work(state, log, start_pidx=pidx)
            else:
                self._advance_work(state, log)
        else:
            state["prompts"][0]["after_lasso"] = lasso
        return self._result(state, log)

    def _resolve_space(self, state, p, space, action, log):
        sid = space["id"]
        provided = {}

        # Accumulation spaces (including the animal markets).
        if space["accumulates"]:
            goods = {k: v for k, v in space["supply"].items() if v > 0}
            space["supply"] = {}
            provided = goods
            animals_gained = {}
            for good, amount in goods.items():
                if good in ANIMAL_TYPES:
                    animals_gained[good] = amount
                else:
                    p["resources"][good] += amount
            if goods:
                log.append(f"{p['name']} takes {cards.goods_str(goods)}")
            self._fire_space_used(state, p, sid, provided, log,
                                  animals=animals_gained)
            return

        if sid == "grain_seeds":
            p["resources"]["grain"] += 1
            provided = {"grain": 1}
            log.append(f"{p['name']} gets 1 grain")
        elif sid == "vegetable_seeds":
            p["resources"]["vegetable"] += 1
            provided = {"vegetable": 1}
            log.append(f"{p['name']} gets 1 vegetable")
        elif sid == "day_laborer":
            p["resources"]["food"] += 2
            provided = {"food": 2}
            log.append(f"{p['name']} gets 2 food")
        elif sid == "resource_market_3p":
            choice = action.get("choice")
            if choice not in ("reed", "stone"):
                raise ValueError("Choose 1 reed or 1 stone")
            p["resources"][choice] += 1
            p["resources"]["food"] += 1
            provided = {choice: 1, "food": 1}
            log.append(f"{p['name']} gets 1 {choice} and 1 food")
        elif sid == "resource_market_4p":
            p["resources"]["reed"] += 1
            p["resources"]["stone"] += 1
            p["resources"]["food"] += 1
            provided = {"reed": 1, "stone": 1, "food": 1}
            log.append(f"{p['name']} gets 1 reed, 1 stone, and 1 food")
        elif sid == "meeting_place":
            state["starting_player"] = p["index"]
            log.append(f"{p['name']} becomes the starting player")
            if action.get("minor"):
                self._play_minor(state, p, action["minor"], log)
        elif sid in ("lessons", "lessons_b"):
            self._play_occupation(state, p, sid, action, log)
        elif sid == "farmland":
            self._do_plow(state, p, action.get("cell"), log)
            if action.get("bake"):
                if not cards.bake_on_space(p, sid):
                    raise ValueError("No card grants baking on Farmland")
                self._do_bake(state, p, action["bake"], log)
        elif sid == "farm_expansion":
            rooms = action.get("rooms") or []
            stables = action.get("stables") or []
            if not rooms and not stables:
                raise ValueError("Build at least one room or stable")
            if rooms:
                self._do_build_rooms(state, p, rooms, log)
            if stables:
                self._do_build_stables(state, p, stables, log=log)
        elif sid == "fencing":
            self._do_build_fences(state, p, action.get("fences"), log)
        elif sid == "grain_utilization":
            sow = action.get("sow") or []
            bake = action.get("bake")
            if not sow and not bake:
                raise ValueError("Sow and/or bake bread")
            if sow:
                self._do_sow(state, p, sow, log)
            if bake:
                self._do_bake(state, p, bake, log)
        elif sid == "cultivation":
            plow = action.get("plow")
            sow = action.get("sow") or []
            if plow is None and not sow:
                raise ValueError("Plow and/or sow")
            if plow is not None:
                self._do_plow(state, p, plow, log)
            if sow:
                self._do_sow(state, p, sow, log)
            if action.get("bake"):
                if not cards.bake_on_space(p, sid):
                    raise ValueError("No card grants baking on Cultivation")
                self._do_bake(state, p, action["bake"], log)
        elif sid == "major_improvement":
            if action.get("minor"):
                self._play_minor(state, p, action["minor"], log)
            elif action.get("improvement"):
                self._do_improvement(state, p, action, log)
            else:
                raise ValueError("Build a major improvement or play a minor one")
        elif sid in ("basic_wish", "urgent_wish"):
            if p["people_total"] >= MAX_PEOPLE:
                raise ValueError("You already have 5 people")
            if sid == "basic_wish" and \
                    self._rooms(p) + cards.extra_rooms(p) <= p["people_total"]:
                raise ValueError("You need more room than people")
            p["people_total"] += 1
            p["people_placed"] += 1  # the newborn does not act this round
            p["newborns"] += 1
            log.append(f"{p['name']}'s family grows by one")
            self._fire(state, "family_growth", p, {}, log)
            if sid == "basic_wish" and action.get("minor"):
                self._play_minor(state, p, action["minor"], log)
        elif sid == "house_redevelopment":
            self._do_renovate(state, p, action, log)
            if action.get("minor"):
                self._play_minor(state, p, action["minor"], log)
            elif action.get("improvement"):
                self._do_improvement(state, p, action, log)
        elif sid == "farm_redevelopment":
            self._do_renovate(state, p, action, log)
            if action.get("fences"):
                self._do_build_fences(state, p, action["fences"], log)
        else:
            raise ValueError(f"Unhandled action space: {sid}")

        self._fire_space_used(state, p, sid, provided, log)

    def _fire_space_used(self, state, p, sid, provided, log, animals=None):
        """Fire the space_used event, then route all gained animals
        (from the space and from card extras) through accommodation."""
        ctx = {"space_id": sid, "goods": provided, "extra": {},
               "log": log, "actor": p["index"]}
        cards.fire(state, "space_used", ctx)
        gained = dict(animals or {})
        for good, amount in ctx["extra"].items():
            if amount <= 0:
                continue
            if good in ANIMAL_TYPES:
                gained[good] = gained.get(good, 0) + amount
            else:
                p["resources"][good] += amount
        if gained:
            self._gain_animals(state, p, gained, log)

    # ── Cards: playing occupations and minor improvements ───────────

    def _play_occupation(self, state, p, space_id, action, log):
        cid = action.get("card")
        if cid not in p["hand_occupations"]:
            raise ValueError("That occupation is not in your hand")
        spec = cards.CARDS[cid]
        if not cards.check_prereq(state, p, cid):
            raise ValueError(
                f"Prerequisite not met: {spec['prereq'][1]}")
        cost = self._occupation_cost(state, p, space_id)
        self._pay(p, {"food": cost})
        p["hand_occupations"].remove(cid)
        inst = cards.new_instance(cid)
        p["occupations"].append(inst)
        p["occs_played"] += 1
        log.append(f"{p['name']} plays the occupation "
                   f"\"{spec['name']}\" ({cost} food)")

        play_fn = spec["hooks"].get("play")
        if play_fn:
            ctx = {"params": action.get("params") or {}, "log": log,
                   "actor": p["index"], "extra": {}}
            play_fn(state, p, inst, ctx)
            self._apply_extras(state, p, ctx["extra"], log)

        self._fire(state, "occupation_played", p,
                   {"card_id": cid}, log)

    def _play_minor(self, state, p, minor, log):
        if not isinstance(minor, dict):
            raise ValueError("Invalid minor improvement action")
        cid = minor.get("card")
        if cid not in p["hand_minors"]:
            raise ValueError("That minor improvement is not in your hand")
        spec = cards.CARDS[cid]
        if not cards.check_prereq(state, p, cid):
            raise ValueError(
                f"Prerequisite not met: {spec['prereq'][1]}")
        self._pay(p, spec["cost"])
        p["hand_minors"].remove(cid)
        inst = cards.new_instance(cid)
        log.append(f"{p['name']} plays the minor improvement \"{spec['name']}\"")

        play_fn = spec["hooks"].get("play")
        if play_fn:
            ctx = {"params": minor.get("params") or {}, "log": log,
                   "actor": p["index"], "extra": {}}
            play_fn(state, p, inst, ctx)
            self._apply_extras(state, p, ctx["extra"], log)

        if spec["traveling"]:
            if state["player_count"] > 1:
                left = state["players"][(p["index"] + 1) % state["player_count"]]
                left["hand_minors"].append(cid)
                log.append(f"\"{spec['name']}\" travels to {left['name']}'s hand")
            else:
                log.append(f"\"{spec['name']}\" is removed from play (solo)")
        else:
            p["minors"].append(inst)
        self._fire(state, "minor_played", p, {"card_id": cid}, log)

    # ── Farm development ─────────────────────────────────────────────

    def _do_plow(self, state, p, cell, log):
        if not isinstance(cell, int) or not (0 <= cell < NUM_CELLS):
            raise ValueError("Choose a farmyard space to plow")
        if cell not in plowable_cells(p):
            raise ValueError("You cannot plow that space")
        p["cells"][cell]["type"] = "field"
        log.append(f"{p['name']} plows a field")
        self._fire(state, "plow", p, {"cell": cell}, log, to_all=False)

    def _room_cost(self, state, p):
        material = ROOM_COST_MATERIAL[p["house_type"]]
        return cards.modified_cost(state, p, "room",
                                   {material: 5, "reed": 2})

    def _buildable_room_cells(self, p, extra_rooms=()):
        pasture_cells = self._pasture_cells(p)
        rooms = {i for i, c in enumerate(p["cells"]) if c["type"] == "room"}
        rooms |= set(extra_rooms)
        eligible = []
        for i, c in enumerate(p["cells"]):
            if i in rooms or c["type"] != "empty" or c["stable"] or i in pasture_cells:
                continue
            if any(nb in rooms for nb in orthogonal_neighbors(i)):
                eligible.append(i)
        return eligible

    def _do_build_rooms(self, state, p, cells, log):
        cost = self._room_cost(state, p)
        built = []
        for cell in cells:
            if not isinstance(cell, int):
                raise ValueError("Invalid room space")
            if cell not in self._buildable_room_cells(p, built):
                raise ValueError("Rooms must go on empty spaces adjacent to your house")
            self._pay(p, cost)
            p["cells"][cell]["type"] = "room"
            built.append(cell)
        log.append(f"{p['name']} builds {len(built)} {p['house_type']} room(s)")
        self._fire(state, "rooms_built", p, {"cells": built}, log,
                   to_all=False)

    def _stable_possible(self, p):
        if self._stables(p) >= MAX_STABLES or p["resources"]["wood"] < 2:
            return False
        return any(c["type"] == "empty" and not c["stable"] for c in p["cells"])

    def _do_build_stables(self, state, p, cells, log):
        if len(cells) != len(set(cells)):
            raise ValueError("Duplicate stable spaces")
        for cell in cells:
            if not isinstance(cell, int) or not (0 <= cell < NUM_CELLS):
                raise ValueError("Invalid stable space")
            if self._stables(p) >= MAX_STABLES:
                raise ValueError("You only have 4 stables")
            c = p["cells"][cell]
            if c["type"] != "empty" or c["stable"]:
                raise ValueError("Stables need an empty space without a tile")
            self._pay(p, {"wood": 2})
            c["stable"] = True
        log.append(f"{p['name']} builds {len(cells)} stable(s)")
        self._fire(state, "stable_built", p, {"cells": list(cells)}, log,
                   to_all=False)

    def _do_build_fences(self, state, p, new_fences, log):
        if not new_fences or not isinstance(new_fences, list):
            raise ValueError("Choose fences to build")
        if len(new_fences) != len(set(new_fences)):
            raise ValueError("Duplicate fences")
        for e in new_fences:
            if e in p["fences"]:
                raise ValueError("Fence already built there")
        old_pastures = {tuple(pa) for pa in compute_pastures(p)}
        layout = p["fences"] + list(new_fences)
        ok, err, _pastures = validate_fence_layout(p, layout)
        if not ok:
            raise ValueError(err)
        cost = cards.modified_cost(state, p, "fences",
                                   {"wood": len(new_fences)},
                                   {"count": len(new_fences)})
        self._pay(p, cost)
        p["fences"] = sorted(layout)
        log.append(f"{p['name']} builds {len(new_fences)} fence(s)")

        new_pastures = [pa for pa in compute_pastures(p)
                        if tuple(pa) not in old_pastures]
        self._fire(state, "fences_built", p,
                   {"new_pastures": new_pastures}, log)

        # Subdividing can strand animals; force re-accommodation if so.
        ok, _err = self._validate_animals(p)
        if not ok and not any(pr["type"] == "accommodate"
                              and pr["player"] == p["index"]
                              for pr in state["prompts"]):
            state["prompts"].append(
                {"type": "accommodate", "player": p["index"], "gained": {}})
            log.append(f"{p['name']} must rearrange their animals")

    def _renovation_possible(self, state, p):
        target = RENOVATION_TARGET.get(p["house_type"])
        if not target:
            return False
        cost = cards.modified_cost(
            state, p, "renovation", {target: self._rooms(p), "reed": 1})
        return self._can_afford(p, cost)

    def _do_renovate(self, state, p, action, log):
        target = RENOVATION_TARGET.get(p["house_type"])
        if not target:
            raise ValueError("Your house is already stone")
        cost = cards.modified_cost(
            state, p, "renovation", {target: self._rooms(p), "reed": 1})
        self._pay(p, cost)
        p["house_type"] = target
        log.append(f"{p['name']} renovates to a {target} house")
        ctx = {"free_stable_cell": action.get("stable"), "log": log,
               "actor": p["index"], "extra": {}}
        cards.fire_player(state, p, "renovate", ctx)
        self._apply_extras(state, p, ctx["extra"], log)

    # ── Improvements, baking, sowing ─────────────────────────────────

    def _buildable_improvements(self, state, p):
        """Major improvement ids the player could get now (incl. upgrades)."""
        out = []
        owns_fireplace = any(i in FIREPLACES for i in p["improvements"])
        for imp in state["available_improvements"]:
            spec = MAJOR_IMPROVEMENTS[imp]
            cost = cards.modified_cost(state, p, "improvement", spec["cost"])
            if self._can_afford(p, cost):
                out.append(imp)
            elif imp in COOKING_HEARTHS and owns_fireplace:
                out.append(imp)  # via fireplace upgrade
        return out

    def _do_improvement(self, state, p, action, log):
        imp = action.get("improvement")
        if imp not in MAJOR_IMPROVEMENTS:
            raise ValueError("Unknown improvement")
        if imp not in state["available_improvements"]:
            raise ValueError("That improvement is taken")
        spec = MAJOR_IMPROVEMENTS[imp]

        if action.get("upgrade"):
            if imp not in COOKING_HEARTHS:
                raise ValueError("Only Cooking Hearths can be upgrades")
            fireplace = next((i for i in p["improvements"] if i in FIREPLACES), None)
            if fireplace is None:
                raise ValueError("You need a Fireplace to upgrade")
            p["improvements"].remove(fireplace)
            state["available_improvements"].append(fireplace)
            state["available_improvements"].sort()
            log.append(f"{p['name']} upgrades their Fireplace to a {spec['name']}")
        else:
            cost = cards.modified_cost(state, p, "improvement", spec["cost"])
            self._pay(p, cost)
            log.append(f"{p['name']} builds the {spec['name']}")

        state["available_improvements"].remove(imp)
        p["improvements"].append(imp)

        if spec.get("well"):
            rnd = state["round"]
            for r in range(rnd + 1, min(TOTAL_ROUNDS, rnd + 5) + 1):
                slot = state["round_goods"].setdefault(str(r), {}) \
                    .setdefault(str(p["index"]), {})
                slot["food"] = slot.get("food", 0) + 1
            log.append("The Well places food on the next round spaces")

        self._fire(state, "improvement_built", p, {"improvement": imp}, log)

        if spec.get("bake_on_build") and action.get("bake"):
            self._do_bake(state, p, action["bake"], log)

    def _bake_spec_of(self, p, key):
        """Bake spec for an owned improvement or in-play card, or None."""
        if key in MAJOR_IMPROVEMENTS:
            if key not in p["improvements"]:
                raise ValueError("You do not own that improvement")
            return MAJOR_IMPROVEMENTS[key].get("bake")
        if key in cards.CARDS:
            if not any(i["id"] == key for i in cards.in_play(p)):
                raise ValueError("You do not have that card in play")
            return cards.CARDS[key].get("bake")
        raise ValueError(f"Unknown baking improvement: {key}")

    def _can_bake(self, p):
        if p["resources"]["grain"] < 1:
            return False
        if any(MAJOR_IMPROVEMENTS[i].get("bake") for i in p["improvements"]):
            return True
        return any(cards.spec(i).get("bake") for i in cards.in_play(p))

    def _do_bake(self, state, p, bake, log):
        if not isinstance(bake, dict) or not bake:
            raise ValueError("Choose grain to bake")
        total_grain = 0
        food = 0
        for key, count in bake.items():
            spec = self._bake_spec_of(p, key)
            if not spec:
                raise ValueError(f"{key} cannot bake bread")
            if not isinstance(count, int) or count < 1:
                raise ValueError("Invalid grain count")
            limit, value = spec
            if limit is not None and count > limit:
                raise ValueError(f"That oven bakes at most {limit} grain")
            total_grain += count
            food += count * value
        if p["resources"]["grain"] < total_grain:
            raise ValueError("Not enough grain")
        food += cards.bake_bonus(p, total_grain)
        p["resources"]["grain"] -= total_grain
        p["resources"]["food"] += food
        log.append(f"{p['name']} bakes {total_grain} grain into {food} food")
        ctx = {"grain": total_grain, "log": log, "actor": p["index"],
               "extra": {}}
        cards.fire_player(state, p, "bake", ctx)
        self._apply_extras(state, p, ctx["extra"], log)

    def _empty_fields(self, p):
        """Sowable targets: field-tile cells and empty card fields."""
        cells = [i for i, c in enumerate(p["cells"])
                 if c["type"] == "field" and not c["crops"]]
        card_targets = [i for i in cards.card_fields(p) if not i["crops"]]
        return cells, card_targets

    def _can_sow(self, p):
        has_crop = p["resources"]["grain"] > 0 or p["resources"]["vegetable"] > 0
        cells, card_targets = self._empty_fields(p)
        return has_crop and bool(cells or card_targets)

    def _do_sow(self, state, p, sow, log):
        if not isinstance(sow, list) or not sow:
            raise ValueError("Choose fields to sow")
        seen_cells = set()
        seen_cards = set()
        sown = []
        counts = {"grain": 0, "vegetable": 0}
        for item in sow:
            crop = item.get("crop")
            if crop not in ("grain", "vegetable"):
                raise ValueError("Sow grain or vegetables")
            if p["resources"][crop] < 1:
                raise ValueError(f"Not enough {crop}")
            if "card" in item:
                cid = item["card"]
                inst = next((i for i in cards.card_fields(p)
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
                inst["crops"] = {"type": crop,
                                 "count": 3 if crop == "grain" else 2}
                sown.append((inst, crop))
            else:
                cell = item.get("cell")
                if not isinstance(cell, int) or not (0 <= cell < NUM_CELLS) \
                        or cell in seen_cells:
                    raise ValueError("Invalid field")
                seen_cells.add(cell)
                c = p["cells"][cell]
                if c["type"] != "field" or c["crops"]:
                    raise ValueError("You can only sow empty fields")
                c["crops"] = {"type": crop,
                              "count": 3 if crop == "grain" else 2}
                sown.append((cell, crop))
            p["resources"][crop] -= 1
            counts[crop] += 1
        parts = [f"{v} {k} field(s)" for k, v in counts.items() if v]
        log.append(f"{p['name']} sows {', '.join(parts)}")
        ctx = {"sown": sown, "log": log, "actor": p["index"], "extra": {}}
        cards.fire_player(state, p, "sow", ctx)
        self._apply_extras(state, p, ctx["extra"], log)

    # ── Animal husbandry ─────────────────────────────────────────────

    def _gain_animals(self, state, p, gained, log):
        """Gained animals must be accommodated before play continues.
        Merges into an existing pending prompt for the same player."""
        for pr in state["prompts"]:
            if pr["type"] == "accommodate" and pr["player"] == p["index"]:
                for a, n in gained.items():
                    pr["gained"][a] = pr["gained"].get(a, 0) + n
                return
        state["prompts"].append(
            {"type": "accommodate", "player": p["index"],
             "gained": dict(gained)})

    def _apply_accommodate(self, state, pidx, action):
        pending = self._prompt(state)
        if not pending or pending["type"] != "accommodate" \
                or pending["player"] != pidx:
            raise ValueError("Nothing to accommodate")
        p = state["players"][pidx]
        log = []

        gained = pending["gained"]
        available = animal_counts(p)
        for a, n in gained.items():
            available[a] += n

        cook = action.get("cook") or {}
        discard = action.get("discard") or {}
        placements = action.get("placements") or []
        pets = action.get("pets") or {}

        # Cook first (only Fireplace/Cooking Hearth-style cooking works
        # at any time).
        cook_values = self._cook_values(p)
        food = 0
        for animal, count in cook.items():
            if animal not in ANIMAL_TYPES:
                raise ValueError("You can only cook animals here")
            if not isinstance(count, int) or count < 1:
                raise ValueError("Invalid cook count")
            if not cook_values or animal not in cook_values:
                raise ValueError("You need a Fireplace or Cooking Hearth to cook animals")
            if count > available[animal]:
                raise ValueError(f"Not enough {animal} to cook")
            available[animal] -= count
            food += count * cook_values[animal]
        if food:
            p["resources"]["food"] += food
            log.append(f"{p['name']} cooks animals for {food} food")

        for animal, count in discard.items():
            if animal not in ANIMAL_TYPES:
                raise ValueError("Invalid discard")
            if not isinstance(count, int) or count < 0 or count > available[animal]:
                raise ValueError(f"Cannot discard that many {animal}")
            available[animal] -= count
            if count:
                log.append(f"{p['name']} returns {count} {animal} to the supply")

        # Remaining animals must be fully placed.
        self._apply_animal_placement(p, placements, pets, available)
        lasso = pending.get("after_lasso")
        state["prompts"].pop(0)

        if state["phase"] == "work" and not self._prompt(state):
            if lasso and p["people_placed"] < p["people_total"]:
                log.append(f"{p['name']} uses the Lasso to place again")
                self._advance_work(state, log, start_pidx=pidx)
            else:
                self._advance_work(state, log)
        return self._result(state, log)

    def _apply_animal_placement(self, p, placements, pets, totals):
        """Replace all animal placements; must place exactly `totals`."""
        for cell in p["cells"]:
            cell["animal"] = None
        p["pets"] = {}

        placed = {a: 0 for a in ANIMAL_TYPES}
        for animal, count in (pets or {}).items():
            if animal not in ANIMAL_TYPES:
                raise ValueError("Invalid pet")
            if not isinstance(count, int) or count < 0:
                raise ValueError("Invalid pet count")
            if count:
                p["pets"][animal] = count
                placed[animal] += count

        seen = set()
        for item in placements:
            cell = item.get("cell")
            animal = item.get("type")
            count = item.get("count")
            if animal not in ANIMAL_TYPES:
                raise ValueError("Invalid animal type")
            if not isinstance(count, int) or count < 1:
                raise ValueError("Invalid animal count")
            if not isinstance(cell, int) or not (0 <= cell < NUM_CELLS) or cell in seen:
                raise ValueError("Invalid animal placement")
            seen.add(cell)
            p["cells"][cell]["animal"] = {"type": animal, "count": count}
            placed[animal] += count

        if placed != totals:
            raise ValueError(
                "All animals must be placed, cooked, or discarded "
                f"(placed {placed}, expected {totals})")

        ok, err = self._validate_animals(p)
        if not ok:
            raise ValueError(err)

    def _place_newborn_animal(self, p, animal):
        """Try to accommodate one newborn animal; returns True if placed."""
        bonus = cards.pasture_bonus(p)
        pastures = compute_pastures(p)
        pasture_of = {}
        occupants = {}
        for pi, pasture in enumerate(pastures):
            occupants[pi] = {"type": None, "count": 0}
            for i in pasture:
                pasture_of[i] = pi
        for i, cell in enumerate(p["cells"]):
            a = cell.get("animal")
            if a and i in pasture_of:
                occ = occupants[pasture_of[i]]
                occ["type"] = a["type"]
                occ["count"] += a["count"]

        # Same-type pasture with headroom.
        for pi, pasture in enumerate(pastures):
            occ = occupants[pi]
            if occ["type"] == animal and \
                    occ["count"] < pasture_capacity(p, pasture, bonus):
                for i in pasture:
                    a = p["cells"][i]["animal"]
                    if a and a["type"] == animal:
                        a["count"] += 1
                        return True
        # Empty pasture.
        for pi, pasture in enumerate(pastures):
            if occupants[pi]["count"] == 0:
                p["cells"][pasture[0]]["animal"] = {"type": animal, "count": 1}
                return True
        # Empty unfenced stable.
        for i, cell in enumerate(p["cells"]):
            if (cell["stable"] and cell["type"] == "empty"
                    and not cell["animal"] and i not in pasture_of):
                cell["animal"] = {"type": animal, "count": 1}
                return True
        # House capacity.
        if sum(p["pets"].values()) < cards.house_capacity(p):
            p["pets"][animal] = p["pets"].get(animal, 0) + 1
            return True
        return False

    # ── Card prompts and activated abilities ─────────────────────────

    def _apply_choice(self, state, pidx, action):
        prompt = self._prompt(state)
        if not prompt or prompt["type"] != "choice" \
                or prompt["player"] != pidx:
            raise ValueError("Nothing to choose")
        index = action.get("index")
        if not isinstance(index, int) or not (0 <= index < len(prompt["options"])):
            raise ValueError("Invalid choice")
        p = state["players"][pidx]
        inst = next((i for i in cards.in_play(p)
                     if i["id"] == prompt["card"]), None)
        if inst is None:
            raise ValueError("Card no longer in play")
        fn = cards.spec(inst).get("resolve_choice")
        if fn is None:
            raise ValueError("Card cannot resolve choices")
        log = []
        ctx = {"index": index, "option": prompt["options"][index],
               "data": prompt.get("data") or {}, "log": log,
               "actor": pidx, "extra": {}}
        state["prompts"].pop(0)
        fn(state, p, inst, ctx)
        self._apply_extras(state, p, ctx["extra"], log)
        if state["phase"] == "work" and not self._prompt(state):
            if prompt.get("after_lasso") and \
                    p["people_placed"] < p["people_total"]:
                self._advance_work(state, log, start_pidx=pidx)
            else:
                self._advance_work(state, log)
        return self._result(state, log)

    def _apply_card_action(self, state, pidx, action):
        if self._prompt(state):
            raise ValueError("A pending decision must be resolved first")
        p = state["players"][pidx]
        if state["phase"] == "work" and state["current_player"] != pidx:
            raise ValueError("Not your turn")
        if state["phase"] == "feeding" and p["fed"]:
            raise ValueError("You already fed your family")
        inst = next((i for i in cards.in_play(p)
                     if i["id"] == action.get("card")), None)
        if inst is None:
            raise ValueError("You do not have that card in play")
        ca = cards.spec(inst).get("card_action")
        if not ca or not ca["available"](state, p, inst):
            raise ValueError("That card action is not available")
        log = []
        ctx = {"params": action.get("params") or {}, "log": log,
               "actor": pidx, "extra": {}}
        ca["apply"](state, p, inst, ctx)
        self._apply_extras(state, p, ctx["extra"], log)
        return self._result(state, log)

    # ── Feeding ──────────────────────────────────────────────────────

    def _apply_feed(self, state, pidx, action):
        if state["phase"] != "feeding":
            raise ValueError("Not the feeding phase")
        if self._prompt(state):
            raise ValueError("A pending decision must be resolved first")
        p = state["players"][pidx]
        if p["fed"]:
            raise ValueError("You already fed your family")
        log = []

        # Optional rearrangement of animals before conversions/breeding.
        if action.get("placements") is not None or action.get("pets") is not None:
            totals = animal_counts(p)
            self._apply_animal_placement(
                p, action.get("placements") or [], action.get("pets") or {},
                totals)
            log.append(f"{p['name']} rearranges their animals")

        cook_values = self._cook_values(p)
        raw = cards.raw_values(p)
        for conv in action.get("conversions") or []:
            good = conv.get("good")
            via = conv.get("via")
            count = conv.get("count")
            if not isinstance(count, int) or count < 1:
                raise ValueError("Invalid conversion count")
            if via == "raw":
                if good not in ("grain", "vegetable"):
                    raise ValueError("Only crops convert to food raw")
                if p["resources"][good] < count:
                    raise ValueError(f"Not enough {good}")
                p["resources"][good] -= count
                p["resources"]["food"] += count * raw[good]
            elif via == "cook":
                if not cook_values or good not in cook_values:
                    raise ValueError("You cannot cook that")
                if good == "vegetable":
                    if p["resources"]["vegetable"] < count:
                        raise ValueError("Not enough vegetables")
                    p["resources"]["vegetable"] -= count
                else:
                    self._remove_animals(p, good, count)
                p["resources"]["food"] += count * cook_values[good]
            elif via in ("joinery", "pottery", "basketmaker"):
                if via not in p["improvements"]:
                    raise ValueError(f"You do not own the {via}")
                if via in p["harvest_conversions_used"]:
                    raise ValueError("Already used this harvest")
                resource, value = MAJOR_IMPROVEMENTS[via]["harvest_food"]
                if good != resource or count != 1:
                    raise ValueError(f"The {via} converts exactly 1 {resource} per harvest")
                if p["resources"][resource] < 1:
                    raise ValueError(f"Not enough {resource}")
                p["resources"][resource] -= 1
                p["resources"]["food"] += value
                p["harvest_conversions_used"].append(via)
            elif isinstance(via, str) and ":" in via:
                # Card-provided conversion "<card_id>:<index>".
                match = next((c for key, c, _inst
                              in cards.conversion_options(p)
                              if key == via), None)
                if match is None:
                    raise ValueError("Unknown card conversion")
                limit = match.get("per_harvest")
                used = p["harvest_conversions_used"].count(via)
                if limit is not None and used + count > limit:
                    raise ValueError("Conversion limit reached this harvest")
                for _ in range(count):
                    for res, amount in match["give"].items():
                        if res in ANIMAL_TYPES:
                            self._remove_animals(p, res, amount)
                        elif p["resources"][res] < amount:
                            raise ValueError(f"Not enough {res}")
                        else:
                            p["resources"][res] -= amount
                    for res, amount in match["get"].items():
                        p["resources"][res] += amount
                    p["harvest_conversions_used"].append(via)
            else:
                raise ValueError("Unknown conversion")

        needed = self._food_needed(state, p)
        paid = min(needed, p["resources"]["food"])
        p["resources"]["food"] -= paid
        shortfall = needed - paid
        if shortfall > 0:
            p["begging"] += shortfall
            log.append(
                f"{p['name']} feeds {paid}/{needed} food and takes "
                f"{shortfall} begging marker(s)")
        else:
            log.append(f"{p['name']} feeds their family ({needed} food)")
        p["fed"] = True

        if all(pl["fed"] for pl in state["players"]):
            self._finish_harvest(state, log)
        return self._result(state, log)

    def _remove_animals(self, p, animal, count):
        """Remove `count` animals of a type from cells/pets (for cooking)."""
        remaining = count
        for cell in p["cells"]:
            a = cell.get("animal")
            if a and a["type"] == animal and remaining > 0:
                take = min(a["count"], remaining)
                a["count"] -= take
                remaining -= take
                if a["count"] <= 0:
                    cell["animal"] = None
        if remaining > 0 and p["pets"].get(animal):
            take = min(p["pets"][animal], remaining)
            p["pets"][animal] -= take
            if p["pets"][animal] <= 0:
                del p["pets"][animal]
            remaining -= take
        if remaining > 0:
            raise ValueError(f"Not enough {animal}")
