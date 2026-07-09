# Implementing compendium decks

How to implement Agricola cards from the General Compendium database.
Read `../CARDS.md` first for the architecture. This file is the working
reference for deck modules under `server/agricola/decks/`.

## Ground rules

1. **Card data comes from the DB.** `cards.compendium()[code]` has the
   parsed name/text/cost/vp/prereq/players for every compendium card.
   Register with `compendium_card(code, ...)` — it fills name, text,
   cost, points, min_players, and traveling from the DB. Pass overrides
   (e.g. `cost={"wood": 1}`) only when the parse is wrong or the cost
   is special. `parse_cost` raises on tokens it doesn't understand —
   that's your cue to hand-write the cost dict.
2. **Fidelity over coverage.** Implement the card's rules text (use the
   DB `rulings` to resolve ambiguity). If the engine can't express the
   effect faithfully, DO NOT approximate silently — add it to the
   module-level `UNIMPLEMENTED` dict with a one-line reason:
   `UNIMPLEMENTED["E42"] = "requires guest tokens"`.
3. **The engine never learns card names.** Everything goes through
   hooks, static ability keys, and queries. If you are tempted to edit
   `engine.py`, stop and mark the card unimplemented instead (note the
   missing mechanic in the reason — we extend the engine deliberately,
   not per-card).
4. Original-edition ("E/I/K/…") cards written for the original game
   map onto revised-engine terms: "wooden hut" → wood house; original
   majors (Fireplace A1/A2 etc.) → this engine's majors
   (`fireplace_2/3`, `cooking_hearth_4/5`, `clay_oven`, `stone_oven`,
   `joinery`, `pottery`, `basketmaker`, `well`).

## What the engine supports (spec keys and helpers)

Registration (all in `server.agricola.cards`):

```python
from server.agricola.cards import (
    compendium_card, card, CARDS, new_instance, spec, in_play,
    add_goods, goods_str, prompt_choice, parse_cost,
    take_bonus, space_bonus, round_income, schedule_on_play,
    harvest_food, on_play_gain, animal_totals_of,
    needs_occupations, exact_occupations, needs_grain_field, combine,
)
```

**Hook events** — `hooks={"<event>": fn}`, `fn(state, player, inst, ctx)`;
every ctx has `log` (append strings), `actor` (acting player index) and
usually `extra` (goods dict granted to the actor; animals are routed
through the accommodation prompt automatically):

| event | fires | extra ctx |
|---|---|---|
| `play` | when this card is played | `params` (client-supplied dict) |
| `space_used` | any player finishes an action space | `space_id`, `goods` the space provided, `occupants` (every player index with a person on that space *after* this placement — length > 1 only when a card's `occupied_ok` let someone place on top of an existing occupant) |
| `occupation_played` / `minor_played` | any player plays a card | `card_id` |
| `round_start` | preparation phase (own cards only) | `round` |
| `improvement_built` | any player builds/upgrades a major improvement | `improvement` id |
| `harvest_field` | field phase of each harvest (own) | `harvest_index` (1..6) |
| `fences_built` | after fences built | `new_pastures` (lists of cells) |
| `stable_built` | after own stables built | `cells` |
| `rooms_built` | after own rooms built | `cells` |
| `plow` | after own plow | `cell` |
| `sow` | after own sow action | `sown`: [(cell-or-inst, crop)] |
| `bake` | after own bake | `grain` count baked |
| `renovate` | after own renovation | `free_stable_cell` param |
| `family_growth` | own family growth | — |
| `converted` | any goods→goods conversion outside a normal action-space grant (feeding-phase conversions in the "feed" action; cooking animals during accommodation instead of placing them) | `give` (goods consumed), `get` (goods produced), `via` ("raw", "cook", an improvement id like "joinery", or the converting card's own id) |
| `returning_home` | once per player at the end of the work phase, before `occupied_by`/`extra_occupants` is reset (own cards only) | `spaces`: action-space ids that player's people occupy this round (includes spaces occupied only via `extra_occupants` -- see occupied-space placement below) |
| `renovate_any` / `plow_any` / `sow_any` / `rooms_built_any` / `stable_built_any` / `bake_any` | broadcast twin of the correspondingly-named owner-only event above, fired to **every** player's cards (not just the actor's) | same fields as the parent event, plus `actor` |

`converted` is broadcast like `space_used` (all players' cards, actor
first) so "each time another player converts..." cards work; filter on
`ctx["actor"]`. Timing: for the "feed" action, every conversion in the
request is applied first (so a hook can't run *between* two conversion
entries and pollute the resource checks or `harvest_conversions_used`
limits later entries in the same request still rely on), then all of
that request's `converted` events fire as a batch, before the food-need
calculation — so a hook that grants extra food via `ctx["extra"]` still
counts toward paying that feeding. For the accommodate-cook branch, the
event fires after the accommodation's own prompt has already been
popped, so a hook granting more animals queues a fresh prompt instead of
being merged into (and discarded with) the one just resolved.

**`returning_home` prompts are safe (verified in `tests/test_agricola.py`):**
calling `prompt_choice` (or granting animals via `ctx["extra"]`) from a
`returning_home` hook works correctly on both harvest and non-harvest
rounds. `_end_work_phase` checks for a pending prompt after firing every
player's `returning_home` hooks; if one is queued, it stops there
instead of cascading into `_start_harvest`/`_end_round`+`_start_round`,
and `get_waiting_for`/`get_valid_actions` hold the game on the prompt's
target player in the meantime. Once the prompt (or the last of several,
if more than one player's cards queued one) is answered, the engine
resumes exactly where it left off — into the harvest on a harvest
round, into the next round otherwise.

`resolve_choice` is **not** a hook: pass it as a top-level spec key
(`compendium_card(..., resolve_choice=fn)`); the engine reads
`spec.get("resolve_choice")` directly. Same signature, ctx carries
`index`, `option`, `data`.

`ctx["extra"]` only reaches the *acting* player. For an "each time ANY
player does X, the card owner gets Y" effect (owner != actor), use
`cards.grant_goods(state, player, gain)` instead of writing straight into
`player["resources"]` — it credits non-animal goods directly and queues
an accommodation prompt for animal goods, so a card that grants sheep to
some other player can't silently corrupt state. `on_play_gain` and
`space_bonus(..., others=True)` already use it internally.

Occupation `prereq=` is enforced the same way minor `prereq=` is:
`_play_occupation` calls `check_prereq` before paying/removing the card,
and `get_valid_actions` excludes an occupation-only space (Lessons) when
no card in hand has its prereq satisfied. Don't add a `raise` in a `play`
hook to re-check something already expressible as `prereq=` — only a
round-dependent or variable-cost condition needs that.

**Guest tokens (extra placements):** `cards.grant_guest(player, n=1)`
grants `player` n additional work-phase placements for the *current*
round only. It's a plain counter (`player["guests"]`), completely
separate from `people_total`: `_advance_work`/`_placement_actions`/
`_apply_place` all check capacity as `people_total + guests`, but
nothing else (feeding's `_food_needed`, `score_player`'s `people`
category, or the family-growth room checks on `basic_wish`/
`urgent_wish`) reads `guests` — they all read `people_total` directly,
so a guest never needs feeding, never scores, and is invisible to
family-growth logic, matching the rulebook ("a guest does not count as
family member," "does not need to be fed"). Call `grant_guest` from
any hook (`play`, `space_used`, `round_start`, a scheduled-round effect,
...); `_start_round` resets every player's `guests` to 0 (before that
round's `round_start` hooks fire) so an unused guest never carries into
the next round, and a `round_start` hook may still grant one for the
round it's firing in. The guest's placement happens in normal
rotation order, like an extra person — it is *not* an immediate
second turn (contrast the Lasso, which explicitly requests one via
`action["lasso"]`).

**Placing on an occupied action space:** `occupied_ok=fn(state, player,
inst, space) -> bool` on a card spec lets `player` place a person on
`space` even though another person (a previous placement by any
player, including `player` themself if the card allows it) is already
there. `_placement_actions` and `_apply_place` both consult
`cards.occupied_ok(state, player, space)` before falling back to the
normal "is this space occupied" check, so listing and validation can't
disagree. **`occupied_ok` must be a pure predicate** — it's evaluated
on every `get_valid_actions` call, not only when a placement actually
happens, so it must not mutate `inst["data"]`; express restrictions
("only this space", "only your Nth placement", "only while some static
condition holds") as reads against `state`/`player`/`space`
(`space["id"]`, `player["people_placed"]`, `state["round"]`, a static
house-type check, ...) rather than as a flag set on first use. A card
is still never allowed to use its own `occupied_ok` to place a *second*
person of the same player on one space in the same round — the engine
rejects that unconditionally (matches every compendium card's "not
with 2 of your own people" ruling).

Because more than one player can now occupy a space, `occupied_by`
keeps its old meaning (the *first* occupant — every existing reader of
`occupied_by == idx` / `is not None` still works unmodified) and a new
list field, `extra_occupants`, holds any additional occupants placed
there via `occupied_ok`. Use `state["action_spaces"]` entries' full
occupancy as `([occupied_by] if occupied_by is not None else []) +
extra_occupants` (or just call the engine's own `_space_occupants`
internally) — this is what `returning_home`'s `spaces` ctx and the
`space_used` event's `occupants` ctx are built from, so a card doesn't
need to reimplement it. Both fields reset to `None` / `[]` every round.
**Client note:** `client/games/Agricola_MP.jsx` only renders
`occupied_by` (the primary occupant) on the board; a future pass needs
to render `extra_occupants` too once a card actually uses this
mechanism.

**Static ability keys** (data on the spec, queried by the engine):

- `cost_mod=fn(state, player, kind, cost, ctx) -> cost` — kinds:
  `room`, `renovation`, `improvement`, `fences` (ctx has `count`).
- `occ_cost_delta=-1` — future occupations cost less.
- `pasture_capacity_bonus=n` — each pasture holds +n.
- `house_capacity=n or "per_room"` — house pet slots.
- `extra_rooms=n` — counts as room for family growth.
- `raw_values={"grain": 2, ...}` — better at-any-time crop→food rate.
- `cook={"sheep": 2, ...}` — cook table (like Fireplace).
- `bake=(limit_or_None, food_per_grain)` — baking improvement;
  the bake dict in actions may then use this card's code as key.
- `bake_bonus_per_grain=n`, `bake_bonus_flat=n` — extra food on bake.
- `bake_on_spaces=("farmland", ...)` — grants Bake Bread on spaces.
- `field={"crops": ("vegetable",)}` — this card is a sowable field.
- `conversions=[{"give": {...}, "get": {...}, "per_harvest": n?}]` —
  exchange rates usable during feeding (give may include animals).
- `lasso=True` — double placement on animal markets. Interacts cleanly
  with guests: the Lasso's replacement-turn check and a guest's
  extra-capacity check are the same `people_total + guests` comparison,
  so a player with both can Lasso *and* still have their guest turn
  later in the rotation.
- `occupied_ok=fn(state, player, inst, space) -> bool` — place on an
  action space another (or the same) player already occupies; see
  above.
- `score_bonus=fn(state, player, inst) -> int` — end-game bonus points.
- `card_action={"available": fn(state, player, inst) -> bool,
   "apply": fn(state, player, inst, ctx),
   "description": "..."}` — an activated ability offered on your turn
  (work phase) and during feeding; `ctx["params"]` carries client input.
- Card-local storage: `inst["data"]` (dict), e.g. once-per-game flags.

**Mid-effect choices**: from any hook,
`prompt_choice(state, player, inst["id"], "Take wood or clay?",
["1 wood", "1 clay"], data={...})`, then define the top-level
`resolve_choice=fn` spec key and read `ctx["index"]`.

**Prompting (or granting animals via `ctx["extra"]`) from a
`round_start` hook is safe**: `_advance_work` holds the game on the
prompt's target player instead of treating "everyone's placement query
is empty while a prompt is pending" as "nobody can place, forfeit the
round". `state["prompts"]` is never cleared by `_start_round` (a card
that queued a prompt earlier the same round, or the round before, is
guaranteed to have been resolved already — see `_end_work_phase`
below). Once the prompt is answered, real placement resumes from the
round's normal starting player.

Existing cards that auto-apply a default instead of prompting from
`round_start`/`returning_home` (because this used to be unsafe) don't
need to change, but a future pass could turn them into real prompts
now that the mechanism supports it.

**Remaining constraint — traveling minor improvements can't prompt from
`play`:** a `traveling=True` card's instance is handed to the left
neighbor (or discarded in solo) immediately after its `play` hook runs,
before it is ever added to `p["minors"]`. If the hook calls
`prompt_choice`, the queued prompt's `resolve_choice` lookup
(`cards.in_play(p)`) will fail once the card has left play, so
traveling cards must still auto-apply their play effect rather than
prompt for it. `round_start`/`returning_home` hooks aren't affected by
this — those only ever fire for cards already sitting in play.

**Scheduled goods on round spaces**:
`state["round_goods"][str(round)][str(pidx)][good] += n` (see
`schedule_on_play` factory for the common on-play form).

**Action space supply**: cards may add goods to a space:
`space["supply"][good] = space["supply"].get(good, 0) + n` where
`space = next(s for s in state["action_spaces"] if s["id"] == ...)`.
Spaces not yet revealed cannot be targeted.

**Useful state facts**: player fields `resources`, `cells` (15 dicts:
`type` empty/room/field, `stable`, `animal`, `crops`), `fences`,
`house_type`, `people_total`, `newborns`, `pets` (dict), `begging`,
`improvements` (major ids), `occupations`/`minors` (instances),
`hand_occupations`/`hand_minors`, `occs_played`. Global: `round`,
`stage`, `harvest_index`, `round_goods`, `action_spaces`,
`starting_player`. Board helpers in `server.agricola.state`
(`compute_pastures`, `plowable_cells`, `animal_counts`, ...).

## Not supported (mark UNIMPLEMENTED, cite the mechanic)

- Affecting other players' hands, people, or farms directly (guest
  tokens and occupied-space placement, per se, ARE supported now --
  see `grant_guest`/`occupied_ok` above -- but removing/returning
  *another player's* person, as in D093 Sheep Inspector or D094/D150
  "return the first person you placed home," is not: there's no
  "unplace" primitive)
- Farmers of the Moor concepts: fuel, horses, forest/moor tiles,
  heating, special actions (all of deck M, and FotM-tagged rulings)
- Moving/removing built fences, rooms, or fields
- Returning played cards to hand; playing cards from other players
- Score-sheet manipulation beyond bonus points
- Wood/food placed on *this card* to be taken by OTHER players
- Cards that modify the action-space CARD deck order or reveal

## Module layout

```python
"""Deck X <occupations|minors> (codes X.. from the compendium DB)."""
from server.agricola.cards import ...

UNIMPLEMENTED = {}

# One section per card, in code order. Cite the DB text in the
# registration (compendium_card pulls it automatically).

compendium_card("E12", hooks=space_bonus(["fishing"], {"food": 1}))
# E12 Fishing Rod gains +1 food (round <8) / +2 (8+): needs a custom
# hook — see below.
```

Cards whose effect varies by round/state need small custom hooks —
follow the patterns in `server/agricola/cards.py` (the "base" deck).

## Tests

Each deck module gets `tests/test_deck_<x>.py`:
- an import/registration test (all codes either registered or in
  UNIMPLEMENTED, no overlap);
- effect tests for at least a third of the implemented cards, chosen
  for mechanical diversity (use the helpers in `tests/test_agricola.py`:
  `make_state`, `give`, `give_card`, `put_in_play`, `add_space`,
  `place`);
- every implemented card must at least be *played* once in a test
  (a loop that plays each card with satisfied prereqs/costs and
  resolves any prompt with option 0 is fine as a smoke test).

Run: `env3.13/bin/python -m pytest tests/test_deck_<x>.py -q`.
