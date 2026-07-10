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
    card_space_owner,
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
- `field={"crops": ("vegetable",), "stacks": n}` — this card is a
  sowable field; `"stacks"` (default 1) gives it that many independent
  crop slots (K105/FR089-style) -- see "Field/fence/grid extensions"
  below for the full contract (`cards.field_stacks`/`get_field_stack`/
  `set_field_stack`/`open_field_stacks`, the stack-addressed sow-action
  shape, and old-save compat).
- `fence_token={"cost": {"wood": 2}}` — this card lets its owner satisfy
  a border fence edge with wood tokens (`player["fence_tokens"]`)
  instead of a fence piece, excluded from `MAX_FENCES` (B030-style) --
  see "Field/fence/grid extensions" below for the full contract.
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
- `card_space={...}` — the card itself becomes an additional action
  space once played; see the dedicated `card_space` section below for
  the full contract.
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

## `card_space`: cards that ARE action spaces (engine phase 9)

Some cards (A039 Chapel, B042 Forest Inn, D023 Pioneering Spirit, D051
Archway, E164 Master Forester, I100 Tavern, I337 Clay Deposit, ...)
don't just react to action spaces or modify their cost/capacity — the
card itself becomes an additional action space once played, usable by
some or all players for the rest of the game. Spec key `card_space`
(a dict), on either an occupation or a minor improvement:

```python
card("i337_clay_deposit", "Clay Deposit", "minor", cost={"food": 1},
     text="An additional action space. A player who uses it must pay "
          "you 1 food and receives 5 clay.",
     card_space={"resolve": _i337_resolve})
```

- `"name"`, `"desc"` — display strings for the space; default to the
  card's own `name`/`text` if omitted.
- `"owner_only"` — bool, default `False`. `True` restricts placement to
  the card's own owner (D023: "an action space FOR YOU ONLY").
- `"acc"` — `None` (default), a goods dict replenished every
  `round_start` like any other accumulation space, or
  `fn(state, owner_player, inst) -> dict` for a computed amount (always
  evaluated for the card's OWNER, regardless of who ends up placing
  there). E164 Master Forester: `{"acc": {"wood": 2}}`. All 7 motivating
  cards use `"acc"` XOR `"resolve"`, never both — `"usable"`'s default
  supply-non-empty gate (see below) is written assuming that split; a
  future card combining both would need its own `"usable"` override if
  the resolve fn doesn't actually depend on `space["supply"]`.
- `"usable"` — `fn(state, player, inst) -> bool`, optional, an extra
  gate beyond the defaults (e.g. D023's round-3-through-8 window).
  `player` is whoever is trying to PLACE there, not necessarily the
  owner (when `owner_only` is also set, by the time `usable` runs
  `player` already IS the owner — the owner_only check happens first).
- `"resolve"` — `fn(state, player, inst, action, log) -> goods dict`,
  optional (an accumulation-only card space like E164 can omit it
  entirely and just falls through to the normal accumulation-space
  handling). Performs the space's own effect for the placing `player`
  (who may not be the owner): do the card's special mechanics here —
  tolls paid to the owner (direct debit from `player["resources"]`,
  credited to the owner via `cards.grant_goods` — the owner isn't the
  acting player, so `ctx["extra"]` isn't available to them), bonus
  points, choices read from `action` (the same client-supplied action
  dict a normal space's dispatch sees), `ValueError` on invalid input
  — and **return only the goods the fn wants credited to `player`**.

  **The contract is exact**: the resolve fn must NOT itself write the
  returned goods into `player["resources"]` (or route an animal
  through accommodation) — it only returns them. The engine credits the
  return value the normal way goods from any other space are credited:
  non-animal goods go straight into `player["resources"]`, animal goods
  are queued for accommodation, `gained(source="space")` fires for the
  total, and `space_used` fires with that same total as its `goods`
  field. Side effects that AREN'T "goods credited to the placing
  player" — a toll paid to the owner, bonus points, a queued prompt —
  stay entirely inside the resolve fn and are not part of the return
  value. Returning `{}` (or nothing) is fine, including for a resolve
  fn that only queues a prompt (see below) and grants everything once
  the choice is answered.

  Find the space's owner from inside a resolve/usable fn with
  `cards.card_space_owner(state, inst)` (looks up `state[
  "action_spaces"]`'s `"card:<cid>"` entry's `owner` index — `resolve`/
  `usable` only receive the PLACING player, not the owner).

**Worked example** (I337 Clay Deposit: toll to the owner, 5 clay to the
placer, no toll for the owner's own placement):
```python
def _i337_resolve(state, player, inst, action, log):
    owner = cards.card_space_owner(state, inst)
    if player is not owner:
        if player["resources"]["food"] < 1:
            raise ValueError("You must pay 1 food to use this space")
        player["resources"]["food"] -= 1
        cards.grant_goods(state, owner, {"food": 1}, log)
        log.append(f"{player['name']} pays {owner['name']} 1 food")
    log.append(f"{player['name']} takes 5 clay")
    return {"clay": 5}
```

**Prompts**: a `resolve` fn may call `prompt_choice` exactly like any
other space resolution (mid-effect choices are always safe — see
above) and return `{}`; the card's `resolve_choice` then credits
whatever the choice implies once it's answered. Because the prompted
player may not be the card's owner, `_apply_choice`'s card-instance
lookup searches every player's in-play cards by id (not just the
prompted player's) — safe since card ids are unique per game.

**Space creation**: `play_minor`/`play_occupation` (in
`sub_actions.py`) both call a shared `_add_card_space` helper once the
card instance joins `player["minors"]`/`["occupations"]`, appending
`{"id": f"card:{cid}", "name", "desc", "occupied_by": None,
"extra_occupants": [], "supply": {}, "accumulates": bool(acc),
"card_space": True, "card": cid, "owner": player["index"]}` to
`state["action_spaces"]`. One space per played card (an `assert`
matches the CARDS registry's per-game uniqueness assumption). A
`traveling=True` card can never declare `card_space` — asserted at
registration in `cards.card` — since a traveling card's instance never
settles into `player["minors"]` for an "owner" to point at.

**Everything else about a card_space is a normal action space**:
occupancy (`occupied_by`/`extra_occupants`, reset every round),
`occupied_ok`, the Lasso's animal-market check (a card_space is never
an animal market), `returning_home`'s `spaces` list, `space_used`'s
`occupants`, and listing in `get_valid_actions` all fall out of the
existing generic handling over `state["action_spaces"]` — no special
case needed anywhere else. A card_space appended mid-round lands at the
end of the list, same as a freshly revealed stage card; the two kinds
of appends don't interact, only affecting cosmetic display order.

**Client note:** `client/games/Agricola_MP.jsx`'s `ActionBoard` sorts
`state.action_spaces` generically into "Permanent" (`s.stage === 0`)
and "Round cards" (`s.stage > 0`) — it does not hardcode ids, so a
card_space's `supply`/`occupied_by` render fine once it's in one of
those groups. But a card_space entry has no `"stage"` key at all (not
part of its dict, unlike PERMANENT_SPACES' `0` or STAGE_CARDS' own
per-card stage number), so `undefined === 0` and `undefined > 0` are
both false — a card_space currently renders in NEITHER group and is
invisible on the board, even though it's fully placeable server-side.
A future client pass needs a third group (or to fold it into "Round
cards") once a real card uses this mechanism.

## Board geometry (engine phase 10)

Some cards reference the board's physical 2D layout rather than just
occupancy: B120 Sweep ("the action space card LEFT OF the card most
recently placed on a round space"), C117 Legworker ("an action space
orthogonally adjacent to another action space occupied by one of your
people"), D144 Water Worker ("Fishing... or one of the three
orthogonally adjacent action spaces"), D165 Pig Stalker ("the action
space immediately ABOVE OR BELOW that accumulation space"), FR006
Badger (a marker that moves to an "orthogonally adjacent revealed
Action space" each round), FR027 Ground Pickaxe Plow ("1/2
orthogonally adjacent Action spaces"), FR037 Necklace ("2 Family
members occupying 2 orthogonally adjacent Action spaces"). None of
these seven cards is registered yet -- this pass only builds the
geometry and queries they'd need (`temp_card`-only tests exercise the
mechanism); registering them is a separate pass, same as items 14/15.

### Coordinate system

`state.py`'s `SPACE_POSITIONS` (keyed by `state["player_count"]`) gives
every PERMANENT_SPACES id a static `(col, row)`; columns increase
rightward, rows increase downward. `ROUND_SLOTS` gives every ROUND
NUMBER (1..14, not stage-card id) a `(col, row)` -- the card revealed
in round N always sits at slot N's position, whatever that stage's
(shuffled) card happens to be. Derived from the Revised Edition
rulebook's board photos and Appendix text (see the derivation comment
above `SPACE_POSITIONS` in `state.py` for exact page references and
caveats) -- the photos are too low-resolution to read pixel-exact, so
the round-space grid is a best-effort reconstruction cross-checked
against the cards' own text (D144 needs Fishing to have exactly 3
neighbors; D165 needs a real above/below pair to ever exist for a
round-space animal market) rather than a confirmed pixel reading.
Treat it as the best available model, not ground truth photographed
off the box.

2-player and 1-player board (1p shares the 2p board):
```
col        0                 1           2        3        4         5         6         7
row 0  Farm Expansion         .           .        .        .         .         .         .
row 1  Meeting Place          .           .        .        .         .         .         .
row 2  Grain Seeds         Forest      Round1   Round5   Round8    Round10   Round12   Round14
row 3  Farmland           Clay Pit     Round2   Round6   Round9    Round11   Round13      .
row 4  Lessons            Reed Bank    Round3   Round7      .         .         .         .
row 5  Day Laborer         Fishing     Round4      .         .         .         .         .
```
Columns 2-7 are the six STAGE_CARDS columns (stage 1: 4 rounds, stage
2: 3, stages 3-5: 2 each, stage 6: 1), each stacked downward from row 2
in round order -- so column 2 (stage 1) is the only one that reaches
row 5, which is what gives Fishing exactly 3 neighbors (Day Laborer
left, Reed Bank above, Round 4 right) once all rounds are revealed.

3-player board adds a column -1 to the left of Farm Expansion:
```
col       -1                0             1
row 0       .          Farm Expansion      .
row 1     Grove         Meeting Place      .
row 2  Resource Mkt      Grain Seeds     Forest
row 3     Hollow          Farmland      Clay Pit
row 4   Lessons_b          Lessons     Reed Bank
row 5       .            Day Laborer    Fishing
```
(columns 2+ continue exactly as the 2-player board above). Row 0 has no
column -1 space -- the Appendix's own worked example ("The Grove is
adjacent to both Farm Expansion and Meeting Place") can't be
reproduced by a single-cell-per-space grid (the printed extension
boxes are taller than one base row), so that documented pair is
restored via `state.EXTRA_ADJACENCY` (per-player-count unordered
override pairs, unioned into `adjacent_spaces`/`spaces_adjacent`).
Extend EXTRA_ADJACENCY only for adjacencies a primary source
documents; everything else stays grid-derived.

4-player board fills all six rows of column -1 (Grove/Lessons_b are the
same spaces as the 3-player board, unchanged per the Appendix; Copse,
the bigger Hollow/Resource Market, and Traveling Players are 4p-only):
```
col       -1                0             1
row 0    Copse        Farm Expansion      .
row 1    Grove         Meeting Place      .
row 2  Resource Mkt     Grain Seeds     Forest
row 3    Hollow          Farmland      Clay Pit
row 4  Lessons_b          Lessons     Reed Bank
row 5  Traveling Players  Day Laborer   Fishing
```
Neither PDF shows the 4-player board's own photo; Copse's row-0 slot
(the one row the 3-player extension leaves empty) is a placement
choice, not a confirmed layout -- it does incidentally restore a
Farm-Expansion adjacency in the extension column (via Copse, one row
up from Grove) that the 3-player board lacks.

A `card_space` ("card:<cid>") has no position -- it sits beside the
board, not on it -- and neither does any id not on the current player
count's board.

### Queries (`cards.py`)

- `space_position(state, space_id) -> (col, row) | None` -- the lookup
  above.
- `adjacent_spaces(state, space_id) -> [ids]` -- ids of spaces that
  EXIST right now (are in `state["action_spaces"]`) at grid distance 1.
  A round slot that hasn't been revealed yet is simply absent from
  `state["action_spaces"]`, so it never appears.
- `spaces_adjacent(state, a, b) -> bool`.
- `left_neighbor(state, space_id) -> id | None` -- the existing space
  at `(col - 1, row)`. B120 Sweep's recipe: call this with
  `state["revealed"][-1]` (the round space most recently placed).

### Worked example (C117-style hook)

```python
def _legworker_hook(state, player, inst, ctx):
    if ctx["actor"] != player["index"]:
        return
    for sid in adjacent_spaces(state, ctx["space_id"]):
        space = next(s for s in state["action_spaces"] if s["id"] == sid)
        occupants = ([space["occupied_by"]] if space["occupied_by"] is not None else []) \
            + space.get("extra_occupants", [])
        if player["index"] in occupants:
            add_goods(ctx["extra"], {"wood": 1})
            ctx["log"].append(f"{player['name']}'s {spec(inst)['name']} adds 1 wood")
            return
```
`ctx["space_id"]` is the space just used (from `space_used`'s ctx, see
the hook table above); `adjacent_spaces` only returns spaces that
exist, so an unrevealed round slot never causes a lookup error. Reading
occupancy is a raw `state["action_spaces"]` scan -- no new primitive
needed, `occupied_by`/`extra_occupants` are already plain state.

## Turn structure (engine phase 11)

Three affordances for cards that manipulate WHO places WHEN, or whether
a placement happens at all -- as opposed to what a placement produces
(every earlier phase). Motivating cards: D053 Tea House, I260 Taster,
I71 Holiday House; K269 Acrobat/K289 Countryman need no engine change
at all (a `returning_home` recipe, see below).

### Skip a placement (D053 Tea House)

D053: "Once per round, you can skip placing your second person and get
1 food instead. (You can place the person later that round.)" Spec key
`skip_turn=fn(state, player, inst) -> gain dict | None` -- the card's
own fn decides when skipping is available (once per round via
`inst["data"]`, "only your 2nd person" via `player["people_placed"]`,
...) and what it grants; the engine never guesses at a card's own
conditions.

While it's a player's placement turn (work phase, no prompt pending,
capacity remaining, not `placement_blocked` -- see below), `cards.
_skip_actions`/`get_valid_actions` offer `{"kind": "skip", "card":
<cid>, "gain": {...}}` for every in-play card whose `skip_turn`
currently returns a truthy dict, **plus one engine-level guard**: at
least one OTHER player must still have an unplaced person. Skipping
when everyone else is already done would be a no-op turn (there is
nobody left to place ahead of) -- the guard makes that structurally
impossible rather than relying on every card's own once-per-round gate
to prevent it.

`{"kind": "skip", "card": <cid>}` (`_apply_skip`) re-validates all of
the above, re-calls `skip_turn` (never trusts the listing), credits
the returned gain through the normal `apply_extras` hub (non-animal
goods straight to resources, animal goods queued for accommodation,
`gained(source="card")` fired) -- exactly like any other hook-granted
extra -- then calls the optional spec key `after_skip=fn(state, player,
inst, log)` so the card can mark its own usage (`inst["data"]
["used_round"] = state["round"]`); this is the ONLY point that mutates
`inst["data"]`, so `skip_turn` itself stays a pure query, matching
`occupied_ok`'s contract. Crucially, **`people_placed` is never
touched** -- the skip defers that placement, it does not forfeit it,
so the same player is revisited later in the round with their capacity
intact. If the gain queued an accommodation prompt, `_apply_skip`
leaves `current_player` as-is and returns without advancing; the
prompt's own resolution (`_apply_accommodate`/`_apply_choice`) calls
`_advance_work(state, log)` with no explicit `start_pidx` once it
drains, which naturally continues from `current_player + 1` -- the same
place `_apply_skip` would have advanced to itself. No `after_lasso`-
style flag is needed (skip has no "immediately place again" clause to
carry across the prompt).

`_advance_work`'s forfeit loop ("cannot use any action space") also
checks `_skip_actions` alongside `_placement_actions` before deciding a
player has nothing to do this turn: a player with a real skip option
available is not "stuck," so they get the turn (to either place or
skip) instead of being force-forfeited.

No `"skipped"` event was added (YAGNI) -- nothing motivating needs to
react to a skip specifically; a card that wants to react to the gain
itself already sees it via `gained(source="card")`.

### One-shot first-placer override (I260 Taster)

I260: "Whenever another player is the starting player, you can pay them
1 food at the start of the round and be the first to place a family
member. After that, play starts with the starting player as usual."
This is a recipe built entirely from existing mechanisms plus one new
`_advance_work` fallback -- **the card itself is not registered**, only
the recipe is documented here (a `temp_card`-only test in
`tests/test_agricola.py` exercises the mechanism end to end).

`_advance_work`'s `start_pidx` resolution order is now: explicit arg →
`state.pop("_pending_work_start", None)` → **NEW:**
`state.pop("_resume_from", None)` → `(current_player + 1) % n`.
`_resume_from` is a one-shot fallback: whichever `_advance_work` call
first falls all the way through to it pops (consumes) it; a
`_pending_work_start` stash set in the meantime (a prompt stalling the
game) already carries the resolved value forward, so `_resume_from`
itself is never read a second time.

The recipe:
1. A `round_start` hook (Taster's owner's card) checks
   `state["starting_player"] != player["index"]` and, if so, calls
   `cards.prompt_choice(state, player, inst["id"], "Pay 1 food to place
   first?", ["yes", "no"])`.
2. `_start_round` finishes firing every player's `round_start` hooks,
   then calls `self._advance_work(state, log, state["starting_player"])`
   -- an EXPLICIT `start_pidx`, so the `_pending_work_start`/
   `_resume_from` fallback chain above is skipped entirely this first
   time. Since the choice prompt from step 1 is already queued,
   `_advance_work` stashes `state["_pending_work_start"] =
   state["starting_player"]` and stops (see its docstring).
3. `resolve_choice`, on `"yes"`: pays the starting player 1 food from
   the owner, then **overwrites** the stash --
   `state["_pending_work_start"] = player["index"]` (the owner, who
   should place first) and sets `state["_resume_from"] =
   state["starting_player"]` (where rotation should resume once the
   owner's out-of-turn placement is done). Overwriting
   `_pending_work_start` here is safe and necessary -- it was only ever
   a "resume once the prompt drains" stash, not yet consumed by
   anything.
4. `_apply_choice`'s own tail calls `_advance_work(state, log)` (no
   explicit `start_pidx`) once the prompt drains; this pops the
   overwritten `_pending_work_start` (the owner's index) and gives them
   the turn.
5. The owner places their one person via the normal `_apply_place`,
   whose own tail again calls `_advance_work(state, log)` with no
   explicit `start_pidx`. This time `_pending_work_start` is already
   gone (step 4 consumed it), so the new fallback fires: it pops
   `_resume_from` (the true starting player, stashed in step 3) and
   rotation resumes from there in normal order -- exactly "after that,
   play starts with the starting player as usual."

A card implementing this recipe for real also needs its own
"food" affordability check before offering the prompt (declining or not
prompting at all if the owner can't pay) -- omitted here since the
recipe itself, not the card's cost-checking, is what's being
documented.

### Placement lockout (I71 Holiday House)

I71: "In round 14, you cannot place any people." Spec key
`placement_blocked=fn(state, player, inst) -> bool`; `cards.
placement_blocked(state, player)` is `True` if any in-play card's fn
returns `True` (query mirrors `occupied_ok`'s shape). `_placement_
actions` and `_skip_actions` both return `[]` immediately for a blocked
player (checked before anything else, including `people_placed`/
capacity), and `_apply_place`/`_apply_skip` both re-check and raise if
blocked -- so a stale/forged action can't sneak a placement through.
`_advance_work`'s existing forfeit branch then picks the blocked player
up automatically (no usable space, no skip either) and logs a
lockout-specific line ("cannot place any people this round and
forfeits N placement(s)") rather than the generic "cannot use any
action space" one, which would be misleading for a card-level lockout
rather than "every space happens to be unaffordable/unusable this
round." Feeding, scoring, and every other per-round mechanic are
untouched -- a locked-out player still eats normally at the next
harvest.

### K269 Acrobat / K289 Countryman: no engine change needed

Both read "after all players have placed [a person on some space], you
may move that person to [some other space] and use it." No new
mechanism is needed -- this is exactly the existing `returning_home`
hook (fires once per player, at the end of the work phase, before
`occupied_by`/`extra_occupants` reset) plus the existing `sub_actions`
transaction API:

```python
def _acrobat_style_hook(state, player, inst, ctx):
    if "some_space_id" not in ctx["spaces"]:
        return  # the person never occupied the qualifying space
    # Perform the target space's effect directly via sub_actions (e.g.
    # sub_actions.sow(...), sub_actions.build_rooms(..., cost_override=
    # "free"), grant_goods(...) for a fixed-gain space) -- NOT a literal
    # person move.
```

**Fidelity simplification, worth calling out explicitly:** this
recipe performs the TARGET action space's effect at the right time, but
does not literally relocate the person token -- the farmyard-cell
occupancy model has no "move a placed person to a different space"
primitive (see "Not supported" below: moving/removing built fences,
rooms, fields is the closest existing precedent for "no primitive
exists"), so the moved person never actually occupies the destination
space for `space_used`/`occupied_ok`/adjacency purposes, and the
ORIGINAL space they returned home from is what shows up in that
player's `returning_home` `ctx["spaces"]`. For K269/K289 specifically
this is invisible in practice (their target actions -- Fishing,
Farmland-style plow-and-sow -- don't depend on space occupancy after
the fact), but a future card whose target space cares about who's
"still standing there" would need a real fidelity check before reusing
this recipe.

## Hand and deck (engine phase 12)

Previously a dealt hand's leftover cards were simply discarded (deal_
hands sliced hands off a shuffled list and threw the remainder away),
and a card could never react from inside a hand, or reach into another
card's hand mechanics. Now: persistent draw/discard piles, plus a
narrow `hand_react` mechanism for a card that reacts while still unplayed.

### Draw and discard piles (K125 Broom)

`cards.deal_hands` now returns `(occ_hands, minor_hands, hand_size,
occ_draw, minor_draw)` -- `occ_draw`/`minor_draw` are whatever's left of
the shuffled decks after every hand is dealt, in shuffled order.
`engine.initial_state` stores these as `state["occupation_draw"]`/
`["minor_draw"]` (top of the pile = **index 0**), plus
`state["occupation_discard"]`/`["minor_discard"]` (start empty). There
is deliberately **no reshuffle-when-empty** -- the physical game never
reshuffles a spent draw pile, so drawing from an empty one just yields
fewer cards, never an error.

Helpers live in `cards.py` (not `sub_actions.py`): they only touch
`state`/`player` dicts directly (no cost, no `gained` event, no
in-play-card query), so there's no reason to route them through
`sub_actions`'s cost/transaction machinery, and putting them in
`cards.py` means both `sub_actions.py` and `engine.py` can call them
without any import-cycle risk (`cards.py` imports neither).

```python
cards.draw_minors(state, player, n, log)          # -> drawn card ids
cards.draw_occupations(state, player, n, log)     # occupation twin
cards.discard_hand_minors(state, player, log)     # -> discarded card ids
cards.discard_hand_occupations(state, player, log)  # occupation twin
```

`draw_minors`/`draw_occupations` draw `min(n, len(pile))` from the top
into `player["hand_minors"]`/`["hand_occupations"]` -- fewer than
requested once the pile runs short, `[]` from an empty pile.
`discard_hand_minors`/`discard_hand_occupations` move the player's
ENTIRE current hand (of that kind) to the matching discard pile and
empty the hand. Every access uses `.setdefault()`/`.get()`, so a state
dict missing these keys entirely (an old save from before this phase)
does not crash -- it behaves as if the pile were empty.

**K125 Broom recipe** ("discard all remaining minor improvements in
your hand, draw 7 new ones"):

```python
def _broom_play(state, player, inst, ctx):
    cards.discard_hand_minors(state, player, ctx["log"])
    cards.draw_minors(state, player, 7, ctx["log"])
```

Broom's own `play` hook runs (and this fires) AFTER
`sub_actions.play_minor` has already removed Broom's own card id from
`player["hand_minors"]` and BEFORE it's appended to `player["minors"]`
-- so `discard_hand_minors` only sees the REST of the hand, never Broom
itself, with no special-casing needed. ("You can play 1 more minor
improvement immediately" is separate, per-card follow-up work -- not
part of this pass, same as every other motivating card's fidelity
pass.)

**View safety**: draw/discard piles are shuffled/hidden state for
EVERY player, not just opponents (unlike a hand, which only its owner
may see) -- nobody should be able to read the future draw order or the
discard history off the wire. `get_player_view`/`get_spectator_view`
replace all four pile lists with their `len()` (same "count only"
treatment opponents' hands already get); `engine._hide_draw_piles` is
the shared helper both views call.

### Hand reactions (`hand_react`, E173 Chief's Daughter)

Some cards react while still sitting in a player's hand, unplayed --
E173: "If another player plays the Chief [E172], you can play this card
immediately at no cost." There is no card instance for a hand card (it
hasn't been played), so this can't be an ordinary hook (which always
gets `inst`). New TOP-LEVEL spec key (like `resolve_choice`, not inside
`hooks`):

```python
hand_react = {"event": "<event name>", "fn": fn}
# fn(state, hand_player, ctx) -> None   -- no `inst` argument
```

**Narrow by design**: only the two BROADCAST card-play events,
`occupation_played` and `minor_played`, are wired up -- from
`sub_actions._fire_broadcast`, right after the normal in-play firing:
```python
if event in ("occupation_played", "minor_played"):
    cards.fire_hand_react(state, event, ctx)
```
`cards.fire_hand_react` scans every player's hand (occupations then
minors, actor first -- same convention as `fire()`), calling `fn` for
every card spec whose `hand_react["event"]` matches. This is
deliberately NOT wired into every `fire()`/`fire_player()` call --
scanning every hand on every event (harvest, gained, space_used, ...)
would cost far more than any card so far needs; extend the event list
only when a real motivating card needs a different trigger. `fn` must
check `ctx["card_id"]` (and/or `ctx["actor"]`) itself -- a `hand_react`
that ignores it fires for every card play, which is almost never what a
real card's text wants.

**The `from_hand` prompt pattern.** E173 says "you CAN" -- it's a
choice, so the natural shape is `prompt_choice`, exactly like any other
mid-effect decision. But `resolve_choice` specs are normally looked up
on the card INSTANCE named by the prompt's `"card"` field (`_apply_
choice` searches every player's `in_play` list) -- a hand card has no
instance to find. `prompt_choice` takes a new `from_hand=True` flag for
this case; when `_apply_choice`'s instance search comes up empty AND
the prompt says `from_hand`, it falls back to the CARDS registry spec
directly and calls `resolve_choice` with **`inst=None`** -- document
this contract wherever a `from_hand` `resolve_choice` is written, since
every other `resolve_choice` in the codebase can assume a real
instance. A fn accepting the offer must play the card itself
(`sub_actions.play_occupation`/`play_minor`, typically with
`cost_override="free"`); declining just does nothing, leaving the card
in hand untouched -- there's no other bookkeeping to undo since nothing
happened yet.

**Worked example** (E173-style, a temp occupation reacting to a chosen
trigger card):

```python
def _daughter_hand_react(state, hand_player, ctx):
    if ctx["card_id"] != "e172_the_chief":
        return
    cards.prompt_choice(
        state, hand_player, "e173_chiefs_daughter",
        "Play Chief's Daughter now at no cost?", ["yes", "no"],
        from_hand=True)

def _daughter_resolve(state, player, inst, ctx):
    # inst is ALWAYS None here -- the from_hand contract.
    if ctx["option"] == "yes":
        sub_actions.play_occupation(state, player, "e173_chiefs_daughter",
                                    ctx["log"], cost_override="free")
    # "no": nothing to do, the card is still in hand.

compendium_card("E173",
    hand_react={"event": "occupation_played", "fn": _daughter_hand_react},
    resolve_choice=_daughter_resolve)
```

**Termination**: the reactive play itself fires `occupation_played`/
`minor_played` again (via the SAME `_fire_broadcast` choke point), which
scans hands again -- but `sub_actions.play_occupation`/`play_minor`
both remove the card from its hand BEFORE firing the broadcast, so the
just-played card can never re-trigger its OWN `hand_react` in that same
scan. A second, unrelated card's `hand_react` CAN still see this second
broadcast (as it should -- "any occupation played" includes one played
via a hand reaction) -- each such further reaction can only happen by
playing another card out of a hand, and every hand is finite and only
shrinks, so the chain always terminates; it never free-runs without a
player answering a new prompt at every step (see
`tests/test_agricola.py`'s `test_hand_react_reactive_play_does_not_loop`
for a worked two-hop chain).

### B023 Final Scenario: engine assessment

B023: "Place the action space card for round 14 face up in front of
you. Only you can use it until it is placed on the game board [at round
14]." Two things to check: (1) can a card reveal/host a FUTURE round
space early, and (2) can hosting it reuse that round card's own
resolution logic rather than reimplementing it.

**(1) is a non-issue in this engine as built.** `state["deck"]` is
hidden from every view (views carry only a count -- a pre-existing
leak of the full reveal order was fixed in this phase's review), but
B023 doesn't need a view exception: this engine has exactly one
stage-6 card, so round 14's identity is deterministic (see (2)), and
the `card_space` entry the recipe creates carries the card's name and
description publicly -- "placed face up in front of you" for free.

**(2) is expressible today with existing `card_space` + `sub_actions`
plumbing, no new engine change** -- but only because of a specific fact
about how THIS engine's stage decks are built: `state.STAGE_CARDS` has
exactly ONE stage-6 entry (`farm_redevelopment`), so `build_stage_deck`
always puts it last -- `state["deck"][13]` is deterministic, not a
random reveal, at the moment the game starts. That means a B023
`card_space` can safely hardcode a mirror of `farm_redevelopment`'s own
sid-dispatch logic (in `engine._resolve_space`) using the same
`sub_actions` calls that dispatch already uses, instead of needing a
generic "resolve whatever round 14 turns out to be" mechanism (which
the engine does NOT have: `_resolve_space`'s per-sid branches are
private to `engine.py`, some are trivial one-offs with no `sub_actions`
equivalent at all, and a card_space's `resolve` fn has no way to invoke
them by id):

```python
def _b023_resolve(state, player, inst, action, log):
    sub_actions.renovate(state, player, log,
                         free_stable_cell=action.get("stable"))
    if action.get("fences"):
        sub_actions.build_fences(state, player, action["fences"], log)
    return {}

def _b023_usable(state, player, inst):
    # Gate closes the moment round 14 starts -- the real
    # farm_redevelopment space (state["deck"][13]) takes over from here.
    return state["round"] < 14 and sub_actions.renovation_possible(state, player)

compendium_card("B023", card_space={
    "owner_only": True, "usable": _b023_usable, "resolve": _b023_resolve})
```

**Caveat, worth re-checking before this recipe is ever actually used**:
this hardcodes farm_redevelopment's mechanics because that's the ONLY
possible round-14 card TODAY. If a future deck module ever adds a
second stage-6 card, `state["deck"][13]` stops being deterministic and
this recipe would silently mirror the wrong mechanics whenever a
different card is revealed there -- a real B023 registration should
assert `STAGE_CARDS` still has exactly one stage-6 entry (or otherwise
confirm `state["deck"][13] == "farm_redevelopment"` at resolve time)
rather than assume it forever. None of K125/E173/B023 are registered by
this pass (`temp_card`-only tests in `tests/test_agricola.py` exercise
each mechanism, including a `test_b023_style_recipe_owner_only_until_
round_14` proof of the recipe above); registering them with
`compendium_card` is still a separate pass, same as every other item.

## Field/fence/grid extensions (engine phase 13)

The 19th and final item of the engine-gap program: card fields with
more than one independent crop stack, a fence-piece substitute (wood
tokens), a "remove a field" recipe, and an assessment of per-player
farmyard-grid growth. Motivating compendium cards: K105 Acreage, FR089
Landscape Gardener, C069 Land Consolidation, B030 Wood Palisades, FR001
Abandoned Willow, FR059 Witches' Dance Ground. None of K105/FR089/C069/
B030/FR001 are registered by this pass (`temp_card`-only tests in
`tests/test_agricola.py` exercise each mechanism, same as phases 8-12);
registering them with `compendium_card` is still a separate pass. FR059
remains gated -- see its own subsection below.

### Field stacks (K105 Acreage, FR089 Landscape Gardener)

A card field's `field={"crops": (...), "stacks": n}` spec key gained an
optional `"stacks"` entry, defaulting to 1. A stacks=1 card (every card
field registered before this phase, and the overwhelming majority of
future ones) is stored EXACTLY as before: `inst["crops"]` is `None` or
`{"type", "count"}`, no `inst["stacks"]` key at all -- zero migration,
every pre-phase-13 save is untouched, and nothing reading `inst["crops"]`
directly for a stacks=1 card (there are many -- see the audit below)
needs to change.

A card declaring `stacks > 1` instead gets `inst["stacks"]`, a list of
`stacks` independent `{"type", "count"}`-or-`None` slots, set up once in
`cards.new_instance`. Four helpers in `cards.py` are the ONLY sanctioned
way to read/write a card field's crop(s) -- they transparently cover
both shapes (and an OLD SAVE's stacks=1 instance, which has `"crops"`
but no `"stacks"` key, exactly like a fresh stacks=1 instance):

- `cards.get_field_stack(inst, i=0)` -- stack `i`'s crop dict or `None`.
- `cards.set_field_stack(inst, i, value)` -- write stack `i`.
- `cards.field_stacks(inst)` -- every stack's crop-or-`None`, in order
  (`[inst.get("crops")]` for a stacks=1/legacy instance).
- `cards.open_field_stacks(inst)` -- indices of currently-unplanted
  stacks.

**Sow action shape:** `sub_actions.sow`'s `sow_items` entries for a card
field gained an optional `"stack"` key: `{"card": cid, "crop": ...,
"stack": i}`. Omit it for a stacks=1 card (defaults to 0, identical to
before). A stacks>1 card may appear TWICE in one `sow_items` list (once
per stack index) -- sowing both of K105's grain stacks, or one of
FR089's stacks with grain and the other with vegetable, in a single
action. Each `(card, stack)` pair may only be sown once per call, and
`stack` must be in range for that card's `field["stacks"]`.

**Harvest semantics:** `engine._run_field_phase`'s card-field loop
iterates `cards.field_stacks(inst)` and credits/decrements each stack
independently -- a stacks=2 card with both stacks planted yields 2 crops
per harvest, one from each stack; `cards.keep_crops_on_harvest` is
checked per stack, same as before per card. `scoring.score_player` sums
every stack's crop into the grain/vegetable scoring category the same
way (via `cards.field_stacks`); card fields already never counted
toward the `"fields"` scoring category (`score_player`'s `fields` tally
only ever counts farmyard CELL tiles, never `cards.card_fields`), so
FR089's "(this card does not count as a field when scoring)" is true of
every card field for free, stacks or no stacks.

**Old-save compat:** an instance persisted before this phase has
`"crops"` and no `"stacks"` key at all -- every helper above treats that
identically to a freshly-created stacks=1 instance (see
`test_field_stack_helpers_read_old_save_shape_safely`).

**Audit: every `inst["crops"]`/`card_fields` reader, and its
disposition** (grep for `\["crops"\]`/`card_fields(` across
`server/agricola/` turns up ~17 files):

- `cards.py` (`needs_grain_field`, `_scythe_hook` -- Scythe), `engine.py`
  (`_run_field_phase`), `scoring.py` (`score_player`) -- all UPDATED to
  read via `cards.field_stacks` (see above), so they see stacks>1 crops
  correctly even though no such card is registered yet.
- `sub_actions.py` (`empty_fields`, `sow`) -- UPDATED (see above).
- Every per-card compendium implementation across `decks/deck_*.py`
  (deck_a/b/c/d/e/i/k/fr's minors and occupations modules, plus
  `tools/export_agricola_cards.py`'s catalog exporter) reads/writes
  `inst["crops"]` DIRECTLY for a card's own effect (e.g. a "your grain
  card fields" counter, or a sow-reactive hook bumping the freshly-sown
  crop's count -- Seed Drill, and compendium E32 Potato Dibber/K118
  Liquid Manure/K-deck Smallholder all do `target["crops"]["count"] +=
  n` from the `sow` event's `ctx["sown"]` list). These are UNCHANGED and
  safe TODAY, because every field card actually registered defaults to
  stacks=1, for which `inst["crops"]` is exactly the old shape. They are
  NOT stack-aware: a stacks>1 card's `inst["crops"]` is always `None`
  (its crops live in `inst["stacks"]` instead), so a future card
  registration that combines a stacks>1 field with one of these
  sow-reactive cards would need updating them to use `cards.
  get_field_stack`/`field_stacks` -- flagged here for whoever registers
  K105/FR089 for real. The `sow` event's `ctx["sown"]` list itself was
  deliberately left as `(target, crop)` 2-tuples (target = the card
  instance or cell index, exactly as before) rather than growing a
  third "which stack" element, specifically to avoid a mass rewrite of
  these ~10 existing consumers; `sub_actions.sow`'s own stack bookkeeping
  (which stack was just planted, and the same-stack-twice check) is
  purely internal to that function and never needs to leak into `sown`.

### Fence tokens (B030 Wood Palisades)

"Instead of a fence piece, you can place 2 wood from your supply on
fence spaces at the edge of your farmyard. These fence spaces with 2
wood are worth 1 bonus point." Requirements: a token edge (a) completes
enclosures exactly like a fence, geometrically; (b) does NOT count
against `MAX_FENCES` (15); (c) costs what the card says (2 wood),
independent of normal fence pricing/discounts; (d) scores a bonus point;
(e) is restricted to the farmyard's outer border.

`player["fences"]` stays the SINGLE geometric truth -- every pasture/
enclosure computation (`compute_pastures`, `validate_fence_layout`'s own
connectivity/enclosure checks) is completely untouched; a token edge is
just a normal member of that list. `player["fence_tokens"]` (new, `{}`
by default -- see `create_player`; `.get("fence_tokens", {})` everywhere
else so an old save missing the key entirely doesn't crash) maps
`edge -> granting card id` for whichever of `player["fences"]` are
tokens. A card grants this with the spec key `fence_token={"cost":
{"wood": 2}}`; `cards.fence_token_card(player)` finds the (first)
in-play card granting it.

`state.validate_fence_layout` gained an optional `token_edges=
frozenset()` parameter (backward compatible -- every pre-existing call
site passes 2 positional args and is unaffected): its `MAX_FENCES` check
becomes `len(fences) - len(token_edges & fences) > MAX_FENCES`, so token
edges don't count against the cap even though they're part of the same
geometric layout being validated. `state.is_border_edge(edge)` (`len(
edge_cells(edge)) == 1`) is the new border-eligibility check.

`sub_actions.build_fences` gained an optional `tokens=` parameter (a
subset of `new_fences` to satisfy with wood tokens instead of a fence
piece):

```python
sub_actions.build_fences(state, player, new_fences, log, tokens={edge, ...})
```

It validates `tokens <= set(new_fences)`, that the player has a
`fence_token_card`, and that every token edge is a border edge (`state.
is_border_edge`); prices the REMAINING (non-token) edges through the
normal `cost_override`/`modified_cost` path exactly as before, then adds
the granting card's own `fence_token["cost"]` (per token, unconditional
-- not affected by `cost_override="free"`, since it's the CARD's own
price, not the generic fence cost); and records each token edge in
`player["fence_tokens"]`. `can_build_fences`'s own `MAX_FENCES` pre-check
similarly excludes `player.get("fence_tokens", {})` from the count.
Scoring is a normal `score_bonus=fn(state, player, inst) -> int` --
`B030`-style: `sum(1 for owner in p.get("fence_tokens", {}).values() if
owner == inst["id"])`; no new scoring plumbing needed.

Engine wiring: the two action-space dispatch sites that build fences
("fencing", "farm_redevelopment") now thread an opaque `action.get(
"tokens")` through to `_do_build_fences`/`sub_actions.build_fences`,
exactly parallel to the existing `action.get("payment")` channel.

**Client note:** `client/games/Agricola_MP.jsx` renders fences from
`player["fences"]` alone; a token edge renders identically to a normal
fence today (acceptable for now -- no client change made in this pass).

### FR001 recipe: "remove an empty field"

FR001 Abandoned Willow: "Immediately remove 1 empty field from your
farmyard and receive 4 wood. (That space now counts as unused.)" No new
engine plumbing needed -- exactly like Shifting Cultivation's on-play
plow (`ctx["params"]["cell"]`, `player["cells"][cell]["type"] =
"field"`), a play hook can flip a field cell back with the same
primitive, run in reverse:

```python
def _abandoned_willow_play(state, player, inst, ctx):
    cell = (ctx.get("params") or {}).get("cell")
    c = player["cells"][cell] if isinstance(cell, int) else None
    if c is None or c["type"] != "field" or c["crops"]:
        raise ValueError("Choose an empty field to remove (params.cell)")
    c["type"] = "empty"
    player["resources"]["wood"] += 4
    ctx["log"].append(f"{player['name']} removes an empty field, gets 4 wood")
```

Verified no hidden invariant breaks: `plowable_cells` treats the
reverted cell as a normal empty, unfenced, un-stabled candidate (plowing
it again works exactly as if it had never been a field); pasture/fence
validation treats it as an ordinary empty cell (`validate_fence_layout`/
`compute_pastures` don't special-case field history); and `score_player`
counts it as `unused` (the same as any other empty, unstabled, unfenced
cell) rather than as a field, matching the card's own "(that space now
counts as unused)" clause exactly. See `test_fr001_style_remove_empty_
field_recipe` for the full regression (plow target, pasture validity,
and scoring all checked after the revert).

### FR059: gated (grid growth)

FR059 Witches' Dance Ground: place beside the farm for +2 farmyard
spaces (plus 1 bonus point), or place ON the farm to remove 2 spaces.
Reassessed for this phase and still gated -- no clean seam: `player[
"cells"]` is a fixed-length (`NUM_CELLS` = `ROWS * COLS` = 15) list, and
EVERY geometry query assumes that fixed shape -- `cell_rc`/`cell_index`/
`cell_edges`/`edge_cells` (the (row, col) <-> index <-> edge-key
coordinate system fence edges are keyed on) are module-level functions
over the GLOBAL `ROWS`/`COLS` constants, not per-player state;
`orthogonal_neighbors`, `compute_regions`'s flood fill, and
`plowable_cells` all walk that same fixed grid; and `SCORING_TABLES`'s
`"fields"`/`"pastures"`/`unused_spaces` categories are tuned to a
15-cell farmyard. Making the farmyard variable-length per player would
mean threading a per-player (rows, cols, extra-cells) shape through
every one of those, plus the fence edge-key scheme itself -- materially
bigger than the multi-stack/fence-token additions above, not a bounded
engine change. This is the one remaining gated geometry gap after phase
13 (see CARDS.md item 19).

## Not supported (mark UNIMPLEMENTED, cite the mechanic)

- Affecting other players' hands, people, or farms directly (guest
  tokens and occupied-space placement, per se, ARE supported now --
  see `grant_guest`/`occupied_ok` above -- but removing/returning
  *another player's* person, as in D093 Sheep Inspector or D094/D150
  "return the first person you placed home," is not: there's no
  "unplace" primitive)
- Farmers of the Moor concepts: fuel, horses, forest/moor tiles,
  heating, special actions (all of deck M, and FotM-tagged rulings)
- Moving/removing built fences or rooms (removing a FIELD is now
  supported -- see "Field/fence/grid extensions"'s FR001 recipe above;
  fences/rooms are a materially bigger case: a fence edge's removal can
  split/merge pastures and strand animals, and a room's removal
  interacts with `house_capacity`'s `"per_room"` mode, `extra_rooms`,
  and the family-growth room check, none of which a plain cell-type
  flip accounts for)
- Per-player farmyard grid growth/shrinkage beyond the fixed 3x5 board
  (FR059 -- see "Field/fence/grid extensions" above)
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
