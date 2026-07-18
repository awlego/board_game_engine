"""
Agricola (Revised Edition) game engine — full game with hand cards.

Each player is dealt 7 occupations and 7 minor improvements (or drafts
them pick-and-pass style before round 1 when the room enables the
draft -- see draft.py). Card effects are implemented via the
registry/hook architecture described in CARDS.md; the engine fires
events and asks modifier queries but never references individual cards.

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
    ANIMAL_TYPES, MAX_PEOPLE, MAX_FENCES,
    NUM_CELLS, TOTAL_ROUNDS, HARVEST_ROUNDS,
    PERMANENT_SPACES, STAGE_CARDS, MAJOR_IMPROVEMENTS,
    stage_of_round, build_stage_deck, create_player, create_action_spaces,
    compute_pastures,
    validate_animal_placement, animal_counts,
    plowable_cells,
)
from server.agricola import bot
from server.agricola import cards
from server.agricola import draft
from server.agricola import sub_actions
from server.agricola.scoring import final_scores

# Register all compendium deck modules at import time so restored
# rooms (persistence) can resolve card specs without a fresh deal.
cards.load_decks()

# Kept as aliases: a few tests/decks referenced these engine-local names
# before the build/renovate transactions moved to sub_actions.py.
ROOM_COST_MATERIAL = sub_actions.ROOM_COST_MATERIAL
RENOVATION_TARGET = sub_actions.RENOVATION_TARGET

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

    @staticmethod
    def validate_card_set(card_ids):
        """GameServer save_card_set hook: check that every id in a
        custom card set is an implemented occupation or minor
        improvement (majors are the fixed supply board, never dealt or
        drafted). Returns the ids unchanged; raises ValueError naming
        the first few offenders so the set builder can show them."""
        cards.load_decks()
        compendium = cards.compendium()
        bad = {}
        for cid in card_ids:
            spec = cards.CARDS.get(cid)
            if spec is not None and spec["type"] in ("occupation", "minor"):
                continue
            db = compendium.get(cid)
            if db and db["type"] == "major":
                bad[cid] = "major improvements can't be in a card set"
            elif spec is not None or db:
                bad[cid] = "not implemented yet"
            else:
                bad[cid] = "unknown id"
        if bad:
            shown = [f"{cid} ({why})" for cid, why in list(bad.items())[:5]]
            more = f" and {len(bad) - 5} more" if len(bad) > 5 else ""
            raise ValueError("Invalid cards: " + ", ".join(shown) + more)
        return card_ids

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
        if n == 3:
            # House rule: the player going third starts with 4 food
            # (printed rule gives every non-starting player 3).
            players[(starting + 2) % 3]["resources"]["food"] = 4

        decks = options.get("decks") or list(self.DEFAULT_DECKS)
        known = cards.implemented_decks()
        decks = [d for d in decks if d in known] or list(self.DEFAULT_DECKS)

        # A custom card set (options["card_pool"], resolved from a
        # saved set by GameServer.create_room) replaces deck selection
        # entirely: the deal/draft pool is exactly those ids.
        pool = options.get("card_pool")
        if not (isinstance(pool, list) and pool):
            pool = None

        # With a draft, the "hands" deal_hands returns are the draft
        # packets instead; players' hands start empty and grow as they
        # pick. deal_hands' auto-shrink (decks too small) clamps the
        # packet size, and keep clamps to that.
        draft_cfg = draft.resolve_options(options)
        rng = random.Random(random.randrange(2 ** 31))
        occ_hands, minor_hands, hand_size, occ_draw, minor_draw = \
            cards.deal_hands(n, rng, decks,
                             hand_size=draft_cfg["deal"] if draft_cfg else 7,
                             pool=pool)
        if draft_cfg:
            draft_cfg["deal"] = hand_size
            draft_cfg["keep"] = min(draft_cfg["keep"], hand_size)
            hand_size = draft_cfg["keep"]
        else:
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
            # Persistent draw/discard piles (engine phase 12) -- see
            # cards.deal_hands/draw_minors/draw_occupations/
            # discard_hand_minors/discard_hand_occupations. Top of a draw
            # pile is index 0; there is no reshuffle-when-empty.
            "occupation_draw": occ_draw,
            "minor_draw": minor_draw,
            "occupation_discard": [],
            "minor_discard": [],
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
        if pool is not None and options.get("card_set_name"):
            state["card_set"] = options["card_set_name"]
        if draft_cfg:
            if draft.start(state, draft_cfg, occ_hands, minor_hands, []):
                self._finish_draft(state, [])
        else:
            self._start_round(state, [])
        return state

    def _finish_draft(self, state, log):
        del state["draft"]
        self._start_round(state, log)

    # Draw/discard piles are shuffled/hidden state for EVERY player, not
    # just opponents (unlike hands, where only the owner may see their
    # own) -- see decks/GUIDE.md's "Hand and deck" section. The stage
    # deck (future round-card order) is hidden the same way: only
    # "revealed" is public knowledge. (An I238 Chamberlain-style card
    # that reveals it to one player will need a per-player exception.)
    _PILE_KEYS = ("occupation_draw", "minor_draw",
                 "occupation_discard", "minor_discard", "deck",
                 "removed_cards")

    def _hide_draw_piles(self, view):
        for key in self._PILE_KEYS:
            if key in view:
                view[key] = len(view[key])

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
        self._hide_draw_piles(view)
        draft.redact_view(view, pidx)
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
        self._hide_draw_piles(view)
        draft.redact_view(view, None)
        return view

    def get_valid_actions(self, state, player_id):
        pidx = self._player_idx(state, player_id)
        if pidx is None or state["game_over"]:
            return []

        if state["phase"] == "draft":
            return draft.valid_actions(state, pidx)

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
                self._skip_actions(state, pidx) + \
                cards.card_actions(state, p)

        return []

    def bot_turn(self, state, player_id, rng):
        """Random Bot: pick and apply one random legal action (bot.py)."""
        return bot.bot_turn(self, state, player_id, rng)

    def apply_action(self, state, player_id, action):
        state = deepcopy(state)
        pidx = self._player_idx(state, player_id)
        if pidx is None:
            raise ValueError("Unknown player")
        if state["game_over"]:
            raise ValueError("The game is over")

        kind = action.get("kind")
        if kind == "draft_pick":
            return self._apply_draft_pick(state, pidx, action)
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
        if kind == "skip":
            return self._apply_skip(state, pidx, action)
        raise ValueError(f"Unknown action kind: {kind}")

    def _apply_draft_pick(self, state, pidx, action):
        if state["phase"] != "draft":
            raise ValueError("No draft in progress")
        log = []
        if draft.apply_pick(state, pidx, action.get("card"), log):
            self._finish_draft(state, log)
        return self._result(state, log)

    def get_waiting_for(self, state):
        if state["game_over"]:
            return []
        if state["phase"] == "draft":
            return [state["players"][i]["player_id"]
                    for i in draft.waiting_on(state)]
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
        elif state["phase"] == "draft":
            desc = draft.phase_description(state)
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

    def _card_space_inst(self, state, space):
        """The live card instance behind a `card_space` action space, or
        None if the card has since left play. No removal path exists yet
        (see CARDS.md), but `_space_usable`/`_resolve_space`/
        `_accumulation_of` all go through this so a future one degrades
        gracefully (space unusable / accumulation skipped / placement
        rejected) instead of crashing."""
        owner = state["players"][space["owner"]]
        return next((i for i in cards.in_play(owner) if i["id"] == space["card"]),
                    None)

    def _capacity(self, player):
        """Placements a player may make this round: people plus any
        guest tokens. Never people_total itself -- feeding, scoring, and
        family-growth room checks all read people_total directly and
        must not see guests."""
        return player["people_total"] + player.get("guests", 0)

    def _space_occupants(self, space):
        """All player indices with a person on `space` this round. Kept
        as `occupied_by` (the first occupant, for backward compatibility
        with every existing reader) plus `extra_occupants` (additional
        occupants placed there via a card's `occupied_ok`)."""
        occ = [space["occupied_by"]] if space["occupied_by"] is not None else []
        return occ + space.get("extra_occupants", [])

    # _pay/_can_afford/_rooms/_stables/_pasture_cells and the
    # build/renovate/improvement/sow/occupation/minor transactions below
    # are thin wrappers: the canonical implementation lives in
    # sub_actions.py so card hooks/card_actions can invoke the exact same
    # code (see that module's docstring).
    def _pay(self, player, cost):
        sub_actions.pay(player, cost)

    def _can_afford(self, player, cost):
        return sub_actions.can_afford(player, cost)

    def _rooms(self, player):
        return sub_actions.rooms(player)

    def _stables(self, player):
        return sub_actions.stables(player)

    def _pasture_cells(self, player):
        return sub_actions.pasture_cells(player)

    def _food_needed(self, state, player):
        adult_cost = 3 if state["player_count"] == 1 else 2
        adults = player["people_total"] - player["newborns"]
        return adult_cost * adults + player["newborns"]

    def _validate_animals(self, state, player):
        return validate_animal_placement(
            player,
            house_cap=cards.house_capacity(state, player),
            pasture_cap=lambda cells, atype: cards.pasture_capacity(
                state, player, cells, atype),
            unfenced_stable_cap=lambda atype: cards.unfenced_stable_capacity(
                state, player, atype),
            secondary_types=lambda info: cards.pasture_secondary_types(
                state, player, info))

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
        sub_actions.apply_extras(state, player, extra, log)

    def _fire_any(self, state, event, actor_player, fields, log):
        """Broadcast the "<event>_any" twin of an owner-only event (fired
        via fire_player elsewhere) to every player's cards, so "each time
        ANOTHER player renovates/plows/sows/builds/bakes..." cards can
        react. ctx is `fields` plus the usual actor/log/extra; extras
        (like _fire) only reach the acting player -- a card belonging to
        someone else must use cards.grant_goods instead."""
        sub_actions.fire_any(state, event, actor_player, fields, log)

    def _fire_converted(self, state, p, give, get, via, log):
        """Fire `converted`: goods were converted to other goods outside
        a normal action-space grant (feeding-phase conversions, cooking
        during accommodation). Broadcast like space_used so "each time
        another player converts..." cards can observe it; extras only
        reach the converting player (p)."""
        ctx = {"give": dict(give), "get": dict(get), "via": via}
        self._fire(state, "converted", p, ctx, log)

    # ── Round setup ──────────────────────────────────────────────────

    def _start_round(self, state, log):
        state["round"] += 1
        rnd = state["round"]
        state["stage"] = stage_of_round(rnd)
        state["phase"] = "work"
        # Prompts must never be wiped here: a returning_home hook (fired
        # by the previous round's _end_work_phase) or a round_start hook
        # from an earlier player this same round may have queued one
        # that hasn't been answered yet. _end_work_phase and
        # _advance_work now refuse to cascade into a new round while any
        # prompt is pending (they stash where to resume and stop), so
        # state["prompts"] is always empty by the time we get here.
        # Assert instead of silently wiping so a future regression fails
        # loudly instead of discarding a card's queued decision.
        assert not state["prompts"], \
            "_start_round entered with unresolved prompts pending"
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
            "extra_occupants": [],
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
                # A gained hook may queue a prompt this early in the round
                # (choice, or an animal grant); _advance_work below stalls
                # on it via _pending_work_start the same way a round_start
                # hook's prompt does, instead of cascading past it.
                cards.fire_gained(state, p, goods, "round_goods", log)

        # Replenish accumulation spaces.
        for space in state["action_spaces"]:
            acc = self._accumulation_of(state, space)
            if acc:
                for good, amount in acc.items():
                    space["supply"][good] = space["supply"].get(good, 0) + amount
            space["occupied_by"] = None
            space["extra_occupants"] = []

        for p in state["players"]:
            p["people_placed"] = 0
            # Guest tokens don't carry over: an unused guest from a card
            # played earlier is gone. This resets before round_start
            # hooks fire below, so a hook may still grant one for the
            # round it's firing in.
            p["guests"] = 0

        # Start-of-round card effects.
        for p in state["players"]:
            ctx = {"round": rnd, "log": log, "actor": p["index"], "extra": {}}
            cards.fire_player(state, p, "round_start", ctx)
            self._apply_extras(state, p, ctx["extra"], log)

        state["round_first_player"] = state["starting_player"]
        self._advance_work(state, log, state["starting_player"])

    def _accumulation_of(self, state, space):
        """Goods to add to `space` in a replenish pass. For a `card_space`,
        the card's own spec's "acc" (a dict, or fn(state, owner_player,
        inst) -> dict) is always evaluated for the card's OWNER,
        regardless of who ends up placing there; unaffected by the
        Forest-only solo halving rule below, which is keyed on the literal
        "forest" id."""
        space_id = space["id"]
        if space.get("card_space"):
            inst = self._card_space_inst(state, space)
            if inst is None:
                return None
            owner = state["players"][space["owner"]]
            acc = cards.spec(inst)["card_space"].get("acc")
            return acc(state, owner, inst) if callable(acc) else acc
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

        If a prompt (choice/accommodate) is already pending -- e.g. a
        round_start hook queued one before anyone placed -- this must
        NOT run the forfeit loop: every player's placement query is
        legitimately empty while a prompt blocks them, and that is not
        the same as "cannot use any space". Stash where we meant to
        resume and stop; _apply_choice/_apply_accommodate re-invoke this
        (via the default start_pidx lookup below) once the prompt queue
        drains.
        """
        n = state["player_count"]
        if start_pidx is None:
            start_pidx = state.pop("_pending_work_start", None)
            if start_pidx is None:
                # One-shot first-placer override (I260 Taster-style: see
                # decks/GUIDE.md's "Turn structure" section). A
                # round_start hook's resolve_choice may set this to let
                # one player place ahead of the normal rotation, then
                # have rotation resume from wherever it actually belongs
                # once that placement is made. Consumed exactly once,
                # right here -- the first _advance_work call that falls
                # all the way through to this fallback (an intervening
                # prompt stall re-stashes the already-resolved value into
                # _pending_work_start above, so it is never read twice).
                start_pidx = state.pop("_resume_from", None)
                if start_pidx is None:
                    start_pidx = (state["current_player"] + 1) % n

        if self._prompt(state):
            state["_pending_work_start"] = start_pidx
            return

        for step in range(n):
            pidx = (start_pidx + step) % n
            p = state["players"][pidx]
            capacity = self._capacity(p)
            if p["people_placed"] >= capacity:
                continue
            # A player with no usable action space may still have a
            # skip-placement option (D053 Tea House-style) -- that's a
            # real choice, not "nobody can place", so don't forfeit them.
            if self._placement_actions(state, pidx) or \
                    self._skip_actions(state, pidx):
                state["current_player"] = pidx
                return
            remaining = capacity - p["people_placed"]
            p["people_placed"] = capacity
            if cards.placement_blocked(state, p):
                log.append(
                    f"{p['name']} cannot place any people this round and "
                    f"forfeits {remaining} placement(s)")
            else:
                log.append(
                    f"{p['name']} cannot use any action space and forfeits "
                    f"{remaining} placement(s)")

        self._end_work_phase(state, log)

    def _end_work_phase(self, state, log):
        log.append("All people return home")
        # Fire once per player, before occupied_by is reset by the next
        # _start_round (or ever, if the game just ended). This is the
        # single place the work phase ends on every round, harvest or
        # not, so the event can't double-fire.
        for p in state["players"]:
            spaces = [s["id"] for s in state["action_spaces"]
                      if p["index"] in self._space_occupants(s)]
            ctx = {"spaces": spaces, "log": log, "actor": p["index"],
                   "extra": {}}
            cards.fire_player(state, p, "returning_home", ctx)
            self._apply_extras(state, p, ctx["extra"], log)
        # A returning_home hook may have queued a prompt (choice, or an
        # animal grant via ctx["extra"]). Don't cascade into harvest/the
        # next round until it's answered -- stash that we still owe this
        # decision; _apply_choice/_apply_accommodate call
        # _finish_end_work_phase once the prompt queue drains.
        if self._prompt(state):
            state["_end_work_phase_pending"] = True
            return
        self._finish_end_work_phase(state, log)

    def _finish_end_work_phase(self, state, log):
        if state["round"] in HARVEST_ROUNDS:
            self._start_harvest(state, log)
        else:
            self._end_round(state, log)

    def _start_harvest(self, state, log):
        state["harvest_index"] += 1
        log.append(f"— Harvest after round {state['round']} —")
        # Fire harvest_start (D070: "before the field phase, you can pay
        # 1 grain to add 1 vegetable to up to 2 fields") before any crop
        # moves. harvest_index must already be bumped above so ctx is
        # correct.
        for p in state["players"]:
            ctx = {"harvest_index": state["harvest_index"], "log": log,
                   "actor": p["index"], "extra": {}}
            cards.fire_player(state, p, "harvest_start", ctx)
            self._apply_extras(state, p, ctx["extra"], log)
        # A harvest_start hook may have queued a prompt (a choice, or an
        # animal grant via extra). The field phase must not run until
        # it's resolved -- mirrors _end_work_phase_pending.
        if self._prompt(state):
            state["_field_phase_pending"] = True
            return
        self._run_field_phase(state, log)

    def _run_field_phase(self, state, log):
        # Field phase: exactly 1 crop from every planted field (tiles +
        # cards), unless cards.keep_crops_on_harvest says to leave a crop
        # type on the field (I226-style: counted/credited but not removed).
        breakdowns = {}
        for p in state["players"]:
            got = {"grain": 0, "vegetable": 0}
            tiles = {"grain": 0, "vegetable": 0}
            card_fields_got = {"grain": 0, "vegetable": 0}
            keep = cards.keep_crops_on_harvest(state, p)
            for cell in p["cells"]:
                crops = cell.get("crops")
                if crops:
                    got[crops["type"]] += 1
                    tiles[crops["type"]] += 1
                    if crops["type"] not in keep:
                        crops["count"] -= 1
                        if crops["count"] <= 0:
                            cell["crops"] = None
            for inst in cards.card_fields(p):
                # Each of a card field's stacks (default 1; K105/FR089-
                # style multi-stack cards have more) yields its own crop
                # independently -- see cards.field_stacks.
                for si, crops in enumerate(cards.field_stacks(inst)):
                    if crops:
                        got[crops["type"]] += 1
                        card_fields_got[crops["type"]] += 1
                        if crops["type"] not in keep:
                            crops["count"] -= 1
                            if crops["count"] <= 0:
                                cards.set_field_stack(inst, si, None)
            p["resources"]["grain"] += got["grain"]
            p["resources"]["vegetable"] += got["vegetable"]
            if got["grain"] or got["vegetable"]:
                parts = [f"{v} {k}" for k, v in got.items() if v]
                log.append(f"{p['name']} harvests {', '.join(parts)}")
            p["fed"] = False
            p["harvest_conversions_used"] = []
            # Crops are never animals, so firing here (rather than
            # batched into the hook loop below) can't interleave an
            # accommodation prompt with another player's crop math.
            harvested = {k: v for k, v in got.items() if v}
            if harvested:
                cards.fire_gained(state, p, harvested, "harvest", log)
            breakdowns[p["index"]] = (got, tiles, card_fields_got)
        # Harvest card effects (Loom, Scythe, Deaconess, ...). Fires for
        # every player even with zero yield (existing behavior).
        for p in state["players"]:
            got, tiles, card_fields_got = breakdowns[p["index"]]
            ctx = {"harvest_index": state["harvest_index"], "got": got,
                   "tiles": tiles, "card_fields": card_fields_got,
                   "log": log, "actor": p["index"], "extra": {}}
            cards.fire_player(state, p, "harvest_field", ctx)
            self._apply_extras(state, p, ctx["extra"], log)
        state["phase"] = "feeding"
        log.append("Feeding phase — each player must feed their family")

    def _finish_harvest(self, state, log):
        # Transient phase, distinct from "feeding": a breeding/gained hook
        # here may grant more animals, which queues the normal
        # accommodate prompt. If phase stayed "feeding", resolving that
        # prompt would hit _apply_accommodate's "phase == feeding and all
        # fed" dispatch and call _finish_harvest a second time --
        # re-breeding the same animals. "breeding" routes that dispatch
        # to _end_round instead (see _apply_choice/_apply_accommodate).
        state["phase"] = "breeding"
        for p in state["players"]:
            totals = animal_counts(p)
            placed = {}
            unplaced = {}
            for animal in ANIMAL_TYPES:
                if totals[animal] >= 2:
                    if self._place_newborn_animal(state, p, animal):
                        placed[animal] = placed.get(animal, 0) + 1
                        log.append(f"{p['name']}'s {animal} breed (+1 {animal})")
                    else:
                        unplaced[animal] = unplaced.get(animal, 0) + 1
                        log.append(
                            f"{p['name']}'s {animal} cannot breed (no room)")
            if placed:
                cards.fire_gained(state, p, placed, "breeding", log)
            ctx = {"newborns": placed, "unplaced": unplaced,
                   "harvest_index": state["harvest_index"], "log": log,
                   "actor": p["index"], "extra": {}}
            cards.fire_player(state, p, "breeding", ctx)
            self._apply_extras(state, p, ctx["extra"], log)
        if self._prompt(state):
            # A breeding/gained hook queued a prompt (a choice, or an
            # animal grant); stop here. _apply_choice/_apply_accommodate
            # call _end_round once phase is still "breeding" and no
            # prompt remains.
            return
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
        if p["people_placed"] >= self._capacity(p):
            return []
        if cards.placement_blocked(state, p):
            return []

        actions = []
        for space in state["action_spaces"]:
            occupants = self._space_occupants(space)
            if occupants:
                # Already occupied: only usable via a card's occupied_ok,
                # and never a second time by this same player (matches
                # every real card's "not with 2 of your own people" text).
                if pidx in occupants or not cards.occupied_ok(state, p, space):
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

    def _skip_actions(self, state, pidx):
        """Skip-placement actions (D053 Tea House: "skip placing your
        second person and get 1 food instead; place them later"). Each
        in-play card's `skip_turn=fn(state, player, inst) -> gain dict |
        None` spec key is queried; a truthy return offers `{"kind":
        "skip", "card": <cid>, "gain": {...}}`. The card's own fn is
        responsible for its own conditions (once per round via
        `inst["data"]`, "only your Nth person" via `people_placed`,
        ...) -- this method only adds the one engine-level guard no
        card should have to re-implement: at least one OTHER player must
        still have an unplaced person, or skipping would be a no-op turn
        (nobody left to place ahead of) that could recur every time this
        player is revisited."""
        if state["phase"] != "work" or self._prompt(state):
            return []
        p = state["players"][pidx]
        if p["people_placed"] >= self._capacity(p):
            return []
        if cards.placement_blocked(state, p):
            return []
        others_remaining = any(
            state["players"][i]["people_placed"] < self._capacity(state["players"][i])
            for i in range(state["player_count"]) if i != pidx)
        if not others_remaining:
            return []
        actions = []
        for inst in cards.in_play(p):
            fn = cards.spec(inst).get("skip_turn")
            if not fn:
                continue
            gain = fn(state, p, inst)
            if gain:
                actions.append({"kind": "skip", "card": inst["id"],
                                "gain": dict(gain)})
        return actions

    def _occupation_cost(self, state, player, space_id):
        return sub_actions.lessons_occupation_cost(state, player, space_id)

    def _minor_playable(self, state, player, cid):
        return sub_actions.can_play_minor(state, player, cid)

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
        if space.get("card_space"):
            return self._card_space_usable(state, p, space)
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
            room_ok = (self._can_afford(p, self._room_cost(state, p, 1))
                       and self._buildable_room_cells(p))
            return bool(room_ok) or self._stable_possible(state, p)
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
                    and self._rooms(p) + cards.extra_rooms(state, p) > p["people_total"])
        if sid == "urgent_wish":
            return p["people_total"] < MAX_PEOPLE
        if sid == "house_redevelopment" or sid == "farm_redevelopment":
            return self._renovation_possible(state, p)
        return False

    def _card_space_usable(self, state, p, space):
        """A `card_space` is usable if: its card is still in play, the
        owner_only gate (if set) is satisfied, the spec's own `usable`
        gate (if any) passes, and -- for an accumulation card space --
        its supply is non-empty (unlike the flat "always True" every
        OTHER accumulation space gets: those are guaranteed non-empty by
        the time anyone can place, since _start_round replenishes before
        the first placement of the round; a card_space can be appended
        mid-round, after that round's replenish already ran, with an
        empty supply until the next round_start)."""
        inst = self._card_space_inst(state, space)
        if inst is None:
            return False
        card_spec = cards.spec(inst)["card_space"]
        if card_spec.get("owner_only") and p["index"] != space["owner"]:
            return False
        usable_fn = card_spec.get("usable")
        if usable_fn and not usable_fn(state, p, inst):
            return False
        if space["accumulates"]:
            return any(v > 0 for v in space["supply"].values())
        return True

    def _apply_place(self, state, pidx, action):
        if state["phase"] != "work":
            raise ValueError("Not the work phase")
        if self._prompt(state):
            raise ValueError("A pending decision must be resolved first")
        if state["current_player"] != pidx:
            raise ValueError("Not your turn")

        p = state["players"][pidx]
        if p["people_placed"] >= self._capacity(p):
            raise ValueError("No people left to place")
        if cards.placement_blocked(state, p):
            raise ValueError("You cannot place people this round")

        space = self._space(state, action.get("space"))
        if space is None:
            raise ValueError("Unknown action space")
        occupants = self._space_occupants(space)
        if occupants:
            if pidx in occupants:
                raise ValueError(
                    "You already have a person on that action space")
            if not cards.occupied_ok(state, p, space):
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
        if space["occupied_by"] is None:
            space["occupied_by"] = pidx
        else:
            space.setdefault("extra_occupants", []).append(pidx)
        p["people_placed"] += 1

        self._resolve_space(state, p, space, action, log)

        # Prompts (accommodation, choices) may be queued; otherwise move on.
        if not self._prompt(state):
            if lasso and p["people_placed"] < self._capacity(p):
                log.append(f"{p['name']} uses the Lasso to place again")
                self._advance_work(state, log, start_pidx=pidx)
            else:
                self._advance_work(state, log)
        else:
            state["prompts"][0]["after_lasso"] = lasso
        return self._result(state, log)

    def _apply_skip(self, state, pidx, action):
        """D053 Tea House-style skip-placement action: instead of
        placing a person now, credit the card's `skip_turn` gain and
        move on -- `people_placed` is deliberately left untouched (the
        placement is deferred, not forfeited), so this player is
        revisited later in the same round's rotation with their
        capacity intact (see `_advance_work` and `_skip_actions`)."""
        if state["phase"] != "work":
            raise ValueError("Not the work phase")
        if self._prompt(state):
            raise ValueError("A pending decision must be resolved first")
        if state["current_player"] != pidx:
            raise ValueError("Not your turn")

        p = state["players"][pidx]
        if p["people_placed"] >= self._capacity(p):
            raise ValueError("No people left to place")
        if cards.placement_blocked(state, p):
            raise ValueError("You cannot place people this round")
        others_remaining = any(
            state["players"][i]["people_placed"] < self._capacity(state["players"][i])
            for i in range(state["player_count"]) if i != pidx)
        if not others_remaining:
            raise ValueError(
                "No other player has a placement left to skip ahead of")

        inst = next((i for i in cards.in_play(p)
                     if i["id"] == action.get("card")), None)
        if inst is None:
            raise ValueError("You do not have that card in play")
        skip_fn = cards.spec(inst).get("skip_turn")
        if not skip_fn:
            raise ValueError("That card does not support skipping a turn")
        gain = skip_fn(state, p, inst)
        if not gain:
            raise ValueError("You cannot skip your placement right now")

        log = [f"{p['name']} skips placing a person using "
              f"\"{cards.spec(inst)['name']}\" ({cards.goods_str(gain)})"]
        self._apply_extras(state, p, dict(gain), log)

        after_skip = cards.spec(inst).get("after_skip")
        if after_skip:
            after_skip(state, p, inst, log)

        if not self._prompt(state):
            self._advance_work(state, log)
        return self._result(state, log)

    def _resolve_space(self, state, p, space, action, log):
        sid = space["id"]
        provided = {}

        if space.get("card_space"):
            inst = self._card_space_inst(state, space)
            if inst is None:
                raise ValueError("That space is no longer available")
            resolve_fn = cards.spec(inst)["card_space"].get("resolve")
            if resolve_fn:
                # The resolve fn does its own special mechanics (tolls to
                # the owner via cards.grant_goods, choices via `action`,
                # ValueError on invalid input) and returns only the goods
                # it wants credited to the placing player `p` -- the
                # engine credits those the normal way (see GUIDE.md's
                # card_space section for the exact contract).
                provided = {g: v for g, v in
                           (resolve_fn(state, p, inst, action, log) or {}).items()
                           if v}
                animals_gained = {g: v for g, v in provided.items()
                                  if g in ANIMAL_TYPES}
                for good, amount in provided.items():
                    if good not in ANIMAL_TYPES:
                        p["resources"][good] += amount
                cards.fire_gained(state, p, provided, "space", log, space_id=sid)
                self._fire_space_used(state, p, sid, provided, log,
                                      animals=animals_gained)
                return
            # No resolve fn: a pure accumulation card space (E164-style)
            # -- falls through to the generic accumulation branch below,
            # identical to every other accumulation space.

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
            # Fire gained (goods may include animals) at receipt time,
            # before the accommodate prompt for those animals is even
            # queued -- matches "when you receive that type of animal"
            # cards, which react to the receipt, not the placement.
            cards.fire_gained(state, p, goods, "space", log, space_id=sid)
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
                self._play_minor(state, p, action["minor"], log, space_id=sid)
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
            payment = action.get("payment")
            if rooms:
                self._do_build_rooms(state, p, rooms, log, space_id=sid,
                                     payment=payment)
            if stables:
                self._do_build_stables(state, p, stables, log=log,
                                       space_id=sid, payment=payment)
        elif sid == "fencing":
            self._do_build_fences(state, p, action.get("fences"), log,
                                  space_id=sid, payment=action.get("payment"),
                                  tokens=action.get("tokens"))
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
                self._play_minor(state, p, action["minor"], log, space_id=sid)
            elif action.get("improvement"):
                self._do_improvement(state, p, action, log, space_id=sid)
            else:
                raise ValueError("Build a major improvement or play a minor one")
        elif sid in ("basic_wish", "urgent_wish"):
            sub_actions.family_growth(state, p, log,
                                      require_room=(sid == "basic_wish"))
            if sid == "basic_wish" and action.get("minor"):
                self._play_minor(state, p, action["minor"], log, space_id=sid)
        elif sid == "house_redevelopment":
            self._do_renovate(state, p, action, log, space_id=sid)
            if action.get("minor"):
                self._play_minor(state, p, action["minor"], log, space_id=sid)
            elif action.get("improvement"):
                self._do_improvement(state, p, action, log, space_id=sid)
        elif sid == "farm_redevelopment":
            self._do_renovate(state, p, action, log, space_id=sid)
            if action.get("fences"):
                self._do_build_fences(state, p, action["fences"], log,
                                      space_id=sid, payment=action.get("payment"),
                                      tokens=action.get("tokens"))
        else:
            raise ValueError(f"Unhandled action space: {sid}")

        # `provided` is only non-empty for the fixed-gain branches above
        # (grain_seeds, vegetable_seeds, day_laborer, resource_market_*);
        # none of them grant animals, so this is the whole "space"-sourced
        # gain (the accumulation branch above fires its own, and returns
        # before reaching here).
        if provided:
            cards.fire_gained(state, p, provided, "space", log, space_id=sid)
        self._fire_space_used(state, p, sid, provided, log)

    def _fire_space_used(self, state, p, sid, provided, log, animals=None):
        """Fire the space_used event, then route all gained animals
        (from the space and from card extras) through accommodation."""
        space = self._space(state, sid)
        occupants = self._space_occupants(space) if space else [p["index"]]
        ctx = {"space_id": sid, "goods": provided, "extra": {},
               "log": log, "actor": p["index"], "occupants": occupants}
        cards.fire(state, "space_used", ctx)
        if animals:
            self._gain_animals(state, p, animals, log)
        # Hook-granted extras (source "card") go through the shared
        # apply_extras hub, same as every other event's extras.
        self._apply_extras(state, p, ctx["extra"], log)

    # ── Cards: playing occupations and minor improvements ───────────

    def _play_occupation(self, state, p, space_id, action, log):
        cid = action.get("card")
        cost = self._occupation_cost(state, p, space_id)
        sub_actions.play_occupation(state, p, cid, log,
                                    params=action.get("params"),
                                    cost_override={"food": cost},
                                    cost_option=action.get("cost_option"))

    def _play_minor(self, state, p, minor, log, space_id=None):
        if not isinstance(minor, dict):
            raise ValueError("Invalid minor improvement action")
        cid = minor.get("card")
        sub_actions.play_minor(state, p, cid, log,
                               params=minor.get("params"),
                               ctx={"space_id": space_id,
                                    "payment": minor.get("payment")},
                               cost_option=minor.get("cost_option"))

    # ── Farm development ─────────────────────────────────────────────

    def _do_plow(self, state, p, cell, log):
        if not isinstance(cell, int) or not (0 <= cell < NUM_CELLS):
            raise ValueError("Choose a farmyard space to plow")
        if cell not in plowable_cells(p):
            raise ValueError("You cannot plow that space")
        p["cells"][cell]["type"] = "field"
        log.append(f"{p['name']} plows a field")
        self._fire(state, "plow", p, {"cell": cell}, log, to_all=False)
        self._fire_any(state, "plow", p, {"cell": cell}, log)

    def _room_cost(self, state, p, count=1):
        return sub_actions.room_cost(state, p, count)

    def _buildable_room_cells(self, p, extra_rooms=()):
        return sub_actions.buildable_room_cells(p, extra_rooms)

    def _do_build_rooms(self, state, p, cells, log, space_id=None, payment=None):
        sub_actions.build_rooms(state, p, cells, log,
                                ctx={"space_id": space_id, "payment": payment})

    def _stable_possible(self, state, p):
        return sub_actions.stable_possible(state, p)

    def _do_build_stables(self, state, p, cells, log, space_id=None, payment=None):
        sub_actions.build_stables(state, p, cells, log,
                                  ctx={"space_id": space_id, "payment": payment})

    def _do_build_fences(self, state, p, new_fences, log, space_id=None,
                         payment=None, tokens=None):
        sub_actions.build_fences(state, p, new_fences, log,
                                 ctx={"space_id": space_id, "payment": payment},
                                 tokens=tokens)

    def _renovation_possible(self, state, p):
        return sub_actions.renovation_possible(state, p)

    def _do_renovate(self, state, p, action, log, space_id=None):
        sub_actions.renovate(state, p, log,
                             free_stable_cell=action.get("stable"),
                             ctx={"space_id": space_id,
                                  "payment": action.get("payment")})

    # ── Improvements, baking, sowing ─────────────────────────────────

    def _buildable_improvements(self, state, p):
        """Major improvement ids the player could get now (incl. upgrades)."""
        return sub_actions.buildable_improvements(state, p)

    def _do_improvement(self, state, p, action, log, space_id=None):
        imp = action.get("improvement")
        spec = MAJOR_IMPROVEMENTS.get(imp)
        sub_actions.build_improvement(state, p, imp, log,
                                      upgrade=bool(action.get("upgrade")),
                                      ctx={"space_id": space_id,
                                           "payment": action.get("payment")})
        if spec and spec.get("bake_on_build") and action.get("bake"):
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
        if food:
            cards.fire_gained(state, p, {"food": food}, "bake", log)
        ctx = {"grain": total_grain, "log": log, "actor": p["index"],
               "extra": {}}
        cards.fire_player(state, p, "bake", ctx)
        self._apply_extras(state, p, ctx["extra"], log)
        self._fire_any(state, "bake", p, {"grain": total_grain}, log)

    def _empty_fields(self, p):
        """Sowable targets: field-tile cells and empty card fields."""
        return sub_actions.empty_fields(p)

    def _can_sow(self, p):
        return sub_actions.can_sow(p)

    def _do_sow(self, state, p, sow, log):
        sub_actions.sow(state, p, sow, log)

    # ── Animal husbandry ─────────────────────────────────────────────

    def _gain_animals(self, state, p, gained, log):
        """Gained animals must be accommodated before play continues.
        Merges into an existing pending prompt for the same player."""
        sub_actions.gain_animals(state, p, gained, log)

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
        converted_events = []
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
            gained = count * cook_values[animal]
            food += gained
            converted_events.append(({animal: count}, {"food": gained}))
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
        self._apply_animal_placement(state, p, placements, pets, available)
        lasso = pending.get("after_lasso")
        state["prompts"].pop(0)

        # Fire `converted` for each animal cooked, now that the prompt
        # this accommodation resolved is already popped: if a hook grants
        # more animals, _gain_animals queues a fresh prompt rather than
        # merging into (and then losing) the one we just resolved.
        for give, get in converted_events:
            self._fire_converted(state, p, give, get, "cook", log)
            cards.fire_gained(state, p, get, "convert", log)

        if not self._prompt(state) and state.pop("_end_work_phase_pending", False):
            # This prompt was the last thing blocking the round/harvest
            # transition (a returning_home hook queued it) -- resume
            # exactly where _end_work_phase left off.
            self._finish_end_work_phase(state, log)
        elif not self._prompt(state) and state.pop("_field_phase_pending", False):
            # Same pattern: a harvest_start hook's prompt was the last
            # thing blocking the field phase.
            self._run_field_phase(state, log)
        elif state["phase"] == "work" and not self._prompt(state):
            if lasso and p["people_placed"] < self._capacity(p):
                log.append(f"{p['name']} uses the Lasso to place again")
                self._advance_work(state, log, start_pidx=pidx)
            else:
                self._advance_work(state, log)
        elif state["phase"] == "feeding" and not self._prompt(state) and \
                all(pl["fed"] for pl in state["players"]):
            self._finish_harvest(state, log)
        elif state["phase"] == "breeding" and not self._prompt(state):
            # A breeding/gained hook's prompt was the last thing blocking
            # the round transition -- resume where _finish_harvest left off.
            self._end_round(state, log)
        return self._result(state, log)

    def _apply_animal_placement(self, state, p, placements, pets, totals):
        """Replace all animal placements (cells, pets, and holder-card
        storage -- `{"card": <card id>, "type": t, "count": n}` entries
        alongside the usual `{"cell": ..., "type": t, "count": n}`);
        must place exactly `totals`."""
        for cell in p["cells"]:
            cell["animal"] = None
        p["pets"] = {}
        for inst in cards.holder_cards(state, p):
            inst["held"] = {}

        placed = {a: 0 for a in ANIMAL_TYPES}
        for animal, count in (pets or {}).items():
            if animal not in ANIMAL_TYPES:
                raise ValueError("Invalid pet")
            if not isinstance(count, int) or count < 0:
                raise ValueError("Invalid pet count")
            if count:
                p["pets"][animal] = count
                placed[animal] += count

        seen_cells = set()
        for item in placements:
            animal = item.get("type")
            count = item.get("count")
            if animal not in ANIMAL_TYPES:
                raise ValueError("Invalid animal type")
            if not isinstance(count, int) or count < 1:
                raise ValueError("Invalid animal count")
            if "card" in item:
                inst = next((i for i in cards.in_play(p)
                            if i["id"] == item["card"]), None)
                if inst is None or not cards.spec(inst).get("holds_animals"):
                    raise ValueError("Invalid card animal storage")
                held = inst.setdefault("held", {})
                held[animal] = held.get(animal, 0) + count
                placed[animal] += count
                continue
            cell = item.get("cell")
            if not isinstance(cell, int) or not (0 <= cell < NUM_CELLS) \
                    or cell in seen_cells:
                raise ValueError("Invalid animal placement")
            seen_cells.add(cell)
            p["cells"][cell]["animal"] = {"type": animal, "count": count}
            placed[animal] += count

        if placed != totals:
            raise ValueError(
                "All animals must be placed, cooked, or discarded "
                f"(placed {placed}, expected {totals})")

        ok, err = self._validate_animals(state, p)
        if not ok:
            raise ValueError(err)
        ok, err = cards.validate_held(state, p)
        if not ok:
            raise ValueError(err)

    def _place_newborn_animal(self, state, p, animal):
        """Try to accommodate one newborn animal; returns True if placed.
        Order: same-type pasture headroom, an empty pasture, unfenced-
        stable headroom (same type or empty), the house, then a holder
        card with headroom. Newborns are never auto-placed as a
        pasture's secondary type (FR013-style allowances) -- that's a
        placement-time-only choice."""
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
                    occ["count"] < cards.pasture_capacity(state, p, pasture, animal):
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
        # Unfenced stable headroom (empty, or already holding this type).
        for i, cell in enumerate(p["cells"]):
            if cell["stable"] and cell["type"] == "empty" and i not in pasture_of:
                a = cell["animal"]
                if a is None:
                    cell["animal"] = {"type": animal, "count": 1}
                    return True
                if a["type"] == animal and \
                        a["count"] < cards.unfenced_stable_capacity(state, p, animal):
                    a["count"] += 1
                    return True
        # House capacity.
        if sum(p["pets"].values()) < cards.house_capacity(state, p):
            p["pets"][animal] = p["pets"].get(animal, 0) + 1
            return True
        # Card storage (e.g. C012 Cattle Farm: 1 cattle per pasture, on
        # the card itself).
        for inst in cards.holder_cards(state, p):
            rule = cards.spec(inst)["holds_animals"](state, p, inst) or {}
            types = rule.get("types")
            if types is not None and animal not in types:
                continue
            held = inst.setdefault("held", {})
            cap = types.get(animal) if types is not None else None
            if cap is not None and held.get(animal, 0) >= cap:
                continue
            total_cap = rule.get("total")
            if total_cap is not None and sum(held.values()) >= total_cap:
                continue
            held[animal] = held.get(animal, 0) + 1
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
        # Every other prompt_choice caller is a card's own hook reacting
        # to its owner's turn, so the card lives in the prompted player's
        # own in_play list -- but a card_space's "resolve" fn may prompt
        # the PLACING player (who may not be the card's owner) about a
        # choice tied to the space's own card. Card ids are unique per
        # game (CARDS.md), so search every player's in_play list, not
        # just the prompted one's.
        inst = next((i for pl in state["players"] for i in cards.in_play(pl)
                     if i["id"] == prompt["card"]), None)
        if inst is not None:
            fn = cards.spec(inst).get("resolve_choice")
        elif prompt.get("from_hand"):
            # E173-style `hand_react` prompt: the card hasn't been played
            # yet, so there's no instance to find -- fall back to the
            # CARDS registry spec directly. resolve_choice then receives
            # inst=None and must play the card itself (typically
            # sub_actions.play_occupation/play_minor with
            # cost_override="free") if the player accepts; declining
            # just leaves it in hand. See decks/GUIDE.md's "Hand
            # reactions" section for the full contract.
            fn = (cards.CARDS.get(prompt["card"]) or {}).get("resolve_choice")
        else:
            raise ValueError("Card no longer in play")
        if fn is None:
            raise ValueError("Card cannot resolve choices")
        log = []
        ctx = {"index": index, "option": prompt["options"][index],
               "data": prompt.get("data") or {}, "log": log,
               "actor": pidx, "extra": {}}
        state["prompts"].pop(0)
        fn(state, p, inst, ctx)
        self._apply_extras(state, p, ctx["extra"], log)
        if not self._prompt(state) and state.pop("_end_work_phase_pending", False):
            # This prompt was the last thing blocking the round/harvest
            # transition (a returning_home hook queued it) -- resume
            # exactly where _end_work_phase left off.
            self._finish_end_work_phase(state, log)
        elif not self._prompt(state) and state.pop("_field_phase_pending", False):
            # Same pattern: a harvest_start hook's prompt was the last
            # thing blocking the field phase.
            self._run_field_phase(state, log)
        elif state["phase"] == "work" and not self._prompt(state):
            if prompt.get("after_lasso") and \
                    p["people_placed"] < self._capacity(p):
                self._advance_work(state, log, start_pidx=pidx)
            else:
                self._advance_work(state, log)
        elif state["phase"] == "feeding" and not self._prompt(state) and \
                all(pl["fed"] for pl in state["players"]):
            self._finish_harvest(state, log)
        elif state["phase"] == "breeding" and not self._prompt(state):
            # A breeding/gained hook's prompt was the last thing blocking
            # the round transition -- resume where _finish_harvest left off.
            self._end_round(state, log)
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
                state, p, action.get("placements") or [], action.get("pets") or {},
                totals)
            log.append(f"{p['name']} rearranges their animals")

        cook_values = self._cook_values(p)
        raw = cards.raw_values(p)
        # Buffer "converted" events instead of firing mid-loop: a hook
        # granting goods (ctx["extra"]) must not run between two
        # conversion entries, where it could pollute the resource checks
        # (available amounts, harvest_conversions_used limits) later
        # entries in this same loop still rely on. Firing the whole
        # batch once the loop is done keeps the conversion math pure.
        converted_events = []
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
                gained = count * raw[good]
                p["resources"]["food"] += gained
                converted_events.append(({good: count}, {"food": gained}, "raw"))
            elif via == "cook":
                if not cook_values or good not in cook_values:
                    raise ValueError("You cannot cook that")
                if good == "vegetable":
                    if p["resources"]["vegetable"] < count:
                        raise ValueError("Not enough vegetables")
                    p["resources"]["vegetable"] -= count
                else:
                    self._remove_animals(p, good, count)
                gained = count * cook_values[good]
                p["resources"]["food"] += gained
                converted_events.append(({good: count}, {"food": gained}, "cook"))
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
                converted_events.append(({resource: 1}, {"food": value}, via))
            elif isinstance(via, str) and ":" in via:
                # Card-provided conversion "<card_id>:<index>".
                match = next((c for key, c, _inst
                              in cards.conversion_options(p)
                              if key == via), None)
                if match is None:
                    raise ValueError("Unknown card conversion")
                match_inst = next((i for key, _c, i
                                   in cards.conversion_options(p)
                                   if key == via), None)
                limit = match.get("per_harvest")
                used = p["harvest_conversions_used"].count(via)
                if limit is not None and used + count > limit:
                    raise ValueError("Conversion limit reached this harvest")
                total_give, total_get = {}, {}
                for _ in range(count):
                    for res, amount in match["give"].items():
                        if res in ANIMAL_TYPES:
                            self._remove_animals(p, res, amount)
                        elif p["resources"][res] < amount:
                            raise ValueError(f"Not enough {res}")
                        else:
                            p["resources"][res] -= amount
                        total_give[res] = total_give.get(res, 0) + amount
                    for res, amount in match["get"].items():
                        p["resources"][res] += amount
                        total_get[res] = total_get.get(res, 0) + amount
                    p["harvest_conversions_used"].append(via)
                converted_events.append(
                    (total_give, total_get, match_inst["id"]))
            else:
                raise ValueError("Unknown conversion")

        for give, get, conv_via in converted_events:
            self._fire_converted(state, p, give, get, conv_via, log)
            cards.fire_gained(state, p, get, "convert", log)

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

        # A "converted" hook could have queued an accommodation prompt
        # (extra animals); don't advance past feeding until it resolves.
        if all(pl["fed"] for pl in state["players"]) and not self._prompt(state):
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
