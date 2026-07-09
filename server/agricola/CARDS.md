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
player, gain)`, which credits non-animal goods directly and queues an
accommodation prompt for animal goods. `on_play_gain` and
`space_bonus(..., others=True)` already do this internally, so declaring a
card with either factory is animal-safe regardless of the gain's contents.

Current hook points: `play`, `occupation_played`, `round_start`,
`space_used`, `fences_built`, `stable_built`, `rooms_built`, `sow`, `bake`,
`renovate`, `plow`, `harvest_field`, `converted`, `returning_home`.

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

- **Guest tokens / extra people**: a per-round placement counter on the
  player, consulted by `_advance_work` (the Lasso's replacement-turn logic
  is the template).
- **Replacement effects** ("use an occupied space"): a `mod_valid` query in
  `_space_usable` / `_apply_place`, same pattern as `cost_mod`.
- **Farmers of the Moor** (deck M): fuel/heating, horses, forest/moor
  tiles — a whole expansion's systems; M-deck cards stay unimplemented
  until that lands.
