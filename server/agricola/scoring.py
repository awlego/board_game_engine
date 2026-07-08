"""Agricola end-game scoring (see rules/rules.md, appendix section 10)."""

from server.agricola.state import (
    MAJOR_IMPROVEMENTS, BUILDING_RESOURCES,
    animal_counts, compute_pastures, table_score,
)
from server.agricola import cards


def score_player(player, state=None):
    """Return a dict of category scores plus 'total' for one player."""
    cells = player["cells"]
    pastures = compute_pastures(player)
    pasture_cells = {i for p in pastures for i in p}
    animals = animal_counts(player)

    fields = sum(1 for c in cells if c["type"] == "field")
    grain = player["resources"]["grain"]
    vegetable = player["resources"]["vegetable"]
    for c in cells:
        if c["crops"]:
            if c["crops"]["type"] == "grain":
                grain += c["crops"]["count"]
            else:
                vegetable += c["crops"]["count"]
    # Crops growing on card fields (e.g. Beanfield) count as crops
    # but not as field tiles.
    for inst in cards.card_fields(player):
        if inst["crops"]:
            if inst["crops"]["type"] == "grain":
                grain += inst["crops"]["count"]
            else:
                vegetable += inst["crops"]["count"]

    unused = sum(
        1 for i, c in enumerate(cells)
        if c["type"] == "empty" and not c["stable"] and i not in pasture_cells
    )
    fenced_stables = sum(1 for i in pasture_cells if cells[i]["stable"])
    rooms = sum(1 for c in cells if c["type"] == "room")

    room_points = 0
    if player["house_type"] == "clay":
        room_points = rooms
    elif player["house_type"] == "stone":
        room_points = rooms * 2

    improvement_points = sum(
        MAJOR_IMPROVEMENTS[i]["points"] for i in player["improvements"]
    )
    improvement_points += cards.printed_points(player)

    bonus = 0
    for imp in player["improvements"]:
        sb = MAJOR_IMPROVEMENTS[imp].get("scoring_bonus")
        if sb:
            resource, tiers = sb
            have = player["resources"][resource]
            for minimum, points in tiers:
                if have >= minimum:
                    bonus += points
                    break
    if state is not None:
        bonus += cards.score_bonuses(state, player)
    bonus -= 3 * player["begging"]

    scores = {
        "fields": table_score("fields", fields),
        "pastures": table_score("pastures", len(pastures)),
        "grain": table_score("grain", grain),
        "vegetable": table_score("vegetable", vegetable),
        "sheep": table_score("sheep", animals["sheep"]),
        "boar": table_score("boar", animals["boar"]),
        "cattle": table_score("cattle", animals["cattle"]),
        "unused_spaces": -unused,
        "fenced_stables": min(fenced_stables, 4),
        "rooms": room_points,
        "people": player["people_total"] * 3,
        "improvements": improvement_points,
        "bonus": bonus,
    }
    scores["total"] = sum(scores.values())
    return scores


def final_scores(state):
    """Score every player; returns (scores list, winner indices)."""
    results = []
    for p in state["players"]:
        s = score_player(p, state)
        s["player_index"] = p["index"]
        s["name"] = p["name"]
        s["tiebreak_resources"] = sum(
            p["resources"][r] for r in BUILDING_RESOURCES
        )
        results.append(s)

    best = max(s["total"] for s in results)
    top = [s for s in results if s["total"] == best]
    if len(top) > 1:
        best_tb = max(s["tiebreak_resources"] for s in top)
        top = [s for s in top if s["tiebreak_resources"] == best_tb]
    winners = [s["player_index"] for s in top]
    return results, winners
