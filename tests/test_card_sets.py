"""Tests for custom card sets: the store (server/card_sets.py), the
GameServer resolution of card_set_id at room creation, and the
Agricola engine's card_pool deal/draft support."""

import random

import pytest

from server.agricola.engine import AgricolaEngine
from server.agricola import cards
from server.card_sets import CardSetStore
from server.server import GameServer


@pytest.fixture
def engine():
    return AgricolaEngine()


def sample_pool(occ=12, minor=12, player_count=4, decks=("A",)):
    """A small valid pool from real implemented cards."""
    return (cards.deck_for("occupation", player_count, decks)[:occ]
            + cards.deck_for("minor", player_count, decks)[:minor])


def make_server(tmp_path=None):
    server = GameServer(
        card_set_dir=str(tmp_path / "card_sets") if tmp_path else None)
    server.register_engine("agricola", AgricolaEngine)
    return server


# ── CardSetStore ─────────────────────────────────────────────────────

@pytest.mark.parametrize("on_disk", [False, True])
def test_store_roundtrip(tmp_path, on_disk):
    store = CardSetStore(str(tmp_path) if on_disk else None)
    pool = sample_pool()
    saved = store.save("agricola", {"name": " My Set ", "cards": pool,
                                    "author": "alex"},
                       AgricolaEngine.validate_card_set)
    assert saved["name"] == "My Set"
    assert saved["cards"] == pool
    assert saved["author"] == "alex"
    assert saved["id"].startswith("my-set-")

    assert store.get("agricola", saved["id"])["cards"] == pool
    assert [s["id"] for s in store.list_sets("agricola")] == [saved["id"]]
    # Scoped by game.
    assert store.list_sets("othergame") == []

    assert store.delete("agricola", saved["id"])
    assert not store.delete("agricola", saved["id"])
    assert store.get("agricola", saved["id"]) is None
    assert store.list_sets("agricola") == []


def test_store_update_by_id_and_fresh_ids(tmp_path):
    store = CardSetStore(str(tmp_path))
    pool = sample_pool()
    a = store.save("agricola", {"name": "Set", "cards": pool},
                   AgricolaEngine.validate_card_set)
    b = store.save("agricola", {"name": "Set", "cards": pool},
                   AgricolaEngine.validate_card_set)
    # Same name without an id never clobbers: fresh suffixed id.
    assert a["id"] != b["id"]
    assert len(store.list_sets("agricola")) == 2
    # Saving WITH the id updates in place.
    updated = store.save("agricola",
                         {"id": a["id"], "name": "Renamed", "cards": pool[:5]},
                         AgricolaEngine.validate_card_set)
    assert updated["id"] == a["id"]
    assert store.get("agricola", a["id"])["name"] == "Renamed"
    assert len(store.list_sets("agricola")) == 2


def test_store_dedupes_and_validates(tmp_path):
    store = CardSetStore(str(tmp_path))
    pool = sample_pool(occ=3, minor=3)
    saved = store.save("agricola", {"name": "Dupes", "cards": pool + pool},
                       AgricolaEngine.validate_card_set)
    assert saved["cards"] == pool

    validate = AgricolaEngine.validate_card_set
    for bad_payload, why in [
        ({"name": "", "cards": pool}, "name"),
        ({"name": "X", "cards": []}, "empty"),
        ({"name": "X", "cards": "nope"}, "not a list"),
        ({"name": "X", "cards": pool, "id": "Bad Id!"}, "bad id"),
        ("nope", "not a dict"),
    ]:
        with pytest.raises(ValueError):
            store.save("agricola", bad_payload, validate)
    with pytest.raises(ValueError):
        store.save("bad game!", {"name": "X", "cards": pool}, validate)
    # Path-traversal shapes are rejected, not resolved.
    assert store.get("agricola", "../evil") is None
    assert not store.delete("agricola", "../evil")


def test_validate_card_set(engine):
    pool = sample_pool(occ=2, minor=2)
    assert AgricolaEngine.validate_card_set(pool) == pool
    with pytest.raises(ValueError, match="not implemented"):
        AgricolaEngine.validate_card_set(pool + ["A010"])
    with pytest.raises(ValueError, match="unknown"):
        AgricolaEngine.validate_card_set(["ZZZZ"])
    major = next(cid for cid, db in cards.compendium().items()
                 if db["type"] == "major")
    with pytest.raises(ValueError, match="major"):
        AgricolaEngine.validate_card_set([major])


# ── GameServer resolution ────────────────────────────────────────────

def test_create_room_freezes_card_set(tmp_path):
    server = make_server(tmp_path)
    pool = sample_pool()
    saved = server.card_sets.save("agricola", {"name": "Frozen", "cards": pool},
                                  AgricolaEngine.validate_card_set)

    code, _, _ = server.create_room("agricola", "Alex",
                                    {"card_set_id": saved["id"]})
    options = server.rooms[code].options
    assert options["card_pool"] == pool
    assert options["card_set_name"] == "Frozen"

    # Deleting the set doesn't touch the existing room, but blocks new ones.
    server.card_sets.delete("agricola", saved["id"])
    assert server.rooms[code].options["card_pool"] == pool
    with pytest.raises(ValueError, match="not found"):
        server.create_room("agricola", "Alex", {"card_set_id": saved["id"]})


def test_create_room_without_set_unchanged():
    server = make_server()
    code, _, _ = server.create_room("agricola", "Alex", {"decks": ["A"]})
    assert "card_pool" not in server.rooms[code].options


# ── Engine deal/draft over a pool ────────────────────────────────────

def test_deal_hands_pool_overrides_decks():
    pool = sample_pool()
    rng = random.Random(7)
    occ_hands, minor_hands, size, occ_draw, minor_draw = \
        cards.deal_hands(2, rng, ["base"], pool=pool)
    dealt = set(occ_draw) | set(minor_draw)
    for hand in occ_hands + minor_hands:
        dealt |= set(hand)
    assert dealt == set(pool)  # the pool, exactly — decks arg ignored
    assert size == 6  # 12 occ / 2 players


def test_deal_hands_pool_respects_min_players():
    three_plus = [cid for cid, c in cards.CARDS.items()
                  if c["type"] == "occupation" and c["min_players"] == 3]
    assert three_plus, "expected some 3+ occupations"
    pool = sample_pool() + three_plus[:2]
    rng = random.Random(7)
    occ_hands, minor_hands, _, occ_draw, _ = \
        cards.deal_hands(2, rng, [], pool=pool)
    dealt_occs = set(occ_draw)
    for hand in occ_hands:
        dealt_occs |= set(hand)
    assert dealt_occs.isdisjoint(three_plus)


def test_initial_state_with_pool_no_draft(engine):
    random.seed(3)
    pool = sample_pool()
    s = engine.initial_state(["a", "b"], ["A", "B"],
                             {"card_pool": pool, "card_set_name": "My Pool",
                              "decks": ["E"]})
    assert s["card_set"] == "My Pool"
    in_play = set(s["occupation_draw"]) | set(s["minor_draw"])
    for p in s["players"]:
        in_play |= set(p["hand_occupations"]) | set(p["hand_minors"])
        assert len(p["hand_occupations"]) == 6
    assert in_play == set(pool)


def test_initial_state_with_pool_draft(engine):
    random.seed(4)
    pool = sample_pool()
    s = engine.initial_state(
        ["a", "b"], ["A", "B"],
        {"card_pool": pool, "draft_mode": "pick_and_pass",
         "draft_deal": 7, "draft_keep": 5})
    assert s["phase"] == "draft"
    # deal auto-shrunk to what 12+12 cards support for 2 players.
    assert s["draft"]["deal"] == 6
    assert s["draft"]["keep"] == 5
    for queue in s["draft"]["queues"]:
        for packet in queue:
            assert set(packet) <= set(pool)


def test_initial_state_ignores_malformed_pool(engine):
    random.seed(5)
    s = engine.initial_state(["a", "b"], ["A", "B"],
                             {"card_pool": "junk", "decks": ["A"]})
    assert "card_set" not in s
    assert all(len(p["hand_occupations"]) == 7 for p in s["players"])
