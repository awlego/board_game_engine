# Agricola Card-Effect Architecture

How this engine supports Agricola's hand cards — hundreds of occupations and
minor improvements, each with a unique rules-bending effect — without turning
`engine.py` into a pile of special cases.

## The problem

Agricola cards do six fundamentally different kinds of things:

1. **One-shot effects on play** — "When you play this card, you immediately
   get 2 wood / plow a field / place food on round spaces."
2. **Triggered effects** — "*Each time* you use the Fishing space / take wood
   / bake bread / build fences / a harvest begins, ...". These are reactions
   to engine events, sometimes to *other players'* events.
3. **Static modifiers** — "Rooms cost 1 less stone", "each pasture holds 2
   more animals", "you may keep 1 animal per room". These aren't reactions;
   they change the *answers to questions* the engine asks (costs, capacities,
   legality).
4. **New conversions** — extra cook/bake/raw exchange rates, used inside the
   existing feeding/baking machinery.
5. **Card-held components** — cards that are themselves fields ("Beanfield"),
   hold scheduled goods, hold animals directly on the card itself, or count
   usage.
6. **Scoring effects** — printed points plus conditional bonus points, some of
   which compare across players ("the player with the most rooms...").

A naive approach (an `if card == "woodcutter"` in every engine function) does
not scale past a dozen cards. The design below scales because the **engine
never knows about individual cards** — it only knows about *hook points* and
*queries*, and cards register handlers for them.

## Design: data-driven registry + event hooks + modifier queries

### 1. The registry (`cards.py`)

Every card is a dict **spec** in a global `CARDS` registry, keyed by id:

```python
card("occ_woodcutter", "Woodcutter", "occupation",
     text="Each time you take wood from an action space, you get 1 wood more.",
     hooks=take_bonus(["wood"], {"wood": 1}))
```

A spec contains only *data and pure functions* — never mutable state. All
mutable card state (goods on the card, counters, crops on card fields) lives
in the **card instance**, a plain dict inside the game state
(`player["occupations"]` / `player["minors"]`):

```python
{"id": "minor_beanfield", "crops": None, "data": {}}
```

This keeps the engine contract intact: game state stays a JSON-serializable
dict (the server snapshots and broadcasts it); logic stays in code, looked up
by card id at runtime.

### 2. Event hooks (push)

The engine **fires events** at well-defined moments; every in-play card of
every player gets a chance to react:

```python
fire(state, "space_used", {"space_id": "fishing", "actor": 2,
                           "goods": {"food": 3}, "extra": {}, "log": log})
```

A hook is `fn(state, player, card, ctx)`. It can mutate the player, stash
counters in `card["data"]`, append log lines, and add goods to `ctx["extra"]`
(the engine merges extras afterward, routing animals through the normal
accommodation flow — so a card that grants sheep automatically triggers the
"place, cook, or discard" prompt). `ctx["extra"]` only reaches the *acting*
player, though — for goods granted to some other player (an "each time ANY
player does X, the card owner gets Y" effect), use `cards.grant_goods(state,
player, gain, log)`, which credits non-animal goods directly and queues an
accommodation prompt for animal goods. `on_play_gain` and
`space_bonus(..., others=True)` already do this internally, so declaring a
card with either factory is animal-safe regardless of the gain's contents.

Every credit above -- `ctx["extra"]`, `grant_goods`, an action space's own
goods, harvest crops, baking, feeding conversions, a scheduled round-space
payout, or a newborn animal from breeding -- also fires the generic
`gained` event (`cards.fire_gained`, owner-only), so a card can react to
"each time you obtain X, from any source" without caring which of those
paths produced it. See `decks/GUIDE.md`'s hook table for the full ctx
shape (including the animals-in-goods caveat and the chained-grant depth
guard).

Current hook points: `play`, `occupation_played`, `round_start`,
`space_used`, `fences_built`, `stable_built`, `rooms_built`, `sow`, `bake`,
`renovate`, `plow`, `harvest_start`, `harvest_field`, `gained`, `breeding`,
`converted`, `returning_home`.

Firing order is deterministic: players in index order starting with the
actor, then each player's cards in play order. `space_used` fires for *all*
players' cards, which is how "each time *another player* uses the Cattle
Market..." cards work — the hook just checks `ctx["actor"]`. `converted`
(any goods→goods conversion outside a normal space grant: feeding-phase
conversions, cooking during accommodation) is broadcast the same way.

`renovate`, `plow`, `sow`, `rooms_built`, `stable_built`, and `bake` fire
only to the *acting* player's own cards (`fire_player`) — dozens of
shipped cards assume that. Each of these also fires a broadcast twin,
`<event>_any` (e.g. `renovate_any`), to every player's cards with the
same ctx plus `actor`, so "each time ANOTHER player renovates/plows/
sows/builds/bakes..." cards are expressible without disturbing the
existing owner-only cards. See `decks/GUIDE.md`'s hook table for the
full ctx shape of every event, including `converted` and
`returning_home` (fired once per player at the end of the work phase,
before `occupied_by` resets).

### 3. Modifier queries (pull)

For static effects, the engine *asks* instead of being told. Each query scans
the acting player's in-play specs:

- `modified_cost(state, player, kind, cost, ctx)` — folds every card's
  `cost_mod(kind, cost, ctx)` over the base cost. Kinds: `room`,
  `stable`, `fences`, `renovation`, `improvement`, `minor`,
  `occupation`. `ctx` carries a batch `count`/`start_index` (rooms and
  fences are priced as one batch total; stables are priced one at a
  time and additionally get a 1-based `index`), the originating
  `space_id` (when the build came from an action space), the
  `improvement`/`card` id, and a `payment` choice blob (opaque; the
  card decides the schema, e.g. `{"reed_to_clay": 2}`) threaded from
  the client action's own `payment` field. See `decks/GUIDE.md`'s
  `cost_mod` entry for the full per-kind contract and a worked payment
  example. `occ_cost_delta` (a separate, simpler query) still applies a
  flat per-occupation delta ("each occupation costs 1 less") ahead of
  the `kind="occupation"` fold.
- `pasture_bonus(player)` — extra capacity per pasture (additive).
- `house_capacity(player)` — how many pets the house holds (Animal Tamer
  returns one per room; declared as data: `house_capacity="per_room"`).
- `extra_rooms(player)` — cards that provide room for people (Caravan),
  consulted by the family-growth room check.
- `raw_values(player)` / cook & bake specs — cards declare `cook`, `bake`,
  `raw_values`, `bake_bonus` keys with the same shape the major improvements
  use, so the feeding/baking code treats a card ability and a Fireplace
  identically.
- `bake_on_spaces` — cards granting an extra "Bake Bread" action on specific
  action spaces (Threshing Board); planners just pass a `bake` param.

Queries are pure functions of the state, so validity checks
(`get_valid_actions`) and application (`apply_action`) can't disagree.

### 4. Choices and parameters

Cards that need decisions take them as **action parameters**, validated by
the card's own `play` hook (e.g. Shifting Cultivation's plow target:
`{"kind":"place","space":"lessons","card":"...","params":{"cell":7}}`).

Mid-effect decisions use the **prompt queue** (`state["prompts"]`): a hook
calls `prompt_choice(state, player, card_id, question, options)`; the engine
blocks until the target player answers `{"kind":"choice","index":i}`, which
dispatches to the card's `resolve_choice` hook. Gaining animals enqueues the
same accommodation prompt the Sheep Market uses. This is safe to do from any
hook point, including `round_start` and `returning_home` — the engine holds
the game on the prompt's target player and resumes the round/harvest
transition correctly once it's answered (see `decks/GUIDE.md`), the one
exception being a traveling card's own `play` hook (see that file). Prefer
parameters (known up front, one round trip) when the decision space is a
board choice; prefer prompts for "your choice of X or Y" effects.

**Activated abilities** are the third input channel: a card with a
`card_action` spec ({available, apply, description}) is offered as an extra
action on the owner's work turn and during feeding, without consuming a
person placement. **Conversions** (`conversions=[{give, get, per_harvest?}]`)
plug into the feeding dialog as additional exchange rates.

### 5. Card-held components

Card fields declare `field={"crops": ("vegetable",)}`; the sow action accepts
`{"card": id}` targets, harvest's field phase walks card crops, and scoring
counts them as crops but *not* as field tiles (matching the rulebook).
Scheduled goods ("place 1 food on the next 3 round spaces") write into
`state["round_goods"][round][player]`, the same mechanism the Well uses.

### 6. Scoring

`score_bonus(state, player, card) -> int` per card, summed into the Bonus
category; printed `points` join the improvements category. Bonus functions
receive the full state so cross-player conditions ("no player has more...")
are one comparison, not an engine feature.

### 7. Bonus build/play sub-actions (`sub_actions.py`)

Roughly 25 cards grant something like "immediately build a room / up to 2
stables / fences / a minor or major improvement / sow / play an occupation,
possibly at a discount or free". Building a room, raising fences, renovating
a house, building a major improvement, playing an occupation/minor, and
sowing a field all have real rules (cell adjacency, house-type-dependent
cost, `available_improvements`/`occs_played` bookkeeping, ...); duplicating
that per-card is exactly the kind of drift the architecture exists to avoid,
so **`server/agricola/sub_actions.py`** is the single, engine-independent
implementation of each transaction. `engine.py` cannot be imported from
`cards.py`/`decks/*.py` (it imports `cards`, which would create a cycle), so
`sub_actions.py` is a third module both sides call into: `engine.py`'s own
action-space dispatch (`_do_build_rooms`, `_do_build_stables`,
`_do_build_fences`, `_do_renovate`, `_do_improvement`, `_play_minor`,
`_play_occupation`, `_do_sow`) is now a thin wrapper around it, and any card
hook or `card_action` can call the same functions directly.

Each transaction function takes the normal `(state, player, ..., log)`
shape and raises `ValueError` on an illegal target (adjacency, occupied
cell, invalid fence layout, ...) — safe to call from any hook because the
engine deep-copies state before applying an action, so a raised error rolls
back cleanly. Cost is `cost_override=None|dict|"free"`: `None` runs the
normal cost through `cards.modified_cost` (so a Stonecutter/Hedge
Keeper-style discount still folds in); a dict is an exact cost to charge
instead (compute your own flat discount and pass it); `"free"` skips
payment. A matching `can_*`/`*_possible` predicate exists for each
transaction (same cost contract) for a `card_action`'s `available` or a
hook's own gating — see `decks/GUIDE.md` for the full API and examples per
input channel (`play` hook params, `card_action` params, `prompt_choice`).
The (behavior-preserving) migration of the pre-existing cards that used to
hand-duplicate fragments of these transactions — Builder's Trowel, Educator,
Scholar, Junior Artist, Craft Teacher, Master Builder, Mason, Hammer
Crusher, Dwelling Plan, Renovation Company, Carpenter's Axe, Furrows, Hut
Builder — is the reference for how to use the module.

## Prerequisites, costs, decks, and dealing

- **Prereq** is a `(predicate, text)` pair; helpers exist for the common
  shapes (`needs_occupations(n)`, `needs_rooms(n)`, `exact_occupations(n)`,
  house-type checks). Prereqs gate playability only — never the effect.
  The engine enforces a card's declared `prereq` for occupations and
  minor improvements alike (`_play_occupation` and `_play_minor` both
  call `check_prereq`, and both are excluded from `get_valid_actions`
  when unmet). A round-dependent or resource-cost condition that can't
  be expressed as a static `(state, player) -> bool` predicate (e.g. "pay
  1 food per remaining harvest") still has to raise from inside the
  card's own `play` hook — that's a variable cost, not a prereq.
- **Cost** is a plain goods dict; anything payable (`{"grain": 1}` included).
- **Deck & player count**: every card carries `deck` ("base" or "custom") and
  `min_players` (1, 3, or 4 — the `occ-1/occ-3/occ-4` classes from the
  official cards). Dealing filters by player count and deals 7 occupations +
  7 minors to each player without replacement.
- **Traveling cards** (`traveling=True`): after the play effect resolves, the
  card goes to the left neighbor's hand instead of into play (removed from
  play in solo games, per the solo rules).

## Factories keep the common cases one-liners

Most Agricola cards are variations on a few shapes, so `cards.py` provides
factories: `take_bonus(goods, gain)`, `space_bonus(spaces, gain)`,
`round_income(gain, condition)`, `schedule_next(good, n)`,
`harvest_food(fn)`. A new "Each time you use X, get Y" card is one
declaration. Genuinely novel cards (Lasso's double placement, Shepherd's
Crook's fence trigger) write a custom hook or, rarely, a small engine feature
exposed as a spec key — the Lasso is the only card in the current set that
needed one (`lasso=True`, consulted by the placement flow).

## Importing cards from the CSV dump

`game_rulebooks_and_resources/Agricola/AgricolaCards-5-4-2026/agricola_cards.csv`
(overnightlemons.com repo) holds 4,360 community cards from
play-agricola.com: `card_id, deck, type (occ-1/occ-3/occ-4/minor), name,
text, cost ("1W,1S" — W/C/R/S/F/G/V), vps, prereq (free text), ...`.

The pipeline for adopting one:
1. Parse the mechanical fields (`type` → card type + `min_players`, `cost`
   via `parse_cost()`, `vps` → points, `prereq` → a predicate helper).
2. **Re-template the text** (per the dump's README, the free-text effects
   need MTG-style normalization) into one of the factory shapes or a custom
   hook.
3. Register with `deck="custom"`.

Step 2 is inherently manual (or LLM-assisted) — the text is natural language
written by hobbyists. The architecture's job is to make the *target* trivial:
if the effect is expressible as trigger→gain, static modifier, scheduler, or
scoring function, the port is a few lines. Several CSV cards are already in
the base pool as proof (Hermit's Stick, Estate Manager, Deaconess, Harvest
Totem — search `deck="custom"` in `cards.py`).

## What the current set covers / known limits

The hand-written "base"/"custom" decks (~62 cards) span every mechanism
above; the compendium decks under `decks/` (see `decks/GUIDE.md` and the
card database in `data/compendium_cards.json`, parsed from the General
Compendium by `tools/parse_compendium.py`) build on the same registry.
Cards whose mechanics the engine cannot express are tracked per deck module
in `UNIMPLEMENTED` (aggregated by `cards.load_decks()`), excluded from deal
pools, and flagged in the client catalog.

Known remaining gaps, and how they'd fit if a future card needs them:

- **Guest tokens / extra people** and **replacement effects** ("use an
  occupied action space") are now supported by the engine —
  `cards.grant_guest(player, n)` (a per-round placement counter folded
  into `_advance_work`/`_placement_actions`/`_apply_place`'s capacity
  check, alongside `people_total`) and the `occupied_ok=fn(state,
  player, inst, space) -> bool` spec key (consulted by
  `_placement_actions`/`_apply_place` before the normal occupied-space
  check), respectively. See `decks/GUIDE.md` for the full contract
  (guest reset timing and feeding/scoring isolation; the `occupied_by`
  + `extra_occupants` representation for spaces shared by more than one
  player, and the client-rendering gap that leaves). No card actually
  uses either mechanism yet — implementing the ~20 blocked compendium
  cards that need them (Guest I73, Telegram A022, Bassinet A025, Head
  of the Family E159, Field Warden E163, Hay Rake FR029, Second Spouse
  C129, Little Peasant B151, Seatmate B129, ...) is a separate pass.
- **Farmers of the Moor is not supported** (Alex's standing policy,
  2026-07-11): deck M (fuel/heating, horses, forest/moor tiles — a
  whole expansion's systems) stays unimplemented, and `(FotM)`-tagged
  DB rulings on base-deck cards are ignored — base-game text/values
  are implemented, with the ignored ruling noted in a comment (see
  decks/GUIDE.md ground rule 5).
- **Bonus build/play sub-actions** (build a room/stable/fence, renovate,
  build a major improvement, play a minor improvement, sow, play an
  occupation, at full/discounted/free cost) are now supported — see
  section 7 above and `sub_actions.py`. This unblocks the "reactively
  grant a discounted/free build or play, banked as a credit and spent via
  a `card_action`" shape (the same pattern Craft Teacher/Scholar/Educator
  already used before they were migrated onto the shared implementation)
  for compendium cards previously marked `UNIMPLEMENTED` for exactly this
  reason (A095, A096, A128, A149, A150, B088, B093, B130, B150, C096,
  C097, C131, C152, D129, D130, E179, FR072, FR078, I228, I249, among
  others) — implementing them is still a separate pass (fidelity to each
  card's own text, and in a few cases a still-missing mechanic like a
  "which action space" redirect, remain to work out per card).
- **A generic "resource gained" event, a breeding-phase event, and
  richer harvest-yield data** are now supported — `gained` (any goods
  credited to a player, from any source, including animal types),
  `breeding` (per player, after that player's own breeding resolves:
  which animals bred and which had no room), `harvest_start` (fires
  before the field phase, for a "before you harvest, you may..." effect),
  and `harvest_field`'s enriched ctx (`got`/`tiles`/`card_fields`
  breakdown) plus the `keep_crops_on_harvest` query (credit a crop
  without removing it from the field). See section 2 above and
  `decks/GUIDE.md`'s hook table for the full contracts (prompt safety,
  the chained-grant depth guard, the transient `"breeding"` phase).
  This unblocks compendium cards previously marked `UNIMPLEMENTED` for
  exactly these missing mechanics -- A048 Shaving Horse, A064 Barley
  Mill, C120 Agricultural Labourer, B021, K310, FR068, FR098 (`gained`);
  C071, B011, D036 (`breeding`); D065, B132, D126, I226, D070
  (`harvest_start`/enriched `harvest_field`) — implementing each card's
  own text (and, for D070, the "pay 1 grain, up to 2 fields" choice
  itself) is still a separate pass.
- **A richer `cost_mod` ctx, plus `stable`/`minor`/`occupation` joining
  `room`/`fences`/`renovation`/`improvement` as cost_mod kinds**, are
  now supported (engine phase 7). Previously: `room`/`renovation`/
  `improvement` cost_mod calls got no ctx at all (no build count, no
  originating space, no improvement/card id); stables paid a hardcoded
  `{"wood": 2}` never routed through `modified_cost`; `play_minor`/the
  Lessons spaces' food cost never called `modified_cost` either. Now:
  room and fence cost is priced as one BATCH TOTAL with `ctx["count"]`
  (and `ctx["start_index"]` for fences); stables route through
  `modified_cost` one at a time with `ctx["index"]`/`ctx["start_index"]`
  for per-Nth pricing; every kind gets `ctx["space_id"]` (the
  originating action space, absent for card-driven calls) and, where
  relevant, `ctx["improvement"]`/`ctx["card"]`; minor and occupation
  food costs run through `modified_cost` (kinds `minor`/`occupation`);
  and a new `ctx["payment"]` channel carries an opaque, card-defined
  payment choice from the client action's own `payment` field (a
  cost_mod consuming it must validate it and raise `ValueError` on
  garbage). See `decks/GUIDE.md`'s `cost_mod` entry for the full
  contract and a worked payment example. This unblocks compendium cards
  previously marked `UNIMPLEMENTED` for exactly these gaps -- A014
  Carpenter's Hammer and FR094 Miser (batch/exact-count room discounts,
  need `ctx["count"]`); D082 Hunting Trophy (space-conditioned discount,
  needs `ctx["space_id"]`); E15 (Clay/Stone Oven) and I220 Well Builder
  (per-improvement discount, needs `ctx["improvement"]` -- the
  major->minor reclassification half of each card is still a separate,
  unrelated gap); E36 Clay Roof (reed<->clay payment choice, needs
  `ctx["payment"]`); FR069 Cat Lover and C088 Carpenter's Apprentice
  (stable-cost discounts, need `kind="stable"`); FR024 (food discount on
  occupations/minors, needs `kind="minor"`/`"occupation"`); K121
  Sawhorse's stable clause was migrated off its old refund-after-the-
  fact hack onto a real `kind="stable"` cost_mod. Implementing the
  still-unregistered cards (A014, FR094, D082, E15, I220, E36, C088,
  FR024) themselves -- i.e. registering them with `compendium_card`, not
  just closing the plumbing gap -- is still a separate pass.
- **A per-pasture/type-aware capacity model, and card-held animal
  storage** (item 14) are now supported (engine phase 8). Previously:
  `pasture_capacity`/`validate_animal_placement` only knew a flat
  "2 per cell, doubled per stable, +flat card bonus, one type per
  pasture, 1 animal in an unfenced stable" model, with no way for a
  card to condition capacity on pasture size or animal type, let two
  types share a pasture, or store animals anywhere but a farmyard cell.
  Now: `pasture_capacity_mod=fn(state, player, inst, info) -> int`
  (per-pasture-size/type capacity deltas), `unfenced_stable_capacity_mod
  =fn(state, player, inst, animal_type) -> int`, and
  `pasture_secondary_types=fn(state, player, inst, info) -> {type:
  max_count}` (a second type sharing a pasture, still counting against
  its normal total capacity) are folded together by
  `cards.pasture_capacity`/`cards.unfenced_stable_capacity`/
  `cards.pasture_secondary_types`; `state.validate_animal_placement`
  takes these as callbacks (defaults reproduce the original flat
  behavior) so it stays card-free. Separately, `holds_animals=fn(state,
  player, inst) -> {"types": {type: max_or_None}, "total": max_or_None}`
  lets a card keep animals on itself (`inst["held"]`), validated by
  `cards.validate_held`; `state.animal_counts` reads `inst["held"]`
  directly (pure data, no `cards` import) so breeding, feeding-
  conversion availability, and scoring see card-held animals for free;
  accommodation placements accept a `{"card": ..., "type": ..., "count":
  ...}` entry alongside the usual cell entry, and newborn auto-placement
  falls back to a holder card once the farm is full. `extra_rooms`/
  `house_capacity` can now also be `fn(state, player, inst) -> int`
  instead of only a flat int/`"per_room"`. See `decks/GUIDE.md`'s "Card-
  aware animal capacity" section for the full contracts (the mixed-type
  "try each primary" validation algorithm, the holder-card contract, and
  the newborn-placement order). This unblocks (or partially unblocks --
  fidelity to each card's own text is still a separate pass, per rule 2)
  the motivating compendium cards: D011 Lawn Fertilizer, E29 Shepherd's
  Pipe, B115 Tinsmith Master, FR013 Chameleon (capacity model); C012
  Cattle Farm, E58 Animal Yard, I102 Wildlife Reserve, K145 Forest
  Pasture, A148 Woolgrower, B148 Pet Broker, FR105 Reformer, C148 Mud
  Wallower (card-held storage; C148's own clay-to-boar exchange "held by
  this card" is a separate, still-unimplemented conversion mechanic);
  D085 Reader, A127 Lodger (computed `extra_rooms`). A085 Homekeeper
  ("exactly one room, if adjacent to a field and a pasture")
  and C085 Den Builder (pay to activate a room) still need a *dynamic,
  farm-geometry-or-payment-conditioned* `extra_rooms`, which the
  callable form supports in principle but neither card is registered
  yet. D148 Domestician Expert ("keep 2 sheep on the border between two
  adjacent rooms") and A011 ("on unplanted fields") don't fit
  `holds_animals`/farmyard cells at all -- both keep animals on farmyard
  geometry this engine doesn't model (a room-border slot; an unplanted
  field cell) rather than on a card instance, so they remain a separate,
  still-open gap. None of the motivating cards above are registered by
  this pass (`temp_card`-only tests in `tests/test_agricola.py` exercise
  the mechanism); registering them with `compendium_card` is still a
  separate pass.
- **Cards that ARE action spaces (item 15)** are now supported (engine
  phase 9). Previously there was no way for a card to become an
  additional space on the board that other players (or only the owner)
  could place a person on. Now: spec key `card_space={"name", "desc",
  "owner_only", "acc", "usable", "resolve"}` on either an occupation or
  a minor improvement appends `{"id": "card:<cid>", ..., "card_space":
  True, "card": cid, "owner": <index>}` to `state["action_spaces"]`
  once the card is played (`sub_actions.play_minor`/`play_occupation`
  share an `_add_card_space` helper); `resolve=fn(state, player, inst,
  action, log) -> goods` performs the space's own effect for whichever
  player places there (who may not be the owner) and returns only the
  goods to credit -- the engine credits them the normal way (resources,
  accommodation for animals, `gained(source="space")`, `space_used`),
  so a toll paid to the owner (`cards.card_space_owner` finds them) or
  bonus points stay inside the fn and never touch the return value; an
  `"acc"` dict/fn makes it a normal accumulation space instead (E164-
  style, no `resolve` needed); `"owner_only"` and `"usable"` gate who
  may place there and when. Every other action-space mechanic
  (occupancy, `occupied_ok`, `returning_home`, `space_used`'s
  `occupants`, round replenishment, listing) falls out of the existing
  generic handling with no special-casing needed. One incidental fix
  fell out of this pass: `_apply_choice`'s card-instance lookup now
  searches every player's in-play cards (not just the prompted
  player's), since a `card_space`'s `resolve` fn may prompt the placing
  player about a choice tied to a card owned by someone else -- safe
  since card ids are unique per game. See `decks/GUIDE.md`'s
  `card_space` section for the full contract and a worked I337 example.
  This unblocks the motivating compendium cards: A039 Chapel, B042
  Forest Inn, D023 Pioneering Spirit, D051 Archway, E164 Master
  Forester, I100 Tavern, I337 Clay Deposit. (C039 Studio Boat is not
  part of this mechanism -- it just aliases an existing action space.
  D051 Archway's second clause, "may use an unoccupied action space
  before returning home," still needs the not-yet-built item-17
  extra-action machinery.) None of the motivating cards are registered
  by this pass (`temp_card`-only tests in `tests/test_agricola.py`
  exercise the mechanism); registering them with `compendium_card` is
  still a separate pass.
- **The board's 2D geometry (item 16)** is now supported (engine phase
  10; corrected to the printed layout in phase 20). Previously there
  was no way for a card to reference the physical layout of action
  spaces -- which one is left of another, or orthogonally adjacent, on
  the actual printed board. Now: every space is a RECT `(col, top,
  height)` in half-row units (the printed boxes are not all one size:
  round spaces are two base rows tall, the 3p extension's boxes 1.5).
  `state.py`'s `SPACE_POSITIONS` (keyed by player count) gives every
  permanent action space its rect, and `ROUND_SLOTS` gives every round
  NUMBER (1-14) a rect -- top-aligned stage columns of two-row boxes,
  with round 1 atop the accumulation column directly above Forest
  (pinned by Alex's photo of the physical board; the Compendium's
  B120 ruling, "The action space must be round 1-6 or 8-12", falls
  out of this layout exactly) --
  so a revealed round space's rect follows from `state["revealed"]`'s
  reveal order; `cards.space_rect`, `cards.adjacent_spaces` (rect
  edge-sharing), `cards.spaces_adjacent`, `cards.left_neighbor`
  (same-shape box one column left; None for rounds 1/3/4, matching
  the B120 ruling), and `cards.vertical_neighbors` (D165's
  above/below) are the queries a card hook uses. See `decks/GUIDE.md`'s
  "Board geometry" section for the full coordinate system (ASCII board
  diagram), the queries, and a worked example. Motivating cards: B120
  Sweep, C117 Legworker, D144 Water Worker, D165 Pig Stalker, FR006
  Badger, FR027 Ground Pickaxe Plow, FR037 Necklace. D144's exactly-three Fishing neighbors are Day
  Laborer, Reed Bank, and the round-4 box, confirmed by the photo.
  **Fidelity caveats**: the Appendix's worked example ("The Grove is
  adjacent to both Farm Expansion and Meeting Place") is now DERIVED
  from the 3p extension's 1.5-row boxes; `state.EXTRA_ADJACENCY` keeps
  the pair only for the unphotographed 4-player strip, whose layout
  (six 1-row boxes, Copse on top) remains a placement choice rather
  than a confirmed reading. Extend EXTRA_ADJACENCY only from primary
  sources.
- **Turn-structure manipulation (item 17)** is now supported (engine
  phase 11). Previously there was no way for a card to let a player skip
  their placement turn for a consolation gain, to place out of the
  normal rotation order, or to be locked out of placing altogether for
  a round. Now: `skip_turn=fn(state, player, inst) -> gain dict | None`
  (queried by `_skip_actions`/offered as a `{"kind": "skip", ...}`
  action alongside placements, guarded so it's never offered once every
  OTHER player is already done placing) plus the optional `after_skip=
  fn(state, player, inst, log)` for the card to mark its own usage;
  `_advance_work`'s `start_pidx` resolution gained a new `_resume_from`
  fallback (after the existing `_pending_work_start` stash, before the
  default `current_player + 1`), a one-shot value a `resolve_choice` can
  set to let one player place ahead of rotation and then have rotation
  resume from wherever it actually belongs; and `placement_blocked=
  fn(state, player, inst) -> bool` (`cards.placement_blocked`), which
  `_placement_actions`/`_skip_actions` both return `[]` for, so the
  existing forfeit branch in `_advance_work` picks a blocked player up
  automatically (now with a lockout-specific log line instead of the
  generic "no usable space" one). See `decks/GUIDE.md`'s "Turn
  structure" section for the full contracts, the I260 first-placer
  recipe worked step by step, and the K269/K289 `returning_home` recipe
  (no engine change needed for those two -- just an existing hook plus
  the `sub_actions` API, with a documented fidelity simplification: the
  "moved" person never literally occupies the target space). This
  unblocks the motivating compendium cards: D053 Tea House (`skip_turn`/
  `after_skip`), I260 Taster (the `_resume_from` recipe), I71 Holiday
  House (`placement_blocked`), K269 Acrobat/K289 Countryman (the
  `returning_home` recipe, no engine change). I238 Chamberlain is only
  PARTIALLY unblocked: revealing upcoming round cards to one player
  needs a player-specific view of `state["deck"]` (currently hidden from
  every view; `get_player_view` would need a new field gated to the
  chamberlain's owner), and its "exclusive use of the current round
  space" clause is per-card `occupied_ok`/`usable`-style work -- neither
  is built by this pass, so I238 stays gated on a view-layer gap, not a
  turn-structure one. None of the motivating cards (D053, I260, I71,
  K269, K289) are registered by this pass (`temp_card`-only tests in
  `tests/test_agricola.py` exercise each mechanism); registering them
  with `compendium_card` is still a separate pass.
- **Hand/deck manipulation (item 18)** is now supported (engine phase
  12). Previously a dealt hand's leftover cards were simply discarded
  (`deal_hands` sliced hands off a shuffled list and threw the rest
  away), and no card could react while still sitting unplayed in a
  hand. Now: `cards.deal_hands` also returns the shuffled leftovers,
  which `initial_state` stores as persistent `state["occupation_draw"]`/
  `["minor_draw"]` piles (top = index 0) plus empty `state["occupation_
  discard"]`/`["minor_discard"]` piles -- `cards.draw_minors`/
  `draw_occupations`/`discard_hand_minors`/`discard_hand_occupations`
  are the card-facing helpers (deliberately in `cards.py`, not
  `sub_actions.py`: no cost/transaction involved, so no reason to route
  through that module, and no import-cycle risk either way); there is
  NO reshuffle-when-empty, matching the physical game. `get_player_view`/
  `get_spectator_view` hide all four piles' contents (count only) from
  EVERY player, not just opponents (`engine._hide_draw_piles`), since
  nobody should see the future draw order or discard history. Separately,
  a new top-level spec key `hand_react={"event", "fn(state, hand_player,
  ctx)"}` (no `inst` -- the card hasn't been played) lets a card react
  from inside a hand; it's wired ONLY into the two broadcast card-play
  events (`occupation_played`/`minor_played`, via `sub_actions._fire_
  broadcast` -> `cards.fire_hand_react`), not every `fire()` call.
  Answering "yes" to a `hand_react`-queued choice needs a card spec's
  `resolve_choice` reachable with no instance to look up -- `prompt_
  choice` gained a `from_hand=True` flag; `_apply_choice`'s instance
  search now falls back to the CARDS registry spec (calling `resolve_
  choice` with `inst=None`) when the search comes up empty and the
  prompt says `from_hand`. See `decks/GUIDE.md`'s "Hand and deck"
  section for the full contracts, the K125 Broom discard/redraw recipe,
  and a worked E173-style `hand_react`/`from_hand` example (including a
  termination argument for the reactive play's own re-fired
  `occupation_played`/`minor_played`). This unblocks the motivating
  compendium cards: K125 Broom (draw piles), E173 Chief's Daughter
  (`hand_react`/`from_hand`). B023 Final Scenario is assessed as
  expressible TODAY with existing `card_space` + `sub_actions` plumbing
  and no new engine change -- but only because this engine's stage 6
  currently has exactly one card (`farm_redevelopment`), making
  `state["deck"][13]` deterministic rather than a genuine random reveal;
  GUIDE.md's recipe documents that coupling explicitly so a future
  second stage-6 card doesn't silently break it. Incidental finding
  (fixed in review): `get_player_view`/`get_spectator_view` used to
  `deepcopy` and return `state["deck"]` (the future round-card order)
  unredacted to everyone, contradicting item 17's description; views
  now carry only a count for it, same treatment as the draw/discard
  piles. B023's "reveal round 14" needs no view exception anyway -- the
  sole stage-6 card makes `state["deck"][13]` deterministic, and its
  `card_space` entry carries the public name. An I238-style selective
  reveal still needs a per-player view exception (item 17).
  None of K125/E173/B023 are registered by this
  pass (`temp_card`-only tests in `tests/test_agricola.py` exercise each
  mechanism, plus a `card_space`-based proof of the B023 recipe);
  registering them with `compendium_card` is still a separate pass.
- **Field/fence/grid extensions (item 19, engine phase 13 — the last
  item of the 19-item engine-gap program)** are now supported for
  multi-stack card fields and a fence-piece substitute; per-player grid
  growth remains gated. Motivating cards, per-card status:
  - **K105 Acreage** (2 independent grain stacks) and **FR089 Landscape
    Gardener** (2 stacks, any crop, plus an on-play bonus Sow action) —
    UNBLOCKED. `field={"crops": ..., "stacks": n}` (default 1, zero
    migration for every existing stacks=1 card field) gives a card
    instance `n` independent `{"type","count"}`-or-`None` slots
    (`inst["stacks"]`, read/written only via `cards.field_stacks`/
    `get_field_stack`/`set_field_stack`/`open_field_stacks`, which also
    read an old save's stacks=1 `inst["crops"]` with no `"stacks"` key
    at all); `sub_actions.sow`'s `sow_items` gained an optional
    `"stack"` selector so the same card id can be sown twice in one
    call (or across separate calls) at different stack indices;
    `engine._run_field_phase` harvests each stack independently; FR089's
    on-play bonus Sow needs no new plumbing either -- a play hook can
    call `sub_actions.sow` directly via the params channel, the same
    shape Shifting Cultivation already uses for its on-play plow.
    **Scoring finding:** FR089's "(this card does not count as a field
    when scoring)" is true of every card field already and always was
    -- `scoring.score_player`'s `fields` tally only ever counts farmyard
    CELL tiles (`c["type"] == "field"`), never `cards.card_fields`, with
    or without this phase's changes. See `decks/GUIDE.md`'s "Field
    stacks" section for the full contract and the reader/writer audit.
  - **C069 Land Consolidation** ("exchange 3 grain in a field for 1
    vegetable in that field") — reassessed, STILL GATED. The multi-stack
    mechanism is scoped to a CARD instance's own stacks; it does not
    give a farmyard CELL (or any one stack) the ability to hold two crop
    types in the same slot, which is exactly what this exchange needs.
    No change from its prior status.
  - **B030 Wood Palisades** (wood tokens instead of a fence piece,
    excluded from the 15-fence cap, worth a bonus point, border-edge
    only) — the general MECHANISM is IMPLEMENTED: `player["fence_tokens"]`
    (edge -> granting card id) tracks which of `player["fences"]` (the
    single geometric truth -- `compute_pastures`/`validate_fence_layout`'s
    pasture logic is completely untouched) are tokens;
    `state.validate_fence_layout` gained an optional `token_edges=`
    parameter (backward compatible) so its own `MAX_FENCES` check
    excludes them; `state.is_border_edge` is the new eligibility check;
    `sub_actions.build_fences` gained an optional `tokens=` parameter
    that prices normal edges as before and the card's own tokens
    separately (`fence_token={"cost": {...}}`), validates ownership and
    border-eligibility, and records them; bonus points are a plain
    `score_bonus` counting the card's own tokens -- no new scoring
    plumbing needed. Decisive reason this stayed at "mechanism only,
    card not registered": consistent with every prior phase's practice
    (guest tokens, card_space, board geometry, turn structure, hand/deck
    manipulation) of proving a general mechanism via `temp_card` tests
    before a specific compendium card adopts it — not a fidelity
    obstacle in B030's own text, which is otherwise completely clean.
  - **FR001 Abandoned Willow** ("remove 1 empty field, receive 4 wood")
    — a plain RECIPE, no plumbing needed: a play hook flips
    `cell["type"]` back to `"empty"` (Shifting Cultivation's on-play
    plow, run in reverse). Verified no hidden invariant breaks (plow
    targets, pasture validity, and scoring all behave correctly on the
    reverted cell) — see `decks/GUIDE.md`'s "FR001 recipe" section and
    `test_fr001_style_remove_empty_field_recipe`.
  - **FR059 Witches' Dance Ground** (+/-2 farmyard spaces) — assessed,
    STILL GATED: no clean seam. `NUM_CELLS`/`cell_rc`/`cell_index`/
    `cell_edges`/`edge_cells` (the coordinate system fence edges are
    keyed on), `orthogonal_neighbors`, `compute_regions`'s flood fill,
    `plowable_cells`, and the `SCORING_TABLES` `"fields"`/`"pastures"`/
    `unused_spaces` categories all assume a fixed 15-cell (3x5) farmyard
    as GLOBAL constants/functions, not per-player state. A variable-
    length or non-rectangular per-player farmyard would need all of
    those to become per-player, and the fence edge-key scheme
    reconsidered too — materially bigger than a bounded engine addition.
    This is the one remaining gated geometry gap after phase 13.

  None of K105/FR089/B030/FR001 are registered by this pass (`temp_card`-
  only tests in `tests/test_agricola.py` exercise each mechanism, plus
  `cards.needs_grain_field`/the Scythe hook/`scoring.score_player` were
  updated to read crops via `cards.field_stacks` so they see a future
  stacks>1 card correctly); registering them with `compendium_card`, and
  updating the handful of existing sow-reactive hooks that still read
  `inst["crops"]` directly (Seed Drill, and compendium E32/K118/
  Smallholder — safe today since no stacks>1 card is registered; see
  `decks/GUIDE.md`'s audit), are both still separate passes.

**Summary — what's still not supported after all 13 phases:**

- FR059-style per-player farmyard grid growth/shrinkage (this phase;
  the fixed 15-cell/3x5 geometry is deeply load-bearing).
- D148 Domestician Expert / A011 ("keep animals on a room-border slot" /
  "on unplanted fields") — storage shapes that don't fit `holds_animals`
  (a card instance) or farmyard cells at all (item 14).
- I238 Chamberlain's selective reveal of upcoming round cards to one
  player (needs a per-player view exception; item 17/18).
- Moving/removing a built fence or room (removing a FIELD is now
  supported — see this phase's FR001 recipe; fences/rooms are a bigger
  case, see `decks/GUIDE.md`'s "Not supported" section for why).
- Unplacing/returning another player's already-placed person (D093
  Sheep Inspector, D094/D150) — no "unplace" primitive.
- Farmers of the Moor (deck M): fuel/heating, horses, forest/moor tiles
  — a whole expansion's systems.
- Returning played cards to hand, or playing another player's cards;
  score-sheet manipulation beyond bonus points; a card modifying the
  action-space CARD deck's order/reveal; wood/food placed on a card to
  be taken by OTHER players.
- Per-card fidelity work still open even where the underlying mechanism
  exists: registering any of the many `temp_card`-only-proven mechanisms
  (items 14-19) onto their actual motivating compendium cards, and the
  large "bonus build/play sub-action" backlog listed under item 7's
  entry above (A095, A096, B088, B150, FR072, and others).

  See `decks/GUIDE.md`'s "Not supported" section for the living,
  itemized version of this list.
