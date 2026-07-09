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
   hold scheduled goods, or count usage.
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
  `renovation`, `improvement`, `fences` (ctx has the fence count), plus
  `occ_cost_delta` for occupation costs ("each occupation costs 1 less").
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

`game_rulebooks/Agricola/AgricolaCards-5-4-2026/agricola_cards.csv`
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
- **Farmers of the Moor** (deck M): fuel/heating, horses, forest/moor
  tiles — a whole expansion's systems; M-deck cards stay unimplemented
  until that lands.
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
