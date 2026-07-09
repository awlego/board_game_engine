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
| `harvest_start` | top of each harvest, before the field phase (own) | `harvest_index` (1..6) |
| `harvest_field` | field phase of each harvest (own) | `harvest_index` (1..6), `got` (`{"grain": n, "vegetable": n}` total credited this harvest), `tiles` (same shape, farmyard field tiles only), `card_fields` (same shape, card fields only) |
| `gained` | any time goods are credited to a player from any source (own cards only) -- see the dedicated section below | `goods` (credited amounts; **may include animal types**), `source` |
| `breeding` | once per player, after that player's breeding phase resolves (own cards only) | `newborns` (animals successfully placed, `{type: count}`), `unplaced` (animals that bred but had no room), `harvest_index` |
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

**`gained`** (`cards.fire_gained(state, player, goods, source, log,
space_id=None)`, called by the engine -- there is no `hooks={"gained":
...}` call site in card code except to *react* to it) fires every time
goods are credited to a player, from any source: `source` is one of
`"space"` (an action space's fixed or accumulated goods -- `space_id` is
also set), `"card"` (any hook-granted extra, routed through
`sub_actions.apply_extras`, `cards.grant_goods`, `on_play_gain`,
`round_income`, `harvest_food`, or a card's own direct `player["resources"]`
write), `"harvest"` (field-phase crops), `"bake"`, `"convert"` (feeding
conversions and accommodate-time cooking), `"round_goods"` (a scheduled
round-space payout), or `"breeding"` (newborns actually placed). `goods`
**may contain animal types** ("sheep"/"boar"/"cattle") -- a `gained` hook
must not assume resource-dict keys; for `source="space"` this fires at
receipt time, before the resulting accommodate prompt (if any) is even
queued, so an animal gain is visible to `gained` before it's placed. A
`gained` hook may add to `ctx["extra"]`; that's credited the normal way
(resources directly, animals via the accommodation queue) and re-fires
`gained` with `source="card"` so a chained grant ("each time you gain
wood, get 1 food") notifies further hooks too -- guarded by a depth
counter (`state["_gained_depth"]`) so a pathological "each time you gain
food, get 1 food" card can't loop forever: past depth 3, the goods are
still credited (by the caller, one level up) but the event stops firing
and chaining. `gained` never fires for spending, refunds, or moving
goods between a player's own stores, and is skipped entirely when
`goods` is empty or all amounts are <= 0.

**`breeding`** fires once per player, right after that player's own
breeding attempts (in `_finish_harvest`) are resolved -- for every
player, even one with no animals (`newborns`/`unplaced` are then both
empty). A `gained(source="breeding")` fires first, for `newborns` only.
`_finish_harvest` sets `state["phase"] = "breeding"` (a transient value,
distinct from "feeding") for the whole of this step: a `breeding` (or
chained `gained`) hook may queue a prompt (a choice, or an animal grant
via `ctx["extra"]`), and the phase must not look like "feeding" while
that's pending -- otherwise resolving it would hit the feeding
dispatch's "phase == feeding and everyone's fed" branch and call
`_finish_harvest` a second time, breeding the same animals twice.
`_apply_choice`/`_apply_accommodate` route `phase == "breeding" and no
prompt pending` to `_end_round` instead (mirroring the `feeding`
dispatch); `_start_round` sets phase back to `"work"` as usual.

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
`cards.grant_goods(state, player, gain, log)` instead of writing straight
into `player["resources"]` — it credits non-animal goods directly, queues
an accommodation prompt for animal goods, and fires `gained(source=
"card")` for the credit, so a card that grants sheep to some other player
can't silently corrupt state. `log` defaults to `[]` if omitted, but pass
`ctx["log"]` when you have one so the `gained` hook's own log lines land
in the same broadcast. `on_play_gain` and `space_bonus(..., others=True)`
already use it internally.

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

- `cost_mod=fn(state, player, kind, cost, ctx) -> cost` — folds over a
  base cost dict; return a (possibly reduced) dict, never negative
  (`modified_cost` drops non-positive entries for you). Kinds:
  `room`, `stable`, `fences`, `renovation`, `improvement`, `minor`,
  `occupation`. `ctx` carries whichever of these the caller has:
  - `count` — items in this batch. **Room and fence cost is a BATCH
    TOTAL**, not per-item: `build_rooms`/`build_fences` price the whole
    batch in one `modified_cost` call (base cost already `count`x the
    per-item amount), so a flat per-item discount must multiply by
    `ctx.get("count", 1)` (see occ_carpenter/occ_stonecutter in
    `cards.py` for the pattern). Stables are priced ONE AT A TIME (see
    below) but still get `count` (the whole batch's size) in ctx.
  - `start_index` — how many of that thing the player already had
    *before* this batch (existing fences/stables) — lets a "your Nth
    item" card compute which absolute positions
    `[start_index+1 .. start_index+count]` this batch covers. Not
    present for rooms (room count has no positional meaning).
  - `index` — kind=`stable` only: the 1-based overall index of THIS
    stable (`start_index` + its position in the batch). Unlike rooms/
    fences, `build_stables` calls `modified_cost` once per stable (each
    stable's cost = `modified_cost(state, player, "stable", {"wood": 2},
    ctx)` with its own `index`), since per-Nth pricing can differ stable
    to stable within one batch (e.g. "your 3rd and 4th stable cost 1
    wood less" spanning a 2+2 split).
  - `space_id` — the originating action-space id, when the build/
    renovate/improvement/minor came from an action space (threaded from
    `engine._resolve_space`); absent/`None` for a card-driven call (a
    `card_action`/hook calling a `sub_actions` transaction directly with
    no space) — see D082 Hunting Trophy (discount only via House/Farm
    Redevelopment, not the plain Major Improvement/Fencing spaces).
  - `improvement` — the major-improvement id, for kind=`improvement`
    (lets a card single out one improvement, e.g. I220 Well Builder,
    instead of misfiring on every major improvement).
  - `card` — the card id, for kind=`minor`/`occupation` (an occupation's
    own food cost passes its own `cid`; the Lessons spaces do too).
  - `payment` — the raw payment-choice value from the client action
    dict (opaque; the CARD decides the schema, e.g.
    `{"reed_to_clay": 2}`). Threaded from `action.get("payment")` (or,
    for a minor played via the `"minor": {...}` sub-dict, that sub-dict's
    own `"payment"` key) into `build_rooms`/`renovate`/
    `build_improvement`/`build_fences`/`play_minor`'s ctx. A cost_mod
    that consumes `ctx["payment"]` MUST validate it and raise
    `ValueError` on garbage (unrecognized shape, out-of-range amount,
    more than the cost it's replacing) — never silently ignore or clamp
    it, per rule 2's "don't approximate silently".

  **Worked payment-channel example** (E36 Clay Roof: "replace 1 or 2
  reed with the same amount of clay whenever you extend or renovate"):
  ```python
  def _clay_roof_mod(state, player, kind, cost, ctx):
      if kind not in ("room", "renovation"):
          return cost
      payment = ctx.get("payment")
      if payment is None:
          return cost
      if not isinstance(payment, dict) or set(payment) != {"reed_to_clay"}:
          raise ValueError("Clay Roof: invalid payment")
      n = payment["reed_to_clay"]
      if not isinstance(n, int) or n <= 0 or n > 2 or n > cost.get("reed", 0):
          raise ValueError("Clay Roof: invalid payment")
      cost = dict(cost)
      cost["reed"] -= n
      cost["clay"] = cost.get("clay", 0) + n
      return cost
  ```
  The client sends `{"kind": "place", "space": "house_redevelopment",
  "payment": {"reed_to_clay": 1}}` (or nests `"payment"` inside a
  `"minor": {...}` action for a minor's own cost). **Caveat:** the
  `can_*`/`*_possible` preview predicates (`renovation_possible`,
  `can_build_rooms`, etc. — what `_space_usable` uses to decide whether
  a space is offered at all) are called with no `payment` in ctx, so a
  card that relies on a payment choice to make an otherwise-unaffordable
  build affordable can make the space look unusable in the preview even
  though the real payment would cover it; the player must hold enough
  for the UNMODIFIED cost too. This is a known limitation, not a bug to
  route around per-card.
- `occ_cost_delta=-1` — future occupations cost less.
- `pasture_capacity_bonus=n` — each pasture holds +n (flat, type-
  agnostic; folded into `cards.pasture_capacity` below, which is what
  every caller queries -- see "Card-aware animal capacity" for the
  richer, per-pasture/type-conditioned form).
- `house_capacity=n, "per_room", or fn(state, player, inst) -> n` —
  house pet slots. The callable form computes a delta (e.g. scaled by
  occupation count) instead of a fixed int; queried via
  `cards.house_capacity(state, player)` (note the `state` argument).
- `extra_rooms=n or fn(state, player, inst) -> n` — counts as room for
  family growth. The callable form (D085 Reader: "room for one person
  once you have 6 occupations in play") computes the value instead of a
  fixed int; queried via `cards.extra_rooms(state, player)` (note the
  `state` argument -- every caller of the old `extra_rooms(player)`/
  `house_capacity(player)` signatures needs updating).
- `raw_values={"grain": 2, ...}` — better at-any-time crop→food rate.
- `cook={"sheep": 2, ...}` — cook table (like Fireplace).
- `bake=(limit_or_None, food_per_grain)` — baking improvement;
  the bake dict in actions may then use this card's code as key.
- `bake_bonus_per_grain=n`, `bake_bonus_flat=n` — extra food on bake.
- `bake_on_spaces=("farmland", ...)` — grants Bake Bread on spaces.
- `field={"crops": ("vegetable",)}` — this card is a sowable field.
- `keep_crops_on_harvest=("vegetable",) or fn(state, player, inst) ->
  iterable` — crop types (queried via `cards.keep_crops_on_harvest(state,
  player)`, unioned across every in-play card) that the field phase
  should count and credit as usual but NOT decrement from the field
  (I226: "take vegetables from the general supply... you keep the
  vegetables on the fields").
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

**Card-aware animal capacity (engine phase 8):** the flat "2 per pasture
cell, doubled per stable, 1 in an unfenced stable, one type per
pasture" model is too rigid for cards like D011 Lawn Fertilizer
(per-pasture-size capacity), E29 Shepherd's Pipe (type-conditioned
pasture/stable capacity), or FR013 Chameleon (a second type sharing a
pasture). Three new spec keys, each queried across every in-play card
and folded together:

- `pasture_capacity_mod=fn(state, player, inst, info) -> int` — a
  capacity delta for one specific pasture. `info` is `{"cells": sorted
  cell list, "size": len(cells), "stables": stable count inside,
  "animal_type": the type being housed, or None when checking
  type-agnostically}`. Query: `cards.pasture_capacity(state, player,
  pasture_cells, animal_type)` = base geometry
  (`state.pasture_capacity`) + the flat `pasture_capacity_bonus` (still
  supported, unchanged) + the sum of every `pasture_capacity_mod`.
  ```python
  # D011 Lawn Fertilizer: size-1 pastures hold 3 (6 with a stable).
  def _lawn_fertilizer_mod(state, player, inst, info):
      if info["size"] != 1:
          return 0
      return 2 if info["stables"] >= 1 else 1  # +1 -> 3; +2 -> 2*2+2=6
  ```
- `unfenced_stable_capacity_mod=fn(state, player, inst, animal_type) ->
  int` — a capacity delta for an unfenced stable holding `animal_type`.
  Query: `cards.unfenced_stable_capacity(state, player, animal_type)` =
  1 + the sum of every mod (e.g. E29 Shepherd's Pipe: "up to 2 sheep in
  each unfenced stable" -> `+1 if animal_type == "sheep" else 0`).
- `pasture_secondary_types=fn(state, player, inst, info) -> {type:
  max_count}` — animal types permitted *alongside* `info["animal_type"]`
  in the same pasture (still counting against that pasture's own total
  `pasture_capacity` -- this grants permission to mix, not extra room).
  Query: `cards.pasture_secondary_types(state, player, info)` merges
  every card's dict, taking the max per type.
  ```python
  # FR013 Chameleon: 1 wild boar in each pasture that holds sheep.
  def _chameleon_secondary(state, player, inst, info):
      return {"boar": 1} if info["animal_type"] == "sheep" else {}
  ```

`state.validate_animal_placement(player, house_cap=1, pasture_cap=None,
unfenced_stable_cap=None, secondary_types=None)` takes these as
callbacks (`pasture_cap(cells, animal_type) -> int`,
`unfenced_stable_cap(animal_type) -> int`, `secondary_types(info) ->
dict`) so `state.py` stays card-free; every real caller
(`engine._validate_animals`, `sub_actions.build_fences`'s
re-accommodation check, `engine._place_newborn_animal`) passes
lambdas that call the `cards.*` queries above. The defaults (`None`)
reproduce the original flat behavior exactly, which is what direct unit
tests of `validate_animal_placement` still rely on.

**Mixed-type validation, exactly:** for a pasture holding more than one
animal type, validity means *some* present type can serve as "primary" —
try each: for that choice, every *other* type present must fit within
`secondary_types({"cells":..., "size":..., "stables":...,
"animal_type": primary}).get(other_type, 0)`, AND the combined total of
every type in the pasture must fit `pasture_cap(cells, primary)`. A
pasture holding only one type skips this entirely and just checks
`total <= pasture_cap(cells, that_type)` (the original rule, extended
per-type/pasture instead of flat).

**Card-held animal storage:** some cards keep animals on themselves
instead of a farmyard cell (C012 Cattle Farm: "1 cattle per pasture, on
this card"; E58 Animal Yard: "2 animals, need not be the same type";
I102 Wildlife Reserve: "1 sheep, 1 wild boar, 1 cattle"; K145 Forest
Pasture: "unlimited wild boar"). Spec key `holds_animals=fn(state,
player, inst) -> {"types": {type: max_or_None}, "total": max_or_None}`
(missing `"types"` = any type allowed; a `None` cap = unlimited; both
constraints, when present, are enforced together). The instance stores
`inst["held"] = {type: count}` — plain data, so `state.animal_counts`
reads it directly off `player["minors"]`/`player["occupations"]` with
no `cards` import (this is also why breeding, feeding-conversion
availability, and scoring automatically see card-held animals: they all
go through `animal_counts`).

- `cards.validate_held(state, player) -> (ok, err)` checks every holder
  card's `inst["held"]` against its own `holds_animals` caps.
- Accommodation (`{"kind": "accommodate", "placements": [...]}`) accepts
  `{"card": <card id>, "type": t, "count": n}` entries alongside the
  usual `{"cell": ..., "type": t, "count": n}` — `_apply_animal_placement`
  resets every holder card's `inst["held"]` to `{}` (like it resets
  `cell["animal"]`/`pets`) before replaying the placements, then runs
  `validate_held` alongside the usual `validate_animal_placement`.
- Newborn auto-placement order (`engine._place_newborn_animal`):
  same-type pasture headroom, an empty pasture, unfenced-stable headroom
  (empty, or already holding that type, using
  `unfenced_stable_capacity`), the house, then the first holder card
  with headroom for that type. A newborn is **never** auto-placed as a
  pasture's secondary type (FR013-style) — that's a placement-time-only
  choice for the player, not an automatic one.
- **Client note:** card instances (and therefore `inst["held"]`) are
  serialized to the client as-is (`get_player_view` just deep-copies
  `state`, which includes `player["minors"]`/`["occupations"]`), so
  held animals ride along automatically. The accommodate UI itself has
  no affordance yet for building a `{"card": ...}` placement entry —
  still a client-side follow-up.

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

**Prompting from `harvest_start` is safe the same way** (D070: "before
the field phase of each harvest, you can pay 1 grain to add 1 vegetable
to each of up to 2 vegetable fields" needs a real choice here).
`_start_harvest` fires `harvest_start` for every player, then checks for
a pending prompt; if one is queued, the field phase is deferred
(`state["_field_phase_pending"] = True`) instead of running with crops
still in flux. `_apply_choice`/`_apply_accommodate` check
`_field_phase_pending` (mirroring `_end_work_phase_pending`) and call
`_run_field_phase` once the prompt queue drains, so the field phase
always runs -- exactly once -- whether or not a `harvest_start` hook
prompted.

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

## Bonus build/play sub-actions (`server.agricola.sub_actions`)

"Immediately build a room / up to 2 stables / fences / a minor or major
improvement / sow / play an occupation, possibly at a discount or free" is
a common shape (~25 compendium cards). Each of those transactions has real
rules behind it (cell adjacency, house-type-dependent cost, `available_
improvements`/`occs_played` bookkeeping, ...), so **don't reimplement it
in a deck module** — `sub_actions.py` is the single implementation, shared
with `engine.py`'s own action-space dispatch:

```python
from server.agricola import sub_actions
```

| transaction | function | cost-aware `can_*`/`*_possible` check |
|---|---|---|
| build rooms | `build_rooms(state, player, cells, log, cost_override=None, ctx=None)` (cost is a BATCH TOTAL for `len(cells)` rooms, not per-room) | `can_build_rooms(state, player, cost_override=None, ctx=None)` (checks affording 1 room) |
| build stables | `build_stables(state, player, cells, log, cost_override=None, ctx=None)` (priced one at a time; a dict `cost_override` is per-stable) | `can_build_stables(state, player, cost_override=None, ctx=None)` (alias of `stable_possible`) |
| build fences | `build_fences(state, player, new_fences, log, cost_override=None, ctx=None)` (cost is a batch total) | `can_build_fences(state, player, cost_override=None, ctx=None)` |
| renovate | `renovate(state, player, log, free_stable_cell=None, cost_override=None, ctx=None)` | `can_renovate(state, player, cost_override=None, ctx=None)` (alias of `renovation_possible`) |
| build a major improvement | `build_improvement(state, player, imp, log, upgrade=False, cost_override=None, ctx=None)` | `can_build_improvement(state, player, imp=None, cost_override=None, ctx=None)` |
| play a minor improvement | `play_minor(state, player, cid, log, params=None, cost_override=None, ctx=None)` | `can_play_minor(state, player, cid, cost_override=None, ctx=None)` |
| play an occupation | `play_occupation(state, player, cid, log, params=None, cost_override=None, ctx=None)` | `can_play_occupation(state, player, cid, cost_override=None, ctx=None)` |
| sow | `sow(state, player, sow_items, log)` (no cost concept -- consumes the sower's own crop, same as the normal action) | `can_sow(player)` |
| family growth (no room/urgent-wish flavor) | `family_growth(state, player, log, require_room=True)` | `can_family_growth(state, player, require_room=True)` |

`ctx` (optional, default `None`) is folded into whatever ctx that
transaction's own `modified_cost` call builds (see the `cost_mod` entry
above for the full per-kind contract) — pass whatever you have (a
card-driven call typically has nothing to add and can omit it
entirely).

Every transaction function **raises `ValueError` on an illegal target**
(bad cell, occupied space, invalid fence layout, unmet prereq, unaffordable
cost, ...) instead of silently doing nothing — safe to call from any hook,
because the engine deep-copies `state` before applying an action, so a
raised error rolls the whole action back. `can_*`/`*_possible` predicates
never mutate anything (they mirror the "is this space usable in principle"
granularity `_space_usable` already uses for the normal action spaces --
they check that *some* legal target/afford exists, not a specific one) so
they're safe to call from `get_valid_actions`-adjacent code every poll.

**Cost**, for every transaction that has one, is `cost_override=None|dict|
"free"`:
- `None` (default) — the normal cost, run through `cards.modified_cost`
  exactly like the matching action space (a Stonecutter/Carpenter/Hedge
  Keeper-style discount still applies).
- a goods dict — an exact cost to charge instead of the computed one
  (compute your own flat discount, e.g. `{"wood": 1}` for "1 less wood",
  and pass it — no boolean flags).
- `"free"` — skip payment entirely.

**Input channel, by target shape:**
- **Open-ended targets** (a set of fence edges, a multi-cell room layout,
  which major improvement, which occupation/minor in hand) MUST arrive via
  a `params` dict — either a card's own `play` hook (`ctx["params"]`, for
  an immediate on-play bonus) or a `card_action` (`ctx["params"]`, for an
  activated ability on the owner's later work turn). There is no prompt
  shape for "pick a set of fence edges".
- **Small enumerable choices** ("which of these 3 spaces", "wood or clay")
  can use `prompt_choice` instead, listing the options and reading
  `ctx["index"]` in `resolve_choice`.
- A "reactively grant a bonus, redeemed later" card (Educator/Scholar's
  shape) banks a credit in `inst["data"]` from whatever hook triggers it,
  then exposes a `card_action` that spends the credit via one of the
  functions above.

**Example — a card hook building a free room** (`play` hook, target via
params):
```python
def _my_card_play(state, player, inst, ctx):
    cells = (ctx.get("params") or {}).get("cells") or []
    sub_actions.build_rooms(state, player, cells, ctx["log"], cost_override="free")

card("my_card", "My Card", "minor", cost={}, text="...",
     hooks={"play": _my_card_play})
# Client sends: {"kind": "place", "space": "meeting_place",
#                "minor": {"card": "my_card", "params": {"cells": [7]}}}
```

**Example — a discounted stable via `card_action`** (activated ability,
available on the owner's own work turn):
```python
def _my_axe_available(state, player, inst):
    return sub_actions.can_build_stables(state, player, cost_override={"wood": 1})

def _my_axe_apply(state, player, inst, ctx):
    cell = (ctx.get("params") or {}).get("cell")
    sub_actions.build_stables(state, player, [cell], ctx["log"],
                              cost_override={"wood": 1})

card("my_axe", "My Axe", "minor", cost={}, text="...",
     card_action={"available": _my_axe_available, "apply": _my_axe_apply,
                 "description": "Build 1 stable for 1 wood"})
```

**Example — banked credit + `card_action`, redeemed as a free occupation
play** (the Craft Teacher/Scholar/Educator shape -- see those cards in
`deck_a_occupations.py`/`deck_k_occupations.py` for the full versions):
```python
def _my_trigger_hook(state, player, inst, ctx):
    inst["data"]["credits"] = inst["data"].get("credits", 0) + 1

def _my_bonus_available(state, player, inst):
    return inst["data"].get("credits", 0) > 0 and bool(player["hand_occupations"])

def _my_bonus_apply(state, player, inst, ctx):
    cid = (ctx.get("params") or {}).get("card")
    if cid not in player["hand_occupations"]:
        raise ValueError("choose an occupation in hand (params.card)")
    sub_actions.play_occupation(state, player, cid, ctx["log"], cost_override="free")
    inst["data"]["credits"] -= 1
```

**Example — playing a minor improvement from a `prompt_choice`** (small
enumerable set: which affordable minor in hand):
```python
def _my_prompt_hook(state, player, inst, ctx):
    playable = [cid for cid in player["hand_minors"]
               if sub_actions.can_play_minor(state, player, cid)]
    if playable:
        prompt_choice(state, player, inst["id"], "Play a minor for free?",
                     [cards.CARDS[cid]["name"] for cid in playable],
                     data={"cids": playable})

def _my_resolve_choice(state, player, inst, ctx):
    cid = ctx["data"]["cids"][ctx["index"]]
    sub_actions.play_minor(state, player, cid, ctx["log"], cost_override="free")
```

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
