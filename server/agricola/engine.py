"""
Agricola (Revised Edition) game engine — base game, official
"without hand cards" beginner variant (see rules/rules.md).

Round flow: preparation (auto) → work (players place people one at a
time) → returning home (auto) → harvest on rounds 4/7/9/11/13/14
(field phase auto → feeding: every player submits a "feed" action →
breeding auto). Game ends after round 14's harvest.

Sub-decisions (accommodating newly gained animals) are modeled with a
single `state["pending"]` blocker that must be resolved by the target
player before play continues.
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
    player_cook_values,
)
from server.agricola.scoring import final_scores

ROOM_COST_MATERIAL = {"wood": "wood", "clay": "clay", "stone": "stone"}
RENOVATION_TARGET = {"wood": "clay", "clay": "stone"}


class AgricolaEngine(GameEngine):
    player_count_range = (1, 4)

    # ── Core interface ───────────────────────────────────────────────

    def initial_state(self, player_ids, player_names):
        n = len(player_ids)
        players = [create_player(i, pid, pname)
                   for i, (pid, pname) in enumerate(zip(player_ids, player_names))]

        starting = random.randrange(n)
        for p in players:
            if n == 1:
                p["resources"]["food"] = 0
            else:
                p["resources"]["food"] = 2 if p["index"] == starting else 3

        rng = random.Random(random.randrange(2 ** 31))
        state = {
            "game": "agricola",
            "player_ids": player_ids,
            "player_count": n,
            "players": players,
            "action_spaces": create_action_spaces(n),
            "deck": build_stage_deck(rng),
            "revealed": [],
            "round": 0,
            "stage": 1,
            "starting_player": starting,
            "round_first_player": starting,
            "current_player": starting,
            "phase": "work",
            "pending": None,
            "round_food": {},
            "available_improvements": sorted(MAJOR_IMPROVEMENTS.keys()),
            "game_over": False,
            "scores": None,
            "winners": None,
        }
        self._start_round(state, [])
        return state

    def get_player_view(self, state, player_id):
        # Open information — full state plus per-player context.
        view = deepcopy(state)
        view["your_player_id"] = player_id
        view["your_player_idx"] = self._player_idx(state, player_id)
        view["valid_actions"] = self.get_valid_actions(state, player_id)
        return view

    def get_valid_actions(self, state, player_id):
        pidx = self._player_idx(state, player_id)
        if pidx is None or state["game_over"]:
            return []

        pending = state.get("pending")
        if pending:
            if pending["player"] != pidx:
                return []
            return [{
                "kind": "accommodate",
                "gained": pending["gained"],
                "description": "Accommodate your animals (place, cook, or discard)",
            }]

        if state["phase"] == "feeding":
            p = state["players"][pidx]
            if p["fed"]:
                return []
            return [{
                "kind": "feed",
                "food_needed": self._food_needed(state, p),
                "description": "Feed your family",
            }]

        if state["phase"] == "work" and state["current_player"] == pidx:
            return self._placement_actions(state, pidx)

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
        if kind == "feed":
            return self._apply_feed(state, pidx, action)
        if kind == "place":
            return self._apply_place(state, pidx, action)
        raise ValueError(f"Unknown action kind: {kind}")

    def get_waiting_for(self, state):
        if state["game_over"]:
            return []
        pending = state.get("pending")
        if pending:
            return [state["players"][pending["player"]]["player_id"]]
        if state["phase"] == "feeding":
            return [p["player_id"] for p in state["players"] if not p["fed"]]
        if state["phase"] == "work":
            return [state["players"][state["current_player"]]["player_id"]]
        return []

    def get_phase_info(self, state):
        rnd = state["round"]
        desc = ""
        if state["game_over"]:
            desc = "Game over"
        elif state.get("pending"):
            p = state["players"][state["pending"]["player"]]
            desc = f"{p['name']} accommodates animals"
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

    # ── Round setup ──────────────────────────────────────────────────

    def _start_round(self, state, log):
        state["round"] += 1
        rnd = state["round"]
        state["stage"] = stage_of_round(rnd)
        state["phase"] = "work"
        state["pending"] = None
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

        # Food placed on this round space (Well).
        payouts = state["round_food"].pop(str(rnd), None)
        if payouts:
            for pidx_str, amount in payouts.items():
                p = state["players"][int(pidx_str)]
                p["resources"]["food"] += amount
                log.append(f"{p['name']} gets {amount} food from the round space (Well)")

        # Replenish accumulation spaces.
        for space in state["action_spaces"]:
            acc = self._accumulation_of(state, space["id"])
            if acc:
                for good, amount in acc.items():
                    space["supply"][good] = space["supply"].get(good, 0) + amount
            space["occupied_by"] = None

        for p in state["players"]:
            p["people_placed"] = 0

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
        log.append(f"— Harvest after round {state['round']} —")
        # Field phase: exactly 1 crop from every planted field.
        for p in state["players"]:
            got = {"grain": 0, "vegetable": 0}
            for cell in p["cells"]:
                crops = cell.get("crops")
                if crops:
                    got[crops["type"]] += 1
                    crops["count"] -= 1
                    if crops["count"] <= 0:
                        cell["crops"] = None
            p["resources"]["grain"] += got["grain"]
            p["resources"]["vegetable"] += got["vegetable"]
            if got["grain"] or got["vegetable"]:
                parts = [f"{v} {k}" for k, v in got.items() if v]
                log.append(f"{p['name']} harvests {', '.join(parts)}")
            p["fed"] = False
            p["harvest_conversions_used"] = []
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
        if state["phase"] != "work" or state.get("pending"):
            return []
        if p["people_placed"] >= p["people_total"]:
            return []

        actions = []
        for space in state["action_spaces"]:
            if space["occupied_by"] is not None:
                continue
            if self._space_usable(state, p, space):
                actions.append({
                    "kind": "place",
                    "space": space["id"],
                    "name": space["name"],
                    "description": space["desc"],
                    "supply": dict(space["supply"]),
                })
        return actions

    def _space_usable(self, state, p, space):
        sid = space["id"]
        if space["accumulates"]:
            return True  # taking the goods is always a valid action
        if sid in ("grain_seeds", "day_laborer", "vegetable_seeds",
                   "resource_market_3p", "resource_market_4p"):
            return True
        if sid == "farmland":
            return bool(self._plowable_cells(p))
        if sid == "farm_expansion":
            return bool(self._room_cost_ok(p) and self._buildable_room_cells(p)) or \
                self._stable_possible(p, wood_cost=2)
        if sid == "side_job":
            return self._stable_possible(p, wood_cost=1) or self._can_bake(p)
        if sid == "fencing":
            # From scratch, the smallest pasture needs 4 fences.
            min_wood = 1 if p["fences"] else 4
            return (p["resources"]["wood"] >= min_wood
                    and len(p["fences"]) < MAX_FENCES)
        if sid == "grain_utilization":
            return self._can_sow(p) or self._can_bake(p)
        if sid == "cultivation":
            return bool(self._plowable_cells(p)) or self._can_sow(p)
        if sid == "major_improvement":
            return bool(self._buildable_improvements(state, p))
        if sid == "basic_wish":
            return (p["people_total"] < MAX_PEOPLE
                    and self._rooms(p) > p["people_total"])
        if sid == "urgent_wish":
            return p["people_total"] < MAX_PEOPLE
        if sid == "house_redevelopment" or sid == "farm_redevelopment":
            return self._renovation_possible(p)
        return False

    def _apply_place(self, state, pidx, action):
        if state["phase"] != "work":
            raise ValueError("Not the work phase")
        if state.get("pending"):
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

        log = [f"{p['name']} places a person on {space['name']}"]
        space["occupied_by"] = pidx
        p["people_placed"] += 1

        self._resolve_space(state, p, space, action, log)

        # Animal accommodation may be pending; otherwise move on.
        if not state.get("pending"):
            self._advance_work(state, log)
        return self._result(state, log)

    def _resolve_space(self, state, p, space, action, log):
        sid = space["id"]

        # Accumulation spaces (including animal markets and meeting place).
        if space["accumulates"]:
            goods = {k: v for k, v in space["supply"].items() if v > 0}
            space["supply"] = {}
            animals_gained = {}
            for good, amount in goods.items():
                if good in ANIMAL_TYPES:
                    animals_gained[good] = amount
                else:
                    p["resources"][good] += amount
            if goods:
                parts = [f"{v} {k}" for k, v in goods.items()]
                log.append(f"{p['name']} takes {', '.join(parts)}")
            if sid == "meeting_place":
                state["starting_player"] = p["index"]
                log.append(f"{p['name']} becomes the starting player")
            if animals_gained:
                self._gain_animals(state, p, animals_gained, log)
            return

        if sid == "grain_seeds":
            p["resources"]["grain"] += 1
            log.append(f"{p['name']} gets 1 grain")
        elif sid == "vegetable_seeds":
            p["resources"]["vegetable"] += 1
            log.append(f"{p['name']} gets 1 vegetable")
        elif sid == "day_laborer":
            p["resources"]["food"] += 2
            log.append(f"{p['name']} gets 2 food")
        elif sid == "resource_market_3p":
            choice = action.get("choice")
            if choice not in ("reed", "stone"):
                raise ValueError("Choose 1 reed or 1 stone")
            p["resources"][choice] += 1
            p["resources"]["food"] += 1
            log.append(f"{p['name']} gets 1 {choice} and 1 food")
        elif sid == "resource_market_4p":
            p["resources"]["reed"] += 1
            p["resources"]["stone"] += 1
            p["resources"]["food"] += 1
            log.append(f"{p['name']} gets 1 reed, 1 stone, and 1 food")
        elif sid == "farmland":
            self._do_plow(p, action.get("cell"), log)
        elif sid == "farm_expansion":
            rooms = action.get("rooms") or []
            stables = action.get("stables") or []
            if not rooms and not stables:
                raise ValueError("Build at least one room or stable")
            if rooms:
                self._do_build_rooms(p, rooms, log)
            if stables:
                self._do_build_stables(p, stables, wood_cost=2, log=log)
        elif sid == "side_job":
            stable = action.get("stable")
            bake = action.get("bake")
            if stable is None and not bake:
                raise ValueError("Build a stable and/or bake bread")
            if stable is not None:
                self._do_build_stables(p, [stable], wood_cost=1, log=log)
            if bake:
                self._do_bake(p, bake, log)
        elif sid == "fencing":
            self._do_build_fences(state, p, action.get("fences"), log)
        elif sid == "grain_utilization":
            sow = action.get("sow") or []
            bake = action.get("bake")
            if not sow and not bake:
                raise ValueError("Sow and/or bake bread")
            if sow:
                self._do_sow(p, sow, log)
            if bake:
                self._do_bake(p, bake, log)
        elif sid == "cultivation":
            plow = action.get("plow")
            sow = action.get("sow") or []
            if plow is None and not sow:
                raise ValueError("Plow and/or sow")
            if plow is not None:
                self._do_plow(p, plow, log)
            if sow:
                self._do_sow(p, sow, log)
        elif sid == "major_improvement":
            self._do_improvement(state, p, action, log)
        elif sid in ("basic_wish", "urgent_wish"):
            if p["people_total"] >= MAX_PEOPLE:
                raise ValueError("You already have 5 people")
            if sid == "basic_wish" and self._rooms(p) <= p["people_total"]:
                raise ValueError("You need more rooms than people")
            p["people_total"] += 1
            p["people_placed"] += 1  # the newborn does not act this round
            p["newborns"] += 1
            log.append(f"{p['name']}'s family grows by one")
        elif sid == "house_redevelopment":
            self._do_renovate(p, log)
            if action.get("improvement"):
                self._do_improvement(state, p, action, log)
        elif sid == "farm_redevelopment":
            self._do_renovate(p, log)
            if action.get("fences"):
                self._do_build_fences(state, p, action["fences"], log)
        else:
            raise ValueError(f"Unhandled action space: {sid}")

    # ── Farm development ─────────────────────────────────────────────

    def _plowable_cells(self, p):
        pasture_cells = self._pasture_cells(p)
        fields = [i for i, c in enumerate(p["cells"]) if c["type"] == "field"]
        eligible = []
        for i, c in enumerate(p["cells"]):
            if c["type"] != "empty" or c["stable"] or i in pasture_cells:
                continue
            if fields and not any(nb in fields for nb in orthogonal_neighbors(i)):
                continue
            eligible.append(i)
        return eligible

    def _do_plow(self, p, cell, log):
        if not isinstance(cell, int) or not (0 <= cell < NUM_CELLS):
            raise ValueError("Choose a farmyard space to plow")
        if cell not in self._plowable_cells(p):
            raise ValueError("You cannot plow that space")
        p["cells"][cell]["type"] = "field"
        log.append(f"{p['name']} plows a field")

    def _room_cost_ok(self, p):
        material = ROOM_COST_MATERIAL[p["house_type"]]
        return p["resources"][material] >= 5 and p["resources"]["reed"] >= 2

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

    def _do_build_rooms(self, p, cells, log):
        material = ROOM_COST_MATERIAL[p["house_type"]]
        built = []
        for cell in cells:
            if not isinstance(cell, int):
                raise ValueError("Invalid room space")
            if cell not in self._buildable_room_cells(p, built):
                raise ValueError("Rooms must go on empty spaces adjacent to your house")
            self._pay(p, {material: 5, "reed": 2})
            p["cells"][cell]["type"] = "room"
            built.append(cell)
        log.append(f"{p['name']} builds {len(built)} {p['house_type']} room(s)")

    def _stable_possible(self, p, wood_cost):
        if self._stables(p) >= MAX_STABLES or p["resources"]["wood"] < wood_cost:
            return False
        return any(c["type"] == "empty" and not c["stable"] for c in p["cells"])

    def _do_build_stables(self, p, cells, wood_cost, log):
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
            self._pay(p, {"wood": wood_cost})
            c["stable"] = True
        log.append(f"{p['name']} builds {len(cells)} stable(s)")

    def _do_build_fences(self, state, p, new_fences, log):
        if not new_fences or not isinstance(new_fences, list):
            raise ValueError("Choose fences to build")
        if len(new_fences) != len(set(new_fences)):
            raise ValueError("Duplicate fences")
        for e in new_fences:
            if e in p["fences"]:
                raise ValueError("Fence already built there")
        layout = p["fences"] + list(new_fences)
        ok, err, _pastures = validate_fence_layout(p, layout)
        if not ok:
            raise ValueError(err)
        self._pay(p, {"wood": len(new_fences)})
        p["fences"] = sorted(layout)
        log.append(f"{p['name']} builds {len(new_fences)} fence(s)")

        # Subdividing can strand animals; force re-accommodation if so.
        ok, _err = validate_animal_placement(p)
        if not ok:
            state["pending"] = {"player": p["index"], "gained": {}}
            log.append(f"{p['name']} must rearrange their animals")

    def _renovation_possible(self, p):
        target = RENOVATION_TARGET.get(p["house_type"])
        if not target:
            return False
        return self._can_afford(p, {target: self._rooms(p), "reed": 1})

    def _do_renovate(self, p, log):
        target = RENOVATION_TARGET.get(p["house_type"])
        if not target:
            raise ValueError("Your house is already stone")
        self._pay(p, {target: self._rooms(p), "reed": 1})
        p["house_type"] = target
        log.append(f"{p['name']} renovates to a {target} house")

    # ── Improvements, baking, sowing ─────────────────────────────────

    def _buildable_improvements(self, state, p):
        """Improvement ids the player could get right now (incl. upgrades)."""
        out = []
        owns_fireplace = any(i in FIREPLACES for i in p["improvements"])
        for imp in state["available_improvements"]:
            spec = MAJOR_IMPROVEMENTS[imp]
            if self._can_afford(p, spec["cost"]):
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
            self._pay(p, spec["cost"])
            log.append(f"{p['name']} builds the {spec['name']}")

        state["available_improvements"].remove(imp)
        p["improvements"].append(imp)

        if spec.get("well"):
            rnd = state["round"]
            for r in range(rnd + 1, min(TOTAL_ROUNDS, rnd + 5) + 1):
                payouts = state["round_food"].setdefault(str(r), {})
                key = str(p["index"])
                payouts[key] = payouts.get(key, 0) + 1
            log.append("The Well places food on the next round spaces")

        if spec.get("bake_on_build") and action.get("bake"):
            self._do_bake(p, action["bake"], log)

    def _can_bake(self, p):
        if p["resources"]["grain"] < 1:
            return False
        return any(MAJOR_IMPROVEMENTS[i].get("bake") for i in p["improvements"])

    def _do_bake(self, p, bake, log):
        if not isinstance(bake, dict) or not bake:
            raise ValueError("Choose grain to bake")
        total_grain = 0
        food = 0
        for imp, count in bake.items():
            if imp not in p["improvements"]:
                raise ValueError("You do not own that improvement")
            spec = MAJOR_IMPROVEMENTS[imp].get("bake")
            if not spec:
                raise ValueError(f"{imp} cannot bake bread")
            if not isinstance(count, int) or count < 1:
                raise ValueError("Invalid grain count")
            limit, value = spec
            if limit is not None and count > limit:
                raise ValueError(f"{MAJOR_IMPROVEMENTS[imp]['name']} bakes at most {limit} grain")
            total_grain += count
            food += count * value
        if p["resources"]["grain"] < total_grain:
            raise ValueError("Not enough grain")
        p["resources"]["grain"] -= total_grain
        p["resources"]["food"] += food
        log.append(f"{p['name']} bakes {total_grain} grain into {food} food")

    def _can_sow(self, p):
        has_crop = p["resources"]["grain"] > 0 or p["resources"]["vegetable"] > 0
        has_field = any(c["type"] == "field" and not c["crops"] for c in p["cells"])
        return has_crop and has_field

    def _do_sow(self, p, sow, log):
        if not isinstance(sow, list) or not sow:
            raise ValueError("Choose fields to sow")
        seen = set()
        sown = {"grain": 0, "vegetable": 0}
        for item in sow:
            cell = item.get("cell")
            crop = item.get("crop")
            if crop not in ("grain", "vegetable"):
                raise ValueError("Sow grain or vegetables")
            if not isinstance(cell, int) or not (0 <= cell < NUM_CELLS) or cell in seen:
                raise ValueError("Invalid field")
            seen.add(cell)
            c = p["cells"][cell]
            if c["type"] != "field" or c["crops"]:
                raise ValueError("You can only sow empty fields")
            if p["resources"][crop] < 1:
                raise ValueError(f"Not enough {crop}")
            p["resources"][crop] -= 1
            c["crops"] = {"type": crop, "count": 3 if crop == "grain" else 2}
            sown[crop] += 1
        parts = [f"{v} {k} field(s)" for k, v in sown.items() if v]
        log.append(f"{p['name']} sows {', '.join(parts)}")

    # ── Animal husbandry ─────────────────────────────────────────────

    def _gain_animals(self, state, p, gained, log):
        """Gained animals must be accommodated before play continues."""
        state["pending"] = {"player": p["index"], "gained": gained}

    def _apply_accommodate(self, state, pidx, action):
        pending = state.get("pending")
        if not pending or pending["player"] != pidx:
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
        pet = action.get("pet")

        # Cook first (only Fireplace/Cooking Hearth cook "at any time").
        cook_values = player_cook_values(p)
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
        self._apply_animal_placement(p, placements, pet, available)
        state["pending"] = None

        if state["phase"] == "work":
            self._advance_work(state, log)
        return self._result(state, log)

    def _apply_animal_placement(self, p, placements, pet, totals):
        """Replace all animal placements; must place exactly `totals`."""
        for cell in p["cells"]:
            cell["animal"] = None
        p["pet"] = None

        placed = {a: 0 for a in ANIMAL_TYPES}
        if pet is not None:
            if pet not in ANIMAL_TYPES:
                raise ValueError("Invalid pet")
            p["pet"] = pet
            placed[pet] += 1

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

        ok, err = validate_animal_placement(p)
        if not ok:
            raise ValueError(err)

    def _place_newborn_animal(self, p, animal):
        """Try to accommodate one newborn animal; returns True if placed."""
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
            if occ["type"] == animal and occ["count"] < pasture_capacity(p, pasture):
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
        # Pet slot.
        if p["pet"] is None:
            p["pet"] = animal
            return True
        return False

    # ── Feeding ──────────────────────────────────────────────────────

    def _apply_feed(self, state, pidx, action):
        if state["phase"] != "feeding":
            raise ValueError("Not the feeding phase")
        if state.get("pending"):
            raise ValueError("A pending decision must be resolved first")
        p = state["players"][pidx]
        if p["fed"]:
            raise ValueError("You already fed your family")
        log = []

        # Optional rearrangement of animals before conversions/breeding.
        if action.get("placements") is not None or action.get("pet") is not None:
            totals = animal_counts(p)
            self._apply_animal_placement(
                p, action.get("placements") or [], action.get("pet"), totals)
            log.append(f"{p['name']} rearranges their animals")

        cook_values = player_cook_values(p)
        for conv in action.get("conversions") or []:
            good = conv.get("good")
            via = conv.get("via")
            count = conv.get("count")
            if not isinstance(count, int) or count < 1:
                raise ValueError("Invalid conversion count")
            if via == "raw":
                if good not in ("grain", "vegetable"):
                    raise ValueError("Only crops convert to 1 food raw")
                if p["resources"][good] < count:
                    raise ValueError(f"Not enough {good}")
                p["resources"][good] -= count
                p["resources"]["food"] += count
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
        """Remove `count` animals of a type from cells/pet (for cooking)."""
        remaining = count
        for cell in p["cells"]:
            a = cell.get("animal")
            if a and a["type"] == animal and remaining > 0:
                take = min(a["count"], remaining)
                a["count"] -= take
                remaining -= take
                if a["count"] <= 0:
                    cell["animal"] = None
        if remaining > 0 and p["pet"] == animal:
            p["pet"] = None
            remaining -= 1
        if remaining > 0:
            raise ValueError(f"Not enough {animal}")
