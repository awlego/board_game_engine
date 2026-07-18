# Agricola (Revised Edition) — Rules Summary

Source: `rules.pdf` (rule book) and `appendix.pdf` (appendix), Lookout Games 2016,
Uwe Rosenberg. 1–4 players, 14 rounds.

## Implementation scope

This engine implements the **full game with hand cards**: each player is
dealt 7 occupations and 7 minor improvements at setup (dealt from the
implemented card pool — see `../cards.py` and `../CARDS.md`; cards are
filtered by player count like the official occ-1/occ-3/occ-4 classes).

- **Lessons** (all counts): play an occupation — your first is free, each
  after that costs 1 food. The second Lessons space (3–4 players) costs
  2 food per occupation; in the 4-player game the first two occupations you
  play there cost 1 food each, later ones 2.
- **Meeting Place**: become starting player, then you may play a minor
  improvement. **Basic Wish for Children**: family growth, then you may play
  a minor improvement. **Major Improvement space** and **House
  Redevelopment**: build a major improvement *or* play a minor improvement.
- Minor improvements have costs (any goods) and prerequisites; traveling
  cards pass to the left neighbor after being played (removed in solo).

All 10 major improvements, full fencing/animal-husbandry rules, harvests,
and the complete scoring (including card points and bonus points) are
implemented. Solo rules are supported (see below).

## Setup

- Each player: farmyard board of 3×5 spaces; two wood rooms (column 0, rows 1
  and 2); 2 people (in the rooms); supply of 3 more people, 4 stables,
  15 fences.
- Starting player is random; they get 2 food, everyone else 3 food.
  (Solo: 0 food.) House rule (3-player games): the player going third
  gets 4 food instead of 3.
- The 14 action space cards are stacked by stage, shuffled within each stage:
  stage 1 (rounds 1–4): Sheep Market, Fencing, Grain Utilization, Major
  Improvement; stage 2 (5–7): Basic Wish for Children, House Redevelopment,
  Western Quarry; stage 3 (8–9): Vegetable Seeds, Pig Market; stage 4 (10–11):
  Cattle Market, Eastern Quarry; stage 5 (12–13): Urgent Wish for Children,
  Cultivation; stage 6 (14): Farm Redevelopment.

## Round structure (14 rounds)

1. **Preparation**: reveal the round's action space card; pay out any food
   placed on the round space (Well); replenish all accumulation spaces.
2. **Work**: starting with the starting player and going clockwise, each player
   places exactly one person on an unoccupied action space and takes at least
   one of its actions (one person per space per round). Players with no people
   left are skipped.
3. **Returning home**: all people return (automatic).
4. **Harvest** at the end of rounds 4, 7, 9, 11, 13, 14 (see below).

## Action spaces

Permanent, all player counts (this variant):

| Space | Action |
|---|---|
| Farm Expansion | Build rooms (5 wood/clay/stone + 2 reed each, by house type) and/or build stables (2 wood each) |
| Meeting Place | Become starting player, then may play a minor improvement |
| Grain Seeds | Get 1 grain |
| Farmland | Plow 1 field |
| Lessons | Play an occupation (first free, then 1 food) |
| Day Laborer | Get 2 food |
| Forest | Accumulation +3 wood (+2 solo) |
| Clay Pit | Accumulation +1 clay |
| Reed Bank | Accumulation +1 reed |
| Fishing | Accumulation +1 food |

3-player extras: Grove (+2 wood), Hollow (+1 clay), Resource Market (1 reed OR
1 stone, plus 1 food), Lessons #2 (occupation for 2 food). 4-player extras:
Copse (+1 wood), Grove (+2 wood), Hollow (+2 clay), Resource Market (1 reed
AND 1 stone AND 1 food), Traveling Players (+1 food), Lessons #2 (first two
occupations 1 food each, then 2).

Stage cards: Sheep/Pig/Cattle Market and the Quarries are accumulation spaces
(+1 of their good per round). Fencing = Build Fences. Grain Utilization = Sow
and/or Bake Bread. Major Improvement = build one major improvement (or upgrade
a Fireplace to a Cooking Hearth). Basic Wish for Children = family growth, only
with more rooms than people. House Redevelopment = 1 renovation, then may build
a major improvement. Urgent Wish for Children = family growth without room.
Cultivation = plow 1 field and/or sow. Farm Redevelopment = 1 renovation, then
may build fences.

## Farmyard rules

- **Rooms** must be adjacent to existing rooms; **fields** adjacent to existing
  fields (the first field anywhere). Both only on empty, unfenced spaces.
- **Renovation**: all rooms at once, wood→clay then clay→stone; costs 1 reed
  plus 1 clay/stone per room. After renovating, new rooms cost the new material.
- **Fences**: 1 wood each, max 15. Fences sit on edges between spaces or on the
  board border. Every fence must form part of the boundary of a fully enclosed
  pasture. Pastures may contain only empty spaces and stables. All pastures
  must form one orthogonally connected group (the first may go anywhere).
  Existing pastures may be subdivided. Fences are never demolished.
- **Stables**: 2 wood each (1 wood on Side Job), max 4, one per space, not on a
  space covered by a tile. A stable in a pasture doubles its capacity (two
  stables quadruple it, etc.). An unfenced stable holds exactly 1 animal.
- **Capacity**: pasture holds 2 animals per space × 2^(stables inside), one
  type per pasture. House holds exactly 1 pet of any type. Animals that cannot
  be accommodated must be cooked (Fireplace/Cooking Hearth) or returned to the
  supply. Animals may be rearranged/discarded at any time (in this engine:
  when gaining animals, when fencing invalidates an arrangement, and during
  the feeding phase).

## Cultivation

- **Sow**: place 1 grain from supply on an empty field → the field holds
  3 grain; or 1 vegetable → the field holds 2 vegetables. Any number of empty
  fields per Sow action.
- **Field phase of harvest**: take exactly 1 crop from every planted field.

## Harvest

1. **Field phase**: 1 crop from each planted field (mandatory, automatic).
2. **Feeding**: 2 food per person; newborns (born this round) need only 1.
   (Solo: 3 food per adult.) Grain/vegetables convert at 1 food raw; Fireplace/
   Cooking Hearth cook vegetables (2/3) and animals (sheep 2/2, boar 2/3,
   cattle 3/4); craft buildings convert once per harvest (Joinery 1 wood→2,
   Pottery 1 clay→2, Basketmaker's Workshop 1 reed→3). Baking bread is NOT
   allowed during feeding. 1 begging marker (−3 points) per missing food.
3. **Breeding**: each animal type with ≥2 animals yields exactly 1 newborn if
   it can be accommodated; newborns cannot be cooked during breeding.

## Major improvements

| Card | Cost | Pts | Effect |
|---|---|---|---|
| Fireplace (×2) | 2 clay / 3 clay | 1 | Cook: sheep 2, boar 2, cattle 3, veg 2; bake 2/grain |
| Cooking Hearth (×2) | 4 clay / 5 clay (or swap a Fireplace) | 1 | Cook: sheep 2, boar 3, cattle 4, veg 3; bake 3/grain |
| Clay Oven | 3 clay 1 stone | 2 | Bake: exactly 1 grain → 5 food per Bake action; bake on build |
| Stone Oven | 1 clay 3 stone | 3 | Bake: up to 2 grain → 4 food each; bake on build |
| Joinery | 2 wood 2 stone | 2 | Harvest: 1 wood → 2 food; scoring: 3/5/7 wood → 1/2/3 pts |
| Pottery | 2 clay 2 stone | 2 | Harvest: 1 clay → 2 food; scoring: 3/5/7 clay → 1/2/3 pts |
| Basketmaker's Workshop | 2 reed 2 stone | 2 | Harvest: 1 reed → 3 food; scoring: 2/4/5 reed → 1/2/3 pts |
| Well | 1 wood 3 stone | 4 | 1 food on each of the next 5 round spaces |

Bake Bread (only via Grain Utilization, Side Job, or on building an oven):
each grain via one owned baking improvement; Clay Oven max 1 grain and Stone
Oven max 2 grain per Bake action; Fireplace/Hearth unlimited.

## Scoring (after round 14's harvest)

| Category | −1 | 1 | 2 | 3 | 4 |
|---|---|---|---|---|---|
| Field tiles | 0–1 | 2 | 3 | 4 | 5+ |
| Pastures | 0 | 1 | 2 | 3 | 4+ |
| Grain (supply+fields) | 0 | 1–3 | 4–5 | 6–7 | 8+ |
| Vegetables | 0 | 1 | 2 | 3 | 4+ |
| Sheep | 0 | 1–3 | 4–5 | 6–7 | 8+ |
| Wild boar | 0 | 1–2 | 3–4 | 5–6 | 7+ |
| Cattle | 0 | 1 | 2–3 | 4–5 | 6+ |

Plus: −1 per unused farmyard space (used = covered by tile/stable or fenced);
+1 per fenced stable; clay rooms 1 each; stone rooms 2 each; people 3 each;
printed points on improvements; craft-building bonus points for leftover
resources; −3 per begging marker. Tie-breaker: building resources left in
supply. Most points wins.

## Solo game

2-player board; start with 0 food; adults cost 3 food at feeding (newborns 1);
Forest accumulates 2 wood instead of 3.
