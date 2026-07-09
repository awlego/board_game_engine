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
| `space_used` | any player finishes an action space | `space_id`, `goods` the space provided |
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
- `lasso=True` — double placement on animal markets.
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

**Do not prompt (or grant animals via `ctx["extra"]`) from a
`round_start` hook**: `_start_round` clears `state["prompts"]` before
firing the event, and a prompt pending at round start blocks every
player's placements, so the effect is silently discarded or the round
is forfeited. Auto-apply the effect instead (pick a sane default, or
place animals best-effort like the breeding phase does).

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

- Guest tokens / extra people / placing on occupied spaces
- Affecting other players' hands, people, or farms directly
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
