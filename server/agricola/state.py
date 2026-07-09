"""
Agricola (Revised Edition) — constants, board geometry, and setup helpers.

Implements the full game: each player is dealt 7 occupations and 7 minor
improvements (see cards.py and CARDS.md for the card system).

Farmyard geometry
-----------------
The farmyard is a 3x5 grid. Cells are indexed 0..14, row-major
(cell = row * 5 + col). The two starting wood rooms are cells 5 and 10
(column 0, rows 1 and 2).

Fences sit on edges. Edge keys are strings:
  "h-r-c"  horizontal edge above cell (r, c);  r in 0..3, c in 0..4
           (r == 3 is the bottom board border below row 2)
  "v-r-c"  vertical edge left of cell (r, c);  r in 0..2, c in 0..5
           (c == 5 is the right board border right of column 4)
"""

ROWS = 3
COLS = 5
NUM_CELLS = ROWS * COLS
STARTING_ROOM_CELLS = (5, 10)

MAX_PEOPLE = 5
MAX_STABLES = 4
MAX_FENCES = 15

ANIMAL_TYPES = ("sheep", "boar", "cattle")
RESOURCE_TYPES = ("food", "wood", "clay", "reed", "stone", "grain", "vegetable")
BUILDING_RESOURCES = ("wood", "clay", "reed", "stone")

HARVEST_ROUNDS = (4, 7, 9, 11, 13, 14)
TOTAL_ROUNDS = 14

# round -> stage
def stage_of_round(rnd):
    if rnd <= 4:
        return 1
    if rnd <= 7:
        return 2
    if rnd <= 9:
        return 3
    if rnd <= 11:
        return 4
    if rnd <= 13:
        return 5
    return 6


# ── Farmyard geometry ────────────────────────────────────────────────

def cell_rc(idx):
    return divmod(idx, COLS)


def cell_index(r, c):
    return r * COLS + c


def cell_edges(idx):
    """The 4 edge keys around a cell (top, bottom, left, right)."""
    r, c = cell_rc(idx)
    return [f"h-{r}-{c}", f"h-{r + 1}-{c}", f"v-{r}-{c}", f"v-{r}-{c + 1}"]


def all_edge_keys():
    edges = []
    for r in range(ROWS + 1):
        for c in range(COLS):
            edges.append(f"h-{r}-{c}")
    for r in range(ROWS):
        for c in range(COLS + 1):
            edges.append(f"v-{r}-{c}")
    return edges


VALID_EDGES = frozenset(all_edge_keys())


def edge_cells(edge):
    """The 1 or 2 cell indices an edge touches (1 for board-border edges)."""
    kind, r, c = edge.split("-")
    r, c = int(r), int(c)
    cells = []
    if kind == "h":
        if r - 1 >= 0:
            cells.append(cell_index(r - 1, c))
        if r <= ROWS - 1:
            cells.append(cell_index(r, c))
    else:
        if c - 1 >= 0:
            cells.append(cell_index(r, c - 1))
        if c <= COLS - 1:
            cells.append(cell_index(r, c))
    return cells


def shared_edge(a, b):
    """Edge key between two orthogonally adjacent cells (or None)."""
    ra, ca = cell_rc(a)
    rb, cb = cell_rc(b)
    if ca == cb and abs(ra - rb) == 1:
        return f"h-{max(ra, rb)}-{ca}"
    if ra == rb and abs(ca - cb) == 1:
        return f"v-{ra}-{max(ca, cb)}"
    return None


def orthogonal_neighbors(idx):
    r, c = cell_rc(idx)
    out = []
    if r > 0:
        out.append(idx - COLS)
    if r < ROWS - 1:
        out.append(idx + COLS)
    if c > 0:
        out.append(idx - 1)
    if c < COLS - 1:
        out.append(idx + 1)
    return out


def compute_regions(fences):
    """
    Split the 15 cells into regions separated by fences (flood fill).
    Returns a list of sets of cell indices.
    """
    fences = set(fences)
    seen = set()
    regions = []
    for start in range(NUM_CELLS):
        if start in seen:
            continue
        region = {start}
        stack = [start]
        seen.add(start)
        while stack:
            cur = stack.pop()
            for nb in orthogonal_neighbors(cur):
                if nb in seen:
                    continue
                if shared_edge(cur, nb) in fences:
                    continue
                seen.add(nb)
                region.add(nb)
                stack.append(nb)
        regions.append(region)
    return regions


def region_is_enclosed(region, fences):
    """
    A region is enclosed iff every board-border edge of its cells is fenced.
    (Edges to cells outside the region are fenced by flood-fill definition.)
    """
    fences = set(fences)
    for idx in region:
        for edge in cell_edges(idx):
            if len(edge_cells(edge)) == 1 and edge not in fences:
                return False
    return True


def compute_pastures(player):
    """
    Return the list of pastures as sorted lists of cell indices.
    A pasture is an enclosed region whose cells are all pasture-eligible
    (no room or field tiles). Assumes the fence layout is valid.
    """
    pastures = []
    for region in compute_regions(player["fences"]):
        if not region_is_enclosed(region, player["fences"]):
            continue
        if all(player["cells"][i]["type"] == "empty" for i in region):
            pastures.append(sorted(region))
    return pastures


def validate_fence_layout(player, fences):
    """
    Validate a complete fence layout (list of edge keys) against the
    player's tiles. Returns (ok, error_message, pastures).
    """
    fences = set(fences)
    for e in fences:
        if e not in VALID_EDGES:
            return False, f"Invalid fence position: {e}", []
    if len(fences) > MAX_FENCES:
        return False, f"Only {MAX_FENCES} fences are available", []

    regions = compute_regions(fences)
    pastures = []
    for region in regions:
        if not region_is_enclosed(region, fences):
            continue
        if any(player["cells"][i]["type"] != "empty" for i in region):
            return False, "Fences cannot enclose rooms or fields", []
        pastures.append(sorted(region))

    # Every fence must border at least one pasture cell.
    pasture_cells = {i for p in pastures for i in p}
    for e in fences:
        if not any(c in pasture_cells for c in edge_cells(e)):
            return False, "Every fence must border a fully enclosed pasture", []

    # All pastures together must form one orthogonally connected group.
    if pasture_cells:
        stack = [next(iter(pasture_cells))]
        seen = {stack[0]}
        while stack:
            cur = stack.pop()
            for nb in orthogonal_neighbors(cur):
                if nb in pasture_cells and nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        if seen != pasture_cells:
            return False, "All pastures must form one connected group", []

    return True, "", pastures


def pasture_capacity(player, pasture, bonus=0):
    """Capacity of a pasture: 2 per cell, doubled per stable inside,
    plus a flat card bonus (e.g. Drinking Trough)."""
    stables = sum(1 for i in pasture if player["cells"][i]["stable"])
    return 2 * len(pasture) * (2 ** stables) + bonus


def animal_counts(player):
    """Total animals on the farm (cells + house pets + card-held storage
    -- e.g. Cattle Farm/Animal Yard/Wildlife Reserve -- since animals kept
    on a card are still animals on the player's farm for every rule that
    counts them: breeding, feeding conversions, scoring). Card instances
    are plain data (`inst["held"] = {type: count}`), read directly here
    with no dependency on cards.py."""
    totals = {t: 0 for t in ANIMAL_TYPES}
    for cell in player["cells"]:
        a = cell.get("animal")
        if a:
            totals[a["type"]] += a["count"]
    for t, n in player.get("pets", {}).items():
        totals[t] += n
    for inst in player.get("occupations", []) + player.get("minors", []):
        for t, n in (inst.get("held") or {}).items():
            totals[t] = totals.get(t, 0) + n
    return totals


def validate_animal_placement(player, house_cap=1, pasture_cap=None,
                              unfenced_stable_cap=None, secondary_types=None):
    """
    Check the current animal placement against husbandry rules.
    `house_cap` is a flat int (card modifiers folded in by the caller,
    e.g. `cards.house_capacity`). The rest are callbacks a card-aware
    caller supplies to fold in per-pasture/type modifiers -- the
    defaults reproduce the original flat behavior exactly (2 per cell,
    doubled per stable inside; 1 animal in an unfenced stable; no second
    type sharing a pasture), so this module stays card-free:

    - `pasture_cap(cells, animal_type) -> int` -- capacity of a pasture
      (`cells`, a sorted list of cell indices) for `animal_type`.
    - `unfenced_stable_cap(animal_type) -> int` -- capacity of an
      unfenced stable for `animal_type`.
    - `secondary_types(info) -> {type: max_count}` -- animal types
      allowed alongside `info["animal_type"]` in that pasture, up to
      `max_count` each, still counting against the pasture's total
      `pasture_cap`. `info` is `{"cells": ..., "size": len(cells),
      "stables": stable count inside, "animal_type": the primary type
      being tried}`.

    Returns (ok, error_message).
    """
    if pasture_cap is None:
        pasture_cap = lambda cells, animal_type: pasture_capacity(player, cells)
    if unfenced_stable_cap is None:
        unfenced_stable_cap = lambda animal_type: 1
    if secondary_types is None:
        secondary_types = lambda info: {}

    pets = player.get("pets", {})
    if any(n < 0 for n in pets.values()):
        return False, "Invalid pets"
    if sum(pets.values()) > house_cap:
        return False, f"Your house holds at most {house_cap} animal(s)"

    pastures = compute_pastures(player)
    pasture_of = {}
    for pi, p in enumerate(pastures):
        for i in p:
            pasture_of[i] = pi

    per_pasture = {}  # pi -> {type: count}
    for idx, cell in enumerate(player["cells"]):
        a = cell.get("animal")
        if not a:
            continue
        if a["count"] <= 0:
            return False, "Animal counts must be positive"
        if idx in pasture_of:
            pi = pasture_of[idx]
            counts = per_pasture.setdefault(pi, {})
            counts[a["type"]] = counts.get(a["type"], 0) + a["count"]
        elif cell["stable"] and cell["type"] == "empty":
            cap = unfenced_stable_cap(a["type"])
            if a["count"] > cap:
                return False, f"An unfenced stable holds only {cap} {a['type']}"
        else:
            return False, "Animals must be in pastures, unfenced stables, or the house"

    for pi, counts in per_pasture.items():
        cells = pastures[pi]
        total = sum(counts.values())
        if len(counts) == 1:
            (only_type,) = counts
            if total > pasture_cap(cells, only_type):
                return False, "Pasture over capacity"
            continue
        # More than one type in this pasture: valid iff some present type
        # can serve as "primary" -- every other type present fits within
        # that primary's secondary allowance, and the combined total
        # still fits the pasture's (primary-conditioned) capacity.
        stables = sum(1 for i in cells if player["cells"][i]["stable"])
        if not any(
            total <= pasture_cap(cells, primary) and all(
                counts[t] <= secondary_types(
                    {"cells": cells, "size": len(cells), "stables": stables,
                     "animal_type": primary}).get(t, 0)
                for t in counts if t != primary)
            for primary in counts
        ):
            return False, "A pasture can only hold one type of animal"
    return True, ""


def plowable_cells(player):
    """Cells where a field tile may be placed right now."""
    pasture_cells = {i for p in compute_pastures(player) for i in p}
    fields = [i for i, c in enumerate(player["cells"]) if c["type"] == "field"]
    eligible = []
    for i, c in enumerate(player["cells"]):
        if c["type"] != "empty" or c["stable"] or i in pasture_cells:
            continue
        if fields and not any(nb in fields for nb in orthogonal_neighbors(i)):
            continue
        eligible.append(i)
    return eligible


# ── Action spaces ────────────────────────────────────────────────────
# "counts": player counts for which the space is on the board.
# "acc": goods added each preparation phase (accumulation spaces).
# Solo exception (Forest 2 wood instead of 3) is handled in the engine.

PERMANENT_SPACES = [
    {"id": "farm_expansion", "name": "Farm Expansion",
     "desc": "Build rooms and/or build stables", "counts": (1, 2, 3, 4)},
    {"id": "meeting_place", "name": "Meeting Place",
     "desc": "Become starting player, then you may play a minor improvement",
     "counts": (1, 2, 3, 4)},
    {"id": "grain_seeds", "name": "Grain Seeds",
     "desc": "Get 1 grain", "counts": (1, 2, 3, 4)},
    {"id": "farmland", "name": "Farmland",
     "desc": "Plow 1 field", "counts": (1, 2, 3, 4)},
    {"id": "lessons", "name": "Lessons",
     "desc": "Play an occupation (your first is free, then 1 food)",
     "counts": (1, 2, 3, 4)},
    {"id": "day_laborer", "name": "Day Laborer",
     "desc": "Get 2 food", "counts": (1, 2, 3, 4)},
    {"id": "forest", "name": "Forest",
     "desc": "Accumulation: 3 wood", "counts": (1, 2, 3, 4), "acc": {"wood": 3}},
    {"id": "clay_pit", "name": "Clay Pit",
     "desc": "Accumulation: 1 clay", "counts": (1, 2, 3, 4), "acc": {"clay": 1}},
    {"id": "reed_bank", "name": "Reed Bank",
     "desc": "Accumulation: 1 reed", "counts": (1, 2, 3, 4), "acc": {"reed": 1}},
    {"id": "fishing", "name": "Fishing",
     "desc": "Accumulation: 1 food", "counts": (1, 2, 3, 4), "acc": {"food": 1}},
    # 3-player spaces
    {"id": "grove", "name": "Grove",
     "desc": "Accumulation: 2 wood", "counts": (3, 4), "acc": {"wood": 2}},
    {"id": "hollow_3p", "name": "Hollow",
     "desc": "Accumulation: 1 clay", "counts": (3,), "acc": {"clay": 1}},
    {"id": "resource_market_3p", "name": "Resource Market",
     "desc": "Get 1 reed or 1 stone, plus 1 food", "counts": (3,)},
    {"id": "lessons_b", "name": "Lessons",
     "desc": "Play an occupation (3p: 2 food; 4p: first two cost 1 food, then 2)",
     "counts": (3, 4)},
    # 4-player spaces
    {"id": "copse", "name": "Copse",
     "desc": "Accumulation: 1 wood", "counts": (4,), "acc": {"wood": 1}},
    {"id": "hollow_4p", "name": "Hollow",
     "desc": "Accumulation: 2 clay", "counts": (4,), "acc": {"clay": 2}},
    {"id": "resource_market_4p", "name": "Resource Market",
     "desc": "Get 1 reed, 1 stone, and 1 food", "counts": (4,)},
    {"id": "traveling_players", "name": "Traveling Players",
     "desc": "Accumulation: 1 food", "counts": (4,), "acc": {"food": 1}},
]

STAGE_CARDS = {
    "sheep_market": {"name": "Sheep Market", "stage": 1,
                     "desc": "Accumulation: 1 sheep", "acc": {"sheep": 1}},
    "fencing": {"name": "Fencing", "stage": 1, "desc": "Build fences"},
    "grain_utilization": {"name": "Grain Utilization", "stage": 1,
                          "desc": "Sow and/or Bake Bread"},
    "major_improvement": {"name": "Major Improvement", "stage": 1,
                          "desc": "Build a major improvement"},
    "basic_wish": {"name": "Basic Wish for Children", "stage": 2,
                   "desc": "Family growth (needs room)"},
    "house_redevelopment": {"name": "House Redevelopment", "stage": 2,
                            "desc": "Renovate, then major improvement"},
    "western_quarry": {"name": "Western Quarry", "stage": 2,
                       "desc": "Accumulation: 1 stone", "acc": {"stone": 1}},
    "vegetable_seeds": {"name": "Vegetable Seeds", "stage": 3,
                        "desc": "Get 1 vegetable"},
    "pig_market": {"name": "Pig Market", "stage": 3,
                   "desc": "Accumulation: 1 wild boar", "acc": {"boar": 1}},
    "cattle_market": {"name": "Cattle Market", "stage": 4,
                      "desc": "Accumulation: 1 cattle", "acc": {"cattle": 1}},
    "eastern_quarry": {"name": "Eastern Quarry", "stage": 4,
                       "desc": "Accumulation: 1 stone", "acc": {"stone": 1}},
    "urgent_wish": {"name": "Urgent Wish for Children", "stage": 5,
                    "desc": "Family growth (even without room)"},
    "cultivation": {"name": "Cultivation", "stage": 5,
                    "desc": "Plow 1 field and/or Sow"},
    "farm_redevelopment": {"name": "Farm Redevelopment", "stage": 6,
                           "desc": "Renovate, then build fences"},
}


def build_stage_deck(rng):
    """Deck of stage card ids in reveal order (round 1 first)."""
    deck = []
    for stage in range(1, 7):
        ids = [cid for cid, c in STAGE_CARDS.items() if c["stage"] == stage]
        rng.shuffle(ids)
        deck.extend(ids)
    return deck


# ── Board geometry (engine phase 10) ─────────────────────────────────
# Physical (col, row) position of every action space, for cards that
# reference the board's 2D layout (B120 Sweep, C117 Legworker, D144
# Water Worker, D165 Pig Stalker, FR006 Badger, FR027 Ground Pickaxe
# Plow, FR037 Necklace -- see CARDS.md item 16 and decks/GUIDE.md's
# "Board geometry" section for the derivation, worked examples, and
# fidelity caveats). Columns increase rightward, rows increase downward.
# Derived from the Revised Edition rulebook's board photos (page 3 of
# en_agricolare.html_Rules_Agricola-RE_EN.pdf: the 2-player and
# 3-player board images) plus the Appendix's text (page 4,
# "Column of the game board" / "The Grove is adjacent to both Farm
# Expansion and Meeting Place", and the per-stage action-space listing
# on page 7) -- both PDFs live in
# overnightlemons.com/game_rulebooks/Agricola/. The rulebook photos are
# too low-resolution to read pixel-exact, so the round-space grid
# (below) is a best-effort reconstruction cross-checked against the
# card text itself (D144 needs Fishing to have exactly 3 neighbors;
# D165 needs a real "above/below" neighbor for a round-space animal
# market to ever exist) rather than a pixel-confirmed reading.

# The base column (all player counts), top to bottom, and the
# accumulation column beside it (Forest is beside Grain Seeds, Clay Pit
# beside Farmland, Reed Bank beside Lessons, Fishing beside Day
# Laborer -- confirmed directly in the board photo):
_BASE_POSITIONS = {
    "farm_expansion": (0, 0),
    "meeting_place": (0, 1),
    "grain_seeds": (0, 2),
    "farmland": (0, 3),
    "lessons": (0, 4),
    "day_laborer": (0, 5),
    "forest": (1, 2),
    "clay_pit": (1, 3),
    "reed_bank": (1, 4),
    "fishing": (1, 5),
}

# The 3-/4-player extension is a separate strip attached to the LEFT of
# the base column (confirmed in the board photo), one row per base-row
# it sits beside. The Appendix gives one concrete adjacency fact --
# "The Grove is adjacent to both Farm Expansion and Meeting Place" --
# which a single-cell-per-space grid cannot reproduce exactly (the
# printed extension boxes are taller than one base row); Grove sits
# beside Meeting Place (row 1) in the grid, and the Farm-Expansion
# adjacency is restored by the explicit EXTRA_ADJACENCY override below.
# Grove/Hollow/Resource Market/Lessons_b are the 3-player extension
# (counts=(3,) except Grove and Lessons_b, which the Appendix confirms
# are UNCHANGED at 4 players too, counts=(3, 4) in PERMANENT_SPACES).
# The 4-player extension additionally has its own Copse (+1 wood) and
# bigger Hollow/Resource Market -- neither PDF shows the 4-player
# board's photo, so Copse is placed at row 0 (beside Farm Expansion),
# the extension column's one otherwise-unused row, rather than a
# confirmed layout; this incidentally reproduces the Appendix's "Grove
# adjacent to Farm Expansion" fact for 4p via Copse-then-Grove instead
# (Copse (-1,0) is adjacent to both Farm Expansion (0,0) and Grove
# (-1,1)).
_3P_EXTRA = {
    "grove": (-1, 1),
    "resource_market_3p": (-1, 2),
    "hollow_3p": (-1, 3),
    "lessons_b": (-1, 4),
}

_4P_EXTRA = {
    "copse": (-1, 0),
    "grove": (-1, 1),
    "resource_market_4p": (-1, 2),
    "hollow_4p": (-1, 3),
    "lessons_b": (-1, 4),
    "traveling_players": (-1, 5),
}

# Keyed by state["player_count"]; 1 and 2 players share the base board.
SPACE_POSITIONS = {
    1: dict(_BASE_POSITIONS),
    2: dict(_BASE_POSITIONS),
    3: {**_BASE_POSITIONS, **_3P_EXTRA},
    4: {**_BASE_POSITIONS, **_4P_EXTRA},
}

# Round-space slot per ROUND NUMBER (1..14), not per card id -- the
# card revealed in round N always sits at slot N's position, whichever
# of that stage's (shuffled) cards it happens to be. Each stage gets
# its own column, stacked downward in round order starting at row 2
# (beside Grain Seeds/Forest, the accumulation column's second row):
# stage 1 (4 rounds) fills rows 2-5 of column 2, stage 2 (3 rounds)
# rows 2-4 of column 3, stages 3-5 (2 rounds each) rows 2-3 of columns
# 4-6, stage 6 (1 round) row 2 of column 7. This is what gives Fishing
# (1, 5) exactly 3 neighbors once all 14 rounds are revealed -- Day
# Laborer (0, 5) left, Reed Bank (1, 4) above, Round 4 (2, 5) right --
# matching D144's own text ("Fishing... or one of the three
# orthogonally adjacent action spaces"), and gives every 2+-round stage
# a genuine internal above/below pair for D165.
ROUND_SLOTS = {
    1: (2, 2), 2: (2, 3), 3: (2, 4), 4: (2, 5),
    5: (3, 2), 6: (3, 3), 7: (3, 4),
    8: (4, 2), 9: (4, 3),
    10: (5, 2), 11: (5, 3),
    12: (6, 2), 13: (6, 3),
    14: (7, 2),
}

# Adjacencies the printed board has but the single-cell grid above
# cannot express (extension-strip boxes are taller than one base row).
# Unordered pairs, keyed by player count; unioned into
# cards.adjacent_spaces/spaces_adjacent. Only add a pair here when a
# primary source documents it -- the Grove pair is stated verbatim in
# the Appendix ("The Grove is adjacent to both Farm Expansion and
# Meeting Place"); no other extension adjacency is documented, so the
# rest stay grid-derived.
EXTRA_ADJACENCY = {
    3: [("grove", "farm_expansion")],
    4: [("grove", "farm_expansion")],
}


# ── Major improvements ───────────────────────────────────────────────
# cook: food per animal/vegetable cooked ("at any time").
# bake: (max grain per Bake action or None for unlimited, food per grain).
# harvest_food: (resource, food) — once per harvest, at most 1 resource.
# scoring_bonus: (resource, [(min_amount, points), ...] highest first).

MAJOR_IMPROVEMENTS = {
    "fireplace_2": {"name": "Fireplace (2 clay)", "cost": {"clay": 2}, "points": 1,
                    "cook": {"sheep": 2, "boar": 2, "cattle": 3, "vegetable": 2},
                    "bake": (None, 2)},
    "fireplace_3": {"name": "Fireplace (3 clay)", "cost": {"clay": 3}, "points": 1,
                    "cook": {"sheep": 2, "boar": 2, "cattle": 3, "vegetable": 2},
                    "bake": (None, 2)},
    "cooking_hearth_4": {"name": "Cooking Hearth (4 clay)", "cost": {"clay": 4}, "points": 1,
                         "cook": {"sheep": 2, "boar": 3, "cattle": 4, "vegetable": 3},
                         "bake": (None, 3), "upgrade_of": "fireplace"},
    "cooking_hearth_5": {"name": "Cooking Hearth (5 clay)", "cost": {"clay": 5}, "points": 1,
                         "cook": {"sheep": 2, "boar": 3, "cattle": 4, "vegetable": 3},
                         "bake": (None, 3), "upgrade_of": "fireplace"},
    "clay_oven": {"name": "Clay Oven", "cost": {"clay": 3, "stone": 1}, "points": 2,
                  "bake": (1, 5), "bake_on_build": True},
    "stone_oven": {"name": "Stone Oven", "cost": {"clay": 1, "stone": 3}, "points": 3,
                   "bake": (2, 4), "bake_on_build": True},
    "joinery": {"name": "Joinery", "cost": {"wood": 2, "stone": 2}, "points": 2,
                "harvest_food": ("wood", 2),
                "scoring_bonus": ("wood", [(7, 3), (5, 2), (3, 1)])},
    "pottery": {"name": "Pottery", "cost": {"clay": 2, "stone": 2}, "points": 2,
                "harvest_food": ("clay", 2),
                "scoring_bonus": ("clay", [(7, 3), (5, 2), (3, 1)])},
    "basketmaker": {"name": "Basketmaker's Workshop", "cost": {"reed": 2, "stone": 2}, "points": 2,
                    "harvest_food": ("reed", 3),
                    "scoring_bonus": ("reed", [(5, 3), (4, 2), (2, 1)])},
    "well": {"name": "Well", "cost": {"wood": 1, "stone": 3}, "points": 4,
             "well": True},
}

FIREPLACES = ("fireplace_2", "fireplace_3")
COOKING_HEARTHS = ("cooking_hearth_4", "cooking_hearth_5")


def player_cook_values(player):
    """Best cooking values across the player's improvements (or None)."""
    best = None
    for imp in player["improvements"]:
        cook = MAJOR_IMPROVEMENTS[imp].get("cook")
        if cook:
            if best is None:
                best = dict(cook)
            else:
                for k, v in cook.items():
                    best[k] = max(best[k], v)
    return best


# ── Scoring tables ───────────────────────────────────────────────────
# List of (minimum amount, points), checked from highest to lowest.

SCORING_TABLES = {
    "fields": [(5, 4), (4, 3), (3, 2), (2, 1), (0, -1)],
    "pastures": [(4, 4), (3, 3), (2, 2), (1, 1), (0, -1)],
    "grain": [(8, 4), (6, 3), (4, 2), (1, 1), (0, -1)],
    "vegetable": [(4, 4), (3, 3), (2, 2), (1, 1), (0, -1)],
    "sheep": [(8, 4), (6, 3), (4, 2), (1, 1), (0, -1)],
    "boar": [(7, 4), (5, 3), (3, 2), (1, 1), (0, -1)],
    "cattle": [(6, 4), (4, 3), (2, 2), (1, 1), (0, -1)],
}


def table_score(table, amount):
    for minimum, points in SCORING_TABLES[table]:
        if amount >= minimum:
            return points
    return -1


# ── Setup helpers ────────────────────────────────────────────────────

def create_cell():
    return {"type": "empty", "stable": False, "animal": None, "crops": None}


def create_player(index, player_id, name):
    cells = [create_cell() for _ in range(NUM_CELLS)]
    for i in STARTING_ROOM_CELLS:
        cells[i]["type"] = "room"
    return {
        "index": index,
        "player_id": player_id,
        "name": name,
        "resources": {r: 0 for r in RESOURCE_TYPES},
        "cells": cells,
        "fences": [],
        "house_type": "wood",
        "people_total": 2,
        "people_placed": 0,
        "guests": 0,
        "newborns": 0,
        "pets": {},
        "begging": 0,
        "improvements": [],
        "hand_occupations": [],
        "hand_minors": [],
        "occupations": [],
        "minors": [],
        "occs_played": 0,
        "harvest_conversions_used": [],
        "fed": False,
    }


def create_action_spaces(player_count):
    spaces = []
    for spec in PERMANENT_SPACES:
        if player_count in spec["counts"]:
            spaces.append({
                "id": spec["id"],
                "name": spec["name"],
                "desc": spec["desc"],
                "stage": 0,
                "occupied_by": None,
                "extra_occupants": [],
                "supply": {},
                "accumulates": bool(spec.get("acc")),
            })
    return spaces
